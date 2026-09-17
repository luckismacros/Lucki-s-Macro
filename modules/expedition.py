# modules/expedition.py
"""
Expeditions: lobby navigation, the pre-start routine, and the in-run loop.

The flow, as described by the player (2026-09-13):

  Lobby:     Play -> Expeditions card -> expedition map card -> Increase Difficulty
             (difficulty - 1 clicks, on the spot found before the first click, since
             the button's background changes colour once clicked) -> Select Stage ->
             Start. Repeat Stage keeps the difficulty, so this only runs from the lobby.

  Pre-start: Expedition Map -> pick the route for the chosen material -> Back ->
             anchor camera -> unit macro ONCE -> Start Game -> Continue, Continue.

  In run:    react to whatever appears -
               Select an upgrade!   -> click the middle of the screen (cards barely matter)
               Continue popup       -> first one after Start Game: Continue.
                                       Checkpoint (Extract visible): Continue, or Extract
                                       at config.EXPEDITION_EXTRACT_AT_CHECKPOINT.
                                       Encounter (no Extract / Encounter signal): Continue.
                                       Each is followed by a second confirm button.
               Start Game           -> (defense nodes) if unit cards are in the hotbar,
                                       replay the macro once; then click it
               Repeat Stage         -> run over; back to pre-start

Units: the macro plays once before the run starts. Reaching a defense point can remove
some placed units - they go back into the hotbar - so before every later Start Game the
hotbar is checked for unit cards (their "Lvl" tag) and the macro replays only if any
are there. Replaying unconditionally worked but cost ~40s per defense for nothing when
every unit was still on the field (the macro replays at its recorded pace).

The check is tuned from real numbers: a single-size search read 0.71 on a live defense
screen that had units in the bar, too low for the original 0.80 bar. It now searches
only the bottom of the screen, at a few sizes, best of a few looks, against
UNIT_TAG_MIN_CONFIDENCE - screens without unit cards topped out at 0.52 that way.

Every non-user failure path goes through _halt(), which sets config.STUCK_DETECTED -
the rule that stops a real problem from being reported as a clean "Run finished".
"""
import time

import cv2

import config
from vision import capture_screen, find_template, _load_template
from input_controller import click_at, anchor_camera
from modules.polling import poll_until, target, settle_match, click_until_gone
from modules.gamemode_select import click_play, _wait_and_click, _run_steps, _sweep_carousel
from modules.match_start import click_start_party
from modules.stage_player import play_preset
from modules.stats import SESSION
from modules import expedition_map, health

# A Continue popup seen again, with no frame in between that lacked one, is the SAME
# popup (the click didn't register, or it is fading out) and must not be counted twice.
# The "no frame in between" part is what decides it - see play_run() and
# _on_quiet_tick(), which forget the last popup the moment any other screen shows. A
# time window alone was tried first and was wrong: a scripted run showed a fast
# encounter right after the start popup being mistaken for it, which would have shifted
# every checkpoint by one. This is only the outer bound for that memory.
SAME_POPUP_SECONDS = 60.0

# Hotbar unit check (see the module docstring). Measured: unit cards 1.00 on the full
# bar and on a 2-card bar, 0.71 live with the older single-size search; no unit cards
# 0.45 on the empty bar and <= 0.52 on the bottom of every map screen.
UNIT_TAG_MIN_CONFIDENCE = 0.64
UNIT_TAG_SCALES = (0.90, 0.95, 1.00, 1.05, 1.10)
UNIT_BAR_TOP_FRACTION = 0.70      # only the bottom 30% of the screen is searched
UNIT_CHECK_LOOKS = 3              # best of this many captures, 0.2s apart
# A reading this close to the line saves the frame (at most UNIT_CHECK_DEBUG_MAX per
# session) so the threshold can be tuned from what the bot actually saw.
UNIT_CHECK_DEBUG_ZONE = (0.55, 0.80)
UNIT_CHECK_DEBUG_MAX = 10


def _unit_tag_confidence(screenshot):
    """Best "Lvl" tag score in the bottom of the screen, over a few sizes."""
    try:
        tag = _load_template(config.EXP_UNIT_LVL_TAG)
    except FileNotFoundError:
        print(f"[Expedition] Missing template {config.EXP_UNIT_LVL_TAG}.")
        return 0.0
    region = screenshot[int(screenshot.shape[0] * UNIT_BAR_TOP_FRACTION):]
    best = 0.0
    for scale in UNIT_TAG_SCALES:
        t = tag if scale == 1.0 else cv2.resize(
            tag, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC if scale > 1.0 else cv2.INTER_AREA)
        if t.shape[0] > region.shape[0] or t.shape[1] > region.shape[1]:
            continue
        best = max(best, float(cv2.minMaxLoc(cv2.matchTemplate(region, t, cv2.TM_CCOEFF_NORMED))[1]))
    return best


def _halt(reason):
    """Stops the run as a fault (not a user stop), unless the user already stopped it."""
    if not config.STOP_REQUESTED:
        config.STUCK_DETECTED = reason
    config.STOP_REQUESTED = True
    return False


def _find(template, screenshot=None, debug_label=None):
    shot = screenshot if screenshot is not None else capture_screen()
    return find_template(shot, template, config.MATCH_THRESHOLD, debug_label=debug_label)


# --- Lobby navigation ----------------------------------------------------------------

# Scroll anchors for click_expeditions_card()'s carousel-sweep fallback: any OTHER
# mode card that's actually on screen (see gamemode_select._carousel_anchor()).
# Expeditions was added to this row after Story/Raid/Challenge and can sit further
# along it - a smaller window or a different card order can leave it scrolled out of
# view by default, which those three were never seen to need a sweep for.
_MODE_CARD_ANCHORS = {
    "story": {"template": config.STORY_CARD, "enabled": True},
    "raid": {"template": config.RAID_CARD, "enabled": True},
    "challenge": {"template": config.CHALLENGE_CARD, "enabled": True},
}


def click_expeditions_card():
    """
    The lobby's Expeditions mode card. Same _wait_and_click every other mode card
    uses; if that plain check times out, falls back to sweeping the mode-card row
    the same way click_map()/click_raid() sweep their own carousels, in case
    Expeditions simply isn't scrolled into view on this screen. Reported live as
    "presses Play and does nothing else" - the same symptom a card sitting off the
    right edge of the row would produce.
    """
    result = _wait_and_click(config.EXPEDITIONS_CARD, "click_expeditions_card", recover_leftover_party=True)
    if result is not False or config.STOP_REQUESTED:
        return result

    print("[Expedition] Expeditions card not found where expected - checking whether it needs scrolling into view...")
    match = _sweep_carousel(config.EXPEDITIONS_CARD, "click_expeditions_card", _MODE_CARD_ANCHORS, "expeditions")
    if not match:
        health.report_missing_template(capture_screen(), "click_expeditions_card", config.EXPEDITIONS_CARD)
        return False

    match = settle_match(config.EXPEDITIONS_CARD, match, "click_expeditions_card") or match
    x, y, conf = match
    print(f"[Expedition] Found the Expeditions card at ({x}, {y}) after scrolling, confidence={conf:.2f} - clicking.")
    click_at(x, y)
    time.sleep(0.8)
    return True


def click_expedition(expedition_key):
    data = config.EXPEDITIONS.get(expedition_key)
    if not data or not data["enabled"]:
        print(f"[Expedition] Unknown or disabled expedition '{expedition_key}'.")
        return False

    template, label = data["template"], f"expedition_{expedition_key}"
    result = poll_until([target(template, "FOUND", debug_label=label)],
                        interval=0.5, label=label, timeout=5.0, stuck_timeout=None)
    if result in ("RECONNECTED", False):
        return result

    match = _find(template) if result == "FOUND" else None
    if not match:
        match = _sweep_carousel(template, label, config.EXPEDITIONS, expedition_key)
    if not match:
        print(f"[Expedition] Could not find the '{data['label']}' card.")
        health.report_missing_template(capture_screen(), label, template)
        return False

    match = settle_match(template, match, label) or match
    x, y, conf = match
    print(f"[Expedition] Clicking '{data['label']}' at ({x}, {y}), confidence={conf:.2f}.")
    click_at(x, y)
    time.sleep(0.8)
    return True


def set_difficulty(difficulty):
    """
    Difficulty N = N-1 clicks on Increase Difficulty. The button is located ONCE and
    that same spot is clicked each time: its background changes colour after a click,
    which can make a fresh match miss it.
    """
    clicks = max(0, int(difficulty) - 1)
    if clicks == 0:
        return True

    result = poll_until([target(config.EXP_INCREASE_DIFFICULTY, "FOUND", debug_label="increase_difficulty")],
                        interval=0.5, label="increase_difficulty", timeout=6.0, stuck_timeout=None)
    if result in ("RECONNECTED", False):
        return result
    match = _find(config.EXP_INCREASE_DIFFICULTY) if result == "FOUND" else None
    if not match:
        print("[Expedition] Could not find the Increase Difficulty button.")
        health.report_missing_template(capture_screen(), "increase_difficulty", config.EXP_INCREASE_DIFFICULTY)
        return False

    # Settled before the first click only - after that the same spot is reused on purpose.
    match = settle_match(config.EXP_INCREASE_DIFFICULTY, match, "increase_difficulty") or match
    x, y, conf = match
    print(f"[Expedition] Increase Difficulty at ({x}, {y}), confidence={conf:.2f} - "
          f"clicking {clicks}x for difficulty {difficulty}.")
    for _ in range(clicks):
        if config.STOP_REQUESTED:
            return False
        click_at(x, y)
        time.sleep(0.5)
    return True


def click_expedition_select_stage():
    return _wait_and_click(config.EXP_SELECT_STAGE_BTN, "click_expedition_select_stage")


def click_expedition_start():
    """The party screen's Start - same button as Story/Raids/Portals."""
    result = poll_until([target(config.START_PARTY_BTN, True, debug_label="start_party_btn")],
                        interval=0.5, label="expedition_start", timeout=config.TIMEOUT_SECONDS,
                        stuck_timeout=None)
    if result in ("RECONNECTED", False):
        return result
    if result == "TIMEOUT":
        print("[Expedition] The Start button never appeared after Select Stage.")
        return False
    return click_start_party()


def run_expedition_flow(expedition_key, difficulty):
    """Lobby -> party screen -> Start. True / False / "RECONNECTED"."""
    return _run_steps([
        click_play,
        click_expeditions_card,
        lambda: click_expedition(expedition_key),
        lambda: set_difficulty(difficulty),
        click_expedition_select_stage,
        click_expedition_start,
    ])


# --- The run -------------------------------------------------------------------------

class ExpeditionRunner:
    def __init__(self, expedition_key, difficulty, material, preset_name, set_phase=None,
                 zoom_out_steps=None):
        self.expedition_key = expedition_key
        self.zoom_out_steps = zoom_out_steps
        self.difficulty = difficulty
        self.material = material
        self.preset_name = preset_name
        self.set_phase = set_phase
        self.consecutive_defeats = 0
        self._unit_debug_saved = 0
        self._reset_run_state()

    def _reset_run_state(self):
        self.checkpoints = 0
        self.start_popup_pending = False
        self.extracted = False
        self.defeat_counted = False
        self._last_popup_at = 0.0
        self._last_popup_kind = None
        # Last time a click actually took (or a run event was handled). The per-poll stuck
        # timer restarts every time a popup is seen, so a button that is seen but never
        # responds would otherwise be retried forever - see play_run().
        self._last_progress = time.time()

    def _phase(self, text, color="#4fc3f7"):
        if self.set_phase:
            self.set_phase(text, color)

    # State detection when the bot is started somewhere other than the lobby.
    def detect_state(self, screenshot):
        if find_template(screenshot, config.PLAY_BTN, config.MATCH_THRESHOLD):
            return "LOBBY"
        if find_template(screenshot, config.EXP_REPEAT_STAGE_BTN, config.MATCH_THRESHOLD):
            return "REPEAT"
        if (find_template(screenshot, config.START_GAME_BTN, config.MATCH_THRESHOLD)
                and find_template(screenshot, config.EXP_MAP_BTN, config.MATCH_THRESHOLD)):
            return "PRESTART"
        return "INRUN"

    def click_repeat_if_present(self):
        """True once Repeat Stage is confirmed gone (clicked, or was never there to
        begin with - nothing to do isn't a failure); False if it's still stuck on
        screen after every click attempt."""
        match = _find(config.EXP_REPEAT_STAGE_BTN)
        if not match:
            return True
        clicked = click_until_gone(config.EXP_REPEAT_STAGE_BTN, match, "expedition_repeat", clicks=2)
        time.sleep(1.0)
        return clicked

    def navigate(self):
        self._phase("NAVIGATING MENUS")
        label = config.EXPEDITIONS[self.expedition_key]["label"]
        print(f"[Expedition] Navigating to {label} (difficulty {self.difficulty})...")
        return run_expedition_flow(self.expedition_key, self.difficulty)

    # --- units ---------------------------------------------------------------------
    def _units_in_bar(self):
        """True when at least one unit card is still in the hotbar."""
        best, best_shot = 0.0, None
        for look in range(UNIT_CHECK_LOOKS):
            shot = capture_screen()
            confidence = _unit_tag_confidence(shot)
            if confidence > best:
                best, best_shot = confidence, shot
            if best >= UNIT_TAG_MIN_CONFIDENCE:
                break
            if look < UNIT_CHECK_LOOKS - 1:
                time.sleep(0.2)

        present = best >= UNIT_TAG_MIN_CONFIDENCE
        print(f"[Expedition] Unit cards in the hotbar: {'yes' if present else 'no'} "
              f"(Lvl tag confidence {best:.2f}, need {UNIT_TAG_MIN_CONFIDENCE:.2f}).")
        if (UNIT_CHECK_DEBUG_ZONE[0] <= best <= UNIT_CHECK_DEBUG_ZONE[1]
                and self._unit_debug_saved < UNIT_CHECK_DEBUG_MAX and best_shot is not None):
            self._unit_debug_saved += 1
            path = health.save_debug_screenshot(f"expedition_unit_check_{best:.2f}", best_shot)
            print(f"[Expedition] That reading is close to the line - saved the frame for tuning: {path}")
        return present

    def _play_unit_macro(self):
        """Plays the unit macro once. True / False / "RECONNECTED"."""
        self._phase("PLACING UNITS", "#2e7d32")
        print(f"[Expedition] Playing unit macro '{self.preset_name}'.")
        result = play_preset(config.EXPEDITION_PRESET_LOCATION, config.EXPEDITION_PRESET_VARIANT,
                             self.preset_name)
        if result == "RECONNECTED" or result is True:
            return result
        if config.STOP_REQUESTED:
            return False
        print(f"[Expedition] The unit macro '{self.preset_name}' is missing or empty - "
              f"record it first (F8 in-game).")
        return _halt("UNIT MACRO MISSING")

    def _click_start_game(self, timeout):
        """Waits for Start Game, clicks it once settled, re-clicks if it stays. True / False / "RECONNECTED" / "TIMEOUT"."""
        result = poll_until([target(config.START_GAME_BTN, True, debug_label="start_game_btn")],
                            interval=0.5, label="expedition_start_game", timeout=timeout, stuck_timeout=None)
        if result is not True:
            return result
        match = _find(config.START_GAME_BTN)
        if match and not click_until_gone(config.START_GAME_BTN, match, "expedition_start_game", clicks=2):
            if config.STOP_REQUESTED:
                return False
        return True

    # --- pre-start -----------------------------------------------------------------
    def prepare_and_start(self):
        """Stage load -> route -> camera -> units -> Start Game. True / False / "RECONNECTED"."""
        self._reset_run_state()

        self._phase("WAITING FOR STAGE")
        print("[Expedition] Waiting for the stage to load (Expedition Map button)...")
        result = poll_until([target(config.EXP_MAP_BTN, True, debug_label="expedition_map_btn")],
                            interval=1.0, label="expedition_stage_load",
                            stuck_timeout=config.STUCK_TIMEOUT_MENU, lobby_grace=25.0)
        if result is not True:
            return result
        time.sleep(1.0)

        if self.material != config.EXPEDITION_RANDOM_MATERIAL:
            self._phase("PICKING ROUTE", "#2e7d32")
            opened = expedition_map.open_map()
            if opened in ("RECONNECTED", False):
                return opened
            if opened == "NOT_OPENED":
                path = health.save_debug_screenshot("expedition_map_not_opened")
                print(f"[Expedition] WARNING: the Expedition Map didn't open - playing the default "
                      f"route this run (screen saved: {path}).")
            else:
                if not expedition_map.choose_route(self.material):
                    return False
                if not expedition_map.close_map():
                    health.save_debug_screenshot("expedition_map_stuck_open")
                    print("[Expedition] The Expedition Map wouldn't close.")
                    return _halt("EXPEDITION MAP WOULD NOT CLOSE")
        else:
            print("[Expedition] Material is Random - skipping route picking.")

        if config.STOP_REQUESTED:
            return False
        self._phase("ANCHORING CAMERA")
        anchor_camera(zoom_out_steps=self.zoom_out_steps)

        result = self._play_unit_macro()
        if result is not True:
            return result

        self._phase("STARTING RUN")
        result = self._click_start_game(timeout=20.0)
        if result in ("RECONNECTED", False):
            return result
        if result == "TIMEOUT":
            print("[Expedition] WARNING: Start Game never appeared - carrying on and watching the run; "
                  "the stuck timer will stop things if nothing happens.")
        else:
            print("[Expedition] Start Game clicked.")

        SESSION.match_started()
        self.start_popup_pending = True
        return True

    # --- in run --------------------------------------------------------------------
    def _on_extracted(self):
        if self.extracted:
            return
        self.extracted = True
        self.consecutive_defeats = 0
        SESSION.victory()
        print("[Expedition] Extracted - run complete.")

    def _press(self, button_template, match, confirm_template, name, on_confirmed=None):
        """
        Clicks a popup button, then its second confirm button - each only once it has
        stopped moving, and each re-clicked if the click didn't take.
        """
        if click_until_gone(button_template, match, f"expedition_{name}", success_template=confirm_template):
            self._last_progress = time.time()
        if config.STOP_REQUESTED:
            return False

        result = poll_until([target(confirm_template, True, debug_label=f"after_{name}")],
                            interval=0.3, label=f"expedition_after_{name}", timeout=6.0, stuck_timeout=None)
        if result in ("RECONNECTED", False):
            return result
        if result == "TIMEOUT":
            print(f"[Expedition] No second '{name}' button appeared - the run loop will click it if it shows up late.")
        else:
            confirm = _find(confirm_template)
            if confirm is None or click_until_gone(confirm_template, confirm, f"expedition_after_{name}"):
                # Confirmed, so this popup is finished: the next Continue seen is a new
                # popup even if it shows up immediately. A scripted run had a checkpoint
                # right after an encounter's confirm, and without this it was taken for
                # that same encounter - which shifts every later checkpoint by one.
                self._last_popup_kind = None
                self._last_progress = time.time()
            if config.STOP_REQUESTED:
                return False
            if on_confirmed:
                on_confirmed()
        time.sleep(1.0)
        return True

    def _handle_continue_popup(self):
        shot = capture_screen()
        cont = _find(config.EXP_CONTINUE_BTN, shot)
        if not cont:
            return True
        extract = _find(config.EXP_EXTRACT_BTN, shot)
        encounter = _find(config.EXP_ENCOUNTER_SIGNAL, shot)
        now = time.time()

        if self._last_popup_kind and now - self._last_popup_at < SAME_POPUP_SECONDS:
            kind = self._last_popup_kind          # same popup as a moment ago - don't recount
        elif self.start_popup_pending:
            self.start_popup_pending = False
            kind = "start"
        elif extract and not encounter:
            self.checkpoints += 1
            kind = "checkpoint"
        else:
            kind = "encounter"
        self._last_popup_kind, self._last_popup_at = kind, now

        if kind == "checkpoint" and extract and self.checkpoints >= config.EXPEDITION_EXTRACT_AT_CHECKPOINT:
            print(f"[Expedition] Checkpoint {self.checkpoints} - extracting.")
            self._phase("EXTRACTING", "#2e7d32")
            return self._press(config.EXP_EXTRACT_BTN, extract, config.EXP_AFTER_EXTRACT_BTN, "extract",
                               on_confirmed=self._on_extracted)

        if kind == "checkpoint":
            print(f"[Expedition] Checkpoint {self.checkpoints} - continuing "
                  f"(extracting at {config.EXPEDITION_EXTRACT_AT_CHECKPOINT}).")
        elif kind == "start":
            print("[Expedition] Run start popup - continuing.")
        else:
            print("[Expedition] Encounter - continuing.")
        return self._press(config.EXP_CONTINUE_BTN, cont, config.EXP_AFTER_CONTINUE_BTN, "continue")

    def _on_quiet_tick(self, screenshot):
        """
        poll_until custom_check. It only runs on a tick where no target matched - so no
        Continue popup is up, and the next one seen is a new popup. Never ends the poll.
        """
        self._last_popup_kind = None
        return None

    def play_run(self):
        """
        Runs until Repeat Stage is clicked. Returns "REPEAT" / False / "RECONNECTED".
        """
        self._phase("RUNNING EXPEDITION", "#2e7d32")
        self._last_progress = time.time()
        targets = [
            target(config.EXP_SELECT_UPGRADE_TEXT, "UPGRADE"),
            target(config.EXP_CLOSE_BTN, "CLOSE"),
            target(config.EXP_CONTINUE_BTN, "CONTINUE"),
            target(config.EXP_AFTER_EXTRACT_BTN, "AFTER_EXTRACT"),
            target(config.EXP_AFTER_CONTINUE_BTN, "AFTER_CONTINUE"),
            target(config.START_GAME_BTN, "START_GAME"),
            target(config.EXP_REPEAT_STAGE_BTN, "REPEAT"),
            target(config.DEFEAT_TEXT, "DEFEAT"),
        ]

        while not config.STOP_REQUESTED:
            if time.time() - self._last_progress > config.EXPEDITION_STUCK_TIMEOUT:
                path = health.save_debug_screenshot("expedition_no_progress")
                print(f"[Expedition] Nothing has worked for {int(config.EXPEDITION_STUCK_TIMEOUT // 60)} "
                      f"minutes - buttons keep being seen but no click takes. Stopping "
                      f"(screen saved: {path}).")
                return _halt("STUCK - BUTTONS NOT RESPONDING")
            result = poll_until(targets, interval=1.0, label="expedition_run",
                                stuck_timeout=config.EXPEDITION_STUCK_TIMEOUT,
                                custom_check=self._on_quiet_tick, lobby_grace=8.0)
            if result in (False, "RECONNECTED"):
                return result
            if result != "CONTINUE":
                self._last_popup_kind = None  # a different screen: the old popup is gone

            if result == "UPGRADE":
                match = _find(config.EXP_SELECT_UPGRADE_TEXT)
                if match:
                    print("[Expedition] Upgrade cards - clicking the middle of the screen.")
                    if click_until_gone(config.EXP_SELECT_UPGRADE_TEXT, match, "expedition_upgrade",
                                        point=(config.REFERENCE_WIDTH // 2, config.REFERENCE_HEIGHT // 2)):
                        self._last_progress = time.time()
                    time.sleep(0.5)

            elif result == "CLOSE":
                match = _find(config.EXP_CLOSE_BTN)
                if match:
                    if click_until_gone(config.EXP_CLOSE_BTN, match, "expedition_close"):
                        self._last_progress = time.time()
                    time.sleep(0.5)

            elif result == "CONTINUE":
                outcome = self._handle_continue_popup()
                if outcome is not True:
                    return outcome
                self._phase("RUNNING EXPEDITION", "#2e7d32")

            elif result in ("AFTER_CONTINUE", "AFTER_EXTRACT"):
                template = config.EXP_AFTER_EXTRACT_BTN if result == "AFTER_EXTRACT" else config.EXP_AFTER_CONTINUE_BTN
                match = _find(template)
                if match:
                    if click_until_gone(template, match, f"expedition_{result.lower()}"):
                        self._last_progress = time.time()
                    if result == "AFTER_EXTRACT" and not config.STOP_REQUESTED:
                        self._on_extracted()
                    time.sleep(1.0)

            elif result == "START_GAME":
                # A Start Game after the run began (a defense node): reaching it can
                # remove placed units, which go back to the hotbar. Put them back
                # before the wave starts - but only if there is anything to put back.
                if self._units_in_bar():
                    print("[Expedition] Defense Start Game - units are back in the hotbar, replaying the macro first.")
                    outcome = self._play_unit_macro()
                    if outcome is not True:
                        return outcome
                else:
                    print("[Expedition] Defense Start Game - hotbar is empty, starting straight away.")
                outcome = self._click_start_game(timeout=8.0)
                if outcome in (False, "RECONNECTED"):
                    return outcome
                self._last_progress = time.time()
                self._phase("RUNNING EXPEDITION", "#2e7d32")

            elif result == "DEFEAT":
                if not self.defeat_counted:
                    self.defeat_counted = True
                    self._last_progress = time.time()
                    self.consecutive_defeats += 1
                    SESSION.defeat()
                    print(f"[Expedition] Defeat ({self.consecutive_defeats} in a row).")
                    if config.defeat_limit_reached(self.consecutive_defeats):
                        health.save_debug_screenshot("too_many_defeats")
                        print(f"[Expedition] {self.consecutive_defeats} defeats in a row - stopping; the "
                              f"unit macro can't hold this difficulty.")
                        return _halt("TOO MANY DEFEATS")
                time.sleep(2.0)

            elif result == "REPEAT":
                match = _find(config.EXP_REPEAT_STAGE_BTN)
                if match:
                    if not self.extracted and not self.defeat_counted:
                        print("[Expedition] Run ended without an extraction being seen.")
                    print("[Expedition] Repeat Stage available.")
                    # Deliberately NOT clicked here - see click_repeat_if_present() and
                    # engine.run_expedition()'s own comment on why. Clicking on the spot
                    # used to start loading a brand new stage before the run's own match
                    # limit was even checked - a Queue step whose limit landed right on
                    # this exact tick would then stop with that half-loaded stage still
                    # on screen, which modules/lobby.return_to_lobby() has no way to
                    # recognise (Repeat Stage - the one button it DOES know how to Exit
                    # from - was already gone, clicked out from under it). Leaving the
                    # click to the caller means a stopped run leaves Repeat Stage
                    # genuinely still there to be seen and exited normally.
                    return "REPEAT"

        return False
