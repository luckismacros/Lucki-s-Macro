# modules/update_check.py
"""
Checks GitHub Releases for a newer build than this one (config.APP_VERSION).

This only finds out what's newer; modules/self_update.py downloads and installs it.
Follows the same rules modules/notify.py does for the same reason - a network call must
never be the thing that blocks startup or breaks a run: fails silent, never raises,
never blocks the caller.

The version is read from the release's tag AND from its zip's name
(Luckis_Macro_v3.6.zip), whichever is higher: the release that shipped 3.6 was still
tagged v.3.3 (one release edited over and over), so a tag-only check never saw it.

GITHUB_REPO must point at a PUBLIC repo - a private one needs a token this app
doesn't (and shouldn't) carry, the same reasoning settings.py keeps the Discord
webhook out of anything that ships.
"""
import json
import re
import urllib.request

import config

# "owner/repo" - the public repo Releases are published to.
GITHUB_REPO = "luckismacros/Lucki-s-Macro"

_TIMEOUT = 6.0


def _parse_version(v):
    """
    "1.14" -> (1, 14), "v1.14" -> (1, 14), "v.1.13" -> (1, 13) for a real numeric
    comparison - a plain string compare would sort "1.9" after "1.10".

    Strips the WHOLE leading non-digit run (not just a "v"/"V") before splitting -
    this repo's own tags aren't consistent about a dot after the "v" (v1.11 vs
    v.1.13 both exist), and stripping only "vV" would leave that dot as an empty
    leading segment, silently prepending a 0 and throwing off the comparison for
    any version where it mattered.
    """
    s = re.sub(r"^[^\d]*", "", (v or "").strip())
    parts = []
    for p in s.split("."):
        digits = "".join(c for c in p if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def check_for_update():
    """
    Returns {"version": "1.15", "url": "<release page>", "notes": "<release body>"}
    if a newer release exists, else None. Never raises - offline, GitHub down, or a
    blanked-out GITHUB_REPO all just mean "no update", not an error the player sees.
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
        from modules.self_update import ASSET_RE
        latest = str(data.get("tag_name") or "").strip()
        asset = None
        for a in data.get("assets") or []:
            m = ASSET_RE.match(a.get("name") or "")
            if m and (asset is None or _parse_version(m.group(1)) > _parse_version(asset[0])):
                asset = (m.group(1), a)
        if asset and _parse_version(asset[0]) >= _parse_version(latest):
            latest = asset[0]
        if not latest or _parse_version(latest) <= _parse_version(config.APP_VERSION):
            return None
        # Displayed to the player, so this is cleaned up with the same leading-junk
        # strip as the comparison itself - "v.1.13" should read "1.13", not ".1.13".
        display = re.sub(r"^[^\d]*", "", latest) or latest
        return {"version": display, "url": data.get("html_url") or "",
                "notes": (data.get("body") or "").strip(),
                "download_url": asset[1].get("browser_download_url") if asset else None,
                "size": asset[1].get("size") if asset else 0}
    except Exception:
        return None
