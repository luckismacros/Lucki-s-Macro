# modules/crash_report.py
"""
Making crashes and silent shutdowns leave evidence - in the log file AND on Discord.

What used to happen (reported 2026-10-07, "it randomly crashes or shuts off"):
  - A crash inside a run sent Discord only the bare exception text ("list index out of
    range") and logged the same one line - no file, no line, no idea what the bot was
    doing. Nothing to fix from.
  - A crash in any OTHER thread (recorder, notifications, update check) went nowhere.
  - A native crash (Qt/OpenCV/driver) or the process being killed (PC sleep, Windows
    update, out of memory) ends the app instantly: no Python exception at all, so
    nothing was ever logged or sent - the window was just gone.

What this does:
  describe()          - exception -> type, message, the exact file:line + function in
                        this app's own code, and that line of source.
  crash_fields()      - the Discord fields every crash/stop message shares.
  install()           - logs uncaught exceptions from every thread (full traceback), and
                        turns on faulthandler so a native crash writes its stack to
                        logs/crash_native.log before the process dies.
  run_started() /
  run_ended()         - a small marker file kept while a run is going. If the app
                        starts and the marker is still there, the previous run never
                        ended normally - check_previous_session() then reports that,
                        with the last lines that session logged and any native crash
                        dump, so a silent shutdown is at least visible the next time.
"""
import faulthandler
import json
import os
import sys
import threading
import time
import traceback

import logger

_LOG_DIR = "logs"
_MARKER = "run_in_progress.json"
_NATIVE = "crash_native.log"
_native_file = None
_app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _path(name):
    return os.path.join(_LOG_DIR, name)


# --- describing an exception -------------------------------------------------------------

def describe(exc):
    """
    {"type", "message", "where", "code", "trace"} for an exception. "where" is the
    innermost frame inside this app (not Python's or a library's), which is the line worth
    looking at; "trace" is the app's own frames, outermost first, as short lines.
    """
    tb = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    own = [f for f in tb if _is_app_file(f.filename)]
    inner = own[-1] if own else (tb[-1] if tb else None)
    where = code = ""
    if inner:
        where = f"{_short(inner.filename)}:{inner.lineno} in {inner.name}()"
        code = (inner.line or "").strip()
    trace = [f"{_short(f.filename)}:{f.lineno} {f.name}()" for f in own[-6:]]
    return {
        "type": type(exc).__name__,
        "message": str(exc) or "(no message)",
        "where": where,
        "code": code,
        "trace": trace,
    }


_APP_TOP_LEVEL = {"engine.py", "vision.py", "input_controller.py", "config.py", "app_qt.py", "logger.py",
                  "settings.py", "gui.py", "theme.py", "dpi_awareness.py", "ui_motion.py"}


def _is_app_file(filename):
    # By name, not by absolute path: inside the built exe every frame's filename is
    # relative (the standard library's too), so "is it under the app folder" can't tell.
    rel = _short(filename)
    first = rel.split("/", 1)[0]
    return first in ("modules", "ui_qt") or rel in _APP_TOP_LEVEL


def _short(filename):
    if not os.path.isabs(filename):
        return filename.replace("\\", "/")
    f = os.path.abspath(filename)
    if f.startswith(_app_root):
        return os.path.relpath(f, _app_root).replace("\\", "/")
    return os.path.basename(filename)


def crash_fields(mode=None, phase=None, started_at=None, matches=None):
    """The context fields shared by crash and stop messages."""
    fields = []
    if mode:
        fields.append(("Mode", mode))
    if phase:
        fields.append(("Was doing", phase))
    if started_at:
        from modules.stats import format_duration
        fields.append(("Running for", format_duration(time.time() - started_at)))
    if matches is not None:
        fields.append(("Matches", str(matches)))
    return fields


def last_lines_block(n=12, limit=1000):
    """The last log lines as a Discord code block, trimmed to fit an embed field."""
    lines = logger.recent_lines(n)
    text = "\n".join(lines)
    while len(text) > limit and lines:
        lines = lines[1:]
        text = "\n".join(lines)
    return f"```\n{text}\n```" if text else ""


def format_for_discord(info):
    """Body text for a crash message from describe()'s dict."""
    body = f"**{info['type']}: {info['message'][:300]}**"
    if info["where"]:
        body += f"\nAt `{info['where']}`"
    if info["code"]:
        body += f"\n```py\n{info['code'][:300]}\n```"
    if len(info["trace"]) > 1:
        body += "\nCalled from: " + " → ".join(f"`{t}`" for t in info["trace"][:-1][-3:])
    return body


# --- catching what nothing else catches -------------------------------------------------

def install():
    """Thread exceptions to the log + Discord, native crashes to logs/crash_native.log."""
    global _native_file
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        _native_file = open(_path(_NATIVE), "a", encoding="utf-8")
        _native_file.write(f"\n=== session started {time.strftime('%Y-%m-%d %H:%M:%S')} (pid {os.getpid()}) ===\n")
        _native_file.flush()
        faulthandler.enable(file=_native_file, all_threads=True)
    except Exception as e:
        print(f"[crash_report] Couldn't enable the native crash log: {e}")

    def _thread_hook(args):
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread else "?"
        print(f"[uncaught in thread '{name}'] " + "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)).rstrip())
        try:
            info = describe(args.exc_value)
            from modules import notify
            notify.send(format_for_discord(info) + f"\n(in the background thread '{name}' - the run itself "
                        f"may carry on)", category="problems", title=f"Error: {info['type']}", good=False,
                        fields=[("Last log lines", last_lines_block(8), False)])
        except Exception:
            pass

    threading.excepthook = _thread_hook


# --- did the last session end normally? -------------------------------------------------

def run_started(mode):
    try:
        with open(_path(_MARKER), "w", encoding="utf-8") as f:
            json.dump({"mode": mode, "started": time.time(), "pid": os.getpid()}, f)
    except Exception as e:
        print(f"[crash_report] Couldn't write the run marker: {e}")


def run_ended():
    try:
        os.remove(_path(_MARKER))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[crash_report] Couldn't remove the run marker: {e}")


def _previous_session_tail(n=15):
    """The last lines the PREVIOUS session wrote to the log file (before this launch's header)."""
    try:
        with open(logger.get_log_path() or _path("macro_slop.log"), "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-2000:]
    except Exception:
        return []
    def is_header(l):
        return "session started." in l or "=====" in l or not l.strip()

    # Step back over THIS launch's header block (separator + "session started" lines), then
    # take what came before it, back to the previous session's own header.
    end = len(lines)
    seen_header = False
    while end > 0 and (is_header(lines[end - 1]) or not seen_header):
        if "session started." in lines[end - 1]:
            seen_header = True
        elif seen_header and not is_header(lines[end - 1]):
            break
        end -= 1
    start = end
    while start > 0 and "session started." not in lines[start - 1]:
        start -= 1
    section = lines[start:end]
    section = [l.rstrip() for l in section if not is_header(l) and "[vision]" not in l]
    return section[-n:]


# What a dump that really killed the process starts with. On Windows faulthandler also
# writes a stack for exceptions that were caught and handled (C++ exceptions inside Qt or
# pywin32 - "Windows fatal exception: code 0xe06d7363" and the like), which ended nothing;
# those must not be reported as the crash.
_FATAL_MARKERS = ("Fatal Python error", "access violation", "stack overflow", "illegal instruction",
                  "in page error", "Segmentation fault")


def _native_dump_since(t):
    """The previous session's native crash dump, if it holds a genuinely fatal one."""
    try:
        with open(_path(_NATIVE), "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        parts = text.split("=== session started")
        prev = parts[-2] if len(parts) >= 2 else ""
        body = prev.split("===", 1)[-1].strip() if "===" in prev else prev.strip()
        # The last fatal dump in that session's section, from its marker line onwards.
        lines = body.splitlines()
        for i in range(len(lines) - 1, -1, -1):
            if any(m.lower() in lines[i].lower() for m in _FATAL_MARKERS):
                return "\n".join(lines[i:i + 30])
        return ""
    except Exception:
        return ""


def check_previous_session():
    """
    Run once at launch. If the last run never ended normally, logs it and tells Discord,
    with what that session was doing last. Removes the marker either way.
    """
    try:
        with open(_path(_MARKER), "r", encoding="utf-8") as f:
            marker = json.load(f)
    except FileNotFoundError:
        return
    except Exception:
        marker = {}
    run_ended()

    started = float(marker.get("started") or 0)
    mode = marker.get("mode") or "?"
    tail = _previous_session_tail()
    native = _native_dump_since(started) if started else ""
    from modules.stats import format_duration
    how_long = ""
    try:
        # When it died = the timestamp on the last line that session logged. (Not the log
        # file's modified time: this launch has already written its own header to it.)
        last_ts = time.mktime(time.strptime(tail[-1][:19], "%Y-%m-%d %H:%M:%S")) if tail else 0
        if started and last_ts >= started:
            how_long = f" after about {format_duration(last_ts - started)}"
    except Exception:
        pass

    print(f"[crash_report] The previous run ({mode}) didn't end normally{how_long} - the app was closed, "
          f"killed or crashed mid-run.")
    if native:
        print("[crash_report] It left a native crash dump (logs/crash_native.log):")
        for line in native.splitlines()[:25]:
            print(f"    {line}")

    cause = ("The app crashed hard (native crash) - the stack it left is attached." if native else
             "No error was logged: the window was closed, the process was killed (PC sleep/shutdown, "
             "Windows update, out of memory, antivirus) or it crashed without Python seeing it.")
    text = "\n".join(tail)
    while len(text) > 1000 and tail:
        tail = tail[1:]
        text = "\n".join(tail)
    fields = [("Mode", mode, True)]
    if text:
        fields.append(("Last thing it logged", f"```\n{text}\n```", False))
    if native:
        fields.append(("Native crash", f"```\n{native[:950]}\n```", False))
    try:
        from modules import notify
        notify.send(f"**The last run ended without stopping properly{how_long}.**\n{cause}",
                    category="problems", title="Last run ended unexpectedly", good=False, fields=fields)
    except Exception as e:
        print(f"[notify] Could not send the previous-session message: {e}")


# --- sending the evidence to another PC -------------------------------------------------

def bundle_for_sharing(max_bytes=9 * 1024 * 1024, max_shots=6):
    """
    One zip (bytes) with what's needed to see what happened on THIS PC from another one:
    the log (+ the previous log file), the native crash log and the newest debug screenshots
    (as JPG). Kept under max_bytes - Discord's upload limit for bots is 10 MB - by dropping
    screenshots first, then the older log. Used by the Discord /logfile command (asked for
    2026-10-08: the logs that matter are always on the other laptop).
    """
    import glob
    import io
    import zipfile
    import cv2

    log_path = logger.get_log_path() or _path("macro_slop.log")

    def build(shots, with_previous_log):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            if os.path.isfile(log_path):
                z.write(log_path, "logs/macro_slop.log")
            if with_previous_log and os.path.isfile(log_path + ".1"):
                z.write(log_path + ".1", "logs/macro_slop.log.1")
            if os.path.isfile(_path(_NATIVE)):
                z.write(_path(_NATIVE), "logs/crash_native.log")
            for shot_path in shots:
                img = cv2.imread(shot_path)
                if img is None:
                    continue
                ok, jpg = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                if ok:
                    z.writestr("debug/" + os.path.splitext(os.path.basename(shot_path))[0] + ".jpg", jpg.tobytes())
        return buf.getvalue()

    shots = sorted(glob.glob(os.path.join("debug", "*.png")), key=os.path.getmtime, reverse=True)[:max_shots]
    data = b""
    for n_shots in range(len(shots), -1, -1):
        data = build(shots[:n_shots], True)
        if len(data) <= max_bytes:
            return data
    return build([], False)
