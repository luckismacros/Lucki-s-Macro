# modules/expedition_map.py
"""
Expedition Map route picking: route the run through the nodes that give the most of
one material.

How the map works (confirmed by the player, 2026-09-13): there is only ever ONE
route, drawn in green from the start wheel to the boss. Clicking a node re-routes
through that node. The Route Rewards bar along the bottom always shows the totals
for the current route.

That makes the bar the ground truth, so nothing here tries to understand the map as
a graph. For each node carrying the chosen material's icon: click it, read that
material's count off the bar, remember the best. Then click the best node again.
Worst case, it tried nodes that didn't help and ends on the best one it saw.

Two details from the example maps shape the detection:
  - A node's icon box is tinted by rarity, and Fuel Cell and Aqua Shard are both blue
    crystals. Every candidate is scored against EVERY material and has to win clearly
    (ICON_MIN_MARGIN); on the examples, real icons won by 0.26+ and the look-alikes
    (silver coins, the other crystal) by 0.10 or less.
  - The boss node carries its own reward strip with the same icons. Those are found
    by their position relative to the teal skull and skipped.

Clicking the icon itself opens a description popup, so the click lands on the node
marker NODE_CLICK_OFFSET_Y px below it. A description that opens anyway is closed.

The map ZOOMS to fit its node layout (confirmed on the first live run: that map was
drawn 1.10x the size of the examples, and every icon was missed at 1.00x). The Route
Rewards bar does not zoom. So every map-space size here - icons, skull, the node click
offset, the boss strip - is multiplied by the map's zoom, measured from the boss skull
each time the map is read (see estimate_map_zoom). Icon crops were themselves taken
from maps at different zooms, so each carries its own capture zoom
(config.EXPEDITION_ICON_NATIVE_ZOOM) and is resized by map_zoom / its own zoom.
"""
import time

import cv2
import numpy as np

import config
from vision import capture_screen, find_template, _load_template
from input_controller import click_at
from modules import digits, health
from modules.polling import poll_until, target

# Everything above this (reference px) is the map. The Route Rewards panel starts ~725.
MAP_BOTTOM = 720

# Route Rewards bar, reference (x0, y0, x1, y1) - where the material cards are searched.
BAR_REGION = (240, 740, 1560, 890)

# Card art found at 0.87-1.00 when present, never above 0.53 when absent.
CARD_MIN_CONFIDENCE = 0.75

# Icon detection. Peaks above ICON_PEAK_FLOOR on any material become candidates; a
# candidate counts for a material only if that material scores ICON_MIN_CONFIDENCE and
# beats every other material by ICON_MIN_MARGIN. Measured on the six example maps.
ICON_PEAK_FLOOR = 0.62
ICON_MIN_CONFIDENCE = 0.70
ICON_MIN_MARGIN = 0.15
ICON_DEDUPE_RADIUS = 12

# Boss reward strip: sits 20-60px above the skull and starts no more than ~90px left of
# it. The nearest real node column is 140px+ left of the skull, hence 115.
BOSS_SKULL_MIN_CONFIDENCE = 0.65
BOSS_STRIP_LEFT_OF_SKULL = 115
BOSS_STRIP_ABOVE_SKULL = (60, 20)

# Icon centre -> node marker centre. 36-37px on every example map.
NODE_CLICK_OFFSET_Y = 36

# After a node click, how long before the bar is read. The bar updates immediately;
# this covers the frame or two it takes to redraw.
CLICK_SETTLE_SECONDS = 0.7

# A map has ~10 columns; this only guards against a detection going badly wrong.
MAX_CANDIDATES = 14


# Map zoom search range. Zoom is measured relative to the skull crop; seen so far: 0.89
# (the School Grounds examples) and 1.02 (the live map and the other three examples).
MAP_ZOOM_MIN = 0.80
MAP_ZOOM_MAX = 1.40

# The map-space px constants above (NODE_CLICK_OFFSET_Y, the boss strip, the dedupe
# radius) were measured on the 0.89-zoom examples, so they scale by zoom / this.
MAP_ZOOM_PX_BASE = 0.89


def _map_px(value, zoom):
    """A map-space constant measured at MAP_ZOOM_PX_BASE -> reference px at this zoom."""
    return value * zoom / MAP_ZOOM_PX_BASE


def _node_offset(zoom):
    return int(round(_map_px(NODE_CLICK_OFFSET_Y, zoom)))


def _px(value):
    """Reference px -> pixels in the current screenshot."""
    return int(round(value * config.render_scale()))


def _resize(image, factor):
    if abs(factor - 1.0) < 1e-3:
        return image
    interp = cv2.INTER_CUBIC if factor > 1.0 else cv2.INTER_AREA
    return cv2.resize(image, None, fx=factor, fy=factor, interpolation=interp)


def estimate_map_zoom(screenshot):
    """
    (zoom, skull_centre_px) for the open Expedition Map, measured from the boss skull
    - one small template that every map has, so it is cheap (well under a second) and
    independent of which materials this map happens to carry. (1.0, None) if no skull
    is convincing, which leaves detection at the examples' zoom.
    """
    region = screenshot[:_px(MAP_BOTTOM)]
    try:
        skull = _load_template(config.EXP_BOSS_SKULL)
    except FileNotFoundError:
        return 1.0, None

    def _score(zoom):
        t = _resize(skull, zoom)
        if t.shape[0] > region.shape[0] or t.shape[1] > region.shape[1]:
            return -1.0, None, t.shape
        _, val, _, loc = cv2.minMaxLoc(cv2.matchTemplate(region, t, cv2.TM_CCOEFF_NORMED))
        return float(val), loc, t.shape

    coarse = max((_score(z)[0], z) for z in np.arange(MAP_ZOOM_MIN, MAP_ZOOM_MAX + 1e-6, 0.05))[1]
    best = (-1.0, 1.0, None, None)
    for z in np.arange(coarse - 0.05, coarse + 0.05 + 1e-6, 0.01):
        val, loc, shape = _score(z)
        if val > best[0]:
            best = (val, float(z), loc, shape)

    val, zoom, loc, shape = best
    if val < BOSS_SKULL_MIN_CONFIDENCE or loc is None:
        print(f"[ExpMap] Couldn't find the boss skull (best {val:.2f}) - assuming map zoom 1.00.")
        return 1.0, None
    return round(zoom, 2), (loc[0] + shape[1] // 2, loc[1] + shape[0] // 2)


def _icon_score_map(region, paths, zoom):
    """Per-pixel best score over all of a material's icon crops at this map zoom, plus the crop size."""
    native = getattr(config, "EXPEDITION_ICON_NATIVE_ZOOM", {})
    templates = []
    for path in paths:
        try:
            templates.append(_resize(_load_template(path), zoom / native.get(path, 1.0)))
        except FileNotFoundError:
            print(f"[ExpMap] Missing icon template: {path}")
    if not templates:
        return None

    h = min(t.shape[0] for t in templates)
    w = min(t.shape[1] for t in templates)
    if h > region.shape[0] or w > region.shape[1]:
        return None

    maps = [cv2.matchTemplate(region, t[:h, :w], cv2.TM_CCOEFF_NORMED) for t in templates]
    return np.max(np.stack(maps), axis=0), (h, w)


def _in_boss_strip(cx, cy, skull, zoom):
    sx, sy = skull
    return (cx >= sx - _px(_map_px(BOSS_STRIP_LEFT_OF_SKULL, zoom))
            and sy - _px(_map_px(BOSS_STRIP_ABOVE_SKULL[0], zoom)) <= cy
            <= sy - _px(_map_px(BOSS_STRIP_ABOVE_SKULL[1], zoom)))


def find_material_icons(screenshot, material, zoom=None, skull=None):
    """
    Nodes on the map carrying `material`'s icon, as [(ref_x, ref_y, confidence,
    margin)] sorted left to right. (ref_x, ref_y) is the ICON centre - click
    NODE_CLICK_OFFSET_Y * zoom below it. Pass zoom/skull from estimate_map_zoom() to
    avoid measuring twice.
    """
    if zoom is None:
        zoom, skull = estimate_map_zoom(screenshot)
    region = screenshot[:_px(MAP_BOTTOM)]

    maps = {}
    for key, data in config.EXPEDITION_MATERIALS.items():
        scored = _icon_score_map(region, data["icons"], zoom)
        if scored is not None:
            maps[key] = scored
    if material not in maps:
        return []

    radius = max(6, _px(_map_px(ICON_DEDUPE_RADIUS, zoom)))
    points = []
    for score, (th, tw) in maps.values():
        work = score.copy()
        for _ in range(60):
            _, val, _, (x, y) = cv2.minMaxLoc(work)
            if val < ICON_PEAK_FLOOR:
                break
            work[max(0, y - radius):y + radius, max(0, x - radius):x + radius] = -1
            centre = (x + tw // 2, y + th // 2)
            if all(abs(centre[0] - p[0]) > radius or abs(centre[1] - p[1]) > radius for p in points):
                points.append(centre)

    window = max(2, _px(_map_px(4, zoom)))
    found = []
    for cx, cy in points:
        scores = {}
        for key, (score, (th, tw)) in maps.items():
            x0, y0 = cx - tw // 2, cy - th // 2
            patch = score[max(0, y0 - window):max(0, y0 + window + 1),
                          max(0, x0 - window):max(0, x0 + window + 1)]
            scores[key] = float(patch.max()) if patch.size else -1.0

        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        best_key, best_val = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else -1.0
        if best_key != material or best_val < ICON_MIN_CONFIDENCE or best_val - runner_up < ICON_MIN_MARGIN:
            continue
        if skull is not None and _in_boss_strip(cx, cy, skull, zoom):
            continue

        rx, ry = config.to_reference(cx, cy)
        found.append((rx, ry, best_val, best_val - runner_up))

    found.sort(key=lambda f: f[0])
    return found


def read_material_count(screenshot, material):
    """
    How much of `material` the current route gives, read off the Route Rewards bar.

    0 when the material has no card at all (the route gives none). None when the card
    is there but its number can't be read - callers must not treat that as 0.
    """
    data = config.EXPEDITION_MATERIALS.get(material)
    if not data:
        return None
    try:
        card = _load_template(data["card"])
    except FileNotFoundError:
        print(f"[ExpMap] Missing route card template: {data['card']}")
        return None

    x0, y0, x1, y1 = (_px(v) for v in BAR_REGION)
    bar = screenshot[y0:y1, x0:x1]
    if bar.shape[0] < card.shape[0] or bar.shape[1] < card.shape[1]:
        return None

    result = cv2.matchTemplate(cv2.cvtColor(bar, cv2.COLOR_BGR2GRAY),
                               cv2.cvtColor(card, cv2.COLOR_BGR2GRAY), cv2.TM_CCOEFF_NORMED)
    _, val, _, (ax, ay) = cv2.minMaxLoc(result)
    if val < CARD_MIN_CONFIDENCE:
        return 0

    # The count label sits above-left of the card art (measured: art starts 14px into
    # the card and 18px below its top; the label runs from the card's left edge).
    ax, ay = ax + x0, ay + y0
    strip = screenshot[max(0, ay - _px(26)):ay + _px(2), max(0, ax - _px(18)):ax + _px(64)]

    best_value, best_conf = None, 0.0
    # 1.1 covers a card drawn slightly enlarged (hovered) - one example map had one.
    for factor in (1.0, 1.1):
        value, conf = digits.read_route_label(strip, scale=config.render_scale() * factor)
        if value is not None and conf > best_conf:
            best_value, best_conf = value, conf
    return best_value


def _map_is_open(screenshot):
    return find_template(screenshot, config.EXP_BACK_BTN, config.MATCH_THRESHOLD) is not None


def _close_description_if_open(screenshot):
    match = find_template(screenshot, config.EXP_CLOSE_BTN, config.MATCH_THRESHOLD)
    if not match:
        return False
    print("[ExpMap] A node description opened - closing it.")
    click_at(match[0], match[1])
    time.sleep(0.5)
    return True


def open_map():
    """
    Opens the Expedition Map. Returns True / False (stopped or halted) /
    "RECONNECTED" / "NOT_OPENED" (button never worked - caller decides).
    """
    if _map_is_open(capture_screen()):
        return True

    result = poll_until(
        [target(config.EXP_MAP_BTN, True, click=True, debug_label="expedition_map_btn")],
        interval=0.5, label="open_expedition_map", timeout=15.0, stuck_timeout=None,
    )
    if result in ("RECONNECTED", False):
        return result
    if result == "TIMEOUT":
        return "NOT_OPENED"

    result = poll_until(
        [target(config.EXP_BACK_BTN, True, debug_label="expedition_map_back")],
        interval=0.5, label="expedition_map_open", timeout=8.0, stuck_timeout=None,
    )
    if result == "TIMEOUT":
        return "NOT_OPENED"
    return result


def close_map():
    """Clicks Back until the map is gone. True when closed, False if it never closed."""
    for _ in range(4):
        if config.STOP_REQUESTED:
            return False
        shot = capture_screen()
        _close_description_if_open(shot)
        match = find_template(shot, config.EXP_BACK_BTN, config.MATCH_THRESHOLD)
        if not match:
            return True
        click_at(match[0], match[1])
        time.sleep(0.8)
    return not _map_is_open(capture_screen())


def _click_node(icon_x, icon_y, zoom):
    click_at(icon_x, icon_y + _node_offset(zoom))
    time.sleep(CLICK_SETTLE_SECONDS)


def choose_route(material):
    """
    With the map already open, routes through the node that gives the most `material`.
    Returns True when done (including "nothing to improve"), False if stopped.
    """
    label = config.EXPEDITION_MATERIALS.get(material, {}).get("label", material)
    time.sleep(0.6)  # the map fades in; reading mid-fade misses icons
    shot = capture_screen()

    start_count = read_material_count(shot, material)
    zoom, skull = estimate_map_zoom(shot)
    candidates = find_material_icons(shot, material, zoom=zoom, skull=skull)
    print(f"[ExpMap] Farming {label}: current route gives "
          f"{'unreadable' if start_count is None else start_count}. Map zoom {zoom:.2f}. "
          f"{len(candidates)} node(s) carry it: {[(c[0], c[1]) for c in candidates]}")

    if not candidates:
        print(f"[ExpMap] No {label} nodes found on this map - keeping the default route.")
        health.save_debug_screenshot(f"expedition_no_{material}_nodes", shot)
        return True

    tried = []
    for icon_x, icon_y, conf, margin in candidates[:MAX_CANDIDATES]:
        if config.STOP_REQUESTED:
            return False
        _click_node(icon_x, icon_y, zoom)
        shot = capture_screen()

        if _close_description_if_open(shot):
            # The click hit the icon rather than the node. The route may or may not
            # have changed, so this node's reading is unknown - skip it.
            continue
        if not _map_is_open(shot):
            print("[ExpMap] The map closed unexpectedly - reopening it.")
            if open_map() is not True:
                return not config.STOP_REQUESTED
            continue

        count = read_material_count(shot, material)
        print(f"[ExpMap]   node at ({icon_x}, {icon_y + _node_offset(zoom)}) -> "
              f"{label} {'unreadable' if count is None else count} (icon {conf:.2f}, margin {margin:.2f})")
        tried.append(((icon_x, icon_y), count))

    readable = [t for t in tried if t[1] is not None]
    if not readable:
        print(f"[ExpMap] Couldn't read the {label} count after any click - leaving the route as it is.")
        health.save_debug_screenshot("expedition_route_unreadable", capture_screen())
        return True

    best_node, best_count = max(readable, key=lambda t: t[1])
    if start_count is not None and best_count < start_count:
        print(f"[ExpMap] NOTE: the best node found gives {best_count}, less than the default "
              f"route's {start_count}. The default route can't be restored by clicking, so "
              f"taking the best available.")

    if tried and tried[-1][0] != best_node:
        for attempt in range(2):
            _click_node(*best_node, zoom)
            shot = capture_screen()
            _close_description_if_open(shot)
            now = read_material_count(shot, material)
            if now == best_count:
                break
            print(f"[ExpMap] Re-selecting the best node read {now}, expected {best_count} "
                  f"(attempt {attempt + 1}).")

    print(f"[ExpMap] Route set: {label} {start_count} -> {best_count}.")
    return True
