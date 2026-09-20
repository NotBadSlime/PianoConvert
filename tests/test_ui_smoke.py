import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow


def test_start_disabled_without_file():
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    assert win.start_button.isEnabled() is False
    assert win.windowTitle() == "PianoConvert"
    assert win.piano_radio.isChecked() is True
