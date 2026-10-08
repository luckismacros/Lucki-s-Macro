# app_qt.py
"""
Entry point for the Qt (PySide6) window. gui.py - the CustomTkinter window - still
works and drives the exact same engine; this is the new face on it.

  python app_qt.py
"""
# --- DPI awareness. Must run before ANY other import - see gui.py for why. ------
import ctypes as _ctypes


def _set_dpi_awareness():
    try:
        if _ctypes.windll.user32.SetProcessDpiAwarenessContext(_ctypes.c_int(-4)):
            return "per-monitor v2 (SetProcessDpiAwarenessContext)"
    except Exception:
        pass
    try:
        _ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "per-monitor (shcore)"
    except Exception as e:
        try:
            if _ctypes.windll.user32.SetProcessDPIAware():
                return "system-aware (user32)"
            return f"FAILED - all APIs refused ({e})"
        except Exception as e2:
            return f"FAILED - {e2}"


DPI_AWARENESS_RESULT = _set_dpi_awareness()
# ------------------------------------------------------------------------------

import os
import sys
import time

_is_bundled = getattr(sys, "frozen", False) or "__compiled__" in globals()
_base_dir = os.path.dirname(sys.executable) if _is_bundled else os.path.dirname(os.path.abspath(__file__))
os.chdir(_base_dir)
os.makedirs("presets", exist_ok=True)

import logger  # noqa: E402

_log_path = logger.install(_base_dir)

# A crash in a Qt slot (a button click, a queue step's own callback, a timer) used to
# have nowhere to go: console=False means there's no console for Python's default
# traceback printer to write to, so it vanished completely - not even a line in the
# log file. From the outside that reads as "the macro randomly stopped" with nothing
# to explain why. This makes sure one always lands in the log instead.
def _log_uncaught_exception(exc_type, exc_value, exc_tb):
    import traceback
    print("[uncaught] " + "".join(traceback.format_exception(exc_type, exc_value, exc_tb)).rstrip())
    try:
        from modules import crash_report, notify
        info = crash_report.describe(exc_value)
        notify.send(crash_report.format_for_discord(info) + "\n(in the app window - the run itself may carry on)",
                    category="problems", title=f"App error: {info['type']}", good=False,
                    fields=[("Last log lines", crash_report.last_lines_block(8), False)])
    except Exception:
        pass


sys.excepthook = _log_uncaught_exception

# Background threads and native (non-Python) crashes - see modules/crash_report.py.
from modules import crash_report  # noqa: E402

crash_report.install()

# Qt would try to set DPI awareness itself and warn that it's already set - it is, to
# the same per-monitor v2 mode Qt wants, so the warning is noise.
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.window=false")

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def _offer_data_from_old_version():
    """
    First launch of a freshly extracted version (no settings.json here yet): find the
    previous version's folder and offer to bring its data over - recordings, walks,
    challenge links, settings (webhook and bot token included) - instead of copying it
    all by hand (modules/migrate.py). Runs before the window loads its settings, so they
    apply straight away. Never fatal.
    """
    import settings
    if os.path.exists(settings.SETTINGS_PATH):
        return
    try:
        from modules import migrate
        from PySide6.QtWidgets import QMessageBox
        found = migrate.find_previous_installs()
        if not found:
            return
        best = found[0]
        when = time.strftime("%d %b %Y %H:%M", time.localtime(best["last_used"])) if best["last_used"] else "?"
        box = QMessageBox()
        box.setWindowTitle("Lucki's Macro - bring your data over?")
        box.setIcon(QMessageBox.Question)
        box.setText("Found your data from another copy of Lucki's Macro:")
        box.setInformativeText(f"{best['path']}\n\n{best['recordings']} recording(s), last used {when}.\n\n"
                               f"Bring over your recordings, walks, challenge links and settings "
                               f"(Discord webhook and bot included)? Nothing in the old folder is changed.")
        yes = box.addButton("Bring it over", QMessageBox.AcceptRole)
        box.addButton("Start fresh", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            print(f"[migrate] Not bringing data over from {best['path']}.")
            return
        result = migrate.copy_data_from(best["path"], replace_recordings=False, replace_settings=True)
        print(f"[migrate] Brought over from {best['path']}: {result['recordings']} recording(s)"
              f"{', settings' if result['settings'] else ''}{', challenge links' if result['links'] else ''}.")
    except Exception as e:
        print(f"[migrate] Couldn't bring data over from an older version: {type(e).__name__}: {e}")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Lucki's Macro")
    from PySide6.QtGui import QIcon
    from ui_qt import brand
    if os.path.exists(brand.ICON_PATH):
        app.setWindowIcon(QIcon(brand.ICON_PATH))
    font = QFont("Segoe UI")
    font.setPixelSize(13)
    app.setFont(font)

    _offer_data_from_old_version()

    from ui_qt.main_window import MainWindow
    window = MainWindow(DPI_AWARENESS_RESULT, _log_path)
    window.launch()
    try:
        return app.exec()
    except KeyboardInterrupt:
        window.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())
