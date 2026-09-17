# modules/portal_reward.py
"""
Post-match Portal reward picking: 3 portal options appear (auto_select.png signals
this), optionally hover each (no click - clicking picks it) to check for the
traitless trait and skip those. Reopening the portal picker afterward is
click_select_portal() in modules/portal_select.py.
"""
import time
import random
from vision import capture_screen, find_template
from input_controller import click_at, drift_hover, move_mouse_to
from modules.prompts import dismiss_click_anywhere_if_present
from modules import health
import config

def _is_traitless(x, y):
    """
    Hovers the slot and checks for traitless.png, with a second re-check after a
    short extra wait before trusting a "not traitless" read - the trait tooltip can
    take a moment to render, and a too-early screenshot was producing false
    negatives (picking traitless portals the check should have skipped).

    Moves to a neutral point first so this registers as a genuine mouseover
    transition - jumping straight from one slot to the next can skip the previous
    slot's "mouse leave" event, leaving its tooltip on screen and making this slot
    falsely read as traitless too. The actual hover onto the slot uses drift_hover
    (approaches from outside instead of teleporting) since the trait tooltip only
    reliably appeared when genuinely/manually hovered, not from an instant jump.
    """
    # The neutral point is a raw move rather than going through click_at/drift_hover,
    # so it needs the reference->screen conversion applied by hand. move_mouse_to works
    # on any monitor (pydirectinput.moveTo only reaches the primary one).
    move_mouse_to(*config.to_screen(*config.PORTAL_REWARD_NEUTRAL_POINT))
    time.sleep(0.15)
    drift_hover(x, y)
    time.sleep(0.6)

    shot = capture_screen()
    first = find_template(shot, config.TRAITLESS_TEXT, config.MATCH_THRESHOLD, debug_label="traitless")
    if config.DEBUG_TRAITLESS:
        conf = _best_confidence(shot)
        health.save_debug_screenshot(f"traitless_hover_{x}_{y}_conf{conf:.3f}", shot)

    if first:
        return True

    time.sleep(0.3)
    return find_template(capture_screen(), config.TRAITLESS_TEXT, config.MATCH_THRESHOLD) is not None


def _best_confidence(screenshot):
    """Best traitless confidence in this frame, for naming the debug screenshot."""
    try:
        import cv2
        from vision import _load_template
        result = cv2.matchTemplate(screenshot, _load_template(config.TRAITLESS_TEXT), cv2.TM_CCOEFF_NORMED)
        return float(cv2.minMaxLoc(result)[1])
    except Exception:
        return 0.0

def pick_portal_reward(avoid_traitless=True):
    """
    Picks one of the 3 post-match reward portals. If avoid_traitless, checks slots
    in order and picks the first one that isn't traitless (no need to check the
    rest once a safe one is found); if all 3 are traitless, picks randomly since
    there's no way to avoid it. If avoid_traitless is False, picks randomly without
    hovering/checking at all.
    There's a ~10s auto-pick cooldown in-game, so this needs to move fast.
    Returns the index (0-2) of the slot clicked.
    """
    dismiss_click_anywhere_if_present(capture_screen())
    slots = config.PORTAL_REWARD_SLOTS

    if avoid_traitless:
        chosen = None
        for i, (x, y) in enumerate(slots):
            if config.STOP_REQUESTED:
                break
            if not _is_traitless(x, y):
                chosen = i
                break
        if chosen is None:
            chosen = random.choice(range(len(slots)))
    else:
        chosen = random.choice(range(len(slots)))

    x, y = slots[chosen]
    print(f"[PortalReward] Picking slot {chosen + 1} at ({x}, {y}).")
    click_at(x, y)
    return chosen
