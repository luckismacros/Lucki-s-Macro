# modules/raid.py
"""
Raids, rebuilt (3.6) on the same pattern as the modes that hold up on 8-hour runs
(Monster Clash, Gem Farm): the engine's shared runner loop (engine.run_monster_clash)
drives a runner that READS THE SCREEN to work out where the game is, instead of
assuming it. Raids used to share Story's run_bot() loop, which only knew "lobby" or
"Select Stage" at the start and trusted single-frame clicks all the way through.

  Joining:  Play -> Raid card -> the raid's island -> Act 1/2/3 -> Select Stage -> Start.
            Every click is checked: the next screen has to show up, or the step is
            clicked again (3 tries), with a screenshot saved if it never does.
  The stage: Auto Play - no camera fix (the game places the units itself), Auto Play on,
            Start Game. A recording - camera anchored, walk (first match only), units,
            Start Game.
  The match: Victory / Defeat banner, or - if the banner is missed - Repeat Stage, the
            Game Results button, or the game's Auto Retry bringing up the next Start Game.
  After:    ONE click on Repeat Stage (a double click can land on View Party). If the
            game's Auto Retry already restarted the stage, nothing is clicked.

navigate() is the way back from anything - a fresh start, Never Stop, a reconnect, a
kick to the lobby, a stray screen - and works from the lobby, the mode cards, the raid
menus, the party screen, the stage, a running match or an end screen.
"""
import time

import config
from vision import capture_screen, find_template
from input_controller import anchor_camera, click_at
from modules.polling import poll_until, target, click_until_gone
from modules.gamemode_select import click_play, click_raid, click_raid_difficulty
from modules.match_start import click_raid_start
from modules.prompts import recover_from_leftover_party, dismiss_click_anywhere_if_present
from modules.stage_player import (play_preset, wait_for_start_game, click_start_game,
                                  press_start_after_macro)
from modules.autoplay import ensure_autoplay_enabled
from modules.reconnect import handle_disconnect_if_present
from modules.stats import SESSION
from modules import health

# A match shorter than this can't have ended - end screens left over from the previous
# match are ignored until then.
RAID_MIN_MATCH = 25.0
# How long a step's next screen gets to show after its click before the click is retried.
STEP_WAIT = 8.0
STEP_ATTEMPTS = 3
# After a match: how long to wait for Repeat Stage / the next Start Game before working
# out from scratch where the game is.
AFTER_MATCH_WAIT = 60.0
# The party screen's Start left on screen this long while waiting for the stage = the
# click didn't take; it's pressed again.
PARTY_NUDGE_SECONDS = 8.0
# While a match runs, the stage (its Auto Play button) gone from the screen this long is
# real trouble - a menu, a popup, a kick nothing else caught - and the runner gets back
# onto the stage. A stage that IS up is never cut short: with the game's Auto Retry +
# Auto Start on, the result banner can go unseen for many matches in a row (Gem Farm,
# live 2026-10-15), and a fixed match time limit stopped healthy runs as "STUCK".
OFF_STAGE_LIMIT = 300.0


def _find(template, shot=None, debug_label=None):
    shot = shot if shot is not None else capture_screen()
    return find_template(shot, template, config.MATCH_THRESHOLD, debug_label=debug_label)


def _any(templates, shot):
    for t in templates:
        if _find(t, shot):
            return t
    return None


def _halt(reason):
    """Stops the run as a fault (not a user stop), unless the user already stopped it."""
    if not config.STOP_REQUESTED:
        config.STUCK_DETECTED = reason
    config.STOP_REQUESTED = True
    return False


def _island_templates():
    return [r["template"] for r in config.RAIDS.values() if r.get("enabled")]


def _act_templates():
    return list(config.RAID_DIFFICULTY_TEMPLATES.values())


def _on_party_screen(shot):
    return _find(config.START_PARTY_BTN, shot) or _find(config.DISBAND_BTN, shot)


def _on_stage(shot):
    return (_find(config.START_GAME_BTN, shot) or _find(config.AUTOPLAY_ON_BTN, shot)
            or _find(config.AUTOPLAY_OFF_BTN, shot))


# --- Joining -----------------------------------------------------------------------------

def _wait_for(templates, seconds):
    """The first of `templates` to show within `seconds`, None, or "RECONNECTED"."""
    deadline = time.time() + seconds
    while not config.STOP_REQUESTED:
        shot = capture_screen()
        if handle_disconnect_if_present(shot):
            return "RECONNECTED"
        hit = _any(templates, shot)
        if hit:
            return hit
        if time.time() >= deadline:
            return None
        time.sleep(0.4)
    return None


def _step(name, action, proof):
    """
    One menu step: action() clicks, and one of `proof` showing up proves it worked. If a
    proof is already on screen the step was done already and nothing is clicked. Retried
    STEP_ATTEMPTS times - one slow frame or a popup over the button used to end the whole
    raid navigation. True / False / "RECONNECTED".
    """
    for attempt in range(1, STEP_ATTEMPTS + 1):
        if config.STOP_REQUESTED:
            return False
        seen = _wait_for(proof, 0)
        if seen == "RECONNECTED":
            return seen
        if seen:
            if attempt == 1:
                print(f"[Raid] {name}: the next screen is already up - nothing to click.")
            return True
        result = action()
        if result == "RECONNECTED":
            return result
        if config.STOP_REQUESTED:
            return False
        seen = _wait_for(proof, STEP_WAIT)
        if seen == "RECONNECTED":
            return seen
        if seen:
            return True
        print(f"[Raid] {name}: the next screen didn't show (attempt {attempt}/{STEP_ATTEMPTS}).")
        dismiss_click_anywhere_if_present(capture_screen())
    path = health.save_debug_screenshot(f"raid_{name}_failed")
    print(f"[Raid] {name} didn't work after {STEP_ATTEMPTS} tries (screen saved: {path}).")
    return False


def _click_raid_card():
    """The Raid mode card - after disbanding a leftover party Play may have reopened."""
    # A party from another mode left open: Play reopens it instead of the cards - disband it.
    recover_from_leftover_party(capture_screen(), click_play)
    match = _find(config.RAID_CARD, debug_label="click_raid_card")
    if not match:
        return False
    return click_until_gone(config.RAID_CARD, match, "click_raid_card")


def _click_select_stage():
    match = _find(config.SELECT_STAGE_BTN, debug_label="raid_select_stage")
    if not match:
        return False
    return click_until_gone(config.SELECT_STAGE_BTN, match, "raid_select_stage")


class RaidRunner:
    def __init__(self, raid_key, act, preset_name, set_phase=None):
        self.raid_key = raid_key
        self.act = act
        self.preset_name = preset_name
        self.auto_play = preset_name == config.AUTO_PLAY_PRESET_NAME
        self.set_phase = set_phase
        self.label = config.RAIDS.get(raid_key, {}).get("label", raid_key)
        self.consecutive_defeats = 0
        self.match_stuck_timeout = config.match_stuck_timeout()
        # Whether the recording's walk has played on this stage (the character is at the
        # recorded spot) - a Repeat Stage keeps it, a fresh join doesn't.
        self.walked = False
        # Whether the next Start Game wait is for a stage the character never left (a
        # Repeat Stage) - see wait_for_start_game(resumed=...).
        self.resumed = False
        self._match_started_at = 0.0
        self._closed_stray_panel = False

    def _phase(self, text, color="#4fc3f7"):
        if self.set_phase:
            self.set_phase(text, color)

    # --- getting onto the stage ---------------------------------------------------------
    def _fresh_stage(self):
        self.walked = False
        self.resumed = False

    def _from_raid_menu(self):
        """From the raid menu's island list (or its act panel) onwards. True / False / "RECONNECTED"."""
        result = _step("raid_island", lambda: click_raid(self.raid_key),
                       [config.SELECT_STAGE_BTN] + _act_templates())
        if result is not True:
            return result

        # The act tile: no screen change proves it, so it's found (a few looks, the panel
        # can still be sliding in) and clicked; Select Stage then confirms the panel is right.
        for attempt in range(1, STEP_ATTEMPTS + 1):
            if config.STOP_REQUESTED:
                return False
            if click_raid_difficulty(self.act):
                break
            time.sleep(1.0)
        else:
            path = health.save_debug_screenshot(f"raid_act_{self.act}_not_found")
            print(f"[Raid] Act {self.act}'s tile never showed (screen saved: {path}).")
            return False
        time.sleep(0.6)

        result = _step("raid_select_stage", _click_select_stage,
                       [config.START_PARTY_BTN, config.DISBAND_BTN])
        if result is not True:
            return result
        return self._start_from_party()

    def _start_from_party(self):
        self._phase("STARTING RAID")
        print("[Raid] On the party screen - pressing Start.")
        result = click_raid_start()
        if result is True:
            self._fresh_stage()
        return result

    def _join(self):
        """Lobby (or the mode cards) -> ... -> Start. True / False / "RECONNECTED"."""
        self._phase("NAVIGATING MENUS", "#4fc3f7")
        print(f"[Raid] Going to {self.label} - Act {self.act}...")
        result = _step("click_play", click_play, [config.RAID_CARD, config.DISBAND_BTN])
        if result is not True:
            return result
        result = _step("raid_card", _click_raid_card, _island_templates() + [config.SELECT_STAGE_BTN])
        if result is not True:
            return result
        return self._from_raid_menu()

    def navigate(self):
        """
        Gets onto the stage from wherever the game is. Returns:
          True          - on the stage, Start Game (or an already running match) is next
          "IN_MATCH"    - a match is already running
          "ENDED"       - a match's end screen is up
          False / "RECONNECTED"
        """
        from modules.lobby import at_lobby, return_to_lobby
        shot = capture_screen()
        if handle_disconnect_if_present(shot):
            return "RECONNECTED"
        if at_lobby(shot) or _find(config.RAID_CARD, shot):
            return self._join()
        if _find(config.START_PARTY_BTN, shot):
            # Whichever raid this party is for, it's the one on screen - the player set it
            # up, or the macro did a moment ago.
            return self._start_from_party()
        if _find(config.SELECT_STAGE_BTN, shot) or _any(_island_templates(), shot):
            print("[Raid] The raid menu is open - carrying on from there.")
            return self._from_raid_menu()
        if _find(config.REPEAT_STAGE_BTN, shot) or _find(config.VICTORY_TEXT, shot) \
                or _find(config.DEFEAT_TEXT, shot):
            print("[Raid] A match's end screen is up.")
            self.walked = False          # not sure where the character stands
            return "ENDED"
        if _find(config.START_GAME_BTN, shot):
            print("[Raid] On the stage, Start Game up.")
            self.walked = False
            return True
        if _find(config.AUTOPLAY_ON_BTN, shot) or _find(config.AUTOPLAY_OFF_BTN, shot):
            print("[Raid] A match is already running - picking it up from here.")
            self._match_started_at = time.time()
            return "IN_MATCH"
        results_btn = _find(config.GAME_RESULTS_BTN, shot)
        if results_btn and not self._closed_stray_panel:
            print("[Raid] The match's results were closed - reopening them.")
            click_at(results_btn[0], results_btn[1])
            return self._look_again()
        close = _find(config.CLOSE_BTN, shot)
        if close and not self._closed_stray_panel:
            print("[Raid] A panel with an X is in the way - closing it and looking again.")
            click_until_gone(config.CLOSE_BTN, close, "raid_close_panel")
            return self._look_again()

        # Nothing recognisable: often just a loading screen or the teleport into the stage.
        print("[Raid] Nothing recognisable on screen - giving it a moment (loading?)...")
        known = [config.PLAY_BTN, config.START_PARTY_BTN, config.START_GAME_BTN, config.REPEAT_STAGE_BTN,
                 config.AUTOPLAY_ON_BTN, config.AUTOPLAY_OFF_BTN, config.RAID_CARD, config.SELECT_STAGE_BTN]
        seen = _wait_for(known, 20.0)
        if seen == "RECONNECTED":
            return seen
        if seen and not self._closed_stray_panel:
            return self._look_again()
        path = health.save_debug_screenshot("raid_unknown_screen")
        print(f"[Raid] Don't recognise this screen (saved: {path}) - going back to the lobby.")
        if not return_to_lobby(timeout=120.0, next_mode="raids"):
            return False
        return self._join()

    def _look_again(self):
        time.sleep(1.0)
        self._closed_stray_panel = True
        try:
            return self.navigate()
        finally:
            self._closed_stray_panel = False

    # --- before the match ---------------------------------------------------------------
    def _wait_for_stage(self):
        """Start Game (or an Auto Start match). Presses Start again if the party screen stays up."""
        last_nudge = [time.time()]

        def nudge(shot):
            if time.time() - last_nudge[0] < PARTY_NUDGE_SECONDS:
                return
            if _find(config.START_PARTY_BTN, shot):
                last_nudge[0] = time.time()
                print("[Raid] Still on the party screen - pressing Start again.")
                click_raid_start()
            elif _find(config.REPEAT_STAGE_BTN, shot):
                last_nudge[0] = time.time()
                print("[Raid] Repeat Stage is still up - clicking it again.")
                self._click_repeat()

        return wait_for_start_game(resumed=self.resumed, nudge=nudge)

    def prepare_and_start(self):
        """Stage loaded -> units (Auto Play or the recording) -> Start Game. True / False / "RECONNECTED"."""
        self._phase("WAITING FOR START GAME")
        result = self._wait_for_stage()
        if result in (False, "RECONNECTED"):
            return result
        if not result:
            return False
        already_running = result == "ALREADY_STARTED"
        self.resumed = True

        if self.auto_play:
            # The game places the units itself - the camera doesn't matter, so it's left alone.
            time.sleep(0.4)
            self._phase("CHECKING AUTO PLAY", "#2e7d32")
            if not ensure_autoplay_enabled():
                print("[Raid] WARNING: couldn't find the Auto Play button - continuing anyway.")
            if not already_running:
                self._phase("STARTING MATCH", "#2e7d32")
                if not click_start_game():
                    print("[Raid] Start Game wasn't there to click - the match is probably already running.")
        else:
            self._phase("ANCHORING CAMERA", "#4fc3f7")
            anchor_camera()
            skip_walk = self.walked
            if already_running and not skip_walk:
                print("[Raid] Auto Start already began the match - skipping the walk, placing units now.")
                skip_walk = True
            self._phase("PLAYING TIMELINE", "#2e7d32")
            print(f"[Raid] Playing '{self.preset_name}'{' (no walk - already at the spot)' if skip_walk else ''}...")
            played = play_preset(self.raid_key, self.act, self.preset_name, skip_movement=skip_walk)
            if played == "RECONNECTED":
                return played
            if not played:
                if config.STOP_REQUESTED:
                    return False
                print(f"[Raid] The recording '{self.preset_name}' couldn't be played (missing or empty).")
                return _halt("RAID PRESET MISSING")
            self.walked = True
            press_start_after_macro()

        SESSION.match_started()
        self._match_started_at = time.time()
        return True

    # --- the match ------------------------------------------------------------------------
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

    def _count(self, result):
        if result == "DEFEAT":
            self.consecutive_defeats += 1
            SESSION.defeat()
            print(f"[Raid] Defeat ({self.consecutive_defeats} in a row).")
            if config.defeat_limit_reached(self.consecutive_defeats):
                health.save_debug_screenshot("raid_too_many_defeats")
                print(f"[Raid] {self.consecutive_defeats} losses in a row - stopping, the team can't clear this raid.")
                return _halt("TOO MANY DEFEATS (RAID)")
        else:
            self.consecutive_defeats = 0
            SESSION.victory()
            print("[Raid] Victory!")
        return True

    def play_match(self):
        """
        Waits for the match to end and counts it. "ENDED" / False / "RECONNECTED".

        The banner is the usual signal, but it's animated and with the game's Auto Retry on
        it barely shows - so Repeat Stage, the Game Results button, or the stage coming back
        to Start Game after the match was seen running also end the wait.
        """
        self._phase("WAITING FOR MATCH END", "#4fc3f7")
        started = self._match_started_at or time.time()
        seen_running = [False]
        last_results_click = [0.0]
        stage_seen = [time.time()]
        warned_long = [False]

        def other_end(shot):
            now = time.time()
            start_game_up = _find(config.START_GAME_BTN, shot)
            if not start_game_up:
                seen_running[0] = True
            if start_game_up or _find(config.AUTOPLAY_ON_BTN, shot) or _find(config.AUTOPLAY_OFF_BTN, shot):
                stage_seen[0] = now
            elif now - stage_seen[0] >= OFF_STAGE_LIMIT:
                return "OFF_STAGE"
            if not warned_long[0] and now - started >= self.match_stuck_timeout:
                warned_long[0] = True
                path = health.save_debug_screenshot("raid_long_match")
                print(f"[Raid] No result seen for {self.match_stuck_timeout / 60:.0f} minutes, but the stage is "
                      f"still up - the game's Auto Retry may be hiding the banner. Carrying on (screen saved: {path}).")
            if time.time() - started < RAID_MIN_MATCH:
                return None
            if _find(config.REPEAT_STAGE_BTN, shot):
                return "SCREEN"
            results_btn = _find(config.GAME_RESULTS_BTN, shot)
            if results_btn and seen_running[0] and time.time() - last_results_click[0] > 6.0:
                # The results popup was closed before it was read - reopen it, the next
                # tick reads the banner / Repeat Stage. Only here, never while picking a
                # menu: this click on the wrong screen opens View Party.
                last_results_click[0] = time.time()
                print("[Raid] The results were closed - reopening them (Game Results).")
                click_at(results_btn[0], results_btn[1])
                return None
            if start_game_up:
                if seen_running[0]:
                    return "RETRIED"
                print("[Raid] Start Game is still waiting to be pressed - pressing it.")
                click_start_game()
            return None

        print("[Raid] Waiting for the match to end...")
        result = poll_until([target(config.VICTORY_TEXT, "VICTORY", debug_label="victory"),
                             target(config.DEFEAT_TEXT, "DEFEAT", debug_label="defeat")],
                            interval=1.0, label="raid_match_result",
                            stuck_timeout=None, custom_check=other_end,
                            lobby_grace=8.0, game_results=False)
        if result in (False, "RECONNECTED"):
            return result
        if result == "OFF_STAGE":
            path = health.save_debug_screenshot("raid_off_stage")
            print(f"[Raid] The stage hasn't been on screen for {OFF_STAGE_LIMIT / 60:.0f} minutes (screen saved: "
                  f"{path}) - getting back onto it.")
            return "RECONNECTED"
        if result == "SCREEN":
            result = self._read_result()
        elif result == "RETRIED":
            print("[Raid] The game's Auto Retry already brought up the next Start Game - counting a win.")
            result = "VICTORY"
            self.resumed = True
        if not self._count(result):
            return False
        return "ENDED"

    # --- after the match ------------------------------------------------------------------
    def _click_repeat(self):
        """ONE click on Repeat Stage once it's settled (a double click can land on View Party)."""
        time.sleep(1.0)
        match = _find(config.REPEAT_STAGE_BTN)
        if not match:
            return True
        print("[Raid] Clicking Repeat Stage.")
        return click_until_gone(config.REPEAT_STAGE_BTN, match, "raid_repeat", clicks=1)

    def after_match(self):
        """
        Repeat the raid. "START" (Start Game / the stage is next) / "IN_MATCH" (the game's
        Auto Retry + Auto Start already began the next match) / "LOST" (nothing
        recognisable - navigate() works it out) / False / "RECONNECTED".
        """
        self._phase("REPEATING RAID")
        ended_at = time.time()
        quiet = [0]

        def watch(shot):
            # Auto Retry: the stage is back without anyone clicking.
            if _find(config.START_GAME_BTN, shot):
                return "START"
            banner = _find(config.VICTORY_TEXT, shot) or _find(config.DEFEAT_TEXT, shot)
            if not banner and (_find(config.AUTOPLAY_ON_BTN, shot) or _find(config.AUTOPLAY_OFF_BTN, shot)):
                quiet[0] += 1
                # A few looks with the stage up and no end screen at all: the next match is running.
                if quiet[0] >= 4 and time.time() - ended_at > 6.0 and not _find(config.GAME_RESULTS_BTN, shot):
                    return "IN_MATCH"
            else:
                quiet[0] = 0
            return None

        result = None
        reopened = False
        for _ in range(3):
            result = poll_until([target(config.REPEAT_STAGE_BTN, "REPEAT", debug_label="raid_repeat_stage"),
                                 target(config.START_PARTY_BTN, "PARTY", debug_label="raid_party_start")],
                                interval=1.0, label="raid_repeat_wait", timeout=AFTER_MATCH_WAIT / 3,
                                stuck_timeout=None, custom_check=watch, lobby_grace=25.0, game_results=False)
            if result != "TIMEOUT" or config.STOP_REQUESTED:
                break
            results_btn = _find(config.GAME_RESULTS_BTN)
            if results_btn and not reopened:
                print("[Raid] No Repeat Stage - reopening the results with Game Results.")
                click_at(results_btn[0], results_btn[1])
                reopened = True
                time.sleep(1.5)

        if result in (False, "RECONNECTED"):
            return result
        if result == "REPEAT":
            if not self._click_repeat():
                path = health.save_debug_screenshot("raid_repeat_stuck")
                print(f"[Raid] Repeat Stage is still on screen after clicking (screen saved: {path}).")
                return "LOST"
            return "START"
        if result == "PARTY":
            # A stray click opened the party screen - starting from it is the same raid.
            print("[Raid] Ended up on the party screen - starting the raid from there.")
            started = self._start_from_party()
            return "START" if started is True else started
        if result == "START":
            print("[Raid] The game's Auto Retry already brought the stage back.")
            return "START"
        if result == "IN_MATCH":
            print("[Raid] The game already started the next match (Auto Retry + Auto Start).")
            self._match_started_at = time.time()
            SESSION.match_started()
            return "IN_MATCH"
        path = health.save_debug_screenshot("raid_no_repeat")
        print(f"[Raid] No Repeat Stage or Start Game after the match (screen saved: {path}) - working out where we are.")
        return "LOST"
