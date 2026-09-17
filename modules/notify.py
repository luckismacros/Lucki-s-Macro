# modules/notify.py
"""
Discord notifications for a run nobody is sitting and watching.

A macro that farms overnight fails silently by definition: Roblox crashes at 03:00,
the bot stops cleanly, and the first anyone knows is eight wasted hours later. This
sends the handful of moments worth waking up for to a Discord channel.

Three rules shape everything here, in order:

1. It must never block the bot. Every send goes through a background worker, so a
   Discord outage costs a queued message and nothing else - the alternative is the
   gameplay loop sitting in a socket timeout while a match plays itself out.
2. It must never break a run. Every failure path is swallowed. A notifier that takes
   the run down with it is worse than no notifier.
3. The webhook URL must never appear anywhere it could be shared. It is a bearer
   credential - anyone holding it can post to that channel - so it is never logged,
   never printed, and never included in an error message.

Messages are Discord embeds rather than plain text - a title, a coloured accent bar,
optional structured fields (win rate, elapsed time, ...) and optionally one attached
screenshot, so a phone notification is readable at a glance without opening the app,
and the one time it IS opened, there's a picture of exactly what the bot was looking
at rather than a wall of numbers to reconstruct the scene from by hand.
"""
import json
import queue
import threading
import time
import uuid
import urllib.error
import urllib.request

# Discord allows roughly 5 requests/second per webhook before it starts rejecting.
# One per second is far below that and still far faster than events are produced.
_MIN_SECONDS_BETWEEN_SENDS = 1.0

# A burst big enough to mean something is wrong (a crash loop retrying, say) should
# not turn into hundreds of queued messages that arrive long after they mattered.
_MAX_QUEUE = 40

_TIMEOUT = 12.0  # a little more than the plain-JSON path's 8s - a multipart image
                 # upload is a bigger request and deserves more room before it's
                 # treated as unreachable rather than merely slow.

# Discord's own per-file attachment cap on a webhook with no server boost is 8MB;
# screenshots here are compressed JPEGs well under that (a 1600x900 game frame at
# quality 82 runs 150-400KB), but the cap is enforced here too so a future change
# that started passing something huge fails soft (screenshot silently dropped)
# instead of Discord rejecting the whole message, text included.
_MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024

_webhook = ""
_categories = {}
_queue = None
_worker = None
_lock = threading.Lock()

# One accent emoji per (category, good) combination, used to prefix the embed title
# so a phone notification banner - which shows the title but not the colour bar -
# still reads at a glance as good/bad/neutral without opening the app.
_EMOJI = {
    ("lifecycle", True): "✅",   # started/finished cleanly
    ("lifecycle", False): "\U0001F6D1",
    ("lifecycle", None): "▶️",
    ("problems", True): "✅",
    ("problems", False): "\U0001F6A8",  # rotating light - a genuine problem
    ("problems", None): "⚠️",
    ("milestones", True): "\U0001F3C6",
    ("milestones", False): "❗",
    ("milestones", None): "✨",
    ("every_match", True): "\U0001F3C5",
    ("every_match", False): "\U0001F480",
    ("every_match", None): "⚔️",
}


def configure(webhook_url, categories):
    """
    Points the notifier at a channel. Safe to call repeatedly (the Settings dialog does).

    `categories` maps category name -> bool, straight from settings, so turning a
    category off in the GUI silently drops those messages at send() with no caller
    needing to know.
    """
    global _webhook, _categories, _queue, _worker
    with _lock:
        _webhook = (webhook_url or "").strip()
        _categories = dict(categories or {})

        if not _webhook:
            return

        if _queue is None:
            _queue = queue.Queue(maxsize=_MAX_QUEUE)
        if _worker is None or not _worker.is_alive():
            # Daemon so a stuck send can never keep the app from closing.
            _worker = threading.Thread(target=_run_worker, name="discord-notify", daemon=True)
            _worker.start()


def is_configured():
    return bool(_webhook)


def send(text, category="lifecycle", title=None, good=None, fields=None,
         image_bytes=None, image_name="screenshot.jpg"):
    """
    Queues one message. Returns True if it was accepted (not that it was delivered).

    text          the embed's main body. Markdown works (Discord renders **bold**,
                  `code`, etc.) the same as typing it in the channel by hand.
    category      which of the Settings dialog's toggles gates this message -
                  "lifecycle", "problems", "milestones" or "every_match".
    title         short embed title. An emoji is prepended automatically (see
                  _EMOJI) unless title already starts with one, so callers don't
                  have to hand-pick an icon for every call site.
    good          tints the accent bar - True green, False red, None blurple -
                  and, combined with category, picks the auto-prepended emoji.
    fields        optional list of (name, value) or (name, value, inline) tuples,
                  rendered as Discord's side-by-side embed fields - use this for
                  structured numbers (elapsed time, matches, win rate) instead of
                  folding them into `text`, which reads better for the sentence
                  parts of the message.
    image_bytes   optional already-encoded image (e.g. from health.jpg_bytes()) to
                  attach and display inline in the embed. None sends text-only.
    image_name    filename Discord shows/serves the attachment as; the extension
                  should match image_bytes' actual encoding.
    """
    if not _webhook or not text:
        return False
    if not _categories.get(category, True):
        return False

    if image_bytes is not None and len(image_bytes) > _MAX_ATTACHMENT_BYTES:
        image_bytes = None  # drop rather than let Discord reject the whole message

    emoji = _EMOJI.get((category, good))
    payload = _build_payload(text, title, good, fields, emoji,
                             attach_image=image_bytes is not None, image_name=image_name)
    try:
        _queue.put_nowait((payload, image_bytes, image_name))
        return True
    except (queue.Full, AttributeError):
        # Dropped deliberately: see _MAX_QUEUE. Losing the 41st message of a burst is
        # better than delivering a backlog nobody can act on.
        return False


def flush(timeout=6.0):
    """Waits briefly for queued messages to go out, for use on shutdown."""
    if _queue is None:
        return
    deadline = time.time() + timeout
    while time.time() < deadline and not _queue.empty():
        time.sleep(0.1)


def _build_payload(text, title, good, fields, emoji, attach_image, image_name):
    colour = 0x2ECC71 if good is True else (0xE74C3C if good is False else 0x5865F2)
    embed = {"description": str(text)[:3900], "color": colour}

    if title:
        title = str(title)[:240]
        if emoji and not title.startswith(emoji):
            title = f"{emoji} {title}"
        embed["title"] = title

    if fields:
        embed_fields = []
        for f in fields:
            name, value = f[0], f[1]
            inline = f[2] if len(f) > 2 else True
            embed_fields.append({"name": str(name)[:256], "value": str(value)[:1024], "inline": bool(inline)})
        embed["fields"] = embed_fields[:25]  # Discord's own per-embed field cap

    if attach_image:
        embed["image"] = {"url": f"attachment://{image_name}"}

    embed["footer"] = {"text": f"Lucki's Macro  •  {time.strftime('%Y-%m-%d %H:%M:%S')}"}
    # Discord shows whichever name the webhook was CREATED with unless a message
    # overrides it - that's set on Discord's own side, in the channel's webhook
    # settings, not in anything this app controls. Sending "username" here overrides
    # it for every message this app posts, so a webhook someone set up years ago
    # under an old name still shows the app's current one without them having to
    # find and edit it on Discord.
    return {"username": "Lucki's Macro", "embeds": [embed]}


def _run_worker():
    last_sent = 0.0
    while True:
        try:
            payload, image_bytes, image_name = _queue.get()
        except Exception:
            return

        gap = time.time() - last_sent
        if gap < _MIN_SECONDS_BETWEEN_SENDS:
            time.sleep(_MIN_SECONDS_BETWEEN_SENDS - gap)

        _post(payload, image_bytes, image_name)
        last_sent = time.time()


def _encode_multipart(payload, image_bytes, image_name):
    """
    Builds a multipart/form-data body by hand: Discord's webhook API takes a
    `payload_json` field plus one `files[N]` field per attachment, and the only
    external HTTP dependency this project has anywhere is urllib - pulling in
    `requests` (which isn't in requirements.txt) for one multipart POST would be a
    heavier addition than just building the ~10 lines of boundary-delimited body by
    hand. Returns (body_bytes, content_type_header).
    """
    boundary = uuid.uuid4().hex
    parts = []

    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="payload_json"\r\n'
        f"Content-Type: application/json\r\n\r\n"
        f"{json.dumps(payload)}\r\n".encode("utf-8")
    )
    parts.append(
        (f"--{boundary}\r\n"
         f'Content-Disposition: form-data; name="files[0]"; filename="{image_name}"\r\n'
         f"Content-Type: image/jpeg\r\n\r\n").encode("utf-8")
        + image_bytes + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))

    body = b"".join(parts)
    return body, f"multipart/form-data; boundary={boundary}"


def _post(payload, image_bytes=None, image_name="screenshot.jpg"):
    """
    One HTTP POST, with every failure swallowed.

    Errors are reported without the URL and without the exception's own text, because
    urllib puts the full request URL into HTTPError's string form - printing it would
    leak the webhook into the log file that gets pasted into chats for debugging,
    which is exactly how this credential would escape.
    """
    try:
        if image_bytes:
            data, content_type = _encode_multipart(payload, image_bytes, image_name)
        else:
            data, content_type = json.dumps(payload).encode("utf-8"), "application/json"

        request = urllib.request.Request(
            _webhook, data=data,
            headers={"Content-Type": content_type, "User-Agent": "MacroSlop/1.0"},
        )
        with urllib.request.urlopen(request, timeout=_TIMEOUT):
            pass
    except urllib.error.HTTPError as e:
        print(f"[notify] Discord rejected the message (HTTP {e.code}). "
              f"If this is 401/404 the webhook URL is wrong or was deleted.")
        # A malformed/oversized image is the one failure worth retrying without it -
        # everything else (bad webhook, rate limit) would fail the retry too. The
        # embed's own "image" field is stripped first, or Discord would render a
        # broken-image placeholder pointing at an attachment that no longer exists
        # in this retry's (image-free) request.
        if image_bytes and e.code in (400, 413):
            try:
                for embed in payload.get("embeds", []):
                    embed.pop("image", None)
                data = json.dumps(payload).encode("utf-8")
                request = urllib.request.Request(
                    _webhook, data=data,
                    headers={"Content-Type": "application/json", "User-Agent": "MacroSlop/1.0"},
                )
                with urllib.request.urlopen(request, timeout=_TIMEOUT):
                    pass
            except Exception:
                pass
    except Exception:
        print("[notify] Could not reach Discord - message dropped, run continues.")
