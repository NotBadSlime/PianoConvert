from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QUrl, Qt, QThread, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStyle,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app.engines.kong_piano import KongPianoEngine
from app.jobs import TaskQueueRunner
from app.models import TaskStatus, TranscriptionParameters, TranscriptionTask
from app.paths import default_output_dir
from app.ui.theme import DARK_STYLESHEET
from app.ui.piano_roll import PianoRollWidget


SUPPORTED_AUDIO = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"}


class TranscriptionThread(QThread):
    task_updated = Signal(object)
    message = Signal(str)
    finished_all = Signal()

    def __init__(self, tasks: list[TranscriptionTask], device: str, parent=None) -> None:
        super().__init__(parent)
        self.tasks = tasks
        self.device = device
        self.runner: TaskQueueRunner | None = None

    def run(self) -> None:
        self.runner = TaskQueueRunner(
            engine=KongPianoEngine(),
            device=self.device,
            on_task_update=self.task_updated.emit,
        )
        for task in self.tasks:
            if task.status is TaskStatus.COMPLETED:
                continue
            self.message.emit(f"Running {task.display_name}")
            self.runner.run_one(task)
        self.finished_all.emit()

    def cancel(self) -> None:
        if self.runner:
            self.runner.request_cancel()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PianoConvert")
        self.resize(1180, 760)
        self.tasks: list[TranscriptionTask] = []
        self.worker: TranscriptionThread | None = None
        self.output_dir = default_output_dir()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.positionChanged.connect(self._on_player_position)

        self._build_toolbar()
        self._build_body()
        self._apply_style()
        self.statusBar().showMessage("Ready")

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        style = self.style()

        import_action = QAction(style.standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton), "Import Audio", self)
        import_action.triggered.connect(self.import_audio)
        toolbar.addAction(import_action)

        folder_action = QAction(style.standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon), "Import Folder", self)
        folder_action.triggered.connect(self.import_folder)
        toolbar.addAction(folder_action)

        toolbar.addSeparator()

        self.start_action = QAction(style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay), "Start Queue", self)
        self.start_action.triggered.connect(self.start_queue)
        toolbar.addAction(self.start_action)

        self.stop_action = QAction(style.standardIcon(QStyle.StandardPixmap.SP_MediaStop), "Stop", self)
        self.stop_action.triggered.connect(self.cancel_queue)
        self.stop_action.setEnabled(False)
        toolbar.addAction(self.stop_action)

        toolbar.addSeparator()
        toolbar.addWidget(QLabel("Device "))
        self.device_combo = QComboBox()
        self.device_combo.addItems(["cuda", "cpu"])
        toolbar.addWidget(self.device_combo)

        toolbar.addSeparator()
        open_output = QAction(style.standardIcon(QStyle.StandardPixmap.SP_DirIcon), "Open Output", self)
        open_output.triggered.connect(self.open_output_dir)
        toolbar.addAction(open_output)

    def _build_body(self) -> None:
        root = QSplitter(Qt.Orientation.Horizontal)
        root.setChildrenCollapsible(False)
        self.setCentralWidget(root)

        self.task_list = QListWidget()
        self.task_list.currentRowChanged.connect(self._select_task)
        root.addWidget(self.task_list)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        header = QHBoxLayout()
        self.current_title = QLabel("No task selected")
        self.current_title.setObjectName("Title")
        header.addWidget(self.current_title, 1)

        self.play_button = QPushButton("Play")
        self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.play_button.clicked.connect(self.toggle_playback)
        self.play_button.setEnabled(False)
        header.addWidget(self.play_button)

        zoom_out = QPushButton("-")
        zoom_out.clicked.connect(lambda: self.piano_roll.zoom_out())
        header.addWidget(zoom_out)
        zoom_in = QPushButton("+")
        zoom_in.clicked.connect(lambda: self.piano_roll.zoom_in())
        header.addWidget(zoom_in)

        self.export_button = QPushButton("Export MIDI")
        self.export_button.clicked.connect(self.export_midi)
        self.export_button.setEnabled(False)
        header.addWidget(self.export_button)
        center_layout.addLayout(header)

        self.piano_roll = PianoRollWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setWidget(self.piano_roll)
        center_layout.addWidget(scroll, 1)
        root.addWidget(center)

        root.addWidget(self._build_parameters_panel())
        root.setSizes([260, 720, 260])

    def _build_parameters_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        title = QLabel("Transcription")
        title.setObjectName("PanelTitle")
        layout.addWidget(title)

        form = QFormLayout()
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(["balanced", "precise"])
        self.preset_combo.currentTextChanged.connect(self._preset_changed)
        form.addRow("Preset", self.preset_combo)

        self.onset_spin = self._threshold_spin(0.3)
        form.addRow("Onset", self.onset_spin)
        self.offset_spin = self._threshold_spin(0.3)
        form.addRow("Offset", self.offset_spin)
        self.frame_spin = self._threshold_spin(0.1)
        form.addRow("Frame", self.frame_spin)
        self.min_duration_spin = QDoubleSpinBox()
        self.min_duration_spin.setRange(0.0, 1.0)
        self.min_duration_spin.setSingleStep(0.01)
        self.min_duration_spin.setValue(0.04)
        form.addRow("Min Note", self.min_duration_spin)

        self.quantize_combo = QComboBox()
        self.quantize_combo.addItems(["off", "1/8", "1/16", "1/4", "triplet"])
        form.addRow("Quantize", self.quantize_combo)

        self.pedal_check = QCheckBox("Include pedal")
        self.pedal_check.setChecked(True)
        form.addRow("", self.pedal_check)
        layout.addLayout(form)

        self.apply_button = QPushButton("Apply To Selected")
        self.apply_button.clicked.connect(self.apply_parameters_to_selected)
        layout.addWidget(self.apply_button)

        self.rerun_button = QPushButton("Re-run Selected")
        self.rerun_button.clicked.connect(self.rerun_selected)
        layout.addWidget(self.rerun_button)
        layout.addStretch(1)
        return panel

    def _threshold_spin(self, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 1.0)
        spin.setSingleStep(0.01)
        spin.setDecimals(2)
        spin.setValue(value)
        return spin

    def _apply_style(self) -> None:
        self.setStyleSheet(DARK_STYLESHEET)

    def import_audio(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Import Audio",
            str(Path.cwd()),
            "Audio Files (*.mp3 *.wav *.flac *.m4a *.ogg *.aac)",
        )
        self._add_audio_paths([Path(file) for file in files])

    def import_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Import Folder", str(Path.cwd()))
        if not folder:
            return
        paths = [
            path
            for path in sorted(Path(folder).iterdir())
            if path.suffix.lower() in SUPPORTED_AUDIO
        ]
        self._add_audio_paths(paths)

    def _add_audio_paths(self, paths: list[Path]) -> None:
        existing = {task.audio_path.resolve() for task in self.tasks}
        for path in paths:
            if path.suffix.lower() not in SUPPORTED_AUDIO:
                continue
            resolved = path.resolve()
            if resolved in existing:
                continue
            task = TranscriptionTask(audio_path=resolved, output_dir=self.output_dir)
            self.tasks.append(task)
            self.task_list.addItem(self._format_task_item(task))
            existing.add(resolved)
        if self.tasks and self.task_list.currentRow() < 0:
            self.task_list.setCurrentRow(0)

    def _format_task_item(self, task: TranscriptionTask) -> QListWidgetItem:
        item = QListWidgetItem()
        item.setText(self._task_label(task))
        item.setData(Qt.ItemDataRole.UserRole, task.id)
        return item

    def _task_label(self, task: TranscriptionTask) -> str:
        return f"{task.display_name}\n{task.status.value}  {task.progress}%"

    def _select_task(self, row: int) -> None:
        task = self._task_at(row)
        if not task:
            self.current_title.setText("No task selected")
            self.piano_roll.set_result(None)
            self.play_button.setEnabled(False)
            self.export_button.setEnabled(False)
            return
        self.current_title.setText(task.display_name)
        self.piano_roll.set_result(task.result)
        self.play_button.setEnabled(True)
        self.export_button.setEnabled(task.result is not None)
        self._load_parameters(task.parameters)
        self.player.setSource(QUrl.fromLocalFile(str(task.audio_path)))

    def _task_at(self, row: int) -> TranscriptionTask | None:
        if row < 0 or row >= len(self.tasks):
            return None
        return self.tasks[row]

    def _selected_task(self) -> TranscriptionTask | None:
        return self._task_at(self.task_list.currentRow())

    def _load_parameters(self, parameters: TranscriptionParameters) -> None:
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentText(parameters.preset)
        self.preset_combo.blockSignals(False)
        self.onset_spin.setValue(parameters.onset_threshold)
        self.offset_spin.setValue(parameters.offset_threshold)
        self.frame_spin.setValue(parameters.frame_threshold)
        self.min_duration_spin.setValue(parameters.minimum_note_duration)
        self.quantize_combo.setCurrentText(parameters.quantization_grid)
        self.pedal_check.setChecked(parameters.include_pedal)

    def _parameters_from_controls(self) -> TranscriptionParameters:
        return TranscriptionParameters(
            preset=self.preset_combo.currentText(),
            onset_threshold=self.onset_spin.value(),
            offset_threshold=self.offset_spin.value(),
            frame_threshold=self.frame_spin.value(),
            minimum_note_duration=self.min_duration_spin.value(),
            quantization_grid=self.quantize_combo.currentText(),
            include_pedal=self.pedal_check.isChecked(),
        )

    def _preset_changed(self, preset: str) -> None:
        self._load_parameters(TranscriptionParameters.for_preset(preset))

    def apply_parameters_to_selected(self) -> None:
        task = self._selected_task()
        if not task:
            return
        task.parameters = self._parameters_from_controls()
        if task.status is TaskStatus.COMPLETED:
            task.mark(TaskStatus.NEEDS_RERUN)
        self._refresh_task_item(task)

    def rerun_selected(self) -> None:
        task = self._selected_task()
        if not task:
            return
        self.apply_parameters_to_selected()
        task.result = None
        task.mark(TaskStatus.PENDING, progress=0)
        self._refresh_task_item(task)
        self.start_queue([task])

    def start_queue(self, tasks: list[TranscriptionTask] | None = None) -> None:
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "PianoConvert", "A transcription queue is already running.")
            return

        queue = tasks or [
            task
            for task in self.tasks
            if task.status in {TaskStatus.PENDING, TaskStatus.FAILED, TaskStatus.NEEDS_RERUN}
        ]
        if not queue:
            self.statusBar().showMessage("No pending tasks")
            return

        self.worker = TranscriptionThread(queue, self.device_combo.currentText(), self)
        self.worker.task_updated.connect(self._on_task_updated)
        self.worker.message.connect(self.statusBar().showMessage)
        self.worker.finished_all.connect(self._on_queue_finished)
        self.start_action.setEnabled(False)
        self.stop_action.setEnabled(True)
        self.worker.start()

    def cancel_queue(self) -> None:
        if self.worker:
            self.worker.cancel()
            self.statusBar().showMessage("Cancel requested")

    def _on_task_updated(self, task: TranscriptionTask) -> None:
        self._refresh_task_item(task)
        if task is self._selected_task():
            self.piano_roll.set_result(task.result)
            self.export_button.setEnabled(task.result is not None)

    def _refresh_task_item(self, task: TranscriptionTask) -> None:
        for row in range(self.task_list.count()):
            item = self.task_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == task.id:
                item.setText(self._task_label(task))
                return

    def _on_queue_finished(self) -> None:
        self.start_action.setEnabled(True)
        self.stop_action.setEnabled(False)
        self.statusBar().showMessage("Queue finished")

    def toggle_playback(self) -> None:
        task = self._selected_task()
        if not task:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.play_button.setText("Play")
            return
        self.player.setSource(QUrl.fromLocalFile(str(task.audio_path)))
        self.player.play()
        self.play_button.setText("Pause")

    def _on_player_position(self, position_ms: int) -> None:
        self.piano_roll.set_playhead_time(position_ms / 1000.0)

    def export_midi(self) -> None:
        task = self._selected_task()
        if not task or not task.result:
            return
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Export MIDI",
            str(task.result.midi_path),
            "MIDI Files (*.mid)",
        )
        if not destination:
            return
        shutil.copyfile(task.result.midi_path, destination)
        self.statusBar().showMessage(f"Exported {destination}")

    def open_output_dir(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_dir.resolve())))


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
