# logger.py
"""
Central logging: tees every print() to a timestamped rotating file, and (once the
GUI exists) to the on-screen log box as well.

Why a tee instead of rewriting the print() calls: the modules are full of print()
diagnostics - [vision]'s confidence readouts, [Reconnect]'s state, [Player]'s
progress - and there are ~94 of them. The shipped exe is built with console=False
(see macro_slop.spec), which leaves sys.stdout as None, and Python's print() throws
its output away silently when that's the case. So every one of those diagnostics
vanished in the build - precisely where they're needed, since a bot that runs for
days fails while nobody's watching and the confidence numbers are the only way to
tell a stale template from a threshold that's slightly too strict.

Redirecting sys.stdout once at startup captures all of them without touching a
single call site, and gives the same lines a timestamp and a file on disk.
"""
import os
import sys
import threading
import logging
from logging.handlers import RotatingFileHandler

LOG_DIR_NAME = "logs"
LOG_FILE_NAME = "macro_slop.log"
MAX_BYTES = 2 * 1024 * 1024  # 2 MB per file
BACKUP_COUNT = 3             # keep macro_slop.log.1 .. .3

_lock = threading.Lock()
_gui_sink = None
_log_path = None
_installed = False


class _Tee:
    """
    Stand-in for sys.stdout that fans each completed line out to the log file, the
    GUI sink and the real stdout (when there is one - there isn't in the windowed
    build).

    Buffers until a newline because print() writes the text and the newline as two
    separate write() calls; emitting per-write would put every message on two lines
    in the log file and fire the GUI sink twice per message.
    """

    def __init__(self, original, logger):
        self._original = original
        self._logger = logger
        self._buffer = ""

    def write(self, text):
        if self._original is not None:
            try:
                self._original.write(text)
            except Exception:
                pass  # a dead/closed console must never take the app down with it

        with _lock:
            self._buffer += text
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                self._emit(line)

    def _emit(self, line):
        if not line.strip():
            return

        try:
            self._logger.info(line)
        except Exception:
            pass

        if _gui_sink is not None:
            try:
                _gui_sink(line)
            except Exception:
                pass  # GUI torn down mid-write, or not on the main thread yet

    def flush(self):
        if self._original is not None:
            try:
                self._original.flush()
            except Exception:
                pass

    def isatty(self):
        return False


def install(base_dir):
    """
    Points sys.stdout/sys.stderr at the tee. Safe to call once; later calls are
    ignored so a re-import can't nest tees inside each other.
    Returns the log file's full path, or None if the file couldn't be opened (in
    which case printing still works exactly as it did before).
    """
    global _installed, _log_path
    if _installed:
        return _log_path

    log_dir = os.path.join(base_dir, LOG_DIR_NAME)
    try:
        os.makedirs(log_dir, exist_ok=True)
        _log_path = os.path.join(log_dir, LOG_FILE_NAME)
        handler = RotatingFileHandler(
            _log_path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    except Exception as e:
        # No disk log available (read-only folder, permissions). Don't take the app
        # down over it - stdout stays exactly as it was.
        print(f"[logger] Could not open a log file: {e}")
        _log_path = None
        return None

    file_logger = logging.getLogger("macro_slop")
    file_logger.setLevel(logging.INFO)
    file_logger.propagate = False
    file_logger.handlers.clear()
    file_logger.addHandler(handler)

    sys.stdout = _Tee(sys.__stdout__, file_logger)
    sys.stderr = _Tee(sys.__stderr__, file_logger)
    _installed = True

    file_logger.info("=" * 70)
    file_logger.info("Lucki's Macro session started.")
    return _log_path


def log_to_file(message):
    """
    Writes a line straight to the log file without going near stdout.

    For the GUI's own messages, which until now existed only in the on-screen log box:
    they are the ones that say WHICH branch the run actually took ("Defeat - that's one
    of this portal's 3 hearts", "No reward choice offered this time"), while the file
    got only the module-level print()s underneath them. Reading a session back
    afterwards meant inferring the decision from its side effects, and a run that had
    already scrolled its GUI box was simply unrecoverable.
    """
    if not _installed or not message or not str(message).strip():
        return
    try:
        logging.getLogger("macro_slop").info(str(message))
    except Exception:
        pass


def set_gui_sink(callback):
    """
    Registers the GUI's log-writing function, called with one line at a time. The
    callback runs on whichever thread printed, so it must marshal to the UI thread
    itself (gui.log() already does, via self.after).
    """
    global _gui_sink
    _gui_sink = callback


def get_log_path():
    return _log_path
