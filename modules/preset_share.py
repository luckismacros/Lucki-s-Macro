# modules/preset_share.py
"""
Export/import for both preset types (Story/Raid click-and-key presets under presets/,
and WASD walk macros under movement_presets/), so one can be handed to someone else as
a single file instead of them having to find the right JSON and drop it in the right
folder under the right filename by hand.

Exported files always carry unambiguous metadata (kind/category/location/variant, or
kind/name for a movement macro) written by export_stage_preset()/export_movement_preset()
below, so an import round-trip through this module never needs guessing. Importing a
FOREIGN file - one of the raw on-disk JSONs shared the old manual-copy way, without that
metadata - is still supported: the "kind" is inferred from the shape of its actions, and
a missing story/raid target is resolved with a small chooser dialog instead of failing.
"""
import json
import os
from tkinter import filedialog, messagebox
import customtkinter as ctk
import config
from theme import (
    FONT_FAMILY, INPUT_DIALOG_STYLE, DROPDOWN_STYLE, SEGMENTED_STYLE, ACCENT_BUTTON_STYLE,
    TEXT_MUTED, BG_APP,
)
from ui_motion import wire_button_feel
# The naming/detection rules live in preset_core so the Qt window shares them exactly.
from modules.preset_core import (  # noqa: F401  (re-exported for gui.py)
    sanitize_preset_name, is_reserved_name, infer_kind as _infer_kind,
    resolve_stage_target as _resolve_stage_target,
)


def _prompt_name(title, text):
    """CTkInputDialog has no way to pre-fill its entry - the caller's `text` is
    expected to mention the default in words, since typing nothing falls back to it."""
    dialog = ctk.CTkInputDialog(text=text, title=title, **INPUT_DIALOG_STYLE)
    name = dialog.get_input()
    return sanitize_preset_name(name) if name else None


# --- Stage presets (Story/Raids) -----------------------------------------------

def export_stage_preset(location_key, variant_key, preset_name):
    """
    Writes presets/{location_key}_{variant_key}_{preset_name}.json out to a
    user-chosen file via a Save dialog, tagged with unambiguous metadata (which
    category it's from, so import never has to guess).
    Returns the chosen path, or None if cancelled/the source file is missing.
    """
    source_path = f"presets/{location_key}_{variant_key}_{preset_name}.json"
    if not os.path.exists(source_path):
        messagebox.showerror("Export Preset", f"Preset file not found:\n{source_path}")
        return None

    with open(source_path, "r") as f:
        data = json.load(f)

    category = "story" if location_key in config.MAPS else ("raids" if location_key in config.RAIDS else None)

    dest_path = filedialog.asksaveasfilename(
        title="Export Preset",
        defaultextension=".json",
        initialfile=f"{preset_name}.json",
        filetypes=[("Macro Slop preset", "*.json")],
    )
    if not dest_path:
        return None

    with open(dest_path, "w") as f:
        json.dump({
            "kind": "stage_preset",
            "category": category,
            "location": location_key,
            "variant": variant_key,
            "preset_name": preset_name,
            "actions": data.get("actions", []),
        }, f, indent=4)

    return dest_path


def _choose_stage_target(parent):
    """
    Small dialog for when an imported file's map/act (or raid/difficulty) can't be
    worked out automatically - lets the user pick where it belongs instead of
    refusing the import. Returns (location_key, variant_key) or None if cancelled.
    """
    result = {}
    dialog = ctk.CTkToplevel(parent, fg_color=BG_APP)
    dialog.title("Where does this preset belong?")
    dialog.geometry("300x360")
    dialog.transient(parent)
    dialog.grab_set()

    ctk.CTkLabel(
        dialog, text="This file doesn't say which map/raid it's for -\npick one:",
        font=ctk.CTkFont(family=FONT_FAMILY, size=12), justify="center"
    ).pack(pady=(15, 10))

    category_var = ctk.StringVar(value="Story")
    ctk.CTkSegmentedButton(
        dialog, values=["Story", "Raids"], variable=category_var,
        command=lambda *_: _rebuild(), **SEGMENTED_STYLE
    ).pack(fill="x", padx=15, pady=(0, 10))

    location_label = ctk.CTkLabel(dialog, text="Map", font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_MUTED)
    location_label.pack(anchor="w", padx=15)
    location_var = ctk.StringVar()
    location_dropdown = ctk.CTkOptionMenu(dialog, variable=location_var, **DROPDOWN_STYLE)
    location_dropdown.pack(fill="x", padx=15, pady=(0, 10))

    variant_label = ctk.CTkLabel(dialog, text="Act", font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_MUTED)
    variant_label.pack(anchor="w", padx=15)
    variant_var = ctk.StringVar()
    variant_dropdown = ctk.CTkOptionMenu(dialog, variable=variant_var, **DROPDOWN_STYLE)
    variant_dropdown.pack(fill="x", padx=15, pady=(0, 10))

    def _rebuild(*_):
        if category_var.get() == "Story":
            location_label.configure(text="Map")
            variant_label.configure(text="Act")
            location_values = [v["label"] for v in config.MAPS.values() if v["enabled"]]
        else:
            location_label.configure(text="Raid")
            variant_label.configure(text="Difficulty")
            location_values = [v["label"] for v in config.RAIDS.values() if v["enabled"]]

        location_dropdown.configure(values=location_values)
        if location_values:
            location_var.set(location_values[0])
        _rebuild_variants()

    def _rebuild_variants(*_):
        if category_var.get() == "Story":
            map_key = next((k for k, v in config.MAPS.items() if v["label"] == location_var.get()), None)
            variant_values = [v["label"] for v in config.MAPS[map_key]["acts"].values() if v["enabled"]] if map_key else []
        else:
            variant_values = [str(n) for n in (1, 2, 3)]

        variant_dropdown.configure(values=variant_values)
        if variant_values:
            variant_var.set(variant_values[0])

    location_var.trace_add("write", _rebuild_variants)
    _rebuild()

    def _confirm():
        if category_var.get() == "Story":
            location_key = next((k for k, v in config.MAPS.items() if v["label"] == location_var.get()), None)
            variant_key = next(
                (k for k, v in config.MAPS[location_key]["acts"].items() if v["label"] == variant_var.get()),
                None
            ) if location_key else None
        else:
            location_key = next((k for k, v in config.RAIDS.items() if v["label"] == location_var.get()), None)
            variant_key = variant_var.get() or None

        if location_key and variant_key:
            result["value"] = (location_key, variant_key)
        dialog.destroy()

    ok_btn = ctk.CTkButton(dialog, text="OK", command=_confirm, **ACCENT_BUTTON_STYLE)
    wire_button_feel(ok_btn)
    ok_btn.pack(fill="x", padx=15, pady=(10, 15))
    dialog.wait_window()
    return result.get("value")


def import_stage_preset(parent):
    """
    Opens a file picker, reads a stage preset (ours or a foreign one), resolves
    which map/act or raid/difficulty it belongs to (automatically if the file says,
    otherwise via _choose_stage_target), asks for/confirms a name, and writes it
    into presets/ using the exact same format _new_preset()/stage_recorder.py
    already write - stage_player.py only ever reads "actions", so an imported
    preset plays back identically to a locally-recorded one.
    Returns (location_key, variant_key, preset_name) on success, or None if
    cancelled or the file isn't a recognizable stage preset.
    """
    source_path = filedialog.askopenfilename(title="Import Preset", filetypes=[("JSON files", "*.json")])
    if not source_path:
        return None

    try:
        with open(source_path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        messagebox.showerror("Import Preset", f"Couldn't read that file:\n{e}")
        return None

    actions = data.get("actions")
    if not isinstance(actions, list):
        messagebox.showerror("Import Preset", "That file doesn't look like a preset (no action list).")
        return None

    kind = data.get("kind") or _infer_kind(actions)
    if kind == "movement_preset":
        messagebox.showerror(
            "Import Preset",
            "This looks like a movement macro, not a stage preset - import it from "
            "the Movement Recorder tool instead."
        )
        return None

    target = _resolve_stage_target(data)
    if target is None:
        target = _choose_stage_target(parent)
        if target is None:
            return None
    location_key, variant_key = target

    default_name = sanitize_preset_name(data.get("preset_name", "") or os.path.splitext(os.path.basename(source_path))[0])
    name = _prompt_name("Import Preset", f"Name this preset (default: {default_name}):") or default_name
    if not name:
        return None
    if is_reserved_name(name):
        messagebox.showerror("Import Preset", "'Auto Play' is a reserved preset name. Choose a different name.")
        return None

    dest_path = f"presets/{location_key}_{variant_key}_{name}.json"
    if os.path.exists(dest_path) and not messagebox.askyesno(
        "Import Preset", f"'{name}' already exists here and will be overwritten. Continue?"
    ):
        return None

    os.makedirs("presets", exist_ok=True)
    with open(dest_path, "w") as f:
        json.dump({"location": location_key, "variant": variant_key, "actions": actions}, f)

    return location_key, variant_key, name


# --- Movement macros (WASD walks) -----------------------------------------------

def export_movement_preset(movement_file_name):
    """
    Writes movement_presets/{movement_file_name}.json out to a user-chosen file via
    a Save dialog, tagged so import never has to guess. Returns the chosen path, or
    None if cancelled/the source file is missing.
    """
    source_path = f"movement_presets/{movement_file_name}.json"
    if not os.path.exists(source_path):
        messagebox.showerror("Export Movement Macro", f"Movement file not found:\n{source_path}")
        return None

    with open(source_path, "r") as f:
        data = json.load(f)

    dest_path = filedialog.asksaveasfilename(
        title="Export Movement Macro",
        defaultextension=".json",
        initialfile=f"{movement_file_name}.json",
        filetypes=[("Macro Slop movement macro", "*.json")],
    )
    if not dest_path:
        return None

    with open(dest_path, "w") as f:
        json.dump({"kind": "movement_preset", "name": movement_file_name, "actions": data.get("actions", [])}, f, indent=4)

    return dest_path


def import_movement_preset():
    """
    Opens a file picker, reads a movement macro (ours or a foreign one), asks
    for/confirms a name, and writes it into movement_presets/ using the exact
    format save_movement()/load_movement() already use.
    Returns the movement_file_name on success, or None if cancelled or the file
    isn't a recognizable movement macro.
    """
    source_path = filedialog.askopenfilename(title="Import Movement Macro", filetypes=[("JSON files", "*.json")])
    if not source_path:
        return None

    try:
        with open(source_path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        messagebox.showerror("Import Movement Macro", f"Couldn't read that file:\n{e}")
        return None

    actions = data.get("actions")
    if not isinstance(actions, list):
        messagebox.showerror("Import Movement Macro", "That file doesn't look like a movement macro (no action list).")
        return None

    kind = data.get("kind") or _infer_kind(actions)
    if kind == "stage_preset":
        messagebox.showerror(
            "Import Movement Macro",
            "This looks like a Story/Raid preset, not a movement macro - import it "
            "from the main Preset Manager instead."
        )
        return None

    default_name = data.get("name") or os.path.splitext(os.path.basename(source_path))[0]
    dialog = ctk.CTkInputDialog(text=f"Name this movement macro (default: {default_name}):", title="Import Movement Macro", **INPUT_DIALOG_STYLE)
    name = dialog.get_input() or default_name
    if not name:
        return None

    dest_path = f"movement_presets/{name}.json"
    if os.path.exists(dest_path) and not messagebox.askyesno(
        "Import Movement Macro", f"'{name}' already exists here and will be overwritten. Continue?"
    ):
        return None

    os.makedirs("movement_presets", exist_ok=True)
    with open(dest_path, "w") as f:
        json.dump({"actions": actions}, f, indent=4)

    return name
