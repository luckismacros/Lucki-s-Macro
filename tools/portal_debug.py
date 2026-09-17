# tools/portal_debug.py
"""
Portal navigation diagnostic.

Entering a portal is four fixed clicks and a typed search term (see
modules/portal_select.py), so when it goes wrong it's almost always one coordinate
being off. This runs each step on its own so you can watch exactly which one misses:

  1  search box   - double-clicks PORTAL_SEARCH_BOX_X/Y; the box should take focus
  2  type         - types the portal's term; the list should filter down to it
  3  first result - clicks PORTAL_FIRST_RESULT_X/Y; the top portal should get picked
  4  confirm      - looks for the Activate Portal / Select button

Run the whole sequence with 1-4, or a single step by its number, and if a click lands
in the wrong place read the right coordinates off `python tools/coord_finder.py` and
put them in config.py.

Usage: python tools/portal_debug.py
  1 / 2 / 3 / 4 - run that step alone
  A             - run the whole search-and-select for the chosen portal
  P             - cycle which portal is being tested
  B             - click Items, then the Portals tab (get the list open first)
  C             - confidence of the buttons this flow waits for
  D             - save the current screenshot to portal_debug_shots/
  ESC           - quit
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import cv2
import pydirectinput
from pynput import keyboard

from vision import capture_screen, _load_template
from input_controller import click_at, type_text
import config

SHOT_DIR = "portal_debug_shots"
shot_count = 0
categories = [k for k, v in config.PORTALS.items() if v["enabled"]]
current = 0


def portal():
    key = categories[current]
    return key, config.PORTALS[key]


def show_target():
    key, data = portal()
    print(f"[portal] testing {data['label']} - search term '{data['search']}'")


def step_search_box():
    print(f"[1] double-clicking the search box at "
          f"({config.PORTAL_SEARCH_BOX_X}, {config.PORTAL_SEARCH_BOX_Y})")
    click_at(config.PORTAL_SEARCH_BOX_X, config.PORTAL_SEARCH_BOX_Y, clicks=2)
    print("    the search box should now be focused, with any old term selected")


def step_type():
    key, data = portal()
    print(f"[2] typing '{data['search']}'")
    type_text(data["search"], config.PORTAL_SEARCH_TYPE_DELAY)
    print(f"    the list should filter to {data['label']}; if the term is missing or")
    print("    doubled ('SumSum'), step 1 didn't focus/select the box")


def step_first_result():
    print(f"[3] clicking the first result at "
          f"({config.PORTAL_FIRST_RESULT_X}, {config.PORTAL_FIRST_RESULT_Y})")
    click_at(config.PORTAL_FIRST_RESULT_X, config.PORTAL_FIRST_RESULT_Y, clicks=2)
    print("    the top portal in the filtered list should now be selected")


def step_confirm():
    screenshot = capture_screen()
    from vision import find_template
    for label, path in (("Activate Portal", config.ACTIVATE_PORTAL_BTN),
                        ("Select", config.PORTAL_SELECT_BTN)):
        match = find_template(screenshot, path, config.MATCH_THRESHOLD, debug_label=label)
        if match:
            print(f"[4] {label} found at ({match[0]}, {match[1]}), conf={match[2]:.3f} "
                  f"- the flow would click it here")
            return
    print("[4] neither Activate Portal nor Select is on screen right now")


def run_all():
    from modules import portal_select
    key, data = portal()
    print(f"=== full search-and-select for {data['label']} (this DOES click) ===")
    result = portal_select.select_portal(key)
    print(f"=== select_portal -> {result} ===")


def open_list():
    from modules import portal_select
    print("[B] clicking Items...")
    if portal_select.click_items() != True:
        print("    Items button not found - are you at the lobby?")
        return
    portal_select.click_portals_tab()
    print("    Portals tab clicked; the list should be open")


def check_buttons():
    from vision import find_template
    screenshot = capture_screen()
    print(f"--- buttons | screen {screenshot.shape[1]}x{screenshot.shape[0]} ---")
    for label, path in (("items", config.ITEMS_BTN),
                        ("activate_portal", config.ACTIVATE_PORTAL_BTN),
                        ("select_portal", config.PORTAL_SELECT_BTN)):
        template = _load_template(path)
        result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
        _, conf, _, loc = cv2.minMaxLoc(result)
        verdict = "MATCH" if conf >= config.MATCH_THRESHOLD else "not on screen"
        print(f"  {conf:.3f}  {label:<18} {verdict:<15} "
              f"at ({loc[0] + template.shape[1] // 2}, {loc[1] + template.shape[0] // 2})")
    print(f"  (threshold {config.MATCH_THRESHOLD})")


def next_portal():
    global current
    current = (current + 1) % len(categories)
    show_target()


def save_shot():
    global shot_count
    os.makedirs(SHOT_DIR, exist_ok=True)
    shot_count += 1
    path = os.path.join(SHOT_DIR, f"shot_{shot_count:02d}.png")
    cv2.imwrite(path, capture_screen())
    print(f"[saved] {path}")


def on_press(key):
    if key == keyboard.Key.esc:
        return False
    char = getattr(key, "char", None)
    if not char:
        return
    actions = {"1": step_search_box, "2": step_type, "3": step_first_result,
               "4": step_confirm, "a": run_all, "p": next_portal,
               "b": open_list, "c": check_buttons, "d": save_shot}
    action = actions.get(char.lower())
    if action:
        action()


def main():
    print("=== Portal Navigation Diagnostic ===")
    print("Open the game first. B opens the portal list for you.")
    print("  1 / 2 / 3 / 4 = search box / type / first result / confirm button")
    print("  A = run the whole search-and-select | P = switch portal")
    print("  B = open the portal list | C = check the buttons")
    print("  D = save a screenshot | ESC = quit")
    show_target()

    with keyboard.Listener(on_press=on_press) as listener:
        listener.join()

    print("Done.")


if __name__ == "__main__":
    main()
