# ui_qt/docking.py
"""
Places the Roblox window inside the Qt window's game area - on whichever monitor
the Qt window is on, at whatever Windows display scaling that monitor uses.

Same idea as the CustomTkinter version (Roblox stays its own top-level window,
borderless, sitting exactly over a hole in this one - reparenting it is what
Roblox's anti-cheat kills), done in a way that holds up on more setups:

* Everything is measured in PHYSICAL pixels straight from Windows. The game area is
  a native child window, so GetWindowRect on it is exact at 100%, 125%, 150%... and
  on a second monitor with negative desktop coordinates. Qt's own geometry is in
  scaled "logical" units and is never used for placement.
* The hole is a real window region (SetWindowRgn) cut to the game's exact physical
  rectangle, not a colour key. Nothing can show through anywhere else, clicks in the
  game area reach the game, and there are no rounding slivers at fractional scaling.
* The game size is chosen from the space the game area actually has on that
  monitor, in 64px 16:9 steps down to config.DOCK_GAME_MIN_WIDTH, so a 1366x768
  laptop and a 4K screen both get the largest size that fits.
"""
import win32api
import win32con
import win32gui

import config
from input_controller import (
    pin_roblox_borderless, set_roblox_topmost, restore_roblox_window, roblox_is_running,
)


def fit_game_size(avail_w, avail_h):
    """Largest 16:9 game (reference size at most) that fits, and whether it fits at all."""
    game_w, game_h = config.DOCK_GAME_WIDTH, config.DOCK_GAME_HEIGHT
    while game_w > config.DOCK_GAME_MIN_WIDTH and (game_w > avail_w or game_h > avail_h):
        game_w -= 64
        game_h = int(round(game_w * 9 / 16))
    return (game_w, game_h), (game_w <= avail_w and game_h <= avail_h)


def window_rect(hwnd):
    return win32gui.GetWindowRect(hwnd)


def monitor_info(hwnd):
    """Physical rect and name of the monitor this window is on."""
    try:
        handle = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        info = win32api.GetMonitorInfo(handle)
        return info["Monitor"], info["Work"], info.get("Device", "")
    except Exception:
        w, h = win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1)
        return (0, 0, w, h), (0, 0, w, h), ""


class DockState:
    def __init__(self):
        self.docked = False          # Roblox is currently sitting in the hole
        self.embedded = True         # False = screen too small, running as two windows
        self.released = False        # user pressed Release; don't auto re-dock
        self.game_size = (config.DOCK_GAME_WIDTH, config.DOCK_GAME_HEIGHT)
        self.game_rect = None        # physical (l, t, r, b) of the hole
        self.last_target = None


def cut_hole(top_hwnd, game_rect):
    """Makes game_rect (physical screen coords) a see-through, click-through hole."""
    wl, wt, wr, wb = window_rect(top_hwnd)
    gl, gt, gr, gb = game_rect
    outer = win32gui.CreateRectRgnIndirect((0, 0, wr - wl, wb - wt))
    hole = win32gui.CreateRectRgnIndirect((gl - wl, gt - wt, gr - wl, gb - wt))
    win32gui.CombineRgn(outer, outer, hole, win32con.RGN_DIFF)
    win32gui.DeleteObject(hole)
    # The system owns the region after SetWindowRgn - it must not be deleted here.
    win32gui.SetWindowRgn(top_hwnd, outer, True)


def fill_hole(top_hwnd):
    try:
        win32gui.SetWindowRgn(top_hwnd, None, True)
    except Exception:
        pass


def dock(top_hwnd, slot_hwnd, state, log=print, compact=False):
    """
    Sizes and pins Roblox over the slot. UI thread only. Returns the client (w, h)
    or None. `state` is updated in place.

    compact=True is the small-screen mode (the panel is a slim window and the slot
    isn't shown): the game is pinned at the top-left of the panel's monitor at the
    largest size that monitor fits, as its own window.
    """
    if not roblox_is_running():
        return None
    if win32gui.IsIconic(top_hwnd):
        return None

    if compact:
        fill_hole(top_hwnd)
        (ml, mt, mr, mb), _, _ = monitor_info(top_hwnd)
        (game_w, game_h), _ = fit_game_size(mr - ml, mb - mt)
        state.game_size = (game_w, game_h)
        state.embedded = False
        target = ("free", ml, mt, game_w, game_h)
        if target == state.last_target and state.docked:
            return state.game_size
        client = pin_roblox_borderless(game_w, game_h, ml, mt)
        state.last_target = target if client is not None else None
        state.game_rect = None
        state.docked = client is not None
        return client

    sl, st, sr, sb = window_rect(slot_hwnd)
    if sl <= -30000 or st <= -30000:
        return None
    (game_w, game_h), fits = fit_game_size(sr - sl, sb - st)
    state.game_size = (game_w, game_h)
    state.embedded = fits
    if not fits:
        return None

    x = sl + (sr - sl - game_w) // 2
    y = st + (sb - st - game_h) // 2
    target = ("dock", x, y, game_w, game_h)
    if target == state.last_target and state.docked and roblox_is_running():
        return state.game_size

    client = pin_roblox_borderless(game_w, game_h, x, y)
    if client is None:
        state.docked = False
        return None
    cw, ch = client
    state.game_rect = (x, y, x + cw, y + ch)
    cut_hole(top_hwnd, state.game_rect)
    set_roblox_topmost(True)
    state.last_target = target
    state.docked = True
    state.released = False
    return client


def undock(top_hwnd, state):
    fill_hole(top_hwnd)
    set_roblox_topmost(False)
    restored = restore_roblox_window()
    state.docked = False
    state.game_rect = None
    state.last_target = None
    return restored
