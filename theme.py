# theme.py
"""
Shared design tokens for every CustomTkinter surface in this app (gui.py,
modules/preset_share.py's dialogs). Living in one place instead of being
redefined per-file is the point: colors, fonts, and corner radii drift apart the
moment they're copy-pasted per call site.

Palette: a saturated accent color (user-selectable - see settings.py) on a
near-black, slightly blue-tinted base with real 1px card borders for depth. This
replaced an earlier flat near-white/monochrome theme with 3-4px corner radii,
which read as unstyled widget defaults rather than a designed app. Start stays
green and Stop/Delete stay red on purpose - those are safety-critical signals,
not decoration - so every accent option is chosen to sit clearly apart from both.

The accent is read from settings.json (via settings.py) once, at import time.
Changing it in the Settings dialog writes the new choice but only takes effect
on next launch, since these are plain constants baked into each widget's style
dict at construction time - there's no live re-theming pass over the whole tree.

CTkSegmentedButton constraint: its `text_color` applies to BOTH the selected and
unselected segment - there's no separate per-state text color in this CTk version
(confirmed by reading its source: only `fg_color` toggles per segment, `text_color`
is one shared value). Since the accent is user-selectable and some options (cyan,
amber, violet, mono) need DARK text for contrast while others (indigo) need
LIGHT text, a segmented button's selected state can't just be ACCENT - it stays a
palette-independent lit-up neutral instead, so light text stays legible against
both states no matter which accent is chosen.

Font: CTk's own bundled "Roboto" renders thin at the small sizes used here, hard
to read at a glance. "Segoe UI" is Windows' native UI font (this app is Windows-
only already - pydirectinput/pywin32 - so it's always present) and reads
noticeably heavier/clearer at the same size without changing any layout.
"""
import settings as _settings

FONT_FAMILY = "Segoe UI"

_user_settings = _settings.load()
_palette = _settings.ACCENT_PALETTES.get(_user_settings["accent"], _settings.ACCENT_PALETTES["indigo"])

ACCENT = _palette["accent"]
ACCENT_HOVER = _palette["accent_hover"]
ACCENT_TEXT = _palette["on_accent"]  # text/icons drawn on top of ACCENT-colored surfaces

BG_APP = "#0a0a0f"       # root window - the "gap" color visible behind every floating card

# Registered as the window's transparent colour key (see gui.BotGUI._build_game_slot):
# every pixel painted in EXACTLY this colour is rendered fully transparent and
# click-through, which is how the docked game shows through the GUI. Deliberately a
# colour nothing in the palette above would ever legitimately use - anything painted
# in it anywhere in this window would silently become a hole.
GAME_SLOT_KEY_COLOR = "#ff00fe"
BG_CARD = "#15151f"      # sidebar / content / status-bar panels, dialog backgrounds
BG_SUNKEN = "#0e0e16"    # "inset" fill for dropdowns, entries, switch tracks, segmented buttons, the log console
BG_ELEVATED = "#212130"  # hover backgrounds

BORDER = "#26263a"       # 1px outline on floating cards - depth without needing real shadows

TEXT_PRIMARY = "#f2f2f6"
TEXT_MUTED = "#9d9db3"
TEXT_DIM = "#68687d"

SUCCESS = "#22c55e"
SUCCESS_HOVER = "#16a34a"
DANGER = "#ef4444"
DANGER_HOVER = "#dc2626"

# Segmented-button-only selected state - see the CTkSegmentedButton note above for
# why this can't just be ACCENT. A cool neutral so it still feels part of the
# same palette rather than generic gray.
SEGMENT_SELECTED = "#3a3a52"
SEGMENT_SELECTED_HOVER = "#48486a"

RADIUS_CARD = 14    # sidebar/content/status-bar panels, the log console, dialogs
RADIUS_CONTROL = 8  # buttons, dropdowns, segmented buttons, switches

# Shared style kwargs, spread into each widget constructor - one definition per
# widget type instead of repeating the same colors at every call site.
CARD_STYLE = dict(
    corner_radius=RADIUS_CARD, fg_color=BG_CARD, border_width=1, border_color=BORDER,
)
SCROLLBAR_STYLE = dict(
    scrollbar_fg_color=BG_SUNKEN, scrollbar_button_color=BORDER, scrollbar_button_hover_color=BG_ELEVATED,
)
DROPDOWN_STYLE = dict(
    corner_radius=RADIUS_CONTROL, height=34,
    # Flat: button_color matches fg_color so the whole control reads as ONE box
    # with a small arrow, not a text field with a separate colored button stuck on.
    fg_color=BG_SUNKEN, button_color=BG_SUNKEN, button_hover_color=BG_ELEVATED,
    text_color=TEXT_PRIMARY, text_color_disabled=TEXT_DIM,
    dropdown_fg_color=BG_CARD, dropdown_hover_color=BG_ELEVATED, dropdown_text_color=TEXT_PRIMARY,
)
SWITCH_STYLE = dict(
    fg_color=BG_SUNKEN, progress_color=ACCENT,
    # A light neutral knob (not a hard white, not near-black) stays visible against
    # BOTH the dark off-track and every possible ACCENT on-track color, since
    # CTkSwitch only allows one knob color regardless of state or palette.
    button_color="#e2e2ea", button_hover_color="#ffffff",
    text_color=TEXT_PRIMARY, text_color_disabled=TEXT_DIM,
)
SEGMENTED_STYLE = dict(
    corner_radius=RADIUS_CONTROL,
    fg_color=BG_SUNKEN, selected_color=SEGMENT_SELECTED, selected_hover_color=SEGMENT_SELECTED_HOVER,
    unselected_color=BG_SUNKEN, unselected_hover_color=BG_ELEVATED,
    text_color=TEXT_PRIMARY, text_color_disabled=TEXT_DIM,
)
ACCENT_BUTTON_STYLE = dict(
    corner_radius=RADIUS_CONTROL, height=34, fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT,
    # hover=False hands hover/press feedback to ui_motion.wire_button_feel() instead
    # of CTk's own instant color swap - every call site using this style is expected
    # to call wire_button_feel(btn) right after constructing it.
    hover=False,
)
CHECKBOX_STYLE = dict(
    corner_radius=5, fg_color=ACCENT, hover_color=ACCENT_HOVER, checkmark_color=ACCENT_TEXT,
    border_color=TEXT_MUTED, text_color=TEXT_PRIMARY, text_color_disabled=TEXT_DIM,
)
# CTkInputDialog's entry/button chrome - used by every "type a name" prompt
# (New/Rename preset, Export/Import naming) so they don't fall back to default blue.
INPUT_DIALOG_STYLE = dict(
    fg_color=BG_CARD, button_fg_color=ACCENT, button_hover_color=ACCENT_HOVER,
    button_text_color=ACCENT_TEXT, entry_fg_color=BG_SUNKEN, entry_border_color=ACCENT,
    entry_text_color=TEXT_PRIMARY, text_color=TEXT_PRIMARY,
)
