# modules/preset_io.py
"""
Exporting and importing recordings - the toolkit-free half (the dialogs are in
ui_qt/dialogs.py). Rewritten 2026-10-08 because the old flow was outdated: an import
decided where a recording belonged from what the FILE said and only understood Story,
Raids and Expeditions, so a Boss Rush / Monster Clash / Portal recording came with a
"Where does this go? Story / Raids / Expeditions" question that had no right answer, and
moving everything to another PC meant one file per recording.

Now:
  - One recording: export it to a file; import a file INTO the slot you opened the menu
    on (no questions - it's for that slot, whatever the file says). Any of this app's
    recording files works, old or new format.
  - Everything: Settings › "Back up my data" makes one zip (recordings, walks, challenge
    links, settings minus secrets); "Restore from a backup" puts every recording back
    where it was, either replacing what's there or keeping yours.
"""
import json
import os
import time
import zipfile

import config
from modules import preset_core

FILE_KIND = "lucki_recording"


def _actions_of(data):
    actions = data.get("actions") if isinstance(data, dict) else None
    if not isinstance(actions, list) or not actions:
        raise ValueError("it isn't a recording (it has no steps)")
    for a in actions:
        if not isinstance(a, dict) or "type" not in a:
            raise ValueError("its steps aren't in a format this app knows")
    return actions


def read_recording(path):
    """
    A recording file's contents: {"actions", "location", "variant", "name"} (the last three
    may be None for files that don't say). Raises ValueError with a readable reason.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ValueError(f"couldn't read it ({e})")
    actions = _actions_of(data)
    name = data.get("name") or data.get("preset_name") or os.path.splitext(os.path.basename(path))[0]
    return {
        "actions": actions,
        "location": data.get("location") or data.get("map"),
        "variant": data.get("variant") or data.get("act"),
        "name": preset_core.sanitize_preset_name(str(name)) or "imported",
    }


def export_one(location_key, variant_key, name, dest):
    """Writes one recording to `dest` in a self-describing format. Raises ValueError."""
    actions = preset_core.load_actions(location_key, variant_key, name)
    if not actions:
        raise ValueError("that recording is missing or has no steps")
    preset_core.write_json_atomic(dest, {
        "kind": FILE_KIND,
        "app_version": config.APP_VERSION,
        "exported": time.strftime("%Y-%m-%d %H:%M"),
        "location": location_key,
        "variant": variant_key,
        "name": name,
        "reference": [config.REFERENCE_WIDTH, config.REFERENCE_HEIGHT],
        "actions": actions,
    })


def unique_name(location_key, variant_key, name):
    """`name`, or `name 2`, `name 3`... - whichever isn't taken in that slot yet."""
    if not os.path.exists(preset_core.preset_path(location_key, variant_key, name)):
        return name
    n = 2
    while os.path.exists(preset_core.preset_path(location_key, variant_key, f"{name}_{n}")):
        n += 1
    return f"{name}_{n}"


def import_into_slot(location_key, variant_key, name, actions):
    """Saves `actions` as recording `name` in this slot (safely). Returns the name used."""
    name = preset_core.sanitize_preset_name(name) or "imported"
    if preset_core.is_reserved_name(name):
        name = f"{name}_imported"
    preset_core.save_actions(location_key, variant_key, name, actions)
    return name


# --- whole backups ------------------------------------------------------------------------

_RESTORABLE_FOLDERS = ("presets", "movement_presets")


def plan_restore(path):
    """
    What a restore from `path` would write: a list of {"dest", "label", "data", "exists"}.
    `path` is a backup zip (Settings › Back up my data) or a single exported recording that
    says which slot it's for. Raises ValueError with a readable reason.
    """
    base = _base_dir()
    items = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                parts = info.filename.replace("\\", "/").split("/")
                filename = parts[-1]
                # Only the known folders, only .json, only plain file names - nothing in a
                # zip can write anywhere else (no "../" tricks).
                if len(parts) == 2 and parts[0] in _RESTORABLE_FOLDERS and filename.endswith(".json") \
                        and filename == os.path.basename(filename):
                    folder = parts[0]
                elif len(parts) == 1 and filename == "challenge_links.json":
                    folder = ""
                else:
                    continue
                raw = z.read(info)
                try:
                    data = json.loads(raw.decode("utf-8"))
                    if folder == "presets":
                        _actions_of(data)
                except Exception:
                    continue                      # damaged entry - skipped, not fatal
                dest = os.path.join(base, folder, filename) if folder else os.path.join(base, filename)
                items.append({"dest": dest, "label": f"{folder}/{filename}" if folder else filename,
                              "data": raw, "exists": os.path.exists(dest)})
        if not items:
            raise ValueError("there are no recordings in that zip")
        return items

    rec = read_recording(path)
    if not rec["location"] or not rec["variant"]:
        raise ValueError("that file doesn't say which mode/stage it's for - open the page it belongs "
                         "to and use ⋯ › Import into this slot instead")
    dest = preset_core.preset_path(rec["location"], rec["variant"], rec["name"])
    data = json.dumps({"location": rec["location"], "variant": rec["variant"],
                       "reference": [config.REFERENCE_WIDTH, config.REFERENCE_HEIGHT],
                       "actions": rec["actions"]}, indent=4).encode("utf-8")
    return [{"dest": os.path.join(base, dest), "label": os.path.basename(dest), "data": data,
             "exists": os.path.exists(os.path.join(base, dest))}]


def apply_restore(items, replace_existing):
    """Writes the planned files. Returns (written, kept) counts."""
    written = kept = 0
    for item in items:
        if item["exists"] and not replace_existing:
            kept += 1
            continue
        os.makedirs(os.path.dirname(item["dest"]) or ".", exist_ok=True)
        tmp = item["dest"] + ".tmp"
        with open(tmp, "wb") as f:
            f.write(item["data"])
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, item["dest"])
        written += 1
    return written, kept


def _base_dir():
    try:
        import settings
        return settings.base_dir()
    except Exception:
        return os.getcwd()
