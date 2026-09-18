# input_controller.py
"""
Mouse/keyboard action wrappers using pydirectinput and ctypes/win32 Windows APIs.
"""
import contextlib
import pydirectinput
import ctypes
import json
import os
import time
import win32api
import win32con
import win32gui
import config
from settings import base_dir as settings_base_dir

pydirectinput.PAUSE = 0.01


@contextlib.contextmanager
def high_res_timer():
    """
    Requests 1ms Windows timer resolution for the duration of the block.

    time.sleep() only wakes a thread at the OS scheduler's own timer tick, which
    defaults to ~15.6ms - so a recorded macro's keyDown/keyUp timing (see
    modules.stage_player.play_preset(), the one place a recording is actually
    replayed live) can overshoot its target by up to that much on every single
    wait it does. A walk holds and releases several keys in a row, each with its
    own wait, so those overshoots don't cancel out - they land at slightly
    different points in each hold, which is what turns into movement that plays
    back a bit longer or a bit shorter than it was recorded, unpredictably.
    Worse on an older/slower machine, where the scheduler has more competing
    work to get through before it wakes this thread again. Bringing the tick
    down to 1ms - the finest grain Windows allows - tightens every sleep() in
    the block to within roughly 1-2ms instead.

    Costs a little extra power/CPU wake-ups system-wide while active, which is
    why this is scoped to just the playback loop rather than the whole run.
    """
    raised = False
    try:
        raised = ctypes.windll.winmm.timeBeginPeriod(1) == 0
    except Exception:
        raised = False
    try:
        yield
    finally:
        if raised:
            try:
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass

# Windows API constants
MOUSEEVENTF_WHEEL = 0x0800

# --- Absolute mouse moves that work on EVERY monitor ------------------------------
# pydirectinput.moveTo() normalises coordinates against the PRIMARY monitor's size
# (GetSystemMetrics(0/1)) and sends MOUSEEVENTF_ABSOLUTE without
# MOUSEEVENTF_VIRTUALDESK, so Windows maps 0..65535 onto the primary screen only.
# With Roblox docked on a second monitor - to the right of the primary, or above it
# with negative coordinates - every click was being squeezed onto the wrong screen.
# pydirectinput.moveRel() goes through moveTo() too, so the hover jitter had the same
# problem. _move_abs() normalises against the whole virtual desktop instead, which is
# what VIRTUALDESK tells SendInput the numbers mean, and then checks where the cursor
# actually landed: the normalisation can round a pixel off on odd desktop sizes, and a
# SetCursorPos correction after the real input event keeps the position exact without
# losing the hardware-style move Roblox needs to register hover.
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def cursor_position():
    """The cursor's real position in virtual-desktop pixels (any monitor)."""
    pt = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def _move_abs(x, y):
    """Moves the cursor to desktop pixel (x, y) on whichever monitor that is."""
    user32 = ctypes.windll.user32
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) or user32.GetSystemMetrics(0)
    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) or user32.GetSystemMetrics(1)
    x, y = int(round(x)), int(round(y))
    vw, vh = max(1, vw), max(1, vh)
    # Windows turns a normalised coordinate back into a pixel by rounding down, so aim
    # at the top of the target pixel's range (ceil) - that lands exactly on it. Landing
    # a pixel off used to be corrected with SetCursorPos, which moves the cursor WITHOUT
    # an input event: Roblox never sees that move, and its idea of what is under the
    # mouse stays stale - the "it hovered the button and the click did nothing until I
    # moved the mouse" symptom. A re-aimed real move is tried first; SetCursorPos is
    # only the last resort, and click_at() follows it with real movement anyway.
    nx = min(65535, max(0, ((x - vx) * 65536 + vw - 1) // vw))
    ny = min(65535, max(0, ((y - vy) * 65536 + vh - 1) // vh))
    for _ in range(2):
        _send_mouse(nx, ny, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)
        lx, ly = cursor_position()
        if (lx, ly) == (x, y):
            return
        nx = min(65535, max(0, nx + (x - lx) * 65536 // vw))
        ny = min(65535, max(0, ny + (y - ly) * 65536 // vh))
    user32.SetCursorPos(x, y)


def _send_mouse(dx, dy, flags):
    extra = ctypes.c_ulong(0)
    event = _INPUT(type=0, u=_INPUTUNION(mi=_MOUSEINPUT(dx, dy, 0, flags, 0, ctypes.pointer(extra))))
    ctypes.windll.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event))


def _rel_move(dx, dy):
    """A relative move: arrives in Roblox as raw mouse movement, like a real mouse."""
    _send_mouse(int(dx), int(dy), MOUSEEVENTF_MOVE)


def move_mouse_to(x, y):
    """Public name for _move_abs(), for modules that position the cursor directly."""
    _move_abs(x, y)


def _nudge():
    """
    Real mouse movement over the target, spread over a couple of frames, then an exact
    re-landing. Roblox only updates what is under the mouse when it receives movement
    AND draws a frame, so the wiggle is paced in frames (longer on a slow PC - see
    config.SLOWNESS), not fired all at once.
    """
    cx, cy = cursor_position()
    frame = 0.02 * config.SLOWNESS
    _rel_move(2, 0)
    time.sleep(frame)
    _rel_move(-2, 0)
    time.sleep(frame)
    _move_abs(cx, cy)

# Timestamp of the last input this module sent to the game. Roblox kicks an idle
# player after ~20 minutes, and "idle" means no input - which the bot genuinely is
# during a long Auto Play match, where it can watch for 10+ minutes without clicking
# anything. Tracking it in one place here (rather than in each poll loop) means every
# path that actually touches the game keeps it fresh automatically, and the anti-idle
# nudge only fires when nothing else has.
_last_input_time = time.time()


def mark_input():
    """Records that input was just sent. Call from anything driving the game that
    doesn't go through this module's own helpers (e.g. play_movement's raw keyDown)."""
    global _last_input_time
    _last_input_time = time.time()


def seconds_since_input():
    """How long since anything was sent to the game."""
    return time.time() - _last_input_time

def _native_scroll(amount):
    """Sends native Windows mouse wheel events (Negative = Down, Positive = Up)."""
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, int(amount), 0)

# The game client's window class. Roblox's own launcher/app shell also carries
# "Roblox" in its title, so a title-only search can hand back the home/browse window
# instead of the running game - and everything downstream then pins, screenshots and
# clicks the wrong window, with no template ever matching. The class name separates
# them reliably: only the actual game client is WINDOWSCLIENT.
ROBLOX_CLIENT_CLASS = "WINDOWSCLIENT"

def _find_roblox_hwnd():
    """
    Returns the visible top-level Roblox GAME window, or None.

    Prefers a window of ROBLOX_CLIENT_CLASS; falls back to a title match only if no
    such window exists, so a future class rename degrades to the old behaviour rather
    than breaking outright.
    """
    by_class, by_title = [], []

    def _collect(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if "roblox" not in win32gui.GetWindowText(hwnd).lower():
            return True
        try:
            if win32gui.GetClassName(hwnd) == ROBLOX_CLIENT_CLASS:
                by_class.append(hwnd)
                return True
        except Exception:
            pass
        by_title.append(hwnd)
        return True

    win32gui.EnumWindows(_collect, None)
    if by_class:
        return by_class[0]
    return by_title[0] if by_title else None

def roblox_is_running():
    """
    True while a Roblox window exists. Public counterpart to _find_roblox_hwnd() for
    the health checks in modules/health.py, which need to distinguish "the client
    lost the server and is showing a Reconnect popup" from "the client is gone".
    """
    return _find_roblox_hwnd() is not None

def pin_roblox_window(width=None, height=None, x=0, y=0):
    """
    Moves and resizes the Roblox window so its CLIENT area - the actual rendered game,
    not the outer window frame - is exactly (width, height) at screen position (x, y).
    Defaults to config.REFERENCE_WIDTH/HEIGHT, the size every coordinate and template
    in config.py was captured against.

    This is what makes this bot's fixed pixel coordinates portable to a different
    monitor: capture_screen() always grabs the whole primary monitor from (0,0), so
    pinning Roblox's client area to (0,0) at the reference size makes that assumption
    true by construction instead of true by accident of one machine's setup.

    Compensates for window chrome (title bar/borders), if any: MoveWindow positions
    the OUTER window rect, so the requested size/position is padded by the delta
    between GetWindowRect (outer) and the client area's actual screen origin/size
    before calling it, ensuring the CLIENT rect - what's actually captured and
    clicked - lands exactly on target. If Roblox is already borderless/maximized,
    that delta is zero and this has no effect.

    Returns the (width, height) of the client area it actually achieved, or None if no
    Roblox window was found / the monitor has no room for the requested size. Callers
    treat None as fatal: every coordinate in config.py is meaningless without the pin,
    so continuing would just click arbitrary places. Returning the size rather than a
    bare True is what will let a future scaling layer notice it got something smaller
    than the reference resolution and scale coordinates to match.
    """
    width = config.REFERENCE_WIDTH if width is None else width
    height = config.REFERENCE_HEIGHT if height is None else height

    hwnd = _find_roblox_hwnd()
    if hwnd is None:
        print("[pin_roblox_window] No Roblox window found.")
        return None

    try:
        # The FULL monitor rect, not the taskbar-excluded "Work" area: capture_screen()
        # grabs the whole physical monitor via mss, which is what every coordinate and
        # template was calibrated against - the "Work" area undershoots that by however
        # much the taskbar takes (~48px tall on this very machine), which would fail
        # this fit-check even on the machine the bot was built on.
        monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        mon_left, mon_top, mon_right, mon_bottom = win32api.GetMonitorInfo(monitor)["Monitor"]
        mon_width, mon_height = mon_right - mon_left, mon_bottom - mon_top

        window_left, window_top, window_right, window_bottom = win32gui.GetWindowRect(hwnd)
        client_left, client_top = win32gui.ClientToScreen(hwnd, (0, 0))
        _, _, client_w, client_h = win32gui.GetClientRect(hwnd)

        # Padding needed so the CLIENT rect - not the outer window - ends up at
        # (x, y) sized (width, height). Zero on a borderless/maximized window.
        pad_left = client_left - window_left
        pad_top = client_top - window_top
        pad_right = window_right - (client_left + client_w)
        pad_bottom = window_bottom - (client_top + client_h)

        # A monitor without room for the full reference size no longer fails outright.
        # Shrinking to the largest 16:9 box that fits and scaling everything by the same
        # ratio is what lets this run on a laptop, or on a friend's machine, without
        # anyone recapturing a single coordinate or template.
        #
        # The fit is deliberately against the RAW monitor, not the monitor minus window
        # chrome. Only the client area has to be on screen - the title bar and borders
        # are free to hang off the edges, and MoveWindow accepts negative coordinates to
        # put them there (a 1920x1080 client on a 1920x1080 monitor really does sit at
        # (-8, -31) with 1936x1119 of outer window). Subtracting chrome here shrinks the
        # client below the size the templates were captured at for no reason, which
        # forces a scale correction that shouldn't exist and makes every template match
        # worse - measurably so: it dropped fishing.png from 0.95 to 0.68.
        if width > mon_width or height > mon_height:
            width, height = config.fit_reference_size(mon_width, mon_height)
            print(f"[pin_roblox_window] This monitor is {mon_width}x{mon_height}, smaller than the "
                  f"{config.REFERENCE_WIDTH}x{config.REFERENCE_HEIGHT} reference. Using {width}x{height} "
                  f"instead and scaling every coordinate and template to match.")

        target_client_x, target_client_y = mon_left + x, mon_top + y
        if (client_left, client_top, client_w, client_h) == (target_client_x, target_client_y, width, height):
            # Already exactly right - e.g. Roblox is running true exclusive fullscreen,
            # which already covers the whole monitor and shouldn't be poked with a
            # MoveWindow it doesn't need. Also keeps this a no-op on the machine it was
            # already calibrated on.
            print("[pin_roblox_window] Already pinned to the target size/position.")
            config.set_client_rect((client_left, client_top), (width, height))
            return (width, height)

        target_x = target_client_x - pad_left
        target_y = target_client_y - pad_top
        target_w = width + pad_left + pad_right
        target_h = height + pad_top + pad_bottom

        win32gui.MoveWindow(hwnd, target_x, target_y, target_w, target_h, True)

        # The scale is derived from what the window ACTUALLY became, never from what was
        # asked for. MoveWindow does not report failure: Windows will clamp a request
        # that doesn't fit, honour a minimum window size, or ignore the call outright on
        # a maximized window - and every one of those leaves a client area that isn't the
        # requested size. Trusting the request in that situation sets a scale describing
        # a window that does not exist, which silently breaks every template match and
        # every click at once, with nothing in the logs to say why.
        actual_left, actual_top = win32gui.ClientToScreen(hwnd, (0, 0))
        _, _, actual_w, actual_h = win32gui.GetClientRect(hwnd)

        if (actual_w, actual_h) != (width, height):
            print(f"[pin_roblox_window] NOTE: asked for a {width}x{height} client area but got "
                  f"{actual_w}x{actual_h}. Using the real size.")

        if (actual_left, actual_top) != (target_client_x, target_client_y):
            # Not a problem any more, just worth recording. Windows won't let a title
            # bar sit fully off the top of the screen, so a windowed Roblox routinely
            # lands a few pixels lower than asked. Captures and clicks are both taken
            # relative to wherever the client actually is (config.CLIENT_ORIGIN, added
            # back on in config.to_screen()), so the offset is accounted for rather
            # than ignored.
            print(f"[pin_roblox_window] Client landed at ({actual_left}, {actual_top}) rather than "
                  f"({target_client_x}, {target_client_y}); working relative to it.")

        # Height is reported rather than judged. A windowed Roblox is confined to the
        # work area, so the client is routinely shorter than 16:9 would imply (1920x1009
        # on a 1080p screen with a taskbar) - and that is fine, because the templates and
        # coordinates were themselves captured that way. What actually breaks things is
        # this height being DIFFERENT from the machine the coordinates were captured on:
        # UI anchored to the bottom of the screen sits higher in a shorter client, while
        # top-anchored UI does not move, so no single offset or scale corrects both.
        # Printed unconditionally so the number is in the log of every machine, which is
        # what makes two machines comparable at a glance.
        expected_h = int(round(actual_w * config.REFERENCE_HEIGHT / config.REFERENCE_WIDTH))
        if abs(actual_h - expected_h) > 4:
            print(f"[pin_roblox_window] Client is {actual_w}x{actual_h}; a 16:9 client of this "
                  f"width would be {actual_w}x{expected_h}, so it is {expected_h - actual_h}px "
                  f"shorter (normal for a windowed game above a taskbar). Bottom-anchored UI "
                  f"sits {expected_h - actual_h}px higher than in a full-height client - which "
                  f"only matters if this differs from the machine the coordinates came from.")

        scale = config.set_client_rect((actual_left, actual_top), (actual_w, actual_h))
        print(f"[pin_roblox_window] Roblox client area is ({actual_left}, {actual_top}) "
              f"sized {actual_w}x{actual_h} (scale {scale:.4f}).")
        return (actual_w, actual_h)
    except Exception as e:
        print(f"[pin_roblox_window] Failed to pin Roblox window: {e}")
        return None

# Truly embedding Roblox inside this app's window (SetParent, making it a WS_CHILD
# of a container frame) was prototyped and tested live: Roblox exits within a
# fraction of a second of the reparent, reproducibly, with no crash dump and no
# Windows Application Error event - consistent with its anti-cheat treating
# reparenting (the classic overlay/cheat-tool technique) as tampering. Stripping the
# window's OWN chrome was then tested in isolation and is NOT detected - it survives
# indefinitely - which is what pin_roblox_borderless() below relies on. The result
# looks the same to the user (game panel with the controls beside it) without ever
# touching the parent/child relationship the anti-cheat objects to.
_borderless_state = {}

# The same rescue information, on disk. _borderless_state is a plain dict in this
# process, so it describes a window only for as long as this process lives - and the
# one case that most needs undoing is the one where it does not: Task Manager, a hard
# crash, a debugger stop. atexit does not run for any of those, so Roblox is left
# borderless, with no title bar to drag it by, still in the always-on-top band, and
# nothing anywhere knows how to put it back. Restarting the macro did not help either,
# because the new process started with an empty dict and restore_roblox_window()
# returned False on its second line.
#
# Writing it out means the NEXT launch can finish the job - see recover_orphaned_pin().
_PIN_STATE_FILE = os.path.join(settings_base_dir(), "roblox_pin_state.json")


def _remember_pin_on_disk(hwnd, style, rect):
    try:
        with open(_PIN_STATE_FILE, "w") as f:
            json.dump({"hwnd": int(hwnd), "style": int(style), "rect": list(rect)}, f)
    except Exception:
        pass  # a rescue note that cannot be written must not break the pin itself


def _forget_pin_on_disk():
    try:
        if os.path.exists(_PIN_STATE_FILE):
            os.remove(_PIN_STATE_FILE)
    except Exception:
        pass


def recover_orphaned_pin():
    """
    Puts back a Roblox window that a previous run left borderless, and returns True
    if it did.

    Called at startup. A leftover state file means the last session pinned Roblox and
    never got to undo it, which from the user's side looks like "Roblox is broken now"
    - chrome-less, unmovable, floating over everything - with no obvious connection to
    the macro that did it, and no way to fix it short of restarting the game.

    The saved hwnd is deliberately NOT required to match. Roblox may well have been
    restarted since, in which case the handle is stale but the window on screen is a
    perfectly normal one that needs nothing done to it - so this checks the actual
    window for the damage (missing caption) rather than trusting the note, and repairs
    only what is really broken.
    """
    if not os.path.exists(_PIN_STATE_FILE):
        return False

    try:
        with open(_PIN_STATE_FILE, "r") as f:
            saved = json.load(f)
    except Exception:
        _forget_pin_on_disk()
        return False

    hwnd = _find_roblox_hwnd()
    if hwnd is None:
        # Nothing to repair now, but the note is kept: Roblox may simply not be open
        # yet, and the window it eventually opens could still be the damaged one.
        return False

    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    if style & win32con.WS_CAPTION:
        _forget_pin_on_disk()  # already normal - a restart, or someone fixed it
        return False

    print("[recover_orphaned_pin] Roblox was left borderless by a previous session "
          "- putting its title bar back.")
    try:
        set_roblox_topmost(False)
        restorable = int(saved.get("style", style)) | (
            win32con.WS_CAPTION | win32con.WS_THICKFRAME | win32con.WS_SYSMENU
            | win32con.WS_MINIMIZEBOX | win32con.WS_MAXIMIZEBOX
        )
        win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, restorable)

        rect = saved.get("rect") or []
        if len(rect) == 4 and rect[0] > -30000 and rect[1] > -30000:
            left, top, right, bottom = rect
            width, height = max(640, right - left), max(480, bottom - top)
        else:
            # No usable rect saved. Any sane visible size beats leaving it as-is.
            left, top, width, height = 160, 90, 1600, 900

        win32gui.SetWindowPos(
            hwnd, 0, left, top, width, height,
            win32con.SWP_FRAMECHANGED | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE,
        )
        return True
    except Exception as e:
        print(f"[recover_orphaned_pin] Could not restore it: {e}")
        return False
    finally:
        _forget_pin_on_disk()


def _physical_window_size(hwnd):
    """
    The window's size in true physical pixels, asked of DWM rather than of the window.

    GetWindowRect and GetClientRect answer in the CALLING process's DPI coordinate
    space, so under display scaling they can disagree both with what is on the monitor
    and with what mss captures - mss is always physical. DWM's extended frame bounds
    are never virtualised, so this is the tiebreaker between "the window really is that
    size" and "the window is fine and it is the measurement that is scaled". Those two
    look identical from every other angle and need opposite fixes.

    (0, 0) if DWM declines to answer; this only ever feeds a diagnostic line.
    """
    class _Rect(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    rect = _Rect()
    try:
        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        hresult = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            ctypes.c_void_p(hwnd), ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect), ctypes.sizeof(rect),
        )
        if hresult != 0:
            return (0, 0)
    except Exception:
        return (0, 0)
    return (rect.right - rect.left, rect.bottom - rect.top)

ROBLOX_MIN_CLIENT_HEIGHT = 638  # measured; see _explain_minimum_size_clamp

def _explain_minimum_size_clamp(want_w, want_h, got_w, got_h):
    """
    Turns "the window is the wrong size" into the one thing the user can act on.

    Roblox will not make its window shorter than ROBLOX_MIN_CLIENT_HEIGHT, measured
    by walking the height down until it stopped following: it clamps at exactly 638px.
    DOCK_GAME_HEIGHT is 639, so this whole design clears that floor by a single pixel.

    That minimum is in LOGICAL pixels, so display scaling multiplies it. At 125% it
    becomes 638 * 1.25 = 797 physical - now well above the 639 the templates need, and
    unreachable no matter what is requested. No code change can fix that; the request
    is refused by Roblox before Windows is even involved. The display scaling is the
    only lever, so say so, with the arithmetic, instead of leaving a size mismatch that
    reads like a bug in the bot.
    """
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
    except Exception:
        return
    if dpi <= 96:
        return

    scaling = round(dpi / 96 * 100)
    floor = round(ROBLOX_MIN_CLIENT_HEIGHT * dpi / 96)
    if abs(got_h - floor) > 2:
        return  # clamped by something else; don't misattribute it

    print(f"[pin_roblox_borderless] CAUSE: this display is at {scaling}% scaling. Roblox "
          f"refuses to make its window shorter than {ROBLOX_MIN_CLIENT_HEIGHT}px, and scaling "
          f"turns that floor into {ROBLOX_MIN_CLIENT_HEIGHT}x{dpi}/96 = {floor}px - taller than "
          f"the {want_h}px this bot needs, so the size cannot be requested at all.")
    print(f"[pin_roblox_borderless] FIX: set this display to 100% scaling "
          f"(Settings > System > Display > Scale). At 100% the floor is "
          f"{ROBLOX_MIN_CLIENT_HEIGHT}px, which {want_h}px clears, and everything matches "
          f"the machine the templates were captured on.")

def _drive_client_to_size(hwnd, x, y, target_w, target_h, attempts=8):
    """
    Moves/resizes hwnd until its CLIENT area really is target_w x target_h, by
    measuring the result and correcting, rather than setting once and hoping.

    SetWindowPos takes a WINDOW size; GetClientRect reports a CLIENT size. Stripping
    the chrome is supposed to make those identical, and on a 100%-scaling desktop it
    does - which is why one uncorrected call worked everywhere it was ever tested. It
    is not identical everywhere: at 125% scaling a 1136x639 request came back as a
    1136x797 client, and because nothing re-checked, that 797 was adopted as the truth
    and written into config.SCALE.

    That number then breaks everything downstream at once. 1136x797 is 1.43:1 where
    the templates were all captured at 16:9, so Roblox lays its UI out at proportions
    no single scale factor can reconcile - every match fails, every click lands
    somewhere else, and it presents as "the templates are broken on this machine".

    Correcting by the observed error converges in one or two passes whatever the cause
    (chrome that didn't disappear, a DPI-virtualised measurement, a window manager
    nudge), because it never needs to know which one it was - it just closes the gap it
    can see. Returns (converged, (client_w, client_h), last_requested) so the caller can
    say plainly that the size is wrong instead of silently trusting it.
    """
    req_w, req_h = target_w, target_h
    client = (0, 0)
    best_error, best_request, best_client = float("inf"), None, None

    for _ in range(attempts):
        # HWND_TOP instead of SWP_NOZORDER: without this, docking on app startup can
        # leave Roblox correctly positioned but stacked BEHIND whatever else already
        # had focus (the terminal, another window) - technically docked, invisibly.
        # SWP_NOACTIVATE still keeps this from stealing keyboard focus outright.
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOP, x, y, req_w, req_h,
            win32con.SWP_FRAMECHANGED | win32con.SWP_NOACTIVATE,
        )
        # Roblox resizes on its own message loop, so the rect right after the call can
        # still be the old one - measuring immediately reads staleness as error and
        # sends the next correction the wrong way.
        time.sleep(0.12)

        _, _, cw, ch = win32gui.GetClientRect(hwnd)
        client = (cw, ch)
        if client == (target_w, target_h):
            return True, client, (req_w, req_h)

        # Whichever attempt got closest is what the window should be left at. Without
        # this, a loop that oscillates around the target (possible when the size the
        # window reports is a rounded-down multiple of the size requested, so no
        # integer request can land exactly) ends by leaving the window wherever the
        # final attempt happened to put it - which can be further off than something
        # it had already achieved three attempts earlier.
        error = abs(target_w - cw) + abs(target_h - ch)
        if error < best_error:
            best_error, best_request, best_client = error, (req_w, req_h), client

        # Aim off by exactly the error just measured. Both plausible causes - fixed
        # chrome that never went away, and a proportionally scaled measurement - are
        # linear in the requested size, so correcting by the observed error walks in
        # for either without needing to know which one it is facing.
        next_w = req_w + (target_w - cw)
        next_h = req_h + (target_h - ch)
        if next_w <= 0 or next_h <= 0 or (next_w, next_h) == (req_w, req_h):
            break  # diverging, or the window is refusing to move at all
        req_w, req_h = next_w, next_h

    if client != best_client and best_request is not None:
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOP, x, y, best_request[0], best_request[1],
            win32con.SWP_FRAMECHANGED | win32con.SWP_NOACTIVATE,
        )
        time.sleep(0.12)
        _, _, cw, ch = win32gui.GetClientRect(hwnd)
        client = (cw, ch)

    return client == (target_w, target_h), client, (best_request or (req_w, req_h))

def pin_roblox_borderless(width, height, x=0, y=0):
    """
    Strips Roblox's title bar and resize frame, then places its window at exactly
    (x, y) sized (width, height).

    Borderless is what makes this exact rather than approximate: with no chrome, the
    client rect IS the window rect, so the client lands at precisely (x, y) at
    precisely (width, height) - none of pin_roblox_window()'s pad_left/pad_top
    compensation, and none of its "Windows nudged the title bar down" slop. The same
    call produces byte-identical client geometry on any monitor, which is the whole
    point: one set of coordinates stays valid everywhere instead of needing a
    per-screen recalibration.

    Restores the window if it's minimized first - a minimized window parks at
    (-32000, -32000) and silently ignores resizes, which would otherwise leave
    config.CLIENT_* describing a window that isn't where it says it is.

    The original style/rect are saved (see _borderless_state) so
    restore_roblox_window() can hand the user back a completely normal window when
    the bot stops. Returns (width, height) actually achieved, or None if there's no
    Roblox window.
    """
    hwnd = _find_roblox_hwnd()
    if hwnd is None:
        print("[pin_roblox_borderless] No Roblox window found.")
        return None

    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.4)

        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        if hwnd not in _borderless_state:
            # The saved "original" style is normalised to definitely HAVE chrome,
            # rather than trusting whatever we found. If a previous session was killed
            # without cleaning up, Roblox is already borderless when we get here, and
            # saving that as-is would mean restore_roblox_window() faithfully restores
            # it to... borderless. The title bar could then never be recovered, which
            # is exactly the state a user can't get themselves out of (no title bar to
            # drag, no frame to resize). Forcing the bits on makes restore always
            # produce a normal window.
            restorable = style | (
                win32con.WS_CAPTION | win32con.WS_THICKFRAME | win32con.WS_SYSMENU
                | win32con.WS_MINIMIZEBOX | win32con.WS_MAXIMIZEBOX
            )
            saved_rect = win32gui.GetWindowRect(hwnd)
            _borderless_state[hwnd] = {"style": restorable, "rect": saved_rect}
            _remember_pin_on_disk(hwnd, restorable, saved_rect)

        borderless = style & ~(win32con.WS_CAPTION | win32con.WS_THICKFRAME)
        win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, borderless)

        converged, (actual_w, actual_h), last_request = _drive_client_to_size(
            hwnd, x, y, width, height)

        if not converged:
            wl, wt, wr, wb = win32gui.GetWindowRect(hwnd)
            phys = _physical_window_size(hwnd)
            print(f"[pin_roblox_borderless] WARNING: could not get a {width}x{height} client. "
                  f"Best was {actual_w}x{actual_h} after {last_request[0]}x{last_request[1]} "
                  f"was requested (window rect {wr - wl}x{wb - wt}, true physical "
                  f"{phys[0]}x{phys[1]}).")
            # The aspect ratio is the part that actually breaks matching, so name it
            # rather than leaving it to be inferred from two numbers.
            want = width / height
            got = actual_w / actual_h if actual_h else 0
            if abs(want - got) > 0.01:
                print(f"[pin_roblox_borderless] That is {got:.2f}:1, not the {want:.2f}:1 the "
                      f"templates were captured at - Roblox lays its UI out differently at this "
                      f"shape, so no single scale factor can make them match.")

            _explain_minimum_size_clamp(width, height, actual_w, actual_h)

        actual_left, actual_top = win32gui.ClientToScreen(hwnd, (0, 0))

        # A minimized window parks at (-32000, -32000) and ignores SetWindowPos, so
        # both the restore above and the move can quietly fail to take effect - the
        # SW_RESTORE is asynchronous, and a window minimized again in between (or a
        # GUI that is itself minimized, handing over an off-screen slot position)
        # lands here anyway. Committing that rect is what makes it dangerous:
        # config.CLIENT_ORIGIN would then point 32000px off-screen, so every
        # capture_screen() grabs empty desktop and every click lands nowhere, for as
        # long as it takes something else to re-pin. Nothing reports an error - the
        # bot just stops recognising a screen that is plainly there. Refusing to
        # commit keeps the last known-good rect in place instead.
        if actual_left <= -30000 or actual_top <= -30000:
            print(f"[pin_roblox_borderless] Window is off-screen at ({actual_left}, {actual_top}) - "
                  f"it is minimized, or the panel handed over an off-screen slot. Keeping the "
                  f"previous client rect rather than pointing every click into the void.")
            return None

        scale = config.set_client_rect((actual_left, actual_top), (actual_w, actual_h))
        print(f"[pin_roblox_borderless] Borderless client at ({actual_left}, {actual_top}) "
              f"sized {actual_w}x{actual_h} (scale {scale:.4f}).")
        return (actual_w, actual_h)
    except Exception as e:
        print(f"[pin_roblox_borderless] Failed: {e}")
        return None

def set_roblox_topmost(enabled):
    """
    Puts the Roblox window in (or takes it out of) the always-on-top band.

    This is how the docked game stays visible, and it is deliberately TOPMOST rather
    than a plain "raise to front". Measured directly: SetWindowPos(HWND_TOP) on
    another process's window returns success and then does nothing at all - the
    window's z-index was unchanged (4 of 18 before and after) because Windows blocks
    z-order changes from a process that isn't in the foreground. HWND_TOPMOST is not
    blocked the same way; the same test moved it 4 -> 2 and set WS_EX_TOPMOST.

    The GUI window is put in the same band while docked (see BotGUI), so the two
    behave as one unit instead of the game floating away from its own slot the moment
    anything else is clicked.

    SWP_NOACTIVATE throughout - none of this may steal typing focus from the controls.
    """
    hwnd = _find_roblox_hwnd()
    if hwnd is None:
        return False
    try:
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOPMOST if enabled else win32con.HWND_NOTOPMOST,
            0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
        )
        return True
    except Exception:
        return False

def restore_roblox_window():
    """
    Undoes pin_roblox_borderless(): puts the title bar and resize frame back and
    returns the window to where it was, so stopping the bot hands back a normal,
    draggable Roblox rather than leaving a chrome-less window the user can't move.
    Returns True if something was restored.
    """
    hwnd = _find_roblox_hwnd()
    if hwnd is None or hwnd not in _borderless_state:
        # Nothing this process pinned. If a PREVIOUS one did and died before undoing
        # it, that window is still borderless on screen right now, and this is the
        # moment to notice - the alternative is returning False and leaving a window
        # nobody can drag.
        return recover_orphaned_pin()

    state = _borderless_state.pop(hwnd)
    _forget_pin_on_disk()
    try:
        win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, state["style"])
        left, top, right, bottom = state["rect"]
        win32gui.SetWindowPos(
            hwnd, 0, left, top, right - left, bottom - top,
            win32con.SWP_FRAMECHANGED | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE,
        )
        print("[restore_roblox_window] Roblox restored to a normal window.")
        return True
    except Exception as e:
        print(f"[restore_roblox_window] Failed: {e}")
        return False

def dock_game_size(monitor_width, monitor_height):
    """
    The size to pin the game to inside the docked window, as (game_w, game_h).

    Prefers config.DOCK_GAME_WIDTH/HEIGHT, shrinking in 16:9 steps (so the uniform
    config.SCALE stays valid) if this monitor can't fit the whole docked window -
    the game plus the sidebar and controls column flanking it, plus the status bar
    below. A 1366x768 laptop can't show a 960-wide game with 550px of chrome around
    it; it can show a smaller one.

    Where the game actually GOES is not decided here: it's positioned over the
    reserved slot inside the GUI window (see BotGUI._reposition_game_over_slot()),
    so it appears embedded. Only the size has to be agreed up front, because that's
    what config.SCALE and every template depend on.
    """
    game_w, game_h = config.DOCK_GAME_WIDTH, config.DOCK_GAME_HEIGHT

    # Everything the panel needs AROUND the game, taken from the one definition of the
    # panel's size so this can't drift away from what the window actually does. Asking
    # for a zero-sized game leaves exactly the chrome. No window-border allowance is
    # folded in: the docked panel is fullscreen, and a stale padding term here is what
    # previously shrank the game below 1600 - the width the soul-count tooltip stops
    # being readable under.
    chrome_w, chrome_h = config.panel_size(0, 0)

    while game_w > config.DOCK_GAME_MIN_WIDTH and (
        game_w + chrome_w > monitor_width or game_h + chrome_h > monitor_height
    ):
        game_w -= 64
        game_h = int(round(game_w * 9 / 16))

    return game_w, game_h

def focus_roblox_window(pin=True):
    """
    Brings the Roblox window to the OS foreground and (by default) pins it to the
    reference size - call this before any automation that starts while the GUI still
    has focus (e.g. right after clicking Start Loop), otherwise the very first
    click/drag sent just re-focuses the window instead of actually registering.

    pin=False focuses only, for callers that are about to place the window
    themselves - the docked layout calls pin_roblox_borderless() straight after, and
    pinning twice just makes the window visibly jump to one size and then another.

    Returns (focused, client_size): focused is True if a Roblox window was found and
    brought to the foreground, client_size is pin_roblox_window()'s result - the
    (width, height) actually achieved, or None if pinning failed.

    These are reported separately because they fail for different reasons and only one
    of them is survivable. Previously this collapsed both into a single True, so a
    failed pin - the case where every hardcoded coordinate in config.py is wrong,
    e.g. a monitor smaller than the reference resolution - was indistinguishable from
    complete success, and the bot would happily run clicking arbitrary points while the
    explanation went to a console the windowed build doesn't have.
    """
    hwnd = _find_roblox_hwnd()
    if hwnd is None:
        return (False, None)

    try:
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.3)
    except Exception:
        return (False, None)

    return (True, pin_roblox_window() if pin else None)

def click_at(x, y, delay_before=0.1, delay_after=0.2, clicks=1):
    """
    Clicks a REFERENCE-space point (see config.SCALE). Callers pass coordinates
    straight out of config.py or out of vision.find_template() without caring what
    the screen is actually doing; the conversion to real pixels happens here.
    """
    time.sleep(delay_before)

    # 1. Teleport to target location (any monitor - see _move_abs)
    _move_abs(*config.to_screen(x, y))

    # 2. Micro-jitter to trigger hover state, then give Roblox time to draw a frame with
    #    the button hovered. Both stretch on a slow PC (config.SLOWNESS): on a 10-year-old
    #    laptop the old fixed timings let the whole hover-and-press fall between frames.
    _nudge()
    time.sleep(0.08 * config.SLOWNESS)

    # 3. Explicit click hold (repeated for `clicks` - the first click into an unfocused
    #    Roblox window often only focuses it rather than registering as a real click)
    for i in range(clicks):
        pydirectinput.mouseDown()
        time.sleep(0.05 * config.SLOWNESS)
        pydirectinput.mouseUp()
        if i < clicks - 1:
            time.sleep(0.1 * config.SLOWNESS)

    mark_input()
    time.sleep(delay_after)

def click_in_place(delay_before=0.1, delay_after=0.2, clicks=1):
    """
    Clicks wherever the cursor already is, without moving it anywhere first - unlike
    click_at(), which always repositions to a REFERENCE-space point before clicking.

    Fishing's re-cast (see modules/fishing.py) used to aim at a fixed configured
    point, which meant teleporting the cursor there every few seconds - an absolute
    reposition that, mid-match, can turn the camera the same way it did during a
    recorded walk (see stage_player.play_preset's own fix for that). Casting doesn't
    need to aim anywhere in particular; it just needs to press the mouse button again
    wherever fishing was already started from.
    """
    time.sleep(delay_before)
    _nudge()  # still worth a tiny wiggle so Roblox registers a fresh hover/frame
    time.sleep(0.08 * config.SLOWNESS)

    for i in range(clicks):
        pydirectinput.mouseDown()
        time.sleep(0.05 * config.SLOWNESS)
        pydirectinput.mouseUp()
        if i < clicks - 1:
            time.sleep(0.1 * config.SLOWNESS)

    mark_input()
    time.sleep(delay_after)

def type_text(text, interval=0.05):
    """
    Types text into whatever field currently has focus, using pydirectinput so the
    keystrokes are DirectInput scancodes - Roblox ignores the synthetic keystrokes
    that ordinary SendInput-with-unicode produces, the same reason clicks go through
    pydirectinput here rather than through win32 messages.

    Capitals are typed as a held Shift plus the lowercase key. pydirectinput's key
    table has no entry for "S" at all, so press("S") silently does nothing and
    returns False - typing "Sum" that way would send "um" and search for the wrong
    thing without any error.

    A per-character interval rather than one burst: a text box that filters a list on
    every keystroke can drop characters typed faster than it re-renders.
    """
    for char in text:
        if char.isupper():
            pydirectinput.keyDown("shift")
            sent = pydirectinput.press(char.lower())
            pydirectinput.keyUp("shift")
        else:
            sent = pydirectinput.press(char)

        if sent is False:
            print(f"[type_text] WARNING: no key mapping for {char!r} - it was not typed.")
        mark_input()
        time.sleep(interval)

def drift_hover(x, y, approach_offset=15, steps=5, step_delay=0.02, settle_delay=0.2):
    """
    Moves the cursor to (x, y) via several incremental relative steps approaching
    from a nearby outside point, instead of an instant teleport (click_at's own tiny
    jitter is enough to register a click, but some hover-only tooltip/reveal UI
    apparently needs a genuine mouse-enter transition to actually trigger - a
    straight teleport can land "already inside" without ever firing that transition).
    """
    # Converted up front so the approach path is walked in real pixels; the offset
    # scales with everything else, so the approach stays proportionally the same.
    target_x, target_y = config.to_screen(x, y)
    offset_x, offset_y = config.to_screen(approach_offset, approach_offset)
    start_x, start_y = target_x - offset_x, target_y - offset_y

    step_delay *= config.SLOWNESS
    settle_delay *= config.SLOWNESS
    _move_abs(start_x, start_y)
    time.sleep(step_delay)

    dx = (target_x - start_x) / steps
    dy = (target_y - start_y) / steps
    for i in range(1, steps + 1):
        _move_abs(start_x + dx * i, start_y + dy * i)
        time.sleep(step_delay)

    _move_abs(target_x, target_y)
    mark_input()
    time.sleep(settle_delay)

def _scroll_carousel(direction, hover=None, notches=18):
    """
    Wheel-scrolls the map/raid carousel. direction is -1 (right/down) or +1 (left/up).

    hover is the REFERENCE-space point to put the cursor on first, defaulting to
    config.MAP_CAROUSEL_HOVER_X/Y. It matters more than anything else here:
    _native_scroll sends MOUSEEVENTF_WHEEL, which Windows delivers to whatever window
    and control is under the CURSOR, so a hover point that no longer sits over the
    carousel means the notches land somewhere inert and the list never moves - with
    no error anywhere, since scrolling has no success value to report. Callers that
    can see a card on screen should pass its position instead of trusting the
    hand-measured constant, which goes stale the moment the game restyles the screen.

    Ends with a tiny jitter, the same fix click_at() already relies on for clicks.
    moveTo() is a no-op at the OS level when the cursor is ALREADY at that pixel -
    which happens constantly here, since consecutive scroll calls often reuse the
    same anchor - and a move that doesn't actually change position never generates
    a real WM_MOUSEMOVE. Roblox only registers hover over the carousel from that
    event, so with no genuine move, the wheel notches below land wherever the
    cursor last had hover from - reported live as "it doesn't scroll... I move the
    cursor a bit and it starts scrolling as wanted", which is exactly a person
    supplying by hand the mouse-move this function was silently skipping.
    """
    hover_x, hover_y = hover if hover else (config.MAP_CAROUSEL_HOVER_X, config.MAP_CAROUSEL_HOVER_Y)
    _move_abs(*config.to_screen(hover_x, hover_y))
    _nudge()
    time.sleep(0.1)

    # Smooth micro-steps (120 = 1 standard wheel notch)
    for _ in range(notches):
        _native_scroll(direction * 120)
        time.sleep(0.015)  # Fast interval creates a smooth continuous scroll

    mark_input()
    time.sleep(0.6)  # Wait for UI snap animation to finish

def scroll_maps_right(hover=None, notches=18):
    """Scrolls the carousel towards the end of the list."""
    _scroll_carousel(-1, hover=hover, notches=notches)

def scroll_maps_left(hover=None, notches=18):
    """Scrolls the carousel back towards the start of the list."""
    _scroll_carousel(1, hover=hover, notches=notches)

def anchor_camera(zoom_out_steps=None):
    """
    Forces the camera into a consistent top-down isometric view.

    zoom_out_steps: how many wheel notches to zoom back out at the end. None uses
    config.CAMERA_ZOOM_OUT_STEPS (Story/Raids/Challenges/Portals). Expeditions passes
    its own value from the slider in its panel. Recorded macros depend on this: change
    it and the units land in different places, so re-record after changing it.

    Every gap below is scaled by config.SLOWNESS, the same way click_at()'s hover
    jitter is (see its own comment) - each scroll notch and each pitch-drag step is
    a separate mouse event that Roblox has to receive AND draw a frame for before it
    counts as registered, on a laptop that can take noticeably longer than the fixed
    gap this used to sleep for. Landing on a camera angle a few degrees short of
    top-down - or a zoom level short of fully in/out - throws off every position a
    macro recorded relative to it, which reads as the walk itself being imprecise
    when the actual cause is the camera never finishing this reset.
    """
    time.sleep(1.0 * config.SLOWNESS)

    # 1. Zoom in close to reset distance drift. Deliberately stops just short of a full
    #    zero-distance zoom - going fully to 0 tends to force Roblox into first-person mode,
    #    which has no pitch clamp and can flip/spin the camera during step 2 below.
    for _ in range(config.CAMERA_ZOOM_IN_STEPS):
        _native_scroll(120)
        time.sleep(0.015 * config.SLOWNESS)

    time.sleep(0.5 * config.SLOWNESS)

    # 2. Angle camera Top-Down (raw relative drag, 0 horizontal drift).
    #    Deliberately does NOT teleport the cursor first (no moveTo/absolute jump): once
    #    zoomed in close, Roblox's camera can start following raw mouse position directly,
    #    so any absolute repositioning here gets read as a spurious look/rotate before the
    #    intentional drag even begins. Dragging from wherever the cursor already is avoids that.
    #    Wrapped so the button is released no matter what: an exception thrown between
    #    the mouseDown and the mouseUp would otherwise leave right-mouse held down, and
    #    in Roblox that means the camera keeps following the cursor and every later click
    #    is swallowed - a broken state that survives until the user clicks manually.
    pydirectinput.mouseDown(button='right')
    try:
        time.sleep(0.1 * config.SLOWNESS)

        for _ in range(config.CAMERA_PITCH_STEPS):
            win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, 0, config.CAMERA_PITCH_STEP_SIZE, 0, 0)
            time.sleep(0.02 * config.SLOWNESS)

        time.sleep(0.1 * config.SLOWNESS)
    finally:
        pydirectinput.mouseUp(button='right')
    time.sleep(0.5 * config.SLOWNESS)

    # 3. Zoom all the way OUT (Scroll DOWN) to max distance
    steps = config.CAMERA_ZOOM_OUT_STEPS if zoom_out_steps is None else max(0, int(zoom_out_steps))
    for _ in range(steps):
        _native_scroll(-120)
        time.sleep(0.015 * config.SLOWNESS)

    mark_input()
    time.sleep(0.5)