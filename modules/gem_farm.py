# modules/gem_farm.py
"""
Gem Farm (Others page, added 2026-10-15): King's Tomb's Master stage on the game's own
Auto Play, with the game's Auto Start and Auto Retry ON - every 40-50s the stage repeats
by itself and pays 100 gems. The macro's whole job:

  1. Get there: lobby -> Play -> Story -> King's Tomb -> Master -> Select Stage -> Start
     (no difficulty click - the Master stage doesn't use one), Auto Play on.
  2. Keep it going: count the wins, press Start Game / Repeat Stage only if one ever shows
     (Auto Start / Auto Retry got switched off), and anti-AFK clicks so Roblox's ~20-minute
     idle kick never fires (poll_until's own, every config.ANTI_IDLE_SECONDS of no input).
  3. Disconnects: Reconnect, the rejoin-through-Roblox-home fallback and Never Stop's full
     reset all apply, then step 1 again.

Steps 2-3 are Monster Clash's Farm mode, which does exactly this already - so this is
that runner with its own way in (navigate) and a short stuck limit for 40-50s matches.

3.6.1 (Gem Farm "rarely turns Auto Play on, sometimes takes the wrong path"):
  - After Start it now WAITS for the stage to load before looking for Auto Play. It used to
    look straight away for 12s, which a King's Tomb teleport often outlasts - so the farm
    started with Auto Play off and never looked again.
  - While farming, Auto Play reading OFF on 3 looks in a row gets switched back on.
  - Every menu click is checked like Raids' (the next screen has to show, or it is clicked
    again), and the map / act clicks wait for their tiles to slide in.
"""
import time

import config
from vision import capture_screen, find_template
from modules.gamemode_select import click_play, click_map, click_act
from modules.match_start import click_start_party
from modules.monster_clash import MonsterClashRunner, FARM
from modules.reconnect import handle_disconnect_if_present
from modules.polling import poll_until, target, click_until_gone
from modules.prompts import recover_from_leftover_party
from modules.raid import _step, STEP_ATTEMPTS
from modules.stage_player import click_start_game, wait_for_start_game
from modules.autoplay import ensure_autoplay_enabled, _read_toggle
from modules.stats import SESSION
from modules import health

MAP_KEY = "kings_tomb"
ACT_KEY = "master"
GEMS_PER_WIN = 100
# Only real trouble: the stage (its Auto Play button) gone from the screen this long - a
# menu, a popup, a kick nothing else caught. Then the macro gets back onto the stage.
OFF_STAGE_LIMIT = 300.0
# A Start Game left on screen this long means the game's Auto Start is off - press it.
START_GAME_GRACE = 8.0
# Kept for the runner's base class; Gem Farm's own play_match() never uses it.
GEM_FARM_STUCK_TIMEOUT = 600.0
# The party screen's Start still up this long while waiting for the stage = the click
# didn't take; it's pressed again.
PARTY_NUDGE_SECONDS = 8.0
# Auto Play has to read OFF this many farm looks in a row (2s apart) before it is clicked
# back on, and is never clicked more often than AUTOPLAY_FIX_COOLDOWN - it's a toggle, so a
# misread must not turn into clicking it on and off.
AUTOPLAY_OFF_LOOKS = 3
AUTOPLAY_FIX_COOLDOWN = 60.0


def _find(template, shot=None):
    shot = shot if shot is not None else capture_screen()
    return find_template(shot, template, config.MATCH_THRESHOLD)


def _map_templates():
    return [m["template"] for m in config.MAPS.values() if m.get("enabled")]


def _click_story_card():
    """The Story mode card - after disbanding a leftover party Play may have reopened."""
    recover_from_leftover_party(capture_screen(), click_play)
    match = find_template(capture_screen(), config.STORY_CARD, config.MATCH_THRESHOLD,
                          debug_label="gem_farm_story_card")
    if not match:
        return False
    return click_until_gone(config.STORY_CARD, match, "gem_farm_story_card")


def _click_select_stage():
    match = find_template(capture_screen(), config.SELECT_STAGE_BTN, config.MATCH_THRESHOLD,
                          debug_label="gem_farm_select_stage")
    if not match:
        return False
    return click_until_gone(config.SELECT_STAGE_BTN, match, "gem_farm_select_stage")


def _pick_kings_tomb_master():
    """
    King's Tomb, then its Master tile. Not a _step(): the act panel is up for whichever map
    is selected, so "the next screen is already showing" can't prove the RIGHT map was
    picked - both clicks are always made. True / False.
    """
    for attempt in range(1, STEP_ATTEMPTS + 1):
        if config.STOP_REQUESTED:
            return False
        if click_map(MAP_KEY):
            time.sleep(0.6)
            if click_act(MAP_KEY, ACT_KEY):
                time.sleep(0.6)
                return True
        print(f"[GemFarm] King's Tomb / Master not picked (attempt {attempt}/{STEP_ATTEMPTS}).")
        time.sleep(1.0)
    path = health.save_debug_screenshot("gem_farm_kings_tomb_master_failed")
    print(f"[GemFarm] Couldn't pick King's Tomb - Master after {STEP_ATTEMPTS} tries (screen saved: {path}).")
    return False


def run_gem_flow():
    """
    Lobby (or the mode cards) -> Story -> King's Tomb -> Master -> Select Stage -> Start.
    Every click but the map / act ones is proven by the next screen showing, and retried.
    True / False / "RECONNECTED".
    """
    result = _step("click_play", click_play, [config.STORY_CARD, config.DISBAND_BTN], tag="GemFarm")
    if result is not True:
        return result
    result = _step("story_card", _click_story_card, _map_templates() + [config.SELECT_STAGE_BTN],
                   tag="GemFarm")
    if result is not True:
        return result
    if not _pick_kings_tomb_master():
        return False
    result = _step("select_stage", _click_select_stage, [config.START_PARTY_BTN, config.DISBAND_BTN],
                   tag="GemFarm")
    if result is not True:
        return result
    return click_start_party()


class GemFarmRunner(MonsterClashRunner):
    def __init__(self, set_phase=None):
        super().__init__(FARM, None, set_phase=set_phase)
        self.match_stuck_timeout = GEM_FARM_STUCK_TIMEOUT
        self._autoplay_fixed_at = 0.0

    # --- the farm itself ---------------------------------------------------------------
    def play_match(self):
        """
        Gem Farm doesn't wait for a match to end - with Auto Start and Auto Retry on, the
        result banner flashes for a moment or not at all (live 2026-10-15: one win seen in
        11 minutes, and the old "no result for 10 min = stuck" restarted a perfectly healthy
        farm). Instead it watches that the STAGE is up: the Auto Play button on screen means
        all is well, whatever else is happening. Along the way it counts a win when the
        banner is caught, presses Start Game / Repeat Stage if one is left waiting (Auto
        Start / Auto Retry got switched off), and poll_until handles disconnects, a kick to
        the lobby and the anti-AFK click. Returns "ENDED" after each counted win (so the run
        limit works), "RECONNECTED" when it has to get back onto the stage, or False.
        """
        self._phase("FARMING GEMS", "#2e7d32")
        state = {"stage_seen": time.time(), "start_game_since": None, "last_counted": 0.0,
                 "autoplay_off_looks": 0}

        def watch(shot):
            now = time.time()
            toggle, on_conf, off_conf = _read_toggle(shot)
            if max(on_conf, off_conf) >= config.MATCH_THRESHOLD:
                state["stage_seen"] = now
            # Auto Play switched off (never turned on at the start, or the game reset it):
            # nothing gets placed and every match is lost, quietly. Switched back on.
            state["autoplay_off_looks"] = state["autoplay_off_looks"] + 1 if toggle == "off" else 0
            if (state["autoplay_off_looks"] >= AUTOPLAY_OFF_LOOKS
                    and now - self._autoplay_fixed_at >= AUTOPLAY_FIX_COOLDOWN):
                print(f"[GemFarm] Auto Play reads OFF (on={on_conf:.3f} off={off_conf:.3f}) - switching it on.")
                self._autoplay_fixed_at = now
                state["autoplay_off_looks"] = 0
                ensure_autoplay_enabled()
            if _find(config.START_GAME_BTN, shot):
                state["stage_seen"] = now
                state["start_game_since"] = state["start_game_since"] or now
                if now - state["start_game_since"] >= START_GAME_GRACE:
                    print("[GemFarm] Start Game is waiting (the game's Auto Start looks off) - pressing it.")
                    click_start_game()
                    state["start_game_since"] = None
            else:
                state["start_game_since"] = None
            repeat = _find(config.REPEAT_STAGE_BTN, shot)
            if repeat:
                state["stage_seen"] = now
                print("[GemFarm] Repeat Stage is waiting (the game's Auto Retry looks off) - pressing it.")
                click_until_gone(config.REPEAT_STAGE_BTN, repeat, "gem_farm_repeat", clicks=1)
            if now - state["stage_seen"] >= OFF_STAGE_LIMIT:
                return "OFF_STAGE"
            return None

        result = poll_until([target(config.VICTORY_TEXT, "VICTORY", debug_label="victory")],
                            interval=2.0, label="gem_farm", stuck_timeout=None, custom_check=watch,
                            lobby_grace=20.0, game_results=False)
        if result in (False, "RECONNECTED"):
            return result
        if result == "OFF_STAGE":
            path = health.save_debug_screenshot("gem_farm_off_stage")
            print(f"[GemFarm] The stage hasn't been on screen for {OFF_STAGE_LIMIT / 60:.0f} minutes (screen saved: "
                  f"{path}) - getting back onto it.")
            return "RECONNECTED"         # the engine re-runs navigate(), which works out where we are
        if result != "VICTORY":
            print(f"[GemFarm] The watch ended unexpectedly ({result}) - checking where we are.")
            return "RECONNECTED"
        # A win: counted once, then the banner is given time to go so it isn't counted twice.
        self.consecutive_defeats = 0
        SESSION.victory()
        print(f"[GemFarm] Win #{SESSION.victories} (~{SESSION.victories * GEMS_PER_WIN} gems this run).")
        deadline = time.time() + 15.0
        while time.time() < deadline and not config.STOP_REQUESTED and _find(config.VICTORY_TEXT):
            time.sleep(1.0)
        return "ENDED"

    def prepare_and_start(self):
        """
        Waits for the stage to load (Start Game, or the match already running with the
        game's Auto Start on), then Auto Play on, then Start Game if it's waiting. The
        party screen's Start is pressed again if it's still up. True / False / "RECONNECTED".
        """
        self._phase("WAITING FOR THE STAGE")
        last_nudge = [time.time()]

        def nudge(shot):
            if time.time() - last_nudge[0] < PARTY_NUDGE_SECONDS:
                return
            if _find(config.START_PARTY_BTN, shot):
                last_nudge[0] = time.time()
                print("[GemFarm] Still on the party screen - pressing Start again.")
                click_start_party()

        result = wait_for_start_game(nudge=nudge)
        if result in (False, "RECONNECTED"):
            return result
        # The stage's side panel can still be sliding in.
        time.sleep(0.4)
        self._phase("CHECKING AUTO PLAY", "#2e7d32")
        if not ensure_autoplay_enabled():
            print("[GemFarm] WARNING: couldn't find the Auto Play button - the farm will keep checking it.")
        self._autoplay_fixed_at = time.time()
        if _find(config.START_GAME_BTN):
            click_start_game()
        SESSION.match_started()
        return True

    def farm_next(self):
        """Nothing to do between wins - the game repeats the stage by itself."""
        SESSION.match_started()
        return True

    def _join(self, from_stage=False):
        self._phase("GOING TO KING'S TOMB")
        print("[GemFarm] Going to King's Tomb - Master stage...")
        result = run_gem_flow()
        if result is True:
            self.position = None
        return result

    def navigate(self):
        """
        Onto the stage from wherever the game is. True (stage up, next is Start Game or a
        running match) / "IN_MATCH" / "ENDED" (an end screen) / False / "RECONNECTED".
        """
        from modules.lobby import at_lobby, return_to_lobby
        shot = capture_screen()
        if handle_disconnect_if_present(shot):
            return "RECONNECTED"
        if at_lobby(shot):
            return self._join()
        if _find(config.START_PARTY_BTN, shot):
            print("[GemFarm] On the party screen - starting from there.")
            return click_start_party()
        if _find(config.REPEAT_STAGE_BTN, shot):
            print("[GemFarm] A match's end screen is up.")
            return "ENDED"
        if _find(config.START_GAME_BTN, shot):
            print("[GemFarm] On the stage, Start Game up.")
            return True
        if _find(config.AUTOPLAY_ON_BTN, shot) or _find(config.AUTOPLAY_OFF_BTN, shot):
            print("[GemFarm] A match is already running - picking it up from here.")
            self._match_started_at = time.time()
            return "IN_MATCH"
        # Somewhere else (another mode's stage, a menu): back to the lobby, then in.
        path = health.save_debug_screenshot("gem_farm_unknown_screen", shot)
        print(f"[GemFarm] Not at the lobby or on the stage (screen saved: {path}) - going back to the lobby.")
        if not return_to_lobby(timeout=120.0):
            return False
        return self._join()
