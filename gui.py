# gui.py
"""
Modern CustomTkinter control panel for Macro Slop.
Features grouped UI cards, live recording status, and a continuous gameplay loop.
"""
# --- DPI awareness. Must run before ANY other import. -------------------------
# Windows locks a process's DPI awareness the first time certain APIs are touched,
# and refuses to change it afterwards. importing customtkinter builds its
# ScalingTracker, which queries DPI - so doing this after that import can be too
# late, SetProcessDpiAwareness then fails with E_ACCESSDENIED, and the process is
# left DPI-UNAWARE.
#
# Unaware is catastrophic here rather than merely cosmetic, because this app drives
# two APIs that disagree under virtualisation: win32 (SetWindowPos/GetClientRect,
# which Windows feeds scaled coordinates) and mss (which always captures real
# physical pixels). At 100% scaling they agree and nothing shows. At 125%/150% the
# client we believe is 1600x900 is physically 2000x1125, so every template is being
# matched against a frame ~25% larger than it was captured at - nothing matches -
# and every coordinate lands short. That reads exactly like "the templates are
# broken on this machine", which is the wrong thing to go and fix.
#
# The outcome is recorded rather than swallowed: a silent failure here looks like a
# stale-template problem from every other angle, so it has to be in the log.
import ctypes as _ctypes

def _set_dpi_awareness():
    # Try the newer SetProcessDpiAwarenessContext first (Windows 10+), which is more
    # reliable. DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4 (per-monitor v2 aware).
    # This survives longer into the process lifecycle than older APIs.
    try:
        result = _ctypes.windll.user32.SetProcessDpiAwarenessContext(_ctypes.c_int(-4))
        if result:
            return "per-monitor v2 (SetProcessDpiAwarenessContext)"
    except Exception:
        pass

    # Fall back to SetProcessDpiAwareness (Windows 8.1+)
    try:
        _ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        return "per-monitor (shcore)"
    except Exception as e:
        # Last resort: SetProcessDPIAware (system-aware, Windows Vista+)
        try:
            if _ctypes.windll.user32.SetProcessDPIAware():
                return "system-aware (user32)"
            return f"FAILED - all APIs refused ({e})"
        except Exception as e2:
            return f"FAILED - {e2}"

DPI_AWARENESS_RESULT = _set_dpi_awareness()
# ------------------------------------------------------------------------------

import customtkinter as ctk
from tkinter import messagebox
import threading
import atexit
import os
import sys
import ctypes
import json
import win32api
import config
import settings
from ui_motion import wire_button_feel

from vision import clear_template_cache as vision_cache_clear
from modules.stage_recorder import StageRecorder
from modules.stats import SESSION
from modules import health
from modules import notify
from modules import preset_share
from theme import (
    FONT_FAMILY, ACCENT, ACCENT_HOVER, ACCENT_TEXT, BG_APP, BG_CARD, BG_SUNKEN, BG_ELEVATED, BORDER,
    TEXT_PRIMARY, TEXT_MUTED, TEXT_DIM, SUCCESS, SUCCESS_HOVER, DANGER, DANGER_HOVER,
    RADIUS_CARD, RADIUS_CONTROL,
    CARD_STYLE, SCROLLBAR_STYLE, DROPDOWN_STYLE, SWITCH_STYLE, SEGMENTED_STYLE, ACCENT_BUTTON_STYLE,
    CHECKBOX_STYLE, INPUT_DIALOG_STYLE, GAME_SLOT_KEY_COLOR,
)
from input_controller import (
    focus_roblox_window, roblox_is_running,
    pin_roblox_borderless, restore_roblox_window, dock_game_size, set_roblox_topmost,
    recover_orphaned_pin,
)
import modules.challenge_links as challenge_links
import logger
from engine import BotEngine

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# DPI awareness is set at the very top of this file, before customtkinter is
# imported - see _set_dpi_awareness(). Doing it here was too late to be reliable.

# CustomTkinter otherwise multiplies every geometry() and widget dimension by the
# monitor's DPI factor. That is right for a normal app and wrong for this one: the
# game slot has to line up with a Roblox window positioned through win32, which works
# in raw physical pixels only. On a 125%/150% laptop the scaled slot would sit
# somewhere the game isn't, and the docked window would come out ~25-50% larger than
# the size computed here - overflowing screens it was measured to fit. Turning CTk's
# scaling off puts both coordinate systems in the same units.
ctk.deactivate_automatic_dpi_awareness()

NL = '\n'  # literal newline for multi-line label text

# All the relative paths used throughout this app (assets/, presets/,
# movement_presets/, challenge_links.json) assume the working directory is this
# file's own folder. That's already true when launching via `python gui.py` from
# here, but isn't guaranteed once packaged as an exe - double-clicking it, a desktop
# shortcut with a different "Start in" folder, or launching from a different drive
# can all leave the process with an unrelated working directory. Pin it explicitly.
# Nuitka does not set sys.frozen the way PyInstaller does - it defines __compiled__
# on compiled modules instead. Checking only sys.frozen would make a Nuitka build fall
# through to the __file__ branch and resolve assets/, presets/ and the log file
# relative to the wrong directory, so both markers are tested.
_is_bundled = getattr(sys, "frozen", False) or "__compiled__" in globals()
_base_dir = os.path.dirname(sys.executable) if _is_bundled else os.path.dirname(os.path.abspath(__file__))
os.chdir(_base_dir)

# Installed before anything else prints: from here on every print() in every module
# lands in logs/macro_slop.log with a timestamp, and (once the GUI is built) in the
# on-screen log too. Without this the windowed build discards all of it - see logger.py.
_log_path = logger.install(_base_dir)

# Sidebar nav order. Deliberately NOT config.GAMEMODES' own dict order (which is
# story, raids, challenges, expeditions, portals, others) - Portals is meant to
# appear before Expeditions/Others in the sidebar.
NAV_ORDER = ["story", "raids", "challenges", "portals", "expeditions", "others"]

# Cap on lines kept in the on-screen log box (the full history lives in the log file).
LOG_MAX_LINES = 500

# Portal categories that share the walk-to-fish system (config.PORTAL_WALKS) - Summer
# and Sovereign are the same map, so the same recorded walks/fishing spots apply to
# both.
PORTAL_WALK_CATEGORIES = ("summer", "sovereign")

class BotGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Macro Slop")
        # Sized around the game slot: sidebar | game, status/log strip beneath the
        # game. Set properly in _apply_dock_layout() once the real game size is
        # known; this is just a sensible pre-dock size.
        self._game_size = (config.DOCK_GAME_WIDTH, config.DOCK_GAME_HEIGHT)
        # Measured at most once per session - see _calibrate_ui_scale.
        self._ui_scale_checked = False
        self.geometry("1000x760")
        self.resizable(True, True)
        self.configure(fg_color=BG_APP)

        # col 0 = sidebar (brand/nav/window controls, AND - since 2026-09 - the
        # gamemode-specific controls that used to live in their own strip below the
        # game; see _build_content_area()). col 1-2 = the reserved game slot
        # (Roblox is positioned over it - see _reposition_game_over_slot); col 1-3
        # in row 1 is the status/log strip under the game.
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=0)
        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)

        os.makedirs("presets", exist_ok=True)

        self.user_settings = settings.load()
        # Turns GAME_SLOT_KEY_COLOR into a see-through, click-through hole - this is
        # what lets the docked game show through instead of being covered by this
        # window. See _build_game_slot().
        if config.DOCK_ENABLED:
            try:
                self.attributes("-transparentcolor", GAME_SLOT_KEY_COLOR)
            except Exception as e:
                print(f"[gui] Transparent slot unavailable ({e}); the game will be "
                      f"raised above this window instead.")
        self.attributes("-alpha", self.user_settings["window_opacity"])
        self.topmost_var = ctk.BooleanVar(value=self.user_settings["always_on_top"])
        self.attributes("-topmost", self.topmost_var.get())

        # Owns the four gamemode run loops, the Never Stop supervisor, and run-end
        # reporting - see engine.py. This BotGUI is its `ui`: the engine calls back
        # into the small set of methods below (log, set_phase, focus_and_pin,
        # calibrate_ui_scale, report_unrecognised_screen, reset_buttons,
        # get_user_settings) rather than touching any Tkinter widget itself.
        self.engine = BotEngine(self)
        self._last_recorder_status = None
        self._last_preset_values = None
        self.active_page_key = "story"

        # Initialize background recorder with F7 Callback
        self.recorder = StageRecorder(f7_callback=self._handle_f7_toggle)
        self.recorder.start()

        self._build_sidebar()
        self._build_game_slot()
        self._build_content_area()
        self._build_status_bar()

        # Only now that log_box exists can print() output be shown on screen. Anything
        # printed before this point still reached the log file, just not the GUI.
        logger.set_gui_sink(self._log_from_print)
        if _log_path:
            self.log(f"Logging to {_log_path}")
        self._log_display_environment()

        self._apply_notification_settings(self.user_settings.get("discord_webhook", ""))
        if notify.is_configured():
            self.log("Discord notifications are on.")

        # Before anything else touches Roblox: if the last session died without undoing
        # its pin, that window is still borderless and always-on-top right now.
        if recover_orphaned_pin():
            self.log("Roblox was left borderless by a previous session - title bar restored.")

        self._on_nav_click(self.active_page_key)
        self._check_recorder_status()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Drops Roblox into the slot as soon as the app launches, rather than only
        # once Start is pressed - the embedded layout is meant to be what you see the
        # whole time the macro is open, not something that appears and then vanishes
        # every time the bot stops. Retries every 2s until Roblox is found (so it's
        # fine to launch the macro first); stops retrying once it succeeds.
        self._docked = False
        self._dock_embedded = True
        self._move_job = None
        self._last_pinned_rect = None
        # Belt and braces on top of _on_close(): that only fires when the window is
        # closed via its X. Any other exit - destroy(), an unhandled exception, the
        # interpreter just ending - would otherwise leave Roblox borderless AND stuck
        # always-on-top, which is a genuinely annoying state to hand back to someone
        # (no title bar to drag, floating over everything). atexit covers all of those.
        atexit.register(self._undock_game_quietly)
        if config.DOCK_ENABLED:
            self.bind("<Configure>", self._on_window_moved)
            self.after(500, self._try_startup_dock)
        else:
            # No game panel to reserve room for: collapse the slot column so the
            # sidebar and controls fill the window on their own. Roblox is pinned to
            # the reference size as its own window when the bot starts (see
            # _focus_and_pin), which is what keeps config.SCALE at 1.0.
            self.game_slot.grid_remove()
            self.grid_columnconfigure(1, minsize=0, weight=0)

    def _undock_game(self):
        """
        Gives Roblox back: normal chrome, out of the always-on-top band, and this
        window returned to whatever the user's own always-on-top preference was.
        """
        set_roblox_topmost(False)
        restore_roblox_window()
        try:
            self.attributes("-topmost", self.topmost_var.get())
        except Exception:
            pass

    def _undock_game_quietly(self):
        """
        atexit variant: same cleanup, but must never raise. By the time this runs the
        Tk window may already be torn down, so anything touching it can throw - and an
        exception in an atexit handler would just print a traceback over whatever the
        user was doing without undoing anything.
        """
        if not config.DOCK_ENABLED or not self._docked or not self._dock_embedded:
            return
        try:
            set_roblox_topmost(False)
            restore_roblox_window()
        except Exception:
            pass

    def _try_startup_dock(self):
        if self.engine.running:
            return
        if not roblox_is_running():
            self.game_slot_hint.configure(text="Waiting for Roblox…")
            self.after(2000, self._try_startup_dock)
            return

        client_size = self._apply_dock_layout()
        if client_size is None:
            self.after(2000, self._try_startup_dock)
            return
        self._docked = True
        self.log(f"Roblox docked at {client_size[0]}x{client_size[1]}.")

    def _build_sidebar(self):
        # Compact: this sits beside the game rather than in a full-width window, so
        # every px it gives back is a px the game panel gets to keep.
        self.sidebar_frame = ctk.CTkFrame(
            self, width=config.DOCK_SIDEBAR_WIDTH, corner_radius=0, fg_color=BG_CARD
        )
        self.sidebar_frame.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self.sidebar_frame.pack_propagate(False)

        brand_row = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        brand_row.pack(fill="x", padx=10, pady=(12, 2))

        self.brand_dot = ctk.CTkLabel(
            brand_row, text="", width=8, height=8, corner_radius=4, fg_color=ACCENT
        )
        self.brand_dot.pack(side="left", padx=(0, 6))

        self.brand_label = ctk.CTkLabel(
            brand_row, text="MACRO SLOP",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"), text_color=TEXT_PRIMARY
        )
        self.brand_label.pack(side="left")

        ctk.CTkLabel(
            self.sidebar_frame, text="GAMEMODES", font=ctk.CTkFont(family=FONT_FAMILY, size=9, weight="bold"),
            text_color=TEXT_DIM
        ).pack(anchor="w", padx=10, pady=(10, 4))

        self.nav_buttons = {}
        for key in NAV_ORDER:
            label = config.GAMEMODES[key]["label"]
            btn = ctk.CTkButton(
                self.sidebar_frame, text=label, anchor="w", corner_radius=RADIUS_CONTROL, height=28,
                fg_color="transparent", hover_color=BG_ELEVATED,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                command=lambda k=key: self._on_nav_click(k)
            )
            btn.pack(fill="x", padx=6, pady=1)
            self.nav_buttons[key] = btn

        # Exit lives here because the panel runs fullscreen and therefore has no title
        # bar to close from. Without it the only way out is Task Manager - which is the
        # single exit that CANNOT hand Roblox its title bar back, because it kills the
        # process before any cleanup runs. The missing button was the actual cause of
        # "Roblox is stuck borderless every time".
        # Top-packed, not bottom-packed. Bottom-packing put them past the window's
        # bottom edge - the grid rows plus their padding come to slightly more than the
        # screen height, so anything anchored to the frame's bottom is clipped and the
        # Quit button was invisible, which is the exact button whose absence forces the
        # Task Manager kill this is meant to prevent.
        ctk.CTkLabel(
            self.sidebar_frame, text="WINDOW", font=ctk.CTkFont(family=FONT_FAMILY, size=9, weight="bold"),
            text_color=TEXT_DIM
        ).pack(anchor="w", padx=10, pady=(18, 4))

        # Hands Roblox back its normal window WITHOUT quitting - for when you just want
        # to play, and as a rescue if the game is ever left chrome-less.
        self.release_btn = ctk.CTkButton(
            self.sidebar_frame, text="⇱  Release Roblox", anchor="w", corner_radius=RADIUS_CONTROL,
            height=28, fg_color="transparent", hover_color=BG_ELEVATED, text_color=TEXT_MUTED,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12), command=self._release_roblox
        )
        self.release_btn.pack(fill="x", padx=6, pady=1)

        self.settings_btn = ctk.CTkButton(
            self.sidebar_frame, text="⚙  Settings", anchor="w", corner_radius=RADIUS_CONTROL, height=28,
            fg_color="transparent", hover_color=BG_ELEVATED, text_color=TEXT_MUTED,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12), command=self._open_settings_dialog
        )
        self.settings_btn.pack(fill="x", padx=6, pady=1)

        # The only way out of a fullscreen window with no title bar. Without it the
        # exit is Task Manager, which kills the process before it can undo the pin and
        # leaves Roblox borderless.
        self.quit_btn = ctk.CTkButton(
            self.sidebar_frame, text="✕  Quit", anchor="w", corner_radius=RADIUS_CONTROL, height=28,
            fg_color="transparent", hover_color=DANGER, text_color=TEXT_MUTED,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12), command=self._on_close
        )
        self.quit_btn.pack(fill="x", padx=6, pady=1)

    def _release_roblox(self):
        """Gives Roblox its title bar back without closing the macro."""
        set_roblox_topmost(False)
        if restore_roblox_window():
            self.log("Roblox released - it's a normal window again. Press Start to re-dock it.")
        else:
            self.log("Roblox wasn't docked, so there was nothing to release.")

    def _apply_nav_highlight(self):
        for key, btn in self.nav_buttons.items():
            active = key == self.active_page_key
            enabled = config.GAMEMODES[key]["enabled"]
            if active:
                text_color = ACCENT_TEXT
            elif enabled:
                text_color = TEXT_MUTED
            else:
                text_color = TEXT_DIM
            btn.configure(
                fg_color=ACCENT if active else "transparent",
                hover_color=ACCENT_HOVER if active else BG_ELEVATED,
                text_color=text_color,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold" if active else "normal"),
            )

    def _build_game_slot(self):
        """
        The literal hole the game shows through.

        This frame is painted in GAME_SLOT_KEY_COLOR, which is registered as the
        window's transparent colour key - so Windows renders those pixels as fully
        transparent AND click-through. The borderless Roblox window sits behind this
        window, aligned to this frame, and shows through the hole.

        Doing it with a colour key rather than z-order is deliberate. The game is a
        separate top-level window (it can't be a child - reparenting is what Roblox's
        anti-cheat kills), so keeping it visually "inside" this one would otherwise
        mean constantly re-raising it above this window every time this window got
        clicked, dragged or alt-tabbed to - a fight against the window manager that
        is lost the moment any of those happen faster than the re-raise. A hole can't
        lose that fight: there is simply nothing painted there to cover the game.
        Click-through is a bonus - clicking the game area reaches the game directly.

        The hint text only reads through while the game isn't there yet, which makes
        "waiting for Roblox" visible instead of looking like a rendering bug.
        """
        slot_color = GAME_SLOT_KEY_COLOR if config.DOCK_ENABLED else BG_CARD
        self.game_slot = ctk.CTkFrame(self, corner_radius=0, fg_color=slot_color)
        # Spans both content columns: the game owns the full width above the strip,
        # and columns 1/2 only need to sum to that width - see _size_window_for_game.
        self.game_slot.grid(row=0, column=1, columnspan=2, sticky="nsew")
        self.game_slot.grid_propagate(False)

        self.game_slot_hint = ctk.CTkLabel(
            self.game_slot, text="Waiting for Roblox…", fg_color=GAME_SLOT_KEY_COLOR,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13), text_color=TEXT_MUTED
        )
        self.game_slot_hint.place(relx=0.5, rely=0.5, anchor="center")

        # Explicitly painted filler for whatever width is left over beside the game.
        # An EMPTY grid cell is not safe here: the window makes one specific colour
        # transparent, and anything not positively covered by an opaque widget can end
        # up showing the desktop straight through the panel. Covering it costs nothing
        # and removes the whole failure mode.
        self.slot_filler = ctk.CTkFrame(self, corner_radius=0, fg_color=BG_APP)
        self.slot_filler.grid(row=0, column=3, sticky="nsew")

    def _build_content_area(self):
        # Lives in the sidebar column now, not its own grid cell under the game - see
        # the 2026-09 layout change. sidebar_frame already spans the full window
        # height (rowspan=2) and only used the top portion of it for the brand/
        # gamemode-nav/window-controls section built in _build_sidebar(); packing
        # this below that fills the REST of that same column instead of needing any
        # new space, which is what keeps the window and the reserved Roblox slot
        # (DOCK_GAME_WIDTH/HEIGHT, panel_size()) exactly the sizes they already were -
        # nothing here changes either one.
        #
        # No drop-shadow frame here any more (there used to be one, floating this
        # card over the transparent game-slot background) - sidebar_frame is already
        # an opaque BG_CARD surface, so a second card-colored layer on top of it
        # would just be a redundant, indistinguishable rectangle rather than a
        # visible seam.
        #
        # Still scrollable, which is what makes this fit at all: the per-gamemode
        # control stacks (Story's preset manager + macro recorder card, especially)
        # were sized for a short, wide strip, and here they run tall and narrow
        # instead - scrolling covers whatever doesn't fit the sidebar's actual
        # height on a given screen, same safety net the old strip relied on.
        self.content_frame = ctk.CTkScrollableFrame(self.sidebar_frame, fg_color="transparent",
                                                    **SCROLLBAR_STYLE)
        self.content_frame.pack(fill="both", expand=True, padx=0, pady=(8, 0))

        self.page_header_label = ctk.CTkLabel(
            self.content_frame, text="STORY", font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"), text_color=TEXT_DIM
        )
        self.page_header_label.pack(anchor="w", padx=15, pady=(10, 0))

        self.grid_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        self.grid_frame.pack(fill="x", padx=10, pady=5)
        self.grid_frame.columnconfigure(0, weight=1)
        self.grid_frame.columnconfigure(1, weight=1)

        # Story controls
        self.map_dropdown = ctk.CTkOptionMenu(self.grid_frame, command=self._on_map_changed, **DROPDOWN_STYLE)
        self.act_dropdown = ctk.CTkOptionMenu(self.grid_frame, command=self._on_act_changed, **DROPDOWN_STYLE)

        # Raid controls
        self.raid_dropdown = ctk.CTkOptionMenu(self.grid_frame, command=self._on_raid_changed, **DROPDOWN_STYLE)

        # Shown when the selected gamemode isn't wired up yet
        self.not_implemented_label = ctk.CTkLabel(
            self.grid_frame, text="This gamemode isn't implemented yet.", text_color=TEXT_MUTED
        )

        self.difficulty_label_widget = ctk.CTkLabel(self.grid_frame, text="Difficulty", font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_MUTED)

        self.difficulty_var = ctk.StringVar(value="Normal")
        self.difficulty_selector = ctk.CTkSegmentedButton(
            self.grid_frame, values=["Normal", "Hard"], variable=self.difficulty_var, command=self._on_difficulty_changed,
            **SEGMENTED_STYLE
        )

        # Story-only: climbs Act 1 -> Act 2 -> ... -> Act 5 -> next map's Act 1 on
        # every win via the Victory screen's Next Stage button, instead of repeating
        # the same stage. Per-stage it uses a recorded preset if one exists for that
        # exact map+act (same preset name as selected in the dropdown), falling back
        # to the game's own Auto Play for any stage that doesn't have one - see
        # _run_bot(). A loss still repeats the current stage (existing
        # MAX_CONSECUTIVE_DEFEATS retry) before the whole run gives up.
        self.auto_next_var = ctk.BooleanVar(value=False)
        self.auto_next_switch = ctk.CTkSwitch(
            self.grid_frame, text="Auto Next (climb acts on victory)",
            variable=self.auto_next_var, font=ctk.CTkFont(family=FONT_FAMILY, size=12), **SWITCH_STYLE
        )

        # Challenges controls
        self.challenge_frame = ctk.CTkFrame(self.grid_frame, fg_color="transparent")
        self.challenge_category_vars = {
            "regular": ctk.BooleanVar(value=True),
            "daily": ctk.BooleanVar(value=False),
            "weekly": ctk.BooleanVar(value=False),
        }
        checks_row = ctk.CTkFrame(self.challenge_frame, fg_color="transparent")
        checks_row.pack(fill="x")
        ctk.CTkCheckBox(checks_row, text="Regular", variable=self.challenge_category_vars["regular"], command=self._on_challenge_options_changed, **CHECKBOX_STYLE).pack(side="left", padx=(0, 10))
        ctk.CTkCheckBox(checks_row, text="Daily", variable=self.challenge_category_vars["daily"], command=self._on_challenge_options_changed, **CHECKBOX_STYLE).pack(side="left", padx=(0, 10))
        ctk.CTkCheckBox(checks_row, text="Weekly", variable=self.challenge_category_vars["weekly"], command=self._on_challenge_options_changed, **CHECKBOX_STYLE).pack(side="left")

        ctk.CTkLabel(self.challenge_frame, text="Regular Challenge(s)", font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_MUTED).pack(anchor="w", pady=(8, 0))
        self.regular_mode_var = ctk.StringVar(value="All")
        self.regular_mode_selector = ctk.CTkSegmentedButton(
            self.challenge_frame, values=["All", "1", "2", "3"], variable=self.regular_mode_var, command=self._on_challenge_options_changed,
            **SEGMENTED_STYLE
        )
        self.regular_mode_selector.pack(fill="x", pady=(2, 8))

        self.btn_challenge_links = ctk.CTkButton(self.challenge_frame, text="Configure Challenge Presets...", command=self._open_challenge_links_dialog, **ACCENT_BUTTON_STYLE)
        wire_button_feel(self.btn_challenge_links)
        self.btn_challenge_links.pack(fill="x")

        self.loop_challenges_var = ctk.BooleanVar(value=True)
        self.loop_challenges_switch = ctk.CTkSwitch(
            self.challenge_frame, text="Loop until stopped (re-check cooldowns)",
            variable=self.loop_challenges_var, font=ctk.CTkFont(family=FONT_FAMILY, size=12), **SWITCH_STYLE
        )
        self.loop_challenges_switch.pack(anchor="w", pady=(10, 0))

        # Portals controls
        self.portal_frame = ctk.CTkFrame(self.grid_frame, fg_color="transparent")
        ctk.CTkLabel(self.portal_frame, text="Portal", font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_MUTED).pack(anchor="w")
        self.portal_category_dropdown = ctk.CTkOptionMenu(self.portal_frame, command=self._on_portal_options_changed, **DROPDOWN_STYLE)
        self.portal_category_dropdown.pack(fill="x", pady=(2, 8))

        # PORTAL_WALK_CATEGORIES only (Summer/Sovereign): where to go once the match
        # loads, and whether to fish there.
        self.portal_walk_label = ctk.CTkLabel(self.portal_frame, text="Walk To", font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_MUTED)
        walk_values = ["Spawn"] + [v["label"] for v in config.PORTAL_WALKS.values()]
        self.portal_walk_var = ctk.StringVar(value="Spawn")
        self.portal_walk_selector = ctk.CTkOptionMenu(
            self.portal_frame, values=walk_values, variable=self.portal_walk_var,
            command=self._on_portal_options_changed, **DROPDOWN_STYLE
        )

        self.portal_fish_var = ctk.BooleanVar(value=False)
        self.portal_fish_switch = ctk.CTkSwitch(
            self.portal_frame, text="Fish once there (Auto Rod required)",
            variable=self.portal_fish_var, font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            command=self._on_portal_options_changed, **SWITCH_STYLE
        )

        self.portal_avoid_traitless_var = ctk.BooleanVar(value=True)
        self.portal_avoid_traitless_switch = ctk.CTkSwitch(
            self.portal_frame, text="Avoid Traitless rewards (hover-check)",
            variable=self.portal_avoid_traitless_var, font=ctk.CTkFont(family=FONT_FAMILY, size=12), **SWITCH_STYLE
        )
        self.portal_avoid_traitless_switch.pack(anchor="w", pady=(8, 8))

        self.portal_stage_info_label = ctk.CTkLabel(
            self.portal_frame,
            text="Auto Play only for now. Each match searches the portal list by name and "
                 "takes the top result, so it always enters the best one you own and keeps "
                 "farming until a loss (continue-after-defeat isn't wired up yet).",
            # 240, not the old 600: this now wraps inside the sidebar column (~270px
            # usable, see _build_content_area()) rather than the old 640px-wide strip.
            font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_MUTED, justify="left", wraplength=240
        )
        self.portal_stage_info_label.pack(anchor="w", fill="x")

        # Expeditions controls. The unit macro is the normal preset manager + recorder
        # below (one shared "expedition/payload" slot for all four maps).
        self.expedition_frame = ctk.CTkFrame(self.grid_frame, fg_color="transparent")
        ctk.CTkLabel(self.expedition_frame, text="Expedition", font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_MUTED).pack(anchor="w")
        self.expedition_dropdown = ctk.CTkOptionMenu(
            self.expedition_frame, values=[v["label"] for v in config.EXPEDITIONS.values() if v["enabled"]],
            command=self._on_expedition_changed, **DROPDOWN_STYLE
        )
        self.expedition_dropdown.pack(fill="x", pady=(2, 8))

        ctk.CTkLabel(self.expedition_frame, text="Difficulty", font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_MUTED).pack(anchor="w")
        self.expedition_difficulty_var = ctk.StringVar(value="3")
        self.expedition_difficulty_selector = ctk.CTkSegmentedButton(
            self.expedition_frame, values=["1", "2", "3"], variable=self.expedition_difficulty_var,
            command=self._on_expedition_options_changed, **SEGMENTED_STYLE
        )
        self.expedition_difficulty_selector.pack(fill="x", pady=(2, 8))

        ctk.CTkLabel(self.expedition_frame, text="Material to farm", font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_MUTED).pack(anchor="w")
        self.expedition_material_var = ctk.StringVar(value="")
        self.expedition_material_dropdown = ctk.CTkOptionMenu(
            self.expedition_frame,
            values=[m["label"] for m in config.EXPEDITION_MATERIALS.values()] + ["Random"],
            variable=self.expedition_material_var, command=self._on_expedition_options_changed, **DROPDOWN_STYLE
        )
        self.expedition_material_dropdown.pack(fill="x", pady=(2, 8))

        saved_zoom = self.user_settings.get("expedition_zoom_out_steps")
        self.expedition_zoom_steps = (int(saved_zoom) if isinstance(saved_zoom, (int, float))
                                      else config.EXPEDITION_CAMERA_ZOOM_OUT_STEPS)
        self.expedition_zoom_label = ctk.CTkLabel(
            self.expedition_frame, text=f"Camera zoom-out: {self.expedition_zoom_steps}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_MUTED
        )
        self.expedition_zoom_label.pack(anchor="w")
        self.expedition_zoom_slider = ctk.CTkSlider(
            self.expedition_frame, from_=0, to=config.CAMERA_ZOOM_OUT_STEPS,
            number_of_steps=config.CAMERA_ZOOM_OUT_STEPS, command=self._on_expedition_zoom_changed,
            fg_color=BG_SUNKEN, progress_color=ACCENT, button_color=ACCENT, button_hover_color=ACCENT_HOVER
        )
        self.expedition_zoom_slider.set(self.expedition_zoom_steps)
        self.expedition_zoom_slider.pack(fill="x", pady=(2, 8))

        ctk.CTkLabel(
            self.expedition_frame,
            text="No Auto Play here: create a preset with 'New' below and record your unit "
                 "placement (F8) in an expedition. One macro covers all four maps. Random "
                 "skips route picking. Changing the zoom-out means re-recording the macro.",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_MUTED, justify="left", wraplength=240
        ).pack(anchor="w", fill="x")

        # Preset Manager UI - Story/Raids only, shown/hidden by _refresh_page_content
        self.preset_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        self.preset_frame.columnconfigure(0, weight=1)

        self.preset_dropdown = ctk.CTkOptionMenu(self.preset_frame, command=self._on_preset_selected, **DROPDOWN_STYLE)
        self.preset_dropdown.grid(row=0, column=0, padx=(5, 2), sticky="ew")

        self.btn_new = ctk.CTkButton(self.preset_frame, text="New", width=54, command=self._new_preset, **ACCENT_BUTTON_STYLE)
        wire_button_feel(self.btn_new)
        self.btn_new.grid(row=0, column=1, padx=2)

        self.btn_ren = ctk.CTkButton(self.preset_frame, text="Rename", width=64, command=self._rename_preset, **ACCENT_BUTTON_STYLE)
        wire_button_feel(self.btn_ren)
        self.btn_ren.grid(row=0, column=2, padx=2)

        self.btn_del = ctk.CTkButton(
            self.preset_frame, text="Delete", width=64, corner_radius=RADIUS_CONTROL,
            fg_color=DANGER, hover_color=DANGER_HOVER, hover=False, command=self._delete_preset
        )
        wire_button_feel(self.btn_del)
        self.btn_del.grid(row=0, column=3, padx=(2, 5))

        # Copy/Export/Import are occasional actions, not everyday ones like New/Rename/
        # Delete above - an equal three-way split keeps them from outweighing the row
        # they're secondary to (Copy used to have its own full-width row to itself).
        self.preset_action_row = ctk.CTkFrame(self.preset_frame, fg_color="transparent")
        self.preset_action_row.grid(row=1, column=0, columnspan=4, padx=5, pady=(6, 0), sticky="ew")

        self.btn_copy = ctk.CTkButton(self.preset_action_row, text="Copy...", command=self._open_copy_dialog, **ACCENT_BUTTON_STYLE)
        wire_button_feel(self.btn_copy)
        self.btn_copy.pack(side="left", expand=True, fill="x", padx=(0, 3))

        self.btn_export = ctk.CTkButton(self.preset_action_row, text="Export...", command=self._export_preset, **ACCENT_BUTTON_STYLE)
        wire_button_feel(self.btn_export)
        self.btn_export.pack(side="left", expand=True, fill="x", padx=3)

        self.btn_import = ctk.CTkButton(self.preset_action_row, text="Import...", command=self._import_preset, **ACCENT_BUTTON_STYLE)
        wire_button_feel(self.btn_import)
        self.btn_import.pack(side="left", expand=True, fill="x", padx=(3, 0))

        self.preset_info_label = ctk.CTkLabel(
            self.content_frame, text="No preset selected", font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_MUTED
        )

        # Macro Recorder card - Story/Raids only, shown/hidden by _refresh_page_content
        self.frame_record = ctk.CTkFrame(self.content_frame, **CARD_STYLE)

        ctk.CTkLabel(self.frame_record, text="MACRO RECORDER (IN-GAME)", font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"), text_color=TEXT_DIM).pack(anchor="w", padx=15, pady=(10, 0))

        self.record_info = ctk.CTkLabel(self.frame_record, text="Press F8 to Record | Press F7 to Start Loop", font=ctk.CTkFont(family=FONT_FAMILY, size=13), text_color=TEXT_PRIMARY)
        self.record_info.pack(pady=(5, 0))

        self.record_status = ctk.CTkLabel(self.frame_record, text="Status: IDLE", font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"), text_color=TEXT_MUTED)
        self.record_status.pack(pady=(0, 10))

        self.frame_record_buttons = ctk.CTkFrame(self.frame_record, fg_color="transparent")
        self.frame_record_buttons.pack(fill="x", padx=15, pady=(0, 15))

        self.record_btn = ctk.CTkButton(
            self.frame_record_buttons, text="●︎  RECORD (F8)", fg_color=SUCCESS, hover_color=SUCCESS_HOVER, hover=False,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"), height=36, corner_radius=RADIUS_CONTROL, command=self._start_recording_clicked
        )
        wire_button_feel(self.record_btn)
        self.record_btn.pack(side="left", expand=True, fill="x", padx=(0, 5))

        self.stop_record_btn = ctk.CTkButton(
            self.frame_record_buttons, text="■︎  STOP RECORDING (F8)", fg_color=DANGER, hover_color=DANGER_HOVER, hover=False,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"), height=36, corner_radius=RADIUS_CONTROL, state="disabled", command=self._stop_recording_clicked
        )
        wire_button_feel(self.stop_record_btn)
        self.stop_record_btn.pack(side="right", expand=True, fill="x", padx=(5, 0))

    def _build_status_bar(self):
        # The WHOLE strip under the game now (columns 1-3, not just the right half):
        # phase, stats and the log. Widened to reclaim the space the gamemode
        # controls used to occupy in column 1 before they moved into the sidebar -
        # see _build_content_area() - which is pure upside for the log, whose lines
        # are long and used to wrap in the narrower column.
        self.status_bar_shadow = ctk.CTkFrame(self, corner_radius=RADIUS_CARD, fg_color="#000000")
        self.status_bar_shadow.grid(row=1, column=1, columnspan=3, sticky="nsew", padx=(14, 10), pady=(10, 8))

        self.status_bar = ctk.CTkFrame(self, height=config.DOCK_STRIP_HEIGHT, **CARD_STYLE)
        self.status_bar.grid(row=1, column=1, columnspan=3, sticky="nsew", padx=(10, 14), pady=(8, 10))
        self.status_bar.lift()
        self.status_bar.grid_propagate(False)
        self.status_bar.grid_columnconfigure(0, weight=1)
        self.status_bar.grid_rowconfigure(3, weight=1)

        self.phase_label = ctk.CTkLabel(
            self.status_bar, text="PHASE: IDLE", font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"), text_color=TEXT_MUTED
        )
        self.phase_label.grid(row=0, column=0, padx=10, pady=(6, 0), sticky="w")

        # Live session counters, opposite the phase. A farming run is judged on
        # throughput over hours, which was previously only recoverable by reading back
        # through the whole log.
        self.stats_label = ctk.CTkLabel(
            self.status_bar, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=TEXT_MUTED, anchor="e"
        )
        self.stats_label.grid(row=0, column=1, padx=10, pady=(6, 0), sticky="e")

        # Armed summary: what will actually run/record if you press F7/F8 right now
        self.armed_label = ctk.CTkLabel(
            self.status_bar, text="Armed: —", font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
            text_color=ACCENT, anchor="w", justify="left", wraplength=900
        )
        self.armed_label.grid(row=1, column=0, columnspan=2, padx=10, pady=(0, 4), sticky="ew")

        self.frame_buttons = ctk.CTkFrame(self.status_bar, fg_color="transparent")
        self.frame_buttons.grid(row=2, column=0, columnspan=2, padx=10, pady=(0, 5), sticky="ew")

        self.start_btn = ctk.CTkButton(
            self.frame_buttons, text="▶︎  START (F7)", fg_color=SUCCESS, hover_color=SUCCESS_HOVER, hover=False,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"), height=30, corner_radius=RADIUS_CONTROL, command=self.start_bot
        )
        wire_button_feel(self.start_btn)
        self.start_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))

        self.stop_btn = ctk.CTkButton(
            self.frame_buttons, text="■︎  STOP (F7)", fg_color=DANGER, hover_color=DANGER_HOVER, hover=False,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"), height=30, corner_radius=RADIUS_CONTROL, state="disabled", command=self.stop_bot
        )
        wire_button_feel(self.stop_btn)
        self.stop_btn.pack(side="right", expand=True, fill="x", padx=(4, 0))

        self.log_box = ctk.CTkTextbox(
            self.status_bar, font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=BG_SUNKEN, text_color="#00ff66", corner_radius=RADIUS_CONTROL,
            border_width=1, border_color=BORDER
        )
        self.log_box.grid(row=3, column=0, columnspan=2, padx=10, pady=(0, 8), sticky="nsew")
        self.log_box.configure(state="disabled")

    def _on_close(self):
        self.engine.user_stop_requested = True
        config.STOP_REQUESTED = True
        settings.save(self.user_settings)

        # Give the bot thread a moment to notice STOP_REQUESTED and unwind through its
        # own finally blocks before the process dies. Those blocks are what release a
        # held mouse button (anchor_camera's right-drag) or held movement keys
        # (play_movement's WASD) - killing the process mid-hold leaves them stuck down
        # in Roblox, where a held right button means the camera keeps following the
        # cursor and every click afterwards is swallowed until you click manually.
        #
        # Bounded rather than an open join(): the thread is a daemon and some waits poll
        # on a 2s interval, so a stuck one must not be able to hold the window open.
        if self.engine.bot_thread is not None and self.engine.bot_thread.is_alive():
            self.engine.bot_thread.join(timeout=3.0)

        # Same reason the mouse button and movement keys get released above: closing
        # while docked would otherwise leave Roblox borderless, with no title bar to
        # drag it by AND stuck always-on-top, long after this app is gone.
        if config.DOCK_ENABLED:
            try:
                self._undock_game()
            except Exception:
                pass

        try:
            self.recorder.mouse_listener.stop()
            self.recorder.keyboard_listener.stop()
        except Exception:
            pass
        self.destroy()

    # --- Settings Dialog ---
    def _apply_notification_settings(self, webhook_url, notify_vars=None):
        """
        Pushes the webhook and category toggles into the notifier.

        Called both when the Settings dialog closes and at startup, so a URL saved in a
        previous session is live from launch rather than only after someone opens
        Settings again.
        """
        self.user_settings["discord_webhook"] = (webhook_url or "").strip()
        if notify_vars:
            for key, var in notify_vars.items():
                self.user_settings[key] = bool(var.get())

        notify.configure(self.user_settings["discord_webhook"], {
            "lifecycle": self.user_settings.get("notify_run_lifecycle", True),
            "problems": self.user_settings.get("notify_problems", True),
            "milestones": self.user_settings.get("notify_milestones", True),
            "match": self.user_settings.get("notify_every_match", False),
        })

    def _open_settings_dialog(self):
        dialog = ctk.CTkToplevel(self, fg_color=BG_APP)
        dialog.title("Settings")
        dialog.geometry("380x820")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        def _section(title):
            ctk.CTkLabel(
                dialog, text=title, font=ctk.CTkFont(family=FONT_FAMILY, size=10, weight="bold"), text_color=TEXT_DIM
            ).pack(anchor="w", padx=20, pady=(18, 8))

        # --- Appearance ---
        _section("APPEARANCE")

        swatch_row = ctk.CTkFrame(dialog, fg_color="transparent")
        swatch_row.pack(anchor="w", padx=20)

        current_accent = self.user_settings["accent"]
        swatch_buttons = {}

        restart_note = ctk.CTkLabel(
            dialog, text="Restart Macro Slop to apply the new accent color.",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_MUTED, justify="left", wraplength=320
        )

        def _select_accent(key):
            self.user_settings["accent"] = key
            for k, b in swatch_buttons.items():
                b.configure(border_width=3 if k == key else 0)
            restart_note.pack(anchor="w", padx=20, pady=(8, 0))

        for key, data in settings.ACCENT_PALETTES.items():
            b = ctk.CTkButton(
                swatch_row, text="", width=32, height=32, corner_radius=16,
                fg_color=data["accent"], hover_color=data["accent"],
                border_width=3 if key == current_accent else 0, border_color=TEXT_PRIMARY,
                command=lambda k=key: _select_accent(k)
            )
            b.pack(side="left", padx=(0, 8))
            swatch_buttons[key] = b

        # --- Window ---
        _section("WINDOW")

        ctk.CTkSwitch(
            dialog, text="Always on top", variable=self.topmost_var, command=self._toggle_topmost,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12), **SWITCH_STYLE
        ).pack(anchor="w", padx=20, pady=(0, 14))

        opacity_header = ctk.CTkFrame(dialog, fg_color="transparent")
        opacity_header.pack(fill="x", padx=20)
        ctk.CTkLabel(opacity_header, text="Window opacity", font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=TEXT_PRIMARY).pack(side="left")
        opacity_value_label = ctk.CTkLabel(
            opacity_header, text=f"{int(self.user_settings['window_opacity'] * 100)}%",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_MUTED
        )
        opacity_value_label.pack(side="right")

        def _on_opacity(value):
            self.user_settings["window_opacity"] = round(value, 2)
            self.attributes("-alpha", value)
            opacity_value_label.configure(text=f"{int(value * 100)}%")

        opacity_slider = ctk.CTkSlider(
            dialog, from_=0.5, to=1.0, number_of_steps=10, command=_on_opacity,
            fg_color=BG_SUNKEN, progress_color=ACCENT, button_color=ACCENT, button_hover_color=ACCENT_HOVER
        )
        opacity_slider.set(self.user_settings["window_opacity"])
        opacity_slider.pack(fill="x", padx=20, pady=(4, 16))

        # --- Behavior ---
        _section("BEHAVIOR")

        confirm_var = ctk.BooleanVar(value=self.user_settings["confirm_delete"])

        def _on_confirm_delete():
            self.user_settings["confirm_delete"] = confirm_var.get()

        ctk.CTkSwitch(
            dialog, text="Confirm before deleting presets", variable=confirm_var, command=_on_confirm_delete,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12), **SWITCH_STYLE
        ).pack(anchor="w", padx=20, pady=(0, 14))

        # --- Run behavior ---
        _section("RUN BEHAVIOR")

        defeats_var = ctk.BooleanVar(value=bool(self.user_settings.get("stop_after_defeats", True)))
        defeats_count = {"value": max(1, min(20, int(self.user_settings.get("max_defeats",
                                                                             config.MAX_CONSECUTIVE_DEFEATS))))}

        def _defeats_text():
            n = defeats_count["value"]
            return f"Stop after {n} defeat{'s' if n != 1 else ''} in a row"

        def _on_defeats_toggle():
            self.user_settings["stop_after_defeats"] = bool(defeats_var.get())
            defeats_slider.configure(state="normal" if defeats_var.get() else "disabled")
            self.engine.apply_run_behavior_settings()

        def _on_defeats_count(value):
            defeats_count["value"] = int(round(value))
            self.user_settings["max_defeats"] = defeats_count["value"]
            defeats_switch.configure(text=_defeats_text())
            self.engine.apply_run_behavior_settings()

        defeats_switch = ctk.CTkSwitch(
            dialog, text=_defeats_text(), variable=defeats_var, command=_on_defeats_toggle,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12), **SWITCH_STYLE
        )
        defeats_switch.pack(anchor="w", padx=20, pady=(0, 4))

        defeats_slider = ctk.CTkSlider(
            dialog, from_=1, to=20, number_of_steps=19, command=_on_defeats_count,
            fg_color=BG_SUNKEN, progress_color=ACCENT, button_color=ACCENT, button_hover_color=ACCENT_HOVER
        )
        defeats_slider.set(defeats_count["value"])
        defeats_slider.configure(state="normal" if defeats_var.get() else "disabled")
        defeats_slider.pack(fill="x", padx=20, pady=(0, 12))

        never_stop_var = ctk.BooleanVar(value=bool(self.user_settings.get("never_stop", False)))

        def _on_never_stop():
            self.user_settings["never_stop"] = bool(never_stop_var.get())

        ctk.CTkSwitch(
            dialog, text="Never stop (restart after a problem)", variable=never_stop_var,
            command=_on_never_stop, font=ctk.CTkFont(family=FONT_FAMILY, size=12), **SWITCH_STYLE
        ).pack(anchor="w", padx=20, pady=(0, 4))

        ctk.CTkLabel(
            dialog, text="When a run stops on its own (stuck, Roblox closed, a button never found, a crash), "
                         "wait and start it again: 30s, then longer each time up to 5 min. Never overrides "
                         "your Stop button or the defeat limit above. Both apply to the running bot straight away.",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10), text_color=TEXT_DIM, justify="left", wraplength=330
        ).pack(anchor="w", padx=20, pady=(0, 6))

        # --- Discord notifications ---
        _section("DISCORD NOTIFICATIONS")

        ctk.CTkLabel(
            dialog, text="Discord › Edit Channel › Integrations › Webhooks › New Webhook "
                         "› Copy Webhook URL, then paste it here.",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_MUTED,
            justify="left", wraplength=320,
        ).pack(anchor="w", padx=20, pady=(0, 6))

        webhook_var = ctk.StringVar(value=self.user_settings.get("discord_webhook", ""))
        webhook_entry = ctk.CTkEntry(
            dialog, textvariable=webhook_var, placeholder_text="https://discord.com/api/webhooks/...",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11), width=320,
        )
        webhook_entry.pack(anchor="w", padx=20, pady=(0, 4))

        ctk.CTkLabel(
            dialog, text="Treat this URL like a password - anyone who has it can post to that channel.",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10), text_color=TEXT_DIM,
            justify="left", wraplength=320,
        ).pack(anchor="w", padx=20, pady=(0, 10))

        notify_vars = {}
        for key, label in (
            ("notify_run_lifecycle", "Run started / finished / stopped"),
            ("notify_problems", "Problems (stuck, disconnected, Roblox closed)"),
            ("notify_milestones", "Milestones (soul counts, targets reached)"),
            ("notify_every_match", "Every match result (very chatty)"),
        ):
            var = ctk.BooleanVar(value=self.user_settings.get(key, True))
            notify_vars[key] = var
            ctk.CTkSwitch(
                dialog, text=label, variable=var,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11), **SWITCH_STYLE
            ).pack(anchor="w", padx=20, pady=(0, 6))

        test_row = ctk.CTkFrame(dialog, fg_color="transparent")
        test_row.pack(anchor="w", padx=20, pady=(4, 0))
        test_result = ctk.CTkLabel(
            test_row, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_MUTED
        )

        def _send_test():
            # Applied before testing, so the button tests what is actually typed in the
            # box rather than whatever was saved the last time the dialog was closed.
            self._apply_notification_settings(webhook_var.get(), notify_vars)
            if not notify.is_configured():
                test_result.configure(text="Paste a webhook URL first.", text_color=DANGER)
            elif notify.send("Macro Slop is connected to this channel.",
                             category="lifecycle", title="Test message", good=True):
                test_result.configure(text="Sent - check your channel.", text_color=SUCCESS)
            else:
                test_result.configure(text="Not sent (is that category switched off?)", text_color=DANGER)

        test_btn = ctk.CTkButton(test_row, text="Send test message", command=_send_test, width=150,
                                 font=ctk.CTkFont(family=FONT_FAMILY, size=11))
        wire_button_feel(test_btn)
        test_btn.pack(side="left")
        test_result.pack(side="left", padx=(10, 0))

        def _close():
            self._apply_notification_settings(webhook_var.get(), notify_vars)
            settings.save(self.user_settings)
            dialog.destroy()

        done_btn = ctk.CTkButton(dialog, text="Done", command=_close, **ACCENT_BUTTON_STYLE)
        wire_button_feel(done_btn)
        done_btn.pack(side="bottom", fill="x", padx=20, pady=20)
        dialog.protocol("WM_DELETE_WINDOW", _close)

    # --- Preset Manager Functions ---
    def _new_preset(self):
        dialog = ctk.CTkInputDialog(text="Enter new preset name:", title="New Preset", **INPUT_DIALOG_STYLE)
        name = dialog.get_input()
        if name:
            name = preset_share.sanitize_preset_name(name)
            location_key = self._get_current_location_key()
            variant_key = self._get_current_variant_key()
            filepath = f"presets/{location_key}_{variant_key}_{name}.json"

            if preset_share.is_reserved_name(name):
                self.log("'Auto Play' is a reserved preset name. Choose a different name.")
                return

            if not os.path.exists(filepath):
                with open(filepath, 'w') as f:
                    json.dump({"location": location_key, "variant": variant_key, "actions": []}, f)
                self.log(f"Created new preset profile: {name}")
                self._refresh_presets_only()
                self.preset_dropdown.set(name)
                self._on_preset_selected(name)
            else:
                self.log(f"A preset named '{name}' already exists here.")

    def _export_preset(self):
        current_name = self.preset_dropdown.get()
        if current_name in ("No Presets Found", config.AUTO_PLAY_PRESET_NAME):
            self.log("ERROR: Select a real preset to export first.")
            return

        location_key = self._get_current_location_key()
        variant_key = self._get_current_variant_key()
        dest_path = preset_share.export_stage_preset(location_key, variant_key, current_name)
        if dest_path:
            self.log(f"Exported '{current_name}' to {dest_path}")

    def _import_preset(self):
        result = preset_share.import_stage_preset(self)
        if result:
            location_key, variant_key, name = result
            self.log(f"Imported preset '{name}' for {location_key}/{variant_key}.")
            self._refresh_presets_only()
            self.preset_dropdown.set(name)
            self._on_preset_selected(name)

    def _rename_preset(self):
        current_name = self.preset_dropdown.get()
        if current_name in ("No Presets Found", config.AUTO_PLAY_PRESET_NAME): return

        dialog = ctk.CTkInputDialog(text="Enter new name:", title="Rename Preset", **INPUT_DIALOG_STYLE)
        new_name = dialog.get_input()
        if new_name:
            new_name = preset_share.sanitize_preset_name(new_name)
            if preset_share.is_reserved_name(new_name):
                self.log("'Auto Play' is a reserved preset name. Choose a different name.")
                return
            location_key = self._get_current_location_key()
            variant_key = self._get_current_variant_key()

            old_path = f"presets/{location_key}_{variant_key}_{current_name}.json"
            new_path = f"presets/{location_key}_{variant_key}_{new_name}.json"

            if os.path.exists(old_path):
                os.rename(old_path, new_path)
                self.log(f"Renamed preset to: {new_name}")
                self._refresh_presets_only()
                self.preset_dropdown.set(new_name)
                self._on_preset_selected(new_name)

    def _delete_preset(self):
        current_name = self.preset_dropdown.get()
        if current_name in ("No Presets Found", config.AUTO_PLAY_PRESET_NAME): return

        if self.user_settings["confirm_delete"] and not messagebox.askyesno(
            "Delete Preset",
            f"Permanently delete preset '{current_name}'?\nThis cannot be undone.",
            parent=self
        ):
            return

        location_key = self._get_current_location_key()
        variant_key = self._get_current_variant_key()
        filepath = f"presets/{location_key}_{variant_key}_{current_name}.json"

        if os.path.exists(filepath):
            os.remove(filepath)
            self.log(f"Deleted preset: {current_name}")
            self._refresh_presets_only()

    def _open_copy_dialog(self):
        """
        Copies the currently selected preset to other acts of the same map (they share
        the same layout, so the recorded positions still line up) or to a different map
        entirely (a starting point - positions likely won't line up there).
        """
        current_name = self.preset_dropdown.get()
        if current_name in ("No Presets Found", config.AUTO_PLAY_PRESET_NAME):
            self.log("ERROR: Select a real preset to copy first.")
            return

        source_map = self._get_current_map_key()
        source_act = self._get_current_act_key()
        source_path = f"presets/{source_map}_{source_act}_{current_name}.json"
        if not os.path.exists(source_path):
            self.log(f"ERROR: Preset file not found: {source_path}")
            return

        dialog = ctk.CTkToplevel(self, fg_color=BG_APP)
        dialog.title(f"Copy '{current_name}'")
        dialog.geometry("320x440")
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(
            dialog, text=f"Copy preset:\n'{current_name}'",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"), justify="center"
        ).pack(pady=(15, 10))

        ctk.CTkLabel(dialog, text="Target Map", font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_MUTED).pack(anchor="w", padx=15)
        map_values = [v["label"] for k, v in config.MAPS.items() if v["enabled"]]
        source_map_label = config.MAPS[source_map]["label"]
        map_var = ctk.StringVar(value=source_map_label)
        map_dropdown = ctk.CTkOptionMenu(dialog, values=map_values, variable=map_var, **DROPDOWN_STYLE)
        map_dropdown.pack(fill="x", padx=15, pady=(0, 10))

        ctk.CTkLabel(dialog, text="Target Acts", font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_MUTED).pack(anchor="w", padx=15)
        checks_frame = ctk.CTkScrollableFrame(dialog, height=180, fg_color=BG_SUNKEN, **SCROLLBAR_STYLE)
        checks_frame.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        act_check_vars = {}

        def _rebuild_act_checks(*_):
            for widget in checks_frame.winfo_children():
                widget.destroy()
            act_check_vars.clear()

            target_map_key = self._map_label_to_key(map_var.get())
            if not target_map_key:
                return
            for act_key, act_data in config.MAPS[target_map_key]["acts"].items():
                if not act_data["enabled"]:
                    continue
                is_source_slot = (target_map_key == source_map and act_key == source_act)
                var = ctk.BooleanVar(value=not is_source_slot)
                cb = ctk.CTkCheckBox(checks_frame, text=act_data["label"], variable=var, state="disabled" if is_source_slot else "normal", **CHECKBOX_STYLE)
                cb.pack(anchor="w", pady=2)
                act_check_vars[act_key] = var

        map_var.trace_add("write", _rebuild_act_checks)
        _rebuild_act_checks()

        def _do_copy():
            target_map_key = self._map_label_to_key(map_var.get())
            if not target_map_key:
                return

            targets = [
                act_key for act_key, var in act_check_vars.items()
                if var.get() and not (target_map_key == source_map and act_key == source_act)
            ]
            if not targets:
                self.log("No target acts selected - nothing copied.")
                dialog.destroy()
                return

            existing = [t for t in targets if os.path.exists(f"presets/{target_map_key}_{t}_{current_name}.json")]
            if existing and not messagebox.askyesno(
                "Overwrite Existing Presets?",
                f"{len(existing)} target preset(s) already exist and will be overwritten. Continue?",
                parent=dialog
            ):
                return

            with open(source_path, "r") as f:
                data = f.read()

            for act_key in targets:
                with open(f"presets/{target_map_key}_{act_key}_{current_name}.json", "w") as f:
                    f.write(data)

            self.log(f"Copied '{current_name}' to {len(targets)} target(s) on {config.MAPS[target_map_key]['label']}.")
            dialog.destroy()
            self._refresh_presets_only()

        copy_btn = ctk.CTkButton(dialog, text="Copy", command=_do_copy, **ACCENT_BUTTON_STYLE)
        wire_button_feel(copy_btn)
        copy_btn.pack(fill="x", padx=15, pady=(5, 15))

    def _map_label_to_key(self, label):
        for k, v in config.MAPS.items():
            if v["label"] == label:
                return k
        return None

    # --- Challenges Functions ---
    def _build_challenge_sequence(self):
        """Ordered list of challenge slot keys to run, from the checkboxes + Regular sub-mode."""
        sequence = []
        if self.challenge_category_vars["regular"].get():
            mode = self.regular_mode_var.get()
            if mode == "All":
                sequence += ["regular_1", "regular_2", "regular_3"]
            else:
                sequence.append(f"regular_{mode}")
        if self.challenge_category_vars["daily"].get():
            sequence.append("daily")
        if self.challenge_category_vars["weekly"].get():
            sequence.append("weekly")
        return sequence

    def _list_all_presets(self):
        """Every existing Story preset file, as (map_key, act_key, preset_name, display_label)."""
        results = []
        try:
            files = os.listdir("presets")
        except FileNotFoundError:
            return results

        for filename in files:
            if not filename.endswith(".json"):
                continue
            for map_key, map_data in config.MAPS.items():
                map_prefix = f"{map_key}_"
                if not filename.startswith(map_prefix):
                    continue
                rest = filename[len(map_prefix):-5]
                for act_key, act_data in map_data["acts"].items():
                    act_prefix = f"{act_key}_"
                    if rest.startswith(act_prefix):
                        preset_name = rest[len(act_prefix):]
                        label = f"{map_data['label']} / {act_data['label']} / {preset_name}"
                        results.append((map_key, act_key, preset_name, label))
                        break
                break
        return results

    def _open_challenge_links_dialog(self):
        all_presets = self._list_all_presets()
        option_labels = [config.AUTO_PLAY_PRESET_NAME] + [p[3] for p in all_presets]
        label_to_combo = {p[3]: (p[0], p[1], p[2]) for p in all_presets}

        dialog = ctk.CTkToplevel(self, fg_color=BG_APP)
        dialog.title("Configure Challenge Presets")
        dialog.geometry("420x500")
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(
            dialog,
            text="Pick which existing Story preset each challenge should use.\n"
                 "Acts are randomized in-game, so re-check these periodically.",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_MUTED, justify="center"
        ).pack(padx=15, pady=(15, 10))

        scroll = ctk.CTkScrollableFrame(dialog, height=340, fg_color=BG_SUNKEN, **SCROLLBAR_STYLE)
        scroll.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        slot_vars = {}
        for slot_key, slot_data in config.CHALLENGE_SLOTS.items():
            link = challenge_links.get_link(slot_key)
            current_label = config.AUTO_PLAY_PRESET_NAME
            if link.get("preset") and link["preset"] != config.AUTO_PLAY_PRESET_NAME:
                for p_map, p_act, p_name, p_label in all_presets:
                    if p_map == link.get("map") and p_act == link.get("act") and p_name == link["preset"]:
                        current_label = p_label
                        break

            ctk.CTkLabel(scroll, text=slot_data["label"], font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"), text_color=TEXT_PRIMARY).pack(anchor="w", pady=(8, 2))
            var = ctk.StringVar(value=current_label if current_label in option_labels else config.AUTO_PLAY_PRESET_NAME)
            dd = ctk.CTkOptionMenu(scroll, values=option_labels, variable=var, **DROPDOWN_STYLE)
            dd.pack(fill="x", pady=(0, 5))
            slot_vars[slot_key] = var

        def _save_all():
            for slot_key, var in slot_vars.items():
                chosen = var.get()
                if chosen == config.AUTO_PLAY_PRESET_NAME or chosen not in label_to_combo:
                    challenge_links.set_link(slot_key, None, None, config.AUTO_PLAY_PRESET_NAME)
                else:
                    map_key, act_key, preset_name = label_to_combo[chosen]
                    challenge_links.set_link(slot_key, map_key, act_key, preset_name)
            self.log("Challenge preset links saved.")
            dialog.destroy()

        save_btn = ctk.CTkButton(dialog, text="Save", command=_save_all, **ACCENT_BUTTON_STYLE)
        wire_button_feel(save_btn)
        save_btn.pack(fill="x", padx=15, pady=(5, 15))

    # --- Macro Recorder Functions ---
    def _start_recording_clicked(self):
        if self.engine.running:
            self.log("ERROR: Cannot start recording while the loop is running.")
            return
        if self.recorder.is_recording:
            return
        if self.preset_dropdown.get() == config.AUTO_PLAY_PRESET_NAME:
            self.log("ERROR: Select or create a real preset before recording (not 'Auto Play').")
            return
        if not self._get_current_location_key() or not self._get_current_variant_key():
            self.log("ERROR: Challenges/Portals have no preset slot - switch to Story or Raids to record.")
            return
        self.recorder.start_recording()
        self.log("Recording started.")

    def _stop_recording_clicked(self):
        if not self.recorder.is_recording:
            return
        self.recorder.stop_recording()
        self.log("Recording stopped and saved.")
        self._refresh_presets_only()

    # --- UI Updaters ---
    def _check_recorder_status(self):
        is_recording = self.recorder.is_recording
        status_snapshot = (is_recording, self.engine.running)

        # Only touch the widgets when something actually changed - reconfiguring a CTk
        # button/label every 500ms even with the same values causes a visible flicker.
        if status_snapshot != self._last_recorder_status:
            self._last_recorder_status = status_snapshot
            if is_recording:
                self.record_status.configure(text="Status: RECORDING...", text_color="#ff1744")
                self.record_btn.configure(state="disabled")
                self.stop_record_btn.configure(state="normal")
            else:
                self.record_status.configure(text="Status: IDLE", text_color=TEXT_MUTED)
                self.record_btn.configure(state="disabled" if self.engine.running else "normal")
                self.stop_record_btn.configure(state="disabled")

        if not self.engine.running and not is_recording:
            self._refresh_presets_only()

        self.after(500, self._check_recorder_status)

    def _on_nav_click(self, gamemode_key):
        self.active_page_key = gamemode_key
        self._apply_nav_highlight()
        self._refresh_page_content()

    def _refresh_page_content(self):
        """Swaps the location/variant controls and difficulty options for the selected gamemode."""
        gamemode_key = self._get_current_gamemode_key()
        gamemode_data = config.GAMEMODES.get(gamemode_key)

        self.page_header_label.configure(text=gamemode_data["label"].upper() if gamemode_data else "SETTINGS")

        self.map_dropdown.grid_remove()
        self.act_dropdown.grid_remove()
        self.raid_dropdown.grid_remove()
        self.not_implemented_label.grid_remove()
        self.difficulty_label_widget.grid_remove()
        self.difficulty_selector.grid_remove()
        self.auto_next_switch.grid_remove()
        self.challenge_frame.grid_remove()
        self.portal_frame.grid_remove()
        self.expedition_frame.grid_remove()

        # The normal preset manager (dropdown + New/Rename/Delete/Copy) and the Macro
        # Recorder card only apply to Story/Raids, which record+play a preset
        # directly. Challenges reuses Story presets via the link dialog instead, and
        # Portals/Expeditions/Others don't use this preset system at all - both
        # sections default to hidden here and are only re-shown in the Story/Raids
        # branches below.
        self.preset_frame.pack(fill="x", padx=10, pady=(0, 5))
        self.preset_info_label.pack(anchor="w", padx=15, pady=(0, 8))
        self.frame_record.pack_forget()

        if not gamemode_data or not gamemode_data["enabled"]:
            self.not_implemented_label.grid(row=0, column=0, columnspan=2, padx=5, pady=20, sticky="ew")
            self.btn_copy.pack_forget()
            self.preset_frame.pack_forget()
            self.preset_info_label.pack_forget()
            self._sync_recorder_target()
        elif gamemode_key == "story":
            # Stacked rather than side-by-side: the two dropdowns sharing one row
            # suited the old 640px-wide strip, but at the sidebar's ~300px a map
            # name like "Fairy King Forest" needs the full row's width to stay
            # readable instead of being squeezed to illegible in half of it.
            self.map_dropdown.grid(row=0, column=0, columnspan=2, padx=5, pady=5, sticky="ew")
            self.act_dropdown.grid(row=1, column=0, columnspan=2, padx=5, pady=(0, 5), sticky="ew")
            self.difficulty_label_widget.configure(text="Difficulty")
            self.difficulty_label_widget.grid(row=2, column=0, columnspan=2, padx=5, pady=(5, 0), sticky="w")
            self.difficulty_selector.configure(values=["Normal", "Hard"])
            self.difficulty_var.set("Normal")
            self.difficulty_selector.grid(row=3, column=0, columnspan=2, padx=5, pady=(2, 5), sticky="ew")
            self.auto_next_switch.grid(row=4, column=0, columnspan=2, padx=5, pady=(6, 5), sticky="w")
            self.btn_copy.pack(side="left", expand=True, fill="x", padx=(0, 3))
            self.frame_record.pack(fill="x", padx=10, pady=(10, 5))
            self._refresh_maps()
        elif gamemode_key == "raids":
            self.raid_dropdown.grid(row=0, column=0, columnspan=2, padx=5, pady=5, sticky="ew")
            self.difficulty_label_widget.configure(text="Raid Difficulty")
            self.difficulty_label_widget.grid(row=1, column=0, columnspan=2, padx=5, pady=(5, 0), sticky="w")
            self.difficulty_selector.configure(values=["1", "2", "3"])
            self.difficulty_var.set("1")
            self.difficulty_selector.grid(row=2, column=0, columnspan=2, padx=5, pady=(2, 5), sticky="ew")
            self.btn_copy.pack_forget()  # Copy dialog is Story-specific for now (map/act concepts)
            self.frame_record.pack(fill="x", padx=10, pady=(10, 5))
            self._refresh_raids()
        elif gamemode_key == "challenges":
            self.challenge_frame.grid(row=0, column=0, columnspan=2, padx=5, pady=5, sticky="ew")
            self.btn_copy.pack_forget()
            self.preset_frame.pack_forget()
            self.preset_info_label.pack_forget()
        elif gamemode_key == "portals":
            self.portal_frame.grid(row=0, column=0, columnspan=2, padx=5, pady=5, sticky="ew")
            self.btn_copy.pack_forget()
            self.preset_frame.pack_forget()
            self.preset_info_label.pack_forget()
            self._refresh_portal_categories()
        elif gamemode_key == "expeditions":
            self.expedition_frame.grid(row=0, column=0, columnspan=2, padx=5, pady=5, sticky="ew")
            self.btn_copy.pack_forget()
            self.frame_record.pack(fill="x", padx=10, pady=(10, 5))
            self._refresh_expeditions()
            self._sync_recorder_target()

        self._update_armed_label()

    def _refresh_maps(self):
        map_values = [f"{v['label']}" for k, v in config.MAPS.items() if v["enabled"]]
        self.map_dropdown.configure(values=map_values)
        if map_values: self.map_dropdown.set(map_values[0])
        self._refresh_acts()

    def _refresh_acts(self):
        map_key = self._get_current_map_key()
        if map_key:
            acts = config.MAPS[map_key]["acts"]
            act_values = [f"{v['label']}" for k, v in acts.items() if v["enabled"]]
            self.act_dropdown.configure(values=act_values)
            if act_values: self.act_dropdown.set(act_values[0])
        self._sync_recorder_target()

    def _refresh_raids(self):
        raid_values = [v["label"] for k, v in config.RAIDS.items() if v["enabled"]]
        self.raid_dropdown.configure(values=raid_values)
        if raid_values: self.raid_dropdown.set(raid_values[0])
        self._sync_recorder_target()

    def _refresh_portal_categories(self):
        portal_values = [v["label"] for k, v in config.PORTALS.items() if v["enabled"]]
        self.portal_category_dropdown.configure(values=portal_values)
        if portal_values: self.portal_category_dropdown.set(portal_values[0])
        self._sync_portal_extra_controls()
        self._update_armed_label()

    def _on_portal_options_changed(self, *_):
        self._sync_portal_extra_controls()
        self._update_armed_label()

    def _sync_portal_extra_controls(self):
        """
        Walk-to-fish only applies to portals in PORTAL_WALK_CATEGORIES (Summer and
        Sovereign share a map) - hide Walk/Fishing for every other portal, and hide
        Fishing specifically when Walk is set to Spawn (no known fishing spot there).
        """
        category_key = self._get_current_portal_category_key()

        if category_key in PORTAL_WALK_CATEGORIES:
            self.portal_walk_label.pack(anchor="w", before=self.portal_stage_info_label)
            self.portal_walk_selector.pack(fill="x", pady=(2, 8), before=self.portal_stage_info_label)
            if self.portal_walk_var.get() != "Spawn":
                self.portal_fish_switch.pack(anchor="w", pady=(0, 8), before=self.portal_stage_info_label)
            else:
                self.portal_fish_switch.pack_forget()
                self.portal_fish_var.set(False)
        else:
            self.portal_walk_label.pack_forget()
            self.portal_walk_selector.pack_forget()
            self.portal_fish_switch.pack_forget()
            self.portal_walk_var.set("Spawn")
            self.portal_fish_var.set(False)

    def _refresh_expeditions(self):
        values = [v["label"] for v in config.EXPEDITIONS.values() if v["enabled"]]
        self.expedition_dropdown.configure(values=values)
        if values and self.expedition_dropdown.get() not in values:
            self.expedition_dropdown.set(values[0])
        self._on_expedition_changed(self.expedition_dropdown.get())

    def _on_expedition_changed(self, choice):
        # Each expedition has its own signature material, so picking a map defaults the
        # material to it - the choice most people want - while leaving it changeable.
        key = self._get_current_expedition_key()
        if key:
            material = config.EXPEDITIONS[key]["material"]
            self.expedition_material_var.set(config.EXPEDITION_MATERIALS[material]["label"])
        self._update_armed_label()

    def _on_expedition_options_changed(self, *_):
        self._update_armed_label()

    def _on_expedition_zoom_changed(self, value):
        steps = int(round(value))
        if steps == self.expedition_zoom_steps:
            return
        self.expedition_zoom_steps = steps
        self.expedition_zoom_label.configure(text=f"Camera zoom-out: {steps}")
        self.user_settings["expedition_zoom_out_steps"] = steps
        settings.save(self.user_settings)
        self._update_armed_label()

    def _get_current_expedition_key(self):
        selected = self.expedition_dropdown.get()
        for k, v in config.EXPEDITIONS.items():
            if v["label"] == selected:
                return k
        return None

    def _get_current_expedition_material_key(self):
        selected = self.expedition_material_var.get()
        for k, v in config.EXPEDITION_MATERIALS.items():
            if v["label"] == selected:
                return k
        return config.EXPEDITION_RANDOM_MATERIAL

    def _get_current_portal_category_key(self):
        selected = self.portal_category_dropdown.get()
        for k, v in config.PORTALS.items():
            if v["label"] == selected: return k
        return None

    def _get_current_portal_walk_key(self):
        """None means Spawn (no walk). Only meaningful for PORTAL_WALK_CATEGORIES."""
        if self._get_current_portal_category_key() not in PORTAL_WALK_CATEGORIES:
            return None
        selected = self.portal_walk_var.get()
        for k, v in config.PORTAL_WALKS.items():
            if v["label"] == selected: return k
        return None

    def _refresh_presets_only(self):
        location_key = self._get_current_location_key()
        variant_key = self._get_current_variant_key()

        if location_key and variant_key:
            prefix = f"{location_key}_{variant_key}_"
            # "Auto Play" is a reserved entry - always available, not backed by a preset file.
            presets = [config.AUTO_PLAY_PRESET_NAME]
            for file in os.listdir("presets"):
                if file.startswith(prefix) and file.endswith(".json"):
                    preset_name = file[len(prefix):-5]
                    presets.append(preset_name)

            # Only reconfigure the dropdown when the list actually changed - this runs every
            # 500ms while idle, and re-setting the same values each tick causes a UI flicker.
            if presets != self._last_preset_values:
                self._last_preset_values = presets
                current = self.preset_dropdown.get()
                self.preset_dropdown.configure(values=presets)
                if current not in presets:
                    self.preset_dropdown.set(presets[0])
                    self._on_preset_selected(presets[0])

        self._update_armed_label()
        self._update_preset_info()

    def _on_map_changed(self, choice):
        self._refresh_acts()  # Act list depends on the map; this also syncs the recorder target.

    def _on_act_changed(self, choice):
        self._sync_recorder_target()

    def _on_raid_changed(self, choice):
        self._sync_recorder_target()

    def _on_difficulty_changed(self, choice):
        if self._get_current_gamemode_key() == "raids":
            self._sync_recorder_target()  # Raid difficulty doubles as the preset variant key
        else:
            self._update_armed_label()

    def _on_challenge_options_changed(self, *_):
        self._update_armed_label()

    def _sync_recorder_target(self):
        self.recorder.current_location = self._get_current_location_key()
        self.recorder.current_variant = self._get_current_variant_key()
        self._refresh_presets_only()

    def _on_preset_selected(self, choice):
        if choice and choice != "No Presets Found":
            self.recorder.preset_name = choice
        self._update_armed_label()
        self._update_preset_info()

    def _update_armed_label(self):
        gamemode_key = self._get_current_gamemode_key()
        gamemode_data = config.GAMEMODES.get(gamemode_key)

        if not gamemode_data or not gamemode_data["enabled"]:
            self.armed_label.configure(text=f"Armed: {self._get_current_gamemode_label()} — not implemented yet")
            return

        if gamemode_key == "challenges":
            sequence = self._build_challenge_sequence()
            labels = [config.CHALLENGE_SLOTS[k]["label"] for k in sequence]
            summary = " → ".join(labels) if labels else "(nothing selected)"
            self.armed_label.configure(text=f"Armed: {summary}")
            return

        if gamemode_key == "expeditions":
            parts = [
                self.expedition_dropdown.get() or "—",
                f"Difficulty {self.expedition_difficulty_var.get()}",
                f"Farm: {self.expedition_material_var.get() or '—'}",
                f"Macro: {self.preset_dropdown.get() or '—'}",
            ]
            self.armed_label.configure(text=f"Armed: {'  →  '.join(parts)}")
            return

        if gamemode_key == "portals":
            parts = [self.portal_category_dropdown.get() or "—"]

            walk_key = self._get_current_portal_walk_key()
            if walk_key:
                walk_label = config.PORTAL_WALKS[walk_key]["label"]
                parts.append(f"Walk: {walk_label}")
                if self.portal_fish_var.get():
                    parts.append("Fish")
            parts.append("Auto Play")

            self.armed_label.configure(text=f"Armed: {'  →  '.join(parts)}")
            return

        if gamemode_key == "story":
            location_label = self.map_dropdown.get() or "—"
            variant_label = self.act_dropdown.get() or "—"
        else:
            location_label = self.raid_dropdown.get() or "—"
            variant_label = f"Difficulty {self.difficulty_var.get()}" if self.difficulty_var.get() else "—"

        difficulty_label = self.difficulty_var.get() or "—"
        preset_label = self.preset_dropdown.get() or "—"

        if gamemode_key == "story":
            self.armed_label.configure(text=f"Armed: {location_label}  →  {variant_label}  →  {difficulty_label}  →  {preset_label}")
        else:
            self.armed_label.configure(text=f"Armed: {location_label}  →  {variant_label}  →  {preset_label}")

    def _update_preset_info(self):
        current_name = self.preset_dropdown.get()
        location_key = self._get_current_location_key()
        variant_key = self._get_current_variant_key()

        if not current_name or current_name == "No Presets Found" or not location_key or not variant_key:
            self.preset_info_label.configure(text="No preset selected")
            return

        if current_name == config.AUTO_PLAY_PRESET_NAME:
            self.preset_info_label.configure(text="Uses the game's built-in Auto Play toggle - no recording needed")
            return

        filepath = f"presets/{location_key}_{variant_key}_{current_name}.json"
        try:
            with open(filepath, "r") as f:
                data = json.load(f)
            actions = data.get("actions", [])
            if not actions:
                self.preset_info_label.configure(text="0 actions recorded — press F8 in-game to record")
            else:
                duration = max(a["time"] for a in actions)
                self.preset_info_label.configure(text=f"{len(actions)} actions • {duration:.1f}s timeline")
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            self.preset_info_label.configure(text="Preset file unreadable")

    def _toggle_topmost(self):
        self.user_settings["always_on_top"] = self.topmost_var.get()
        self.attributes("-topmost", self.topmost_var.get())

    def _get_current_map_key(self):
        return self._map_label_to_key(self.map_dropdown.get())

    def _get_current_act_key(self):
        selected = self.act_dropdown.get()
        map_key = self._get_current_map_key()
        if map_key:
            for k, v in config.MAPS[map_key]["acts"].items():
                if v["label"] == selected: return k
        return None

    def _get_current_raid_key(self):
        selected = self.raid_dropdown.get()
        for k, v in config.RAIDS.items():
            if v["label"] == selected: return k
        return None

    def _get_current_gamemode_key(self):
        return self.active_page_key

    def _get_current_gamemode_label(self):
        data = config.GAMEMODES.get(self.active_page_key)
        return data["label"] if data else self.active_page_key

    def _get_current_location_key(self):
        """The map (Story) or raid (Raids) currently selected - the first preset-scoping dimension."""
        gamemode_key = self._get_current_gamemode_key()
        if gamemode_key == "story":
            return self._get_current_map_key()
        if gamemode_key == "raids":
            return self._get_current_raid_key()
        if gamemode_key == "expeditions":
            return config.EXPEDITION_PRESET_LOCATION
        return None

    def _get_current_variant_key(self):
        """The act (Story) or difficulty (Raids) currently selected - the second preset-scoping dimension."""
        gamemode_key = self._get_current_gamemode_key()
        if gamemode_key == "story":
            return self._get_current_act_key()
        if gamemode_key == "raids":
            return self.difficulty_var.get() or None
        if gamemode_key == "expeditions":
            return config.EXPEDITION_PRESET_VARIANT
        return None

    def _tick_stats(self):
        """
        Refreshes the session counters once a second while a run is active.

        Polled rather than pushed because elapsed time and the per-match average move
        on their own, with no event to hang an update on - and it keeps every counter
        bump in the bot thread a plain increment with no UI marshalling of its own.
        """
        if self.engine.running:
            self.stats_label.configure(text=SESSION.line())
            self.after(1000, self._tick_stats)

    def set_phase(self, text, color="#a8a8a8"):
        def _update():
            self.phase_label.configure(text=f"PHASE: {text}", text_color=color)
        self.after(0, _update)

    def log(self, message, to_file=True):
        # to_file=False only for lines arriving FROM the stdout tee - those are already
        # in the file, and writing them back would duplicate every one of them.
        if to_file:
            logger.log_to_file(message)

        def _write():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", f"> {message}\n")

            # Trimmed to a fixed number of lines. A run going for days pushes a steady
            # stream of per-poll diagnostics through here, and an untrimmed textbox
            # keeps every one of them in memory for the life of the process.
            line_count = int(self.log_box.index("end-1c").split(".")[0])
            if line_count > LOG_MAX_LINES:
                self.log_box.delete("1.0", f"{line_count - LOG_MAX_LINES}.0")

            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.after(0, _write)

    def get_user_settings(self):
        """
        engine.py's `ui.get_user_settings()` - see engine.BotEngine's own docstring
        for the full protocol this class implements for it.
        """
        return self.user_settings

    def _log_from_print(self, line):
        """
        Sink for logger.py's stdout tee: every print() from every module arrives here.
        Routed through log() so it picks up the same self.after marshalling - these
        lines arrive on the bot thread, not the UI thread. to_file=False because the
        file logger is where this line just came from.
        """
        self.log(line, to_file=False)

    def _handle_f7_toggle(self):
        if self.engine.running:
            self.after(0, self.stop_bot)
        else:
            self.after(0, self.start_bot)

    def start_bot(self):
        if self.recorder.is_recording:
            self.log("ERROR: Cannot start the loop while recording (F8) is active.")
            return

        gamemode_key = self._get_current_gamemode_key()
        gamemode_data = config.GAMEMODES.get(gamemode_key)
        if not gamemode_data or not gamemode_data["enabled"]:
            label = self._get_current_gamemode_label()
            self.log(f"ERROR: '{label}' isn't implemented yet.")
            messagebox.showwarning(
                "Not Implemented",
                f"'{label}' isn't wired up yet - only Story and Raids work right now.",
                parent=self
            )
            return

        if gamemode_key == "challenges":
            sequence = self._build_challenge_sequence()
            if not sequence:
                self.log("ERROR: Select at least one challenge category to run.")
                messagebox.showwarning(
                    "No Challenges Selected",
                    "Check at least one of Regular / Daily / Weekly first.",
                    parent=self
                )
                return
            slot_links = {slot_key: challenge_links.get_link(slot_key) for slot_key in sequence}
            loop = self.loop_challenges_var.get()
            target, args = self.engine.run_challenges, (sequence, slot_links, loop)
        elif gamemode_key == "portals":
            category_key = self._get_current_portal_category_key()
            if not category_key:
                self.log("ERROR: No portal category selected.")
                messagebox.showwarning("No Portal Selected", "Pick a portal first.", parent=self)
                return
            walk_key = self._get_current_portal_walk_key()
            fish_enabled = walk_key is not None and self.portal_fish_var.get()
            target, args = self.engine.run_portal, (category_key, walk_key, fish_enabled,
                                                     self.portal_avoid_traitless_var.get())
        elif gamemode_key == "expeditions":
            expedition_key = self._get_current_expedition_key()
            if not expedition_key:
                self.log("ERROR: No expedition selected.")
                messagebox.showwarning("No Expedition Selected", "Pick an expedition first.", parent=self)
                return
            preset_name = self.preset_dropdown.get()
            preset_path = (f"presets/{config.EXPEDITION_PRESET_LOCATION}_"
                           f"{config.EXPEDITION_PRESET_VARIANT}_{preset_name}.json")
            if preset_name in ("", "No Presets Found", config.AUTO_PLAY_PRESET_NAME) or not os.path.exists(preset_path):
                self.log("ERROR: Expeditions has no Auto Play - click 'New', then record your unit "
                         "placement with F8 inside an expedition.")
                messagebox.showwarning(
                    "No Unit Macro",
                    "Expeditions has no Auto Play.\nClick 'New', then record your unit placement "
                    "with F8 while inside an expedition.",
                    parent=self
                )
                return
            target, args = self.engine.run_expedition, (
                expedition_key, self.expedition_difficulty_var.get(),
                self._get_current_expedition_material_key(), preset_name,
                self.expedition_zoom_steps,
            )
        else:
            location_key = self._get_current_location_key()
            variant_key = self._get_current_variant_key()
            # Story: Normal/Hard, lowercased for the click_difficulty() lookup. Raids: the
            # difficulty IS the variant_key already ("1"/"2"/"3"), so just reuse it as-is.
            difficulty = self.difficulty_var.get().lower() if gamemode_key == "story" else variant_key
            preset_name = self.preset_dropdown.get()
            auto_next = gamemode_key == "story" and self.auto_next_var.get()

            if preset_name == "No Presets Found":
                self.log("ERROR: No preset created. Click 'New' to make one!")
                messagebox.showwarning(
                    "No Preset Selected",
                    "There's no preset to run here.\nClick 'New' to record one first.",
                    parent=self
                )
                return

            target, args = self.engine.run_bot, (gamemode_key, location_key, variant_key, difficulty, preset_name, auto_next)

        # Re-reads the same GUI getters the branches above already validated with,
        # rather than threading gamemode-specific locals (preset_name, category_key,
        # ...) out of whichever single branch actually set them - they're cheap,
        # side-effect-free state reads, so asking again here is simpler and safer
        # than trying to keep 3 different branches' worth of variables in scope.
        # None (rather than an empty list) tells engine.start() to skip the "Run
        # started" message entirely regardless of whether notifications are on.
        fields = None
        if notify.is_configured():
            fields = [("Gamemode", self._get_current_gamemode_label())]
            if gamemode_key == "portals":
                fields.append(("Portal", self._get_current_portal_category_key() or "-"))
            elif gamemode_key == "challenges":
                fields.append(("Challenges", "looping" if self.loop_challenges_var.get() else "one pass"))
            elif gamemode_key == "expeditions":
                fields.append(("Expedition", self.expedition_dropdown.get() or "-"))
                fields.append(("Difficulty", self.expedition_difficulty_var.get()))
                fields.append(("Farming", self.expedition_material_var.get() or "-"))
                fields.append(("Macro", self.preset_dropdown.get()))
                fields.append(("Camera zoom-out", str(self.expedition_zoom_steps)))
            else:
                fields.append(("Preset", self.preset_dropdown.get()))
                if gamemode_key == "story":
                    fields.append(("Difficulty", self.difficulty_var.get()))
                    if self.auto_next_var.get():
                        fields.append(("Auto Next", "on"))

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.engine.start(target, args, notify_fields=fields)
        self._tick_stats()  # must follow engine.start() - the ticker stops itself once engine.running goes False

    def stop_bot(self):
        self.engine.stop()

    def _log_display_environment(self):
        """
        Records DPI awareness and the monitor's real size at startup.

        This is the first thing to check when the bot works on one machine and
        recognises nothing on another: at anything other than 100% scaling, a
        DPI-unaware process gets scaled coordinates from win32 while mss keeps
        capturing physical pixels, and every template silently misses. Without this
        line, that is indistinguishable from stale templates - and the machine it
        happens on is usually the one you can't debug directly.
        """
        try:
            dpi = ctypes.windll.user32.GetDpiForSystem()
            scaling = f"{round(dpi / 96 * 100)}%"
        except Exception:
            dpi, scaling = "?", "?"

        screen_w = win32api.GetSystemMetrics(0)
        screen_h = win32api.GetSystemMetrics(1)

        self.log(f"Display: {screen_w}x{screen_h}, DPI {dpi} ({scaling} scaling), "
                 f"awareness: {DPI_AWARENESS_RESULT}")

        if isinstance(DPI_AWARENESS_RESULT, str) and DPI_AWARENESS_RESULT.startswith("FAILED"):
            self.log("WARNING: DPI awareness could not be set. If this display is not at 100% "
                     "scaling, template matching and clicks will both be wrong - the window "
                     "will not be the size this app thinks it is.")

    def calibrate_ui_scale(self, screenshot):
        """
        Learns how much larger this machine draws the UI, when nothing is matching.

        Roblox does not pick its UI size from the viewport alone, so a client pinned to
        exactly the right pixel size can still render every element bigger than the
        templates were captured at - measured at 1.10x on one laptop, from Windows'
        Accessibility text size, which is a different setting from Display scaling and
        so survives getting the latter right. Nothing about the window rect reveals it;
        it is only visible in a frame.

        Runs only after a match has already failed, so a healthy machine never pays for
        it, and only commits when two templates independently agree (see
        health.measure_ui_scale) - a wrong scale here would move every click as well as
        every template, which is worse than not adapting. Measured once and kept: it is
        a property of the machine, not of the screen being looked at.

        Runs BEFORE the state check rather than after it fails, because failing is not a
        reliable trigger: on the laptop this was written for, items.png still scored
        0.796 against a 0.789 threshold at the wrong scale. That is a pass, so the
        Portals flow saw a lobby, called nothing wrong, and then missed every template
        after it. A margin that thin is not a signal anything can key off.

        Returns True if a new scale was adopted, meaning the caller should look again.
        """
        if self._ui_scale_checked:
            return False

        measured = health.measure_ui_scale(
            screenshot, [config.PLAY_BTN, config.ITEMS_BTN, config.STORY_CARD])
        if measured is None:
            # Not enough agreement to trust - most likely this frame simply isn't the
            # lobby. Deliberately not marked as checked, so the next run start (which
            # may be at the lobby) still gets a chance to measure.
            return False

        self._ui_scale_checked = True
        if abs(measured - 1.0) < 0.02:
            return False  # measured, and this machine is fine

        config.set_ui_scale(measured)
        vision_cache_clear()
        self.log(f"This machine draws the game UI {measured:.3f}x larger than the templates "
                 f"expect - adapting every template and coordinate to match.")
        self.log("That usually means Windows' text size (Settings > Accessibility > Text "
                 "size) is above 100% here. Setting it to 100% and restarting Roblox would "
                 "remove the guesswork, but the bot will work either way.")
        return True

    def report_unrecognised_screen(self, screenshot, label, looking_for):
        """
        Saves the screen and the scale in play when the bot can't tell where it is.

        "No lobby detected" while sitting in the lobby has several possible causes that
        look identical from the outside - a stale template, a window scaled to a size
        the templates weren't captured at, a window that isn't at the screen's top-left,
        or genuinely being somewhere else. All that separates them is the actual frame
        and the scale it was matched at, which is exactly what goes missing the moment
        the run moves on.
        """
        path = health.save_debug_screenshot(label, screenshot)
        self.log(f"Couldn't find {looking_for} on screen. Screen is "
                 f"{screenshot.shape[1]}x{screenshot.shape[0]}, scale {config.SCALE:.4f}.")
        if path:
            self.log(f"Saved what it actually saw to: {path}")

        # Sweep the lobby templates across sizes and report which size would have
        # matched. This is the difference between "the Play button wasn't found" and
        # "the Play button is there but 4% larger than the template" - the second is
        # actionable and the first isn't, and the answer ends up in the log rather than
        # in a screenshot someone has to notice, find and send on.
        self.log("Checking whether these templates would match at a different size...")
        for tpl, best_scale, best_conf, cur_conf in health.probe_scales(
                screenshot, [config.PLAY_BTN, config.ITEMS_BTN, config.STORY_CARD]):
            name = os.path.basename(tpl)
            verdict = ""
            if best_conf >= 0.85 and abs(best_scale - config.SCALE) > 0.015:
                verdict = f"  <-- would MATCH at scale {best_scale:.3f}, not {config.SCALE:.3f}"
            elif best_conf < 0.6:
                verdict = "  <-- no size matches; template is stale or it isn't on screen"
            self.log(f"   {name:<18} now {cur_conf:.3f} | best {best_conf:.3f} "
                     f"@ scale {best_scale:.3f}{verdict}")

    def _size_window_for_game(self, game_w, game_h):
        """
        Sizes this window so the reserved slot ends up exactly game_w x game_h, and
        centers it on screen. Must run on the UI thread.
        """
        self.game_slot.configure(width=game_w, height=game_h)
        # The game spans columns 1+2 in row 0, so those two have to add up to
        # exactly game_w. The split BETWEEN them no longer means anything visually
        # (status_bar spans columns 1-3 as one piece since the gamemode controls
        # moved into the sidebar - see _build_content_area()); it only has to keep
        # summing correctly, which is why the arithmetic below is untouched from
        # when this split still mattered rather than reworked to a single column.
        controls_w = min(config.DOCK_CONTROLS_WIDTH, game_w // 2)
        # Columns 1+2 must total EXACTLY game_w and must not stretch. The slot is
        # painted in the transparent key colour, so any pixel of it the game does not
        # cover is a hole straight through to the desktop - letting column 2 take the
        # leftover width of a fullscreen window put a 180px window onto whatever was
        # behind the panel. The slack goes to column 3 instead, which the log spans.
        self.grid_columnconfigure(1, minsize=controls_w, weight=0)
        self.grid_columnconfigure(2, minsize=game_w - controls_w, weight=0)
        self.grid_columnconfigure(3, weight=1)
        self.grid_rowconfigure(0, minsize=game_h)
        self.grid_rowconfigure(1, minsize=config.DOCK_STRIP_HEIGHT)

        total_w, total_h = config.panel_size(game_w, game_h)
        screen_w = win32api.GetSystemMetrics(0)
        screen_h = win32api.GetSystemMetrics(1)

        # Fullscreen rather than a sized window, and not for looks: a title bar costs
        # ~34px, and at 1600x900 the layout overshoots a 1080 screen by two of them.
        # Losing the decorations is what keeps the game at the width the soul-count
        # tooltip can actually be read at - a decorated window would silently shrink
        # the game to 1472, where the digits stop resolving.
        self.minsize(min(total_w, screen_w), min(total_h, screen_h))
        try:
            self.attributes("-fullscreen", True)
        except Exception:
            # Some window managers refuse it; a maximised window is the next best
            # thing and still clears the decorations from the bottom edge.
            self.geometry(f"{screen_w}x{screen_h}+0+0")
        self.update_idletasks()

    def _reposition_game_over_slot(self):
        """
        Puts the borderless Roblox window exactly over the reserved slot, so it reads
        as embedded inside this window.

        Roblox is never made a child of anything here - that's what its anti-cheat
        kills. It stays a separate top-level window that just happens to be sized and
        positioned to fill the hole, and raised above this one. Must run on the UI
        thread, because it reads the slot's live on-screen position.

        Returns the client (w, h) actually achieved, or None if Roblox isn't there.
        """
        game_w, game_h = self._game_size
        x, y = self.game_slot.winfo_rootx(), self.game_slot.winfo_rooty()

        # A minimized/withdrawn panel still answers winfo_rootx() - with the off-screen
        # coordinates Windows parks it at. Following it there drags the game window
        # off-screen too and, worse, sets config.CLIENT_ORIGIN to that position, so
        # every capture and click for the rest of the run aims at empty desktop.
        # Minimizing the panel mid-run is an ordinary thing to do, so this stays put
        # and lets the restore re-pin instead. "zoomed" (maximized) is a real, visible
        # state and must still dock - only iconic/withdrawn are the problem.
        if self.state() not in ("normal", "zoomed") or x <= -30000 or y <= -30000:
            return None

        # <Configure> fires for every layout change, not just real moves - re-pinning
        # on each one floods the log and thrashes the game window for no reason.
        # Only an actual change of target rect is worth acting on.
        target = (x, y, game_w, game_h)
        if target == self._last_pinned_rect and roblox_is_running():
            return self._game_size
        self._last_pinned_rect = target

        client_size = pin_roblox_borderless(game_w, game_h, x, y)
        if client_size is not None:
            self.game_slot_hint.configure(text="")
            # Both windows go into the always-on-top band together so they behave as
            # one unit - see input_controller.set_roblox_topmost() for why a plain
            # raise isn't usable here. Restored to the user's own preference by
            # _undock_game().
            set_roblox_topmost(True)
            self.attributes("-topmost", True)
        return client_size

    def _apply_dock_layout(self):
        """
        Sizes this window around the game, then drops the game into the slot.

        Returns the (width, height) the game client actually became, or None.
        """
        screen_w = win32api.GetSystemMetrics(0)
        screen_h = win32api.GetSystemMetrics(1)
        self._game_size = dock_game_size(screen_w, screen_h)

        # Can the whole embedded layout physically fit? The game can't shrink below
        # DOCK_GAME_MIN_WIDTH without Roblox clamping its height and breaking the
        # uniform config.SCALE, so on a screen too small for game + chrome the answer
        # is simply no. Rather than shoving the controls column off the edge, fall
        # back to the game and the panel as two ordinary windows.
        gw, gh = self._game_size
        total_w, total_h = config.panel_size(gw, gh)
        self._dock_embedded = total_w <= screen_w and total_h <= screen_h

        # Marshalled to the UI thread: this can be called from the bot thread (via
        # _focus_and_pin) and Tk is not thread-safe. Positioning the game has to
        # happen after the resize has actually been laid out, hence doing both in
        # the same UI-thread callback rather than racing them.
        done = threading.Event()
        result = {}

        def _run():
            try:
                # Starting the loop with the panel minimized is a normal thing to do
                # (set it running, tuck it away). The slot has no on-screen position
                # while it's iconified, so bring it back first - otherwise
                # _reposition_game_over_slot() correctly refuses to pin and the run
                # aborts with a window-pin error that the user can't act on.
                if self.state() == "iconic":
                    self.deiconify()
                    self.update_idletasks()

                if self._dock_embedded:
                    self._size_window_for_game(*self._game_size)
                    result["client"] = self._reposition_game_over_slot()
                else:
                    result["client"] = self._pin_game_unembedded(screen_w, screen_h)
            finally:
                done.set()

        if threading.current_thread() is threading.main_thread():
            _run()
        else:
            self.after(0, _run)
            done.wait(timeout=5.0)
        return result.get("client")

    def _pin_game_unembedded(self, screen_w, screen_h):
        """
        Small-screen fallback: pin the game at the correct size as its own window and
        keep this panel as an ordinary, movable window instead of wrapping it.

        The bot itself is unaffected - every coordinate and template depends on the
        game's SIZE (config.SCALE), not on it sitting inside this window - so this
        loses the embedded look and nothing else. Neither window is made topmost here,
        so the panel can be moved aside or alt-tabbed behind the game normally.
        """
        game_w, game_h = self._game_size

        # The slot is a transparent colour key: with no game behind it, it would be a
        # hole straight through to the desktop. Paint it as a normal panel instead.
        self.game_slot.configure(fg_color=BG_CARD)
        self.game_slot_hint.configure(
            fg_color=BG_CARD,
            text=("Screen too small to embed the game." + NL + NL
                  + f"Roblox is pinned to {game_w}x{game_h} in its own" + NL
                  + "window - everything works, it just isn't" + NL
                  + "wrapped inside this panel."),
        )
        self.grid_columnconfigure(1, minsize=0, weight=0)
        self.game_slot.grid_remove()

        panel_w = config.DOCK_SIDEBAR_WIDTH + config.DOCK_CONTENT_WIDTH + 24
        panel_h = min(screen_h - 60, 760)
        self.minsize(panel_w, 480)
        self.geometry(f"{panel_w}x{panel_h}+{max(0, screen_w - panel_w - 20)}+20")
        self.update_idletasks()

        client_size = pin_roblox_borderless(game_w, game_h, 0, 0)
        if client_size is not None:
            self.log(f"Screen is {screen_w}x{screen_h} - too small to embed the game. "
                     "Running as two separate windows instead.")
        return client_size

    def _on_window_moved(self, event=None):
        """
        Keeps the game glued to the slot when the window is dragged or resized.

        Debounced: <Configure> fires continuously through a drag, and re-pinning the
        game on every one of those events would fight the drag and thrash the game
        window. Only the settled position matters.
        """
        if not config.DOCK_ENABLED or not self._docked:
            return
        if self._move_job is not None:
            self.after_cancel(self._move_job)
        self._move_job = self.after(120, self._reposition_game_over_slot)

    def focus_and_pin(self):
        """
        Focuses Roblox and pins its client area, refusing to start if either fails.

        A failed pin used to be a warning the loop ran straight past. That's the case
        where every hardcoded coordinate in config.py points somewhere meaningless,
        so the bot would run clicking arbitrary points forever while the only
        explanation went to a console the windowed build doesn't have. Stopping with
        a visible reason beats "it just does nothing useful".
        Returns True if it's safe to proceed.
        """
        focused, client_size = focus_roblox_window(pin=not config.DOCK_ENABLED)

        if not focused:
            self.log("ERROR: Could not find or focus the Roblox window. Is Roblox running?")
            self.set_phase("NO ROBLOX WINDOW", "#ff1744")
            config.STOP_REQUESTED = True
            # Without this, _notify_run_ended() sees STOP_REQUESTED=True and
            # STUCK_DETECTED=None - indistinguishable from the user pressing Stop -
            # so "Roblox isn't even running" was reported as a neutral "Run stopped"
            # instead of the actual problem it is. Same bug class as everywhere else
            # in this file, caught here during the 2026-09-13 audit.
            config.STUCK_DETECTED = "NO ROBLOX WINDOW"
            return False

        if config.DOCK_ENABLED:
            client_size = self._apply_dock_layout()

        if client_size is None:
            self.log("ERROR: Couldn't pin the Roblox window. Every coordinate this bot uses "
                     "assumes a known client size, so it would just click the wrong places. "
                     "See the log file for the exact reason.")
            self.set_phase("WINDOW PIN FAILED", "#ff1744")
            config.STOP_REQUESTED = True
            config.STUCK_DETECTED = "WINDOW PIN FAILED"
            return False

        width, height = client_size
        if config.SCALE == 1.0:
            self.log(f"Roblox pinned to {width}x{height}.")
        else:
            self.log(f"Roblox pinned to {width}x{height} - every coordinate and template "
                     f"is being scaled by {config.SCALE:.3f} to match.")
        return True

    def reset_buttons(self):
        def _update():
            self.start_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
        self.after(0, _update)

if __name__ == "__main__":
    app = BotGUI()
    try:
        app.mainloop()
    except KeyboardInterrupt:
        app._on_close()