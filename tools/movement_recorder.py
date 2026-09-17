# tools/movement_recorder.py
"""
Movement macro recorder - small standalone GUI. Records WASD + Space (jump) hold
timing so the bot can later walk a fixed path (e.g. spawn -> a portal's fishing spot)
by replaying the same holds. No mouse look is recorded - movement only.

This tool is separate from the main bot GUI (gui.py) - it doesn't feed into
automation yet. It's for recording/testing a path and finding coordinates (e.g. the
fishing spot) to hand back for wiring into the real flow later.

Usage: python tools/movement_recorder.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import threading
import customtkinter as ctk
from pynput import keyboard
from modules.movement_recorder import key_name, save_movement, play_movement
from input_controller import anchor_camera, focus_roblox_window
from modules import preset_share

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class MovementRecorderGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Movement Recorder")
        self.geometry("420x520")
        self.resizable(False, False)

        self.is_recording = False
        self.start_time = 0.0
        self.actions = []
        self.held = set()
        self.last_recording = None

        ctk.CTkLabel(self, text="MOVEMENT RECORDER", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(20, 5))
        ctk.CTkLabel(
            self, text="Records WASD + Space (jump) hold timing only - no mouse look.",
            font=ctk.CTkFont(size=11), text_color="#9e9e9e", wraplength=380
        ).pack(pady=(0, 15))

        ctk.CTkLabel(self, text="Save As", font=ctk.CTkFont(size=11), text_color="#9e9e9e").pack(anchor="w", padx=20)
        self.name_entry = ctk.CTkEntry(self, placeholder_text="e.g. summer_initial_point")
        self.name_entry.pack(fill="x", padx=20, pady=(2, 15))

        self.camera_btn = ctk.CTkButton(
            self, text="Lock Camera (Anchor)", fg_color="#455a64", hover_color="#263238",
            command=self._lock_camera_clicked
        )
        self.camera_btn.pack(fill="x", padx=20, pady=(0, 15))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=20)

        self.record_btn = ctk.CTkButton(
            btn_row, text="RECORD (F8)", fg_color="#2e7d32", hover_color="#1b5e20",
            font=ctk.CTkFont(weight="bold"), height=38, command=self._start_clicked
        )
        self.record_btn.pack(side="left", expand=True, fill="x", padx=(0, 5))

        self.stop_btn = ctk.CTkButton(
            btn_row, text="STOP (F8)", fg_color="#c62828", hover_color="#8e0000",
            font=ctk.CTkFont(weight="bold"), height=38, state="disabled", command=self._stop_clicked
        )
        self.stop_btn.pack(side="right", expand=True, fill="x", padx=(5, 0))

        self.replay_btn = ctk.CTkButton(self, text="Replay Last (F9)", command=self._replay_clicked)
        self.replay_btn.pack(fill="x", padx=20, pady=(15, 0))

        self.save_btn = ctk.CTkButton(
            self, text="Save As (name above)", fg_color="#1565c0", hover_color="#0d47a1",
            command=self._save_clicked
        )
        self.save_btn.pack(fill="x", padx=20, pady=(10, 0))

        share_row = ctk.CTkFrame(self, fg_color="transparent")
        share_row.pack(fill="x", padx=20, pady=(10, 0))

        self.export_btn = ctk.CTkButton(share_row, text="Export...", command=self._export_clicked)
        self.export_btn.pack(side="left", expand=True, fill="x", padx=(0, 5))

        self.import_btn = ctk.CTkButton(share_row, text="Import...", command=self._import_clicked)
        self.import_btn.pack(side="right", expand=True, fill="x", padx=(5, 0))

        self.status_label = ctk.CTkLabel(
            self, text="Status: IDLE", font=ctk.CTkFont(size=13, weight="bold"), text_color="#a8a8a8"
        )
        self.status_label.pack(pady=(25, 5))

        self.info_label = ctk.CTkLabel(
            self, text="No recording yet.", font=ctk.CTkFont(size=11), text_color="#9e9e9e", wraplength=380
        )
        self.info_label.pack(pady=(0, 10))

        ctk.CTkLabel(
            self, text="Global hotkeys also work anywhere: F8 start/stop, F9 replay, ESC quits this tool.",
            font=ctk.CTkFont(size=10), text_color="#616161", wraplength=380
        ).pack(side="bottom", pady=(0, 15))

        self.keyboard_listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self.keyboard_listener.start()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # --- thread-safe UI helpers (safe to call from the pynput listener thread too) ---
    def _set_status(self, text, color="#a8a8a8"):
        self.after(0, lambda: self.status_label.configure(text=f"Status: {text}", text_color=color))

    def _set_info(self, text):
        self.after(0, lambda: self.info_label.configure(text=text))

    # --- Camera ---
    def _lock_camera_clicked(self):
        self.camera_btn.configure(state="disabled", text="Anchoring...")
        threading.Thread(target=self._lock_camera_worker, daemon=True).start()

    def _lock_camera_worker(self):
        # pin=False: the default pin=True calls pin_roblox_window(), which resizes
        # Roblox to the full REFERENCE 1920x1080 at (0,0) - and because this tool is
        # its own process, nothing ever restores it, leaving the window stuck
        # full-screen after the tool exits. Anchoring only needs focus, not a resize.
        if not focus_roblox_window(pin=False):
            self._set_info("WARNING: Could not find/focus the Roblox window.")
        anchor_camera()
        self.after(0, lambda: self.camera_btn.configure(state="normal", text="Lock Camera (Anchor)"))
        self._set_info("Camera anchored - this is the view the bot will use during a real run.")

    # --- Recording controls (shared by buttons + hotkeys) ---
    def _start_clicked(self):
        if not self.name_entry.get().strip():
            self._set_info("Enter a save name first.")
            return
        self._start_recording()

    def _stop_clicked(self):
        self._stop_recording()

    def _replay_clicked(self):
        self._replay_last()

    def _save_clicked(self):
        if not self.last_recording:
            self._set_info("Nothing recorded yet - record something first.")
            return
        name = self.name_entry.get().strip()
        if not name:
            self._set_info("Enter a name in the field above first.")
            return
        filepath = save_movement(self.last_recording, name)
        self._set_info(f"Saved {len(self.last_recording)} events to {filepath}.")

    def _export_clicked(self):
        """Exports the SAVED file named in the field above, not just the in-memory
        last recording - so you can export something saved in an earlier session too."""
        name = self.name_entry.get().strip()
        if not name:
            self._set_info("Enter the saved name to export in the field above first.")
            return
        dest_path = preset_share.export_movement_preset(name)
        if dest_path:
            self._set_info(f"Exported '{name}' to {dest_path}")

    def _import_clicked(self):
        name = preset_share.import_movement_preset()
        if name:
            self.name_entry.delete(0, "end")
            self.name_entry.insert(0, name)
            self._set_info(f"Imported '{name}' - it's saved to movement_presets/ and ready to replay.")

    def _start_recording(self):
        if self.is_recording or not self.name_entry.get().strip():
            return
        self.is_recording = True
        self.start_time = time.time()
        self.actions = []
        self.held = set()
        self._set_status("RECORDING", "#e53935")
        self._set_info("Walk the path now. Stop when you reach the target.")
        self.after(0, lambda: self.record_btn.configure(state="disabled"))
        self.after(0, lambda: self.stop_btn.configure(state="normal"))
        self.after(0, lambda: self.name_entry.configure(state="disabled"))

    def _stop_recording(self):
        if not self.is_recording:
            return
        self.is_recording = False
        now = time.time() - self.start_time

        # Release any keys still logically held at stop time, so a saved recording
        # never ends with a key stuck down mid-hold.
        for name in list(self.held):
            self.actions.append({"time": round(now, 3), "type": "keyup", "key": name})
        self.held.clear()

        self.last_recording = list(self.actions)
        save_name = self.name_entry.get().strip() or "unnamed"
        filepath = save_movement(self.last_recording, save_name)

        self._set_status("IDLE", "#a8a8a8")
        self._set_info(
            f"Saved {len(self.last_recording)} events ({now:.2f}s) to {filepath}. "
            f"Change the name above and hit 'Save As' to save it under a different name."
        )
        self.after(0, lambda: self.record_btn.configure(state="normal"))
        self.after(0, lambda: self.stop_btn.configure(state="disabled"))
        self.after(0, lambda: self.name_entry.configure(state="normal"))

    def _replay_last(self):
        if self.is_recording or not self.last_recording:
            if not self.last_recording:
                self._set_info("Nothing recorded yet.")
            return
        self.after(0, lambda: self.replay_btn.configure(state="disabled", text="Replaying..."))
        threading.Thread(target=self._replay_worker, daemon=True).start()

    def _replay_worker(self):
        self._set_status("REPLAYING (testing)", "#4fc3f7")
        time.sleep(1.5)  # gives you a moment to tab back into Roblox
        play_movement(self.last_recording)
        self._set_status("IDLE", "#a8a8a8")
        self.after(0, lambda: self.replay_btn.configure(state="normal", text="Replay Last (F9)"))

    # --- Global hotkeys (mirror the buttons, work regardless of window focus) ---
    def _on_press(self, key):
        if key == keyboard.Key.f8:
            if not self.is_recording:
                self.after(0, self._start_clicked)
            else:
                self.after(0, self._stop_clicked)
            return
        if key == keyboard.Key.f9:
            self.after(0, self._replay_clicked)
            return
        if key == keyboard.Key.esc:
            self.after(0, self._on_close)
            return

        if not self.is_recording:
            return
        name = key_name(key)
        if name is None or name in self.held:
            return  # ignore non-movement keys and OS auto-repeat while a key stays held
        self.held.add(name)
        elapsed = time.time() - self.start_time
        self.actions.append({"time": round(elapsed, 3), "type": "keydown", "key": name})

    def _on_release(self, key):
        if not self.is_recording:
            return
        name = key_name(key)
        if name is None or name not in self.held:
            return
        self.held.discard(name)
        elapsed = time.time() - self.start_time
        self.actions.append({"time": round(elapsed, 3), "type": "keyup", "key": name})

    def _on_close(self):
        if self.is_recording:
            self._stop_recording()
        try:
            self.keyboard_listener.stop()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    app = MovementRecorderGUI()
    app.mainloop()
