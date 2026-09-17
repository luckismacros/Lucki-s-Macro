# ui_qt/widgets.py
"""
The animated building blocks every Qt screen is made from.

Everything here paints itself (paintEvent) rather than relying only on a stylesheet,
because the point of moving to Qt is motion: a knob that slides, a highlight that
glides between nav items, buttons that glow on hover, numbers that count up. All
durations go through theme.dur(), so Settings > Reduce motion turns every one of
them into an instant change.
"""
from PySide6.QtCore import (
    Qt, QRectF, QPointF, QPoint, QSize, Signal, Property, QPropertyAnimation,
    QVariantAnimation, QEasingCurve, QTimer, QByteArray, QEvent,
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QPixmap, QPainterPath, QLinearGradient, QFontMetrics, QFont,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QAbstractButton, QHBoxLayout, QVBoxLayout,
    QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QStackedWidget, QSizePolicy,
    QApplication, QScrollArea, QComboBox,
)

from . import theme


# ----------------------------------------------------------------------------- helpers

def mix(a, b, t):
    ca, cb = QColor(a), QColor(b)
    return QColor(
        round(ca.red() + (cb.red() - ca.red()) * t),
        round(ca.green() + (cb.green() - ca.green()) * t),
        round(ca.blue() + (cb.blue() - ca.blue()) * t),
        round(ca.alpha() + (cb.alpha() - ca.alpha()) * t),
    )


def with_alpha(color, alpha):
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def label(text="", role=None, wrap=False, parent=None):
    lbl = QLabel(text, parent)
    if role:
        lbl.setProperty("role", role)
    if wrap:
        lbl.setWordWrap(True)
    return lbl


def repolish(widget):
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def add_shadow(widget, blur=28, alpha=0.45, dy=10, color="#000000"):
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    effect.setColor(with_alpha(color, alpha))
    widget.setGraphicsEffect(effect)
    return effect


# ----------------------------------------------------------------------------- icons

ICONS = {
    "story": '<path d="M4 5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2V5z"/><path d="M4 19a2 2 0 0 1 2-2h12"/>',
    "raids": '<path d="M14.5 17.5 3 6V3h3l11.5 11.5"/><path d="m13 19 6-6"/><path d="m16 16 4 4"/>',
    "challenges": '<path d="M8 21h8"/><path d="M12 17v4"/><path d="M7 4h10v5a5 5 0 0 1-10 0V4z"/>'
                  '<path d="M17 5h3v2a3 3 0 0 1-3 3"/><path d="M7 5H4v2a3 3 0 0 0 3 3"/>',
    "portals": '<circle cx="12" cy="12" r="9"/><path d="M12 7a5 5 0 1 0 5 5"/>',
    "expeditions": '<circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2 5-5 2 2-5 5-2z"/>',
    "others": '<rect x="4" y="4" width="6" height="6" rx="1.5"/><rect x="14" y="4" width="6" height="6" rx="1.5"/>'
              '<rect x="4" y="14" width="6" height="6" rx="1.5"/><rect x="14" y="14" width="6" height="6" rx="1.5"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.9 4.9 7 7M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1 7 17M17 7l2.1-2.1"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5V14M12 17v.5"/>',
    "release": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/>',
    "quit": '<path d="M6 6l12 12M18 6 6 18"/>',
    "close": '<path d="M6 6l12 12M18 6 6 18"/>',
    "check": '<path d="m5 12 5 5 9-10"/>',
    "alert": '<circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16.5v.5"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.5"/>',
    "cross": '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "chevron_right": '<path d="m9 6 6 6-6 6"/>',
    "chevron_left": '<path d="m15 6-6 6 6 6"/>',
    "chevron_down": '<path d="m6 9 6 6 6-6"/>',
    "chevron_up": '<path d="m6 15 6-6 6 6"/>',

    "trophy": '<path d="M8 21h8"/><path d="M12 17v4"/><path d="M7 4h10v5a5 5 0 0 1-10 0V4z"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16v4z"/><path d="m14 6 4 4"/>',
    "trash": '<path d="M4 7h16"/><path d="M10 11v6M14 11v6"/><path d="M6 7l1 13h10l1-13"/><path d="M9 7V4h6v3"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    "upload": '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
    "download": '<path d="M12 4v12M7 11l5 5 5-5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
    "monitor": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    "bell": '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    "target": '<circle cx="12" cy="12" r="3"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/>',
    "log": '<path d="m5 8 4 4-4 4M12 16h7"/>',
    "chart": '<path d="M4 19V5M4 19h16M8 15v-4M12 15V8M16 15v-6"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>',
    "palette": '<circle cx="12" cy="12" r="9"/><circle cx="8.5" cy="10" r="1.2"/><circle cx="12" cy="7.5" r="1.2"/><circle cx="15.5" cy="10" r="1.2"/>',
    "keyboard": '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
    "shield": '<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/>',
}
FILLED_ICONS = {
    "play": '<path d="M7 5v14l12-7z"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2.5"/>',
    "record": '<circle cx="12" cy="12" r="7"/>',
    "logo": '<path d="M7 5v14l12-7z"/>',
}
_pixmap_cache = {}


def icon_pixmap(name, color, size=18, stroke=1.9):
    ratio = 2.0
    screen = QApplication.primaryScreen()
    if screen is not None:
        ratio = max(1.0, screen.devicePixelRatio()) * 1.5
    key = (name, QColor(color).name(QColor.HexArgb), size, stroke, ratio)
    cached = _pixmap_cache.get(key)
    if cached is not None:
        return cached
    c = QColor(color)
    hex_color = c.name()
    opacity = c.alphaF()
    if name in FILLED_ICONS:
        body = FILLED_ICONS[name]
        attrs = f'fill="{hex_color}" fill-opacity="{opacity}" stroke="none"'
    else:
        body = ICONS.get(name, ICONS["info"])
        attrs = (f'fill="none" stroke="{hex_color}" stroke-opacity="{opacity}" stroke-width="{stroke}" '
                 f'stroke-linecap="round" stroke-linejoin="round"')
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" {attrs}>{body}</svg>'
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    renderer.render(p)
    p.end()
    pm.setDevicePixelRatio(ratio)
    _pixmap_cache[key] = pm
    return pm


def icon_label(name, color=theme.TEXT_MUTED, size=18, stroke=1.9):
    lbl = QLabel()
    lbl.setPixmap(icon_pixmap(name, color, size, stroke))
    lbl.setFixedSize(size, size)
    return lbl


# ----------------------------------------------------------------------------- scrolling

class VScroll(QScrollArea):
    """
    Scrolls vertically only, and keeps its content exactly as wide as the visible
    area. A plain QScrollArea lets content grow wider than itself (a long dropdown
    entry is enough), which pushes cards and switches off the right edge.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)

    def setWidget(self, widget):
        super().setWidget(widget)
        widget.setMinimumWidth(0)
        self._fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _fit(self):
        w = self.widget()
        if w is not None:
            w.setFixedWidth(self.viewport().width())


def shrinkable_combo(box):
    """Lets a dropdown shrink below its longest entry instead of forcing its parent wider."""
    box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    box.setMinimumContentsLength(6)
    box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return box


# ----------------------------------------------------------------------------- cards

class Card(QFrame):
    """A rounded surface. variant: 'card' (raised), 'cardSunken', 'panel', 'dashed'."""

    def __init__(self, variant="card", parent=None, margins=(12, 12, 12, 12), spacing=8):
        super().__init__(parent)
        self.setObjectName(variant)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*margins)
        self.body.setSpacing(spacing)


class StepBadge(QWidget):
    """The small circle on a setup step: a number, or a green check once it's done."""

    def __init__(self, number, parent=None):
        super().__init__(parent)
        self.number = number
        self._done = 0.0
        self.setFixedSize(20, 20)
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._on_value)

    def _on_value(self, v):
        self._done = float(v)
        self.update()

    def set_done(self, done):
        target = 1.0 if done else 0.0
        if abs(target - self._done) < 0.01:
            return
        self._anim.stop()
        self._anim.setDuration(theme.dur(260))
        self._anim.setEasingCurve(QEasingCurve.OutBack)
        self._anim.setStartValue(self._done)
        self._anim.setEndValue(target)
        self._anim.start()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = max(0.0, min(1.0, self._done))
        p.setPen(Qt.NoPen)
        p.setBrush(mix(theme.BORDER, with_alpha(theme.SUCCESS, 0.18), t))
        p.drawEllipse(QRectF(0, 0, 20, 20))
        if t > 0.5:
            pm = icon_pixmap("check", theme.SUCCESS_TEXT, 12, 3)
            p.setOpacity((t - 0.5) * 2)
            p.drawPixmap(4, 4, pm)
        else:
            p.setOpacity(1 - t * 2)
            p.setPen(QColor(theme.TEXT_MUTED))
            f = QFont(theme.FONT_FAMILY)
            f.setPixelSize(11)
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRectF(0, 0, 20, 20), Qt.AlignCenter, str(self.number))
        p.end()


class StepCard(Card):
    """A numbered setup step: badge, title, optional trailing widget, then its controls."""

    def __init__(self, number, title, trailing=None, parent=None):
        super().__init__("card", parent, margins=(12, 11, 12, 12), spacing=8)
        header = QHBoxLayout()
        header.setSpacing(8)
        self.badge = StepBadge(number)
        header.addWidget(self.badge)
        self.title = label(title, "title")
        header.addWidget(self.title, 1)
        if trailing is not None:
            header.addWidget(trailing)
        self.body.addLayout(header)

    def set_done(self, done):
        self.badge.set_done(done)


# ----------------------------------------------------------------------------- toggle

class ToggleSwitch(QAbstractButton):
    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(38, 22)
        self._pos = 1.0 if checked else 0.0
        self._hover = 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, on):
        self._anim.stop()
        self._anim.setDuration(theme.dur(180))
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get_knob(self):
        return self._pos

    def _set_knob(self, value):
        self._pos = value
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def enterEvent(self, e):
        self._hover = 1.0
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = 0.0
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, event):
        if self._anim.state() != QPropertyAnimation.Running:
            self._pos = 1.0 if self.isChecked() else 0.0
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(1.0 if self.isEnabled() else 0.4)
        t = self._pos
        track = mix(theme.BG_SUNKEN, theme.accent(), t)
        p.setPen(QPen(mix(theme.BORDER_STRONG if self._hover else theme.BORDER, theme.accent(), t), 1))
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 11, 11)
        knob_x = 3 + t * (self.width() - 22)
        p.setPen(Qt.NoPen)
        p.setBrush(mix("#7a7a90", "#ffffff", t))
        p.drawEllipse(QRectF(knob_x, 3, 16, 16))
        p.end()


class ToggleRow(QWidget):
    """A labelled switch row: title (+ optional hint underneath) on the left, switch on the right."""
    toggled = Signal(bool)

    def __init__(self, title, hint=None, checked=False, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        text = QVBoxLayout()
        text.setSpacing(1)
        self.title = label(title)
        self.title.setStyleSheet("font-size: 12.5px;")
        text.addWidget(self.title)
        self.hint = label(hint or "", "hint", wrap=True)
        self.hint.setVisible(bool(hint))
        text.addWidget(self.hint)
        row.addLayout(text, 1)
        self.switch = ToggleSwitch(checked)
        self.switch.toggled.connect(self.toggled.emit)
        row.addWidget(self.switch, 0, Qt.AlignVCenter)

    def isChecked(self):
        return self.switch.isChecked()

    def setChecked(self, value):
        self.switch.setChecked(value)

    def set_hint(self, text):
        self.hint.setText(text or "")
        self.hint.setVisible(bool(text))


# ----------------------------------------------------------------------------- segmented

class Segmented(QWidget):
    """A pill-shaped choice control whose selected highlight slides between options."""
    changed = Signal(str)

    def __init__(self, options, value=None, parent=None, height=34):
        super().__init__(parent)
        self._buttons = {}
        self._value = None
        self._thumb = QRectF()
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_thumb)
        self.setFixedHeight(height)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(3, 3, 3, 3)
        self._row.setSpacing(3)
        self.set_options(options, value)

    def set_options(self, options, value=None):
        for b in self._buttons.values():
            b.deleteLater()
        self._buttons = {}
        for opt in options:
            key, text = (opt if isinstance(opt, tuple) else (opt, opt))
            b = QPushButton(text)
            b.setObjectName("segBtn")
            b.setCursor(Qt.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            b.clicked.connect(lambda _=False, k=key: self.set_value(k, emit=True))
            self._row.addWidget(b)
            self._buttons[key] = b
        keys = list(self._buttons)
        self._value = None
        self.set_value(value if value in self._buttons else (keys[0] if keys else None), animate=False)

    def value(self):
        return self._value

    def set_value(self, key, emit=False, animate=True):
        if key not in self._buttons:
            return
        changed = key != self._value
        self._value = key
        for k, b in self._buttons.items():
            b.setProperty("selected", "true" if k == key else "false")
            repolish(b)
        target = QRectF(self._buttons[key].geometry())
        if animate and not self._thumb.isNull() and self.isVisible():
            self._anim.stop()
            self._anim.setDuration(theme.dur(220))
            self._anim.setStartValue(self._thumb)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._thumb = target
            self.update()
        if changed and emit:
            self.changed.emit(key)

    def _on_thumb(self, rect):
        self._thumb = QRectF(rect)
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._snap)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._snap)

    def _snap(self):
        if self._value in self._buttons and self._anim.state() != QVariantAnimation.Running:
            self._thumb = QRectF(self._buttons[self._value].geometry())
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(1.0 if self.isEnabled() else 0.5)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(QColor(theme.BG_SUNKEN))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), theme.RADIUS_CONTROL, theme.RADIUS_CONTROL)
        if not self._thumb.isNull():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.SEGMENT))
            p.drawRoundedRect(self._thumb, 6, 6)
        p.end()


# ----------------------------------------------------------------------------- buttons

class Button(QAbstractButton):
    """
    kind: 'accent' | 'success' | 'danger' | 'ghost' | 'subtle' | 'link'
    Hover fades the fill, press darkens it, and solid kinds carry a soft glow that
    grows on hover.
    """

    def __init__(self, text="", kind="accent", icon=None, key_hint=None, parent=None, height=36,
                 font_px=13, bold=True, glow=True):
        super().__init__(parent)
        self.setText(text)
        self.kind = kind
        self.icon_name = icon
        self.key_hint = key_hint
        self.font_px = font_px
        self.bold = bold
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(height)
        self._hover = 0.0
        self._press = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._on_hover)
        self._glow = None
        if glow and kind in ("accent", "success", "danger"):
            self._glow = QGraphicsDropShadowEffect(self)
            self._glow.setOffset(0, 6)
            self._glow.setBlurRadius(18)
            self._glow.setColor(with_alpha(self._base_color(), 0.28))
            self.setGraphicsEffect(self._glow)

    def set_kind(self, kind):
        self.kind = kind
        if self._glow is not None:
            self._glow.setColor(with_alpha(self._base_color(), 0.28))
        self.update()

    def _base_color(self):
        return {
            "accent": theme.accent(), "success": theme.SUCCESS, "danger": theme.DANGER,
            "ghost": "#00000000", "subtle": theme.BG_RAISED, "link": "#00000000",
        }.get(self.kind, theme.accent())

    def _hover_color(self):
        return {
            "accent": theme.accent_hover(), "success": "#16a34a", "danger": "#dc2626",
            "ghost": theme.BG_ELEVATED, "subtle": theme.BG_ELEVATED, "link": "#00000000",
        }.get(self.kind, theme.accent_hover())

    def _text_color(self):
        return {
            "accent": theme.on_accent(), "success": theme.SUCCESS_INK, "danger": "#ffffff",
            "ghost": theme.TEXT_MUTED, "subtle": theme.TEXT, "link": theme.accent(),
        }.get(self.kind, "#ffffff")

    def _on_hover(self, v):
        self._hover = float(v)
        if self._glow is not None:
            self._glow.setBlurRadius(18 + 14 * self._hover)
            self._glow.setColor(with_alpha(self._base_color(), 0.28 + 0.2 * self._hover))
        self.update()

    def _animate_hover(self, target):
        self._anim.stop()
        self._anim.setDuration(theme.dur(140))
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e):
        self._animate_hover(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._animate_hover(0.0)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self._press = 1.0
        self.update()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._press = 0.0
        self.update()
        super().mouseReleaseEvent(e)

    def _font(self):
        f = QFont(theme.FONT_FAMILY)
        f.setPixelSize(self.font_px)
        f.setWeight(QFont.DemiBold if self.bold else QFont.Normal)
        return f

    def sizeHint(self):
        fm = QFontMetrics(self._font())
        w = fm.horizontalAdvance(self.text()) + 28
        if self.icon_name:
            w += 22
        if self.key_hint:
            w += fm.horizontalAdvance(self.key_hint) + 22
        return QSize(w, self.height())

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(1.0 if self.isEnabled() else 0.45)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        radius = min(theme.RADIUS_CONTROL + 2, self.height() / 2)
        fill = mix(self._base_color(), self._hover_color(), self._hover if self.isEnabled() else 0)
        if self._press:
            fill = mix(fill, "#000000", 0.18)
        if self.kind in ("ghost", "subtle"):
            p.setPen(QPen(mix(theme.BORDER, theme.BORDER_STRONG, self._hover), 1))
        else:
            p.setPen(Qt.NoPen)
        p.setBrush(fill)
        if self.kind != "link":
            p.drawRoundedRect(rect, radius, radius)

        text_color = QColor(self._text_color())
        if self.kind == "ghost":
            text_color = mix(theme.TEXT_MUTED, theme.TEXT, self._hover)
        font = self._font()
        p.setFont(font)
        fm = QFontMetrics(font)
        text_w = fm.horizontalAdvance(self.text())
        icon_w = 22 if self.icon_name else 0
        hint_w = (fm.horizontalAdvance(self.key_hint) + 22) if self.key_hint else 0
        total = icon_w + text_w + hint_w
        x = (self.width() - total) / 2
        cy = self.height() / 2
        if self.icon_name:
            size = 15 if self.height() < 44 else 17
            p.drawPixmap(int(x), int(cy - size / 2), icon_pixmap(self.icon_name, text_color, size, 2.1))
            x += icon_w
        p.setPen(text_color)
        p.drawText(QRectF(x, 0, text_w + 2, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text())
        x += text_w + 8
        if self.key_hint:
            f2 = QFont(font)
            f2.setPixelSize(11)
            f2.setBold(True)
            p.setFont(f2)
            kw = QFontMetrics(f2).horizontalAdvance(self.key_hint) + 12
            kr = QRectF(x, cy - 9, kw, 18)
            p.setPen(Qt.NoPen)
            p.setBrush(with_alpha("#000000", 0.2))
            p.drawRoundedRect(kr, 5, 5)
            p.setPen(text_color)
            p.drawText(kr, Qt.AlignCenter, self.key_hint)
        p.end()


class IconButton(QAbstractButton):
    """A compact icon + caption button for toolbars and the sidebar footer."""

    def __init__(self, icon, text="", parent=None, color=theme.TEXT_MUTED, hover_color=theme.TEXT,
                 danger=False, size=15):
        super().__init__(parent)
        self.icon_name = icon
        self.setText(text)
        self._color = color
        self._hover_color = theme.DANGER_TEXT if danger else hover_color
        self._icon_size = size
        self._hover = 0.0
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(32)
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(lambda v: (setattr(self, "_hover", float(v)), self.update()))

    def sizeHint(self):
        f = QFont(theme.FONT_FAMILY)
        f.setPixelSize(12)
        return QSize(QFontMetrics(f).horizontalAdvance(self.text()) + self._icon_size + 22, 32)

    def enterEvent(self, e):
        self._anim.stop(); self._anim.setDuration(theme.dur(140)); self._anim.setStartValue(self._hover); self._anim.setEndValue(1.0); self._anim.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._anim.stop(); self._anim.setDuration(theme.dur(140)); self._anim.setStartValue(self._hover); self._anim.setEndValue(0.0); self._anim.start()
        super().leaveEvent(e)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(1.0 if self.isEnabled() else 0.4)
        if self._hover > 0:
            p.setPen(Qt.NoPen)
            p.setBrush(with_alpha(theme.BG_ELEVATED, self._hover))
            p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 8, 8)
        color = mix(self._color, self._hover_color, self._hover)
        f = QFont(theme.FONT_FAMILY)
        f.setPixelSize(12)
        p.setFont(f)
        tw = QFontMetrics(f).horizontalAdvance(self.text())
        gap = 6 if self.text() else 0
        total = self._icon_size + gap + tw
        x = (self.width() - total) / 2
        p.drawPixmap(int(x), int((self.height() - self._icon_size) / 2),
                     icon_pixmap(self.icon_name, color, self._icon_size, 1.8))
        if self.text():
            p.setPen(color)
            p.drawText(QRectF(x + self._icon_size + gap, 0, tw + 2, self.height()), Qt.AlignVCenter, self.text())
        p.end()


# ----------------------------------------------------------------------------- navigation

class NavItem(QAbstractButton):
    def __init__(self, key, text, icon, parent=None):
        super().__init__(parent)
        self.key = key
        self.icon_name = icon
        self.setText(text)
        self.trailing = ""
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(36)
        self._hover = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(lambda v: (setattr(self, "_hover", float(v)), self.update()))

    def enterEvent(self, e):
        self._anim.stop(); self._anim.setDuration(theme.dur(120)); self._anim.setStartValue(self._hover); self._anim.setEndValue(1.0); self._anim.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._anim.stop(); self._anim.setDuration(theme.dur(160)); self._anim.setStartValue(self._hover); self._anim.setEndValue(0.0); self._anim.start()
        super().leaveEvent(e)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        active = self.isChecked()
        if not active and self._hover > 0:
            p.setPen(Qt.NoPen)
            p.setBrush(with_alpha(theme.BG_ELEVATED, self._hover))
            p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 8, 8)
        if not self.isEnabled():
            color = QColor(theme.TEXT_DIM)
        elif active:
            color = QColor(theme.on_accent())
        else:
            color = mix(theme.TEXT_MUTED, theme.TEXT, self._hover)
        p.drawPixmap(10, 9, icon_pixmap(self.icon_name, color, 18, 1.9 if active else 1.75))
        f = QFont(theme.FONT_FAMILY)
        f.setPixelSize(13)
        f.setWeight(QFont.DemiBold if active else QFont.Normal)
        p.setFont(f)
        p.setPen(color)
        p.drawText(QRectF(39, 0, self.width() - 45, self.height()), Qt.AlignVCenter, self.text())
        if self.trailing:
            f2 = QFont(f)
            f2.setPixelSize(11)
            f2.setWeight(QFont.Normal)
            p.setFont(f2)
            c2 = QColor(color)
            c2.setAlphaF(0.8)
            p.setPen(c2)
            p.drawText(QRectF(0, 0, self.width() - 10, self.height()), Qt.AlignVCenter | Qt.AlignRight, self.trailing)
        p.end()


class NavList(QWidget):
    """The mode list. The accent pill behind the active item glides to the one you pick."""
    changed = Signal(str)

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self._items = {}
        self._pill = QRectF()
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(lambda r: (setattr(self, "_pill", QRectF(r)), self.update()))
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        for key, text, icon, enabled in items:
            item = NavItem(key, text, icon)
            item.setEnabled(enabled)
            if not enabled:
                item.trailing = "soon"
            item.clicked.connect(lambda _=False, k=key: self.select(k, emit=True))
            col.addWidget(item)
            self._items[key] = item
        self._current = None

    def item(self, key):
        return self._items.get(key)

    def select(self, key, emit=False, animate=True):
        if key not in self._items:
            return
        for k, it in self._items.items():
            it.setChecked(k == key)
        target = QRectF(self._items[key].geometry())
        if animate and not self._pill.isNull() and self.isVisible():
            self._anim.stop()
            self._anim.setDuration(theme.dur(260))
            self._anim.setStartValue(self._pill)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._pill = target
            self.update()
        changed = key != self._current
        self._current = key
        if emit and changed:
            self.changed.emit(key)

    def set_trailing(self, key, text):
        for k, it in self._items.items():
            if it.isEnabled():
                it.trailing = text if k == key else ""
            it.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._snap)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._snap)

    def _snap(self):
        if self._current in self._items and self._anim.state() != QVariantAnimation.Running:
            self._pill = QRectF(self._items[self._current].geometry())
            self.update()

    def paintEvent(self, event):
        if self._pill.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        glow = QRectF(self._pill).adjusted(-2, 2, 2, 6)
        for i, a in enumerate((0.07, 0.05, 0.03)):
            p.setPen(Qt.NoPen)
            p.setBrush(with_alpha(theme.accent(), a))
            p.drawRoundedRect(glow.adjusted(-i * 2, -i, i * 2, i * 2), 10 + i, 10 + i)
        p.setBrush(QColor(theme.accent()))
        p.drawRoundedRect(self._pill, 8, 8)
        p.end()


# ----------------------------------------------------------------------------- status bits

class PulseDot(QWidget):
    """A status dot that radiates a soft ring while `pulsing` is on."""

    def __init__(self, color=theme.SUCCESS, pulsing=True, parent=None, size=16, dot=8):
        super().__init__(parent)
        self._color = color
        self._pulsing = pulsing
        self._phase = 0.0
        self._dot = dot
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def set_state(self, color, pulsing):
        self._color = color
        self._pulsing = pulsing
        self.update()

    def _tick(self):
        if self._pulsing and not theme.reduce_motion() and self.isVisible():
            self._phase = (self._phase + 0.033 / 1.6) % 1.0
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        if self._pulsing and not theme.reduce_motion():
            r = self._dot / 2 + self._phase * (self.width() / 2 - self._dot / 2)
            p.setPen(Qt.NoPen)
            p.setBrush(with_alpha(self._color, 0.45 * (1 - self._phase)))
            p.drawEllipse(c, r, r)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self._color))
        p.drawEllipse(c, self._dot / 2, self._dot / 2)
        p.end()


class StatusPill(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(9, 3, 11, 3)
        row.setSpacing(6)
        self.dot = PulseDot(theme.TEXT_DIM, False, size=12, dot=7)
        row.addWidget(self.dot)
        self.text = label("Idle")
        row.addWidget(self.text)
        self._color = theme.TEXT_DIM
        self.set_state("Idle", theme.TEXT_DIM, False)

    def set_state(self, text, color, pulsing):
        self._color = color
        self.text.setText(text)
        self.text.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {color};")
        self.dot.set_state(color, pulsing)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(with_alpha(self._color, 0.35), 1))
        p.setBrush(with_alpha(self._color, 0.12))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), self.height() / 2, self.height() / 2)
        p.end()


class ActivityBar(QWidget):
    """A thin bar with a light sweeping across it while something is in progress."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(4)
        self._phase = 0.0
        self._active = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def set_active(self, active):
        self._active = active
        self.update()

    def _tick(self):
        if self._active and self.isVisible():
            self._phase = (self._phase + 0.016 / 1.6) % 1.0
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.BG_SUNKEN))
        p.drawRoundedRect(QRectF(0, 0, self.width(), 4), 2, 2)
        if self._active:
            if theme.reduce_motion():
                p.setBrush(with_alpha(theme.accent(), 0.6))
                p.drawRoundedRect(QRectF(0, 0, self.width(), 4), 2, 2)
            else:
                w = self.width() * 0.3
                x = -w + self._phase * (self.width() + w)
                grad = QLinearGradient(x, 0, x + w, 0)
                grad.setColorAt(0, with_alpha(theme.accent(), 0))
                grad.setColorAt(0.5, QColor(theme.accent()))
                grad.setColorAt(1, with_alpha(theme.accent(), 0))
                p.setBrush(grad)
                p.drawRoundedRect(QRectF(x, 0, w, 4), 2, 2)
        p.end()


class CheckRow(QWidget):
    """One line of the ready check: an ok / warning / problem icon, a label and a detail."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.icon = QLabel()
        self.icon.setFixedSize(14, 14)
        row.addWidget(self.icon)
        self.text = label(text)
        self.text.setStyleSheet("font-size: 12.5px;")
        row.addWidget(self.text, 1)
        self.detail = label("")
        row.addWidget(self.detail)
        self.state = None
        self.set_state("idle", "")

    def set_state(self, state, detail, text=None):
        self.state = state
        if text is not None:
            self.text.setText(text)
        icon, color = {
            "ok": ("check", theme.SUCCESS_TEXT),
            "warn": ("alert", theme.WARNING),
            "bad": ("cross", theme.DANGER_TEXT),
            "idle": ("clock", theme.TEXT_DIM),
        }[state]
        self.icon.setPixmap(icon_pixmap(icon, color, 14, 2.6 if state == "ok" else 2.1))
        detail_color = {"ok": theme.TEXT_DIM, "idle": theme.TEXT_DIM}.get(state, color)
        self.detail.setText(detail)
        self.detail.setStyleSheet(f"font-size: 11.5px; color: {detail_color};")


class StatCard(Card):
    """A big number with a label. Integer values count up to their new value."""

    def __init__(self, title, parent=None):
        super().__init__("card", parent, margins=(14, 12, 14, 12), spacing=2)
        self.title = label(title)
        self.title.setStyleSheet(f"font-size: 12px; color: {theme.TEXT_MUTED};")
        self.body.addWidget(self.title)
        self.value = label("-", "stat")
        self.body.addWidget(self.value)
        self.body.addStretch(1)
        self.sub = label("", "hint")
        self.body.addWidget(self.sub)
        self._int_value = None
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(lambda v: self.value.setText(str(int(v))))

    def set_value(self, value, sub=None, color=None):
        if isinstance(value, int):
            if self._int_value is not None and value != self._int_value and not theme.reduce_motion():
                self._anim.stop()
                self._anim.setDuration(500)
                self._anim.setStartValue(self._int_value)
                self._anim.setEndValue(value)
                self._anim.start()
            else:
                self.value.setText(str(value))
            self._int_value = value
        else:
            self._int_value = None
            self.value.setText(str(value))
        self.value.setStyleSheet(f"color: {color};" if color else "")
        if sub is not None:
            self.sub.setText(sub)


# ----------------------------------------------------------------------------- chips & cards

class Chip(QPushButton):
    def __init__(self, text, checked=False, parent=None):
        super().__init__(text, parent)
        self.setObjectName("chip")
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)


class FlowRow(QWidget):
    """Lays chips out left to right, wrapping onto new lines."""

    def __init__(self, parent=None, spacing=5):
        super().__init__(parent)
        self._widgets = []
        self._spacing = spacing

    def add(self, widget):
        widget.setParent(self)
        widget.show()
        self._widgets.append(widget)
        self._relayout()

    def clear(self):
        for w in self._widgets:
            w.deleteLater()
        self._widgets = []
        self._relayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self):
        x = y = 0
        line_h = 0
        width = max(1, self.width())
        for w in self._widgets:
            hint = w.sizeHint()
            if x > 0 and x + hint.width() > width:
                x = 0
                y += line_h + self._spacing
                line_h = 0
            w.setGeometry(x, y, hint.width(), hint.height())
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        self.setMinimumHeight(y + line_h)

    def sizeHint(self):
        return QSize(self.width(), self.minimumHeight())


class ImageCard(QAbstractButton):
    """A selectable card with artwork on top and a caption. The accent ring fades in when picked."""

    def __init__(self, key, text, pixmap_path=None, parent=None, image_height=52):
        super().__init__(parent)
        self.key = key
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self._pixmap = QPixmap(pixmap_path) if pixmap_path else QPixmap()
        self._image_height = image_height
        self._sel = 0.0
        self._hover = 0.0
        self.setFixedHeight(image_height + 26)
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(lambda v: (setattr(self, "_sel", float(v)), self.update()))
        self.toggled.connect(self._animate)

    def _animate(self, on):
        self._anim.stop()
        self._anim.setDuration(theme.dur(200))
        self._anim.setStartValue(self._sel)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def enterEvent(self, e):
        self._hover = 1.0
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = 0.0
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, event):
        if self._anim.state() != QVariantAnimation.Running:
            self._sel = 1.0 if self.isChecked() else 0.0
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(2, 2, self.width() - 4, self.height() - 4)
        if self._sel > 0:
            p.setPen(Qt.NoPen)
            p.setBrush(with_alpha(theme.accent(), 0.18 * self._sel))
            p.drawRoundedRect(r.adjusted(-2, -2, 2, 2), 11, 11)
        path = QPainterPath()
        path.addRoundedRect(r, 9, 9)
        p.setClipPath(path)
        p.fillRect(r, QColor(theme.BG_SUNKEN))
        if not self._pixmap.isNull():
            img_rect = QRectF(r.x(), r.y(), r.width(), self._image_height)
            scaled = self._pixmap.scaled(int(img_rect.width() * 2), int(img_rect.height() * 2),
                                         Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            sx = (scaled.width() - img_rect.width() * 2) / 2
            sy = (scaled.height() - img_rect.height() * 2) / 2
            p.setOpacity(0.78 + 0.22 * max(self._sel, self._hover))
            p.drawPixmap(img_rect, scaled, QRectF(sx, sy, img_rect.width() * 2, img_rect.height() * 2))
            p.setOpacity(1.0)
        p.setClipping(False)
        f = QFont(theme.FONT_FAMILY)
        f.setPixelSize(11)
        f.setWeight(QFont.DemiBold if self.isChecked() else QFont.Normal)
        p.setFont(f)
        p.setPen(mix(theme.TEXT_MUTED, theme.TEXT, max(self._sel, self._hover * 0.6)))
        p.drawText(QRectF(r.x() + 8, r.y() + self._image_height, r.width() - 12, r.height() - self._image_height),
                   Qt.AlignVCenter, self.text())
        p.setPen(QPen(mix(theme.BORDER, theme.accent(), self._sel), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 9, 9)
        p.end()


# ----------------------------------------------------------------------------- transitions

class FadeStack(QStackedWidget):
    """A stacked widget whose pages fade and rise into place when switched."""

    # Sized to the page on show, not the tallest page - otherwise a short page (Portals)
    # leaves a gap the height of the longest one (Expeditions) before whatever follows.
    def sizeHint(self):
        w = self.currentWidget()
        return w.sizeHint() if w is not None else super().sizeHint()

    def minimumSizeHint(self):
        w = self.currentWidget()
        return w.minimumSizeHint() if w is not None else super().minimumSizeHint()

    def set_current(self, widget):
        same = widget is self.currentWidget()
        self.setCurrentWidget(widget)
        for i in range(self.count()):
            page = self.widget(i)
            page.setSizePolicy(QSizePolicy.Preferred,
                               QSizePolicy.Preferred if page is widget else QSizePolicy.Ignored)
        self.updateGeometry()
        if same:
            return
        if theme.reduce_motion():
            return
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        anim = QVariantAnimation(widget)
        anim.setDuration(220)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.valueChanged.connect(lambda v: effect.setOpacity(float(v)))
        anim.finished.connect(lambda: widget.setGraphicsEffect(None))
        anim.start(QVariantAnimation.DeleteWhenStopped)


class SwapColumn(QWidget):
    """
    Shows one of several pages, fading the new one in - like FadeStack, but hidden
    pages take no space at all. A stacked widget reserves room for its tallest page,
    which left a gap under short pages and pushed whatever follows out of view.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._col = QVBoxLayout(self)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(0)
        self._pages = []
        self._current = None

    def addWidget(self, widget):
        self._pages.append(widget)
        self._col.addWidget(widget)
        widget.setVisible(self._current is None)
        if self._current is None:
            self._current = widget

    def currentWidget(self):
        return self._current

    def set_current(self, widget):
        if widget is self._current and widget.isVisible():
            return
        for page in self._pages:
            if page is not widget:
                page.hide()
        self._current = widget
        widget.show()
        if theme.reduce_motion():
            return
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        anim = QVariantAnimation(widget)
        anim.setDuration(220)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.valueChanged.connect(lambda v: effect.setOpacity(float(v)))
        anim.finished.connect(lambda: widget.setGraphicsEffect(None))
        anim.start(QVariantAnimation.DeleteWhenStopped)


class Overlay(QWidget):
    """
    A dimmed layer over the whole window holding a panel that slides in - used for
    Settings (from the right) and the welcome wizard / dialogs (rising in the middle).
    Clicking the dim area or pressing Escape closes it.
    """
    closed = Signal()

    def __init__(self, host, panel, mode="right", width=580, dismissable=True):
        super().__init__(host)
        self.host = host
        self.panel = panel
        self.mode = mode
        self.panel_width = width
        self.dismissable = dismissable
        self._dim = 0.0
        panel.setParent(self)
        self.setFocusPolicy(Qt.StrongFocus)
        self.hide()
        host.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self.host and event.type() == QEvent.Resize and self.isVisible():
            self._layout_panel(final=True)
        return False

    def _layout_panel(self, final=True, t=1.0):
        self.setGeometry(0, 0, self.host.width(), self.host.height())
        if self.mode == "right":
            w = min(self.panel_width, self.width())
            x = self.width() - w + (1 - t) * 60
            self.panel.setGeometry(int(x), 0, w, self.height())
        else:
            hint = self.panel.sizeHint()
            w = min(self.panel_width, self.width() - 40)
            h = min(max(hint.height(), self.panel.minimumHeight()), self.height() - 40)
            x = (self.width() - w) / 2
            y = (self.height() - h) / 2 + (1 - t) * 18
            self.panel.setGeometry(int(x), int(y), w, h)

    def open(self):
        self.raise_()
        self.show()
        self.setFocus()
        self._layout_panel(t=0.0)
        effect = QGraphicsOpacityEffect(self.panel)
        self.panel.setGraphicsEffect(effect)
        anim = QVariantAnimation(self)
        anim.setDuration(theme.dur(320))
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        def step(v):
            v = float(v)
            self._dim = v
            effect.setOpacity(v)
            self._layout_panel(t=v)
            self.update()

        anim.valueChanged.connect(step)
        anim.finished.connect(lambda: self.panel.setGraphicsEffect(None))
        if theme.reduce_motion():
            step(1.0)
            self.panel.setGraphicsEffect(None)
        else:
            anim.start(QVariantAnimation.DeleteWhenStopped)

    def close_overlay(self):
        if not self.isVisible():
            return
        if theme.reduce_motion():
            self.hide()
            self.closed.emit()
            return
        effect = QGraphicsOpacityEffect(self.panel)
        self.panel.setGraphicsEffect(effect)
        anim = QVariantAnimation(self)
        anim.setDuration(200)
        anim.setEasingCurve(QEasingCurve.InCubic)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)

        def step(v):
            v = float(v)
            self._dim = v
            effect.setOpacity(v)
            self._layout_panel(t=v)
            self.update()

        def done():
            self.hide()
            self.panel.setGraphicsEffect(None)
            self.closed.emit()

        anim.valueChanged.connect(step)
        anim.finished.connect(done)
        anim.start(QVariantAnimation.DeleteWhenStopped)

    def mousePressEvent(self, event):
        if self.dismissable and not self.panel.geometry().contains(event.position().toPoint()):
            self.close_overlay()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.dismissable:
            self.close_overlay()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), with_alpha("#05050a", 0.6 * self._dim))
        p.end()


# ----------------------------------------------------------------------------- toasts

class Toast(QWidget):
    """
    A floating notice that slides in over the game area and fades away by itself.
    A separate top-level tool window, because the docked game area is a see-through
    hole in the main window - nothing drawn inside the main window can appear there.
    """

    def __init__(self, title, body, kind="success", timeout_ms=6000):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFixedWidth(392)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 18)
        card = QFrame()
        card.setObjectName("toastCard")
        icon_name, color = {
            "success": ("trophy", theme.SUCCESS_TEXT), "info": ("info", theme.accent()),
            "warning": ("alert", theme.WARNING), "error": ("alert", theme.DANGER_TEXT),
        }.get(kind, ("info", theme.accent()))
        card.setStyleSheet(f"#toastCard {{ background: rgba(21,21,31,242); border: 1px solid {theme.BORDER}; border-radius: 14px; }}")
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 13, 14, 13)
        row.setSpacing(12)
        badge = QLabel()
        badge.setFixedSize(34, 34)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(f"background: {theme.rgba(QColor(color).name(), 0.15)}; border-radius: 10px;")
        badge.setPixmap(icon_pixmap(icon_name, color, 18, 2))
        row.addWidget(badge, 0, Qt.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(3)
        head = QHBoxLayout()
        t = label(title)
        t.setStyleSheet("font-size: 13.5px; font-weight: 700;")
        head.addWidget(t, 1)
        when = label("just now", "hint")
        head.addWidget(when)
        text.addLayout(head)
        b = label(body, wrap=True)
        b.setStyleSheet(f"font-size: 12.5px; color: {theme.TEXT_MUTED};")
        text.addWidget(b)
        row.addLayout(text, 1)
        outer.addWidget(card)
        add_shadow(card, blur=36, alpha=0.5, dy=12)
        self._timeout = timeout_ms
        self._target = QPoint()

    def show_at(self, top_right):
        self.adjustSize()
        self._target = QPoint(top_right.x() - self.width(), top_right.y())
        start = QPoint(self._target.x() + 48, self._target.y())
        self.move(start if not theme.reduce_motion() else self._target)
        self.setWindowOpacity(0.0 if not theme.reduce_motion() else 1.0)
        self.show()
        if not theme.reduce_motion():
            anim = QVariantAnimation(self)
            anim.setDuration(420)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.valueChanged.connect(lambda v: (
                self.move(QPoint(int(self._target.x() + 48 * (1 - float(v))), self._target.y())),
                self.setWindowOpacity(float(v))))
            anim.start(QVariantAnimation.DeleteWhenStopped)
        QTimer.singleShot(self._timeout, self.dismiss)

    def move_to(self, top_right):
        self._target = QPoint(top_right.x() - self.width(), top_right.y())
        self.move(self._target)

    def dismiss(self):
        if theme.reduce_motion():
            self.close()
            return
        anim = QVariantAnimation(self)
        anim.setDuration(260)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.valueChanged.connect(lambda v: self.setWindowOpacity(float(v)))
        anim.finished.connect(self.close)
        anim.start(QVariantAnimation.DeleteWhenStopped)
