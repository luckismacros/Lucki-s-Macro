# modules/sharkfang.py
"""
Sharkfang Farm (Others page, added 2026-10-19): the Tidal Siege summer event's fishing
gamemode, farmed for its Sharkfang unit. LIMITED EVENT - when it ends, delete this
module, its config block (SHARKFANG_*), assets/templates/sharkfang/, run_sharkfang() in
engine.py and the Sharkfang option on the Others page.

As described by the player (2026-10-19):
  Joining: Events -> Tidal Siege -> Event Gamemode -> Event Mode (on Mode Selection) ->
  Select Stage -> Start.
  On the stage (the Summer portal's map): camera, walk to the fishing spot (the Summer
  portal's own Auto Play fishing walk), Auto Play on, Start Game - and FISH. Fishing is
  the whole point of the mode; everything else hangs off the catch.
  The bar isn't empty when a match starts, so fish 3 minutes first. From then on the bar
  is checked every 30 seconds: anything in its 6 slots is dragged onto the bin, except the
  Sharktooth Conch (the tooth), which is kept.
  With a tooth in the bar: once the storm dial looks exactly like storm.png (purple
  quarter at the top right), press the tooth - no confirm, it's used at once - then watch
  for Obtained Rewards with the Sharkfang card, and click it away ("click anywhere").
  Sharkfang obtained -> Settings -> Restart -> Restart: the match starts over with the
  character and camera where they were, so just Auto Play on, Start Game, cast again.
  No tooth 18 minutes in (about wave 70) -> the same restart. With a tooth, never restart
  until it's been used.

Fallbacks, because a missed step costs a whole Sharkfang:
  - The bar is looked at for the tooth every poll, and the storm every poll while there's
    a tooth, so a tooth caught between bar checks doesn't miss its storm. The dial has to match in shape, and seven points around
    its rim have to be the right colour - purple only in the top right quarter.
  - The bar is found whole while it's empty, by its empty slots while partly full, or
    from the bin (always on screen) once the distance between the two has been seen -
    that distance is saved to disk, so a later run finds a full bar too.
  - Junk is dragged from the rightmost slot, so nothing slides into the emptied slot; a
    drag that changed nothing is tried once more, slower; a missing bin uses where it was
    last. A slot counts as empty by its picture or by being dark and flat.
  - The tooth is never binned: it's checked first in every slot, a slot that looks even a
    bit like it is left alone, and each slot is looked at again right before its drag.
  - A tooth press that didn't take is pressed again; after the press, the popup's title
    counts as the reward too.
  - The restart takes either Settings gear; the reward popup in the way is clicked off.
  - Anything unexpected goes back through navigate(), which reads the screen.
"""
import json
import os
import time

import cv2

import config
import settings
from vision import capture_screen, find_template, find_all_templates, score_template
from input_controller import anchor_camera, click_at, drag_to
from modules.gamemode_select import _run_steps
from modules.monster_clash import MonsterClashRunner, FARM, AT_SPAWN, AT_SPOT, UNKNOWN, _click_through
from modules.reconnect import handle_disconnect_if_present
from modules.polling import poll_until, target, click_until_gone
from modules.stage_player import play_preset, wait_for_start_game, click_start_game
from modules.autoplay import ensure_autoplay_enabled
from modules.fishing import start_fishing, FishingTicker
from modules.stats import SESSION
from modules import health

# Where the bar, the bin and the storm dial were last seen this session - HUD elements,
# they stay put - and the bin-to-bar distance read from disk (False: not read yet).
_SEEN = {"bar": None, "bin": None, "offset": False, "dial": None}


def _find(template, shot=None):
    shot = shot if shot is not None else capture_screen()
    return find_template(shot, template, config.MATCH_THRESHOLD)


def _roi(shot, center, reach):
    """The part of the frame within `reach` reference px of `center`, and its top-left."""
    s = config.render_scale()
    cx, cy, r = center[0] * s, center[1] * s, reach * s
    x0, y0 = max(0, int(cx - r)), max(0, int(cy - r))
    return shot[y0:int(cy + r), x0:int(cx + r)], (x0, y0)


def _find_near(shot, template, center, half):
    """The template within `half` (+ a margin) reference px of `center`, or None."""
    return find_template(_roi(shot, center, half + 12)[0], template, config.MATCH_THRESHOLD)


def _score_near(shot, template, center, half):
    found = score_template(_roi(shot, center, half + 12)[0], template)
    return found[2] if found else 0.0


# --- The bar's position, kept between runs ------------------------------------------------

def _offset_file():
    return os.path.join(settings.base_dir(), "sharkfang_bar.json")


def _saved_bin_offset():
    if _SEEN["offset"] is False:
        try:
            with open(_offset_file(), encoding="utf-8") as f:
                dx, dy = json.load(f)["bin_to_bar"]
            _SEEN["offset"] = (float(dx), float(dy))
        except Exception:
            _SEEN["offset"] = None
    return _SEEN["offset"]


def _save_bin_offset(bar, bin_btn):
    offset = (float(bar[0] - bin_btn[0]), float(bar[1] - bin_btn[1]))
    if _saved_bin_offset() == offset:
        return
    _SEEN["offset"] = offset
    try:
        with open(_offset_file(), "w", encoding="utf-8") as f:
            json.dump({"bin_to_bar": list(offset)}, f)
    except Exception as e:
        print(f"[Sharkfang] Couldn't save where the bar is ({e}) - it'll be found again next time.")


# --- Joining -----------------------------------------------------------------------------

def click_events():
    return _click_through(config.SHARKFANG_EVENTS_BTN, "sharkfang_events",
                          (config.SHARKFANG_TIDAL_SIEGE_CARD, config.SHARKFANG_EVENT_GAMEMODE_BTN))


def click_tidal_siege():
    return _click_through(config.SHARKFANG_TIDAL_SIEGE_CARD, "sharkfang_tidal_siege",
                          (config.SHARKFANG_EVENT_GAMEMODE_BTN,))


def click_event_gamemode():
    return _click_through(config.SHARKFANG_EVENT_GAMEMODE_BTN, "sharkfang_event_gamemode",
                          (config.SHARKFANG_EVENT_MODE_CARD, config.SHARKFANG_SELECT_STAGE_BTN))


def click_event_mode():
    """Mode Selection's Event Mode card (not Portal Mode)."""
    return _click_through(config.SHARKFANG_EVENT_MODE_CARD, "sharkfang_event_mode",
                          (config.SHARKFANG_SELECT_STAGE_BTN,))


def click_select_stage():
    return _click_through(config.SHARKFANG_SELECT_STAGE_BTN, "sharkfang_select_stage",
                          (config.SHARKFANG_START_BTN, config.START_PARTY_BTN))


def click_start():
    """The party screen's Start - the event's own look, or the ordinary Start Party button."""
    if not _find(config.SHARKFANG_START_BTN) and _find(config.START_PARTY_BTN):
        from modules.match_start import click_start_party
        return click_start_party()
    return _click_through(config.SHARKFANG_START_BTN, "sharkfang_start")


_JOIN_STEPS = [click_events, click_tidal_siege, click_event_gamemode, click_event_mode, click_select_stage,
               click_start]
# Each menu screen, latest first, with where in _JOIN_STEPS to carry on from.
_RESUME_AT = [
    (config.SHARKFANG_START_BTN, 5),
    (config.SHARKFANG_SELECT_STAGE_BTN, 4),
    (config.SHARKFANG_EVENT_MODE_CARD, 3),
    (config.SHARKFANG_EVENT_GAMEMODE_BTN, 2),
    (config.SHARKFANG_TIDAL_SIEGE_CARD, 1),
]


# --- The run ---------------------------------------------------------------------------

class SharkfangRunner(MonsterClashRunner):
    def __init__(self, walk_preset_name, cast_pos, equip_rod, set_phase=None):
        super().__init__(FARM, walk_preset_name, set_phase=set_phase)
        self.cast_pos = cast_pos
        # See engine.run_portal(): the rod button is a toggle, only ever clicked when asked to.
        self.rod_equipped = not equip_rod
        self.fishing = False              # cast at the spot this match
        self.match_started_at = time.time()
        self.restart_reason = None        # why play_match() ended: OBTAINED / MATCH_LIMIT / MATCH_OVER
        self._warned_no_bar = False
        self._warned_unsure = False
        self._reported_tooth = None
        self._storm_logged_at = 0.0

    # --- getting onto the stage ---------------------------------------------------------
    def _join(self, steps=None):
        self._phase("JOINING TIDAL SIEGE")
        print("[Sharkfang] Joining Tidal Siege - Event Gamemode...")
        result = _run_steps(steps or _JOIN_STEPS)
        if result is True:
            self.position = AT_SPAWN
        return result

    def navigate(self):
        """
        Onto the stage from wherever the game is. True (Start Game next) / "IN_MATCH" /
        "ENDED" (a restart is due - restart_reason says why) / False / "RECONNECTED".
        """
        from modules.lobby import at_lobby, return_to_lobby
        self.fishing = False
        shot = capture_screen()
        if handle_disconnect_if_present(shot):
            return "RECONNECTED"
        if at_lobby(shot):
            return self._join()
        for template, step in _RESUME_AT:
            if _find(template, shot):
                print("[Sharkfang] In the event menus - carrying on from there.")
                return self._join(_JOIN_STEPS[step:])
        if _find(config.START_PARTY_BTN, shot):
            return self._join([click_start])
        if _find(config.SHARKFANG_OBTAINED, shot):
            print("[Sharkfang] The Sharkfang reward is on screen - restarting the match.")
            self.restart_reason = "OBTAINED"
            return "ENDED"
        if _find(config.REPEAT_STAGE_BTN, shot):
            print("[Sharkfang] The match is over.")
            self.restart_reason = "MATCH_OVER"
            return "ENDED"
        if _find(config.START_GAME_BTN, shot):
            print("[Sharkfang] On the stage, Start Game is up.")
            self.position = UNKNOWN
            return True
        if _find(config.AUTOPLAY_ON_BTN, shot) or _find(config.AUTOPLAY_OFF_BTN, shot):
            print("[Sharkfang] A match is already running - picking it up from here.")
            self.position = UNKNOWN
            self.match_started_at = time.time()
            return "IN_MATCH"
        path = health.save_debug_screenshot("sharkfang_unknown_screen", shot)
        print(f"[Sharkfang] Not at the lobby or on the stage (screen saved: {path}) - going back to the lobby.")
        if not return_to_lobby(timeout=120.0):
            return False
        return self._join()

    # --- the fishing spot -----------------------------------------------------------------
    def _to_fishing_spot(self):
        """
        Camera and the Summer fishing walk. Already on the spot (after a restart, which keeps
        the character and camera) there's nothing to do; just joined, it walks from spawn;
        anywhere else, it teleports to spawn first. True / False / "RECONNECTED".
        """
        if self.position == AT_SPOT:
            return True
        if self.position == AT_SPAWN:
            self._phase("ANCHORING CAMERA")
            anchor_camera()
            time.sleep(0.5)
        else:
            print("[Sharkfang] Not sure where the character is - teleporting to spawn before the walk.")
            result = self._spawn._reset_to_spawn(halt=False)     # re-anchors the camera itself
            if result in (False, "RECONNECTED"):
                return result
            if result is None:
                print("[Sharkfang] Couldn't teleport to spawn - walking from here anyway.")
                anchor_camera()
                time.sleep(0.5)

        self._phase("WALKING TO FISHING SPOT")
        print("[Sharkfang] Walking to the fishing spot (the Summer portal's fishing walk)...")
        result = play_preset(config.PORTAL_PRESET_LOCATION, config.SHARKFANG_WALK_CATEGORY,
                             self.walk_preset_name, skip_movement=False)
        if result == "RECONNECTED" or (result is False and config.STOP_REQUESTED):
            return result
        if result is not True:
            print("[Sharkfang] The Summer portal's fishing walk isn't recorded - fishing from where the character "
                  "is. Record it on the Portals page (Summer).")
        self.position = AT_SPOT
        return True

    def _start_fishing(self):
        self._phase("FISHING", "#2e7d32")
        time.sleep(0.4)
        self.rod_equipped = start_fishing(already_equipped=self.rod_equipped, cast_pos=self.cast_pos)
        if not self.rod_equipped:
            print("[Sharkfang] WARNING: the fishing button wasn't on screen, so the rod isn't equipped and this "
                  "match isn't fishing. Is your Auto Rod equipped?")
        self.fishing = True

    def prepare_and_start(self):
        """Stage loaded -> fishing spot -> Auto Play on -> Start Game -> cast."""
        self._phase("WAITING FOR START GAME")
        result = wait_for_start_game()
        if result in (False, "RECONNECTED"):
            return result
        result = self._to_fishing_spot()
        if result is not True:
            return result
        time.sleep(0.4)
        self._phase("CHECKING AUTO PLAY", "#2e7d32")
        if not ensure_autoplay_enabled():
            print("[Sharkfang] WARNING: couldn't find the Auto Play button - carrying on.")
        self._phase("STARTING MATCH", "#2e7d32")
        if not click_start_game():
            print("[Sharkfang] Start Game wasn't there to click - the match has probably started already.")
        SESSION.match_started()
        self.match_started_at = time.time()
        self._start_fishing()
        return True

    # --- the catch bar ------------------------------------------------------------------
    @staticmethod
    def _slot_center(bar, k):
        return (bar[0] + config.SHARKFANG_FIRST_SLOT_DX + config.SHARKFANG_SLOT_PITCH * k,
                bar[1] + config.SHARKFANG_SLOT_DY)

    def _bin(self, shot):
        found = _find(config.SHARKFANG_BIN_BTN, shot)
        if found:
            _SEEN["bin"] = (found[0], found[1])
        return _SEEN["bin"]

    def _bar_by_slots(self, shot):
        """
        The bar from its empty slots, when some are full. Every spot an empty-slot crop
        scores well is a guess at the bar; the guess where the most slots match their OWN
        crop wins (a neighbour's crop never reaches 0.88, the right one does).
        """
        best, best_key = None, (0, 0.0)
        for k, template in enumerate(config.SHARKFANG_SLOTS):
            for x, y, _ in find_all_templates(shot, template, 0.75)[:3]:
                guess = (x - config.SHARKFANG_FIRST_SLOT_DX - config.SHARKFANG_SLOT_PITCH * k,
                         y - config.SHARKFANG_SLOT_DY)
                scores = [_score_near(shot, config.SHARKFANG_SLOTS[j], self._slot_center(guess, j),
                                      config.SHARKFANG_SLOT_HALF) for j in range(6)]
                key = (sum(s >= 0.88 for s in scores), sum(scores))
                if key > best_key:
                    best, best_key = guess, key
        return best if best_key[0] >= 1 else None

    def _bar_center(self, shot):
        """
        The bar's centre (reference px), or None. Once found it's remembered (it never moves),
        so this costs nothing after the first time - a whole-screen search is slow on a slow
        PC (0.4s each, live 2026-10-20). Found whole, from the bin, or from its empty slots.
        """
        if _SEEN["bar"] is not None:
            return _SEEN["bar"]
        whole = _find(config.SHARKFANG_EMPTY_BAR, shot)
        if whole:
            _SEEN["bar"] = (whole[0], whole[1])
        else:
            bin_btn, offset = self._bin(shot), _saved_bin_offset()
            if bin_btn and offset:
                _SEEN["bar"] = (bin_btn[0] + offset[0], bin_btn[1] + offset[1])
            else:
                _SEEN["bar"] = self._bar_by_slots(shot)
        if _SEEN["bar"]:
            print(f"[Sharkfang] Found the bar at ({_SEEN['bar'][0]:.0f}, {_SEEN['bar'][1]:.0f}).")
            bin_btn = self._bin(shot)
            if bin_btn:
                _save_bin_offset(_SEEN["bar"], bin_btn)
        return _SEEN["bar"]

    def _slot_state(self, shot, bar, k):
        """
        "empty", "tooth", "unsure" (looks a bit like the tooth - never binned, never used) or
        "item" (anything else - binned).
        """
        center = self._slot_center(bar, k)
        # The tooth first: whatever else this slot might look like, a tooth is never binned.
        tooth = _score_near(shot, config.SHARKFANG_TOOTH, center, config.SHARKFANG_SLOT_HALF)
        if tooth >= config.TEMPLATE_THRESHOLDS[config.SHARKFANG_TOOTH]:
            return "tooth"
        if tooth >= config.SHARKFANG_TOOTH_CAUTION:
            return "unsure"
        if _find_near(shot, config.SHARKFANG_SLOTS[k], center, config.SHARKFANG_SLOT_HALF):
            return "empty"
        if self._looks_empty(shot, center):
            return "empty"
        return "item"

    @staticmethod
    def _looks_empty(shot, center):
        """Dark and flat, like an empty slot - see SHARKFANG_EMPTY_MAX_BRIGHTNESS."""
        inside = _roi(shot, center, config.SHARKFANG_SLOT_HALF - 12)[0]
        if inside.size == 0:
            return False
        value = cv2.cvtColor(inside, cv2.COLOR_BGR2HSV)[:, :, 2]
        return (float(value.mean()) < config.SHARKFANG_EMPTY_MAX_BRIGHTNESS
                and float((value > 170).mean()) < config.SHARKFANG_EMPTY_MAX_BRIGHT_SHARE)

    def _tooth_slot(self, shot):
        bar = self._bar_center(shot)
        if bar is None:
            return None
        for k in range(6):
            if self._slot_state(shot, bar, k) == "tooth":
                return k
        return None

    def _slot_pixels(self, shot, bar, k):
        return _roi(shot, self._slot_center(bar, k), config.SHARKFANG_SLOT_HALF - 4)[0].astype("int16")

    def _clear_bar(self, shot):
        """
        Drags everything that isn't the tooth onto the bin. Always the rightmost one first,
        so nothing slides along into the slot just emptied - taking the leftmost made the
        next item slide into it, which looked like the drag hadn't worked and got the same
        slot dragged three times (live 2026-10-20). A drag counts as done when the bar holds
        one item fewer or that slot visibly changed; only a drag that changed nothing is
        tried once more, slower. Returns (bar found, tooth's slot or None).
        """
        bar = self._bar_center(shot)
        if bar is None:
            if not self._warned_no_bar:
                self._warned_no_bar = True
                path = health.save_debug_screenshot("sharkfang_no_bar", shot)
                print(f"[Sharkfang] Can't find the bar yet (all 6 slots full and never seen before) - trying again "
                      f"shortly (screen saved: {path}).")
            return False, None
        states = [self._slot_state(shot, bar, k) for k in range(6)]
        if "item" in states:
            self._phase("CLEARING THE BAR", "#2e7d32")
        retried = False
        for _ in range(8):
            junk = [k for k, state in enumerate(states) if state == "item"]
            if not junk or config.STOP_REQUESTED:
                break
            bin_btn = self._bin(shot)
            if not bin_btn:
                path = health.save_debug_screenshot("sharkfang_no_bin", shot)
                print(f"[Sharkfang] The bar has junk in it but the bin isn't on screen (screen saved: {path}).")
                break
            k = junk[-1]
            shot = capture_screen()
            if self._slot_state(shot, bar, k) != "item":
                states = [self._slot_state(shot, bar, j) for j in range(6)]
                continue
            before = self._slot_pixels(shot, bar, k)
            x, y = self._slot_center(bar, k)
            if retried:
                print(f"[Sharkfang] Slot {k + 1} didn't empty - dragging it once more, slower.")
                drag_to(x, y, bin_btn[0], bin_btn[1], steps=30, hold=0.35)
            else:
                print(f"[Sharkfang] Slot {k + 1}: not the tooth - dragging it onto the bin.")
                drag_to(x, y, bin_btn[0], bin_btn[1])
            shot = capture_screen()
            new_states = [self._slot_state(shot, bar, j) for j in range(6)]
            after = self._slot_pixels(shot, bar, k)
            changed = before.shape == after.shape and float(abs(before - after).mean()) > 12.0
            took = sum(s != "empty" for s in new_states) < sum(s != "empty" for s in states) or changed
            states = new_states
            if took:
                retried = False
            elif retried:
                path = health.save_debug_screenshot("sharkfang_drag_failed", shot)
                print(f"[Sharkfang] Slot {k + 1} won't go in the bin (screen saved: {path}) - trying again at the "
                      f"next check.")
                break
            else:
                retried = True
        tooth = states.index("tooth") if "tooth" in states else None
        if tooth != self._reported_tooth:
            self._reported_tooth = tooth
            if tooth is not None:
                print(f"[Sharkfang] The tooth is in slot {tooth + 1} - keeping it for the storm.")
        if "unsure" in states and not self._warned_unsure:
            self._warned_unsure = True
            path = health.save_debug_screenshot("sharkfang_unsure_slot", shot)
            print(f"[Sharkfang] Slot {states.index('unsure') + 1} looks a bit like the tooth - leaving it alone to "
                  f"be safe (screen saved: {path}).")
        self._phase("FISHING", "#2e7d32")
        return True, tooth

    # --- the storm and the tooth ----------------------------------------------------------
    def _storm_ready(self, shot):
        """
        True only when the storm dial is exactly storm.png: shape and colour (the template
        match itself), then each quarter's colour on its own - the top right purple, the
        rest gold.
        """
        if _SEEN["dial"] is not None:
            # It never moves: look only around where it was.
            area, (x0, y0) = _roi(shot, _SEEN["dial"], config.SHARKFANG_STORM_SIZE)
            found = find_template(area, config.SHARKFANG_STORM, config.MATCH_THRESHOLD)
            s = config.render_scale()
            dial = (found[0] + x0 / s, found[1] + y0 / s, found[2]) if found else None
        else:
            dial = _find(config.SHARKFANG_STORM, shot)
            if dial:
                _SEEN["dial"] = (dial[0], dial[1])
        if not dial:
            return False
        return self._storm_colours_ok(shot, dial)

    def _storm_progress_note(self, shot):
        """While a tooth waits: the closest the dial has come, every 20s, for the log."""
        if time.time() - self._storm_logged_at < 20.0:
            return
        self._storm_logged_at = time.time()
        best = score_template(shot, config.SHARKFANG_STORM)
        print(f"[Sharkfang] Waiting for the storm with the tooth (dial match {best[2] if best else 0.0:.2f}, needs "
              f"{config.TEMPLATE_THRESHOLDS[config.SHARKFANG_STORM]}).")

    def _storm_colours_ok(self, shot, dial):
        """Every point in SHARKFANG_STORM_POINTS the colour it must be, on the dial as found."""
        s = config.render_scale()
        size = config.SHARKFANG_STORM_SIZE * s
        left, top = dial[0] * s - size / 2, dial[1] * s - size / 2
        for (px, py), colour in config.SHARKFANG_STORM_POINTS:
            x, y = int(round(left + px * s)), int(round(top + py * s))
            patch = shot[max(0, y - 2):y + 3, max(0, x - 2):x + 3].reshape(-1, 3).mean(axis=0)
            b, g, r = (float(v) for v in patch)
            ok = (b > r + 30 and b > g + 30) if colour == "purple" else (r > b + 30)
            if not ok:
                if time.time() - self._storm_logged_at >= 20.0:
                    self._storm_logged_at = time.time()
                    print(f"[Sharkfang] The storm dial is up but the purple quarter isn't exactly at the top right "
                          f"yet - not using the tooth.")
                return False
        return True

    def _press_tooth(self, slot):
        x, y = self._slot_center(_SEEN["bar"], slot)
        self._phase("USING THE TOOTH", "#f9a825")
        click_at(x, y)

    # --- the match ------------------------------------------------------------------------
    def play_match(self):
        """
        Fishes until a restart is due: the Sharkfang obtained, 18 minutes without a tooth,
        or the match over. Returns "ENDED" (restart_reason set), "RECONNECTED" or False.
        """
        if not self.fishing:
            # Picked up mid-match (the run was restarted): get to the spot and cast first.
            result = self._to_fishing_spot()
            if result is not True:
                return result
            if not ensure_autoplay_enabled():
                print("[Sharkfang] WARNING: couldn't find the Auto Play button - carrying on.")
            self._start_fishing()

        self._phase("FISHING", "#2e7d32")
        ticker = FishingTicker(cast_pos=self.cast_pos)
        state = {"next_bar": time.time() + config.SHARKFANG_BAR_FIRST_CHECK_SECONDS, "tooth": None,
                 "pressed_at": None, "presses": 0, "said_waiting": False}
        print(f"[Sharkfang] Fishing. The bar gets cleared {config.SHARKFANG_BAR_FIRST_CHECK_SECONDS / 60:.0f} minutes "
              f"from now, then checked every {config.SHARKFANG_BAR_CHECK_SECONDS:.0f} seconds.")

        def after_press(shot, now):
            """While waiting for the reward: press again if the tooth is still there."""
            if state["presses"] < 3 and now - state["pressed_at"] >= 4.0 * state["presses"]:
                slot = self._tooth_slot(shot)
                if slot is not None:
                    state["presses"] += 1
                    print(f"[Sharkfang] The tooth is still in the bar - pressing it again "
                          f"({state['presses']}/3).")
                    self._press_tooth(slot)
            if now - state["pressed_at"] >= config.SHARKFANG_OBTAIN_WAIT:
                path = health.save_debug_screenshot("sharkfang_no_reward", shot)
                print(f"[Sharkfang] No Sharkfang {config.SHARKFANG_OBTAIN_WAIT / 60:.0f} minutes after using the "
                      f"tooth (screen saved: {path}) - back to fishing.")
                state["pressed_at"] = None
                state["next_bar"] = now

        def watch(shot):
            now = time.time()
            if _find(config.SHARKFANG_OBTAINED, shot):
                return "OBTAINED"
            if state["pressed_at"] is not None and _find(config.SHARKFANG_OBTAINED_TITLE, shot):
                return "OBTAINED"
            if _find(config.REPEAT_STAGE_BTN, shot) or _find(config.DEFEAT_TEXT, shot):
                return "MATCH_OVER"
            if state["pressed_at"] is not None:
                after_press(shot, now)
                return None
            # The tooth is looked for every poll (six small looks at the bar - quick), and
            # the storm only while there is one: a tooth caught between bar checks is used
            # in its storm all the same.
            if _SEEN["bar"] is not None:
                tooth = self._tooth_slot(shot)
                if tooth is not None and tooth != self._reported_tooth:
                    self._reported_tooth = tooth
                    print(f"[Sharkfang] The tooth is in slot {tooth + 1} - keeping it for the storm.")
                state["tooth"] = tooth
            if state["tooth"] is not None:
                if self._storm_ready(shot):
                    print(f"[Sharkfang] The storm is here and the tooth is in slot {state['tooth'] + 1} - using it.")
                    self._press_tooth(state["tooth"])
                    state.update(pressed_at=now, presses=1, tooth=None)
                    self._reported_tooth = None
                    return None
                self._storm_progress_note(shot)
            if now >= state["next_bar"]:
                found, state["tooth"] = self._clear_bar(shot)
                state["next_bar"] = time.time() + config.SHARKFANG_BAR_CHECK_SECONDS
            if now - self.match_started_at >= config.SHARKFANG_MATCH_LIMIT_SECONDS:
                if state["tooth"] is None:
                    state["tooth"] = self._tooth_slot(shot)
                if state["tooth"] is None:
                    return "MATCH_LIMIT"
                if not state["said_waiting"]:
                    state["said_waiting"] = True
                    print(f"[Sharkfang] {config.SHARKFANG_MATCH_LIMIT_SECONDS / 60:.0f} minutes in, but there's a "
                          f"tooth in the bar - staying until the storm comes to use it.")
            return None

        result = poll_until([], interval=2.0, label="sharkfang", stuck_timeout=None, on_tick=ticker.tick,
                            custom_check=watch, lobby_grace=20.0, game_results=False)
        if result in (False, "RECONNECTED"):
            return result
        if result == "OBTAINED":
            self._close_reward()
            self.consecutive_defeats = 0
            SESSION.victory()
            print(f"[Sharkfang] SHARKFANG OBTAINED! ({SESSION.victories} this run) - restarting the match.")
            try:
                from modules import notify
                notify.send(f"Sharkfang obtained ({SESSION.victories} this run).", category="milestones",
                            title="Sharkfang obtained", good=True, image_bytes=health.jpg_bytes(capture_screen()))
            except Exception as e:
                print(f"[notify] Couldn't send the Sharkfang message: {e}")
        elif result == "MATCH_LIMIT":
            print(f"[Sharkfang] {config.SHARKFANG_MATCH_LIMIT_SECONDS / 60:.0f} minutes and no tooth - restarting "
                  f"the match.")
        elif result == "MATCH_OVER":
            print("[Sharkfang] The match ended - starting it again.")
        else:
            print(f"[Sharkfang] Stopped watching the match unexpectedly ({result}) - checking where we are.")
            return "RECONNECTED"
        self.restart_reason = result
        return "ENDED"

    def _close_reward(self):
        """Clicks the Obtained Rewards popup away, on its own "(Click anywhere to close)" line."""
        for _ in range(4):
            shot = capture_screen()
            card = _find(config.SHARKFANG_OBTAINED, shot)
            title = card or _find(config.SHARKFANG_OBTAINED_TITLE, shot)
            if not title:
                return
            x, y = (card[0], card[1] + config.SHARKFANG_OBTAINED_CLOSE_DY) if card else \
                (config.ANTI_IDLE_X, config.ANTI_IDLE_Y)
            print("[Sharkfang] Closing the reward popup.")
            click_at(x, y)
            time.sleep(1.0)
        print("[Sharkfang] The reward popup is still up - the restart will click it away.")

    # --- restarting ---------------------------------------------------------------------
    def _restart(self):
        """Settings -> Restart -> Restart. True / "LOST" / False / "RECONNECTED"."""
        self._phase("RESTARTING MATCH", "#f9a825")
        for _ in range(10):
            if config.STOP_REQUESTED:
                return False
            shot = capture_screen()
            if handle_disconnect_if_present(shot):
                return "RECONNECTED"
            confirm = _find(config.SHARKFANG_RESTART_CONFIRM_BTN, shot)
            if confirm:
                print("[Sharkfang] Confirming the restart.")
                if click_until_gone(config.SHARKFANG_RESTART_CONFIRM_BTN, confirm, "sharkfang_restart_confirm"):
                    return True
                continue
            restart = _find(config.SHARKFANG_RESTART_BTN, shot)
            if restart:
                print("[Sharkfang] Clicking Restart.")
                click_at(restart[0], restart[1])
                poll_until([target(config.SHARKFANG_RESTART_CONFIRM_BTN, True)], interval=0.5,
                           label="sharkfang_restart_confirm_wait", timeout=5.0, stuck_timeout=None)
                continue
            gear = _find(config.SHARKFANG_SETTINGS_BTN, shot) or _find(config.SETTINGS_BTN, shot)
            if gear:
                # With the reward popup up, this first click only closes it - the next round
                # finds the gear again and opens Settings.
                print("[Sharkfang] Opening Settings.")
                click_at(gear[0], gear[1])
                time.sleep(1.2)
                continue
            # Something covers the gear - most likely the reward popup ("click anywhere to close").
            click_at(config.ANTI_IDLE_X, config.ANTI_IDLE_Y)
            time.sleep(1.0)
        path = health.save_debug_screenshot("sharkfang_restart_failed")
        print(f"[Sharkfang] Couldn't restart the match from Settings (screen saved: {path}) - working out "
              f"where we are.")
        return "LOST"

    def farm_next(self):
        """Restart the match (or Repeat Stage if it ended), then Auto Play, Start Game, fishing."""
        reason, self.restart_reason = self.restart_reason, None
        self.fishing = False
        repeat = _find(config.REPEAT_STAGE_BTN) if reason == "MATCH_OVER" else None
        if repeat:
            print("[Sharkfang] Clicking Repeat Stage.")
            click_until_gone(config.REPEAT_STAGE_BTN, repeat, "sharkfang_repeat")
            self.position = UNKNOWN          # a finished match may not keep the character where it was
        else:
            result = self._restart()
            if result is not True:
                return result
        return self.prepare_and_start()
