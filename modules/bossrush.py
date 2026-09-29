# modules/bossrush.py
"""
Boss Rush: lobby navigation, then a fixed loop of 6 gates followed by a boss fight.

The flow, as described by the player (2026-09-26):

  Lobby:  Play -> Boss Rush card -> Select Stage -> Start -> Start Game -> lands on
          the hub map with 6 gates.

  Hub:    the hub camera/position resets to the same fixed spot every time it's
          returned to (confirmed live), so each gate is reached the same way
          regardless of clear order: play that gate's own walk-only preset
          (bossrush_walk_gate{N}), wait for the "Enter Gate" prompt, press E, wait
          for Start Game, place units (only if the hotbar still has them - the same
          check Expeditions uses, since units are only ever placed once per cycle)
          and clear the fight. Clearing a gate offers a card to pick (any of them -
          the middle one is clicked) and returns to the hub. A continue/boss choice
          can appear before the next gate (the player says this only ever happens
          after gate 2) - Continue is clicked whenever that choice shows, to keep
          working through the 6 rather than fighting the boss early.

  Boss:   once all 6 gates are cleared, a final Start Game offers the boss fight -
          its own separate macro (units are placed fresh here too). Beating it
          offers the ordinary, shared Repeat Stage button; clicking it and then
          Start Game again starts the 6 gates over from gate 1.

No autoplay at all in this mode - every fight is a recorded macro (see
config.BOSSRUSH_GATE_VARIANT / BOSSRUSH_BOSS_VARIANT). This module only owns
navigation and knowing when to play which macro; play_preset() (modules/stage_player)
does the actual clicking/placing/walking, exactly like every other mode.
"""
import time

import pydirectinput

import config
from vision import capture_screen, find_template, find_all_templates
from input_controller import click_at, anchor_camera, high_res_timer, mark_input, type_text
from modules.polling import poll_until, target, settle_match, click_until_gone
from modules.gamemode_select import click_play, _wait_and_click, _run_steps, _sweep_carousel
from modules.stage_player import play_preset
from modules.stats import SESSION
from modules import health, notify
from modules.reconnect import handle_disconnect_if_present


# Where the current cycle is up to, kept at module level so it outlives the runner: a
# Never Stop restart builds a new BossRushRunner, and used to start the walks over from
# gate 1 on a hub where gates 1-3 were already gone. Also carries what the gate steering
# has learned (per-gate label offsets, walking speed) across restarts.
_PROGRESS = {"cleared": 0, "at": 0.0, "auto_start": False}
_LEARNED = {"gate_offsets": {}, "walk_speed": None}


def _set_progress(cleared, auto_start=None):
    _PROGRESS["cleared"] = cleared
    _PROGRESS["at"] = time.time()
    if auto_start is not None:
        _PROGRESS["auto_start"] = auto_start


def _recent_progress():
    """Gates cleared in the cycle still under way (1-6), or 0 if there's none to pick up."""
    if time.time() - _PROGRESS["at"] > config.BOSSRUSH_RESUME_WINDOW:
        return 0
    return _PROGRESS["cleared"]


def _find(template, screenshot=None, debug_label=None):
    shot = screenshot if screenshot is not None else capture_screen()
    return find_template(shot, template, config.MATCH_THRESHOLD, debug_label=debug_label)


def _halt(reason):
    """Stops the run as a fault (not a user stop), unless the user already stopped it."""
    if not config.STOP_REQUESTED:
        config.STUCK_DETECTED = reason
    config.STOP_REQUESTED = True
    return False


# --- Lobby navigation ----------------------------------------------------------------

# Scroll anchors for the carousel-sweep fallback: any OTHER mode card actually on screen
# (see gamemode_select._carousel_anchor()). Boss Rush is the newest mode, so it can sit
# past the visible end of the mode-card row - same situation Expeditions' card handles.
_MODE_CARD_ANCHORS = {
    "story": {"template": config.STORY_CARD, "enabled": True},
    "raid": {"template": config.RAID_CARD, "enabled": True},
    "challenge": {"template": config.CHALLENGE_CARD, "enabled": True},
    "expeditions": {"template": config.EXPEDITIONS_CARD, "enabled": True},
}


def click_bossrush_card():
    """
    Same plain check every mode card uses; if it times out, sweeps the mode-card row in
    case the card is simply scrolled out of view (see modules/expedition.py's
    click_expeditions_card, which this mirrors).
    """
    result = _wait_and_click(config.BOSSRUSH_CARD, "click_bossrush_card", recover_leftover_party=True)
    if result is not False or config.STOP_REQUESTED:
        return result

    # Saved BEFORE any scrolling: a frame taken after the sweep shows the list already
    # scrolled away, which is useless for telling why the card didn't match.
    path = health.save_debug_screenshot("bossrush_card_before_scroll")
    print(f"[BossRush] Boss Rush card not found where expected (screen saved: {path}) - "
          f"checking whether it needs scrolling into view...")
    match = _sweep_carousel(config.BOSSRUSH_CARD, "click_bossrush_card", _MODE_CARD_ANCHORS, "bossrush")
    if not match:
        health.report_missing_template(capture_screen(), "click_bossrush_card", config.BOSSRUSH_CARD)
        return False

    match = settle_match(config.BOSSRUSH_CARD, match, "click_bossrush_card") or match
    x, y, conf = match
    print(f"[BossRush] Found the Boss Rush card at ({x}, {y}) after scrolling, confidence={conf:.2f} - clicking.")
    click_at(x, y)
    time.sleep(0.8)
    return True


def click_bossrush_map_tile():
    """
    The "Boss Rush - District 7" tile on the stage screen. It's normally already
    selected, so this is best-effort: clicked if it shows, skipped (not a failure) if
    not, and never allowed to fail the navigation.
    """
    result = poll_until([target(config.BOSSRUSH_MAP_TILE, True, click=True, debug_label="bossrush_map_tile")],
                        interval=0.5, label="click_bossrush_map_tile", timeout=5.0, stuck_timeout=None)
    if result in (False, "RECONNECTED"):
        return result
    if result == "TIMEOUT":
        print("[BossRush] Map tile not found - assuming it's already selected.")
    time.sleep(0.5)
    return True


def click_bossrush_select_stage():
    return _wait_and_click(config.BOSSRUSH_SELECT_STAGE_BTN, "click_bossrush_select_stage")


def click_bossrush_start():
    """The party screen's Start, before the run itself begins."""
    return _wait_and_click(config.BOSSRUSH_START_BTN, "click_bossrush_start")


def run_bossrush_menu_flow():
    """
    Lobby -> Boss Rush card -> map tile -> Select Stage -> Start. Lands on whatever screen shows
    the hub-entry Start Game button next - see BossRushRunner._click_start_game(),
    which is what actually gets onto the hub. Returns True/False/"RECONNECTED".
    """
    return _run_steps([
        click_play,
        click_bossrush_card,
        click_bossrush_map_tile,
        click_bossrush_select_stage,
        click_bossrush_start,
    ])


# --- The run ---------------------------------------------------------------------------

class BossRushRunner:
    def __init__(self, gate_preset_name, boss_preset_name, walk_preset_names, set_phase=None):
        """
        walk_preset_names: {gate_number (1-6): recording name} - each gate's walk is
        its own PresetPicker slot (see ui_qt/pages.py BossRushPage), not one shared name.
        """
        self.gate_preset_name = gate_preset_name
        self.boss_preset_name = boss_preset_name
        self.walk_preset_names = walk_preset_names
        self.set_phase = set_phase
        self.consecutive_defeats = 0
        # True once the hub loaded WITHOUT a Start Game button (see _enter_hub): the
        # game's own Auto Start is taking those clicks, so gates won't show one either.
        self.auto_start = False
        # Learned while running (see _learn_gate_offset / _seek_gate): where each gate's
        # label sits relative to the character when its prompt is up, and how fast the
        # character crosses the screen.
        self.gate_offsets = _LEARNED["gate_offsets"]
        self.walk_speed = _LEARNED["walk_speed"] or config.BOSSRUSH_SEEK_START_SPEED
        # (gate number, step) to pick a cycle up from - set by navigate() when a restart
        # lands mid-cycle, used once by play_cycle(). Gate 7 = the boss.
        self._resume = None

    def _phase(self, text, color="#4fc3f7"):
        if self.set_phase:
            self.set_phase(text, color)

    # --- units ---
    def _place_units(self, variant, preset_name):
        """Plays the gate or boss unit macro. True / False / "RECONNECTED"."""
        self._phase("PLACING UNITS", "#2e7d32")
        print(f"[BossRush] Playing the {variant} macro '{preset_name}'.")
        result = play_preset(config.BOSSRUSH_PRESET_LOCATION, variant, preset_name)
        if result is True or result == "RECONNECTED":
            return result
        if config.STOP_REQUESTED:
            return False
        print(f"[BossRush] The {variant} macro '{preset_name}' is missing or empty - "
              f"record it first (F8 in-game).")
        return _halt(f"BOSS RUSH {variant.upper()} MACRO MISSING")

    # --- navigation ------------------------------------------------------------------
    def navigate(self):
        # Already on the Boss Rush map (the run was stopped and started again, or Never
        # Stop restarted it after a problem): there's no Play button to find from here,
        # which is exactly how a tester's restarts failed (2026-09-27, "click_play
        # TIMEOUT" twice while standing in the hub). Pick up from the hub instead.
        shot = capture_screen()
        if _find(config.REPEAT_STAGE_BTN, shot):
            _set_progress(0)
            print("[BossRush] Already on the boss's Repeat Stage screen - repeating from there.")
            return self.click_repeat_if_present()
        cleared = _recent_progress()
        if cleared:
            from modules.lobby import at_lobby
            if not at_lobby(shot):
                return self._resume_mid_cycle(cleared, shot)
        if _find(config.BOSSRUSH_MAP_HUD, shot) or _find(config.BOSSRUSH_START_GAME_BTN, shot):
            print("[BossRush] Already on the Boss Rush map - skipping the menus. (If some gates were "
                  "already cleared this cycle, the walks start again from gate 1.)")
            return self._enter_hub()
        if _find(config.BOSSRUSH_START_BTN, shot):
            print("[BossRush] Already on the party screen - starting from there.")
            return self._enter_hub()        # clicks Start itself (see its party-screen handling)

        _set_progress(0)
        self._phase("NAVIGATING MENUS")
        print("[BossRush] Navigating to Boss Rush...")
        result = run_bossrush_menu_flow()
        if result is not True:
            return result
        return self._enter_hub()

    def _enter_hub(self):
        """
        Map loads -> fix the camera -> THEN click Start Game. The camera goes first so
        the recorded walks and macros line up with what they were recorded against.
        Used after the party screen, after Repeat Stage, and when a run starts on the map.

        "Loaded" is Start Game OR the hub's HUD (config.BOSSRUSH_MAP_HUD). Start Game on
        its own isn't enough: with the game's own Auto Start setting on, the hub never
        shows it - a tester's bot (2026-09-27) sat on "Waiting for map" on a fully loaded
        hub until it timed out, every time, and never got as far as the camera or a walk.
        The HUD is a screen overlay, so it matches whatever the camera is doing.
        """
        _set_progress(0)
        self._phase("WAITING FOR MAP")
        print("[BossRush] Waiting for the map to load (Start Game button or the hub's HUD)...")
        # The party screen's Select Stage / Start are watched for too, and clicked: that's
        # where a stray click on the results screen's View Party (right next to Repeat
        # Stage in Boss Rush) lands - reported live 2026-09-29, the run then sat there
        # until it failed. Both are default-threshold and score <= 0.62 on the hub and on
        # Start Game, so they can't be confused with either.
        #
        # 2026-09-29 (the user's own PC): the click meant for Repeat Stage randomly opens View
        # Party instead, and the run sat on that screen. Back is the way out of it: it lands
        # on the results screen again, and Repeat Stage is then clicked from there. Back is
        # listed before Start / Select Stage so a party screen that also shows those is left
        # by Back rather than started from.
        party_clicks = 0
        while True:
            result = poll_until([target(config.BOSSRUSH_START_GAME_BTN, "START_GAME", debug_label="bossrush_map_loaded"),
                                 target(config.BOSSRUSH_MAP_HUD, "HUD", debug_label="bossrush_map_hud"),
                                 target(config.BACK_BTN, "BACK", debug_label="bossrush_back"),
                                 target(config.REPEAT_STAGE_BTN, "RESULTS", debug_label="bossrush_results_repeat"),
                                 target(config.BOSSRUSH_START_BTN, "PARTY_START", debug_label="bossrush_party_start"),
                                 target(config.BOSSRUSH_SELECT_STAGE_BTN, "PARTY_SELECT", debug_label="bossrush_party_select")],
                                interval=1.0, label="bossrush_map_loaded", timeout=90.0,
                                stuck_timeout=None, lobby_grace=25.0)
            if result not in ("BACK", "RESULTS", "PARTY_START", "PARTY_SELECT") or party_clicks >= 6:
                break
            party_clicks += 1
            time.sleep(0.5)                    # let a popping-in screen settle before clicking
            if result == "BACK":
                match = _find(config.BACK_BTN)
                if match:
                    print("[BossRush] On the party screen (View Party got clicked) - clicking Back.")
                    click_until_gone(config.BACK_BTN, match, "bossrush_back")
                time.sleep(1.5)
                continue
            if result == "RESULTS":
                # Back on the results screen (or still on it): Repeat Stage is the click that
                # was wanted in the first place.
                match = _find(config.REPEAT_STAGE_BTN)
                if match:
                    print("[BossRush] On the results screen - clicking Repeat Stage.")
                    click_until_gone(config.REPEAT_STAGE_BTN, match, "bossrush_repeat", clicks=1)
                time.sleep(1.5)
                continue
            template = config.BOSSRUSH_START_BTN if result == "PARTY_START" else config.BOSSRUSH_SELECT_STAGE_BTN
            match = _find(template)
            if match:
                print(f"[BossRush] On the party screen instead of the map - clicking "
                      f"{'Start' if result == 'PARTY_START' else 'Select Stage'} to get back in.")
                click_until_gone(template, match, f"bossrush_{result.lower()}")
            time.sleep(1.0)
        if result in ("BACK", "RESULTS", "PARTY_START", "PARTY_SELECT"):
            path = health.save_debug_screenshot("bossrush_stuck_on_party_screen")
            print(f"[BossRush] Still on the party/results screen after {party_clicks} clicks "
                  f"(screen saved: {path}).")
            return False
        if result == "TIMEOUT":
            path = health.save_debug_screenshot("bossrush_map_not_loaded")
            print(f"[BossRush] Neither Start Game nor the Boss Rush HUD appeared (screen saved: {path}).")
            return False
        if result in (False, "RECONNECTED"):
            return result

        if result == "HUD":
            # The HUD can beat the button onto the screen - give it a moment first.
            wait = poll_until([target(config.BOSSRUSH_START_GAME_BTN, "START_GAME", debug_label="bossrush_hub_start_game")],
                              interval=0.5, label="bossrush_hub_start_game",
                              timeout=config.BOSSRUSH_START_GAME_GRACE, stuck_timeout=None)
            if wait in (False, "RECONNECTED"):
                return wait
            result = "START_GAME" if wait == "START_GAME" else "HUD"

        if config.STOP_REQUESTED:
            return False
        time.sleep(1.0)
        self._phase("ANCHORING CAMERA")
        anchor_camera()
        time.sleep(0.5)

        # One more look after the camera move, whichever way the map was recognised.
        match = _find(config.BOSSRUSH_START_GAME_BTN)
        if match:
            self.auto_start = False
            self._phase("STARTING RUN")
            if not click_until_gone(config.BOSSRUSH_START_GAME_BTN, match, "bossrush_start_game", clicks=2):
                if config.STOP_REQUESTED:
                    return False
        elif result == "START_GAME":
            # It was there before the camera moved and is gone now - clicked by Auto Start.
            self.auto_start = True
            print("[BossRush] Start Game went away by itself - the game's Auto Start is on.")
        else:
            self.auto_start = True
            path = health.save_debug_screenshot("bossrush_hub_without_start_game")
            print(f"[BossRush] (screen saved: {path})")
            print(f"[BossRush] The hub has loaded with no Start Game button after "
                  f"{config.BOSSRUSH_START_GAME_GRACE:.0f}s - the game's own Auto Start is on, "
                  f"carrying on without it (gates won't show one either).")
        time.sleep(1.0)
        return True

    def _resume_mid_cycle(self, cleared, shot):
        """
        A restart (Never Stop, or Stop then Start) landed in the middle of a cycle that
        had already cleared `cleared` gates. Works out where exactly from the screen and
        sets self._resume for play_cycle(), instead of walking gate 1 again - that gate
        is gone, and the run used to fail there.
        """
        self.auto_start = _PROGRESS["auto_start"]
        nxt = cleared + 1
        where = f"gate {nxt}" if nxt <= config.BOSSRUSH_TOTAL_GATES else "the boss"
        print(f"[BossRush] Picking the run back up: {cleared} gate(s) were already cleared this cycle, "
              f"next is {where}.")
        self._phase("RESUMING RUN")

        if _find(config.BOSSRUSH_SELECT_CARD, shot) and nxt <= config.BOSSRUSH_TOTAL_GATES:
            print(f"[BossRush] Gate {nxt}'s card screen is up - picking a card first.")
            self._resume = (nxt, "card")
        elif _find(config.BOSSRUSH_CONTINUE_BTN, shot):
            result = self._click_continue(timeout=5.0)
            if result is not True:
                return result
            time.sleep(3.0)
            if nxt > config.BOSSRUSH_TOTAL_GATES:
                self._resume = (nxt, "start")
            else:
                result = self._wait_for_hub_return()
                if result is not True:
                    return result
                self._resume = (nxt, "walk")
        elif _find(config.BOSSRUSH_START_GAME_BTN, shot):
            # Inside the next gate (or at the boss) with the fight not started yet.
            self._resume = (nxt, "start")
        elif nxt > config.BOSSRUSH_TOTAL_GATES:
            self._resume = (nxt, "fight")       # the boss fight is already running
        else:
            # On the hub, or inside a gate mid-fight. Gate labels only show on the hub -
            # and only with the camera anchored, so it's anchored once before deciding.
            labels = self._gate_labels(shot)
            if not labels:
                anchor_camera()
                time.sleep(0.8)
                labels = self._gate_labels(capture_screen())
            if labels:
                self._resume = (nxt, "walk")
            else:
                print(f"[BossRush] No gates in view - assuming gate {nxt}'s fight is under way.")
                self._resume = (nxt, "fight")

        print(f"[BossRush] Resuming at {where} ({self._resume[1]}).")
        notify.problem("Boss Rush picked up mid-run",
                       f"The run restarted with {cleared}/{config.BOSSRUSH_TOTAL_GATES} gates already "
                       f"cleared - carrying on from {where} instead of starting over at gate 1.",
                       recovered=True)
        return True

    def _wait_for_hub_return(self):
        """
        After a gate: waits for the hub's HUD before the next walk starts, instead of a
        fixed pause only. A walk that begins while the teleport back is still going
        starts from the wrong spot (or with the character frozen for its first second),
        and ends short of the gate - one cause of "the walk isn't precise". Never fails
        the run: after 20s it just carries on as before.
        """
        result = poll_until([target(config.BOSSRUSH_MAP_HUD, True, debug_label="bossrush_hub_return")],
                            interval=0.5, label="bossrush_hub_return", timeout=20.0, stuck_timeout=None)
        if result in (False, "RECONNECTED"):
            return result
        if result == "TIMEOUT":
            print("[BossRush] Didn't see the hub's HUD within 20s after the gate - carrying on.")
        # The HUD shows up with the hub; the character needs a moment more to land.
        time.sleep(1.5)
        return True

    def _walk_to_gate(self, gate_number):
        variant = config.bossrush_walk_variant(gate_number)
        name = self.walk_preset_names.get(gate_number)
        if not name:
            print(f"[BossRush] No walk recording chosen for gate {gate_number}.")
            return _halt("BOSS RUSH GATE WALK MISSING")

        # The "Gate" labels only match at the anchored zoom (they grow as the camera
        # closes in), so none visible on the hub means the camera has drifted - and a
        # walk replayed under a drifted camera goes somewhere else. Fixed before walking.
        shot = capture_screen()
        if self._settings_open(shot):
            # A Settings panel left open (it sometimes ignores being closed) covers the
            # middle of the screen and swallows the walk's keys - closed first.
            print("[BossRush] The Settings panel is open - closing it before the walk.")
            self._close_settings()
            shot = capture_screen()
        if _find(config.BOSSRUSH_MAP_HUD, shot) and not self._gate_labels(shot):
            print("[BossRush] No gate labels visible - the camera has moved. Re-anchoring it before the walk.")
            self._phase("ANCHORING CAMERA")
            anchor_camera()
            time.sleep(0.8)

        self._phase(f"WALKING TO GATE {gate_number}")
        print(f"[BossRush] Walking to gate {gate_number} (macro '{name}')...")
        result = play_preset(config.BOSSRUSH_PRESET_LOCATION, variant, name, movement_only=True)
        if result is True or result == "RECONNECTED":
            return result
        if config.STOP_REQUESTED:
            return False
        print(f"[BossRush] The walk for gate {gate_number} ('{name}') is missing or empty - "
              f"record it first (F8 in-game, walk only - hold W/A/S/D from the hub spawn point "
              f"to the gate, then press F8 again without placing anything).")
        return _halt("BOSS RUSH GATE WALK MISSING")

    def _enter_gate(self, gate_number):
        """
        Gets into the gate the walk was meant to reach, in three layers:

          1. Where the walk ended: E at the prompt (pressed blind if the prompt isn't
             recognised - it has scored under the bar on a gate the character was
             standing at, and a stray E away from a gate does nothing).
          2. Still outside: find the closest "Gate" label and steer to it by sight
             (_seek_gate) until the prompt shows, then E.
          3. Still outside: Settings -> Teleport to Spawn (_reset_to_spawn), play this
             gate's walk again from the start, and repeat 1-2. BOSSRUSH_GATE_RESETS
             rounds of that before the run stops.

        Returns once the gate has opened: True / False / "RECONNECTED".
        """
        for round_no in range(0, config.BOSSRUSH_GATE_RESETS + 1):
            if config.STOP_REQUESTED:
                return False
            if round_no > 0:
                print(f"[BossRush] Resetting to spawn and walking to gate {gate_number} again "
                      f"(reset {round_no}/{config.BOSSRUSH_GATE_RESETS}).")
                try:
                    shot_bytes = health.jpg_bytes()
                except Exception:
                    shot_bytes = None
                notify.problem("Boss Rush: reset to spawn",
                               f"Couldn't get into gate {gate_number} - the walk ended away from it and "
                               f"walking to it by sight didn't work. Teleporting back to spawn to walk it "
                               f"again (reset {round_no}/{config.BOSSRUSH_GATE_RESETS}).",
                               image_bytes=shot_bytes)
                result = self._reset_to_spawn()
                if result is not True:
                    return result
                result = self._walk_to_gate(gate_number)
                if result is not True:
                    return result

            self._phase("ENTERING GATE")
            result = self._press_enter(gate_number, prompt_wait=8.0, blind_ok=True)
            if result is not None:
                return result

            if gate_number >= 2 and _find(config.BOSSRUSH_SELECT_CARD):
                # Can't get in because the previous gate isn't over: its card is up now.
                # Finish it properly instead of teleporting around a live fight.
                print(f"[BossRush] Gate {gate_number - 1}'s card screen is up - it wasn't finished. "
                      f"Finishing it first.")
                return "PREVIOUS_UNFINISHED"

            result = self._seek_gate(gate_number)
            if result in (False, "RECONNECTED"):
                return result
            if result is True:
                self._phase("ENTERING GATE")
                result = self._press_enter(gate_number, prompt_wait=2.0, blind_ok=False)
                if result is not None:
                    return result

        path = health.save_debug_screenshot("bossrush_could_not_enter_gate")
        print(f"[BossRush] Couldn't get into gate {gate_number} after {config.BOSSRUSH_GATE_RESETS} resets "
              f"to spawn (screen saved: {path}).")
        return _halt("BOSS RUSH COULD NOT ENTER GATE")

    def _press_enter(self, gate_number, prompt_wait, blind_ok):
        """
        One go at the prompt: waits up to prompt_wait for it, presses E (and clicks the
        prompt when seen), and checks the gate opened. A prompt that stays up after a
        press that didn't take gets one more. True = in, None = not in (try the next
        layer), False / "RECONNECTED" as usual.
        """
        for attempt in (1, 2):
            if config.STOP_REQUESTED:
                return False
            result = poll_until([target(config.BOSSRUSH_ENTER_GATE_BTN, True, debug_label="bossrush_enter_gate")],
                                interval=0.4, label="bossrush_enter_gate",
                                timeout=prompt_wait if attempt == 1 else 1.5, stuck_timeout=None)
            if result in (False, "RECONNECTED"):
                return result

            shot = capture_screen()
            match = _find(config.BOSSRUSH_ENTER_GATE_BTN, shot) if result is True else None
            if match is None and not blind_ok:
                return None
            if match is None and attempt > 1:
                return None          # a second blind E won't do what the first didn't
            if match:
                self._learn_gate_offset(gate_number, shot)
            pydirectinput.press("e")
            if match:
                print(f"[BossRush] Enter Gate prompt at ({match[0]}, {match[1]}) - pressed E and clicking it.")
                time.sleep(0.3)
                click_at(match[0], match[1])
            else:
                print("[BossRush] Enter Gate prompt not recognised - pressed E anyway.")

            if match is None and self.auto_start:
                # With Auto Start there's no Start Game to prove a blind E worked, and
                # "the prompt is gone" proves nothing when it was never seen - a short
                # walk looks exactly like that. Only a Start Game counts here.
                opened = poll_until([target(config.BOSSRUSH_START_GAME_BTN, True, debug_label="bossrush_gate_loaded")],
                                    interval=0.5, label="bossrush_gate_loaded", timeout=5.0, stuck_timeout=None)
                opened = None if opened == "TIMEOUT" else opened
            else:
                opened = self._gate_opened(prompt_seen=bool(match))
            if opened is not None:
                return opened
            print("[BossRush] The gate didn't open.")
        return None

    def _gate_opened(self, prompt_seen):
        """True / None (didn't open) / False / "RECONNECTED" after an E press."""
        if self.auto_start:
            return self._entered_without_start_game(prompt_seen=prompt_seen)
        loaded = poll_until([target(config.BOSSRUSH_START_GAME_BTN, True, debug_label="bossrush_gate_loaded")],
                            interval=0.5, label="bossrush_gate_loaded", timeout=12.0, stuck_timeout=None)
        if loaded in (False, "RECONNECTED", True):
            return loaded
        return None

    # --- finding a gate by sight -----------------------------------------------------
    def _gate_labels(self, shot):
        return find_all_templates(shot, config.BOSSRUSH_GATE, config.MATCH_THRESHOLD)

    def _learn_gate_offset(self, gate_number, shot):
        """
        With the prompt up, the closest label is this gate's, and where it sits relative
        to the character is exactly where _seek_gate should put it. Remembered per gate,
        so a later rescue aims at the spot the good walks reached rather than a guess.
        """
        px, py = config.BOSSRUSH_PLAYER_POS
        labels = self._gate_labels(shot)
        if not labels:
            return
        lx, ly, _ = min(labels, key=lambda l: (l[0] - px) ** 2 + (l[1] - py) ** 2)
        offset = (lx - px, ly - py)
        if abs(offset[0]) <= 150 and abs(offset[1]) <= 150:
            if self.gate_offsets.get(gate_number) != offset:
                print(f"[BossRush] Gate {gate_number}: label sits at {offset} from the character "
                      f"when the prompt shows - remembered for steering.")
            self.gate_offsets[gate_number] = offset

    def _seek_gate(self, gate_number):
        """
        The walk ended somewhere without the Enter Gate prompt: find the "Gate" label
        closest to where this gate's label should be, and walk at it in short WASD
        pulses until the prompt appears.

        The camera follows the character, so the character stays at BOSSRUSH_PLAYER_POS
        and the world (labels included) slides the other way as it walks - moving the
        label onto its target offset IS walking to the gate. Each pulse is checked by
        how far the label actually moved: barely at all means something is in the way,
        answered with a jump (Space) held into the same direction, then a sidestep.

        True = the prompt is up. None = gave up (no labels, stuck, heading the wrong
        way, out of time) - the caller resets to spawn. False / "RECONNECTED" as usual.
        """
        self._phase(f"FINDING GATE {gate_number}")
        px, py = config.BOSSRUSH_PLAYER_POS
        base_off = self.gate_offsets.get(gate_number, config.BOSSRUSH_GATE_LABEL_OFFSET)
        # Tried in turn once the label is on its spot and still no prompt: the spot
        # itself, then a little below/above/left/right of it.
        probes = [(0, 0), (0, 35), (0, -35), (-35, 0), (35, 0)]
        probe_i = 0
        tracked = None            # (x, y) of the label being steered to
        stuck = 0
        stuck_total = 0           # blocked pulses in all, so a wall can't be retried forever
        worse = 0
        last_dist = None
        misses = 0
        speed_known = self.walk_speed != config.BOSSRUSH_SEEK_START_SPEED
        deadline = time.time() + config.BOSSRUSH_SEEK_TIMEOUT
        print(f"[BossRush] No Enter Gate prompt - looking for the closest gate to walk to "
              f"(aiming the label at {base_off} from the character).")

        while time.time() < deadline:
            if config.STOP_REQUESTED:
                return False
            shot = capture_screen()
            if handle_disconnect_if_present(shot):
                return "RECONNECTED"
            if _find(config.BOSSRUSH_ENTER_GATE_BTN, shot):
                print("[BossRush] Enter Gate prompt found.")
                return True

            labels = self._gate_labels(shot)
            if not labels:
                misses += 1
                if misses >= 4:
                    path = health.save_debug_screenshot("bossrush_no_gate_labels")
                    print(f"[BossRush] Can't see any gate labels (screen saved: {path}).")
                    return None
                time.sleep(0.3)
                continue
            misses = 0

            off_x, off_y = base_off[0] + probes[probe_i][0], base_off[1] + probes[probe_i][1]
            want = (px + off_x, py + off_y)          # where the label should end up on screen
            if tracked is None:
                label = min(labels, key=lambda l: (l[0] - want[0]) ** 2 + (l[1] - want[1]) ** 2)
                print(f"[BossRush] {len(labels)} gate(s) visible - heading for the one at "
                      f"({label[0]}, {label[1]}).")
            else:
                label = min(labels, key=lambda l: (l[0] - tracked[0]) ** 2 + (l[1] - tracked[1]) ** 2)
                if (label[0] - tracked[0]) ** 2 + (label[1] - tracked[1]) ** 2 > 90 ** 2:
                    # Lost it (hidden behind something, or it was a different label) -
                    # pick again from scratch rather than chase a far one.
                    label = min(labels, key=lambda l: (l[0] - want[0]) ** 2 + (l[1] - want[1]) ** 2)
                    print(f"[BossRush] Lost the gate label - re-picking the one at ({label[0]}, {label[1]}).")
            lx, ly = label[0], label[1]

            # How far the CHARACTER must move (screen px): the label slides the opposite way.
            mx, my = lx - want[0], ly - want[1]
            dist = (mx * mx + my * my) ** 0.5
            if dist <= 12:
                probe_i += 1
                if probe_i >= len(probes):
                    print("[BossRush] Reached the gate's label but no prompt showed.")
                    return None
                tracked = (lx, ly)
                last_dist = None
                continue

            if last_dist is not None and dist > last_dist + 8:
                worse += 1
                if worse >= 3:
                    print("[BossRush] Walking is taking the character away from the gate - giving up on steering.")
                    return None
            else:
                worse = 0
            last_dist = dist

            jump = stuck >= 1
            sidestep = stuck >= 2
            # Until one pulse has been measured the speed is a guess, so the first is kept
            # short; after that each pulse covers at most ~60px, so the label can't jump
            # far enough to be mistaken for its neighbour (gates sit ~100px apart).
            max_hold = 0.12 if not speed_known else min(config.BOSSRUSH_SEEK_MAX_PULSE, 60.0 / self.walk_speed)
            tx, ty = self._walk_pulse(mx, my, jump=jump, sidestep=sidestep, max_hold=max_hold)
            time.sleep(0.2)

            after = self._gate_labels(capture_screen())
            if not after:
                tracked = None
                continue
            # Where the label should be now, given the move just made.
            pred_x = lx - (1 if mx > 0 else -1) * self.walk_speed * tx
            pred_y = ly - (1 if my > 0 else -1) * self.walk_speed * ty
            nx, ny, _ = min(after, key=lambda l: (l[0] - pred_x) ** 2 + (l[1] - pred_y) ** 2)
            moved_x, moved_y = lx - nx, ly - ny
            expected = self.walk_speed * max(tx, ty)
            moved = (moved_x * moved_x + moved_y * moved_y) ** 0.5
            if expected >= 10 and moved < 0.25 * expected and speed_known:
                stuck += 1
                stuck_total += 1
                print(f"[BossRush] Barely moved ({moved:.0f}px) - "
                      f"{'jumping' if stuck == 1 else 'jumping and sidestepping'} next.")
                if stuck > 4 or stuck_total >= 6:
                    print("[BossRush] Stuck against something - giving up on steering.")
                    return None
            else:
                stuck = 0
                if max(tx, ty) >= 0.1 and moved > 3:
                    measured = max(40.0, min(800.0, moved / max(tx, ty)))
                    self.walk_speed = measured if not speed_known else 0.6 * self.walk_speed + 0.4 * measured
                    speed_known = True
                    _LEARNED["walk_speed"] = self.walk_speed
            tracked = (nx, ny)

        print(f"[BossRush] Couldn't reach a gate within {config.BOSSRUSH_SEEK_TIMEOUT:.0f}s of steering.")
        return None

    def _walk_pulse(self, mx, my, jump=False, sidestep=False, max_hold=None):
        """
        Holds W/A/S/D towards a screen-space move (mx, my) - both axes at once, each for
        its own share of the distance - and returns how long each axis was held.
        """
        speed = self.walk_speed
        cap = config.BOSSRUSH_SEEK_MAX_PULSE if max_hold is None else max_hold
        tx = min(abs(mx) / speed, cap) if abs(mx) > 6 else 0.0
        ty = min(abs(my) / speed, cap) if abs(my) > 6 else 0.0
        keys = []
        if tx:
            keys.append(("d" if mx > 0 else "a", tx))
        if ty:
            keys.append(("s" if my > 0 else "w", ty))
        if sidestep:
            # Around whatever is in the way: a short step at right angles first.
            side = ("a" if my > 0 else "d") if abs(my) >= abs(mx) else ("s" if mx > 0 else "w")
            with high_res_timer():
                pydirectinput.keyDown(side)
                try:
                    time.sleep(0.3)
                finally:
                    pydirectinput.keyUp(side)
            mark_input()
        if not keys:
            return 0.0, 0.0

        held = []
        with high_res_timer():
            try:
                for key, _ in keys:
                    pydirectinput.keyDown(key)
                    held.append(key)
                if jump:
                    time.sleep(0.05)
                    pydirectinput.press("space")
                start = time.time()
                for key, hold in sorted(keys, key=lambda k: k[1]):
                    remaining = hold - (time.time() - start)
                    if remaining > 0:
                        time.sleep(remaining)
                    pydirectinput.keyUp(key)
                    held.remove(key)
            finally:
                # Never leave a key down - the character would walk off on its own.
                for key in held:
                    pydirectinput.keyUp(key)
        mark_input()
        return tx, ty

    # --- starting over from spawn ---------------------------------------------------
    def _reset_to_spawn(self):
        """
        Settings (top-bar gear) -> search "teleport" -> Teleport To Spawn, then closes the
        panel and re-anchors the camera - puts the character back on the spot every gate
        walk was recorded from. True / False / "RECONNECTED"; a menu that can't be worked
        stops the run.
        """
        self._phase("RESET TO SPAWN", "#f9a825")
        for attempt in (1, 2, 3):
            if config.STOP_REQUESTED:
                return False
            shot = capture_screen()
            if handle_disconnect_if_present(shot):
                return "RECONNECTED"
            teleport = _find(config.TELEPORT_SPAWN_BTN, shot)
            if not teleport:
                if not _find(config.SETTINGS_SEARCH_BAR, shot):
                    # Panel not open (the gear only exists while it isn't - it changes look
                    # while open, which is why the panel is detected by its search box).
                    gear = _find(config.SETTINGS_BTN, shot, debug_label="settings_btn")
                    if not gear:
                        path = health.save_debug_screenshot("bossrush_no_settings_button")
                        print(f"[BossRush] Settings button not found (screen saved: {path}).")
                        time.sleep(1.0)
                        continue
                    print(f"[BossRush] Opening Settings at ({gear[0]}, {gear[1]}).")
                    click_at(gear[0], gear[1])
                    time.sleep(1.2)
                teleport = self._search_for_teleport()
                if teleport in (False, "RECONNECTED"):
                    return teleport
                if not teleport:
                    path = health.save_debug_screenshot("bossrush_no_teleport_button")
                    print(f"[BossRush] Settings is open but Teleport To Spawn didn't show up "
                          f"(screen saved: {path}).")
                    self._close_settings()
                    continue

            print(f"[BossRush] Teleport to Spawn at ({teleport[0]}, {teleport[1]}) - clicking.")
            click_at(teleport[0], teleport[1])
            time.sleep(1.5)
            self._close_settings()
            time.sleep(1.0)
            self._phase("ANCHORING CAMERA")
            anchor_camera()
            time.sleep(0.8)
            return True

        path = health.save_debug_screenshot("bossrush_reset_to_spawn_failed")
        print(f"[BossRush] Couldn't teleport back to spawn (screen saved: {path}).")
        return _halt("BOSS RUSH COULD NOT RESET TO SPAWN")

    def _search_for_teleport(self):
        """
        With the Settings panel open: clicks the search box, clears it and types
        "teleport", then waits for the Teleport To Spawn button. Returns its match, None
        if it never showed, False / "RECONNECTED" as usual.

        A box that still holds last time's text is handled too - the panel keeps it, and
        then shows the placeholder no more, so the box is clicked at its fixed spot
        instead of being found. Nothing is typed unless the panel is really open (its
        search box or its red X is on screen): stray keystrokes into the game would be
        hotkeys.
        """
        shot = capture_screen()
        bar = _find(config.SETTINGS_SEARCH_BAR, shot)
        close = _find(config.SETTINGS_CLOSE_BTN, shot)
        panel_open = bool(bar) or bool(close and abs(close[0] - 1316) < 40 and abs(close[1] - 167) < 40)
        if not panel_open and not _find(config.TELEPORT_SPAWN_BTN, shot):
            print("[BossRush] The Settings panel isn't open - not typing into the game.")
            return None
        x, y = (bar[0], bar[1]) if bar else config.SETTINGS_SEARCH_POS
        if not _find(config.TELEPORT_SPAWN_BTN, shot):
            print(f"[BossRush] Searching the settings for 'teleport' (box at ({x}, {y})).")
            click_at(x, y)
            time.sleep(0.4)
            for _ in range(12):
                pydirectinput.press("backspace")
            type_text("teleport")
            mark_input()
            time.sleep(1.0)
        result = poll_until([target(config.TELEPORT_SPAWN_BTN, True, debug_label="teleport_spawn")],
                            interval=0.4, label="teleport_spawn", timeout=5.0, stuck_timeout=None)
        if result in (False, "RECONNECTED"):
            return result
        return _find(config.TELEPORT_SPAWN_BTN) if result is True else None

    @staticmethod
    def _settings_close_button(shot):
        """
        The Settings panel's red X if it's on screen: either close crop (the panel's own,
        and the generic close.png), accepted only near where that panel draws it - other
        panels have identical X buttons elsewhere.
        """
        for template in (config.SETTINGS_CLOSE_BTN, config.CLOSE_SETTINGS_BTN, config.CLOSE_BTN):
            close = _find(template, shot)
            if close and abs(close[0] - 1316) < 45 and abs(close[1] - 167) < 45:
                return close
        return None

    def _settings_open(self, shot):
        return bool(_find(config.TELEPORT_SPAWN_BTN, shot) or _find(config.SETTINGS_SEARCH_BAR, shot)
                    or self._settings_close_button(shot))

    def _close_settings(self, attempts=5):
        """
        Closes the Settings panel with its red X (the gear looks different while the panel
        is open, so it can't be relied on to toggle it). The game sometimes ignores a click
        on it and leaves the panel up (seen live 2026-09-29), so it is looked at again after
        every click and clicked again for as long as it is there.
        """
        for attempt in range(1, attempts + 1):
            shot = capture_screen()
            if not self._settings_open(shot):
                return True
            close = self._settings_close_button(shot)
            x, y = (close[0], close[1]) if close else (1316, 167)
            if attempt > 1:
                print(f"[BossRush] The settings panel is still open - clicking its X again ({attempt}/{attempts}).")
            click_at(x, y)
            time.sleep(1.0)
        if self._settings_open(capture_screen()):
            path = health.save_debug_screenshot("bossrush_settings_wont_close")
            print(f"[BossRush] The settings panel won't close (screen saved: {path}).")
            return False
        return True

    def _entered_without_start_game(self, prompt_seen):
        """
        Auto Start mode's version of "did the gate open?": no Start Game will ever come
        to prove it, so a Start Game is still taken if one shows, and otherwise the
        Enter Gate prompt having gone (and staying gone) is the proof - it only exists
        while standing at a closed gate. True = entered, None = not yet (retry E),
        False / "RECONNECTED" as usual.
        """
        gone_since = None
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if config.STOP_REQUESTED:
                return False
            shot = capture_screen()
            if _find(config.BOSSRUSH_START_GAME_BTN, shot):
                return True
            if _find(config.BOSSRUSH_ENTER_GATE_BTN, shot):
                gone_since = None
            elif gone_since is None:
                gone_since = time.time()
            elif time.time() - gone_since >= 2.0:
                if not prompt_seen:
                    print("[BossRush] Enter Gate prompt wasn't recognised before E, and isn't there now - "
                          "assuming the gate opened.")
                return True
            time.sleep(0.4)
        return None

    def _wait_for_start_game(self, timeout=30.0, auto_start_timeout=5.0):
        """
        Waits (without clicking) until the Start Game button is up. True / False / "RECONNECTED".

        In Auto Start mode (see _enter_hub) the button isn't expected at all: the wait is
        cut to auto_start_timeout and not seeing it is True, not a failure - the fight has
        already begun on its own.
        """
        if self.auto_start:
            timeout = min(timeout, auto_start_timeout)
        result = poll_until([target(config.BOSSRUSH_START_GAME_BTN, True, debug_label="bossrush_start_game")],
                            interval=0.5, label="bossrush_start_game", timeout=timeout, stuck_timeout=None)
        if result == "TIMEOUT" and self.auto_start:
            print("[BossRush] No Start Game here (the game's Auto Start is on) - carrying on.")
            return True
        if result == "TIMEOUT":
            # "TIMEOUT" is a truthy string, so returning it would read as success further
            # up and the run would carry on into a match that never started.
            path = health.save_debug_screenshot("bossrush_start_game_not_found")
            print(f"[BossRush] Start Game never appeared within {timeout:.0f}s (screen saved: {path}).")
            return False
        return result

    def _click_start_game(self, timeout=20.0):
        """
        Waits for and clicks Start Game - shared by hub entry (from the party screen
        or after Repeat Stage), a gate (after Enter Gate), and the boss (after the
        last gate's continue). Same button, three different lead-ins.
        """
        result = self._wait_for_start_game(timeout)
        if result is not True:
            return result
        match = _find(config.BOSSRUSH_START_GAME_BTN)
        if match and not click_until_gone(config.BOSSRUSH_START_GAME_BTN, match, "bossrush_start_game", clicks=2):
            if config.STOP_REQUESTED:
                return False
        return True

    def _click_continue(self, timeout=30.0):
        """
        The "Skip & Fight Boss?" choice that follows every gate after the first. Continue
        is always the right click - the boss option is deliberately never taken until all
        6 gates are done. True / False / "RECONNECTED"; not seeing it in time is a warning,
        not a fault (the run carries on and the next step's own waits catch a real problem).
        """
        self._phase("CONTINUE")
        result = poll_until([target(config.BOSSRUSH_CONTINUE_BTN, True, click=True, clicks=2,
                                    debug_label="bossrush_continue")],
                            interval=0.5, label="bossrush_continue", timeout=timeout, stuck_timeout=None)
        if result in (False, "RECONNECTED"):
            return result
        if result == "TIMEOUT":
            path = health.save_debug_screenshot("bossrush_continue_not_found")
            print(f"[BossRush] No Continue button within {timeout:.0f}s (screen saved: {path}) - carrying on.")
        time.sleep(1.0)
        return True

    # --- one gate ----------------------------------------------------------------------
    def clear_gate(self, gate_number, start_at="walk"):
        """
        Walk to a gate, enter it, fight it, pick a card. True / False / "RECONNECTED".
        start_at skips the steps already done when a restart resumes mid-gate:
        "walk" (all of it), "start" (in the gate, Start Game up), "fight", "card".
        """
        step = ("walk", "start", "fight", "card").index(start_at)
        self._phase(f"GATE {gate_number}", "#2e7d32")
        print(f"[BossRush] --- Gate {gate_number}/{config.BOSSRUSH_TOTAL_GATES} ---")

        if step == 0:
            result = self._walk_to_gate(gate_number)
            if result is not True:
                return result

            result = self._enter_gate(gate_number)    # returns once the gate has opened
            if result == "PREVIOUS_UNFINISHED":
                result = self._finish_gate(gate_number - 1)
                if result is not True:
                    return result
                return self.clear_gate(gate_number)
            if result is not True:
                return result

            # Units go down BEFORE Start Game, and only on the first gate of a cycle: they stay
            # on the field for gates 2-6, which just need Start Game.
            if gate_number == 1:
                result = self._place_units(config.BOSSRUSH_GATE_VARIANT, self.gate_preset_name)
                if result is not True:
                    return result

        if step <= 1:
            result = self._click_start_game()
            if result is not True:
                return result
            # One run = 6 gates + the boss, so it's counted once: started at gate 1, and won or
            # lost only at the boss (fight_boss). Counting every gate made one run read as 7
            # wins - in the stats, Discord's per-match messages and milestones, and the run limit.
            if gate_number == 1:
                SESSION.match_started()

        return self._finish_gate(gate_number)

    def _finish_gate(self, gate_number):
        """
        The fight is running: waits for the card that ends it (or a defeat), picks a card,
        and gets back to the hub. True / False / "RECONNECTED" / whatever _gate_lost gives.
        """
        self._phase("FIGHTING GATE", "#2e7d32")
        # A lost gate ends the run on the results screen - only the card was watched for
        # before, so a defeat here sat out the full 25-minute stuck timer. And a gate that
        # takes far longer than the usual ~70s is stuck, not slow.
        deadline = time.time() + config.BOSSRUSH_GATE_FIGHT_TIMEOUT
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                result = "TIMEOUT"
            else:
                result = poll_until([target(config.BOSSRUSH_SELECT_CARD, True, debug_label="bossrush_select_card"),
                                     target(config.DEFEAT_TEXT, "DEFEAT", debug_label="bossrush_gate_defeat")],
                                    interval=1.0, label="bossrush_select_card",
                                    timeout=remaining, stuck_timeout=None)
            if result == "DEFEAT":
                return self._gate_lost(gate_number)
            if result == "TIMEOUT":
                path = health.save_debug_screenshot("bossrush_gate_fight_stuck")
                print(f"[BossRush] Gate {gate_number} hasn't finished after "
                      f"{config.BOSSRUSH_GATE_FIGHT_TIMEOUT / 60:.0f} minutes (screen saved: {path}).")
                return _halt("BOSS RUSH GATE FIGHT STUCK")
            if result is not True:
                return result
            # One frame isn't proof: live 2026-09-29 a single matching frame made the bot
            # call gate 1 cleared and walk off while wave 10 was still being fought (the
            # unit panel was open on screen). The card stays up until picked, so a real
            # one is still there a moment later.
            time.sleep(0.7)
            match = _find(config.BOSSRUSH_SELECT_CARD)
            if match:
                break
            print("[BossRush] Card screen flickered for a moment but isn't there - the fight isn't over.")

        print("[BossRush] Gate cleared - picking a card (middle of the screen).")
        click_until_gone(config.BOSSRUSH_SELECT_CARD, match, "bossrush_select_card",
                         point=(config.REFERENCE_WIDTH // 2, config.REFERENCE_HEIGHT // 2))
        print(f"[BossRush] Gate {gate_number}/{config.BOSSRUSH_TOTAL_GATES} cleared.")
        _set_progress(gate_number, auto_start=self.auto_start)

        if gate_number == 1:
            # No Continue choice after the first gate: the player is already back at the
            # gate selection, so the next walk can start once the hub is really back.
            time.sleep(2.5)
            return self._wait_for_hub_return()

        # Gates 2-6: Continue first, then the teleport back (or, after gate 6, to the boss).
        time.sleep(1.0)
        result = self._click_continue()
        if result is not True:
            return result
        time.sleep(3.0)
        if gate_number == config.BOSSRUSH_TOTAL_GATES:
            return True          # off to the boss, not back to the hub
        return self._wait_for_hub_return()

    def _gate_lost(self, gate_number):
        """
        Defeat at a gate: the run is over. Counted as a lost run and handed back as
        "REPEAT" once Repeat Stage shows, so the engine starts the next run the usual way.
        """
        _set_progress(0)
        self.consecutive_defeats += 1
        SESSION.defeat()
        print(f"[BossRush] Defeated at gate {gate_number} ({self.consecutive_defeats} in a row).")
        if config.defeat_limit_reached(self.consecutive_defeats):
            health.save_debug_screenshot("bossrush_too_many_defeats")
            print(f"[BossRush] {self.consecutive_defeats} defeats in a row - stopping.")
            return _halt("TOO MANY DEFEATS (BOSS RUSH)")
        return poll_until([target(config.REPEAT_STAGE_BTN, "REPEAT", debug_label="bossrush_repeat_wait")],
                          interval=2.0, label="bossrush_repeat_wait", stuck_timeout=config.STUCK_TIMEOUT_MENU)

    # --- the boss ------------------------------------------------------------------------
    def fight_boss(self, start_at="start"):
        """
        The 7th Start Game - the boss itself, its own macro. Does NOT click Repeat
        Stage itself (see click_repeat_if_present()) - leaving it on screen lets a
        stopped run be exited the normal way, the same reason modules/expedition.py's
        play_run() leaves its own Repeat Stage unclicked.
        Returns "REPEAT" / "RETRIED" (the game's Auto Retry already began the next run -
        the hub's Start Game is up) / False / "RECONNECTED".
        """
        self._phase("BOSS", "#c62828")
        print("[BossRush] All gates cleared - fighting the boss.")

        if start_at != "fight":         # "fight" = resumed with the boss fight already running
            # Auto Start gets longer here than at a gate: the trip to the boss is a real
            # teleport, and units placed before it lands would go down in the hub.
            result = self._wait_for_start_game(timeout=60.0, auto_start_timeout=8.0)
            if result is not True:
                return result
            result = self._place_units(config.BOSSRUSH_BOSS_VARIANT, self.boss_preset_name)
            if result is not True:
                return result

            result = self._click_start_game()
            if result is not True:
                return result
            # Not SESSION.match_started() here - the run was already started at gate 1.

        self._phase("FIGHTING BOSS", "#c62828")
        result = poll_until(
            [target(config.VICTORY_TEXT, "VICTORY", debug_label="bossrush_victory"),
             target(config.DEFEAT_TEXT, "DEFEAT", debug_label="bossrush_defeat"),
             # The game's own Auto Retry can skip the result screen entirely and land
             # straight back on the new run's hub (see the Repeat Stage wait below).
             target(config.BOSSRUSH_START_GAME_BTN, "RETRIED", debug_label="bossrush_auto_retried")],
            interval=2.0, label="bossrush_boss_result", stuck_timeout=config.BOSSRUSH_STUCK_TIMEOUT)
        if result in (False, "RECONNECTED"):
            return result
        _set_progress(0)                # the cycle is over either way

        if result == "RETRIED":
            # Result screen never seen. Auto Retry only follows a finished run, so it's
            # counted as a win - otherwise a run limit could never be reached this way.
            self.consecutive_defeats = 0
            SESSION.victory()
            print("[BossRush] The game's Auto Retry already started the next run (no result screen "
                  "seen) - counting this one as a win.")
            return "RETRIED"

        if result == "DEFEAT":
            self.consecutive_defeats += 1
            SESSION.defeat()
            print(f"[BossRush] Boss defeat ({self.consecutive_defeats} in a row).")
            if config.defeat_limit_reached(self.consecutive_defeats):
                health.save_debug_screenshot("bossrush_too_many_defeats")
                print(f"[BossRush] {self.consecutive_defeats} defeats in a row - stopping; the "
                      f"boss macro can't win this fight.")
                return _halt("TOO MANY DEFEATS (BOSS RUSH BOSS)")
        else:
            self.consecutive_defeats = 0
            SESSION.victory()
            print("[BossRush] Boss defeated.")

        # No Repeat Stage but the hub's Start Game instead = the game's own Auto Retry is on
        # and has already started the next run (rare - most players have it off).
        result = poll_until([target(config.REPEAT_STAGE_BTN, "REPEAT", debug_label="bossrush_repeat_wait"),
                             target(config.BOSSRUSH_START_GAME_BTN, "RETRIED", debug_label="bossrush_auto_retried")],
                            interval=2.0, label="bossrush_repeat_wait", stuck_timeout=config.STUCK_TIMEOUT_MENU)
        if result == "RETRIED":
            print("[BossRush] No Repeat Stage - the game's Auto Retry already started the next run.")
        return result

    def click_repeat_if_present(self):
        """
        Clicks the shared Repeat Stage button, then the Start Game that follows it -
        that second click is what actually gets back onto the hub with 6 fresh gates.
        True once both are done (or Repeat Stage was already gone); False if it's
        still stuck on screen; "RECONNECTED" if a disconnect showed up in between.
        """
        match = _find(config.REPEAT_STAGE_BTN)
        if not match:
            return True
        # ONE click, on a panel that has finished popping in. It used to be a double
        # click as soon as the button was seen: View Party sits right beside Repeat Stage
        # in Boss Rush (no Select Portal between them), and a click landing while the
        # panel was still moving - or a second click after the first had already taken -
        # could open View Party instead (reported live 2026-09-29). click_until_gone
        # still re-clicks if this one doesn't register.
        time.sleep(1.0)
        match = _find(config.REPEAT_STAGE_BTN)
        if not match:
            return self._enter_hub()
        if not click_until_gone(config.REPEAT_STAGE_BTN, match, "bossrush_repeat", clicks=1):
            return False
        time.sleep(1.0)
        return self._enter_hub()

    # --- a full cycle ----------------------------------------------------------------
    def play_cycle(self):
        """
        One full loop: 6 gates then the boss - or the rest of one, when navigate() found
        a cycle already under way. Returns "REPEAT" / "RETRIED" / False / "RECONNECTED".
        """
        first_gate, first_step = self._resume or (1, "walk")
        self._resume = None
        for gate_number in range(first_gate, config.BOSSRUSH_TOTAL_GATES + 1):
            if config.STOP_REQUESTED:
                return False
            result = self.clear_gate(gate_number, first_step if gate_number == first_gate else "walk")
            if result is not True:
                return result

        boss_step = first_step if first_gate > config.BOSSRUSH_TOTAL_GATES else "start"
        return self.fight_boss(boss_step)
