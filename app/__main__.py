from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow
from app.ui.styles import APP_QSS


def main() -> None:
    app = QApplication([])
    app.setStyleSheet(APP_QSS)
    win = MainWindow()
    win.resize(720, 760)
    win.show()
    app.exec()


if __name__ == "__main__":
    main()
