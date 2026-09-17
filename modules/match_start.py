# modules/match_start.py
"""
Module 3: Party screen execution.
"""
from vision import capture_screen, find_template
from input_controller import click_at
from modules.prompts import dismiss_click_anywhere_if_present
from modules.polling import poll_until, target, settle_match, click_until_gone
import config

def _click_start_button(step_name, fixed_point=None):
    """
    Shared by click_start_party(), click_raid_start() and click_portal_start():
    Story's "Start Party", Raids' "Start" and Portals' "Start" all turned out to be
    the exact same button graphic (confirmed by testing config.START_PARTY_BTN
    against all three party screens at 0.92-1.000 confidence), just rendered at
    whatever position that particular party screen's layout happens to put it -
    which varies with how much reward/party content sits above it. One template,
    found live, covers all three instead of three guessed-and-measured points.

    fixed_point overrides the measured point every OTHER screen shares (see
    config.START_BTN_X/Y) - Raids needs its own (config.RAID_START_BTN_X/Y): a
    6-player party (3 rows) pushes Start noticeably lower than a 4-player one (2
    rows) does, further than the shared point's own drift tolerance reliably covers.

    This used to be a single screenshot-and-match with no wait and no retry - every
    other button click in this codebase polls for its target and re-clicks if the
    first click doesn't take (see modules/polling.py), but this one didn't, and the
    party screen sliding/rendering in is exactly the kind of thing a single instant
    check can lose a race against - especially on a slower machine or connection.
    A miss here used to come back as a bare False that every caller then silently
    ignored (see the fixes in engine.py alongside this one), which is how "the bot
    presses Play and just sits there" and "didn't click Start" reports happened with
    nothing in the log to explain why. Polling (like modules/expedition.py's
    click_expedition_start() already did for this exact button) and retrying the
    click via click_until_gone() closes both gaps at once.

    Returns True / False / "RECONNECTED".
    """
    if config.STOP_REQUESTED:
        print(f"[{step_name}] Operation canceled by user.")
        return False

    result = poll_until(
        [target(config.START_PARTY_BTN, True, debug_label=step_name)],
        interval=0.3, label=step_name, timeout=config.TIMEOUT_SECONDS, stuck_timeout=None,
    )
    if result in ("RECONNECTED", False):
        return result
    if result == "TIMEOUT":
        print(f"[{step_name}] Could not find the Start button on screen.")
        return False

    match = find_template(capture_screen(), config.START_PARTY_BTN, config.MATCH_THRESHOLD, debug_label=step_name)
    if not match:
        print(f"[{step_name}] Could not find the Start button on screen.")
        return False

    match = settle_match(config.START_PARTY_BTN, match, step_name) or match
    mx, my, confidence = match

    # The match proves we're on a party screen; the measured point decides where the
    # cursor goes. Clicking the match centre was landing a few px under the button -
    # close enough to look right, far enough to do nothing at all. See
    # config.START_BTN_X for why a crop-derived centre drifts and a measured one
    # doesn't. If the button has genuinely moved (a taller party screen pushes it
    # down), the drift is way past a few pixels and the live match is used instead.
    x, y = fixed_point or (config.START_BTN_X, config.START_BTN_Y)
    drift = max(abs(mx - x), abs(my - y))
    if drift > config.START_BTN_MAX_DRIFT:
        print(f"[{step_name}] Start button found at ({mx}, {my}), confidence={confidence:.2f} - "
              f"{drift}px from the usual spot ({x}, {y}), so this screen has moved it. "
              f"Clicking where it actually is.")
        x, y = mx, my
    else:
        print(f"[{step_name}] Start button confirmed at ({mx}, {my}), confidence={confidence:.2f} - "
              f"clicking the measured point ({x}, {y}).")

    if click_until_gone(config.START_PARTY_BTN, match, step_name, point=(x, y)):
        return True
    if config.STOP_REQUESTED or (x, y) == (mx, my):
        return False

    # The measured point (x, y) missed every attempt above. Rather than keep hammering
    # a point that plainly isn't landing on the button on THIS screen, fall back to
    # the live match centre - close enough to look right normally isn't even in play
    # any more once the calibrated point has already failed this many times in a row.
    print(f"[{step_name}] The measured point never worked - trying the live match centre "
          f"({mx}, {my}) instead.")
    fresh = find_template(capture_screen(), config.START_PARTY_BTN, config.MATCH_THRESHOLD, debug_label=step_name)
    if not fresh:
        return True  # gone on its own between attempts - nothing left to click
    return click_until_gone(config.START_PARTY_BTN, fresh, step_name, point=(fresh[0], fresh[1]))

def click_start_party():
    return _click_start_button("click_start_party")

def click_raid_start():
    return _click_start_button("click_raid_start", fixed_point=(config.RAID_START_BTN_X, config.RAID_START_BTN_Y))

def click_portal_start():
    return _click_start_button("click_portal_start")

def click_challenge_start(after_match=False):
    """
    Clicks the challenge Start button.

    after_match picks the second position: once a challenge has been completed and the
    next stage selected, the button sits about 43px higher than it does on a cold entry
    from the lobby. Both are fixed coordinates rather than a template match, so getting
    this wrong is silent - the click simply lands on empty UI and the run then waits
    forever for a match that never started.
    """
    if config.STOP_REQUESTED:
        print("[click_challenge_start] Operation canceled by user.")
        return False

    if after_match:
        x, y = config.CHALLENGE_START_AFTER_MATCH_X, config.CHALLENGE_START_AFTER_MATCH_Y
        where = "after-match"
    else:
        x, y = config.CHALLENGE_START_X, config.CHALLENGE_START_Y
        where = "cold-entry"

    dismiss_click_anywhere_if_present(capture_screen())
    print(f"[click_challenge_start] Clicking {where} Start at ({x}, {y})")
    click_at(x, y)
    return True

