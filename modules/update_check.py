# modules/update_check.py
"""
Checks GitHub Releases for a newer build than this one (config.APP_VERSION).

Tier 1 only, by design: this never downloads or applies anything, it just tells the
player a newer version exists and links to the release page for them to grab it
themselves. Follows the same rules modules/notify.py does for the same reason - a
network call must never be the thing that blocks startup or breaks a run: fails
silent, never raises, never blocks the caller.

GITHUB_REPO must point at a PUBLIC repo - a private one needs a token this app
doesn't (and shouldn't) carry, the same reasoning settings.py keeps the Discord
webhook out of anything that ships.
"""
import json
import urllib.request

import config

# "owner/repo" - set once the GitHub repo exists. Left unset (no "/") until then, so
# check_for_update() is a guaranteed no-op rather than an error against a 404.
GITHUB_REPO = ""

_TIMEOUT = 6.0


def _parse_version(v):
    """"1.14" -> (1, 14) for a real numeric comparison - a plain string compare would
    sort "1.9" after "1.10"."""
    parts = []
    for p in (v or "").strip().lstrip("vV").split("."):
        digits = "".join(c for c in p if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def check_for_update():
    """
    Returns {"version": "1.15", "url": "<release page>", "notes": "<release body>"}
    if a newer release exists, else None. Never raises - offline, GitHub down, or
    GITHUB_REPO not set yet all just mean "no update", not an error the player sees.
    """
    if "/" not in GITHUB_REPO:
        return None
    try:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "LuckisMacro-UpdateCheck"},
        )
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latest = str(data.get("tag_name") or "").strip()
        if not latest or _parse_version(latest) <= _parse_version(config.APP_VERSION):
            return None
        return {"version": latest.lstrip("vV"), "url": data.get("html_url") or "",
                "notes": (data.get("body") or "").strip()}
    except Exception:
        return None
