# tools/template_capture.py
"""
Live template capture tool.

The bot recognizes buttons, cards and stage tiles by matching cropped reference
images (assets/templates/...) against the live screen - see vision.find_template().
Those crops have to come from a real screenshot at some point; this tool is how you
make one without editing pixels by hand.

Run it while Roblox is open and showing whatever screen has the element you want to
capture (the Story entrance, a map carousel, a reward screen, ...). It grabs one
screenshot, lets you drag a box around the element, and saves that crop straight to
assets/templates/. Repeat for as many elements as you need in one run.

This is also the way to fix a template that stopped matching after a game update, or
to add one for a new machine/resolution if a crop taken elsewhere doesn't transfer -
capture it fresh against this screen instead of guessing pixel offsets.

Usage: python tools/template_capture.py
  - Drag a box around the element, then press ENTER or SPACE to confirm the crop
    (or 'c' to cancel and retry the same capture).
  - You'll then be asked for a name - e.g. "story/act_6" saves to
    assets/templates/story/act_6.png. Leave it blank to discard the crop instead of
    saving it.
  - Press ESC while dragging, or answer 'q' to the "capture another?" prompt, to quit.

After saving, run tools/template_check.py to see the confidence this crop gets
against a screenshot before trusting it in a real run.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must run before any win32 window call below - see dpi_awareness.py. gui.py does this
# first thing for exactly this reason; this tool makes the same MoveWindow/
# GetClientRect calls and needs the same guard, and going without it is one concrete
# way a capture session silently produces crops sized for a client that was never
# actually the size it was pinned to.
import dpi_awareness
DPI_AWARENESS_RESULT = dpi_awareness.set_dpi_awareness()

import cv2
import win32gui

import config
import vision
from input_controller import pin_roblox_window, _find_roblox_hwnd

TEMPLATE_DIR = "assets/templates"
WINDOW_NAME = "template_capture - drag a box, ENTER/SPACE to confirm, ESC to cancel"


def _capture_one(screenshot):
    """
    Runs OpenCV's built-in drag-to-select UI against one screenshot. Returns the
    cropped BGR image, or None if the user cancelled (ESC / zero-size box).
    """
    box = cv2.selectROI(WINDOW_NAME, screenshot, showCrosshair=True, fromCenter=False)
    cv2.destroyWindow(WINDOW_NAME)
    x, y, w, h = box
    if w == 0 or h == 0:
        return None
    return screenshot[y:y + h, x:x + w]


def _save(crop, name, target_dir=TEMPLATE_DIR):
    """
    name may include a subfolder, e.g. "story/act_6" -> assets/templates/story/act_6.png,
    matching how vision.find_template() and config.py reference templates elsewhere.

    target_dir picks the tree: the reference templates by default, or a size-specific
    assets/templates@WxH/ in --variant mode. The name is the same either way, because
    that is exactly how vision pairs a variant with the template it stands in for.
    """
    rel_path = name if name.endswith(".png") else f"{name}.png"
    full_path = os.path.join(target_dir, rel_path)
    os.makedirs(os.path.dirname(full_path) or target_dir, exist_ok=True)
    cv2.imwrite(full_path, crop)
    return full_path


def main():
    print("=== Template Capture ===")
    print(f"DPI awareness: {DPI_AWARENESS_RESULT}")
    print("Make sure Roblox is open and showing the screen with the element you want.")

    # --variant: capture into assets/templates@<W>x<H>/ instead, against the window
    # exactly as it already is, WITHOUT pinning it.
    #
    # That combination is the point. The docked size is the one a variant has to be
    # taken at, and the macro is what puts the window there - so this mode leaves the
    # window alone and lets the macro own it. Pinning here would drag Roblox back to
    # native mid-session and fight the macro for the window, which is exactly what
    # made a retemplate run capture nothing for fifteen minutes.
    variant_mode = "--variant" in sys.argv
    target = TEMPLATE_DIR

    if variant_mode:
        hwnd = _find_roblox_hwnd()
        client = win32gui.GetClientRect(hwnd)[2:] if hwnd else None
        if client:
            config.set_client_rect(win32gui.ClientToScreen(hwnd, (0, 0)), client)
        target = f"assets/templates@{client[0]}x{client[1]}" if client else TEMPLATE_DIR
        print(f"VARIANT MODE - saving to {target}/")
        print(f"Roblox is {client[0]}x{client[1]} and will be left exactly as it is.")
        if client != (config.DOCK_GAME_WIDTH, config.DOCK_GAME_HEIGHT):
            print(f"NOTE: that is not the docked size "
                  f"({config.DOCK_GAME_WIDTH}x{config.DOCK_GAME_HEIGHT}). Start the macro "
                  f"first so it docks the game, or these crops will be the wrong size.")
    else:
        # Pinned first so config.SCALE reflects this machine's real client size - a crop
        # taken against an unpinned or arbitrarily-sized window is captured at whatever
        # scale that window happened to be, which is only useful for THIS run rather than
        # being a trustworthy reference template.
        size = pin_roblox_window()
        if size is None:
            print("WARNING: couldn't pin the Roblox window - capturing the raw primary "
                  "monitor instead. Templates saved this way may not scale correctly.")
        else:
            print(f"Roblox pinned to {size[0]}x{size[1]} (scale {config.SCALE:.4f}).")

    while True:
        input("\nSwitch to the screen you want to capture from, then press ENTER here...")
        screenshot = vision.capture_screen()

        crop = _capture_one(screenshot)
        if crop is None:
            print("Cancelled - no crop taken.")
        else:
            print(f"Captured a {crop.shape[1]}x{crop.shape[0]} crop.")
            name = input(
                "Save as (e.g. 'story/act_6'), or leave blank to discard: "
            ).strip()
            if name:
                path = _save(crop, name, target)
                print(f"Saved to {path}")
                print(f"Check it with: python tools/template_check.py -t {os.path.basename(name)}")
            else:
                print("Discarded.")

        again = input("\nCapture another? [Y/n/q] ").strip().lower()
        if again in ("n", "q"):
            break

    print("\nDone. Remember: a crop taken here only proves it matches THIS machine's "
          "screen right now - re-run tools/template_check.py against a fresh "
          "screenshot any time you're unsure a template still holds up.")


if __name__ == "__main__":
    main()
