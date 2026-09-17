# modules/lobby.py
"""
Getting from wherever a finished run left the game back to the lobby, so the queue
can start its next step (which may be a different gamemode) from a known screen.

Best effort, verified at every step: it only ever reports success once the lobby's
Play or Items button is actually on screen. The route out of a result screen is the
same one the Challenges flow already uses (Exit -> the in-game Play button -> the
party screen), finished with Disband, which is what returns a party to the lobby.
"""
import time

import config
from input_controller import click_at
from vision import capture_screen, find_template
from modules.prompts import dismiss_click_anywhere_if_present, handle_game_results_if_present
from modules import health
from modules.gamemode_select import click_view_party, click_change_gamemode

RESULT_SCREEN_TEMPLATES = ("VICTORY_TEXT", "DEFEAT_TEXT", "REPEAT_STAGE_BTN", "NEXT_STAGE_BTN", "PORTAL_SELECT_BTN")

# next_mode -> the card that shows once the mode-card chooser is up. Used to
# recognise that the chooser is ALREADY open (reached via Change Gamemode - see
# below) rather than waiting forever for a raw Play button that isn't coming back.
# Portals has no card here at all (see the docstring on return_to_lobby) - it isn't
# in this map on purpose.
_MODE_CARD = {
    "story": "STORY_CARD",
    "raids": "RAID_CARD",
    "challenges": "CHALLENGE_CARD",
    "expeditions": "EXPEDITIONS_CARD",
}


def _find(shot, name):
    path = getattr(config, name, None)
    return find_template(shot, path, config.MATCH_THRESHOLD) if path else None


def at_lobby(shot=None):
    shot = capture_screen() if shot is None else shot
    return bool(_find(shot, "PLAY_BTN") or _find(shot, "ITEMS_BTN"))


def return_to_lobby(timeout=60.0, log=print, next_mode=None):
    """
    True once the QUEUE's next step can start; False on timeout or a stop.

    next_mode names the gamemode the queue is about to move into ("portals",
    "story", "raids", "challenges", "expeditions"). Where that changes which button
    continues past the lobby, it's used: Portals opens through the Items panel, every
    other mode through the small in-lobby Play button - so "back at the lobby" for a
    step going into Portals means Items is on screen, not Play.

    Also true once the general mode-card chooser is already up (next_mode's own card
    is on screen) - not just the raw pre-Play lobby. Play Small (after Exit) doesn't
    always land on the raw lobby: confirmed live, it can drop straight onto whichever
    single gamemode was last active (e.g. still showing Expeditions specifically),
    which has no Play button of its own to find and no way back to the raw lobby
    either - only a "Change Gamemode" button that jumps to the SAME card chooser
    clicking Play produces. Recognising that chooser as a second valid destination,
    and clicking Change Gamemode to reach it when stuck, is what closes that dead
    end. click_play() (modules/gamemode_select.py) knows to treat the chooser
    already being up as its own success too, so the next step's own navigation
    doesn't then fail trying to click a Play button that was already used.
    """
    continue_btn = "ITEMS_BTN" if next_mode == "portals" else "PLAY_BTN"
    small_btn = "ITEMS_SMALL_BTN" if next_mode == "portals" else "PLAY_SMALL_BTN"
    card = _MODE_CARD.get(next_mode)

    deadline = time.time() + timeout
    exits = 0
    while time.time() < deadline and not config.STOP_REQUESTED:
        shot = capture_screen()
        if _find(shot, continue_btn):
            log("Back at the lobby.")
            return True
        if card and _find(shot, card):
            log("The mode chooser is already up (reached via Change Gamemode).")
            return True

        if dismiss_click_anywhere_if_present(shot):
            time.sleep(1.0)
            continue

        # The match-end popup (Victory/Defeat/reward) got closed before it was read -
        # confirmed live: a queue step can end (hit its run limit, or "All selected
        # challenges complete!") at the exact moment this recovery button is up
        # instead of the popup itself, which means NONE of RESULT_SCREEN_TEMPLATES
        # below are on screen to react to - nothing else in this loop would ever
        # click anything again, and the wait just timed out looking like a dead end.
        # Reopening it puts Victory/Defeat text back for that check to find.
        if handle_game_results_if_present(shot):
            time.sleep(1.0)
            continue

        # The post-match summary card's own close button - shows up between a
        # finished match and the lobby on Story/Raids/Portals, on top of whatever
        # button would otherwise be clicked next.
        close_btn = _find(shot, "CLOSE_BTN")
        if close_btn:
            log("Closing the match summary...")
            click_at(close_btn[0], close_btn[1])
            time.sleep(1.5)
            continue

        disband = _find(shot, "DISBAND_BTN")
        if disband:
            log("Leaving the party (Disband)...")
            click_at(disband[0], disband[1])
            time.sleep(2.0)
            continue

        # Leaving a Challenges pass that ended without playing anything (every
        # selected slot on cooldown) - can take one Back click (heading to a
        # non-Portals mode) or two in a row (heading to Portals) before the next
        # button shows. Clicked every time it's seen, so either count is covered.
        back = _find(shot, "BACK_BTN")
        if back:
            log("Clicking Back...")
            click_at(back[0], back[1])
            time.sleep(1.5)
            continue

        # Challenges' own victory screen doesn't always offer the generic Close (X) -
        # "View Party" is its proven-live way out instead (see
        # gamemode_select.click_view_party()'s own docstring: confirmed to lead straight
        # to Change Gamemode, no separate Exit/Close needed). Tried here as a second
        # route off that screen, alongside (not instead of) the Close/Items-small path
        # above - whichever button the screen actually has gets taken.
        if _find(shot, "VIEW_PARTY_BTN"):
            log("Leaving via View Party...")
            click_view_party()
            click_change_gamemode()
            time.sleep(1.0)
            continue

        small = _find(shot, small_btn)
        if small:
            click_at(small[0], small[1], clicks=2)
            # Confirmed live: this small icon (Items or Play) leads back to the same
            # screen the big lobby button would - just keep waiting for continue_btn
            # to show up normally, exactly like every other mode. An earlier version
            # special-cased Portals here (treating this click alone as the handoff),
            # which skipped the wait this screen actually still needs.
            time.sleep(2.0)
            continue

        # Stuck on a specific gamemode's own screen rather than the raw lobby or the
        # card chooser (see the docstring above). Tried regardless of next_mode -
        # including "portals": the chooser this leads to is also where the raw
        # ITEMS_BTN/PLAY_BTN lobby buttons live (continue_btn is re-checked fresh every
        # tick above), so it's still worth reaching even though Portals has no card of
        # its own to recognise there. Two crops of the same button are tried (see
        # gamemode_select.click_change_gamemode()'s own note) since either one might be
        # the one that matches here.
        change_gamemode = _find(shot, "CHANGE_GAMEMODE_QUEUE_BTN") or _find(shot, "CHANGE_GAMEMODE_BTN")
        if change_gamemode:
            log("Stuck on a single gamemode's screen - clicking Change Gamemode...")
            click_at(change_gamemode[0], change_gamemode[1])
            time.sleep(1.5)
            continue

        if exits < 3 and any(_find(shot, name) for name in RESULT_SCREEN_TEMPLATES):
            log("Leaving the result screen (Exit)...")
            click_at(config.CHALLENGE_EXIT_X, config.CHALLENGE_EXIT_Y)
            exits += 1
            time.sleep(2.5)
            continue

        time.sleep(1.0)

    if not config.STOP_REQUESTED:
        path = health.save_debug_screenshot("return_to_lobby_stuck")
        log(f"Couldn't find the way back to the lobby for the next queue step "
            f"(screen saved: {path or config.DEBUG_DIR}).")
    return False
