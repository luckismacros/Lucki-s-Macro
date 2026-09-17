# modules/challenge_links.py
"""
Persistent per-challenge-slot preset links: which Story map/act/preset each challenge
slot (regular_1, regular_2, regular_3, daily, weekly) should reuse, since challenges
run on Story maps but the act shown for each challenge is randomized by the game and
can't be predicted - the player sets/updates these links manually as needed.
"""
import json
import os
import config

LINKS_FILE = "challenge_links.json"

def _load():
    if not os.path.exists(LINKS_FILE):
        return {}
    try:
        with open(LINKS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

def _save(links):
    with open(LINKS_FILE, "w") as f:
        json.dump(links, f, indent=4)

def get_link(slot_key):
    """Returns {"map": map_key|None, "act": act_key|None, "preset": preset_name}."""
    links = _load()
    return links.get(slot_key, {"map": None, "act": None, "preset": config.AUTO_PLAY_PRESET_NAME})

def set_link(slot_key, map_key, act_key, preset_name):
    links = _load()
    links[slot_key] = {"map": map_key, "act": act_key, "preset": preset_name}
    _save(links)
