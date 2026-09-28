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
from vision import capture_screen, find_template
from input_controller import click_at, anchor_camera
from modules.polling import poll_until, target, settle_match, click_until_gone
from modules.gamemode_select import click_play, _wait_and_click, _run_steps, _sweep_carousel
from modules.stage_player import play_preset
from modules.stats import SESSION
from modules import health


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
            print("[BossRush] Already on the boss's Repeat Stage screen - repeating from there.")
            return self.click_repeat_if_present()
        if _find(config.BOSSRUSH_MAP_HUD, shot) or _find(config.BOSSRUSH_START_GAME_BTN, shot):
            print("[BossRush] Already on the Boss Rush map - skipping the menus. (If some gates were "
                  "already cleared this cycle, the walks start again from gate 1.)")
            return self._enter_hub()

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
        self._phase("WAITING FOR MAP")
        print("[BossRush] Waiting for the map to load (Start Game button or the hub's HUD)...")
        result = poll_until([target(config.BOSSRUSH_START_GAME_BTN, "START_GAME", debug_label="bossrush_map_loaded"),
                             target(config.BOSSRUSH_MAP_HUD, "HUD", debug_label="bossrush_map_hud")],
                            interval=1.0, label="bossrush_map_loaded", timeout=90.0,
                            stuck_timeout=None, lobby_grace=25.0)
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

    def _enter_gate(self):
        """
        Presses E at the gate AND clicks the E button, then checks it took (the gate's
        Start Game button appears). The prompt's crop scores below the normal bar on some
        gates, so if it is not recognised E is pressed anyway - a stray E away from a gate
        does nothing, and the Start Game check catches a real failure. Retried up to 4 times.
        """
        self._phase("ENTERING GATE")
        for attempt in range(1, 5):
            if config.STOP_REQUESTED:
                return False
            result = poll_until([target(config.BOSSRUSH_ENTER_GATE_BTN, True, debug_label="bossrush_enter_gate")],
                                interval=0.4, label="bossrush_enter_gate",
                                timeout=8.0 if attempt == 1 else 2.0, stuck_timeout=None)
            if result in (False, "RECONNECTED"):
                return result

            match = _find(config.BOSSRUSH_ENTER_GATE_BTN) if result is True else None
            pydirectinput.press("e")
            if match:
                print(f"[BossRush] Enter Gate prompt at ({match[0]}, {match[1]}) - pressed E and clicking it "
                      f"(attempt {attempt}).")
                time.sleep(0.3)
                click_at(match[0], match[1])
            else:
                print(f"[BossRush] Enter Gate prompt not recognised - pressed E anyway (attempt {attempt}).")

            if self.auto_start:
                entered = self._entered_without_start_game(prompt_seen=bool(match))
                if entered is not None:
                    return entered
            else:
                loaded = poll_until([target(config.BOSSRUSH_START_GAME_BTN, True, debug_label="bossrush_gate_loaded")],
                                    interval=0.5, label="bossrush_gate_loaded", timeout=12.0, stuck_timeout=None)
                if loaded in (False, "RECONNECTED"):
                    return loaded
                if loaded is True:
                    return True
            print("[BossRush] The gate didn't open - trying E again.")

        path = health.save_debug_screenshot("bossrush_could_not_enter_gate")
        print(f"[BossRush] Couldn't get into the gate after 4 tries (screen saved: {path}).")
        return _halt("BOSS RUSH COULD NOT ENTER GATE")

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
    def clear_gate(self, gate_number):
        """Walk to a gate, enter it, fight it, pick a card. True / False / "RECONNECTED"."""
        self._phase(f"GATE {gate_number}", "#2e7d32")
        print(f"[BossRush] --- Gate {gate_number}/{config.BOSSRUSH_TOTAL_GATES} ---")

        result = self._walk_to_gate(gate_number)
        if result is not True:
            return result

        result = self._enter_gate()          # returns once the gate's Start Game button is up
        if result is not True:
            return result

        # Units go down BEFORE Start Game, and only on the first gate of a cycle: they stay
        # on the field for gates 2-6, which just need Start Game.
        if gate_number == 1:
            result = self._place_units(config.BOSSRUSH_GATE_VARIANT, self.gate_preset_name)
            if result is not True:
                return result

        result = self._click_start_game()
        if result is not True:
            return result
        SESSION.match_started()

        self._phase("FIGHTING GATE", "#2e7d32")
        result = poll_until([target(config.BOSSRUSH_SELECT_CARD, True, debug_label="bossrush_select_card")],
                            interval=1.0, label="bossrush_select_card",
                            stuck_timeout=config.BOSSRUSH_STUCK_TIMEOUT)
        if result is not True:
            return result

        match = _find(config.BOSSRUSH_SELECT_CARD)
        if match:
            print("[BossRush] Gate cleared - picking a card (middle of the screen).")
            click_until_gone(config.BOSSRUSH_SELECT_CARD, match, "bossrush_select_card",
                             point=(config.REFERENCE_WIDTH // 2, config.REFERENCE_HEIGHT // 2))
        SESSION.victory()

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

    # --- the boss ------------------------------------------------------------------------
    def fight_boss(self):
        """
        The 7th Start Game - the boss itself, its own macro. Does NOT click Repeat
        Stage itself (see click_repeat_if_present()) - leaving it on screen lets a
        stopped run be exited the normal way, the same reason modules/expedition.py's
        play_run() leaves its own Repeat Stage unclicked.
        Returns "REPEAT" / False / "RECONNECTED".
        """
        self._phase("BOSS", "#c62828")
        print("[BossRush] All gates cleared - fighting the boss.")

        # Auto Start gets longer here than at a gate: the trip to the boss is a real teleport,
        # and units placed before it lands would go down in the hub.
        result = self._wait_for_start_game(timeout=60.0, auto_start_timeout=8.0)
        if result is not True:
            return result
        result = self._place_units(config.BOSSRUSH_BOSS_VARIANT, self.boss_preset_name)
        if result is not True:
            return result

        result = self._click_start_game()
        if result is not True:
            return result
        SESSION.match_started()

        self._phase("FIGHTING BOSS", "#c62828")
        result = poll_until(
            [target(config.VICTORY_TEXT, "VICTORY", debug_label="bossrush_victory"),
             target(config.DEFEAT_TEXT, "DEFEAT", debug_label="bossrush_defeat")],
            interval=2.0, label="bossrush_boss_result", stuck_timeout=config.BOSSRUSH_STUCK_TIMEOUT)
        if result in (False, "RECONNECTED"):
            return result

        if result == "DEFEAT":
            self.consecutive_defeats += 1
            SESSION.defeat()
            print(f"[BossRush] Boss defeat ({self.consecutive_defeats} in a row).")
            if config.defeat_limit_reached(self.consecutive_defeats):
                health.save_debug_screenshot("bossrush_too_many_defeats")
                print(f"[BossRush] {self.consecutive_defeats} defeats in a row - stopping; the "
                      f"boss macro can't win this fight.")
                return _halt("TOO MANY BOSS DEFEATS")
        else:
            self.consecutive_defeats = 0
            SESSION.victory()
            print("[BossRush] Boss defeated.")

        result = poll_until([target(config.REPEAT_STAGE_BTN, True, debug_label="bossrush_repeat_wait")],
                            interval=2.0, label="bossrush_repeat_wait", stuck_timeout=config.STUCK_TIMEOUT_MENU)
        if result is not True:
            return result
        return "REPEAT"

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
        if not click_until_gone(config.REPEAT_STAGE_BTN, match, "bossrush_repeat", clicks=2):
            return False
        time.sleep(1.0)
        return self._enter_hub()

    # --- a full cycle ----------------------------------------------------------------
    def play_cycle(self):
        """One full loop: 6 gates then the boss. Returns "REPEAT" / False / "RECONNECTED"."""
        for gate_number in range(1, config.BOSSRUSH_TOTAL_GATES + 1):
            if config.STOP_REQUESTED:
                return False
            result = self.clear_gate(gate_number)
            if result is not True:
                return result

        return self.fight_boss()
