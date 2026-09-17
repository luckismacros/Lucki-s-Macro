# tools/resize_roblox.py
"""
Pins Roblox to an exact client size and leaves it there, so screenshots taken by hand
(or by tools/template_capture.py) land at a size the rest of the codebase can trust.

Why this exists
----------------
Every template in assets/templates/ has to be captured against a 1920x1080 client -
that is what config.REFERENCE_WIDTH/HEIGHT names, and what config.SCALE scales DOWN
from for every other client size. Recapturing against anything else produces a template
that is quietly the wrong size: it still matches, at some scale that isn't 1.0, and
config.effective_threshold() has no way to know it should demand less of it - it just
scores low, and reads exactly like a stale or mistuned template rather than a wrongly
captured one. That is what took assets/templates/story_card.png, raid_card.png,
challenge_card.png and play_btn.png down to 0.26-0.63 confidence in September - each was
captured against a client roughly 1728px wide, not 1920.

Two sizes actually matter here:

  1920x1080 (the default) - for assets/templates/. Every master template lives here,
      and this is the only size that folder is ever allowed to be captured at.

  1600x900 (config.DOCK_GAME_WIDTH x HEIGHT) - for assets/templates@1600x900/, the
      folder of same-size captures used for templates that don't survive being shrunk
      (mostly text - see vision.py's own docstring for why). Prefer
      tools/retemplate.py for these: it captures, positions and verifies each crop
      automatically. Use this tool plus tools/template_capture.py --variant only if
      retemplate.py's automatic search genuinely can't find something.

This tool does the pinning half of that job precisely - it does not take any
screenshots itself. Run it, confirm the size it reports, then run
tools/template_capture.py (default mode, for 1920x1080) or with --variant (for a
same-size folder) to actually crop something.

Usage:
  python tools/resize_roblox.py               # pin to 1920x1080, for assets/templates/
  python tools/resize_roblox.py 1600 900       # pin to 1600x900, for the variant folder
  python tools/resize_roblox.py --restore      # give the window its title bar back
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must run before any win32 window call below - see dpi_awareness.py. This is the same
# guard gui.py applies, and skipping it here is one concrete way earlier captures ended
# up sized for a client that was never actually the size it was pinned to: an unaware
# process's idea of a given width and mss's physical-pixel screenshot can silently stop
# agreeing under display scaling.
import dpi_awareness
DPI_AWARENESS_RESULT = dpi_awareness.set_dpi_awareness()

import config
from input_controller import _find_roblox_hwnd, pin_roblox_borderless, restore_roblox_window


def _which_folder(width, height):
    if (width, height) == (config.REFERENCE_WIDTH, config.REFERENCE_HEIGHT):
        return "assets/templates/ - the reference set every master template lives in"
    if (width, height) == (config.DOCK_GAME_WIDTH, config.DOCK_GAME_HEIGHT):
        return (f"assets/templates@{width}x{height}/ - same-size variants "
                f"(prefer tools/retemplate.py to actually produce these)")
    return ("nothing config.py currently expects - only useful for a one-off check, "
            "not for capturing templates")


def main():
    parser = argparse.ArgumentParser(
        description="Pin Roblox to an exact client size for template capture.")
    parser.add_argument("width", nargs="?", type=int, default=None,
                         help=f"client width (default {config.REFERENCE_WIDTH})")
    parser.add_argument("height", nargs="?", type=int, default=None,
                         help=f"client height (default {config.REFERENCE_HEIGHT})")
    parser.add_argument("--restore", action="store_true",
                         help="undo this and give Roblox its title bar back")
    args = parser.parse_args()

    print(f"DPI awareness: {DPI_AWARENESS_RESULT}")
    if DPI_AWARENESS_RESULT.startswith("FAILED"):
        print("WARNING: could not get DPI awareness. On any monitor that isn't at 100%")
        print("         display scaling, the size pinned below may not match the size")
        print("         actually screenshotted. Set Settings > System > Display > Scale")
        print("         to 100% before capturing if this tool's own report of the")
        print("         achieved size looks wrong.")

    if args.restore:
        if restore_roblox_window():
            print("Roblox's title bar and normal size are back.")
        else:
            print("Nothing to restore (Roblox wasn't pinned borderless by this tool).")
        return 0

    if (args.width is None) != (args.height is None):
        print("ERROR: pass both width and height, or neither.")
        return 1

    width = args.width or config.REFERENCE_WIDTH
    height = args.height or config.REFERENCE_HEIGHT

    if width * 9 != height * 16:
        print(f"WARNING: {width}x{height} is not 16:9. Every template and coordinate in")
        print(f"         this codebase assumes a 16:9 client - Roblox lays its UI out")
        print(f"         differently at other aspect ratios, which no scale factor can")
        print(f"         correct for. Continuing anyway, since you asked for this size.")

    print(f"\nTarget: {width}x{height}")
    print(f"Captures at this size belong in: {_which_folder(width, height)}\n")

    hwnd = _find_roblox_hwnd()
    if hwnd is None:
        print("ERROR: no Roblox window found. Start Roblox and try again.")
        return 1

    # pin_roblox_borderless rather than pin_roblox_window: it measures the client rect
    # after each attempt and corrects towards the target instead of computing a chrome
    # offset once and trusting it, which is what makes it the reliable one to use for a
    # capture that has to be exact - see input_controller._drive_client_to_size().
    result = pin_roblox_borderless(width, height)
    if result is None:
        print("\nFAILED - see the message above from pin_roblox_borderless.")
        return 1

    actual_w, actual_h = result
    if (actual_w, actual_h) != (width, height):
        print(f"\nWARNING: asked for {width}x{height} but the client is really "
              f"{actual_w}x{actual_h}.")
        print(f"         Anything captured now is {actual_w}x{actual_h} - do NOT save it")
        print(f"         into a folder named for {width}x{height}, it will silently be")
        print(f"         the wrong size again.")
    else:
        print(f"\nConfirmed: Roblox's client area is exactly {actual_w}x{actual_h}.")

    print("\nRoblox is borderless and pinned in place. Take your screenshots now -")
    print("tools/template_capture.py, or by hand - then run:")
    print("  python tools/resize_roblox.py --restore")
    print("to give it its title bar back when you're done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
