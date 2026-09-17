# modules/movement_recorder.py
"""
Shared engine for WASD movement macros - recording key hold/release timing and
replaying it. No mouse look; movement only (W/A/S/D + Space for jump).

Used by tools/movement_recorder.py to record/calibrate a path (e.g. spawn -> a portal's
fishing spot), and later by the main bot to walk that same path during a real run.
"""
import time
import json
import os
import pydirectinput
from pynput import keyboard
import input_controller

MOVEMENT_KEYS = ("w", "a", "s", "d", "space")

def key_name(key_obj):
    """Maps a pynput key object to one of MOVEMENT_KEYS, or None if it's not one."""
    if key_obj == keyboard.Key.space:
        return "space"
    char = getattr(key_obj, "char", None)
    if char and char.lower() in ("w", "a", "s", "d"):
        return char.lower()
    return None

def save_movement(actions, name):
    os.makedirs("movement_presets", exist_ok=True)
    filepath = f"movement_presets/{name}.json"
    with open(filepath, "w") as f:
        json.dump({"actions": actions}, f, indent=4)
    return filepath

def load_movement(name):
    filepath = f"movement_presets/{name}.json"
    try:
        with open(filepath, "r") as f:
            data = json.load(f)
        return data.get("actions", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def play_movement(actions, stop_check=None, disconnect_check=None):
    """
    Replays a recorded WASD/jump timeline via pydirectinput.keyDown/keyUp, holding each
    key for the same duration it was originally held. stop_check, if given, is polled
    periodically and playback aborts (releasing all held keys first) if it returns
    True - meant for wiring in config.STOP_REQUESTED from the main bot.

    disconnect_check, if given, is polled once a second and aborts with "RECONNECTED"
    when it reports a disconnect was detected and resolved. Walks run up to ~9 seconds,
    which used to be the single longest stretch where nothing watched for a disconnect:
    the keys would keep firing into a dead client for the rest of the walk, and only
    the poll loop after it would notice. Throttled to 1/sec because a check means a
    screenshot plus a template match - far too costly at this loop's 5ms cadence.

    Always releases every key it pressed, even on early abort or an exception, so a
    cancelled walk never leaves a movement key stuck down - which matters more here
    than anywhere else, since a stuck W key walks the character away indefinitely.
    Returns True if it played to completion, False if aborted via stop_check, or
    "RECONNECTED" if disconnect_check fired.
    """
    held = set()
    start = time.time()
    last_disconnect_check = 0.0
    try:
        for action in actions:
            target = action["time"]
            while (time.time() - start) < target:
                if stop_check and stop_check():
                    return False

                now = time.time()
                if disconnect_check and now - last_disconnect_check >= 1.0:
                    last_disconnect_check = now
                    if disconnect_check():
                        return "RECONNECTED"

                time.sleep(0.005)

            key = action["key"]
            if action["type"] == "keydown":
                pydirectinput.keyDown(key)
                held.add(key)
            elif action["type"] == "keyup":
                pydirectinput.keyUp(key)
                held.discard(key)
            input_controller.mark_input()
        return True
    finally:
        for key in held:
            pydirectinput.keyUp(key)
