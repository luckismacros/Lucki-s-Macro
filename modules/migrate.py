# modules/migrate.py
"""
Bringing your data over from an older version (asked for 2026-10-08: "I download the new
zip, then manually paste in everything needed").

Everything personal lives next to the exe: settings.json (webhook, bot token, every
choice), presets/ (recordings), movement_presets/, challenge_links.json. A new version
extracted to a new folder starts without any of it. Two ways to fix that:

  - First launch of a fresh folder (no settings.json yet): app_qt.py asks
    find_previous_installs() for older "Lucki's Macro" folders and offers to bring the
    newest one's data over in one click, BEFORE the window loads its settings.
  - Settings › "Bring over data from another folder…": the same copy from any folder.

copy_data_from() never deletes anything and, for recordings, never overwrites one this
install already has (unless asked) - the worst it can do is add files.
"""
import os
import shutil

import settings

APP_EXE = "Lucki's Macro.exe"
DATA_FILES = ("settings.json", "challenge_links.json")
DATA_FOLDERS = ("presets", "movement_presets")


def _has_data(folder):
    return os.path.isfile(os.path.join(folder, "settings.json")) or \
        os.path.isdir(os.path.join(folder, "presets"))


def _count_recordings(folder):
    total = 0
    for sub in DATA_FOLDERS:
        p = os.path.join(folder, sub)
        if os.path.isdir(p):
            total += sum(1 for f in os.listdir(p) if f.endswith(".json"))
    return total


def _last_used(folder):
    times = []
    for name in DATA_FILES:
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            times.append(os.path.getmtime(p))
    for sub in DATA_FOLDERS + ("logs",):
        p = os.path.join(folder, sub)
        if os.path.isdir(p):
            times.append(os.path.getmtime(p))
    return max(times) if times else 0.0


def find_previous_installs(max_depth=3):
    """
    Other folders that hold this app's data, newest first: [{"path", "recordings",
    "last_used"}]. Looks around the usual places a zip gets extracted (next to this
    folder, Downloads, Desktop, Documents), a few levels deep - quick, and never the
    folder this copy runs from.
    """
    here = os.path.normcase(os.path.abspath(settings.base_dir()))
    home = os.path.expanduser("~")
    roots = [os.path.dirname(settings.base_dir()), os.path.dirname(os.path.dirname(settings.base_dir())),
             os.path.join(home, "Downloads"), os.path.join(home, "Desktop"), os.path.join(home, "Documents"),
             os.path.join(home, "OneDrive", "Desktop"), os.path.join(home, "OneDrive", "Documents")]
    found, seen = [], set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip("\\/").count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            depth = dirpath.count(os.sep) - base_depth
            # skip heavy folders that can't hold an install
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in
                           ("node_modules", "_internal", "PySide6", "cv2", "numpy", "presets",
                            "movement_presets", "logs", "debug", "assets", "build", "__pycache__")]
            if depth >= max_depth:
                dirnames[:] = []
            if APP_EXE not in filenames and "settings.json" not in filenames:
                continue
            key = os.path.normcase(os.path.abspath(dirpath))
            if key == here or key in seen or not _has_data(dirpath):
                continue
            if APP_EXE not in filenames and not os.path.isdir(os.path.join(dirpath, "presets")):
                continue        # some other program's settings.json
            seen.add(key)
            found.append({"path": dirpath, "recordings": _count_recordings(dirpath),
                          "last_used": _last_used(dirpath)})
    found.sort(key=lambda f: f["last_used"], reverse=True)
    return found


def copy_data_from(source, replace_recordings=False, replace_settings=True):
    """
    Copies the data from `source` into this install. Returns a dict of counts:
    {"recordings", "skipped", "settings", "links"}. Never deletes anything.
    """
    dest_base = settings.base_dir()
    out = {"recordings": 0, "skipped": 0, "settings": False, "links": False}
    for sub in DATA_FOLDERS:
        src_dir = os.path.join(source, sub)
        if not os.path.isdir(src_dir):
            continue
        dst_dir = os.path.join(dest_base, sub)
        os.makedirs(dst_dir, exist_ok=True)
        for name in os.listdir(src_dir):
            if not name.endswith(".json"):
                continue
            src, dst = os.path.join(src_dir, name), os.path.join(dst_dir, name)
            if os.path.exists(dst) and not replace_recordings:
                out["skipped"] += 1
                continue
            shutil.copy2(src, dst)
            out["recordings"] += 1
    src_settings = os.path.join(source, "settings.json")
    if os.path.isfile(src_settings) and (replace_settings or not os.path.exists(settings.SETTINGS_PATH)):
        shutil.copy2(src_settings, settings.SETTINGS_PATH)
        out["settings"] = True
    src_links = os.path.join(source, "challenge_links.json")
    dst_links = os.path.join(dest_base, "challenge_links.json")
    if os.path.isfile(src_links) and (replace_settings or not os.path.exists(dst_links)):
        shutil.copy2(src_links, dst_links)
        out["links"] = True
    return out
