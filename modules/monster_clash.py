# modules/monster_clash.py
"""
Monster Clash (a Battle Event): lobby navigation, then Auto Play matches on one stage,
plus the helicopter bonus stage that can follow any of them.

The flow, as described by the player (2026-10-07):

  Lobby:  Events -> Monster Clash card -> Play Event -> Play - Choose Stage ->
          Select Stage -> Start -> lands on the stage.

  Stage:  Auto Play on, then the ordinary Start Game. Nothing else - the game's own
          Auto Play fights the match.

  Heli:   when a match ends, a helicopter can land at one fixed spot on the map. E at
          it takes the character to a slightly different stage on the same map (Auto
          Play + Start Game again, same as always), and finishing that one sends it
          back to the normal stage.

The helicopter is handled the way Portals handles its fishing spot: rather than
watching for the helicopter and steering to it, the character WALKS to the landing
spot before every fresh Start Game (a walk-only recording, played after the camera is
anchored) and simply stands there through the match.

Detecting it relies on the game's Auto Retry (the player runs with it on): normally
the next round's Start Game is back within MONSTER_CLASH_RETRY_GRACE (3s) of a match
ending. When it isn't, Auto Retry is holding for the helicopter, so E is pressed (at
the prompt if there's a crop of it - config.MONSTER_CLASH_HELI_PROMPT - blind
otherwise) until the bonus stage's Start Game shows. Repeat Stage is still clicked
if it ever shows (Auto Retry off).

Where the character stands is tracked so the walk is only replayed when needed:
  - straight in from the lobby: at spawn, walk.
  - Auto Retry / Repeat Stage with no helicopter: still on the spot, no walk.
  - the bonus stage: no walk at all.
  - back from the bonus stage, or unknown: Settings -> Teleport to Spawn first, then
    the walk, so it starts from the spot it was recorded at.
"""
import os
import time

import pydirectinput

import config
from vision import capture_screen, find_template
from input_controller import anchor_camera, click_at, mark_input
from modules.polling import poll_until, target, click_until_gone
from modules.gamemode_select import _wait_and_click, _run_steps
from modules.stage_player import play_preset, wait_for_start_game, click_start_game
from modules.autoplay import ensure_autoplay_enabled
from modules.reconnect import handle_disconnect_if_present
from modules.bossrush import BossRushRunner
from modules.stats import SESSION
from modules import health

# Where the character is on the normal stage.
AT_SPAWN = "spawn"        # just came in from the lobby
AT_SPOT = "spot"          # standing on the helicopter's landing spot
UNKNOWN = "unknown"       # anything else - teleport to spawn before walking


def _find(template, screenshot=None, debug_label=None):
    shot = screenshot if screenshot is not None else capture_screen()
    return find_template(shot, template, config.MATCH_THRESHOLD, debug_label=debug_label)


def _halt(reason):
    """Stops the run as a fault (not a user stop), unless the user already stopped it."""
    if not config.STOP_REQUESTED:
        config.STUCK_DETECTED = reason
    config.STOP_REQUESTED = True
    return False


def has_heli_prompt_crop():
    return os.path.exists(config.MONSTER_CLASH_HELI_PROMPT)


# --- Lobby navigation ----------------------------------------------------------------

def _next_is_up(shot, template, next_template):
    """
    True if the NEXT menu step's button is on screen. A button can stay on screen after
    it worked - Play Event does (live 2026-10-07): Play - Choose Stage appears above it
    and Play Event never goes away, so both are up at once in different places.

    The green buttons also look alike (Select Stage scores up to 0.73 on Choose Stage's
    crop), so when both crops land on the SAME spot it's one button matching both, and
    the next one only counts if it's the better match there.
    """
    if not next_template:
        return False
    nxt = _find(next_template, shot)
    if not nxt:
        return False
    own = _find(template, shot)
    if own is None:
        return True
    same_spot = abs(nxt[0] - own[0]) <= 40 and abs(nxt[1] - own[1]) <= 25
    return not same_spot or nxt[2] >= own[2]


def _click_through(template, step_name, next_template=None):
    """
    Waits for a menu button and clicks it until the screen has moved on: either the
    button is gone, or next_template (the following step's button) has appeared. If
    that next button is already up, this step was already done and nothing is clicked.
    True / False / "RECONNECTED".
    """
    deadline = time.time() + config.TIMEOUT_SECONDS
    match = None
    while time.time() < deadline:
        if config.STOP_REQUESTED:
            return False
        shot = capture_screen()
        if handle_disconnect_if_present(shot):
            return "RECONNECTED"
        if _next_is_up(shot, template, next_template):
            print(f"[{step_name}] The next button is already up - this step is done.")
            return True
        match = _find(template, shot, debug_label=step_name)
        if match:
            break
        time.sleep(config.POLL_INTERVAL)
    if not match:
        print(f"[{step_name}] TIMEOUT: template not found.")
        return False

    for attempt in range(1, 4):
        if config.STOP_REQUESTED:
            return False
        time.sleep(0.4)                  # let the button finish popping in
        shot = capture_screen()
        if _next_is_up(shot, template, next_template):
            break
        match = _find(template, shot) or match
        print(f"[MonsterClash] {step_name}: clicking at ({match[0]}, {match[1]}), confidence={match[2]:.2f}"
              f"{f' (attempt {attempt}/3)' if attempt > 1 else ''}.")
        click_at(match[0], match[1])
        # Up to ~3s for the click to show: the button gone, or the next one up.
        moved_on = False
        for _ in range(12):
            time.sleep(0.25)
            shot = capture_screen()
            if _next_is_up(shot, template, next_template) or not _find(template, shot):
                moved_on = True
                break
        if moved_on:
            break
        print(f"[{step_name}] Nothing changed after the click.")
    else:
        path = health.save_debug_screenshot(f"monster_clash_{step_name}")
        print(f"[{step_name}] Still on the same screen after 3 clicks (screen saved: {path}).")
        return False
    time.sleep(0.6)
    return True


def click_events():
    return _wait_and_click(config.MONSTER_CLASH_EVENTS_BTN, "click_events")


def click_monster_clash_card():
    return _click_through(config.MONSTER_CLASH_CARD, "click_monster_clash_card",
                          next_template=config.MONSTER_CLASH_PLAY_EVENT_BTN)


def click_play_event():
    return _click_through(config.MONSTER_CLASH_PLAY_EVENT_BTN, "click_play_event",
                          next_template=config.MONSTER_CLASH_CHOOSE_STAGE_BTN)


def click_choose_stage():
    return _click_through(config.MONSTER_CLASH_CHOOSE_STAGE_BTN, "click_choose_stage",
                          next_template=config.MONSTER_CLASH_SELECT_STAGE_BTN)


def click_select_stage():
    return _click_through(config.MONSTER_CLASH_SELECT_STAGE_BTN, "click_monster_clash_select_stage",
                          next_template=config.MONSTER_CLASH_START_BTN)


def click_start():
    """The party screen's Start, before the stage loads."""
    return _click_through(config.MONSTER_CLASH_START_BTN, "click_monster_clash_start")


def run_monster_clash_menu_flow():
    """Lobby -> ... -> Start. True / False / "RECONNECTED"."""
    return _run_steps([
        click_events,
        click_monster_clash_card,
        click_play_event,
        click_choose_stage,
        click_select_stage,
        click_start,
    ])


# --- The run ---------------------------------------------------------------------------

class MonsterClashRunner:
    def __init__(self, walk_preset_name, set_phase=None):
        self.walk_preset_name = walk_preset_name
        self.set_phase = set_phase
        self.consecutive_defeats = 0
        self.position = AT_SPAWN
        # True while the stage being played is the helicopter's bonus stage.
        self.in_bonus = False
        # True when Auto Retry's next Start Game ended the last match (it's already up).
        self._retried = False
        # Borrowed for its Settings -> Teleport to Spawn routine (the same menu on every map).
        self._spawn = BossRushRunner(None, None, {}, set_phase=set_phase)

    def _phase(self, text, color="#4fc3f7"):
        if self.set_phase:
            self.set_phase(text, color)

    # --- navigation ------------------------------------------------------------------
    def navigate(self):
        """
        Gets onto the stage from wherever the game is: the lobby (full menu flow), the
        party screen (just Start), or already on the stage / its results screen (nothing
        to do - the next step picks it up). True / False / "RECONNECTED".
        """
        shot = capture_screen()
        repeat = _find(config.REPEAT_STAGE_BTN, shot)
        if repeat:
            print("[MonsterClash] On a results screen - clicking Repeat Stage.")
            self.position = UNKNOWN
            self.in_bonus = False
            time.sleep(1.0)
            repeat = _find(config.REPEAT_STAGE_BTN) or repeat
            click_until_gone(config.REPEAT_STAGE_BTN, repeat, "monster_clash_repeat", clicks=1)
            return True
        if _find(config.START_GAME_BTN, shot):
            print("[MonsterClash] Already on the stage - skipping the menus.")
            self.position = UNKNOWN
            return True
        if _find(config.MONSTER_CLASH_START_BTN, shot):
            print("[MonsterClash] Already on the party screen - starting from there.")
            self.position = AT_SPAWN
            self.in_bonus = False
            return click_start()

        from modules.lobby import at_lobby
        if not at_lobby(shot) and (_find(config.AUTOPLAY_ON_BTN, shot) or _find(config.AUTOPLAY_OFF_BTN, shot)):
            # The stage's side panel is up: a match is already running (restart mid-match).
            print("[MonsterClash] A match looks to be under way already - picking it up from here.")
            self.position = UNKNOWN
            return "IN_MATCH"

        self._phase("NAVIGATING MENUS")
        print("[MonsterClash] Navigating to Monster Clash...")
        result = run_monster_clash_menu_flow()
        if result is True:
            self.position = AT_SPAWN
            self.in_bonus = False
        return result

    # --- before Start Game -----------------------------------------------------------
    def _walk_to_spot(self):
        """
        Anchors the camera and plays the walk to the helicopter's landing spot, teleporting
        to spawn first unless the character is known to be there. Never fails the run: a
        walk that can't happen only costs this match's helicopter. True / False /
        "RECONNECTED".
        """
        if self.position == UNKNOWN:
            print("[MonsterClash] Not sure where the character is - teleporting to spawn before the walk.")
            result = self._spawn._reset_to_spawn(halt=False)
            if result in (False, "RECONNECTED"):
                return result
            if result is None:
                print("[MonsterClash] Couldn't teleport to spawn - skipping the walk this match "
                      "(Auto Play still plays it; a helicopter would be missed).")
                return True
            # _reset_to_spawn re-anchors the camera itself.
        else:
            self._phase("ANCHORING CAMERA")
            anchor_camera()
            time.sleep(0.5)

        self._phase("WALKING TO HELI SPOT")
        print(f"[MonsterClash] Walking to the helicopter's landing spot (macro '{self.walk_preset_name}')...")
        result = play_preset(config.MONSTER_CLASH_PRESET_LOCATION, config.MONSTER_CLASH_WALK_VARIANT,
                             self.walk_preset_name, movement_only=True)
        if result == "RECONNECTED" or (result is False and config.STOP_REQUESTED):
            return result
        if result is not True:
            print(f"[MonsterClash] The helicopter walk '{self.walk_preset_name}' is missing or empty - "
                  f"record it first (F8 in-game, walk only, from the stage's spawn).")
            return _halt("MONSTER CLASH HELI WALK MISSING")
        self.position = AT_SPOT
        return True

    def prepare_and_start(self):
        """
        Stage loaded -> (walk to the spot) -> Auto Play on -> Start Game.
        True / False / "RECONNECTED".
        """
        self._phase("WAITING FOR START GAME")
        result = wait_for_start_game(resumed=(self.position == AT_SPOT and not self.in_bonus))
        if result in (False, "RECONNECTED"):
            return result

        if not self.in_bonus and self.position != AT_SPOT and self.walk_preset_name:
            result = self._walk_to_spot()
            if result is not True:
                return result
        elif self.in_bonus:
            print("[MonsterClash] Bonus stage - no walk needed here.")

        time.sleep(0.4)
        self._phase("CHECKING AUTO PLAY", "#2e7d32")
        if not ensure_autoplay_enabled():
            print("[MonsterClash] WARNING: couldn't find the Auto Play button - continuing anyway.")
        self._phase("STARTING MATCH", "#2e7d32")
        if not click_start_game():
            print("[MonsterClash] Start Game wasn't there to click - the match is probably already running.")
        SESSION.match_started()
        return True

    # --- the match -------------------------------------------------------------------
    def play_match(self):
        """
        Waits for the match to end and counts it. "ENDED" / False / "RECONNECTED".

        With the game's Auto Retry on, the result popup only shows for a moment before the
        next round's Start Game comes up, so that Start Game counts as "the match ended"
        too (from MONSTER_CLASH_MIN_MATCH seconds in - before that it's this round's own
        button still fading out). Counted as a win, the same as Boss Rush's auto-retry.
        """
        self._phase("BONUS STAGE" if self.in_bonus else "WAITING FOR MATCH END", "#4fc3f7")
        self._retried = False
        started = time.time()

        def _auto_retried(shot):
            if time.time() - started < config.MONSTER_CLASH_MIN_MATCH:
                return None
            if _find(config.START_GAME_BTN, shot, debug_label="monster_clash_auto_retried"):
                return "RETRIED"
            return None

        print("[MonsterClash] Waiting for the match to end...")
        result = poll_until([target(config.VICTORY_TEXT, "VICTORY", debug_label="victory"),
                             target(config.DEFEAT_TEXT, "DEFEAT", debug_label="defeat")],
                            interval=1.0, label="monster_clash_match_result",
                            stuck_timeout=config.match_stuck_timeout(), custom_check=_auto_retried,
                            lobby_grace=8.0)
        if result in (False, "RECONNECTED"):
            return result

        stage = "Bonus stage" if self.in_bonus else "Match"
        if result == "DEFEAT":
            self.consecutive_defeats += 1
            SESSION.defeat()
            print(f"[MonsterClash] {stage} lost ({self.consecutive_defeats} in a row).")
            if config.defeat_limit_reached(self.consecutive_defeats):
                health.save_debug_screenshot("monster_clash_too_many_defeats")
                return _halt("TOO MANY DEFEATS (MONSTER CLASH)")
        else:
            self.consecutive_defeats = 0
            SESSION.victory()
            if result == "RETRIED":
                self._retried = True
                print(f"[MonsterClash] {stage} over - Auto Retry already brought up the next Start Game "
                      f"(result screen not seen, counted as a win).")
            else:
                print(f"[MonsterClash] {stage} won.")
        return "ENDED"

    # --- after the match: the helicopter -------------------------------------------
    def _press_e(self):
        pydirectinput.press("e")
        mark_input()

    def _take_heli(self):
        """
        Start Game didn't come back on its own: Auto Retry is holding for the helicopter.
        Presses E (at the prompt if there's a crop of it, blind every
        MONSTER_CLASH_BLIND_E_INTERVAL otherwise - it has to fly in and land first) until
        the next Start Game shows, which is the bonus stage loaded.
        "READY" / "REPEAT" (no helicopter after all - the results screen's Repeat Stage is
        up) / "TIMEOUT" / False / "RECONNECTED".
        """
        with_crop = has_heli_prompt_crop()
        self._phase("TAKING HELI")
        print(f"[MonsterClash] No Start Game {config.MONSTER_CLASH_RETRY_GRACE:.0f}s after the match - "
              f"the helicopter must be here. Pressing E "
              f"{'at its prompt' if with_crop else 'until the bonus stage loads'}...")
        deadline = time.time() + config.MONSTER_CLASH_HELI_WAIT
        last_e = 0.0
        presses = 0
        while time.time() < deadline:
            if config.STOP_REQUESTED:
                return False
            shot = capture_screen()
            if handle_disconnect_if_present(shot):
                return "RECONNECTED"
            if _find(config.START_GAME_BTN, shot, debug_label="monster_clash_bonus_start_game"):
                print(f"[MonsterClash] Start Game is up after {presses} E press(es) - "
                      f"{'bonus stage' if presses else 'next stage'} loaded.")
                return "READY" if presses else "LATE_RETRY"
            if _find(config.REPEAT_STAGE_BTN, shot):
                return "REPEAT"
            prompt = _find(config.MONSTER_CLASH_HELI_PROMPT, shot, debug_label="heli_prompt") if with_crop else None
            if (prompt or not with_crop) and time.time() - last_e >= config.MONSTER_CLASH_BLIND_E_INTERVAL:
                if prompt:
                    print(f"[MonsterClash] Helicopter prompt at ({prompt[0]}, {prompt[1]}), "
                          f"confidence={prompt[2]:.2f} - pressing E.")
                self._press_e()
                presses += 1
                last_e = time.time()
            time.sleep(0.4)
        path = health.save_debug_screenshot("monster_clash_no_start_game_after_heli")
        print(f"[MonsterClash] Still no Start Game {config.MONSTER_CLASH_HELI_WAIT:.0f}s into the helicopter "
              f"wait (screen saved: {path}).")
        return "TIMEOUT"

    def _click_repeat(self):
        """ONE click on Repeat Stage once it's settled (a double click can hit View Party)."""
        time.sleep(1.0)
        match = _find(config.REPEAT_STAGE_BTN)
        if not match:
            return True
        print("[MonsterClash] Clicking Repeat Stage.")
        if not click_until_gone(config.REPEAT_STAGE_BTN, match, "monster_clash_repeat", clicks=1):
            path = health.save_debug_screenshot("monster_clash_repeat_stuck")
            print(f"[MonsterClash] Repeat Stage is still on screen after clicking (screen saved: {path}).")
            return False
        return True

    def continue_after_match(self):
        """
        On to the next stage. With Auto Retry on, the next Start Game normally shows within
        MONSTER_CLASH_RETRY_GRACE seconds of the match ending; when it doesn't, Auto Retry
        is waiting on the helicopter, so E is pressed until the bonus stage loads. Finishing
        the bonus stage brings the normal one back by itself. Repeat Stage (Auto Retry off)
        is clicked when it shows. True / False / "RECONNECTED".
        """
        was_bonus = self.in_bonus
        self.in_bonus = False
        if was_bonus:
            # Back on the normal stage, wherever the game put the character.
            print("[MonsterClash] Bonus stage done - back to the normal stage.")
            self.position = UNKNOWN

        if self._retried:
            return True                  # the next Start Game is already up

        self._phase("NEXT STAGE")
        result = poll_until([target(config.START_GAME_BTN, "READY", debug_label="monster_clash_next_start_game"),
                             target(config.REPEAT_STAGE_BTN, "REPEAT", debug_label="monster_clash_repeat")],
                            interval=0.5, label="monster_clash_auto_retry",
                            timeout=config.MONSTER_CLASH_RETRY_GRACE, stuck_timeout=None)
        if result in (False, "RECONNECTED"):
            return result
        if result == "READY":
            return True

        if result == "TIMEOUT" and not was_bonus:
            result = self._take_heli()
            if result in (False, "RECONNECTED"):
                return result
            if result == "READY":
                self.in_bonus = True
                return True
            if result == "LATE_RETRY":
                return True              # Auto Retry was just slow - no helicopter, no E pressed

        if result == "TIMEOUT":
            # After the bonus stage, or still nothing after the helicopter wait: give the
            # next stage the ordinary menu-length wait before calling it stuck.
            self.position = UNKNOWN
            result = poll_until([target(config.START_GAME_BTN, "READY", debug_label="monster_clash_next_start_game"),
                                 target(config.REPEAT_STAGE_BTN, "REPEAT", debug_label="monster_clash_repeat")],
                                interval=1.0, label="monster_clash_next_stage",
                                stuck_timeout=config.STUCK_TIMEOUT_MENU, lobby_grace=25.0)
            if result in (False, "RECONNECTED"):
                return result
            if result == "READY":
                return True

        # Repeat Stage: Auto Retry is off. Stage repeats in place, so the character is
        # still on the spot.
        return self._click_repeat()
