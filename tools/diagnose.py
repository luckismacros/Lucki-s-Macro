# tools/diagnose.py
"""
One-shot environment report for "it works on my machine but not on this one".

Run it while Roblox is open and sitting in the lobby. It reports every number that
decides whether template matching can possibly work - monitor size, DPI scaling,
Roblox's real client rect, the derived scale - then captures the screen and scores
every template against it.

The point is to separate causes that look identical from the outside:

  * every template scores ~0.3 and the scale is not 1.0
        -> the window is a size the templates were never captured at
  * every template scores ~0.3 and the scale IS 1.0
        -> the templates are stale, or the window isn't where we think it is
  * some templates match and others don't
        -> those specific templates are stale, not an environment problem
  * scale is 1.0, templates match, but clicks miss
        -> the client area isn't at the screen's top-left

Usage:  python tools/diagnose.py            (writes diagnose_report.txt too)
"""
import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Match gui.py's DPI setup exactly - reading these numbers under different DPI
# awareness than the bot runs with would report a screen size the bot never sees.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import cv2
import mss
import win32api
import win32con
import win32gui

import config
import vision
from input_controller import _find_roblox_hwnd, pin_roblox_window

TEMPLATE_DIR = "assets/templates"
OUT = []


def say(line=""):
    print(line)
    OUT.append(line)


def main():
    say("=" * 78)
    say("MACRO SLOP - ENVIRONMENT DIAGNOSTIC")
    say("=" * 78)

    # --- screen ---
    say("\n[1] SCREEN")
    with mss.mss() as sct:
        mon = sct.monitors[1]
        say(f"  mss primary monitor      : {mon['width']}x{mon['height']} at ({mon['left']}, {mon['top']})")
    say(f"  win32 virtual screen     : {win32api.GetSystemMetrics(win32con.SM_CXSCREEN)}"
        f"x{win32api.GetSystemMetrics(win32con.SM_CYSCREEN)}")
    try:
        # 96 DPI == 100% scaling. Anything else means Windows is scaling the desktop.
        dpi = ctypes.windll.user32.GetDpiForSystem()
        say(f"  system DPI               : {dpi}  ({round(dpi / 96 * 100)}% display scaling)")
    except Exception:
        say("  system DPI               : unavailable")
    say(f"  reference this bot expects: {config.REFERENCE_WIDTH}x{config.REFERENCE_HEIGHT}")

    # --- roblox window ---
    say("\n[2] ROBLOX WINDOW")
    hwnd = _find_roblox_hwnd()
    if hwnd is None:
        say("  NOT FOUND - start Roblox and sit in the lobby, then run this again.")
        _write()
        return 1

    say(f"  title                    : {win32gui.GetWindowText(hwnd)!r}")
    wl, wt, wr, wb = win32gui.GetWindowRect(hwnd)
    cl, ct = win32gui.ClientToScreen(hwnd, (0, 0))
    _, _, cw, ch = win32gui.GetClientRect(hwnd)
    say(f"  outer window rect        : ({wl}, {wt}) to ({wr}, {wb})  = {wr - wl}x{wb - wt}")
    say(f"  CLIENT area (what counts): ({cl}, {ct})  = {cw}x{ch}")
    say(f"  chrome (borders+titlebar): {(wr - wl) - cw}x{(wb - wt) - ch}")
    if ch:
        say(f"  client aspect ratio      : {cw / ch:.4f}   (reference is "
            f"{config.REFERENCE_WIDTH / config.REFERENCE_HEIGHT:.4f})")

    # --- pin, exactly as the bot does it ---
    say("\n[3] PINNING (as the bot does at startup)")
    size = pin_roblox_window()
    if size is None:
        say("  PIN FAILED - see the message above.")
    else:
        say(f"  achieved client size     : {size[0]}x{size[1]}")
        say(f"  resulting config.SCALE   : {config.SCALE:.4f}")
        if config.SCALE == 1.0:
            say("  -> full reference size; templates are used at their captured size.")
        else:
            say(f"  -> templates are being shrunk to {config.SCALE * 100:.1f}% before matching.")
        cl2, ct2 = win32gui.ClientToScreen(hwnd, (0, 0))
        if (cl2, ct2) != (0, 0):
            say(f"  WARNING: client is at ({cl2}, {ct2}), not (0, 0). Every coordinate in "
                f"config.py is measured from the screen's top-left, so clicks will be off "
                f"by exactly this much.")

    # --- what the bot actually sees ---
    say("\n[4] TEMPLATE SCORES AGAINST THE CURRENT SCREEN")
    say("    (be in the LOBBY when running this)")
    shot = vision.capture_screen()
    say(f"  captured frame           : {shot.shape[1]}x{shot.shape[0]}")

    os.makedirs(config.DEBUG_DIR, exist_ok=True)
    shot_path = os.path.join(config.DEBUG_DIR, "diagnose_screen.png")
    cv2.imwrite(shot_path, shot)
    say(f"  saved to                 : {shot_path}")

    import glob
    paths = sorted(glob.glob(os.path.join(TEMPLATE_DIR, "*.png")))
    rows = []
    for p in paths:
        try:
            tpl = vision._load_template(p)
            if tpl.shape[0] > shot.shape[0] or tpl.shape[1] > shot.shape[1]:
                rows.append((-1.0, p, "template bigger than the screen"))
                continue
            r = cv2.matchTemplate(shot, tpl, cv2.TM_CCOEFF_NORMED)
            _, mx, _, loc = cv2.minMaxLoc(r)
            rows.append((float(mx), p, f"at {config.to_reference(loc[0], loc[1])}"))
        except Exception as e:
            rows.append((-1.0, p, f"ERROR {e}"))

    rows.sort(reverse=True)
    say(f"\n  {'template':<40}{'conf':>8}  {'needs':>7}   where")
    say("  " + "-" * 74)
    for conf, p, where in rows:
        need = config.effective_threshold(p, config.MATCH_THRESHOLD)
        mark = "OK  " if conf >= need else ("NEAR" if conf >= 0.5 else "MISS")
        say(f"  {os.path.basename(p):<40}{conf:8.3f}  {need:7.3f}   {mark} {where}")

    matched = sum(1 for c, _, _ in rows if c >= config.MATCH_THRESHOLD)
    say(f"\n  {matched} of {len(rows)} templates matched.")

    say("\n[5] READING THIS")
    if config.SCALE != 1.0 and matched == 0:
        say("  Nothing matched and the window is scaled. The templates were captured at")
        say(f"  {config.REFERENCE_WIDTH}x{config.REFERENCE_HEIGHT} and are being shrunk to fit this")
        say("  window - which only works if the game's UI shrinks by the same ratio. If the")
        say("  game keeps text or buttons at a fixed pixel size instead, no single scale can")
        say("  line them up, and the fix is a window at the reference size rather than a")
        say("  cleverer scale factor.")
    elif config.SCALE == 1.0 and matched == 0:
        say("  Nothing matched even at full size, so scaling isn't the problem. Either the")
        say("  screen isn't showing the lobby, the templates are stale, or the captured")
        say("  frame isn't the Roblox window - compare debug/diagnose_screen.png against")
        say("  what was actually on screen.")
    elif matched:
        say("  Some templates matched, so capture and scaling are working. Anything listed")
        say("  MISS above is either not currently on screen (expected - most templates only")
        say("  appear on their own screens) or genuinely stale.")

    _write()
    return 0


def _write():
    path = "diagnose_report.txt"
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(OUT))
        print(f"\nFull report written to {path} - send that file plus "
              f"{config.DEBUG_DIR}/diagnose_screen.png")
    except Exception as e:
        print(f"(couldn't write {path}: {e})")


if __name__ == "__main__":
    sys.exit(main())
