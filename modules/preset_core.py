# modules/preset_core.py
"""
Toolkit-free preset helpers: naming rules, file-type detection, map/raid target
resolution, and reading a preset's steps.

Split out of preset_share.py (which opens CustomTkinter dialogs) so the Qt window
can use the exact same rules without importing Tkinter - one definition, so a name
written by one UI is always found by the other.
"""
import json
import os
import shutil

import config

PRESET_DIR = "presets"
# Recordings that ship with the app (read-only, inside assets/ so the build bundles them).
DEFAULT_PRESET_DIR = os.path.join("assets", "default_presets")


def install_default_presets(already_installed):
    """
    Copies each bundled default recording into presets/ ONCE.

    `already_installed` is the list of filenames handled on earlier launches (kept in
    settings). A default the user deleted is therefore not brought back, and one they
    edited is never overwritten - an existing file of the same name always wins.
    Returns the filenames handled this time (to add to that list).
    """
    handled = []
    if not os.path.isdir(DEFAULT_PRESET_DIR):
        return handled
    os.makedirs(PRESET_DIR, exist_ok=True)
    for filename in sorted(os.listdir(DEFAULT_PRESET_DIR)):
        if not filename.endswith(".json") or filename in already_installed:
            continue
        dest = os.path.join(PRESET_DIR, filename)
        if not os.path.exists(dest):
            shutil.copyfile(os.path.join(DEFAULT_PRESET_DIR, filename), dest)
        handled.append(filename)
    return handled


def is_default_preset(location_key, variant_key, name):
    return os.path.exists(os.path.join(DEFAULT_PRESET_DIR, f"{location_key}_{variant_key}_{name}.json"))


def restore_default_preset(location_key, variant_key, name):
    """Puts the shipped version of a default recording back, replacing local edits."""
    filename = f"{location_key}_{variant_key}_{name}.json"
    os.makedirs(PRESET_DIR, exist_ok=True)
    shutil.copyfile(os.path.join(DEFAULT_PRESET_DIR, filename), os.path.join(PRESET_DIR, filename))


def sanitize_preset_name(name):
    """What user-typed text becomes on disk. Shared by New/Rename and every import."""
    return name.strip().replace(" ", "_").lower()


def is_reserved_name(name):
    return name == sanitize_preset_name(config.AUTO_PLAY_PRESET_NAME)


def infer_kind(actions):
    """
    'stage_preset' (click/key), 'movement_preset' (keydown/keyup), or None, from the
    shape of a foreign file's actions.
    """
    for action in actions:
        action_type = action.get("type")
        if action_type in ("keydown", "keyup"):
            return "movement_preset"
        if action_type in ("click", "key"):
            return "stage_preset"
    return None


def resolve_stage_target(data):
    """(location_key, variant_key) an imported file belongs to, or None if ambiguous."""
    location_key = data.get("location") or data.get("map")
    variant_key = data.get("variant") or data.get("act")
    category = data.get("category")

    if category == "story" and location_key in config.MAPS:
        return location_key, variant_key
    if category == "raids" and location_key in config.RAIDS:
        return location_key, variant_key
    if not category:
        if location_key in config.MAPS:
            return location_key, variant_key
        if location_key in config.RAIDS:
            return location_key, variant_key
    return None


def preset_path(location_key, variant_key, name):
    return f"{PRESET_DIR}/{location_key}_{variant_key}_{name}.json"


def list_presets(location_key, variant_key):
    """Preset names saved for this location/variant, sorted."""
    if not location_key or not variant_key or not os.path.isdir(PRESET_DIR):
        return []
    prefix = f"{location_key}_{variant_key}_"
    return sorted(f[len(prefix):-5] for f in os.listdir(PRESET_DIR)
                  if f.startswith(prefix) and f.endswith(".json"))


def load_actions(location_key, variant_key, name):
    """The preset's action list, or None if the file is missing/unreadable."""
    try:
        with open(preset_path(location_key, variant_key, name), "r") as f:
            data = json.load(f)
        actions = data.get("actions", [])
        return actions if isinstance(actions, list) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_actions(location_key, variant_key, name, actions):
    os.makedirs(PRESET_DIR, exist_ok=True)
    with open(preset_path(location_key, variant_key, name), "w") as f:
        json.dump({
            "location": location_key,
            "variant": variant_key,
            "reference": [config.REFERENCE_WIDTH, config.REFERENCE_HEIGHT],
            "actions": actions,
        }, f, indent=4)


# How long a gap between two actions has to be before it shows as its own "wait" row.
WAIT_ROW_SECONDS = 1.5


def describe_steps(actions):
    """
    Turns a raw timeline into readable steps for the placement editor.

    Returns a list of dicts: {"kind": "place"|"select"|"wait", "text", "time",
    "unit", "spot", "pos", "action_indexes"}. A key press followed by clicks reads as
    "Pick unit N, place it at spot K"; later clicks with the same unit read as "Place
    unit N again at spot K". Every step remembers which raw actions it came from, so
    deleting or retiming a step edits exactly those.
    """
    steps = []
    current_unit = None
    unit_used = False
    spot = 0
    pending_select = None
    last_time = None

    for index, action in enumerate(actions):
        if action.get("type") in ("move", "keydown", "keyup"):
            continue  # the recorded mouse path / any walk at the start - not a step of its own
        t = float(action.get("time", 0.0))
        if last_time is not None and t - last_time >= WAIT_ROW_SECONDS:
            steps.append({"kind": "wait", "text": f"wait {t - last_time:.1f} s", "time": last_time,
                          "unit": None, "spot": None, "pos": None, "action_indexes": []})
        last_time = t

        if action.get("type") == "key":
            if pending_select is not None:
                steps.append(pending_select)
            current_unit = str(action.get("value"))
            unit_used = False
            pending_select = {"kind": "select", "text": f"Pick unit {current_unit}", "time": t,
                              "unit": current_unit, "spot": None, "pos": None,
                              "action_indexes": [index]}
        elif action.get("type") == "click":
            spot += 1
            pos = tuple(action.get("pos", (0, 0)))
            if pending_select is not None:
                indexes = pending_select["action_indexes"] + [index]
                steps.append({"kind": "place", "text": f"Pick unit {current_unit}, place it at spot {spot}",
                              "time": t, "unit": current_unit, "spot": spot, "pos": pos,
                              "action_indexes": indexes})
                pending_select = None
            else:
                who = f"unit {current_unit} again" if current_unit and unit_used else (
                    f"unit {current_unit}" if current_unit else "a unit")
                steps.append({"kind": "place", "text": f"Place {who} at spot {spot}", "time": t,
                              "unit": current_unit, "spot": spot, "pos": pos,
                              "action_indexes": [index]})
            unit_used = True

    if pending_select is not None:
        steps.append(pending_select)
    return steps


def timeline_length(actions):
    return max((float(a.get("time", 0.0)) for a in actions), default=0.0)


def has_movement(actions):
    """True if this recording starts with (or contains) a walk - see stage_player.play_preset."""
    return any(a.get("type") in ("keydown", "keyup") for a in actions)
