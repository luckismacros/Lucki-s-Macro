# modules/sho.py
"""
Sho Farm (Others page, added 2026-11-01): farm the Infernal story stage for Torn Photos,
then turn 16 of them in at an NPC on Flaming Monastery for the Sho unit.

As described by the player (2026-11-01):
  Farming: Play -> Story card -> the map whose card carries the "Infernal" tag (the
  Infernal stage moves from map to map, so the tag is looked for every time) -> the fire
  tile at the top of the act list (not 1-5 / Infinite / Master) -> Select Stage -> Start
  -> Auto Play on -> Start Game -> finish -> Repeat Stage. The game's Auto Start and Auto
  Retry must be OFF - every match ends on the victory panel, where the rewards are read.
  Every win pays one Torn Photo (on the victory panel, above Repeat Stage). 16 = 1 Sho.
  When to turn in: after 16 counted wins, or as soon as the Torn Photo reward shows the
  red outline + warning triangle (the photo limit is reached) - whichever comes first.
  Turning in: Close (the victory panel's X) -> small Play -> Change Gamemode -> Story ->
  Flaming Monastery (scrolled to) -> Act 1 -> Select Stage -> Start. On the stage, NOT
  Start Game: fix the camera, play the recorded walk to the NPC, press E. Nothing opens ->
  Settings -> Teleport to Spawn and walk again. The NPC's panel opens:
    - Turn In greyed out (No_Turn_in.png): not 16 photos yet - back to farming.
    - otherwise: click Turn In, the unit arrives ("click anywhere to close" - clicked if
      it shows), then back to the lobby and farming starts over.

Fallbacks:
  - A win whose rewards show no Torn Photo means the Infernal stage has moved to another
    map (Repeat Stage replays the old one): the macro goes back through the menus to find
    the tag again. If the very next win shows no photo either, it's the photo that can't
    be seen, not the stage - from then on every win is counted without looking.
  - The count is saved to disk (sho_farm.json), so a restart keeps it. If the NPC says
    "not yet" the count starts again from 0 and the limit warning decides.
  - If the limit warning was seen but the button reads greyed out, it is clicked anyway:
    a greyed out button does nothing, while a misread would otherwise loop forever.
  - Every menu click is checked (the next screen has to show, or it's clicked again);
    anything unexpected goes back through navigate(), which reads the screen.
"""
import json
import os
import time

import pydirectinput

import config
import settings
from vision import capture_screen, find_template, score_template
from input_controller import (anchor_camera, click_at, mark_input, ensure_roblox_focus, scroll_maps_left,
                              scroll_maps_right)
from modules.gamemode_select import (click_play, click_map, _find_within, _sweep_carousel, _carousel_anchor,
                                     _frames_differ, CAROUSEL_STEP_NOTCHES, CAROUSEL_MAX_STEPS)
from modules.match_start import click_start_party
from modules.polling import poll_until, target, click_until_gone, settle_match, click_stage_button
from modules.prompts import recover_from_leftover_party, dismiss_click_anywhere_if_present
from modules.raid import _step, STEP_ATTEMPTS
from modules.reconnect import handle_disconnect_if_present
from modules.stage_player import play_preset, wait_for_start_game, click_start_game
from modules.autoplay import ensure_autoplay_enabled
from modules.bossrush import BossRushRunner
from modules.stats import SESSION
from modules import health

TAG = "Sho"
# A match shorter than this can't have ended - end screens left over from the previous
# match are ignored until then.
MIN_MATCH = 25.0
# After a match: how long to wait for Repeat Stage before working out where the game is.
AFTER_MATCH_WAIT = 60.0
# The party screen's Start still up this long while waiting for the stage = the click
# didn't take; it's pressed again.
PARTY_NUDGE_SECONDS = 8.0
# While a match runs, the stage (its Auto Play button) gone this long = real trouble.
OFF_STAGE_LIMIT = 300.0
# Start Game has to be gone this many looks in a row (1s apart) before the match counts as
# running. One frame without it (it fades when clicked) used to be enough, so a Start Game
# click that didn't take - still up 25s later - read as the game's Auto Retry restarting
# the stage, and a win was counted that never happened (live 2026-11-01).
RUNNING_AFTER_LOOKS = 5
# How long the reward cards get to pop in on the victory panel before they're read.
REWARD_LOOK_SECONDS = 3.0
LOOK_REACH = 45            # ref px around the found card to score both looks
LIMIT_LOOK_MIN = 0.85      # the card must match the limit look at least this well
LIMIT_LOOK_MARGIN = 0.10   # ...and this much better than the normal look
# After clicking Turn In: how long the unit's popup gets to show.
OBTAIN_WAIT = 8.0
# After the unit: how long its "click anywhere" and the NPC's thank-you line get to show.
FINISH_WAIT = 12.0
# After Accept Quest: how long the Infernal Stage shortcut gets to show.
SHORTCUT_WAIT = 10.0
# A match running this much longer than the usual one (and at least OVERDUE_MIN) ended
# without its results being seen - the panel was closed. Live 2026-11-01: a match sat on
# "Waiting for the match to end" with no panel up until it was stopped by hand.
OVERDUE_FACTOR = 2.0
OVERDUE_MIN = 300.0
# ...then this long more for Game Results to bring the panel back before leaving the stage.
OVERDUE_GRACE = 15.0

_STORY_CARDS = (config.SHO_STORY_CARD, config.STORY_CARD)
_SELECT_STAGE = (config.SHO_SELECT_STAGE_BTN, config.SELECT_STAGE_BTN)
_SELECT_STAGE_FLAMING = (config.SHO_SELECT_STAGE_FLAMING_BTN, config.SHO_SELECT_STAGE_BTN, config.SELECT_STAGE_BTN)
_PARTY_STARTS = (config.SHO_START_BTN, config.SHO_START_FLAMING_BTN, config.START_PARTY_BTN)
_CLOSES = (config.SHO_CLOSE_BTN, config.CLOSE_BTN)
_PLAY_SMALLS = (config.SHO_PLAY_SMALL_BTN, config.PLAY_SMALL_BTN)
_CHANGE_GAMEMODES = (config.SHO_CHANGE_GAMEMODE_BTN, config.CHANGE_GAMEMODE_BTN, config.CHANGE_GAMEMODE_QUEUE_BTN)
_REPEATS = (config.REPEAT_STAGE_BTN, config.SHO_REPEAT_STAGE_BTN)
_INFERNAL_TAGS = (config.SHO_INFERNAL_TAG, config.SHO_INFERNAL_TAG_SMALL)
_TORN_PHOTOS = (config.SHO_TORN_PHOTO_NORMAL, config.SHO_TORN_PHOTO)
_DIALOGUES = (config.SHO_DIALOGUE_ARROW, config.SHO_DIALOGUE_BTN)

# The photo count and whether a trip to the NPC is due, kept at module level so a Never
# Stop restart (a new runner) carries on with them; the count is also saved to disk.
_STATE = {"loaded": False, "photos": 0, "turn_in_due": False, "limit_seen": False,
          "limit_misses": 0}      # trips in a row where the limit warning showed but Turn In stayed grey
# That many and the run stops: the warning and the button disagree, and going on would be
# one match and one trip to the NPC, forever.
LIMIT_MISSES_MAX = 2


def _find(template, shot=None, debug_label=None):
    shot = shot if shot is not None else capture_screen()
    return find_template(shot, template, config.MATCH_THRESHOLD, debug_label=debug_label)


def _find_any(templates, shot=None):
    """(template, match) for the best-scoring of `templates` on screen, or (None, None)."""
    shot = shot if shot is not None else capture_screen()
    best = (None, None)
    for t in templates:
        m = _find(t, shot)
        if m and (best[1] is None or m[2] > best[1][2]):
            best = (t, m)
    return best


def _any_up(templates, shot):
    return _find_any(templates, shot)[0] is not None


def _roi(shot, center, reach):
    """The part of the frame within `reach` reference px of `center`, and its top-left (screen px)."""
    s = config.render_scale()
    cx, cy, r = center[0] * s, center[1] * s, reach * s
    x0, y0 = max(0, int(cx - r)), max(0, int(cy - r))
    return shot[y0:int(cy + r), x0:int(cx + r)], (x0, y0)


def _score_near(shot, template, center, reach):
    area = _roi(shot, center, reach)[0]
    if area.size == 0:
        return 0.0
    found = score_template(area, template)
    return found[2] if found else 0.0


def _halt(reason):
    """Stops the run as a fault (not a user stop), unless the user already stopped it."""
    if not config.STOP_REQUESTED:
        config.STUCK_DETECTED = reason
    config.STOP_REQUESTED = True
    return False


def _map_templates():
    return [m["template"] for m in config.MAPS.values() if m.get("enabled")]


# --- The photo count, kept between runs ----------------------------------------------------

def _state_file():
    return os.path.join(settings.base_dir(), "sho_farm.json")


def _load_state():
    if _STATE["loaded"]:
        return
    _STATE["loaded"] = True
    try:
        with open(_state_file(), encoding="utf-8") as f:
            data = json.load(f)
        _STATE["photos"] = max(0, int(data.get("photos", 0)))
        _STATE["turn_in_due"] = bool(data.get("turn_in_due", False))
        _STATE["limit_seen"] = bool(data.get("limit_seen", False))
    except Exception:
        pass


def _save_state():
    try:
        with open(_state_file(), "w", encoding="utf-8") as f:
            json.dump({"photos": _STATE["photos"], "turn_in_due": _STATE["turn_in_due"],
                       "limit_seen": _STATE["limit_seen"]}, f)
    except Exception as e:
        print(f"[{TAG}] Couldn't save the Torn Photo count ({e}) - it starts from where it is next time.")


# --- Menu clicks ----------------------------------------------------------------------------

def _click_found(templates, label, clicks=1):
    """Clicks whichever of `templates` is on screen until it's gone. True / False."""
    template, match = _find_any(templates)
    if not match:
        return False
    return click_until_gone(template, match, label, clicks=clicks)


def _click_story_card():
    """The Story mode card - after disbanding a leftover party Play may have reopened."""
    recover_from_leftover_party(capture_screen(), click_play)
    return _click_found(_STORY_CARDS, "sho_story_card")


def _click_party_start():
    """
    The party screen's Start. The player's own crops are clicked where they're found; the
    standard Start Party button goes through click_start_party(), which knows its spot.
    True / False / "RECONNECTED".
    """
    template, match = _find_any(_PARTY_STARTS)
    if not match:
        return False
    if template == config.START_PARTY_BTN:
        return click_start_party()
    match = settle_match(template, match, "sho_start") or match
    print(f"[{TAG}] Pressing Start at ({match[0]}, {match[1]}), confidence={match[2]:.2f}.")
    return click_until_gone(template, match, "sho_start")


def _wait_for_menu(templates, seconds):
    """True once one of `templates` shows within `seconds`, None if not, or "RECONNECTED"."""
    deadline = time.time() + seconds
    while not config.STOP_REQUESTED:
        shot = capture_screen()
        if handle_disconnect_if_present(shot):
            return "RECONNECTED"
        if _any_up(templates, shot):
            return True
        if time.time() >= deadline:
            return None
        time.sleep(0.4)
    return None


def _press_e():
    ensure_roblox_focus()
    pydirectinput.press("e")
    mark_input()


# --- The run ---------------------------------------------------------------------------

class ShoRunner:
    def __init__(self, walk_preset_name, set_phase=None):
        _load_state()
        self.walk_preset_name = walk_preset_name
        self.set_phase = set_phase
        self.consecutive_defeats = 0
        self.match_stuck_timeout = config.match_stuck_timeout()
        # Whether the next Start Game wait is for a stage the character never left (a
        # Repeat Stage) - see wait_for_start_game(resumed=...).
        self.resumed = False
        self._match_started_at = 0.0
        self._last_result = None          # VICTORY / DEFEAT of the match that just ended, None = unknown
        self._no_photo_wins = 0           # wins in a row whose rewards showed no Torn Photo
        self._photo_unreadable = False    # the photo can't be seen at all - count wins without it
        self._warned_auto = False
        self._closed_stray_panel = False
        self._match_times = []            # how long the matches took - see OVERDUE_FACTOR
        self._tag_missing = False         # the last look for the Infernal tag found none
        self._infernal_missing = False    # every try of the last join found no Infernal tag
        self._quest_trips = 0             # trips to accept the quest since the last win
        # Borrowed for its Settings -> Teleport to Spawn routine (the same menu on every map).
        self._spawn = BossRushRunner(None, None, {}, set_phase=set_phase)
        print(f"[{TAG}] Torn Photos counted so far: {_STATE['photos']}/{config.SHO_PHOTOS_NEEDED}"
              f"{' - a turn-in is due' if _STATE['turn_in_due'] else ''}.")

    def _phase(self, text, color="#4fc3f7"):
        if self.set_phase:
            self.set_phase(text, color)

    # --- getting onto the Infernal stage -----------------------------------------------
    @staticmethod
    def _find_tag(shot):
        """The Infernal tag at either size: (template, match) for the better one, or (None, None)."""
        return _find_any(_INFERNAL_TAGS, shot)

    def _sweep_for_tag(self):
        """
        The map list from its start to its end, a step at a time, looking for the tag at either
        size (gamemode_select._sweep_carousel only takes one picture). (template, match) or
        (None, None).
        """
        anchor = _carousel_anchor(config.MAPS, None) or (config.MAP_CAROUSEL_HOVER_X, config.MAP_CAROUSEL_HOVER_Y)
        print(f"[{TAG}] The Infernal tag isn't in view - scrolling the map list (cursor at {anchor}).")
        scroll_maps_left(hover=anchor, notches=CAROUSEL_STEP_NOTCHES * CAROUSEL_MAX_STEPS)
        time.sleep(0.4)
        previous = capture_screen()
        for _ in range(CAROUSEL_MAX_STEPS):
            if config.STOP_REQUESTED:
                return None, None
            found = self._find_tag(previous)
            if found[1]:
                return found
            scroll_maps_right(hover=anchor, notches=CAROUSEL_STEP_NOTCHES)
            time.sleep(0.4)
            current = capture_screen()
            if not _frames_differ(previous, current):
                break                    # the end of the list
            previous = current
        return self._find_tag(capture_screen())

    def _click_infernal_map(self):
        """
        Finds the map card with the Infernal tag (scrolling if needed) and clicks its name. An
        act panel left open over the maps (the game reopens the last map's) is closed first -
        unless it already is the Infernal map's, then nothing needs clicking. True / False.
        """
        shot = capture_screen()
        if _find(config.SHO_STAGE_ICON, shot):
            print(f"[{TAG}] The Infernal map's act list is already open.")
            return True
        template, close = _find_any(_CLOSES, shot)
        if close and not self._find_tag(shot)[1]:
            print(f"[{TAG}] Another map's act list is open over the maps - closing it.")
            click_until_gone(template, close, "sho_close_act_panel")
            time.sleep(0.8)

        deadline = time.time() + 2.5
        template, match = None, None
        while not config.STOP_REQUESTED:
            template, match = self._find_tag(capture_screen())
            if match or time.time() >= deadline:
                break
            time.sleep(0.3)
        if not match:
            template, match = self._sweep_for_tag()
        self._tag_missing = not match
        if not match:
            path = health.save_debug_screenshot("sho_no_infernal_tag")
            print(f"[{TAG}] No map card with the Infernal tag (screen saved: {path}).")
            return False
        # The list glides after a scroll - click where the card stops.
        match = settle_match(template, match, "sho_infernal_tag") or match
        x, y = match[0], match[1] + config.SHO_INFERNAL_NAME_DY
        print(f"[{TAG}] The Infernal stage is the map at ({match[0]}, {match[1]}), confidence={match[2]:.2f} - "
              f"clicking its name.")
        click_at(x, y)
        return True

    def _click_stage_icon(self):
        """The fire tile at the top of the act list. True / False."""
        label = "sho_stage_icon"
        match = _find_within(config.SHO_STAGE_ICON, label)
        if not match:
            print(f"[{TAG}] The fire tile isn't in the act list - not the Infernal map?")
            return False
        match = settle_match(config.SHO_STAGE_ICON, match, label) or match
        for attempt in range(1, 4):
            print(f"[{TAG}] Clicking the fire tile at ({match[0]}, {match[1]}), confidence={match[2]:.2f}"
                  f"{f' (attempt {attempt}/3)' if attempt > 1 else ''}.")
            click_at(match[0], match[1])
            time.sleep(0.8)
            # Picked, the tile turns blue and stops looking like its grey crop. Still grey
            # where it was = the click didn't land; still grey after 3 clicks = the picked
            # look is close to the grey one, nothing more to do about it.
            still = _find(config.SHO_STAGE_ICON)
            if not still or abs(still[0] - match[0]) > 20 or abs(still[1] - match[1]) > 20:
                return True
            match = still
        print(f"[{TAG}] The fire tile still looks unpicked after 3 clicks - carrying on.")
        return True

    def _from_story_menu(self):
        """From the Story map list onwards. True / False / "RECONNECTED"."""
        self._infernal_missing = False
        missing = 0
        for attempt in range(1, STEP_ATTEMPTS + 1):
            if config.STOP_REQUESTED:
                return False
            self._tag_missing = False
            picked = self._click_infernal_map()
            missing += self._tag_missing
            if picked:
                time.sleep(0.6)
                if self._click_stage_icon():
                    time.sleep(0.6)
                    break
            print(f"[{TAG}] Infernal stage not picked (attempt {attempt}/{STEP_ATTEMPTS}).")
            time.sleep(1.0)
        else:
            path = health.save_debug_screenshot("sho_infernal_failed")
            print(f"[{TAG}] Couldn't pick the Infernal stage after {STEP_ATTEMPTS} tries (screen saved: {path}).")
            self._infernal_missing = missing == STEP_ATTEMPTS
            return False
        return self._select_and_start()

    def _select_and_start(self):
        """The Infernal stage's Select Stage -> the party's Start. True / False / "RECONNECTED"."""
        if _wait_for_menu(list(_SELECT_STAGE) + list(_PARTY_STARTS), 3.0) is None and _find(config.SHO_STAGE_ICON):
            self._click_stage_icon()
            time.sleep(0.6)
        if not _any_up(_PARTY_STARTS, capture_screen()):
            result = _step("sho_select_stage", lambda: _click_found(_SELECT_STAGE, "sho_select_stage"),
                           list(_PARTY_STARTS) + [config.DISBAND_BTN], tag=TAG)
            if result is not True:
                return result
        return self._start_from_party()

    def _start_from_party(self):
        self._phase("STARTING THE INFERNAL STAGE")
        print(f"[{TAG}] On the party screen - pressing Start.")
        result = _click_party_start()
        if result is True:
            self.resumed = False
        return result

    def _to_story_menu(self):
        """Lobby (or the mode cards) -> Play -> Story card. True / False / "RECONNECTED"."""
        result = _step("click_play", click_play, list(_STORY_CARDS) + [config.DISBAND_BTN], tag=TAG)
        if result is not True:
            return result
        return _step("story_card", _click_story_card,
                     _map_templates() + list(_INFERNAL_TAGS) + [config.SHO_FLAMING_MONASTERY,
                                         config.SHO_STAGE_ICON] + list(_SELECT_STAGE_FLAMING), tag=TAG)

    def _join(self):
        self._phase("GOING TO THE INFERNAL STAGE")
        print(f"[{TAG}] Going to the Infernal stage...")
        result = self._to_story_menu()
        if result is not True:
            return result
        result = self._from_story_menu()
        if result is not False or not self._infernal_missing or config.STOP_REQUESTED:
            return result
        # No Infernal tag on any map: the quest for the next Sho isn't accepted (the stage
        # only shows with it). The NPC gives it, and then its Infernal Stage shortcut.
        if self._quest_trips >= 1:
            print(f"[{TAG}] Still no Infernal tag after going to the NPC for the quest - stopping. Check in the game "
                  f"that the Sho quest is accepted and the Infernal stage shows in Story.")
            return _halt("SHO NO INFERNAL STAGE")
        self._quest_trips += 1
        print(f"[{TAG}] No Infernal tag on any map - the quest probably isn't accepted. Going to the NPC for it.")
        result = self._turn_in_trip(quest_only=True)
        if result == "JOINED":
            return True
        if result is True:
            return self._join()
        return result

    def navigate(self):
        """
        Onto the Infernal stage from wherever the game is - a trip to the NPC first if one
        is due. Returns True (Start Game next) / "IN_MATCH" / "ENDED" / False / "RECONNECTED".
        """
        from modules.lobby import at_lobby, return_to_lobby
        shot = capture_screen()
        if handle_disconnect_if_present(shot):
            return "RECONNECTED"
        if _STATE["turn_in_due"]:
            result = self._turn_in_trip()
            if result == "JOINED":
                return True
            if result is not True:
                return result
            shot = capture_screen()
        if at_lobby(shot) or _any_up(_STORY_CARDS, shot):
            return self._join()
        if _any_up(_PARTY_STARTS, shot):
            return self._start_from_party()
        if self._find_tag(shot)[1] or _find(config.SHO_STAGE_ICON, shot) \
                or _any_up(_SELECT_STAGE, shot):
            print(f"[{TAG}] The Story menu is open - carrying on from there.")
            result = self._from_story_menu()
            if result is not False or config.STOP_REQUESTED:
                return result
            # Not the Story menu after all (another mode's Select Stage), or a stale one.
            print(f"[{TAG}] Couldn't carry on from that menu - going back to the lobby and starting over.")
            if not return_to_lobby(timeout=120.0, next_mode="story"):
                return False
            return self._join()
        if _any_up(_REPEATS, shot) or _find(config.VICTORY_TEXT, shot) or _find(config.DEFEAT_TEXT, shot):
            print(f"[{TAG}] A match's end screen is up.")
            self._last_result = None
            return "ENDED"
        if _find(config.START_GAME_BTN, shot):
            print(f"[{TAG}] On a stage, Start Game up.")
            return True
        if _find(config.AUTOPLAY_ON_BTN, shot) or _find(config.AUTOPLAY_OFF_BTN, shot):
            print(f"[{TAG}] A match is already running - picking it up from here.")
            self._match_started_at = time.time()
            return "IN_MATCH"
        if not self._closed_stray_panel and self._leave_npc(shot):
            return self._look_again()
        if _any_up(_CHANGE_GAMEMODES, shot):
            print(f"[{TAG}] On a gamemode's own screen - Change Gamemode, then Story.")
            _click_found(_CHANGE_GAMEMODES, "sho_change_gamemode")
            return self._look_again()
        results_btn = _find(config.GAME_RESULTS_BTN, shot)
        if results_btn and not self._closed_stray_panel:
            print(f"[{TAG}] The match's results were closed - reopening them.")
            click_at(results_btn[0], results_btn[1])
            return self._look_again()

        print(f"[{TAG}] Nothing recognisable on screen - giving it a moment (loading?)...")
        known = [config.PLAY_BTN, config.START_GAME_BTN, config.AUTOPLAY_ON_BTN, config.AUTOPLAY_OFF_BTN] + \
            list(_PARTY_STARTS) + list(_REPEATS) + list(_STORY_CARDS)
        seen = _wait_for_menu(known, 20.0)
        if seen == "RECONNECTED":
            return seen
        if seen and not self._closed_stray_panel:
            return self._look_again()
        path = health.save_debug_screenshot("sho_unknown_screen")
        print(f"[{TAG}] Don't recognise this screen (saved: {path}) - going back to the lobby.")
        if not return_to_lobby(timeout=120.0, next_mode="story"):
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
        """Start Game. Presses Start / Repeat Stage again if one stays up."""
        last_nudge = [time.time()]

        def nudge(shot):
            if time.time() - last_nudge[0] < PARTY_NUDGE_SECONDS:
                return
            if _any_up(_PARTY_STARTS, shot):
                last_nudge[0] = time.time()
                print(f"[{TAG}] Still on the party screen - pressing Start again.")
                _click_party_start()
            elif _any_up(_REPEATS, shot):
                last_nudge[0] = time.time()
                print(f"[{TAG}] Repeat Stage is still up - clicking it again.")
                self._click_repeat()

        return wait_for_start_game(resumed=self.resumed, nudge=nudge)

    def _warn_auto(self, what):
        if not self._warned_auto:
            self._warned_auto = True
            print(f"[{TAG}] WARNING: the game's {what} looks ON. Turn Auto Start and Auto Retry OFF for Sho Farm - "
                  f"the macro reads every victory panel to count the Torn Photos.")

    def prepare_and_start(self):
        """Stage loaded -> Auto Play on -> Start Game. True / False / "RECONNECTED"."""
        self._phase("WAITING FOR START GAME")
        result = self._wait_for_stage()
        if result in (False, "RECONNECTED"):
            return result
        if not result:
            return False
        already_running = result == "ALREADY_STARTED"
        self.resumed = True
        time.sleep(0.4)
        self._phase("CHECKING AUTO PLAY", "#2e7d32")
        if not ensure_autoplay_enabled():
            print(f"[{TAG}] WARNING: couldn't find the Auto Play button - continuing anyway.")
        if already_running:
            self._warn_auto("Auto Start")
        else:
            self._phase("STARTING MATCH", "#2e7d32")
            if not click_start_game():
                print(f"[{TAG}] Start Game wasn't there to click - the match is probably already running.")
        SESSION.match_started()
        self._match_started_at = time.time()
        self._last_result = None
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
        self._last_result = result
        if result == "DEFEAT":
            self.consecutive_defeats += 1
            SESSION.defeat()
            print(f"[{TAG}] Defeat ({self.consecutive_defeats} in a row) - no Torn Photo this time.")
            if config.defeat_limit_reached(self.consecutive_defeats):
                health.save_debug_screenshot("sho_too_many_defeats")
                print(f"[{TAG}] {self.consecutive_defeats} losses in a row - stopping, the team can't clear the "
                      f"Infernal stage.")
                return _halt("TOO MANY DEFEATS (SHO)")
        else:
            self.consecutive_defeats = 0
            self._quest_trips = 0
            SESSION.victory()
            print(f"[{TAG}] Victory!")
        return True

    def play_match(self):
        """Waits for the match to end and counts it. "ENDED" / False / "RECONNECTED"."""
        self._phase("WAITING FOR MATCH END", "#4fc3f7")
        started = self._match_started_at or time.time()
        seen_running = [False]
        gone_looks = [0]
        last_start_press = [0.0]
        last_results_click = [0.0]
        stage_seen = [time.time()]
        overdue_at = [0.0]
        usual = sorted(self._match_times)[len(self._match_times) // 2] if self._match_times else 0.0
        overdue_after = max(OVERDUE_MIN, usual * OVERDUE_FACTOR)

        def other_end(shot):
            now = time.time()
            start_game_up = _find(config.START_GAME_BTN, shot)
            gone_looks[0] = 0 if start_game_up else gone_looks[0] + 1
            if gone_looks[0] >= RUNNING_AFTER_LOOKS:
                seen_running[0] = True
            if start_game_up or _find(config.AUTOPLAY_ON_BTN, shot) or _find(config.AUTOPLAY_OFF_BTN, shot):
                stage_seen[0] = now
            elif now - stage_seen[0] >= OFF_STAGE_LIMIT:
                return "OFF_STAGE"
            if now - started < MIN_MATCH:
                return None
            if _any_up(_REPEATS, shot):
                return "SCREEN"
            results_btn = _find(config.GAME_RESULTS_BTN, shot)
            if results_btn and seen_running[0] and now - last_results_click[0] > 6.0:
                # The results popup was closed before it was read - reopen it.
                last_results_click[0] = now
                print(f"[{TAG}] The results were closed - reopening them (Game Results).")
                click_at(results_btn[0], results_btn[1])
                return None
            if seen_running[0] and now - started >= overdue_after:
                if not overdue_at[0]:
                    overdue_at[0] = now
                    path = health.save_debug_screenshot("sho_match_overdue")
                    print(f"[{TAG}] The match has run {(now - started) / 60:.1f} min, longer than usual "
                          f"({usual / 60:.1f} min) - its results were probably closed (screen saved: {path}).")
                elif now - overdue_at[0] >= OVERDUE_GRACE:
                    return "PANEL_GONE"
            if start_game_up:
                if seen_running[0]:
                    return "RETRIED"
                if now - last_start_press[0] >= 5.0:
                    last_start_press[0] = now
                    print(f"[{TAG}] Start Game is still waiting to be pressed - pressing it.")
                    click_start_game()
            return None

        print(f"[{TAG}] Waiting for the match to end...")
        result = poll_until([target(config.VICTORY_TEXT, "VICTORY", debug_label="victory"),
                             target(config.DEFEAT_TEXT, "DEFEAT", debug_label="defeat")],
                            interval=1.0, label="sho_match_result", stuck_timeout=None, custom_check=other_end,
                            lobby_grace=8.0, game_results=False)
        if result in (False, "RECONNECTED"):
            return result
        if result == "OFF_STAGE":
            path = health.save_debug_screenshot("sho_off_stage")
            print(f"[{TAG}] The stage hasn't been on screen for {OFF_STAGE_LIMIT / 60:.0f} minutes (screen saved: "
                  f"{path}) - getting back onto it.")
            return "RECONNECTED"
        if result == "PANEL_GONE":
            # Nothing to read and no Repeat Stage: after_match() leaves the stage and joins again.
            self._last_result = "PANEL_GONE"
            return "ENDED"
        if result in ("SCREEN", "VICTORY", "DEFEAT"):
            self._match_times = (self._match_times + [time.time() - started])[-10:]
        if result == "SCREEN":
            result = self._read_result()
        elif result == "RETRIED":
            # The game's Auto Retry restarted the stage: the panel was never there to read.
            self._warn_auto("Auto Retry")
            print(f"[{TAG}] The game restarted the stage by itself - counting a win.")
            if not self._count("VICTORY"):
                return False
            self._add_photo(seen=None)
            self._last_result = "RETRIED"
            self.resumed = True
            return "ENDED"
        if not self._count(result):
            return False
        return "ENDED"

    # --- the rewards ------------------------------------------------------------------------
    def _read_rewards(self):
        """
        (photo seen, limit reached) on the victory panel. The cards pop in, so it looks for up
        to REWARD_LOOK_SECONDS. The limit is decided by how the found card looks: it must look
        like the limit card (Torn_Photo.png, taken from the limit example) clearly more than the
        normal card (Torn_Photo_Normal.png), or carry the warning triangle at its corner.
        """
        photo = limit = False
        deadline = time.time() + REWARD_LOOK_SECONDS
        while not config.STOP_REQUESTED:
            shot = capture_screen()
            _, card = _find_any(_TORN_PHOTOS, shot)
            if card:
                photo = True
                center = (card[0], card[1])
                limit_look = _score_near(shot, config.SHO_TORN_PHOTO, center, LOOK_REACH)
                normal_look = _score_near(shot, config.SHO_TORN_PHOTO_NORMAL, center, LOOK_REACH)
                corner = (card[0] + config.SHO_LIMIT_WARNING_DX, card[1] + config.SHO_LIMIT_WARNING_DY)
                warning = _score_near(shot, config.SHO_LIMIT_WARNING, corner, 22)
                looks_limit = limit_look >= LIMIT_LOOK_MIN and limit_look >= normal_look + LIMIT_LOOK_MARGIN
                if looks_limit or warning >= config.TEMPLATE_THRESHOLDS[config.SHO_LIMIT_WARNING]:
                    limit = True
                    print(f"[{TAG}] The Torn Photo looks like the limit card (limit look {limit_look:.2f}, normal "
                          f"look {normal_look:.2f}, warning {warning:.2f}) - the photos can be turned in.")
                    break
            if time.time() >= deadline:
                break
            time.sleep(0.5)
        if photo and not limit:
            print(f"[{TAG}] The Torn Photo looks normal - no turn-in yet.")
        return photo, limit

    def _add_photo(self, seen):
        """One more Torn Photo counted. seen: whether the card was seen (None = not looked)."""
        _STATE["photos"] += 1
        # The count only sends a turn-in when the card can't be read at all; otherwise the
        # limit card on the rewards decides (the count can be off, e.g. photos from before).
        if seen is not True and self._photo_unreadable and _STATE["photos"] >= config.SHO_PHOTOS_NEEDED:
            _STATE["turn_in_due"] = True
        _save_state()
        how = "" if seen else " (counted from the win - the card wasn't read)"
        print(f"[{TAG}] Torn Photo {_STATE['photos']}/{config.SHO_PHOTOS_NEEDED}{how}.")

    def _check_rewards(self):
        """
        Reads the victory panel after a win and counts the photo. Returns "MOVED" when the
        Infernal stage looks like it has moved to another map, else None.
        """
        photo, limit = self._read_rewards()
        if photo:
            self._no_photo_wins = 0
            self._photo_unreadable = False
            self._add_photo(seen=True)
        elif self._photo_unreadable:
            self._add_photo(seen=False)
        else:
            self._no_photo_wins += 1
            path = health.save_debug_screenshot("sho_no_torn_photo")
            if self._no_photo_wins == 1:
                print(f"[{TAG}] A win with no Torn Photo in the rewards (screen saved: {path}) - the Infernal stage "
                      f"has probably moved to another map. Going to find it again.")
                return "MOVED"
            # Straight after finding the stage again - it's the card that can't be seen.
            self._photo_unreadable = True
            print(f"[{TAG}] Still no Torn Photo in the rewards right after finding the Infernal stage again (screen "
                  f"saved: {path}) - the card can't be read here, so every win counts from now on.")
            self._add_photo(seen=False)
        if limit:
            _STATE.update(turn_in_due=True, limit_seen=True)
            _save_state()
        return None

    # --- after the match ------------------------------------------------------------------
    def _click_repeat(self, leave_if_refused=False):
        """ONE click on Repeat Stage once it's settled (a double click can land on View Party)."""
        time.sleep(1.0)
        template, match = _find_any(_REPEATS)
        if not match:
            return True
        print(f"[{TAG}] Clicking Repeat Stage.")
        if leave_if_refused:
            return click_stage_button(template, match, "sho_repeat", clicks=1)
        return click_until_gone(template, match, "sho_repeat", clicks=1)

    def after_match(self):
        """
        Reads the rewards, then Repeat Stage - or the trip to the NPC when it's due, or back
        through the menus when the Infernal stage moved. "START" / "IN_MATCH" / "LOST" (navigate()
        takes over) / False / "RECONNECTED".
        """
        self._phase("READING THE REWARDS")
        if self._last_result == "PANEL_GONE":
            self._last_result = None
            print(f"[{TAG}] No results panel to read (it isn't counted) - leaving the stage and joining again.")
            result = self._to_mode_cards()
            return result if result in (False, "RECONNECTED") else "LOST"
        if self._last_result == "RETRIED":
            self._last_result = None
            return self._next_or_trip(on_end_screen=False)

        result = None
        reopened = False
        for _ in range(3):
            result = poll_until([target(config.REPEAT_STAGE_BTN, "REPEAT", debug_label="sho_repeat_stage"),
                                 target(config.SHO_REPEAT_STAGE_BTN, "REPEAT"),
                                 target(config.START_GAME_BTN, "START")],
                                interval=1.0, label="sho_repeat_wait", timeout=AFTER_MATCH_WAIT / 3,
                                stuck_timeout=None, lobby_grace=25.0, game_results=False)
            if result != "TIMEOUT" or config.STOP_REQUESTED:
                break
            results_btn = _find(config.GAME_RESULTS_BTN)
            if results_btn and not reopened:
                print(f"[{TAG}] No Repeat Stage - reopening the results with Game Results.")
                click_at(results_btn[0], results_btn[1])
                reopened = True
                time.sleep(1.5)
        if result in (False, "RECONNECTED"):
            return result
        if result == "START":
            # The game's Auto Retry brought the stage back before the panel could be read.
            self._warn_auto("Auto Retry")
            if self._last_result == "VICTORY":
                self._add_photo(seen=None)
            self._last_result = None
            return self._next_or_trip(on_end_screen=False)
        if result != "REPEAT":
            path = health.save_debug_screenshot("sho_no_repeat")
            print(f"[{TAG}] No Repeat Stage after the match (screen saved: {path}) - working out where we are.")
            return "LOST"

        if self._last_result is None:
            # Picked up on an end screen (a restart): read and count it now.
            if not self._count(self._read_result()):
                return False
        won = self._last_result == "VICTORY"
        self._last_result = None
        if won and self._check_rewards() == "MOVED":
            result = self._to_mode_cards()
            return result if result in (False, "RECONNECTED") else "LOST"
        return self._next_or_trip(on_end_screen=True)

    def _next_or_trip(self, on_end_screen):
        """Repeat Stage, or the trip to the NPC once it's due."""
        if _STATE["turn_in_due"]:
            result = self._turn_in_trip()
            if result == "JOINED":
                return "START"
            return result if result in (False, "RECONNECTED") else "LOST"
        if not on_end_screen:
            return "START"
        clicked = self._click_repeat(leave_if_refused=True)
        if clicked == "RECONNECTED":
            return clicked
        return "START" if clicked else "LOST"

    # --- the trip to the NPC ----------------------------------------------------------------
    def _to_mode_cards(self):
        """
        Off a stage to the mode cards: any panel's X (the victory panel, the NPC's) -> small
        Play -> Change Gamemode. Nothing to do at the lobby or the mode cards already. Falls
        back to return_to_lobby() (which knows every way out) if a step doesn't work.
        True / False / "RECONNECTED".
        """
        from modules.lobby import at_lobby, return_to_lobby
        shot = capture_screen()
        if at_lobby(shot) or _any_up(_STORY_CARDS, shot) or _any_up(_map_templates(), shot):
            return True
        self._leave_npc(shot)
        self._phase("LEAVING THE STAGE")
        print(f"[{TAG}] Leaving the stage: close the panel -> small Play -> Change Gamemode.")
        template, close = _find_any(_CLOSES)
        if close:
            click_until_gone(template, close, "sho_close_panel")
            time.sleep(0.8)
        result = _step("sho_play_small", lambda: _click_found(_PLAY_SMALLS, "sho_play_small", clicks=2),
                       list(_CHANGE_GAMEMODES) + list(_STORY_CARDS), tag=TAG)
        if result == "RECONNECTED":
            return result
        if result is True:
            result = _step("sho_change_gamemode", lambda: _click_found(_CHANGE_GAMEMODES, "sho_change_gamemode"),
                           list(_STORY_CARDS), tag=TAG)
            if result in (True, "RECONNECTED"):
                return result
        if config.STOP_REQUESTED:
            return False
        print(f"[{TAG}] That way out didn't work - going back to the lobby another way.")
        return return_to_lobby(timeout=120.0, next_mode="story")

    def _leave_npc(self, shot=None):
        """The NPC panel's Leave, if it's open - it covers the stage's buttons. True if it was."""
        shot = shot if shot is not None else capture_screen()
        leave = _find(config.SHO_LEAVE_BTN, shot)
        if not leave:
            return False
        print(f"[{TAG}] Leaving the NPC's panel.")
        click_until_gone(config.SHO_LEAVE_BTN, leave, "sho_leave_npc")
        time.sleep(0.8)
        return True

    def _click_flaming_monastery(self):
        label = "sho_flaming_monastery"
        match = _find_within(config.SHO_FLAMING_MONASTERY, label, seconds=2.5)
        if not match:
            match = _sweep_carousel(config.SHO_FLAMING_MONASTERY, label, config.MAPS, "flaming_monastery")
        if not match:
            print(f"[{TAG}] Flaming Monastery's name not found - trying its card art instead.")
            return click_map("flaming_monastery")
        match = settle_match(config.SHO_FLAMING_MONASTERY, match, label) or match
        print(f"[{TAG}] Flaming Monastery at ({match[0]}, {match[1]}), confidence={match[2]:.2f} - clicking.")
        click_at(match[0], match[1])
        return True

    def _click_act_1(self):
        """Act 1's tile - the player's crop (selected look) or Story's own (either look)."""
        template, match = None, None
        deadline = time.time() + 4.0
        while not config.STOP_REQUESTED and time.time() < deadline:
            template, match = _find_any((config.SHO_ACT_1, config.ACT_TEMPLATES["act_1"]))
            if match:
                break
            time.sleep(0.3)
        if not match:
            path = health.save_debug_screenshot("sho_no_act_1")
            print(f"[{TAG}] Act 1's tile isn't on screen (screen saved: {path}).")
            return False
        match = settle_match(template, match, "sho_act_1") or match
        print(f"[{TAG}] Clicking Act 1 at ({match[0]}, {match[1]}), confidence={match[2]:.2f}.")
        click_at(match[0], match[1])
        return True

    def _to_flaming_monastery(self):
        """Mode cards / lobby -> Story -> Flaming Monastery -> Act 1 -> Select Stage -> Start."""
        self._phase("GOING TO FLAMING MONASTERY", "#f9a825")
        if not _any_up(_map_templates(), capture_screen()):
            result = self._to_story_menu()
            if result is not True:
                return result
        for attempt in range(1, STEP_ATTEMPTS + 1):
            if config.STOP_REQUESTED:
                return False
            if self._click_flaming_monastery():
                time.sleep(0.6)
                if self._click_act_1():
                    time.sleep(0.6)
                    break
            print(f"[{TAG}] Flaming Monastery - Act 1 not picked (attempt {attempt}/{STEP_ATTEMPTS}).")
            time.sleep(1.0)
        else:
            path = health.save_debug_screenshot("sho_flaming_monastery_failed")
            print(f"[{TAG}] Couldn't pick Flaming Monastery - Act 1 (screen saved: {path}).")
            return False
        result = _step("sho_select_stage_flaming",
                       lambda: _click_found(_SELECT_STAGE_FLAMING, "sho_select_stage_flaming"),
                       list(_PARTY_STARTS) + [config.DISBAND_BTN], tag=TAG)
        if result is not True:
            return result
        print(f"[{TAG}] On the party screen - pressing Start.")
        return _click_party_start()

    def _read_panel(self, shot):
        """
        The NPC's Turn In button: ("ready", (x, y)) / ("not_ready", (x, y)) / (None, None) when
        the panel isn't open. Found by its greyed out crop or by its Torn Photo icon; greyed out
        or not is then read from its colour (see SHO_GREY_MAX_BRIGHTNESS).
        """
        if os.path.exists(config.SHO_TURN_IN_BTN):
            ready = _find(config.SHO_TURN_IN_BTN, shot)
            if ready:
                return "ready", (ready[0], ready[1])
        grey = _find(config.SHO_NO_TURN_IN, shot)
        if grey:
            button = (grey[0], grey[1])
        else:
            icon = _find(config.SHO_TURN_IN_PHOTO, shot)
            if not icon:
                return None, None
            button = (icon[0] + config.SHO_TURN_IN_BTN_DX, icon[1] + config.SHO_TURN_IN_BTN_DY)
        if self._looks_grey(shot, button):
            return "not_ready", button
        print(f"[{TAG}] The Turn In button isn't greyed out.")
        return "ready", button

    @staticmethod
    def _looks_grey(shot, button):
        """The button's centre (bare background): flat dark grey = greyed out."""
        s = config.render_scale()
        x, y = int(round(button[0] * s)), int(round(button[1] * s))
        patch = shot[max(0, y - 2):y + 3, max(0, x - 2):x + 3]
        if patch.size == 0:
            return False
        b, g, r = (float(v) for v in patch.reshape(-1, 3).mean(axis=0))
        return max(b, g, r) <= config.SHO_GREY_MAX_BRIGHTNESS and max(b, g, r) - min(b, g, r) <= config.SHO_GREY_MAX_SPREAD

    def _wait_for_panel(self):
        """
        The NPC's panel within SHO_NPC_PANEL_WAIT: (state, point) or (None, None) / "RECONNECTED".
        If E opened the NPC's dialogue instead (the quest isn't accepted yet), the dialogue is
        clicked through, the quest accepted, and E pressed again for the panel - once.
        """
        talked = False
        deadline = time.time() + config.SHO_NPC_PANEL_WAIT
        while not config.STOP_REQUESTED:
            shot = capture_screen()
            if handle_disconnect_if_present(shot):
                return "RECONNECTED"
            state, point = self._read_panel(shot)
            if state:
                return state, point
            if not talked and self._dialogue_up(shot):
                talked = True
                print(f"[{TAG}] The NPC is talking instead of showing Turn In - the quest isn't accepted yet.")
                accepted = self._talk_through()
                time.sleep(0.6)
                if accepted or _find(config.SHO_INFERNAL_STAGE_BTN):
                    return "shortcut", None
                print(f"[{TAG}] Pressing E again for the Turn In panel.")
                _press_e()
                deadline = time.time() + config.SHO_NPC_PANEL_WAIT
                continue
            if time.time() >= deadline:
                break
            time.sleep(0.4)
        return None, None

    @staticmethod
    def _dialogue_up(shot):
        return _any_up(_DIALOGUES, shot) or bool(_find(config.SHO_ACCEPT_QUEST_BTN, shot))

    def _talk_through(self):
        """
        Clicks the NPC's dialogue on (on its own arrow - a click anywhere else doesn't move it),
        Accept Quest when it shows, until the dialogue is gone or the Infernal Stage shortcut
        shows. True if the quest was accepted.
        """
        accepted = False
        clicks = 0
        quiet_since = None
        while not config.STOP_REQUESTED and clicks < config.SHO_DIALOGUE_MAX_CLICKS:
            shot = capture_screen()
            if _find(config.SHO_INFERNAL_STAGE_BTN, shot):
                break
            accept = _find(config.SHO_ACCEPT_QUEST_BTN, shot)
            if accept:
                print(f"[{TAG}] Accepting the quest.")
                click_until_gone(config.SHO_ACCEPT_QUEST_BTN, accept, "sho_accept_quest")
                accepted = True
                clicks += 1
                quiet_since = None
                time.sleep(0.8)
                continue
            line = _find_any(_DIALOGUES, shot)[1]
            if line:
                click_at(line[0], line[1])
                clicks += 1
                quiet_since = None
                time.sleep(0.8)
                continue
            # Nothing up: the next line can take a moment - the dialogue is over after 2s of nothing.
            quiet_since = quiet_since or time.time()
            if time.time() - quiet_since >= 2.0:
                break
            time.sleep(0.3)
        if clicks >= config.SHO_DIALOGUE_MAX_CLICKS:
            path = health.save_debug_screenshot("sho_dialogue_stuck")
            print(f"[{TAG}] Still in the dialogue after {clicks} clicks (screen saved: {path}).")
        return accepted

    def _start_next_quest(self):
        """After the unit: E at the NPC again, through the dialogue, Accept Quest. True if accepted."""
        self._phase("ACCEPTING THE NEXT QUEST", "#f9a825")
        for attempt in (1, 2):
            if config.STOP_REQUESTED:
                return False
            print(f"[{TAG}] Pressing E to start the next quest{' (again)' if attempt > 1 else ''}.")
            _press_e()
            deadline = time.time() + config.SHO_NPC_PANEL_WAIT
            while not config.STOP_REQUESTED and time.time() < deadline:
                if self._dialogue_up(capture_screen()):
                    if self._talk_through():
                        print(f"[{TAG}] Next quest accepted.")
                        return True
                    break
                time.sleep(0.4)
        path = health.save_debug_screenshot("sho_quest_not_accepted")
        print(f"[{TAG}] Couldn't accept the next quest (screen saved: {path}) - the next trip to the NPC will try "
              f"again when it opens the dialogue.")
        return False

    def _walk_to_npc(self, first):
        """Camera, then the recorded walk. True / False / "RECONNECTED"."""
        if first:
            self._phase("FIXING THE CAMERA")
            anchor_camera()
            time.sleep(0.5)
        else:
            print(f"[{TAG}] Nothing opened - teleporting to spawn and walking again.")
            result = self._spawn._reset_to_spawn(halt=False)      # re-anchors the camera itself
            if result in (False, "RECONNECTED"):
                return result
            if result is None:
                print(f"[{TAG}] Couldn't teleport to spawn - walking from here anyway.")
                anchor_camera()
                time.sleep(0.5)
        self._phase("WALKING TO THE NPC", "#f9a825")
        print(f"[{TAG}] Walking to the NPC (recording '{self.walk_preset_name}')...")
        result = play_preset(config.SHO_PRESET_LOCATION, config.SHO_WALK_VARIANT, self.walk_preset_name,
                             movement_only=True)
        if result == "RECONNECTED" or (result is False and config.STOP_REQUESTED):
            return result
        if result is not True:
            print(f"[{TAG}] The walk to the NPC '{self.walk_preset_name}' is missing or empty - record it on the "
                  f"Others page (Sho).")
            return _halt("SHO NPC WALK MISSING")
        return True

    def _talk_to_npc(self):
        """Walk + E until the NPC's panel opens. (state, point) / (None, None) / False / "RECONNECTED"."""
        for attempt in range(1, config.SHO_NPC_ATTEMPTS + 1):
            if config.STOP_REQUESTED:
                return False
            result = self._walk_to_npc(first=attempt == 1)
            if result is not True:
                return result
            time.sleep(0.4)
            print(f"[{TAG}] Pressing E at the NPC (attempt {attempt}/{config.SHO_NPC_ATTEMPTS}).")
            _press_e()
            found = self._wait_for_panel()
            if found == "RECONNECTED" or found[0]:
                return found
        path = health.save_debug_screenshot("sho_npc_no_panel")
        print(f"[{TAG}] The NPC's panel never opened after {config.SHO_NPC_ATTEMPTS} walks (screen saved: {path}). "
              f"If the character doesn't end up next to the NPC, re-record the walk (Fix camera first, then walk "
              f"from the spawn).")
        return None, None

    def _close_reward(self):
        """
        After Turn In: the unit's popup ("Obtained Rewards", click anywhere to close), clicked
        away if it shows. True if it showed.
        """
        deadline = time.time() + OBTAIN_WAIT
        seen = False
        while not config.STOP_REQUESTED and time.time() < deadline:
            shot = capture_screen()
            if dismiss_click_anywhere_if_present(shot):
                seen = True
                time.sleep(0.8)
                continue
            if _find(config.SHO_OBTAINED_TITLE, shot):
                seen = True
                print(f"[{TAG}] Closing the reward popup (click anywhere).")
                click_at(config.ANTI_IDLE_X, config.ANTI_IDLE_Y)
                time.sleep(1.0)
                deadline = max(deadline, time.time() + 2.0)
                continue
            if seen:
                return True
            time.sleep(0.4)
        return seen

    def _turn_in(self, point, require_popup=False):
        """
        Clicks Turn In; True once the unit's popup showed, or the button went grey / the
        panel closed. require_popup: only the popup counts - for a button that was already
        grey before the click, where "grey afterwards" proves nothing.
        """
        for attempt in (1, 2):
            if config.STOP_REQUESTED:
                return False
            print(f"[{TAG}] Clicking Turn In at ({point[0]:.0f}, {point[1]:.0f})"
                  f"{' (again)' if attempt > 1 else ''}.")
            click_at(point[0], point[1])
            if self._close_reward():
                return True
            if require_popup:
                continue
            state, new_point = self._read_panel(capture_screen())
            if state == "not_ready":
                print(f"[{TAG}] The Turn In button went grey - the photos were taken.")
                return True
            if state is None:
                # The panel closed with no popup seen: the unit may have arrived quietly.
                print(f"[{TAG}] The panel closed after Turn In (no reward popup seen).")
                return True
            point = new_point or point
        return False

    def _finish_obtain(self):
        """
        After the unit: its "click anywhere to continue" closed, then the NPC's thank-you line
        clicked away on its arrow. True once that line was clicked through.
        """
        self._phase("CLOSING THE REWARD", "#f9a825")
        started = time.time()
        clicked_blind = False
        while not config.STOP_REQUESTED and time.time() - started < FINISH_WAIT:
            shot = capture_screen()
            if dismiss_click_anywhere_if_present(shot):
                time.sleep(0.8)
                continue
            if _find(config.SHO_OBTAINED_TITLE, shot):
                print(f"[{TAG}] Closing the reward popup (click anywhere).")
                click_at(config.ANTI_IDLE_X, config.ANTI_IDLE_Y)
                time.sleep(1.0)
                continue
            if self._dialogue_up(shot):
                print(f"[{TAG}] Clicking through the NPC's thank-you line.")
                self._talk_through()
                return True
            if not clicked_blind and time.time() - started >= 3.0:
                # The popup can be missed by the picture - "click anywhere" takes any click.
                clicked_blind = True
                print(f"[{TAG}] No popup or dialogue seen yet - one click to continue, in case it's up.")
                click_at(config.ANTI_IDLE_X, config.ANTI_IDLE_Y)
                time.sleep(1.0)
                continue
            time.sleep(0.4)
        path = health.save_debug_screenshot("sho_after_unit")
        print(f"[{TAG}] The NPC's thank-you line didn't show after the unit (screen saved: {path}) - pressing E "
              f"for the next quest anyway.")
        return False

    def _take_shortcut(self):
        """
        After Accept Quest the NPC offers "Infernal Stage", which opens the Infernal stage's Select
        Stage straight away: clicked, then Select Stage -> Start. True / False / "RECONNECTED".
        """
        self._phase("INFERNAL STAGE SHORTCUT", "#f9a825")
        match = None
        deadline = time.time() + SHORTCUT_WAIT
        while not config.STOP_REQUESTED and time.time() < deadline:
            shot = capture_screen()
            match = _find(config.SHO_INFERNAL_STAGE_BTN, shot)
            if match:
                break
            line = _find_any(_DIALOGUES, shot)[1]
            if line:
                click_at(line[0], line[1])     # the lines after Accept Quest come first
                time.sleep(0.8)
                continue
            time.sleep(0.4)
        if not match:
            path = health.save_debug_screenshot("sho_no_infernal_shortcut")
            print(f"[{TAG}] The Infernal Stage button didn't show after accepting the quest (screen saved: {path}).")
            return False
        print(f"[{TAG}] Taking the Infernal Stage shortcut at ({match[0]}, {match[1]}), confidence={match[2]:.2f}.")
        result = _step("sho_infernal_stage",
                       lambda: _click_found((config.SHO_INFERNAL_STAGE_BTN,), "sho_infernal_stage"),
                       list(_SELECT_STAGE) + list(_PARTY_STARTS) + [config.SHO_STAGE_ICON], tag=TAG)
        if result is not True:
            return result
        return self._select_and_start()

    def _shortcut_or_back(self):
        """The Infernal Stage shortcut, or the menus if it isn't there. "JOINED" / True / False / "RECONNECTED"."""
        result = self._take_shortcut()
        if result is True:
            return "JOINED"
        if result == "RECONNECTED" or config.STOP_REQUESTED:
            return result if result == "RECONNECTED" else False
        print(f"[{TAG}] No shortcut - back to the lobby and through the menus.")
        self._phase("BACK TO THE LOBBY")
        result = self._to_mode_cards()
        return result if result in (False, "RECONNECTED") else True

    def _turn_in_trip(self, quest_only=False):
        """
        To the NPC on Flaming Monastery: turns the photos in (quest_only: only there for the
        quest), accepts the next quest and takes the Infernal Stage shortcut it offers.
        "JOINED" (the Infernal stage is starting - Start Game next) / True (back at the mode
        cards, carry on from there) / False / "RECONNECTED".
        """
        if quest_only:
            self._phase("GOING FOR THE QUEST", "#f9a825")
        else:
            self._phase("GOING TO TURN IN THE PHOTOS", "#f9a825")
            print(f"[{TAG}] Time to turn in the Torn Photos ({_STATE['photos']} counted"
                  f"{', limit reached' if _STATE['limit_seen'] else ''}).")
        result = self._to_mode_cards()
        if result in (False, "RECONNECTED") or config.STOP_REQUESTED:
            return result if result == "RECONNECTED" else False
        result = self._to_flaming_monastery()
        if result is not True:
            return result

        self._phase("WAITING FOR THE STAGE")
        loaded = wait_for_start_game()          # never clicks Start Game - only waits for the stage
        if loaded in (False, "RECONNECTED"):
            return loaded
        time.sleep(1.0)
        found = self._talk_to_npc()
        if found in (False, "RECONNECTED"):
            return found
        state, point = found
        if state is None:
            return False
        if state == "shortcut":
            # E opened the dialogue instead of Turn In: the quest wasn't accepted, now it is.
            print(f"[{TAG}] Quest accepted.")
            if not quest_only:
                _STATE.update(photos=0, turn_in_due=False, limit_seen=False)
                _save_state()
            return self._shortcut_or_back()

        limit_seen = _STATE["limit_seen"]
        if state == "not_ready" and limit_seen and not quest_only:
            # The limit warning was on the reward, so it should be ready - a greyed out
            # button does nothing when clicked, so press it anyway in case it was misread.
            print(f"[{TAG}] The button reads greyed out, but the limit warning was seen - pressing it anyway.")
            if self._turn_in(point, require_popup=True):
                state = "done"
        elif state == "ready":
            if not self._turn_in(point):
                path = health.save_debug_screenshot("sho_turn_in_failed")
                print(f"[{TAG}] Turn In didn't take (screen saved: {path}).")
                return False
            state = "done"

        if state == "done":
            _STATE["limit_misses"] = 0
            _STATE.update(photos=0, turn_in_due=False, limit_seen=False)
            _save_state()
            SESSION.sho_obtained()
            print(f"[{TAG}] SHO OBTAINED! ({SESSION.shos} this run)")
            try:
                from modules import notify
                notify.send("Sho obtained - 16 Torn Photos turned in.", category="milestones",
                            title="Sho obtained", good=True, image_bytes=health.jpg_bytes(capture_screen()))
            except Exception as e:
                print(f"[notify] Couldn't send the Sho message: {e}")
            self._finish_obtain()
            time.sleep(0.6)
            if self._start_next_quest():
                return self._shortcut_or_back()
            self._phase("BACK TO THE LOBBY")
            result = self._to_mode_cards()
            return result if result in (False, "RECONNECTED") else True

        if quest_only:
            # Turn In shows, so the quest IS running - the missing tag is something else.
            self._leave_npc()
            path = health.save_debug_screenshot("sho_quest_running_no_tag")
            print(f"[{TAG}] The quest is already accepted (the NPC shows Turn In), yet no map has the Infernal tag "
                  f"(screen saved: {path}) - stopping. Please send the sho_no_infernal_tag screenshots.")
            return _halt("SHO NO INFERNAL STAGE")

        path = health.save_debug_screenshot("sho_not_enough_photos")
        print(f"[{TAG}] Turn In is greyed out - not 16 Torn Photos yet (screen saved: {path}). Back to farming; "
              f"the limit card on the reward decides when to come back.")
        if limit_seen:
            _STATE["limit_misses"] += 1
        _STATE.update(photos=0, turn_in_due=False, limit_seen=False)
        _save_state()
        if _STATE["limit_misses"] >= LIMIT_MISSES_MAX:
            _STATE["limit_misses"] = 0
            print(f"[{TAG}] {LIMIT_MISSES_MAX} times in a row the Torn Photo showed the limit card but Turn In stayed "
                  f"greyed out - stopping instead of going back and forth. Please send the saved screenshots "
                  f"(sho_not_enough_photos) so the check can be fixed.")
            return _halt("SHO TURN IN STAYS GREY")

        self._phase("BACK TO THE LOBBY")
        result = self._to_mode_cards()
        return result if result in (False, "RECONNECTED") else True
