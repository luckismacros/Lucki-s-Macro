# ui_qt/theme.py
"""
Design tokens and the application stylesheet for the Qt interface.

The palette continues the existing app (theme.py): near-black with a blue tint,
1px card borders, a user-selectable accent, Segoe UI. What the Qt version adds is
depth and motion - shadows, glows and animated state changes - which the
CustomTkinter version could not draw.

Unlike the old theme, the accent is applied live: changing it in Settings rebuilds
the stylesheet and repaints, no restart.
"""
import settings

FONT_FAMILY = "Segoe UI"

BG_APP = "#0a0a0f"
BG_CARD = "#15151f"
BG_RAISED = "#1a1a26"
BG_SUNKEN = "#0e0e16"
BG_ELEVATED = "#212130"
BORDER = "#26263a"
BORDER_STRONG = "#3f3f55"
SEGMENT = "#3a3a52"

TEXT = "#f2f2f6"
TEXT_SOFT = "#d4d4de"
TEXT_MUTED = "#9d9db3"
TEXT_DIM = "#68687d"

SUCCESS = "#22c55e"
SUCCESS_TEXT = "#4ade80"
SUCCESS_INK = "#052e16"
DANGER = "#ef4444"
DANGER_TEXT = "#f87171"
WARNING = "#fbbf24"
INFO = "#4fc3f7"

RADIUS_CARD = 14
RADIUS_CONTROL = 8

_state = {"accent": "#f97316", "accent_hover": "#ea580c", "on_accent": "#ffffff", "reduce_motion": False}


def load_from_settings(user_settings):
    palette = settings.ACCENT_PALETTES.get(user_settings.get("accent"), settings.ACCENT_PALETTES["orange"])
    _state["accent"] = palette["accent"]
    _state["accent_hover"] = palette["accent_hover"]
    _state["on_accent"] = palette["on_accent"]
    _state["reduce_motion"] = bool(user_settings.get("reduce_motion", False))


def accent():
    return _state["accent"]


def accent_hover():
    return _state["accent_hover"]


def on_accent():
    return _state["on_accent"]


def reduce_motion():
    return _state["reduce_motion"]


def dur(ms):
    """Animation duration, or 0 when Reduce motion is on."""
    return 0 if _state["reduce_motion"] else ms


def rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def stylesheet():
    a = accent()
    return f"""
    QWidget {{
        color: {TEXT};
        font-family: "{FONT_FAMILY}";
        font-size: 13px;
    }}
    QMainWindow, #appRoot {{ background: {BG_APP}; }}
    #sidebar {{ background: {BG_CARD}; border-right: 1px solid {BORDER}; }}
    #strip {{ background: {BG_CARD}; border-top: 1px solid {BORDER}; }}
    #filler {{ background: {BG_APP}; }}

    QLabel {{ background: transparent; }}
    QLabel[role="section"] {{ color: {TEXT_DIM}; font-size: 10px; font-weight: 700; letter-spacing: 1.2px; }}
    QLabel[role="title"] {{ font-size: 13px; font-weight: 600; }}
    QLabel[role="hint"] {{ color: {TEXT_DIM}; font-size: 11.5px; }}
    QLabel[role="muted"] {{ color: {TEXT_MUTED}; font-size: 12.5px; }}
    QLabel[role="big"] {{ font-size: 19px; font-weight: 600; }}
    QLabel[role="stat"] {{ font-size: 27px; font-weight: 700; }}
    QLabel[role="h1"] {{ font-size: 22px; font-weight: 700; }}
    QLabel[role="brand"] {{ font-size: 15px; font-weight: 700; }}

    QFrame#card {{ background: {BG_RAISED}; border: 1px solid {BORDER}; border-radius: {RADIUS_CARD - 2}px; }}
    QFrame#cardSunken {{ background: {BG_SUNKEN}; border: 1px solid {BORDER}; border-radius: {RADIUS_CARD - 2}px; }}
    QFrame#panel {{ background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: {RADIUS_CARD}px; }}
    QFrame#dashed {{ background: transparent; border: 1.5px dashed {BORDER}; border-radius: {RADIUS_CARD}px; }}

    QComboBox {{
        background: {BG_SUNKEN}; border: 1px solid {BORDER}; border-radius: {RADIUS_CONTROL}px;
        padding: 0 12px; min-height: 34px; color: {TEXT};
    }}
    QComboBox:hover {{ border-color: {BORDER_STRONG}; }}
    QComboBox:focus {{ border-color: {a}; }}
    QComboBox::drop-down {{ border: none; width: 26px; }}
    QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
    QComboBox QAbstractItemView {{
        background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px;
        selection-background-color: {rgba(a, 0.22)}; selection-color: {TEXT}; outline: none;
    }}

    QLineEdit, QPlainTextEdit, QTextEdit, QDoubleSpinBox, QSpinBox {{
        background: {BG_SUNKEN}; border: 1px solid {BORDER}; border-radius: {RADIUS_CONTROL}px;
        padding: 6px 10px; color: {TEXT}; selection-background-color: {rgba(a, 0.35)};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus {{ border-color: {a}; }}
    QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
        width: 0; border: none;
    }}
    QPlainTextEdit#logView {{ font-family: Consolas, "Cascadia Mono", monospace; font-size: 11.5px; color: {TEXT_SOFT}; }}

    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 3px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {BORDER_STRONG}; }}
    QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
    QScrollBar:horizontal {{ height: 0; }}

    QSlider::groove:horizontal {{ height: 4px; background: {BG_SUNKEN}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {a}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: #e2e2ea; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
    QSlider::handle:horizontal:hover {{ background: #ffffff; }}
    QSlider:disabled {{ opacity: 0.4; }}

    QToolTip {{
        background: {BG_ELEVATED}; color: {TEXT}; border: 1px solid {BORDER_STRONG};
        border-radius: 6px; padding: 6px 9px; font-size: 12px;
    }}
    QMenu {{ background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px; }}
    QMenu::item {{ padding: 6px 14px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {rgba(a, 0.22)}; }}

    QListWidget {{ background: transparent; border: none; outline: none; }}
    QListWidget::item {{ border-radius: 8px; padding: 0; margin: 2px 0; }}
    QListWidget::item:selected {{ background: {rgba(a, 0.16)}; }}

    QPushButton#chip {{
        background: {BG_SUNKEN}; border: 1px solid {BORDER}; border-radius: 13px;
        padding: 4px 11px; font-size: 12px; color: {TEXT_MUTED}; min-height: 18px;
    }}
    QPushButton#chip:hover {{ border-color: {BORDER_STRONG}; color: {TEXT}; }}
    QPushButton#chip:checked {{
        background: {rgba(a, 0.18)}; border: 1px solid {a}; color: {TEXT}; font-weight: 600;
    }}
    QPushButton#segBtn {{ background: transparent; border: none; color: {TEXT_MUTED}; font-size: 12px; padding: 0 8px; }}
    QPushButton#segBtn[selected="true"] {{ color: {TEXT}; font-weight: 600; }}
    QPushButton#segBtn:disabled {{ color: {TEXT_DIM}; }}
    QDialog {{ background: {BG_CARD}; }}
    """
