# modules/halloween.py
"""
Halloween Event (Events page, added 2026-11-01). LIMITED EVENT ("Ends Update 6") - when it
ends, delete this module, its config block (HALLOWEEN_*), assets/templates/Halloween/,
run_halloween() in engine.py and the Halloween option on the Events page.

As described by the player (2026-11-01):
  Lobby -> Events -> Halloween Event -> (Event Gamemode) -> Select Stage -> Start -> Auto
  Play on -> Start Game. Once the stage counter at the top (the circle left of "Heat Bars")
  shows the Heat the player picked on the Events page (5, 6 or 7 - the game won't sell the boss
  below Heat 5): Forge ->
  scroll down a bit to the Final Boss card -> its Buy -> the Forge's Close (the same X as the
  other panels). The boss ends the run; on a win, Repeat Stage -> Auto Play checked -> Start Game, and again.

Fallbacks:
  - The Final Boss's Buy is only looked for under the Final Boss card's own top: every card
    in the Forge has the same "Buy 500" button.
  - The card not in view after Forge: scrolled down a little at a time, then back up.
  - Auto-buy (2026-11-06): every Forge card has its own auto-buy button, and the game buys that
    card when it can until the connection drops. With the player's auto-buy option on, the Final
    Boss's is turned on at the start of every match, and only that card's. If the Heat goes past
    5 and the match is still running, the Forge is checked and the macro buys it if the game
    didn't. With the option off, the button is kept off and the macro buys at the picked Heat.
  - The boss is bought ONCE per match. Since the 2026-11-03 patch the stages keep
    going after the buy and the match can run a long time, so neither the stage going up nor
    a long wait means the buy failed - re-buying only threw candy away (live 2026-11-03: it
    kept buying until the candy ran out).
  - Forge clicked again while its panel doesn't open; a buy that failed is retried 10s later.
  - Works with the game's Auto Start / Auto Retry ON or OFF: with Auto Retry the stage counter
    dropping back (or the next match running) is the end of the match, nothing to click.
  - The stage is read from the white digit in the circle (any circle colour). The picked Heat
    or later on two looks in a row buys the boss - so a match picked up already running (after
    a restart) buys only once it's really there. The whole circle's picture is only a backup for
    when the digit can't be read: a digit that was read always wins.
  - Digits with no saved sample are learned during a match: the counter showing a glyph none
    of the samples know, steady for a few looks and unlike the stage before it, is that stage
    + 1 (6 -> 7 -> 8). Learned digits are saved next to the settings (stage_digits_learned/),
    so the next run knows them from the start.
  - Start Game seen while a match is being watched gets pressed, so a match is never left
    unstarted; after Repeat Stage it's told apart from Auto Start straight away.
  - Anything unexpected goes back through navigate(), which reads the screen: a readable stage
    counter means a match is running; a panel over the screen is closed and it looks again;
    only then does it go back to the lobby.
"""
import os
import time

import cv2
import numpy as np

import config
from vision import capture_screen, find_template
from input_controller import click_at, scroll_maps_left, scroll_maps_right
from modules.monster_clash import _click_through
from modules.gamemode_select import _run_steps
from modules.match_start import click_start_party
from modules.polling import poll_until, target, click_until_gone, click_stage_button
from modules.reconnect import handle_disconnect_if_present
from modules.stage_player import wait_for_start_game, click_start_game
from modules.autoplay import ensure_autoplay_enabled
from modules.stats import SESSION
from modules import health

TAG = "Halloween"
# A match shorter than this can't have ended - an end screen left over is ignored until then.
MIN_MATCH = 25.0
# A match that restarted sooner than this without the boss isn't counted as a defeat - reaching
# the boss's stage alone takes minutes, so this was a misread or a leftover, not a real loss.
MIN_MATCH_FOR_DEFEAT = 60.0
# After a match: how long to wait for Repeat Stage before working out where the game is.
AFTER_MATCH_WAIT = 45.0
# After Forge: how long the Final Boss card gets to show before scrolling for it.
FORGE_OPEN_WAIT = 2.5
# Scrolling the Forge for the card: notches per step, and steps each way.
FORGE_SCROLL_NOTCHES = 3
FORGE_SCROLL_STEPS = 8
# Where the cursor goes to scroll the Forge (the middle of the screen, over its cards).
FORGE_HOVER = (800, 450)
# Where the card's buttons sit, from the centre of its name-and-text crop (reference px). Each
# card has a narrow Buy and, beside it, its own auto-buy button (added 2026-11-06).
BUY_DX, BUY_DY = -28, 78
AUTO_DX, AUTO_DY = 65, 78
# The Forge's list ends at y=726, so a Buy centred lower than this is cut off. A blind click at
# that edge landed on the unit bar (live 2026-11-04) and picked up a unit instead.
FORGE_BUY_MAX_Y = 695
# Extra scrolls to bring a Buy that's below the list's edge into view.
FORGE_BUY_SCROLLS = 3
# Buy is green (can be bought) when this share of its pixels is clearly green: 0.63 on a green
# button, 0.00 on a grey one.
BUY_GREEN_SHARE = 0.35
BUY_HALF_W, BUY_HALF_H = 55, 15     # the area measured around the button's centre (ref px)
# With Buy still grey at the picked Heat: how long to give it to turn green before trying later.
BUY_GREEN_WAIT = 5.0
# The auto-buy button is teal while on (with a number on it) and dark grey while off: 0.50 of
# its pixels are teal when on, 0.00 when off, measured on live frames at any Heat.
AUTO_HALF = 20
AUTO_ON_SHARE = 0.25
AUTO_OFF_SHARE = 0.05
AUTO_TOGGLE_TRIES = 3
# "Forging" over the card means the boss is already paid for, whoever bought it.
FORGING_DY = -165
FORGING_REACH = 60
# Setting auto-buy when a match begins: how often to try while the Forge isn't reachable yet.
AUTO_SET_RETRY = 15.0
AUTO_SET_TRIES = 5
# Bought, then the stage read lower than the stage bought at this many times (not necessarily
# in a row - unreadable looks in between don't matter): that match is over and the next one
# is running (the end screen was missed). Counted from the buy; a read at or above the bought
# stage starts the count over, so stray misreads over a long match never add up.
STAGE_LOWER_AFTER_BUY = 3
# The stage number is read from the white digit in the stage circle, so the circle's colour
# doesn't matter - Stage_4.png is the whole circle and stopped matching once the circle wasn't
# the green it was captured in (live 2026-11-02: past stage 4 to 5 without buying). Digits
# cut from live frames at the reference size. 4 and 5 were re-cut on 2026-11-04 (the old 4 scored
# 0.66 on live frames). The re-cut ones score under 0.47 against every other digit. 3 and 7 are
# the ones the macro learned live. The lower digits stay even though the boss isn't sold there:
# reading them is how the macro knows the stage started over.
STAGE_DIGITS_DIR = "assets/digits_stage"
STAGE_DIGIT_MIN = 0.80          # a digit read below this counts as unreadable
# The Heats the Final Boss can be bought at, as offered on the Events page. The game doesn't sell
# it below 5. To offer another: add it here and to config.HALLOWEEN_HEAT_PICS, and cut its digit.
BOSS_HEATS = (5, 6, 7)
DEFAULT_BOSS_HEAT = 5
STAGE_READS_NEEDED = 2          # the picked Heat or later read this many looks in a row before buying
# Learning digits that have no sample (see the module docstring).
LEARNED_DIR_NAME = "stage_digits_learned"
LEARNED_MIN = 0.90              # a learned digit has to match this well to count as read
LEARN_LOOKS = 3                 # the unknown glyph steady this many looks in a row
LEARN_SAME = 0.85               # "steady": each look this close to the first
LEARN_UNLIKE = 0.70             # and below this against the stage before it (not the same digit)
STAGE_GLYPH_REACH = 40          # ref px around the circle's centre to look for the digit
# No stage number read this long into a match: a screenshot is saved once, to fix the read.
STAGE_UNREADABLE_AFTER = 90.0
# The stage read lower than the highest seen this match, this many looks in a row: the game
# started the stage over by itself (Auto Retry) - the match is over.
STAGE_DOWN_LOOKS = 3
# A buy that didn't get as far as clicking Buy: tried again this long after.
BUY_FAIL_COOLDOWN = 10.0
# Clicks on Forge before giving up on it opening.
FORGE_OPEN_TRIES = 3
_REPEATS = (config.HALLOWEEN_REPEAT_STAGE_BTN, config.REPEAT_STAGE_BTN)
_CLOSES = (config.HALLOWEEN_CLOSE_BTN, config.CLOSE_BTN, config.SETTINGS_CLOSE_BTN)
# After Repeat Stage: how long to tell "Start Game is up" (Auto Start off) from "the match
# already started" (Auto Start on). The shared Start Game wait takes 45s to believe the latter
# (START_GAME_LINGER_GRACE) on a stage that never went away, which a repeat is.
AFTER_REPEAT_WAIT = 20.0
# While watching a match: Start Game up this many looks in a row gets pressed (at most every
# START_PRESS_EVERY seconds) - a match is never left sitting unstarted, whatever Auto Start is.
START_UP_LOOKS = 2
START_PRESS_EVERY = 5.0
# A screen nothing matched: this long for something known to show (loading) before leaving.
UNKNOWN_SCREEN_WAIT = 10.0
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


_generic_digits = {}       # the game font's digits from assets/digits (another size)
_circle_digits = {}        # the stage circle's own samples (assets/digits_stage)
_learned_digits = {}       # learned live (stage_digits_learned/ next to the settings)
_learned_loaded = [False]


def _learned_dir():
    import settings
    return os.path.join(settings.base_dir(), LEARNED_DIR_NAME)


def _load_stage_digits():
    if not _generic_digits:
        from modules import digits
        for ch, img in digits.load_digit_templates().items():
            _generic_digits[ch] = cv2.threshold(img, 140, 255, cv2.THRESH_BINARY)[1]
    if not _circle_digits and os.path.isdir(STAGE_DIGITS_DIR):
        for name in os.listdir(STAGE_DIGITS_DIR):
            ch = os.path.splitext(name)[0]
            img = cv2.imread(os.path.join(STAGE_DIGITS_DIR, name), cv2.IMREAD_GRAYSCALE)
            if len(ch) == 1 and ch.isdigit() and img is not None:
                _circle_digits[ch] = img
    if not _learned_loaded[0]:
        _learned_loaded[0] = True
        try:
            folder = _learned_dir()
            for name in (os.listdir(folder) if os.path.isdir(folder) else []):
                ch = os.path.splitext(name)[0]
                img = cv2.imread(os.path.join(folder, name), cv2.IMREAD_GRAYSCALE)
                if len(ch) == 1 and ch.isdigit() and ch not in _circle_digits and img is not None:
                    _learned_digits[ch] = img
        except OSError:
            pass


def _stage_glyphs(shot):
    """The stage circle's digit masks, left to right ([] if none): its near-white pixels only
    (any circle colour behind), the digit-sized blob nearest the centre - and the one beside it
    for stage 10+."""
    s = config.render_scale()
    cx, cy, r = config.HALLOWEEN_STAGE_X * s, config.HALLOWEEN_STAGE_Y * s, STAGE_GLYPH_REACH * s
    x0, y0 = max(0, int(cx - r)), max(0, int(cy - r))
    area = shot[y0:int(cy + r), x0:int(cx + r)]
    if area.size == 0:
        return []
    lo, hi = area.min(axis=2).astype(int), area.max(axis=2).astype(int)
    mask = (((lo > 200) & (hi - lo < 40)) * 255).astype(np.uint8)
    n, _, stats, centres = cv2.connectedComponentsWithStats(mask)
    blobs = []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if not (9 * s <= h <= 24 * s and 3 * s <= w <= 20 * s and a >= 25 * s * s):
            continue
        d = (centres[i][0] + x0 - cx) ** 2 + (centres[i][1] + y0 - cy) ** 2
        blobs.append((d, centres[i], mask[y:y + h, x:x + w]))
    if not blobs:
        return []
    blobs.sort(key=lambda b: b[0])
    near = blobs[0]
    pair = [b for b in blobs[1:] if abs(b[1][0] - near[1][0]) <= 16 * s and abs(b[1][1] - near[1][1]) <= 4 * s]
    return [g for _, _, g in sorted([near] + pair[:1], key=lambda b: b[1][0])]


def _classify(glyph):
    """(digit, conf) or (None, best conf): the samples at STAGE_DIGIT_MIN, learned ones at LEARNED_MIN."""
    from modules import digits
    _load_stage_digits()
    ch, conf = digits.classify_glyph(glyph, {**_generic_digits, **_circle_digits})
    if ch is None or conf < STAGE_DIGIT_MIN:
        ch = None
    if _learned_digits:
        lch, lconf = digits.classify_glyph(glyph, _learned_digits)
        if lconf >= LEARNED_MIN and (ch is None or lconf > conf):
            return lch, lconf
    return (ch, conf) if ch is not None else (None, conf)


def read_stage(shot):
    """(stage number as text or None, confidence) from the stage circle."""
    glyphs = _stage_glyphs(shot)
    if not glyphs:
        return None, 0.0
    out, worst = "", 1.0
    for glyph in glyphs:
        ch, conf = _classify(glyph)
        if ch is None:
            return None, conf
        out, worst = out + ch, min(worst, conf)
    return out, worst


class StageLearner:
    """
    Learns the circle's digits that have no sample: after stage N is read, a glyph nothing
    knows - steady for LEARN_LOOKS looks, and unlike N's own - is stage N + 1.
    """

    def __init__(self):
        self.last = None             # the last stage read (single digit)
        self.last_glyph = None
        self.cand = None
        self.looks = 0

    def look(self, shot, n):
        from modules import digits
        glyphs = _stage_glyphs(shot)
        if n is not None:
            self.cand, self.looks = None, 0
            if len(glyphs) == 1:
                self.last, self.last_glyph = n, glyphs[0]
            return None
        if self.last is None or self.last >= 9 or len(glyphs) != 1:
            return None
        nxt = str(self.last + 1)
        if nxt in _circle_digits or nxt in _learned_digits:
            return None                # a known digit just didn't read this time
        g = glyphs[0]
        if self.last_glyph is not None and digits.classify_glyph(g, {"x": self.last_glyph})[1] >= LEARN_UNLIKE:
            self.cand, self.looks = None, 0
            return None                # still stage N, drawn a little differently
        if self.cand is not None and digits.classify_glyph(g, {"x": self.cand})[1] >= LEARN_SAME:
            self.looks += 1
        else:
            self.cand, self.looks = g, 1
        if self.looks < LEARN_LOOKS:
            return None
        _learned_digits[nxt] = self.cand
        try:
            os.makedirs(_learned_dir(), exist_ok=True)
            cv2.imwrite(os.path.join(_learned_dir(), f"{nxt}.png"), self.cand)
        except (OSError, cv2.error):
            pass
        print(f"[{TAG}] Learned what the stage counter's {nxt} looks like (after {self.last}).")
        self.last, self.last_glyph = int(nxt), self.cand
        self.cand, self.looks = None, 0
        return int(nxt)


def _buy_is_green(shot, point):
    """True if the Buy button centred on `point` (ref px) is the green, buyable one."""
    s = config.render_scale()
    x0, y0 = max(0, int((point[0] - BUY_HALF_W) * s)), max(0, int((point[1] - BUY_HALF_H) * s))
    area = shot[y0:int((point[1] + BUY_HALF_H) * s), x0:int((point[0] + BUY_HALF_W) * s)]
    if area.size == 0:
        return False
    b, g, r = (area[..., i].astype(int) for i in range(3))
    return float(((g - np.maximum(r, b)) > 40).mean()) >= BUY_GREEN_SHARE


def _buy_point(card):
    """The centre of the Final Boss's Buy, from its card. Every card's Buy looks the same."""
    return card[0] + BUY_DX, card[1] + BUY_DY


def _auto_point(card):
    """The centre of the Final Boss's own auto-buy button. Taken from its card, so it can't be another's."""
    return card[0] + AUTO_DX, card[1] + AUTO_DY


def _auto_state(shot, card):
    """True if the Final Boss's auto-buy is on, False if off, None if that spot looks like neither."""
    s = config.render_scale()
    x, y = _auto_point(card)
    half = AUTO_HALF * s
    area = shot[max(0, int(y * s - half)):int(y * s + half), max(0, int(x * s - half)):int(x * s + half)]
    if area.size == 0:
        return None
    b, g, r = (area[..., i].astype(int) for i in range(3))
    teal = float((((g - r) > 30) & ((b - r) > 30)).mean())
    if teal >= AUTO_ON_SHARE:
        return True
    return False if teal <= AUTO_OFF_SHARE and 20 <= area.mean() <= 90 else None


def _is_forging(shot, card):
    """True if "Forging" shows over the Final Boss's card: it's bought and on its way."""
    return _find_near(shot, config.HALLOWEEN_FORGING_TEXT, (card[0], card[1] + FORGING_DY), FORGING_REACH) is not None


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
    def __init__(self, set_phase=None, boss_heat=DEFAULT_BOSS_HEAT, auto_buy=True):
        self.set_phase = set_phase
        self.boss_heat = boss_heat if boss_heat in BOSS_HEATS else DEFAULT_BOSS_HEAT
        # The game's auto-buy buys as soon as it can, which is Heat 5. Any later Heat has to be
        # the macro's own click, with auto-buy kept off so the game doesn't buy early.
        self.auto_buy = bool(auto_buy) and self.boss_heat == DEFAULT_BOSS_HEAT
        self.consecutive_defeats = 0
        self.resumed = False              # the next Start Game wait is for a stage never left (Repeat Stage)
        self._match_started_at = 0.0
        self._last_result = None          # VICTORY / DEFEAT of the match that just ended, None = unknown
        self._restarted = False           # the game's Auto Retry already started the next match
        self._end_stage = None            # the highest stage read in the match that just ended
        self._buy_fail_saved = False      # one "no Buy button" screenshot per match
        self._watched_from_start = False  # False when a match was picked up already running

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
            return self._pick_up(shot)
        if read_stage(shot)[0] is not None:
            # The stage counter is up, so a match is running - the Auto Play button is just
            # hidden (a panel over it).
            return self._pick_up(shot)
        if not getattr(self, "_looked_again", False):
            template, close = _find_any(_CLOSES, shot)
            if close:
                print(f"[{TAG}] A panel is open over the screen - closing it and looking again.")
                click_until_gone(template, close, "halloween_close_panel")
            else:
                print(f"[{TAG}] Nothing recognisable on screen - giving it a moment (loading?)...")
                time.sleep(UNKNOWN_SCREEN_WAIT)
            self._looked_again = True
            try:
                return self.navigate()
            finally:
                self._looked_again = False
        path = health.save_debug_screenshot("halloween_unknown_screen", shot)
        print(f"[{TAG}] Not at the lobby or on the stage (screen saved: {path}) - going back to the lobby.")
        if not return_to_lobby(timeout=120.0):
            return False
        return self._join()

    def _pick_up(self, shot):
        """A match already running: Auto Play checked, then watched from here."""
        stage, _ = read_stage(shot)
        print(f"[{TAG}] A match is already running (stage {stage or '?'}) - picking it up from here.")
        if not ensure_autoplay_enabled():
            print(f"[{TAG}] WARNING: couldn't find the Auto Play button - carrying on.")
        self._match_started_at = time.time()
        self._watched_from_start = False  # the boss may have been bought before this look
        return "IN_MATCH"

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
        self._last_result = None
        self._watched_from_start = True
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
                time.sleep(0.3 * config.SLOWNESS)    # the list glides, and a moving card can be missed
                found = _find(config.HALLOWEEN_FINAL_BOSS_TITLE)
                if found:
                    return found
        return None

    def _open_forge_and_find_card(self):
        """
        The Final Boss card's top, opening the Forge first if it isn't open - Forge is clicked
        again (up to FORGE_OPEN_TRIES) while the panel hasn't opened. Match or None.
        """
        for attempt in range(1, FORGE_OPEN_TRIES + 1):
            if config.STOP_REQUESTED:
                return None
            shot = capture_screen()
            card = _find(config.HALLOWEEN_FINAL_BOSS_TITLE, shot)
            if card:
                return self._with_buy_in_view(card)
            panel_open = _find_any(_CLOSES, shot)[0] is not None
            if not panel_open:
                forge = _find(config.HALLOWEEN_FORGE_BTN, shot)
                if not forge:
                    path = health.save_debug_screenshot("halloween_no_forge", shot)
                    print(f"[{TAG}] The Forge button isn't on screen (screen saved: {path}).")
                    return None
                print(f"[{TAG}] Opening the Forge{f' (attempt {attempt}/{FORGE_OPEN_TRIES})' if attempt > 1 else ''}.")
                click_at(forge[0], forge[1])
                time.sleep(0.8)
                if _find(config.HALLOWEEN_FINAL_BOSS_TITLE) is None and _find_any(_CLOSES, capture_screen())[0] is None:
                    continue                 # the panel didn't open - click Forge again
            card = self._find_card()
            if card:
                return self._with_buy_in_view(card)
            return None                      # the Forge is open but the card isn't in it
        print(f"[{TAG}] The Forge didn't open after {FORGE_OPEN_TRIES} clicks.")
        return None

    @staticmethod
    def _with_buy_in_view(card):
        """The card, scrolled so its Buy is inside the Forge's list. None if it can't be."""
        scrolls = 0
        while card[1] + BUY_DY > FORGE_BUY_MAX_Y:
            if config.STOP_REQUESTED or scrolls >= FORGE_BUY_SCROLLS:
                return None
            scrolls += 1
            scroll_maps_right(hover=FORGE_HOVER, notches=FORGE_SCROLL_NOTCHES)
            found = _find(config.HALLOWEEN_FINAL_BOSS_TITLE)
            if found is None:
                time.sleep(0.3)              # still sliding - one more look
                found = _find(config.HALLOWEEN_FINAL_BOSS_TITLE)
            if found is None:
                return None
            card = found
        if scrolls:
            print(f"[{TAG}] The Final Boss's Buy was below the list - scrolled it into view.")
        return card

    def _buy_final_boss(self):
        """Forge -> the Final Boss card -> its Buy -> close. True if Buy was clicked."""
        self._phase("BUYING THE FINAL BOSS", "#f9a825")
        card = self._open_forge_and_find_card()
        if not card:
            path = health.save_debug_screenshot("halloween_no_final_boss")
            print(f"[{TAG}] Couldn't get to the Final Boss card (screen saved: {path}).")
            return False
        if _is_forging(capture_screen(), card):
            # The game's auto-buy got there first. Buying again would only waste the souls.
            print(f"[{TAG}] The Final Boss is already forging - nothing to buy.")
            self._close_forge()
            self._phase("WAITING FOR MATCH END", "#4fc3f7")
            return True
        point = _buy_point(card)
        deadline = time.time() + BUY_GREEN_WAIT
        while not _buy_is_green(capture_screen(), point):
            if time.time() >= deadline or config.STOP_REQUESTED:
                # Only a green Buy gets clicked: a blind click can land on the unit bar, and a
                # grey one does nothing. False tries the whole buy again after BUY_FAIL_COOLDOWN.
                if not self._buy_fail_saved:
                    self._buy_fail_saved = True
                    path = health.save_debug_screenshot("halloween_no_buy_button")
                    print(f"[{TAG}] The Final Boss's Buy isn't green (screen saved: {path}) - not enough souls "
                          f"yet? Trying again in {BUY_FAIL_COOLDOWN:.0f}s.")
                return False
            time.sleep(0.25)
        print(f"[{TAG}] Buying the Final Boss at ({point[0]:.0f}, {point[1]:.0f}).")
        return self._click_buy(point)

    def _set_forge_auto(self, want_on):
        """
        Turns the Final Boss's auto-buy on or off, and only that card's: the button's place comes
        from the card itself. Reads it first, because a click on one that's already right would
        flip it the wrong way. True once it reads as wanted.
        """
        word = "on" if want_on else "off"
        card = self._open_forge_and_find_card()
        if not card:
            print(f"[{TAG}] Couldn't reach the Final Boss card to turn its auto-buy {word}.")
            return False
        time.sleep(0.4 * config.SLOWNESS)
        card = _find(config.HALLOWEEN_FINAL_BOSS_TITLE) or card
        done = False
        for _ in range(AUTO_TOGGLE_TRIES):
            if config.STOP_REQUESTED:
                break
            state = _auto_state(capture_screen(), card)
            if state is want_on:
                done = True
                break
            if state is None:
                path = health.save_debug_screenshot("halloween_auto_buy_unreadable")
                print(f"[{TAG}] Can't tell if the Final Boss's auto-buy is on (screen saved: {path}) - not touching it.")
                break
            x, y = _auto_point(card)
            print(f"[{TAG}] Turning the Final Boss's auto-buy {word} at ({x:.0f}, {y:.0f}).")
            click_at(x, y)
            time.sleep(0.8 * config.SLOWNESS)
        else:
            done = _auto_state(capture_screen(), card) is want_on
        if done:
            print(f"[{TAG}] The Final Boss's auto-buy is {word}.")
        else:
            print(f"[{TAG}] Couldn't get the Final Boss's auto-buy {word}.")
        self._close_forge()
        return done

    def _click_buy(self, point):
        """Buy clicked, then the Forge closed. True."""
        click_at(point[0], point[1])
        time.sleep(1.0)
        health.save_debug_screenshot("halloween_after_buy")
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
        """
        Buys the Final Boss at the picked Heat, then waits for the end - the end screen, or (the
        game's Auto Retry on) the stage starting over. "ENDED" / False / "RECONNECTED".
        """
        boss_heat = self.boss_heat
        self._phase(f"WAITING FOR HEAT {boss_heat}", "#4fc3f7")
        self._buy_fail_saved = False
        if getattr(self, "_learner", None) is None:
            self._learner = StageLearner()
        learner = self._learner
        started = self._match_started_at or time.time()
        bought_at = [0.0]
        bought_stage = [None]
        next_try = [0.0]
        reads = [0]                       # looks at the picked Heat or later
        down_looks = [0]                  # looks in a row below the highest stage this match
        highest = [0]
        prev_n = [None]
        last_stage = [None]
        last_read = [time.time()]
        unreadable_saved = [False]
        start_looks = [0]
        last_start_press = [0.0]
        lower_after_buy = [0]             # reads below the bought stage since the last read at/above it
        auto_set = [False]                # the boss's auto-buy reads as this run wants it
        auto_tries = [0]
        auto_next = [0.0]
        left_to_game = [False]            # the Heat was reached and the game's auto-buy should have bought it
        over_looks = [0]                  # looks past the Heat with the buy left to the game

        def bought(stage_now):
            bought_at[0] = time.time()
            bought_stage[0] = stage_now
            highest[0] = max(highest[0], stage_now or 0)
            lower_after_buy[0] = 0

        def buy(stage_now):
            if self._buy_final_boss():
                bought(stage_now)
            else:
                next_try[0] = time.time() + BUY_FAIL_COOLDOWN

        def watch(shot):
            now = time.time()
            if now - started >= MIN_MATCH and _find_any(_REPEATS, shot)[0]:
                return "SCREEN"
            # Start Game waiting to be pressed (Auto Start off, and the match was taken for
            # running): press it, or the stage sits at 1 forever.
            start_up = _find(config.START_GAME_BTN, shot) is not None
            start_looks[0] = start_looks[0] + 1 if start_up else 0
            if start_up and bought_at[0] and start_looks[0] >= START_UP_LOOKS:
                # Start Game back after the boss was bought: that match ended (end screen missed)
                # and the next one is waiting - the watch starts over for it.
                print(f"[{TAG}] Start Game is up after the Final Boss was bought - that match is over.")
                return "RESTARTED"
            if start_looks[0] >= START_UP_LOOKS and now - last_start_press[0] >= START_PRESS_EVERY:
                last_start_press[0] = now
                print(f"[{TAG}] Start Game is waiting to be pressed - pressing it.")
                click_start_game()
                return None
            if start_up:
                # Before the match starts the counter can still show the last match's stage -
                # never read it then (that could buy the boss before the match even started).
                return None
            stage, conf = read_stage(shot)
            n = int(stage) if stage is not None else None
            learned = learner.look(shot, n)
            if learned is not None:
                n, conf = learned, LEARNED_MIN
            if n is not None:
                last_read[0] = now
                if n != last_stage[0]:
                    print(f"[{TAG}] Heat {n} (read {conf:.2f}).")
                    last_stage[0] = n
                # Lower than the highest stage seen (read twice in a row, so one misread can't
                # set it): the game started the stage over.
                down_looks[0] = down_looks[0] + 1 if n < highest[0] else 0
                if down_looks[0] >= STAGE_DOWN_LOOKS:
                    return "RESTARTED"
                if n == prev_n[0]:
                    highest[0] = max(highest[0], n)
                prev_n[0] = n
            elif not unreadable_saved[0] and now - last_read[0] >= STAGE_UNREADABLE_AFTER:
                unreadable_saved[0] = True
                path = health.save_debug_screenshot("halloween_stage_unreadable", shot)
                print(f"[{TAG}] Can't read the Heat number (screen saved: {path}) - still watching for the "
                      f"Heat {boss_heat} picture too.")

            # Auto-buy resets when the connection does, and it has to be right before the boss's
            # Heat. Whichever way it's wanted, it's set at the start of every match: on for the
            # game to buy at Heat 5, off so the game can't buy before a later Heat.
            if not auto_set[0] and not bought_at[0] and auto_tries[0] < AUTO_SET_TRIES and now >= auto_next[0]:
                auto_tries[0] += 1
                self._phase("SETTING THE FORGE'S AUTO-BUY", "#f9a825")
                auto_set[0] = self._set_forge_auto(self.auto_buy)
                auto_next[0] = time.time() + AUTO_SET_RETRY
                self._phase(f"WAITING FOR HEAT {boss_heat}", "#4fc3f7")
                if not auto_set[0] and auto_tries[0] >= AUTO_SET_TRIES:
                    print(f"[{TAG}] Giving up on setting auto-buy this match - the macro will buy the Final Boss itself.")
                return None

            if bought_at[0]:
                # Back below the stage it was bought at: that match ended and the next one is
                # running (its end screen was missed) - watch the new one from the start.
                if n is not None and bought_stage[0] is not None:
                    lower_after_buy[0] = lower_after_buy[0] + 1 if n < bought_stage[0] else 0
                    if lower_after_buy[0] >= STAGE_LOWER_AFTER_BUY:
                        print(f"[{TAG}] Heat {n} after buying the Final Boss at Heat {bought_stage[0]} - "
                              f"that match is over and the next one is running.")
                        return "RESTARTED"
                    # Past the Heat with the buy left to the game: it may never have happened (not
                    # enough souls, say). The Forge shows which: "Forging" means leave it, a green
                    # Buy means the macro buys it now. Otherwise the match runs on with no boss.
                    over_looks[0] = over_looks[0] + 1 if left_to_game[0] and n > bought_stage[0] else 0
                    if over_looks[0] >= STAGE_READS_NEEDED and now >= next_try[0]:
                        print(f"[{TAG}] Heat {n} and the match is still going - checking the Forge's auto-buy "
                              f"really bought the Final Boss.")
                        if self._buy_final_boss():
                            left_to_game[0] = False
                        else:
                            next_try[0] = time.time() + BUY_FAIL_COOLDOWN
                        over_looks[0] = 0
                # Bought once - never again this match (the stages carry on after the buy).
                return None

            if now < next_try[0]:
                return None
            # The picked Heat's picture (the whole circle) only when the digit can't be read: with
            # three look-alike circles to choose from, a digit that was read has the last word.
            picture = None
            if n is None:
                picture = _find_near(shot, config.HALLOWEEN_HEAT_PICS[boss_heat],
                                     (config.HALLOWEEN_STAGE_X, config.HALLOWEEN_STAGE_Y), config.HALLOWEEN_STAGE_REACH)
            # An unreadable look (a popup over the counter, an effect) neither counts nor resets.
            if (n is not None and n >= boss_heat) or picture:
                reads[0] += 1
            elif n is not None:
                reads[0] = 0
            if reads[0] >= STAGE_READS_NEEDED:
                how = 'picture ' + format(picture[2], '.2f') if picture else 'digit ' + format(conf, '.2f')
                reads[0] = 0
                # Already past the Heat (a match picked up late, or Heat 5 never read): don't assume,
                # go and look. _buy_final_boss() leaves it alone if it's already forging.
                if self.auto_buy and auto_set[0] and (n is None or n <= boss_heat):
                    print(f"[{TAG}] Heat {n or boss_heat} reached ({how}) - the Forge's auto-buy takes it from here.")
                    self._phase("WAITING FOR MATCH END", "#4fc3f7")
                    left_to_game[0] = True
                    bought(n if n is not None else boss_heat)
                else:
                    print(f"[{TAG}] Heat {n or boss_heat} reached ({how}) - buying the Final Boss.")
                    buy(n if n is not None else boss_heat)
            return None

        print(f"[{TAG}] Waiting for Heat {boss_heat}...")
        result = poll_until([target(config.VICTORY_TEXT, "VICTORY", debug_label="victory"),
                             target(config.DEFEAT_TEXT, "DEFEAT", debug_label="defeat")],
                            interval=1.0, label="halloween_match", stuck_timeout=self._stuck_timeout(),
                            custom_check=watch, lobby_grace=8.0, game_results=False)
        self._end_stage = highest[0] or None
        if config.STUCK_DETECTED:
            print(f"[{TAG}] When it stopped: highest stage read {highest[0] or 'none'}, last read "
                  f"{last_stage[0] if last_stage[0] is not None else 'none'}, Final Boss "
                  f"{'bought' if bought_at[0] else 'not bought'}, watched for {time.time() - started:.0f}s.")
        if result in (False, "RECONNECTED"):
            return result
        if result == "RESTARTED":
            # Auto Retry: the end screen came and went between looks, so neither result was
            # seen. The boss ends the run, so with it bought this counts as a win. Without it
            # the run can only have ended by losing - unless the match was picked up part way
            # (the boss may have been bought before that) or it ended too soon to be a match.
            print(f"[{TAG}] That match is over and the next one has started - watching it from the start.")
            self._restarted = True
            if bought_at[0]:
                print(f"[{TAG}] Result screen not seen - counted as a win because the Final Boss was bought.")
                result = "VICTORY"
            elif self._watched_from_start and time.time() - started >= MIN_MATCH_FOR_DEFEAT:
                print(f"[{TAG}] Result screen not seen - counted as a defeat because the match ended "
                      f"before the Final Boss was bought.")
                result = "DEFEAT"
            else:
                return "ENDED"               # unknown: counted as neither
            return "ENDED" if self._count(result) else False
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
        """
        The next match: Repeat Stage (Auto Retry off), or nothing to do when the game started it
        over by itself (Auto Retry on). "START" / "IN_MATCH" / "LOST" (navigate() takes over) /
        False / "RECONNECTED".
        """
        if self._restarted:
            return self._next_already_running()
        self._phase("NEXT MATCH")
        end_stage = self._end_stage
        lower_looks = [0]

        def restarted(shot):
            # The end screen gone and the stage counter back below where the match ended.
            if _find_any(_REPEATS, shot)[0] or _find(config.VICTORY_TEXT, shot) or _find(config.DEFEAT_TEXT, shot):
                lower_looks[0] = 0
                return None
            stage, _ = read_stage(shot)
            low = stage is not None and (end_stage is None or int(stage) < end_stage)
            lower_looks[0] = lower_looks[0] + 1 if low else 0
            return "RESTARTED" if lower_looks[0] >= STAGE_DOWN_LOOKS else None

        result = poll_until([target(config.HALLOWEEN_REPEAT_STAGE_BTN, "REPEAT", debug_label="halloween_repeat"),
                             target(config.REPEAT_STAGE_BTN, "REPEAT"),
                             target(config.START_GAME_BTN, "START")],
                            interval=1.0, label="halloween_next_wait", timeout=AFTER_MATCH_WAIT,
                            stuck_timeout=None, custom_check=restarted, lobby_grace=25.0)
        if result in (False, "RECONNECTED"):
            return result
        if self._last_result is None and result == "REPEAT" and not self._count(self._read_result()):
            return False
        self._last_result = None
        if result == "RESTARTED":
            print(f"[{TAG}] The game started the stage over by itself (Auto Retry).")
            return self._next_already_running()
        if result == "START":
            print(f"[{TAG}] The stage is back with Start Game up.")
            self.resumed = True
            return "START"
        if result != "REPEAT":
            path = health.save_debug_screenshot("halloween_no_repeat")
            print(f"[{TAG}] No Repeat Stage after the match (screen saved: {path}) - working out where we are.")
            return "LOST"
        time.sleep(1.0)                    # it slides in - one click once it's settled
        template, match = _find_any(_REPEATS, capture_screen())
        if match:
            print(f"[{TAG}] Clicking Repeat Stage.")
            clicked = click_stage_button(template, match, "halloween_repeat", clicks=1)
            if clicked == "RECONNECTED":
                return clicked
            if clicked is not True:
                return "LOST"
        return self._after_repeat(end_stage)

    def _after_repeat(self, end_stage):
        """
        Right after Repeat Stage: Start Game up (Auto Start off) -> "START"; the stage counter
        back below where the match ended with no Start Game (Auto Start on) -> "IN_MATCH".
        Neither within AFTER_REPEAT_WAIT -> "START" (the shared Start Game wait sorts it out).
        """
        looks = [0]

        def started(shot):
            if _find_any(_REPEATS, shot)[0]:
                looks[0] = 0
                return None
            stage, _ = read_stage(shot)
            low = stage is not None and (end_stage is None or int(stage) < end_stage)
            looks[0] = looks[0] + 1 if low else 0
            return "RUNNING" if looks[0] >= STAGE_DOWN_LOOKS else None

        result = poll_until([target(config.START_GAME_BTN, "START")], interval=1.0, label="halloween_after_repeat",
                            timeout=AFTER_REPEAT_WAIT, stuck_timeout=None, custom_check=started, lobby_grace=25.0)
        if result in (False, "RECONNECTED"):
            return result
        if result == "RUNNING":
            print(f"[{TAG}] The match started by itself (Auto Start).")
            return self._next_already_running()
        self.resumed = True
        return "START"

    def _next_already_running(self):
        """The game's Auto Retry started the next match: Auto Play checked, then watch it."""
        self._restarted = False
        self._last_result = None
        self._phase("CHECKING AUTO PLAY", "#2e7d32")
        if not ensure_autoplay_enabled():
            print(f"[{TAG}] WARNING: couldn't find the Auto Play button - carrying on.")
        SESSION.match_started()
        self._match_started_at = time.time()
        self._watched_from_start = True
        return "IN_MATCH"
