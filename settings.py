# settings.py
"""
Persisted user preferences for the GUI (accent color, always-on-top, etc.), read
by theme.py at import time and written by the Settings dialog in gui.py.

Kept separate from config.py on purpose - config.py is game data (maps, raids,
gamemodes) that ships with the app, this is per-install UI preference that lives
next to the exe/script and survives updates.

Computes its own base directory from this file's own location (same trick gui.py
uses for its own _base_dir) rather than relying on the current working directory,
since theme.py imports this before gui.py has had a chance to chdir into place.
"""
import json
import os
import sys

# Nuitka does not set sys.frozen the way PyInstaller does - it defines __compiled__
# on compiled modules instead. Checking only sys.frozen would make a Nuitka build fall
# through to the __file__ branch and resolve assets/, presets/ and the log file
# relative to the wrong directory, so both markers are tested.
if getattr(sys, "frozen", False) or "__compiled__" in globals():
    _base_dir = os.path.dirname(sys.executable)
else:
    _base_dir = os.path.dirname(os.path.abspath(__file__))

SETTINGS_PATH = os.path.join(_base_dir, "settings.json")


def base_dir():
    """Where per-install files belong: next to the exe, or next to the source."""
    return _base_dir

# Each palette carries its own "on_accent" text color rather than a single shared
# white/black - some accents here are dark/saturated enough for white text
# (indigo), others are bright enough that white text would wash out (cyan, amber,
# violet, the near-white mono option), so contrast is chosen per-swatch.
ACCENT_PALETTES = {
    "orange": {"label": "Orange", "accent": "#f97316", "accent_hover": "#ea580c", "on_accent": "#ffffff"},
    "indigo": {"label": "Indigo", "accent": "#6366f1", "accent_hover": "#4f46e5", "on_accent": "#ffffff"},
    "cyan": {"label": "Cyan", "accent": "#22d3ee", "accent_hover": "#06b6d4", "on_accent": "#062227"},
    "violet": {"label": "Violet", "accent": "#a78bfa", "accent_hover": "#8b5cf6", "on_accent": "#1b1033"},
    "amber": {"label": "Amber", "accent": "#f59e0b", "accent_hover": "#d97706", "on_accent": "#1f1400"},
    "mono": {"label": "Monochrome", "accent": "#e8e8e8", "accent_hover": "#c9c9c9", "on_accent": "#0a0a0a"},
}

DEFAULTS = {
    "accent": "orange",
    "always_on_top": False,
    "confirm_delete": True,
    "window_opacity": 1.0,

    # Discord webhook for run notifications. Lives here rather than in config.py
    # because it is per-install and secret-ish: anyone holding this URL can post to
    # the channel, so it must never be committed, logged, or shipped in a build.
    # settings.json is already per-install and outside the bundled assets, which is
    # exactly the right place for it. Empty disables notifications entirely.
    "discord_webhook": "",
    "notify_run_lifecycle": True,   # started / finished / stopped by user
    "notify_problems": True,        # stuck, Roblox closed, disconnected
    "notify_milestones": True,      # soul counts, target reached, type switches
    "notify_every_match": False,    # one message per match - very chatty, off by default

    # Expeditions' camera zoom-out, set by the slider in its panel. None = use
    # config.EXPEDITION_CAMERA_ZOOM_OUT_STEPS.
    "expedition_zoom_out_steps": None,

    # Run behavior (Settings > Run behavior). stop_after_defeats off = defeats never end
    # a run. never_stop = after a run stops on its own because of a problem, wait and
    # start it again instead of ending (never overrides the Stop button or the defeat
    # limit) - see gui.BotGUI._supervised_run().
    "stop_after_defeats": True,
    "max_defeats": 4,
    "never_stop": False,

    # --- Qt window (app_qt.py) ---
    "reduce_motion": False,         # turn every animation into an instant change
    "dock_screen": "",              # monitor name to open on; "" = where Windows puts it
    "welcome_done": False,          # first-run guide shown once
    "stop_after_hours": 0.0,        # 0 = no time limit; otherwise stop after this long
    "sound_on_stop": True,          # play a sound when a run ends by itself
    "ui_state": {},                 # last mode and every mode's choices, restored on launch
    "ui_scale_by_client": {},       # measured game-UI size per client size ("1600x900": 1.085)
    "slow_pc_mode": "auto",         # "auto" measures this PC; "on" / "off" force it
    "stop_after_matches": 0,        # 0 = no limit; otherwise stop once this many matches are played
    "defaults_installed": [],       # bundled default recordings already copied into presets/
    "game_settings_confirmed": False,  # user ticked "Auto Start and Auto Retry are on in the game"
    "orange_default_applied": False,   # one-time switch of the old purple default to orange

    # Autoclicker (modules/autoclicker.py). Position/interval persist; "enabled" does
    # NOT restore true on launch on purpose - a run should never start clicking before
    # the player has actually turned it on this session.
    "autoclicker_pos": None,           # [x, y] reference-space, or None until Located
    "autoclicker_interval": None,      # seconds; None = config.AUTOCLICKER_DEFAULT_INTERVAL

    # Rebindable hotkeys (modules/stage_recorder.py reads these instead of hardcoded
    # keys). Stored as the canonical strings modules/keybinds.py serializes to/from.
    "keybind_start_stop": "f7",
    "keybind_record": "f8",
    "keybind_autoclicker": "f9",
}


def load():
    """Returns the saved settings merged over DEFAULTS - unknown/missing keys
    fall back silently so an old or hand-edited settings.json can't crash startup."""
    if not os.path.exists(SETTINGS_PATH):
        return dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULTS)

    merged = dict(DEFAULTS)
    for key in DEFAULTS:
        if key in data:
            merged[key] = data[key]
    if merged["accent"] not in ACCENT_PALETTES:
        merged["accent"] = DEFAULTS["accent"]
    # The app's colour moved from purple to orange with the Lucki's Macro rename. Anyone
    # still on the old purple default gets moved across once; a colour picked on purpose
    # afterwards is left alone.
    if not merged.get("orange_default_applied"):
        if merged["accent"] == "indigo":
            merged["accent"] = "orange"
        merged["orange_default_applied"] = True
    return merged


def save(values):
    """Writes the full settings dict. Callers should load(), mutate, then save()
    the merged result so an unrelated key never gets dropped."""
    with open(SETTINGS_PATH, "w") as f:
        json.dump(values, f, indent=2)
