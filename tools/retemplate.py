# tools/retemplate.py
"""
Re-captures templates at the EMBEDDED game size, so the macro can wrap Roblox inside
its window again without losing template matching.

Why this exists
---------------
Embedding shrinks the game (config.DOCK_GAME_WIDTH x DOCK_GAME_HEIGHT). vision.py
then shrinks each reference-resolution template by the same ratio before matching,
which works for artwork and fails for text: Roblox re-renders small text with its own
hinting rather than drawing a scaled-down copy of the big glyphs. Measured on one
screen, story_card.png scored 0.61 shrunk but 0.98 as a same-size capture.

So instead of shrinking a big template, this captures a real one at the small size.

How it works
------------
Per screen you show it:
  1. Pins the game the way the bot runs it un-embedded, where matching is reliable,
     and works out which templates are on this screen.
  2. Re-pins at the embedded size and looks for each element again - but only in a
     small window around where the native sighting says it should be. Searching the
     whole frame does not work: the shrunken text scores so poorly that unrelated
     scenery can out-score the real button.
  3. Crops that rectangle out of the embedded screenshot and saves it as the variant.
  4. Screens it twice before keeping it - against the shrunken reference artwork (is
     this even the right thing?) and against a fresh screenshot (is it stable?).
     Both are needed: the second alone will happily certify a patch of sky, because
     sky is extremely stable.

Output goes to assets/templates@<W>x<H>/, mirroring assets/templates/. vision.py
picks those up automatically whenever the client is that size, and ignores them
otherwise, so nothing changes until the game is actually embedded.

The embedded size is identical on every monitor, which is the real prize here: these
variants are correct on any machine, unlike reference-resolution templates that only
suit whatever the capture machine's native size happened to be.

Usage: python tools/retemplate.py
       Navigate to a screen, press SPACE. Repeat. ESC when done.
"""
import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must run before any win32 window call below - see dpi_awareness.py. This tool pins
# the window twice per screen (native, then embedded) via the same MoveWindow/
# GetClientRect calls gui.py guards against running DPI-unaware, and needs the same
# guard for the same reason: an unaware process's idea of "1600x900" and mss's can
# silently stop being the same number of physical pixels under display scaling.
import dpi_awareness
DPI_AWARENESS_RESULT = dpi_awareness.set_dpi_awareness()

import cv2
import win32gui
from pynput import keyboard

import config
import vision
from input_controller import (_find_roblox_hwnd, focus_roblox_window,
                              pin_roblox_borderless, restore_roblox_window)

# Confidence at native size above which a template counts as "on this screen".
# Present templates measure 0.86-0.97 there; absent ones sat at 0.28-0.50 across a
# full 66-template sweep, so this sits in open space between the two populations.
PRESENT_AT_NATIVE = 0.78

# How far from the position predicted off the native sighting the element is allowed
# to actually be, in embedded pixels. A window rather than a point because the native
# client is not exactly 16:9 (a windowed game sits above the taskbar) while the
# embedded one is, so the two do not map perfectly onto each other.
#
# Searching only this window is what keeps a crop honest. Searching the WHOLE frame
# with the shrunken template is what produced a "Select Stage" template containing a
# patch of sky: the shrunken text scores so badly that some unrelated background can
# out-score the real button, and nothing downstream noticed.
SEARCH_PAD = 70

# A finished crop is compared against the shrunken reference artwork. It will not
# score highly - if it did, this tool would not need to exist - but the right element
# still correlates far better than an arbitrary piece of scenery does.
MIN_SIMILARITY = 0.50

# A freshly cropped variant re-matched against a new screenshot of the same screen
# should be near-perfect. Anything less means the element moved or animated between
# the two captures, and the crop is not worth keeping.
VERIFY_AT_LEAST = 0.85


def _all_templates():
    out = []
    for p in glob.glob("assets/templates/**/*.png", recursive=True):
        out.append(p.replace(os.sep, "/"))
    return sorted(out)


def _best(img, tpl):
    """(confidence, (x, y)) of the best match, or (-1, None) if the template is bigger."""
    if tpl.shape[0] > img.shape[0] or tpl.shape[1] > img.shape[1]:
        return -1.0, None
    m = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
    _, mx, _, ml = cv2.minMaxLoc(m)
    return mx, ml


def _macro_is_running():
    """
    True if a Macro Slop instance is up, either the built exe or gui.py from source.

    Detected by its window rather than by process name, because the source form shows
    up as a generic "python.exe" that cannot be told apart from this tool.
    """
    found = []

    def _check(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == "Macro Slop":
            found.append(hwnd)
        return True

    try:
        win32gui.EnumWindows(_check, None)
    except Exception:
        return False
    return bool(found)


def _pin_native():
    _, client = focus_roblox_window(pin=True)
    time.sleep(1.2)
    return client


def _pin_embedded():
    client = pin_roblox_borderless(config.DOCK_GAME_WIDTH, config.DOCK_GAME_HEIGHT, 100, 100)
    time.sleep(1.8)
    return client


def capture_one_screen(templates, out_dir, done, only=None):
    """
    Re-captures every template visible on whatever screen is showing. Returns count.

    `only` restricts the whole pass to templates whose path contains one of the given
    substrings. That is an escape hatch for genuinely ambiguous screens: the raid
    difficulty tiles and the story act tiles are differently-framed crops of similar
    artwork, so on the raid screen act_2.png outscores difficulty_2.png on its own
    tile and the clash rule below hands it the spot. Narrowing the pass to "raids"
    settles it by hand, which beats a heuristic that could just as easily give
    next_stage.png a crop of the Select Stage button - a mistake that is silent,
    permanent, and much more damaging than a missing template.
    """
    native = _pin_native()
    if native is None:
        print("  ERROR: couldn't pin the Roblox window.")
        return 0
    shot = vision.capture_screen()

    # Detected over EVERY template, including ones already captured. They take no
    # further part in capturing, but they must still be able to win a contested spot
    # below - otherwise an already-done template silently stops defending its own
    # button and a lookalike walks off with it.
    present = []
    for path in templates:
        if only and not any(k in path for k in only):
            continue
        tpl = vision._load_template(path, scale=1.0)
        conf, loc = _best(shot, tpl)
        if conf >= PRESENT_AT_NATIVE:
            present.append((path, conf, loc))

    # Two templates claiming the SAME piece of screen means one of them is a
    # lookalike, not a second sighting - "Next Stage" and "Select Stage" are both
    # green pills with bold white text and match each other at ~0.86. Without this,
    # next_stage.png gets a variant cropped from the Select Stage button and the bot
    # can no longer tell the two apart at all. Highest confidence wins the spot.
    present.sort(key=lambda e: -e[1])
    kept = []
    for path, conf, loc in present:
        t = vision._load_template(path, scale=1.0)
        rect = (loc[0], loc[1], loc[0] + t.shape[1], loc[1] + t.shape[0])
        clash = None
        for kpath, _, _, krect in kept:
            if not (rect[2] <= krect[0] or rect[0] >= krect[2]
                    or rect[3] <= krect[1] or rect[1] >= krect[3]):
                clash = kpath
                break
        if clash:
            print(f"    (ignoring {os.path.basename(path)} at {conf:.3f} - same spot as "
                  f"{os.path.basename(clash)})")
            continue
        kept.append((path, conf, loc, rect))
    present = [(p_, c_, l_) for p_, c_, l_, _ in kept]

    if not present:
        print("  Nothing recognised on this screen.")
        return 0

    print(f"  {len(present)} template(s) on this screen:")
    for path, conf, _ in present:
        mark = " (already captured)" if path in done else ""
        print(f"    {os.path.basename(path):34s} {conf:.3f}{mark}")

    nat_h, nat_w = shot.shape[:2]

    embedded = _pin_embedded()
    if embedded is None:
        print("  ERROR: couldn't pin the game to the embedded size.")
        return 0
    small = vision.capture_screen()
    emb_h, emb_w = small.shape[:2]

    crops = {}
    for path, _, nat_loc in present:
        # Something already captured only needs another look if the ones on file fail
        # HERE. That is the case an alternate exists for: the same element drawn
        # differently (an act tile while it is the selected act, a toggle mid-press),
        # which no single crop can cover.
        if path in done:
            hit = vision.find_template(small, path, config.MATCH_THRESHOLD)
            if hit:
                continue
            print(f"    {os.path.basename(path)} is captured but doesn't match this "
                  f"state - taking an alternate")

        tpl = vision._load_template(path, scale=config.SCALE)  # reference art, shrunk
        h, w = tpl.shape[:2]

        # Where the native sighting says it should be, in embedded pixels.
        #
        # Measured from the CENTRE with a single uniform ratio, not by stretching each
        # axis to fit. The native client is not 16:9 (a windowed game loses height to
        # the taskbar) while the embedded one is, so an independent y ratio quietly
        # skews everything away from the middle - which is how the difficulty tiles
        # ended up cropping scenery from ~50px off target. Roblox lays its menus out
        # centred and scales them uniformly, so this matches what it actually does.
        ratio = emb_w / nat_w
        px = int(round(emb_w / 2 + (nat_loc[0] - nat_w / 2) * ratio))
        py = int(round(emb_h / 2 + (nat_loc[1] - nat_h / 2) * ratio))
        x0 = max(0, px - SEARCH_PAD)
        y0 = max(0, py - SEARCH_PAD)
        x1 = min(emb_w, px + w + SEARCH_PAD)
        y1 = min(emb_h, py + h + SEARCH_PAD)
        window = small[y0:y1, x0:x1]
        if window.shape[0] < h or window.shape[1] < w:
            print(f"    SKIP {os.path.basename(path)} - no room to search near "
                  f"the predicted spot")
            continue

        conf, loc = _best(window, tpl)
        if loc is None:
            continue
        gx, gy = x0 + loc[0], y0 + loc[1]
        crop = small[gy:gy + h, gx:gx + w].copy()

        sim = _best(crop, tpl)[0] if crop.shape[:2] == tpl.shape[:2] else -1.0
        if sim < MIN_SIMILARITY:
            print(f"    SKIP {os.path.basename(path)} - crop looks like "
                  f"the wrong thing (similarity {sim:.3f})")
            continue
        crops[path] = (crop, (gx, gy), sim)

    # Verify against a SECOND screenshot: a crop taken from the same frame it is
    # matched against would score 1.0 by construction and prove nothing.
    time.sleep(1.5)
    check = vision.capture_screen()

    saved = 0
    for path, (crop, loc, sim) in crops.items():
        conf, _ = _best(check, crop)
        name = os.path.basename(path)
        if conf < VERIFY_AT_LEAST:
            print(f"    REJECT {name} - verified only {conf:.3f} (animating?)")
            continue
        dest = os.path.join(out_dir, os.path.relpath(path, "assets/templates"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(dest):
            stem, ext = os.path.splitext(dest)
            n = 2
            while os.path.exists(f"{stem}__{n}{ext}"):
                n += 1
            dest = f"{stem}__{n}{ext}"
            name += f"  (alternate #{n})"
        cv2.imwrite(dest, crop)
        done.add(path)
        saved += 1
        print(f"    OK  {name:32s} {crop.shape[1]}x{crop.shape[0]}  "
              f"verified {conf:.3f}  similarity {sim:.3f}")

    _pin_native()
    return saved


def main():
    print(f"DPI awareness: {DPI_AWARENESS_RESULT}")
    only = [a for a in sys.argv[1:] if not a.startswith("-")] or None
    if only:
        print(f"(restricted to templates matching: {', '.join(only)})")

    if _macro_is_running():
        print("ERROR: Macro Slop is still running - close it first (the Quit button in")
        print("       its sidebar), then run this again.")
        print()
        print("       Why this is fatal rather than merely untidy: the macro re-docks")
        print("       Roblox every 2 seconds, and this tool needs to hold the window at")
        print("       native size long enough to recognise what is on screen. With both")
        print("       running they fight over the same window, every screen detects zero")
        print("       templates, and a full session captures nothing at all.")
        return

    hwnd = _find_roblox_hwnd()
    if hwnd is None:
        print("ERROR: no Roblox window found. Start Roblox and try again.")
        return
    if win32gui.IsIconic(hwnd):
        print("ERROR: Roblox is minimized. Bring it back up and try again.")
        return

    w, h = config.DOCK_GAME_WIDTH, config.DOCK_GAME_HEIGHT
    out_dir = f"assets/templates@{w}x{h}"
    os.makedirs(out_dir, exist_ok=True)

    templates = _all_templates()
    done = set()
    for path in templates:
        dest = os.path.join(out_dir, os.path.relpath(path, "assets/templates"))
        if os.path.exists(dest):
            done.add(path)

    print("=== Re-capture templates at the embedded size ===")
    print(f"Embedded size: {w}x{h}   ->  {out_dir}/")
    print(f"{len(templates)} templates total, {len(done)} already done.\n")
    print("Show the game a screen, then press SPACE. ESC when you're finished.")
    print("The window will resize twice per capture and settle back - that's expected.\n")

    placement = win32gui.GetWindowPlacement(hwnd)
    state = {"go": False, "quit": False}

    def on_press(key):
        if key == keyboard.Key.esc:
            state["quit"] = True
            return False
        if key == keyboard.Key.space:
            state["go"] = True
            return False

    try:
        while True:
            state["go"] = False
            remaining = len(templates) - len(done)
            print(f"--- waiting  ({len(done)}/{len(templates)} done, {remaining} left) "
                  f"- SPACE to capture, ESC to finish ---")
            with keyboard.Listener(on_press=on_press) as listener:
                listener.join()
            if state["quit"]:
                break
            n = capture_one_screen(templates, out_dir, done, only=only)
            print(f"  -> {n} saved this screen\n")
    finally:
        restore_roblox_window()
        try:
            win32gui.SetWindowPlacement(hwnd, placement)
        except Exception:
            pass

    print(f"\n=== Finished: {len(done)}/{len(templates)} templates have variants ===")
    missing = [p for p in templates if p not in done]
    if missing:
        print("Still missing (show these screens and run again):")
        for p in missing:
            print(f"  {p}")
    else:
        print("All templates captured - the embedded layout can be turned on.")


if __name__ == "__main__":
    main()
