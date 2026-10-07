# modules/monster_clash.py
"""
Monster Clash (a Battle Event): lobby navigation, then Auto Play matches on one stage -
in one of two modes (config.MONSTER_CLASH_MODES), as described by the player
(2026-10-07):

  Joining: Events -> Monster Clash card -> Play Event -> Play - Choose Stage ->
           Select Stage -> Start -> on the stage. From inside a stage the same menus
           open from the small Events icon in the bottom bar instead.

  "farm":  the game's own Auto Start and Auto Retry are ON. The stage repeats by itself
           forever, so the bot just keeps the run alive: counts results, clicks Start
           Game / Repeat Stage if one ever shows, anti-AFK clicks (poll_until's own),
           reconnects and rejoins after a disconnect or a kick to the lobby.

  "rift":  Auto Start and Auto Retry OFF - the helicopter (the "Monster's Rift") can only
           spawn then. Before each fresh Start Game the character walks to the landing
           spot (a walk-only recording, played after the camera is anchored). When a
           match ends:
             - "Start Rift" prompt (rare): E, then the rift's Start Game as usual.
             - otherwise (most of the time): Repeat Stage, then Start Game again - the
               character is still standing on the spot.
           The rift ends on a victory panel: its X (close_gui), then the small Events
           icon, which joins the normal stage again through the same menus.
           The "Game Results" button sits right under the Start Rift prompt, so the
           generic Game Results recovery click is OFF for every wait here.

Where the character stands (rift mode) is tracked so the walk is only replayed when
needed; when it's unknown (a restart mid-stage), Settings -> Teleport to Spawn comes
first so the walk starts from the spot it was recorded at.
"""
import time

import pydirectinput

import config
from vision import capture_screen, find_template
from input_controller import anchor_camera, click_at, mark_input, ensure_roblox_focus
from modules.polling import poll_until, target, click_until_gone
from modules.gamemode_select import _run_steps
from modules.stage_player import play_preset, wait_for_start_game, click_start_game
from modules.autoplay import ensure_autoplay_enabled
from modules.reconnect import handle_disconnect_if_present
from modules.bossrush import BossRushRunner
from modules.stats import SESSION
from modules import health

FARM = "farm"
RIFT = "rift"

# Whether the run is inside the rift, kept at module level so it outlives the runner: a
# Never Stop restart builds a new MonsterClashRunner, and one that landed on the rift's
# victory panel would otherwise take its Repeat Stage for an ordinary match's and click it.
_RIFT_STATE = {"in_rift": False, "at": 0.0}
_RIFT_STATE_WINDOW = 1200.0      # a rift longer ago than this is over, whatever happened


def reset_rift_state():
    """A fresh run (the Start button) starts outside the rift; only Never Stop restarts keep it."""
    _RIFT_STATE["in_rift"] = False
    _RIFT_STATE["at"] = 0.0

# Where the character is on the normal stage (rift mode).
AT_SPAWN = "spawn"        # just joined
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


# --- Joining -----------------------------------------------------------------------------

def _next_is_up(shot, template, next_templates):
    """
    True if one of the NEXT menu step's buttons is on screen. A button can stay on screen
    after it worked - Play Event does (live 2026-10-07): Play - Choose Stage appears above
    it and Play Event never goes away, so both are up at once in different places.

    The green buttons also look alike (Select Stage scores up to 0.73 on Choose Stage's
    crop), so when both crops land on the SAME spot it's one button matching both, and
    the next one only counts if it's the better match there.
    """
    for nxt_template in next_templates or ():
        nxt = _find(nxt_template, shot)
        if not nxt:
            continue
        own = _find(template, shot)
        if own is None:
            return True
        same_spot = abs(nxt[0] - own[0]) <= 40 and abs(nxt[1] - own[1]) <= 25
        if not same_spot or nxt[2] >= own[2]:
            return True
    return False


def _click_through(template, step_name, next_templates=None):
    """
    Waits for a menu button and clicks it until the screen has moved on: either the
    button is gone, or one of next_templates (the following step's buttons) has
    appeared. If a next button is already up, this step was already done and nothing is
    clicked. True / False / "RECONNECTED".
    """
    deadline = time.time() + config.TIMEOUT_SECONDS
    match = None
    while time.time() < deadline:
        if config.STOP_REQUESTED:
            return False
        shot = capture_screen()
        if handle_disconnect_if_present(shot):
            return "RECONNECTED"
        if _next_is_up(shot, template, next_templates):
            print(f"[{step_name}] The next button is already up - this step is done.")
            return True
        match = _find(template, shot, debug_label=step_name)
        if match:
            break
        time.sleep(config.POLL_INTERVAL)
    if not match:
        path = health.save_debug_screenshot(f"monster_clash_{step_name}_not_found")
        print(f"[{step_name}] TIMEOUT: template not found (screen saved: {path}).")
        return False

    for attempt in range(1, 4):
        if config.STOP_REQUESTED:
            return False
        time.sleep(0.4)                  # let the button finish popping in
        shot = capture_screen()
        if _next_is_up(shot, template, next_templates):
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
            if _next_is_up(shot, template, next_templates) or not _find(template, shot):
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


# The card can already be selected when the events panel opens (the game remembers it),
# and a selected card scores lower (0.66 measured) - Play Event already being up counts.
_AFTER_EVENTS = (config.MONSTER_CLASH_CARD, config.MONSTER_CLASH_PLAY_EVENT_BTN)


def click_events():
    return _click_through(config.MONSTER_CLASH_EVENTS_BTN, "click_events", _AFTER_EVENTS)


def click_events_small():
    return _click_through(config.MONSTER_CLASH_EVENTS_SMALL_BTN, "click_events_small", _AFTER_EVENTS)


def click_monster_clash_card():
    return _click_through(config.MONSTER_CLASH_CARD, "click_monster_clash_card",
                          (config.MONSTER_CLASH_PLAY_EVENT_BTN,))


def click_play_event():
    return _click_through(config.MONSTER_CLASH_PLAY_EVENT_BTN, "click_play_event",
                          (config.MONSTER_CLASH_CHOOSE_STAGE_BTN,))


def click_choose_stage():
    return _click_through(config.MONSTER_CLASH_CHOOSE_STAGE_BTN, "click_choose_stage",
                          (config.MONSTER_CLASH_SELECT_STAGE_BTN,))


def click_select_stage():
    return _click_through(config.MONSTER_CLASH_SELECT_STAGE_BTN, "click_monster_clash_select_stage",
                          (config.MONSTER_CLASH_START_BTN,))


def click_start():
    """The party screen's Start, before the stage loads."""
    return _click_through(config.MONSTER_CLASH_START_BTN, "click_monster_clash_start")


def run_join_flow(from_stage=False):
    """
    Events (the lobby's button, or the small in-game icon when from_stage) -> ... -> Start.
    True / False / "RECONNECTED".
    """
    return _run_steps([
        click_events_small if from_stage else click_events,
        click_monster_clash_card,
        click_play_event,
        click_choose_stage,
        click_select_stage,
        click_start,
    ])


# --- The run ---------------------------------------------------------------------------

class MonsterClashRunner:
    def __init__(self, mode, walk_preset_name, set_phase=None):
        self.mode = mode
        self.walk_preset_name = walk_preset_name
        self.set_phase = set_phase
        self.consecutive_defeats = 0
        self.position = AT_SPAWN
        # True while the stage being played is the rift (E was pressed at Start Rift).
        self._in_rift = bool(_RIFT_STATE["in_rift"]) and time.time() - _RIFT_STATE["at"] < _RIFT_STATE_WINDOW
        if self.in_rift:
            print("[MonsterClash] Picking up inside the rift (the run restarted during it).")
        self._match_started_at = 0.0
        self._warned_auto_retry = False
        # Borrowed for its Settings -> Teleport to Spawn routine (the same menu on every map).
        self._spawn = BossRushRunner(None, None, {}, set_phase=set_phase)

    def _phase(self, text, color="#4fc3f7"):
        if self.set_phase:
            self.set_phase(text, color)

    @property
    def in_rift(self):
        return self._in_rift

    @in_rift.setter
    def in_rift(self, value):
        self._in_rift = bool(value)
        _RIFT_STATE["in_rift"] = self._in_rift
        _RIFT_STATE["at"] = time.time()

    # --- getting onto the stage ---------------------------------------------------------
    def _join(self, from_stage):
        self._phase("JOINING MONSTER CLASH")
        print(f"[MonsterClash] Joining Monster Clash ({'small Events icon' if from_stage else 'lobby Events'})...")
        result = run_join_flow(from_stage)
        if result is True:
            self.position = AT_SPAWN
            self.in_rift = False
        return result

    def navigate(self):
        """
        Gets onto the stage from wherever the game is - every restart (Never Stop, Stop then
        Start, a reconnect) comes through here, so it reads the screen rather than trusting
        any remembered state. Returns:
          True          - on a stage, the next Start Game (or an already running match) is next
          "IN_MATCH"    - a match is already running
          "RIFT_PROMPT" - the Start Rift prompt is up (rift mode)
          "ENDED"       - a match's end screen is up (Repeat Stage)
          False / "RECONNECTED"
        """
        from modules.lobby import at_lobby
        shot = capture_screen()
        if handle_disconnect_if_present(shot):
            return "RECONNECTED"
        if at_lobby(shot):
            return self._join(from_stage=False)
        if _find(config.MONSTER_CLASH_START_BTN, shot):
            print("[MonsterClash] On the party screen - starting from there.")
            self.position = AT_SPAWN
            self.in_rift = False
            return click_start()
        if _find(config.MONSTER_CLASH_PLAY_EVENT_BTN, shot) or _find(config.MONSTER_CLASH_CARD, shot):
            print("[MonsterClash] The events menu is open - joining from there.")
            result = _run_steps([click_monster_clash_card, click_play_event, click_choose_stage,
                                 click_select_stage, click_start])
            if result is True:
                self.position = AT_SPAWN
                self.in_rift = False
            return result
        if self.mode == RIFT and _find(config.MONSTER_CLASH_START_RIFT, shot):
            print("[MonsterClash] The Start Rift prompt is up.")
            self.position = UNKNOWN
            return "RIFT_PROMPT"
        if _find(config.REPEAT_STAGE_BTN, shot):
            print("[MonsterClash] A match's end screen is up.")
            self.position = UNKNOWN
            return "ENDED"
        close = _find(config.MONSTER_CLASH_CLOSE_GUI_BTN, shot)
        if close and self.mode == RIFT and self.in_rift:
            print("[MonsterClash] The rift's victory panel is up - closing it and rejoining.")
            return self._leave_rift()
        if close and not getattr(self, "_closed_stray_panel", False):
            # Some other panel (a game popup, a Settings panel left open): close it and look again.
            print("[MonsterClash] A panel with an X is in the way - closing it and looking again.")
            self._closed_stray_panel = True
            click_until_gone(config.MONSTER_CLASH_CLOSE_GUI_BTN, close, "monster_clash_close_panel")
            time.sleep(1.0)
            try:
                return self.navigate()
            finally:
                self._closed_stray_panel = False
        if _find(config.START_GAME_BTN, shot):
            print("[MonsterClash] On a stage, Start Game up.")
            self.position = UNKNOWN
            return True
        if _find(config.AUTOPLAY_ON_BTN, shot) or _find(config.AUTOPLAY_OFF_BTN, shot):
            print("[MonsterClash] A match looks to be under way already - picking it up from here.")
            self.position = UNKNOWN
            self._match_started_at = time.time()
            return "IN_MATCH"
        if _find(config.MONSTER_CLASH_EVENTS_SMALL_BTN, shot):
            return self._join(from_stage=True)
        path = health.save_debug_screenshot("monster_clash_unknown_screen", shot)
        print(f"[MonsterClash] Don't recognise this screen (saved: {path}) - trying the lobby's Events.")
        return self._join(from_stage=False)

    # --- before Start Game ---------------------------------------------------------------
    def _walk_to_spot(self):
        """
        Anchors the camera and plays the walk to the helicopter's landing spot, teleporting
        to spawn first unless the character is known to be there. A walk that can't happen
        only costs this match's rift - the run goes on. True / False / "RECONNECTED".
        """
        if self.position == UNKNOWN:
            print("[MonsterClash] Not sure where the character is - teleporting to spawn before the walk.")
            result = self._spawn._reset_to_spawn(halt=False)
            if result in (False, "RECONNECTED"):
                return result
            if result is None:
                print("[MonsterClash] Couldn't teleport to spawn - skipping the walk this match.")
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

    def _wait_for_start_game(self):
        """
        Waits for Start Game. Rift mode has its own wait with the Game Results click OFF (it
        sits right under the Start Rift prompt), and calls out Auto Start being on, which
        stops the rift from spawning. True / "ALREADY_STARTED" / False / "RECONNECTED".
        """
        if self.mode == FARM:
            return wait_for_start_game()
        result = poll_until([target(config.START_GAME_BTN, True, debug_label="start_game_btn")],
                            interval=1.0, label="monster_clash_start_game", timeout=90.0,
                            stuck_timeout=None, lobby_grace=25.0, game_results=False)
        if result == "TIMEOUT":
            if _find(config.AUTOPLAY_ON_BTN) or _find(config.AUTOPLAY_OFF_BTN):
                print("[MonsterClash] No Start Game but the stage is up - the game's Auto Start looks ON. "
                      "Rift hunt needs Auto Start and Auto Retry OFF (the rift can't spawn otherwise).")
                self._warn_auto_retry()
                return "ALREADY_STARTED"
            path = health.save_debug_screenshot("monster_clash_no_start_game")
            print(f"[MonsterClash] Start Game never appeared (screen saved: {path}).")
            return _halt("MONSTER CLASH START GAME NEVER APPEARED")
        return result

    def _warn_auto_retry(self):
        if self._warned_auto_retry:
            return
        self._warned_auto_retry = True
        try:
            from modules import notify
            notify.problem("Monster Clash: turn Auto Retry off",
                           "Rift hunt is running with the game's Auto Start / Auto Retry ON - the rift can't "
                           "spawn like that. Turn both off in the game's settings, or switch to Farm mode.",
                           image_bytes=health.jpg_bytes())
        except Exception as e:
            print(f"[notify] Could not send the Auto Retry message: {e}")

    def prepare_and_start(self):
        """
        Stage loaded -> (rift mode: walk to the spot) -> Auto Play on -> Start Game.
        True / False / "RECONNECTED".
        """
        self._phase("WAITING FOR START GAME")
        result = self._wait_for_start_game()
        if result in (False, "RECONNECTED"):
            return result
        already_running = result == "ALREADY_STARTED"

        if already_running:
            print("[MonsterClash] The match is already running - no walk this time (it would wander off "
                  "mid-fight).")
        elif self.mode == RIFT and not self.in_rift and self.position != AT_SPOT:
            result = self._walk_to_spot()
            if result is not True:
                return result
        elif self.in_rift:
            print("[MonsterClash] In the rift - no walk needed here.")

        time.sleep(0.4)
        self._phase("CHECKING AUTO PLAY", "#2e7d32")
        if not ensure_autoplay_enabled():
            print("[MonsterClash] WARNING: couldn't find the Auto Play button - continuing anyway.")
        self._phase("STARTING MATCH", "#2e7d32")
        if not click_start_game():
            print("[MonsterClash] Start Game wasn't there to click - the match is probably already running.")
        SESSION.match_started()
        self._match_started_at = time.time()
        return True

    # --- the match ------------------------------------------------------------------------
    def _count(self, result):
        """Counts a match result. False if the defeat limit was hit (run halted)."""
        where = "Rift" if self.in_rift else "Match"
        if result == "DEFEAT":
            self.consecutive_defeats += 1
            SESSION.defeat()
            print(f"[MonsterClash] {where} lost ({self.consecutive_defeats} in a row).")
            if config.defeat_limit_reached(self.consecutive_defeats):
                health.save_debug_screenshot("monster_clash_too_many_defeats")
                return _halt("TOO MANY DEFEATS (MONSTER CLASH)")
        else:
            self.consecutive_defeats = 0
            SESSION.victory()
            print(f"[MonsterClash] {where} won.")
        return True

    def _read_result(self):
        """After an end screen was seen without its banner: Defeat if it shows within ~3s."""
        for _ in range(3):
            shot = capture_screen()
            if _find(config.DEFEAT_TEXT, shot):
                return "DEFEAT"
            if _find(config.VICTORY_TEXT, shot):
                return "VICTORY"
            time.sleep(1.0)
        return "VICTORY"

    def play_match(self):
        """
        Waits for the match to end and counts it. "ENDED" / False / "RECONNECTED".

        Rift mode: any end-of-match screen counts - the banner, Repeat Stage, Start Rift, or
        (in the rift) its victory panel's X - since the banner animates and can be missed.
        Farm mode: the banner, or Auto Retry's next Start Game / a Repeat Stage.
        """
        self._phase("IN THE RIFT" if self.in_rift else "WAITING FOR MATCH END", "#4fc3f7")
        started = self._match_started_at or time.time()
        # Farm: whether the match was seen running (no Start Game on screen). A Start Game
        # that was never gone means nothing started it (the game's Auto Start is off) - it
        # gets clicked, not counted as a finished match.
        seen_running = [False]

        def _other_end(shot):
            if self.mode == FARM and not _find(config.START_GAME_BTN, shot):
                seen_running[0] = True
            if time.time() - started < config.MONSTER_CLASH_MIN_MATCH:
                return None
            if _find(config.REPEAT_STAGE_BTN, shot):
                return "SCREEN"
            if self.mode == RIFT:
                if _find(config.MONSTER_CLASH_START_RIFT, shot):
                    return "SCREEN"
                if self.in_rift and _find(config.MONSTER_CLASH_CLOSE_GUI_BTN, shot):
                    return "SCREEN"
            elif _find(config.START_GAME_BTN, shot):
                if seen_running[0]:
                    return "RETRIED"
                print("[MonsterClash] Start Game is still waiting to be pressed (Auto Start looks off) - pressing it.")
                click_start_game()
                return None
            return None

        print("[MonsterClash] Waiting for the match to end...")
        result = poll_until([target(config.VICTORY_TEXT, "VICTORY", debug_label="victory"),
                             target(config.DEFEAT_TEXT, "DEFEAT", debug_label="defeat")],
                            interval=1.0, label="monster_clash_match_result",
                            stuck_timeout=config.match_stuck_timeout(), custom_check=_other_end,
                            lobby_grace=8.0, game_results=False)
        if result in (False, "RECONNECTED"):
            return result
        if result == "SCREEN":
            result = self._read_result()
        elif result == "RETRIED":
            print("[MonsterClash] Auto Retry already brought up the next Start Game - counting a win.")
            result = "VICTORY"
        if not self._count(result):
            return False
        return "ENDED"

    # --- farm mode: between matches ------------------------------------------------------
    def farm_next(self):
        """
        Farm mode, after a match: the game repeats the stage itself. Whatever it still needs
        a click for (Start Game with Auto Start off, Repeat Stage with Auto Retry off) is
        clicked; otherwise this waits for the result banner to clear so the same win isn't
        counted twice. True / False / "RECONNECTED".
        """
        self._phase("FARMING")
        deadline = time.time() + 20.0
        while time.time() < deadline:
            if config.STOP_REQUESTED:
                return False
            shot = capture_screen()
            if handle_disconnect_if_present(shot):
                return "RECONNECTED"
            repeat = _find(config.REPEAT_STAGE_BTN, shot)
            if repeat:
                time.sleep(1.0)
                repeat = _find(config.REPEAT_STAGE_BTN) or repeat
                print("[MonsterClash] Repeat Stage is up (Auto Retry looks off) - clicking it.")
                click_until_gone(config.REPEAT_STAGE_BTN, repeat, "monster_clash_repeat", clicks=1)
                time.sleep(2.0)
                continue
            if _find(config.START_GAME_BTN, shot):
                print("[MonsterClash] Start Game is up (Auto Start looks off) - clicking it.")
                click_start_game()
                break
            if not (_find(config.VICTORY_TEXT, shot) or _find(config.DEFEAT_TEXT, shot)):
                break
            time.sleep(1.0)
        self._match_started_at = time.time()
        SESSION.match_started()
        return True

    # --- rift mode: between matches ------------------------------------------------------
    def _press_e(self):
        ensure_roblox_focus()
        pydirectinput.press("e")
        mark_input()

    def take_rift(self):
        """E at the Start Rift prompt until it's gone. True / None (wouldn't take) / False."""
        self._phase("STARTING RIFT", "#c62828")
        presses = 0
        for attempt in range(1, 6):
            if config.STOP_REQUESTED:
                return False
            prompt = _find(config.MONSTER_CLASH_START_RIFT)
            if not prompt:
                break
            print(f"[MonsterClash] Start Rift prompt at ({prompt[0]}, {prompt[1]}), confidence={prompt[2]:.2f} - "
                  f"pressing E{f' (attempt {attempt}/5)' if attempt > 1 else ''}.")
            self._press_e()
            presses += 1
            time.sleep(1.5)
        if not presses:
            print("[MonsterClash] The Start Rift prompt was gone before E could be pressed.")
            return None
        if _find(config.MONSTER_CLASH_START_RIFT):
            path = health.save_debug_screenshot("monster_clash_rift_wont_start")
            print(f"[MonsterClash] The Start Rift prompt is still up after 5 presses (screen saved: {path}).")
            return None
        print("[MonsterClash] Into the rift!")
        SESSION.rift()
        try:
            from modules import notify
            notify.send("The Monster's Rift spawned - the macro went in.", category="lifecycle",
                        title="Monster Clash: rift!", good=True, image_bytes=health.jpg_bytes())
        except Exception as e:
            print(f"[notify] Could not send the rift message: {e}")
        self.in_rift = True
        return True

    def _leave_rift(self):
        """
        The rift is over: its victory panel's X (if it's still up), then the small Events
        icon and the usual menus back onto the normal stage. True / False / "RECONNECTED".
        """
        self.in_rift = False
        self._phase("LEAVING RIFT")
        time.sleep(1.0)                  # the panel pops in
        for template in (config.MONSTER_CLASH_CLOSE_GUI_BTN, config.CLOSE_BTN):
            close = _find(template)
            if close:
                print(f"[MonsterClash] Closing the rift's victory panel (X at ({close[0]}, {close[1]})).")
                click_until_gone(template, close, "monster_clash_close_gui")
                time.sleep(1.0)
                break
        else:
            print("[MonsterClash] No victory panel X - assuming it's already closed.")
        result = self._join(from_stage=True)
        if result is not False or config.STOP_REQUESTED:
            return result
        # The small Events icon didn't lead anywhere: go back to the lobby and join from there.
        print("[MonsterClash] Couldn't rejoin through the small Events icon - going back to the lobby instead.")
        from modules.lobby import return_to_lobby
        if not return_to_lobby(timeout=90.0):
            return False
        return self._join(from_stage=False)

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

    def rift_next(self):
        """
        Rift mode, after a match: rift over -> leave it and rejoin; Start Rift -> E; else
        Repeat Stage. True / "LOST" (nothing recognisable - navigate() works it out) /
        False / "RECONNECTED".
        """
        if self.in_rift:
            return self._leave_rift()

        # Start Rift can take a moment to show (the helicopter flies in), so it's watched
        # for a while before Repeat Stage is taken - Repeat would throw the rift away.
        self._phase("WATCHING FOR RIFT")
        deadline = time.time() + config.MONSTER_CLASH_RIFT_WAIT
        while time.time() < deadline:
            if config.STOP_REQUESTED:
                return False
            shot = capture_screen()
            if handle_disconnect_if_present(shot):
                return "RECONNECTED"
            if _find(config.MONSTER_CLASH_START_RIFT, shot, debug_label="start_rift"):
                result = self.take_rift()
                if result is not None:
                    return result
                break
            if _find(config.START_GAME_BTN, shot):
                # The stage restarted by itself: Auto Retry is on.
                print("[MonsterClash] The next Start Game came up by itself - the game's Auto Retry is ON. "
                      "Rift hunt needs it OFF (the rift can't spawn otherwise).")
                self._warn_auto_retry()
                return True
            time.sleep(0.5)

        # No rift this time: Repeat Stage. If its panel was closed, Game Results reopens it -
        # only now, once the rift is ruled out (that button sits under the rift prompt).
        self._phase("REPEATING STAGE")
        reopened = False
        result = None
        for _ in range(3):
            result = poll_until([target(config.REPEAT_STAGE_BTN, "REPEAT", debug_label="monster_clash_repeat"),
                                 target(config.START_GAME_BTN, "READY", debug_label="monster_clash_start_game"),
                                 target(config.MONSTER_CLASH_START_RIFT, "RIFT", debug_label="start_rift")],
                                interval=1.0, label="monster_clash_repeat_wait", timeout=10.0,
                                stuck_timeout=None, lobby_grace=25.0, game_results=False)
            if result != "TIMEOUT" or config.STOP_REQUESTED:
                break
            results_btn = _find(config.GAME_RESULTS_BTN)
            if results_btn and not reopened:
                print("[MonsterClash] No Repeat Stage - reopening the results with Game Results.")
                click_at(results_btn[0], results_btn[1])
                reopened = True
                time.sleep(1.5)
        if result in (False, "RECONNECTED"):
            return result
        if result == "RIFT":
            taken = self.take_rift()
            if taken is not None:
                return taken
            # E wouldn't take: carry on with Repeat Stage rather than wait on a rift
            # that never starts.
            if _find(config.REPEAT_STAGE_BTN):
                return self._click_repeat()
            return "LOST"
        if result == "READY":
            return True
        if result == "TIMEOUT":
            path = health.save_debug_screenshot("monster_clash_no_repeat")
            print(f"[MonsterClash] No Repeat Stage, Start Game or Start Rift after the match (screen saved: {path}) "
                  f"- working out where we are.")
            return "LOST"
        return self._click_repeat()       # stage repeats in place: still on the spot
