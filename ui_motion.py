# ui_motion.py
"""
Small animation helpers layered on top of theme.py's static style tokens.

CustomTkinter's own hover handling swaps fg_color -> hover_color instantly on
<Enter>/<Leave> - that instant snap (plus the total absence of any press
feedback) is a big part of why a plain CTk button reads as an unstyled widget
rather than a designed one, even once the colors themselves are right. This
module replaces that instant swap with a short color-interpolated transition,
and adds a darker "pressed" state on mouse-down that most CTk apps skip.

wire_button_feel() only works on buttons with a real solid hex fg_color/
hover_color (e.g. the Start/Stop/Record buttons and every ACCENT_BUTTON_STYLE
button) - it no-ops for fg_color="transparent" buttons (the sidebar nav/
Settings entries), since "transparent" has no RGB to interpolate against and
those already get a strong enough state change from the pill background swap
in _apply_nav_highlight. Call AFTER constructing the button, and construct it
with hover=False so CTk's own instant handler doesn't fight this one.
"""
import re

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _hex_to_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c))) for c in rgb)


def _lerp_hex(color_a, color_b, t):
    a, b = _hex_to_rgb(color_a), _hex_to_rgb(color_b)
    return _rgb_to_hex(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))


def _darken(color, amount=0.18):
    return _lerp_hex(color, "#000000", amount)


def wire_button_feel(button, duration_ms=110, steps=8, attr="fg_color"):
    """Adds an animated hover transition + a darker press state to `button`.
    Safe to call on any CTkButton; silently does nothing if its fg_color isn't
    a plain hex color (e.g. "transparent" nav buttons)."""
    base_color = button.cget(attr)
    hover_color = button.cget("hover_color")
    if not (isinstance(base_color, str) and isinstance(hover_color, str)
            and _HEX_RE.match(base_color) and _HEX_RE.match(hover_color)):
        return

    press_color = _darken(hover_color)
    state = {"job": None}

    def _cancel():
        if state["job"] is not None:
            try:
                button.after_cancel(state["job"])
            except Exception:
                pass
            state["job"] = None

    def _step(current, target, i):
        if button.cget("state") == "disabled":
            return
        t = i / steps
        try:
            button.configure(**{attr: _lerp_hex(current, target, t)})
        except Exception:
            return  # widget was destroyed mid-animation
        if i < steps:
            state["job"] = button.after(max(1, duration_ms // steps), lambda: _step(current, target, i + 1))

    def _go(target):
        if button.cget("state") == "disabled":
            return
        _cancel()
        current = button.cget(attr)
        if not (isinstance(current, str) and _HEX_RE.match(current)):
            current = base_color
        _step(current, target, 0)

    button.bind("<Enter>", lambda e: _go(hover_color), add="+")
    button.bind("<Leave>", lambda e: _go(base_color), add="+")
    button.bind("<ButtonPress-1>", lambda e: _go(press_color), add="+")
    button.bind("<ButtonRelease-1>", lambda e: _go(hover_color), add="+")
