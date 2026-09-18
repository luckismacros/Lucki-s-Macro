# engine.py
"""
UI-independent bot orchestration: the four gamemode run loops, the Never Stop
supervisor, and how a run's end gets classified and reported to Discord.

This is everything gui.py used to hold directly on BotGUI (extracted 2026-09-14,
alongside the redesigned Qt UI) - method bodies are otherwise UNCHANGED from the
tested, 8-hour-run-hardened versions, only their self-references were mechanically
rewritten to go through the small interface below instead of touching Tkinter
widgets directly. No UI toolkit is imported here.

BotEngine needs a `ui` object implementing:
  log(message)                                      - print a line to the screen
  set_phase(text, color="#a8a8a8")                   - update the phase indicator
  focus_and_pin() -> bool                            - focus + dock Roblox; False
                                                        also means the run should
                                                        stop (config.STOP_REQUESTED
                                                        and config.STUCK_DETECTED
                                                        are already set)
  calibrate_ui_scale(screenshot)                     - one-time UI-scale probe
  report_unrecognised_screen(screenshot, label,
                              looking_for)            - diagnostic logging when a
                                                        state-detect check misses
  reset_buttons()                                    - Start/Stop button states
  get_user_settings() -> dict                        - stop_after_defeats,
                                                        max_defeats, never_stop, ...

gui.py's BotGUI implements this by forwarding to its existing (renamed, otherwise
untouched) Tkinter methods. Any other UI - the Qt rebuild, a headless test - only
has to implement this same small surface; it never needs to know how a run loop
actually works.
"""
import os
import time
import threading

import config
from vision import capture_screen, find_template
from input_controller import anchor_camera, click_at
from modules.gamemode_select import run_flow, run_raid_flow, run_challenge_slot_flow
from modules.match_start import click_start_party, click_raid_start, click_challenge_start, click_portal_start
from modules.stage_player import (
    play_preset, wait_for_start_game, click_start_game, wait_for_repeat_stage,
    wait_for_next_stage, wait_for_match_result, wait_for_reward_or_select,
    press_start_after_macro, match_already_started,
)
from modules.autoplay import ensure_autoplay_enabled
from modules.portal_select import run_portal_flow, reselect_portal, click_select_portal
from modules.polling import poll_until, target
from modules.portal_reward import pick_portal_reward
from modules.expedition import ExpeditionRunner
from modules.stats import SESSION
from modules import health, notify
from modules.fishing import start_fishing

# How many times to retry reopening the portal picker and re-selecting after a
# reward, before giving up on the run entirely. Confirmed live (2026-09-13) that a
# single failure here can be a one-off render/network hiccup that a plain retry
# recovers from immediately - see the retry loop in run_portal(). 3 gives that
# hiccup room to clear without retrying forever against a genuinely broken screen.
PORTAL_REENTER_MAX_ATTEMPTS = 3

# How many times a fresh portal entry from the lobby is tried before the run stops.
# One failed attempt used to stop the run outright - including when the bot simply
# wasn't at the lobby yet because a result screen was still up.
PORTAL_NAV_MAX_ATTEMPTS = 3

# Never Stop restart timing: wait RESTART_BASE_DELAY before the first restart, doubling
# each time up to RESTART_MAX_DELAY. A run that lasted RESTART_RESET_AFTER seconds before
# failing counts as healthy again, so the next wait starts back at the base.
RESTART_BASE_DELAY = 30
RESTART_MAX_DELAY = 300
RESTART_RESET_AFTER = 600

# Stops that Never Stop deliberately does NOT restart after: hitting the defeat limit is
# a choice made in Settings, not a problem to recover from.
NEVER_STOP_EXEMPT_PREFIXES = ("TOO MANY DEFEATS",)


class BotEngine:
    def __init__(self, ui):
        self.ui = ui
        self.running = False
        self.user_stop_requested = False
        self.restart_pending = False
        self.restart_reason = None
        self.run_started_at = None
        self.bot_thread = None

    # --- thin passthroughs, so the extracted method bodies below read exactly as
    # they did as BotGUI methods (self.log(...), self.set_phase(...)) -----------
    def log(self, message):
        self.ui.log(message)

    def set_phase(self, text, color="#a8a8a8"):
        self.ui.set_phase(text, color)

    # --- lifecycle ---------------------------------------------------------------
    def start(self, target, args, notify_fields=None):
        """
        Starts a run in a background thread, supervised for Never Stop.

        target/args: one of this engine's own run_* methods (bound) and its
        positional arguments - the caller (the UI's start button handler) decides
        which gamemode and reads whatever it needs from its own widgets first.

        notify_fields: the gamemode-specific fields for the "Run started" Discord
        message (e.g. [("Preset", "my_macro"), ("Difficulty", "Hard")]), or None to
        skip that message entirely regardless of whether notifications are on -
        the caller passes None when it never even checked notify.is_configured().
        The two settings-derived fields (defeat limit, Never Stop) are appended
        here, since those come from get_user_settings() rather than gamemode UI.
        """
        config.STOP_REQUESTED = False
        config.STUCK_DETECTED = None
        self.user_stop_requested = False
        self.restart_pending = False
        self.apply_run_behavior_settings()
        SESSION.start()
        self.running = True
        self.run_started_at = time.time()

        if notify.is_configured() and notify_fields is not None:
            user_settings = self.ui.get_user_settings()
            fields = list(notify_fields)
            fields.append(("Defeat limit", f"{config.STOP_AFTER_DEFEATS} in a row"
                           if config.STOP_AFTER_DEFEATS else "never stop on defeats"))
            fields.append(("Never stop", "on" if user_settings.get("never_stop") else "off"))
            # Best-effort screenshot of whatever's on screen right now (the lobby,
            # or wherever the user manually left off) - a picture of the starting
            # point is worth having even though nothing has gone wrong yet, the
            # same reasoning as attaching one to every other run-lifecycle message.
            notify.send("The macro is running.", category="lifecycle",
                        title="Run started", good=None, fields=fields,
                        image_bytes=health.jpg_bytes())

        self.bot_thread = threading.Thread(target=self._supervised_run, args=(target, args), daemon=True)
        self.bot_thread.start()

    def _limit_reached(self):
        """
        True once this run has played as many matches as the user asked for.

        `match_limit` is set by the UI before start() (0 = no limit). Checked right after a
        match's result is handled and BEFORE the next one is started, so a run of 50 plays
        exactly 50 and then ends as a normal, green "Run finished" - no STUCK_DETECTED, so a
        queue can carry on to its next step. Counts across Never Stop restarts, because
        SESSION is only reset by start().
        """
        limit = int(getattr(self, "match_limit", 0) or 0)
        done = SESSION.victories + SESSION.defeats
        if limit and done >= limit:
            self.log(f"Played {done} of {limit} runs - done.")
            return True
        return False

    def run_bot(self, gamemode_key, location_key, variant_key, difficulty, preset_name, auto_next=False):
        # Whether a leading walk in the preset (see stage_player.play_preset's
        # skip_movement) still needs to play. True until proven otherwise: the
        # character's actual position is unknown at the very start of a run. Set to
        # False the moment a walk has played for THIS (location, variant); reset to
        # True on any fresh trip through the lobby (_navigate_from_lobby, below) -
        # that's the one thing that can move the character back to its spawn without
        # this loop doing it. Auto Next changing stage naturally starts fresh too,
        # since walked_for's key changes to the new stage.
        walked_for = [None]

        def _resolve_stage_mode():
            """
            Whether THIS stage should use the game's Auto Play or a recorded preset.

            Without Auto Next this is a fixed, one-time choice (what the dropdown
            said). With Auto Next on, location_key/variant_key change under us on
            every win (see the VICTORY branch below), and a preset recorded for one
            map/act is a human timeline for that exact stage - replaying it against
            a different map's layout places units at the wrong times in the wrong
            place. So each stage re-checks for a same-named preset file of its own,
            falling back to Auto Play for any stage that doesn't have one.
            """
            if preset_name == config.AUTO_PLAY_PRESET_NAME:
                return True
            if not auto_next:
                return False
            return not os.path.exists(f"presets/{location_key}_{variant_key}_{preset_name}.json")

        def _navigate_from_lobby():
            """
            Runs the full lobby-to-match navigation for the active gamemode. Retries
            automatically if a disconnect interrupts navigation itself.
            """
            # A fresh trip through the lobby always means the character is back at the
            # stage's own starting position (or about to be) - so a walk gets to play
            # again next match, whatever it played for last.
            walked_for[0] = None
            while True:
                self.set_phase("NAVIGATING MENUS", "#4fc3f7")

                if gamemode_key == "raids":
                    self.log(f"Navigating to Raid: {location_key} (Difficulty {difficulty})...")
                    result = run_raid_flow(location_key, difficulty)
                else:
                    self.log(f"Navigating menus from the lobby (Difficulty: {difficulty})...")
                    result = run_flow(location_key, variant_key, difficulty)

                if result == "RECONNECTED":
                    self.log("Disconnected again mid-navigation - reconnected, retrying...")
                    continue

                if not result:
                    self.log("Failed lobby navigation.")
                    # A found-this-huge-bug-during-audit fix: run_flow()/run_raid_flow()
                    # chain several steps together, and NOT all of them go through
                    # poll_until() (click_map/click_act/click_difficulty/click_raid/
                    # click_raid_difficulty are single vision checks with no poll wait
                    # at all - see modules/gamemode_select.py). Those can return False
                    # purely because one frame's template match missed, with
                    # STOP_REQUESTED never touched - so EVERY break/return in run_bot()
                    # fed by this function (six of them) was silently reporting a
                    # template-matching failure as a clean, successful "Run finished".
                    # This is the one chokepoint all of them funnel through, so fixing
                    # it here fixes every one of those six at once, the same way the
                    # portal re-entry / no-reward dead ends were fixed individually
                    # elsewhere in this file.
                    if not config.STOP_REQUESTED:
                        config.STUCK_DETECTED = "LOBBY NAVIGATION FAILED"
                    return False
                if config.STOP_REQUESTED:
                    return False

                self.log("Starting match...")
                start_result = click_raid_start() if gamemode_key == "raids" else click_start_party()
                if start_result == "RECONNECTED":
                    self.log("Disconnected while clicking Start - reconnected. Redoing navigation...")
                    continue
                if not start_result:
                    # Was silently discarded before this fix - a missed Start click
                    # (the party screen still sliding in when the one-shot check used
                    # to run) left the run waiting forever for a match that never
                    # started, with nothing in the log to say why. See the same
                    # chokepoint fix a few lines up in this function.
                    self.log("Could not click Start after selecting the stage.")
                    if not config.STOP_REQUESTED:
                        config.STUCK_DETECTED = "LOBBY NAVIGATION FAILED"
                    return False
                return True

        try:
            self.set_phase("DETECTING STATE", "#a8a8a8")
            if not self.ui.focus_and_pin():
                return
            self.log("Detecting current game state...")

            screenshot = capture_screen()
            self.ui.calibrate_ui_scale(screenshot)
            in_lobby = find_template(screenshot, config.PLAY_BTN, config.MATCH_THRESHOLD,
                                     debug_label="play_btn (state detect)")
            in_stage_select = None if in_lobby else find_template(screenshot, config.SELECT_STAGE_BTN, config.MATCH_THRESHOLD)

            if in_lobby:
                self.log("Lobby detected!")
                if not _navigate_from_lobby():
                    return
            elif in_stage_select:
                self.set_phase("CONFIRMING STAGE", "#4fc3f7")
                self.log("Already on Select Stage screen. Confirming stage...")
                x, y, _ = in_stage_select
                click_at(x, y, clicks=2)
                if config.STOP_REQUESTED:
                    return

                self.log("Starting match...")
                start_result = click_raid_start() if gamemode_key == "raids" else click_start_party()
                if start_result == "RECONNECTED":
                    self.log("Disconnected while clicking Start - reconnected. Redoing navigation...")
                    if not _navigate_from_lobby():
                        return
                elif not start_result:
                    self.log("Could not click Start on the resumed stage - stopping.")
                    if not config.STOP_REQUESTED:
                        config.STUCK_DETECTED = "LOBBY NAVIGATION FAILED"
                    return
            else:
                self.ui.report_unrecognised_screen(screenshot, "no_lobby",
                                                 "the lobby's Play button")
                self.log("No lobby detected. Assuming In-Game Start!")

            self.log(f"Phase 3: Continuous Gameplay Loop Started ({preset_name})")
            if auto_next:
                self.log("Auto Next is on - will climb to the next act on every victory.")

            consecutive_defeats = 0

            while not config.STOP_REQUESTED:
                self.set_phase("WAITING FOR START GAME", "#4fc3f7")
                result = wait_for_start_game()

                if result == "RECONNECTED":
                    self.log("Disconnected and reconnected - back at the lobby. Redoing navigation...")
                    if not _navigate_from_lobby():
                        break
                    continue

                if not result:
                    break

                SESSION.match_started()
                self.log("Match Loaded! Anchoring camera...")
                self.set_phase("ANCHORING CAMERA", "#4fc3f7")
                anchor_camera()

                is_auto_play = _resolve_stage_mode()
                if auto_next:
                    mode = "Auto Play" if is_auto_play else f"preset '{preset_name}'"
                    self.log(f"Auto Next: {location_key} / {variant_key} -> using {mode}.")
                if is_auto_play:
                    # A brief settle before the very first read: anchor_camera() can
                    # still be finishing its own drag/release the instant this runs,
                    # and a frame mid-motion is what a live 2026-09-14 miss looked like.
                    time.sleep(0.4)
                    self.log("Auto Play preset selected - checking in-game Auto Play toggle...")
                    self.set_phase("CHECKING AUTO PLAY", "#2e7d32")
                    if not ensure_autoplay_enabled():
                        self.log("WARNING: Couldn't find the Auto Play button on screen - continuing anyway.")

                    self.log("Clicking Start Game...")
                    if not click_start_game():
                        self.log("WARNING: Couldn't find Start Game button to click.")
                else:
                    stage_key = (location_key, variant_key)
                    skip_walk = walked_for[0] == stage_key
                    self.log("Executing Timeline Preset..." if not skip_walk else
                            "Executing Timeline Preset (already at the recorded spot - not walking)...")
                    self.set_phase("PLAYING TIMELINE", "#2e7d32")
                    preset_result = play_preset(location_key, variant_key, preset_name, skip_movement=skip_walk)

                    if preset_result == "RECONNECTED":
                        self.log("Disconnected and reconnected during playback - back at the lobby. Redoing navigation...")
                        if not _navigate_from_lobby():
                            break
                        continue

                    if not preset_result:
                        # play_preset() also returns False for a missing or empty preset
                        # file, with no stop requested - that has to read as a problem,
                        # not as a clean "Run finished".
                        if not config.STOP_REQUESTED:
                            self.log(f"Preset '{preset_name}' couldn't be played (missing or empty) - stopping.")
                            config.STUCK_DETECTED = "PRESET PLAYBACK FAILED"
                        break

                    # However this stage's walk went (played for the first time, or
                    # skipped because it already had), the character is now known to be
                    # at the recorded spot - a Repeat Stage of this SAME stage can skip
                    # it next time.
                    walked_for[0] = stage_key

                    # A recorded macro only places units - it never presses Start Game
                    # itself unless the player's own recording happened to click it.
                    # Auto Play's branch above already presses it explicitly; this is
                    # the same step for a macro, safe even when it's not needed (see
                    # press_start_after_macro's own docstring).
                    press_start_after_macro()

                self.log("Match Complete. Waiting for the match to end...")
                self.set_phase("WAITING FOR MATCH END", "#4fc3f7")
                # Victory/Defeat is the real match-end signal, and the banner is the
                # same in every gamemode. Polling straight for Repeat Stage instead
                # (what this used to do) meant a loss was never recognised as one -
                # it just silently repeated, or sat here forever if the defeat screen
                # took a different route to that button. Both outcomes repeat the
                # stage, so the result only changes what gets logged.
                # variant_key is the act for Story, so an Infinite run gets the hours-long
                # stuck bound instead of the normal-match one.
                end_result = wait_for_match_result(act_key=variant_key)

                if end_result == "RECONNECTED":
                    self.log("Disconnected and reconnected while waiting for the match to end - back at the lobby. Redoing navigation...")
                    if not _navigate_from_lobby():
                        break
                    continue

                if not end_result:
                    break

                if end_result == "DEFEAT":
                    consecutive_defeats += 1
                    SESSION.defeat()
                    self.log(f"Defeat ({consecutive_defeats} in a row) - repeating the stage anyway.")

                    # Without this the loop would replay an unwinnable stage forever.
                    # Applies whether or not Auto Next is on - a stage Auto Play/the
                    # preset can't clear isn't going to clear on the 5th try either,
                    # so the whole run stops rather than grinding on it forever.
                    if config.defeat_limit_reached(consecutive_defeats):
                        self.log(f"{consecutive_defeats} losses in a row - stopping, since this preset "
                                 f"clearly can't clear this stage.")
                        self.set_phase("TOO MANY DEFEATS", "#ff1744")
                        # See the matching comment in run_portal(): without this the
                        # run reports as a clean, successful finish instead of the
                        # problem it actually is.
                        config.STUCK_DETECTED = "TOO MANY DEFEATS"
                        health.save_debug_screenshot("too_many_defeats")
                        break
                elif end_result == "VICTORY":
                    consecutive_defeats = 0
                    SESSION.victory()
                    self.log("Victory!")
                else:
                    # REWARD_SELECT is a Portals-only screen; it shouldn't turn up here.
                    self.log(f"Match ended ({end_result}) - repeating the stage.")

                if self._limit_reached():
                    break

                # Auto Next only ever tries to advance on a genuine win - Defeat's
                # banner has no Next Stage button at all, it only ever offers Repeat.
                advanced = False
                if auto_next and gamemode_key == "story" and end_result == "VICTORY":
                    self.set_phase("ADVANCING TO NEXT STAGE", "#4fc3f7")
                    next_click_result = wait_for_next_stage()

                    if next_click_result == "RECONNECTED":
                        self.log("Disconnected and reconnected while advancing - back at the lobby. Redoing navigation...")
                        if not _navigate_from_lobby():
                            break
                        continue

                    if next_click_result is True:
                        next_stage = config.next_story_stage(location_key, variant_key)
                        if next_stage is None:
                            self.log("Auto Next reached the end of the tracked chain "
                                     "(last act of the last map) - stopping here.")
                            self.set_phase("AUTO NEXT COMPLETE", "#2e7d32")
                            break
                        location_key, variant_key = next_stage
                        map_label = config.MAPS[location_key]["label"]
                        act_label = config.MAPS[location_key]["acts"][variant_key]["label"]
                        self.log(f"Next Stage clicked - advancing to {map_label} / {act_label}.")
                        advanced = True
                    else:
                        self.log("No Next Stage button appeared - falling back to Repeat Stage.")

                if not advanced:
                    self.set_phase("REPEATING STAGE", "#4fc3f7")
                    repeat_result = wait_for_repeat_stage()

                    if repeat_result == "RECONNECTED":
                        self.log("Disconnected and reconnected while waiting to repeat - back at the lobby. Redoing navigation...")
                        if not _navigate_from_lobby():
                            break
                        continue

                    if not repeat_result:
                        break

                    self.log("Repeat Stage clicked. Restarting...")
        except Exception as e:
            self.log(f"ERROR: Bot crashed unexpectedly - {e}")
            # Without this a crash reached _cleanup() looking like a clean finish and was
            # reported as a green "Run finished" right after the "Bot crashed" alert.
            if not self.user_stop_requested:
                config.STUCK_DETECTED = f"CRASHED ({type(e).__name__})"
            # Best-effort screenshot of whatever was on screen at the moment it died -
            # wrapped separately so a capture failure (e.g. Roblox already gone) can't
            # swallow the crash notification itself.
            try:
                shot_bytes = health.jpg_bytes()
            except Exception:
                shot_bytes = None
            notify.send(f"```{e}```", category="problems", title="Bot crashed",
                        good=False, image_bytes=shot_bytes)
        finally:
            self._cleanup()

    def run_portal(self, category_key, fish_enabled, avoid_traitless, preset_name=None, equip_rod=False,
                   fish_pos=None):
        """
        Continuously farms a portal: navigate in -> Start -> (optional walk + fish)
        -> wait for the match to end -> on a win, hover-pick a reward (avoiding
        Traitless if the toggle's on) -> Select Portal -> re-enter -> loop. On a
        Defeat there's no reward screen, so it clicks Repeat Stage instead and keeps
        farming rather than stopping.

        Every entry searches the portal list by name and takes the top result, so it
        always enters the best one currently owned without being told a tier - and the
        rewards each win hands out keep the loop fed indefinitely.

        If the Items button isn't found at the start (it moves/hides once a match is
        live), assumes the game is already mid-run and skips straight past the
        first-time navigation, same as Story/Raids' own resume detection.
        """
        try:
            self.set_phase("DETECTING STATE", "#a8a8a8")
            if not self.ui.focus_and_pin():
                return

            portal_label = config.PORTALS[category_key]["label"]

            screenshot = capture_screen()
            # Portals never passes through the Play-button state detection the other
            # gamemodes start from, so without its own call a UI-scale mismatch would go
            # unmeasured on exactly the mode most likely to hit it.
            self.ui.calibrate_ui_scale(screenshot)
            at_lobby = find_template(screenshot, config.ITEMS_BTN, config.MATCH_THRESHOLD,
                                     debug_label="items_btn (state detect)") is not None
            need_full_navigation = at_lobby
            is_repeat_match = False
            if not at_lobby:
                # Confirmed live: starting (or restarting) the bot can land here
                # while a previous match's post-victory summary is STILL up on
                # screen - no Items button (it's not the lobby), but not "already
                # in-game" either. The old code only checked for the lobby and
                # treated every other screen the same way ("must be mid-match"),
                # which sent this exact screen straight into a Start Game wait that
                # was never going to resolve, since the actual match had already
                # ended. Checking for Select Portal specifically catches it and
                # clicks through instead of stranding the run silently.
                stray_select = find_template(screenshot, config.PORTAL_SELECT_BTN, config.MATCH_THRESHOLD,
                                             debug_label="select_portal (state detect)")
                if stray_select:
                    x, y, confidence = stray_select
                    self.log(f"Found a leftover Select Portal from a previous match (confidence="
                             f"{confidence:.2f}) - clicking through instead of starting a new one.")
                    click_at(x, y)
                    time.sleep(0.5)
                    self.set_phase("RE-ENTERING PORTAL", "#4fc3f7")
                    reenter_result = reselect_portal(category_key)
                    if reenter_result == "RECONNECTED":
                        self.log("Disconnected while re-entering - reconnected. Redoing full navigation...")
                        need_full_navigation = True
                    elif not reenter_result:
                        self.log(f"Couldn't re-select a {portal_label} after the leftover screen - "
                                 f"stopping. Check {config.DEBUG_DIR} for the frame at that moment.")
                        # Without this the run reports as a clean, successful finish
                        # instead of the problem it actually is - same fix as the
                        # other dead ends in this file.
                        config.STUCK_DETECTED = "COULD NOT RE-ENTER PORTAL"
                        return
                    else:
                        is_repeat_match = True
                else:
                    self.ui.report_unrecognised_screen(screenshot, "no_items", "the lobby's Items button")
                    self.log("No Items button detected - assuming already in-game, skipping navigation.")

            # After a reward-reselect restarts the match, the game keeps your
            # position/camera from before - no need to walk or re-anchor again, just
            # re-cast if fishing's enabled. Only a fresh entry (first match, or after a
            # reconnect drops you back at the lobby) needs those.
            consecutive_defeats = 0
            # Whether the rod counts as equipped. fishing.png is a TOGGLE and nothing on
            # screen reliably shows which way it is (measured 2026-09-14: the button looks
            # the same on and off), so the bot must never click it on a guess.
            #   equip_rod False (default): the player equips the rod before starting;
            #     the toggle is never touched, only the fishing spot is clicked.
            #   equip_rod True: the rod button is clicked once, the first time it's
            #     found this run, and never again.
            fishing_equipped = not equip_rod

            # A macro's recording already carries whatever walk it needs (see
            # stage_player.play_preset) - fishing for it is just a fixed cast point,
            # no destination to pick. Auto Play has no preset to embed a walk into, so
            # it still needs its own standalone walk, played below only when fishing.
            is_macro = bool(preset_name) and preset_name != config.AUTO_PLAY_PRESET_NAME

            while not config.STOP_REQUESTED:
                if need_full_navigation:
                    # The rod's equip memory is deliberately NOT forgotten here any more.
                    # Forgetting it made start_fishing() click the toggle again after every
                    # re-navigation - on a rod Auto Rod had kept equipped - which switched
                    # fishing OFF at random points in a run (live, 2026-09-14).
                    nav_succeeded = False
                    reentered = False
                    nav_failures = 0
                    while not config.STOP_REQUESTED:
                        self.set_phase("NAVIGATING MENUS", "#4fc3f7")

                        self.log(f"Searching for {portal_label}...")
                        nav_result = run_portal_flow(category_key)

                        if nav_result == "RECONNECTED":
                            self.log("Disconnected mid-navigation - reconnected. Retrying...")
                            continue
                        if not nav_result:
                            if config.STOP_REQUESTED:
                                break
                            # Not finding the lobby often just means a result screen is
                            # still up. If it offers Select Portal, enter from there.
                            if find_template(capture_screen(), config.PORTAL_SELECT_BTN, config.MATCH_THRESHOLD):
                                self.log("Not at the lobby, but Select Portal is on screen - "
                                         "entering the next portal from here...")
                                outcome = self._reenter_portal(category_key, portal_label)
                                if outcome == "OK":
                                    nav_succeeded = reentered = True
                                    break
                                if outcome == "STOPPED":
                                    break
                                if outcome == "RECONNECTED":
                                    continue

                            nav_failures += 1
                            if nav_failures < PORTAL_NAV_MAX_ATTEMPTS:
                                self.log(f"Couldn't get into a {portal_label} (attempt {nav_failures}/"
                                         f"{PORTAL_NAV_MAX_ATTEMPTS}) - trying again in 10s...")
                                waited = 0.0
                                while waited < 10.0 and not config.STOP_REQUESTED:
                                    time.sleep(1.0)
                                    waited += 1.0
                                continue

                            self.log(f"Failed to find/activate {portal_label} after {PORTAL_NAV_MAX_ATTEMPTS} "
                                     f"attempts - you may not own one, or the search box/first-result "
                                     f"coordinates need tuning. Stopping.")
                            # Same chokepoint fix as _navigate_from_lobby(): a plain
                            # vision/config miss here must not look identical to a
                            # user-requested stop by the time _notify_run_ended() runs.
                            if not config.STOP_REQUESTED:
                                config.STUCK_DETECTED = "PORTAL NAVIGATION FAILED"
                            break

                        time.sleep(config.PORTAL_ACTIVATE_TO_START_DELAY)
                        self.log("Clicking Start...")
                        start_result = click_portal_start()
                        if start_result == "RECONNECTED":
                            self.log("Disconnected while clicking Start - reconnected. Retrying...")
                            continue
                        if not start_result:
                            # Was silently discarded before this fix - a missed Start
                            # click here left the run waiting forever for a match that
                            # never loaded, reported identically to a clean run. Same
                            # chokepoint fix as the nav_result branch just above.
                            nav_failures += 1
                            if nav_failures < PORTAL_NAV_MAX_ATTEMPTS and not config.STOP_REQUESTED:
                                self.log(f"Couldn't click Start for {portal_label} (attempt "
                                         f"{nav_failures}/{PORTAL_NAV_MAX_ATTEMPTS}) - trying again in 10s...")
                                waited = 0.0
                                while waited < 10.0 and not config.STOP_REQUESTED:
                                    time.sleep(1.0)
                                    waited += 1.0
                                continue
                            self.log(f"Couldn't click Start for {portal_label} after "
                                     f"{PORTAL_NAV_MAX_ATTEMPTS} attempts - stopping.")
                            if not config.STOP_REQUESTED:
                                config.STUCK_DETECTED = "PORTAL NAVIGATION FAILED"
                            break

                        nav_succeeded = True
                        break

                    if config.STOP_REQUESTED or not nav_succeeded:
                        return
                    # Re-entering through Select Portal restarts the match where the
                    # character already stands, same as the after-victory re-entry.
                    is_repeat_match = reentered

                self.set_phase("WAITING FOR START GAME", "#4fc3f7")
                game_result = wait_for_start_game()
                if game_result == "RECONNECTED":
                    self.log("Disconnected while loading - reconnected. Redoing navigation...")
                    need_full_navigation = True
                    is_repeat_match = False
                    continue
                if not game_result:
                    return

                # Whether this match should be fishing, and where to cast. Always the
                # Portals page's configured spot (config.PORTAL_AUTOPLAY_FISH_X/
                # Y_DEFAULT unless the player changed it) rather than wherever the
                # cursor happens to be sitting - relying on the cursor's last position
                # (modules.fishing.click_in_place()) was what made fishing miss
                # intermittently: a macro's own final click doesn't always land on the
                # fishing spot, and press_start_after_macro() below moves the cursor to
                # the Start Game button right before this anyway, so click-in-place was
                # casting on whatever was under THAT click, not the fishing spot.
                fishing_active = fish_enabled
                cast_pos = fish_pos if fishing_active else None

                SESSION.match_started()
                if not is_repeat_match:
                    self.log(f"{portal_label} loaded! Anchoring camera...")
                    self.set_phase("ANCHORING CAMERA", "#4fc3f7")
                    anchor_camera()

                if not is_macro and fishing_active and not is_repeat_match and not config.STOP_REQUESTED:
                    # Auto Play has no macro of its own to carry a walk in, so fishing
                    # there plays this separately recorded one first - the player's own
                    # recording (made the normal way, via this same portal's "My macro"
                    # slot under the reserved name config.PORTAL_AUTOPLAY_WALK_PRESET),
                    # not a different movement system. Only on a fresh entry, same as
                    # everywhere else a leading walk is skipped on a genuine repeat.
                    self.log("Walking to the fishing spot (Auto Play)...")
                    self.set_phase("WALKING", "#4fc3f7")
                    walk_result = play_preset(config.PORTAL_PRESET_LOCATION, category_key,
                                              config.PORTAL_AUTOPLAY_WALK_PRESET, skip_movement=False)
                    if walk_result == "RECONNECTED":
                        self.log("Disconnected mid-walk - reconnected. Redoing navigation...")
                        need_full_navigation = True
                        is_repeat_match = False
                        continue
                    if not walk_result and not config.STOP_REQUESTED:
                        # No longer a reason to give up on fishing this run: casting is
                        # always at the configured coordinates now (see cast_pos
                        # above), not wherever this walk would have left the cursor.
                        self.log(f"No separate walk recorded for Auto Play fishing "
                                 f"('{config.PORTAL_AUTOPLAY_WALK_PRESET}') - casting at the configured "
                                 f"coordinates from wherever Auto Play already is.")

                if is_macro:
                    # The player's own macro for this portal, recorded the same way as a
                    # Story one - walk (if any), then place units. is_repeat_match is
                    # already exactly "is the character known to still be at last
                    # match's spot" (it's what the old walk-and-fish system gated on
                    # too), so a leading walk in the macro reuses that same signal
                    # rather than needing its own tracking.
                    self.log(f"Playing macro '{preset_name}'..." if not is_repeat_match else
                            f"Playing macro '{preset_name}' (already at the recorded spot - not walking)...")
                    self.set_phase("PLAYING TIMELINE", "#2e7d32")
                    preset_result = play_preset(config.PORTAL_PRESET_LOCATION, category_key, preset_name,
                                                skip_movement=is_repeat_match)
                    if preset_result == "RECONNECTED":
                        self.log("Disconnected during playback - reconnected. Redoing navigation...")
                        need_full_navigation = True
                        is_repeat_match = False
                        continue
                    if not preset_result:
                        if not config.STOP_REQUESTED:
                            self.log(f"Macro '{preset_name}' couldn't be played (missing or empty) - stopping.")
                            config.STUCK_DETECTED = "PRESET PLAYBACK FAILED"
                        return
                    # The macro only places units - see the same fix's comment in
                    # run_bot(). Safe even if the macro (or Auto Start) already began
                    # the match: press_start_after_macro() only clicks when it finds
                    # the button.
                    press_start_after_macro()
                else:
                    time.sleep(0.4)  # let anchor_camera()'s (or the walk's) own motion settle first
                    self.log("Checking Auto Play toggle...")
                    self.set_phase("CHECKING AUTO PLAY", "#2e7d32")
                    if not ensure_autoplay_enabled():
                        self.log("WARNING: Couldn't find the Auto Play button on screen - continuing anyway.")
                    self.log("Clicking Start Game...")
                    if not click_start_game():
                        self.log("WARNING: Couldn't find Start Game button to click.")

                if fishing_active and is_repeat_match and not config.STOP_REQUESTED:
                    # First entries get a natural settle delay from the multi-second
                    # walk; repeat matches skip the walk entirely and would otherwise
                    # cast immediately after clicking Start Game, before the match has
                    # actually finished loading (fishing.png may not be ready yet, so
                    # the click was silently missing).
                    time.sleep(2.0)

                if fishing_active and not config.STOP_REQUESTED:
                    self.log("Re-casting..." if is_repeat_match else "Casting...")
                    self.set_phase("FISHING", "#2e7d32")
                    fishing_equipped = start_fishing(already_equipped=fishing_equipped, cast_pos=cast_pos)
                    if not fishing_equipped:
                        self.log("WARNING: Fishing button wasn't on screen - the rod never got equipped, "
                                 "so this match won't actually fish. (Is Auto Rod equipped? Is fishing.png "
                                 "still accurate?)")

                self.log("Waiting for the match to end...")
                self.set_phase("WAITING FOR MATCH END", "#4fc3f7")
                # allow_game_results=False: Portals' reward-pick cards can render on
                # top of the Game Results recovery button while still leaving it
                # matchable underneath - confirmed live, it was getting clicked
                # through the cards mid-pick and corrupting the reward flow. It's
                # safe again once picking is done - click_select_portal() (below,
                # after pick_portal_reward()) has it back on for exactly that
                # recovery case.
                end_result = wait_for_match_result(fishing=fishing_active, cast_pos=cast_pos, allow_game_results=False)

                # Confirmed via a real capture (see wait_for_reward_or_select): most
                # wins go straight from Victory to a plain summary screen with no
                # reward choice at all - "Select Portal" is already sitting on the
                # SAME frame as the Victory banner. A genuine reward choice (an
                # "Auto-selecting in..." countdown) is the rare case, not the
                # default - so this checks for either instead of assuming the
                # choice screen is always coming.
                if end_result == "VICTORY":
                    self.log(f"{portal_label} - Victory! Checking for a reward choice...")
                    reward_wait = wait_for_reward_or_select()
                    if reward_wait in ("REWARD_SELECT", "SELECT_PORTAL_READY"):
                        end_result = reward_wait
                    elif reward_wait == "RECONNECTED":
                        end_result = "RECONNECTED"

                if end_result == "RECONNECTED":
                    self.log("Disconnected while waiting for match end - reconnected. Redoing navigation...")
                    need_full_navigation = True
                    is_repeat_match = False
                    continue
                if not end_result:
                    self.log("Cancelled while waiting for match end.")
                    return
                if end_result == "DEFEAT":
                    consecutive_defeats += 1
                    SESSION.defeat()
                    self.log(f"{portal_label} - Defeat ({consecutive_defeats} in a row). "
                             f"That's one of this portal's 3 hearts.")

                    if config.defeat_limit_reached(consecutive_defeats):
                        self.log(f"{consecutive_defeats} losses in a row - a whole portal burned through "
                                 f"and the next one going the same way. Stopping: this preset can't clear "
                                 f"this portal.")
                        self.set_phase("TOO MANY DEFEATS", "#ff1744")
                        # Without this, _cleanup() sees STOP_REQUESTED still False and
                        # STUCK_DETECTED still None - indistinguishable from a clean
                        # finish, so _notify_run_ended() sent a GREEN "Run finished"
                        # for a run that just gave up on unwinnable content. See the
                        # same fix in run_bot() and run_challenges().
                        config.STUCK_DETECTED = "TOO MANY DEFEATS"
                        health.save_debug_screenshot("too_many_defeats")
                        return

                    if self._limit_reached():
                        return

                    self.set_phase("REPEATING STAGE", "#4fc3f7")
                    # Bounded, unlike Story/Raids' unlimited wait, because here a missing
                    # Repeat Stage button is expected rather than exceptional: a portal has
                    # 3 hearts, and the third loss destroys it, so the button stops
                    # appearing.
                    repeat_result = wait_for_repeat_stage(timeout=30.0)

                    if repeat_result == "RECONNECTED":
                        self.log("Disconnected while waiting to repeat - reconnected. Redoing navigation...")
                        need_full_navigation = True
                        is_repeat_match = False
                        continue
                    if repeat_result == "TIMEOUT":
                        # No Repeat Stage: this portal used its last heart (or the button
                        # wasn't recognised). The defeat screen still offers Select Portal,
                        # so the next portal is picked right here, the same way as after a
                        # win. Heading for the lobby instead was the bug that ended an
                        # 8-hour run on 2026-09-14: the bot was still on the defeat screen,
                        # never found the lobby's Items button, and stopped.
                        self.log(f"No Repeat Stage button after the defeat - picking the next "
                                 f"{portal_label} with Select Portal instead...")
                        self.set_phase("RE-ENTERING PORTAL", "#4fc3f7")
                        outcome = self._reenter_portal(category_key, portal_label)
                        if outcome == "STOPPED":
                            return
                        if outcome == "OK":
                            self.log("Entered the next portal from the defeat screen.")
                            need_full_navigation = False
                            is_repeat_match = True
                            continue
                        if outcome == "RECONNECTED":
                            self.log("Disconnected while re-entering - reconnected. Redoing full navigation...")
                        else:
                            self.log("Select Portal didn't work from the defeat screen either - "
                                     "trying a fresh entry from the lobby.")
                        need_full_navigation = True
                        is_repeat_match = False
                        continue
                    if not repeat_result:
                        self.log("Cancelled while waiting to repeat.")
                        return

                    # Confirmed in-game: a repeated match keeps your position and camera,
                    # so there's no need to re-walk or re-anchor - just a re-cast if
                    # fishing is on, which the is_repeat_match branch above handles.
                    self.log("Stage repeated on the same portal - already positioned, skipping walk/camera.")
                    need_full_navigation = False
                    is_repeat_match = True
                    continue
                if end_result == "VICTORY":
                    # This is the dead end: a win was confirmed but NEITHER the
                    # reward choice NOR Select Portal ever cleared their match,
                    # which per the confirmed real flow should never legitimately
                    # happen - one of the two is always on that screen. Both
                    # templates now have same-size captures at the embedded scale,
                    # so the shrink penalty is no longer a candidate explanation;
                    # a stale crop (a game update restyling the screen) is. Save
                    # the exact frame instead of stopping blind - a bug report with
                    # no screen to check is unactionable, one with this screenshot
                    # is usually obvious at a glance.
                    path = health.save_debug_screenshot("portal_no_reward_after_victory")
                    self.log(f"Victory, but neither a reward choice nor Select Portal showed up in "
                             f"time - stopping. Saved the screen to {path or config.DEBUG_DIR} - if "
                             f"either is visibly there, that template needs recapturing "
                             f"(python tools/retemplate.py).")
                    # Same fix as MAX_CONSECUTIVE_DEFEATS elsewhere in this file: without
                    # this, _cleanup() sees STOP_REQUESTED still False and STUCK_DETECTED
                    # still None - indistinguishable from a clean finish - and reports a
                    # GREEN "Run finished" for a run that just hit a template-matching
                    # dead end.
                    config.STUCK_DETECTED = "NO REWARD OR SELECT PORTAL"
                    return

                consecutive_defeats = 0
                SESSION.victory()

                if end_result == "REWARD_SELECT":
                    SESSION.reward_picked()
                    self.log("Reward-choice screen detected - picking a portal...")
                    self.set_phase("PICKING REWARD", "#2e7d32")
                    pick_portal_reward(avoid_traitless)
                    time.sleep(0.5)
                else:
                    # end_result == "SELECT_PORTAL_READY" - no choice was offered
                    # this time, so there's nothing to pick; Select Portal is
                    # already up and waiting.
                    self.log("No reward choice offered this time - Select Portal is already up.")

                if self._limit_reached():
                    return

                self.set_phase("RE-ENTERING PORTAL", "#4fc3f7")
                # Retried rather than a one-shot attempt: confirmed live
                # (2026-09-13, after 55 straight clean matches) that this step can
                # fail once on a transient render/network hiccup and then succeed
                # immediately on a plain retry - the picker reopening or the confirm
                # button rendering is occasionally just slow, not actually broken.
                # The search-first-result design also structurally CANNOT run out of
                # portals (see modules/portal_select.py's own module docstring), so
                # "you may have none left" was always the wrong diagnosis for this
                # failure - it was a one-off hiccup that a whole unattended overnight
                # run was being ended over. Bounded at 3 attempts so a GENUINE break
                # (a stale template, a restyled screen) still stops the run rather
                # than retrying forever against something that will never recover.
                outcome = self._reenter_portal(category_key, portal_label)

                if outcome == "STOPPED":
                    return
                if outcome == "RECONNECTED":
                    self.log("Disconnected while re-entering - reconnected. Redoing full navigation...")
                    need_full_navigation = True
                    is_repeat_match = False
                    continue
                if outcome != "OK":
                    self.log(f"Still couldn't re-enter a {portal_label} after "
                             f"{PORTAL_REENTER_MAX_ATTEMPTS} attempts - stopping. Check "
                             f"{config.DEBUG_DIR} for the frame from each attempt: if the "
                             f"button was visibly there every time, the template needs "
                             f"recapturing (python tools/retemplate.py); if the screen looked "
                             f"different from usual, that's the real cause to chase down.")
                    # Without this the run reports as a clean, successful finish
                    # instead of the problem it actually is - same fix as the other
                    # dead ends in this file and MAX_CONSECUTIVE_DEFEATS.
                    config.STUCK_DETECTED = "COULD NOT RE-ENTER PORTAL"
                    return

                self.log("Match restarted - already positioned, skipping walk/camera this time.")
                need_full_navigation = False
                is_repeat_match = True
                # loop back for another match
        except Exception as e:
            self.log(f"ERROR: Bot crashed unexpectedly - {e}")
            # Without this a crash reached _cleanup() looking like a clean finish and was
            # reported as a green "Run finished" right after the "Bot crashed" alert.
            if not self.user_stop_requested:
                config.STUCK_DETECTED = f"CRASHED ({type(e).__name__})"
            # Best-effort screenshot of whatever was on screen at the moment it died -
            # wrapped separately so a capture failure (e.g. Roblox already gone) can't
            # swallow the crash notification itself.
            try:
                shot_bytes = health.jpg_bytes()
            except Exception:
                shot_bytes = None
            notify.send(f"```{e}```", category="problems", title="Bot crashed",
                        good=False, image_bytes=shot_bytes)
        finally:
            self._cleanup()

    def _reenter_portal(self, category_key, portal_label):
        """
        Select Portal -> search -> Select, retried up to PORTAL_REENTER_MAX_ATTEMPTS.

        Used from any result screen that offers Select Portal: after a win (the reward
        screen), and after a defeat that left no Repeat Stage. Returns "OK",
        "RECONNECTED", "STOPPED", or None when every attempt failed.
        """
        for attempt in range(1, PORTAL_REENTER_MAX_ATTEMPTS + 1):
            if config.STOP_REQUESTED:
                return "STOPPED"
            if attempt == 1:
                self.log("Reopening the portal picker...")
            else:
                self.log(f"Retrying ({attempt}/{PORTAL_REENTER_MAX_ATTEMPTS})...")
                time.sleep(2.0)

            select_result = click_select_portal()
            if select_result == "RECONNECTED":
                return "RECONNECTED"
            if not select_result:
                # click_select_portal() already saved a debug screenshot on its own
                # TIMEOUT (see portal_select.py._wait_and_click), so the frame from THIS
                # attempt is sitting in config.DEBUG_DIR.
                self.log(f"Couldn't find the Select Portal button "
                         f"(attempt {attempt}/{PORTAL_REENTER_MAX_ATTEMPTS}).")
                continue

            self.log(f"Searching for the next {portal_label}...")
            reenter_result = reselect_portal(category_key)
            if reenter_result == "RECONNECTED":
                return "RECONNECTED"
            if not reenter_result:
                self.log(f"Couldn't re-select a {portal_label} "
                         f"(attempt {attempt}/{PORTAL_REENTER_MAX_ATTEMPTS}).")
                continue

            # reselect_portal() finding and clicking its confirm button is not proof
            # the right portal got picked: search_portal()/select_portal() type into
            # the search box and click "the first result" at fixed coordinates with no
            # vision check in between (see portal_select.py's own docstrings). If the
            # search box hadn't cleared its old text yet, or the list hadn't finished
            # re-filtering, that blind click can land on the wrong item - and ITS
            # confirm button matches just as cleanly, so reselect_portal() reports
            # success even though nothing that starts a stage was actually chosen.
            # Confirmed against tester logs (2026-09-16/17): three separate runs hit
            # exactly this - "Found ... confidence=1.00 - clicking" right here,
            # followed by a match that never began, caught only 900s later by
            # wait_for_match_result's stuck timeout, which killed the whole session.
            # A short, bounded check for real stage-entry evidence catches it here
            # instead, where a retry is still cheap.
            confirmed = poll_until(
                [
                    target(config.START_GAME_BTN, True, debug_label="reenter_start_game"),
                    target(config.AUTOPLAY_ON_BTN, True, debug_label="reenter_autoplay_on"),
                    target(config.AUTOPLAY_OFF_BTN, True, debug_label="reenter_autoplay_off"),
                    target(config.REWARD_SELECT_TEXT, True, debug_label="reenter_reward_select"),
                    target(config.VICTORY_TEXT, True, debug_label="reenter_victory"),
                    target(config.DEFEAT_TEXT, True, debug_label="reenter_defeat"),
                ],
                interval=1.0,
                label="portal_reenter_verify",
                timeout=20.0,
                stuck_timeout=None,
                lobby_grace=6.0,
            )
            if confirmed == "RECONNECTED":
                return "RECONNECTED"
            if confirmed != True:  # noqa: E712 - "TIMEOUT", or False (cancel/halt - caught at the next loop top)
                health.save_debug_screenshot("portal_reenter_no_stage")
                self.log(f"The picker accepted a selection but no match actually started "
                         f"(attempt {attempt}/{PORTAL_REENTER_MAX_ATTEMPTS}) - probably picked "
                         f"the wrong item. Retrying...")
                continue

            return "OK"
        return None

    def run_challenges(self, sequence, slot_links, give_up_if_nothing_playable=False):
        """
        Runs a sequence of challenge slots (e.g. Regular 1/2/3 -> Daily -> Weekly), each
        optionally reusing an existing Story preset (or Auto Play) via slot_links.

        self.match_limit (set by the caller before start(), same as every other
        gamemode - the "How many runs" card) counts PASSES through the whole
        sequence here, not individual matches: "Regular - All three" plus 1 run
        means all 3 Regular slots played once, not 1 slot then stop. 0 ("Forever")
        never stops on its own - a finished pass just restarts from the top, which is
        how Regular Challenges (unlock every ~10 min) get farmed continuously over a
        long run. Either way, a pass that finds nothing playable at all (everything
        on cooldown) doesn't count towards the target - it isn't a real pass, and a
        bounded run count should still wait for one it can actually complete rather
        than quit having done nothing.

        give_up_if_nothing_playable is for a run QUEUE step: there, a pass with
        nothing playable ends the step immediately (so the queue can move on to
        whatever comes next) instead of waiting CHALLENGE_LOOP_WAIT_SECONDS to
        recheck - camping in place would otherwise hold up every step after this one
        for as long as everything stays on cooldown. A standalone run (this page's
        own Start button) always waits, regardless of this flag - that wait is the
        entire point of "Forever" there.

        Assumes it starts at the lobby (no in-progress-match/select-stage detection like
        Story/Raids have, since a mid-sequence resume isn't well-defined here).
        """
        try:
            self.set_phase("DETECTING STATE", "#a8a8a8")
            if not self.ui.focus_and_pin():
                return

            index = 0
            nav_context = "cold"
            previous_category = None
            pass_num = 1
            played_this_pass = 0
            passes_completed = 0
            target_passes = int(getattr(self, "match_limit", 0) or 0)
            consecutive_defeats = 0

            while not config.STOP_REQUESTED:
                if index >= len(sequence):
                    if played_this_pass == 0:
                        if give_up_if_nothing_playable:
                            self.log("Nothing was playable this pass (everything's on cooldown) - "
                                     "nothing left to do for this queue step.")
                            break
                        wait_s = config.CHALLENGE_LOOP_WAIT_SECONDS
                        self.log(f"Pass {pass_num} complete - nothing was playable (all on cooldown). "
                                 f"Waiting {int(wait_s)}s before checking again...")
                        self.set_phase("WAITING TO RE-CHECK", "#4fc3f7")
                        slept = 0.0
                        while slept < wait_s and not config.STOP_REQUESTED:
                            time.sleep(1.0)
                            slept += 1.0
                        index = 0
                        pass_num += 1
                        played_this_pass = 0
                        continue

                    passes_completed += 1
                    self.log(f"Pass {pass_num} complete - {played_this_pass} challenge(s) played this round.")
                    if target_passes and passes_completed >= target_passes:
                        self.log(f"Completed {passes_completed} of {target_passes} requested run(s) - done.")
                        break

                    index = 0
                    pass_num += 1
                    played_this_pass = 0
                    continue

                slot_key = sequence[index]
                slot_label = config.CHALLENGE_SLOTS[slot_key]["label"]
                category = config.CHALLENGE_SLOTS[slot_key]["category"]
                self.set_phase(f"ENTERING {slot_label.upper()}", "#4fc3f7")
                self.log(f"Navigating to {slot_label}...")

                nav_result = run_challenge_slot_flow(slot_key, nav_context, previous_category)

                if nav_result == "RECONNECTED":
                    self.log("Disconnected mid-navigation - reconnected. Retrying this challenge from the lobby...")
                    nav_context, previous_category = "cold", None
                    continue

                if nav_result == "SKIP":
                    SESSION.challenge_skipped()
                    self.log(f"{slot_label} is on cooldown (Available in...) - skipping.")
                    nav_context, previous_category = "after_skip", category
                    index += 1
                    continue

                if not nav_result:
                    self.log(f"Failed to enter {slot_label}.")
                    # A navigation miss, not a user stop - must not report as "Run finished".
                    if not config.STOP_REQUESTED:
                        config.STUCK_DETECTED = "CHALLENGE NAVIGATION FAILED"
                    break

                # nav_context still describes how this slot was reached, and the Start
                # button moves depending on that - see click_challenge_start().
                self.log("Clicking Start...")
                click_challenge_start(after_match=(nav_context == "after_match"))

                self.set_phase("WAITING FOR START GAME", "#4fc3f7")
                game_result = wait_for_start_game()

                if game_result == "RECONNECTED":
                    self.log("Disconnected while loading - reconnected. Retrying this challenge...")
                    nav_context, previous_category = "cold", None
                    continue
                if not game_result:
                    break

                SESSION.match_started()
                self.log(f"{slot_label} loaded! Anchoring camera...")
                self.set_phase("ANCHORING CAMERA", "#4fc3f7")
                anchor_camera()

                link = slot_links.get(slot_key) or {}
                preset_name = link.get("preset") or config.AUTO_PLAY_PRESET_NAME
                reconnected_mid_match = False

                if preset_name == config.AUTO_PLAY_PRESET_NAME:
                    self.log("Auto Play selected for this challenge - checking toggle...")
                    self.set_phase("CHECKING AUTO PLAY", "#2e7d32")
                    if not ensure_autoplay_enabled():
                        self.log("WARNING: Couldn't find the Auto Play button on screen - continuing anyway.")
                    # Safety net for a forgotten in-game Auto Start/Auto Retry: if the
                    # match already began on its own (banner or wave counter say so -
                    # see match_already_started()), Start Game has nothing left to do
                    # and clicking it blind risks a stray misclick on whatever's under
                    # it instead.
                    if match_already_started():
                        self.log("Match already started (Game Started! banner or a wave under way) - "
                                 "not clicking Start Game.")
                    else:
                        self.log("Clicking Start Game...")
                        if not click_start_game():
                            self.log("WARNING: Couldn't find Start Game button to click.")
                else:
                    map_key, act_key = link.get("map"), link.get("act")
                    if not map_key or not act_key:
                        self.log(f"WARNING: No preset linked for {slot_label} - nothing to play. "
                                 f"Set it via 'Configure Challenge Presets...' or use Auto Play.")
                    else:
                        self.log(f"Executing preset '{preset_name}' from {map_key}/{act_key}...")
                        self.set_phase("PLAYING TIMELINE", "#2e7d32")
                        preset_result = play_preset(map_key, act_key, preset_name)

                        if preset_result == "RECONNECTED":
                            self.log("Disconnected during playback - reconnected. Retrying this challenge...")
                            nav_context, previous_category = "cold", None
                            reconnected_mid_match = True
                        elif not preset_result:
                            self.log(f"Preset playback failed for {slot_label}.")

                if reconnected_mid_match:
                    continue

                self.log("Waiting for the match to end...")
                self.set_phase("WAITING FOR MATCH END", "#4fc3f7")
                # Challenges don't show a Repeat Stage button (you can't repeat one) -
                # Victory/Defeat is the match-end signal here instead.
                end_result = wait_for_match_result()

                if end_result == "RECONNECTED":
                    self.log("Disconnected while waiting for match end - reconnected. Retrying this challenge...")
                    nav_context, previous_category = "cold", None
                    continue
                if not end_result:
                    break

                if end_result == "DEFEAT":
                    consecutive_defeats += 1
                    SESSION.defeat()
                    self.log(f"{slot_label} was defeated ({consecutive_defeats} in a row) - "
                             f"moving on to the next challenge.")

                    # Challenges advance rather than retry, so a streak this long isn't one
                    # hard stage - it's every linked preset losing, which usually means the
                    # links point at the wrong presets rather than that the challenges are hard.
                    if config.defeat_limit_reached(consecutive_defeats):
                        self.log(f"{consecutive_defeats} challenges lost in a row - stopping. Check the "
                                 f"presets linked under 'Configure Challenge Presets...'.")
                        self.set_phase("TOO MANY DEFEATS", "#ff1744")
                        # See the matching comment in run_portal(): without this the
                        # run reports as a clean, successful finish instead of the
                        # problem it actually is.
                        config.STUCK_DETECTED = "TOO MANY DEFEATS"
                        health.save_debug_screenshot("too_many_defeats")
                        break
                elif end_result == "VICTORY":
                    consecutive_defeats = 0
                    SESSION.victory()
                    self.log(f"{slot_label} complete.")
                else:
                    # Portals' reward screen shouldn't ever show up here - treat it as
                    # match-end anyway rather than silently calling it a win.
                    self.log(f"{slot_label} ended ({end_result}) - moving on.")
                nav_context, previous_category = "after_match", category
                played_this_pass += 1
                index += 1
        except Exception as e:
            self.log(f"ERROR: Bot crashed unexpectedly - {e}")
            # Without this a crash reached _cleanup() looking like a clean finish and was
            # reported as a green "Run finished" right after the "Bot crashed" alert.
            if not self.user_stop_requested:
                config.STUCK_DETECTED = f"CRASHED ({type(e).__name__})"
            # Best-effort screenshot of whatever was on screen at the moment it died -
            # wrapped separately so a capture failure (e.g. Roblox already gone) can't
            # swallow the crash notification itself.
            try:
                shot_bytes = health.jpg_bytes()
            except Exception:
                shot_bytes = None
            notify.send(f"```{e}```", category="problems", title="Bot crashed",
                        good=False, image_bytes=shot_bytes)
        finally:
            self._cleanup()

    def run_expedition(self, expedition_key, difficulty, material, preset_name, zoom_out_steps=None):
        """
        Farms one expedition: lobby -> (route, camera, units, Start Game) -> run ->
        extract -> Repeat Stage -> again. The flow itself lives in modules/expedition.py;
        this owns the outer loop, reconnect handling and how a run's end is reported.
        """
        def _mark_failure(reason):
            # A False from the runner without a user stop must never read as a clean
            # finish - the runner sets its own reasons, this is only the safety net.
            if not config.STOP_REQUESTED and not config.STUCK_DETECTED:
                config.STUCK_DETECTED = reason

        try:
            self.set_phase("DETECTING STATE", "#a8a8a8")
            if not self.ui.focus_and_pin():
                return

            label = config.EXPEDITIONS[expedition_key]["label"]
            material_label = config.EXPEDITION_MATERIALS.get(material, {}).get("label", "Random")
            runner = ExpeditionRunner(expedition_key, difficulty, material, preset_name,
                                      set_phase=self.set_phase, zoom_out_steps=zoom_out_steps)

            screenshot = capture_screen()
            self.ui.calibrate_ui_scale(screenshot)
            state = runner.detect_state(capture_screen())
            self.log(f"Expedition: {label}, difficulty {difficulty}, farming {material_label}, "
                     f"macro '{preset_name}', camera zoom-out {zoom_out_steps}. Starting from: {state}.")

            if state == "REPEAT":
                runner.click_repeat_if_present()
                stage = "PRESTART"
            elif state == "INRUN":
                self.log("Not at the lobby or a stage start - assuming a run is already going. "
                         "Its checkpoint count starts at 0 here, so this one run may extract a "
                         "checkpoint late.")
                stage = "INRUN"
            else:
                stage = "PRESTART"
            need_nav = state == "LOBBY"

            while not config.STOP_REQUESTED:
                if need_nav:
                    result = runner.navigate()
                    if result == "RECONNECTED":
                        self.log("Disconnected mid-navigation - reconnected. Retrying...")
                        continue
                    if not result:
                        self.log(f"Failed to navigate to {label}.")
                        _mark_failure("EXPEDITION NAVIGATION FAILED")
                        return
                    need_nav = False
                    stage = "PRESTART"

                if stage == "PRESTART":
                    result = runner.prepare_and_start()
                    if result == "RECONNECTED":
                        self.log("Disconnected before the run started - reconnected. Redoing navigation...")
                        need_nav = True
                        continue
                    if not result:
                        _mark_failure("EXPEDITION START FAILED")
                        return
                    stage = "INRUN"

                result = runner.play_run()
                if result == "RECONNECTED":
                    self.log("Disconnected mid-run - reconnected. Redoing navigation...")
                    need_nav = True
                    continue
                if result == "REPEAT":
                    if self._limit_reached():
                        # Repeat Stage is found but deliberately NOT clicked here (see
                        # modules.expedition.ExpeditionRunner.play_run()'s own comment) -
                        # stopping now leaves it genuinely still on screen, which a
                        # queue's next step (modules.lobby.return_to_lobby()) already
                        # knows how to Exit from. Clicking it first and stopping after
                        # would have left a brand new stage half-loaded instead, which
                        # nothing downstream can recognise.
                        return
                    self.log("Expedition repeated - setting up the next run.")
                    if not runner.click_repeat_if_present():
                        self.log("Repeat Stage didn't click - retrying...")
                        continue
                    stage = "PRESTART"
                    continue
                _mark_failure("EXPEDITION RUN FAILED")
                return
        except Exception as e:
            self.log(f"ERROR: Bot crashed unexpectedly - {e}")
            # Without this a crash reached _cleanup() looking like a clean finish and was
            # reported as a green "Run finished" right after the "Bot crashed" alert.
            if not self.user_stop_requested:
                config.STUCK_DETECTED = f"CRASHED ({type(e).__name__})"
            try:
                shot_bytes = health.jpg_bytes()
            except Exception:
                shot_bytes = None
            notify.send(f"```{e}```", category="problems", title="Bot crashed",
                        good=False, image_bytes=shot_bytes)
        finally:
            self._cleanup()

    def _supervised_run(self, target, args):
        """
        Runs a gamemode loop, and - with Never Stop on - starts it again whenever it
        stopped on its own because of a problem.

        The loop's own finally-block calls _cleanup(), which decides: with Never Stop on
        and a restartable reason, it sets restart_pending and returns before reporting
        the run as over. This then waits (interruptible by Stop) and calls the loop
        again. Every restart re-runs the loop from its own state detection, so it picks
        up from wherever the game actually is - lobby, a result screen, mid-match.
        """
        restarts_in_a_row = 0
        while True:
            self.restart_pending = False
            started = time.time()
            try:
                target(*args)
            except Exception as e:
                # Every run_* method already catches its own exceptions and reports
                # them as a crash - this is the safety net for whatever somehow gets
                # past that (a wrong argument count from a stale caller, for
                # instance: that fails at THIS call, before target's own try block
                # ever starts). Without it, an exception here kills this whole
                # background thread silently - this is a daemon thread with
                # console=False, so there is no traceback anywhere, not even in the
                # log file - the bot just stops with nothing to explain why. Exactly
                # what "it randomly stopped" looks like from outside.
                self.log(f"ERROR: Bot crashed unexpectedly - {e}")
                if not self.user_stop_requested:
                    config.STUCK_DETECTED = f"CRASHED ({type(e).__name__})"
                try:
                    shot_bytes = health.jpg_bytes()
                except Exception:
                    shot_bytes = None
                try:
                    notify.send(f"```{e}```", category="problems", title="Bot crashed",
                                good=False, image_bytes=shot_bytes)
                except Exception as notify_e:
                    print(f"[notify] Could not send the crash message: {notify_e}")
                self._cleanup()
            if not self.restart_pending:
                return

            if time.time() - started >= RESTART_RESET_AFTER:
                restarts_in_a_row = 0
            restarts_in_a_row += 1
            SESSION.restarted()
            delay = min(RESTART_BASE_DELAY * (2 ** (restarts_in_a_row - 1)), RESTART_MAX_DELAY)
            reason = self.restart_reason

            self.log(f"NEVER STOP: the run stopped on its own ({reason}). Restarting in {delay}s "
                     f"(restart #{SESSION.restarts}).")
            self.set_phase(f"RESTARTING IN {delay}s - {reason}", "#ffb300")
            try:
                notify.send(f"**{reason}**\nNever Stop is on - restarting in {delay}s "
                            f"(restart #{SESSION.restarts}).",
                            category="problems", title="Problem - restarting the run", good=False,
                            fields=SESSION.notify_fields(),
                            image_bytes=self._latest_debug_jpg() or health.jpg_bytes())
            except Exception as e:
                print(f"[notify] Could not send the restart message: {e}")

            # The halt that ended the run set STOP_REQUESTED; clear it so the next run can
            # start. From here only the Stop button (user_stop_requested) ends things.
            config.STOP_REQUESTED = False
            config.STUCK_DETECTED = None
            waited = 0
            while waited < delay and not self.user_stop_requested:
                time.sleep(1.0)
                waited += 1
            if self.user_stop_requested:
                config.STOP_REQUESTED = True
                self._cleanup()
                return
            self.log(f"NEVER STOP: restarting now (restart #{SESSION.restarts}).")

    def _cleanup(self):
        # Every way a run can end funnels through here, which is exactly what a
        # notification wants: the point of messaging Discord is the unattended run that
        # ended at 03:00 for a reason nobody was awake to see.
        stopped_by_user = False
        reason = config.STUCK_DETECTED

        # Never Stop: a problem is not the end of the run. Hand back to _supervised_run()
        # before anything reports the run as over - no summary, no "Run stopped" message,
        # buttons stay as they are. It sends its own "restarting" alert instead.
        if (reason and self.ui.get_user_settings().get("never_stop") and not self.user_stop_requested
                and not reason.startswith(NEVER_STOP_EXEMPT_PREFIXES)):
            self.log(f"STOPPED - {reason}. Never Stop is on, so the run will restart. A screenshot "
                     f"of the screen at that moment is in the '{config.DEBUG_DIR}' folder.")
            self.restart_reason = reason
            self.restart_pending = True
            return

        if reason:
            # polling.py stops the run by setting STOP_REQUESTED, which would otherwise
            # be indistinguishable from the user pressing Stop - the one case where
            # "Bot halted successfully" would be actively misleading.
            self.log(f"STOPPED - {reason}. The log above says what it was waiting for, and a "
                     f"screenshot of the screen at that moment is in the '{config.DEBUG_DIR}' folder.")
            self.set_phase(f"{reason} - CHECK LOG", "#ff1744")
        elif self.user_stop_requested:
            self.log("Bot halted successfully.")
            self.set_phase("IDLE", "#a8a8a8")
            stopped_by_user = True
        else:
            # Ended by itself with nothing wrong: a one-pass Challenges run completed, or
            # Auto Next reached the last act. Every failure path sets STUCK_DETECTED.
            self.log("Run finished.")
            self.set_phase("FINISHED", "#2e7d32")

        summary = SESSION.report()
        if summary:
            for line in summary.splitlines():
                self.log(line)

        # Never allowed to skip the button reset below, whatever it does.
        try:
            self._notify_run_ended(config.STUCK_DETECTED, stopped_by_user, summary)
        except Exception as e:
            print(f"[notify] Could not send the run-ended message: {e}")

        # Deliberately does NOT undock here. The docked layout is meant to persist
        # for the whole time the UI is open, not flicker away every time the bot
        # stops. Roblox only gets its title bar back when the app itself closes.

        self.running = False
        self.ui.reset_buttons()

    def _notify_run_ended(self, stuck_reason, stopped_by_user, summary):
        """
        Tells Discord how the run finished, if notifications are on.

        Split by cause rather than sent as one generic "run ended": a run the user
        stopped is not news, while one that died on its own at 4am is the entire reason
        this feature exists, and a phone notification is only useful if its colour and
        title say which happened without opening it.

        Numbers go in as structured fields (SESSION.notify_fields()) rather than into
        the body text - readable at a glance in the embed instead of a wall of text.

        Every branch attaches a screenshot, not just the problem ones - a picture of
        where the run actually ended up is worth having whether or not anything went
        wrong. A stuck/crashed run prefers the debug screenshot already saved at the
        EXACT moment of failure (whatever health.save_debug_screenshot() last wrote
        for THIS run) over a fresh one, since a poll tick or two can pass between
        that moment and the run fully unwinding to here - the frame that actually
        explains "why" is the one from when it happened, not "whatever's on screen
        now". A clean or user-stopped run has no such moment to prefer, so those
        just take a fresh capture directly.
        """
        if not notify.is_configured():
            return

        fields = SESSION.notify_fields()

        if stuck_reason:
            body = f"**{stuck_reason}**\nSomething stopped this run on its own - see the fields below and the attached screenshot for what it was looking at."
            image = self._latest_debug_jpg() or health.jpg_bytes()
            notify.send(body, category="problems", title="Run stopped on its own",
                        good=False, fields=fields, image_bytes=image)
        elif stopped_by_user:
            notify.send("Stopped by request." if not fields else "Stopped by request - here's what it got through.",
                        category="lifecycle", title="Run stopped", good=None, fields=fields,
                        image_bytes=health.jpg_bytes())
        else:
            notify.send("Finished cleanly." if not fields else "Finished cleanly - here's the session.",
                        category="lifecycle", title="Run finished", good=True, fields=fields,
                        image_bytes=health.jpg_bytes())

    def _latest_debug_jpg(self):
        """
        JPEG bytes of the newest screenshot in config.DEBUG_DIR, or None.

        Used to attach the actual evidence a stuck/crashed run already saved (via
        health.save_debug_screenshot()/StuckTimer.report()) to its Discord
        notification, rather than taking a FRESH screenshot after the fact - by the
        time a run has fully unwound to _cleanup(), the screen has often already
        moved on (Roblox restored to a normal window, GUI undocked), so a new capture
        here would show the wrong moment entirely. The one already on disk is the
        real one.
        """
        try:
            import glob
            shots = glob.glob(os.path.join(config.DEBUG_DIR, "*.png"))
            if not shots:
                return None
            newest = max(shots, key=os.path.getmtime)
            # Older than this run's own start isn't this run's evidence - a debug/
            # folder from a previous session shouldn't get attached to today's message.
            if self.run_started_at and os.path.getmtime(newest) < self.run_started_at:
                return None
            import cv2
            return health.jpg_bytes(cv2.imread(newest))
        except Exception:
            return None

    def stop(self):
        self.user_stop_requested = True
        config.STOP_REQUESTED = True
        self.log("Stop requested. Waiting for action to finish...")
        self.ui.reset_buttons()

    def apply_run_behavior_settings(self):
        """Pushes the defeat limit into config, where every gamemode loop reads it live."""
        settings_ = self.ui.get_user_settings()
        if settings_.get("stop_after_defeats", True):
            config.STOP_AFTER_DEFEATS = max(1, int(settings_.get("max_defeats", config.MAX_CONSECUTIVE_DEFEATS)))
        else:
            config.STOP_AFTER_DEFEATS = 0
