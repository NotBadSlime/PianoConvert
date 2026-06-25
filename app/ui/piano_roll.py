from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from app.models import TranscriptionResult


class PianoRollWidget(QWidget):
    low_note = 21
    high_note = 108
    keyboard_width = 72
    row_height = 12

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._result: TranscriptionResult | None = None
        self._pixels_per_second = 80
        self._playhead_time = 0.0
        self.setMinimumSize(760, self.row_count * self.row_height + 24)

    @property
    def row_count(self) -> int:
        return self.high_note - self.low_note + 1

    def set_result(self, result: TranscriptionResult | None) -> None:
        self._result = result
        self._playhead_time = 0.0
        self.updateGeometry()
        self.update()

    def set_playhead_time(self, seconds: float) -> None:
        self._playhead_time = max(0.0, seconds)
        self.update()

    def zoom_in(self) -> None:
        self._pixels_per_second = min(220, int(self._pixels_per_second * 1.25))
        self.updateGeometry()
        self.update()

    def zoom_out(self) -> None:
        self._pixels_per_second = max(30, int(self._pixels_per_second / 1.25))
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        duration = self._result.effective_duration if self._result else 8.0
        width = self.keyboard_width + int(max(8.0, duration) * self._pixels_per_second) + 80
        height = self.row_count * self.row_height + 24
        return QSize(width, height)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.fillRect(self.rect(), QColor("#111827"))
        self._draw_grid(painter)
        self._draw_notes(painter)
        self._draw_playhead(painter)

    def _draw_grid(self, painter: QPainter) -> None:
        total_width = max(self.width(), self.sizeHint().width())
        painter.fillRect(0, 0, self.keyboard_width, self.height(), QColor("#f8fafc"))

        for index, midi_note in enumerate(range(self.high_note, self.low_note - 1, -1)):
            y = index * self.row_height
            is_black = midi_note % 12 in {1, 3, 6, 8, 10}
            key_color = QColor("#1f2937") if is_black else QColor("#ffffff")
            grid_color = QColor("#1f2937") if is_black else QColor("#243044")
            painter.fillRect(0, y, self.keyboard_width, self.row_height, key_color)
            painter.fillRect(
                self.keyboard_width,
                y,
                total_width - self.keyboard_width,
                self.row_height,
                grid_color,
            )
            painter.setPen(QPen(QColor("#334155"), 1))
            painter.drawLine(0, y, total_width, y)
            if midi_note % 12 == 0:
                painter.setPen(QColor("#475569") if is_black else QColor("#64748b"))
                painter.drawText(6, y + self.row_height - 2, f"C{(midi_note // 12) - 1}")

        painter.setPen(QPen(QColor("#344256"), 1))
        for second in range(0, int(total_width / self._pixels_per_second) + 1):
            x = self.keyboard_width + second * self._pixels_per_second
            painter.drawLine(x, 0, x, self.height())

    def _draw_notes(self, painter: QPainter) -> None:
        if not self._result:
            painter.setPen(QColor("#94a3b8"))
            painter.drawText(
                QRectF(self.keyboard_width, 0, self.width() - self.keyboard_width, self.height()),
                Qt.AlignmentFlag.AlignCenter,
                "Import audio and run transcription to preview notes",
            )
            return

        for note in self._result.note_events:
            if note.midi_note < self.low_note or note.midi_note > self.high_note:
                continue
            x = self.keyboard_width + note.onset_time * self._pixels_per_second
            y = (self.high_note - note.midi_note) * self.row_height + 1
            width = max(3.0, note.duration * self._pixels_per_second)
            height = max(4.0, self.row_height - 2)
            hue = int(150 + (note.velocity / 127) * 55)
            color = QColor.fromHsv(hue, 170, 230)
            painter.fillRect(QRectF(x, y, width, height), color)
            painter.setPen(QPen(QColor("#dbeafe"), 1))
            painter.drawRect(QRectF(x, y, width, height))

    def _draw_playhead(self, painter: QPainter) -> None:
        x = self.keyboard_width + self._playhead_time * self._pixels_per_second
        painter.setPen(QPen(QColor("#f97316"), 2))
        painter.drawLine(int(x), 0, int(x), self.height())
