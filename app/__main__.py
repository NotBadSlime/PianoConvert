from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow
from app.ui.styles import APP_QSS


def main() -> None:
    import sys

    if "--smoke" in sys.argv:
        from app.frozen_smoke import run_smoke

        raise SystemExit(run_smoke())
    if "--homr" in sys.argv:
        from app.pdf_omr import homr_entry

        homr_entry(sys.argv[sys.argv.index("--homr") + 1])
        return

    app = QApplication([])
    app.setStyleSheet(APP_QSS)
    win = MainWindow()
    win.resize(860, 760)
    win.show()
    app.exec()


if __name__ == "__main__":
    main()
