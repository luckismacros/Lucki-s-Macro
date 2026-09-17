# modules/stage_player.py
import json
import time
import pydirectinput
from input_controller import click_at, move_mouse_to, mark_input
from vision import capture_screen, find_template
from modules.reconnect import handle_disconnect_if_present
from modules.prompts import dismiss_click_anywhere_if_present, handle_game_results_if_present
from modules.fishing import FishingTicker
from modules.autoclicker import AutoClickerTicker
from modules.polling import poll_until, target
from modules import health
import config

def play_preset(location_key, variant_key, preset_name, skip_movement=False):
    """
    Plays back a recording: number-key presses, clicks, the mouse path between them,
    and - if the player walked (held W/A/S/D/Space) before placing anything - that
    walk too. It's all one timeline, because that's how it was recorded: press F8,
    walk to your spot if you need to, place your units, press F8 again.

    skip_movement=True replays ONLY the placement (keys/clicks/mouse path), leaving
    out the walk at the start entirely - not just the key presses, but the time it
    took, so placement starts immediately instead of waiting through a walk that
    never happens. For when the character is already known to be at the recorded
    spot: still standing there from the match this exact stage/portal was last
    entered on, so walking again would just walk PAST it. Callers decide when that's
    true (engine.BotEngine tracks it per stage; Portals' own is_repeat_match already
    tracked the equivalent thing for its old walk-to-fish system) - this function
    only carries out the choice.

    Returns True on a completed timeline, False if cancelled/missing/empty, or
    "RECONNECTED" if a disconnect was detected and resolved mid-playback - caller
    must redo lobby navigation in that case, same as wait_for_start_game().
    """
    filename = f"presets/{location_key}_{variant_key}_{preset_name}.json"
    try:
        with open(filename, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"[Player] No preset found at {filename}. Please record one first.")
        return False

    actions = data.get("actions", [])
    if not actions:
        print("[Player] Preset is empty.")
        return False

    # Skipping the walk means skipping the TIME it took too: the first key/click
    # after it was recorded at (say) t=8.4s because 8.4s of walking came first, and
    # without this every skipped playback would still sit idle for 8.4s before
    # placing a single unit. Backdating start_time by that much makes "elapsed"
    # already past every walk-only timestamp the moment playback begins - each of
    # those actions is skipped outright below, so nothing is actually waited on, this
    # only has to fast-forward the CLOCK past them for whatever comes next.
    time_shift = 0.0
    if skip_movement:
        placement_times = [a["time"] for a in actions if a["type"] in ("key", "click")]
        if placement_times:
            time_shift = min(placement_times)
        print(f"[Player] Already at the recorded spot - skipping the walk ({time_shift:.1f}s of it).")

    print(f"[Player] Loaded {len(actions)} actions. Starting playback...")
    start_time = time.time() - time_shift
    last_disconnect_check = 0.0
    lobby_ticks = 0  # consecutive once-a-second checks that found the lobby (see poll_until's lobby_grace)
    held_movement = set()

    try:
        for action in actions:
            if skip_movement and action["type"] in ("keydown", "keyup"):
                continue

            target_time = action["time"]

            while (time.time() - start_time) < target_time:
                if config.STOP_REQUESTED:
                    print("[Player] Playback canceled by user.")
                    return False

                # Throttled to once/sec - a screenshot+match every 10ms would be far too costly.
                # Skipped entirely while a movement key is actually held: dismiss_click_anywhere_if_present
                # and handle_game_results_if_present both CLICK when they match, and a click here is an
                # absolute mouse teleport (see input_controller.click_at) - mid-walk, that spins the camera
                # (WASD is camera-relative) and sends the character off in the wrong direction for the rest
                # of the walk. That's what made a recorded walk come out "short, delayed... stopped before
                # reaching the point": this whole block is new since movement got embedded in the same
                # timeline, because only a walk holds a key long enough to ever hit this 1s throttle - a
                # click/key gap almost never did. The old, always-reliable modules.movement_recorder.play_movement()
                # never ran any of this during a walk either, only a non-clicking disconnect_check().
                now = time.time()
                if now - last_disconnect_check >= 1.0 and not held_movement:
                    last_disconnect_check = now
                    current_shot = capture_screen()
                    dismiss_click_anywhere_if_present(current_shot)
                    handle_game_results_if_present(current_shot)
                    if handle_disconnect_if_present(current_shot):
                        return "RECONNECTED"

                    # Sent back to the lobby mid-timeline with no popup (server restart):
                    # stop placing units into the lobby and let the caller navigate back.
                    from modules.lobby import at_lobby
                    if at_lobby(current_shot):
                        lobby_ticks += 1
                        if lobby_ticks >= 6:
                            print("[Player] The lobby has been on screen for 6s - the game sent us back. "
                                  "Stopping playback so the run can navigate back in.")
                            return "RECONNECTED"
                    else:
                        lobby_ticks = 0

                    # This is the one loop that isn't poll_until (it's a timeline being
                    # replayed on a schedule, not a wait for something to appear), so the
                    # health check it would otherwise inherit has to be made explicitly.
                    # Without it, a client that dies mid-preset gets clicked at for the
                    # rest of the timeline before anything notices.
                    if not health.roblox_alive():
                        print("[Player] The Roblox window is gone - stopping playback.")
                        config.STOP_REQUESTED = True
                        config.STUCK_DETECTED = "ROBLOX CLOSED"
                        return False

                time.sleep(0.01)

            if action["type"] == "move":
                # The recorded mouse path between clicks: replayed so the cursor travels the
                # way the player's did, instead of jumping from one placement to the next.
                mx, my = action["pos"]
                move_mouse_to(*config.to_screen(mx, my))
                continue

            if action["type"] == "key":
                print(f"[Player] Pressing '{action['value']}' at {target_time}s")
                pydirectinput.press(action["value"])

            elif action["type"] == "click":
                x, y = action["pos"]
                print(f"[Player] Placing unit at ({x}, {y}) at {target_time}s")

                # Reduce to a fast double-click to prevent thread blocking
                for _ in range(2):
                    if config.STOP_REQUESTED:
                        return False
                    click_at(x, y, delay_before=0.01, delay_after=0.01)

            elif action["type"] in ("keydown", "keyup"):
                # Only reached when actually walking (skip_movement filtered these out
                # above otherwise) - held the same way modules.movement_recorder's own
                # play_movement() holds a standalone walk.
                walk_key = action["key"]
                if action["type"] == "keydown":
                    pydirectinput.keyDown(walk_key)
                    held_movement.add(walk_key)
                    print(f"[Player] Walking: '{walk_key}' down at {target_time}s")
                else:
                    pydirectinput.keyUp(walk_key)
                    held_movement.discard(walk_key)
                mark_input()

        print("[Player] Timeline complete.")
        return True
    finally:
        # A cancelled or interrupted walk must never leave a movement key stuck down -
        # that would walk the character away indefinitely after playback ends.
        for walk_key in held_movement:
            pydirectinput.keyUp(walk_key)

def wait_for_start_game():
    """
    Polls until the green Start Game button appears (map load) - or until it becomes
    clear the match already started without it, because the account has the game's
    "Auto Start" setting on. That setting skips the button entirely, so waiting for
    it here would poll for the full STUCK_TIMEOUT_MENU and wrongly declare the run
    stuck even though the match is playing out fine. Does NOT click Start Game
    itself either way - normal preset play doesn't need it, the match starts on its
    own once loaded.

    There's no button to detect for the Auto Start case - only its absence - so it's
    inferred instead from the in-game Auto Play toggle: that button lives in the same
    stage-only side panel and, on an Auto Start run, is the first thing that proves
    we're actually on the stage rather than still loading. It's required to hold for
    3 consecutive ticks before being trusted, because Auto Play and Start Game
    normally render in the very same instant a stage finishes loading - a single
    frame where Start Game merely hasn't drawn *yet* must not be mistaken for Auto
    Start, or a normal run would skip a Start Game click it still needed to make.

    Returns:
      True              - Start Game button detected; caller clicks it as normal.
      "ALREADY_STARTED" - Auto Start skipped the button; the match is already
                           running. Every caller only ever checks this for
                           truthiness, so it's handled identically to True - the
                           only difference is what gets logged.
      False             - cancelled by the user, or the run was halted (stuck / no client).
      "RECONNECTED"     - was disconnected and got back in; caller must redo lobby
                           navigation, since reconnecting always drops you back at
                           the lobby, not the match.
    """
    print("[Player] Waiting for Start Game button (map load)...")
    already_started_streak = 0
    seen_without_panel = False
    started_waiting = time.time()

    def _check_already_started(screenshot):
        nonlocal already_started_streak, seen_without_panel
        on_stage = (
            find_template(screenshot, config.AUTOPLAY_ON_BTN, config.MATCH_THRESHOLD, debug_label="autoplay_on") or
            find_template(screenshot, config.AUTOPLAY_OFF_BTN, config.MATCH_THRESHOLD, debug_label="autoplay_off")
        )

        if not on_stage:
            # The panel being gone is the map actually loading. From here on, seeing it
            # again means a NEW stage came up, which is the thing worth reacting to.
            seen_without_panel = True
            already_started_streak = 0
            return None

        # The panel is up but was never seen to go away, so this is very likely the
        # PREVIOUS match's stage UI, which survives on screen for a few seconds after a
        # portal re-select or a stage repeat. Counting it concluded "Auto Start is on"
        # ~3s after the confirm click, skipped the wait for the real Start Game button,
        # and left the match sitting unstarted - the caller's click_start_game() then
        # found nothing to click, warned, and went off to wait for a match that was
        # never running. Cold entries from the lobby are unaffected either way: there
        # is no panel there, so the first tick sets the flag immediately.
        if not seen_without_panel:
            # Not free forever, though - resuming into a stage that is genuinely
            # already up never shows a panel-less frame at all. Past a grace period
            # long enough for any real map load to have surfaced Start Game first,
            # believe it.
            if time.time() - started_waiting >= config.START_GAME_LINGER_GRACE:
                print(f"[Player] Stage panel has been up for "
                      f"{config.START_GAME_LINGER_GRACE:.0f}s without Start Game ever appearing "
                      f"- treating the match as already running.")
                return "ALREADY_STARTED"
            return None

        already_started_streak += 1
        if already_started_streak >= 3:
            print("[Player] Start Game never appeared but the stage is clearly up "
                  "(Auto Start is on) - treating the match as already started.")
            return "ALREADY_STARTED"
        return None

    return poll_until(
        [target(config.START_GAME_BTN, True, debug_label="start_game_btn")],
        interval=1.0,
        label="start_game",
        # A map load is a menu-speed wait, not a match-length one: if Start Game hasn't
        # appeared in minutes, the load didn't just take a while, something is wrong.
        stuck_timeout=config.STUCK_TIMEOUT_MENU,
        custom_check=_check_already_started,
        # Long grace: right after Start is pressed the lobby can still be showing while
        # the teleport into the stage happens.
        lobby_grace=25.0,
    )

def click_start_game():
    """
    Finds and clicks the Start Game button. Normal preset play never needs this - the
    match starts on its own once loaded - but Auto Play mode does nothing else to signal
    "ready", so it needs an explicit click here to actually begin the match.
    """
    screenshot = capture_screen()
    match = find_template(screenshot, config.START_GAME_BTN, config.MATCH_THRESHOLD, debug_label="start_game_btn")
    if match:
        x, y, _ = match
        print(f"[Player] Clicking Start Game at ({x}, {y}).")
        click_at(x, y, clicks=2)
        return True
    print("[Player] Could not find Start Game button to click.")
    return False

def match_already_started(shot=None):
    """
    True if a match is already under way - the transient "Game Started!" banner
    (config.GAME_STARTED_TEXT) is still on screen, or the wave counter has moved off
    zero (config.WAVE_ZERO_TEXT is captured AT zero, so it stops matching once a wave
    is actually under way - see its own comment in config.py).

    Independent of the game's own Auto Start/Auto Retry setting on purpose - this is
    the safety net for a player who forgot to turn that on: whatever pressed Start
    (or didn't need to, because the match already began on its own) leaves one of
    these two signals true, so callers don't need to trust that toggle at all.

    shot lets a caller reuse a screenshot it already has; otherwise one is captured.
    """
    shot = capture_screen() if shot is None else shot
    if find_template(shot, config.GAME_STARTED_TEXT, config.MATCH_THRESHOLD, debug_label="game_started"):
        return True
    return find_template(shot, config.WAVE_ZERO_TEXT, config.MATCH_THRESHOLD, debug_label="wave_zero") is None

def press_start_after_macro(settle_seconds=1.0):
    """
    Presses Start Game right after a recorded macro finishes placing units - unless
    the match is already running.

    A recorded macro only ever places units (number keys + clicks); it never presses
    Start Game itself unless the player's OWN recording happened to click it as one of
    its steps. Two things can make that press unnecessary by the time the macro ends:
      - the player's recording already clicked Start Game itself, or
      - the account has the game's own Auto Start setting on, and the match began on
        its own partway through the recording.
    Both are recognised the same safe way: click_start_game() only ever clicks when it
    actually finds the button on screen, so calling it after either case is a harmless
    no-op - there's nothing left to click.

    The extra check here is only to log the right thing and skip a moment of settling
    time when it's clearly not needed: the transient "Game Started!" banner
    (config.GAME_STARTED_TEXT) still on screen, or the wave counter having moved off
    zero (config.WAVE_ZERO_TEXT is captured AT zero, so it stops matching once a wave
    is actually under way - see its own comment in config.py).
    """
    def game_started(shot):
        return find_template(shot, config.GAME_STARTED_TEXT, config.MATCH_THRESHOLD, debug_label="game_started")

    shot = capture_screen()
    if game_started(shot):
        print("[Player] 'Game Started!' is still showing - the match already began. Not clicking Start Game.")
        return

    # The wave counter not reading "0" is a real signal, but on its own it's also what
    # a HUD that simply hasn't finished drawing yet looks like - not proof either way.
    # Rather than guess, give it a moment and check the one UNAMBIGUOUS signal
    # (the banner) again before deciding. Whenever it's still unclear after that,
    # press Start Game anyway: click_start_game() only ever clicks when it actually
    # finds the button, so pressing it on a match that's already running is a
    # harmless no-op - the wrong direction to guess in is skipping a press the match
    # genuinely still needed, which is the exact bug this function exists to fix.
    if find_template(shot, config.WAVE_ZERO_TEXT, config.MATCH_THRESHOLD, debug_label="wave_zero") is None:
        time.sleep(1.0)
        if game_started(capture_screen()):
            print("[Player] 'Game Started!' appeared - the match already began. Not clicking Start Game.")
            return

    time.sleep(settle_seconds)  # the last recorded click needs a moment to register before this one
    print("[Player] Pressing Start Game after the macro...")
    if not click_start_game():
        print("[Player] Start Game button wasn't there - the match must already be running.")


def wait_for_repeat_stage(click=True, timeout=None):
    """
    Polls until the Repeat Stage button appears, then (by default) clicks it to replay
    the stage. Callers reach this AFTER wait_for_match_result() has already told them
    how the match went - Victory and Defeat both repeat, so this doesn't care which.
    Challenges mode passes click=False - it only needs to know the match ended, since
    it advances to a *different* challenge afterward rather than repeating this one.

    timeout, if given, caps the wait and returns "TIMEOUT" rather than treating the
    absence as a fault. The Portals loop passes one because a missing Repeat Stage is
    an expected outcome there: a portal has 3 hearts and the third loss destroys it,
    so the button legitimately stops appearing and that's how the loop learns to enter
    a fresh portal.

    Returns True/False/"RECONNECTED"/"TIMEOUT" - note "TIMEOUT" is truthy, so callers
    that pass a timeout must check for it explicitly.
    """
    print("[Player] Waiting for Repeat Stage button...")
    return poll_until(
        [target(config.REPEAT_STAGE_BTN, True, click=click, clicks=2,
                debug_label="repeat_stage_btn")],
        interval=2.0,
        label="repeat_stage",
        timeout=timeout,
        # Only guarded as "stuck" when the caller hasn't bounded it itself - otherwise
        # the two limits would race and the shorter one would always win anyway.
        stuck_timeout=None if timeout else config.STUCK_TIMEOUT_MENU,
        lobby_grace=8.0,
    )

def wait_for_next_stage(timeout=8.0):
    """
    Polls for the Next Stage button and clicks it - Story's Auto Next mode uses this
    on a Victory to advance to the next act instead of repeating this one. Only ever
    offered on Victory, never on Defeat.

    Bounded by a short timeout rather than the long stuck limit: not finding it here
    is an ordinary, expected outcome - the auto-advance chain has run out of stages,
    or this particular stage simply doesn't offer a next one - not a sign anything
    is stuck.

    Returns True/False/"RECONNECTED"/"TIMEOUT" - note "TIMEOUT" is truthy, so callers
    must check for it explicitly (see wait_for_repeat_stage()).
    """
    print("[Player] Waiting for Next Stage button...")
    return poll_until(
        [target(config.NEXT_STAGE_BTN, True, click=True, clicks=2, debug_label="next_stage_btn")],
        interval=1.0,
        label="next_stage",
        timeout=timeout,
        stuck_timeout=None,  # already bounded by `timeout`
        lobby_grace=6.0,
    )

def wait_for_match_result(fishing=False, cast_pos=None, act_key=None, allow_game_results=True):
    """
    Polls until Victory, Defeat, or (Portals only) the post-match 3-portal
    reward-select screen appears - whichever shows up first is the match-end signal.
    Does not click anything itself besides the optional fishing keep-alive. The
    end-of-match banner is the same in every gamemode, which is what lets all four
    loops share this.

    fishing=True re-clicks every few seconds as a keep-alive cast for the fishing
    portals (see modules/fishing.py) - wherever the cursor already is, unless cast_pos
    (x, y) is given, in which case it re-clicks there instead.
    act_key selects the stuck limit: Infinite acts run for hours by design and get a
    correspondingly longer bound (see config.match_stuck_timeout).

    allow_game_results controls whether this wait may click the "Game Results"
    recovery button if it turns up (see modules.prompts.handle_game_results_if_present
    and modules.polling.poll_until's game_results param). Story/Raids/Challenges keep
    it on: their match-end popup has nothing rendering on top of it, so recovering
    from an early closed popup is pure upside. Portals passes False here - confirmed
    live, that same button sits directly under the reward-pick cards and still
    registers a match through them, so a stray click here doesn't recover anything,
    it just fires a real click into whatever's on top of it while the loop is trying
    to read/pick a reward. For Portals, recovering that popup is click_select_portal's
    job instead (see modules.portal_select.click_select_portal), once picking is done
    and there is no longer a card screen left to sit underneath the button.

    Returns:
      "VICTORY"       - won (no reward screen seen yet).
      "DEFEAT"        - lost. Story/Raids and Portals repeat the stage from here just
                        like a win; Challenges moves on to the next slot (a lost
                        challenge isn't completed, so it stays off cooldown and comes
                        back around on the next pass anyway).
      "REWARD_SELECT" - the 3-portal reward-choice screen is up (Portals).
      False           - cancelled by the user, or the run was halted (stuck / no client).
      "RECONNECTED"   - was disconnected and got back in; caller must redo lobby navigation.
    """
    print("[Player] Waiting for Victory/Defeat/Reward-Select (match end)...")
    fish_ticker = FishingTicker(cast_pos=cast_pos) if fishing else None
    autoclick_ticker = AutoClickerTicker()

    def on_tick():
        if fish_ticker:
            fish_ticker.tick()
        autoclick_ticker.tick()

    return poll_until(
        [
            target(config.REWARD_SELECT_TEXT, "REWARD_SELECT", debug_label="reward_select"),
            target(config.VICTORY_TEXT, "VICTORY", debug_label="victory"),
            target(config.DEFEAT_TEXT, "DEFEAT", debug_label="defeat"),
        ],
        interval=2.0,
        label="match_result",
        on_tick=on_tick,
        stuck_timeout=config.match_stuck_timeout(act_key),
        game_results=allow_game_results,
        lobby_grace=8.0,
    )

def wait_for_reward_or_select(timeout=15.0):
    """
    After a Victory banner, Portals does one of two things, confirmed by finally
    getting a real capture of the screen this was dying on:

    - Rarely, a genuine reward CHOICE - config.REWARD_SELECT_TEXT's own reference
      art reads "Auto-selecting in", i.e. a countdown over a pick-one-of-several
      screen, not "a reward exists" in general.
    - Normally, no choice at all: the plain post-match summary (stats, auto-granted
      loot, a "Select Portal" button) appears directly, with nothing to pick.

    The previous version only ever watched for the first case, so an ordinary win
    - the common case - waited the full timeout for a countdown that was never
    coming, then gave up on the whole run WITHOUT EVER TRYING Select Portal, even
    though it was sitting right there in the frame the entire time (confirmed via
    the debug screenshot that dead end saved). Watching for both at once fixes both
    directions: whichever the game actually shows wins immediately, and the "no
    choice offered" case no longer needs a reward pick before it can proceed.

    Always with the Game Results click disabled - see wait_for_match_result's
    allow_game_results for why: this is exactly the window where reward cards may
    already be up (just not yet confirmed), and the button sits obscured underneath
    them.

    Returns:
      "REWARD_SELECT"       - the countdown/choice screen is up; go pick one.
      "SELECT_PORTAL_READY" - no choice was offered; Select Portal is already there.
      "RECONNECTED"         - disconnected and got back in.
      False                 - cancelled by the user, or the run was halted.
      "TIMEOUT"             - neither ever appeared.
    """
    print(f"[Player] Waiting up to {int(timeout)}s for a reward choice or the Select Portal button...")
    return poll_until(
        [
            target(config.REWARD_SELECT_TEXT, "REWARD_SELECT", debug_label="reward_select"),
            target(config.PORTAL_SELECT_BTN, "SELECT_PORTAL_READY", debug_label="select_portal_ready"),
        ],
        interval=1.0,
        label="reward_or_select",
        timeout=timeout,
        stuck_timeout=None,  # already bounded by `timeout`
        lobby_grace=6.0,
        game_results=False,
    )
