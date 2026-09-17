# modules/autoclicker.py
"""
Background anti-AFK clicker for long/infinite runs - clicks a saved reference-space
position on an interval so the player doesn't need to stay at the keyboard to avoid
an idle disconnect.

Ticked the same way modules.fishing.FishingTicker is: once per poll cycle, via
poll_until's on_tick (see modules.stage_player.wait_for_match_result). That scopes its
active window to the same one fishing already uses - only while actually waiting on a
live match's Victory/Defeat/Reward, which is naturally quiet during lobby navigation,
menu screens and disconnect recovery, and resumes on its own once a fresh
wait_for_match_result starts back up after re-entering a match.

State lives on config (config.AUTOCLICKER_ENABLED/_POS/_INTERVAL) rather than being
passed in at construction, the same cross-thread-flag pattern config.STOP_REQUESTED
already uses - the on/off toggle and the Locate button both run on the UI thread while
this ticks on the engine thread, and the player may flip either mid-match, so the
latest value has to be read fresh every tick rather than snapshotted once per run.

Unlike fishing's "fail open" (cast unless the match is visibly over), this fails
CLOSED: a skipped click costs nothing, while a stray click during a menu or the lobby
can register a real, unwanted action. match_already_started() from stage_player is the
gate - the same signal (the "Game Started!" banner, or the wave counter off zero) the
user asked for as a general "is a match actually running" check.
"""
import time

from vision import capture_screen
from input_controller import click_at
import config


class AutoClickerTicker:
    def __init__(self):
        self.last_click = 0.0
        self.holding = False

    def tick(self):
        if not config.AUTOCLICKER_ENABLED or not config.AUTOCLICKER_POS or config.STOP_REQUESTED:
            return

        now = time.time()
        interval = config.AUTOCLICKER_INTERVAL or config.AUTOCLICKER_DEFAULT_INTERVAL
        if now - self.last_click < interval:
            return
        self.last_click = now

        # Local import: stage_player imports this module at top level (to build the
        # ticker alongside FishingTicker in wait_for_match_result), so importing
        # stage_player back at module load time here would be circular.
        from modules.stage_player import match_already_started

        if not match_already_started(capture_screen()):
            if not self.holding:
                self.holding = True
                print("[Autoclicker] No confirmed match in progress - holding the click.")
            return

        self.holding = False
        click_at(*config.AUTOCLICKER_POS)
