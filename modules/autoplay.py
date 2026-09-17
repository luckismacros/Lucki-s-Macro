# modules/autoplay.py
"""
Module: in-game "Auto Play" toggle detection/activation.
The game has its own built-in autoplay system; this just makes sure it's switched on.
"""
import time
from vision import capture_screen, score_template
from input_controller import click_at
import config

# How long to keep polling for the Auto Play button before giving up. On some maps
# the in-game UI takes noticeably longer to render after the camera anchor sequence
# finishes - a single one-shot check was missing the button entirely on those maps
# and silently starting the match with Auto Play never actually turned on.
AUTOPLAY_CHECK_TIMEOUT = 12.0
AUTOPLAY_CHECK_POLL_INTERVAL = 0.5

# How high either face has to score before the button counts as on screen at all.
# Both templates sit around 0.33-0.39 on a lobby with no button present and 0.83-0.87
# when it is there, so this sits in open space between the two.
AUTOPLAY_PRESENT_AT = 0.60

# How far apart the two readings must be to call a winner. They differ by ~0.036 on a
# real frame, so this stays small on purpose - it only rejects a genuine dead heat.
AUTOPLAY_MIN_MARGIN = 0.012

def _read_toggle(screenshot):
    """
    Which face the Auto Play button is showing: "on", "off", or None if neither is
    convincing enough to call.

    Scored head to head rather than tested one after the other. "Auto Play" and
    "Auto Playing" are the same green pill with the same gear icon and one word of
    difference, so each scores highly against the other's screen: measured on a real
    portal pre-start capture with the button plainly OFF, the ON template still read
    0.829 against a 0.789 bar. Because the old code asked "is it already on?" first
    and took yes for an answer, that reading alone was enough to report "Already Auto
    Playing" and return without ever clicking - which is why Auto Play kept starting
    matches switched off. The OFF template scored 0.865 on that same frame, so the
    comparison gets it right where the fixed bar could not.
    """
    on = score_template(screenshot, config.AUTOPLAY_ON_BTN)
    off = score_template(screenshot, config.AUTOPLAY_OFF_BTN)
    on_conf = on[2] if on else 0.0
    off_conf = off[2] if off else 0.0

    # One of the two faces has to actually be there. Below this, the button isn't on
    # screen yet and the comparison is just noise against noise - on an empty lobby
    # the same two templates read 0.326 and 0.390.
    if max(on_conf, off_conf) < AUTOPLAY_PRESENT_AT:
        return None, on_conf, off_conf

    # A clear winner decides it. Near-ties are left undecided rather than guessed:
    # acting on a coin flip here either clicks an already-on toggle back off, or
    # reports success on a match that starts with Auto Play disabled.
    if abs(on_conf - off_conf) < AUTOPLAY_MIN_MARGIN:
        return None, on_conf, off_conf

    return ("on" if on_conf > off_conf else "off"), on_conf, off_conf


def ensure_autoplay_enabled():
    """
    Polls until the in-game Auto Play button can be read, then makes sure it is on.
    Returns True if Auto Play is (or becomes) enabled, False if the button never
    became readable within the timeout.
    """
    elapsed = 0.0
    while elapsed <= AUTOPLAY_CHECK_TIMEOUT:
        if config.STOP_REQUESTED:
            return False

        state, on_conf, off_conf = _read_toggle(capture_screen())

        if state == "on":
            print(f"[AutoPlay] Already Auto Playing (on={on_conf:.3f} off={off_conf:.3f}).")
            return True

        if state == "off":
            off = score_template(capture_screen(), config.AUTOPLAY_OFF_BTN)
            if not off:
                time.sleep(AUTOPLAY_CHECK_POLL_INTERVAL)
                elapsed += AUTOPLAY_CHECK_POLL_INTERVAL
                continue
            x, y, _ = off
            print(f"[AutoPlay] Reads as OFF (on={on_conf:.3f} off={off_conf:.3f}) - "
                  f"clicking Auto Play at ({x}, {y}).")

            # SINGLE click - this is a toggle, so clicking twice turns it on and
            # straight back off again. The game remembers the setting globally, so a
            # stray second click doesn't just spoil this match, it leaves every later
            # one running without Auto Play too.
            click_at(x, y)
            time.sleep(0.6)

            after, a_on, a_off = _read_toggle(capture_screen())
            if after == "on":
                print(f"[AutoPlay] Confirmed on (on={a_on:.3f} off={a_off:.3f}).")
                return True
            if after == "off":
                print(f"[AutoPlay] Still reads off (on={a_on:.3f} off={a_off:.3f}) - "
                      f"clicking once more.")
                click_at(x, y)
                time.sleep(0.6)
                final, f_on, f_off = _read_toggle(capture_screen())
                if final == "on":
                    print(f"[AutoPlay] Confirmed on (on={f_on:.3f} off={f_off:.3f}).")
                else:
                    print(f"[AutoPlay] WARNING: still not reading as on "
                          f"(on={f_on:.3f} off={f_off:.3f}) - continuing anyway.")
                return True

            # Undecided after the click. Leaving it alone is the safe branch: the
            # click probably landed, and clicking again on a maybe is exactly the
            # toggle-it-back-off mistake this whole function exists to avoid.
            print(f"[AutoPlay] Can't read the button after clicking "
                  f"(on={a_on:.3f} off={a_off:.3f}) - assuming the click landed.")
            return True

        time.sleep(AUTOPLAY_CHECK_POLL_INTERVAL)
        elapsed += AUTOPLAY_CHECK_POLL_INTERVAL

    print(f"[AutoPlay] Could not read the Auto Play button after {AUTOPLAY_CHECK_TIMEOUT}s.")
    return False
