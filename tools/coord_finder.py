# tools/coord_finder.py
"""
Run it, hover the spot in Roblox, hit SPACE. It prints the coordinate and exits.

    python tools/coord_finder.py

The window is pinned to the SAME geometry the bot uses at runtime before anything is
captured, and put back exactly as it was on the way out.

Both halves of that matter. Capturing against whatever size Roblox happened to be
(e.g. maximized, client 1904x1041) while the bot runs at a different one (pinned,
client 1920x1009) puts every bottom-anchored target ~32px away from where it was
recorded, because config.SCALE is derived from width and cannot express a height
difference. And an earlier version of this tool pinned without ever restoring - it
ran as its own process, so nothing undid it - which is what kept leaving Roblox
stuck full-screen. GetWindowPlacement/SetWindowPlacement round-trips the whole
state, maximized-ness included, in a finally block.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pydirectinput
import win32gui
from pynput import keyboard

import config
from input_controller import (_find_roblox_hwnd, focus_roblox_window,
                              pin_roblox_borderless)

_result = {}


def _on_press(key):
    if key == keyboard.Key.esc:
        return False
    if key == keyboard.Key.space:
        _result["pos"] = pydirectinput.position()
        return False


def main():
    hwnd = _find_roblox_hwnd()
    if hwnd is None:
        print("ERROR: no Roblox window found. Start Roblox and try again.")
        return

    if win32gui.IsIconic(hwnd):
        print("ERROR: Roblox is minimized. Bring it back up, then run this again.")
        return

    placement = win32gui.GetWindowPlacement(hwnd)
    try:
        # Pinned to whatever the bot ACTUALLY runs at, and when embedding is on that
        # is a true 16:9 client.
        #
        # The aspect ratio is the part that bites. config.SCALE is derived from width
        # alone, so a reference coordinate only means the same thing in two clients
        # that share an aspect ratio. Points captured in a maximised 1904x1041 window
        # (1.83) and replayed in a 1136x639 one (1.78) land ~10-20px out vertically,
        # which is exactly far enough to sit just under a button instead of on it -
        # and no arithmetic fixes it afterwards, because different pieces of Roblox
        # UI anchor to the top, the centre and the bottom.
        if config.DOCK_ENABLED:
            # Pinned at wherever the window's own top-left corner ALREADY is, not a
            # hardcoded spot - a maximized window sits a few px negative
            # (Windows' invisible resize border), and forcing it to (100, 100)
            # instead made it visibly jump across the screen, which is disorienting
            # when you're trying to hover something on it a second later. Resizing
            # in place has no such effect: only the size changes, not the corner.
            cur_left, cur_top, _, _ = win32gui.GetWindowRect(hwnd)
            client = pin_roblox_borderless(config.DOCK_GAME_WIDTH,
                                           config.DOCK_GAME_HEIGHT, cur_left, cur_top)
        else:
            _, client = focus_roblox_window(pin=True)
        if client is None:
            print("ERROR: couldn't pin the Roblox window.")
            return

        w, h = client
        print(f"Roblox pinned to {w}x{h} (the size the bot runs at), "
              f"aspect {w / h:.3f}, scale {config.SCALE:.4f}.")
        print("Hover the spot, press SPACE (ESC to cancel)...")

        with keyboard.Listener(on_press=_on_press) as listener:
            listener.join()

        if "pos" not in _result:
            print("Cancelled.")
            return

        screen_x, screen_y = _result["pos"]
        x, y = config.to_reference(screen_x - config.CLIENT_ORIGIN[0],
                                   screen_y - config.CLIENT_ORIGIN[1])
        print(f"\n{x}, {y}")
    finally:
        try:
            from input_controller import restore_roblox_window
            restore_roblox_window()
            win32gui.SetWindowPlacement(hwnd, placement)
            print("(Roblox put back the way it was.)")
        except Exception as e:
            print(f"(Couldn't restore the Roblox window: {e})")


if __name__ == "__main__":
    main()
