"""Compatibility shim: the style sheet is generated from the design tokens in ``app.ui.theme``.

``APP_QSS`` is the light theme; the running app switches themes through ``ThemeManager``.
"""

from app.ui.theme import LIGHT, SPACE, UI_FONTS, build_qss

FONT_FAMILY = ", ".join(UI_FONTS)
COLORS = LIGHT
C = COLORS
APP_QSS = build_qss(LIGHT)

__all__ = ["APP_QSS", "COLORS", "C", "FONT_FAMILY", "SPACE"]
