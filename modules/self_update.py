# modules/self_update.py
"""
Updates the app in place: download the new release zip, swap the program files, restart.
Asked for 2026-10-29: "I can't always go on GitHub, upload a release, go on my laptop,
download... it would be so much better if it updated itself (with permission)."

How it works (no script, no admin rights, no extra program):

  1. download_and_stage() - the running app downloads the release's zip into
     <app folder>/_update/ and extracts it there (the run can keep going meanwhile).
  2. launch_installer()   - starts the NEW version's exe from _update/ with
     --apply-update, then the app closes itself.
  3. apply_from_argv()    - (that new exe) waits for the old app to exit, moves the old
     program files into _update/old/, copies the new ones in, and starts the updated app.
     Anything that fails puts the old files back and restarts the old version instead.
  4. finish_on_startup()  - (the updated app) clears _update/ away and says what happened,
     and which run to carry on with if the update interrupted one.

Your data is never touched: the release zip has none of it (presets, recordings,
settings, logs - see USER_DATA), and only the names the zip contains are replaced.

Only works in the built app - running from source (python app_qt.py) there's no exe to
swap, so supported() is False there and the UI falls back to the release page.
"""
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

EXE_NAME = "Lucki's Macro.exe"
UPDATE_DIR = "_update"
# Never replaced or moved, even if a zip ever did contain them.
USER_DATA = {"presets", "movement_presets", "challenge_links.json", "settings.json", "settings.json.bak",
             "settings.json.tmp", "logs", "debug", UPDATE_DIR}
ASSET_RE = re.compile(r"^Luckis_Macro_v\.?(\d[\d.]*)\.zip$", re.I)

_DETACHED = 0x00000008 | 0x00000200        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
_CHUNK = 1 << 20


class UpdateError(Exception):
    pass


def supported():
    return getattr(sys, "frozen", False) and os.path.basename(sys.executable).lower() == EXE_NAME.lower()


def app_dir():
    return os.path.dirname(os.path.abspath(sys.executable))


def _update_dir(base=None):
    return os.path.join(base or app_dir(), UPDATE_DIR)


# --- 1. download ----------------------------------------------------------------------

def download_and_stage(info, progress=None):
    """
    Downloads info["download_url"] (from update_check.check_for_update) and extracts it.
    progress(fraction) is called as it goes. Returns the folder holding the new exe;
    raises UpdateError with a readable reason.
    """
    url, size = info.get("download_url"), int(info.get("size") or 0)
    if not url:
        raise UpdateError("this release has no app zip attached")
    work = _update_dir()
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    zip_path = os.path.join(work, "download.zip")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "LuckisMacro-Updater"})
        with urllib.request.urlopen(request, timeout=30) as resp, open(zip_path + ".part", "wb") as out:
            total = size or int(resp.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = resp.read(_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                got += len(chunk)
                if progress and total:
                    progress(min(1.0, got / total))
    except Exception as e:
        raise UpdateError(f"the download failed ({type(e).__name__}: {e})")
    if size and got != size:
        raise UpdateError(f"the download stopped early ({got / 1e6:.0f} of {size / 1e6:.0f} MB)")
    os.replace(zip_path + ".part", zip_path)

    new_root = os.path.join(work, "new")
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(new_root)
    except Exception as e:
        raise UpdateError(f"the downloaded zip is damaged ({e})")
    finally:
        try:
            os.remove(zip_path)
        except OSError:
            pass
    for root, _dirs, files in os.walk(new_root):
        if EXE_NAME in files:
            return root
    raise UpdateError(f"the zip has no {EXE_NAME} in it")


# --- 2. hand over to the new exe -------------------------------------------------------

def launch_installer(staged_dir, from_version, resume=None):
    """Starts the new exe in install mode. The caller must close the app right after."""
    args = [os.path.join(staged_dir, EXE_NAME), "--apply-update", app_dir(),
            "--pid", str(os.getpid()), "--from", str(from_version)]
    if resume:
        args += ["--resume", resume]
    subprocess.Popen(args, cwd=staged_dir, creationflags=_DETACHED, close_fds=True)


# --- 3. the install itself (runs in the NEW exe) ---------------------------------------

def _arg(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def _wait_for_exit(pid, seconds):
    SYNCHRONIZE = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return True                          # already gone
    try:
        return ctypes.windll.kernel32.WaitForSingleObject(handle, int(seconds * 1000)) == 0
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _retry(fn, what, log, tries=20):
    # Antivirus scanners and Windows itself can hold a just-closed exe/dll for a moment.
    for attempt in range(tries):
        try:
            return fn()
        except OSError as e:
            if attempt == tries - 1:
                raise OSError(f"{what}: {e}")
            time.sleep(0.5)


def _copy(src, dst):
    if os.path.isdir(src):
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)


def _remove(path):
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.remove(path)


def _start(exe, argv=()):
    subprocess.Popen([exe, *argv], cwd=os.path.dirname(exe), creationflags=_DETACHED, close_fds=True)


def apply_from_argv(argv):
    """
    Entry point for `Lucki's Macro.exe --apply-update <app folder> --pid N --from X
    [--resume page]` - see app_qt.py. Returns the process exit code.
    """
    import config
    target = _arg(argv, "--apply-update")
    pid = int(_arg(argv, "--pid", "0") or 0)
    from_version = _arg(argv, "--from", "?")
    resume = _arg(argv, "--resume")
    restart = "--no-restart" not in argv       # testing: install, don't open the window
    src = app_dir()
    work = _update_dir(target)
    os.makedirs(os.path.join(target, "logs"), exist_ok=True)
    log_file = open(os.path.join(target, "logs", "update.log"), "a", encoding="utf-8")

    def log(msg):
        log_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
        log_file.flush()

    def report(ok, error=None):
        with open(os.path.join(work, "result.json"), "w", encoding="utf-8") as f:
            json.dump({"ok": ok, "from": from_version, "to": config.APP_VERSION, "resume": resume,
                       "error": error}, f)

    log(f"Updating {target} from v{from_version} to v{config.APP_VERSION}.")
    target_exe = os.path.join(target, EXE_NAME)
    if not os.path.isdir(target) or os.path.normcase(os.path.abspath(target)) == os.path.normcase(src):
        log("Bad target folder - nothing done.")
        return 1
    if pid and not _wait_for_exit(pid, 90):
        log("The old app didn't close within 90s - update cancelled, nothing changed.")
        report(False, "the old window didn't close")
        return 1

    old = os.path.join(work, "old")
    shutil.rmtree(old, ignore_errors=True)
    os.makedirs(old, exist_ok=True)
    moved, installed = [], []
    try:
        for name in os.listdir(src):
            if name in USER_DATA:
                continue
            dst = os.path.join(target, name)
            if os.path.lexists(dst):
                # A rename (same folder tree): instant, and all-or-nothing - shutil.move would
                # fall back to copying a locked file and leave half of it behind.
                _retry(lambda: os.replace(dst, os.path.join(old, name)), f"moving out {name}", log)
                moved.append(name)
            _retry(lambda: _copy(os.path.join(src, name), dst), f"copying in {name}", log)
            installed.append(name)
    except Exception as e:
        log(f"FAILED: {e} - putting the old version back.")
        for name in installed:
            try:
                _remove(os.path.join(target, name))
            except Exception as e2:
                log(f"  couldn't remove new {name}: {e2}")
        for name in moved:
            try:
                _retry(lambda: os.replace(os.path.join(old, name), os.path.join(target, name)),
                       f"restoring {name}", log)
            except Exception as e2:
                log(f"  couldn't restore {name}: {e2}")
        report(False, str(e))
        if restart and os.path.exists(target_exe):
            _start(target_exe)
        log_file.close()
        return 1

    log(f"Installed {len(installed)} items - starting v{config.APP_VERSION}.")
    report(True)
    if restart:
        _start(target_exe)
    log_file.close()
    return 0


# --- 4. after the restart --------------------------------------------------------------

def finish_on_startup():
    """
    In the freshly started app: what the last update did ({"ok", "from", "to", "resume",
    "error"}) or None, and _update/ cleared away (in the background - the installer exe
    in it can take a moment to exit).
    """
    if not supported():
        return None
    work = _update_dir()
    result_path = os.path.join(work, "result.json")
    result = None
    if os.path.isfile(result_path):
        try:
            with open(result_path, encoding="utf-8") as f:
                result = json.load(f)
        except Exception:
            result = None
    if os.path.isdir(work):
        def clean():
            for _ in range(10):
                time.sleep(3.0)
                shutil.rmtree(work, ignore_errors=True)
                if not os.path.exists(work):
                    return
        import threading
        threading.Thread(target=clean, daemon=True).start()
    return result
