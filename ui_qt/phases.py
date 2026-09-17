# ui_qt/phases.py
"""
Turns the engine's terse phase codes ("WAITING FOR MATCH END") into the sentence the
RIGHT NOW card shows ("Waiting for the match to end"), plus a tone that picks its
colour and whether the progress bar sweeps.

The engine keeps emitting the same strings it always has - the CustomTkinter window
still shows them raw - so nothing here can change bot behaviour. An unknown code
falls back to a tidied version of itself rather than disappearing.
"""
import re

# tone: "work" (blue, bar sweeping), "play" (green, bar sweeping), "idle",
#       "done" (green, still), "wait" (amber, sweeping), "bad" (red, still)
_EXACT = {
    "IDLE": ("Idle", "idle"),
    "FINISHED": ("Finished", "done"),
    "DETECTING STATE": ("Checking where the game is", "work"),
    "NAVIGATING MENUS": ("Finding the way through the menus", "work"),
    "CONFIRMING STAGE": ("Confirming the stage", "work"),
    "WAITING FOR START GAME": ("Waiting for the Start button", "work"),
    "ANCHORING CAMERA": ("Lining up the camera", "work"),
    "CHECKING AUTO PLAY": ("Turning on the game's Auto Play", "play"),
    "PLAYING TIMELINE": ("Placing units from your recording", "play"),
    "WAITING FOR MATCH END": ("Waiting for the match to end", "play"),
    "TOO MANY DEFEATS": ("Stopped - too many defeats in a row", "bad"),
    "ADVANCING TO NEXT STAGE": ("Moving on to the next stage", "work"),
    "AUTO NEXT COMPLETE": ("Climbed every stage", "done"),
    "REPEATING STAGE": ("Starting the stage again", "work"),
    "RE-ENTERING PORTAL": ("Opening the next portal", "work"),
    "WALKING": ("Walking to the spot", "play"),
    "FISHING": ("Fishing", "play"),
    "PICKING REWARD": ("Picking a reward", "play"),
    "WAITING TO RE-CHECK": ("Waiting for challenge cooldowns", "wait"),
    "NO ROBLOX WINDOW": ("Roblox isn't open", "bad"),
    "TESTING RECORDING": ("Testing your recording", "play"),
    "RETURNING TO LOBBY": ("Heading back to the lobby for the next queue step", "work"),
    # Expeditions (modules/expedition.py)
    "PLACING UNITS": ("Placing units from your recording", "play"),
    "WAITING FOR STAGE": ("Waiting for the stage to load", "work"),
    "PICKING ROUTE": ("Picking a route on the map", "play"),
    "STARTING RUN": ("Starting the expedition", "work"),
    "EXTRACTING": ("Extracting with the loot", "play"),
    "RUNNING EXPEDITION": ("Running the expedition", "play"),
    "WINDOW PIN FAILED": ("Couldn't place the Roblox window", "bad"),
}

TONE_COLORS = {
    "work": "#fb923c",
    "play": "#4ade80",
    "idle": "#9d9db3",
    "done": "#4ade80",
    "wait": "#fbbf24",
    "bad": "#f87171",
}


def _tidy(code):
    text = code.strip().lower()
    return text[:1].upper() + text[1:] if text else "Working"


def describe(code, color=None):
    """(sentence, detail, tone) for one engine phase string."""
    code = (code or "").strip()
    if code in _EXACT:
        text, tone = _EXACT[code]
        return text, "", tone

    m = re.match(r"RESTARTING IN (\d+)s - (.+)", code)
    if m:
        return f"Restarting by itself in {m.group(1)} s", f"Stopped because: {_tidy(m.group(2))}", "wait"

    m = re.match(r"(.+) - CHECK LOG$", code)
    if m:
        return f"Stopped - {_tidy(m.group(1))}", "The Activity feed and the log say what happened.", "bad"

    m = re.match(r"ENTERING (.+)", code)
    if m:
        return f"Entering {m.group(1).title()}", "", "work"

    # Expedition and other module phases: guess a tone from the engine's own colour.
    tone = "work"
    if color:
        c = color.lower()
        if c in ("#ff1744", "#ef4444"):
            tone = "bad"
        elif c in ("#2e7d32", "#22c55e", "#00e676"):
            tone = "play"
        elif c in ("#ffb300", "#fbbf24"):
            tone = "wait"
        elif c in ("#a8a8a8",):
            tone = "work"
    return _tidy(code), "", tone
