# config.py
"""
Global Configuration & State Management
"""

# Bumped by hand on every release. Compared against GitHub's latest release tag by
# modules/update_check.py - see its own module docstring for the full scheme.
APP_VERSION = "1.14"

# Flow Control
STOP_REQUESTED = False
TIMEOUT_SECONDS = 10.0
POLL_INTERVAL = 0.5
MATCH_THRESHOLD = 0.85

# Autoclicker live state - flipped by the global keybind or the mode page's toggle
# (UI thread) and read every tick by modules.autoclicker (engine thread), the same
# cross-thread-flag pattern STOP_REQUESTED above already uses. AUTOCLICKER_POS is a
# reference-space (x, y) set via the Locate button, or None until the player picks one.
AUTOCLICKER_ENABLED = False
AUTOCLICKER_POS = None
AUTOCLICKER_INTERVAL = None
AUTOCLICKER_DEFAULT_INTERVAL = 2.0

# Per-template overrides of MATCH_THRESHOLD, consulted by vision.find_template().
# One global threshold has to serve every template at once, so tuning it for the one
# flaky template loosens matching for all ~25 of them - and a false positive on
# victory.png or defeat.png now misroutes the whole match-end decision. Put a template
# here to give it its own threshold instead: raise it for big high-contrast art that
# should never match loosely, lower it for small text or animated elements that sit
# just under the global. Anything absent uses whatever the caller passed.
#   Example: TEMPLATE_THRESHOLDS = {VICTORY_TEXT: 0.92, AVAILABLE_IN_TEXT: 0.78}
TEMPLATE_THRESHOLDS = {
    # The Challenge card slides as its list settles, so its confidence oscillates
    # instead of holding still. Measured across a real session it sat at 0.803, 0.827,
    # 0.836, 0.842, 0.843, 0.845 while plainly on screen, only occasionally crossing
    # the 0.85 default - which is why it took ~10s and ~20 polls to catch, then worked
    # fine. When the card is genuinely absent it scores 0.165-0.182, so the gap between
    # present and absent is enormous and 0.78 sits safely in the middle: well clear of
    # the lowest real sighting, nowhere near a false positive.
    "assets/templates/challenge_card.png": 0.78,

    # Story's difficulty buttons and the Act 1 tile were measured across every
    # reference screenshot in Images_For_Claude: present-but-not-selected they score
    # as low as 0.822-0.843 (a subtle glow/highlight shifts depending on which
    # difficulty or act is currently active), while absent they never clear 0.55. 0.8
    # sits well inside that gap. Re-measure with tools/template_check.py if a game
    # update restyles these.
    "assets/templates/story/difficulty_normal.png": 0.8,
    "assets/templates/story/difficulty_hard.png": 0.8,
    "assets/templates/story/act_1.png": 0.8,

    # Next Stage and Select Stage are both plain green pill buttons with bold white
    # text, similar enough in shape/colour that Next Stage read 0.862 confidence
    # against the Story entrance screen's Select Stage button in testing - just
    # over the 0.85 default. Raised well above that false reading; the genuine match
    # (Victory screen) scores 1.000, so 0.93 costs nothing real.
    "assets/templates/next_stage.png": 0.93,

    # The raid difficulty tiles used to be listed here at 0.8/0.68/0.72. Those figures
    # were measured against crops of the WHOLE tile, whose selected (red) vs unselected
    # (grey) styling is what dragged their present-scores down to 0.766-0.835 in the
    # first place. They now have same-size captures cropped tight to just the number,
    # which excludes almost all of that styling - so the loose thresholds no longer buy
    # anything and are an active liability at this crop size: 0.68 against a ~36x26
    # glyph is easily reachable by an unrelated digit elsewhere on screen, and a false
    # match here silently plays the WRONG difficulty. Left on the 0.85 default (0.789
    # at the embedded scale) with the rest. If one of them misses, the log prints the
    # confidence it did reach - tune from that number rather than guessing.

    # Both now HAVE same-size captures at the embedded scale, so these entries are
    # currently no-ops (a template with a variant already gets the mild relief).
    # They stay as a safety net for the case where a variant is missing - at some
    # other client size, or before one has been recaptured after a game update.
    # Both were measured scoring high on screens where they are absent: play_small
    # reads 0.639 against empty lobby ground, and reconnect_btn 0.732 against a
    # portal pre-start screen. Without these entries the large shrink relief would
    # drop the bar to 0.605 and turn both into standing false positives - and a
    # false reconnect_btn is the expensive one, since it makes the loop abandon a
    # live match to "reconnect" from a disconnect that never happened.
    "assets/templates/play_small.png": 0.85,
    "assets/templates/reconnect_btn.png": 0.85,
}

# Templates that must look like an ACTUAL rendering of the button, not merely the
# same shape - see vision._looks_exact() for the mechanism and why NCC alone can't
# tell the difference. Value is the max mean-per-pixel absolute difference (0-255)
# allowed between the matched region and the template; anything looser than that is
# treated as no match at all, even though it cleared MATCH_THRESHOLD on shape.
#
# GAME_RESULTS_BTN is the one confirmed case: it renders directly underneath
# Portals' 3-portal reward cards at reduced opacity, and that dimmed duplicate
# scored 0.85-0.95 there - well past threshold on shape alone - while doing nothing
# useful when clicked, since the real interactive thing was the cards on top of it.
# Confirmed live 2026-09-12: six repeated recovery-button clicks in a row against
# that exact screen, none of which had any effect. 40 sits comfortably between an
# undimmed real match (single digits - same render, same compression) and a
# meaningfully dimmed duplicate (60-100+, from a ~30-50% opacity/brightness cut).
#
# Deliberately NOT applied to every template: most SHOULD tolerate the minor
# brightness/exposure drift a different map background or lighting causes behind
# them, and this check would make matching needlessly brittle for those. Opt a
# template in here only when it has a KNOWN dimmed/overlaid duplicate elsewhere on
# screen, the same way TEMPLATE_THRESHOLDS above is tuned per-template rather than
# globally.
EXACT_MATCH_TEMPLATES = {
    "assets/templates/game_results_btn.png": 40,
    # The text-only Repeat Stage / Select Portal crops (see REPEAT_STAGE_BTN): small
    # white text matches a dimmed copy just as well as the real button (1.00 at 55%
    # brightness), which would click through whatever is covering it. Measured mean
    # difference: 0.0 on the real buttons, 56-61 on a 55%-dimmed copy.
    "assets/templates/repeat_stage_text.png": 35,
    "assets/templates/select_portal_text.png": 35,
}

# How much of the match threshold to give back as the screen shrinks (see SCALE).
# Downscaling a template genuinely costs confidence - fine detail and edges are
# averaged away by the resize, so a perfect match no longer scores 1.0 - which means a
# fixed threshold silently gets stricter the smaller the screen is. Measured across
# every template in assets/templates, the worst case falls to ~0.93 at 1366x768 and
# ~0.83 at 1280x720, so the relief is scaled to the shortfall rather than guessed:
#     effective = threshold - (1 - SCALE) * SCALE_THRESHOLD_RELIEF
# At SCALE 1.0 this subtracts exactly nothing, so a full-size screen behaves as before.
SCALE_THRESHOLD_RELIEF = 0.15

# The relief above was measured for MILD downscaling (1366x768, 1280x720). Embedded
# play is far more aggressive - SCALE 0.59 - and at that size the relief is nowhere
# near enough, because resizing is not what the game does. Roblox re-renders its text
# at the smaller size with its own hinting and antialiasing; cv2.resize just averages
# pixels. The two never look the same, so text-heavy templates collapse: story_card
# measured 0.632 against a 0.789 requirement while plainly on screen.
#
# The real fix for a template is a same-size capture in assets/templates@WxH (see
# tools/retemplate.py) - those are matched unresized and need no relief at all. This
# is the fallback for the ones not captured yet, sized so a 0.85 default lands near
# 0.60 at the embedded scale: low, but the alternative for those templates is not
# matching at all, ever.
SHRUNK_THRESHOLD_RELIEF = 0.60


def effective_threshold(template_path, base, shrunk=False):
    """
    The threshold to actually require for this template right now.

    Three cases, because how much slack a template needs depends entirely on whether
    it is being resized:

    - An explicit TEMPLATE_THRESHOLDS entry was measured by hand against real
      present/absent readings, and several exist specifically to sit ABOVE a known
      false positive (next_stage vs. select_stage). Those keep the mild relief only -
      relaxing them further would reopen the exact confusion they were added to stop.
    - No same-size variant: the template is being shrunk, so it cannot score normally
      and gets the large relief.
    - A same-size variant exists: it is matched at its captured size, so it needs
      only the mild relief. Not zero: a variant is a crop taken at a predicted
      position, so it can be a pixel or two off even when correct, and these are
      matching fine today at exactly this figure - tightening them to buy strictness
      nobody asked for would risk breaking what already works.

    Never relaxes below 0.5 - past that, matching stops being meaningful and a false
    positive becomes likelier than a real miss.
    """
    override = TEMPLATE_THRESHOLDS.get(template_path)
    if override is not None:
        threshold = override
        relief = SCALE_THRESHOLD_RELIEF
    else:
        threshold = base
        relief = SHRUNK_THRESHOLD_RELIEF if shrunk else SCALE_THRESHOLD_RELIEF

    if SCALE < 1.0 and relief:
        threshold -= (1.0 - SCALE) * relief
    return max(0.5, threshold)

# How many losses in a row before the loop gives up and stops.
# Story/Raids and Portals both repeat the stage after a defeat, so without a cap a
# preset that simply can't win retries forever. In Portals that's not just wasted
# time: each loss costs one of the portal's 3 hearts, and the third destroys the
# portal - so 4 consecutive losses means one portal was burned through completely and
# the next is going the same way, which is the point where it's the preset at fault
# and not variance.
MAX_CONSECUTIVE_DEFEATS = 4

# The limit actually in force for the current run: MAX_CONSECUTIVE_DEFEATS is only the
# default. Set from Settings > Run behavior by the GUI; 0 = never stop on defeats.
STOP_AFTER_DEFEATS = MAX_CONSECUTIVE_DEFEATS


def defeat_limit_reached(consecutive_defeats):
    """True when this many defeats in a row should stop the run (never, if disabled)."""
    return STOP_AFTER_DEFEATS > 0 and consecutive_defeats >= STOP_AFTER_DEFEATS

# --- Anti-idle ---
# Roblox kicks a player who sends no input for ~20 minutes. A long Auto Play match is
# genuinely input-free for exactly that kind of stretch, so a harmless double-click is
# sent if nothing else has touched the game in this long. The point is one confirmed
# to be inert during a match - it must not place a unit, open the Roblox menu, or hit
# any UI, so don't move it without re-verifying in-game.
ANTI_IDLE_SECONDS = 300.0
ANTI_IDLE_X = 841
ANTI_IDLE_Y = 279

# --- Stuck detection ---
# Every poll loop waits for a template that is normally about to appear. When one
# never does - a game update moved a button, an unhandled screen is up, the client
# died - the loop has no way to tell "still loading" from "will wait forever", and
# silently stalls until someone notices. These bound that wait: exceeding one means
# something is wrong, so the run stops with a screenshot saved to DEBUG_DIR rather
# than hanging.
#
# Split because menu waits and match waits differ by orders of magnitude. Navigation
# resolves in seconds. A match is minutes - and Infinite has no end condition at all,
# so it gets its own much longer bound; a single global value would have to exceed the
# longest Infinite run and would then be far too slack to ever catch a stuck menu.
STUCK_TIMEOUT_MENU = 180.0        # navigation/menu waits
STUCK_TIMEOUT_MATCH = 900.0       # a normal match (longest observed ~7 min)
STUCK_TIMEOUT_MATCH_INFINITE = 10800.0  # Infinite acts: 1h+ is normal, so 3h

# How long stage_player.wait_for_start_game() will keep waiting when the stage panel
# is up but was never seen to disappear - i.e. it cannot tell a genuinely-running
# match from the previous match's UI still on screen after a restart.
#
# Must comfortably exceed a real map load, because that is the case being protected:
# a portal re-select shows the OLD stage panel for a few seconds, and concluding
# "already running" from it skips the wait for the real Start Game button and leaves
# the match unstarted. Measured cold-entry loads here run ~24s, so this sits well past
# that - it only ever costs time in the genuine resume-into-a-live-match case, where
# waiting a little is harmless.
START_GAME_LINGER_GRACE = 45.0

# Set by modules/polling.py when it halts a run for a reason that isn't the user
# pressing Stop - holds the short phase name to show ("STUCK", "ROBLOX CLOSED"), or
# None for a normal stop. Read and cleared by the GUI.
STUCK_DETECTED = None

# Saves a screenshot of every traitless hover-check into DEBUG_DIR, named with the
# slot and the confidence it read. Diagnostic for the case where the check runs and
# reports a confidence but never actually sees the tooltip - a stable, never-varying
# confidence means the tooltip isn't in the captured frame at all, and the only way to
# tell whether it failed to render, rendered somewhere unexpected, or was simply
# restyled by a game update is to look at what was on screen at that moment.
# Costs one PNG write per reward slot checked; turn off once traitless works.
# Turned off now that traitless is confirmed working in-game (verified 2026-09-05:
# a traitless portal was detected and skipped, the next clean one picked). Flip back
# to True to capture a PNG of every hover-check if it ever misbehaves again.
DEBUG_TRAITLESS = False

# Where stuck-detection screenshots and other post-mortem artifacts are written.
DEBUG_DIR = "debug"


def match_stuck_timeout(act_key=None):
    """
    The stuck limit for waiting out a match. Infinite acts have no end condition and
    routinely run past an hour, so they get their own far longer bound - a single
    value would have to exceed the longest Infinite run and would then be far too
    slack to catch anything stuck in any other mode.
    """
    if act_key == "infinite":
        return STUCK_TIMEOUT_MATCH_INFINITE
    return STUCK_TIMEOUT_MATCH

# Every coordinate and template PNG in this file was captured with Roblox's client
# area filling exactly this much of the screen, starting at the screen's top-left.
# capture_screen() (vision.py) grabs the whole primary monitor from (0,0), so every
# hardcoded coordinate here is implicitly relative to that same origin - which only
# lines up with what's actually on screen if Roblox's window is pinned to exactly
# this size at (0,0). input_controller.pin_roblox_window() does that pinning (called
# from focus_roblox_window()), which is what lets this whole file work unmodified on
# a different machine's monitor instead of only by accident on this one.
#
# Changed from 1920x1080 to 1600x900 on 2026-09-12, when every template in
# assets/templates/ was recaptured native at 1600x900 (a deliberate choice - that is
# also DOCK_GAME_WIDTH/HEIGHT below, the size the bot actually docks Roblox to on
# every real run). Every hardcoded coordinate in this file was rescaled by the same
# ratio (x1600/1920, unrelated to config.SCALE, done once by hand at the same time)
# to keep meaning the same relative point in the frame.
#
# The effect of REFERENCE now equalling DOCK_GAME_WIDTH/HEIGHT: SCALE (below) is 1.0
# on every normal run, since the client is always pinned to exactly this size before
# anything is matched or clicked - see set_client_rect(). Nothing shrinks any more,
# and vision.py's assets/templates@WxH/ same-size-variant mechanism sits unused
# unless a monitor is too small to fit this and DOCK_GAME_MIN_WIDTH's fallback
# kicks in (see the DOCK_GAME_WIDTH block) - that is the one case SCALE still moves.
REFERENCE_WIDTH = 1600
REFERENCE_HEIGHT = 900

# --- Resolution scaling ---
# Ratio of Roblox's actual pinned client width to REFERENCE_WIDTH, set by
# input_controller.pin_roblox_window() once it knows what it could actually achieve.
# 1.0 on any monitor with room for the full reference size, which is the common case
# and leaves every number below meaning exactly what it says.
#
# On a smaller screen (a laptop) the client physically cannot be 1920x1080, so it gets
# pinned to the largest 16:9 box that fits and this drops below 1.0. Everything in this
# codebase - every coordinate here, every preset on disk, every match reported by
# vision.py - stays in REFERENCE space regardless. The conversion happens at exactly two
# boundaries: vision.find_template() divides its results back down into reference space,
# and input_controller's mouse helpers multiply back up to real pixels before moving.
# Keeping the middle of the program in one coordinate system is what makes this safe to
# retrofit: no navigation logic, no config value and no recorded preset has to change,
# and presets stay portable between machines with different screens.
SCALE = 1.0

# How much bigger Roblox is drawing its own UI than the templates were captured at, as
# a multiplier on SCALE. 1.0 means the two machines agree and this whole mechanism is
# inert, which is the normal case.
#
# SCALE alone assumes that a client of a given pixel size always renders the same UI at
# the same size. That held across every machine tested until a laptop rendered the exact
# same 1136x639 client with every UI element 1.10x larger - same artwork, same English
# text, same 16:9 viewport, just bigger. Roblox picks its UI scale from more than the
# viewport (Windows' Accessibility text size is the usual culprit, and it is a separate
# setting from Display scaling), so a machine can be pinned perfectly and still draw a
# UI no template matches.
#
# Kept apart from SCALE rather than folded into it because they are measured differently
# and fail differently: SCALE is known exactly from the window rect, while this can only
# be observed from a frame. Multiplying them together at the point of use keeps the
# window geometry honest while still letting matching and coordinates follow the UI.
# Set by health.measure_ui_scale() via set_ui_scale(); see there for how it is derived.
UI_SCALE = 1.0

# How much longer to wait for Roblox to notice the mouse on this PC, as a multiplier on
# click_at()'s hover/press timings. 1.0 on a normal PC. Set at startup from
# health.measure_machine_speed() (an old laptop measured slow enough that fixed timings
# let clicks fall between Roblox's frames), or forced by Settings > Runs > This PC.
SLOWNESS = 1.0

# Coarse-to-fine template matching (vision.match_best). False = the old full-frame match
# everywhere - identical results, several times slower. Kept as a switch for diagnosis.
FAST_MATCHING = True

# Where Roblox's client area actually sits on screen, and how big it actually is.
# Set by input_controller.pin_roblox_window() from the real window, never assumed.
#
# Everything is measured relative to this rather than to the desktop, because the
# window cannot be relied on to be at (0, 0). Windows refuses to put a title bar
# fully off the top of the screen, so asking for a client at (0, 0) on a windowed
# Roblox gets silently nudged down - observed here as a client at (0, 23) sized
# 1920x1009 when 1920x1080 at (0, 0) was requested. Absolute desktop coordinates are
# wrong by that offset, and a screenshot of the whole desktop is wrong by that much
# plus the vertical squash. Capturing and clicking relative to the client makes the
# window's position and its chrome irrelevant.
CLIENT_ORIGIN = (0, 0)
CLIENT_SIZE = None  # (w, h), or None to fall back to the whole primary monitor


# --- Docked layout ---
# The GUI reserves an empty slot in its own layout (sidebar | game slot | controls,
# with the status bar underneath) and the borderless Roblox window is positioned
# exactly over that slot, kept above this window in z-order. The result looks like
# the game is embedded inside the macro - the InformaalFrog-style reference layout -
# without the game ever actually becoming a child window.
#
# That distinction matters: genuinely embedding it (SetParent, making Roblox a
# WS_CHILD of a container frame) was prototyped and tested live, and Roblox's
# anti-cheat kills the client within a fraction of a second of the reparent, twice,
# reproducibly. Stripping the window's OWN chrome was then tested in isolation and
# is NOT detected - it survives indefinitely. So: borderless + positioned over a
# hole, never reparented.
#
# Borderless is also what makes the geometry exact. With no chrome, the client rect
# IS the window rect, so asking for a window at (x, y) sized WxH gives a client at
# exactly (x, y) sized exactly WxH - no title-bar nudge, no border padding, no
# per-machine delta. Same request, same client geometry, every monitor - which is
# what makes one set of coordinates portable instead of per-screen recalibration.
#
# 16:9 is preserved throughout so the uniform SCALE below stays valid. It is
# non-negotiable even though template matching itself tested flat down to 720x405:
# every template was captured on a 16:9 client, and a non-16:9 client makes Roblox
# re-lay-out its UI rather than uniformly scale it (bottom-anchored UI moves relative
# to top-anchored UI), which no single SCALE can correct for.
#
# --- Why 1600x900 ---
# The binding constraint is the soul-count tooltip ("95x / 150x Owned"), which the
# farming loop has to READ rather than merely recognise. Simulating that text at each
# candidate width against the digit reader: 1472 fails (its '5' scores 0.638 against a
# 0.70 bar), 1536 scrapes through at 0.703, 1568 fails outright at 0.008 because the
# glyphs merge at that resample ratio, and 1600 clears it at 0.850 - the widest margin
# of anything tried, and non-fragile in a way the sizes just below it are not. Since
# REFERENCE_WIDTH is now 1600 too (see above), that text is captured and read at this
# same size natively - no shrink is layered on top any more, so this margin can only
# be better than the figure above, which was measured WITH one.
#
# The cost is vertical: a 900-tall game leaves ~148px of a 1080 screen for the strip
# under it, which is why the controls moved below the game instead of beside it.
#
# --- What this size means for template matching ---
# 1600 is both REFERENCE_WIDTH and DOCK_GAME_WIDTH, so on every normal run SCALE is
# 1.0: assets/templates/ is matched exactly as captured, with no resize step at all.
# That sidesteps the whole reason assets/templates@WxH/ same-size variants exist in
# the first place (Roblox re-renders small text with its own hinting rather than
# drawing a shrunken copy of the big glyphs, so a shrunk template never lines up with
# it - see vision._load_template()'s docstring) - there is simply nothing to shrink
# from any more for the size this bot actually runs at.
#
# That mechanism still matters for the one case where the client ISN'T this size: a
# monitor too small to fit 1600x900, where dock_game_size() falls back towards
# DOCK_GAME_MIN_WIDTH (1136, see below). assets/templates@1136x639/ already holds a
# full set of real captures taken at exactly that size for that reason, and nothing
# about them depends on what REFERENCE_WIDTH is defined as - only on their own pixel
# content, so that fallback keeps working unchanged.
#
# --- The floor ---
# 1136x639 is the SMALLEST 16:9 size that works, and it is a hard floor set by Roblox
# itself, not a preference. Probed directly against the live client: any request below
# 816x638 of client area is silently clamped up to 816x638 (asking for 960x540 gives
# 960x638; asking for 640x360 gives 816x638). Since the height can't go below 638 and
# 16:9 has to hold, the narrowest legal panel is 638*16/9 = 1134, and 1136x639 was
# confirmed to be honoured exactly.
DOCK_GAME_WIDTH = 1600
DOCK_GAME_HEIGHT = 900
# The sidebar and controls column flanking the game inside the docked window. The
# earlier "panel needs 600px" measurement counted the sidebar INSIDE that width, so
# the controls column alone only ever needed ~350; these keep margin over that while
# staying tight, since every px here is a px of screen the window doesn't give back.
# 1920 - 1600 game = 320 of width that has to go somewhere. Spent on the sidebar
# rather than left as an empty margin: at 140 the leftover sat beside the game as a
# dead strip, and because the game slot is painted in the transparent key colour, dead
# strip next to it reads as a hole through the panel onto the desktop.
DOCK_SIDEBAR_WIDTH = 300

# The controls don't sit BESIDE the game (that cost 370px of width the game needed
# to reach 1600 - width being the dimension the soul-count tooltip depends on), and
# as of 2026-09 they don't sit in a strip UNDER it either - they moved into the
# sidebar column instead (see gui.BotGUI._build_content_area()), which was already
# full window height and had unused space below its nav buttons. The strip under the
# game is now the log/status area alone, full width.
#
# DOCK_CONTENT_WIDTH stays at 0 (nothing beside the game) for the same reason it was
# already 0 before this move - it was never about the strip's split, only about
# whether anything sits to the SIDE of the game eating into its width, which is
# still no. DOCK_CONTROLS_WIDTH is now read only by _size_window_for_game() to keep
# an internal column split summing correctly - see the comment there - not to size
# anything a person actually sees, since nothing is rendered at that split any more.
DOCK_CONTENT_WIDTH = 0          # nothing beside the game any more
DOCK_CONTROLS_WIDTH = 640       # legacy split point; see _size_window_for_game()
# 140 rather than 150: the rows carry ~18px of their own padding, and at 150 the panel
# came to fractionally more than a 1080 screen, which clipped whatever sat at the very
# bottom of the sidebar.
DOCK_STRIP_HEIGHT = 140         # height of the controls+log strip beneath the game


def panel_size(game_w, game_h):
    """
    The window size needed to hold a game_w x game_h game, as (width, height).

    Every caller that needs this number goes through here. It used to be written out
    by hand in three places - the window sizer, the does-it-fit check, and the game
    size chooser - and they disagreed: two of them carried a +24 and +60 padding for
    window borders that stopped existing when the panel went fullscreen. The check
    then computed 1924 for a layout that really needed 1900, decided it would not fit
    on a 1920 screen, and silently dropped to un-embedded mode with the game pinned
    off the edge. One definition means the three can never disagree again.
    """
    return DOCK_SIDEBAR_WIDTH + game_w, game_h + DOCK_STRIP_HEIGHT + 30
# Floor for the 16:9 shrink in input_controller.dock_game_size(). This is the hard
# floor described above - Roblox won't render a client shorter than 638px, so there is
# no smaller legal 16:9 size to fall back to. On a screen too small to fit even this
# plus the chrome, the window is simply bigger than the screen and has to be moved by
# hand; shrinking further would break the aspect ratio and take every template with it.
#
# Note this is well below DOCK_GAME_WIDTH, so a small monitor really can land here -
# and at 1136 wide (SCALE 1136/1600 = 0.71) the reference templates DO need a
# same-size variant set to keep matching. See the DOCK_GAME_WIDTH block.
DOCK_GAME_MIN_WIDTH = 1136


# Set True/False to decide by hand; None means "work it out" via
# _embedded_templates_ready().
#
# Forced ON: the embedded layout is the point of this app, so it stays on.
#
# At the current DOCK_GAME_WIDTH this override costs nothing at all: DOCK_GAME_WIDTH/
# HEIGHT now EQUALS REFERENCE_WIDTH/HEIGHT, so a normal run docks to exactly the size
# assets/templates/ was captured at - SCALE is 1.0, nothing is shrunk, and
# _embedded_templates_ready() returns True immediately without needing a same-size
# variant folder at all (see below). The gate only does real work on the smaller sizes
# a cramped monitor can fall back to (DOCK_GAME_MIN_WIDTH), where a shrink really does
# happen and text-bearing templates need a same-size capture to survive it.
#
# It stays forced rather than going back to None because None makes the gate
# all-or-nothing in the wrong direction: adding ONE new template (a new map, a new
# portal) leaves it briefly without a variant, which would flip embedding off and
# change the client size out from under every coordinate calibrated at the docked
# size. One template on the shrink path is a small, local problem; the whole layout
# silently switching is not.
DOCK_FORCE = True

def _embedded_templates_ready():
    """
    True once every template can be matched at the embedded (docked) size.

    This gate exists because docking to a size the templates can't be shrunk to is
    exactly the failure that broke everything before: the game shrinks, vision.py falls
    back to shrinking reference-resolution templates, and every text-bearing one stops
    matching - the bot then sits in the lobby recognising nothing.

    Trivially True whenever DOCK_GAME_WIDTH/HEIGHT equals REFERENCE_WIDTH/HEIGHT - the
    current, common case. There is nothing to shrink from in that case (SCALE is 1.0
    when docked), so no same-size variant folder is needed, and requiring one would be
    asking assets/templates@1600x900/ to exist and be a byte-for-byte match of
    assets/templates/ for no reason - which is exactly the confusion a bogus copy of
    that folder caused once already (see vision._variant_is_native()'s docstring).

    Below that size (a monitor too small for DOCK_GAME_WIDTH, falling back towards
    DOCK_GAME_MIN_WIDTH) a real shrink does happen, and this checks for a REAL variant
    at that smaller size - not just a file at the right path. A file existing there was
    the entire test once, and copying assets/templates/ wholesale into a same-named
    folder passed it completely while providing nothing: every "variant" was reference-
    resolution artwork under a new name, too big to ever match at 1:1, and the gate
    reported a full set anyway. Checking the size (via vision._variant_is_native())
    closes that off - a variant only counts if it is dimensioned like a capture taken
    at that client.

    Produce the variants with tools/retemplate.py.
    """
    if (DOCK_GAME_WIDTH, DOCK_GAME_HEIGHT) == (REFERENCE_WIDTH, REFERENCE_HEIGHT):
        return True

    import glob as _glob
    import os as _os
    base = [p.replace(_os.sep, "/") for p in _glob.glob("assets/templates/**/*.png", recursive=True)]
    if not base:
        return False  # wrong working directory - assume not ready rather than guess

    # Imported here rather than at module scope: vision imports config, so a top-level
    # import would be circular, and this is called once at startup.
    import vision as _vision

    # The scale the DOCKED client will run at, passed explicitly: this is called while
    # config is still being imported, so the live SCALE is 1.0 and would make the
    # size check pass unconditionally.
    dock_scale = float(DOCK_GAME_WIDTH) / float(REFERENCE_WIDTH)

    variant_dir = f"assets/templates@{DOCK_GAME_WIDTH}x{DOCK_GAME_HEIGHT}"
    for path in base:
        rel = _os.path.relpath(path, "assets/templates")
        variant = _os.path.join(variant_dir, rel)
        if not _os.path.exists(variant):
            return False
        if not _vision._variant_is_native(variant.replace(_os.sep, "/"), path, dock_scale):
            return False
    return True

DOCK_ENABLED = DOCK_FORCE if DOCK_FORCE is not None else _embedded_templates_ready()


def set_client_rect(origin, size):
    """
    Records the client area the bot should work against, and derives SCALE from it.
    Returns the scale.
    """
    global CLIENT_ORIGIN, CLIENT_SIZE, SCALE
    CLIENT_ORIGIN = (int(origin[0]), int(origin[1]))
    CLIENT_SIZE = (int(size[0]), int(size[1]))
    SCALE = float(size[0]) / float(REFERENCE_WIDTH)
    return SCALE


def set_scale(client_width):
    """Scale only, for callers that have no rect (the offline tools)."""
    global SCALE
    SCALE = float(client_width) / float(REFERENCE_WIDTH)
    return SCALE


def set_ui_scale(multiplier):
    """Records how much larger than expected Roblox is drawing its UI. See UI_SCALE."""
    global UI_SCALE
    UI_SCALE = float(multiplier)
    return UI_SCALE


def render_scale():
    """
    The size reference artwork actually appears at on screen: SCALE x UI_SCALE.

    Every place that converts between reference space and pixels goes through this
    rather than reading SCALE directly, so that a UI scale discovered at runtime
    reaches template sizes and click coordinates together. Splitting them - fixing
    matching but not coordinates - would be worse than not adapting at all: the bot
    would confidently find a button and then click somewhere it isn't.
    """
    return SCALE * UI_SCALE


def to_screen(x, y):
    """
    Reference-space point -> real desktop pixels, for moving the mouse.

    Adds CLIENT_ORIGIN: vision.capture_screen() crops to exactly the client rect, so
    every coordinate here - including everything vision.find_template() returns -
    is client-relative, and needs the client's actual on-screen position added back
    to become a point pydirectinput can move to. tools/coord_finder.py's own
    calibration points are captured the matching way (client-relative, origin
    already subtracted at capture time - see its own to_reference() call), so this
    is symmetric for hand-measured coordinates too.

    CLIENT_ORIGIN is normally (0, 0) or close to it (see pin_roblox_window() - a
    windowed Roblox can land a few pixels off due to Windows refusing to push its
    title bar off the top of the screen), so this is usually a near no-op - but it
    reflects wherever the client actually is, whatever that turns out to be.
    """
    s = render_scale()
    if s == 1.0:
        return int(round(x)) + CLIENT_ORIGIN[0], int(round(y)) + CLIENT_ORIGIN[1]
    return int(round(x * s)) + CLIENT_ORIGIN[0], int(round(y * s)) + CLIENT_ORIGIN[1]


def to_reference(x, y):
    """
    A point read out of a captured frame -> reference space.
    Frames come from capture_screen(), which grabs the client area, so these are
    already client-relative and only need the scale removed - no origin shift.
    """
    s = render_scale()
    if s == 1.0:
        return int(x), int(y)
    return int(round(x / s)), int(round(y / s))


def fit_reference_size(monitor_width, monitor_height):
    """
    Largest 16:9 box no bigger than the reference resolution that fits this monitor.

    Aspect ratio is preserved deliberately: a single uniform scale factor is only
    correct if both axes shrink by the same amount, and template matching would fail
    outright against a non-uniformly squashed image. On a 16:10 or ultrawide monitor
    this simply leaves unused desktop beside or below the client, which costs nothing -
    the client is still pinned at (0, 0) and everything is measured from there.
    """
    width = min(REFERENCE_WIDTH, monitor_width)
    height = int(round(width * REFERENCE_HEIGHT / REFERENCE_WIDTH))
    if height > monitor_height:
        height = monitor_height
        width = int(round(height * REFERENCE_WIDTH / REFERENCE_HEIGHT))
    return width, height

# Template Paths
PLAY_BTN = "assets/templates/play_btn.png"
PLAY_SMALL_BTN = "assets/templates/play_small.png"
STORY_CARD = "assets/templates/story_card.png"
SELECT_STAGE_BTN = "assets/templates/select_stage_btn.png"
START_GAME_BTN = "assets/templates/start_game.png"
# Just the "Repeat Stage" TEXT, not the whole button. The button's width changes with
# how many buttons share its row: Portals' defeat screen fits four (Repeat Stage, Select
# Portal, View Party, leave), so it is narrower there, and the old whole-button crop
# (repeat_stage.png) scored 0.588 against it - which ended an 8-hour run on a single
# defeat (2026-09-14 02:05). The text is drawn the same size either way: 1.00 on that
# defeat screen, 1.00 on the old wide button, <= 0.69 on every other button.
REPEAT_STAGE_BTN = "assets/templates/repeat_stage_text.png"
NEXT_STAGE_BTN = "assets/templates/next_stage.png"
AUTOPLAY_ON_BTN = "assets/templates/autoplay_on.png"
AUTOPLAY_OFF_BTN = "assets/templates/autoplay_off.png"
RECONNECT_BTN = "assets/templates/reconnect_btn.png"
CLICK_ANYWHERE_TEXT = "assets/templates/click_anywhere_to_continue.png"

# Recovery-only button: shows up when the Victory/Defeat/reward popup gets closed
# before it's been read (e.g. a stray click lands on its own close button), leaving
# the match-end HUD frozen with no Victory/Defeat/reward text anywhere on screen for
# any wait to find - every gamemode's match-end wait would otherwise stall until its
# stuck timeout. Checked every poll tick (see modules/polling.poll_until()), the same
# way "Click anywhere to continue" and a disconnect popup are - it can turn up while
# waiting for anything, not just a match result.
GAME_RESULTS_BTN = "assets/templates/game_results_btn.png"

# Recovery-only button, same idea as GAME_RESULTS_BTN but for a different stuck
# screen: clicking Play resumes an existing party directly instead of showing the
# gamemode cards, if one was left sitting around from a previous manual session in
# a DIFFERENT gamemode (e.g. manually entered Raids, backed out without disbanding,
# then started the Story loop - Play lands back on that Raid party screen, which has
# no Story card for click_story_card() to find). Shared between Story and Raids -
# confirmed 1.000 confidence against both party screens - see
# modules/gamemode_select._recover_from_leftover_party().
DISBAND_BTN = "assets/templates/disband_btn.png"

VICTORY_TEXT = "assets/templates/victory.png"
DEFEAT_TEXT = "assets/templates/defeat.png"

# Max times to click the "Click anywhere to continue" prompt before giving up on it.
CLICK_ANYWHERE_MAX_CLICKS = 12
RAID_CARD = "assets/templates/raid_card.png"
CHALLENGE_CARD = "assets/templates/challenge_card.png"
CHANGE_GAMEMODE_BTN = "assets/templates/change_gamemode.png"

# Same button, its own crop - used by the run queue when return_to_lobby() finds
# itself stuck on a specific gamemode's own screen (reached via Play Small) instead
# of the general mode-card chooser. Clicking it lands on the same screen clicking
# Play does (the cards), which is why click_play() also treats those cards already
# being up as "nothing left to click" - see modules/lobby.py and
# modules/gamemode_select.click_play().
CHANGE_GAMEMODE_QUEUE_BTN = "assets/templates/change_gamemode_queue.png"
AVAILABLE_IN_TEXT = "assets/templates/available_in.png"
ITEMS_SMALL_BTN = "assets/templates/items_small.png"

# A generic "X" close button on a post-match summary/result card. Used by the run
# queue when moving from one mode's finished match into the next mode's own entry
# point - see modules/lobby.return_to_lobby(). Not needed by any single-mode run
# loop; those already have their own specific way off the result screen.
CLOSE_BTN = "assets/templates/close.png"

# A generic "Back" button - shows up (sometimes more than once in a row) leaving a
# Challenges pass that ended without playing anything (every selected slot was on
# cooldown - "Available in..."), on the way back out to either the raw lobby or the
# next queue step's own entry point. Same run-queue-only scope as CLOSE_BTN above.
BACK_BTN = "assets/templates/back.png"

# "View Party" - one of the buttons on a challenge's own victory screen (alongside
# Repeat Stage / Select Portal / Leave, depending on gamemode). Confirmed live: this
# is what actually gets off that screen towards Change Gamemode, no separate Exit or
# Play Small click needed first - see gamemode_select.click_view_party().
VIEW_PARTY_BTN = "assets/templates/view_party.png"

# The transient "Game Started!" banner shown for a couple of seconds right when a
# match begins - see stage_player.press_start_after_macro(). Its own on-screen
# window is short, so it is only trusted right after a macro finishes playing, not
# polled continuously.
GAME_STARTED_TEXT = "assets/templates/game_started.png"

# Captured WHILE the wave counter reads exactly "0" - the "0" glyph is baked into
# this crop, so once a wave is actually under way the live counter no longer looks
# like this at all (a different digit is drawn, not this one redrawn smaller). That
# makes plain template matching enough to answer "has a wave started?" without
# reading the number at all - see stage_player.press_start_after_macro().
WAVE_ZERO_TEXT = "assets/templates/waves.png"

# Gamemode categories. Only "story", "raids" and "challenges" are actually wired up
# right now - the rest are listed so they're visible in the GUI, but starting the loop
# on one of them gives a clear "not implemented yet" error instead of silently doing nothing.
GAMEMODES = {
    "story":       {"label": "Story",       "enabled": True},
    "raids":       {"label": "Raids",       "enabled": True},
    "challenges":  {"label": "Challenges",  "enabled": True},
    "expeditions": {"label": "Expeditions", "enabled": True},
    "portals":     {"label": "Portals",     "enabled": True},
    "others":      {"label": "Others",      "enabled": False},
}

# How often to click Reconnect on the disconnect popup while waiting to get back in.
RECONNECT_POLL_INTERVAL = 10.0

# Reserved preset name that triggers the game's built-in Auto Play toggle instead of a
# recorded timeline. Always shown in the preset dropdown, never stored as a preset file.
AUTO_PLAY_PRESET_NAME = "Auto Play"

# Fixed Coordinates
# Start Party (Story) and Start (Raids) turned out to be the exact same button
# graphic - confirmed by testing config.START_PARTY_BTN against both screens at
# 1.000 confidence each - just rendered at whatever position that screen's party
# panel happens to land it, which varies with how much content (rewards, party
# slots) sits above it. One shared template covers both; see
# modules/match_start.py's click_start_party()/click_raid_start().
START_PARTY_BTN = "assets/templates/start_party_btn.png"

# Where the Start button actually is, measured with tools/coord_finder.py against the
# real embedded geometry. The template above still decides WHETHER we're on a party
# screen, but this decides WHERE to click, because the match's centre was landing
# just under the button and doing nothing: a same-size variant is a crop taken at a
# predicted position, and if that crop sits a few pixels low then every match made
# from it is low by the same few pixels, forever. A measured point has no such drift.
#
# Story, Raids and Challenges all put the button here once a mode is chosen.
START_BTN_X = 941
START_BTN_Y = 608

# How far the live match may sit from the point above before the match wins instead.
# The point covers the normal case; a party screen carrying unusual reward/party
# content above the button genuinely moves it, and that is a real relocation rather
# than the few-pixel crop drift this is here to cancel out.
START_BTN_MAX_DRIFT = 75

# Raids' OWN Start point - a Raid party holds 6 players (3 rows) where Story/
# Portals/Challenges hold 4 (2 rows), which pushes Start noticeably further down
# than START_BTN_Y above: confirmed live, the button sits ~82 reference-px lower
# there, just past START_BTN_MAX_DRIFT - right on the edge of the "has this moved"
# check, which is what made it land low inconsistently rather than reliably either
# way. Not hand-measured against the embedded geometry like START_BTN_X/Y - there
# was no live game to measure against, so this was extracted from a single
# reference screenshot (Images_For_Claude/Raids/Start_Raid.png, a raw screen
# capture, not a template_capture.py grab) by template-matching START_PARTY_BTN
# against it and scaling the match centre by that screenshot's own size to
# REFERENCE_WIDTH/HEIGHT. Re-measure with tools/coord_finder.py against the real
# embedded window once that's possible - this is a best-effort stand-in until then.
RAID_START_BTN_X = 950
RAID_START_BTN_Y = 690

# Difficulty Selection (Story stages only). Found by vision rather than a fixed
# point - see modules/gamemode_select.click_difficulty(). Template-based clicks don't
# care what resolution or aspect ratio the client is running at, unlike a coordinate
# measured once against one screen.
NORMAL_DIFFICULTY_TEMPLATE = "assets/templates/story/difficulty_normal.png"
HARD_DIFFICULTY_TEMPLATE = "assets/templates/story/difficulty_hard.png"

# Act Selection (Story stages). Each act tile is found by matching its number badge
# rather than clicking a fixed point - see modules/gamemode_select.click_act(). The
# templates are cropped to the number only, above the row of stars, so the same
# template matches the tile whether it has 0, 1, 2 or 3 stars earned.
ACT_TEMPLATES = {
    "act_1":    "assets/templates/story/act_1.png",
    "act_2":    "assets/templates/story/act_2.png",
    "act_3":    "assets/templates/story/act_3.png",
    "act_4":    "assets/templates/story/act_4.png",
    "act_5":    "assets/templates/story/act_5.png",
    "infinite": "assets/templates/story/act_infinite.png",
    "master":   "assets/templates/story/act_master.png",
}

# Raids - same vision-driven approach as Story: the raid card is found by its own
# artwork (see modules/gamemode_select.click_raid()). A second raid means the
# carousel can now scroll past one page, so click_raid() got the same
# scroll-search fallback click_map() already had for Story's maps.
RAIDS = {
    "spirit_city": {"label": "Spirit City", "enabled": True, "template": "assets/templates/raids/map_spirit_city.png"},
    "snowy_castle": {"label": "Snowy Castle", "enabled": True, "template": "assets/templates/raids/map_snowy_castle.png"},
}

# Raid difficulty tiles (1/2/3) - same number-badge matching as Story's acts, see
# modules/gamemode_select.click_raid_difficulty(). difficulty_1.png was cropped from
# its *selected* (red) appearance rather than unselected (grey) like 2 and 3 - unlike
# Story's acts, testing showed it happened to generalize to both states far better
# that way (0.835-1.000 present either way vs 0.486 worst false - see the threshold
# override below), which matters here because a raid's difficulty 1 starts
# pre-selected on a fresh entry the same way Story's Act 1 does.
RAID_DIFFICULTY_TEMPLATES = {
    "1": "assets/templates/raids/difficulty_1.png",
    "2": "assets/templates/raids/difficulty_2.png",
    "3": "assets/templates/raids/difficulty_3.png",
}

# Challenges
# End-of-match "Exit" button (victory screen) - used to advance to the next challenge
# instead of repeating the same one.
CHALLENGE_EXIT_X = 1296
CHALLENGE_EXIT_Y = 166

# "Back" button shown on an already-completed ("Available in...") challenge's info screen
CHALLENGE_BACK_X = 606
CHALLENGE_BACK_Y = 696

# Challenges' own Start button (distinct from Story's Start Party and Raids' Start).
# Used on a cold entry - the first challenge of a run, reached straight from the lobby.
CHALLENGE_START_X = 941
CHALLENGE_START_Y = 608

# The same button sits ~43px higher (~36px at this resolution) once you've finished a
# challenge and picked the next stage, because that screen is laid out differently
# from the first-entry one. Measured in-game. Clicking the cold-entry point there
# lands below the button and the match never starts, which shows up as an endless
# wait for Start Game rather than as an error - nothing reports a click that hit
# nothing.
CHALLENGE_START_AFTER_MATCH_X = 1000
CHALLENGE_START_AFTER_MATCH_Y = 588

# When looping Challenges and an entire pass finds nothing playable (everything on
# cooldown), how long to wait before trying the whole sequence again. Regular
# Challenges refresh individually every ~10 minutes, so this doesn't need to be long -
# it just avoids hammering through Back-clicks nonstop while nothing has changed.
CHALLENGE_LOOP_WAIT_SECONDS = 60.0

CHALLENGE_CATEGORIES = {
    "regular": {"label": "Regular Challenge", "x": 384, "y": 257},
    "daily":   {"label": "Daily Challenge",   "x": 384, "y": 394},
    "weekly":  {"label": "Weekly Challenge",  "x": 384, "y": 534},
}

CHALLENGE_SLOTS = {
    "regular_1": {"label": "Regular Challenge 1", "category": "regular", "x": 663, "y": 314},
    "regular_2": {"label": "Regular Challenge 2", "category": "regular", "x": 643, "y": 484},
    "regular_3": {"label": "Regular Challenge 3", "category": "regular", "x": 643, "y": 649},
    "daily":     {"label": "Daily Challenge",     "category": "daily",   "x": 663, "y": 314},
    "weekly":    {"label": "Weekly Challenge",    "category": "weekly",  "x": 663, "y": 314},
}

# --- Portals ---
ITEMS_BTN = "assets/templates/items.png"
PORTALS_TAB_X = 363
PORTALS_TAB_Y = 278
ACTIVATE_PORTAL_BTN = "assets/templates/activate_portal.png"
# Portal's own Start button is config.START_PARTY_BTN - see match_start.click_portal_start().

# Best-guess pause between Activate Portal registering and clicking Start - the screen
# transition needs a moment, otherwise the Start click can land before the button is
# actually there yet. Needs live tuning.
PORTAL_ACTIVATE_TO_START_DELAY = 0.8

# The portal list's search box, and the first result under it.
#
# Portals are picked by TYPING, not by scrolling and recognising cards. Searching
# "Sum" filters the list down to the Summer portals and puts the best one you own at
# the top, so clicking the first result always enters the highest tier available -
# without the bot needing to know anything about tiers, card artwork, or how far down
# the list something sits. It also can't run out: as the reward screen keeps handing
# you portals, the top result just keeps being whatever the best one currently is.
PORTAL_SEARCH_BOX_X = 1028
PORTAL_SEARCH_BOX_Y = 173
PORTAL_FIRST_RESULT_X = 532
PORTAL_FIRST_RESULT_Y = 268

# The post-reward re-entry picker (opened by clicking "Select Portal") uses the same
# search box as the lobby's Items -> Portals screen, but its results list sits shifted
# slightly to the LEFT of where the lobby's does. Clicking the lobby's first-result
# point there was landing just past the first card and hitting the SECOND result
# instead - the wrong portal, every time. This is that screen's own first-result
# point instead.
PORTAL_RESELECT_FIRST_RESULT_X = 396
PORTAL_RESELECT_FIRST_RESULT_Y = 266

# The search box is DOUBLE-clicked before typing so any previous term is selected and
# gets replaced, rather than the new one being appended to it ("SumSum" matches
# nothing). Then a pause to let the list filter before the first result is clicked -
# clicking too early hits whatever was in that slot before the filter applied.
PORTAL_SEARCH_TYPE_DELAY = 0.05   # between keystrokes
PORTAL_SEARCH_FILTER_DELAY = 0.8  # after typing, before clicking the first result

PORTALS = {
    "summer":    {"label": "Summer Portal",         "enabled": True, "search": "Sum"},
    "skyruins":  {"label": "Sky Ruin's Portal",     "enabled": True, "search": "Sky"},
    "sovereign": {"label": "Sovereign Portal",      "enabled": True, "search": "Sov"},
    "lightning": {"label": "Lightning God's Portal","enabled": True, "search": "Lig"},
}

# Fishing
FISHING_BTN = "assets/templates/fishing.png"

# The fishing rank badge that sits bottom-right while a rod is actively fishing. Six
# ranks exist and a player shows exactly one, so these are alternatives: any of them
# matching means "still fishing", none matching means the badge is gone.
#
# What this is FOR matters, because a badge check like this was removed once before
# for good reason. It is a gate on the periodic re-CAST only - never on re-equipping.
# The old removed logic re-clicked the fishing button when it thought the rod was
# unequipped, so a false negative actively un-equipped a working session. Gating a
# cast is the harmless direction: a false negative just skips one click and tries
# again a few seconds later.
#
# The gate exists because the cast is otherwise a blind timer that keeps firing after
# the match has ended, straight into the post-match screens - which is exactly how a
# won Portals match could end up with its Victory/Select Portal screen clicked past
# before the loop ever got to read it. The badge disappears when the match does, so
# it's the signal that says "a match is still live, casting is safe".
# Only rank 1. The game cycles six rank badges and a player shows exactly one of them,
# so the other five were never going to match on this account - they only cost five
# template matches per tick to rule out, every tick. The other five crops are parked in
# _quarantine/ rather than deleted, since a different account would need them back.
FISHING_RANK_TEMPLATES = ["assets/templates/fishing/1.png"]

# Auto Play has no macro of its own to carry a walk in, so fishing there plays this
# separately recorded one first, to get the character near water - recorded the
# normal way (this portal's own "My macro" slot, i.e. presets/portal_{category}_
# {this name}.json - see stage_player.play_preset), just under this reserved name
# instead of one the player picks in the UI. Played once per fresh entry, then cast
# at the Portals page's configured spot (PORTAL_AUTOPLAY_FISH_X/Y_DEFAULT below,
# unless changed) rather than wherever the cursor happens to be - see
# modules.fishing.start_fishing. If it hasn't been recorded for a given portal
# category yet, fishing still goes ahead and casts at that same spot from wherever
# Auto Play already is - see run_portal().
PORTAL_AUTOPLAY_WALK_PRESET = "autoplay_movemet"

# Cast point for fishing, always used now rather than wherever the cursor happens to
# be left (see modules.fishing.start_fishing) - the option under Fishing in the
# Portals page. These are just the pre-filled starting values, measured against the
# bundled PORTAL_AUTOPLAY_WALK_PRESET recording for Summer; the Portals page keeps
# its OWN value per portal category (ui_qt/pages.py's PortalsPage), since Summer and
# Sovereign are different maps with this spot in a different place on screen - a
# single shared value would be right for whichever one it was measured on and
# silently wrong for the other.
PORTAL_AUTOPLAY_FISH_X_DEFAULT = 666
PORTAL_AUTOPLAY_FISH_Y_DEFAULT = 389

# Post-match 3-portal reward choice + traitless-avoidance
REWARD_SELECT_TEXT = "assets/templates/auto_select.png"  # signals the 3-portal reward screen is up
TRAITLESS_TEXT = "assets/templates/traitless.png"
PORTAL_REWARD_SLOTS = [(349, 471), (804, 496), (1202, 493)]

# Moved to between checking each reward slot's trait, so the game genuinely
# registers a mouseover leave/enter transition instead of a stale tooltip from the
# previous slot lingering into the next slot's screenshot (jumping straight between
# two hover targets can skip the leave event entirely). Well below the reward cards.
PORTAL_REWARD_NEUTRAL_POINT = (573, 227)

# Two different buttons in the re-selection flow, despite the similar names/text:
#   PORTAL_SELECT_BTN         "Select Portal" - clicked right after picking a reward,
#                             to reopen the portal picker.
#   PORTAL_CONFIRM_SELECT_BTN "Select" (green) - clicked after the searched-for
#                             portal is chosen in that picker, to confirm it and
#                             restart the match. Reusing PORTAL_SELECT_BTN here too
#                             was the bug: that text never appears a second time, so
#                             the confirm click silently timed out and the portal
#                             search result sat clicked but never confirmed.
# Text-only for the same reason as REPEAT_STAGE_BTN: the old whole-button crop
# (select_portal.png) is the wide victory-screen button and read 0.705 on the narrower
# defeat-screen one. The text: 1.00 on both, <= 0.56 on every other button.
PORTAL_SELECT_BTN = "assets/templates/select_portal_text.png"
PORTAL_CONFIRM_SELECT_BTN = "assets/templates/portal_select.png"

# Neutral point to hover the cursor over the map carousel while scroll-wheeling pages
MAP_CAROUSEL_HOVER_X = 533
MAP_CAROUSEL_HOVER_Y = 333

# Camera Anchor Tuning (adjust by eye in-game if the zoom/pitch overshoots or undershoots)
CAMERA_ZOOM_IN_STEPS = 28    # scroll-up notches; kept just short of forcing first-person (avoids pitch flip)
CAMERA_PITCH_STEPS = 15      # number of right-drag pitch pulses
CAMERA_PITCH_STEP_SIZE = 30  # mickeys per pulse; lower = less risk of overshooting past top-down
CAMERA_ZOOM_OUT_STEPS = 30   # scroll-down notches to reach max camera distance

def _default_acts():
    """Returns a fresh acts dict so each map owns its own copy (not a shared reference)."""
    return {
        "act_1":    {"label": "Act 1",    "enabled": True},
        "act_2":    {"label": "Act 2",    "enabled": True},
        "act_3":    {"label": "Act 3",    "enabled": True},
        "act_4":    {"label": "Act 4",    "enabled": True},
        "act_5":    {"label": "Act 5",    "enabled": True},
        "infinite": {"label": "Infinite", "enabled": True},
        "master":   {"label": "Master",   "enabled": True},
    }

# Each map is identified by the distinctive island artwork on its carousel card
# rather than a page/slot position - see modules/gamemode_select.click_map(). That
# artwork doesn't move if the carousel is reordered or the list scrolls to a
# different starting point, which a fixed slot index would silently get wrong.
MAPS = {
    "school_grounds":    {"label": "School Grounds",    "enabled": True, "template": "assets/templates/story/map_school_grounds.png", "acts": _default_acts()},
    "flower_forest":     {"label": "Flower Forest",     "enabled": True, "template": "assets/templates/story/map_flower_forest.png", "acts": _default_acts()},
    "rose_kingdom":      {"label": "Rose Kingdom",      "enabled": True, "template": "assets/templates/story/map_rose_kingdom.png", "acts": _default_acts()},
    "fairy_king_forest": {"label": "Fairy King Forest", "enabled": True, "template": "assets/templates/story/map_fairy_king_forest.png", "acts": _default_acts()},
    "kings_tomb":        {"label": "King's Tomb",       "enabled": True, "template": "assets/templates/story/map_kings_tomb.png", "acts": _default_acts()},
    "east_town":         {"label": "East Town",         "enabled": True, "template": "assets/templates/story/map_east_town.png", "acts": _default_acts()},
    "crimson_shore":     {"label": "Crimson Shore",     "enabled": True, "template": "assets/templates/story/map_crimson_shore.png", "acts": _default_acts()},
}

# The linear campaign order Auto Next climbs through: Act 1-5 of one map, then Act 1
# of the next map, in MAPS' own dict order (which matches the carousel's left-to-right
# page order). Infinite and Master are deliberately excluded - they're side content
# reached by picking them explicitly, not part of the story's main progression, and
# there's no confirmed evidence of what (if anything) "Next Stage" does after Master.
STORY_AUTO_NEXT_ACTS = ["act_1", "act_2", "act_3", "act_4", "act_5"]


def next_story_stage(map_key, act_key):
    """
    The (map_key, act_key) that comes after this one in the Auto Next chain, or None
    if this was the last act of the last map - Auto Next stops cleanly there rather
    than guessing at Infinite/Master or looping back to the start.
    """
    map_keys = list(MAPS.keys())
    if map_key not in map_keys or act_key not in STORY_AUTO_NEXT_ACTS:
        return None

    act_index = STORY_AUTO_NEXT_ACTS.index(act_key)
    if act_index + 1 < len(STORY_AUTO_NEXT_ACTS):
        return map_key, STORY_AUTO_NEXT_ACTS[act_index + 1]

    map_index = map_keys.index(map_key)
    if map_index + 1 < len(map_keys):
        return map_keys[map_index + 1], STORY_AUTO_NEXT_ACTS[0]

    return None


# --- Expeditions ---
# Play -> Expeditions card -> one of four expedition maps -> Increase Difficulty (0-2
# clicks) -> Select Stage -> Start. In the stage: open the Expedition Map and route
# through the nodes that give the most of the chosen material, anchor the camera,
# place units with a recorded macro (this mode has no Auto Play), then Start Game.
# During the run the bot answers whatever pops up (upgrade cards, encounters,
# checkpoints, Start Game at defense nodes) and extracts at the second checkpoint.
# See modules/expedition.py and modules/expedition_map.py.
EXPEDITION_DIR = "assets/templates/expeditions"
_EXP_MAP_DIR = f"{EXPEDITION_DIR}/Expedition_Map"

EXPEDITIONS_CARD = f"{EXPEDITION_DIR}/Expeditions_Card.png"
EXP_INCREASE_DIFFICULTY = f"{EXPEDITION_DIR}/Increase_Difficulty.png"
EXP_SELECT_STAGE_BTN = f"{EXPEDITION_DIR}/Select_Stage.png"
EXP_MAP_BTN = f"{EXPEDITION_DIR}/Expedition_Map.png"
EXP_CONTINUE_BTN = f"{EXPEDITION_DIR}/Continue_Button.png"
EXP_AFTER_CONTINUE_BTN = f"{EXPEDITION_DIR}/After_Continue.png"
EXP_EXTRACT_BTN = f"{EXPEDITION_DIR}/Extract.png"
EXP_AFTER_EXTRACT_BTN = f"{EXPEDITION_DIR}/After_Extract.png"
EXP_REPEAT_STAGE_BTN = f"{EXPEDITION_DIR}/Repeat_Stage.png"
EXP_SELECT_UPGRADE_TEXT = f"{EXPEDITION_DIR}/Select_An_Upgrade.png"
EXP_ENCOUNTER_SIGNAL = f"{EXPEDITION_DIR}/Encounter_Signal.png"
# The "Lvl" tag every unit card in the hotbar carries, whatever the unit - cropped from
# Full_Units_Bar.png. Found = units are still waiting to be placed. See
# modules/expedition.py (_units_in_bar) for how it is searched and the measured scores.
EXP_UNIT_LVL_TAG = f"{EXPEDITION_DIR}/Unit_Lvl_Tag.png"
EXP_BACK_BTN = f"{_EXP_MAP_DIR}/Back_Button.png"
EXP_CLOSE_BTN = f"{_EXP_MAP_DIR}/Close_Button.png"
# The teal skull under the boss node. Only used to find (and ignore) the boss's own
# reward strip, which carries the same material icons as real nodes.
EXP_BOSS_SKULL = f"{_EXP_MAP_DIR}/Boss_Skull.png"

EXPEDITIONS = {
    "school_grounds": {"label": "School Grounds", "enabled": True, "material": "cursed_timber",
                       "template": f"{EXPEDITION_DIR}/School_Grounds.png"},
    "flower_forest":  {"label": "Flower Forest",  "enabled": True, "material": "lush_dirt",
                       "template": f"{EXPEDITION_DIR}/Flower_Forest.png"},
    "rose_kingdom":   {"label": "Rose Kingdom",   "enabled": True, "material": "aqua_shard",
                       "template": f"{EXPEDITION_DIR}/Rose_Kingdom.png"},
    "east_town":      {"label": "East Town",      "enabled": True, "material": "mechanical_scrap",
                       "template": f"{EXPEDITION_DIR}/East_Town.png"},
}

# "icons" are the small reward icons above map nodes. A node's icon box is tinted by
# rarity (green, pink, teal, blue...), and colour matching is what keeps Fuel Cell and
# Aqua Shard apart (both blue crystals), so Fuel Cell - which appears on every map in
# every tint - carries one crop per tint seen. "card" is the artwork of that material's
# card in the Route Rewards bar, used to find the card before reading its count.
EXPEDITION_MATERIALS = {
    "cursed_timber":    {"label": "Cursed Timber",
                         "icons": [f"{_EXP_MAP_DIR}/Cursed_Timber_Icon.png"],
                         "card": f"{_EXP_MAP_DIR}/Route_Cards/cursed_timber.png"},
    "lush_dirt":        {"label": "Lush Dirt",
                         "icons": [f"{_EXP_MAP_DIR}/Lush_Dirt_Icon.png"],
                         "card": f"{_EXP_MAP_DIR}/Route_Cards/lush_dirt.png"},
    "aqua_shard":       {"label": "Aqua Shard",
                         "icons": [f"{_EXP_MAP_DIR}/Aqua_Shard_Icon.png"],
                         "card": f"{_EXP_MAP_DIR}/Route_Cards/aqua_shard.png"},
    "mechanical_scrap": {"label": "Mechanical Scrap",
                         "icons": [f"{_EXP_MAP_DIR}/Mechanical_Scrap_Icon.png"],
                         "card": f"{_EXP_MAP_DIR}/Route_Cards/mechanical_scrap.png"},
    "fuel_cell":        {"label": "Fuel Cell",
                         "icons": [f"{_EXP_MAP_DIR}/Fuel_Cell_Icon.png",
                                   f"{_EXP_MAP_DIR}/Icon_Variants/Fuel_Cell_green.png",
                                   f"{_EXP_MAP_DIR}/Icon_Variants/Fuel_Cell_pink.png",
                                   f"{_EXP_MAP_DIR}/Icon_Variants/Fuel_Cell_teal.png",
                                   f"{_EXP_MAP_DIR}/Icon_Variants/Fuel_Cell_lime.png"],
                         "card": f"{_EXP_MAP_DIR}/Route_Cards/fuel_cell.png"},
}
EXPEDITION_RANDOM_MATERIAL = "random"

# The zoom of the map each icon crop above was taken from (the map zooms to fit its
# node layout - see modules/expedition_map.py). Measured with the boss skull on each
# source map. A crop is resized by live_map_zoom / its zoom before matching. A new
# icon crop needs its own entry here; anything missing is assumed 1.0.
EXPEDITION_ICON_NATIVE_ZOOM = {
    f"{_EXP_MAP_DIR}/Cursed_Timber_Icon.png": 0.89,
    f"{_EXP_MAP_DIR}/Lush_Dirt_Icon.png": 1.02,
    f"{_EXP_MAP_DIR}/Aqua_Shard_Icon.png": 1.02,
    f"{_EXP_MAP_DIR}/Mechanical_Scrap_Icon.png": 1.02,
    f"{_EXP_MAP_DIR}/Fuel_Cell_Icon.png": 1.02,
    f"{_EXP_MAP_DIR}/Icon_Variants/Fuel_Cell_green.png": 0.89,
    f"{_EXP_MAP_DIR}/Icon_Variants/Fuel_Cell_pink.png": 0.89,
    f"{_EXP_MAP_DIR}/Icon_Variants/Fuel_Cell_teal.png": 0.89,
    f"{_EXP_MAP_DIR}/Icon_Variants/Fuel_Cell_lime.png": 1.02,
}

# The unit macro is shared by all four maps - the payload is the same everywhere - so
# it lives in one preset slot: presets/expedition_payload_<name>.json.
# Portal macros are saved per portal kind: presets/portal_<category>_<name>.json
PORTAL_PRESET_LOCATION = "portal"
EXPEDITION_PRESET_LOCATION = "expedition"
EXPEDITION_PRESET_VARIANT = "payload"

# Continue at the first checkpoint, Extract at this one (the checkpoint after the boss).
EXPEDITION_EXTRACT_AT_CHECKPOINT = 2

# Longest a run may go without ANY recognised popup/button before it counts as stuck.
# Longer than a normal match's limit: a single defense node can run for many minutes.
EXPEDITION_STUCK_TIMEOUT = 1500.0

# Camera zoom-out for Expeditions, in wheel notches (0 = stay zoomed in close, 30 = the
# full zoom-out every other mode uses, CAMERA_ZOOM_OUT_STEPS). This is only the
# DEFAULT: the slider in the Expeditions panel overrides it and is saved in
# settings.json, so it can be changed without rebuilding. Re-record the unit macro
# after changing it - the macro's clicks assume the camera it was recorded with.
EXPEDITION_CAMERA_ZOOM_OUT_STEPS = 13