# modules/polling.py
"""
The one poll loop every wait in this bot shares.

Before this existed, the same six lines - capture, match, dismiss prompts, check
for a disconnect, sleep - were written out five separate times across
stage_player.py, gamemode_select.py and portal_select.py. That duplication is the
reason safety checks were missing rather than an oversight: adding one meant
pasting it into five loops and keeping them in sync forever, so none ever got
added. With the shape extracted, a check added here is a check every wait gets.

What poll_until() does on every tick, in order:
  1. honours config.STOP_REQUESTED
  2. captures one screenshot and tests every target against it
  3. runs the caller's custom_check, if the caller passed one
  4. runs the caller's on_tick (the fishing keep-alive is the only user)
  5. dismisses a "Click anywhere to continue" prompt
  6. reopens the match-end popup if it was closed before being read ("Game Results")
  7. resolves a disconnect, if one is up
  8. verifies Roblox still exists at all
  9. sends an anti-idle click if nothing has touched the game in a long time
  10. gives up if this wait has run impossibly long

Steps 8-10 are the ones no loop had before.
"""
import time
from collections import namedtuple

import config
from vision import capture_screen, find_template
from input_controller import click_at
from modules.prompts import dismiss_click_anywhere_if_present, handle_game_results_if_present
from modules.reconnect import handle_disconnect_if_present
from modules import health

# One thing a poll is watching for. `result` is what poll_until returns when this
# target matches, which is what lets a single loop watch for Victory/Defeat/reward
# at once and report which arrived.
Target = namedtuple("Target", ["template", "result", "click", "clicks", "debug_label"])


def target(template, result=True, click=False, clicks=1, debug_label=None):
    """Convenience constructor so callers don't have to pass every field."""
    return Target(template, result, click, clicks, debug_label)


# --- Clicking a button that may still be moving ---------------------------------------
# Popups slide/pop into place. A template can match a button perfectly (1.00) while it
# is still travelling, and a click sent to that position lands where the button WAS -
# confirmed live 2026-09-13: Expeditions' second Continue button was found at 1.00 in
# the same second the first one was clicked, and the click landed under the settled
# button. So a found button is re-checked until two looks agree before it is clicked.
SETTLE_STEP_SECONDS = 0.15
SETTLE_MAX_SECONDS = 2.0
SETTLE_TOLERANCE_PX = 2

# After a click, how long the button gets to disappear before it counts as missed.
CLICK_GONE_WAIT_SECONDS = 1.5


def settle_match(template, match, label, threshold=None):
    """
    Re-finds `template` until its position holds still, and returns that latest
    (x, y, confidence). None if the button disappeared while waiting (nothing to click).
    Still moving after SETTLE_MAX_SECONDS -> returns the latest position anyway.
    """
    threshold = config.MATCH_THRESHOLD if threshold is None else threshold
    previous = match
    misses = 0
    deadline = time.time() + SETTLE_MAX_SECONDS
    while time.time() < deadline and not config.STOP_REQUESTED:
        time.sleep(SETTLE_STEP_SECONDS)
        current = find_template(capture_screen(), template, threshold)
        if current is None:
            misses += 1
            if misses >= 3:
                return None
            continue
        if (abs(current[0] - previous[0]) <= SETTLE_TOLERANCE_PX
                and abs(current[1] - previous[1]) <= SETTLE_TOLERANCE_PX):
            if (current[0], current[1]) != (match[0], match[1]):
                print(f"[{label}] Button was still moving: found at ({match[0]}, {match[1]}), "
                      f"settled at ({current[0]}, {current[1]}).")
            return current
        previous = current
    return previous


def click_until_gone(template, match, label, clicks=1, attempts=3, point=None, success_template=None):
    """
    Clicks a found button once it has settled, then makes sure the click took: the
    button has to disappear (or `success_template` has to appear - for a button that
    opens something on top of itself). Not taken -> re-find and click again, up to
    `attempts` times.

    point: click here instead of on the button (e.g. "click anywhere" screens), while
           still using the button's disappearance as the proof the click worked.

    True when the click took (or the button was already gone), False if it was still
    there after every attempt or the run was stopped.
    """
    for attempt in range(1, attempts + 1):
        if config.STOP_REQUESTED:
            return False
        settled = settle_match(template, match, label)
        if settled is None:
            return True
        x, y, confidence = settled
        cx, cy = point if point else (x, y)
        suffix = f" (attempt {attempt}/{attempts})" if attempt > 1 else ""
        print(f"[{label}] Clicking at ({cx}, {cy}), confidence={confidence:.2f}{suffix}.")
        click_at(cx, cy, clicks=clicks)

        deadline = time.time() + CLICK_GONE_WAIT_SECONDS
        while time.time() < deadline:
            time.sleep(0.25)
            shot = capture_screen()
            if success_template and find_template(shot, success_template, config.MATCH_THRESHOLD):
                return True
            still = find_template(shot, template, config.MATCH_THRESHOLD)
            if still is None:
                return True
            match = still
        print(f"[{label}] Still on screen after the click - it didn't register.")
    print(f"[{label}] Still on screen after {attempts} clicks - moving on.")
    return False


def _halt(reason_phase):
    """
    Stops the run from inside a poll.

    Sets STOP_REQUESTED rather than inventing a new sentinel that all 19 call sites
    in gui.py would have to learn: every loop already breaks out on a falsy result,
    so returning False here unwinds them correctly with no changes. STUCK_DETECTED
    is what lets the GUI report this as a fault instead of a normal user stop.
    """
    config.STOP_REQUESTED = True
    config.STUCK_DETECTED = reason_phase
    return False


def poll_until(targets, interval, label, timeout=None, stuck_timeout=None,
               on_tick=None, anti_idle=True, check_alive=True, custom_check=None,
               game_results=True, lobby_grace=None):
    """
    Polls until one of `targets` matches, and returns that target's `result`.

    targets      list of Target (see target()); tested in order, first match wins
    interval     seconds between ticks
    label        short name for logs and debug screenshot filenames
    timeout      soft limit: returns "TIMEOUT" when exceeded. For waits where not
                 finding the thing is an expected outcome the caller handles
                 (a challenge on cooldown, a portal out of hearts).
    stuck_timeout hard limit: something is wrong. Saves a screenshot, stops the run.
                 Pass None when `timeout` already bounds this wait.
    on_tick      called once per tick after the target checks (fishing keep-alive)
    anti_idle    send an idle-avoidance click when nothing has touched the game
    check_alive  stop if the Roblox window disappears
    game_results whether to look for and click the "Game Results" recovery button
                 (see modules.prompts.handle_game_results_if_present). Defaults to
                 on, but MUST be off for any wait where a screen with real content
                 the bot still needs to act on can render on top of (and partially
                 obscure) that button - Portals' reward-pick cards are exactly this:
                 confirmed live, the button was still matching underneath the cards,
                 getting clicked mid-pick, and corrupting the reward flow. See
                 modules.stage_player.wait_for_match_result()'s allow_game_results
                 for where this is actually threaded through.
    custom_check called with the tick's screenshot, right after the target list is
                 checked and found nothing; returning anything other than None ends
                 the poll with that as the result, same as a matched target - but
                 without clicking anything. For a condition that isn't "template
                 appeared", like inferring a state from something ELSE'S absence
                 (see stage_player.wait_for_start_game()), which a Target can't
                 express since it only ever fires on presence.

    Returns the matching target's result, or:
      False         - cancelled by the user, or the run was halted (stuck/no client)
      "RECONNECTED" - a disconnect was detected and resolved; the caller must redo
                      lobby navigation, since reconnecting always lands at the lobby
      "TIMEOUT"     - `timeout` elapsed with no match
    """
    stuck = health.StuckTimer(stuck_timeout, label)
    elapsed = 0.0
    # lobby_grace (seconds, None = off): for waits that happen INSIDE a stage/match.
    # If the lobby stays on screen that long, the player was sent back there without a
    # disconnect popup - a server restart for a patch does exactly that (live,
    # 2026-09-14: a Portals run sat on "Waiting for the match to end" in the lobby). It
    # is reported as "RECONNECTED", which every caller already handles by redoing the
    # navigation from the lobby and carrying on. The grace keeps a brief glimpse of the
    # lobby during a normal teleport from counting.
    lobby_seen_at = None

    while not config.STOP_REQUESTED:
        screenshot = capture_screen()

        for t in targets:
            match = find_template(screenshot, t.template, config.MATCH_THRESHOLD,
                                  debug_label=t.debug_label)
            if match:
                if t.click:
                    # Clicked only once it has stopped moving - see settle_match().
                    settled = settle_match(t.template, match, label)
                    if settled is None:
                        continue  # vanished while settling; keep polling
                    x, y, confidence = settled
                    print(f"[{label}] Found at ({x}, {y}), confidence={confidence:.2f} - clicking.")
                    click_at(x, y, clicks=t.clicks)
                return t.result

        if custom_check is not None:
            custom_result = custom_check(screenshot)
            if custom_result is not None:
                return custom_result

        # Before on_tick: the fishing keep-alive would otherwise keep clicking the lobby.
        if lobby_grace is not None:
            from modules.lobby import at_lobby  # lazy: lobby imports prompts, which polling also uses
            if at_lobby(screenshot):
                now = time.time()
                if lobby_seen_at is None:
                    lobby_seen_at = now
                    print(f"[{label}] The lobby is on screen - checking whether the game sent us back there...")
                elif now - lobby_seen_at >= lobby_grace:
                    print(f"[{label}] Still in the lobby after {lobby_grace:.0f}s - the game sent us back "
                          f"(server restart or kick). Going back to what we were doing.")
                    try:
                        from modules import notify, stats
                        stats.SESSION.disconnect()
                        notify.send("Sent back to the lobby without a disconnect popup (server restart or kick). "
                                    "Navigating back in and carrying on.", category="problems",
                                    title="Back in the lobby", good=False, image_bytes=health.jpg_bytes(screenshot))
                    except Exception as e:
                        print(f"[{label}] Couldn't send the lobby notification: {e}")
                    return "RECONNECTED"
            else:
                lobby_seen_at = None

        if on_tick is not None:
            on_tick()

        dismiss_click_anywhere_if_present(screenshot)
        if game_results:
            handle_game_results_if_present(screenshot)

        if handle_disconnect_if_present(screenshot):
            return "RECONNECTED"

        # Checked AFTER the disconnect handler: a disconnect popup is a live client
        # and must be handled as one, not mistaken for a dead game.
        if check_alive and not health.roblox_alive():
            print("=" * 70)
            print(f"[polling] The Roblox window is gone while waiting for '{label}'.")
            print("[polling] That isn't a disconnect - there's no Reconnect popup to click,")
            print("[polling] because the client itself is no longer running. Stopping.")
            print("=" * 70)
            return _halt("ROBLOX CLOSED")

        if anti_idle:
            health.anti_idle_tick()

        if stuck.expired():
            waiting_for = ", ".join(t.template for t in targets)
            stuck.report(screenshot, extra=f"It was waiting for: {waiting_for}")
            return _halt("STUCK")

        time.sleep(interval)
        elapsed += interval

        if timeout is not None and elapsed >= timeout:
            return "TIMEOUT"

    print(f"[{label}] Cancelled.")
    return False
