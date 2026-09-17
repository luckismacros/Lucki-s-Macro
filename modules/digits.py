# modules/digits.py
"""
Reading numbers off the screen, for the counts no template can express.

Every other question this bot asks is "is this thing present?", which template matching
answers directly. "How many Sacrificed Souls do I have?" is not that question - the
answer is 151 different images, and the useful part is the number itself.

Deliberately NOT an OCR engine. Tesseract would mean bundling a ~50MB binary into the
build, and general OCR is actually WORSE here: it is trained on document text and does
poorly on short strings in a stylised game font, which is exactly this case. The game
draws digits from a fixed font that never changes, so matching ten small glyph images
is both simpler and more accurate than a general recogniser.

The size constraint that shaped this: at the docked 1136x639 client these digits render
4-6px tall, where anti-aliasing is most of the glyph and 8/9/6/5 are not reliably
distinguishable. At a 1920-wide client the same text is 9-12px, which is comfortable.
So a caller must read counts with the client at reference size, not docked - see
soul farming, which enlarges only for the check and shrinks back to play.
"""
import os

import cv2
import numpy as np

import config

# Deliberately OUTSIDE assets/templates/. Everything under that tree is treated as a
# screen element by the rest of the system - vision swaps in size-specific variants,
# and tools/retemplate.py tries to re-capture each one from a live screen. Digit glyphs
# are neither: they are tiny generic shapes that match noise anywhere on screen (a '1'
# scored 0.997 against the lobby), so sitting in that tree they out-ranked real buttons
# during a re-capture pass and consumed the slots meant for them.
DIGITS_DIR = "assets/digits"

# Below this, a glyph is called unreadable rather than guessed at. A wrong digit here
# is not a cosmetic error - it decides how many more matches to farm, so failing loudly
# and falling back to the caller's own count is always better than a confident misread.
MIN_GLYPH_CONFIDENCE = 0.70

# Anything shorter than this is punctuation or noise, not a digit. The 'x' and '/' in
# "95x / 150x Owned" sit clearly below the digit line; digits are the tall glyphs.
MIN_DIGIT_HEIGHT_RATIO = 0.75

_cache = {}


def load_digit_templates():
    """
    The digit images available, as {character: image}. Cached.

    A partial set is usable rather than fatal: with only some digits present, numbers
    made of those digits still read correctly and the rest report as unreadable, which
    the caller handles. That matters because these are captured from the game itself -
    a digit nobody has seen on screen yet simply has no sample.
    """
    if _cache:
        return _cache
    if not os.path.isdir(DIGITS_DIR):
        return _cache
    for ch in "0123456789":
        path = os.path.join(DIGITS_DIR, f"{ch}.png")
        if os.path.exists(path):
            img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if img is not None:
                _cache[ch] = img
    return _cache


def segment_glyphs(region, threshold=140):
    """
    Bounding boxes of the bright glyphs in `region`, left to right.

    The text is near-white on a dark pill in every case this reads, so a plain
    brightness threshold separates it far more reliably than edge detection would.
    """
    if region is None or region.size == 0:
        return []
    grey = region if region.ndim == 2 else cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(grey, threshold, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = [cv2.boundingRect(c) for c in contours if cv2.contourArea(c) >= 3]
    return sorted(boxes, key=lambda b: b[0])


def classify_glyph(glyph, templates=None):
    """Best (character, confidence) for one glyph image, or (None, 0.0)."""
    templates = templates if templates is not None else load_digit_templates()
    if not templates or glyph is None or glyph.size == 0:
        return None, 0.0

    grey = glyph if glyph.ndim == 2 else cv2.cvtColor(glyph, cv2.COLOR_BGR2GRAY)
    best_char, best_conf = None, 0.0
    for ch, template in templates.items():
        # Compared at a common size so a one-pixel difference in how the glyph was
        # cropped cannot decide the answer.
        h, w = template.shape[:2]
        resized = cv2.resize(grey, (w, h), interpolation=cv2.INTER_AREA)
        conf = float(cv2.minMaxLoc(cv2.matchTemplate(resized, template, cv2.TM_CCOEFF_NORMED))[1])
        if conf > best_conf:
            best_char, best_conf = ch, conf
    return best_char, best_conf


def read_number(region, templates=None):
    """
    The leading number in `region`, or None if any digit of it is unreadable.

    Reads only up to the first non-digit, which is what makes "95x / 150x Owned" work
    without knowing anything about the rest of the string: the count is the first run
    of digits, and 'x' ends it. Returning None rather than a partial number keeps a
    half-read "15" out of a place expecting "150".
    """
    templates = templates if templates is not None else load_digit_templates()
    if not templates:
        return None

    boxes = segment_glyphs(region)
    if not boxes:
        return None

    tallest = max(h for _, _, _, h in boxes)
    grey = region if region.ndim == 2 else cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)

    out = ""
    for x, y, w, h in boxes:
        if h < tallest * MIN_DIGIT_HEIGHT_RATIO:
            break  # dropped below the digit line: punctuation, so the number ended
        ch, conf = classify_glyph(grey[y:y + h, x:x + w], templates)
        if ch is None or conf < MIN_GLYPH_CONFIDENCE:
            return None
        out += ch

    return int(out) if out else None


# --- Route Rewards counts (Expeditions) ---------------------------------------------
# The count labels on the Expedition Map's Route Rewards cards ("24x") are a different,
# much smaller render than the soul tooltip above: 8px-tall digits that frequently
# touch ("11", "60" come out as one blob at any brightness cut), so read_number()'s
# segment-then-classify approach can't be used - there is nothing clean to segment.
# Instead every glyph template is slid along the label and the strongest
# non-overlapping hits are kept, which needs no segmentation at all.
#
# Built from the six Route Rewards examples in assets/templates/expeditions/
# Expedition_Map/Expedition_Map_Examples: every material-card count read correctly
# there, each example tested against glyphs built only from the other five.
ROUTE_DIGITS_DIR = "assets/digits_route"

# Real glyphs scored 0.92+ in that test; a digit template against the wrong digit
# peaked at 0.80 (8 vs 9), and the best hit per position always wins, so this only
# has to reject background.
ROUTE_MIN_CONFIDENCE = 0.72

_route_cache = {}


def load_route_digit_templates():
    """Route-label glyphs '0'-'9' and 'x', as {char: float32 grey image}. Cached."""
    if _route_cache:
        return _route_cache
    if not os.path.isdir(ROUTE_DIGITS_DIR):
        return _route_cache
    for ch in "0123456789x":
        img = cv2.imread(os.path.join(ROUTE_DIGITS_DIR, f"{ch}.png"), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            _route_cache[ch] = img.astype(np.float32)
    return _route_cache


def read_route_label(strip, scale=1.0, templates=None):
    """
    The number in a Route Rewards count label ("24x" -> 24), as (value, confidence),
    or (None, 0.0) if it can't be read.

    `strip` is a BGR or grey crop around the label. Reads left to right and stops at
    the 'x', so anything past the label is ignored. The 'x' is required: without it
    there is no proof the whole number was seen, and a half-read "2" of "24" would be
    worse than no reading.
    """
    templates = templates if templates is not None else load_route_digit_templates()
    if not templates or "x" not in templates or strip is None or strip.size == 0:
        return None, 0.0

    # The labels are white; the card behind them is saturated colour. The darkest
    # channel is high only where all three are - i.e. on the text - so it isolates the
    # glyphs from a yellow or green card that plain greyscale would call bright too.
    grey = (strip.min(axis=2) if strip.ndim == 3 else strip).astype(np.float32)

    detections = []
    for ch, tpl in templates.items():
        if scale != 1.0:
            tpl = cv2.resize(tpl, (max(2, round(tpl.shape[1] * scale)), max(2, round(tpl.shape[0] * scale))),
                             interpolation=cv2.INTER_LINEAR)
        th, tw = tpl.shape[:2]
        if th > grey.shape[0] or tw > grey.shape[1]:
            continue
        per_column = cv2.matchTemplate(grey, tpl, cv2.TM_CCOEFF_NORMED).max(axis=0)
        for x, v in enumerate(per_column):
            if v >= ROUTE_MIN_CONFIDENCE and v == per_column[max(0, x - 2):x + 3].max():
                detections.append((float(v), x, tw, ch))

    # Strongest first, then drop anything overlapping a stronger hit - that is what
    # stops a '1' matching the stem inside a '4' from being read as an extra digit.
    detections.sort(reverse=True)
    kept = []
    for v, x, w, ch in detections:
        if all(x + w <= kx + 1 or kx + kw <= x + 1 for _, kx, kw, _ in kept):
            kept.append((v, x, w, ch))
    kept.sort(key=lambda d: d[1])

    number, confidence = "", 1.0
    for v, _, _, ch in kept:
        if ch == "x":
            return (int(number), confidence) if number else (None, 0.0)
        number += ch
        confidence = min(confidence, v)
    return None, 0.0
