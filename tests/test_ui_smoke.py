import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow


def test_main_window_can_be_created():
    app = QApplication.instance() or QApplication(sys.argv)

    window = MainWindow()

    assert window.windowTitle() == "PianoConvert"
    assert window.task_list.count() == 0
