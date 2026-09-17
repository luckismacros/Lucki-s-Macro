# ui_qt/pages.py
"""
One setup page per gamemode, shown in the sidebar under SETUP.

Every page is a short stack of numbered step cards that tick green once that step
is sorted, and answers the same few questions for the main window:

  checks()    rows for the READY CHECK card
  run_spec()  (engine method name, args, discord fields) - raises ValueError with a
              plain-language reason when something is missing
  summary()   one line for the RIGHT NOW card while idle
  state() / restore()  so every choice survives a restart

The run arguments are exactly the ones gui.py builds, so both windows drive the
engine identically.
"""
import os

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QComboBox, QMenu, QSlider, QButtonGroup, QSpinBox, QLabel,
)

import config
import settings
from modules import preset_core, challenge_links
from . import theme, dialogs
from .art import ArtBanner, art_path
from .widgets import (
    StepCard, Segmented, ToggleRow, Button, IconButton, Chip, FlowRow, ImageCard, label, shrinkable_combo,
)

AUTO = config.AUTO_PLAY_PRESET_NAME
PORTAL_FISH_CATEGORIES = ("summer", "sovereign")


def pretty(name):
    return (name or "").replace("_", " ")


def combo(items, parent=None):
    box = shrinkable_combo(QComboBox(parent))
    for text, key in items:
        box.addItem(text, key)
    return box


def set_combo(box, key):
    idx = box.findData(key)
    if idx >= 0:
        box.setCurrentIndex(idx)


def field(text):
    lbl = label(text)
    lbl.setStyleSheet(f"font-size: 12px; color: {theme.TEXT_MUTED};")
    return lbl


# ----------------------------------------------------------------------------- recording picker

class PresetPicker(StepCard):
    """
    "How units get placed": the game's Auto Play, or one of your recordings for this
    exact stage - with New / Record / Edit right there, and the rarer actions
    (rename, copy, export, import, delete) under More.
    """
    changed = Signal()

    def __init__(self, host, number, title, slot_fn, allow_auto=True, trailing_text=None, can_copy=False):
        trailing = label(trailing_text, "hint") if trailing_text else None
        super().__init__(number, title, trailing)
        self.host = host
        self.slot_fn = slot_fn
        self.allow_auto = allow_auto
        self.can_copy = can_copy
        self._names = None
        self.extra_info = ""

        self.source = None
        if allow_auto:
            self.source = Segmented([("auto", "Game's Auto Play"), ("rec", "My macro")], "auto")
            self.source.changed.connect(lambda _: self._on_source())
            self.body.addWidget(self.source)

        # Shown while Auto Play is picked. The macro tools are one click away and this
        # says so - hidden entirely, people couldn't find where macros are made.
        self.auto_hint = QWidget()
        auto_col = QVBoxLayout(self.auto_hint)
        auto_col.setContentsMargins(0, 0, 0, 0)
        auto_col.setSpacing(2)
        auto_col.addWidget(label("Nothing to record - the game places them.", "hint", wrap=True))
        to_macro = Button("Record my own macro instead  ›", "link", height=26, font_px=12, bold=False)
        to_macro.clicked.connect(lambda: self.source.set_value("rec", emit=True))
        auto_col.addWidget(to_macro, 0, Qt.AlignLeft)
        self.body.addWidget(self.auto_hint)

        self.rec_box = QWidget()
        col = QVBoxLayout(self.rec_box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(7)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.combo = shrinkable_combo(QComboBox())
        self.combo.currentIndexChanged.connect(lambda _: self._on_pick())
        row.addWidget(self.combo, 1)
        self.new_btn = IconButton("plus", "New")
        self.new_btn.setToolTip("Make a new, empty recording for this stage")
        self.new_btn.clicked.connect(self.new_preset)
        row.addWidget(self.new_btn)
        col.addLayout(row)
        self.info = label("", "hint", wrap=True)
        col.addWidget(self.info)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.record_btn = Button("Record", "subtle", icon="record", key_hint="F8", height=32, font_px=12)
        self.record_btn.setToolTip("Press, then place your units in Roblox. Need to walk somewhere first? Hold "
                                   "W/A/S/D (Space to jump) before you start placing - the walk is saved as part "
                                   "of this same recording, and only replays again once you reach a different "
                                   "stage. Press F8 again when you're done.")
        self.record_btn.clicked.connect(lambda: self.host.toggle_recording())
        actions.addWidget(self.record_btn, 1)
        self.edit_btn = Button("Edit", "subtle", icon="edit", height=32, font_px=12)
        self.edit_btn.setToolTip("See every step and every spot, move spots, delete mistakes")
        self.edit_btn.clicked.connect(self.edit_preset)
        actions.addWidget(self.edit_btn, 1)
        self.more_btn = IconButton("list", "More")
        self.more_btn.clicked.connect(self._more_menu)
        actions.addWidget(self.more_btn)
        col.addLayout(actions)
        self.body.addWidget(self.rec_box)
        self._on_source(emit=False)

    # --- state

    def uses_auto(self):
        return self.source is not None and self.source.value() == "auto"

    def recording_name(self):
        return self.combo.currentData()

    def selected_name(self):
        return AUTO if self.uses_auto() else self.recording_name()

    def slot(self):
        return self.slot_fn()

    def _on_source(self, emit=True):
        auto = self.uses_auto()
        self.auto_hint.setVisible(auto)
        self.rec_box.setVisible(not auto)
        if not auto:
            self.refresh_list(force=True)
        self._after_change(emit)

    def _on_pick(self):
        self._update_info()
        self._after_change(True)

    def _after_change(self, emit):
        self.sync_recorder()
        state, _ = self.check()
        self.set_done(state == "ok")
        if emit:
            self.changed.emit()

    def sync_recorder(self):
        rec = self.host.recorder
        if rec.is_recording:
            return
        loc, var = self.slot()
        rec.current_location = loc
        rec.current_variant = var
        rec.preset_name = self.recording_name() or AUTO

    def refresh_list(self, select=None, force=False):
        loc, var = self.slot()
        names = preset_core.list_presets(loc, var)
        if names == self._names and select is None and not force:
            self._update_info()
            return
        previous = select or self.combo.currentData()
        self._names = names
        self.combo.blockSignals(True)
        self.combo.clear()
        for n in names:
            self.combo.addItem(pretty(n), n)
        if not names:
            self.combo.addItem("No recordings yet - click New", None)
        if previous in names:
            set_combo(self.combo, previous)
        self.combo.blockSignals(False)
        self._update_info()
        self._after_change(True)

    def _update_info(self):
        name = self.recording_name()
        has = name is not None
        self.edit_btn.setEnabled(has)
        self.record_btn.setEnabled(has or self.host.recorder.is_recording)
        if not has:
            self.info.setText("Click New, give it a name, then Record.")
            return
        loc, var = self.slot()
        actions = preset_core.load_actions(loc, var, name)
        if actions is None:
            self.info.setText("That recording's file can't be read.")
        elif not actions:
            self.info.setText("Empty so far - press Record, then place your units in Roblox.")
        else:
            steps = [s for s in preset_core.describe_steps(actions) if s["kind"] != "wait"]
            text = f"{len(steps)} steps · {preset_core.timeline_length(actions):.0f} s"
            self.info.setText(text + (f" · {self.extra_info}" if self.extra_info else ""))

    def check(self):
        if self.uses_auto():
            return "ok", "Auto Play"
        name = self.recording_name()
        if not name:
            return "bad", "nothing recorded yet"
        loc, var = self.slot()
        actions = preset_core.load_actions(loc, var, name)
        if not actions:
            return "bad", f"'{pretty(name)}' is empty"
        steps = len([s for s in preset_core.describe_steps(actions) if s["kind"] != "wait"])
        return "ok", f"{pretty(name)} · {steps} steps"

    def set_recording(self, recording):
        self.record_btn.setText("Stop" if recording else "Record")
        self.record_btn.set_kind("danger" if recording else "subtle")
        self.record_btn.icon_name = "stop" if recording else "record"
        self.record_btn.setEnabled(recording or self.recording_name() is not None)
        for w in (self.combo, self.new_btn, self.edit_btn, self.more_btn):
            w.setEnabled(not recording)
        if self.source is not None:
            self.source.setEnabled(not recording)
        if not recording:
            self._update_info()

    def state(self):
        return {"source": self.source.value() if self.source else "rec", "name": self.recording_name()}

    def restore(self, data):
        if self.source is not None and data.get("source") in ("auto", "rec"):
            self.source.set_value(data["source"], animate=False)
        self._on_source(emit=False)
        self.refresh_list(select=data.get("name"), force=True)

    # --- actions

    def new_preset(self):
        loc, var = self.slot()
        if not loc or not var:
            return
        raw = dialogs.prompt_text(self.host, "New recording",
                                  "Give it a name you'll recognise, like 'fast clear' or 'six units'.",
                                  placeholder="my setup", ok_text="Create")
        if not raw:
            return
        name = preset_core.sanitize_preset_name(raw)
        if preset_core.is_reserved_name(name):
            dialogs.message(self.host, "Pick another name", "'Auto Play' is reserved for the game's own Auto Play.")
            return
        if os.path.exists(preset_core.preset_path(loc, var, name)):
            dialogs.message(self.host, "Already exists", f"There's already a recording called '{pretty(name)}' here - it's selected now.")
        else:
            preset_core.save_actions(loc, var, name, [])
            self.host.log(f"Made a new recording '{pretty(name)}'. Press Record (F8), then place your units in Roblox.")
        if self.source is not None:
            self.source.set_value("rec", animate=True)
        self._on_source(emit=False)
        self.refresh_list(select=name, force=True)

    def edit_preset(self):
        name = self.recording_name()
        if name:
            loc, var = self.slot()
            self.host.open_editor(loc, var, name)

    def _more_menu(self):
        menu = QMenu(self)
        name = self.recording_name()
        a_rename = menu.addAction("Rename…")
        a_copy = menu.addAction("Copy to other acts…") if self.can_copy else None
        a_export = menu.addAction("Export to a file…")
        a_import = menu.addAction("Import from a file…")
        loc_, var_ = self.slot()
        a_restore = (menu.addAction("Restore default version")
                     if name and preset_core.is_default_preset(loc_, var_, name) else None)
        menu.addSeparator()
        a_delete = menu.addAction("Delete")
        for a in (a_rename, a_copy, a_export, a_delete):
            if a is not None:
                a.setEnabled(name is not None)
        chosen = menu.exec(self.more_btn.mapToGlobal(self.more_btn.rect().bottomLeft()))
        if chosen is None:
            return
        if chosen is a_rename:
            self.rename_preset()
        elif chosen is a_copy:
            loc, var = self.slot()
            n = dialogs.copy_story_preset(self.host, loc, var, name)
            if n:
                self.host.log(f"Copied '{pretty(name)}' to {n} act{'s' if n != 1 else ''}.")
        elif chosen is a_export:
            loc, var = self.slot()
            dest = dialogs.export_stage_preset(self.host, loc, var, name)
            if dest:
                self.host.log(f"Exported '{pretty(name)}' to {dest}")
        elif chosen is a_import:
            result = dialogs.import_stage_preset(self.host)
            if result:
                loc, var, new_name = result
                self.host.log(f"Imported '{pretty(new_name)}'.")
                if (loc, var) == self.slot():
                    if self.source is not None:
                        self.source.set_value("rec")
                    self._on_source(emit=False)
                    self.refresh_list(select=new_name, force=True)
                else:
                    self.host.toast("Imported", f"'{pretty(new_name)}' was saved for a different stage than the one selected.", "info")
        elif a_restore is not None and chosen is a_restore:
            if dialogs.confirm(self.host, "Restore the default?",
                               f"Your changes to '{pretty(name)}' will be replaced by the version that ships with "
                               f"Lucki's Macro.", "Restore", danger=True):
                preset_core.restore_default_preset(loc_, var_, name)
                self.host.log(f"Restored the default '{pretty(name)}'.")
                self.refresh_list(select=name, force=True)
        elif chosen is a_delete:
            self.delete_preset()

    def rename_preset(self):
        name = self.recording_name()
        if not name:
            return
        raw = dialogs.prompt_text(self.host, "Rename", "New name:", default=pretty(name), ok_text="Rename")
        if not raw:
            return
        new = preset_core.sanitize_preset_name(raw)
        if new == name:
            return
        if preset_core.is_reserved_name(new):
            dialogs.message(self.host, "Pick another name", "'Auto Play' is reserved.")
            return
        loc, var = self.slot()
        if os.path.exists(preset_core.preset_path(loc, var, new)):
            dialogs.message(self.host, "Already exists", f"There's already a recording called '{pretty(new)}'.")
            return
        os.rename(preset_core.preset_path(loc, var, name), preset_core.preset_path(loc, var, new))
        self.host.log(f"Renamed '{pretty(name)}' to '{pretty(new)}'.")
        self.refresh_list(select=new, force=True)

    def delete_preset(self):
        name = self.recording_name()
        if not name:
            return
        if self.host.user_settings.get("confirm_delete", True) and not dialogs.confirm(
                self.host, "Delete recording?", f"'{pretty(name)}' will be gone for good.", "Delete", danger=True):
            return
        loc, var = self.slot()
        try:
            os.remove(preset_core.preset_path(loc, var, name))
        except OSError:
            pass
        self.host.log(f"Deleted recording '{pretty(name)}'.")
        self.refresh_list(force=True)


# ----------------------------------------------------------------------------- runs

class RunsCard(StepCard):
    """
    How many matches to play before stopping - forever, a preset count, or any
    number.

    unit/unit_hint let a page reword what one "run" actually counts as - Challenges
    is the one case where it isn't one match (see ChallengesPage, and
    engine.run_challenges()'s own docstring): "Regular - All three" plus 1 run means
    all 3 slots played once, not 1 slot then stop, so the hint here says "pass"
    rather than "match" for that page specifically.

    choices lets a page cut this down to fewer options - Challenges again: a "pass"
    covers everything selected at once, so a count in between "once" and "forever"
    isn't a real use case there the way "50 runs" is for a single-match mode.
    """
    changed = Signal()
    queue_requested = Signal()

    def __init__(self, number, unit="match", unit_hint="", choices=None):
        super().__init__(number, "How many runs")
        self.unit = unit
        self.unit_hint = unit_hint
        choices = choices or [("0", "Forever"), ("50", "50"), ("999", "999"), ("custom", "Custom")]
        self.choice = Segmented(choices, choices[0][0])
        self.body.addWidget(self.choice)
        self.custom_row = QWidget()
        row = QHBoxLayout(self.custom_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(field("Stop after"))
        self.spin = QSpinBox()
        self.spin.setRange(1, 99999)
        self.spin.setValue(10)
        self.spin.setFixedWidth(96)
        row.addWidget(self.spin)
        row.addWidget(field("runs"))
        row.addStretch(1)
        self.body.addWidget(self.custom_row)
        self.hint = label("", "hint", wrap=True)
        self.body.addWidget(self.hint)
        self.queue_btn = Button("Add to queue", "subtle", icon="plus", height=30, font_px=12)
        self.queue_btn.setToolTip("Save this exact setup and number of runs as a step in the Queue")
        self.queue_btn.clicked.connect(self.queue_requested.emit)
        self.body.addWidget(self.queue_btn)
        self.choice.changed.connect(lambda _: self._sync(True))
        self.spin.valueChanged.connect(lambda _: self._sync(True))
        self._sync(False)
        self.set_done(True)

    def value(self):
        c = self.choice.value()
        return self.spin.value() if c == "custom" else int(c)

    def describe(self):
        v = self.value()
        return "forever" if v == 0 else f"{v} run{'s' if v != 1 else ''}"

    def _sync(self, emit):
        self.custom_row.setVisible(self.choice.value() == "custom")
        v = self.value()
        if v == 0:
            self.hint.setText("Keeps going until you press Stop.")
        else:
            plural = "es" if self.unit.endswith("h") else "s"
            suffix = f" {self.unit_hint}" if self.unit_hint else ""
            self.hint.setText(f"Stops by itself after {v} {self.unit}{plural if v != 1 else ''}.{suffix}")
        if emit:
            self.changed.emit()

    def state(self):
        return {"choice": self.choice.value(), "custom": self.spin.value()}

    def restore(self, data):
        self.spin.setValue(int(data.get("custom", 10) or 10))
        if data.get("choice") in ("0", "50", "999", "custom"):
            self.choice.set_value(data["choice"], animate=False)
        self._sync(False)


# ----------------------------------------------------------------------------- base page

class ModePage(QWidget):
    changed = Signal()
    key = ""

    # What one "run" on the shared RunsCard counts as, and which counts are offered -
    # see its own docstring. Both overridden by ChallengesPage.
    runs_unit = "match"
    runs_unit_hint = "Wins and losses both count."
    runs_choices = None

    def __init__(self, host):
        super().__init__()
        self.host = host
        self.picker = None
        self.runs = None
        self.cards = []
        self.col = QVBoxLayout(self)
        self.col.setContentsMargins(0, 0, 0, 0)
        self.col.setSpacing(10)

    def add_card(self, card):
        self.cards.append(card)
        self.col.addWidget(card)
        return card

    def finish(self):
        if self.runs is None and self.key not in ("others", "queue"):
            self.runs = self.add_card(RunsCard(len(self.cards) + 1, unit=self.runs_unit,
                                                unit_hint=self.runs_unit_hint, choices=self.runs_choices))
            self.runs.changed.connect(self.emit_changed)
            self.runs.queue_requested.connect(lambda: self.host.add_to_queue(self))
        self.col.addStretch(1)
        if self.picker is not None:
            self.picker.changed.connect(self.changed.emit)

    def emit_changed(self, *_):
        self.update_steps()
        self.changed.emit()

    def update_steps(self):
        pass

    def checks(self):
        if self.picker is None:
            return []
        state, detail = self.picker.check()
        return [("Units", state, detail)]

    def run_spec(self):
        raise NotImplementedError

    def run_limit(self):
        """Matches to play before stopping (0 = forever)."""
        return self.runs.value() if self.runs is not None else 0

    def full_state(self):
        d = dict(self.state())
        if self.runs is not None:
            d["runs"] = self.runs.state()
        return d

    def full_restore(self, data):
        self.restore(data)
        if self.runs is not None and isinstance(data.get("runs"), dict):
            self.runs.restore(data["runs"])

    def summary(self):
        return ""

    def state(self):
        return {"picker": self.picker.state()} if self.picker else {}

    def restore(self, data):
        if self.picker is not None and "picker" in data:
            self.picker.restore(data["picker"])

    def set_locked(self, locked):
        for card in self.cards:
            card.setEnabled(not locked)

    def on_shown(self):
        if self.picker is not None:
            self.picker.refresh_list(force=True)

    def units_label(self):
        if self.picker is None:
            return "Auto Play"
        return "Auto Play" if self.picker.uses_auto() else f"recording '{pretty(self.picker.recording_name())}'"

    def require_units(self):
        name = self.picker.selected_name()
        if not name:
            raise ValueError("Pick a recording for your units, or switch to the game's Auto Play.")
        if name != AUTO:
            loc, var = self.picker.slot()
            if not preset_core.load_actions(loc, var, name):
                raise ValueError(f"The recording '{pretty(name)}' has no steps yet. Press Record (F8) and place "
                                 f"your units in Roblox first.")
        return name


# ----------------------------------------------------------------------------- story

class StoryPage(ModePage):
    key = "story"

    def __init__(self, host):
        super().__init__(host)
        c1 = self.add_card(StepCard(1, "Which stage"))
        self.banner = ArtBanner()
        c1.body.addWidget(self.banner)
        self.map_box = combo([(m["label"], k) for k, m in config.MAPS.items() if m["enabled"]])
        c1.body.addWidget(self.map_box)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.act_box = shrinkable_combo(QComboBox())
        row.addWidget(self.act_box, 1)
        self.difficulty = Segmented([("Normal", "Normal"), ("Hard", "Hard")], "Normal")
        row.addWidget(self.difficulty, 1)
        c1.body.addLayout(row)
        self.auto_next = ToggleRow("Climb to the next act on every win",
                                   "Uses a recording with the same name on each act when there is one, Auto Play where there isn't.")
        c1.body.addWidget(self.auto_next)
        c1.set_done(True)

        self.picker = self.add_card(PresetPicker(host, 2, "How units get placed", self.slot, can_copy=True))
        self.map_box.currentIndexChanged.connect(self._on_map)
        self.act_box.currentIndexChanged.connect(self._on_slot)
        self.difficulty.changed.connect(self.emit_changed)
        self.auto_next.toggled.connect(self.emit_changed)
        self._fill_acts()
        self.finish()
        self.update_steps()

    def slot(self):
        return self.map_box.currentData(), self.act_box.currentData()

    def _fill_acts(self, select=None):
        self.act_box.blockSignals(True)
        self.act_box.clear()
        m = self.map_box.currentData()
        if m in config.MAPS:
            for k, a in config.MAPS[m]["acts"].items():
                if a["enabled"]:
                    self.act_box.addItem(a["label"], k)
        if select:
            set_combo(self.act_box, select)
        self.act_box.blockSignals(False)

    def _on_map(self, *_):
        self._fill_acts(self.act_box.currentData())
        self._on_slot()

    def _on_slot(self, *_):
        self.picker.refresh_list(force=True)
        self.emit_changed()

    def update_steps(self):
        m = self.map_box.currentData()
        self.banner.set_art("story_maps", m, self.map_box.currentText(),
                            f"{self.act_box.currentText()} · {self.difficulty.value()}")

    def run_spec(self):
        m, a = self.slot()
        name = self.require_units()
        auto_next = self.auto_next.isChecked()
        fields = [("Gamemode", "Story"), ("Preset", name), ("Difficulty", self.difficulty.value())]
        if auto_next:
            fields.append(("Auto Next", "on"))
        return "run_bot", ("story", m, a, self.difficulty.value().lower(), name, auto_next), fields

    def summary(self):
        climb = ", climbing acts" if self.auto_next.isChecked() else ""
        return f"{self.map_box.currentText()} {self.act_box.currentText()} ({self.difficulty.value()}{climb}) with {self.units_label()}"

    def state(self):
        d = super().state()
        d.update(map=self.map_box.currentData(), act=self.act_box.currentData(),
                 difficulty=self.difficulty.value(), auto_next=self.auto_next.isChecked())
        return d

    def restore(self, data):
        set_combo(self.map_box, data.get("map"))
        self._fill_acts(data.get("act"))
        self.difficulty.set_value(data.get("difficulty", "Normal"), animate=False)
        self.auto_next.setChecked(bool(data.get("auto_next", False)))
        super().restore(data)


# ----------------------------------------------------------------------------- raids

class RaidsPage(ModePage):
    key = "raids"

    def __init__(self, host):
        super().__init__(host)
        c1 = self.add_card(StepCard(1, "Which raid"))
        self.banner = ArtBanner()
        c1.body.addWidget(self.banner)
        self.raid_box = combo([(r["label"], k) for k, r in config.RAIDS.items() if r["enabled"]])
        c1.body.addWidget(self.raid_box)
        c1.body.addWidget(field("Difficulty"))
        self.difficulty = Segmented([("1", "1"), ("2", "2"), ("3", "3 - hardest")], "1")
        c1.body.addWidget(self.difficulty)
        c1.set_done(True)
        self.picker = self.add_card(PresetPicker(host, 2, "How units get placed", self.slot))
        self.raid_box.currentIndexChanged.connect(self._on_slot)
        self.difficulty.changed.connect(self._on_slot)
        self.finish()
        self.update_steps()

    def slot(self):
        return self.raid_box.currentData(), self.difficulty.value()

    def _on_slot(self, *_):
        self.picker.refresh_list(force=True)
        self.emit_changed()

    def update_steps(self):
        self.banner.set_art("raids", self.raid_box.currentData(), self.raid_box.currentText(),
                            f"Difficulty {self.difficulty.value()}")

    def run_spec(self):
        r, d = self.slot()
        name = self.require_units()
        return "run_bot", ("raids", r, d, d, name, False), [("Gamemode", "Raids"), ("Preset", name)]

    def summary(self):
        return f"{self.raid_box.currentText()}, difficulty {self.difficulty.value()}, with {self.units_label()}"

    def state(self):
        d = super().state()
        d.update(raid=self.raid_box.currentData(), difficulty=self.difficulty.value())
        return d

    def restore(self, data):
        set_combo(self.raid_box, data.get("raid"))
        self.difficulty.set_value(data.get("difficulty", "1"), animate=False)
        super().restore(data)


# ----------------------------------------------------------------------------- challenges

class ChallengesPage(ModePage):
    key = "challenges"
    # See engine.run_challenges()'s own docstring: "runs" here counts full passes
    # through the selected challenges, not individual matches. Once or Forever only -
    # a count in between isn't a real use case when one "run" already means every
    # selected slot, not a single match.
    runs_unit = "pass"
    runs_unit_hint = "Each pass plays every selected challenge once."
    runs_choices = [("0", "Forever"), ("1", "Once")]

    def __init__(self, host):
        super().__init__(host)
        c1 = self.add_card(StepCard(1, "Which challenges"))
        row = QHBoxLayout()
        row.setSpacing(6)
        self.cat_chips = {}
        for key, text, on in (("regular", "Regular", True), ("daily", "Daily", False), ("weekly", "Weekly", False)):
            chip = Chip(text, on)
            chip.toggled.connect(self.emit_changed)
            row.addWidget(chip)
            self.cat_chips[key] = chip
        row.addStretch(1)
        c1.body.addLayout(row)
        self.regular_label = field("Regular challenges to play")
        c1.body.addWidget(self.regular_label)
        self.regular = Segmented([("All", "All three"), ("1", "1"), ("2", "2"), ("3", "3")], "All")
        self.regular.changed.connect(self.emit_changed)
        c1.body.addWidget(self.regular)
        self.c1 = c1

        c2 = self.add_card(StepCard(2, "Units for each challenge"))
        self.links_info = label("", "hint", wrap=True)
        c2.body.addWidget(self.links_info)
        links = Button("Choose setups…", "subtle", icon="list", height=34, font_px=12)
        links.clicked.connect(self._edit_links)
        c2.body.addWidget(links)
        c2.set_done(True)

        self.finish()
        self.update_steps()

    def sequence(self):
        seq = []
        if self.cat_chips["regular"].isChecked():
            mode = self.regular.value()
            seq += ["regular_1", "regular_2", "regular_3"] if mode == "All" else [f"regular_{mode}"]
        if self.cat_chips["daily"].isChecked():
            seq.append("daily")
        if self.cat_chips["weekly"].isChecked():
            seq.append("weekly")
        return seq

    def _links_summary(self):
        linked, missing = 0, 0
        for slot in config.CHALLENGE_SLOTS:
            link = challenge_links.get_link(slot)
            if link.get("preset") and link["preset"] != AUTO:
                linked += 1
                if not os.path.exists(preset_core.preset_path(link.get("map"), link.get("act"), link["preset"])):
                    missing += 1
        return linked, missing

    def update_steps(self):
        regular = self.cat_chips["regular"].isChecked()
        self.regular.setVisible(regular)
        self.regular_label.setVisible(regular)
        self.c1.set_done(bool(self.sequence()))
        linked, missing = self._links_summary()
        total = len(config.CHALLENGE_SLOTS)
        if linked == 0:
            text = "All challenges use the game's Auto Play."
        else:
            text = f"{linked} use your Story recordings, {total - linked} use Auto Play."
        if missing:
            text += f" {missing} linked recording{'s are' if missing != 1 else ' is'} missing."
        self.links_info.setText(text)

    def _edit_links(self):
        if dialogs.challenge_links_dialog(self.host):
            self.host.log("Challenge setups saved.")
        self.emit_changed()

    def checks(self):
        linked, missing = self._links_summary()
        if missing:
            return [("Setups", "warn", f"{missing} linked recording missing")]
        return [("Setups", "ok", "Auto Play" if linked == 0 else f"{linked} recordings linked")]

    def run_spec(self):
        seq = self.sequence()
        if not seq:
            raise ValueError("Pick at least one of Regular, Daily or Weekly.")
        slot_links = {k: challenge_links.get_link(k) for k in seq}
        # "How many runs" (below, added by finish()) counts PASSES through this whole
        # selection, not individual slots - see engine.run_challenges()'s own
        # docstring. "Forever" there is what keeps checking through a cooldown.
        return "run_challenges", (seq, slot_links), [
            ("Gamemode", "Challenges"), ("Challenges", ", ".join(
                config.CHALLENGE_SLOTS[k]["label"] for k in seq))]

    def summary(self):
        names = [config.CHALLENGE_SLOTS[k]["label"] for k in self.sequence()]
        return ", ".join(names) or "nothing picked"

    def state(self):
        return {"cats": {k: c.isChecked() for k, c in self.cat_chips.items()},
                "regular": self.regular.value()}

    def restore(self, data):
        for k, v in data.get("cats", {}).items():
            if k in self.cat_chips:
                self.cat_chips[k].setChecked(bool(v))
        self.regular.set_value(data.get("regular", "All"), animate=False)
        self.update_steps()

    def on_shown(self):
        self.update_steps()


# ----------------------------------------------------------------------------- portals

class PortalsPage(ModePage):
    key = "portals"

    def __init__(self, host):
        super().__init__(host)
        c1 = self.add_card(StepCard(1, "Which portal"))
        self.banner = ArtBanner(height=104, fit="contain")
        c1.body.addWidget(self.banner)
        self.portal_box = combo([(p["label"], k) for k, p in config.PORTALS.items() if p["enabled"]])
        c1.body.addWidget(self.portal_box)
        c1.body.addWidget(label("Always enters the best one of this kind you own.", "hint", wrap=True))
        c1.set_done(True)

        self.picker = self.add_card(PresetPicker(host, 2, "How units get placed", self.slot))

        c3 = self.add_card(StepCard(3, "Extras", label("optional", "hint")))
        self.traitless = ToggleRow("Skip Traitless rewards", "Hovers each reward and never picks a Traitless one.", True)
        self.traitless.toggled.connect(self.emit_changed)
        c3.body.addWidget(self.traitless)
        # Summer/Sovereign always fish - no toggle needed, since there's no real reason
        # to enter one of these and NOT fish. The rod-equip mode and cast point are
        # still configurable below.
        # The rod button is a toggle whose state can't be read off the screen, so the bot
        # only clicks it when told to - otherwise it can switch a working rod off. Off by
        # default: equip it yourself once and the bot never touches that button again.
        self.rod = ToggleRow("Equip the rod for me",
                             "Off: equip your Auto Rod yourself before pressing Start - the bot only casts, never "
                             "touching that button. On: the bot clicks it once, the first time it sees it - only "
                             "turn this on if the rod ISN'T already equipped, since clicking it while equipped "
                             "switches it off.")
        self.rod.toggled.connect(self.emit_changed)
        c3.body.addWidget(self.rod)
        self.fish_coords_label = label(
            "Casts here after Start Game, and keeps re-casting at the same spot - always on for these "
            "portals, no setup needed. Change it below only if your fishing spot sits somewhere else.",
            "hint", wrap=True)
        c3.body.addWidget(self.fish_coords_label)
        self.fish_coords_row = QWidget()
        coords_row = QHBoxLayout(self.fish_coords_row)
        coords_row.setContentsMargins(0, 0, 0, 0)
        coords_row.addWidget(field("X"))
        self.fish_x = QSpinBox()
        self.fish_x.setRange(0, 4000)
        self.fish_x.setValue(config.PORTAL_AUTOPLAY_FISH_X_DEFAULT)
        self.fish_x.setFixedWidth(80)
        self.fish_x.valueChanged.connect(self.emit_changed)
        coords_row.addWidget(self.fish_x)
        coords_row.addWidget(field("Y"))
        self.fish_y = QSpinBox()
        self.fish_y.setRange(0, 4000)
        self.fish_y.setValue(config.PORTAL_AUTOPLAY_FISH_Y_DEFAULT)
        self.fish_y.setFixedWidth(80)
        self.fish_y.valueChanged.connect(self.emit_changed)
        coords_row.addWidget(self.fish_y)
        coords_row.addStretch(1)
        locate = IconButton("target", "Locate")
        locate.setToolTip("Click the fishing spot in Roblox to fill these in.")
        locate.setFocusPolicy(Qt.NoFocus)
        locate.clicked.connect(self._locate_fish_spot)
        coords_row.addWidget(locate)
        c3.body.addWidget(self.fish_coords_row)
        self.fish_hint = label("Fishing: Summer and Sovereign portals only.", "hint", wrap=True)
        c3.body.addWidget(self.fish_hint)
        c3.set_done(True)
        # Summer and Sovereign are different maps with the fishing spot in a
        # different place on screen - one shared X/Y used to silently mis-cast on
        # whichever of the two wasn't used to measure the default. Kept per category
        # so tuning one can't quietly break the other - see _save_fish_coords()/
        # _load_fish_coords() below, swapped on every portal change.
        self._fish_coords_by_category = {}
        self._fish_coords_category = self.portal_box.currentData()
        self.portal_box.currentIndexChanged.connect(self._on_portal_changed)
        self.picker.changed.connect(self.emit_changed)
        self.finish()
        self.update_steps()

    def _locate_fish_spot(self):
        def picked(xy):
            if not xy:
                return
            x, y = xy
            self.fish_x.setValue(x)
            self.fish_y.setValue(y)

        self.host._pick_screen_click(
            picked, "Click the fishing spot in Roblox. Esc cancels.")

    def slot(self):
        return config.PORTAL_PRESET_LOCATION, self.portal_box.currentData()

    def can_fish(self):
        return self.portal_box.currentData() in PORTAL_FISH_CATEGORIES

    def _save_fish_coords(self, category):
        if category:
            self._fish_coords_by_category[category] = (self.fish_x.value(), self.fish_y.value())

    def _load_fish_coords(self, category):
        x, y = self._fish_coords_by_category.get(
            category, (config.PORTAL_AUTOPLAY_FISH_X_DEFAULT, config.PORTAL_AUTOPLAY_FISH_Y_DEFAULT))
        for spin, value in ((self.fish_x, x), (self.fish_y, y)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _on_portal_changed(self, _index):
        self._save_fish_coords(self._fish_coords_category)
        self._fish_coords_category = self.portal_box.currentData()
        self._load_fish_coords(self._fish_coords_category)
        self.picker.refresh_list(force=True)
        self.emit_changed()

    def update_steps(self):
        self.banner.set_art("portals", self.portal_box.currentData(), self.portal_box.currentText())
        can_fish = self.can_fish()
        self.fish_hint.setVisible(not can_fish)
        self.rod.setVisible(can_fish)
        self.fish_coords_label.setVisible(can_fish)
        self.fish_coords_row.setVisible(can_fish)

    def run_spec(self):
        cat = self.portal_box.currentData()
        if not cat:
            raise ValueError("Pick a portal first.")
        name = self.require_units()
        fish = self.can_fish()
        preset = None if name == AUTO else name
        equip_rod = fish and self.rod.isChecked()
        fish_pos = (self.fish_x.value(), self.fish_y.value()) if fish else None
        return "run_portal", (cat, fish, self.traitless.isChecked(), preset, equip_rod, fish_pos), [
            ("Gamemode", "Portals"), ("Portal", cat), ("Units", name)]

    def summary(self):
        extras = []
        if self.traitless.isChecked():
            extras.append("skipping Traitless")
        if self.can_fish():
            extras.append("fishing")
        return self.portal_box.currentText() + (f" ({', '.join(extras)})" if extras else "") + f" with {self.units_label()}"

    def state(self):
        self._save_fish_coords(self._fish_coords_category)
        return {"portal": self.portal_box.currentData(), "traitless": self.traitless.isChecked(),
                "picker": self.picker.state(), "rod": self.rod.isChecked(),
                "fish_coords": {cat: list(xy) for cat, xy in self._fish_coords_by_category.items()}}

    def restore(self, data):
        set_combo(self.portal_box, data.get("portal"))
        self.traitless.setChecked(bool(data.get("traitless", True)))
        # "rod" used to be the Segmented control's "self"/"auto" string; still read
        # for anyone upgrading from a settings.json saved before it became a toggle.
        rod = data.get("rod")
        self.rod.setChecked(rod is True or rod == "auto")
        self._fish_coords_by_category = {cat: tuple(xy) for cat, xy in data.get("fish_coords", {}).items()}
        # fish_x/fish_y (flat, no per-category split) and autoplay_fish_x/y (the old
        # per-toggle names before that) - both read as this portal's own value, for
        # anyone upgrading from a settings.json saved before coords were per-category.
        cat = self.portal_box.currentData()
        if cat not in self._fish_coords_by_category and ("fish_x" in data or "autoplay_fish_x" in data):
            self._fish_coords_by_category[cat] = (
                int(data.get("fish_x", data.get("autoplay_fish_x", config.PORTAL_AUTOPLAY_FISH_X_DEFAULT))),
                int(data.get("fish_y", data.get("autoplay_fish_y", config.PORTAL_AUTOPLAY_FISH_Y_DEFAULT))))
        self._fish_coords_category = cat
        self._load_fish_coords(cat)
        if isinstance(data.get("picker"), dict):
            self.picker.restore(data["picker"])
        self.update_steps()


# ----------------------------------------------------------------------------- expeditions

class ExpeditionsPage(ModePage):
    key = "expeditions"

    def __init__(self, host):
        super().__init__(host)
        c1 = self.add_card(StepCard(1, "Expedition"))
        grid = QGridLayout()
        grid.setSpacing(6)
        self.map_group = QButtonGroup(self)
        self.map_cards = {}
        for i, (k, e) in enumerate((k, e) for k, e in config.EXPEDITIONS.items() if e["enabled"]):
            card = ImageCard(k, e["label"], art_path("expeditions", k) or e.get("template"))
            self.map_group.addButton(card)
            grid.addWidget(card, i // 2, i % 2)
            self.map_cards[k] = card
        c1.body.addLayout(grid)
        first = next(iter(self.map_cards.values()), None)
        if first:
            first.setChecked(True)
        c1.set_done(True)

        c2 = self.add_card(StepCard(2, "Difficulty and loot"))
        self.difficulty = Segmented([("1", "1"), ("2", "2"), ("3", "3 - hardest")], "3")
        self.difficulty.changed.connect(self.emit_changed)
        c2.body.addWidget(self.difficulty)
        self.material_flow = FlowRow()
        self.material_group = QButtonGroup(self)
        self.material_chips = {}
        for k, m in list(config.EXPEDITION_MATERIALS.items()) + [(config.EXPEDITION_RANDOM_MATERIAL, {"label": "Random"})]:
            chip = Chip(m["label"])
            self.material_group.addButton(chip)
            self.material_flow.add(chip)
            self.material_chips[k] = chip
        c2.body.addWidget(self.material_flow)
        self.material_hint = label("", "hint", wrap=True)
        c2.body.addWidget(self.material_hint)
        c2.set_done(True)

        self.picker = self.add_card(PresetPicker(host, 3, "Unit placement", self.slot, allow_auto=False,
                                                 trailing_text="needed here"))
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(field("Camera zoom-out"), 1)
        saved = host.user_settings.get("expedition_zoom_out_steps")
        self.zoom = int(saved) if isinstance(saved, (int, float)) else config.EXPEDITION_CAMERA_ZOOM_OUT_STEPS
        self.zoom_value = label(str(self.zoom), "title")
        zoom_row.addWidget(self.zoom_value)
        self.picker.body.addLayout(zoom_row)
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(0, config.CAMERA_ZOOM_OUT_STEPS)
        self.zoom_slider.setValue(self.zoom)
        self.zoom_slider.setToolTip("How far the camera zooms out before placing units. "
                                    "Change it and your recording's spots will be off - re-record after.")
        self.zoom_slider.valueChanged.connect(self._on_zoom)
        self.picker.body.addWidget(self.zoom_slider)
        self.picker.extra_info = f"camera {self.zoom}"

        self.map_group.buttonClicked.connect(self._on_map)
        self.material_group.buttonClicked.connect(lambda _: self.emit_changed())
        self._select_default_material()
        self.finish()

    def slot(self):
        return config.EXPEDITION_PRESET_LOCATION, config.EXPEDITION_PRESET_VARIANT

    def expedition_key(self):
        for k, c in self.map_cards.items():
            if c.isChecked():
                return k
        return None

    def material_key(self):
        for k, c in self.material_chips.items():
            if c.isChecked():
                return k
        return config.EXPEDITION_RANDOM_MATERIAL

    def _select_default_material(self):
        k = self.expedition_key()
        if k:
            self.material_chips[config.EXPEDITIONS[k]["material"]].setChecked(True)
        self.update_steps()

    def _on_map(self, *_):
        self._select_default_material()
        self.emit_changed()

    def _on_zoom(self, value):
        self.zoom = int(value)
        self.zoom_value.setText(str(self.zoom))
        self.picker.extra_info = f"camera {self.zoom}"
        self.picker._update_info()
        self.host.user_settings["expedition_zoom_out_steps"] = self.zoom
        self._save_timer = getattr(self, "_save_timer", None) or QTimer(self, singleShot=True, interval=600)
        try:
            self._save_timer.timeout.disconnect()
        except (RuntimeError, TypeError):
            pass
        self._save_timer.timeout.connect(lambda: settings.save(self.host.user_settings))
        self._save_timer.start()
        self.changed.emit()

    def update_steps(self):
        k = self.expedition_key()
        mat = self.material_key()
        if mat == config.EXPEDITION_RANDOM_MATERIAL:
            text = "Takes whichever route comes first - the quickest runs."
        elif k and config.EXPEDITIONS[k]["material"] == mat:
            text = f"{config.EXPEDITION_MATERIALS[mat]['label']} drops on this map, so it's picked for you."
        else:
            text = f"Picks the route with the most {config.EXPEDITION_MATERIALS[mat]['label']} each run."
        self.material_hint.setText(text)

    def run_spec(self):
        k = self.expedition_key()
        if not k:
            raise ValueError("Pick an expedition first.")
        name = self.picker.recording_name()
        if not name or not preset_core.load_actions(*self.slot(), name):
            raise ValueError("Expeditions don't have Auto Play, so they need a unit placement recording. "
                             "Click New, then press Record (F8) inside an expedition and place your units.")
        mat = self.material_key()
        mat_label = "Random" if mat == config.EXPEDITION_RANDOM_MATERIAL else config.EXPEDITION_MATERIALS[mat]["label"]
        fields = [("Gamemode", "Expeditions"), ("Expedition", config.EXPEDITIONS[k]["label"]),
                  ("Difficulty", self.difficulty.value()), ("Farming", mat_label), ("Macro", name),
                  ("Camera zoom-out", str(self.zoom))]
        return "run_expedition", (k, self.difficulty.value(), mat, name, self.zoom), fields

    def summary(self):
        k = self.expedition_key()
        mat = self.material_key()
        mat_label = "anything" if mat == config.EXPEDITION_RANDOM_MATERIAL else config.EXPEDITION_MATERIALS[mat]["label"]
        return f"{config.EXPEDITIONS[k]['label'] if k else '-'}, difficulty {self.difficulty.value()}, farming {mat_label}"

    def state(self):
        d = super().state()
        d.update(expedition=self.expedition_key(), difficulty=self.difficulty.value(), material=self.material_key())
        return d

    def restore(self, data):
        if data.get("expedition") in self.map_cards:
            self.map_cards[data["expedition"]].setChecked(True)
        self.difficulty.set_value(data.get("difficulty", "3"), animate=False)
        if data.get("material") in self.material_chips:
            self.material_chips[data["material"]].setChecked(True)
        else:
            self._select_default_material()
        self.update_steps()
        super().restore(data)


class OthersPage(ModePage):
    key = "others"

    def __init__(self, host):
        super().__init__(host)
        c = self.add_card(StepCard(1, "Coming soon"))
        c.body.addWidget(label("Nothing to farm here yet.", "hint"))
        self.finish()

    def checks(self):
        return [("Mode", "bad", "not available yet")]

    def run_spec(self):
        raise ValueError("This mode isn't available yet.")

    def summary(self):
        return "Not available yet"


# ----------------------------------------------------------------------------- queue

class QueueRow(QWidget):
    def __init__(self, index, item, count, on_move, on_remove):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        num = QLabel(str(index + 1))
        num.setFixedSize(22, 22)
        num.setAlignment(Qt.AlignCenter)
        num.setStyleSheet(f"background: {theme.accent()}; color: {theme.on_accent()}; border-radius: 11px; "
                          f"font-weight: 700; font-size: 11px;")
        row.addWidget(num, 0, Qt.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        mode_label = config.GAMEMODES.get(item.get("mode"), {}).get("label", item.get("mode", "?"))
        texts.addWidget(label(f"{mode_label} · {item.get('runs_text', '')}", "title"))
        texts.addWidget(label(item.get("summary", ""), "hint", wrap=True))
        row.addLayout(texts, 1)
        for icon, fn, enabled, tip in (
            ("chevron_up", lambda: on_move(index, -1), index > 0, "Move up"),
            ("chevron_down", lambda: on_move(index, 1), index < count - 1, "Move down"),
            ("trash", lambda: on_remove(index), True, "Remove this step"),
        ):
            b = IconButton(icon, "", size=14)
            b.setFixedWidth(26)
            b.setToolTip(tip)
            b.setEnabled(enabled)
            b.clicked.connect(fn)
            row.addWidget(b, 0, Qt.AlignTop)


class QueuePage(ModePage):
    """
    A list of steps - each a saved mode setup plus a number of runs - played in order:
    e.g. 55 portals, then every challenge, then 30 portals. Steps are added from any
    mode's How many runs card.
    """
    key = "queue"

    def __init__(self, host):
        super().__init__(host)
        self.items = []
        c1 = self.add_card(StepCard(1, "Steps, in order"))
        self.list_host = QWidget()
        self.list_col = QVBoxLayout(self.list_host)
        self.list_col.setContentsMargins(0, 0, 0, 0)
        self.list_col.setSpacing(10)
        c1.body.addWidget(self.list_host)
        self.empty = label("Nothing queued yet. Set up a mode (Portals, Challenges...), choose its number of runs, "
                           "then press “Add to queue” on its How many runs card.", "hint", wrap=True)
        c1.body.addWidget(self.empty)
        self.clear_btn = Button("Clear queue", "ghost", icon="trash", height=30, font_px=12)
        self.clear_btn.clicked.connect(self.clear)
        c1.body.addWidget(self.clear_btn, 0, Qt.AlignLeft)

        c2 = self.add_card(StepCard(2, "When it reaches the end"))
        self.repeat = ToggleRow("Start again from the top", "Off: stop after the last step.", False)
        self.repeat.toggled.connect(self.emit_changed)
        c2.body.addWidget(self.repeat)
        c2.body.addWidget(label("Beta - between steps the bot goes back to the lobby before starting the next "
                                "mode. If it can't find the way, the queue stops and Activity says where.",
                                "hint", wrap=True))
        self.skip_stuck = ToggleRow("Skip a step that gets stuck", "Off: a stuck step ends the whole queue.", False)
        self.skip_stuck.toggled.connect(self.emit_changed)
        c2.body.addWidget(self.skip_stuck)
        c2.set_done(True)
        self.finish()
        self._rebuild()

    def add_item(self, item):
        self.items.append(item)
        self._rebuild()
        self.emit_changed()

    def clear(self):
        self.items = []
        self._rebuild()
        self.emit_changed()

    def _move(self, i, d):
        j = i + d
        # Both ends checked: a row's buttons can still be clicked for a moment after a
        # step above it was removed, and then `i` points past the end of the list.
        if 0 <= i < len(self.items) and 0 <= j < len(self.items):
            self.items[i], self.items[j] = self.items[j], self.items[i]
            self._rebuild()
            self.emit_changed()

    def _remove(self, i):
        if 0 <= i < len(self.items):
            del self.items[i]
            self._rebuild()
            self.emit_changed()

    def _rebuild(self):
        while self.list_col.count():
            w = self.list_col.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for i, item in enumerate(self.items):
            self.list_col.addWidget(QueueRow(i, item, len(self.items), self._move, self._remove))
        self.list_host.setVisible(bool(self.items))
        self.empty.setVisible(not self.items)
        self.clear_btn.setVisible(bool(self.items))
        self.cards[0].set_done(bool(self.items))

    def _endless(self, item):
        if item.get("runs"):
            return False
        if item.get("mode") == "challenges":
            return bool(item.get("state", {}).get("loop", True))
        return True

    def checks(self):
        if not self.items:
            return [("Queue", "bad", "no steps yet")]
        rows = [("Queue", "ok", f"{len(self.items)} step{'s' if len(self.items) != 1 else ''}")]
        for i, item in enumerate(self.items):
            if self._endless(item) and (i < len(self.items) - 1 or self.repeat.isChecked()):
                rows.append(("Endless step", "warn", f"step {i + 1} never ends - set its runs"))
                break
        return rows

    def run_spec(self):
        if not self.items:
            raise ValueError("The queue is empty. Add steps with “Add to queue” on any mode's How many runs card.")
        return "queue", (), None

    def summary(self):
        if not self.items:
            return "Empty queue"
        parts = [f"{config.GAMEMODES.get(it['mode'], {}).get('label', it['mode'])} ({it.get('runs_text', '')})"
                 for it in self.items]
        return " → ".join(parts) + (", then again" if self.repeat.isChecked() else "")

    def state(self):
        return {"items": self.items, "repeat": self.repeat.isChecked(), "skip_stuck": self.skip_stuck.isChecked()}

    def restore(self, data):
        self.items = [i for i in (data.get("items") or []) if isinstance(i, dict) and i.get("mode") in PAGE_CLASSES]
        self.repeat.setChecked(bool(data.get("repeat", False)))
        self.skip_stuck.setChecked(bool(data.get("skip_stuck", False)))
        self._rebuild()


PAGE_CLASSES = {
    "story": StoryPage, "raids": RaidsPage, "challenges": ChallengesPage,
    "portals": PortalsPage, "expeditions": ExpeditionsPage, "others": OthersPage,
    "queue": QueuePage,
}
