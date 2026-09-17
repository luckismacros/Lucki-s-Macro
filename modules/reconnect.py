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
        if reconnect_match:
            x, y, _ = reconnect_match
            print(f"[Reconnect] Clicking Reconnect at ({x}, {y}).")
            click_at(x, y, clicks=2)
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
