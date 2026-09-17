# modules/health.py
"""
Run-health primitives: is the game still there, have we been waiting impossibly
long, keep the session from being idle-kicked, and leave evidence behind when
something goes wrong.

These exist because every wait in this bot is an open-ended "poll until the thing
I expect shows up" loop. That's correct while the thing is genuinely coming, and
catastrophic when it isn't - a moved button after a game update, an unhandled
screen, a client that died - because the loop has no way to tell "still loading"
from "will never arrive" and just stalls silently until a human notices. For a bot
meant to run unattended for days, that failure is worse than crashing.

Nothing here decides policy. modules/polling.py calls these and acts on them.
"""
import os
import time
import cv2

import config
import input_controller
from vision import capture_screen


def roblox_alive():
    """
    True while a Roblox window still exists.

    Disconnect detection (modules/reconnect.py) keys off the Reconnect popup, which
    only covers the case where the client is alive and telling you it lost the
    server. If Roblox crashes, is closed, or exits outright, no popup ever renders -
    so every template match simply fails forever and every poll loop spins. This is
    the check that tells those two situations apart.
    """
    return input_controller.roblox_is_running()


def save_debug_screenshot(label, screenshot=None):
    """
    Writes the current screen to config.DEBUG_DIR as <timestamp>_<label>.png and
    returns the path (or None if it couldn't be written).

    The whole point of stopping on a stuck loop is to preserve what was on screen at
    the time. Without the frame, "it stopped waiting for start_game" is unactionable;
    with it, the cause is usually obvious at a glance, and it's also the raw material
    for a new/retuned template.
    """
    try:
        os.makedirs(config.DEBUG_DIR, exist_ok=True)
        if screenshot is None:
            screenshot = capture_screen()
        safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
        filename = f"{time.strftime('%Y%m%d_%H%M%S')}_{safe_label}.png"
        path = os.path.join(config.DEBUG_DIR, filename)
        cv2.imwrite(path, screenshot)
        return path
    except Exception as e:
        print(f"[health] Could not save a debug screenshot: {e}")
        return None


def jpg_bytes(screenshot=None, quality=82):
    """
    The current (or given) screen, JPEG-encoded in memory - for attaching to a
    Discord notification (modules.notify.send's image_bytes), which needs raw
    bytes to upload rather than a path on disk.

    JPEG rather than PNG: a 1600x900 game frame runs 150-400KB at quality 82,
    comfortably clear of Discord's attachment cap (see notify._MAX_ATTACHMENT_BYTES)
    and small enough to not noticeably delay the notification queue; the same frame
    as a lossless PNG can run 3-5x that for a screenshot nobody is pixel-peeping.

    Returns None (rather than raising) on any failure, since a notification missing
    its picture is far better than a notification that never sends at all - see
    notify.py's own "must never break a run" rule, which this exists to serve.
    """
    try:
        if screenshot is None:
            screenshot = capture_screen()
        ok, buf = cv2.imencode(".jpg", screenshot, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None
    except Exception as e:
        print(f"[health] Could not encode a screenshot for Discord: {e}")
        return None


def anti_idle_tick():
    """
    Sends a harmless double-click if nothing has touched the game for
    config.ANTI_IDLE_SECONDS, and returns True when it fired.

    Roblox kicks a player who sends no input for ~20 minutes. The bot is genuinely
    input-free for exactly that kind of stretch during a long Auto Play match - it
    can watch for 10+ minutes without clicking anything - so an unattended overnight
    run would get idle-kicked mid-farm for no other reason.

    Deliberately driven by input_controller's last-input timestamp rather than a
    timer of its own, so any real click, keystroke or scroll the bot makes counts as
    activity and this never fires during navigation - only when the bot really has
    been sitting still. The click point is config.ANTI_IDLE_X/Y, verified inert
    mid-match; click_at() marks input itself, so the timer resets automatically.
    """
    if input_controller.seconds_since_input() < config.ANTI_IDLE_SECONDS:
        return False

    print(f"[health] No input sent for {int(config.ANTI_IDLE_SECONDS)}s - sending an anti-idle "
          f"click at ({config.ANTI_IDLE_X}, {config.ANTI_IDLE_Y}) so Roblox doesn't idle-kick us.")
    input_controller.click_at(config.ANTI_IDLE_X, config.ANTI_IDLE_Y, clicks=2)
    return True


def measure_ui_scale(screenshot, template_paths, min_confidence=0.80, tolerance=0.02,
                      skip_confidence=0.92):
    """
    How much larger than expected this frame is drawing the UI, or None if unsure.

    Sweeps each template over sizes and keeps the size that matched best. A single
    template agreeing is not enough - a lone strong peak can be a coincidence, and
    adopting a wrong scale is worse than adopting none, since it corrupts click
    coordinates as well as matching. So at least two templates must independently peak
    at the same multiplier (within `tolerance`) and both clear `min_confidence` before
    anything is reported.

    Returns the multiplier relative to config.SCALE - 1.10 where the UI is 10% larger,
    which is what a laptop drawing an identical 1136x639 client actually did. Deliberately
    reports rather than applies: the caller decides whether to trust it, and it lands in
    the log either way.

    Tries a cheap shortcut first: matching each template ONCE, at the size it would
    already be matched at right now (honouring any native variant, exactly like a real
    find_template() call). If at least two independently clear `skip_confidence`, the
    machine's UI_SCALE is evidently already correct and the expensive sweep below is
    skipped entirely.

    This is not a minor saving. Measured against a real 1600x900 lobby frame, the full
    sweep (~40 resized match attempts per template) took 7.6 seconds - almost the whole
    gap between "Detecting current game state" and "Lobby detected!" in a real run's
    log, paid on EVERY startup regardless of whether anything is actually wrong. The
    2026-09-12 REFERENCE_WIDTH change made this newly noticeable rather than newly
    slow: SCALE is 1.0 on every normal run now, so the templates plainly-visible
    on-screen genuinely do match near-perfectly at the current scale (items.png
    measured 0.997, play_btn.png 1.000 on that same frame) - which is exactly the
    condition this shortcut exists to recognise and shortcut through, rather than
    re-discovering it the slow way with a decoy sweep of 40 sizes either side of the
    one that already works.
    """
    from vision import _load_template, match_best

    quick_hits = 0
    for path in template_paths:
        try:
            tpl = _load_template(path)  # same sizing find_template() would use right now
        except Exception:
            continue
        if tpl.shape[0] > screenshot.shape[0] or tpl.shape[1] > screenshot.shape[1]:
            continue
        conf = float(match_best(screenshot, tpl)[0])
        if conf >= skip_confidence:
            quick_hits += 1
    if quick_hits >= 2:
        return None

    peaks = []
    for path, best_scale, best_conf, _ in probe_scales(screenshot, template_paths):
        if best_conf >= min_confidence:
            peaks.append((path, best_scale / config.SCALE, best_conf))

    if len(peaks) < 2:
        return None

    for i, (_, mult_a, _) in enumerate(peaks):
        agree = [m for _, m, _ in peaks if abs(m - mult_a) <= tolerance]
        if len(agree) >= 2:
            return sum(agree) / len(agree)
    return None


def measure_machine_speed():
    """
    How much slower this PC is than the one the click timings were tuned on, as a
    factor from 1.0 (as fast or faster) up to 3.0.

    A PC that is slow at image matching is, in practice, also one where Roblox draws
    fewer frames - and Roblox only notices the mouse is over a button on a new frame.
    A hover and press that fit comfortably inside one frame on a fast PC can fall
    entirely between two frames on an old laptop, so the click "happens" and Roblox
    never sees it. The factor stretches those waits (input_controller.click_at).

    Measured on the same kind of work the bot does all day: full-resolution matches on
    a 1600x900 frame. A modern desktop takes ~0.04 s per match - and ~0.07 s while busy
    with something else, which must NOT count as slow, or every busy PC would click
    sluggishly. So nothing stretches until a match takes 0.12 s, a level only a genuinely
    old machine reaches; from there the factor grows with it, capped at 3x.
    """
    import cv2
    import numpy as np
    rng = np.random.default_rng(7)
    frame = rng.integers(0, 255, (900, 1600, 3), dtype=np.uint8)
    tpl = frame[400:460, 700:860].copy()
    cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)  # warm-up
    times = []
    for _ in range(3):
        t = time.perf_counter()
        cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
        times.append(time.perf_counter() - t)
    per_match = sorted(times)[1]
    return max(1.0, min(3.0, per_match / 0.12)), per_match


def probe_scales(screenshot, template_paths, spread=0.20, step=0.01):
    """
    Asks "is this frame just a slightly different size than the templates expect?"

    Sweeps each template across a range of sizes against one frame and reports the size
    that scores best. Templates only match at the scale they were captured at, so a
    client even a couple of percent off makes a large template fail while a small one
    still squeaks through - which reads as "some buttons work and others don't" rather
    than as a sizing problem, and is exactly the pattern that is otherwise impossible
    to tell apart from a stale template without a screenshot to inspect by hand.

    Diagnostic only: it reports, it never changes SCALE. If every template agrees on a
    scale other than the current one, that number is the answer and belongs in the log
    where it can be read later, rather than in a screenshot someone has to send on.
    Returns [(template, best_scale, best_confidence, confidence_at_current_scale), ...].

    The sweep is centred on config.SCALE, not on 1.0. _load_template(scale=1.0) hands
    back the full-resolution REFERENCE master deliberately (an explicit scale never
    substitutes a native variant - see vision._load_template), so the size that master
    needs to be matched at is SCALE, currently ~0.59. A fixed 0.80-1.20 window never
    reached it: every reported "best" came from sizes that were all wrong, and
    "confidence at the current scale" was recorded only when the sweep hit SCALE, which
    it never did - so that column read a constant 0.000 no matter what was on screen.
    Both halves of the output were noise, which is worse than no diagnostic at all: it
    was read as "no size matches, the template must be stale".
    """
    import cv2
    from vision import _load_template, match_best

    centre = config.SCALE
    low, high = centre * (1.0 - spread), centre * (1.0 + spread)

    results = []
    for path in template_paths:
        try:
            base = _load_template(path, scale=1.0)
        except Exception:
            continue

        current = 0.0
        best = (0.0, centre)
        # Stepped outwards from SCALE rather than upwards from `low`, so that SCALE is
        # always itself one of the sizes tried. Walking a grid from an arbitrary start
        # steps straight over it, leaving the "at the current scale" figure measured at
        # whatever grid point happened to fall nearest - a number that reads as the
        # answer to "does it match right now?" while quietly being the answer to
        # something else.
        rungs = int(round((high - low) / (2 * step)))
        for k in range(-rungs, rungs + 1):
            pct = centre + k * step
            if pct <= 0:
                continue
            w = max(1, int(round(base.shape[1] * pct)))
            h = max(1, int(round(base.shape[0] * pct)))
            if h <= screenshot.shape[0] and w <= screenshot.shape[1]:
                interp = cv2.INTER_AREA if pct < 1.0 else cv2.INTER_CUBIC
                resized = cv2.resize(base, (w, h), interpolation=interp)
                conf = float(match_best(screenshot, resized)[0])
                if conf > best[0]:
                    best = (conf, pct)
                if k == 0:
                    current = conf

        results.append((path, best[1], best[0], current))
    return results


def report_missing_template(screenshot, label, template_path):
    """
    Saves the screen and runs a scale-sweep diagnostic for one template a navigation
    step expected to find and didn't - the click_map()/click_raid() analogue of
    gui.BotGUI._report_unrecognised_screen(), for callers in modules/ that have no
    GUI log to write to and so print directly (see logger.py - stdout is captured
    into the same log file either way).

    Without this, a failed click_map()/click_raid() left nothing behind but "Could
    not find 'x' on screen" - which cannot tell a stale template, a template that's
    the right art at the wrong size, and a genuinely different screen apart, and by
    the time anyone looks the live game has moved on and the frame is gone. Called
    from the sweep's failure path, so what it saves is the LAST frame the sweep
    looked at - the carousel wherever it ended up - not merely the first one.
    """
    path = save_debug_screenshot(label, screenshot)
    if path:
        print(f"[health] Saved what it actually saw to: {path}")

    for tpl, best_scale, best_conf, cur_conf in probe_scales(screenshot, [template_path]):
        name = os.path.basename(tpl)
        verdict = ""
        if best_conf >= 0.85 and abs(best_scale - config.SCALE) > 0.015:
            verdict = f"  <-- would MATCH at scale {best_scale:.3f}, not {config.SCALE:.3f}"
        elif best_conf < 0.6:
            verdict = "  <-- no size matches anywhere in this frame; template is stale, or it genuinely isn't on screen"
        else:
            verdict = "  <-- present-ish but under threshold at every size tried; probably not this template's card at all"
        print(f"[health]   {name:<28} now {cur_conf:.3f} | best {best_conf:.3f} @ scale {best_scale:.3f}{verdict}")


class StuckTimer:
    """
    Bounds how long one wait may run before it's treated as stuck rather than slow.

    Deliberately dumb: it doesn't try to recognise unexpected screens or reason about
    what's on them. A poll loop already knows exactly what it's waiting for, so
    "stuck" is just "this has run longer than any legitimate version of this wait
    could take". That's a timer on a loop that already exists, with no new failure
    modes of its own.

    timeout=None disables it entirely, for waits that are already bounded by their
    own shorter timeout.
    """

    def __init__(self, timeout, label):
        self.timeout = timeout
        self.label = label
        self.started = time.time()

    @property
    def elapsed(self):
        return time.time() - self.started

    def expired(self):
        return self.timeout is not None and self.elapsed >= self.timeout

    def report(self, screenshot=None, extra=None):
        """
        Logs why this looks stuck and saves the offending frame. Returns the
        screenshot path, or None.
        """
        print("=" * 70)
        print(f"[health] STUCK: '{self.label}' has been waiting {int(self.elapsed)}s "
              f"(limit {int(self.timeout)}s).")
        if extra:
            print(f"[health] {extra}")
        path = save_debug_screenshot(f"stuck_{self.label}", screenshot)
        if path:
            print(f"[health] Saved the screen at the moment it gave up to: {path}")
            print("[health] Open that image to see what was actually up - it's usually obvious,")
            print("[health] and it's what a corrected template would be re-cropped from.")
        print("=" * 70)
        return path
