# modules/gamemode_select.py
"""
Module 2: Gamemode + Map + Act selection.
Story's and Raids' map/act/difficulty selection is entirely vision-driven - every
click finds its target's artwork or icon on screen first (see
click_map/click_act/click_difficulty/click_raid/click_raid_difficulty below), rather
than clicking a fixed point measured on one reference screen. That's what keeps it
working across different resolutions and aspect ratios without per-machine
recalibration. Challenges still uses fixed coordinates for its categories/slots and
hasn't been converted yet.
"""
import time
import cv2
from vision import capture_screen, find_template
from input_controller import click_at, scroll_maps_right, scroll_maps_left
from modules.prompts import dismiss_click_anywhere_if_present, recover_from_leftover_party
from modules.polling import poll_until, target, settle_match
from modules import health
import config

def _wait_and_click(template_path, step_name, clicks=1, recover_leftover_party=False):
    """
    Shared poll-until-found-then-click. Bounded by config.TIMEOUT_SECONDS, so a
    missing button here is a normal negative result (the caller decides what it
    means) rather than a fault - which is why no stuck limit is layered on top.

    recover_leftover_party=True adds prompts.recover_from_leftover_party() as this
    poll's custom_check, re-clicking Play if it fires - only meaningful for the step
    right after click_play(), which is the one that can land on a stray party screen
    instead of the cards it expects.

    Returns True / False / "RECONNECTED".
    """
    custom_check = (lambda shot: recover_from_leftover_party(shot, click_play)) if recover_leftover_party else None
    result = poll_until(
        # debug_label makes find_template report its best confidence even on a failed
        # match. Without it a navigation step that never finds its button says only
        # "TIMEOUT", which cannot distinguish a stale template from a slightly strict
        # threshold from the window being scaled wrong - the three causes that look
        # identical from the outside and need completely different fixes.
        [target(template_path, True, click=True, clicks=clicks, debug_label=step_name)],
        interval=config.POLL_INTERVAL,
        label=step_name,
        timeout=config.TIMEOUT_SECONDS,
        stuck_timeout=None,
        custom_check=custom_check,
    )
    if result == "TIMEOUT":
        print(f"[{step_name}] TIMEOUT: template not found.")
        return False
    return result

# Any one of these being on screen means the mode-card chooser is already up - see
# click_play()'s own tolerance for reaching it a different way (Change Gamemode,
# from the run queue - see modules/lobby.return_to_lobby()) below.
_MODE_CARD_TEMPLATES = (config.STORY_CARD, config.RAID_CARD, config.CHALLENGE_CARD, config.EXPEDITIONS_CARD)


def click_play():
    """
    Double-clicked: this is the very first click after leaving the GUI window, and the
    first click into an unfocused Roblox window often just focuses it instead of
    registering.

    Skipped entirely when the mode-card chooser is already open - the run queue can
    land here via Change Gamemode instead of a raw Play click (see
    modules/lobby.return_to_lobby()), and there is no Play button left to find at
    that point; clicking Play a second time from on top of the chooser has no target
    and would just waste this step's whole timeout before failing.
    """
    shot = capture_screen()
    if any(find_template(shot, t, config.MATCH_THRESHOLD) for t in _MODE_CARD_TEMPLATES):
        print("[click_play] The mode cards are already showing - not clicking Play again.")
        return True
    return _wait_and_click(config.PLAY_BTN, "click_play", clicks=2)

def click_play_small():
    """
    The Play button shown after backing out of a match via the victory-screen Exit
    button looks different (smaller) from the lobby's normal Play button.
    """
    return _wait_and_click(config.PLAY_SMALL_BTN, "click_play_small", clicks=2)

def click_story_card():
    return _wait_and_click(config.STORY_CARD, "click_story_card", recover_leftover_party=True)

def _find_map(template, debug_label):
    return find_template(capture_screen(), template, config.MATCH_THRESHOLD, debug_label=debug_label)

# One sweep step, in wheel notches. Smaller than a full page on purpose: the point is
# to never step over an entry, and the sweep stops itself the moment the list stops
# moving, so an over-small step costs a little time and an over-large one costs a map.
CAROUSEL_STEP_NOTCHES = 6
# Hard cap on sweep steps, so a carousel that somehow keeps changing can't loop forever.
CAROUSEL_MAX_STEPS = 12
# Mean per-pixel difference between two frames above which the screen is judged to
# have actually changed. Comfortably above camera/animation jitter, far below a real
# scroll.
CAROUSEL_MOVED_THRESHOLD = 1.0


def _frames_differ(before, after):
    if before is None or after is None or before.shape != after.shape:
        return True
    return float(cv2.absdiff(before, after).mean()) > CAROUSEL_MOVED_THRESHOLD


def _carousel_anchor(entries, exclude_key):
    """
    A REFERENCE-space point to park the cursor on before wheel-scrolling: the position
    of any OTHER entry's card that is currently on screen.

    The wheel goes to whatever is under the cursor, so this has to be over the
    carousel itself or the scroll does nothing at all. A card we can actually see is
    over it by definition, which a hand-measured constant only is until the game moves
    the row - that constant (config.MAP_CAROUSEL_HOVER_X/Y) is the fallback for when
    nothing at all is recognised, not the first choice.
    """
    shot = capture_screen()
    for key, data in entries.items():
        if key == exclude_key or not data.get("enabled"):
            continue
        hit = find_template(shot, data["template"], config.MATCH_THRESHOLD)
        if hit:
            return (hit[0], hit[1])
    return None


def _sweep_carousel(template, debug_label, entries, key):
    """
    Looks for `template` across the whole carousel: rewinds to the start, then steps
    towards the end, checking after every step.

    Replaces an earlier "scroll all the way one way, then all the way the other"
    which assumed exactly two pages - with a third page (or an entry sitting mid-way
    between the two extremes) the middle of the list was never looked at, and the
    only symptom was a plain "could not find" on a map that was simply off-view.

    Stops early when a scroll leaves the screen unchanged, which is what hitting the
    end of the list looks like from here - there's no scrollbar to read.
    Returns the match tuple, or None.
    """
    anchor = _carousel_anchor(entries, key)
    if anchor:
        print(f"[{debug_label}] Not in view - scrolling the carousel (cursor over a card at {anchor}).")
    else:
        anchor = (config.MAP_CAROUSEL_HOVER_X, config.MAP_CAROUSEL_HOVER_Y)
        print(f"[{debug_label}] Not in view, and no other card recognised either - scrolling with the "
              f"configured hover point {anchor}. If nothing scrolls, that point is the thing to "
              f"re-measure (tools/coord_finder.py).")

    scroll_maps_left(hover=anchor, notches=CAROUSEL_STEP_NOTCHES * CAROUSEL_MAX_STEPS)
    previous = capture_screen()

    for step in range(CAROUSEL_MAX_STEPS):
        match = find_template(previous, template, config.MATCH_THRESHOLD, debug_label=debug_label)
        if match:
            return match

        scroll_maps_right(hover=anchor, notches=CAROUSEL_STEP_NOTCHES)
        current = capture_screen()
        if not _frames_differ(previous, current):
            # Either the end of the list, or the wheel isn't reaching the carousel at
            # all - the two look identical from here, so say both.
            print(f"[{debug_label}] Carousel stopped moving after {step + 1} step(s) - either the end "
                  f"of the list, or the scroll isn't reaching it (cursor was at {anchor}).")
            return None
        previous = current

    return find_template(previous, template, config.MATCH_THRESHOLD, debug_label=debug_label)


def click_map(map_key):
    """
    Finds the map's card on the carousel and clicks it - no page/slot math, so the
    carousel can be reordered or start on any page without breaking this.
    """
    if config.STOP_REQUESTED:
        return False

    map_data = config.MAPS.get(map_key)
    if not map_data or not map_data["enabled"]:
        print(f"[click_map] Map '{map_key}' not enabled.")
        return False

    template = map_data["template"]
    debug_label = f"map_{map_key}"
    match = _find_map(template, debug_label)

    if not match:
        match = _sweep_carousel(template, debug_label, config.MAPS, map_key)

    if not match:
        print(f"[click_map] Could not find '{map_key}' on screen.")
        health.report_missing_template(capture_screen(), debug_label, template)
        return False

    # The carousel glides after a scroll - click where the card stops, not where it passed.
    match = settle_match(template, match, debug_label) or match
    x, y, confidence = match
    print(f"[click_map] Found '{map_key}' at ({x}, {y}), confidence={confidence:.2f} - clicking.")
    click_at(x, y)
    time.sleep(0.5)
    return True

def click_act(map_key, act_key):
    """
    Finds the act's numbered tile in the act list and clicks it. The template is
    cropped above the star row, so it matches the tile at any completion level -
    but it still requires the tile to actually be on screen, which a locked act
    normally is (just without stars yet), so this doesn't distinguish locked from
    unlocked. A locked act simply won't advance past this click; the Select Stage
    wait downstream will time out and report the failure.
    """
    if config.STOP_REQUESTED:
        return False

    template = config.ACT_TEMPLATES.get(act_key)
    if not template:
        print(f"[click_act] Unknown act key: '{act_key}'")
        return False

    debug_label = f"act_{act_key}"
    match = find_template(capture_screen(), template, config.MATCH_THRESHOLD, debug_label=debug_label)
    if not match:
        print(f"[click_act] Could not find '{act_key}' tile on screen.")
        health.report_missing_template(capture_screen(), debug_label, template)
        return False

    match = settle_match(template, match, debug_label) or match
    x, y, confidence = match
    print(f"[click_act] Found '{act_key}' at ({x}, {y}), confidence={confidence:.2f} - clicking.")
    click_at(x, y)
    return True

def click_difficulty(difficulty):
    """Story stages only - finds and clicks the Normal or Hard difficulty button."""
    if config.STOP_REQUESTED:
        return False

    template = config.HARD_DIFFICULTY_TEMPLATE if difficulty == "hard" else config.NORMAL_DIFFICULTY_TEMPLATE
    debug_label = f"difficulty_{difficulty}"
    match = find_template(capture_screen(), template, config.MATCH_THRESHOLD, debug_label=debug_label)
    if not match:
        print(f"[click_difficulty] Could not find '{difficulty}' button on screen.")
        health.report_missing_template(capture_screen(), debug_label, template)
        return False

    match = settle_match(template, match, debug_label) or match
    x, y, confidence = match
    print(f"[click_difficulty] Found '{difficulty}' at ({x}, {y}), confidence={confidence:.2f} - clicking.")
    click_at(x, y)
    return True

def click_select_stage():
    return _wait_and_click(config.SELECT_STAGE_BTN, "click_select_stage")

def run_flow(map_key, act_key, difficulty="normal"):
    """Story flow. Returns True/False/"RECONNECTED" (propagated from any step that detects a disconnect)."""
    steps = [
        click_play,
        click_story_card,
        lambda: click_map(map_key),
        lambda: click_act(map_key, act_key),
        lambda: click_difficulty(difficulty),
        click_select_stage,
    ]
    return _run_steps(steps)

def click_raid_card():
    return _wait_and_click(config.RAID_CARD, "click_raid_card", recover_leftover_party=True)

def click_raid(raid_key):
    """
    Finds the raid's card art and clicks it - same approach as Story's click_map(),
    including the scroll-search: with a second raid now in the game, the carousel
    can scroll past one page just like Story's map list.
    """
    if config.STOP_REQUESTED:
        return False

    raid_data = config.RAIDS.get(raid_key)
    if not raid_data or not raid_data["enabled"]:
        print(f"[click_raid] Raid '{raid_key}' not enabled.")
        return False

    dismiss_click_anywhere_if_present(capture_screen())
    template = raid_data["template"]
    debug_label = f"raid_{raid_key}"
    match = find_template(capture_screen(), template, config.MATCH_THRESHOLD, debug_label=debug_label)

    if not match:
        match = _sweep_carousel(template, debug_label, config.RAIDS, raid_key)

    if not match:
        print(f"[click_raid] Could not find '{raid_key}' on screen.")
        health.report_missing_template(capture_screen(), debug_label, template)
        return False

    match = settle_match(template, match, debug_label) or match
    x, y, confidence = match
    print(f"[click_raid] Found '{raid_key}' at ({x}, {y}), confidence={confidence:.2f} - clicking.")
    click_at(x, y)
    return True

def click_raid_difficulty(difficulty):
    """Finds and clicks a raid's difficulty tile (1/2/3) - same approach as Story's click_act()."""
    if config.STOP_REQUESTED:
        return False

    template = config.RAID_DIFFICULTY_TEMPLATES.get(difficulty)
    if not template:
        print(f"[click_raid_difficulty] Unknown difficulty: '{difficulty}'")
        return False

    dismiss_click_anywhere_if_present(capture_screen())
    debug_label = f"raid_difficulty_{difficulty}"
    match = find_template(capture_screen(), template, config.MATCH_THRESHOLD, debug_label=debug_label)
    if not match:
        print(f"[click_raid_difficulty] Could not find difficulty '{difficulty}' tile on screen.")
        health.report_missing_template(capture_screen(), debug_label, template)
        return False

    match = settle_match(template, match, debug_label) or match
    x, y, confidence = match
    print(f"[click_raid_difficulty] Found difficulty '{difficulty}' at ({x}, {y}), confidence={confidence:.2f} - clicking.")
    click_at(x, y)
    return True

def run_raid_flow(raid_key, difficulty="1"):
    """Raid flow. Returns True/False/"RECONNECTED" (propagated from any step that detects a disconnect)."""
    steps = [
        click_play,
        click_raid_card,
        lambda: click_raid(raid_key),
        lambda: click_raid_difficulty(difficulty),
        click_select_stage,
    ]
    return _run_steps(steps)

def _run_steps(steps):
    for step in steps:
        if config.STOP_REQUESTED:
            return False
        result = step()
        if result == "RECONNECTED":
            return "RECONNECTED"
        if not result:
            return False
    return True

def click_view_party():
    """
    "View Party" on the challenge's own victory screen - used to advance to a
    different challenge instead of repeating the same one.

    Confirmed live: clicked directly from the victory screen, this is what actually
    leads to Change Gamemode - no separate Exit/Close or Play Small click needed
    first. That two-step Exit-then-Play-Small route (and, before it, a blind fixed-
    coordinate "Exit" click) is what left both the standalone Challenges loop and
    the run queue unable to get past a finished challenge at all.
    """
    return _wait_and_click(config.VIEW_PARTY_BTN, "click_view_party")

def click_change_gamemode():
    """
    The button that returns from a specific gamemode's own screen to the general
    mode-card chooser. Two crops of it exist - config.CHANGE_GAMEMODE_BTN (the
    original) and config.CHANGE_GAMEMODE_QUEUE_BTN (captured later, for the run
    queue's own transition - see modules/lobby.return_to_lobby()) - tried together
    here since either one is the same button and whichever actually matches on a
    given screen should still get the click through.
    """
    result = poll_until(
        [target(config.CHANGE_GAMEMODE_BTN, True, click=True, debug_label="click_change_gamemode"),
         target(config.CHANGE_GAMEMODE_QUEUE_BTN, True, click=True, debug_label="click_change_gamemode_queue")],
        interval=config.POLL_INTERVAL, label="click_change_gamemode",
        timeout=config.TIMEOUT_SECONDS, stuck_timeout=None,
    )
    if result == "TIMEOUT":
        print("[click_change_gamemode] TIMEOUT: template not found.")
        return False
    return result

def click_challenge_card():
    return _wait_and_click(config.CHALLENGE_CARD, "click_challenge_card", recover_leftover_party=True)

def click_challenge_category(category_key):
    if config.STOP_REQUESTED:
        return False

    category_data = config.CHALLENGE_CATEGORIES.get(category_key)
    if not category_data:
        print(f"[click_challenge_category] Unknown category: '{category_key}'")
        return False

    target_x, target_y = category_data["x"], category_data["y"]
    print(f"[click_challenge_category] Clicking '{category_key}' at ({target_x}, {target_y})")
    click_at(target_x, target_y)
    return True

def click_challenge_slot(slot_key):
    if config.STOP_REQUESTED:
        return False

    slot_data = config.CHALLENGE_SLOTS.get(slot_key)
    if not slot_data:
        print(f"[click_challenge_slot] Unknown slot: '{slot_key}'")
        return False

    target_x, target_y = slot_data["x"], slot_data["y"]
    print(f"[click_challenge_slot] Clicking '{slot_key}' at ({target_x}, {target_y})")
    click_at(target_x, target_y)
    return True

def is_challenge_on_cooldown():
    """True if the 'Available in ...' text is showing instead of the Select Stage button."""
    screenshot = capture_screen()
    match = find_template(screenshot, config.AVAILABLE_IN_TEXT, config.MATCH_THRESHOLD, debug_label="available_in")
    return match is not None

def click_challenge_back():
    if config.STOP_REQUESTED:
        return False
    print(f"[click_challenge_back] Clicking Back at ({config.CHALLENGE_BACK_X}, {config.CHALLENGE_BACK_Y})")
    click_at(config.CHALLENGE_BACK_X, config.CHALLENGE_BACK_Y)
    return True

def _category_slot_count(category_key):
    return sum(1 for v in config.CHALLENGE_SLOTS.values() if v["category"] == category_key)

def run_challenge_slot_flow(slot_key, nav_context, previous_category=None):
    """
    Navigates to one challenge slot, then checks whether it's already on cooldown.
    nav_context controls how we get there - the screen you land on differs depending
    on what just happened:
      "cold"        - very first entry this run: Play -> Challenge Card -> category -> slot.
      "after_match" - just finished a match, still on its victory screen: View Party ->
                       Change Gamemode -> Challenge Card -> category -> slot. Lands
                       directly on the mode-card chooser - NOT the raw lobby.
      "after_skip"  - just clicked Back off an on-cooldown slot's info screen. Where that
                       lands depends on the category we just backed out of:
                         - multi-slot category (Regular, 3 slots): lands on THAT SAME
                           category's own slot list (confirmed). If the next slot is in
                           the same category, click it directly.
                         - single-slot category (Daily/Weekly, 1 slot): lands one level
                           up, on the Challenge Card's category picker (confirmed - this
                           is what was causing the loop to fail after checking Weekly).
                           Just needs a category click, not a full re-entry.
                       Crossing from a multi-slot category into a different category is
                       still untested territory - falls back to the "after_match" sequence
                       as a best guess, but that may need fixing once someone hits it.
    Returns:
      True          - reached the Select Stage screen for this slot, ready to click it.
      False         - navigation failed or was cancelled.
      "RECONNECTED" - a disconnect was detected and resolved; caller must retry from the lobby.
      "SKIP"        - this slot is on cooldown ("Available in..."); Back was already clicked.
    """
    slot_data = config.CHALLENGE_SLOTS.get(slot_key)
    if not slot_data:
        print(f"[run_challenge_slot_flow] Unknown challenge slot: '{slot_key}'")
        return False

    category = slot_data["category"]
    steps = []

    if nav_context == "after_skip" and category == previous_category and _category_slot_count(previous_category) > 1:
        pass  # Multi-slot category (Regular) - Back landed on this category's own slot list.
    elif nav_context == "after_skip":
        # Still inside the Challenges panel: Back landed either on the category picker
        # (after Daily/Weekly) or on Regular's slot list, which has the same category tabs
        # beside it. Either way the next category is one click away.
        #
        # Crossing from Regular into Daily used to fall through to the "after_match"
        # route below - Exit, then wait for the in-game Play button - which clicked Exit
        # on a panel that has none and then waited 13s for a button that was never going
        # to appear. That is what ended every Challenges loop whose second pass found all
        # three Regular challenges on cooldown (log 2026-09-14 20:41:48).
        steps.append(lambda: click_challenge_category(category))
    elif nav_context == "cold":
        steps.append(click_play)
        steps.append(click_challenge_card)
        steps.append(lambda: click_challenge_category(category))
    else:
        # "after_match", or "after_skip" crossing from a multi-slot category into a
        # different category (best-effort guess)
        steps.append(click_view_party)
        steps.append(click_change_gamemode)
        steps.append(click_challenge_card)
        steps.append(lambda: click_challenge_category(category))

    steps.append(lambda: click_challenge_slot(slot_key))

    result = _run_steps(steps)
    if result != True:
        return result

    if config.STOP_REQUESTED:
        return False

    time.sleep(1.0)  # let the result screen settle before checking for the cooldown text
    if is_challenge_on_cooldown():
        print(f"[run_challenge_slot_flow] '{slot_key}' is on cooldown. Clicking Back.")
        click_challenge_back()
        return "SKIP"

    return click_select_stage()