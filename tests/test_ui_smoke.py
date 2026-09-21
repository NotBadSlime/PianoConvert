import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from app.convert import ConvertResult
from app.history import HistoryItem, append_item
from app.ui.main_window import MainWindow


def test_start_disabled_without_file():
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    assert win.start_button.isEnabled() is False
    assert win.windowTitle() == "PianoConvert"
    assert win.piano_radio.isChecked() is True


def test_choosing_file_enables_start(tmp_path):
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    win.set_source_file(src)
    assert win.start_button.isEnabled() is True
    assert "a.mp3" in win.file_label.text()


def test_partial_row_disables_musicxml(tmp_path, monkeypatch):
    hist = tmp_path / "h.json"
    monkeypatch.setattr("app.history.history_path", lambda: hist)
    monkeypatch.setattr("app.ui.main_window.history_path", lambda: hist)
    midi = tmp_path / "t.mid"
    midi.write_bytes(b"m")
    append_item(
        HistoryItem(
            id="1",
            title="t",
            source_path="x",
            kind="piano",
            created_at="2026-01-01T00:00:00",
            status="partial",
            midi_path=str(midi),
            musicxml_path=str(tmp_path / "missing.musicxml"),
            folder=str(tmp_path),
            error="谱面导出失败",
        )
    )
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.reload_history()
    row = win.history_list.itemWidget(win.history_list.item(0))
    assert row is not None
    assert row.xml_button.isEnabled() is False
    assert row.midi_button.isEnabled() is True


def test_finished_resets_ui_if_history_save_fails(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("app.ui.main_window.append_item", boom)
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    win.set_source_file(src)
    win._busy = True
    win.start_button.setText("取消")
    win.pick_button.setEnabled(False)
    result = ConvertResult(
        status="success",
        folder=tmp_path,
        midi_path=tmp_path / "t.mid",
        musicxml_path=tmp_path / "t.musicxml",
        error="",
        title="t",
        kind="piano",
        source_path=src,
    )
    with pytest.raises(OSError, match="disk full"):
        win._on_finished(result)
    assert win._busy is False
    assert win.start_button.text() == "开始转换"
    assert win.start_button.isEnabled() is True
    assert win.pick_button.isEnabled() is True
