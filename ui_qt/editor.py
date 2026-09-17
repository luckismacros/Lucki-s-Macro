# ui_qt/editor.py
"""
The Unit Placement editor: a recording shown as readable steps ("Pick unit 2, place
it at spot 3") next to a picture of the map with every spot numbered on it.

Select a step to find its spot; drag a spot to move it; delete a step that was a
misclick. Nothing touches the file until Save.

The picture is a screenshot of the game taken just before the editor opened (the
editor itself covers the game, so a live capture would only photograph the editor).
"""
import copy

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QPainterPath, QPixmap, QImage
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QListWidget, QListWidgetItem, QLabel,
)

import config
from modules import preset_core
from . import theme, dialogs
from .widgets import Button, IconButton, Segmented, label, icon_pixmap, with_alpha, Card

REF_W, REF_H = config.REFERENCE_WIDTH, config.REFERENCE_HEIGHT

RESULT_SAVED = 1
RESULT_RECORD = 2
RESULT_TEST = 3


def screenshot_pixmap():
    """The game's client area as a QPixmap, or a null pixmap if it can't be captured."""
    try:
        if config.CLIENT_SIZE is None:
            return QPixmap()
        from vision import capture_screen
        import cv2
        frame = capture_screen()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, _ = rgb.shape
        image = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        return QPixmap.fromImage(image)
    except Exception as e:
        print(f"[editor] Couldn't capture the game for the placement map: {e}")
        return QPixmap()


class MapCanvas(QWidget):
    spot_selected = Signal(int)          # step index
    spot_moved = Signal(int, int, int)   # step index, ref x, ref y

    # Precision: how far the map can be zoomed in when placing/adjusting a spot.
    MIN_ZOOM = 1.0
    MAX_ZOOM = 8.0
    ZOOM_STEP = 1.15

    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self.pix = pixmap
        self.steps = []
        self.selected = -1
        self.show_order = False
        self._drag = None
        self.zoom = 1.0
        self.pan = QPointF(0.0, 0.0)  # extra offset on top of the base fit rect, widget px
        self._pan_start = None
        self._pan_origin = QPointF(0.0, 0.0)
        self.setMinimumSize(560, 315)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)

    def set_steps(self, steps, selected=-1):
        self.steps = steps
        self.selected = selected
        self.update()

    def reset_zoom(self):
        self.zoom = 1.0
        self.pan = QPointF(0.0, 0.0)
        self.update()

    def zoom_by(self, factor, center=None):
        """Zooms in/out by `factor`, keeping the point under `center` (widget coords,
        defaults to the canvas centre) fixed - the same feel as zooming into a map."""
        center = center if center is not None else QPointF(self.width() / 2, self.height() / 2)
        new_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self.zoom * factor))
        if new_zoom == self.zoom:
            return
        ref_pt = self._to_ref(center)
        self.zoom = new_zoom
        new_widget_pt = self._to_widget(ref_pt)
        self.pan += center - new_widget_pt
        self._clamp_pan()
        self.update()

    def _clamp_pan(self):
        """Keeps the zoomed image from being panned entirely off screen."""
        base = self._base_frame()
        fw, fh = base.width() * self.zoom, base.height() * self.zoom
        max_x = max(0.0, (fw - self.width()) / 2 + base.width() / 2)
        max_y = max(0.0, (fh - self.height()) / 2 + base.height() / 2)
        self.pan.setX(max(-max_x, min(max_x, self.pan.x())))
        self.pan.setY(max(-max_y, min(max_y, self.pan.y())))

    def _base_frame(self):
        """The zoom=1 letterboxed rect - the reference image at exactly-fit size."""
        w, h = self.width(), self.height()
        fw = min(w, h * REF_W / REF_H)
        fh = fw * REF_H / REF_W
        return QRectF((w - fw) / 2, (h - fh) / 2, fw, fh)

    def _frame(self):
        """The rect the reference image is currently drawn into - base rect scaled by
        the zoom level and shifted by the pan offset. Every coordinate conversion
        goes through this, so zoom/pan need no other special-casing anywhere."""
        base = self._base_frame()
        fw, fh = base.width() * self.zoom, base.height() * self.zoom
        cx, cy = base.center().x() + self.pan.x(), base.center().y() + self.pan.y()
        return QRectF(cx - fw / 2, cy - fh / 2, fw, fh)

    def _to_widget(self, pos):
        r = self._frame()
        return QPointF(r.x() + pos[0] / REF_W * r.width(), r.y() + pos[1] / REF_H * r.height())

    def _to_ref(self, pt):
        r = self._frame()
        x = (pt.x() - r.x()) / r.width() * REF_W
        y = (pt.y() - r.y()) / r.height() * REF_H
        return int(max(0, min(REF_W - 1, x))), int(max(0, min(REF_H - 1, y)))

    def _spots(self):
        return [(i, s) for i, s in enumerate(self.steps) if s["kind"] == "place" and s["pos"]]

    def _hit(self, pt):
        for i, s in reversed(self._spots()):
            if (self._to_widget(s["pos"]) - pt).manhattanLength() <= 22:
                return i
        return -1

    def mousePressEvent(self, event):
        self.setFocus()
        if event.button() == Qt.RightButton:
            # Right-drag pans - kept off the left button so it can never be mistaken
            # for grabbing (and accidentally nudging) a spot underneath the cursor.
            self._pan_start = event.position()
            self._pan_origin = QPointF(self.pan)
            self.setCursor(Qt.ClosedHandCursor)
            return
        i = self._hit(event.position())
        if i >= 0:
            self.selected = i
            self._drag = i
            self.spot_selected.emit(i)
            self.setCursor(Qt.ClosedHandCursor)
            self.update()

    def mouseMoveEvent(self, event):
        if self._pan_start is not None:
            self.pan = self._pan_origin + (event.position() - self._pan_start)
            self._clamp_pan()
            self.update()
        elif self._drag is not None:
            self.steps[self._drag]["pos"] = self._to_ref(event.position())
            self.update()
        else:
            self.setCursor(Qt.OpenHandCursor if self._hit(event.position()) >= 0 else Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton and self._pan_start is not None:
            self._pan_start = None
            self.setCursor(Qt.ArrowCursor)
            return
        if self._drag is not None:
            x, y = self.steps[self._drag]["pos"]
            self.spot_moved.emit(self._drag, x, y)
            self._drag = None
            self.setCursor(Qt.OpenHandCursor)

    def wheelEvent(self, event):
        factor = self.ZOOM_STEP if event.angleDelta().y() > 0 else 1 / self.ZOOM_STEP
        self.zoom_by(factor, event.position())
        event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_by(self.ZOOM_STEP)
        elif event.key() == Qt.Key_Minus:
            self.zoom_by(1 / self.ZOOM_STEP)
        elif event.key() == Qt.Key_0:
            self.reset_zoom()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = self._frame()
        path = QPainterPath()
        path.addRoundedRect(r, 10, 10)
        p.setClipPath(path)
        if not self.pix.isNull():
            p.drawPixmap(r, self.pix, QRectF(self.pix.rect()))
            p.fillRect(r, with_alpha("#000000", 0.18))
        else:
            p.fillRect(r, QColor(theme.BG_SUNKEN))
            p.setPen(QPen(with_alpha(theme.BORDER, 0.7), 1))
            step = r.width() / 16
            x = r.x()
            while x < r.right():
                p.drawLine(QPointF(x, r.y()), QPointF(x, r.bottom()))
                x += step
            y = r.y()
            while y < r.bottom():
                p.drawLine(QPointF(r.x(), y), QPointF(r.right(), y))
                y += step
            p.setPen(QColor(theme.TEXT_DIM))
            f = QFont(theme.FONT_FAMILY)
            f.setPixelSize(13)
            p.setFont(f)
            p.drawText(r, Qt.AlignCenter, "No picture of the game yet - dock Roblox and open this again\n"
                                          "inside a match to see the map behind your spots.")
        p.setClipping(False)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 10, 10)

        spots = self._spots()
        if self.show_order and len(spots) > 1:
            pen = QPen(with_alpha(theme.accent(), 0.8), 2, Qt.DashLine)
            p.setPen(pen)
            for (_, a), (_, b) in zip(spots, spots[1:]):
                p.drawLine(self._to_widget(a["pos"]), self._to_widget(b["pos"]))

        f = QFont(theme.FONT_FAMILY)
        f.setPixelSize(12)
        f.setBold(True)
        p.setFont(f)
        for i, s in spots:
            c = self._to_widget(s["pos"])
            sel = i == self.selected
            if sel:
                p.setPen(Qt.NoPen)
                for k, a in enumerate((0.25, 0.14, 0.07)):
                    p.setBrush(with_alpha(theme.accent(), a))
                    p.drawEllipse(c, 17 + k * 5, 17 + k * 5)
            p.setPen(QPen(QColor("#ffffff") if sel else with_alpha("#000000", 0.5), 2))
            p.setBrush(QColor(theme.accent()))
            p.drawEllipse(c, 13, 13)
            p.setPen(QColor(theme.on_accent()))
            p.drawText(QRectF(c.x() - 13, c.y() - 13, 26, 26), Qt.AlignCenter, str(s["spot"]))
        p.end()


class StepRow(QWidget):
    def __init__(self, step, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 7, 10, 7)
        row.setSpacing(10)
        if step["kind"] == "wait":
            icon = QLabel()
            icon.setPixmap(icon_pixmap("clock", theme.TEXT_DIM, 14))
            icon.setFixedWidth(26)
            icon.setAlignment(Qt.AlignCenter)
            row.addWidget(icon)
            t = label(step["text"])
            t.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px; font-style: italic;")
            row.addWidget(t, 1)
            return
        badge = QLabel(step["unit"] or "?")
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(26, 26)
        badge.setStyleSheet(f"background: {theme.rgba(theme.accent(), 0.18)}; color: {theme.TEXT}; "
                            f"border-radius: 7px; font-weight: 700; font-size: 12px;")
        row.addWidget(badge)
        text = step["text"]
        if step["unit"]:
            text = text.replace(f"unit {step['unit']}", f"<b>unit {step['unit']}</b>")
        if step["spot"]:
            text = text.replace(f"spot {step['spot']}", f"<b>spot {step['spot']}</b>")
        t = QLabel(text)
        t.setTextFormat(Qt.RichText)
        t.setStyleSheet(f"font-size: 12.5px; color: {theme.TEXT_SOFT};")
        row.addWidget(t, 1)
        when = label(f"{step['time']:.1f} s", "hint")
        row.addWidget(when)


class UnitPlacementEditor(dialogs.FloatingDialog):
    def __init__(self, host, location_key, variant_key, name, context, pixmap):
        super().__init__(host, "", width=1180, closable=True)
        self.host = host
        self.loc, self.var, self.name = location_key, variant_key, name
        self.actions = copy.deepcopy(preset_core.load_actions(location_key, variant_key, name) or [])
        self.dirty = False

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label(f"Unit placement - {name.replace('_', ' ')}", "big"))
        titles.addWidget(label(context, "hint"))
        head.addLayout(titles, 1)
        rec = Button("Record again", "subtle", icon="record", key_hint="F8", height=36, font_px=12)
        rec.setToolTip("Closes this and records over the recording from scratch")
        rec.clicked.connect(self._record_again)
        head.addWidget(rec)
        clear = Button("Clear all", "ghost", icon="trash", height=36, font_px=12)
        clear.clicked.connect(self._clear)
        head.addWidget(clear)
        self.save_btn = Button("Save", "accent", icon="check", height=36)
        self.save_btn.setMinimumWidth(100)
        self.save_btn.clicked.connect(self._save)
        head.addWidget(self.save_btn)
        close = IconButton("close", "", size=16)
        close.setFixedWidth(32)
        close.clicked.connect(self.reject)
        head.addWidget(close)
        self.body.addLayout(head)

        content = QHBoxLayout()
        content.setSpacing(14)

        left = Card("cardSunken", margins=(10, 12, 10, 10), spacing=8)
        left.setFixedWidth(380)
        lhead = QHBoxLayout()
        lhead.addWidget(label("Steps", "title"), 1)
        self.count_label = label("", "hint")
        lhead.addWidget(self.count_label)
        left.body.addLayout(lhead)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_row)
        left.body.addWidget(self.list, 1)
        lfoot = QHBoxLayout()
        self.delete_btn = Button("Delete step", "ghost", icon="trash", height=32, font_px=12)
        self.delete_btn.clicked.connect(self._delete_step)
        lfoot.addWidget(self.delete_btn)
        lfoot.addStretch(1)
        left.body.addLayout(lfoot)
        content.addWidget(left)

        right = Card("cardSunken", margins=(14, 12, 14, 12), spacing=8)
        rhead = QHBoxLayout()
        rt = QVBoxLayout()
        rt.setSpacing(1)
        rt.addWidget(label("Where your units go", "title"))
        rt.addWidget(label("Picture taken when you opened this - drag a spot to move it, scroll to zoom in for "
                          "more precision, right-drag to pan", "hint", wrap=True))
        rhead.addLayout(rt, 1)
        reset_zoom_btn = IconButton("refresh", "Reset zoom")
        reset_zoom_btn.setToolTip("Back to fit-the-window (also: press 0 on the map)")
        reset_zoom_btn.clicked.connect(lambda: self.canvas.reset_zoom())
        rhead.addWidget(reset_zoom_btn)
        self.view_mode = Segmented([("spots", "Spots"), ("order", "Spots + order")], "spots", height=32)
        self.view_mode.setFixedWidth(220)
        rhead.addWidget(self.view_mode)
        right.body.addLayout(rhead)
        self.canvas = MapCanvas(pixmap)
        self.canvas.spot_selected.connect(self._select_step)
        self.canvas.spot_moved.connect(self._moved)
        self.view_mode.changed.connect(lambda v: (setattr(self.canvas, "show_order", v == "order"), self.canvas.update()))
        right.body.addWidget(self.canvas, 1)
        self.caption = label("Select a step or a spot.", "hint")
        right.body.addWidget(self.caption)
        content.addWidget(right, 1)
        self.body.addLayout(content, 1)

        foot = QHBoxLayout()
        foot.addWidget(label("Test plays this recording once in Roblox right now, without starting a whole run.", "hint"), 1)
        test = Button("Test in game", "subtle", icon="play", height=36, font_px=12)
        test.clicked.connect(self._test)
        foot.addWidget(test)
        self.body.addLayout(foot)
        self.card.setMinimumHeight(720)
        self._rebuild()

    # --- model

    def _rebuild(self, keep_row=None):
        self.steps = preset_core.describe_steps(self.actions)
        self.list.blockSignals(True)
        self.list.clear()
        for step in self.steps:
            item = QListWidgetItem()
            row = StepRow(step)
            item.setSizeHint(row.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
        self.list.blockSignals(False)
        real = [s for s in self.steps if s["kind"] != "wait"]
        self.count_label.setText(f"{len(real)} steps · {preset_core.timeline_length(self.actions):.1f} s")
        self.canvas.set_steps(self.steps)
        if keep_row is not None and 0 <= keep_row < len(self.steps):
            self.list.setCurrentRow(keep_row)
        else:
            self._on_row(-1)
        self.save_btn.setText("Save" if self.dirty else "Saved")
        self.save_btn.setEnabled(self.dirty)

    def _on_row(self, row):
        step = self.steps[row] if 0 <= row < len(self.steps) else None
        self.delete_btn.setEnabled(step is not None and step["kind"] != "wait")
        self.canvas.selected = row if step and step["kind"] == "place" else -1
        self.canvas.update()
        if step is None:
            self.caption.setText("Select a step or a spot.")
        elif step["kind"] == "place":
            self.caption.setText(f"Spot {step['spot']} - unit {step['unit'] or '?'} · placed at {step['time']:.1f} s · drag to move")
        elif step["kind"] == "select":
            self.caption.setText(f"Picks unit {step['unit']} at {step['time']:.1f} s without placing it")
        else:
            self.caption.setText("A pause - the recording waits here before the next step.")

    def _select_step(self, index):
        self.list.setCurrentRow(index)

    def _moved(self, index, x, y):
        step = self.steps[index]
        click_indexes = [i for i in step["action_indexes"] if self.actions[i].get("type") == "click"]
        if not click_indexes:
            return
        self.actions[click_indexes[-1]]["pos"] = [x, y]
        self.dirty = True
        self._rebuild(keep_row=index)

    def _delete_step(self):
        row = self.list.currentRow()
        if not (0 <= row < len(self.steps)) or self.steps[row]["kind"] == "wait":
            return
        for i in sorted(self.steps[row]["action_indexes"], reverse=True):
            del self.actions[i]
        self.dirty = True
        self._rebuild(keep_row=min(row, len(self.actions)))

    def _clear(self):
        if not self.actions:
            return
        if dialogs.confirm(self, "Clear every step?", "The recording will be empty until you save a new one.",
                           "Clear all", danger=True):
            self.actions = []
            self.dirty = True
            self._rebuild()

    def _save(self):
        preset_core.save_actions(self.loc, self.var, self.name, self.actions)
        self.dirty = False
        self._rebuild(keep_row=self.list.currentRow())
        self.host.log(f"Saved recording '{self.name.replace('_', ' ')}'.")

    def _record_again(self):
        if self.dirty and not dialogs.confirm(self, "Record over it?", "Unsaved edits will be lost.", "Record", danger=True):
            return
        self.done(RESULT_RECORD)

    def _test(self):
        if self.dirty:
            self._save()
        self.done(RESULT_TEST)

    def reject(self):
        if self.dirty and not dialogs.confirm(self, "Close without saving?", "Your edits will be lost.",
                                              "Discard", danger=True, cancel_text="Keep editing"):
            return
        super().reject()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            self._delete_step()
            return
        super().keyPressEvent(event)
