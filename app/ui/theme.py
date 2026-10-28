"""Design tokens, light / dark palettes and the generated style sheet.

Everything visual is defined here once:

- ``SPACE``   4 / 8-pt spacing scale used by the layouts
- ``RADIUS``  corner radii (cards 14, boxes 12, controls 8, chips 6)
- ``TYPE``    the type scale (px, weight)
- ``LIGHT`` / ``DARK``  colour tokens; both palettes have exactly the same keys

``build_qss(tokens)`` turns a palette into the application style sheet and
``ThemeManager`` applies it live (跟随系统 / 浅色 / 深色) without a restart.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPalette

MODE_SYSTEM = "system"
MODE_LIGHT = "light"
MODE_DARK = "dark"
MODES = (MODE_SYSTEM, MODE_LIGHT, MODE_DARK)
MODE_LABELS = {MODE_SYSTEM: "跟随系统", MODE_LIGHT: "浅色", MODE_DARK: "深色"}

SPACE = {"xxs": 2, "xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 20, "xxl": 24, "xxxl": 32}
RADIUS = {"xs": 4, "sm": 6, "md": 8, "lg": 12, "xl": 14, "pill": 999}

# (pixel size, weight)
TYPE = {
    "display": (22, 700),   # window headline
    "title": (16, 700),     # card titles, preview title
    "heading": (14, 600),   # file name, row title, banner title
    "body": (13, 400),
    "caption": (12, 400),   # helper text, meta lines
    "micro": (11, 600),     # chips, badges
}

UI_FONTS = ('"Microsoft YaHei UI"', '"Segoe UI Variable Text"', '"Segoe UI"', '"Microsoft YaHei"',
            '"PingFang SC"', '"Noto Sans CJK SC"', '"Segoe UI Symbol"', "sans-serif")
NUMBER_FONTS = ('"Segoe UI Variable Display"', '"Segoe UI Variable Text"', '"Segoe UI"',
                '"Microsoft YaHei UI"', '"Segoe UI Symbol"', "sans-serif")
ICON_FONTS = ('"Segoe Fluent Icons"', '"Segoe MDL2 Assets"')
# Preview: an ASCII monospace face; CJK labels are drawn in CJK_FONTS and squeezed onto the grid.
MONO_FAMILIES = ["Cascadia Mono", "Consolas", "Lucida Console", "Courier New"]
CJK_FAMILIES = ["Microsoft YaHei UI", "Microsoft YaHei", "DengXian", "SimHei", "PingFang SC", "Noto Sans CJK SC"]
SYMBOL_FAMILIES = ["Segoe UI Symbol", "Segoe UI", "Microsoft YaHei UI"]

LIGHT = {
    "name": "light",
    "bg": "#f3f4f8",
    "surface": "#ffffff",
    "surface_alt": "#f7f8fb",
    "surface_hover": "#f1f3f8",
    "surface_pressed": "#e8ebf2",
    "track": "#eceef4",
    "border": "#e3e6ee",
    "border_strong": "#cfd5df",
    "divider": "#eceef3",
    "text": "#1b2130",
    "text_2": "#465063",
    "muted": "#6b7487",
    "faint": "#9aa2b1",
    "disabled": "#aab0bd",
    "disabled_bg": "#f1f2f6",
    "accent": "#4263eb",
    "accent_hover": "#3b5bdb",
    "accent_pressed": "#364fc7",
    "accent_soft": "#edf2ff",
    "accent_soft_hover": "#e1e8ff",
    "accent_line": "#bac8ff",
    "primary": "#4c6ef5",
    "primary_hover": "#4263eb",
    "primary_pressed": "#3b5bdb",
    "primary_disabled": "#c5cff6",
    "primary_disabled_text": "#f4f6fe",
    "on_primary": "#ffffff",
    "focus": "#4c6ef5",
    "focus_on_primary": "#1b2130",
    "success": "#2f9e44",
    "success_text": "#2b8a3e",
    "success_soft": "#ebfbee",
    "success_line": "#b2f2bb",
    "warn": "#e8590c",
    "warn_text": "#b3500b",
    "warn_soft": "#fff4e6",
    "warn_line": "#ffd8a8",
    "danger": "#e03131",
    "danger_text": "#c92a2a",
    "danger_soft": "#fff5f5",
    "danger_soft_hover": "#ffe3e3",
    "danger_line": "#ffc9c9",
    "selection": "#d0dbff",
    "selection_text": "#1b2130",
    "scroll": "#cdd2dc",
    "scroll_hover": "#b3bac7",
    "tooltip": "#1f2533",
    "tooltip_text": "#f5f7fb",
    "toast": "#1f2533",
    "toast_text": "#f5f7fb",
    "toast_muted": "#b9c0cd",
    "toast_border": "#1f2533",
    "toast_action": "#9db1ff",
    "toast_success": "#69db7c",
    "toast_error": "#ff8787",
    "shadow": "#101828",
    "shadow_alpha": 0.075,
    "drop_hover": "#f6f8ff",
    # keyboard-score preview
    "score_bg": "#fbfcfe",
    "score_bar": "#b4bccb",
    "score_melody": "#3b5bdb",
    "score_accomp": "#0b7285",
    "score_mark": "#c2410c",
    "score_rest": "#98a0b0",
    "score_comment": "#7a8294",
    "score_section": "#3b5bdb",
    "score_number": "#98a0b0",
    "score_grace": "#5c667a",
}

DARK = {
    "name": "dark",
    "bg": "#15171c",
    "surface": "#1e2127",
    "surface_alt": "#24282f",
    "surface_hover": "#2a2f37",
    "surface_pressed": "#323843",
    "track": "#17191e",
    "border": "#2e333c",
    "border_strong": "#3c424d",
    "divider": "#2a2e36",
    "text": "#e7e9ee",
    "text_2": "#b8bfcb",
    "muted": "#8e96a5",
    "faint": "#6c7483",
    "disabled": "#5b626f",
    "disabled_bg": "#22252b",
    "accent": "#91a7ff",
    "accent_hover": "#a5b7ff",
    "accent_pressed": "#7c93f5",
    "accent_soft": "#262d4a",
    "accent_soft_hover": "#2e375b",
    "accent_line": "#4a5ca8",
    "primary": "#4c6ef5",
    "primary_hover": "#5c7cfa",
    "primary_pressed": "#4263eb",
    "primary_disabled": "#2c3557",
    "primary_disabled_text": "#7f8bb3",
    "on_primary": "#ffffff",
    "focus": "#91a7ff",
    "focus_on_primary": "#ffffff",
    "success": "#40c057",
    "success_text": "#69db7c",
    "success_soft": "#1b2c21",
    "success_line": "#2b5235",
    "warn": "#fd7e14",
    "warn_text": "#ffa94d",
    "warn_soft": "#30261a",
    "warn_line": "#5a4122",
    "danger": "#fa5252",
    "danger_text": "#ff8787",
    "danger_soft": "#351e21",
    "danger_soft_hover": "#43252a",
    "danger_line": "#66303a",
    "selection": "#33427a",
    "selection_text": "#f3f5fa",
    "scroll": "#3a404b",
    "scroll_hover": "#4b5260",
    "tooltip": "#2c313a",
    "tooltip_text": "#e7e9ee",
    "toast": "#2c313a",
    "toast_text": "#eef0f4",
    "toast_muted": "#a3abb9",
    "toast_border": "#3c424d",
    "toast_action": "#a5b7ff",
    "toast_success": "#69db7c",
    "toast_error": "#ff8787",
    "shadow": "#000000",
    "shadow_alpha": 0.30,
    "drop_hover": "#232838",
    "score_bg": "#191c21",
    "score_bar": "#4c5461",
    "score_melody": "#a5b7ff",
    "score_accomp": "#66d9e8",
    "score_mark": "#ffa94d",
    "score_rest": "#6f7786",
    "score_comment": "#7f8796",
    "score_section": "#a5b7ff",
    "score_number": "#6f7786",
    "score_grace": "#aab2c0",
}

PALETTES = {"light": LIGHT, "dark": DARK}


def tokens_for(name: str) -> dict:
    return DARK if name == "dark" else LIGHT


def _fonts(stack) -> str:
    return ", ".join(stack)


def _t(key: str) -> str:
    size, weight = TYPE[key]
    return f"font-size: {size}px; font-weight: {weight};"


def build_qss(c: dict) -> str:
    """The whole application style sheet, generated from one palette of tokens."""
    r = RADIUS
    ui = _fonts(UI_FONTS)
    num = _fonts(NUMBER_FONTS)
    icon = _fonts(ICON_FONTS)
    mono = _fonts(f'"{f}"' for f in MONO_FAMILIES)
    return f"""
/* generated by app/ui/theme.py ({c['name']}) */
QWidget {{ font-family: {ui}; font-size: {TYPE['body'][0]}px; color: {c['text']}; }}
QMainWindow {{ background: {c['bg']}; }}
QToolTip {{
  background: {c['tooltip']}; color: {c['tooltip_text']}; border: 1px solid {c['tooltip']};
  padding: 5px 8px; border-radius: {r['sm']}px; font-size: {TYPE['caption'][0]}px;
}}
QLabel {{ background: transparent; }}
QLabel[glyph="true"] {{ font-family: {icon}; font-weight: 400; }}

/* ---------- typography ---------- */
QLabel#hero {{ {_t('display')} color: {c['text']}; }}
QLabel#subtitle {{ color: {c['muted']}; font-size: {TYPE['body'][0]}px; }}
QLabel#cardTitle {{ {_t('title')} }}
QLabel#sectionLabel {{ color: {c['text']}; font-weight: 600; }}
QLabel#muted {{ color: {c['muted']}; }}
QLabel#helper {{ color: {c['muted']}; font-size: {TYPE['caption'][0]}px; }}
QLabel#faint {{ color: {c['faint']}; font-size: {TYPE['caption'][0]}px; }}
QLabel#note {{ color: {c['warn_text']}; font-size: {TYPE['caption'][0]}px; }}
QLabel#error {{ color: {c['danger_text']}; }}
QLabel#gpuNote {{
  color: {c['warn_text']}; font-size: {TYPE['caption'][0]}px; background: {c['warn_soft']};
  border: 1px solid {c['warn_line']}; border-radius: {r['md']}px; padding: 8px 10px;
}}
QLabel#appBadge {{ background: transparent; }}

/* ---------- cards & boxes ---------- */
QFrame#card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: {r['xl']}px; }}
QFrame#divider {{ background: {c['divider']}; border: none; max-height: 1px; min-height: 1px; }}
QFrame#settings {{ background: {c['surface_alt']}; border: 1px solid {c['border']}; border-radius: {r['lg']}px; }}
QFrame#options, QFrame#styleBox {{ background: transparent; border: none; }}
QFrame#noteBox {{ background: {c['warn_soft']}; border: 1px solid {c['warn_line']}; border-radius: {r['lg']}px; }}
QFrame#noteBox QLabel {{ color: {c['warn_text']}; font-size: {TYPE['caption'][0]}px; }}
QLabel#noteIcon {{ color: {c['warn_text']}; font-size: 15px; }}

/* ---------- drop zone ---------- */
QFrame#dropZone {{
  background: {c['surface_alt']}; border: 2px dashed {c['border_strong']}; border-radius: {r['lg']}px;
}}
QFrame#dropZone:hover {{ border-color: {c['accent_line']}; background: {c['drop_hover']}; }}
QFrame#dropZone:focus {{ border: 2px solid {c['focus']}; }}
QFrame#dropZone[state="filled"] {{ border: 1px solid {c['border']}; background: {c['surface']}; }}
QFrame#dropZone[state="filled"]:hover {{ border-color: {c['accent_line']}; }}
QFrame#dropZone[state="filled"]:focus {{ border: 2px solid {c['focus']}; }}
QFrame#dropZone[drag="true"], QFrame#dropZone[state="filled"][drag="true"] {{
  border: 2px dashed {c['accent']}; background: {c['accent_soft']};
}}
QFrame#dropZone[disabled="true"] {{ background: {c['surface_alt']}; border-color: {c['border']}; }}
QLabel#dropIcon {{
  background: {c['accent_soft']}; color: {c['accent']}; border-radius: 24px; font-size: 22px;
}}
QLabel#dropTitle {{ {_t('heading')} font-size: 15px; color: {c['text']}; }}
QLabel#fileBadge {{
  background: {c['accent_soft']}; color: {c['accent']}; border-radius: {r['lg']}px;
  font-family: {num}; font-size: 12px; font-weight: 700;
}}
QLabel#fileName {{ {_t('heading')} }}
QLabel#typeChip {{
  background: {c['accent_soft']}; color: {c['accent']}; border-radius: {r['sm']}px;
  padding: 1px 8px; font-size: {TYPE['micro'][0]}px; font-weight: 600;
}}

/* ---------- buttons ---------- */
QPushButton {{
  background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border_strong']};
  border-radius: {r['md']}px; padding: 6px 12px; min-height: 20px;
}}
QPushButton:hover {{ background: {c['surface_hover']}; border-color: {c['border_strong']}; }}
QPushButton:pressed {{ background: {c['surface_pressed']}; }}
QPushButton:focus {{ border: 2px solid {c['focus']}; padding: 5px 11px; }}
QPushButton:disabled {{ background: {c['disabled_bg']}; color: {c['disabled']}; border-color: {c['border']}; }}

QPushButton#primary {{
  background: {c['primary']}; color: {c['on_primary']}; border: 1px solid {c['primary']};
  border-radius: {r['lg'] - 2}px; padding: 10px 20px; font-size: 14px; font-weight: 600; min-height: 22px;
}}
QPushButton#primary:hover {{ background: {c['primary_hover']}; border-color: {c['primary_hover']}; }}
QPushButton#primary:pressed {{ background: {c['primary_pressed']}; border-color: {c['primary_pressed']}; }}
QPushButton#primary:focus {{ border: 2px solid {c['focus_on_primary']}; padding: 9px 19px; }}
QPushButton#primary:disabled {{
  background: {c['primary_disabled']}; border-color: {c['primary_disabled']}; color: {c['primary_disabled_text']};
}}
QPushButton#primary[danger="true"] {{
  background: {c['danger_soft']}; color: {c['danger_text']}; border: 1px solid {c['danger_line']};
}}
QPushButton#primary[danger="true"]:hover {{ background: {c['danger_soft_hover']}; border-color: {c['danger']}; }}
QPushButton#primary[danger="true"]:pressed {{ background: {c['danger_line']}; }}
QPushButton#primary[danger="true"]:focus {{ border: 2px solid {c['danger']}; padding: 9px 19px; }}
QPushButton#primary[danger="true"]:disabled {{
  background: {c['disabled_bg']}; color: {c['disabled']}; border-color: {c['border']};
}}

QPushButton#link {{
  background: transparent; border: 1px solid transparent; color: {c['accent']};
  padding: 3px 6px; border-radius: {r['sm']}px; min-height: 18px;
}}
QPushButton#link:hover {{ background: {c['accent_soft']}; color: {c['accent_hover']}; }}
QPushButton#link:pressed {{ background: {c['accent_soft_hover']}; color: {c['accent_pressed']}; }}
QPushButton#link:focus {{ border: 2px solid {c['focus']}; padding: 2px 5px; }}
QPushButton#link:disabled {{ color: {c['disabled']}; background: transparent; }}

QPushButton#ghost, QPushButton#iconButton {{
  background: transparent; border: 1px solid transparent; color: {c['text_2']};
  padding: 5px 10px; border-radius: {r['md']}px;
}}
QPushButton#ghost:hover, QPushButton#iconButton:hover {{ background: {c['surface_hover']}; color: {c['text']}; }}
QPushButton#ghost:pressed, QPushButton#iconButton:pressed {{ background: {c['surface_pressed']}; }}
QPushButton#ghost:focus, QPushButton#iconButton:focus {{ border: 2px solid {c['focus']}; padding: 4px 9px; }}
QPushButton#ghost:disabled, QPushButton#iconButton:disabled {{ color: {c['disabled']}; background: transparent; }}
QPushButton#iconButton {{ padding: 4px; min-width: 22px; min-height: 22px; font-size: 13px; }}
QPushButton#iconButton:focus {{ padding: 3px; }}

QPushButton#small {{ padding: 4px 10px; font-size: {TYPE['caption'][0]}px; border-radius: {r['sm'] + 1}px; min-height: 18px; }}
QPushButton#small:focus {{ padding: 3px 9px; }}
QPushButton#rowAction {{
  padding: 2px 8px; font-size: {TYPE['caption'][0]}px; border-radius: {r['sm']}px; min-height: 18px;
  background: {c['surface']}; color: {c['text_2']}; border: 1px solid {c['border']};
}}
QPushButton#rowAction:hover {{ background: {c['accent_soft']}; color: {c['accent']}; border-color: {c['accent_line']}; }}
QPushButton#rowAction:pressed {{ background: {c['accent_soft_hover']}; }}
QPushButton#rowAction:focus {{ border: 2px solid {c['focus']}; padding: 1px 7px; }}
QPushButton#rowAction:disabled {{ color: {c['disabled']}; background: transparent; border-color: {c['divider']}; }}
QPushButton#closeBanner {{
  background: transparent; border: none; color: {c['muted']}; font-size: 12px;
  padding: 0px; min-width: 24px; max-width: 24px; min-height: 24px; max-height: 24px; border-radius: 12px;
}}
QPushButton#closeBanner:hover {{ background: {c['surface_hover']}; color: {c['text']}; }}
QPushButton#closeBanner:focus {{ border: 2px solid {c['focus']}; }}

/* ---------- segmented toggles (radio buttons) ---------- */
QFrame#segmented {{ background: {c['track']}; border: 1px solid {c['border']}; border-radius: {r['md'] + 1}px; }}
QRadioButton#segment {{
  background: transparent; color: {c['text_2']}; border: 1px solid transparent; border-radius: {r['md'] - 1}px;
  padding: 5px 14px; spacing: 0px; min-height: 18px;
}}
QRadioButton#segment::indicator {{ width: 0px; height: 0px; }}
QRadioButton#segment:hover {{ color: {c['text']}; background: {c['surface_hover']}; }}
QRadioButton#segment:checked {{
  background: {c['surface']}; color: {c['accent']}; font-weight: 600; border: 1px solid {c['border_strong']};
}}
QRadioButton#segment:focus {{ border: 2px solid {c['focus']}; padding: 4px 13px; }}
QRadioButton#segment:disabled {{ color: {c['disabled']}; }}
QRadioButton#segment:checked:disabled {{ color: {c['faint']}; background: {c['surface_alt']}; }}
QFrame#segmented[compact="true"] QRadioButton#segment {{ padding: 4px 10px; font-size: {TYPE['caption'][0]}px; }}
QFrame#segmented[compact="true"] QRadioButton#segment:focus {{ padding: 3px 9px; }}
QFrame#segmented[tabs="true"] QRadioButton#segment {{ padding: 6px 16px; font-size: 14px; }}
QFrame#segmented[tabs="true"] QRadioButton#segment:focus {{ padding: 5px 15px; }}
QFrame#segmented[tabs="true"] QRadioButton#segment[badge="true"] {{ padding-right: 40px; }}
QFrame#segmented[tabs="true"] QRadioButton#segment[badge="true"]:focus {{ padding-right: 39px; }}
QLabel#tabBadge {{
  background: {c['surface_pressed']}; color: {c['text_2']}; border-radius: 9px;
  padding: 0px 6px; font-family: {num}; font-size: {TYPE['micro'][0]}px; font-weight: 600;
}}
QLabel#countChip {{
  background: {c['surface_alt']}; color: {c['muted']}; border: 1px solid {c['border']};
  border-radius: 9px; padding: 0px 8px; font-size: {TYPE['micro'][0]}px;
}}

/* ---------- progress ---------- */
QProgressBar {{ border: none; background: transparent; min-height: 6px; max-height: 6px; color: transparent; }}
QLabel#percent {{ color: {c['accent']}; font-family: {num}; font-size: 13px; font-weight: 700; }}
QLabel#elapsed {{ color: {c['muted']}; font-family: {num}; font-size: {TYPE['caption'][0]}px; }}
QLabel#status {{ color: {c['text_2']}; }}

/* ---------- result banner ---------- */
QFrame#banner {{ border-radius: {r['lg']}px; border: 1px solid {c['success_line']}; background: {c['success_soft']}; }}
QFrame#banner[kind="partial"] {{ border-color: {c['warn_line']}; background: {c['warn_soft']}; }}
QFrame#banner[kind="error"] {{ border-color: {c['danger_line']}; background: {c['danger_soft']}; }}
QLabel#bannerIcon {{
  background: {c['success']}; color: #ffffff; border-radius: 12px; font-weight: 700; font-size: 12px;
}}
QLabel#bannerIcon[kind="partial"] {{ background: {c['warn']}; }}
QLabel#bannerIcon[kind="error"] {{ background: {c['danger']}; }}
QLabel#bannerTitle {{ {_t('heading')} }}
QLabel#bannerDetail {{ color: {c['text_2']}; font-size: {TYPE['caption'][0]}px; }}
QFrame#banner[kind="error"] QLabel#bannerDetail {{ color: {c['danger_text']}; }}

/* ---------- inputs ---------- */
QLineEdit {{
  background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: {r['md']}px;
  padding: 6px 10px; selection-background-color: {c['selection']}; selection-color: {c['selection_text']};
}}
QLineEdit:hover {{ border-color: {c['accent_line']}; }}
QLineEdit:focus {{ border: 2px solid {c['focus']}; padding: 5px 9px; }}
QLineEdit#search {{ background: {c['surface_alt']}; padding-left: 6px; }}
QLineEdit#search:focus {{ background: {c['surface']}; padding-left: 5px; }}

/* ---------- history list ---------- */
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ background: transparent; border: none; }}
QListWidget::item:selected, QListWidget::item:hover {{ background: transparent; }}
QFrame#historyRow {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: {r['lg']}px; }}
QFrame#historyRow:hover {{ border-color: {c['accent_line']}; background: {c['surface_alt']}; }}
QFrame#historyRow[selected="true"] {{ border: 1px solid {c['accent']}; background: {c['accent_soft']}; }}
QFrame#historyRow[selected="true"] QLabel#rowBadge {{ background: {c['surface']}; }}
QLabel#rowTitle {{ font-weight: 600; font-size: {TYPE['heading'][0] - 1}px; }}
QLabel#rowTime {{ color: {c['muted']}; font-family: {num}; font-size: {TYPE['caption'][0]}px; }}
QLabel#rowBadge {{
  background: {c['accent_soft']}; color: {c['accent']}; border-radius: 10px; font-size: 16px;
}}
QLabel#rowBadge[tone="failed"] {{ background: {c['danger_soft']}; color: {c['danger_text']}; }}
QLabel#rowBadge[tone="partial"] {{ background: {c['warn_soft']}; color: {c['warn_text']}; }}
QLabel#chip {{ border-radius: {r['sm']}px; padding: 0px 7px; font-size: {TYPE['micro'][0]}px; font-weight: 600; }}
QLabel#chip[tone="success"] {{ background: {c['success_soft']}; color: {c['success_text']}; border: 1px solid {c['success_line']}; }}
QLabel#chip[tone="partial"] {{ background: {c['warn_soft']}; color: {c['warn_text']}; border: 1px solid {c['warn_line']}; }}
QLabel#chip[tone="failed"] {{ background: {c['danger_soft']}; color: {c['danger_text']}; border: 1px solid {c['danger_line']}; }}
QLabel#chip[tone="kind"] {{ background: {c['surface_alt']}; color: {c['text_2']}; border: 1px solid {c['border']}; }}
QLabel#rowNote {{ color: {c['warn_text']}; font-size: {TYPE['caption'][0]}px; }}
QLabel#rowError {{ color: {c['danger_text']}; font-size: {TYPE['caption'][0]}px; }}

/* ---------- empty states ---------- */
QLabel#emptyIcon {{ background: {c['accent_soft']}; color: {c['accent']}; border-radius: 28px; font-size: 24px; }}
QLabel#emptyIcon[tone="error"] {{ background: {c['danger_soft']}; color: {c['danger_text']}; }}
QLabel#emptyIcon[tone="warn"] {{ background: {c['warn_soft']}; color: {c['warn_text']}; }}
QLabel#emptyTitle {{ {_t('heading')} color: {c['text']}; }}
QLabel#emptyText {{ color: {c['muted']}; font-size: {TYPE['caption'][0]}px; }}

/* ---------- preview ---------- */
QLabel#previewTitle {{ {_t('title')} }}
QLabel#previewMeta {{ color: {c['muted']}; font-size: {TYPE['caption'][0]}px; }}
QLabel#previewBadge {{ background: {c['accent_soft']}; color: {c['accent']}; border-radius: 10px; font-size: 17px; }}
QFrame#scoreFrame {{ background: {c['score_bg']}; border: 1px solid {c['border']}; border-radius: {r['lg']}px; }}
QTextEdit#scoreView {{
  background: transparent; border: none;
  font-family: {mono}; selection-background-color: {c['selection']}; selection-color: {c['selection_text']};
}}
QFrame#scoreFooter {{ background: transparent; border: none; border-top: 1px solid {c['divider']}; }}
QPushButton#zoomButton {{
  background: transparent; border: 1px solid transparent; color: {c['text_2']}; border-radius: {r['sm']}px;
  font-family: {num}; font-size: 13px; font-weight: 600; padding: 1px 8px; min-height: 20px;
}}
QPushButton#zoomButton:hover {{ background: {c['surface_hover']}; color: {c['text']}; }}
QPushButton#zoomButton:pressed {{ background: {c['surface_pressed']}; }}
QPushButton#zoomButton:focus {{ border: 2px solid {c['focus']}; padding: 0px 7px; }}
QPushButton#zoomButton:disabled {{ color: {c['disabled']}; background: transparent; }}
QPushButton#zoomLabel {{
  background: transparent; border: 1px solid transparent; color: {c['muted']}; border-radius: {r['sm']}px;
  font-family: {num}; font-size: {TYPE['caption'][0]}px; padding: 1px 4px; min-width: 44px; min-height: 20px;
}}
QPushButton#zoomLabel:hover {{ background: {c['surface_hover']}; color: {c['text']}; }}
QPushButton#zoomLabel:focus {{ border: 2px solid {c['focus']}; padding: 0px 3px; }}

/* ---------- toast ---------- */
QLabel#toastText {{ color: {c['toast_text']}; font-size: 13px; }}
QLabel#toastIcon {{ color: {c['toast_text']}; font-size: 15px; }}
QLabel#toastIcon[tone="success"] {{ color: {c['toast_success']}; }}
QLabel#toastIcon[tone="error"] {{ color: {c['toast_error']}; }}
QLabel#toastIcon[tone="info"] {{ color: {c['toast_action']}; }}
QPushButton#toastAction {{
  background: transparent; border: 1px solid transparent; color: {c['toast_action']}; font-weight: 600;
  padding: 3px 8px; border-radius: {r['sm']}px; min-height: 18px;
}}
QPushButton#toastAction:hover {{ background: rgba(255, 255, 255, 0.10); }}
QPushButton#toastAction:focus {{ border: 2px solid {c['toast_action']}; padding: 2px 7px; }}
QPushButton#toastClose {{
  background: transparent; border: none; color: {c['toast_muted']}; font-size: 11px;
  padding: 0px; min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px; border-radius: 11px;
}}
QPushButton#toastClose:hover {{ background: rgba(255, 255, 255, 0.10); color: {c['toast_text']}; }}

/* ---------- menus ---------- */
QMenu {{
  background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: {r['md']}px; padding: 4px;
}}
QMenu::item {{ padding: 6px 28px 6px 12px; border-radius: {r['sm']}px; color: {c['text']}; }}
QMenu::item:selected {{ background: {c['surface_hover']}; }}
QMenu::item:disabled {{ color: {c['disabled']}; }}
QMenu::icon {{ padding-left: 8px; }}
QMenu::separator {{ height: 1px; background: {c['divider']}; margin: 4px 6px; }}
QMenu[danger="true"]::item:selected {{ background: {c['danger_soft']}; }}

/* ---------- scrollbars ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 3px 2px 3px 2px; }}
QScrollBar::handle:vertical {{ background: {c['scroll']}; border-radius: 3px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: {c['scroll_hover']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 3px 2px 3px; }}
QScrollBar::handle:horizontal {{ background: {c['scroll']}; border-radius: 3px; min-width: 32px; }}
QScrollBar::handle:horizontal:hover {{ background: {c['scroll_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QAbstractScrollArea::corner {{ background: transparent; }}

QStatusBar {{ background: {c['bg']}; color: {c['muted']}; font-size: {TYPE['caption'][0]}px; min-height: 26px; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {c['muted']}; font-size: {TYPE['caption'][0]}px; }}

/* ---------- dialogs (QMessageBox / QFileDialog when not native) ---------- */
QDialog {{ background: {c['surface']}; }}
QMessageBox QLabel {{ color: {c['text']}; }}
"""


def build_palette(c: dict) -> QPalette:
    """A QPalette that matches the tokens, for the parts of Qt that the style sheet does not reach."""
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: c["bg"],
        QPalette.ColorRole.WindowText: c["text"],
        QPalette.ColorRole.Base: c["surface"],
        QPalette.ColorRole.AlternateBase: c["surface_alt"],
        QPalette.ColorRole.ToolTipBase: c["tooltip"],
        QPalette.ColorRole.ToolTipText: c["tooltip_text"],
        QPalette.ColorRole.PlaceholderText: c["muted"],
        QPalette.ColorRole.Text: c["text"],
        QPalette.ColorRole.Button: c["surface"],
        QPalette.ColorRole.ButtonText: c["text"],
        QPalette.ColorRole.BrightText: "#ffffff",
        QPalette.ColorRole.Light: c["surface"],
        QPalette.ColorRole.Midlight: c["border"],
        QPalette.ColorRole.Mid: c["border_strong"],
        QPalette.ColorRole.Dark: c["border_strong"],
        QPalette.ColorRole.Shadow: c["shadow"],
        QPalette.ColorRole.Highlight: c["selection"],
        QPalette.ColorRole.HighlightedText: c["selection_text"],
        QPalette.ColorRole.Link: c["accent"],
        QPalette.ColorRole.LinkVisited: c["accent_pressed"],
    }
    for role, value in roles.items():
        pal.setColor(QPalette.ColorGroup.All, role, QColor(value))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(c["disabled"]))
    if hasattr(QPalette.ColorRole, "Accent"):
        pal.setColor(QPalette.ColorGroup.All, QPalette.ColorRole.Accent, QColor(c["primary"]))
    return pal


def system_scheme() -> str:
    """"dark" or "light" as reported by the OS (Unknown counts as light)."""
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
    except Exception:  # noqa: BLE001 - no GUI / very old Qt
        return MODE_LIGHT
    return MODE_DARK if scheme == Qt.ColorScheme.Dark else MODE_LIGHT


def _colorref(hex_color: str) -> int:
    color = QColor(hex_color)
    return color.red() | (color.green() << 8) | (color.blue() << 16)


def apply_window_frame(window, c: dict) -> None:
    """Windows 10/11: dark title bar in the dark theme; on Windows 11 the caption takes the window colour."""
    if sys.platform != "win32" or QGuiApplication.platformName() != "windows":
        return
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = wintypes.HWND(int(window.winId()))
        dwm = ctypes.windll.dwmapi
        dark = ctypes.c_int(1 if c["name"] == "dark" else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (newer, older builds)
            if dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(dark), ctypes.sizeof(dark)) == 0:
                break
        for attribute, value in ((35, c["bg"]), (36, c["text"]), (34, c["border"])):  # caption, text, border
            ref = ctypes.c_uint(_colorref(value))
            dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(ref), ctypes.sizeof(ref))
    except Exception:  # noqa: BLE001 - purely cosmetic
        pass


class ThemeManager(QObject):
    """Holds the chosen mode (跟随系统 / 浅色 / 深色) and applies the matching palette live."""

    changed = Signal(str)  # the effective palette name: "light" or "dark"

    def __init__(self, mode: str = MODE_SYSTEM, parent: QObject | None = None):
        super().__init__(parent)
        self._mode = mode if mode in MODES else MODE_SYSTEM
        self._effective = ""
        self._applying = False
        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_system_scheme_changed)
        except Exception:  # noqa: BLE001
            pass

    # -- state
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def effective(self) -> str:
        return self._effective or self._resolve()

    @property
    def tokens(self) -> dict:
        return tokens_for(self.effective)

    def _resolve(self) -> str:
        if self._mode == MODE_SYSTEM:
            return system_scheme()
        return self._mode

    # -- actions
    def start(self) -> None:
        """First application of the remembered mode."""
        self._hint_scheme()
        self.apply(force=True)

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            mode = MODE_SYSTEM
        self._mode = mode
        self._hint_scheme()
        self.apply(force=True)

    def apply(self, force: bool = False) -> None:
        if self._applying:
            return
        effective = self._resolve()
        if not force and effective == self._effective:
            return
        self._applying = True
        try:
            self._effective = effective
            tokens = tokens_for(effective)
            app = QGuiApplication.instance()
            if app is not None:
                app.setPalette(build_palette(tokens))
                if hasattr(app, "setStyleSheet"):
                    app.setStyleSheet(build_qss(tokens))
        finally:
            self._applying = False
        self.changed.emit(effective)

    def _hint_scheme(self) -> None:
        """Tell Qt (title bars, native bits) which scheme the app uses; "system" lets the OS decide."""
        try:
            hints = QGuiApplication.styleHints()
            if self._mode == MODE_SYSTEM:
                if hasattr(hints, "unsetColorScheme"):
                    hints.unsetColorScheme()
            elif hasattr(hints, "setColorScheme"):
                hints.setColorScheme(Qt.ColorScheme.Dark if self._mode == MODE_DARK else Qt.ColorScheme.Light)
        except Exception:  # noqa: BLE001
            pass

    def _on_system_scheme_changed(self, *_args) -> None:
        if self._mode == MODE_SYSTEM:
            self.apply()
