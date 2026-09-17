# ui_qt/art.py
"""
Pictures for the interface: map islands, raid islands, portals, mode banners.

Every picture is optional and found by name, so adding one is just dropping a file in:

  assets/ui/<category>/<key>.png      (or .jpg)

  category  story_maps  raids  portals  expeditions  modes  challenges
  key       the same key config.py uses: "school_grounds", "spirit_city", "summer", ...

Missing pictures never break anything - the control simply shows without one.
"""
import os

from PySide6.QtCore import Qt, QRectF, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QPixmap, QPainterPath, QLinearGradient, QColor, QFont
from PySide6.QtWidgets import QWidget

from . import theme

ART_DIR = os.path.join("assets", "ui")
_cache = {}


def art_path(category, key):
    if not key:
        return None
    for ext in (".png", ".jpg"):
        path = os.path.join(ART_DIR, category, f"{key}{ext}")
        if os.path.exists(path):
            return path
    return None


def art_pixmap(category, key):
    path = art_path(category, key)
    if path is None:
        return QPixmap()
    if path not in _cache:
        _cache[path] = QPixmap(path)
    return _cache[path]


def draw_cover(painter, rect, pix, align_y=0.5):
    """Draws pix filling rect completely (cropping the overflow), like CSS background-size: cover."""
    if pix.isNull():
        return
    scale = max(rect.width() / pix.width(), rect.height() / pix.height())
    sw, sh = rect.width() / scale, rect.height() / scale
    sx = (pix.width() - sw) / 2
    sy = (pix.height() - sh) * align_y
    painter.drawPixmap(rect, pix, QRectF(sx, sy, sw, sh))


class ArtBanner(QWidget):
    """
    A rounded picture strip with a caption that crossfades when the selection changes
    (a new map picked, a different portal). Hides itself when there is no picture.
    """

    def __init__(self, height=92, parent=None, fit="cover"):
        super().__init__(parent)
        self.setFixedHeight(height)
        # "cover" fills the strip (landscape art); "contain" shows the whole picture on a
        # soft backdrop made from itself (tall art like portals would otherwise be cut).
        self.fit = fit
        self._old = QPixmap()
        self._new = QPixmap()
        self._caption = ""
        self._sub = ""
        self._t = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(lambda v: (setattr(self, "_t", float(v)), self.update()))
        self.hide()

    def set_art(self, category, key, caption="", sub=""):
        pix = art_pixmap(category, key)
        self._caption, self._sub = caption, sub
        if pix.cacheKey() == self._new.cacheKey() and not pix.isNull():
            self.update()
            return
        self._old, self._new = self._new, pix
        self.setVisible(not pix.isNull())
        self._anim.stop()
        if theme.reduce_motion() or self._old.isNull():
            self._t = 1.0
            self.update()
            return
        self._anim.setDuration(320)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(0, 0, self.width(), self.height())
        path = QPainterPath()
        path.addRoundedRect(r, 10, 10)
        p.setClipPath(path)
        p.fillRect(r, QColor(theme.BG_SUNKEN))
        for pix, alpha in ((self._old, 1.0 - self._t), (self._new, self._t)):
            if pix.isNull() or alpha <= 0:
                continue
            if self.fit == "contain":
                p.setOpacity(alpha * 0.35)
                draw_cover(p, r, pix)
                p.setOpacity(alpha)
                s = min(r.width() / pix.width(), (r.height() - 8) / pix.height())
                w, h = pix.width() * s, pix.height() * s
                p.drawPixmap(QRectF(r.width() - w - 12, (r.height() - h) / 2, w, h), pix, QRectF(pix.rect()))
            else:
                p.setOpacity(alpha)
                draw_cover(p, r, pix)
        p.setOpacity(1.0)
        shade = QLinearGradient(0, r.height() * 0.35, 0, r.height())
        shade.setColorAt(0, QColor(0, 0, 0, 0))
        shade.setColorAt(1, QColor(5, 5, 10, 215))
        p.fillRect(r, shade)
        if self._caption:
            f = QFont(theme.FONT_FAMILY)
            f.setPixelSize(14)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor("#ffffff"))
            base = r.height() - (24 if self._sub else 10)
            p.drawText(QRectF(12, 0, r.width() - 24, base), Qt.AlignLeft | Qt.AlignBottom, self._caption)
            if self._sub:
                f.setPixelSize(11)
                f.setBold(False)
                p.setFont(f)
                p.setPen(QColor(255, 255, 255, 190))
                p.drawText(QRectF(12, 0, r.width() - 24, r.height() - 8), Qt.AlignLeft | Qt.AlignBottom, self._sub)
        p.setClipping(False)
        p.setPen(QColor(theme.BORDER))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        p.end()
