# vision.py
"""
Screen capture and template matching utilities.
"""
import mss
import numpy as np
import os
import cv2
import config

_alpha_warned = set()
_template_cache = {}

def capture_screen(region=None):
    """
    Capture Roblox's client area and return it as a BGR numpy array (OpenCV's native
    format).

    Grabs exactly the client rect recorded by config.set_client_rect() (set by
    input_controller.pin_roblox_window()), not the whole desktop. The window cannot
    be relied on to sit at the monitor's own (0, 0): Windows refuses to push a title
    bar off the top of the screen, so a windowed Roblox asked to put its client at
    the origin can end up nudged a few pixels down - and a whole-monitor screenshot
    would then contain the game shifted by that offset, with unrelated desktop above
    it. Every template match and every derived coordinate would be wrong by exactly
    that much, in a way that looks like the templates being broken.

    Cropping to the client removes the question entirely: frame coordinates are
    always client-relative (0, 0 = the client's own top-left corner), which is the
    space every template was captured in and the space config.to_reference() expects.
    config.to_screen() is the other half of this - it adds config.CLIENT_ORIGIN back
    on, to turn a client-relative point into a real point pydirectinput can move to.

    region: explicit {"top", "left", "width", "height"} override. None uses the
            client rect, falling back to the whole primary monitor when no window has
            been pinned yet (the offline tools, and the very first capture at
            startup, before config.CLIENT_SIZE is set).
    """
    with mss.mss() as sct:
        if region:
            monitor = region
        elif config.CLIENT_SIZE is not None:
            monitor = {
                "left": config.CLIENT_ORIGIN[0],
                "top": config.CLIENT_ORIGIN[1],
                "width": config.CLIENT_SIZE[0],
                "height": config.CLIENT_SIZE[1],
            }
        else:
            monitor = sct.monitors[1]
        shot = sct.grab(monitor)
        img = np.array(shot)  # BGRA
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

def native_variant_path(template_path):
    """
    Where the copy of this template captured AT the current client size would live,
    or None if the client isn't a size we keep a variant set for.

    "assets/templates/story_card.png" -> "assets/templates@1600x900/story_card.png"
    """
    if config.CLIENT_SIZE is None:
        return None
    w, h = config.CLIENT_SIZE
    if (w, h) == (config.REFERENCE_WIDTH, config.REFERENCE_HEIGHT):
        return None
    prefix = "assets/templates/"
    p = template_path.replace(os.sep, "/")
    if not p.startswith(prefix):
        return None
    return f"assets/templates@{w}x{h}/" + p[len(prefix):]


# Variants already rejected as not-really-variants, so the warning is printed once each
# rather than on every frame of a loop that runs for days.
_bogus_variants = set()

# (variant path, master path, rounded scale) -> bool, because _usable_variant() is on the
# hot path: it is consulted for every candidate of every template on every frame. Without
# this, the size check would re-decode two PNGs each time, which is precisely the
# redundant disk I/O _template_cache exists to avoid.
_variant_verdicts = {}

# How close to the MASTER's own dimensions a "variant" has to be before it is treated as
# a copy of the master rather than a capture. 4% is far tighter than any real crop of a
# smaller client could land (at the sizes this bot docks to, a genuine capture is 17-41%
# smaller), and far looser than the 0% a byte-copy actually is.
_COPY_OF_MASTER = 0.04

# Above this scale a variant buys nothing anyway - the master is already near enough to
# native size - so the check has nothing meaningful to compare against and stands down.
_VARIANT_SCALE_CEILING = 0.95


def _variant_is_native(variant_path, master_path, scale=None):
    """
    True unless this "variant" is really just a copy of the reference master.

    A variant is trusted completely - it is matched at 1:1 with no resizing, and it is
    tried ahead of the reference master. That trust rests on a filename and a folder
    name and nothing else, which is exactly how this went wrong: copying
    assets/templates/ wholesale into assets/templates@1600x900/ produced a "variant" for
    every template that was really the 1920-wide master under a new name. Matched at 1:1
    against a 1600-wide screen each one was ~20% too big and could never match anything,
    silently - the master was still scored alongside it and usually won, so the folder
    looked like it was working while contributing nothing but wasted matching.

    The test is deliberately narrow: reject only when the file is within a few percent of
    the MASTER's own dimensions while the client is small enough that a real capture
    would be visibly smaller. It does not try to police crop sizes in general, because a
    variant taken by hand (tools/template_capture.py --variant) is a freehand box and can
    legitimately be somewhat larger or smaller than the predicted crop - failing those
    would trade a silent bug for a noisy one. A variant that is wrong in some subtler way
    is a question for tools/template_check.py, which sweeps sizes and can say so with
    evidence.

    scale: the render scale to judge against. Defaults to the live one, which is what
    matching wants. config._embedded_templates_ready() passes the DOCK size's scale
    explicitly, because it runs at import time - before any window has been pinned - when
    the live scale is still 1.0 and this check would wave everything through.
    """
    s = config.render_scale() if scale is None else scale
    if s >= _VARIANT_SCALE_CEILING:
        return True

    key = (variant_path, master_path, round(s, 4))
    cached = _variant_verdicts.get(key)
    if cached is not None:
        return cached

    variant = cv2.imread(variant_path, cv2.IMREAD_UNCHANGED)
    master = cv2.imread(master_path, cv2.IMREAD_UNCHANGED)
    if variant is None or master is None:
        # Nothing to compare. Not cached: an unreadable file is a transient condition
        # (a half-written capture), not a verdict worth remembering for the whole run.
        return True

    same_w = abs(variant.shape[1] - master.shape[1]) / master.shape[1] <= _COPY_OF_MASTER
    same_h = abs(variant.shape[0] - master.shape[0]) / master.shape[0] <= _COPY_OF_MASTER
    verdict = not (same_w and same_h)
    _variant_verdicts[key] = verdict

    if not verdict and variant_path not in _bogus_variants:
        _bogus_variants.add(variant_path)
        client = (f"{config.CLIENT_SIZE[0]}x{config.CLIENT_SIZE[1]}"
                  if config.CLIENT_SIZE else f"{s:.4f}-scale")
        print(
            f"[vision] IGNORING {variant_path}: it is "
            f"{variant.shape[1]}x{variant.shape[0]}, the same size as the reference "
            f"master - but a capture taken at this {client} client should be about "
            f"{master.shape[1] * s:.0f}x{master.shape[0] * s:.0f}. That is a copy of "
            f"assets/templates/, not a native capture, and matched at 1:1 it can never "
            f"hit anything. Re-capture it with tools/retemplate.py, or delete the folder. "
            f"Falling back to the reference template, which still works."
        )
    return verdict


def _usable_variant(template_path):
    """The native-size stand-in for this template, or None if there isn't a usable one."""
    variant = native_variant_path(template_path)
    if variant is None or not os.path.exists(variant):
        return None
    if not _variant_is_native(variant, template_path):
        return None
    return variant

def _load_template(template_path, scale=None):
    """
    Loads (and caches) a template as BGR, ready to match against the current screen.

    Two ways that can happen, and the first is strongly preferred:

    1. A variant captured at the CURRENT client size exists (see
       native_variant_path, and tools/retemplate.py which produces them). It is used
       as-is, no resizing at all.
    2. Otherwise the reference-resolution template is shrunk by config.SCALE.

    Path 2 is a fallback because shrinking only works for artwork. Roblox re-renders
    small TEXT with its own hinting and antialiasing rather than drawing a scaled-down
    copy of the big glyphs, so a shrunken text template never lines up with it -
    measured, story_card.png scores 0.98 as a same-size variant and 0.61 shrunk to the
    same dimensions on the very same screen. Anything text-bearing needs a real
    variant; icons survive path 2 fine.

    Cached by (resolved path, scale): decoding the PNG on every call would be a huge
    amount of redundant disk I/O across several templates per screenshot in a loop
    that runs for days, and resizing on every call would be the same waste again.
    Keying on scale as well means a mid-run re-pin to a different size can't serve a
    stale resize.
    """
    # An explicit scale means the caller wants THIS template at THAT size (the
    # re-capture tool asks for the reference artwork at 1.0 to identify screens).
    # Only the default path may substitute a variant, or that request silently
    # returns something else entirely.
    if scale is None:
        scale = config.render_scale()
        variant = _usable_variant(template_path)
        if variant is not None:
            # A variant is already the right size for this client, so it normally needs
            # no resizing at all - but it was captured on a machine drawing the UI at
            # config.UI_SCALE 1.0. Where that differs, the variant is the correct
            # artwork at the wrong size, and UI_SCALE alone is what it needs.
            template_path, scale = variant, config.UI_SCALE

    cache_key = (template_path, round(scale, 4))
    cached = _template_cache.get(cache_key)
    if cached is not None:
        return cached

    template = cv2.imread(template_path, cv2.IMREAD_UNCHANGED)
    if template is None:
        raise FileNotFoundError(f"Template not found: {template_path}")

    if template.ndim == 3 and template.shape[2] == 4:
        alpha = template[:, :, 3]
        if alpha.min() != alpha.max() and template_path not in _alpha_warned:
            _alpha_warned.add(template_path)
            print(
                f"[vision] WARNING: {template_path} has real transparency. "
                f"Loading it as opaque discards the alpha channel and leaves corrupted "
                f"filler pixels behind, which silently breaks matching. Re-save it as a "
                f"plain rectangular crop (no transparent background) instead."
            )
        template = cv2.cvtColor(template, cv2.COLOR_BGRA2BGR)
    elif template.ndim == 2:  # grayscale source - matchTemplate needs matching channels
        template = cv2.cvtColor(template, cv2.COLOR_GRAY2BGR)

    if scale != 1.0:
        new_w = max(1, int(round(template.shape[1] * scale)))
        new_h = max(1, int(round(template.shape[0] * scale)))
        # INTER_AREA is the right filter for shrinking - it averages over the source
        # pixels rather than sampling them, which preserves the edges matchTemplate
        # keys on. INTER_LINEAR here visibly softens small text and costs confidence.
        # It is the wrong filter for growing, though, where it degenerates towards
        # nearest-neighbour and leaves blocky edges; that only comes up when UI_SCALE
        # is above 1.0, which no machine did until one drew the UI 10% large.
        interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
        template = cv2.resize(template, (new_w, new_h), interpolation=interp)

    _template_cache[cache_key] = template
    return template

# --- Fast matching ---------------------------------------------------------------
# A full-frame matchTemplate costs roughly (frame area x template area). On a 20-thread
# desktop that is ~55 ms per template at 1600x900; on a 10-year-old laptop it was slow
# enough that finding one button took seconds, with a poll tick checking several.
#
# match_best() searches a half-size copy of the frame with a half-size template first
# (about 16x less work), takes the few strongest spots, and then re-scores ONLY those
# spots at full resolution. The confidence it returns is therefore the same full-res
# TM_CCOEFF_NORMED value the old code produced, measured at the same pixel, so every
# threshold in config.py keeps its meaning. What the coarse pass can do is miss - pick
# the wrong few spots - which returns a lower score (a retry), never a false hit; the
# equivalence test run when this was written found no such misses on any saved frame.
FAST_MIN_SIDE = 24      # templates thinner than this at full res are matched directly
_COARSE_PEAKS = 16      # how many coarse spots get re-scored at full resolution
_REFINE_PAD = 6         # full-res pixels searched around each coarse spot
# A weak real match can be under-read by the coarse pass by a few hundredths - enough,
# right at the threshold, to turn a hit into a miss (measured: select_stage_btn 0.699
# full vs 0.648 fast against a 0.69 bar). find_template() re-checks anything within
# this distance below its threshold at full resolution, so every decision made near the
# line is made by exactly the same full-frame match as before.
NEAR_MISS_RECHECK = 0.15
_half_frame = {"src": None, "small": None}
_half_templates = {}


def _half(img):
    return cv2.resize(img, (max(1, img.shape[1] // 2), max(1, img.shape[0] // 2)),
                      interpolation=cv2.INTER_AREA)


def _half_of_frame(screenshot):
    # One frame is matched against many templates in a row; halve it once.
    if _half_frame["src"] is not screenshot:
        _half_frame["src"], _half_frame["small"] = screenshot, _half(screenshot)
    return _half_frame["small"]


def _half_of_template(template):
    entry = _half_templates.get(id(template))
    if entry is None or entry[0] is not template:
        if len(_half_templates) > 512:
            _half_templates.clear()
        entry = (template, _half(template))
        _half_templates[id(template)] = entry
    return entry[1]


def match_best(screenshot, template, exact=False):
    """
    Best (confidence, (x, y) top-left) of template in screenshot, or (-1.0, None).
    exact=True always runs the plain full-frame match.
    """
    t_h, t_w = template.shape[:2]
    s_h, s_w = screenshot.shape[:2]
    if t_h > s_h or t_w > s_w:
        return -1.0, None
    if (exact or not getattr(config, "FAST_MATCHING", True) or min(t_h, t_w) < FAST_MIN_SIDE
            or s_h * s_w < 250_000):
        _, val, _, loc = cv2.minMaxLoc(cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED))
        return val, loc

    small_t = _half_of_template(template)
    coarse = cv2.matchTemplate(_half_of_frame(screenshot), small_t, cv2.TM_CCOEFF_NORMED)
    c_h, c_w = small_t.shape[:2]
    best_val, best_loc = -1.0, None
    for _ in range(_COARSE_PEAKS):
        _, peak, _, (px, py) = cv2.minMaxLoc(coarse)
        if peak <= -1.0:
            break
        # Blank out this peak's neighbourhood so the next pass finds a different spot.
        coarse[max(0, py - c_h // 2):py + c_h // 2 + 1, max(0, px - c_w // 2):px + c_w // 2 + 1] = -2.0
        x0, y0 = max(0, 2 * px - _REFINE_PAD), max(0, 2 * py - _REFINE_PAD)
        x1, y1 = min(s_w, 2 * px + _REFINE_PAD + t_w + 1), min(s_h, 2 * py + _REFINE_PAD + t_h + 1)
        roi = screenshot[y0:y1, x0:x1]
        if roi.shape[0] < t_h or roi.shape[1] < t_w:
            continue
        _, val, _, (lx, ly) = cv2.minMaxLoc(cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED))
        if val > best_val:
            best_val, best_loc = val, (x0 + lx, y0 + ly)
    return best_val, best_loc


def _match_at(screenshot, candidate, scale):
    """(confidence, location, shape) for one image at one size, or (-1, None, None)."""
    template = _load_template(candidate, scale=scale)
    val, loc = match_best(screenshot, template)
    if loc is None:
        return -1.0, None, None
    return val, loc, template.shape[:2]


# Searching across sizes was tried here and removed. Shrinking a template far enough
# makes it match almost anything: sweeping +-25% turned a lobby screenshot into seven
# confident hits for act tiles, raid difficulty numbers and Select Stage - none of
# which were on screen - because a 60px crop taken down to 28px has too little left to
# be distinctive. A missed match costs a retry; a false one clicks the wrong thing and
# is not noticed, so the single expected size is the safer rule.


def clear_template_cache():
    """
    Drops every cached template image.

    The cache is keyed by (path, scale), so a changed config.UI_SCALE would miss it and
    reload correctly anyway - but the old entries stay resident for the life of the
    process, and the one caller here has just discovered that every template it holds
    was built for the wrong size. Clearing keeps the cache honest rather than letting it
    accumulate a set of images nothing will ask for again.
    """
    _template_cache.clear()
    # Whether a variant is the right size for the client is decided against the scale in
    # force when it was first looked at, so a re-pin can turn a rejected variant into a
    # correct one (or the reverse). Keeping the rejections would make the first size the
    # process happened to see permanent for the rest of the run.
    _variant_verdicts.clear()
    _bogus_variants.clear()

def _match_candidates(template_path):
    """
    Every image that may stand in for this template, best-known first.

    Normally just the template itself. When a variant captured at the current client
    size exists it replaces that - the base variant plus any "<name>__2.png",
    "<name>__3.png" alternates sitting beside it.

    Alternates exist because one crop cannot describe a UI element that is drawn more
    than one way: an act tile is styled differently while it is the selected act, and
    a single capture of the other state measured 0.725 against a 0.739 threshold - a
    miss by a hair, purely from being in the wrong state.
    """
    variant = _usable_variant(template_path)
    if variant is None:
        return [template_path]

    out = [variant]
    stem, ext = os.path.splitext(variant)
    n = 2
    while True:
        alt = f"{stem}__{n}{ext}"
        if not os.path.exists(alt):
            break
        out.append(alt)
        n += 1

    # The plain template is kept in the running even when a variant exists, instead of
    # being replaced by it. A variant used to win by default, which meant re-capturing
    # an image in assets/templates/ changed nothing at all while an older variant of
    # the same thing sat in front of it - the new crop was simply never looked at, and
    # nothing said so. Scoring both and letting the better one win makes a fresh
    # capture take effect wherever it is put.
    out.append(template_path)
    return out

def _candidate_scale(candidate, template_path):
    """
    How much to resize this candidate before matching it against the screen.

    Two kinds of image end up in a candidate list and they need opposite treatment:

    - The reference master is 1920-wide artwork, so it has to come down by the full
      render scale (SCALE for the client's size, times UI_SCALE for a game drawing its
      interface larger than the templates were captured at).
    - A native variant was already captured at this client's size, so SCALE is baked
      into it and applying SCALE again would shrink it twice. It needs UI_SCALE alone,
      which is 1.0 on a machine where the game agrees with the templates.

    Split out of find_template() so score_template() cannot drift away from it; the two
    functions answer the same question about the same screen and used to size the same
    candidate differently.
    """
    if candidate != template_path:
        return config.UI_SCALE
    return config.render_scale()


def score_template(screenshot, template_path):
    """
    Best (x, y, confidence) for this template with NO threshold applied - the caller
    decides what the number means.

    Needed when two templates describe mutually exclusive states of the SAME control
    and look nearly alike, e.g. the Auto Play button's "Auto Play" and "Auto Playing"
    faces: identical green pill, identical gear icon, one word apart. Tested
    separately, each clears a fixed bar on the other's screen, so whichever happens
    to be asked about first wins and the answer is decided by call order rather than
    by the screen. Scored against each other, the higher number is the state actually
    showing, which is the question that was being asked all along.
    """
    best_val, best_loc, best_shape = -1.0, None, None
    for candidate in _match_candidates(template_path):
        # Sized exactly the way find_template() sizes the same candidate - see
        # _candidate_scale(). This used to call _load_template(candidate) with no scale
        # for the master, which re-runs variant substitution internally and so handed
        # back the VARIANT a second time instead of the shrunk master. The master was
        # then never scored here at all, which quietly undid the "score both and let the
        # better one win" rule that _match_candidates() documents - and made
        # score_template() and find_template() disagree about the same screen.
        template = _load_template(candidate, scale=_candidate_scale(candidate, template_path))
        t_h, t_w = template.shape[:2]
        s_h, s_w = screenshot.shape[:2]
        if t_h > s_h or t_w > s_w:
            continue
        # Exact: callers compare two look-alike templates' scores against each other
        # (Auto Play vs Auto Playing), where a few hundredths decide the answer.
        max_val, max_loc = match_best(screenshot, template, exact=True)
        if max_loc is not None and max_val > best_val:
            best_val, best_loc, best_shape = max_val, max_loc, template.shape[:2]

    if best_loc is None:
        return None
    h, w = best_shape
    x, y = config.to_reference(best_loc[0] + w // 2, best_loc[1] + h // 2)
    return (x, y, best_val)


def _looks_exact(screenshot, loc, shape, candidate, scale, max_diff, debug_label):
    """
    True unless the matched region is a genuinely different-LOOKING rendering of the
    same shape - a dimmed, tinted or overlaid duplicate that normalized cross-
    correlation (TM_CCOEFF_NORMED) cannot tell apart from the real thing, because NCC
    normalizes away brightness and contrast by design: a uniformly darker copy of the
    exact same pattern can still score close to 1.0 on shape alone.

    That is exactly what let GAME_RESULTS_BTN fire on Portals' 3-card reward screen:
    the recovery button renders directly underneath the reward cards, at reduced
    opacity, and read confidences of 0.85-0.95 there - well past MATCH_THRESHOLD -
    while being clicked over and over for no effect, because the actual clickable
    thing was the cards on top of it, not the dimmed button beneath them. Confirmed
    live 2026-09-12: six repeated "'Game Results' recovery button detected" clicks
    in a row against exactly that screen, none of which did anything.

    This is a mean-ABSOLUTE-difference check, deliberately not another correlation -
    it is measuring the one thing NCC ignores (actual pixel brightness/color), not
    re-measuring the thing NCC already covers (shape). Only consulted for templates
    listed in config.EXACT_MATCH_TEMPLATES: most templates SHOULD tolerate the minor
    brightness/exposure drift a different map background or time-of-day lighting
    causes behind them, and this would make matching needlessly brittle for those.
    It is only correct for a control whose "duplicate" is a dimmed copy of the exact
    same fixed art, which is a narrow, deliberately opted-into case.
    """
    try:
        template = _load_template(candidate, scale=scale)
    except Exception:
        return True  # can't check it; don't turn a load failure into a false reject

    h, w = shape
    crop = screenshot[loc[1]:loc[1] + h, loc[0]:loc[0] + w]
    if crop.shape != template.shape:
        return True  # ran off the edge of the frame; nothing sane to compare

    diff = float(np.abs(crop.astype(np.int16) - template.astype(np.int16)).mean())
    if diff <= max_diff:
        return True

    if debug_label:
        print(f"[vision] {debug_label}: shape matched but pixels differ too much "
              f"(mean diff={diff:.1f}, need <= {max_diff}) - looks like a dimmed/"
              f"overlaid duplicate, not the real thing. Treating as not found.")
    return False


def find_template(screenshot, template_path, threshold=0.85, debug_label=None):
    """
    Search for template_path inside screenshot using normalized cross-correlation.
    Returns (x, y, confidence) of the CENTER of the best match if above threshold,
    otherwise None.
    debug_label: if set, prints the best confidence found even on a failed match -
    use this to tell a stale/wrong template (confidence stays low, e.g. <0.5) apart
    from a threshold that's just slightly too strict (confidence sits just under it).

    A template listed in config.TEMPLATE_THRESHOLDS uses its own threshold instead of
    the one passed in, so a single awkward template can be tuned without loosening
    matching for every other template that shares the global default. The threshold is
    then relaxed in proportion to how far the screen is scaled down, since a shrunk
    template cannot score as highly as a full-size one (see config.effective_threshold).

    Where a template has several captured looks, each is tried and the best wins - see
    _match_candidates().
    """
    # A candidate list that still starts with the plain reference path means no
    # same-size capture exists for this screen, so everything below is about to be
    # shrunk - which costs far more confidence than a resize "should", and is what
    # effective_threshold needs to know to hand out the right amount of slack.
    candidates = _match_candidates(template_path)

    best_val, best_loc, best_shape, best_scale, best_candidate = -1.0, None, None, 1.0, None
    for candidate in candidates:
        # A same-size capture is used as-is; the reference template is shrunk to fit
        # this screen. UI_SCALE is normally 1.0 and only differs where the game draws
        # its interface larger than the templates were captured at.
        scale = _candidate_scale(candidate, template_path)
        val, loc, shape = _match_at(screenshot, candidate, scale)
        if val > best_val:
            best_val, best_loc, best_shape, best_scale, best_candidate = val, loc, shape, scale, candidate

    if best_loc is None:
        if debug_label:
            print(f"[vision] {debug_label}: no usable template (none fit the screen)")
        return None

    # Relief is decided by the size that actually won, not by which folder the image
    # came from: a shrunk template genuinely cannot score as highly as a same-size one,
    # while one matched at 1:1 has no excuse and should be held to the full bar.
    base_threshold = threshold
    threshold = config.effective_threshold(template_path, base_threshold, shrunk=best_scale < 0.999)

    if getattr(config, "FAST_MATCHING", True) and abs(best_val - threshold) < NEAR_MISS_RECHECK:
        # Close to the line, on either side: redo it with the full-resolution match (see
        # NEAR_MISS_RECHECK) and take that answer wholesale. Below the line this rescues
        # an under-read hit; above it, a weak match can have two near-equal spots
        # (select_portal: 0.704 vs 0.703 at different buttons) and the coarse pass must
        # not be the one choosing which gets clicked.
        best_val, best_loc, best_shape, best_scale, best_candidate = -1.0, None, None, 1.0, None
        for candidate in candidates:
            scale = _candidate_scale(candidate, template_path)
            template = _load_template(candidate, scale=scale)
            val, loc = match_best(screenshot, template, exact=True)
            if loc is not None and val > best_val:
                best_val, best_loc, best_shape = val, loc, template.shape[:2]
                best_scale, best_candidate = scale, candidate
        if best_loc is None:
            return None
        threshold = config.effective_threshold(template_path, base_threshold, shrunk=best_scale < 0.999)

    if best_val < threshold:
        if debug_label:
            print(f"[vision] {debug_label}: best confidence={best_val:.3f} (need >= {threshold})")
        return None

    max_diff = config.EXACT_MATCH_TEMPLATES.get(template_path)
    if max_diff is not None and not _looks_exact(screenshot, best_loc, best_shape,
                                                   best_candidate, best_scale, max_diff,
                                                   debug_label):
        return None

    h, w = best_shape
    center_x = best_loc[0] + w // 2
    center_y = best_loc[1] + h // 2

    # Converted back to reference space before returning. Everything downstream - the
    # click that follows, the coordinates in config.py it gets compared against - works
    # in reference space, so this is the one place a real screen pixel is translated.
    center_x, center_y = config.to_reference(center_x, center_y)
    return (center_x, center_y, best_val)
