from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QComboBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.convert import AUDIO_SUFFIXES
from app.engines.base import CancelledError
from app.gpu_setup import explain_text, gpu_installed, install
from app.history import HistoryItem, append_item, load_items, remove_item, restore_item
from app.keyboard_score import (
    ARPEGGIO_AUTO,
    ARPEGGIO_CHORD,
    ARPEGGIO_SPREAD,
    CHORD_LOOSE,
    CHORD_STANDARD,
    CHORD_STRICT,
    RANGE_STRATEGY_DEFAULT,
    RANGE_STRATEGY_LABELS,
    RANGE_STRATEGY_MODERN,
    RANGE_STRATEGY_SMART,
    SCORE_SUFFIXES,
    STYLE_DETAILED,
    STYLE_SIMPLE,
)
from app.paths import app_icon_path
from app.pdf_omr import PDF_SUFFIXES
from app.settings import (
    ARPEGGIO,
    CHORD,
    PREVIEW_SECTION,
    PREVIEW_ZOOM,
    RANGE_STRATEGY,
    SCORE_STYLE,
    THEME,
    load_settings,
    save_setting,
)
from app.ui import icons
from app.ui.preview import KIND_GLYPHS, KIND_LABELS, STATUS_LABELS, PreviewPanel, format_time
from app.ui.theme import MODE_DARK, MODE_LIGHT, MODE_SYSTEM, MODES, ThemeManager, apply_window_frame
from app.ui.widgets import (
    ArtworkHeader,
    DropZone,
    ElidedLabel,
    Segmented,
    ShadowRoot,
    SmoothProgressBar,
    Toast,
    EmptyState,
    chip,
    repolish,
    set_prop,
    tab_focus,
)

AUDIO_FILTER = "音频文件 (*.mp3 *.wav *.flac *.ogg *.m4a)"
SCORE_FILTER = "乐谱文件 (*.mid *.midi *.musicxml *.xml *.mxl)"
PDF_FILTER = "PDF 或图片五线谱 (*.pdf *.png *.jpg *.jpeg)"

TAB_PREVIEW = "preview"
TAB_HISTORY = "history"

_MIDI_SUFFIXES = {".mid", ".midi"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
SUPPORTED_SUFFIXES = AUDIO_SUFFIXES | SCORE_SUFFIXES | PDF_SUFFIXES
# how rolled chords are written and what counts as struck together, when left alone
LAYOUT_DEFAULTS = {"arpeggio": ARPEGGIO_AUTO, "chord": CHORD_STANDARD}


def _globs(suffixes) -> str:
    return " ".join(f"*{s}" for s in sorted(suffixes))


def all_files_filter() -> str:
    image = _IMAGE_SUFFIXES & PDF_SUFFIXES
    pdf_only = PDF_SUFFIXES - image
    xml = SCORE_SUFFIXES - _MIDI_SUFFIXES
    parts = [
        f"全部支持的文件 ({_globs(SUPPORTED_SUFFIXES)})",
        f"音频 ({_globs(AUDIO_SUFFIXES)})",
        f"MIDI ({_globs(SCORE_SUFFIXES & _MIDI_SUFFIXES)})",
        f"MusicXML ({_globs(xml)})",
        f"PDF ({_globs(pdf_only)})",
        f"图片 ({_globs(image)})",
    ]
    return ";;".join(parts)


def file_type_label(path: Path | None) -> str:
    if path is None:
        return ""
    suffix = path.suffix.lower()
    if suffix in AUDIO_SUFFIXES:
        return "音频"
    if suffix in _MIDI_SUFFIXES:
        return "MIDI"
    if suffix in SCORE_SUFFIXES:
        return "MusicXML"
    if suffix in _IMAGE_SUFFIXES:
        return "图片"
    if suffix in PDF_SUFFIXES:
        return "PDF"
    return "文件"


def _format_size(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024**3:
        return f"{size / 1024 / 1024:.1f} MB"
    return f"{size / 1024**3:.2f} GB"


def _format_elapsed(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    return f"{seconds // 60} 分 {seconds % 60:02d} 秒"


def default_engines():
    from app.engines.basic_pitch import BasicPitchEngine
    from app.engines.kong_piano import KongPianoEngine

    return {"piano": KongPianoEngine(), "other": BasicPitchEngine()}


def _first_dropped_path(event: QDragEnterEvent | QDropEvent) -> Path | None:
    mime = event.mimeData()
    if not mime.hasUrls():
        return None
    for url in mime.urls():
        path = Path(url.toLocalFile())
        if path.suffix.lower() in SUPPORTED_SUFFIXES:
            return path
    return None


def _is_score_path(path: Path | None) -> bool:
    return path is not None and path.suffix.lower() in SCORE_SUFFIXES


def _is_pdf_path(path: Path | None) -> bool:
    return path is not None and path.suffix.lower() in PDF_SUFFIXES


def _direct_score_path(path: Path | None) -> bool:
    return _is_score_path(path) or _is_pdf_path(path)


def _format_time(raw: str) -> str:
    return format_time(raw)


def _exists(path: str | os.PathLike | None) -> bool:
    return bool(path) and str(path) not in {"", "."} and Path(path).exists()


def _startfile(path: str) -> None:
    os.startfile(path)  # type: ignore[attr-defined]


def _notify_error(parent: QWidget | None, text: str) -> None:
    """A non-fatal problem: a toast when the main window is around, otherwise a message box."""
    window = parent.window() if parent is not None else None
    if window is not None and hasattr(window, "show_toast"):
        window.show_toast(text, "error")
        return
    QMessageBox.warning(parent, "PianoConvert", text)


def open_path(parent: QWidget | None, path: str | os.PathLike | None) -> bool:
    if not _exists(path):
        _notify_error(parent, "文件不在了，可能已被移动或删除")
        return False
    try:
        _startfile(str(Path(path)))
    except OSError:
        _notify_error(parent, "文件不在了，或者打不开")
        return False
    return True


class GpuDownloadWorker(QObject):
    progress = Signal(str, float)
    finished = Signal()
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, cancel: Event):
        super().__init__()
        self._cancel = cancel

    def run(self) -> None:
        try:
            install(self._cancel, lambda message, ratio: self.progress.emit(str(message), float(ratio)))
            self.finished.emit()
        except CancelledError:
            self.cancelled.emit()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc) or exc.__class__.__name__)


class ConvertWorker(QObject):
    progress = Signal(str, float)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, convert_fn, source: Path, kind: str, cancel: Event):
        super().__init__()
        self._convert_fn = convert_fn
        self._source = source
        self._kind = kind
        self._cancel = cancel

    def run(self) -> None:
        try:
            result = self._convert_fn(self._source, self._kind, self._cancel, self._emit_progress)
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc) or exc.__class__.__name__)

    def _emit_progress(self, message: str, ratio: float) -> None:
        self.progress.emit(str(message), float(ratio))


def _partial_note(error: str) -> str:
    keyboard = "键盘谱导出失败" in error
    score = "谱面导出失败" in error
    if keyboard and not score:
        return "键盘谱导出失败"
    if keyboard and score:
        return "谱面或键盘谱导出失败"
    return "谱面导出失败"


class HistoryRow(QWidget):
    menu_requested = Signal(object, QPoint)    # right click: context menu at a global position

    def __init__(self, item: HistoryItem, parent: QWidget | None = None):
        super().__init__(parent)
        self.item = item

        self.card = QFrame()
        self.card.setObjectName("historyRow")
        self.card.setProperty("selected", False)

        self.badge = icons.glyph_label(KIND_GLYPHS.get(item.kind, "keyboard"), "rowBadge", 36)
        self.badge.setProperty("tone", item.status if item.status in ("failed", "partial") else "")
        self._title = ElidedLabel(item.title)
        self._title.setObjectName("rowTitle")
        self.status_chip = chip(STATUS_LABELS.get(item.status, item.status), item.status or "kind")
        self.kind_chip = chip(KIND_LABELS.get(item.kind, item.kind), "kind")
        time_label = QLabel(_format_time(item.created_at))
        time_label.setObjectName("rowTime")

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        top.addWidget(self._title, 1)
        top.addWidget(self.status_chip)

        meta = QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(8)
        meta.addWidget(self.kind_chip)
        meta.addWidget(time_label)
        meta.addStretch(1)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(4)
        text.addLayout(top)
        text.addLayout(meta)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(12)
        head.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        head.addLayout(text, 1)

        column = QVBoxLayout(self.card)
        column.setContentsMargins(12, 10, 12, 10)
        column.setSpacing(8)
        column.addLayout(head)

        error = item.error or ""
        if item.status == "partial":
            note = QLabel(_partial_note(error))
            note.setObjectName("rowNote")
            column.addWidget(note)
        elif item.status == "failed" and error:
            err = QLabel(error)
            err.setObjectName("rowError")
            err.setWordWrap(True)
            column.addWidget(err)

        self.midi_button = QPushButton("MIDI")
        self.midi_button.setToolTip("打开 MIDI")
        self.xml_button = QPushButton("MusicXML")
        self.xml_button.setToolTip("打开 MusicXML")
        self.keyboard_button = QPushButton("打开键盘谱")
        self.keyboard_button.setToolTip("用记事本等程序打开（双击这一条也可以）")
        self.folder_button = QPushButton("打开文件夹")
        self.folder_button.setToolTip("打开这次结果所在的文件夹")
        targets = (
            (self.keyboard_button, item.keyboard_path),
            (self.folder_button, item.folder),
            (self.midi_button, item.midi_path),
            (self.xml_button, item.musicxml_path),
        )
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(4)
        for button, target in targets:
            button.setObjectName("rowAction")
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            tab_focus(button)
            button.setEnabled(_exists(target))
            button.clicked.connect(lambda _=False, t=target: self._open(t))
            if item.status == "failed":
                button.hide()
            buttons.addWidget(button)
        buttons.addStretch(1)
        if item.status != "failed":
            column.addLayout(buttons)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 4, 0)
        outer.setSpacing(0)
        outer.addWidget(self.card)
        self.setToolTip("单击预览 · 双击打开键盘谱 · 右键更多" if _exists(item.keyboard_path) else "单击查看 · 右键更多")

    def refresh_icons(self, tokens: dict) -> None:
        for button, name in ((self.keyboard_button, "keyboard"), (self.folder_button, "folder")):
            button.setIcon(icons.icon(name, tokens["text_2"], 14, tokens["disabled"]))
            button.setIconSize(QSize(14, 14))

    def set_selected(self, on: bool) -> None:
        set_prop(self.card, "selected", bool(on))

    def matches(self, query: str) -> bool:
        query = (query or "").strip().lower()
        return not query or query in (self.item.title or "").lower()

    def sizeHint(self) -> QSize:  # noqa: N802
        hint = super().sizeHint()
        return QSize(320, max(hint.height(), 72))

    def open_keyboard(self) -> bool:
        if not _exists(self.item.keyboard_path):
            return False
        return open_path(self.window(), self.item.keyboard_path)

    # A double click is left to the list: it emits itemActivated once and, unlike a double click
    # swallowed here, does not report the trailing mouse release as one more single click.

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self.menu_requested.emit(self, event.globalPos())
        event.accept()

    def _open(self, path: str) -> None:
        open_path(self.window(), path)


class MainWindow(QMainWindow):
    def __init__(self, convert_fn=None, engines=None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("PianoConvert")
        self.setAcceptDrops(True)
        self.resize(1100, 760)
        self.setMinimumSize(900, 700)

        self._source: Path | None = None
        self._busy = False
        self._downloading = False
        self._cancel: Event | None = None
        self._thread: QThread | None = None
        self._worker: ConvertWorker | None = None
        self._job_started: float | None = None
        self._last_result = None
        self._history_viewed_during_job = False
        self._pending_click_id = ""
        if engines is None:
            try:
                engines = default_engines()
            except Exception:
                engines = {}
        self._engines = engines
        self._convert_fn = convert_fn or self._make_default_convert()

        settings = load_settings()
        mode = settings.get(THEME, MODE_SYSTEM)
        self.theme = ThemeManager(mode if mode in MODES else MODE_SYSTEM, self)

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._flush_history_click)

        self._build_ui(settings)
        self._build_shortcuts()
        self._apply_window_icon()
        self._apply_source_mode()
        self.theme.changed.connect(self._on_theme_changed)
        self.theme.start()
        self.reload_history()
        self._preload_latest()

    # ------------------------------------------------------------------ setup
    def _make_default_convert(self):
        engines = self._engines

        def _fn(source, kind, cancel, on_progress):
            from app.convert import run as convert_run
            from app.convert import run_score

            style = getattr(self, "_job_style", None) or self.score_style()
            range_strategy = getattr(self, "_job_range_strategy", None) or self.range_strategy()
            layout = getattr(self, "_job_layout", None) or self.layout_options()
            # only settings that differ from the defaults are passed on
            extra = {
                **({"range_strategy": range_strategy} if range_strategy != RANGE_STRATEGY_DEFAULT else {}),
                **{key: value for key, value in layout.items() if value != LAYOUT_DEFAULTS[key]},
            }
            if kind == "score":
                return run_score(
                    source,
                    cancel,
                    output_root=None,
                    on_progress=on_progress,
                    style=style,
                    **extra,
                )
            if kind == "pdf":
                from app.convert import run_pdf

                return run_pdf(
                    source,
                    cancel,
                    output_root=None,
                    on_progress=on_progress,
                    style=style,
                    **extra,
                )
            return convert_run(
                source,
                kind,
                engines,
                cancel,
                output_root=None,
                on_progress=on_progress,
                style=style,
                **extra,
            )

        return _fn

    def _apply_window_icon(self) -> None:
        icon_path = app_icon_path()
        if icon_path.is_file():
            icon = QIcon(str(icon_path))
            self.setWindowIcon(icon)
            app = self.window().windowHandle()
            if app is not None:
                app.setIcon(icon)
            application = QApplication.instance()
            if application is not None:
                application.setWindowIcon(icon)

    def _build_shortcuts(self) -> None:
        self._shortcuts = []
        for keys, slot in (
            ("Ctrl+O", self._choose_any_file),
            ("Ctrl+Return", self._shortcut_start),
            ("Ctrl+Enter", self._shortcut_start),
            ("Ctrl+F", self.focus_search),
        ):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)

    def _shortcut_start(self) -> None:
        if not self._busy and self.start_button.isEnabled():
            self._on_start_clicked()

    # ------------------------------------------------------------- build UI
    def _build_ui(self, settings: dict) -> None:
        root = ShadowRoot()
        self._root = root

        header = self._build_header()
        convert_card = self._build_convert_card()
        history_card = self._build_right_card(settings)
        self._history_card = history_card

        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(20)
        for card, stretch, min_width in ((convert_card, 4, 420), (history_card, 6, 410)):
            card.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            card.setMinimumWidth(min_width)
            columns.addWidget(card, stretch)
            root.add_shadow(card)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 18, 24, 8)
        layout.setSpacing(16)
        layout.addWidget(header)
        layout.addLayout(columns, 1)
        self.setCentralWidget(root)
        # Start with the focus on the background: no focus ring until the keyboard is used.
        root.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._initial_focus_done = False

        self.toast = Toast(root)

        self.hint_label = QLabel("Ctrl+O 选择文件 · Ctrl+Enter 开始转换 · Ctrl+F 搜索记录")
        self.hint_label.setObjectName("helper")
        self.device_status_label = QLabel("")
        self.device_status_label.setObjectName("helper")
        self.statusBar().addWidget(self.device_status_label, 1)
        self.statusBar().addPermanentWidget(self.hint_label)
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().setContentsMargins(26, 0, 26, 6)

        for widget in (
            root,
            convert_card,
            history_card,
            self.drop_zone,
            self.history_list,
            self.history_list.viewport(),
            self.search_edit,
            self.preview,
            self.preview.view,
            self.preview.view.viewport(),
        ):
            widget.setAcceptDrops(True)
            widget.installEventFilter(self)

    def _build_header(self) -> QWidget:
        header = ArtworkHeader()
        self._artwork_header = header
        column = QVBoxLayout(header)
        column.setContentsMargins(20, 16, 20, 16)
        column.setSpacing(12)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        self.app_badge = QLabel()
        self.app_badge.setObjectName("appBadge")
        self.app_badge.setFixedSize(40, 40)
        icon_path = app_icon_path()
        if icon_path.is_file():
            self.app_badge.setPixmap(QIcon(str(icon_path)).pixmap(QSize(40, 40)))
            self.app_badge.setScaledContents(True)
        else:
            self.app_badge.hide()
        hero = QLabel("把音频或乐谱转成琴谱")
        hero.setObjectName("hero")
        subtitle = QLabel("音频、MIDI、MusicXML，或 PDF / 图片五线谱，一键生成原神键盘谱")
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        subtitle.setMinimumWidth(420)
        subtitle.setMaximumWidth(475)
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(2)
        titles.addWidget(hero)
        titles.addWidget(subtitle)

        self.theme_system_radio = QRadioButton("跟随系统")
        self.theme_light_radio = QRadioButton("浅色")
        self.theme_dark_radio = QRadioButton("深色")
        self.theme_system_radio.setToolTip("和 Windows 的浅色 / 深色设置保持一致")
        self.theme_light_radio.setToolTip("始终使用浅色界面")
        self.theme_dark_radio.setToolTip("始终使用深色界面")
        self._theme_radios = {
            MODE_SYSTEM: self.theme_system_radio,
            MODE_LIGHT: self.theme_light_radio,
            MODE_DARK: self.theme_dark_radio,
        }
        self.theme_switch = Segmented(list(self._theme_radios.values()), compact=True)
        self.theme_switch.setToolTip("界面颜色")
        self._theme_radios.get(self.theme.mode, self.theme_system_radio).setChecked(True)
        for mode, radio in self._theme_radios.items():
            radio.toggled.connect(lambda on, m=mode: on and self.set_theme_mode(m))

        row.addWidget(self.app_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addLayout(titles)
        row.addStretch(1)
        column.addLayout(row)
        column.addWidget(self.theme_switch, 0, Qt.AlignmentFlag.AlignLeft)
        return header

    def _build_convert_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        self._convert_card = card
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        title = QLabel("新建转换")
        title.setObjectName("cardTitle")
        layout.addWidget(title)

        layout.addWidget(self._build_drop_zone(), 1)
        layout.addLayout(self._build_type_pickers())
        layout.addWidget(self._build_settings())

        self.pdf_note = QLabel(
            "打谱软件导出的 PDF 五线谱会直接读取谱面上的音符，基本不会认错。"
            "扫描件、拍照和截图只能看图识别，准确度有限，请对照原谱；第一次看图识别需要联网下载识别模型。"
            "支持印刷五线谱和双手简谱（自动判断），不认吉他谱、手写谱。"
        )
        self.pdf_note.setObjectName("note")
        self.pdf_note.setWordWrap(True)
        self.pdf_box = QFrame()
        self.pdf_box.setObjectName("noteBox")
        pdf_layout = QHBoxLayout(self.pdf_box)
        pdf_layout.setContentsMargins(12, 10, 12, 10)
        pdf_layout.setSpacing(10)
        self.pdf_icon = icons.glyph_label("info", "noteIcon")
        self.pdf_icon.setFixedWidth(18)
        pdf_layout.addWidget(self.pdf_icon, 0, Qt.AlignmentFlag.AlignTop)
        pdf_layout.addWidget(self.pdf_note, 1)
        layout.addWidget(self.pdf_box)
        self.pdf_note.hide()
        self.pdf_box.hide()

        # while no file is chosen the drop zone takes the free height; afterwards this spacer does
        self._convert_spacer = QWidget()
        self._convert_spacer.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._convert_spacer, 0)
        self._convert_layout = layout
        layout.addWidget(self._build_progress())
        layout.addWidget(self._build_banner())

        self.start_button = QPushButton("开始转换")
        self.start_button.setObjectName("primary")
        self.start_button.setProperty("danger", False)
        self.start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_button.setToolTip("Ctrl+Enter")
        self.start_button.setEnabled(False)
        self.start_button.setIconSize(QSize(18, 18))
        tab_focus(self.start_button)
        self.start_button.clicked.connect(self._on_start_clicked)
        layout.addWidget(self.start_button)
        return card

    def _build_drop_zone(self) -> DropZone:
        zone = DropZone()
        zone.setMinimumHeight(112)
        zone.setMaximumHeight(300)
        zone.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        zone.clicked.connect(self._choose_any_file)
        self.drop_zone = zone
        zone_layout = QVBoxLayout(zone)
        zone_layout.setContentsMargins(16, 14, 16, 14)
        zone_layout.setSpacing(0)

        # empty state
        self.drop_empty = QWidget()
        empty = QVBoxLayout(self.drop_empty)
        empty.setContentsMargins(0, 0, 0, 0)
        empty.setSpacing(6)
        self.drop_icon = icons.glyph_label("upload", "dropIcon", 48)
        self.drop_title = QLabel("拖入文件，或点击选择")
        self.drop_title.setObjectName("dropTitle")
        self.drop_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_hint = QLabel("支持 音频 · MIDI · MusicXML · PDF · 图片")
        self.drop_hint.setObjectName("helper")
        self.drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.addWidget(self.drop_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        empty.addSpacing(2)
        empty.addWidget(self.drop_title)
        empty.addWidget(self.drop_hint)

        # filled state
        self.drop_filled = QWidget()
        filled = QHBoxLayout(self.drop_filled)
        filled.setContentsMargins(2, 0, 0, 0)
        filled.setSpacing(14)
        self.file_badge = QLabel("")
        self.file_badge.setObjectName("fileBadge")
        self.file_badge.setFixedSize(52, 52)
        self.file_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_label = ElidedLabel("尚未选择文件")
        self.file_label.setObjectName("fileName")
        self.file_type_label = QLabel("")
        self.file_type_label.setObjectName("typeChip")
        self.file_type_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.file_meta_label = ElidedLabel("")
        self.file_meta_label.setObjectName("helper")
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(8)
        meta_row.addWidget(self.file_type_label)
        meta_row.addWidget(self.file_meta_label, 1)
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(6)
        text_col.addStretch(1)
        text_col.addWidget(self.file_label)
        text_col.addLayout(meta_row)
        text_col.addStretch(1)
        self.change_button = QPushButton("更换")
        self.change_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.change_button.setToolTip("Ctrl+O")
        tab_focus(self.change_button)
        self.change_button.clicked.connect(self._choose_any_file)
        filled.addWidget(self.file_badge)
        filled.addLayout(text_col, 1)
        filled.addWidget(self.change_button, 0, Qt.AlignmentFlag.AlignVCenter)

        zone_layout.addStretch(1)
        zone_layout.addWidget(self.drop_empty)
        zone_layout.addWidget(self.drop_filled)
        zone_layout.addStretch(1)
        self.drop_filled.hide()
        return zone

    def _build_type_pickers(self) -> QHBoxLayout:
        label = QLabel("按类型选择：")
        label.setObjectName("helper")
        self.pick_button = QPushButton("音频")
        self.pick_button.clicked.connect(self._choose_file)
        self.pick_score_button = QPushButton("MIDI / MusicXML")
        self.pick_score_button.clicked.connect(self._choose_score_file)
        self.pick_pdf_button = QPushButton("PDF / 图片")
        self.pick_pdf_button.clicked.connect(self._choose_pdf_file)
        row = QHBoxLayout()
        row.setContentsMargins(2, 0, 0, 0)
        row.setSpacing(2)
        row.addWidget(label)
        for button in (self.pick_button, self.pick_score_button, self.pick_pdf_button):
            button.setObjectName("link")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setIconSize(QSize(14, 14))
            tab_focus(button)
            row.addWidget(button)
        row.addStretch(1)
        return row

    @staticmethod
    def _setting_row(label: QLabel, help_label: QLabel, control: QWidget) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(14, 8, 12, 8)
        h.setSpacing(12)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        text.addWidget(label)
        text.addWidget(help_label)
        h.addLayout(text, 1)
        h.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    @staticmethod
    def _divider() -> QFrame:
        line = QFrame()
        line.setObjectName("divider")
        line.setFixedHeight(1)
        return line

    def _build_settings(self) -> QFrame:
        self.settings_box = QFrame()
        self.settings_box.setObjectName("settings")
        column = QVBoxLayout(self.settings_box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self._build_options())
        column.addWidget(self._build_style_row())
        column.addWidget(self._build_range_row())
        column.addWidget(self._build_advanced())
        return self.settings_box

    # how rolled chords are written, and what counts as struck together
    ARPEGGIO_HELP = {
        ARPEGGIO_AUTO: "琶音写成 { }",
        ARPEGGIO_CHORD: "琶音写成 ( ) 一起按",
        ARPEGGIO_SPREAD: "弹出来的琶音分格写",
    }
    ARPEGGIO_TIPS = {
        ARPEGGIO_AUTO: "谱上的琶音记号，和 MIDI 里弹出来的琶音，都写成 { }：从低到高依次快速按",
        ARPEGGIO_CHORD: "所有琶音都写成 ( )：一起按，更好弹",
        ARPEGGIO_SPREAD: "MIDI 里弹出来的琶音按实际先后分格写；谱上有记号的仍写 { }",
    }
    CHORD_HELP = {
        CHORD_STRICT: "相差 30 毫秒内算一起按",
        CHORD_STANDARD: "相差 50 毫秒内算一起按",
        CHORD_LOOSE: "相差 80 毫秒内算一起按",
    }

    def _build_advanced(self) -> QWidget:
        """琶音写法 and 和弦判定, behind a 高级选项 toggle; remembered between runs, and a
        change lays out the score in 预览 again at once."""
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.advanced_button = QPushButton("高级选项")
        self.advanced_button.setObjectName("link")
        self.advanced_button.setIconSize(QSize(14, 14))
        self.advanced_button.setCheckable(True)
        self.advanced_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.advanced_button.setToolTip("琶音写法、和弦判定")
        tab_focus(self.advanced_button)
        toggle_row = QHBoxLayout()
        toggle_row.setContentsMargins(10, 4, 12, 4)
        toggle_row.addWidget(self.advanced_button)
        toggle_row.addStretch(1)
        column.addLayout(toggle_row)

        self.advanced_box = QWidget()
        inner = QVBoxLayout(self.advanced_box)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)
        settings = load_settings()

        self.arpeggio_label = QLabel("琶音写法")
        self.arpeggio_label.setObjectName("sectionLabel")
        self.arpeggio_auto_radio = QRadioButton("自动")
        self.arpeggio_chord_radio = QRadioButton("一起按")
        self.arpeggio_spread_radio = QRadioButton("分开写")
        self._arpeggio_radios = {
            ARPEGGIO_AUTO: self.arpeggio_auto_radio,
            ARPEGGIO_CHORD: self.arpeggio_chord_radio,
            ARPEGGIO_SPREAD: self.arpeggio_spread_radio,
        }
        for mode, radio in self._arpeggio_radios.items():
            radio.setToolTip(self.ARPEGGIO_TIPS[mode])
        self._arpeggio_radios.get(settings.get(ARPEGGIO), self.arpeggio_auto_radio).setChecked(True)
        self.arpeggio_segment = Segmented(list(self._arpeggio_radios.values()))
        self.arpeggio_help = ElidedLabel("")
        self.arpeggio_help.setObjectName("helper")
        inner.addWidget(self._divider())
        inner.addWidget(self._setting_row(self.arpeggio_label, self.arpeggio_help, self.arpeggio_segment))

        self.chord_label = QLabel("和弦判定")
        self.chord_label.setObjectName("sectionLabel")
        self.chord_strict_radio = QRadioButton("严格")
        self.chord_standard_radio = QRadioButton("标准")
        self.chord_loose_radio = QRadioButton("宽松")
        self._chord_radios = {
            CHORD_STRICT: self.chord_strict_radio,
            CHORD_STANDARD: self.chord_standard_radio,
            CHORD_LOOSE: self.chord_loose_radio,
        }
        for mode, radio in self._chord_radios.items():
            radio.setToolTip(self.CHORD_HELP[mode] + "（只影响录音和演奏录的 MIDI）")
        self._chord_radios.get(settings.get(CHORD), self.chord_standard_radio).setChecked(True)
        self.chord_segment = Segmented(list(self._chord_radios.values()))
        self.chord_help = ElidedLabel("")
        self.chord_help.setObjectName("helper")
        inner.addWidget(self._divider())
        inner.addWidget(self._setting_row(self.chord_label, self.chord_help, self.chord_segment))
        column.addWidget(self.advanced_box)

        for radio in list(self._arpeggio_radios.values()) + list(self._chord_radios.values()):
            radio.toggled.connect(lambda checked: checked and self._on_layout_option_changed())
        self.advanced_button.toggled.connect(self._set_advanced_open)
        self._set_advanced_open(False)
        self._sync_layout_help()
        return box

    def _set_advanced_open(self, on: bool) -> None:
        self.advanced_box.setVisible(on)
        self.advanced_button.setText("收起高级选项" if on else "高级选项：琶音写法、和弦判定")
        self._refresh_advanced_icon()

    def _refresh_advanced_icon(self) -> None:
        theme = getattr(self, "theme", None)
        tokens = getattr(theme, "tokens", None) or {}
        color = tokens.get("accent", "#2f8a7a")
        name = "chevron_down" if self.advanced_button.isChecked() else "chevron_right"
        self.advanced_button.setIcon(icons.icon(name, color, 14))

    def arpeggio_mode(self) -> str:
        return next((mode for mode, radio in self._arpeggio_radios.items() if radio.isChecked()), ARPEGGIO_AUTO)

    def chord_mode(self) -> str:
        return next((mode for mode, radio in self._chord_radios.items() if radio.isChecked()), CHORD_STANDARD)

    def layout_options(self) -> dict:
        return {"arpeggio": self.arpeggio_mode(), "chord": self.chord_mode()}

    def _sync_layout_help(self) -> None:
        self.arpeggio_help.setText(self.ARPEGGIO_HELP[self.arpeggio_mode()])
        self.chord_help.setText(self.CHORD_HELP[self.chord_mode()])

    def _on_layout_option_changed(self) -> None:
        save_setting(ARPEGGIO, self.arpeggio_mode())
        save_setting(CHORD, self.chord_mode())
        self._sync_layout_help()
        self.rerender_preview()

    def rerender_preview(self) -> bool:
        """Lay out the score shown in 预览 again with the current settings (no new
        conversion: the conversion's own MIDI / MusicXML is used)."""
        item_id = self.preview.current_item_id() if hasattr(self, "preview") else ""
        if not item_id or self._busy:
            return False
        row = next((r for r in self.history_rows() if r.item.id == item_id), None)
        if row is None or row.item.status == "failed" or not _exists(row.item.keyboard_path):
            return False
        item = row.item
        from app.convert import rerender_keyboard

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            rerender_keyboard(
                item.kind, item.source_path, item.midi_path, item.musicxml_path, item.keyboard_path,
                style=self.score_style(), range_strategy=self.range_strategy(), **self.layout_options(),
            )
        except Exception as exc:  # noqa: BLE001
            self.show_toast(f"没能重新排版：{exc}"[:80], "error")
            return False
        finally:
            QApplication.restoreOverrideCursor()
        self.preview.show_item(item)
        self.show_toast("已按新设置重新排版", "success")
        return True

    def _build_options(self) -> QFrame:
        self.options_box = QFrame()
        self.options_box.setObjectName("options")
        column = QVBoxLayout(self.options_box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        self.kind_label = QLabel("乐器类型")
        self.kind_label.setObjectName("sectionLabel")
        self.piano_radio = QRadioButton("钢琴")
        self.other_radio = QRadioButton("其他乐器")
        self.piano_radio.setChecked(True)
        self.kind_segment = Segmented([self.piano_radio, self.other_radio])
        self.kind_help = ElidedLabel("")
        self.kind_help.setObjectName("helper")
        self.piano_radio.toggled.connect(lambda _c: self._sync_help())

        self._gpu_installed = gpu_installed()
        self.device_label = QLabel("计算设备")
        self.device_label.setObjectName("sectionLabel")
        self.gpu_radio = QRadioButton("GPU")
        self.cpu_radio = QRadioButton("CPU")
        self.device_segment = Segmented([self.gpu_radio, self.cpu_radio])
        if self._gpu_installed:
            self.gpu_radio.setChecked(True)
        else:
            self.cpu_radio.setChecked(True)
        self.gpu_radio.toggled.connect(lambda _checked: self._sync_gpu_controls())
        self.device_help = ElidedLabel("")
        self.device_help.setObjectName("helper")

        full_gpu_text = explain_text()
        first_line = full_gpu_text.strip().splitlines()[0] if full_gpu_text.strip() else ""
        self.gpu_note = QLabel(f"{first_line}\n下载时会显示进度和已下载大小；也可以用安装脚本手动安装。")
        self.gpu_note.setToolTip(full_gpu_text)
        self.gpu_note.setObjectName("gpuNote")
        self.gpu_note.setWordWrap(True)
        self.gpu_note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.gpu_download_button = QPushButton("下载 GPU 组件")
        self.gpu_download_button.setObjectName("small")
        self.gpu_download_button.clicked.connect(self._on_gpu_download_clicked)
        self.gpu_script_button = QPushButton("打开安装脚本")
        self.gpu_script_button.setObjectName("small")
        self.gpu_script_button.clicked.connect(self._open_gpu_script)
        for button in (self.gpu_download_button, self.gpu_script_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setIconSize(QSize(14, 14))
            tab_focus(button)
        gpu_actions = QHBoxLayout()
        gpu_actions.setContentsMargins(0, 0, 0, 0)
        gpu_actions.setSpacing(8)
        gpu_actions.addWidget(self.gpu_download_button)
        gpu_actions.addWidget(self.gpu_script_button)
        gpu_actions.addStretch(1)
        self.gpu_note.hide()
        self.gpu_download_button.hide()
        self.gpu_script_button.hide()
        gpu_box = QWidget()
        gpu_column = QVBoxLayout(gpu_box)
        gpu_column.setContentsMargins(14, 0, 14, 8)
        gpu_column.setSpacing(6)
        gpu_column.addWidget(self.gpu_note)
        gpu_column.addLayout(gpu_actions)
        self._gpu_box = gpu_box

        column.addWidget(self._setting_row(self.kind_label, self.kind_help, self.kind_segment))
        column.addWidget(self._divider())
        column.addWidget(self._setting_row(self.device_label, self.device_help, self.device_segment))
        column.addWidget(gpu_box)
        column.addWidget(self._divider())
        self._sync_help()
        return self.options_box

    def _build_style_row(self) -> QFrame:
        """详细 / 简洁 keyboard-score style; the choice is remembered between runs."""
        self.style_box = QFrame()
        self.style_box.setObjectName("styleBox")
        column = QVBoxLayout(self.style_box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.style_label = QLabel("键盘谱样式")
        self.style_label.setObjectName("sectionLabel")
        self.detailed_radio = QRadioButton("详细")
        self.simple_radio = QRadioButton("简洁")
        self.detailed_radio.setToolTip("每个小节分两行：上面旋律（一次一个键），下面伴奏")
        self.simple_radio.setToolTip("旋律和伴奏写在同一行")
        self.style_segment = Segmented([self.detailed_radio, self.simple_radio])
        self.style_help = ElidedLabel("")
        self.style_help.setObjectName("helper")
        if load_settings().get(SCORE_STYLE) == STYLE_SIMPLE:
            self.simple_radio.setChecked(True)
        else:
            self.detailed_radio.setChecked(True)
        self.detailed_radio.toggled.connect(self._on_style_toggled)
        column.addWidget(self._setting_row(self.style_label, self.style_help, self.style_segment))
        self._sync_style_help()
        return self.style_box

    def _build_range_row(self) -> QFrame:
        """Choose how notes outside the lyre range are transposed and folded."""
        self.range_box = QFrame()
        self.range_box.setObjectName("rangeBox")
        column = QVBoxLayout(self.range_box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.range_label = QLabel("音域处理")
        self.range_label.setObjectName("sectionLabel")
        self.range_combo = QComboBox()
        for strategy in (RANGE_STRATEGY_MODERN, RANGE_STRATEGY_SMART):
            self.range_combo.addItem(RANGE_STRATEGY_LABELS[strategy], strategy)
        self.range_combo.setToolTip(
            "新版逐音折叠；旧版智能方案尽量保持乐句和弦的八度关系。"
        )
        saved = load_settings().get(RANGE_STRATEGY, RANGE_STRATEGY_DEFAULT)
        index = self.range_combo.findData(saved)
        self.range_combo.setCurrentIndex(index if index >= 0 else 0)
        self.range_help = ElidedLabel("")
        self.range_help.setObjectName("helper")
        self.range_combo.currentIndexChanged.connect(self._on_range_strategy_changed)
        column.addWidget(self._setting_row(self.range_label, self.range_help, self.range_combo))
        self._sync_range_help()
        return self.range_box

    def range_strategy(self) -> str:
        value = self.range_combo.currentData()
        return value if value in RANGE_STRATEGY_LABELS else RANGE_STRATEGY_DEFAULT

    def _on_range_strategy_changed(self, _index: int) -> None:
        save_setting(RANGE_STRATEGY, self.range_strategy())
        self._sync_range_help()
        if hasattr(self, "_chord_radios"):
            self.rerender_preview()

    def _sync_range_help(self) -> None:
        help_text = {
            RANGE_STRATEGY_MODERN: "新版：每个音独立按八度折回 C3–B5",
            RANGE_STRATEGY_SMART: "旧版：尽量保持乐句和弦的整体八度",
        }
        self.range_help.setText(help_text[self.range_strategy()])

    def score_style(self) -> str:
        return STYLE_SIMPLE if self.simple_radio.isChecked() else STYLE_DETAILED

    def _on_style_toggled(self, checked: bool) -> None:
        save_setting(SCORE_STYLE, self.score_style())
        self._sync_style_help()
        if hasattr(self, "_chord_radios"):
            self.rerender_preview()

    def _sync_style_help(self) -> None:
        if self.score_style() == STYLE_SIMPLE:
            self.style_help.setText("全部写在一行")
        else:
            self.style_help.setText("旋律、伴奏分两行")

    def _build_progress(self) -> QWidget:
        self.progress_box = QWidget()
        col = QVBoxLayout(self.progress_box)
        col.setContentsMargins(2, 0, 2, 0)
        col.setSpacing(8)
        self.status_label = QLabel("选择文件开始转换")
        self.status_label.setObjectName("muted")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.elapsed_label = QLabel("")
        self.elapsed_label.setObjectName("elapsed")
        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("percent")
        self.percent_label.setMinimumWidth(40)
        self.percent_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addWidget(self.status_label, 1)
        row.addWidget(self.elapsed_label)
        row.addWidget(self.percent_label)
        self.progress_bar = SmoothProgressBar()
        col.addLayout(row)
        col.addWidget(self.progress_bar)
        self._set_progress_detail_visible(False)
        return self.progress_box

    def _build_banner(self) -> QFrame:
        banner = QFrame()
        banner.setObjectName("banner")
        banner.setProperty("kind", "success")
        self.result_banner = banner
        row = QHBoxLayout(banner)
        row.setContentsMargins(14, 12, 8, 12)
        row.setSpacing(12)
        self.banner_icon = icons.glyph_label("check", "bannerIcon", 24)
        self.banner_icon.setProperty("kind", "success")
        self.banner_title = QLabel("")
        self.banner_title.setObjectName("bannerTitle")
        self.banner_detail = QLabel("")
        self.banner_detail.setObjectName("bannerDetail")
        self.banner_detail.setWordWrap(True)
        self.banner_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text = QVBoxLayout()
        text.setContentsMargins(0, 1, 0, 0)
        text.setSpacing(2)
        text.addWidget(self.banner_title)
        text.addWidget(self.banner_detail)
        self.banner_keyboard_button = QPushButton("打开键盘谱")
        self.banner_keyboard_button.setObjectName("small")
        self.banner_keyboard_button.clicked.connect(
            lambda: open_path(self, getattr(self._last_result, "keyboard_path", None))
        )
        self.banner_folder_button = QPushButton("打开文件夹")
        self.banner_folder_button.setObjectName("small")
        self.banner_folder_button.clicked.connect(
            lambda: open_path(self, getattr(self._last_result, "folder", None))
        )
        self.banner_close = QPushButton(icons.glyph("close"))
        self.banner_close.setObjectName("closeBanner")
        self.banner_close.setProperty("glyph", icons.has_icon_font())
        self.banner_close.setToolTip("关闭")
        self.banner_close.clicked.connect(self.hide_banner)
        for button in (self.banner_keyboard_button, self.banner_folder_button, self.banner_close):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setIconSize(QSize(14, 14))
            tab_focus(button)
        row.addWidget(self.banner_icon, 0, Qt.AlignmentFlag.AlignTop)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 6, 0, 0)
        actions.setSpacing(8)
        actions.addWidget(self.banner_keyboard_button)
        actions.addWidget(self.banner_folder_button)
        actions.addStretch(1)
        text.addLayout(actions)
        row.addLayout(text, 1)
        row.addWidget(self.banner_close, 0, Qt.AlignmentFlag.AlignTop)
        banner.hide()
        return banner

    def _build_right_card(self, settings: dict) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 12, 16)
        layout.setSpacing(14)

        self.tab_preview_radio = QRadioButton("预览")
        self.tab_history_radio = QRadioButton("记录")
        self.tab_preview_radio.setToolTip("查看键盘谱")
        self.tab_history_radio.setToolTip("以前转换过的结果")
        self.tab_history_radio.setProperty("badge", True)
        self.tabs = Segmented([self.tab_preview_radio, self.tab_history_radio], tabs=True)
        self.history_count = QLabel("0", self.tab_history_radio)
        self.history_count.setObjectName("tabBadge")
        self.history_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.history_count.setFixedHeight(18)
        self.history_count.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.tab_history_radio.installEventFilter(self)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("search")
        self.search_edit.setPlaceholderText("搜索标题  Ctrl+F")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(120)
        self.search_edit.setMaximumWidth(260)
        self.search_edit.textChanged.connect(lambda _t: self._apply_history_filter())
        self._search_action = None

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 4, 0)
        head.setSpacing(12)
        head.addWidget(self.tabs, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        head.addWidget(self.search_edit, 1, Qt.AlignmentFlag.AlignVCenter)

        # 预览
        zoom = settings.get(PREVIEW_ZOOM, 100)
        section = settings.get(PREVIEW_SECTION, "keyboard")
        self.preview = PreviewPanel(zoom=zoom if isinstance(zoom, int) else 100, section=section)
        self.preview.copied.connect(lambda: self.show_toast("已复制", "success"))
        self.preview.zoom_changed.connect(lambda z: save_setting(PREVIEW_ZOOM, int(z)))
        self.preview.section_changed.connect(lambda s: save_setting(PREVIEW_SECTION, s))
        self.preview.open_file_requested.connect(lambda p: open_path(self, p))
        self.preview.open_folder_requested.connect(lambda p: open_path(self, p))
        preview_page = QWidget()
        preview_layout = QVBoxLayout(preview_page)
        preview_layout.setContentsMargins(0, 0, 4, 0)
        preview_layout.addWidget(self.preview)

        # 记录
        self.history_list = QListWidget()
        self.history_list.setSpacing(4)
        self.history_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.history_list.setFrameShape(QFrame.Shape.NoFrame)
        self.history_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history_list.customContextMenuRequested.connect(self._on_list_context_menu)
        self.history_list.currentItemChanged.connect(self._on_history_current_changed)
        self.history_list.itemActivated.connect(self._on_history_activated)
        self.history_list.itemClicked.connect(self._on_history_clicked)
        self.history_list.installEventFilter(self)

        self.history_empty = EmptyState("history", "")
        self.history_empty.hide()
        history_page = QWidget()
        history_layout = QVBoxLayout(history_page)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.setSpacing(0)
        history_layout.addWidget(self.history_list, 1)
        history_layout.addWidget(self.history_empty, 1)

        self.right_stack = QStackedWidget()
        self.right_stack.addWidget(preview_page)
        self.right_stack.addWidget(history_page)
        self._pages = {TAB_PREVIEW: preview_page, TAB_HISTORY: history_page}
        self.tab_preview_radio.toggled.connect(lambda on: on and self._set_tab(TAB_PREVIEW))
        self.tab_history_radio.toggled.connect(lambda on: on and self._set_tab(TAB_HISTORY))
        self.tab_history_radio.setChecked(True)
        self._set_tab(TAB_HISTORY)

        layout.addLayout(head)
        layout.addWidget(self.right_stack, 1)
        return card

    # ------------------------------------------------------------- theme
    def set_theme_mode(self, mode: str) -> None:
        if mode not in MODES:
            mode = MODE_SYSTEM
        radio = self._theme_radios[mode]
        if not radio.isChecked():
            radio.setChecked(True)  # re-enters through toggled
            return
        if mode != self.theme.mode or not self.theme.effective:
            save_setting(THEME, mode)
            self.theme.set_mode(mode)

    def _on_theme_changed(self, _name: str) -> None:
        tokens = self.theme.tokens
        self._root.set_tokens(tokens)
        self._artwork_header.set_tokens(tokens)
        self.toast.set_tokens(tokens)
        self.progress_bar.set_tokens(tokens)
        self.preview.set_tokens(tokens)
        self.refresh_icons()
        for row in self.history_rows():
            row.refresh_icons(tokens)
        for widget in (self.history_empty.icon_label, self.preview.empty_state.icon_label):
            repolish(widget)
        apply_window_frame(self, tokens)

    def refresh_icons(self) -> None:
        c = self.theme.tokens

        def ico(name, color, size=16):
            return icons.icon(name, color, size, c["disabled"])

        self.theme_system_radio.setIcon(ico("theme_system", c["text_2"], 14))
        self.theme_light_radio.setIcon(ico("theme_light", c["text_2"], 14))
        self.theme_dark_radio.setIcon(ico("theme_dark", c["text_2"], 14))
        for radio in self._theme_radios.values():
            radio.setIconSize(QSize(14, 14))
        self.tab_preview_radio.setIcon(ico("view", c["text_2"]))
        self.tab_history_radio.setIcon(ico("history", c["text_2"]))
        for radio in (self.tab_preview_radio, self.tab_history_radio):
            radio.setIconSize(QSize(16, 16))
        self.pick_button.setIcon(ico("audio", c["accent"], 14))
        self.pick_score_button.setIcon(ico("midi", c["accent"], 14))
        self.pick_pdf_button.setIcon(ico("pdf", c["accent"], 14))
        if hasattr(self, "advanced_button"):
            name = "chevron_down" if self.advanced_button.isChecked() else "chevron_right"
            self.advanced_button.setIcon(ico(name, c["accent"], 14))
        self.gpu_download_button.setIcon(ico("download", c["text_2"], 14))
        self.gpu_script_button.setIcon(ico("script", c["text_2"], 14))
        self.banner_keyboard_button.setIcon(ico("keyboard", c["text_2"], 14))
        self.banner_folder_button.setIcon(ico("folder", c["text_2"], 14))
        self._sync_start_icon()
        if self._search_action is not None:
            self.search_edit.removeAction(self._search_action)
            self._search_action = None
        search_icon = ico("search", c["muted"], 14)
        if not search_icon.isNull():
            self._search_action = self.search_edit.addAction(search_icon, QLineEdit.ActionPosition.LeadingPosition)

    def _sync_start_icon(self) -> None:
        c = self.theme.tokens
        if self._busy:
            icon = icons.icon("stop", c["danger_text"], 16, c["disabled"])
        else:
            icon = icons.icon("convert", c["on_primary"], 16, c["primary_disabled_text"])
        self.start_button.setIcon(icon)

    # ------------------------------------------------------------- toast
    def show_toast(self, text: str, tone: str = "info", action_text: str | None = None, action=None,
                   duration_ms: int | None = None) -> None:
        self.toast.show_message(text, tone, action_text, action, duration_ms)

    # ------------------------------------------------------------- tabs
    def current_tab(self) -> str:
        return TAB_PREVIEW if self.right_stack.currentWidget() is self._pages[TAB_PREVIEW] else TAB_HISTORY

    def show_preview_tab(self) -> None:
        self.tab_preview_radio.setChecked(True)
        self._set_tab(TAB_PREVIEW)

    def show_history_tab(self) -> None:
        self.tab_history_radio.setChecked(True)
        self._set_tab(TAB_HISTORY)

    def _set_tab(self, name: str) -> None:
        page = self._pages[name]
        if self.right_stack.currentWidget() is not page:
            self.right_stack.setCurrentWidget(page)
        self.search_edit.setVisible(name == TAB_HISTORY)
        if name == TAB_HISTORY and self._busy:
            self._history_viewed_during_job = True
        if name == TAB_HISTORY:
            QTimer.singleShot(0, self._fit_history_rows)

    def focus_search(self) -> None:
        self.show_history_tab()
        self.search_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search_edit.selectAll()

    def _place_tab_badge(self) -> None:
        radio = self.tab_history_radio
        badge = self.history_count
        badge.adjustSize()
        width = max(22, badge.sizeHint().width())
        badge.resize(width, 18)
        badge.move(radio.width() - width - 12, (radio.height() - 18) // 2)

    # ------------------------------------------------------- drag & drop
    def eventFilter(self, watched, event):
        et = event.type()
        if et in {QEvent.Type.DragEnter, QEvent.Type.DragMove}:
            self.dragEnterEvent(event)
            return True
        if et == QEvent.Type.DragLeave:
            self.drop_zone.set_dragging(False)
            return True
        if et == QEvent.Type.Drop:
            self.dropEvent(event)
            return True
        if watched is self.tab_history_radio and et in (QEvent.Type.Resize, QEvent.Type.Show):
            self._place_tab_badge()
        elif watched is self.search_edit and et == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape and self.search_edit.text():
                self.search_edit.clear()
                return True
            if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.visible_history_count():
                self.history_list.setFocus()
                if self.history_list.currentItem() is None or self.history_list.currentItem().isHidden():
                    for index in range(self.history_list.count()):
                        if not self.history_list.item(index).isHidden():
                            self.history_list.setCurrentRow(index)
                            break
                return True
        elif watched is self.history_list and et == QEvent.Type.KeyPress:
            row = self._current_row()
            if row is not None and event.key() == Qt.Key.Key_Delete:
                self.remove_history_row(row)
                return True
            if row is not None and event.key() == Qt.Key.Key_Space:
                self.preview_history_row(row)
                return True
        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._busy or _first_dropped_path(event) is None:
            self.drop_zone.set_dragging(False)
            event.ignore()
            return
        self.drop_zone.set_dragging(True)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.drop_zone.set_dragging(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self.drop_zone.set_dragging(False)
        path = None if self._busy else _first_dropped_path(event)
        if path is None:
            event.ignore()
            return
        event.acceptProposedAction()
        self.set_source_file(path)

    # ------------------------------------------------------- source file
    def set_source_file(self, path: Path) -> None:
        self._source = Path(path)
        self.file_label.setText(self._source.name)
        self.file_type_label.setText(file_type_label(self._source))
        suffix = self._source.suffix.lstrip(".").upper() or "?"
        self.file_badge.setText(suffix[:4])
        meta = [m for m in (_format_size(self._source), self._source.parent.name) if m]
        self.file_meta_label.setText(" · ".join(meta))
        self.file_meta_label.setToolTip(str(self._source))
        self.drop_empty.hide()
        self.drop_filled.show()
        self.drop_zone.set_state("filled")
        self.drop_zone.setToolTip("点击或拖入以更换文件")
        self.drop_zone.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.drop_zone.setFixedHeight(84)
        self._convert_layout.setStretchFactor(self.drop_zone, 0)
        self._convert_layout.setStretchFactor(self._convert_spacer, 1)
        if not self._busy:
            self.hide_banner()
        self._apply_source_mode()
        if not self._busy:
            self.start_button.setEnabled(True)
            self.status_label.setText("已就绪，按 Ctrl+Enter 或点击下方按钮开始")

    def _apply_source_mode(self) -> None:
        direct = _direct_score_path(self._source)
        self.options_box.setVisible(not direct)
        self.kind_label.setVisible(not direct)
        self.piano_radio.setVisible(not direct)
        self.other_radio.setVisible(not direct)
        self.device_label.setVisible(not direct)
        self.gpu_radio.setVisible(not direct)
        self.cpu_radio.setVisible(not direct)
        self._sync_gpu_controls()
        is_pdf = _is_pdf_path(self._source)
        self.pdf_note.setVisible(is_pdf)
        self.pdf_box.setVisible(is_pdf)
        if self._busy:
            return
        self.start_button.setText("转换为键盘谱" if direct else "开始转换")

    def _sync_help(self) -> None:
        if self.piano_radio.isChecked():
            self.kind_help.setText("钢琴独奏，识别最准")
        else:
            self.kind_help.setText("吉他、人声等通用识别")

    def _show_device_status(self) -> None:
        if not hasattr(self, "device_status_label"):
            return
        if _direct_score_path(self._source):
            text = "乐谱文件直接转换，无需选择设备"
        elif self.gpu_radio.isChecked() and self._gpu_installed:
            text = "使用 GPU"
        elif self.gpu_radio.isChecked():
            text = "使用 GPU 前需要先下载显卡组件"
        else:
            text = "使用 CPU，会比较慢"
        self.device_status_label.setText(text)

    def _sync_gpu_controls(self) -> None:
        self._gpu_installed = gpu_installed()
        audio = not _direct_score_path(self._source)
        want_gpu = audio and self.gpu_radio.isChecked()
        self.gpu_note.setVisible(want_gpu and not self._gpu_installed)
        self.gpu_download_button.setVisible(want_gpu and not self._gpu_installed)
        self.gpu_script_button.setVisible(want_gpu)
        self._gpu_box.setVisible(want_gpu)
        if self.gpu_radio.isChecked():
            self.device_help.setText(
                "显卡加速，速度快" if self._gpu_installed else "需先下载显卡组件"
            )
        else:
            self.device_help.setText("无需额外组件，较慢")
        self._show_device_status()

    def _pick(self, title: str, filters: str) -> None:
        if self._busy:
            return
        start_dir = str(self._source.parent) if self._source is not None else ""
        chosen, _ = QFileDialog.getOpenFileName(self, title, start_dir, filters)
        if chosen:
            self.set_source_file(Path(chosen))

    def _choose_any_file(self) -> None:
        self._pick("选择文件", all_files_filter())

    def _choose_file(self) -> None:
        self._pick("选择音频文件", AUDIO_FILTER)

    def _choose_score_file(self) -> None:
        self._pick("选择 MIDI / MusicXML", SCORE_FILTER)

    def _choose_pdf_file(self) -> None:
        self._pick("选择 PDF 或图片五线谱", PDF_FILTER)

    def _wants_gpu(self) -> bool:
        return self.gpu_radio.isChecked() and not _direct_score_path(self._source)

    # ------------------------------------------------------------- GPU
    def _open_gpu_script(self) -> None:
        from app.gpu_setup import install_script_path
        import subprocess

        path = install_script_path()
        if not path.is_file():
            self.show_toast(f"找不到安装脚本：{path}", "error")
            return
        subprocess.Popen(["explorer", "/select,", str(path)])

    def _on_gpu_download_clicked(self) -> None:
        if self._downloading:
            if self._cancel is not None:
                self._cancel.set()
                self.status_label.setText("正在取消下载…")
            return
        if self._busy:
            return
        answer = QMessageBox.question(
            self,
            "下载 GPU 组件",
            explain_text() + "\n\n开始下载后，进度条会显示百分比和已下载大小。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_gpu_download()

    def _start_gpu_download(self) -> None:
        self._enter_busy_ui("准备下载 GPU 组件")
        self._downloading = True
        self.gpu_download_button.setText("取消下载")
        self.gpu_download_button.setEnabled(True)
        self.start_button.setEnabled(False)
        self._thread = QThread(self)
        worker = GpuDownloadWorker(self._cancel)
        self._worker = worker
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(self._on_gpu_download_finished, Qt.ConnectionType.QueuedConnection)
        worker.cancelled.connect(self._on_gpu_download_cancelled, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(self._on_gpu_download_failed, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(self._thread.quit)
        worker.cancelled.connect(self._thread.quit)
        worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(worker.deleteLater)
        self._thread.start()

    def _on_gpu_download_finished(self) -> None:
        self._downloading = False
        self.progress_bar.set_ratio(1.0)
        self._reset_idle_ui("GPU 组件已安装")
        self._sync_gpu_controls()
        self.show_toast("GPU 组件已安装，可以用显卡转换了", "success")

    def _on_gpu_download_cancelled(self) -> None:
        self._downloading = False
        self.progress_bar.set_ratio(0.0, animate=False)
        self._reset_idle_ui("已取消下载")

    def _on_gpu_download_failed(self, message: str) -> None:
        self._downloading = False
        self.progress_bar.set_ratio(0.0, animate=False)
        self._reset_idle_ui("GPU 组件下载失败")
        QMessageBox.warning(self, "PianoConvert", message or "GPU 组件下载失败")

    # ------------------------------------------------------------- jobs
    def _on_start_clicked(self) -> None:
        if self._busy:
            if self._cancel is not None:
                self._cancel.set()
                self.status_label.setText("正在取消…")
                self.start_button.setEnabled(False)
            return
        if self._source is None:
            return
        if self._wants_gpu() and not gpu_installed():
            self._on_gpu_download_clicked()
            return
        self._start_job()

    def _set_inputs_enabled(self, enabled: bool) -> None:
        for widget in (
            self.pick_button,
            self.pick_score_button,
            self.pick_pdf_button,
            self.change_button,
            self.piano_radio,
            self.other_radio,
            self.gpu_radio,
            self.cpu_radio,
            self.gpu_download_button,
            self.gpu_script_button,
            self.detailed_radio,
            self.simple_radio,
            self.range_combo,
        ):
            widget.setEnabled(enabled)
        self.drop_zone.set_interactive(enabled)

    def _set_progress_detail_visible(self, on: bool) -> None:
        self.progress_bar.setVisible(on)
        self.percent_label.setVisible(on)
        self.elapsed_label.setVisible(on)
        self.progress_bar.set_running(on)
        self.status_label.setObjectName("status" if on else "muted")
        repolish(self.status_label)

    def _enter_busy_ui(self, status_text: str = "准备中") -> None:
        """Switch the window into its running state (no worker is started here)."""
        self._busy = True
        self._cancel = Event()
        self._history_viewed_during_job = False
        self.hide_banner()
        self._set_inputs_enabled(False)
        self.start_button.setText("取消")
        set_prop(self.start_button, "danger", True)
        self.start_button.setEnabled(True)
        self._sync_start_icon()
        self.progress_bar.set_ratio(0.0, animate=False)
        self.percent_label.setText("0%")
        self.status_label.setText(status_text)
        self.progress_box.show()
        self._set_progress_detail_visible(True)
        self._job_started = time.monotonic()
        self._tick_elapsed()
        self._elapsed_timer.start()

    def _start_job(self) -> None:
        if self._busy or self._source is None:
            return
        self._enter_busy_ui("准备中")
        self._job_style = self.score_style()
        self._job_range_strategy = self.range_strategy()
        self._job_layout = self.layout_options()
        for engine in self._engines.values():
            engine.device = "cuda" if self.gpu_radio.isChecked() else "cpu"

        if _is_pdf_path(self._source):
            kind = "pdf"
        elif _is_score_path(self._source):
            kind = "score"
        else:
            kind = "piano" if self.piano_radio.isChecked() else "other"
        self._thread = QThread(self)
        self._worker = ConvertWorker(self._convert_fn, self._source, kind, self._cancel)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        self._worker.finished.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)
        self._worker.failed.connect(self._on_failed, Qt.ConnectionType.QueuedConnection)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()

    def _elapsed_seconds(self) -> float:
        return 0.0 if self._job_started is None else time.monotonic() - self._job_started

    def _tick_elapsed(self) -> None:
        self.elapsed_label.setText(f"已用 {_format_elapsed(self._elapsed_seconds())}")

    def _on_progress(self, message: str, ratio: float) -> None:
        ratio = max(0.0, min(1.0, ratio))
        value = int(ratio * 100)
        self.status_label.setText(message)
        self.progress_bar.set_ratio(ratio)
        self.percent_label.setText(f"{value}%")

    def _on_finished(self, result) -> None:
        status = getattr(result, "status", "")
        elapsed = self._elapsed_seconds()
        if status == "cancelled":
            self.progress_bar.set_ratio(0.0, animate=False)
            self._reset_idle_ui("已取消")
            return
        if status in {"success", "partial", "failed"}:
            labels = {"success": "完成", "partial": "部分成功", "failed": "转换失败"}
            status_text = labels.get(status, "转换结束")
            try:
                if status == "success":
                    self.progress_bar.set_ratio(1.0)
                item = self._save_history(result)
                self.reload_history()
                self._show_new_result(item)
            finally:
                self._reset_idle_ui(status_text)
                self.show_result_banner(result, elapsed)
            return
        self._reset_idle_ui("转换结束")

    def _show_new_result(self, item: HistoryItem) -> None:
        """Select the new record; a usable keyboard score goes straight into 预览."""
        if not self.select_history_id(item.id) and self.history_list.count():
            self.history_list.setCurrentRow(0)
        if item.status == "failed" or not _exists(item.keyboard_path):
            return
        if self._history_viewed_during_job and self.current_tab() == TAB_HISTORY:
            self.show_toast(f"「{item.title}」转换完成", "success", "查看", self.show_preview_tab)
        else:
            self.show_preview_tab()

    def _on_failed(self, message: str) -> None:
        self.progress_bar.set_ratio(0.0, animate=False)
        title = self._source.stem if self._source is not None else ""
        self._reset_idle_ui("转换失败")
        self.show_result_banner(SimpleNamespace(status="failed", error=message or "转换失败", title=title))

    def _reset_idle_ui(self, status_text: str = "") -> None:
        self._busy = False
        self._cancel = None
        self._elapsed_timer.stop()
        self._set_inputs_enabled(True)
        self.gpu_download_button.setText("下载 GPU 组件")
        set_prop(self.start_button, "danger", False)
        self.progress_bar.setTextVisible(False)
        self._set_progress_detail_visible(False)
        self._apply_source_mode()
        self.start_button.setEnabled(self._source is not None)
        self._sync_start_icon()
        if status_text:
            self.status_label.setText(status_text)

    # ------------------------------------------------------------ banner
    def show_result_banner(self, result, elapsed: float | None = None) -> None:
        self._last_result = result
        status = getattr(result, "status", "")
        title = getattr(result, "title", "") or ""
        took = f" · 用时 {_format_elapsed(elapsed)}" if elapsed else ""
        if status == "success":
            kind, glyph, head = "success", "check", "转换完成"
            detail = f"{title}{took}"
        elif status == "partial":
            kind, glyph, head = "partial", "warning", "部分成功"
            detail = f"{_partial_note(getattr(result, 'error', '') or '')}{took}"
        else:
            kind, glyph, head = "error", "close", "转换失败"
            detail = getattr(result, "error", "") or "转换失败"
        icons.set_glyph(self.banner_icon, glyph)
        if not icons.has_icon_font():
            self.banner_icon.setText({"success": "✓", "partial": "!", "error": "×"}[kind])
        self.banner_title.setText(head)
        self.banner_detail.setText(detail)
        self.banner_detail.setToolTip(title)
        set_prop(self.result_banner, "kind", kind)
        set_prop(self.banner_icon, "kind", kind)
        has_outputs = kind != "error"
        self.banner_keyboard_button.setVisible(has_outputs)
        self.banner_folder_button.setVisible(has_outputs)
        self.banner_keyboard_button.setEnabled(_exists(getattr(result, "keyboard_path", None)))
        self.banner_folder_button.setEnabled(_exists(getattr(result, "folder", None)))
        self.result_banner.show()
        self.progress_box.hide()

    def hide_banner(self) -> None:
        self.result_banner.hide()
        self.progress_box.show()

    # ----------------------------------------------------------- history
    def _save_history(self, result) -> HistoryItem:
        item = HistoryItem(
            id=str(uuid4()),
            title=result.title,
            source_path=str(result.source_path),
            kind=result.kind,
            created_at=datetime.now().isoformat(),
            status=result.status,
            midi_path=str(result.midi_path),
            musicxml_path=str(result.musicxml_path),
            folder=str(result.folder),
            error=result.error or "",
            keyboard_path=str(getattr(result, "keyboard_path", "") or ""),
        )
        append_item(item)
        return item

    def reload_history(self) -> None:
        try:
            items = load_items()
        except (OSError, ValueError):
            items = []
        selected = self._current_row()
        keep_id = selected.item.id if selected is not None else ""
        blocked = self.history_list.blockSignals(True)
        self.history_list.clear()
        tokens = self.theme.tokens
        for item in items[::-1]:
            row = HistoryRow(item, self.history_list)
            row.refresh_icons(tokens)
            row.menu_requested.connect(self._show_history_menu)
            list_item = QListWidgetItem()
            list_item.setData(Qt.ItemDataRole.UserRole, item.id)
            self.history_list.addItem(list_item)
            self.history_list.setItemWidget(list_item, row)
        self.history_list.blockSignals(blocked)
        self.history_count.setText(str(len(items)))
        self._place_tab_badge()
        if keep_id:
            self.select_history_id(keep_id, load_preview=False)
        self._apply_history_filter()

    def history_rows(self) -> list[HistoryRow]:
        rows = []
        for index in range(self.history_list.count()):
            widget = self.history_list.itemWidget(self.history_list.item(index))
            if isinstance(widget, HistoryRow):
                rows.append(widget)
        return rows

    def _row_for(self, item) -> HistoryRow | None:
        widget = self.history_list.itemWidget(item) if item is not None else None
        return widget if isinstance(widget, HistoryRow) else None

    def _current_row(self) -> HistoryRow | None:
        return self._row_for(self.history_list.currentItem())

    def select_history_id(self, item_id: str, load_preview: bool = True) -> bool:
        for index in range(self.history_list.count()):
            list_item = self.history_list.item(index)
            if list_item.data(Qt.ItemDataRole.UserRole) == item_id:
                if not load_preview:
                    blocked = self.history_list.blockSignals(True)
                    self.history_list.setCurrentItem(list_item)
                    self.history_list.blockSignals(blocked)
                    row = self._row_for(list_item)
                    if row is not None:
                        row.set_selected(True)
                else:
                    self.history_list.setCurrentItem(list_item)
                    row = self._row_for(list_item)
                    if row is not None and self.preview.current_item_id() != item_id:
                        self.preview.show_item(row.item)
                self.history_list.scrollToItem(list_item)
                return True
        return False

    def visible_history_count(self) -> int:
        return sum(
            1 for i in range(self.history_list.count()) if not self.history_list.item(i).isHidden()
        )

    def _apply_history_filter(self) -> None:
        query = self.search_edit.text()
        for index in range(self.history_list.count()):
            item = self.history_list.item(index)
            widget = self.history_list.itemWidget(item)
            item.setHidden(isinstance(widget, HistoryRow) and not widget.matches(query))
        visible = self.visible_history_count()
        if self.history_list.count() == 0:
            self.history_empty.set_glyph("history")
            self.history_empty.setText("还没有转换记录\n转换完成后会显示在这里")
        elif visible == 0:
            self.history_empty.set_glyph("search")
            self.history_empty.setText(f"没有标题包含「{query.strip()}」的记录\n换个关键词试试，或按 Esc 清空搜索")
        self.history_empty.setVisible(visible == 0)
        self.history_list.setVisible(visible > 0)
        self._fit_history_rows()

    def _preload_latest(self) -> None:
        """Startup: put the newest usable keyboard score into 预览 (the tab stays on 记录)."""
        for row in self.history_rows():
            if row.item.status != "failed" and _exists(row.item.keyboard_path):
                self.select_history_id(row.item.id)
                return

    def _on_history_current_changed(self, current, previous) -> None:
        for item, on in ((previous, False), (current, True)):
            row = self._row_for(item)
            if row is not None:
                row.set_selected(on)
        row = self._row_for(current)
        if row is not None and self.preview.current_item_id() != row.item.id:
            self.preview.show_item(row.item)

    def _on_history_clicked(self, item) -> None:
        """Single click: show the record in 预览 (the tab switches once it is not a double click)."""
        row = self._row_for(item)
        if row is None:
            return
        if self.preview.current_item_id() != row.item.id:
            self.preview.show_item(row.item)
        self._pending_click_id = row.item.id
        self._click_timer.start(max(120, QApplication.doubleClickInterval()))

    def _flush_history_click(self) -> None:
        if self._pending_click_id:
            self._pending_click_id = ""
            self.show_preview_tab()

    def preview_history_row(self, row: HistoryRow) -> None:
        self._click_timer.stop()
        self._pending_click_id = ""
        self.select_history_id(row.item.id, load_preview=False)
        self.preview.show_item(row.item)
        self.show_preview_tab()

    def _on_history_activated(self, item) -> None:
        """Double click or Enter: open the keyboard score in the system's text editor."""
        self._click_timer.stop()
        self._pending_click_id = ""
        row = self._row_for(item)
        if row is not None:
            row.open_keyboard()

    def _on_list_context_menu(self, pos: QPoint) -> None:
        item = self.history_list.itemAt(pos) or self.history_list.currentItem()
        row = self._row_for(item)
        if row is not None:
            self._show_history_menu(row, self.history_list.viewport().mapToGlobal(pos))

    def _history_menu(self, row: HistoryRow) -> QMenu:
        c = self.theme.tokens
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        item = row.item
        has_keyboard = _exists(item.keyboard_path)
        preview = menu.addAction(icons.icon("view", c["text_2"], 16, c["disabled"]), "预览")
        preview.triggered.connect(lambda: self.preview_history_row(row))
        keyboard = menu.addAction(icons.icon("keyboard", c["text_2"], 16, c["disabled"]), "打开键盘谱")
        keyboard.setEnabled(has_keyboard)
        keyboard.triggered.connect(row.open_keyboard)
        folder = menu.addAction(icons.icon("folder", c["text_2"], 16, c["disabled"]), "打开文件夹")
        folder.setEnabled(_exists(item.folder))
        folder.triggered.connect(lambda: open_path(self, item.folder))
        menu.addSeparator()
        remove = menu.addAction(icons.icon("delete", c["danger_text"], 16, c["disabled"]), "从记录中移除")
        remove.setToolTip("只移除这条记录，转换好的文件不会删除")
        remove.triggered.connect(lambda: self.remove_history_row(row))
        return menu

    def _show_history_menu(self, row: HistoryRow, global_pos: QPoint) -> None:
        self._click_timer.stop()
        self._pending_click_id = ""
        self.select_history_id(row.item.id, load_preview=True)
        self._history_menu(row).exec(global_pos)

    def remove_history_row(self, row: HistoryRow) -> bool:
        """Remove one record (files on disk are kept); the toast offers 撤销."""
        try:
            removed = remove_item(row.item.id)
        except (OSError, ValueError):
            self.show_toast("没能更新转换记录", "error")
            return False
        if removed is None:
            return False
        item, index = removed
        if self.preview.current_item_id() == item.id:
            self.preview.clear()
        self.reload_history()

        def undo() -> None:
            try:
                restore_item(item, index)
            except (OSError, ValueError):
                self.show_toast("没能恢复这条记录", "error")
                return
            self.reload_history()
            self.select_history_id(item.id, load_preview=False)
            self.show_toast("已恢复", "success")

        self.show_toast(f"已从记录中移除「{item.title}」（文件仍保留）", "info", "撤销", undo)
        return True

    def _fit_history_rows(self) -> None:
        width = max(280, self.history_list.viewport().width() - 2 * self.history_list.spacing())
        for index in range(self.history_list.count()):
            item = self.history_list.item(index)
            widget = self.history_list.itemWidget(item)
            if widget is None:
                height = 96
            elif widget.hasHeightForWidth():
                height = max(widget.heightForWidth(width), widget.sizeHint().height())
            else:
                height = widget.sizeHint().height()
            item.setSizeHint(QSize(width, height))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "history_list"):
            self._fit_history_rows()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._fit_history_rows()
        self._place_tab_badge()
        apply_window_frame(self, self.theme.tokens)
        if not self._initial_focus_done:
            self._initial_focus_done = True
            self._root.setFocus(Qt.FocusReason.OtherFocusReason)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._cancel is not None:
            self._cancel.set()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
        super().closeEvent(event)
