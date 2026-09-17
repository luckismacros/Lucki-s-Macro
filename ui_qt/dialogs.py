# ui_qt/dialogs.py
"""
Every pop-up in the Qt window: prompts, confirmations, preset import/export/copy,
challenge links, and the Settings sheet.

They are all separate top-level windows rather than overlays drawn inside the main
window. That is not a style choice: while Roblox is docked, the game area is a real
hole cut out of the main window, and nothing the main window paints can ever appear
there - an in-window overlay would be missing a 1600x900 chunk out of its middle.
A top-level dialog floats over the game like any other window.
"""
import json
import os
import subprocess
import threading

from PySide6.QtCore import Qt, QTimer, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QPainter, QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QFileDialog, QSlider, QSpinBox, QDoubleSpinBox, QStackedWidget,
    QGridLayout, QAbstractButton,
)

import config
import settings
import logger
from modules import preset_core, challenge_links, notify
from . import theme
from .widgets import (
    Button, IconButton, ToggleRow, Segmented, label, add_shadow,
    Chip, with_alpha, VScroll,
)


# ----------------------------------------------------------------------------- base

class FloatingDialog(QDialog):
    """
    A dialog that covers the main window's whole area as ONE translucent top-level
    window: it paints the dim veil itself and slides a rounded card into place on top
    of it. placement: "center" (the card rises into the middle) or "right" (a full
    height side sheet sliding in from the right edge). Clicking the veil closes it.

    One window rather than a dialog plus a separate dim window: a second always-on-
    top window has to win a z-order race against both the panel and the docked game
    every time, and in practice it didn't - the dim never showed.
    """

    def __init__(self, parent, title="", width=440, placement="center", closable=True):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.placement = placement
        self._width = width
        self._closable = closable
        self._t = 0.0
        self._anim = None

        self.card = QFrame(self)
        self.card.setObjectName("dialogCard")
        radius = 0 if placement == "right" else theme.RADIUS_CARD + 2
        border = f"border-left: 1px solid {theme.BORDER};" if placement == "right" else f"border: 1px solid {theme.BORDER};"
        self.card.setStyleSheet(f"#dialogCard {{ background: {theme.BG_CARD}; {border} border-radius: {radius}px; }}")
        add_shadow(self.card, blur=50, alpha=0.6, dy=16 if placement != "right" else 0)

        self.body = QVBoxLayout(self.card)
        self.body.setContentsMargins(22, 18, 22, 22)
        self.body.setSpacing(12)

        if title:
            head = QHBoxLayout()
            self.title_label = label(title, "big")
            head.addWidget(self.title_label, 1)
            if closable:
                close = IconButton("close", "", size=16)
                close.setFixedWidth(32)
                close.clicked.connect(self.reject)
                head.addWidget(close)
            self.body.addLayout(head)

    # --- geometry & motion

    def _host_rect(self):
        parent = self.parentWidget()
        if parent is not None and parent.isVisible():
            return parent.window().frameGeometry()
        return QGuiApplication.primaryScreen().availableGeometry()

    def _layout_card(self):
        t = self._t
        w, h = self.width(), self.height()
        if self.placement == "right":
            cw = min(self._width, w)
            x = w - cw + int(70 * (1 - t))
            self.card.setGeometry(x, 0, cw, h)
        else:
            cw = min(self._width, w - 40)
            ch = max(self.card.minimumHeight(), self.card.sizeHint().height())
            ch = min(ch, h - 40)
            x = (w - cw) // 2
            y = (h - ch) // 2 + int(16 * (1 - t))
            self.card.setGeometry(x, y, cw, ch)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_card()

    def showEvent(self, event):
        self.setGeometry(self._host_rect())
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        if theme.reduce_motion():
            self._set_t(1.0)
            return
        self._set_t(0.0)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(320 if self.placement == "right" else 240)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(lambda v: self._set_t(float(v)))
        self._anim.start()

    def _set_t(self, t):
        self._t = t
        self.setWindowOpacity(t)
        self._layout_card()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), with_alpha("#05050a", 0.55))
        p.end()

    def mousePressEvent(self, event):
        if self._closable and not self.card.geometry().contains(event.position().toPoint()):
            self.reject()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and not self._closable:
            return
        super().keyPressEvent(event)

    def add_buttons(self, ok_text="OK", cancel_text="Cancel", danger=False):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        if cancel_text:
            cancel = Button(cancel_text, "ghost", height=36)
            cancel.setMinimumWidth(96)
            cancel.clicked.connect(self.reject)
            row.addWidget(cancel)
        ok = Button(ok_text, "danger" if danger else "accent", height=36)
        ok.setMinimumWidth(110)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        self.body.addLayout(row)
        return ok


# ----------------------------------------------------------------------------- simple prompts

def prompt_text(parent, title, text, default="", placeholder="", ok_text="OK"):
    dlg = FloatingDialog(parent, title, width=400)
    msg = label(text, "muted", wrap=True)
    dlg.body.addWidget(msg)
    edit = QLineEdit(default)
    edit.setPlaceholderText(placeholder)
    edit.setMinimumHeight(38)
    edit.selectAll()
    dlg.body.addWidget(edit)
    ok = dlg.add_buttons(ok_text)
    edit.returnPressed.connect(dlg.accept)
    ok.setEnabled(bool(default.strip()))
    edit.textChanged.connect(lambda t: ok.setEnabled(bool(t.strip())))
    QTimer.singleShot(0, edit.setFocus)
    if dlg.exec() == QDialog.Accepted and edit.text().strip():
        return edit.text().strip()
    return None


def confirm(parent, title, text, ok_text="OK", danger=False, cancel_text="Cancel"):
    dlg = FloatingDialog(parent, title, width=420)
    dlg.body.addWidget(label(text, "muted", wrap=True))
    dlg.add_buttons(ok_text, cancel_text, danger=danger)
    return dlg.exec() == QDialog.Accepted


def message(parent, title, text, ok_text="Got it"):
    dlg = FloatingDialog(parent, title, width=420)
    dlg.body.addWidget(label(text, "muted", wrap=True))
    dlg.add_buttons(ok_text, None)
    dlg.exec()


def _field(text):
    lbl = label(text)
    lbl.setStyleSheet(f"font-size: 12px; color: {theme.TEXT_MUTED};")
    return lbl


# ----------------------------------------------------------------------------- import / export

def export_stage_preset(parent, location_key, variant_key, preset_name):
    """Same file format as modules/preset_share.export_stage_preset."""
    source = preset_core.preset_path(location_key, variant_key, preset_name)
    if not os.path.exists(source):
        message(parent, "Export", f"That recording's file is missing:\n{source}")
        return None
    with open(source, "r") as f:
        data = json.load(f)
    category = ("story" if location_key in config.MAPS else "raids" if location_key in config.RAIDS
                else "expeditions" if location_key == config.EXPEDITION_PRESET_LOCATION else None)
    dest, _ = QFileDialog.getSaveFileName(parent, "Export recording", f"{preset_name}.json",
                                          "Lucki's Macro recording (*.json)")
    if not dest:
        return None
    with open(dest, "w") as f:
        json.dump({
            "kind": "stage_preset", "category": category, "location": location_key,
            "variant": variant_key, "preset_name": preset_name, "actions": data.get("actions", []),
        }, f, indent=4)
    return dest


def choose_stage_target(parent):
    """Where an imported file belongs, when the file itself doesn't say."""
    dlg = FloatingDialog(parent, "Where does this recording go?", width=400)
    dlg.body.addWidget(label("The file doesn't say which map or raid it was made for. Pick one:", "muted", wrap=True))
    kind = Segmented([("story", "Story"), ("raids", "Raids"), ("expeditions", "Expeditions")], "story")
    dlg.body.addWidget(kind)
    loc_label = _field("Map")
    dlg.body.addWidget(loc_label)
    loc = QComboBox()
    dlg.body.addWidget(loc)
    var_label = _field("Act")
    dlg.body.addWidget(var_label)
    var = QComboBox()
    dlg.body.addWidget(var)

    def rebuild_locations():
        loc.blockSignals(True)
        loc.clear()
        k = kind.value()
        loc.setVisible(k != "expeditions")
        loc_label.setVisible(k != "expeditions")
        var.setVisible(k != "expeditions")
        var_label.setVisible(k != "expeditions")
        if k == "story":
            loc_label.setText("Map")
            var_label.setText("Act")
            for key, m in config.MAPS.items():
                if m["enabled"]:
                    loc.addItem(m["label"], key)
        elif k == "raids":
            loc_label.setText("Raid")
            var_label.setText("Difficulty")
            for key, r in config.RAIDS.items():
                if r["enabled"]:
                    loc.addItem(r["label"], key)
        loc.blockSignals(False)
        rebuild_variants()

    def rebuild_variants():
        var.clear()
        k = kind.value()
        if k == "story" and loc.currentData() in config.MAPS:
            for key, a in config.MAPS[loc.currentData()]["acts"].items():
                if a["enabled"]:
                    var.addItem(a["label"], key)
        elif k == "raids":
            for n in ("1", "2", "3"):
                var.addItem(n, n)

    kind.changed.connect(lambda _: rebuild_locations())
    loc.currentIndexChanged.connect(lambda _: rebuild_variants())
    rebuild_locations()
    dlg.add_buttons("Use this")
    if dlg.exec() != QDialog.Accepted:
        return None
    if kind.value() == "expeditions":
        return config.EXPEDITION_PRESET_LOCATION, config.EXPEDITION_PRESET_VARIANT
    if loc.currentData() and var.currentData():
        return loc.currentData(), var.currentData()
    return None


def import_stage_preset(parent):
    """Returns (location_key, variant_key, name) or None."""
    source, _ = QFileDialog.getOpenFileName(parent, "Import recording", "", "JSON files (*.json)")
    if not source:
        return None
    try:
        with open(source, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        message(parent, "Import", f"Couldn't read that file:\n{e}")
        return None
    actions = data.get("actions")
    if not isinstance(actions, list):
        message(parent, "Import", "That file isn't a recording (it has no list of steps).")
        return None
    if (data.get("kind") or preset_core.infer_kind(actions)) == "movement_preset":
        message(parent, "Import", "That's a walking macro, not a unit placement recording.")
        return None

    if data.get("category") == "expeditions" or data.get("location") == config.EXPEDITION_PRESET_LOCATION:
        target = (config.EXPEDITION_PRESET_LOCATION, config.EXPEDITION_PRESET_VARIANT)
    else:
        target = preset_core.resolve_stage_target(data) or choose_stage_target(parent)
    if target is None:
        return None
    location_key, variant_key = target

    default = preset_core.sanitize_preset_name(
        data.get("preset_name", "") or os.path.splitext(os.path.basename(source))[0])
    name = prompt_text(parent, "Import recording", "Name it:", default=default, ok_text="Import")
    if not name:
        return None
    name = preset_core.sanitize_preset_name(name)
    if preset_core.is_reserved_name(name):
        message(parent, "Import", "'Auto Play' is reserved - pick another name.")
        return None
    dest = preset_core.preset_path(location_key, variant_key, name)
    if os.path.exists(dest) and not confirm(parent, "Replace it?",
                                            f"A recording called '{name}' already exists there. Replace it?",
                                            "Replace", danger=True):
        return None
    os.makedirs(preset_core.PRESET_DIR, exist_ok=True)
    with open(dest, "w") as f:
        json.dump({"location": location_key, "variant": variant_key, "actions": actions}, f)
    return location_key, variant_key, name


# ----------------------------------------------------------------------------- copy

def copy_story_preset(parent, source_map, source_act, name):
    """Copies a Story recording to other acts (same layout) or another map. Returns copies made."""
    source = preset_core.preset_path(source_map, source_act, name)
    if not os.path.exists(source):
        message(parent, "Copy", "That recording's file is missing.")
        return 0

    dlg = FloatingDialog(parent, f"Copy '{name}'", width=420)
    dlg.body.addWidget(label("Acts on the same map share one layout, so a recording copied to them "
                             "places units in the same spots. On another map it's only a starting point.",
                             "muted", wrap=True))
    dlg.body.addWidget(_field("Copy to map"))
    map_box = QComboBox()
    for key, m in config.MAPS.items():
        if m["enabled"]:
            map_box.addItem(m["label"], key)
    map_box.setCurrentIndex(max(0, map_box.findData(source_map)))
    dlg.body.addWidget(map_box)
    dlg.body.addWidget(_field("Acts"))
    chips_host = QWidget()
    chips = QGridLayout(chips_host)
    chips.setContentsMargins(0, 0, 0, 0)
    chips.setSpacing(6)
    dlg.body.addWidget(chips_host)
    act_chips = {}

    def rebuild():
        for c in act_chips.values():
            c.deleteLater()
        act_chips.clear()
        target_map = map_box.currentData()
        i = 0
        for act_key, act in config.MAPS[target_map]["acts"].items():
            if not act["enabled"]:
                continue
            is_source = target_map == source_map and act_key == source_act
            chip = Chip(act["label"] + ("  (this one)" if is_source else ""), checked=not is_source)
            chip.setEnabled(not is_source)
            chips.addWidget(chip, i // 3, i % 3)
            act_chips[act_key] = chip
            i += 1

    map_box.currentIndexChanged.connect(lambda _: rebuild())
    rebuild()
    dlg.add_buttons("Copy")
    if dlg.exec() != QDialog.Accepted:
        return 0
    target_map = map_box.currentData()
    targets = [k for k, c in act_chips.items() if c.isChecked() and c.isEnabled()]
    if not targets:
        return 0
    existing = [t for t in targets if os.path.exists(preset_core.preset_path(target_map, t, name))]
    if existing and not confirm(parent, "Replace existing?",
                                f"{len(existing)} of those acts already have a recording called '{name}'. Replace them?",
                                "Replace", danger=True):
        return 0
    with open(source, "r") as f:
        content = f.read()
    for act_key in targets:
        with open(preset_core.preset_path(target_map, act_key, name), "w") as f:
            f.write(content)
    return len(targets)


# ----------------------------------------------------------------------------- challenge links

def list_story_presets():
    """Every Story recording on disk: (map_key, act_key, name, label)."""
    results = []
    if not os.path.isdir(preset_core.PRESET_DIR):
        return results
    for filename in sorted(os.listdir(preset_core.PRESET_DIR)):
        if not filename.endswith(".json"):
            continue
        for map_key, m in config.MAPS.items():
            if not filename.startswith(f"{map_key}_"):
                continue
            rest = filename[len(map_key) + 1:-5]
            for act_key, act in m["acts"].items():
                if rest.startswith(f"{act_key}_"):
                    name = rest[len(act_key) + 1:]
                    results.append((map_key, act_key, name, f"{m['label']} - {act['label']} - {name}"))
                    break
            break
    return results


def challenge_links_dialog(parent):
    presets = list_story_presets()
    dlg = FloatingDialog(parent, "Challenge setups", width=480)
    dlg.body.addWidget(label("Each challenge can use the game's Auto Play or one of your Story recordings. "
                             "The game shuffles which act a challenge is, so check these now and then.",
                             "muted", wrap=True))
    boxes = {}
    for slot_key, slot in config.CHALLENGE_SLOTS.items():
        dlg.body.addWidget(_field(slot["label"]))
        box = QComboBox()
        box.addItem("Game's Auto Play", None)
        for p in presets:
            box.addItem(p[3], (p[0], p[1], p[2]))
        link = challenge_links.get_link(slot_key)
        if link.get("preset") and link["preset"] != config.AUTO_PLAY_PRESET_NAME:
            idx = box.findData((link.get("map"), link.get("act"), link["preset"]))
            box.setCurrentIndex(max(0, idx))
        dlg.body.addWidget(box)
        boxes[slot_key] = box
    dlg.add_buttons("Save")
    if dlg.exec() != QDialog.Accepted:
        return False
    for slot_key, box in boxes.items():
        data = box.currentData()
        if data is None:
            challenge_links.set_link(slot_key, None, None, config.AUTO_PLAY_PRESET_NAME)
        else:
            challenge_links.set_link(slot_key, data[0], data[1], data[2])
    return True


# ----------------------------------------------------------------------------- settings

class _Section(QWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.col = QVBoxLayout(self)
        self.col.setContentsMargins(0, 0, 0, 0)
        self.col.setSpacing(12)
        self.col.addWidget(label(title, "section"))


class _Swatch(QAbstractButton):
    def __init__(self, key, color, parent=None):
        super().__init__(parent)
        self.key = key
        self.color = color
        self.setCheckable(True)
        self.setFixedSize(34, 34)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.isChecked():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.TEXT))
            p.drawEllipse(0, 0, 34, 34)
            p.setBrush(QColor(theme.BG_CARD))
            p.drawEllipse(2, 2, 30, 30)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.color))
        p.drawEllipse(5, 5, 24, 24)
        p.end()


class SettingsSheet(FloatingDialog):
    """
    The Settings side sheet. Every change applies immediately (to the running bot
    too, where that makes sense) and is saved when the sheet closes.
    """

    TABS = [("runs", "Runs"), ("discord", "Discord"), ("look", "Look"), ("window", "Window"), ("help", "Advanced")]

    def __init__(self, host, start_tab="runs"):
        super().__init__(host, "Settings", width=540, placement="right")
        self.host = host
        self.s = host.user_settings
        tabs = Segmented(self.TABS, start_tab)
        self.body.addWidget(tabs)
        self.stack = QStackedWidget()
        self.body.addWidget(self.stack, 1)
        self.pages = {}
        for key, builder in (("runs", self._runs), ("discord", self._discord), ("look", self._look),
                             ("window", self._window), ("help", self._advanced)):
            scroll = VScroll()
            inner = QWidget()
            col = QVBoxLayout(inner)
            col.setContentsMargins(2, 8, 10, 8)
            col.setSpacing(22)
            builder(col)
            col.addStretch(1)
            scroll.setWidget(inner)
            self.stack.addWidget(scroll)
            self.pages[key] = scroll
        self.stack.setCurrentWidget(self.pages[start_tab])
        tabs.changed.connect(lambda k: self.stack.setCurrentWidget(self.pages[k]))
        foot = QHBoxLayout()
        foot.addWidget(label("Changes apply straight away.", "hint"), 1)
        done = Button("Done", "accent", height=36)
        done.setMinimumWidth(110)
        done.clicked.connect(self.accept)
        foot.addWidget(done)
        self.body.addLayout(foot)

    # --- Runs

    def _runs(self, col):
        sec = _Section("WHEN THINGS GO WRONG")
        n = max(1, min(20, int(self.s.get("max_defeats", config.MAX_CONSECUTIVE_DEFEATS))))
        defeats = ToggleRow("Stop after losing in a row",
                            "If your setup keeps losing, stop instead of burning through portals and keys.",
                            bool(self.s.get("stop_after_defeats", True)))
        sec.col.addWidget(defeats)
        row = QHBoxLayout()
        row.addWidget(_field("Stop after"))
        spin = QSpinBox()
        spin.setRange(1, 20)
        spin.setValue(n)
        spin.setFixedWidth(80)
        row.addWidget(spin)
        row.addWidget(_field("losses in a row"))
        row.addStretch(1)
        sec.col.addLayout(row)

        def on_defeats(_=None):
            self.s["stop_after_defeats"] = defeats.isChecked()
            self.s["max_defeats"] = spin.value()
            spin.setEnabled(defeats.isChecked())
            self.host.engine.apply_run_behavior_settings()
            self.host.refresh_ready()

        defeats.toggled.connect(on_defeats)
        spin.valueChanged.connect(on_defeats)
        spin.setEnabled(defeats.isChecked())

        never = ToggleRow("Never stop",
                          "Stuck screen, Roblox closed, a crash: wait and start again instead of giving up. "
                          "Never overrides your Stop button or the loss limit.",
                          bool(self.s.get("never_stop", False)))
        sec.col.addWidget(never)
        ladder = QHBoxLayout()
        ladder.setSpacing(6)
        for i, t in enumerate(("30 s", "1 min", "2 min", "up to 5 min")):
            chip = Chip(t, checked=True)
            chip.setEnabled(False)
            ladder.addWidget(chip)
            if i < 3:
                ladder.addWidget(QLabel("›"))
        ladder.addStretch(1)
        ladder_host = QWidget()
        ladder_host.setLayout(ladder)
        sec.col.addWidget(ladder_host)
        ladder_host.setVisible(never.isChecked())

        def on_never(v):
            self.s["never_stop"] = bool(v)
            ladder_host.setVisible(bool(v))
            self.host.refresh_stats()

        never.toggled.connect(on_never)
        col.addWidget(sec)

        sec2 = _Section("TIME LIMIT")
        hours = float(self.s.get("stop_after_hours", 0) or 0)
        timed = ToggleRow("Stop after a set time", "Handy before bed or before you need the PC back.", hours > 0)
        sec2.col.addWidget(timed)
        row2 = QHBoxLayout()
        row2.addWidget(_field("Stop after"))
        hspin = QDoubleSpinBox()
        hspin.setRange(0.25, 72)
        hspin.setSingleStep(0.5)
        hspin.setDecimals(2)
        hspin.setValue(hours if hours > 0 else 8.0)
        hspin.setFixedWidth(90)
        row2.addWidget(hspin)
        row2.addWidget(_field("hours"))
        row2.addStretch(1)
        sec2.col.addLayout(row2)

        def on_time(_=None):
            self.s["stop_after_hours"] = hspin.value() if timed.isChecked() else 0.0
            hspin.setEnabled(timed.isChecked())

        timed.toggled.connect(on_time)
        hspin.valueChanged.connect(on_time)
        hspin.setEnabled(timed.isChecked())
        sec2.col.addWidget(label("How many runs to play is set per mode, in its “How many runs” card.", "hint", wrap=True))
        sound = ToggleRow("Play a sound when a run stops by itself", None, bool(self.s.get("sound_on_stop", True)))
        sound.toggled.connect(lambda v: self.s.__setitem__("sound_on_stop", bool(v)))
        sec2.col.addWidget(sound)
        col.addWidget(sec2)

        sec_pc = _Section("THIS PC")
        sec_pc.col.addWidget(label("Older PCs run Roblox with fewer frames, and a click that's too quick can be "
                                   "missed. Auto measures this PC and waits longer only when it needs to.",
                                   "muted", wrap=True))
        speed = Segmented([("auto", "Auto"), ("on", "Slow PC mode"), ("off", "Fast PC")],
                          self.s.get("slow_pc_mode", "auto"))

        def on_speed(v):
            self.s["slow_pc_mode"] = v
            self.host.apply_speed_mode()
            speed_hint.setText(f"Clicks currently wait {config.SLOWNESS:.1f}x as long as on a fast PC.")

        speed.changed.connect(on_speed)
        sec_pc.col.addWidget(speed)
        speed_hint = label(f"Clicks currently wait {config.SLOWNESS:.1f}x as long as on a fast PC.", "hint")
        sec_pc.col.addWidget(speed_hint)
        col.addWidget(sec_pc)

        sec3 = _Section("PRESETS")
        conf = ToggleRow("Ask before deleting a recording", None, bool(self.s.get("confirm_delete", True)))
        conf.toggled.connect(lambda v: self.s.__setitem__("confirm_delete", bool(v)))
        sec3.col.addWidget(conf)
        col.addWidget(sec3)

    # --- Discord

    def _discord(self, col):
        sec = _Section("DISCORD ALERTS")
        sec.col.addWidget(label("Get a message when a run starts, ends or runs into trouble. In Discord: "
                                "Edit Channel › Integrations › Webhooks › New Webhook › Copy Webhook URL, "
                                "then paste it here.", "muted", wrap=True))
        row = QHBoxLayout()
        edit = QLineEdit(self.s.get("discord_webhook", ""))
        edit.setEchoMode(QLineEdit.Password)
        edit.setPlaceholderText("https://discord.com/api/webhooks/...")
        edit.setMinimumHeight(38)
        row.addWidget(edit, 1)
        show = IconButton("eye" if False else "shield", "Show")
        show.setToolTip("Show or hide the link")
        show.clicked.connect(lambda: edit.setEchoMode(
            QLineEdit.Normal if edit.echoMode() == QLineEdit.Password else QLineEdit.Password))
        row.addWidget(show)
        sec.col.addLayout(row)
        sec.col.addWidget(label("Treat this link like a password - anyone who has it can post in that channel.", "hint", wrap=True))

        toggles = {}
        for key, title, hint in (
            ("notify_run_lifecycle", "Run started, finished or stopped", None),
            ("notify_problems", "Problems", "Stuck, disconnected, Roblox closed"),
            ("notify_milestones", "Milestones", "Soul counts, targets reached"),
            ("notify_every_match", "Every match result", "Very chatty"),
        ):
            t = ToggleRow(title, hint, bool(self.s.get(key, key != "notify_every_match")))
            sec.col.addWidget(t)
            toggles[key] = t

        test_row = QHBoxLayout()
        test = Button("Send test", "subtle", icon="bell", height=34)
        test_row.addWidget(test)
        result = label("", "hint")
        test_row.addWidget(result, 1)
        sec.col.addLayout(test_row)

        def apply(_=None):
            for key, t in toggles.items():
                self.s[key] = t.isChecked()
            self.host.apply_notification_settings(edit.text())

        def send_test():
            apply()
            if not notify.is_configured():
                result.setText("Paste a webhook link first.")
                result.setStyleSheet(f"color: {theme.DANGER_TEXT}; font-size: 12px;")
            elif notify.send("Lucki's Macro is connected to this channel.", category="lifecycle",
                             title="Test message", good=True):
                result.setText("Sent - check your channel.")
                result.setStyleSheet(f"color: {theme.SUCCESS_TEXT}; font-size: 12px;")
            else:
                result.setText("Not sent - is that category switched off?")
                result.setStyleSheet(f"color: {theme.DANGER_TEXT}; font-size: 12px;")

        edit.editingFinished.connect(apply)
        for t in toggles.values():
            t.toggled.connect(apply)
        test.clicked.connect(send_test)
        self._apply_discord = apply
        col.addWidget(sec)

    # --- Look

    def _look(self, col):
        sec = _Section("LOOK AND FEEL")
        sec.col.addWidget(_field("Accent color"))
        row = QHBoxLayout()
        row.setSpacing(6)
        swatches = []
        for key, pal in settings.ACCENT_PALETTES.items():
            sw = _Swatch(key, pal["accent"])
            sw.setToolTip(pal["label"])
            sw.setChecked(key == self.s.get("accent"))
            swatches.append(sw)
            row.addWidget(sw)

            def pick(_=False, k=key):
                self.s["accent"] = k
                for other in swatches:
                    other.setChecked(other.key == k)
                self.host.apply_theme()

            sw.clicked.connect(pick)
        row.addStretch(1)
        sec.col.addLayout(row)

        motion = ToggleRow("Reduce motion", "Turns off slides, glows and pulses.", bool(self.s.get("reduce_motion", False)))

        def on_motion(v):
            self.s["reduce_motion"] = bool(v)
            self.host.apply_theme()

        motion.toggled.connect(on_motion)
        sec.col.addWidget(motion)

        op_row = QHBoxLayout()
        op_row.addWidget(_field("Panel opacity"), 1)
        op_val = label(f"{int(self.s.get('window_opacity', 1.0) * 100)}%", "hint")
        op_row.addWidget(op_val)
        sec.col.addLayout(op_row)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(50, 100)
        slider.setValue(int(self.s.get("window_opacity", 1.0) * 100))

        def on_opacity(v):
            self.s["window_opacity"] = round(v / 100, 2)
            op_val.setText(f"{v}%")
            self.host.setWindowOpacity(v / 100)

        slider.valueChanged.connect(on_opacity)
        sec.col.addWidget(slider)
        sec.col.addWidget(label("Only the panel fades - the game always stays fully visible.", "hint", wrap=True))
        col.addWidget(sec)

    # --- Window

    def _window(self, col):
        sec = _Section("MONITOR")
        sec.col.addWidget(label("Which screen Lucki's Macro and the game open on. The game is sized to fit that "
                                "screen automatically, at any resolution or Windows scaling.", "muted", wrap=True))
        box = QComboBox()
        current = self.host.current_screen_name()
        for i, screen in enumerate(QGuiApplication.screens()):
            geo = screen.geometry()
            dpr = screen.devicePixelRatio()
            w, h = round(geo.width() * dpr), round(geo.height() * dpr)
            primary = "  (main)" if screen == QGuiApplication.primaryScreen() else ""
            box.addItem(f"Screen {i + 1}: {w} x {h} at {round(dpr * 100)}%{primary}", screen.name())
        box.setCurrentIndex(max(0, box.findData(current)))

        def on_screen(_):
            name = box.currentData()
            self.s["dock_screen"] = name
            self.host.move_to_screen(name)

        box.currentIndexChanged.connect(on_screen)
        sec.col.addWidget(box)
        size = self.host.dock_state.game_size
        sec.col.addWidget(label(f"Game size on this screen: {size[0]} x {size[1]}", "hint"))
        col.addWidget(sec)

        sec2 = _Section("WINDOW")
        top = ToggleRow("Keep on top when Roblox isn't docked",
                        "While the game is docked both windows always stay on top together.",
                        bool(self.s.get("always_on_top", False)))

        def on_top(v):
            self.s["always_on_top"] = bool(v)
            self.host.apply_topmost()

        top.toggled.connect(on_top)
        sec2.col.addWidget(top)
        col.addWidget(sec2)

        sec3 = _Section("KEYBINDS")
        sec3.col.addWidget(label("Click Rebind, then press the new key. These work anywhere - even while "
                                 "Roblox has focus. Esc always closes a dialog or this panel and can't be "
                                 "rebound.", "muted", wrap=True))
        self._keybind_chips = {}

        def _chip(text):
            k = QLabel(text.upper())
            k.setAlignment(Qt.AlignCenter)
            k.setFixedSize(52, 26)
            k.setStyleSheet(f"background: {theme.BG_SUNKEN}; border: 1px solid {theme.BORDER}; "
                            f"border-radius: 6px; font-weight: 700; font-size: 12px;")
            return k

        def _other_keys(setting_key):
            return {self.s.get(k, d) for k, d in
                    (("keybind_start_stop", "f7"), ("keybind_record", "f8"), ("keybind_autoclicker", "f9"))
                    if k != setting_key}

        def _rebind_row(setting_key, default, title, hint):
            chip = _chip(self.s.get(setting_key, default))
            self._keybind_chips[setting_key] = chip

            def on_rebind():
                def applied(value):
                    if not value:
                        return
                    self.s[setting_key] = value
                    chip.setText(value.upper())
                    rec = self.host.recorder
                    if setting_key == "keybind_start_stop":
                        rec.start_stop_key = value
                    elif setting_key == "keybind_record":
                        rec.record_key = value
                    elif setting_key == "keybind_autoclicker":
                        rec.autoclicker_key = value
                    from modules.keybinds import LIKELY_CONFLICTS
                    if value in LIKELY_CONFLICTS:
                        self.host.toast(
                            "Heads up",
                            f"'{value.upper()}' is also used {'to move' if value in 'wasd' else 'to select a unit'} "
                            f"while recording - every press of it will now also trigger this hotkey.",
                            "warning", 7000)

                self.host._pick_keybind(applied, exclude=_other_keys(setting_key))

            r = QHBoxLayout()
            r.addWidget(chip)
            text = QVBoxLayout()
            text.setSpacing(1)
            text.addWidget(label(title))
            text.addWidget(label(hint, "hint", wrap=True))
            r.addLayout(text, 1)
            rebind = Button("Rebind", "subtle", height=30, font_px=12)
            # No keyboard focus: a focused QAbstractButton re-activates itself on
            # Enter/Space by default, which would re-fire this exact button the
            # moment the capture listener saw one of those keys - see modules.
            # keybinds.RESERVED's own note.
            rebind.setFocusPolicy(Qt.NoFocus)
            rebind.clicked.connect(on_rebind)
            r.addWidget(rebind)
            sec3.col.addLayout(r)

        _rebind_row("keybind_start_stop", "f7", "Start / stop the bot", "Default: F7")
        _rebind_row("keybind_record", "f8", "Start / stop recording", "Default: F8")
        _rebind_row("keybind_autoclicker", "f9", "Toggle the Autoclicker", "Default: F9")
        col.addWidget(sec3)

        sec4 = _Section("AUTOCLICKER")
        sec4.col.addWidget(label("Clicks a saved spot on an interval during a match, so a long/infinite run "
                                 "doesn't idle-disconnect. Pauses itself during navigation, the lobby or a "
                                 "disconnect, and picks back up once you're actually in a match.",
                                 "muted", wrap=True))
        pos = self.s.get("autoclicker_pos")
        self._ac_pos_label = label(f"Position: {pos[0]}, {pos[1]}" if pos else "Position: not set", "hint")
        sec4.col.addWidget(self._ac_pos_label)
        ac_row = QHBoxLayout()
        locate = Button("Locate position", "subtle", icon="target", height=34, font_px=12)
        locate.setFocusPolicy(Qt.NoFocus)

        def on_locate(_=False):
            def picked(xy):
                if not xy:
                    return
                x, y = xy
                self.s["autoclicker_pos"] = [x, y]
                config.AUTOCLICKER_POS = (x, y)
                self._ac_pos_label.setText(f"Position: {x}, {y}")

            self.host._pick_screen_click(
                picked, "Click where the Autoclicker should click. Esc cancels.")

        locate.clicked.connect(on_locate)
        ac_row.addWidget(locate)

        test = Button("Test click", "subtle", icon="target", height=34, font_px=12)
        test.setFocusPolicy(Qt.NoFocus)
        test.setToolTip("Fires one click at the saved position right now, so you can confirm it's right "
                        "without starting a whole run.")

        def on_test(_=False):
            pos = config.AUTOCLICKER_POS
            if not pos:
                self.host.toast("No position set", "Locate a click position first.", "warning")
                return
            if self.host.engine.running:
                self.host.toast("Bot is running", "Stop the run before test-clicking.", "warning")
                return
            if not self.host.dock_state.docked:
                self.host.toast("Dock Roblox first", "A click only means something once the game is docked.",
                               "warning")
                return

            def run():
                from input_controller import click_at
                click_at(*pos)
                self.host.bridge.call.emit(
                    lambda: self.host.toast("Clicked", f"Clicked at {pos[0]}, {pos[1]}.", "success"))

            threading.Thread(target=run, daemon=True).start()

        test.clicked.connect(on_test)
        ac_row.addWidget(test)
        sec4.col.addLayout(ac_row)

        interval_row = QHBoxLayout()
        interval_row.addWidget(_field("Click every"))
        interval_spin = QDoubleSpinBox()
        interval_spin.setRange(0.5, 60.0)
        interval_spin.setSingleStep(0.5)
        interval_spin.setDecimals(1)
        interval_spin.setValue(float(self.s.get("autoclicker_interval") or config.AUTOCLICKER_DEFAULT_INTERVAL))
        interval_spin.setFixedWidth(90)
        interval_row.addWidget(interval_spin)
        interval_row.addWidget(_field("seconds"))
        interval_row.addStretch(1)
        sec4.col.addLayout(interval_row)

        def on_interval(v):
            self.s["autoclicker_interval"] = v
            config.AUTOCLICKER_INTERVAL = v

        interval_spin.valueChanged.connect(on_interval)

        ac_toggle = ToggleRow("Autoclicker is on", "Also toggled by its keybind above, from anywhere.",
                              bool(config.AUTOCLICKER_ENABLED))

        def on_ac_toggle(v):
            if v and not config.AUTOCLICKER_POS:
                ac_toggle.setChecked(False)
                self.host.toast("No position set", "Locate a click position first.", "warning")
                return
            config.AUTOCLICKER_ENABLED = bool(v)

        ac_toggle.toggled.connect(on_ac_toggle)
        sec4.col.addWidget(ac_toggle)
        col.addWidget(sec4)

    # --- Advanced

    def _advanced(self, col):
        sec_u = _Section("UPDATES")
        self._update_body = label("", "muted", wrap=True)
        sec_u.col.addWidget(self._update_body)
        u_row = QHBoxLayout()
        check_btn = Button("Check for updates", "subtle", icon="download", height=34, font_px=12)
        check_btn.setFocusPolicy(Qt.NoFocus)

        def on_check(_=False):
            check_btn.setEnabled(False)
            self._update_body.setText("Checking...")
            from modules import update_check

            def run():
                info = update_check.check_for_update()
                self.host.bridge.call.emit(lambda: self._after_update_check(info, check_btn))

            threading.Thread(target=run, daemon=True).start()

        check_btn.clicked.connect(on_check)
        u_row.addWidget(check_btn)
        self._open_release_btn = Button("Open release page", "accent", height=34, font_px=12)
        self._open_release_btn.setFocusPolicy(Qt.NoFocus)
        self._open_release_btn.clicked.connect(
            lambda: os.startfile(self.host._update_info["url"]) if self.host._update_info else None)
        u_row.addWidget(self._open_release_btn)
        sec_u.col.addLayout(u_row)
        self._refresh_update_section()
        col.addWidget(sec_u)

        sec = _Section("FILES")
        path = logger.get_log_path() or "(no log file)"
        sec.col.addWidget(label("Everything the bot does is written to a log file. If something goes wrong, "
                                "that file and the debug screenshots are what to send.", "muted", wrap=True))
        sec.col.addWidget(label(path, "hint", wrap=True))
        row = QHBoxLayout()
        for text, folder in (("Open logs", "logs"), ("Open debug shots", "debug"), ("Open recordings", "presets")):
            b = Button(text, "subtle", height=34, font_px=12)
            b.clicked.connect(lambda _=False, f=folder: _open_folder(f))
            row.addWidget(b)
        sec.col.addLayout(row)
        col.addWidget(sec)

        sec_d = _Section("DEBUG TOOLS")
        sec_d.col.addWidget(label("For working on your own recordings and coordinates without running a whole "
                                  "farm loop. Dock Roblox first (Start once, or just let it auto-dock).",
                                  "muted", wrap=True))

        cam_row = QHBoxLayout()
        cam_btn = Button("Fix camera now", "subtle", icon="target", height=34, font_px=12)
        cam_btn.setToolTip("Replays the same right-click-drag a real run starts with, so you can see and tune "
                           "the view before recording a macro against it.")
        cam_btn.clicked.connect(lambda: self.host.fix_camera())
        cam_row.addWidget(cam_btn)
        coord_btn = Button("Coordinate finder", "subtle", icon="target", height=34, font_px=12)
        coord_btn.setToolTip("Hover any spot in the docked game and press Space - the reference-space (x, y) for "
                             "it is written to the log and shown as a toast. Press Esc to cancel.")
        # See the Keybinds section's own note on rebind.setFocusPolicy(Qt.NoFocus) -
        # same reasoning: this button listens for Space, so it must not also be the
        # thing Space re-activates.
        coord_btn.setFocusPolicy(Qt.NoFocus)
        coord_btn.clicked.connect(lambda: self.host.find_coordinate())
        cam_row.addWidget(coord_btn)
        shot_btn = Button("Debug screenshot", "subtle", icon="monitor", height=34, font_px=12)
        shot_btn.setToolTip("Saves exactly what the bot currently sees to the debug folder - useful for checking "
                            "why a template isn't matching.")
        shot_btn.clicked.connect(lambda: self.host.debug_screenshot())
        cam_row.addWidget(shot_btn)
        sec_d.col.addLayout(cam_row)

        sec_d.col.addWidget(label(
            "How to use them:\n"
            "1. Fix camera - click it, then don't touch the mouse. It lines the camera up the same way a real "
            "run would before placing units - handy for checking a zoom level, or getting the view right before "
            "recording a macro.\n"
            "2. Coordinate finder - click it, hover the exact spot in Roblox you need a number for (a fishing "
            "spot, a button you're about to add), press Space. The two numbers it logs are what config.py and "
            "the movement/fishing settings expect.\n"
            "3. Debug screenshot - click it any time to save the current frame to the debug folder, at the size "
            "and scale the bot is actually matching against - the same kind of file a stuck run saves "
            "automatically, on demand.", "hint", wrap=True))
        col.addWidget(sec_d)

        sec_r = _Section("SOMETHING NOT WORKING?")
        sec_r.col.addWidget(label("Makes one zip on your Desktop with the logs, the latest debug screenshots and your "
                                  "screen details - everything needed to figure out a problem. Your Discord link is "
                                  "left out, so it's safe to send.", "muted", wrap=True))
        report = Button("Make troubleshooting report", "accent", icon="download", height=36)
        report.clicked.connect(lambda: self.host.make_report())
        sec_r.col.addWidget(report)
        col.addWidget(sec_r)

        sec2 = _Section("FIRST-RUN GUIDE")
        again = Button("Show the tutorial again", "subtle", height=34)
        again.clicked.connect(lambda: (self.accept(), QTimer.singleShot(250, self.host.open_welcome)))
        sec2.col.addWidget(again)
        col.addWidget(sec2)

    def _refresh_update_section(self):
        info = self.host._update_info
        if info:
            notes = f"\n\n{info['notes']}" if info.get("notes") else ""
            self._update_body.setText(f"v{info['version']} is available - this is v{config.APP_VERSION}.{notes}")
        else:
            self._update_body.setText(f"You're on v{config.APP_VERSION}.")
        self._open_release_btn.setVisible(bool(info))

    def _after_update_check(self, info, check_btn):
        self.host._update_info = info
        check_btn.setEnabled(True)
        self._refresh_update_section()
        if info:
            self.host.toast("Update available", f"v{info['version']} is out.", "info")
        else:
            self.host.toast("Up to date", f"You're on v{config.APP_VERSION}, the latest.", "success")

    def done(self, result):
        if hasattr(self, "_apply_discord"):
            self._apply_discord()
        settings.save(self.s)
        super().done(result)


def _open_folder(folder):
    path = os.path.join(settings.base_dir(), folder)
    os.makedirs(path, exist_ok=True)
    try:
        os.startfile(path)
    except Exception:
        subprocess.Popen(["explorer", path])
