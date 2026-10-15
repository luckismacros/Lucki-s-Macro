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
"""
import time

import config
from vision import capture_screen, find_template
from modules.gamemode_select import (click_play, click_story_card, click_map, click_act,
                                     click_select_stage, _run_steps)
from modules.match_start import click_start_party
from modules.monster_clash import MonsterClashRunner, FARM
from modules.reconnect import handle_disconnect_if_present
from modules.polling import poll_until, target, click_until_gone
from modules.stage_player import click_start_game
from modules.autoplay import ensure_autoplay_enabled
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


def _find(template, shot=None):
    shot = shot if shot is not None else capture_screen()
    return find_template(shot, template, config.MATCH_THRESHOLD)


def run_gem_flow():
    """Lobby -> King's Tomb Master's party screen -> Start. True / False / "RECONNECTED"."""
    return _run_steps([
        click_play,
        click_story_card,
        lambda: click_map(MAP_KEY),
        lambda: click_act(MAP_KEY, ACT_KEY),
        click_select_stage,
        click_start_party,
    ])


class GemFarmRunner(MonsterClashRunner):
    def __init__(self, set_phase=None):
        super().__init__(FARM, None, set_phase=set_phase)
        self.match_stuck_timeout = GEM_FARM_STUCK_TIMEOUT

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
        state = {"stage_seen": time.time(), "start_game_since": None, "last_counted": 0.0}

        def watch(shot):
            now = time.time()
            if _find(config.AUTOPLAY_ON_BTN, shot) or _find(config.AUTOPLAY_OFF_BTN, shot):
                state["stage_seen"] = now
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
        """Start Game if it's waiting (Auto Start is meant to be on), Auto Play on."""
        self._phase("STARTING", "#2e7d32")
        if not ensure_autoplay_enabled():
            print("[GemFarm] WARNING: couldn't find the Auto Play button - continuing anyway.")
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
