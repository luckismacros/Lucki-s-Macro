# modules/portal_select.py
"""
Module: Portal gamemode navigation.

Getting into a portal is: Items -> Portals tab -> type the portal's name into the
search box -> click the first result -> confirm.

Searching is what makes this work at all. The game sorts the filtered list best-first,
so the top result for "Sum" is always the strongest Summer portal you currently own.
The bot therefore never has to know which tiers exist, recognise card artwork, or find
anything in a scrolling list - and it can't run out, because the reward screen keeps
topping the inventory up and the top result simply becomes whatever the best one is
now. That replaced an earlier version that scrolled the list matching card images,
which had to identify animated, colour-cycling cards and could scroll straight past
the portal it wanted.
"""
import time
from input_controller import click_at, type_text
from modules.polling import poll_until, target
from modules.prompts import recover_from_leftover_party
import config

def _wait_and_click(template_path, step_name, clicks=1, recover_leftover_party=False, timeout=None):
    """
    Shared poll-until-found-then-click. Bounded by `timeout` (defaults to
    config.TIMEOUT_SECONDS), so a missing button here is a normal negative result
    (the caller decides what it means) rather than a fault - which is why no stuck
    limit is layered on top.

    recover_leftover_party=True adds prompts.recover_from_leftover_party() as this
    poll's custom_check, re-clicking Items if it fires - only meaningful for
    click_items(), the step that can land on a stray party screen (from Story/Raids/
    Challenges) instead of the Items menu it expects. Confirmed Portals is exposed
    to this too: config.DISBAND_BTN and config.START_PARTY_BTN both match Portals'
    own party screen just as well as Story's/Raids'.

    On a genuine TIMEOUT, saves a debug screenshot before returning - see
    click_select_portal() for why this specific step needs it.

    Returns True / False / "RECONNECTED".
    """
    custom_check = (lambda shot: recover_from_leftover_party(shot, click_items)) if recover_leftover_party else None
    result = poll_until(
        # debug_label makes find_template report its best confidence even on a failed
        # match. Without it a navigation step that never finds its button says only
        # "TIMEOUT", which cannot distinguish a stale template from a slightly strict
        # threshold from the window being scaled wrong - the three causes that look
        # identical from the outside and need completely different fixes.
        [target(template_path, True, click=True, clicks=clicks, debug_label=step_name)],
        interval=config.POLL_INTERVAL,
        label=step_name,
        timeout=timeout if timeout is not None else config.TIMEOUT_SECONDS,
        stuck_timeout=None,
        custom_check=custom_check,
    )
    if result == "TIMEOUT":
        print(f"[{step_name}] TIMEOUT: template not found.")
        from modules import health
        health.save_debug_screenshot(f"timeout_{step_name}")
        return False
    return result

def click_items():
    return _wait_and_click(config.ITEMS_BTN, "click_items", recover_leftover_party=True)

def click_portals_tab():
    if config.STOP_REQUESTED:
        return False
    print(f"[click_portals_tab] Clicking at ({config.PORTALS_TAB_X}, {config.PORTALS_TAB_Y})")
    click_at(config.PORTALS_TAB_X, config.PORTALS_TAB_Y)
    time.sleep(0.5)
    return True

def click_select_portal():
    """
    The "Select" button that reopens the portal picker right after a reward is
    picked. The same button/template is clicked again once a portal is chosen in that
    picker - see select_portal's confirm_template - to confirm it and restart.

    The real on-screen order (confirmed after this was debugged live): the match
    ending shows the reward-pick screen directly - Victory text and the 3 reward
    cards together, not Victory first and cards later. Picking a card is what makes
    THIS button appear, on the screen that follows the pick. So there is a real
    screen transition to wait through here, not just a render delay - given a longer
    timeout than the default nav click to give it room, rather than reporting a
    false failure while that transition is still playing out.
    """
    return _wait_and_click(config.PORTAL_SELECT_BTN, "click_select_portal", timeout=20.0)

def search_portal(category_key):
    """
    Types the portal's search term into the list's search box, so the list filters
    down to that portal with the best one you own at the top. The search box itself
    is at the same point on both entry screens - see select_portal() for the point
    that is not.

    The box is DOUBLE-clicked rather than clicked: on a re-entry it already holds the
    previous term, and a double-click selects that so the new one replaces it instead
    of being appended ("SumSum" would filter to nothing and the first-result click
    would land on an empty list).
    Returns True, or False if the category is unknown/disabled.
    """
    portal_data = config.PORTALS.get(category_key)
    if not portal_data or not portal_data["enabled"]:
        print(f"[search_portal] Portal '{category_key}' not enabled.")
        return False

    term = portal_data["search"]
    print(f"[search_portal] Searching '{term}' for {portal_data['label']} at "
          f"({config.PORTAL_SEARCH_BOX_X}, {config.PORTAL_SEARCH_BOX_Y})")
    click_at(config.PORTAL_SEARCH_BOX_X, config.PORTAL_SEARCH_BOX_Y, clicks=2)
    type_text(term, config.PORTAL_SEARCH_TYPE_DELAY)
    time.sleep(config.PORTAL_SEARCH_FILTER_DELAY)  # let the list finish filtering
    return True

def select_portal(category_key, confirm_template=None, first_result=None):
    """
    Searches for the portal, clicks the first result, then clicks the confirm button.

    Shared by both entry paths: a fresh entry from the lobby confirms with "Activate
    Portal", while the post-reward re-selection (reached via click_select_portal())
    confirms with "Select" - the same button click_select_portal() itself pressed.
    confirm_template defaults to config.ACTIVATE_PORTAL_BTN. first_result, if given,
    is an (x, y) override for where the first result sits on THIS screen - the
    post-reward picker renders its results list shifted left of the lobby's, so the
    lobby's point there was landing on the SECOND result instead. See
    reselect_portal().
    Returns True/False/"RECONNECTED".
    """
    if config.STOP_REQUESTED:
        return False

    if not search_portal(category_key):
        return False

    result_x, result_y = first_result or (config.PORTAL_FIRST_RESULT_X, config.PORTAL_FIRST_RESULT_Y)
    print(f"[select_portal] Clicking the first result at ({result_x}, {result_y})")
    # Double-clicked: a single click sometimes only registered as a hover on a list
    # item - the same hover-registration lag click_at's own jitter usually covers.
    click_at(result_x, result_y, clicks=2)

    confirm_template = confirm_template or config.ACTIVATE_PORTAL_BTN
    return _wait_and_click(confirm_template, "click_portal_confirm")

def run_portal_flow(category_key):
    """
    Navigates from the lobby into the chosen portal's Activate screen (does NOT click
    Start - that's a separate fixed-coordinate click, same pattern as Story/Raids/
    Challenges' own Start buttons).
    Returns True/False/"RECONNECTED".
    """
    if config.STOP_REQUESTED:
        return False

    result = click_items()
    if result != True:
        return result

    if not click_portals_tab():
        return False

    return select_portal(category_key)

def reselect_portal(category_key):
    """
    Portal re-entry after a reward pick. The picker is already open at that point
    (click_select_portal() opened it), so this skips Items/Portals-tab and goes
    straight to searching again - which is also what makes the loop self-sustaining,
    since the top result is re-evaluated every time.

    This picker renders its results list shifted left of the lobby's Items -> Portals
    list, so clicking the lobby's first-result point here was actually landing on the
    SECOND result - picking the wrong portal every time. Uses this screen's own point
    (config.PORTAL_RESELECT_FIRST_RESULT_X/Y) instead.

    Confirms with PORTAL_CONFIRM_SELECT_BTN ("Select", green) rather than
    PORTAL_SELECT_BTN ("Select Portal") - click_select_portal() already used that
    text to reopen this picker, and it doesn't appear a second time. Waiting on it
    again here as the confirm was the bug: the click on the chosen portal register,
    but the confirm never found its button, so the match never actually restarted.
    Returns True/False/"RECONNECTED".
    """
    return select_portal(
        category_key,
        confirm_template=config.PORTAL_CONFIRM_SELECT_BTN,
        first_result=(config.PORTAL_RESELECT_FIRST_RESULT_X, config.PORTAL_RESELECT_FIRST_RESULT_Y),
    )
