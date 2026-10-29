"""Glyph icons from the Windows icon fonts, with a plain-text fallback.

"Segoe Fluent Icons" (Windows 11) and "Segoe MDL2 Assets" (Windows 10) share their
code points. When neither font is installed, ``glyph()`` returns a short text
fallback and ``icon()`` returns a null QIcon, so buttons simply show their text.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QWidget

ICON_FAMILIES = ("Segoe Fluent Icons", "Segoe MDL2 Assets")

# name: (code point, text fallback)
GLYPHS: dict[str, tuple[int, str]] = {
    "add": (0xE710, "+"),
    "upload": (0xE898, "+"),
    "audio": (0xE8D6, "♪"),
    "music": (0xEC4F, "♪"),
    "midi": (0xE90B, "♪"),
    "microphone": (0xE720, "♪"),
    "document": (0xE8A5, "≡"),
    "pdf": (0xEA90, "P"),
    "photo": (0xEB9F, "▣"),
    "keyboard": (0xE765, "⌨"),
    "folder": (0xE838, "▤"),
    "open": (0xE8A7, "↗"),
    "copy": (0xE8C8, "⧉"),
    "search": (0xE721, "⌕"),
    "delete": (0xE74D, "×"),
    "close": (0xE711, "×"),
    "check": (0xE73E, "✓"),
    "completed": (0xE930, "✓"),
    "error": (0xE783, "!"),
    "error_badge": (0xEA39, "×"),
    "warning": (0xE7BA, "!"),
    "info": (0xE946, "i"),
    "play": (0xE768, "▶"),
    "convert": (0xE8AB, "⇄"),
    "stop": (0xE71A, "■"),
    "history": (0xE81C, "◷"),
    "view": (0xE890, "◉"),
    "zoom_in": (0xE8E8, "A+"),
    "zoom_out": (0xE8E7, "A−"),
    "theme_system": (0xE7F4, "◐"),
    "theme_light": (0xE706, "☀"),
    "theme_dark": (0xE708, "☾"),
    "gpu": (0xE945, "⚡"),
    "download": (0xE896, "↓"),
    "script": (0xE756, ">"),
    "undo": (0xE7A7, "↶"),
    "clock": (0xE823, "◷"),
    "more": (0xE712, "…"),
    "chevron_down": (0xE70D, "v"),
    "chevron_right": (0xE76C, ">"),
    "options": (0xE713, "*"),
}

_family_cache: list[str | None] = []


def icon_family() -> str | None:
    """The installed icon font, or None."""
    if not _family_cache:
        found = None
        try:
            families = set(QFontDatabase.families())
        except Exception:  # noqa: BLE001 - no font database yet
            families = set()
        for family in ICON_FAMILIES:
            if family in families:
                found = family
                break
        _family_cache.append(found)
    return _family_cache[0]


def reset_cache() -> None:
    _family_cache.clear()
    _icon_cache.clear()


def has_icon_font() -> bool:
    return icon_family() is not None


def glyph(name: str) -> str:
    code, fallback = GLYPHS[name]
    return chr(code) if has_icon_font() else fallback


def fallback_text(name: str) -> str:
    return GLYPHS[name][1]


_icon_cache: dict[tuple, QIcon] = {}


def _render(name: str, color: str, size: int, dpr: float) -> QPixmap:
    family = icon_family()
    pixmap = QPixmap(int(round(size * dpr)), int(round(size * dpr)))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    font = QFont(family)
    font.setPixelSize(size)
    font.setStyleStrategy(QFont.StyleStrategy.NoFontMerging)
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, chr(GLYPHS[name][0]))
    painter.end()
    return pixmap


def icon(name: str, color: str, size: int = 16, disabled_color: str | None = None) -> QIcon:
    """A QIcon drawn from the icon font (null QIcon when the font is missing)."""
    if not has_icon_font():
        return QIcon()
    screen = QGuiApplication.primaryScreen()
    dpr = max(2.0, screen.devicePixelRatio() if screen is not None else 1.0)
    key = (name, color, size, disabled_color, dpr)
    cached = _icon_cache.get(key)
    if cached is not None:
        return cached
    result = QIcon()
    normal = _render(name, color, size, dpr)
    result.addPixmap(normal, QIcon.Mode.Normal, QIcon.State.Off)
    result.addPixmap(normal, QIcon.Mode.Active, QIcon.State.Off)
    result.addPixmap(normal, QIcon.Mode.Selected, QIcon.State.Off)
    result.addPixmap(normal, QIcon.Mode.Normal, QIcon.State.On)
    if disabled_color:
        result.addPixmap(_render(name, disabled_color, size, dpr), QIcon.Mode.Disabled, QIcon.State.Off)
    _icon_cache[key] = result
    return result


def set_glyph(label: QLabel, name: str) -> None:
    """Show glyph ``name`` in a label: the icon font when installed, else the text fallback."""
    label.setText(glyph(name))
    label.setProperty("glyph", has_icon_font())
    label.setProperty("glyphName", name)


def glyph_label(name: str, object_name: str, size: int | None = None, parent: QWidget | None = None) -> QLabel:
    label = QLabel(parent)
    label.setObjectName(object_name)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    set_glyph(label, name)
    if size:
        label.setFixedSize(size, size)
    return label
