# modules/halloween.py
"""
Halloween Event (Events page, added 2026-11-01). LIMITED EVENT ("Ends Update 6") - when it
ends, delete this module, its config block (HALLOWEEN_*), assets/templates/Halloween/,
run_halloween() in engine.py and the Halloween option on the Events page.

As described by the player (2026-11-01):
  Lobby -> Events -> Halloween Event -> (Event Gamemode) -> Select Stage -> Start -> Auto
  Play on -> Start Game. Once the stage counter at the top (the circle left of "Heat Bars")
  shows 4: Forge -> scroll down a bit to the Final Boss card -> its Buy -> the Forge's
  Close (the same X as the other panels). The boss ends the
  run; on a win, Repeat Stage -> Auto Play checked -> Start Game, and again.

Fallbacks:
  - The Final Boss's Buy is only looked for under the Final Boss card's own top: every card
    in the Forge has the same "Buy 500" button.
  - The card not in view after Forge: scrolled down a little at a time, then back up.
  - Bought but no end screen BUY_RETRY_AFTER later (the click didn't take, or there wasn't
    enough candy yet): Forge -> Buy again.
  - A match picked up already running (after a restart) may be past stage 4 already, so if
    stage 4 isn't seen within PICKUP_BUY_AFTER, the boss is bought anyway.
  - Anything unexpected goes back through navigate(), which reads the screen.
"""
import time

import config
from vision import capture_screen, find_template
from input_controller import click_at, scroll_maps_left, scroll_maps_right
from modules.monster_clash import _click_through
from modules.gamemode_select import _run_steps
from modules.match_start import click_start_party
from modules.polling import poll_until, target, click_until_gone
from modules.reconnect import handle_disconnect_if_present
from modules.stage_player import wait_for_start_game, click_start_game
from modules.autoplay import ensure_autoplay_enabled
from modules.stats import SESSION
from modules import health

TAG = "Halloween"
# A match shorter than this can't have ended - an end screen left over is ignored until then.
MIN_MATCH = 25.0
# After a match: how long to wait for Repeat Stage before working out where the game is.
AFTER_MATCH_WAIT = 45.0
# After Forge: how long the Final Boss card gets to show before scrolling for it.
FORGE_OPEN_WAIT = 2.5
# Scrolling the Forge for the card: notches per step, and steps each way.
FORGE_SCROLL_NOTCHES = 3
FORGE_SCROLL_STEPS = 8
# Where the cursor goes to scroll the Forge (the middle of the screen, over its cards).
FORGE_HOVER = (800, 450)
# The Buy button's centre below the Final Boss card's top crop (reference px).
BUY_DY = 141
BUY_REACH = 110        # wider than the button (169 px) - the area has to hold it whole
# Bought, but no end screen this long after: buy again.
BUY_RETRY_AFTER = 240.0
# A match picked up mid-way: stage 4 not seen this long -> buy anyway.
PICKUP_BUY_AFTER = 60.0
_REPEATS = (config.HALLOWEEN_REPEAT_STAGE_BTN, config.REPEAT_STAGE_BTN)
_CLOSES = (config.HALLOWEEN_CLOSE_BTN, config.CLOSE_BTN)
# After Buy: how long the Forge's close button gets to show.
FORGE_CLOSE_WAIT = 3.0


def _find(template, shot=None):
    shot = shot if shot is not None else capture_screen()
    return find_template(shot, template, config.MATCH_THRESHOLD)


def _find_any(templates, shot):
    for t in templates:
        m = _find(t, shot)
        if m:
            return t, m
    return None, None


def _find_near(shot, template, center, reach):
    """The template within `reach` reference px of `center` (in screen-relative reference px), or None."""
    s = config.render_scale()
    x0, y0 = max(0, int((center[0] - reach) * s)), max(0, int((center[1] - reach) * s))
    area = shot[y0:int((center[1] + reach) * s), x0:int((center[0] + reach) * s)]
    if area.size == 0:
        return None
    m = find_template(area, template, config.MATCH_THRESHOLD)
    return (m[0] + x0 / s, m[1] + y0 / s, m[2]) if m else None


def _halt(reason):
    if not config.STOP_REQUESTED:
        config.STUCK_DETECTED = reason
    config.STOP_REQUESTED = True
    return False


# --- Joining -------------------------------------------------------------------------------

def click_events():
    return _click_through(config.HALLOWEEN_EVENTS_BTN, "halloween_events",
                          (config.HALLOWEEN_EVENT_CARD, config.HALLOWEEN_EVENT_GAMEMODE_BTN,
                           config.HALLOWEEN_SELECT_STAGE_BTN))


def click_event_card():
    return _click_through(config.HALLOWEEN_EVENT_CARD, "halloween_event",
                          (config.HALLOWEEN_EVENT_GAMEMODE_BTN, config.HALLOWEEN_SELECT_STAGE_BTN))


def click_event_gamemode():
    """Event Gamemode - skipped when the event card goes straight to Select Stage."""
    return _click_through(config.HALLOWEEN_EVENT_GAMEMODE_BTN, "halloween_event_gamemode",
                          (config.HALLOWEEN_SELECT_STAGE_BTN, config.HALLOWEEN_START_BTN, config.START_PARTY_BTN))


def click_select_stage():
    return _click_through(config.HALLOWEEN_SELECT_STAGE_BTN, "halloween_select_stage",
                          (config.HALLOWEEN_START_BTN, config.START_PARTY_BTN))


def click_start():
    """The party screen's Start - the event's own look, or the ordinary Start Party button."""
    if not _find(config.HALLOWEEN_START_BTN) and _find(config.START_PARTY_BTN):
        return click_start_party()
    return _click_through(config.HALLOWEEN_START_BTN, "halloween_start")


_JOIN_STEPS = [click_events, click_event_card, click_event_gamemode, click_select_stage, click_start]
# Each menu screen, latest first, with where in _JOIN_STEPS to carry on from.
_RESUME_AT = [
    (config.HALLOWEEN_START_BTN, 4),
    (config.HALLOWEEN_SELECT_STAGE_BTN, 3),
    (config.HALLOWEEN_EVENT_GAMEMODE_BTN, 2),
    (config.HALLOWEEN_EVENT_CARD, 1),
]


# --- The run -------------------------------------------------------------------------------

class HalloweenRunner:
    def __init__(self, set_phase=None):
        self.set_phase = set_phase
        self.consecutive_defeats = 0
        self.resumed = False              # the next Start Game wait is for a stage never left (Repeat Stage)
        self._match_started_at = 0.0
        self._picked_up = False           # the match was already running when the macro got to it
        self._last_result = None          # VICTORY / DEFEAT of the match that just ended, None = unknown

    def _phase(self, text, color="#4fc3f7"):
        if self.set_phase:
            self.set_phase(text, color)

    # --- getting onto the stage -----------------------------------------------------------
    def _join(self, steps=None):
        self._phase("JOINING THE HALLOWEEN EVENT")
        print(f"[{TAG}] Joining the Halloween Event...")
        result = _run_steps(steps or _JOIN_STEPS)
        if result is True:
            self.resumed = False
        return result

    def navigate(self):
        """Onto the stage from wherever the game is. True / "IN_MATCH" / "ENDED" / False / "RECONNECTED"."""
        from modules.lobby import at_lobby, return_to_lobby
        shot = capture_screen()
        if handle_disconnect_if_present(shot):
            return "RECONNECTED"
        if at_lobby(shot):
            return self._join()
        for template, step in _RESUME_AT:
            if _find(template, shot):
                print(f"[{TAG}] In the event menus - carrying on from there.")
                return self._join(_JOIN_STEPS[step:])
        if _find(config.START_PARTY_BTN, shot):
            return self._join([click_start])
        if _find_any(_REPEATS, shot)[0] or _find(config.VICTORY_TEXT, shot) or _find(config.DEFEAT_TEXT, shot):
            print(f"[{TAG}] A match's end screen is up.")
            self._last_result = None
            return "ENDED"
        if _find(config.START_GAME_BTN, shot):
            print(f"[{TAG}] On the stage, Start Game is up.")
            return True
        if _find(config.AUTOPLAY_ON_BTN, shot) or _find(config.AUTOPLAY_OFF_BTN, shot):
            print(f"[{TAG}] A match is already running - picking it up from here.")
            self._match_started_at = time.time()
            self._picked_up = True
            return "IN_MATCH"
        path = health.save_debug_screenshot("halloween_unknown_screen", shot)
        print(f"[{TAG}] Not at the lobby or on the stage (screen saved: {path}) - going back to the lobby.")
        if not return_to_lobby(timeout=120.0):
            return False
        return self._join()

    # --- before the match -----------------------------------------------------------------
    def prepare_and_start(self):
        """Stage loaded -> Auto Play on -> Start Game. True / False / "RECONNECTED"."""
        self._phase("WAITING FOR START GAME")
        result = wait_for_start_game(resumed=self.resumed)
        if result in (False, "RECONNECTED"):
            return result
        self.resumed = True
        time.sleep(0.4)
        self._phase("CHECKING AUTO PLAY", "#2e7d32")
        if not ensure_autoplay_enabled():
            print(f"[{TAG}] WARNING: couldn't find the Auto Play button - carrying on.")
        if result != "ALREADY_STARTED":
            self._phase("STARTING MATCH", "#2e7d32")
            if not click_start_game():
                print(f"[{TAG}] Start Game wasn't there to click - the match has probably started already.")
        SESSION.match_started()
        self._match_started_at = time.time()
        self._picked_up = False
        self._last_result = None
        return True

    # --- the Final Boss ----------------------------------------------------------------------
    def _find_card(self):
        """The Final Boss card's top in the open Forge, scrolling for it if needed. Match or None."""
        deadline = time.time() + FORGE_OPEN_WAIT
        while not config.STOP_REQUESTED:
            found = _find(config.HALLOWEEN_FINAL_BOSS_TITLE)
            if found or time.time() >= deadline:
                break
            time.sleep(0.3)
        if found:
            return found
        print(f"[{TAG}] The Final Boss card isn't in view - scrolling the Forge down.")
        for scroll in (scroll_maps_right, scroll_maps_left):        # down first, then back up
            for _ in range(FORGE_SCROLL_STEPS):
                if config.STOP_REQUESTED:
                    return None
                scroll(hover=FORGE_HOVER, notches=FORGE_SCROLL_NOTCHES)
                found = _find(config.HALLOWEEN_FINAL_BOSS_TITLE)
                if found:
                    return found
        return None

    def _buy_final_boss(self):
        """Forge -> the Final Boss card -> its Buy. True if Buy was clicked."""
        self._phase("BUYING THE FINAL BOSS", "#f9a825")
        shot = capture_screen()
        card = _find(config.HALLOWEEN_FINAL_BOSS_TITLE, shot)
        if not card:
            forge = _find(config.HALLOWEEN_FORGE_BTN, shot)
            if not forge:
                path = health.save_debug_screenshot("halloween_no_forge")
                print(f"[{TAG}] The Forge button isn't on screen (screen saved: {path}).")
                return False
            print(f"[{TAG}] Opening the Forge.")
            click_at(forge[0], forge[1])
            time.sleep(0.8)
            card = self._find_card()
        if not card:
            path = health.save_debug_screenshot("halloween_no_final_boss")
            print(f"[{TAG}] Couldn't find the Final Boss card in the Forge (screen saved: {path}).")
            return False
        guess = (card[0], card[1] + BUY_DY)
        buy = _find_near(capture_screen(), config.HALLOWEEN_BUY_BTN, guess, BUY_REACH)
        point = (buy[0], buy[1]) if buy else guess
        print(f"[{TAG}] Buying the Final Boss at ({point[0]:.0f}, {point[1]:.0f})"
              f"{'' if buy else ' (its Buy button not recognised - clicking where it should be)'}.")
        click_at(point[0], point[1])
        time.sleep(1.0)
        self._close_forge()
        self._phase("WAITING FOR MATCH END", "#4fc3f7")
        return True

    @staticmethod
    def _close_forge():
        """The Forge's X once the boss is bought (the same X as the other panels)."""
        deadline = time.time() + FORGE_CLOSE_WAIT
        while not config.STOP_REQUESTED:
            template, close = _find_any(_CLOSES, capture_screen())
            if close:
                print(f"[{TAG}] Closing the Forge.")
                click_until_gone(template, close, "halloween_close_forge")
                return True
            if time.time() >= deadline:
                break
            time.sleep(0.3)
        path = health.save_debug_screenshot("halloween_no_forge_close")
        print(f"[{TAG}] The Forge's close button wasn't found (screen saved: {path}) - waiting for the match anyway.")
        return False

    # --- the match ------------------------------------------------------------------------
    def play_match(self):
        """Buys the Final Boss at stage 4, then waits for the end. "ENDED" / False / "RECONNECTED"."""
        self._phase("WAITING FOR STAGE 4", "#4fc3f7")
        started = self._match_started_at or time.time()
        bought_at = [0.0]

        def watch(shot):
            now = time.time()
            if now - started >= MIN_MATCH and _find_any(_REPEATS, shot)[0]:
                return "SCREEN"
            if bought_at[0]:
                if now - bought_at[0] >= BUY_RETRY_AFTER:
                    print(f"[{TAG}] No end screen {BUY_RETRY_AFTER / 60:.0f} min after buying the Final Boss - "
                          f"buying it again.")
                    if self._buy_final_boss():
                        bought_at[0] = time.time()
                    else:
                        bought_at[0] = now - BUY_RETRY_AFTER + 30.0    # try again in 30s
                return None
            at_stage_4 = _find_near(shot, config.HALLOWEEN_STAGE_4,
                                    (config.HALLOWEEN_STAGE_X, config.HALLOWEEN_STAGE_Y), config.HALLOWEEN_STAGE_REACH)
            late_pickup = self._picked_up and now - started >= PICKUP_BUY_AFTER
            if at_stage_4 or late_pickup:
                if at_stage_4:
                    print(f"[{TAG}] Stage 4 reached (confidence={at_stage_4[2]:.2f}).")
                else:
                    print(f"[{TAG}] Picked up mid-match and stage 4 never showed - it's probably past it. "
                          f"Buying the Final Boss now.")
                if self._buy_final_boss():
                    bought_at[0] = time.time()
                elif config.STOP_REQUESTED:
                    return None
            return None

        print(f"[{TAG}] Waiting for stage 4...")
        result = poll_until([target(config.VICTORY_TEXT, "VICTORY", debug_label="victory"),
                             target(config.DEFEAT_TEXT, "DEFEAT", debug_label="defeat")],
                            interval=1.0, label="halloween_match", stuck_timeout=self._stuck_timeout(),
                            custom_check=watch, lobby_grace=8.0, game_results=False)
        if result in (False, "RECONNECTED"):
            return result
        if result == "SCREEN":
            result = self._read_result()
        if not self._count(result):
            return False
        return "ENDED"

    @staticmethod
    def _stuck_timeout():
        return config.match_stuck_timeout()

    @staticmethod
    def _read_result():
        """An end screen seen without its banner: Defeat if it shows within ~3s."""
        for _ in range(3):
            shot = capture_screen()
            if _find(config.DEFEAT_TEXT, shot):
                return "DEFEAT"
            if _find(config.VICTORY_TEXT, shot):
                return "VICTORY"
            time.sleep(1.0)
        return "VICTORY"

    def _count(self, result):
        self._last_result = result
        if result == "DEFEAT":
            self.consecutive_defeats += 1
            SESSION.defeat()
            print(f"[{TAG}] Defeat ({self.consecutive_defeats} in a row).")
            if config.defeat_limit_reached(self.consecutive_defeats):
                health.save_debug_screenshot("halloween_too_many_defeats")
                print(f"[{TAG}] {self.consecutive_defeats} losses in a row - stopping, the team can't clear it.")
                return _halt("TOO MANY DEFEATS (HALLOWEEN)")
        else:
            self.consecutive_defeats = 0
            SESSION.victory()
            print(f"[{TAG}] Victory!")
        return True

    # --- after the match --------------------------------------------------------------------
    def after_match(self):
        """Repeat Stage. "START" / "LOST" (navigate() takes over) / False / "RECONNECTED"."""
        self._phase("REPEATING THE STAGE")
        result = poll_until([target(config.HALLOWEEN_REPEAT_STAGE_BTN, "REPEAT", debug_label="halloween_repeat"),
                             target(config.REPEAT_STAGE_BTN, "REPEAT")],
                            interval=1.0, label="halloween_repeat_wait", timeout=AFTER_MATCH_WAIT,
                            stuck_timeout=None, lobby_grace=25.0)
        if result in (False, "RECONNECTED"):
            return result
        if result != "REPEAT":
            path = health.save_debug_screenshot("halloween_no_repeat")
            print(f"[{TAG}] No Repeat Stage after the match (screen saved: {path}) - working out where we are.")
            return "LOST"
        if self._last_result is None and not self._count(self._read_result()):
            return False
        self._last_result = None
        time.sleep(1.0)                    # it slides in - one click once it's settled
        template, match = _find_any(_REPEATS, capture_screen())
        if match:
            print(f"[{TAG}] Clicking Repeat Stage.")
            if not click_until_gone(template, match, "halloween_repeat", clicks=1):
                path = health.save_debug_screenshot("halloween_repeat_stuck")
                print(f"[{TAG}] Repeat Stage is still up after clicking (screen saved: {path}).")
                return "LOST"
        self.resumed = True
        return "START"
