# ui_qt/welcome.py
"""
The first-run tutorial. Five short pages:

  1 Welcome     what Lucki's Macro does and how the screen is laid out
  2 Roblox      connecting the game (live status)
  3 Mode        choosing what to farm
  4 Units       how unit placement works: Auto Play vs your own recording
  5 Running     starting, watching, stopping, and what every setting is for

Shown once on first launch; Help in the sidebar opens it again any time.
"""
from PySide6.QtCore import Qt, QRectF, QTimer, QVariantAnimation
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QFontMetrics, QPainterPath, QLinearGradient
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QAbstractButton, QButtonGroup, QLabel,
)

from input_controller import roblox_is_running
from . import theme
from .art import art_pixmap, draw_cover
from .dialogs import FloatingDialog
from .widgets import Button, FadeStack, PulseDot, Card, VScroll, ToggleRow, label, icon_pixmap, mix, with_alpha
import settings

MODES = [
    ("portals", "Portals", "Plays your best portal again and again and picks the rewards for you.",
     "No recording needed", theme.SUCCESS_TEXT, ("portals", "sovereign")),
    ("story", "Story", "Repeats one stage, or climbs to the next act every time you win.",
     "No recording needed", theme.SUCCESS_TEXT, ("story_maps", "rose_kingdom")),
    ("raids", "Raids", "Farms one raid at the difficulty you pick.",
     "No recording needed", theme.SUCCESS_TEXT, ("raids", "spirit_city")),
    ("challenges", "Challenges", "Checks Regular, Daily and Weekly challenges and plays each one as it unlocks.",
     "Reuses your Story setups", "#fdba74", ("story_maps", "kings_tomb")),
    ("expeditions", "Expeditions", "Picks the map route with the most of the material you want, then extracts.",
     "Comes with a starter recording", theme.WARNING, ("expeditions", "school_grounds")),
]


def info_row(icon, title, text, color=None):
    """An icon badge with a bold title and a sentence or two under it."""
    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(12)
    badge = QLabel()
    badge.setFixedSize(34, 34)
    badge.setAlignment(Qt.AlignCenter)
    badge.setStyleSheet(f"background: {theme.BG_ELEVATED}; border-radius: 9px;")
    badge.setPixmap(icon_pixmap(icon, color or theme.accent(), 17))
    row.addWidget(badge, 0, Qt.AlignTop)
    col = QVBoxLayout()
    col.setSpacing(2)
    col.addWidget(label(title, "title"))
    col.addWidget(label(text, "muted", wrap=True))
    row.addLayout(col, 1)
    return w


def key_chip(key):
    k = QLabel(key)
    k.setAlignment(Qt.AlignCenter)
    k.setFixedSize(46, 28)
    k.setStyleSheet(f"background: {theme.BG_ELEVATED}; border: 1px solid {theme.BORDER_STRONG}; "
                    f"border-radius: 7px; font-weight: 700;")
    return k


class LayoutDiagram(QWidget):
    """A small drawing of the window with its three areas numbered."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(170)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = min(self.width(), 420)
        x0 = (self.width() - w) / 2
        h = self.height() - 4
        outer = QRectF(x0, 2, w, h)
        p.setPen(QPen(QColor(theme.BORDER_STRONG), 1.2))
        p.setBrush(QColor(theme.BG_APP))
        p.drawRoundedRect(outer, 10, 10)
        side = QRectF(x0 + 6, 8, w * 0.2, h - 12)
        game = QRectF(side.right() + 6, 8, outer.right() - side.right() - 12, (h - 12) * 0.74)
        strip = QRectF(game.left(), game.bottom() + 6, game.width(), outer.bottom() - game.bottom() - 12)
        f = QFont(theme.FONT_FAMILY)
        for rect, fill, num, text in (
            (side, theme.BG_CARD, "1", "Set up\n& Start"),
            (game, "#1d2a3a", "2", "Roblox plays here"),
            (strip, theme.BG_CARD, "3", "What it's doing · stats · activity"),
        ):
            p.setPen(QPen(QColor(theme.BORDER), 1))
            p.setBrush(QColor(fill))
            p.drawRoundedRect(rect, 7, 7)
            c = QRectF(rect.left() + 8, rect.top() + 8, 20, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.accent()))
            p.drawEllipse(c)
            f.setPixelSize(11)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(theme.on_accent()))
            p.drawText(c, Qt.AlignCenter, num)
            f.setBold(False)
            f.setPixelSize(11)
            p.setFont(f)
            p.setPen(QColor(theme.TEXT_SOFT))
            p.drawText(rect.adjusted(6, 30, -6, -4), Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, text)
        p.end()


class ModeCard(QAbstractButton):
    def __init__(self, key, title, desc, badge, badge_color, art, parent=None):
        super().__init__(parent)
        self.key, self.title, self.desc, self.badge, self.badge_color = key, title, desc, badge, badge_color
        self.art = art_pixmap(*art)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(128)
        self._sel = 0.0
        self._hover = 0.0
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

    def leaveEvent(self, e):
        self._hover = 0.0
        self.update()

    def paintEvent(self, event):
        if self._anim.state() != QVariantAnimation.Running:
            self._sel = 1.0 if self.isChecked() else 0.0
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(1, 1, self.width() - 2, self.height() - 2)
        clip = QPainterPath()
        clip.addRoundedRect(r, 12, 12)
        p.setClipPath(clip)
        p.fillRect(r, mix(theme.BG_RAISED, with_alpha(theme.accent(), 0.12), self._sel * 0.9))
        if not self.art.isNull():
            art_rect = QRectF(r.width() * 0.52, r.top(), r.width() * 0.48, r.height())
            p.setOpacity(0.45 + 0.35 * max(self._sel, self._hover))
            draw_cover(p, art_rect, self.art)
            p.setOpacity(1.0)
            fade = QLinearGradient(art_rect.left(), 0, art_rect.left() + art_rect.width() * 0.7, 0)
            base = mix(theme.BG_RAISED, with_alpha(theme.accent(), 0.12), self._sel * 0.9)
            fade.setColorAt(0, base)
            end = QColor(base)
            end.setAlpha(0)
            fade.setColorAt(1, end)
            p.fillRect(art_rect, fade)
        p.setClipping(False)
        p.setPen(QPen(mix(mix(theme.BORDER, theme.BORDER_STRONG, self._hover), theme.accent(), self._sel), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 12, 12)

        badge_r = QRectF(16, 16, 34, 34)
        p.setPen(Qt.NoPen)
        p.setBrush(mix(theme.BG_ELEVATED, theme.accent(), self._sel))
        p.drawRoundedRect(badge_r, 9, 9)
        p.drawPixmap(24, 24, icon_pixmap(self.key, theme.on_accent() if self._sel > 0.5 else theme.TEXT, 18))
        f = QFont(theme.FONT_FAMILY)
        f.setPixelSize(15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(62, 16, r.width() - 70, 34), Qt.AlignVCenter, self.title)
        f2 = QFont(theme.FONT_FAMILY)
        f2.setPixelSize(12)
        p.setFont(f2)
        p.setPen(QColor(theme.TEXT_MUTED))
        p.drawText(QRectF(16, 56, r.width() * 0.62, 44), Qt.TextWordWrap, self.desc)
        f3 = QFont(theme.FONT_FAMILY)
        f3.setPixelSize(11)
        f3.setBold(True)
        p.setFont(f3)
        bw = QFontMetrics(f3).horizontalAdvance(self.badge) + 18
        br = QRectF(16, r.height() - 28, bw, 20)
        p.setPen(Qt.NoPen)
        p.setBrush(with_alpha(self.badge_color, 0.16))
        p.drawRoundedRect(br, 10, 10)
        p.setPen(QColor(self.badge_color))
        p.drawText(br, Qt.AlignCenter, self.badge)
        p.end()


class StepDots(QWidget):
    NAMES = ["Welcome", "Roblox", "What to farm", "Units", "Running"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current = 0
        self.setFixedHeight(34)

    def set_current(self, i):
        self.current = i
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        n = len(self.NAMES)
        w = self.width() / n
        f = QFont(theme.FONT_FAMILY)
        f.setPixelSize(12)
        for i, name in enumerate(self.NAMES):
            x = i * w
            done, cur = i < self.current, i == self.current
            if i > 0:
                p.setPen(QPen(QColor(theme.SUCCESS if done or cur else theme.BORDER), 2))
                p.drawLine(int(x - 10), 17, int(x + 4), 17)
            circle = QRectF(x + 8, 6, 22, 22)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.SUCCESS if done else theme.accent() if cur else theme.BORDER))
            p.drawEllipse(circle)
            if done:
                p.drawPixmap(int(circle.x() + 4), int(circle.y() + 4), icon_pixmap("check", theme.SUCCESS_INK, 14, 3))
            else:
                f.setBold(True)
                p.setFont(f)
                p.setPen(QColor(theme.on_accent() if cur else theme.TEXT_MUTED))
                p.drawText(circle, Qt.AlignCenter, str(i + 1))
            f.setBold(cur)
            p.setFont(f)
            p.setPen(QColor(theme.TEXT if cur else theme.TEXT_MUTED if done else theme.TEXT_DIM))
            p.drawText(QRectF(x + 36, 0, w - 44, 34), Qt.AlignVCenter, name)
        p.end()


class WelcomeDialog(FloatingDialog):
    def __init__(self, host, current_mode="portals"):
        super().__init__(host, "", width=820, closable=True)
        self.host = host
        self.chosen_mode = current_mode if current_mode in {m[0] for m in MODES} else "portals"

        head = QVBoxLayout()
        head.setSpacing(4)
        self.head_title = label("Welcome to Lucki's Macro", "h1")
        head.addWidget(self.head_title)
        self.head_sub = label("Five quick pages - then it farms on its own.", "muted")
        head.addWidget(self.head_sub)
        self.body.addLayout(head)
        self.dots = StepDots()
        self.body.addWidget(self.dots)

        self.stack = FadeStack()
        self.pages = [self._page_intro(), self._page_connect(), self._page_modes(),
                      self._page_units(), self._page_running()]
        for pg in self.pages:
            self.stack.addWidget(pg)
        self.body.addWidget(self.stack, 1)

        foot = QHBoxLayout()
        skip = Button("Skip - I know my way around", "link", height=34, font_px=12, bold=False)
        skip.clicked.connect(self.reject)
        foot.addWidget(skip)
        foot.addStretch(1)
        self.back = Button("Back", "ghost", height=38)
        self.back.setMinimumWidth(90)
        self.back.clicked.connect(lambda: self._go(self.index - 1))
        foot.addWidget(self.back)
        self.next = Button("Next", "accent", icon="chevron_right", height=38)
        self.next.setMinimumWidth(140)
        self.next.clicked.connect(self._next)
        foot.addWidget(self.next)
        self.body.addLayout(foot)
        self.card.setMinimumHeight(690)
        self.index = 0
        self._go(0)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_roblox)
        self._timer.start(1000)
        self._poll_roblox()

    # ------------------------------------------------------------------ pages

    def _scroll_page(self, build):
        scroll = VScroll()
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 8, 8, 4)
        col.setSpacing(14)
        build(col)
        col.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    def _page_intro(self):
        def build(col):
            col.addWidget(label("Lucki's Macro plays Roblox for you: it goes through the menus, starts matches, places "
                                "your units, handles wins and losses, and starts again - for as long as you want.",
                                "muted", wrap=True))
            col.addWidget(LayoutDiagram())
            col.addWidget(info_row("list", "1 · The sidebar - set up and Start",
                                   "Pick what to farm, fill in the numbered cards (they tick green when done), check "
                                   "READY CHECK, then press Start."))
            col.addWidget(info_row("monitor", "2 · The game",
                                   "Roblox snaps into this space by itself. Leave it alone while the bot runs - moving "
                                   "the mouse over it can knock a click off target."))
            col.addWidget(info_row("chart", "3 · The strip under the game",
                                   "RIGHT NOW says what the bot is doing in plain words. The cards count time, matches, "
                                   "wins and losses. ACTIVITY is the story of the session; Full log has every detail."))
        return self._scroll_page(build)

    def _page_connect(self):
        def build(col):
            col.addWidget(label("Open Roblox and join the game. Lucki's Macro finds it and docks it next to the "
                                "controls - no window dragging.", "muted", wrap=True))
            card = Card("card", margins=(18, 16, 18, 16))
            row = QHBoxLayout()
            self.conn_dot = PulseDot(theme.WARNING, True, size=22, dot=10)
            row.addWidget(self.conn_dot)
            texts = QVBoxLayout()
            texts.setSpacing(2)
            self.conn_title = label("Looking for Roblox…", "title")
            self.conn_detail = label("", "hint", wrap=True)
            texts.addWidget(self.conn_title)
            texts.addWidget(self.conn_detail)
            row.addLayout(texts, 1)
            card.body.addLayout(row)
            col.addWidget(card)

            must = Card("card", margins=(18, 16, 18, 16), spacing=10)
            must.setStyleSheet(f"QFrame#card {{ border: 1px solid {theme.rgba(theme.WARNING, 0.55)}; }}")
            head = QHBoxLayout()
            head.setSpacing(12)
            warn = QLabel()
            warn.setFixedSize(34, 34)
            warn.setAlignment(Qt.AlignCenter)
            warn.setStyleSheet(f"background: {theme.rgba(theme.WARNING, 0.16)}; border-radius: 9px;")
            warn.setPixmap(icon_pixmap("alert", theme.WARNING, 18))
            head.addWidget(warn, 0, Qt.AlignTop)
            texts = QVBoxLayout()
            texts.setSpacing(2)
            texts.addWidget(label("Required: turn on Auto Start and Auto Retry in the game", "title"))
            texts.addWidget(label("In Roblox, open the game's settings and switch both ON. The bot relies on them - "
                                  "without Auto Start matches don't begin by themselves, and without Auto Retry "
                                  "they don't repeat.", "muted", wrap=True))
            head.addLayout(texts, 1)
            must.body.addLayout(head)
            done = ToggleRow("I've turned both on", None, bool(self.host.user_settings.get("game_settings_confirmed")))

            def on_done(v):
                self.host.user_settings["game_settings_confirmed"] = bool(v)
                settings.save(self.host.user_settings)
                self.host.refresh_ready()

            done.toggled.connect(on_done)
            must.body.addWidget(done)
            col.addWidget(must)

            col.addWidget(info_row("monitor", "Any screen works",
                                   "The game is sized to fit whichever monitor Lucki's Macro is on, at any Windows "
                                   "scaling. Choose the monitor in Settings › Window."))
            col.addWidget(info_row("release", "Want to play yourself?",
                                   "Release (bottom of the sidebar) gives Roblox back as a normal window. Dock Roblox, "
                                   "or just pressing Start, puts it back."))
            col.addWidget(info_row("alert", "Keep Roblox's own settings normal",
                                   "Don't change the game's UI scale or window mode while the bot runs. If Windows' text "
                                   "size is above 100%, it adapts by itself the first time and remembers it."))
        return self._scroll_page(build)

    def _page_modes(self):
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 8, 0, 0)
        col.setSpacing(10)
        col.addWidget(label("What do you want to farm?", "title"))
        grid = QGridLayout()
        grid.setSpacing(10)
        self.mode_group = QButtonGroup(self)
        for i, m in enumerate(MODES):
            card = ModeCard(*m)
            card.setChecked(m[0] == self.chosen_mode)
            card.clicked.connect(lambda _=False, k=m[0]: setattr(self, "chosen_mode", k))
            self.mode_group.addButton(card)
            grid.addWidget(card, i // 2, i % 2)
        col.addLayout(grid)
        col.addWidget(label("Not sure?  Start with Portals - it needs the least setup. You can switch modes any "
                            "time from the sidebar.", "hint", wrap=True))
        col.addStretch(1)
        return w

    def _page_units(self):
        def build(col):
            self.units_mode_note = label("", "muted", wrap=True)
            col.addWidget(self.units_mode_note)
            col.addWidget(label("TWO WAYS TO PLACE UNITS", "section"))
            col.addWidget(info_row("check", "The game's Auto Play",
                                   "The game places units itself. Nothing to set up - works for Story, Raids, "
                                   "Challenges and Portals. Pick “Game's Auto Play” under How units get placed.",
                                   theme.SUCCESS_TEXT))
            col.addWidget(info_row("record", "Your own recording (a macro)",
                                   "Lucki's Macro watches you place units once - which number key you press and where you "
                                   "click - and repeats exactly that, at the same timing, every match. Usually stronger "
                                   "than Auto Play. Expeditions need one."))
            col.addWidget(label("MAKING A RECORDING", "section"))
            steps = Card("cardSunken", margins=(16, 14, 16, 14), spacing=10)
            for n, (title, text) in enumerate((
                ("Click New", "Name it - e.g. “six units”. A recording belongs to the stage selected above it."),
                ("Start the match in Roblox", "Get to the moment you'd normally start placing units."),
                ("Press F8 (or Record)", "A red bar appears over the game with a timer."),
                ("Walk there first, if you need to", "Some stages need the character moved before units go down. "
                                                     "Just hold W/A/S/D (Space to jump) before you start placing - "
                                                     "it's saved as part of this same recording, no separate step."),
                ("Place your units", "Press the unit's number key (1-6), click where it goes. Repeat. Waiting "
                                     "between placements and your mouse movement are recorded too, so playback "
                                     "moves the way you did."),
                ("Press F8 again", "Saved. Keys 1-6, left clicks, any walk, and the mouse path are all in the one "
                                   "file - see it below."),
                ("Check it with Edit", "See every step as a sentence and every spot on a picture of the map. Drag a "
                                       "spot to move it, delete a misclick, then Test in game to watch it play once."),
            ), 1):
                row = QHBoxLayout()
                row.setSpacing(12)
                num = QLabel(str(n))
                num.setAlignment(Qt.AlignCenter)
                num.setFixedSize(24, 24)
                num.setStyleSheet(f"background: {theme.accent()}; color: {theme.on_accent()}; border-radius: 12px; "
                                  f"font-weight: 700; font-size: 12px;")
                row.addWidget(num, 0, Qt.AlignTop)
                texts = QVBoxLayout()
                texts.setSpacing(1)
                texts.addWidget(label(title, "title"))
                texts.addWidget(label(text, "hint", wrap=True))
                row.addLayout(texts, 1)
                steps.body.addLayout(row)
            col.addWidget(steps)
            col.addWidget(label("GOOD TO KNOW", "section"))
            col.addWidget(info_row("target", "A walk only replays when it needs to",
                                   "The first time a stage is entered, a leading walk plays in full. Repeat Stage "
                                   "of that SAME stage skips it - the character is already there - so it can't "
                                   "walk further each time. A different stage (Auto Next, a raid's other "
                                   "difficulty, a different portal) walks again once, the same way."))
            col.addWidget(info_row("copy", "One map, many acts",
                                   "Acts of the same Story map share a layout: More › Copy to other acts reuses a "
                                   "recording everywhere on that map."))
            col.addWidget(info_row("upload", "Share with friends",
                                   "More › Export saves a recording to a file; Import loads one. It works on any "
                                   "screen size - spots are stored relative to the game, not your monitor."))
            col.addWidget(info_row("target", "Expeditions: camera matters",
                                   "The bot zooms the camera out before placing units. If you change the zoom slider, "
                                   "re-record - the spots were saved for the old zoom."))
            col.addWidget(info_row("trophy", "Starter recordings",
                                   "Some modes come with a recording already made. Use it as-is, edit it, or make your "
                                   "own. More › Restore default brings the original back."))
        return self._scroll_page(build)

    def _page_running(self):
        def build(col):
            col.addWidget(label("STARTING", "section"))
            keys = Card("cardSunken", margins=(16, 12, 16, 12), spacing=10)
            for key, text in (("F7", "Start or stop the bot - works even while you're clicked into Roblox."),
                              ("F8", "Start or stop a recording.")):
                r = QHBoxLayout()
                r.addWidget(key_chip(key))
                r.addWidget(label(text, "muted", wrap=True), 1)
                keys.body.addLayout(r)
            col.addWidget(keys)
            col.addWidget(info_row("check", "READY CHECK before you press Start",
                                   "Green = good, amber = worth a look, red = it won't work yet (and says why). Start "
                                   "from the lobby - or from where a run left off; the bot works out where it is.",
                                   theme.SUCCESS_TEXT))
            col.addWidget(info_row("chart", "While it runs",
                                   "Hands off the mouse over the game. RIGHT NOW and ACTIVITY tell you what's happening; "
                                   "a pop-up appears when a run finishes or stops by itself, with a sound."))
            col.addWidget(label("SETTINGS - WHAT EACH ONE DOES", "section"))
            for icon, title, text in (
                ("list", "How many runs, and the Queue",
                 "Every mode has a How many runs card: Forever, 50, 999 or any number. “Add to queue” on that card "
                 "saves the setup as a step - e.g. 55 portals, then all challenges, then 30 portals - and the Queue "
                 "page plays the steps in order, heading back to the lobby between them."),
                ("shield", "Runs › Stop after losing in a row",
                 "If your setup keeps losing, stop instead of wasting keys or portals. Pick how many losses."),
                ("refresh", "Runs › Never stop",
                 "For overnight farming. If something goes wrong (stuck screen, Roblox crashed), wait and start again - "
                 "30 s, then longer each time, up to 5 min. Your Stop button and the loss limit still win."),
                ("clock", "Runs › Stop after a set time",
                 "Handy before bed. (A number of runs is set per mode, see above.)"),
                ("monitor", "Runs › This PC",
                 "Older PCs draw Roblox with fewer frames and can miss quick clicks. Auto measures your PC; Slow PC mode "
                 "forces longer waits if clicks still get missed."),
                ("bell", "Discord",
                 "Paste a channel webhook to get messages (with a screenshot) when a run starts, ends or has a problem. "
                 "Treat the link like a password."),
                ("palette", "Look", "Accent colour, panel opacity, and Reduce motion if you prefer no animations."),
                ("monitor", "Window", "Which monitor to use, and keeping the panel on top when Roblox isn't docked."),
                ("log", "Advanced",
                 "Open the logs and debug screenshots, and make a troubleshooting report to send if something goes wrong."),
            ):
                col.addWidget(info_row(icon, title, text))
        return self._scroll_page(build)

    # ------------------------------------------------------------------ flow

    def _poll_roblox(self):
        state = self.host.dock_state
        if state.docked:
            w, h = state.game_size
            self.conn_dot.set_state(theme.SUCCESS, False)
            self.conn_title.setText("Found and docked")
            self.conn_detail.setText(f"Roblox is running at {w} x {h} inside the panel.")
        elif roblox_is_running():
            self.conn_dot.set_state(theme.accent(), True)
            self.conn_title.setText("Found Roblox - docking it…")
            self.conn_detail.setText("It'll snap into place in a moment.")
        else:
            self.conn_dot.set_state(theme.WARNING, True)
            self.conn_title.setText("Waiting for Roblox")
            self.conn_detail.setText("Open Roblox and join the game. You can carry on and do this later too.")

    def _go(self, i):
        self.index = max(0, min(len(self.pages) - 1, i))
        if self.index == 3:
            self._fill_units()
        self.stack.set_current(self.pages[self.index])
        self.dots.set_current(self.index)
        self.back.setVisible(self.index > 0)
        last = self.index == len(self.pages) - 1
        self.next.setText("Let's farm" if last else "Next")
        self.next.icon_name = "check" if last else "chevron_right"
        self.next.update()

    def _next(self):
        if self.index == len(self.pages) - 1:
            self.accept()
        else:
            self._go(self.index + 1)

    def _fill_units(self):
        notes = {
            "portals": "For Portals you're already set - it always uses the game's Auto Play. On Summer and "
                       "Sovereign it fishes automatically: casts (and keeps re-casting) at a fixed spot after "
                       "Start Game, no toggle needed - equip the Auto Rod yourself first, or let the bot equip "
                       "it once. Extras lets you change that cast point if yours sits somewhere else. Read on "
                       "if you want to know how recordings work for other modes.",
            "story": "For Story you can start with the game's Auto Play right away, or make a recording for a stronger "
                     "placement.",
            "raids": "For Raids you can start with the game's Auto Play right away, or make a recording per difficulty.",
            "challenges": "Challenges use Auto Play unless you link your Story recordings to them under “Units for each "
                          "challenge”.",
            "expeditions": "Expeditions have no Auto Play, so they use a recording. One comes ready to use - check it "
                           "with Edit, or record your own.",
        }
        self.units_mode_note.setText(notes.get(self.chosen_mode, ""))
