# ui_qt/main_window.py
"""
The Qt main window: sidebar (what to farm, setup, ready check, Start), the docked
game, and the strip under it (right now, stats, activity).

It is the `ui` that engine.BotEngine talks to - the same seven methods gui.py
implements - so the bot runs identically under either window. Everything the
engine calls arrives on the bot thread and is handed to the UI thread through Qt
signals; focus_and_pin() additionally waits for the UI thread to finish docking,
because the run cannot continue until the game is where the engine expects it.
"""
import atexit
import os
import threading
import time

import win32con
import win32gui

from PySide6.QtCore import Qt, QObject, Signal, QTimer, QPoint
from PySide6.QtGui import QColor, QGuiApplication, QPixmap, QIcon
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QFrame, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QListWidget, QListWidgetItem, QPlainTextEdit, QApplication, QLabel,
)

import config
import settings
import logger
from engine import BotEngine
from vision import clear_template_cache as vision_cache_clear
from modules.stage_recorder import StageRecorder
from modules.stats import SESSION, format_duration
from modules import health, notify, preset_core, lobby
from input_controller import (
    focus_roblox_window, roblox_is_running, recover_orphaned_pin,
)

from . import theme, dialogs, docking, phases, brand
from .pages import PAGE_CLASSES
from .widgets import (
    NavList, Button, IconButton, StatusPill, Card, CheckRow, StatCard, ActivityBar, SwapColumn,
    Toast, PulseDot, VScroll, label, icon_pixmap, add_shadow,
)

NAV = [
    ("story", "Story", "story"), ("raids", "Raids", "raids"), ("challenges", "Challenges", "challenges"),
    ("portals", "Portals", "portals"), ("expeditions", "Expeditions", "expeditions"), ("queue", "Queue", "list"),
    ("others", "Others", "others"),
]
SIDEBAR_WIDTH = 320
STRIP_HEIGHT = 180
ACTIVITY_MAX = 150
LOG_MAX = 600


class Bridge(QObject):
    log = Signal(str, bool)
    phase = Signal(str, str)
    reset = Signal()
    call = Signal(object)
    f7 = Signal()
    autoclicker = Signal()


# ----------------------------------------------------------------------------- game area

class GameSlot(QWidget):
    """
    The space the game docks into. A native child window so its on-screen rectangle
    can be read from Windows in physical pixels. Its own content (a placeholder) is
    only ever visible while the game isn't there.
    """
    resized = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_NativeWindow)
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet(f"background: {theme.BG_APP};")
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)
        self.card = Card("dashed", margins=(40, 34, 40, 34), spacing=10)
        self.card.setFixedWidth(460)
        top = QHBoxLayout()
        top.setAlignment(Qt.AlignCenter)
        self.dot = PulseDot(theme.WARNING, True, size=26, dot=12)
        top.addWidget(self.dot)
        self.card.body.addLayout(top)
        self.title = label("Waiting for Roblox", "big")
        self.title.setAlignment(Qt.AlignCenter)
        self.card.body.addWidget(self.title)
        self.text = label("", "muted", wrap=True)
        self.text.setAlignment(Qt.AlignCenter)
        self.card.body.addWidget(self.text)
        self.action = Button("Dock Roblox", "accent", icon="monitor", height=38)
        self.action.setFixedWidth(200)
        row = QHBoxLayout()
        row.setAlignment(Qt.AlignCenter)
        row.addWidget(self.action)
        self.card.body.addLayout(row)
        outer.addWidget(self.card, 0, Qt.AlignCenter)

    def show_state(self, title, text, color, pulsing, action_text=None):
        self.title.setText(title)
        self.text.setText(text)
        self.dot.set_state(color, pulsing)
        self.action.setVisible(bool(action_text))
        if action_text:
            self.action.setText(action_text)
        self.card.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


class RecordingBar(QWidget):
    """Floats over the top of the game while recording: timer, steps so far, Stop."""

    def __init__(self, on_stop):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 16)
        card = QFrame()
        card.setObjectName("recCard")
        card.setStyleSheet(f"#recCard {{ background: rgba(21,21,31,245); border: 1px solid {theme.rgba(theme.DANGER, 0.55)}; border-radius: 22px; }}")
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 7, 8, 7)
        row.setSpacing(12)
        row.addWidget(PulseDot(theme.DANGER, True, size=18, dot=9))
        t = label("Recording")
        t.setStyleSheet(f"font-weight: 700; color: {theme.DANGER_TEXT};")
        row.addWidget(t)
        row.addWidget(label("Place your units in Roblox now - every unit you pick and spot you click is saved.", "muted"))
        self.clock = label("00:00")
        self.clock.setStyleSheet("font-weight: 700; font-family: Consolas;")
        row.addWidget(self.clock)
        self.count = label("0 steps", "hint")
        row.addWidget(self.count)
        stop = Button("Stop", "danger", icon="stop", key_hint="F8", height=32, font_px=12, glow=False)
        stop.clicked.connect(on_stop)
        row.addWidget(stop)
        outer.addWidget(card)
        add_shadow(card, blur=30, alpha=0.55, dy=8)

    def update_status(self, seconds, steps):
        self.clock.setText(f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}")
        self.count.setText(f"{steps} step{'s' if steps != 1 else ''}")


# ----------------------------------------------------------------------------- main window

class MainWindow(QMainWindow):
    def __init__(self, dpi_result="", log_path=None, preview=False):
        super().__init__()
        self.preview = preview
        self.dpi_result = dpi_result
        self.setWindowTitle("Lucki's Macro")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.user_settings = settings.load()
        # Starter recordings shipped in assets/default_presets - each copied in once, so a
        # deleted default stays deleted and an edited one is never overwritten.
        if not preview:
            try:
                done = list(self.user_settings.get("defaults_installed") or [])
                new = preset_core.install_default_presets(done)
                if new:
                    self.user_settings["defaults_installed"] = done + new
                    settings.save(self.user_settings)
            except Exception as e:
                print(f"[defaults] Couldn't install default recordings: {e}")
        theme.load_from_settings(self.user_settings)
        QApplication.instance().setStyleSheet(theme.stylesheet())

        self.bridge = Bridge()
        self.bridge.log.connect(self._on_log)
        self.bridge.phase.connect(self._on_phase)
        self.bridge.reset.connect(self._on_run_ended)
        self.bridge.call.connect(lambda fn: fn())
        self.bridge.f7.connect(self.toggle_run)
        self.bridge.autoclicker.connect(self.toggle_autoclicker)

        # Autoclicker's position/interval persist across launches; "enabled" never
        # restores true on its own - see settings.py's own comment on autoclicker_pos.
        pos = self.user_settings.get("autoclicker_pos")
        config.AUTOCLICKER_POS = tuple(pos) if pos else None
        config.AUTOCLICKER_INTERVAL = self.user_settings.get("autoclicker_interval")
        config.AUTOCLICKER_ENABLED = False

        self.engine = BotEngine(self)
        self.recorder = StageRecorder(
            f7_callback=self.bridge.f7.emit,
            autoclicker_callback=self.bridge.autoclicker.emit,
            start_stop_key=self.user_settings.get("keybind_start_stop", "f7"),
            record_key=self.user_settings.get("keybind_record", "f8"),
            autoclicker_key=self.user_settings.get("keybind_autoclicker", "f9"),
        )
        self.dock_state = docking.DockState()
        self.compact = False
        self._ui_scale_checked = False
        self._was_recording = False
        self._reopen_editor = None
        self._test_thread = None
        self._toasts = []
        self._phase_code = "IDLE"
        self._last_run_outcome = None
        self._update_info = None
        self._time_limit_hit = False

        self._build()
        self.setWindowOpacity(float(self.user_settings.get("window_opacity", 1.0)))

        if not preview:
            self.recorder.start()
            logger.set_gui_sink(lambda line: self.bridge.log.emit(line, False))
            if log_path:
                self.log(f"Logging to {log_path}")
            self._log_display_environment()
            if recover_orphaned_pin():
                self.log("Roblox was left borderless by a previous session - its title bar is back.")
            atexit.register(self._undock_quietly)
            threading.Thread(target=self._measure_speed, daemon=True).start()
        self.apply_notification_settings(self.user_settings.get("discord_webhook", ""))

        self._save_timer = QTimer(self, singleShot=True, interval=800)
        self._save_timer.timeout.connect(self._save_ui_state)
        self._restore_ui_state()

        self._tick = QTimer(self)
        self._tick.timeout.connect(self._every_second)
        self._tick.start(1000)
        self._rec_timer = QTimer(self)
        self._rec_timer.timeout.connect(self._poll_recorder)
        self._rec_timer.start(250)
        if not preview:
            self._dock_timer = QTimer(self)
            self._dock_timer.timeout.connect(self._auto_dock)
            self._dock_timer.start(2000)
            self._redock_timer = QTimer(self, singleShot=True, interval=200)
            self._redock_timer.timeout.connect(self._redock)
            self.slot.resized.connect(lambda: self.dock_state.docked and self._redock_timer.start())
        self.refresh_ready()
        self.refresh_stats()

    # ------------------------------------------------------------------ build

    def _build(self):
        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_sidebar())
        self.game_area = QWidget()
        v = QVBoxLayout(self.game_area)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.slot = GameSlot()
        self.slot.action.clicked.connect(self.dock_again)
        v.addWidget(self.slot, 1)
        v.addWidget(self._build_strip())
        h.addWidget(self.game_area, 1)
        self._update_slot_placeholder()

    def _build_sidebar(self):
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(SIDEBAR_WIDTH)
        col = QVBoxLayout(side)
        col.setContentsMargins(16, 16, 16, 12)
        col.setSpacing(10)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo = QLabel()
        logo.setFixedSize(30, 30)
        logo.setAlignment(Qt.AlignCenter)
        logo.setPixmap(icon_pixmap("logo", theme.on_accent(), 14))
        self.logo = logo
        brand.addWidget(logo)
        brand.addWidget(label("Lucki's Macro", "brand"), 1)
        self.pill = StatusPill()
        brand.addWidget(self.pill)
        col.addLayout(brand)
        self._style_logo()

        col.addSpacing(6)
        col.addWidget(label("WHAT TO FARM", "section"))
        self.nav = NavList([(k, t, i, config.GAMEMODES.get(k, {}).get("enabled", True)) for k, t, i in NAV])
        self.nav.changed.connect(self._on_nav)
        col.addWidget(self.nav)

        col.addSpacing(6)
        setup_head = QHBoxLayout()
        setup_head.addWidget(label("SETUP", "section"), 1)
        self.setup_hint = label("", "hint")
        setup_head.addWidget(self.setup_hint)
        col.addLayout(setup_head)

        scroll = VScroll()
        inner = QWidget()
        inner_col = QVBoxLayout(inner)
        inner_col.setContentsMargins(0, 0, 4, 0)
        inner_col.setSpacing(14)
        self.page_stack = SwapColumn()
        self.pages = {}
        for key, cls in PAGE_CLASSES.items():
            page = cls(self)
            page.changed.connect(self._on_page_changed)
            self.page_stack.addWidget(page)
            self.pages[key] = page
        inner_col.addWidget(self.page_stack)

        ready = QVBoxLayout()
        ready.setSpacing(8)
        ready.addWidget(label("READY CHECK", "section"))
        self.ready_card = Card("cardSunken", margins=(12, 10, 12, 10), spacing=7)
        self.ready_rows = {}
        ready.addWidget(self.ready_card)
        inner_col.addLayout(ready)
        inner_col.addStretch(1)
        scroll.setWidget(inner)
        col.addWidget(scroll, 1)

        self.start_btn = Button("Start", "success", icon="play", key_hint="F7", height=50, font_px=15)
        self.start_btn.setToolTip("F7 starts and stops it from anywhere - even while you're clicked into Roblox")
        self.start_btn.clicked.connect(self.toggle_run)
        col.addWidget(self.start_btn)

        foot = QHBoxLayout()
        foot.setSpacing(2)
        for icon, text, fn, tip in (
            ("settings", "Settings", lambda: self.open_settings(), "Run behavior, Discord alerts, look, monitor"),
            ("help", "Help", lambda: self.open_welcome(), "Show the quick guide again"),
            ("release", "Release", lambda: self.release_roblox(), "Give Roblox back its normal window without quitting"),
        ):
            b = IconButton(icon, text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            foot.addWidget(b, 1)
        quit_btn = IconButton("quit", "", danger=True)
        quit_btn.setFixedWidth(34)
        quit_btn.setToolTip("Quit Lucki's Macro (gives Roblox its window back)")
        quit_btn.clicked.connect(self.close)
        foot.addWidget(quit_btn)
        col.addLayout(foot)
        return side

    def _style_logo(self):
        pix = QPixmap(brand.LOGO_PATH)
        if pix.isNull():
            self.logo.setStyleSheet(f"background: {theme.accent()}; border-radius: 9px;")
            self.logo.setPixmap(icon_pixmap("logo", theme.on_accent(), 14))
            return
        from PySide6.QtGui import QPainter, QPainterPath
        size = 60
        out = QPixmap(size, size)
        out.fill(Qt.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(2, 2, size - 4, size - 4, 16, 16)
        p.setClipPath(path)
        p.drawPixmap(0, 0, pix.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        p.end()
        out.setDevicePixelRatio(2.0)
        self.logo.setStyleSheet(f"border: 1.5px solid {theme.accent()}; border-radius: 9px;")
        self.logo.setPixmap(out)

    def _build_strip(self):
        strip = QFrame()
        strip.setObjectName("strip")
        strip.setFixedHeight(STRIP_HEIGHT)
        row = QHBoxLayout(strip)
        row.setContentsMargins(14, 14, 14, 14)
        row.setSpacing(12)

        now = Card("card", margins=(16, 13, 16, 14), spacing=4)
        now.setFixedWidth(380)
        head = QHBoxLayout()
        head.addWidget(label("RIGHT NOW", "section"), 1)
        self.now_dot = PulseDot(theme.TEXT_DIM, False, size=14, dot=7)
        head.addWidget(self.now_dot)
        now.body.addLayout(head)
        self.now_title = label("Ready when you are", "big", wrap=True)
        now.body.addWidget(self.now_title)
        self.now_detail = label("", "muted", wrap=True)
        now.body.addWidget(self.now_detail)
        now.body.addStretch(1)
        self.now_bar = ActivityBar()
        now.body.addWidget(self.now_bar)
        row.addWidget(now)

        self.stats = {}
        for key, title in (("time", "Running for"), ("matches", "Matches"), ("wl", "Won / lost"),
                           ("rate", "Win rate"), ("restarts", "Auto-restarts")):
            card = StatCard(title)
            card.setMinimumWidth(128)
            card.setMaximumWidth(170)
            row.addWidget(card)
            self.stats[key] = card

        act = Card("card", margins=(14, 12, 10, 10), spacing=6)
        act.setMinimumWidth(260)
        ahead = QHBoxLayout()
        ahead.addWidget(label("ACTIVITY", "section"), 1)
        self.log_toggle = IconButton("log", "Full log")
        self.log_toggle.setFixedHeight(24)
        self.log_toggle.clicked.connect(self._toggle_log)
        ahead.addWidget(self.log_toggle)
        act.body.addLayout(ahead)
        self.feed_stack = QStackedWidget()
        self.activity = QListWidget()
        self.activity.setStyleSheet("QListWidget::item { padding: 2px 0; }")
        self.activity.setWordWrap(False)
        self.activity_empty = label("Nothing yet. Each step the bot takes is written here in plain words.", "hint", wrap=True)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(LOG_MAX)
        self.feed_stack.addWidget(self.activity_empty)
        self.feed_stack.addWidget(self.activity)
        self.feed_stack.addWidget(self.log_view)
        act.body.addWidget(self.feed_stack, 1)
        row.addWidget(act, 1)
        self._strip = strip
        return strip

    # ------------------------------------------------------------------ window placement

    def launch(self):
        """Opens fullscreen on the saved monitor (or wherever Windows puts it)."""
        self.winId()
        self.move_to_screen(self.user_settings.get("dock_screen") or "", first=True)
        if not self.user_settings.get("welcome_done"):
            QTimer.singleShot(900, self.open_welcome)
        QTimer.singleShot(600, self._auto_dock)
        QTimer.singleShot(2000, lambda: self.check_for_updates(silent=True))

    def check_for_updates(self, silent=True):
        """
        Checks GitHub Releases for a newer build (modules.update_check). silent=True
        (launch) only toasts when something's actually found; silent=False (the
        Settings button) always tells the player something, including "up to date".

        Runs on a background thread the same way every other network call here does
        (modules.notify) - a slow/offline check must never freeze the window.
        """
        from modules import update_check

        def run():
            info = update_check.check_for_update()
            self.bridge.call.emit(lambda: self._on_update_checked(info, silent))

        threading.Thread(target=run, daemon=True).start()

    def _on_update_checked(self, info, silent):
        self._update_info = info
        if info:
            self.log(f"Update available: v{info['version']} (this is v{config.APP_VERSION}).")
            self.toast("Update available", f"v{info['version']} is out - this is v{config.APP_VERSION}. "
                      "See Settings > Advanced to open the release page.", "info", 10000)
        elif not silent:
            self.toast("Up to date", f"You're on v{config.APP_VERSION}, the latest.", "success")

    def _screen_by_name(self, name):
        for s in QGuiApplication.screens():
            if s.name() == name:
                return s
        return None

    def current_screen_name(self):
        s = self.screen()
        return s.name() if s else ""

    def move_to_screen(self, name, first=False):
        screen = self._screen_by_name(name) or (None if first else self.screen()) or QGuiApplication.primaryScreen()
        was_docked = self.dock_state.docked
        if self.compact:
            self._leave_compact()
        if was_docked:
            docking.fill_hole(self._hwnd())
        self.showNormal()
        handle = self.windowHandle()
        if handle is not None:
            handle.setScreen(screen)
        self.setGeometry(screen.geometry())
        self.showFullScreen()
        self.dock_state.last_target = None
        if not first:
            self.log(f"Moved to {screen.name()}.")
            if was_docked or roblox_is_running():
                QTimer.singleShot(350, self._redock)

    def _hwnd(self):
        return int(self.winId())

    def apply_topmost(self):
        topmost = (self.dock_state.docked and not self.compact) or bool(self.user_settings.get("always_on_top"))
        try:
            win32gui.SetWindowPos(self._hwnd(), win32con.HWND_TOPMOST if topmost else win32con.HWND_NOTOPMOST,
                                  0, 0, 0, 0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
        except Exception:
            pass

    def _enter_compact(self):
        """Screen too small to hold the game plus the panels: the panel becomes a slim window on the right."""
        if self.compact:
            return
        self.compact = True
        docking.fill_hole(self._hwnd())
        self.game_area.hide()
        screen = self.screen() or QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        self.showNormal()
        self.setGeometry(g.right() - SIDEBAR_WIDTH - 8, g.top() + 8, SIDEBAR_WIDTH, g.height() - 16)
        self.log("This screen is too small to show the game inside the panel, so the panel is a slim "
                 "window on the right and Roblox is its own window. Everything still works.")

    def _leave_compact(self):
        self.compact = False
        self.game_area.show()

    # ------------------------------------------------------------------ docking

    def _dock_now(self):
        """UI thread. Returns the client (w, h) or None."""
        if self.preview or not roblox_is_running():
            return None
        if self.isMinimized():
            self.showFullScreen()
            QApplication.processEvents()
        if not self.compact:
            sl, st, sr, sb = docking.window_rect(int(self.slot.winId()))
            _, fits = docking.fit_game_size(sr - sl, sb - st)
            if not fits:
                self._enter_compact()
                QApplication.processEvents()
        client = docking.dock(self._hwnd(), int(self.slot.winId()), self.dock_state, self.log, compact=self.compact)
        self.apply_topmost()
        self._update_slot_placeholder()
        self.refresh_ready()
        return client

    def _auto_dock(self):
        if self.preview or self.engine.running:
            return
        state = self.dock_state
        if state.docked and not roblox_is_running():
            state.docked = False
            state.last_target = None
            docking.fill_hole(self._hwnd())
            self.apply_topmost()
            self.log("Roblox closed.")
            self._update_slot_placeholder()
            self.refresh_ready()
            return
        if state.docked or state.released or not roblox_is_running():
            self._update_slot_placeholder()
            return
        client = self._dock_now()
        if client:
            self.log(f"Roblox docked at {client[0]}x{client[1]}.")
            self.toast("Roblox connected", f"Docked at {client[0]} x {client[1]}. Pick what to farm, then press Start.", "success")

    def _redock(self):
        if self.dock_state.docked or roblox_is_running():
            self.dock_state.last_target = None
            self._dock_now()

    def dock_again(self):
        self.dock_state.released = False
        if not roblox_is_running():
            self.toast("Roblox isn't open", "Open Roblox and join the game - it docks by itself.", "warning")
            return
        client = self._dock_now()
        if client:
            self.log(f"Roblox docked at {client[0]}x{client[1]}.")

    def release_roblox(self):
        if self.engine.running:
            self.toast("Stop the run first", "Roblox can't be released while the bot is using it.", "warning")
            return
        restored = docking.undock(self._hwnd(), self.dock_state)
        self.dock_state.released = True
        if self.compact:
            self._leave_compact()
            self.showFullScreen()
        self.apply_topmost()
        self._update_slot_placeholder()
        self.refresh_ready()
        self.log("Roblox released - it's a normal window again." if restored
                 else "Roblox wasn't docked, so there was nothing to release.")

    def _update_slot_placeholder(self):
        state = self.dock_state
        if state.docked:
            self.slot.card.hide()
            return
        if state.released:
            self.slot.show_state("Roblox is released", "It's a normal window again, so you can play yourself. "
                                 "Dock it again whenever you want the bot back - Start does it too.",
                                 theme.TEXT_DIM, False, "Dock Roblox")
        elif roblox_is_running():
            self.slot.show_state("Docking Roblox…", "Found the game - it will snap into this space.",
                                 theme.accent(), True, "Dock now")
        else:
            self.slot.show_state("Waiting for Roblox", "Open Roblox and join the game. It snaps into this space "
                                 "by itself - no dragging windows around.", theme.WARNING, True)

    def _undock_quietly(self):
        try:
            if self.dock_state.docked:
                docking.undock(self._hwnd(), self.dock_state)
        except Exception:
            pass

    # ------------------------------------------------------------------ BotEngine ui protocol

    def log(self, message, to_file=True):
        if to_file:
            logger.log_to_file(message)
        self.bridge.log.emit(str(message), True)

    def set_phase(self, text, color="#a8a8a8"):
        self.bridge.phase.emit(str(text), str(color))

    def reset_buttons(self):
        self.bridge.reset.emit()

    def get_user_settings(self):
        return self.user_settings

    def run_on_ui(self, fn, timeout=10.0):
        if threading.current_thread() is threading.main_thread():
            return fn()
        result = {}
        done = threading.Event()

        def wrapper():
            try:
                result["value"] = fn()
            finally:
                done.set()

        self.bridge.call.emit(wrapper)
        done.wait(timeout)
        return result.get("value")

    def focus_and_pin(self):
        focused, _ = focus_roblox_window(pin=False)
        if not focused:
            self.log("ERROR: Could not find or focus the Roblox window. Is Roblox running?")
            self.set_phase("NO ROBLOX WINDOW", "#ff1744")
            config.STOP_REQUESTED = True
            config.STUCK_DETECTED = "NO ROBLOX WINDOW"
            return False

        def dock():
            self.dock_state.released = False
            return self._dock_now()

        client = self.run_on_ui(dock)
        if client is None:
            self.log("ERROR: Couldn't place the Roblox window. Every position this bot clicks assumes a known "
                     "window size, so it would click the wrong places. The log file has the exact reason.")
            self.set_phase("WINDOW PIN FAILED", "#ff1744")
            config.STOP_REQUESTED = True
            config.STUCK_DETECTED = "WINDOW PIN FAILED"
            return False
        self._apply_saved_ui_scale(client)
        w, h = client
        if config.SCALE == 1.0:
            self.log(f"Roblox pinned to {w}x{h}.")
        else:
            self.log(f"Roblox pinned to {w}x{h} - every coordinate and template is scaled by {config.SCALE:.3f} to match.")
        return True

    def _apply_saved_ui_scale(self, client):
        saved = (self.user_settings.get("ui_scale_by_client") or {}).get(f"{client[0]}x{client[1]}")
        if isinstance(saved, (int, float)) and abs(float(saved) - config.UI_SCALE) > 0.001:
            config.set_ui_scale(float(saved))
            vision_cache_clear()
            if abs(float(saved) - 1.0) >= 0.02:
                self.log(f"Using this PC's remembered game UI size ({float(saved):.3f}x) - no need to measure it again.")

    def _measure_speed(self):
        try:
            factor, per_match = health.measure_machine_speed()
        except Exception as e:
            print(f"[speed] Couldn't measure this PC's speed: {e}")
            factor, per_match = 1.0, 0.0
        self._measured_slowness = factor
        print(f"[speed] One full-screen match takes {per_match * 1000:.0f} ms here - slowness factor {factor:.2f}.")
        self.apply_speed_mode(log=True)

    def apply_speed_mode(self, log=False):
        mode = self.user_settings.get("slow_pc_mode", "auto")
        measured = getattr(self, "_measured_slowness", 1.0)
        config.SLOWNESS = {"off": 1.0, "on": max(2.0, measured)}.get(mode, measured)
        if log and config.SLOWNESS > 1.05:
            self.log(f"This PC is on the slower side, so clicks wait {config.SLOWNESS:.1f}x longer for Roblox "
                     f"to notice them. Change it in Settings › Runs › This PC.")

    def calibrate_ui_scale(self, screenshot):
        """Same as gui.BotGUI.calibrate_ui_scale - see there for the full reasoning."""
        if self._ui_scale_checked:
            return False
        measured = health.measure_ui_scale(screenshot, [config.PLAY_BTN, config.ITEMS_BTN, config.STORY_CARD])
        if measured is None:
            return False
        self._ui_scale_checked = True
        # Remembered per client size, so this PC only pays for the measuring sweep once -
        # on an old laptop it took most of a minute at every single run start.
        saved = self.user_settings.setdefault("ui_scale_by_client", {})
        key = f"{screenshot.shape[1]}x{screenshot.shape[0]}"
        if abs(measured - 1.0) < 0.02:
            changed = config.UI_SCALE != 1.0
            if changed:
                config.set_ui_scale(1.0)
                vision_cache_clear()
            saved[key] = 1.0
            settings.save(self.user_settings)
            return changed
        config.set_ui_scale(measured)
        vision_cache_clear()
        saved[key] = round(measured, 4)
        settings.save(self.user_settings)
        self.log(f"This machine draws the game UI {measured:.3f}x larger than the templates expect - "
                 f"adapting every template and coordinate to match.")
        self.log("That usually means Windows' text size (Settings > Accessibility > Text size) is above 100% "
                 "here. Setting it to 100% and restarting Roblox removes the guesswork, but the bot works either way.")
        return True

    def report_unrecognised_screen(self, screenshot, label_text, looking_for):
        """Same as gui.BotGUI.report_unrecognised_screen."""
        path = health.save_debug_screenshot(label_text, screenshot)
        self.log(f"Couldn't find {looking_for} on screen. Screen is {screenshot.shape[1]}x{screenshot.shape[0]}, "
                 f"scale {config.SCALE:.4f}.")
        if path:
            self.log(f"Saved what it actually saw to: {path}")
        self.log("Checking whether these templates would match at a different size...")
        for tpl, best_scale, best_conf, cur_conf in health.probe_scales(
                screenshot, [config.PLAY_BTN, config.ITEMS_BTN, config.STORY_CARD]):
            verdict = ""
            if best_conf >= 0.85 and abs(best_scale - config.SCALE) > 0.015:
                verdict = f"  <-- would MATCH at scale {best_scale:.3f}, not {config.SCALE:.3f}"
            elif best_conf < 0.6:
                verdict = "  <-- no size matches; template is stale or it isn't on screen"
            self.log(f"   {os.path.basename(tpl):<18} now {cur_conf:.3f} | best {best_conf:.3f} "
                     f"@ scale {best_scale:.3f}{verdict}")

    def _log_display_environment(self):
        for i, s in enumerate(QGuiApplication.screens()):
            g, dpr = s.geometry(), s.devicePixelRatio()
            self.log(f"Screen {i + 1} ({s.name()}): {round(g.width() * dpr)}x{round(g.height() * dpr)} "
                     f"at {round(dpr * 100)}% scaling{' (main)' if s == QGuiApplication.primaryScreen() else ''}")
        self.log(f"DPI awareness: {self.dpi_result}")
        if isinstance(self.dpi_result, str) and self.dpi_result.startswith("FAILED"):
            self.log("WARNING: DPI awareness could not be set. If a screen isn't at 100% scaling, matching "
                     "and clicks will both be wrong.")

    # ------------------------------------------------------------------ run control

    def current_page(self):
        return self.page_stack.currentWidget()

    def toggle_run(self):
        queue = getattr(self, "_queue", None)
        if queue and queue.get("active") and not self.engine.running:
            queue["active"] = False
            self.log("Queue cancelled.")
            self._show_idle()
            self._update_pill()
            return
        if self._test_thread is not None and self._test_thread.is_alive():
            config.STOP_REQUESTED = True
            return
        if self.engine.running:
            self.stop_bot()
        else:
            self.start_bot()

    def start_bot(self):
        if self.engine.running:
            return
        if self.recorder.is_recording:
            self.toast("Still recording", "Press F8 to finish the recording before starting.", "warning")
            return
        page = self.current_page()
        if page.key == "queue":
            self.start_queue()
            return
        try:
            method, args, fields = page.run_spec()
        except ValueError as e:
            self.log(f"Can't start yet: {e}")
            self.toast("Not ready yet", str(e), "warning")
            self.refresh_ready(flash=True)
            return
        if not notify.is_configured():
            fields = None
        self._time_limit_hit = False
        self._limit_text = ""
        self._last_run_outcome = None
        self.engine.match_limit = page.run_limit()
        self.engine.start(getattr(self.engine, method), args, notify_fields=fields)
        self.log(f"Started: {page.summary()} - {page.runs.describe() if page.runs else 'forever'}.")
        self._set_running_ui(True)
        self.refresh_stats()

    def stop_bot(self):
        if not self.engine.running:
            return
        self.engine.stop()
        self.start_btn.setText("Stopping…")
        self.start_btn.setEnabled(False)
        self._on_phase_display("Stopping", "Finishing the current step safely - buttons and keys are released first.", "wait")

    def _set_running_ui(self, running):
        self.start_btn.setEnabled(True)
        self.start_btn.setText("Stop" if running else "Start")
        self.start_btn.set_kind("danger" if running else "success")
        self.start_btn.icon_name = "stop" if running else "play"
        self.start_btn.update()
        for key, page in self.pages.items():
            page.set_locked(running)
        for key, _, _ in NAV:
            item = self.nav.item(key)
            if item is not None:
                item.setEnabled(config.GAMEMODES.get(key, {}).get("enabled", True) and (not running or item.isChecked()))
        self._update_pill()

    def _on_run_ended(self):
        self._set_running_ui(False)
        queue = getattr(self, "_queue", None)
        if queue and queue.get("active"):
            stuck_reason = config.STUCK_DETECTED
            step_ok = (not self.engine.user_stop_requested and not stuck_reason
                       and not self._time_limit_hit)
            # "Skip a step that gets stuck" (Queue page) only covers a genuine stuck
            # failure - a deliberate Stop or a time limit still always ends/pauses the
            # queue, since those are the player's own decision, not a problem to route
            # around.
            skip_stuck = (stuck_reason and queue.get("skip_stuck")
                         and not self.engine.user_stop_requested and not self._time_limit_hit)
            if step_ok or skip_stuck:
                if skip_stuck:
                    sentence, _, _ = phases.describe(stuck_reason)
                    self.log(f"Queue step {queue['index'] + 1} got stuck ({sentence}) - skipping to the "
                             f"next step ('Skip a step that gets stuck' is on).")
                    config.STUCK_DETECTED = None
                queue["index"] += 1
                self._on_phase_display("Next queue step coming up",
                                       "Press Stop or F7 now to cancel the rest of the queue.", "work")
                QTimer.singleShot(3000, self._run_queue_step)
                self._update_pill()
                return
            queue["active"] = False
        reason = config.STUCK_DETECTED
        stats = SESSION.line()
        if self._time_limit_hit:
            self.toast(self._limit_text or "Limit reached", f"Stopped at the limit you set. {stats}", "info")
            self._last_run_outcome = "time"
        elif self.engine.user_stop_requested:
            self.toast("Stopped", stats, "info")
            self._last_run_outcome = "user"
            self._show_idle()
        elif reason:
            sentence, _, _ = phases.describe(reason)
            self.toast("The run stopped by itself", f"{sentence}. Activity and the log say what happened.", "error", 12000)
            self._last_run_outcome = "problem"
            self._beep()
        else:
            self.toast("Run finished", stats, "success")
            self._last_run_outcome = "finished"
            self._beep()
        self.refresh_stats()
        self.refresh_ready()

    def _beep(self):
        if self.user_settings.get("sound_on_stop", True):
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                pass

    def test_preset(self, loc, var, name):
        if self.engine.running or (self._test_thread and self._test_thread.is_alive()):
            self.toast("Busy", "Stop the current run before testing a recording.", "warning")
            return
        from modules.stage_player import play_preset

        def run():
            config.STOP_REQUESTED = False
            config.STUCK_DETECTED = None
            try:
                self.set_phase("TESTING RECORDING", "#2e7d32")
                if self.focus_and_pin():
                    result = play_preset(loc, var, name)
                    self.log("Test finished - that's exactly what a run will do." if result is True
                             else "Test stopped.")
            except Exception as e:
                self.log(f"ERROR: The test crashed - {e}")
            finally:
                config.STOP_REQUESTED = False
                self.set_phase("IDLE", "#a8a8a8")
                self.bridge.call.emit(self._test_finished)

        self._test_thread = threading.Thread(target=run, daemon=True)
        self._test_thread.start()
        self.start_btn.setText("Stop test")
        self.start_btn.set_kind("danger")
        self.start_btn.icon_name = "stop"
        self.log(f"Testing '{name.replace('_', ' ')}' - press F7 or Stop test to cancel.")

    def _test_finished(self):
        self._test_thread = None
        self._set_running_ui(False)

    # ------------------------------------------------------------------ queue

    def add_to_queue(self, page):
        try:
            page.run_spec()  # refuse a step that couldn't start, now rather than mid-queue
        except ValueError as e:
            self.toast("Can't add that yet", str(e), "warning")
            return
        # A queue step can't be "Forever" - the step after it would then never play. Only
        # the whole QUEUE (the "Start again from the top" toggle on the Queue page) is
        # allowed to run forever; each step needs its own real number of runs first.
        if page.run_limit() == 0:
            self.toast("Pick a number of runs first",
                      "A queue step can't be set to Forever, or the steps after it would never play. "
                      "Choose 50, 999 or Custom on this mode's How many runs card, then add it.", "warning")
            return
        item = {"mode": page.key, "state": page.full_state(), "runs": page.run_limit(),
                "runs_text": page.runs.describe() if page.runs else "forever", "summary": page.summary()}
        self.pages["queue"].add_item(item)
        count = len(self.pages["queue"].items)
        self.toast("Added to the queue", f"Step {count}: {item['summary']} - {item['runs_text']}. "
                                         f"Open Queue in the sidebar to see or reorder it.", "success")
        self.log(f"Queue step {count} added: {item['summary']} - {item['runs_text']}.")
        self._save_timer.start()

    def start_queue(self):
        page = self.pages["queue"]
        try:
            page.run_spec()
        except ValueError as e:
            self.toast("Queue is empty", str(e), "warning")
            return
        self._queue = {"items": [dict(i) for i in page.items], "index": 0, "active": True,
                       "repeat": page.repeat.isChecked(), "skip_stuck": page.skip_stuck.isChecked(),
                       "started_any": False}
        self.log(f"Queue started: {page.summary()}.")
        self._run_queue_step()

    def _queue_done(self, title, body, kind="success"):
        if getattr(self, "_queue", None):
            self._queue["active"] = False
        self.toast(title, body, kind, 9000)
        self.log(f"{title}. {body}")
        self._beep()
        self._show_idle()
        self._update_pill()

    def _run_queue_step(self):
        queue = getattr(self, "_queue", None)
        if not queue or not queue.get("active") or self.engine.running:
            return
        if queue["index"] >= len(queue["items"]):
            if queue["repeat"]:
                queue["index"] = 0
            else:
                self._queue_done("Queue finished", f"All {len(queue['items'])} steps played.")
                return
        item = queue["items"][queue["index"]]
        # A private copy of the mode's page, set up exactly as the step was saved - the
        # visible sidebar page is never touched.
        step_page = PAGE_CLASSES[item["mode"]](self)
        step_page.full_restore(item.get("state") or {})
        try:
            method, args, fields = step_page.run_spec()
        except ValueError as e:
            self._queue_done("Queue stopped", f"Step {queue['index'] + 1} can't start: {e}", "error")
            return
        if method == "run_challenges":
            # A standalone Challenges run always waits out a cooldown to recheck
            # later (see engine.run_challenges()'s own docstring) - a queue step's
            # job is "make progress, then let the next step run", so a pass that
            # finds nothing playable at all ends the step immediately instead of
            # camping here for as long as CHALLENGE_LOOP_WAIT_SECONDS keeps resetting,
            # holding up everything queued after it.
            args = (*args, True)
        queue["page"] = step_page
        run = getattr(self.engine, method)
        needs_lobby = queue["started_any"]
        queue["started_any"] = True

        def step(*a):
            if needs_lobby:
                self.set_phase("RETURNING TO LOBBY", "#4fc3f7")
                self.log("Queue: heading back to the lobby for the next step...")
                if not lobby.return_to_lobby(log=self.log, next_mode=item["mode"]):
                    if not config.STOP_REQUESTED:
                        config.STUCK_DETECTED = "COULD NOT RETURN TO LOBBY"
                    self.engine._cleanup()
                    return
            run(*a)

        if not notify.is_configured():
            fields = None
        self.engine.match_limit = int(item.get("runs") or 0)
        self.log(f"Queue step {queue['index'] + 1}/{len(queue['items'])}: {item['summary']} - {item['runs_text']}.")
        self._time_limit_hit = False
        self.engine.start(step, args, notify_fields=fields)
        self._set_running_ui(True)
        self.refresh_stats()

    # ------------------------------------------------------------------ recording

    def toggle_recording(self):
        rec = self.recorder
        if rec.is_recording:
            rec.stop_recording()
            return
        if self.engine.running:
            self.toast("The bot is running", "Stop it before recording.", "warning")
            return
        page = self.current_page()
        picker = page.picker
        if picker is None or picker.uses_auto() or not picker.recording_name():
            self.toast("Pick a recording first", "Switch to My recording and click New to make one.", "warning")
            return
        picker.sync_recorder()
        rec.start_recording()
        if not self.dock_state.docked:
            self.log("Recording - note Roblox isn't docked, so spots are saved relative to wherever the game window is.")

    def _poll_recorder(self):
        rec = self.recorder
        recording = rec.is_recording
        if recording != self._was_recording:
            self._was_recording = recording
            picker = self.current_page().picker
            if picker is not None:
                picker.set_recording(recording)
            if recording:
                self._rec_bar = getattr(self, "_rec_bar", None) or RecordingBar(self.toggle_recording)
                self._rec_bar.adjustSize()
                self._rec_bar.update_status(0, 0)
                self._place_rec_bar()
                self._rec_bar.show()
                self.log("Recording started - place your units in Roblox. Press F8 when you're done.")
            else:
                if getattr(self, "_rec_bar", None) is not None:
                    self._rec_bar.hide()
                n = len(rec.actions)
                self.log(f"Recording saved: {n} action{'s' if n != 1 else ''}.")
                self.toast("Recording saved", f"{n} actions. Open Edit to check every spot.", "success")
                if picker is not None:
                    picker.refresh_list(force=True)
                if self._reopen_editor:
                    args, self._reopen_editor = self._reopen_editor, None
                    QTimer.singleShot(300, lambda: self.open_editor(*args))
                self.refresh_ready()
            self._update_pill()
        elif recording and getattr(self, "_rec_bar", None) is not None:
            steps = sum(1 for a in rec.actions if a.get("type") == "click")
            self._rec_bar.update_status(time.time() - rec.start_time, steps)

    def _place_rec_bar(self):
        bar = self._rec_bar
        bar.adjustSize()
        if self.compact or not self.slot.isVisible():
            geo = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
            bar.move(geo.center().x() - bar.width() // 2, geo.top() + 12)
        else:
            top = self.slot.mapToGlobal(QPoint(self.slot.width() // 2, 10))
            bar.move(top.x() - bar.width() // 2, top.y())

    # ------------------------------------------------------------------ dialogs

    def open_settings(self, tab="runs"):
        dialogs.SettingsSheet(self, tab).exec()
        self.refresh_ready()
        self.refresh_stats()

    def open_welcome(self):
        from .welcome import WelcomeDialog
        dlg = WelcomeDialog(self, self.nav._current or "portals")
        accepted = dlg.exec()
        self.user_settings["welcome_done"] = True
        settings.save(self.user_settings)
        if accepted and not self.engine.running:
            self.nav.select(dlg.chosen_mode, emit=True)

    def open_editor(self, loc, var, name):
        if self.engine.running:
            self.toast("The bot is running", "Stop it before editing a recording.", "warning")
            return
        from .editor import UnitPlacementEditor, screenshot_pixmap, RESULT_RECORD, RESULT_TEST
        pix = screenshot_pixmap() if self.dock_state.docked else QPixmap()
        if loc == config.EXPEDITION_PRESET_LOCATION:
            context = "Used by all four expeditions"
        elif loc in config.MAPS:
            context = f"{config.MAPS[loc]['label']} - {config.MAPS[loc]['acts'].get(var, {}).get('label', var)}"
        elif loc in config.RAIDS:
            context = f"{config.RAIDS[loc]['label']} - difficulty {var}"
        else:
            context = ""
        dlg = UnitPlacementEditor(self, loc, var, name, context, pix)
        result = dlg.exec()
        picker = self.current_page().picker
        if picker is not None:
            picker.refresh_list(force=True)
        self.refresh_ready()
        if result == RESULT_RECORD:
            self._reopen_editor = (loc, var, name)
            self.toggle_recording()
        elif result == RESULT_TEST:
            self.test_preset(loc, var, name)

    # ------------------------------------------------------------------ settings hooks

    def apply_notification_settings(self, webhook_url):
        self.user_settings["discord_webhook"] = (webhook_url or "").strip()
        notify.configure(self.user_settings["discord_webhook"], {
            "lifecycle": self.user_settings.get("notify_run_lifecycle", True),
            "problems": self.user_settings.get("notify_problems", True),
            "milestones": self.user_settings.get("notify_milestones", True),
            "match": self.user_settings.get("notify_every_match", False),
        })
        self.refresh_ready()

    def apply_theme(self):
        theme.load_from_settings(self.user_settings)
        QApplication.instance().setStyleSheet(theme.stylesheet())
        self._style_logo()
        for w in QApplication.allWidgets():
            if isinstance(w, Button):
                w.set_kind(w.kind)
            w.update()
        settings.save(self.user_settings)

    # ------------------------------------------------------------------ pages, ready check, stats

    def _restore_ui_state(self):
        ui = self.user_settings.get("ui_state") or {}
        for key, data in (ui.get("pages") or {}).items():
            if key in self.pages and isinstance(data, dict):
                try:
                    self.pages[key].full_restore(data)
                except Exception as e:
                    print(f"[ui] Couldn't restore the {key} page: {e}")
        mode = ui.get("mode")
        if mode not in self.pages or not config.GAMEMODES.get(mode, {"enabled": True}).get("enabled"):
            mode = "portals"
        self.nav.select(mode, emit=False, animate=False)
        self._show_page(mode)

    def _save_ui_state(self):
        self.user_settings["ui_state"] = {
            "mode": self.nav._current,
            "pages": {k: p.full_state() for k, p in self.pages.items()},
        }
        settings.save(self.user_settings)

    def _on_nav(self, key):
        self._show_page(key)
        self._save_timer.start()

    def _show_page(self, key):
        page = self.pages[key]
        self.page_stack.set_current(page)
        page.on_shown()
        self.nav.set_trailing(key, "")
        self.refresh_ready()

    def _on_page_changed(self):
        if self.sender() is self.current_page():
            self.refresh_ready()
        self._save_timer.start()

    def refresh_ready(self, flash=False):
        rows = []
        state = self.dock_state
        if state.docked:
            w, h = state.game_size
            rows.append(("Roblox", "ok", f"docked · {w} x {h}" if not self.compact else f"own window · {w} x {h}"))
        elif roblox_is_running() if not self.preview else False:
            rows.append(("Roblox", "warn", "open - docks when you press Start"))
        else:
            rows.append(("Roblox", "bad", "not open"))
        confirmed = bool(self.user_settings.get("game_settings_confirmed"))
        rows.append(("Auto Start & Retry", "ok" if confirmed else "warn",
                     "on in the game" if confirmed else "turn on in game"))
        page = self.current_page()
        rows.extend(page.checks() if page is not None else [])
        rows.append(("Discord alerts", "ok" if notify.is_configured() else "idle",
                     "connected" if notify.is_configured() else "not set up"))

        seen = set()
        for text, st, detail in rows:
            row = self.ready_rows.get(text)
            if row is None:
                row = CheckRow(text)
                self.ready_rows[text] = row
                self.ready_card.body.addWidget(row)
            row.set_state(st, detail)
            row.show()
            seen.add(text)
        for text, row in self.ready_rows.items():
            if text not in seen:
                row.hide()

        blocking = [(t, d) for t, s, d in rows if s == "bad" and t != "Roblox"]
        self.setup_hint.setText("ready" if not blocking else "")
        self.setup_hint.setStyleSheet(f"color: {theme.SUCCESS_TEXT}; font-size: 11.5px;")
        if flash:
            self.ready_card.setStyleSheet(f"QFrame#cardSunken {{ border: 1px solid {theme.DANGER}; }}")
            QTimer.singleShot(1400, lambda: self.ready_card.setStyleSheet(""))
        if not self.engine.running and self._phase_code in ("IDLE",):
            self._show_idle(blocking)
        self._update_pill()

    def _show_idle(self, blocking=None):
        page = self.current_page()
        if blocking is None:
            blocking = [(t, d) for t, s, d in (page.checks() if page else []) if s == "bad"]
        if blocking:
            t, d = blocking[0]
            self._on_phase_display("Almost ready", f"{t}: {d}.", "idle")
        else:
            summary = page.summary() if page else ""
            self._on_phase_display("Ready when you are",
                                   f"{summary}. Go to the lobby (or where it left off), then press Start or F7.", "idle")

    def _update_pill(self):
        if self.recorder.is_recording:
            self.pill.set_state("Recording", theme.DANGER_TEXT, True)
        elif self.engine.running:
            if self.engine.restart_pending:
                self.pill.set_state("Restarting", theme.WARNING, True)
            else:
                queue = getattr(self, "_queue", None)
                if queue and queue.get("active"):
                    self.pill.set_state(f"Queue {queue['index'] + 1}/{len(queue['items'])}", theme.SUCCESS_TEXT, True)
                else:
                    self.pill.set_state("Running", theme.SUCCESS_TEXT, True)
        elif self._test_thread is not None:
            self.pill.set_state("Testing", theme.SUCCESS_TEXT, True)
        else:
            self.pill.set_state("Idle", theme.TEXT_DIM, False)

    def refresh_stats(self):
        s = SESSION
        started = s.started_at is not None
        never = bool(self.user_settings.get("never_stop"))
        if not started:
            self.stats["time"].set_value("-", "starts with the run")
            self.stats["matches"].set_value(0, "")
            self.stats["wl"].set_value("- / -", "")
            self.stats["rate"].set_value("-", "")
            self.stats["restarts"].set_value(0, f"Never stop is {'on' if never else 'off'}")
            return
        since = time.strftime("%H:%M", time.localtime(s.started_at))
        self.stats["time"].set_value(format_duration(s.elapsed), f"since {since}")
        self.stats["matches"].set_value(s.matches, f"about {format_duration(s.avg_match)} each" if s.avg_match else "")
        extra = f"{s.disconnects} reconnect{'s' if s.disconnects != 1 else ''}" if s.disconnects else ""
        self.stats["wl"].set_value(f"{s.victories} / {s.defeats}", extra)
        total = s.victories + s.defeats
        if total:
            rate = round(100 * s.victories / total)
            color = theme.SUCCESS_TEXT if rate >= 80 else theme.WARNING if rate >= 50 else theme.DANGER_TEXT
            sub = f"{s.rewards_picked} rewards picked" if s.rewards_picked else ""
            self.stats["rate"].set_value(f"{rate}%", sub, color)
        else:
            self.stats["rate"].set_value("-", "")
        self.stats["restarts"].set_value(s.restarts, f"Never stop is {'on' if never else 'off'}")

    def _every_second(self):
        if self.engine.running:
            self.refresh_stats()
            hours = float(self.user_settings.get("stop_after_hours") or 0)
            max_matches = int(self.user_settings.get("stop_after_matches") or 0)
            finished = SESSION.victories + SESSION.defeats
            if hours > 0 and SESSION.elapsed >= hours * 3600 and not self.engine.user_stop_requested:
                self._time_limit_hit = True
                self._limit_text = "Time limit reached"
                self.log(f"Time limit reached ({hours:g} h) - stopping.")
                self.stop_bot()
            elif max_matches > 0 and finished >= max_matches and not self.engine.user_stop_requested:
                self._time_limit_hit = True
                self._limit_text = "Match limit reached"
                self.log(f"Played {finished} matches - the limit you set. Stopping.")
                self.stop_bot()
            self._update_pill()
        elif not self.recorder.is_recording and self.isVisible():
            picker = self.current_page().picker
            if picker is not None and int(time.time()) % 3 == 0:
                picker.refresh_list()
                self.refresh_ready()

    # ------------------------------------------------------------------ phase & activity

    def _on_phase(self, code, color):
        self._phase_code = code
        sentence, detail, tone = phases.describe(code, color)
        if code == "IDLE":
            self._show_idle()
            return
        if not detail and self.engine.running:
            detail = self.current_page().summary()
            if SESSION.matches:
                detail = f"Match {SESSION.matches} · {detail}"
        self._on_phase_display(sentence, detail, tone)

    def _on_phase_display(self, sentence, detail, tone):
        color = phases.TONE_COLORS.get(tone, theme.TEXT_MUTED)
        self.now_title.setText(sentence)
        self.now_title.setStyleSheet(f"color: {theme.TEXT if tone in ('idle', 'work', 'play') else color};")
        self.now_detail.setText(detail)
        moving = tone in ("work", "play", "wait")
        self.now_dot.set_state(color, moving)
        self.now_bar.set_active(moving)

    def _on_log(self, message, from_engine):
        stamp = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"{stamp}  {message}")
        if not from_engine:
            return
        text = message.strip()
        if not text or message.startswith("   ") or text.startswith(("Session summary", "Matches played", "Won / Lost",
                                                                        "Win rate", "Average match", "Portal rewards")):
            return
        low = text.lower()
        if text.startswith("ERROR") or "stopped -" in low or "crash" in low:
            color = theme.DANGER_TEXT
        elif text.startswith("WARNING") or "never stop" in low or "restart" in low:
            color = theme.WARNING
        elif any(w in low for w in ("victory", "won", "reward", "docked", "saved", "finished", "started")):
            color = theme.SUCCESS_TEXT
        else:
            color = theme.TEXT_MUTED
        item = QListWidgetItem(f"{stamp[:5]}   {text}")
        item.setToolTip(text)
        dot = QPixmap(10, 10)
        dot.fill(Qt.transparent)
        from PySide6.QtGui import QPainter
        p = QPainter(dot)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(1, 1, 8, 8)
        p.end()
        item.setIcon(QIcon(dot))
        item.setForeground(QColor(theme.TEXT_SOFT if color == theme.TEXT_MUTED else color))
        self.activity.insertItem(0, item)
        while self.activity.count() > ACTIVITY_MAX:
            self.activity.takeItem(self.activity.count() - 1)
        if self.feed_stack.currentWidget() is self.activity_empty:
            self.feed_stack.setCurrentWidget(self.activity)

    def _toggle_log(self):
        showing_log = self.feed_stack.currentWidget() is self.log_view
        if showing_log:
            self.feed_stack.setCurrentWidget(self.activity if self.activity.count() else self.activity_empty)
            self.log_toggle.setText("Full log")
        else:
            self.feed_stack.setCurrentWidget(self.log_view)
            self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())
            self.log_toggle.setText("Activity")
        self.log_toggle.update()

    # ------------------------------------------------------------------ troubleshooting

    def fix_camera(self):
        """Debug tool: replays the same camera anchor a real run starts with, without
        needing a whole mode set up or a match running."""
        if self.engine.running or (self._test_thread and self._test_thread.is_alive()):
            self.toast("Busy", "Stop the current run before using this.", "warning")
            return

        def run():
            from input_controller import anchor_camera
            try:
                if not self.focus_and_pin():
                    self.bridge.call.emit(lambda: self.toast("Couldn't find Roblox", "Dock it first.", "warning"))
                    return
                anchor_camera()
                self.bridge.call.emit(lambda: self.toast(
                    "Camera fixed", "That's the view a real run lines up before placing units.", "success"))
            except Exception as e:
                msg = str(e)
                self.bridge.call.emit(lambda: self.toast("Camera fix failed", msg, "error"))

        self.toast("Fixing the camera…", "Don't touch the mouse for a moment.", "info", 4000)
        threading.Thread(target=run, daemon=True).start()

    def _pick_screen_position(self, on_picked, prompt="Hover the spot in Roblox, then press Space. Esc cancels."):
        """
        Hover a spot in the docked game, press Space, get its reference-space (x, y) -
        the same numbers config.py and every preset use. on_picked(x, y) is called on
        the Qt thread once picked; on_picked(None) if cancelled or Roblox isn't docked.

        Used by the Coordinate finder debug tool, where you often want a point you
        deliberately do NOT click through (something on a live match's HUD, say).
        The Autoclicker and fishing-spot Locate buttons use _pick_screen_click()
        instead (below) - a real click doubles as "use this spot", which is what
        those two actually want.
        """
        if self.engine.running or (self._test_thread and self._test_thread.is_alive()):
            self.toast("Busy", "Stop the current run before using this.", "warning")
            on_picked(None)
            return
        if not self.dock_state.docked:
            self.toast("Dock Roblox first", "A coordinate only means something once the game is actually docked.",
                      "warning")
            on_picked(None)
            return

        from pynput import keyboard
        from input_controller import cursor_position
        result = {}
        done = threading.Event()

        def on_press(key):
            if key == keyboard.Key.space:
                result["pos"] = cursor_position()
                done.set()
                return False
            if key == keyboard.Key.esc:
                done.set()
                return False

        listener = keyboard.Listener(on_press=on_press)
        listener.start()
        self.toast("Locate", prompt, "info", 8000)

        def run():
            done.wait(30.0)
            listener.stop()
            if "pos" not in result:
                self.bridge.call.emit(lambda: (self.toast("Cancelled", "No point was picked.", "info"), on_picked(None)))
                return
            sx, sy = result["pos"]
            x, y = config.to_reference(sx - config.CLIENT_ORIGIN[0], sy - config.CLIENT_ORIGIN[1])
            self.log(f"[Locate] {x}, {y}")
            self.bridge.call.emit(lambda: on_picked((x, y)))

        threading.Thread(target=run, daemon=True).start()

    def _pick_screen_click(self, on_picked, prompt="Click the spot in Roblox. Esc cancels."):
        """
        Click a spot in the docked game to capture its reference-space (x, y) -
        used by the Autoclicker and fishing-spot Locate buttons, where the point
        being picked is one the bot is going to click anyway, so a real click there
        to pick it is harmless (unlike _pick_screen_position's hover+Space, which
        exists specifically to avoid clicking through). on_picked((x, y)) is called
        on the Qt thread once picked; on_picked(None) if cancelled, busy, or Roblox
        isn't docked.
        """
        if self.engine.running or (self._test_thread and self._test_thread.is_alive()):
            self.toast("Busy", "Stop the current run before using this.", "warning")
            on_picked(None)
            return
        if not self.dock_state.docked:
            self.toast("Dock Roblox first", "A position only means something once the game is actually docked.",
                      "warning")
            on_picked(None)
            return

        from pynput import mouse, keyboard
        result = {}
        done = threading.Event()

        def on_click(x, y, button, pressed):
            if button == mouse.Button.left and pressed:
                result["pos"] = (x, y)
                done.set()
                return False

        def on_key(key):
            if key == keyboard.Key.esc:
                done.set()
                return False

        mouse_listener = mouse.Listener(on_click=on_click)
        key_listener = keyboard.Listener(on_press=on_key)
        mouse_listener.start()
        key_listener.start()
        self.toast("Locate", prompt, "info", 8000)

        def run():
            done.wait(30.0)
            mouse_listener.stop()
            key_listener.stop()
            if "pos" not in result:
                self.bridge.call.emit(lambda: (self.toast("Cancelled", "No point was picked.", "info"), on_picked(None)))
                return
            sx, sy = result["pos"]
            x, y = config.to_reference(sx - config.CLIENT_ORIGIN[0], sy - config.CLIENT_ORIGIN[1])
            self.log(f"[Locate] {x}, {y}")
            self.bridge.call.emit(lambda: on_picked((x, y)))

        threading.Thread(target=run, daemon=True).start()

    def _pick_keybind(self, on_picked, exclude=()):
        """
        Captures the next single key press anywhere, for rebinding a global hotkey
        (Settings > Window > Keybinds). on_picked(key_string) is called on the Qt
        thread; on_picked(None) if cancelled (Esc), reserved (Esc/Space - the picker
        flows above already use those), or already assigned to another action
        (`exclude`, the other current bindings).
        """
        from pynput import keyboard
        from modules.keybinds import key_to_string, RESERVED
        result = {}
        done = threading.Event()

        def on_press(key):
            if key == keyboard.Key.esc:
                done.set()
                return False
            result["key"] = key_to_string(key)
            done.set()
            return False

        listener = keyboard.Listener(on_press=on_press)
        listener.start()
        self.toast("Press a key", "Press the new key now. Esc cancels.", "info", 8000)

        def run():
            done.wait(15.0)
            listener.stop()
            value = result.get("key")
            if not value:
                self.bridge.call.emit(lambda: on_picked(None))
                return
            if value in RESERVED:
                self.bridge.call.emit(lambda: (
                    self.toast("Can't use that key", "Esc and Space are reserved for these picker prompts.", "warning"),
                    on_picked(None)))
                return
            if value in exclude:
                self.bridge.call.emit(lambda: (
                    self.toast("Already in use", f"'{value.upper()}' is bound to another action.", "warning"),
                    on_picked(None)))
                return
            self.bridge.call.emit(lambda: on_picked(value))

        threading.Thread(target=run, daemon=True).start()

    def toggle_autoclicker(self):
        if not config.AUTOCLICKER_POS:
            self.toast("No position set", "Locate a click position first - Settings > Window > Autoclicker.",
                      "warning")
            return
        config.AUTOCLICKER_ENABLED = not config.AUTOCLICKER_ENABLED
        state = "ON" if config.AUTOCLICKER_ENABLED else "OFF"
        self.log(f"Autoclicker: {state}.")
        self.toast("Autoclicker", f"Now {state}.", "success" if config.AUTOCLICKER_ENABLED else "info")

    def find_coordinate(self):
        """Debug tool: hover a spot in the docked game, press Space, get its
        reference-space (x, y) - the same numbers config.py and every preset use."""
        self._pick_screen_position(
            lambda pos: self.toast("Coordinate", f"{pos[0]}, {pos[1]}  -  also written to the log.", "success")
            if pos else None)

    def debug_screenshot(self):
        """Debug tool: saves exactly what the bot currently sees, on demand."""
        try:
            from vision import capture_screen
            shot = capture_screen()
            path = health.save_debug_screenshot("manual", shot)
            self.log(f"Debug screenshot saved: {path}")
            self.toast("Screenshot saved", path or "Saved to the debug folder.", "success")
        except Exception as e:
            self.toast("Couldn't capture", str(e), "error")

    def backup_my_data(self):
        """
        Zips presets/, movement_presets/, challenge_links.json and settings.json
        (webhook stripped, same trick as make_report()'s safe dict) to the Desktop -
        a one-click safety net before updating, reinstalling Windows, or trying
        something risky. Separate from make_report(): that one is for troubleshooting
        (logs + debug shots, no config), this one is for restoring your setup
        (config, no logs/debug).

        Matters most right now because updating is still a manual "download the new
        zip and extract it" - see modules.update_check's own docstring. The new zip
        never contains these files, so extracting it OVER the existing folder leaves
        them untouched; this backup only matters if someone deletes the old folder
        first instead. Once there's a real auto-updater this becomes optional rather
        than the only safety net.
        """
        import json
        import subprocess
        import zipfile
        base = settings.base_dir()
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(desktop):
            desktop = base
        dest = os.path.join(desktop, f"Lucki's Macro backup {time.strftime('%Y-%m-%d %H-%M')}.zip")
        safe = {k: v for k, v in self.user_settings.items() if k != "discord_webhook"}
        safe["discord_webhook_set"] = bool(self.user_settings.get("discord_webhook"))
        try:
            with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("settings_without_webhook.json", json.dumps(safe, indent=2))
                for folder in ("presets", "movement_presets"):
                    src = os.path.join(base, folder)
                    if not os.path.isdir(src):
                        continue
                    for root, _dirs, files in os.walk(src):
                        for name in files:
                            path = os.path.join(root, name)
                            z.write(path, os.path.join(folder, os.path.relpath(path, src)))
                links = os.path.join(base, "challenge_links.json")
                if os.path.isfile(links):
                    z.write(links, "challenge_links.json")
        except Exception as e:
            self.toast("Couldn't make the backup", str(e), "error")
            return None
        self.log(f"Backup saved: {dest}")
        self.toast("Backup ready", "Your recordings and settings are saved to your Desktop. Your Discord link "
                  "isn't included - if you use one, you'll need to paste it in again after restoring.",
                  "success", 9000)
        try:
            subprocess.Popen(["explorer", "/select,", dest])
        except Exception:
            pass
        return dest

    def make_report(self):
        """
        Zips what's needed to work out a problem - logs, the newest debug screenshots,
        screen/scale info and settings - onto the Desktop. The Discord webhook is left
        out: the report is meant to be sent to someone.
        """
        import glob
        import json
        import subprocess
        import zipfile
        base = settings.base_dir()
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(desktop):
            desktop = base
        dest = os.path.join(desktop, f"Lucki's Macro report {time.strftime('%Y-%m-%d %H-%M')}.zip")
        safe = {k: v for k, v in self.user_settings.items() if k != "discord_webhook"}
        safe["discord_webhook_set"] = bool(self.user_settings.get("discord_webhook"))
        lines = [f"Created {time.strftime('%Y-%m-%d %H:%M:%S')}",
                 f"DPI awareness: {self.dpi_result}",
                 f"Game size: {self.dock_state.game_size}, docked: {self.dock_state.docked}, compact: {self.compact}",
                 f"SCALE {config.SCALE:.4f}, UI_SCALE {config.UI_SCALE:.4f}, SLOWNESS {config.SLOWNESS:.2f}"]
        for i, s in enumerate(QGuiApplication.screens()):
            g, dpr = s.geometry(), s.devicePixelRatio()
            lines.append(f"Screen {i + 1}: {round(g.width() * dpr)}x{round(g.height() * dpr)} at {round(dpr * 100)}%")
        preset_dir = os.path.join(base, "presets")
        lines.append("Recordings: " + (", ".join(sorted(os.listdir(preset_dir))) if os.path.isdir(preset_dir) else "none"))
        try:
            with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("about.txt", "\n".join(lines))
                z.writestr("settings_without_webhook.json", json.dumps(safe, indent=2))
                for path in glob.glob(os.path.join(base, "logs", "*.log*")):
                    z.write(path, os.path.join("logs", os.path.basename(path)))
                shots = sorted(glob.glob(os.path.join(base, "debug", "*")), key=os.path.getmtime)[-12:]
                for path in shots:
                    z.write(path, os.path.join("debug", os.path.basename(path)))
        except Exception as e:
            self.toast("Couldn't make the report", str(e), "error")
            return None
        self.log(f"Troubleshooting report saved: {dest}")
        self.toast("Report ready", "Saved to your Desktop - send that zip file. It doesn't include your Discord link.",
                   "success", 9000)
        try:
            subprocess.Popen(["explorer", "/select,", dest])
        except Exception:
            pass
        return dest

    # ------------------------------------------------------------------ toasts

    def toast(self, title, body, kind="success", timeout_ms=6000):
        if self.preview:
            return
        def alive(w):
            # A toast deletes itself when it fades out; asking a deleted one anything
            # raises instead of returning False (this crashed the run-ended handler).
            try:
                return w.isVisible()
            except RuntimeError:
                return False

        self._toasts = [t for t in self._toasts if alive(t)]
        t = Toast(title, body, kind, timeout_ms)
        if self.compact or not self.slot.isVisible():
            geo = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
            anchor = QPoint(geo.right() - SIDEBAR_WIDTH - 20, geo.top() + 16)
        else:
            anchor = self.slot.mapToGlobal(QPoint(self.slot.width() - 12, 12))
        y = anchor.y()
        for other in self._toasts:
            if alive(other):
                y += other.height() - 8
        t.show_at(QPoint(anchor.x(), y))
        self._toasts.append(t)

    # ------------------------------------------------------------------ closing

    def closeEvent(self, event):
        self.engine.user_stop_requested = True
        config.STOP_REQUESTED = True
        self._save_ui_state()
        if self.engine.bot_thread is not None and self.engine.bot_thread.is_alive():
            self.engine.bot_thread.join(timeout=3.0)
        if self._test_thread is not None and self._test_thread.is_alive():
            self._test_thread.join(timeout=2.0)
        if not self.preview:
            try:
                docking.undock(self._hwnd(), self.dock_state)
            except Exception:
                pass
            try:
                self.recorder.mouse_listener.stop()
                self.recorder.keyboard_listener.stop()
            except Exception:
                pass
        for t in list(self._toasts):
            try:
                t.close()
            except RuntimeError:
                pass
        if getattr(self, "_rec_bar", None) is not None:
            self._rec_bar.close()
        event.accept()
        QApplication.instance().quit()
