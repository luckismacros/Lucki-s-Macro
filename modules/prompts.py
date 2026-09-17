# modules/prompts.py
"""
Generic on-screen prompt handling - things that can interrupt any flow regardless of
gamemode, the same way a disconnect can: "Click anywhere to continue", the "Game
Results" recovery button, and a leftover party from a different gamemode.
"""
import time
from vision import capture_screen, find_template
from input_controller import click_at
import config

def dismiss_click_anywhere_if_present(screenshot):
    """
    If the "Click anywhere to continue" prompt is on screen, clicks it away repeatedly
    (any point works - it says "anywhere") until it's gone, up to CLICK_ANYWHERE_MAX_CLICKS
    times. Returns True if the prompt was seen at all (whether or not it was fully dismissed).
    """
    match = find_template(screenshot, config.CLICK_ANYWHERE_TEXT, config.MATCH_THRESHOLD, debug_label="click_anywhere")
    if not match:
        return False

    print("[Prompts] 'Click anywhere to continue' detected. Clicking it away...")
    x, y, _ = match

    for attempt in range(config.CLICK_ANYWHERE_MAX_CLICKS):
        click_at(x, y, delay_before=0.05, delay_after=0.3)
        if find_template(capture_screen(), config.CLICK_ANYWHERE_TEXT, config.MATCH_THRESHOLD):
            continue

        # It looked gone, but a fade/transition frame can dip below the match
        # threshold for a moment while the prompt is still actually there - the exact
        # bug where the loop reports "gone" but one more real click was still needed.
        # Confirm it stays gone across a second check before trusting it.
        time.sleep(0.3)
        if not find_template(capture_screen(), config.CLICK_ANYWHERE_TEXT, config.MATCH_THRESHOLD):
            print(f"[Prompts] 'To continue' text gone after {attempt + 1} click(s). Clicking once more in "
                  f"case it switched to 'Click anywhere to close' (different wording, not template-matched)...")
            click_at(x, y, delay_before=0.05, delay_after=0.2)
            return True
        print(f"[Prompts] Prompt reappeared after apparent dismiss (click {attempt + 1}) - still there, continuing.")

    print(f"[Prompts] Prompt still visible after {config.CLICK_ANYWHERE_MAX_CLICKS} clicks - giving up, clicking once more anyway.")
    click_at(x, y, delay_before=0.05, delay_after=0.2)
    return True

def handle_game_results_if_present(screenshot):
    """
    If the "Game Results" recovery button is on screen, clicks it to reopen the
    match-end popup. This turns up when Victory/Defeat/the reward screen gets closed
    before it's been read - the match has already fully ended (waves complete, HUD
    frozen) but nothing reports it, because the Victory/Defeat/reward text that every
    wait actually looks for lives inside the popup that's now gone. Without this, any
    wait for a match result stalls until its stuck timeout, in every gamemode, since
    wait_for_match_result()'s targets never turn up.

    Doesn't need to know what happens next - clicking this just reopens the normal
    popup, and the very next poll tick sees whatever it shows (Victory/Defeat/reward)
    same as if it had never closed. Returns True if the button was seen (and clicked).
    """
    match = find_template(screenshot, config.GAME_RESULTS_BTN, config.MATCH_THRESHOLD, debug_label="game_results")
    if not match:
        return False

    print("[Prompts] 'Game Results' recovery button detected - the match-end popup was "
          "closed before it could be read. Clicking it to reopen...")
    x, y, _ = match
    click_at(x, y, delay_before=0.05, delay_after=0.5)
    return True

def recover_from_leftover_party(screenshot, reclick_entry):
    """
    custom_check for poll_until, used by the step right after whatever button
    actually enters a gamemode (Play for Story/Raids/Challenges, Items for Portals -
    see click_story_card()/click_raid_card()/click_challenge_card() in
    modules/gamemode_select.py and click_items() in modules/portal_select.py).

    That entry button resumes an existing party directly instead of showing the
    gamemode's own menu, if one was left active from a previous manual session in a
    DIFFERENT gamemode - e.g. manually entered Raids, backed out without disbanding,
    then started the Story loop. Play lands back on that Raid party screen, which
    has no Story card on it, so the caller's own target would otherwise just time
    out after config.TIMEOUT_SECONDS with no idea why. Portals' Items button turned
    out to share the exact same party panel (confirmed: config.DISBAND_BTN and
    config.START_PARTY_BTN both match Portals' own party screen), so it's just as
    exposed to this as Story/Raids/Challenges are.

    reclick_entry is the caller's own zero-argument function to press that same
    entry button again once we're confirmed back at the raw lobby - it's the one
    piece that differs per gamemode, which is why it isn't hardcoded here.

    Always returns None - this is never itself the thing the caller is waiting for,
    it just clears the way so the real target can appear on a later tick of the
    same poll.
    """
    match = find_template(screenshot, config.DISBAND_BTN, config.MATCH_THRESHOLD, debug_label="disband_btn")
    if not match:
        return None

    x, y, confidence = match
    print(f"[recover] A different gamemode's party is still active (found Disband at "
          f"({x}, {y}), confidence={confidence:.2f}) - disbanding it.")

    # Confirmed gone rather than assumed: a single click plus a flat sleep is
    # exactly the bug this project already hit once before with "Click anywhere to
    # continue" (see dismiss_click_anywhere_if_present) - a fade/transition frame
    # can dip the template below threshold for a moment while the button is still
    # actually there, and a slower client may just need more than one click's worth
    # of time to actually process the disband.
    still_there = True
    for attempt in range(5):
        click_at(x, y, delay_before=0.05, delay_after=0.6)
        still_there = find_template(capture_screen(), config.DISBAND_BTN, config.MATCH_THRESHOLD) is not None
        if not still_there:
            break
        print(f"[recover] Disband still visible after click {attempt + 1} - retrying.")

    if still_there:
        print("[recover] Disband button never went away after 5 clicks - giving up on "
              "this recovery attempt; the caller's own poll will keep retrying.")
        return None

    # Whatever screen disbanding actually lands on isn't guaranteed - it might be
    # the raw lobby (needs a fresh entry click) or it might drop straight onto the
    # screen this poll is already waiting for. Checking first instead of
    # unconditionally calling reclick_entry() matters because that's its own
    # poll_until bounded by config.TIMEOUT_SECONDS - blindly calling it when the
    # entry button was never going to appear burns nearly this entire poll's own
    # timeout budget hunting for a button that isn't there, leaving almost no ticks
    # left to notice the real target once it's visible. Either lobby marker counts,
    # same reasoning as modules/reconnect.py's own lobby check: Play and Items are
    # both on the raw open-world lobby regardless of which one the caller cares about.
    time.sleep(0.5)
    post_disband_shot = capture_screen()
    at_lobby = (find_template(post_disband_shot, config.PLAY_BTN, config.MATCH_THRESHOLD)
                or find_template(post_disband_shot, config.ITEMS_BTN, config.MATCH_THRESHOLD))
    if at_lobby:
        print("[recover] Back at the lobby - retrying entry.")
        if not reclick_entry():
            print("[recover] Entry click didn't register after disbanding - the caller's own "
                  "poll will keep retrying on the next tick.")
    else:
        print("[recover] Disbanded - not back at the raw lobby, so skipping the re-click "
              "and letting this poll check for its own target directly.")
    return None
