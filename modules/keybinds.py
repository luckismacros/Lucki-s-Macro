# modules/keybinds.py
"""
Serializes any pynput key to/from the plain string stored in settings.json
(settings.py's keybind_start_stop / keybind_record / keybind_autoclicker), and back
into something StageRecorder.on_press can compare a live keypress against.

modules.movement_recorder.key_name() does something similar but is hardcoded to WASD +
space for recording playback - not reusable for an arbitrary rebindable key (F-keys,
letters, etc.), which is what this is for instead.
"""
from pynput import keyboard

# Keys the picker flows themselves already use, and so can never be assigned to an
# action - rebinding "the bot start/stop key" to Esc would make the coordinate/keybind
# pickers impossible to cancel, and to Space would collide with the coordinate finder.
# enter/return are reserved too: Qt activates a focused button on either one by
# default, so binding a hotkey to them would re-click whichever button is still
# focused after starting the capture (the same bug Space caused when the Locate
# button used to listen for it - see ui_qt.main_window._pick_screen_click).
RESERVED = {"esc", "space", "enter", "return"}

# Not blocked (unlike RESERVED) - just worth a heads-up, since these keys are already
# in active use elsewhere in THIS app: WASD/Space move the character during a
# recording (modules.movement_recorder.key_name) and 1-6 select units
# (modules.stage_recorder.StageRecorder.on_press). Rebinding a global hotkey onto one
# of these means every ordinary press of it - in-game or while recording - now ALSO
# fires that hotkey, since the keybind listener is always running in the background.
LIKELY_CONFLICTS = {"w", "a", "s", "d"} | {str(i) for i in range(1, 7)}


def key_to_string(key):
    """A pynput key -> its canonical settings string ("f9", "g", ...), or None for a
    key that can't be sensibly used as a hotkey (modifier-only presses, etc. still
    come through fine - Ctrl/Shift/Alt themselves just aren't refused here)."""
    name = getattr(key, "name", None)
    if name:
        return name
    char = getattr(key, "char", None)
    if char:
        return char.lower()
    return None


def string_to_key(value):
    """The settings string -> a pynput key/KeyCode to compare a live keypress
    against, or None if the string doesn't map to anything (e.g. never set)."""
    if not value:
        return None
    special = getattr(keyboard.Key, value, None)
    if isinstance(special, keyboard.Key):
        return special
    if len(value) == 1:
        return keyboard.KeyCode.from_char(value)
    return None


def matches(key, value):
    """True if a live pynput key event matches the settings string `value`."""
    target = string_to_key(value)
    return target is not None and key == target
