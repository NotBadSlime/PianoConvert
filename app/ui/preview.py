"""Built-in preview of keyboard scores (键盘谱 .txt).

The score is a plain-text grid: every beat of the 旋律 row sits exactly above the same
beat of the 伴奏 row, counting East-Asian wide characters (旋律, 伴奏, 〔〕) as two columns
and the jianpu octave dots (U+0323 below, U+0307 above) as zero columns. No single Windows
font renders all of that on one grid, so ``ScoreView``

- draws ASCII in a monospace face (Cascadia Mono → Consolas → …),
- draws wide characters in a CJK face and pads each one to exactly two columns
  (absolute letter spacing per character), so the rows stay aligned with any font,
- removes the combining dots from the displayed text and paints them itself, centred
  above / below their digit (fonts place U+0307 / U+0323 badly on digits),
- copies the original text, dots included (Ctrl+C, 复制).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QMimeData, QPointF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QKeySequence,
    QPainter,
    QRawFont,
    QShortcut,
    QSyntaxHighlighter,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.ui import icons
from app.ui.theme import CJK_FAMILIES, LIGHT, MONO_FAMILIES, SYMBOL_FAMILIES
from app.ui.widgets import ElidedLabel, EmptyState, Segmented, chip, set_prop, tab_focus

SECTION_KEYBOARD = "keyboard"
SECTION_JIANPU = "jianpu"
SECTION_FINGERTIP = "fingertip"
SECTION_ALL = "all"
SECTIONS = (SECTION_KEYBOARD, SECTION_JIANPU, SECTION_FINGERTIP, SECTION_ALL)
SECTION_LABELS = {
    SECTION_KEYBOARD: "键盘谱",
    SECTION_JIANPU: "简谱",
    SECTION_FINGERTIP: "指尖键盘谱",
    SECTION_ALL: "全部",
}
JIANPU_MARK = "【简谱】"
KEYBOARD_MARK = "【键盘谱】"
FINGERTIP_MARK = "【指尖键盘谱】"

ZOOM_LEVELS = (70, 80, 90, 100, 110, 125, 150, 175, 200)
DEFAULT_ZOOM = 100
BASE_PX = 14          # monospace pixel size at 100 %
LINE_FACTOR = 1.85    # line height / pixel size: room for the jianpu dots above and below

DOT_KINDS = {"\u0307": "above", "\u0323": "below"}
SUPERSCRIPT_DIGITS = set("¹²³⁴⁵⁶⁷⁸⁹⁰")

KIND_LABELS = {"piano": "钢琴", "other": "其他乐器", "score": "键盘谱", "pdf": "PDF"}
STATUS_LABELS = {"success": "成功", "partial": "部分成功", "failed": "失败"}
KIND_GLYPHS = {"piano": "audio", "other": "microphone", "score": "midi", "pdf": "pdf"}
_SOURCE_TYPES = {
    ".mid": "MIDI", ".midi": "MIDI", ".musicxml": "MusicXML", ".xml": "MusicXML", ".mxl": "MusicXML",
    ".pdf": "PDF", ".png": "图片", ".jpg": "图片", ".jpeg": "图片",
}


def source_label(item) -> str:
    """"钢琴录音" / "MIDI" / "PDF" … : what a history record was converted from."""
    kind = getattr(item, "kind", "") or ""
    if kind == "piano":
        return "钢琴录音"
    if kind == "other":
        return "其他乐器录音"
    suffix = Path(getattr(item, "source_path", "") or "").suffix.lower()
    return _SOURCE_TYPES.get(suffix) or KIND_LABELS.get(kind, kind)


def format_time(raw: str) -> str:
    try:
        return datetime.fromisoformat(raw).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return (raw or "").replace("T", " ")


def _exists(path) -> bool:
    return bool(path) and str(path) not in {"", "."} and Path(path).exists()


# ---------------------------------------------------------------- text helpers


def split_sections(text: str) -> dict[str, str]:
    """{"all": whole text, "jianpu": …, "keyboard": …}; a section missing from the file is absent."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    lines = text.split("\n")
    marks: dict[str, int] = {}
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == JIANPU_MARK and SECTION_JIANPU not in marks:
            marks[SECTION_JIANPU] = index
        elif stripped == KEYBOARD_MARK and SECTION_KEYBOARD not in marks:
            marks[SECTION_KEYBOARD] = index
        elif stripped == FINGERTIP_MARK and SECTION_FINGERTIP not in marks:
            marks[SECTION_FINGERTIP] = index
    sections = {SECTION_ALL: text.rstrip("\n")}
    order = sorted(marks.items(), key=lambda kv: kv[1])
    for n, (name, start) in enumerate(order):
        end = order[n + 1][1] if n + 1 < len(order) else len(lines)
        body = "\n".join(lines[start + 1 : end]).strip("\n")
        if body.strip():
            sections[name] = body
    return sections


_META = re.compile(r"(\d+/\d+)\s+♩=(\d+)\s+每拍\s*(\d+)\s*格")


def score_meta(text: str) -> list[str]:
    """Meter, tempo and cells per beat from the score's header, e.g. ["4/4", "♩=96", "每拍 1 格"]."""
    for line in (text or "").splitlines()[:6]:
        match = _META.search(line)
        if match:
            return [match.group(1), f"♩={match.group(2)}", f"每拍 {match.group(3)} 格"]
    return []


def strip_dots(text: str) -> tuple[str, dict[int, list[tuple[int, str]]], list[int]]:
    """Remove the jianpu octave dots.

    Returns the display text, the dots per line (column of the digit, "above"/"below") and,
    for every display position, the index of the same character in the original text.
    """
    shown: list[str] = []
    origin: list[int] = []
    dots: dict[int, list[tuple[int, str]]] = {}
    line = 0
    column = 0
    for index, ch in enumerate(text):
        kind = DOT_KINDS.get(ch)
        if kind is not None and column > 0:
            dots.setdefault(line, []).append((column - 1, kind))
            continue
        shown.append(ch)
        origin.append(index)
        if ch == "\n":
            line += 1
            column = 0
        else:
            column += 1
    origin.append(len(text))
    return "".join(shown), dots, origin


def display_columns(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


# ---------------------------------------------------------------- highlighter

_ROLE_COLORS = {
    "key": "text",
    "number": "score_number",
    "melody": "score_melody",
    "accomp": "score_accomp",
    "bar": "score_bar",
    "mark": "score_mark",
    "rest": "score_rest",
    "bracket": "text_2",
    "grace": "score_grace",
    "comment": "score_comment",
    "section": "score_section",
}
_LABEL_ROLES = (("旋律", "melody"), ("伴奏", "accomp"))
_BAR_NUMBER = re.compile(r"^\s*(\d+)")


def line_roles(text: str) -> list[str]:
    """Colour role of every character of one display line of the score grid."""
    roles = ["key"] * len(text)
    in_mark = False
    for i, ch in enumerate(text):
        if ch == "〔":
            in_mark = True
        if in_mark:
            roles[i] = "mark"
        if ch == "〕":
            in_mark = False
    match = _BAR_NUMBER.match(text)
    if match and "|" in text:
        for i in range(match.start(1), match.end(1)):
            roles[i] = "number"
    for label, role in _LABEL_ROLES:
        start = text.find(label)
        if start >= 0 and "|" in text[start:]:
            for i in range(start, start + len(label)):
                roles[i] = role
    for i, ch in enumerate(text):
        if roles[i] != "key":
            continue
        if ch == "|":
            roles[i] = "bar"
        elif ch in "-0":
            roles[i] = "rest"
        elif ch in "(){}":
            roles[i] = "bracket"
        elif ("a" <= ch <= "z") or ch in SUPERSCRIPT_DIGITS:
            roles[i] = "grace"
    return roles


class ScoreHighlighter(QSyntaxHighlighter):
    """Colours the grid and forces every character onto the monospace column grid."""

    def __init__(self, document):
        super().__init__(document)
        self._px = BASE_PX
        self._tokens = LIGHT
        self._formats: dict[tuple, QTextCharFormat] = {}
        self._advances: dict[tuple, float] = {}
        self._setup_fonts()

    # -- configuration
    def configure(self, px: int | None = None, tokens: dict | None = None) -> None:
        if px is not None:
            self._px = int(px)
        if tokens is not None:
            self._tokens = tokens
        self._setup_fonts()
        self.rehighlight()

    @property
    def px(self) -> int:
        return self._px

    @property
    def column_width(self) -> float:
        return self._col

    def _font(self, families: list[str], px: int, weight: QFont.Weight | None = None) -> QFont:
        font = QFont()
        font.setFamilies(families)
        font.setPixelSize(px)
        if weight is not None:
            font.setWeight(weight)
        return font

    def _setup_fonts(self) -> None:
        self._formats.clear()
        self._advances.clear()
        self.mono = self._font(MONO_FAMILIES, self._px)
        self.cjk = self._font(CJK_FAMILIES, self._px)
        self.cjk_bold = self._font(CJK_FAMILIES, self._px, QFont.Weight.DemiBold)
        self.symbol = self._font(SYMBOL_FAMILIES, self._px)
        self.prose = self._font(CJK_FAMILIES, max(9, self._px - 2))
        self.section = self._font(CJK_FAMILIES, self._px, QFont.Weight.Bold)
        self._col = QFontMetricsF(self.mono).horizontalAdvance("0") or self._px * 0.6
        raw = QRawFont.fromFont(self.mono)
        self._mono_has = raw.supportsCharacter if raw.isValid() else (lambda _c: False)

    # -- per character
    def font_class(self, ch: str) -> str:
        if ch.isascii():
            return "mono"
        if display_columns(ch) == 2:
            return "cjk"
        if self._mono_has(ord(ch)):
            return "mono"
        return "symbol"

    def font_for(self, ch: str, role: str = "key") -> QFont:
        cls = self.font_class(ch)
        if cls == "cjk":
            return self.cjk_bold if role in ("melody", "accomp") else self.cjk
        return self.symbol if cls == "symbol" else self.mono

    def _spacing(self, ch: str, role: str) -> float:
        """Extra pixels after ``ch`` so that it covers exactly its display columns."""
        cls = self.font_class(ch)
        if cls == "mono":
            return 0.0
        key = (ch, role)
        if key not in self._advances:
            advance = QFontMetricsF(self.font_for(ch, role)).horizontalAdvance(ch)
            self._advances[key] = display_columns(ch) * self._col - advance
        return self._advances[key]

    def _format(self, role: str, cls: str, spacing: float) -> QTextCharFormat:
        key = (role, cls, round(spacing, 2))
        fmt = self._formats.get(key)
        if fmt is not None:
            return fmt
        fmt = QTextCharFormat()
        if role == "comment":
            font = self.prose
        elif role == "section":
            font = self.section
        elif cls == "cjk":
            font = self.cjk_bold if role in ("melody", "accomp") else self.cjk
        elif cls == "symbol":
            font = self.symbol
        else:
            font = self.mono
        fmt.setFont(font)
        if spacing:
            fmt.setFontLetterSpacingType(QFont.SpacingType.AbsoluteSpacing)
            fmt.setFontLetterSpacing(spacing)
        fmt.setForeground(QColor(self._tokens[_ROLE_COLORS.get(role, "text")]))
        self._formats[key] = fmt
        return fmt

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        if not text:
            return
        stripped = text.lstrip()
        if stripped.startswith("#"):
            self.setFormat(0, len(text), self._format("comment", "prose", 0.0))
            return
        if stripped.startswith("【"):
            self.setFormat(0, len(text), self._format("section", "prose", 0.0))
            return
        roles = line_roles(text)
        start = 0
        current = None
        for i, ch in enumerate(text):
            role = roles[i] if ch != " " else "key"
            cls = self.font_class(ch)
            key = (role, cls, round(self._spacing(ch, role), 2))
            if key != current:
                if current is not None:
                    self.setFormat(start, i - start, self._format(*current))
                start, current = i, key
        if current is not None:
            self.setFormat(start, len(text) - start, self._format(*current))


# ---------------------------------------------------------------- view


def _x_of(line, position: int) -> float:
    value = line.cursorToX(position)
    return float(value[0] if isinstance(value, tuple) else value)


class ScoreView(QTextEdit):
    """Read-only, grid-aligned keyboard-score view with painted jianpu dots."""

    zoom_step = Signal(int)  # +1 / -1 from Ctrl+wheel

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("scoreView")
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.setTabChangesFocus(True)
        self.setAcceptRichText(False)
        self.document().setDocumentMargin(10)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._original = ""
        self._dots: dict[int, list[tuple[int, str]]] = {}
        self._origin: list[int] = [0]
        self._tokens = LIGHT
        self.highlighter = ScoreHighlighter(self.document())

    # -- content
    def set_score_text(self, text: str) -> None:
        self._original = text or ""
        shown, self._dots, self._origin = strip_dots(self._original)
        self.setPlainText(shown)
        self._apply_line_height()
        self.moveCursor(QTextCursor.MoveOperation.Start)
        self.horizontalScrollBar().setValue(0)
        self.verticalScrollBar().setValue(0)

    def original_text(self) -> str:
        return self._original

    def dots(self) -> dict[int, list[tuple[int, str]]]:
        return self._dots

    def original_range(self, start: int, end: int) -> str:
        start = max(0, min(start, len(self._origin) - 1))
        end = max(start, min(end, len(self._origin) - 1))
        return self._original[self._origin[start] : self._origin[end]]

    def selected_original_text(self) -> str:
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return ""
        return self.original_range(cursor.selectionStart(), cursor.selectionEnd())

    # -- look
    def set_pixel_size(self, px: int) -> None:
        self.highlighter.configure(px=px)
        self._apply_line_height()
        self.viewport().update()

    def set_tokens(self, tokens: dict) -> None:
        self._tokens = tokens
        self.highlighter.configure(tokens=tokens)
        self.viewport().update()

    def _apply_line_height(self) -> None:
        height = round(self.highlighter.px * LINE_FACTOR)
        cursor = QTextCursor(self.document())
        cursor.select(QTextCursor.SelectionType.Document)
        block_format = QTextBlockFormat()
        block_format.setLineHeight(height, QTextBlockFormat.LineHeightTypes.FixedHeight.value)
        cursor.mergeBlockFormat(block_format)

    # -- copy keeps the original characters (dots included)
    def createMimeDataFromSelection(self) -> QMimeData:  # noqa: N802
        mime = QMimeData()
        mime.setText(self.selected_original_text())
        return mime

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        copy = menu.addAction("复制")
        copy.setShortcut(QKeySequence.StandardKey.Copy)
        copy.setEnabled(self.textCursor().hasSelection())
        copy.triggered.connect(self.copy)
        select_all = menu.addAction("全选")
        select_all.setShortcut(QKeySequence.StandardKey.SelectAll)
        select_all.triggered.connect(self.selectAll)
        menu.exec(event.globalPos())

    def wheelEvent(self, event) -> None:  # noqa: N802
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                self.zoom_step.emit(1 if delta > 0 else -1)
            event.accept()
            return
        if modifiers & Qt.KeyboardModifier.ShiftModifier and event.angleDelta().y():
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - event.angleDelta().y())
            event.accept()
            return
        super().wheelEvent(event)

    # -- painted jianpu dots
    def _font_at(self, block, index: int) -> tuple[QFont, QColor]:
        for fmt_range in block.layout().formats():
            if fmt_range.start <= index < fmt_range.start + fmt_range.length:
                return fmt_range.format.font(), fmt_range.format.foreground().color()
        return self.highlighter.mono, QColor(self._tokens["text"])

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self._dots:
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        dx = self.horizontalScrollBar().value()
        dy = self.verticalScrollBar().value()
        doc = self.document()
        layout = doc.documentLayout()
        px = self.highlighter.px
        radius = max(1.15, px * 0.088)
        gap = max(1.4, px * 0.11)
        height = self.viewport().height()
        block = self.cursorForPosition(self.viewport().rect().topLeft()).block()
        if block.isValid() and block.previous().isValid():
            block = block.previous()
        while block.isValid():
            rect = layout.blockBoundingRect(block)
            top = rect.top() - dy
            if top > height:
                break
            dots = self._dots.get(block.blockNumber())
            if dots and rect.bottom() - dy >= 0:
                text = block.text()
                text_layout = block.layout()
                stacked: dict[tuple[int, str], int] = {}
                for index, kind in dots:
                    if index >= len(text):
                        continue
                    line = text_layout.lineForTextPosition(index)
                    if not line.isValid():
                        continue
                    font, color = self._font_at(block, index)
                    ink = QFontMetricsF(font).tightBoundingRect(text[index])
                    x0 = _x_of(line, index)
                    x1 = _x_of(line, index + 1)
                    spacing = font.letterSpacing() if font.letterSpacingType() == QFont.SpacingType.AbsoluteSpacing else 0
                    cx = rect.left() + (x0 + x1 - spacing) / 2 - dx
                    baseline = top + line.y() + line.ascent()
                    level = stacked.get((index, kind), 0)
                    stacked[(index, kind)] = level + 1
                    step = level * (2 * radius + gap * 0.8)
                    if kind == "above":
                        cy = baseline + ink.top() - gap - radius - step
                    else:
                        cy = baseline + ink.bottom() + gap + radius + step
                    painter.setBrush(color)
                    painter.drawEllipse(QPointF(cx, cy), radius, radius)
            block = block.next()
        painter.end()


class ScoreFooter(QFrame):
    """Slim bar under the score: a hint on the left, A− 100% A+ on the right."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("scoreFooter")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 4, 6, 4)
        row.setSpacing(2)
        self.hint_label = ElidedLabel("按住 Ctrl 滚动滚轮可以缩放")
        self.hint_label.setObjectName("faint")
        self.zoom_out_button = QPushButton("A−")
        self.zoom_in_button = QPushButton("A+")
        self.zoom_label = QPushButton("100%")
        self.zoom_out_button.setObjectName("zoomButton")
        self.zoom_in_button.setObjectName("zoomButton")
        self.zoom_label.setObjectName("zoomLabel")
        self.zoom_label.setToolTip("恢复 100%（Ctrl+0）")
        self.zoom_out_button.setToolTip("缩小字号（Ctrl+−）")
        self.zoom_in_button.setToolTip("放大字号（Ctrl+＋）")
        self.zoom_out_button.setAccessibleName("缩小")
        self.zoom_in_button.setAccessibleName("放大")
        row.addWidget(self.hint_label, 1)
        for button in (self.zoom_out_button, self.zoom_label, self.zoom_in_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            tab_focus(button)
            row.addWidget(button)


class PreviewPanel(QWidget):
    """Header (title, meta, score section), the score view and its actions."""

    copied = Signal()
    zoom_changed = Signal(int)
    section_changed = Signal(str)
    open_file_requested = Signal(str)
    open_folder_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None, zoom: int = DEFAULT_ZOOM, section: str = SECTION_KEYBOARD):
        super().__init__(parent)
        self._tokens = LIGHT
        self._sections: dict[str, str] = {}
        self._section = section if section in SECTIONS else SECTION_KEYBOARD
        self._zoom = zoom if zoom in ZOOM_LEVELS else DEFAULT_ZOOM
        self._path = ""
        self._folder = ""
        self._item_id = ""
        self._build()
        self._apply_zoom(emit=False)
        self.clear()

    # ------------------------------------------------------------- build
    def _build(self) -> None:
        self.badge = icons.glyph_label("keyboard", "previewBadge", 40)
        self.title_label = ElidedLabel("")
        self.title_label.setObjectName("previewTitle")
        self.status_chip = chip("", "kind")
        self.status_chip.hide()
        self.meta_label = ElidedLabel("")
        self.meta_label.setObjectName("previewMeta")
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title_row.addWidget(self.title_label, 1)
        title_row.addWidget(self.status_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        text_col.addLayout(title_row)
        text_col.addWidget(self.meta_label)

        self.keyboard_radio = QRadioButton(SECTION_LABELS[SECTION_KEYBOARD])
        self.jianpu_radio = QRadioButton(SECTION_LABELS[SECTION_JIANPU])
        self.fingertip_radio = QRadioButton(SECTION_LABELS[SECTION_FINGERTIP])
        self.all_radio = QRadioButton(SECTION_LABELS[SECTION_ALL])
        self.keyboard_radio.setToolTip("只看键盘谱（电脑按键）")
        self.jianpu_radio.setToolTip("只看简谱（数字）")
        self.fingertip_radio.setToolTip("只看旧版格式的指尖键盘谱")
        self.all_radio.setToolTip("整个文件，包括开头的说明")
        self.section_segment = Segmented(
            [self.keyboard_radio, self.jianpu_radio, self.fingertip_radio, self.all_radio],
            compact=True,
        )
        self._section_radios = {
            SECTION_KEYBOARD: self.keyboard_radio,
            SECTION_JIANPU: self.jianpu_radio,
            SECTION_FINGERTIP: self.fingertip_radio,
            SECTION_ALL: self.all_radio,
        }
        self._section_radios[self._section].setChecked(True)
        for name, radio in self._section_radios.items():
            radio.toggled.connect(lambda on, n=name: on and self.set_section(n))

        self.header = QWidget()
        head = QHBoxLayout(self.header)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(12)
        head.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addLayout(text_col, 1)
        head.addWidget(self.section_segment, 0, Qt.AlignmentFlag.AlignVCenter)

        self.view = ScoreView()
        self.view.zoom_step.connect(self._zoom_by)
        self.footer = ScoreFooter()
        self.zoom_out_button = self.footer.zoom_out_button
        self.zoom_in_button = self.footer.zoom_in_button
        self.zoom_label = self.footer.zoom_label
        self.zoom_out_button.clicked.connect(self.zoom_out)
        self.zoom_in_button.clicked.connect(self.zoom_in)
        self.zoom_label.clicked.connect(lambda: self.set_zoom(DEFAULT_ZOOM))
        self.score_frame = QFrame()
        self.score_frame.setObjectName("scoreFrame")
        frame = QVBoxLayout(self.score_frame)
        frame.setContentsMargins(1, 1, 1, 1)
        frame.setSpacing(0)
        frame.addWidget(self.view, 1)
        frame.addWidget(self.footer)

        self.empty_state = EmptyState(
            "keyboard", "还没有可预览的键盘谱\n转换完成后会显示在这里；也可以在「记录」里点一条查看"
        )
        self.message_state = EmptyState("warning", "")
        self.stack = QStackedWidget()
        self.stack.addWidget(self.empty_state)
        self.stack.addWidget(self.score_frame)
        self.stack.addWidget(self.message_state)

        self.copy_button = QPushButton("复制")
        self.copy_button.setToolTip("把正在显示的内容复制到剪贴板")
        self.open_file_button = QPushButton("打开文件")
        self.open_file_button.setToolTip("用记事本等程序打开这份键盘谱")
        self.open_folder_button = QPushButton("打开文件夹")
        self.open_folder_button.setToolTip("打开这次转换结果所在的文件夹")
        self.copy_button.clicked.connect(self.copy_text)
        self.open_file_button.clicked.connect(lambda: self.open_file_requested.emit(self._path))
        self.open_folder_button.clicked.connect(lambda: self.open_folder_requested.emit(self._folder))
        self.actions = QWidget()
        actions = QHBoxLayout(self.actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        for button in (self.copy_button, self.open_file_button, self.open_folder_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            tab_focus(button)
            actions.addWidget(button)
        actions.addStretch(1)

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(12)
        column.addWidget(self.header)
        column.addWidget(self.stack, 1)
        column.addWidget(self.actions)

        for keys, slot in (
            (QKeySequence.StandardKey.ZoomIn, self.zoom_in),
            ("Ctrl+=", self.zoom_in),
            (QKeySequence.StandardKey.ZoomOut, self.zoom_out),
            ("Ctrl+0", lambda: self.set_zoom(DEFAULT_ZOOM)),
        ):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
        self.refresh_icons()

    # ------------------------------------------------------------- look
    def set_tokens(self, tokens: dict) -> None:
        self._tokens = tokens
        self.view.set_tokens(tokens)
        self.refresh_icons()

    def refresh_icons(self) -> None:
        c = self._tokens
        pairs = (
            (self.copy_button, "copy"),
            (self.open_file_button, "open"),
            (self.open_folder_button, "folder"),
        )
        for button, name in pairs:
            button.setIcon(icons.icon(name, c["text_2"], 16, c["disabled"]))
            button.setIconSize(QSize(16, 16))

    # ------------------------------------------------------------- state
    def is_empty(self) -> bool:
        return self.stack.currentWidget() is self.empty_state

    def showing_score(self) -> bool:
        return self.stack.currentWidget() is self.score_frame

    def current_item_id(self) -> str:
        return self._item_id

    def current_path(self) -> str:
        return self._path

    def section(self) -> str:
        return self._section

    def shown_section(self) -> str:
        """The section on screen (falls back to 全部 when the file has no such section)."""
        return self._section if self._section in self._sections else SECTION_ALL

    def available_sections(self) -> list[str]:
        return [name for name in SECTIONS if name in self._sections]

    def text(self) -> str:
        if not self.showing_score():
            return ""
        return self._sections.get(self.shown_section(), "")

    def zoom(self) -> int:
        return self._zoom

    def clear(self) -> None:
        self._sections = {}
        self._path = ""
        self._folder = ""
        self._item_id = ""
        self.view.set_score_text("")
        self.header.hide()
        self.actions.hide()
        self.stack.setCurrentWidget(self.empty_state)

    def show_item(self, item) -> bool:
        """Show a history item; returns True when a keyboard score is on screen."""
        kind = getattr(item, "kind", "") or ""
        status = getattr(item, "status", "") or ""
        meta = [m for m in (source_label(item), format_time(getattr(item, "created_at", "") or "")) if m]
        self._item_id = getattr(item, "id", "") or ""
        self._folder = getattr(item, "folder", "") or ""
        path = getattr(item, "keyboard_path", "") or ""
        title = getattr(item, "title", "") or Path(path).stem
        icons.set_glyph(self.badge, KIND_GLYPHS.get(kind, "keyboard"))
        if status == "failed":
            reason = getattr(item, "error", "") or "转换失败"
            return self._show_message(title, meta, status, "error_badge", "error", f"这次转换失败\n{reason}")
        if not _exists(path):
            if status == "partial":
                reason = getattr(item, "error", "") or "键盘谱没有写成"
                return self._show_message(title, meta, status, "warning", "warn", f"这次没有生成键盘谱\n{reason}")
            return self._show_message(
                title, meta, status, "warning", "warn", "键盘谱文件不在了\n可能已被移动或删除，可以重新转换一次"
            )
        return self.show_file(path, title=title, meta=meta, status=status, item_id=self._item_id, folder=self._folder)

    def show_file(self, path, title: str | None = None, meta: list[str] | None = None, status: str = "",
                  item_id: str = "", folder: str = "") -> bool:
        path = str(path)
        try:
            raw = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return self._show_message(title or Path(path).stem, meta or [], status, "warning", "warn",
                                      "读不了这个键盘谱文件\n可能已被移动、删除或正被别的程序占用")
        self._path = path
        self._folder = folder or str(Path(path).parent)
        self._item_id = item_id
        self._sections = split_sections(raw)
        info = list(meta or []) + score_meta(raw)
        self._set_header(title or Path(path).stem, info, status)
        for name, radio in self._section_radios.items():
            radio.setEnabled(name in self._sections)
        self._render_section()
        self.stack.setCurrentWidget(self.score_frame)
        self.header.show()
        self.actions.show()
        self.copy_button.setEnabled(True)
        self.open_file_button.setEnabled(True)
        self.open_folder_button.setEnabled(_exists(self._folder))
        return True

    def _set_header(self, title: str, meta: list[str], status: str) -> None:
        self.title_label.setText(title)
        self.meta_label.setText(" · ".join(meta))
        if status in ("partial", "failed"):
            self.status_chip.setText(STATUS_LABELS[status])
            set_prop(self.status_chip, "tone", status)
            self.status_chip.show()
        else:
            self.status_chip.hide()

    def _show_message(self, title: str, meta: list[str], status: str, glyph: str, tone: str, text: str) -> bool:
        self._sections = {}
        self._path = ""
        self.view.set_score_text("")
        self._set_header(title, meta, status)
        self.message_state.set_glyph(glyph, tone)
        self.message_state.setText(text)
        for radio in self._section_radios.values():
            radio.setEnabled(False)
        self.stack.setCurrentWidget(self.message_state)
        self.header.show()
        self.actions.show()
        self.copy_button.setEnabled(False)
        self.open_file_button.setEnabled(False)
        self.open_folder_button.setEnabled(_exists(self._folder))
        return False

    # ------------------------------------------------------------- sections
    def set_section(self, name: str) -> None:
        if name not in SECTIONS:
            return
        changed = name != self._section
        self._section = name
        radio = self._section_radios[name]
        if not radio.isChecked():
            radio.setChecked(True)
        if self.showing_score():
            self._render_section()
        if changed:
            self.section_changed.emit(name)

    def _render_section(self) -> None:
        shown = self.shown_section()
        self.view.set_score_text(self._sections.get(shown, ""))
        radio = self._section_radios[shown]
        if not radio.isChecked():
            blocked = radio.blockSignals(True)
            radio.setChecked(True)
            radio.blockSignals(blocked)

    # ------------------------------------------------------------- copy
    def copy_text(self) -> bool:
        text = self.text()
        if not text:
            return False
        QGuiApplication.clipboard().setText(text)
        self.copied.emit()
        return True

    # ------------------------------------------------------------- zoom
    def set_zoom(self, percent: int) -> None:
        percent = min(ZOOM_LEVELS, key=lambda z: abs(z - int(percent)))
        if percent == self._zoom:
            return
        self._zoom = percent
        self._apply_zoom(emit=True)

    def zoom_in(self) -> None:
        self._zoom_by(1)

    def zoom_out(self) -> None:
        self._zoom_by(-1)

    def _zoom_by(self, steps: int) -> None:
        index = ZOOM_LEVELS.index(self._zoom) if self._zoom in ZOOM_LEVELS else ZOOM_LEVELS.index(DEFAULT_ZOOM)
        index = max(0, min(len(ZOOM_LEVELS) - 1, index + steps))
        self.set_zoom(ZOOM_LEVELS[index])

    def _apply_zoom(self, emit: bool) -> None:
        self.view.set_pixel_size(max(8, round(BASE_PX * self._zoom / 100)))
        self.zoom_label.setText(f"{self._zoom}%")
        self.zoom_out_button.setEnabled(self._zoom > ZOOM_LEVELS[0])
        self.zoom_in_button.setEnabled(self._zoom < ZOOM_LEVELS[-1])
        if emit:
            self.zoom_changed.emit(self._zoom)
