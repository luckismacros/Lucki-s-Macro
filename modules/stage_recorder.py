import time
import json
import os
from pynput import mouse, keyboard
import config
from modules.movement_recorder import key_name
from modules.keybinds import matches as key_matches

class StageRecorder:
    def __init__(self, f7_callback=None, autoclicker_callback=None,
                 start_stop_key="f7", record_key="f8", autoclicker_key="f9"):
        self.is_recording = False
        self.start_time = 0.0
        self.actions = []
        self.current_location = "school_grounds"
        self.current_variant = "act_1"
        self.preset_name = "default"
        self.keyboard_listener = None
        self.mouse_listener = None
        self.f7_callback = f7_callback
        self.autoclicker_callback = autoclicker_callback
        # Rebindable - see ui_qt/dialogs.py's Keybinds section. Read fresh on every
        # keypress (not resolved to a pynput key once up front) so a rebind made while
        # this listener is already running takes effect immediately.
        self.start_stop_key = start_stop_key
        self.record_key = record_key
        self.autoclicker_key = autoclicker_key

        # Debounce tracking
        self.last_key = None
        self.last_key_time = 0.0
        self.held_movement = set()

        os.makedirs("presets", exist_ok=True)

    def on_click(self, x, y, button, pressed):
        try:
            if not self.is_recording or not pressed or button != mouse.Button.left:
                return
                
            elapsed = time.time() - self.start_time

            # Stored in REFERENCE space, not raw screen pixels. pynput reports the
            # raw DESKTOP cursor position, which needs the client's current on-screen
            # origin subtracted before the scale does (see config.to_screen()) - on a
            # smaller/differently-positioned screen that's a different number for the
            # same spot in the game, so recording raw would bake this machine's
            # window position/resolution into the preset and place units in the wrong
            # place anywhere else. Converting here is what keeps a shared preset
            # correct on someone else's monitor, and it costs nothing at scale 1.0
            # with the client pinned at the origin.
            ref_x, ref_y = config.to_reference(x - config.CLIENT_ORIGIN[0], y - config.CLIENT_ORIGIN[1])
            self.actions.append({"time": elapsed, "type": "click", "pos": (ref_x, ref_y)})
            print(f"[Record] Clicked ({ref_x}, {ref_y}) at {elapsed:.2f}s")
        except Exception:
            pass

    # How often the mouse path is sampled while recording, and the smallest movement
    # worth keeping. ~30 samples a second follows a hand's path closely enough to look
    # natural on playback without flooding the file.
    MOVE_SAMPLE_SECONDS = 1 / 30
    MOVE_MIN_PIXELS = 4

    def on_move(self, x, y):
        """
        Records the mouse's path between clicks, so playback moves the cursor the way
        you did instead of teleporting from spot to spot. Stored in reference space like
        clicks, and only while the cursor is over the game.
        """
        try:
            if not self.is_recording:
                return
            now = time.time()
            if now - getattr(self, "_last_move_time", 0.0) < self.MOVE_SAMPLE_SECONDS:
                return
            rel_x, rel_y = x - config.CLIENT_ORIGIN[0], y - config.CLIENT_ORIGIN[1]
            if config.CLIENT_SIZE is not None and not (
                    0 <= rel_x < config.CLIENT_SIZE[0] and 0 <= rel_y < config.CLIENT_SIZE[1]):
                return
            ref_x, ref_y = config.to_reference(rel_x, rel_y)
            last = getattr(self, "_last_move_pos", None)
            if last and abs(ref_x - last[0]) + abs(ref_y - last[1]) < self.MOVE_MIN_PIXELS:
                return
            self._last_move_time = now
            self._last_move_pos = (ref_x, ref_y)
            self.actions.append({"time": now - self.start_time, "type": "move", "pos": (ref_x, ref_y)})
        except Exception:
            pass

    def on_press(self, key):
        try:
            # Start/stop the bot (Disabled while recording to prevent overlaps)
            if key_matches(key, self.start_stop_key):
                if not self.is_recording and self.f7_callback:
                    self.f7_callback()
                elif self.is_recording:
                    print(f"[Warning] Stop recording ({self.record_key.upper()}) before "
                          f"starting playback ({self.start_stop_key.upper()})!")
                return

            # Record Toggle
            if key_matches(key, self.record_key):
                if not self.is_recording:
                    self.start_recording()
                else:
                    self.stop_recording()
                return

            # Autoclicker toggle - works everywhere, recording or not, same as the
            # other two: it's a background anti-AFK feature, not part of a recording.
            if key_matches(key, self.autoclicker_key):
                if self.autoclicker_callback:
                    self.autoclicker_callback()
                return

            if not self.is_recording:
                return

            # Number Keys 1-6 with Debounce (Prevents timeline flooding)
            if hasattr(key, 'char') and key.char in ['1', '2', '3', '4', '5', '6']:
                current_time = time.time()

                # Only record if the key changed, or if 0.5s has passed since the last press
                if current_time - self.last_key_time > 0.5 or self.last_key != key.char:
                    elapsed = current_time - self.start_time
                    self.actions.append({"time": elapsed, "type": "key", "value": key.char})
                    print(f"[Record] Selected Unit {key.char} at {elapsed:.2f}s")

                    self.last_key = key.char
                    self.last_key_time = current_time
                return

            # W/A/S/D + Space: recorded as part of THIS SAME timeline, not a separate
            # macro - if you walk to your placement spot before placing units, that
            # walk is just the start of the recording. Played back the same way
            # modules.movement_recorder.play_movement() replays a standalone walk
            # (pydirectinput.keyDown/keyUp - see stage_player.play_preset()), so a
            # macro that starts with movement moves the character first and then
            # places units once it arrives, exactly as recorded.
            move_key = key_name(key)
            if move_key and move_key not in self.held_movement:
                self.held_movement.add(move_key)
                elapsed = time.time() - self.start_time
                self.actions.append({"time": elapsed, "type": "keydown", "key": move_key})
                print(f"[Record] Movement '{move_key}' down at {elapsed:.2f}s")

        except Exception:
            pass

    def on_release(self, key):
        try:
            if not self.is_recording:
                return
            move_key = key_name(key)
            if move_key and move_key in self.held_movement:
                self.held_movement.discard(move_key)
                elapsed = time.time() - self.start_time
                self.actions.append({"time": elapsed, "type": "keyup", "key": move_key})
                print(f"[Record] Movement '{move_key}' up at {elapsed:.2f}s")
        except Exception:
            pass

    def start_recording(self):
        if self.preset_name == config.AUTO_PLAY_PRESET_NAME:
            print("[StageRecorder] Cannot record into 'Auto Play' - select or create a real preset first.")
            return

        # Gamemodes without a map/act (Challenges, Portals) leave these unset, which
        # would otherwise save the timeline to "presets/None_None_<name>.json".
        if not self.current_location or not self.current_variant:
            print("[StageRecorder] Cannot record here - switch to Story or Raids first "
                  "(Challenges/Portals have no preset slot of their own).")
            return

        self.is_recording = True
        self.start_time = time.time()
        self.actions = []
        self.last_key = None
        self._last_move_time = 0.0
        self._last_move_pos = None
        self.held_movement = set()
        print("\n[StageRecorder] 🔴 RECORDING STARTED. Walk there if you need to, then place your units! Press F8 to stop.")

    def stop_recording(self):
        if not self.is_recording:
            return

        self.is_recording = False
        # A movement key still held when F8 stops the recording would otherwise replay
        # as "held forever" - close it off at the moment recording actually ended.
        end_time = time.time() - self.start_time
        for key in sorted(self.held_movement):
            self.actions.append({"time": end_time, "type": "keyup", "key": key})
        self.held_movement = set()
        filename = f"presets/{self.current_location}_{self.current_variant}_{self.preset_name}.json"

        with open(filename, 'w') as f:
            json.dump({
                "location": self.current_location,
                "variant": self.current_variant,
                # Recorded against this reference size, so a preset carries the frame it
                # was authored in rather than leaving a future reader to guess.
                "reference": [config.REFERENCE_WIDTH, config.REFERENCE_HEIGHT],
                "actions": self.actions
            }, f, indent=4)
            
        print(f"\n[StageRecorder] ⏹️ RECORDING SAVED: {len(self.actions)} actions to {filename}.")

    def start(self):
        print(f"[StageRecorder] Background listeners active. "
              f"{self.record_key.upper()} = Record | {self.start_stop_key.upper()} = Play | "
              f"{self.autoclicker_key.upper()} = Autoclicker")
        self.mouse_listener = mouse.Listener(on_click=self.on_click, on_move=self.on_move)
        self.keyboard_listener = keyboard.Listener(on_press=self.on_press, on_release=self.on_release)
        self.mouse_listener.start()
        self.keyboard_listener.start()