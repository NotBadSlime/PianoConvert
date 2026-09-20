from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from threading import Event
from uuid import uuid4

from PySide6.QtCore import QEvent, QObject, QThread, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from app.convert import AUDIO_SUFFIXES
from app.history import HistoryItem, append_item
from app.paths import history_path
from app.ui.styles import APP_QSS

KIND_LABELS = {"piano": "钢琴", "other": "其他乐器"}
STATUS_LABELS = {"success": "成功", "partial": "部分成功", "failed": "失败"}
AUDIO_FILTER = "音频文件 (*.mp3 *.wav *.flac *.ogg *.m4a)"


def default_engines():
    from app.engines.basic_pitch import BasicPitchEngine
    from app.engines.kong_piano import KongPianoEngine

    return {"piano": KongPianoEngine(), "other": BasicPitchEngine()}


def _first_audio_path(event: QDragEnterEvent | QDropEvent) -> Path | None:
    mime = event.mimeData()
    if not mime.hasUrls():
        return None
    for url in mime.urls():
        path = Path(url.toLocalFile())
        if path.suffix.lower() in AUDIO_SUFFIXES:
            return path
    return None


def _format_time(raw: str) -> str:
    try:
        return datetime.fromisoformat(raw).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return raw.replace("T", " ")


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


class HistoryRow(QWidget):
    def __init__(self, item: HistoryItem, parent: QWidget | None = None):
        super().__init__(parent)
        self.item = item

        title = QLabel(item.title)
        title.setStyleSheet("font-weight: 600;")
        meta = QLabel(
            " · ".join(
                [
                    KIND_LABELS.get(item.kind, item.kind),
                    _format_time(item.created_at),
                    STATUS_LABELS.get(item.status, item.status),
                ]
            )
        )
        meta.setObjectName("muted")

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(4)
        text_col.addWidget(title)
        text_col.addWidget(meta)
        if item.status == "partial":
            note = QLabel("谱面导出失败")
            note.setObjectName("note")
            text_col.addWidget(note)
        elif item.status == "failed" and item.error:
            err = QLabel(item.error)
            err.setObjectName("error")
            err.setWordWrap(True)
            text_col.addWidget(err)

        self.midi_button = QPushButton("打开 MIDI")
        self.xml_button = QPushButton("打开 MusicXML")
        self.folder_button = QPushButton("打开文件夹")
        self.midi_button.clicked.connect(lambda: self._open(item.midi_path))
        self.xml_button.clicked.connect(lambda: self._open(item.musicxml_path))
        self.folder_button.clicked.connect(lambda: self._open(item.folder))

        if item.status == "partial":
            self.xml_button.setEnabled(False)
        elif item.status == "failed":
            self.midi_button.hide()
            self.xml_button.hide()
            self.folder_button.hide()

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)
        row.addLayout(text_col, 1)
        row.addWidget(self.midi_button)
        row.addWidget(self.xml_button)
        row.addWidget(self.folder_button)
        self.setMinimumHeight(72)

    def _open(self, path: str) -> None:
        target = Path(path) if path else Path()
        if not path or not target.exists():
            QMessageBox.warning(self.window(), "PianoConvert", "文件不在了")
            return
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except OSError:
            QMessageBox.warning(self.window(), "PianoConvert", "文件不在了")


class MainWindow(QMainWindow):
    def __init__(self, convert_fn=None, engines=None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("PianoConvert")
        self.setStyleSheet(APP_QSS)
        self.setAcceptDrops(True)

        self._source: Path | None = None
        self._busy = False
        self._cancel: Event | None = None
        self._thread: QThread | None = None
        self._worker: ConvertWorker | None = None
        if engines is None:
            try:
                engines = default_engines()
            except Exception:
                engines = {}
        self._engines = engines
        self._convert_fn = convert_fn or self._make_default_convert()

        self._build_ui()
        self._show_device_status()
        self.reload_history()

    def _make_default_convert(self):
        engines = self._engines

        def _fn(source, kind, cancel, on_progress):
            from app.convert import run as convert_run

            return convert_run(
                source,
                kind,
                engines,
                cancel,
                output_root=None,
                on_progress=on_progress,
            )

        return _fn

    def _show_device_status(self) -> None:
        try:
            from app.device import resolve_device

            device = resolve_device()
        except Exception:
            device = "cpu"
        if device == "cpu":
            self.statusBar().showMessage("使用 CPU，会比较慢")

    def _build_ui(self) -> None:
        hero = QLabel("把音频转成乐谱")
        hero.setObjectName("hero")
        subtitle = QLabel("选择一首音频，生成 MIDI 和 MusicXML")
        subtitle.setObjectName("muted")

        self.pick_button = QPushButton("选择音频文件")
        self.pick_button.clicked.connect(self._choose_file)
        self.file_label = QLabel("尚未选择文件")
        self.file_label.setObjectName("muted")

        file_row = QHBoxLayout()
        file_row.addWidget(self.pick_button)
        file_row.addWidget(self.file_label, 1)

        self.piano_radio = QRadioButton("钢琴")
        self.other_radio = QRadioButton("其他乐器")
        self.piano_radio.setChecked(True)
        kind_row = QHBoxLayout()
        kind_row.addWidget(self.piano_radio)
        kind_row.addWidget(self.other_radio)
        kind_row.addStretch(1)

        self.start_button = QPushButton("开始转换")
        self.start_button.setObjectName("primary")
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self._on_start_clicked)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.status_label = QLabel("选择音频文件开始转换")
        self.status_label.setObjectName("muted")

        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(12)
        card_layout.addWidget(hero)
        card_layout.addWidget(subtitle)
        card_layout.addLayout(file_row)
        card_layout.addWidget(QLabel("乐器类型"))
        card_layout.addLayout(kind_row)
        card_layout.addWidget(self.start_button)
        card_layout.addWidget(self.progress_bar)
        card_layout.addWidget(self.status_label)

        card = QFrame()
        card.setObjectName("card")
        card.setLayout(card_layout)

        history_title = QLabel("转换记录")
        history_title.setStyleSheet("font-weight: 600; font-size: 16px;")
        self.history_list = QListWidget()
        self.history_list.setSpacing(4)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(16)
        layout.addWidget(card)
        layout.addWidget(history_title)
        layout.addWidget(self.history_list, 1)
        self.setCentralWidget(root)

        for widget in (root, card, self.history_list):
            widget.setAcceptDrops(True)
            widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        et = event.type()
        if et in {QEvent.Type.DragEnter, QEvent.Type.DragMove}:
            self.dragEnterEvent(event)
            return True
        if et == QEvent.Type.Drop:
            self.dropEvent(event)
            return True
        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._busy or _first_audio_path(event) is None:
            event.ignore()
            return
        event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        path = None if self._busy else _first_audio_path(event)
        if path is None:
            event.ignore()
            return
        event.acceptProposedAction()
        self.set_source_file(path)

    def set_source_file(self, path: Path) -> None:
        self._source = Path(path)
        self.file_label.setText(self._source.name)
        if not self._busy:
            self.start_button.setEnabled(True)

    def _choose_file(self) -> None:
        if self._busy:
            return
        chosen, _ = QFileDialog.getOpenFileName(self, "选择音频文件", "", AUDIO_FILTER)
        if chosen:
            self.set_source_file(Path(chosen))

    def _on_start_clicked(self) -> None:
        if self._busy:
            if self._cancel is not None:
                self._cancel.set()
                self.status_label.setText("正在取消…")
            return
        if self._source is None:
            return
        self._start_job()

    def _start_job(self) -> None:
        if self._busy or self._source is None:
            return
        self._busy = True
        self._cancel = Event()
        self.start_button.setText("取消")
        self.start_button.setEnabled(True)
        self.pick_button.setEnabled(False)
        self.piano_radio.setEnabled(False)
        self.other_radio.setEnabled(False)
        self.progress_bar.setValue(0)
        self.status_label.setText("准备中")

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

    def _on_progress(self, message: str, ratio: float) -> None:
        self.status_label.setText(message)
        self.progress_bar.setValue(int(max(0.0, min(1.0, ratio)) * 100))

    def _on_finished(self, result) -> None:
        status = getattr(result, "status", "")
        if status == "cancelled":
            self.progress_bar.setValue(0)
            self._reset_idle_ui("已取消")
            return
        if status in {"success", "partial", "failed"}:
            labels = {"success": "完成", "partial": "部分成功", "failed": "转换失败"}
            status_text = labels.get(status, "转换结束")
            try:
                self._save_history(result)
                self.reload_history()
                if self.history_list.count():
                    self.history_list.setCurrentRow(0)
                if status == "success":
                    self.progress_bar.setValue(100)
            finally:
                self._reset_idle_ui(status_text)
            return
        self._reset_idle_ui("转换结束")

    def _on_failed(self, message: str) -> None:
        self.progress_bar.setValue(0)
        self._reset_idle_ui(message or "转换失败")
        QMessageBox.warning(self, "PianoConvert", message or "转换失败")

    def _reset_idle_ui(self, status_text: str = "") -> None:
        self._busy = False
        self._cancel = None
        self.start_button.setText("开始转换")
        self.start_button.setEnabled(self._source is not None)
        self.pick_button.setEnabled(True)
        self.piano_radio.setEnabled(True)
        self.other_radio.setEnabled(True)
        if status_text:
            self.status_label.setText(status_text)

    def _save_history(self, result) -> None:
        append_item(
            HistoryItem(
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
            )
        )

    def reload_history(self) -> None:
        path = history_path()
        if path.exists():
            items = [HistoryItem(**row) for row in json.loads(path.read_text(encoding="utf-8"))]
        else:
            items = []
        self.history_list.clear()
        for item in items[::-1]:
            row = HistoryRow(item, self.history_list)
            list_item = QListWidgetItem()
            list_item.setSizeHint(row.sizeHint())
            self.history_list.addItem(list_item)
            self.history_list.setItemWidget(list_item, row)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._cancel is not None:
            self._cancel.set()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
        super().closeEvent(event)
