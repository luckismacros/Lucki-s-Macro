# modules/fishing.py
"""
Fishing (Auto Rod required). fishing.png is a TOGGLE - clicking it while already
equipped un-equips the rod, so it's only ever clicked once, up front, to equip.
With Auto Rod equipped, casting at the fishing spot is enough - the rod handles
catching on its own.

The periodic re-cast never touches the equip button - only the fishing spot. There is
deliberately no rank-badge-driven RE-EQUIP logic: a detection false negative there
un-equips a perfectly good session.

The cast does need SOME idea of whether a match is still running, because as a pure
timer it kept clicking through Victory / reward-choice / Select Portal - which is how
a WON Portals match could end up advanced past its own reward screen before the loop
read it, on the portals that fish (Summer/Sovereign) and only those.

Two ways to know that, and the direction matters more than it looks:

  - Cast only when fishing is CONFIRMED (the rank badge). Tried, and it blocked every
    cast for an entire match, because the badge never matched - fishing silently did
    nothing at all. One bad template costs the whole feature.
  - Cast unless the match is visibly OVER (below). One bad template costs a single
    stray click.

So this fails open, on the second rule, using the same end-of-match templates the
match-end poll itself is built on - the ones already proven to match reliably.
"""
import time
from vision import capture_screen, find_template
from input_controller import click_at, click_in_place
import config

# Seconds between re-clicks of the fish point. Deliberately BELOW the match-end poll
# interval (2.0s) that drives tick(), so every tick casts and the real cadence is the
# poll's. At exactly 2.0 the comparison lands on the boundary and timing jitter drops
# every other tick - which is what made the old 3.0 behave like one cast per 4s.
FISHING_RECAST_INTERVAL = 1.5

# How long to keep looking for the rod button before giving up. One snapshot taken
# the instant the walk ended missed it (0.617 against 0.85, live 2026-09-14) while
# the match UI was still settling; a few seconds of looking finds it reliably.
EQUIP_SEARCH_SECONDS = 8.0

# Screens that mean the match is over (or ending). A cast fired into any of these is
# a real click on the post-match UI - see the module docstring.
def _match_end_templates():
    return (config.VICTORY_TEXT, config.DEFEAT_TEXT,
            config.REWARD_SELECT_TEXT, config.CLICK_ANYWHERE_TEXT)

def start_fishing(already_equipped=False, cast_pos=None):
    """
    Casts wherever the cursor already is, equipping the rod first via fishing.png
    UNLESS the caller already knows it's equipped.

    Deliberately doesn't move the cursor anywhere to cast by default - see
    click_in_place(). There's usually nowhere in particular it needs to aim: casting
    is just pressing the mouse button again wherever the player (or a walk/macro)
    already left the cursor sitting once fishing was started from there.

    cast_pos, if given as (x, y), casts there instead - for Auto Play, whose walk only
    moves the character and never the mouse, so the cursor can be left anywhere by the
    time fishing starts (a macro's own recorded clicks don't have this problem, so
    they never pass this).

    fishing.png is a TOGGLE - clicking it while already equipped un-equips the rod
    instead of doing nothing. Auto Rod keeps the rod equipped BETWEEN matches, the
    same way Auto Play's own in-game toggle stays on - but this used to be called
    fresh every match with no memory of that, so it clicked the toggle every single
    time regardless of state. That flips the rod off exactly as often as it flips it
    on: confirmed live, matches alternated fishing/not-fishing every other match with
    nothing in the log to explain why, since the click itself always "succeeded" -
    it just wasn't supposed to happen on the matches where the rod was already on.

    already_equipped is the CALLER's own record of whether it already equipped the
    rod earlier this run with nothing happening since to unequip it - trusted over a
    fresh visual re-check here, deliberately. The one template that WOULD confirm
    equipped state visually is the fishing rank badge (find_rank_badge()), and it is
    already known to be unreliable enough that gating the ongoing re-cast on it once
    silently killed fishing outright for an entire match (see FishingTicker's own
    docstring) - the same risk would apply here. A disconnect/reconnect does NOT
    reset this record on purpose - confirmed live, Auto Rod stays equipped through
    one exactly like Auto Play's own toggle does, and forgetting that here once
    made the bot re-click an already-equipped rod after every reconnect, switching
    fishing off at random points in a run (see engine.run_portal()'s own comment on
    this).

    Returns True if fishing is (or should now be) active, False only if the fishing
    button couldn't be found on screen while actually trying to equip - in that
    specific case the rod's state is unknown, so the caller should say so loudly
    rather than assume fishing started.
    """
    if already_equipped:
        print("[Fishing] Already equipped from earlier this run - casting without touching the toggle.")
        click_at(*cast_pos) if cast_pos else click_in_place()
        return True

    match = None
    deadline = time.time() + EQUIP_SEARCH_SECONDS
    while not config.STOP_REQUESTED:
        screenshot = capture_screen()
        match = find_template(screenshot, config.FISHING_BTN, config.MATCH_THRESHOLD, debug_label="fishing_btn")
        if match is not None or time.time() >= deadline:
            break
        time.sleep(0.5)

    equipped = match is not None
    if equipped:
        fx, fy, _ = match
        print(f"[Fishing] Equipping rod at ({fx}, {fy}).")
        # The equip button itself DOES need an aimed click - fishing.png is a
        # specific on-screen icon, not "wherever the cursor is".
        click_at(fx, fy)
        time.sleep(0.3)
    else:
        print("[Fishing] Fishing button not found on screen - rod not equipped.")

    click_at(*cast_pos) if cast_pos else click_in_place()
    return equipped

def find_rank_badge(screenshot, preferred=None):
    """
    The fishing rank badge currently on screen, as its template path, or None.

    A player only ever shows one of the six ranks, so `preferred` (the one that
    matched last time) is checked first and short-circuits - which keeps the common
    case at a single template match rather than six.
    """
    candidates = config.FISHING_RANK_TEMPLATES
    if preferred:
        candidates = [preferred] + [p for p in candidates if p != preferred]

    for path in candidates:
        if find_template(screenshot, path, config.MATCH_THRESHOLD):
            return path
    return None


class FishingTicker:
    """
    Call tick() once per poll cycle while a match is in progress. Re-clicks wherever
    the cursor already is (see click_in_place()) every FISHING_RECAST_INTERVAL
    seconds, but ONLY while the fishing rank badge is visible.

    That condition is the whole point: without it this is a blind timer that goes on
    clicking after the match has ended, landing real clicks on the Victory / reward /
    Select Portal screens that follow. Skipping a cast costs one interval and nothing
    else, so failing closed here is the cheap direction.
    """
    def __init__(self, cast_pos=None):
        self.cast_pos = cast_pos
        self.last_click = time.time()
        self.holding = False
        self.badge_checked = False

    def tick(self):
        now = time.time()
        if now - self.last_click < FISHING_RECAST_INTERVAL:
            return
        self.last_click = now

        screenshot = capture_screen()

        # One-shot diagnostic, not a gate: says in the log whether the rank badge is
        # findable during a live match at all. It was tried as the gate and blocked
        # every single cast for a whole match, so nothing depends on it until that is
        # understood - but the answer is worth having for free.
        if not self.badge_checked:
            self.badge_checked = True
            rank = find_rank_badge(screenshot)
            print(f"[Fishing] Rank badge check: {rank.rsplit('/', 1)[-1] if rank else 'none of the 6 matched'}.")

        # Fail OPEN: cast unless something says the match is over. The inverse (cast
        # only when fishing is confirmed) is what silently stopped fishing entirely -
        # one bad template there costs the whole feature, while one bad template here
        # costs a single stray click, which is the direction worth failing in.
        ended = next((t for t in _match_end_templates()
                      if find_template(screenshot, t, config.MATCH_THRESHOLD)), None)
        if ended:
            if not self.holding:
                self.holding = True
                print(f"[Fishing] {ended.rsplit('/', 1)[-1]} is on screen - holding the re-cast so it "
                      f"doesn't click through the post-match screens.")
            return

        self.holding = False
        click_at(*self.cast_pos) if self.cast_pos else click_in_place()
