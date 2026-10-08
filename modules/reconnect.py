# modules/reconnect.py
"""
Module: disconnect detection and auto-reconnect.
Reconnecting drops you back at the lobby, not the match - the caller is responsible
for redoing the full lobby navigation once handle_disconnect_if_present() returns True.
"""
import time
from vision import capture_screen, find_template
from input_controller import click_at
import input_controller
from modules import stats, health, notify
import config

# How long a disconnect may sit unresolved before a SECOND Discord message goes out
# to say so ("still trying"), and how often after that. A single "disconnected!" at
# the start and then silence for the rest of a long outage is indistinguishable from
# the bot having quietly died too - this is what tells the two apart on a phone
# without needing to remote in and check.
_STILL_TRYING_AFTER = 5 * 60.0


def _find(shot, template, label=None):
    return find_template(shot, template, config.MATCH_THRESHOLD, debug_label=label)


def _at_lobby(shot):
    return bool(_find(shot, config.PLAY_BTN) or _find(shot, config.ITEMS_BTN))


def _wait_click(template, label, timeout):
    """Waits for a button and clicks it. True if clicked, False if it never showed / stop."""
    deadline = time.time() + timeout
    while time.time() < deadline and not config.STOP_REQUESTED:
        match = _find(capture_screen(), template, label)
        if match:
            x, y, conf = match
            print(f"[Reconnect] {label}: clicking at ({x}, {y}), confidence={conf:.2f}.")
            click_at(x, y)
            return True
        time.sleep(1.0)
    print(f"[Reconnect] {label}: not found within {timeout:.0f}s.")
    return False


def rejoin_via_home(why="Reconnect isn't working"):
    """
    The way out when Reconnect doesn't work (the player's own recipe, 2026-10-08): Leave
    on the disconnect popup -> Roblox's Home -> Search -> type the game's name -> the first
    result -> Play -> wait for the lobby. True once the lobby is up, False if a step failed
    (the caller goes back to clicking Reconnect and tries this again later).
    """
    print(f"[Reconnect] {why} - leaving and rejoining through Roblox's home screen.")
    shot = capture_screen()
    if _find(shot, config.ROBLOX_LEAVE_BTN):
        if not _wait_click(config.ROBLOX_LEAVE_BTN, "roblox_leave", 5.0):
            return False
        time.sleep(5.0)
    if not _wait_click(config.ROBLOX_HOME_BTN, "roblox_home", 45.0):
        return False
    time.sleep(3.0)
    if not _wait_click(config.ROBLOX_SEARCH_BAR, "roblox_search", 30.0):
        return False
    time.sleep(1.0)
    input_controller.type_text(config.ROBLOX_GAME_SEARCH)
    time.sleep(0.5)
    input_controller.ensure_roblox_focus()
    import pydirectinput
    pydirectinput.press("enter")
    input_controller.mark_input()
    time.sleep(4.0)
    x, y = config.ROBLOX_SEARCH_RESULT_POS
    print(f"[Reconnect] Opening the first search result at ({x}, {y}).")
    click_at(x, y)
    time.sleep(3.0)
    if not _wait_click(config.ROBLOX_PLAY_BTN, "roblox_play", 30.0):
        return False

    print("[Reconnect] Joining the game - waiting for the lobby...")
    deadline = time.time() + config.ROBLOX_REJOIN_LOAD_TIMEOUT
    while time.time() < deadline and not config.STOP_REQUESTED:
        shot = capture_screen()
        if _at_lobby(shot):
            return True
        if _find(shot, config.RECONNECT_BTN):
            print("[Reconnect] Disconnected again while joining.")
            return False
        time.sleep(3.0)
    print(f"[Reconnect] The lobby didn't load within {config.ROBLOX_REJOIN_LOAD_TIMEOUT:.0f}s of pressing Play.")
    return False


def _press(key):
    import pydirectinput
    input_controller.ensure_roblox_focus()
    pydirectinput.press(key)
    input_controller.mark_input()


def _at_roblox_home(shot):
    return bool(_find(shot, config.ROBLOX_HOME_BTN) or _find(shot, config.ROBLOX_SEARCH_BAR))


def leave_and_rejoin():
    """
    A full reset (the Discord /reset command, and Never Stop's last resort): get out of the
    game server, then back in through Roblox's home screen.

      - Disconnect popup up: Leave.
      - In the game: Esc, L, Enter (Roblox's own "leave game" keys) - tried twice.
      - Already on Roblox's home screen: nothing to leave.

    Then rejoin_via_home(). Returns (ok, message).
    """
    if not input_controller.roblox_is_running():
        return False, "Roblox isn't running."
    shot = capture_screen()
    popup = bool(_find(shot, config.ROBLOX_LEAVE_BTN) or _find(shot, config.RECONNECT_BTN))
    if popup:
        print("[Reset] The disconnect popup is up - leaving through it.")
        # rejoin_via_home() clicks Leave itself - but only if it's recognised. The popup can
        # be up with just Reconnect matching; give Leave a few seconds, then fall back to the
        # in-game keys (Esc closes the popup, then Esc-L-Enter leaves).
        deadline = time.time() + 8.0
        while time.time() < deadline and not _find(capture_screen(), config.ROBLOX_LEAVE_BTN):
            time.sleep(1.0)
        if not _find(capture_screen(), config.ROBLOX_LEAVE_BTN):
            print("[Reset] The popup's Leave button isn't recognised - using the leave keys instead.")
            _press("esc")
            time.sleep(1.0)
            popup = False
    if not popup and not _at_roblox_home(capture_screen()):
        for attempt in (1, 2):
            if config.STOP_REQUESTED:
                return False, "Stopped."
            print(f"[Reset] Leaving the game: Esc, L, Enter (attempt {attempt}/2).")
            _press("esc")
            time.sleep(1.2)
            _press("l")
            time.sleep(1.2)
            _press("enter")
            deadline = time.time() + 25.0
            while time.time() < deadline and not config.STOP_REQUESTED:
                time.sleep(2.0)
                if _at_roblox_home(capture_screen()):
                    break
            if _at_roblox_home(capture_screen()):
                break
        else:
            if config.STOP_REQUESTED:
                return False, "Stopped."
            path = health.save_debug_screenshot("reset_could_not_leave")
            return False, f"Couldn't leave the game with Esc, L, Enter (screen saved: {path})."
    if config.STOP_REQUESTED:
        return False, "Stopped."
    if rejoin_via_home(why="Reset"):
        return True, "Left and rejoined - back at the lobby."
    path = health.save_debug_screenshot("reset_rejoin_failed")
    return False, f"Left the game, but rejoining didn't finish (screen saved: {path})."


def full_reset(focus_and_pin):
    """
    The whole reset as one sequence, shared by Discord's /reset and Never Stop's last
    resort: focus/place Roblox, leave and rejoin, place it again (Roblox can come back at
    another size after its home screen). focus_and_pin is the window's own (it docks
    Roblox). Returns (ok, message).
    """
    if not focus_and_pin():
        return False, "Couldn't find/focus the Roblox window - is Roblox running?"
    ok, msg = leave_and_rejoin()
    if ok:
        focus_and_pin()
    return ok, msg


def handle_disconnect_if_present(screenshot):
    """
    Checks whether the disconnect ("Reconnect"/"Cancel") popup is on screen. If it is,
    clicks Reconnect every RECONNECT_POLL_INTERVAL seconds until the lobby (Play button)
    is visible again, or the user cancels.
    Returns True if a disconnect was detected and resolved (caller should redo lobby
    navigation - reconnecting always lands back at the lobby), False if no disconnect
    popup was present at all.
    """
    match = find_template(screenshot, config.RECONNECT_BTN, config.MATCH_THRESHOLD, debug_label="reconnect_btn")
    if not match:
        return False

    print("[Reconnect] Disconnect popup detected! Attempting to reconnect...")
    started_at = time.time()
    last_update_sent = started_at
    notify.send(
        "The Reconnect popup is up - Roblox lost the connection to the server. "
        "Clicking Reconnect until the lobby comes back.",
        category="problems", title="Disconnected", good=False,
        image_bytes=health.jpg_bytes(screenshot),
    )

    reconnect_clicks = 0
    rejoin_attempts = 0
    while not config.STOP_REQUESTED:
        current_shot = capture_screen()

        # Either lobby marker counts as "we're back". Play is what Story/Raids/
        # Challenges navigate from and Items is what Portals uses; both are on the
        # lobby, so waiting only on Play made this needlessly fragile for the one
        # mode that never looks at it.
        if (find_template(current_shot, config.PLAY_BTN, config.MATCH_THRESHOLD)
                or find_template(current_shot, config.ITEMS_BTN, config.MATCH_THRESHOLD)):
            stats.SESSION.disconnect()
            down_for = stats.format_duration(time.time() - started_at)
            print(f"[Reconnect] Reconnected! Back at the lobby after {down_for}.")
            notify.send(f"Back at the lobby after **{down_for}** offline. Resuming navigation.",
                        category="problems", title="Reconnected", good=True)
            return True

        # Clicking Reconnect forever is right for a long outage, but only while there
        # is still a client to click. If Roblox itself died, nothing here can recover
        # it and this would otherwise be an infinite loop against a closed window.
        if not input_controller.roblox_is_running():
            print("[Reconnect] The Roblox window disappeared while reconnecting - "
                  "the client is gone, not just disconnected. Giving up.")
            # No notify.send here: config.STUCK_DETECTED below is picked up by
            # gui.BotGUI._notify_run_ended() once the run finishes unwinding, which
            # already attaches the session's stats - a second, earlier message here
            # would just be a less complete duplicate of that one.
            config.STOP_REQUESTED = True
            config.STUCK_DETECTED = "ROBLOX CLOSED"
            return False

        reconnect_match = find_template(current_shot, config.RECONNECT_BTN, config.MATCH_THRESHOLD, debug_label="reconnect_btn")
        if reconnect_match and reconnect_clicks >= config.RECONNECT_TRIES_BEFORE_REJOIN:
            # Reconnect alone can loop forever (live 2026-10-08: over 2 hours). Leave and
            # come back in through Roblox's home screen instead; if that fails too, a few
            # more Reconnect clicks, then the rejoin again.
            rejoin_attempts += 1
            notify.send(f"Reconnect didn't work after {reconnect_clicks} tries - leaving and rejoining the game "
                        f"through Roblox's home screen (attempt {rejoin_attempts}).",
                        category="problems", title="Rejoining the game", good=None,
                        image_bytes=health.jpg_bytes(current_shot))
            if rejoin_via_home():
                stats.SESSION.disconnect()
                down_for = stats.format_duration(time.time() - started_at)
                print(f"[Reconnect] Rejoined! Back at the lobby after {down_for}.")
                notify.send(f"Rejoined through Roblox's home screen - back at the lobby after **{down_for}**. "
                            f"Resuming.", category="problems", title="Reconnected", good=True)
                return True
            path = health.save_debug_screenshot(f"rejoin_failed_{rejoin_attempts}")
            print(f"[Reconnect] Rejoining didn't work (screen saved: {path}) - back to Reconnect for now.")
            reconnect_clicks = 0
            continue
        if reconnect_match:
            x, y, _ = reconnect_match
            reconnect_clicks += 1
            print(f"[Reconnect] Clicking Reconnect at ({x}, {y}) (try {reconnect_clicks}).")
            click_at(x, y, clicks=2)
        elif reconnect_clicks and not _at_lobby(current_shot) and (
                _find(current_shot, config.ROBLOX_HOME_BTN) or _find(current_shot, config.ROBLOX_SEARCH_BAR)):
            # Thrown out to Roblox's home screen with no popup at all: rejoin from here.
            if rejoin_via_home():
                stats.SESSION.disconnect()
                print("[Reconnect] Rejoined from Roblox's home screen.")
                return True
        else:
            print("[Reconnect] Popup not visible - waiting for the lobby to finish loading...")

        # A long outage gets a periodic "still down" ping instead of just the first
        # one - the whole point being that a silent 45-minute gap here should NOT
        # look identical to the bot having crashed with nothing left to say.
        if time.time() - last_update_sent >= _STILL_TRYING_AFTER:
            last_update_sent = time.time()
            down_for = stats.format_duration(time.time() - started_at)
            notify.send(f"Still trying to reconnect - down for **{down_for}** so far.",
                        category="problems", title="Still disconnected", good=False,
                        image_bytes=health.jpg_bytes(current_shot))

        time.sleep(config.RECONNECT_POLL_INTERVAL)

    return False
