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


def test_main_window_uses_readable_dark_theme():
    app = QApplication.instance() or QApplication(sys.argv)

    window = MainWindow()
    stylesheet = window.styleSheet()

    assert "#0b1020" in stylesheet
    assert "#e5e7eb" in stylesheet
    assert "QPushButton:disabled" in stylesheet
    assert "QComboBox" in stylesheet
    assert "QDoubleSpinBox" in stylesheet
    assert "QSplitter::handle" in stylesheet
