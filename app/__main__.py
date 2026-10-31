def main() -> None:
    import sys

    if "--smoke" in sys.argv:
        from app.frozen_smoke import run_smoke

        raise SystemExit(run_smoke())
    if "--homr" in sys.argv:
        from app.pdf_omr import homr_entry

        homr_entry(sys.argv[sys.argv.index("--homr") + 1])
        return
    if "--copista" in sys.argv:
        from app.copista_runtime import entry

        entry(sys.argv[sys.argv.index("--copista") + 1])
        return
    if "--convert-score" in sys.argv:
        # Headless check of the PDF / image route, used to verify a packaged build:
        #   PianoConvert.exe --convert-score <file> <output folder>
        from pathlib import Path
        from threading import Event

        from app.convert import run_pdf

        index = sys.argv.index("--convert-score")
        result = run_pdf(Path(sys.argv[index + 1]), Event(), output_root=Path(sys.argv[index + 2]))
        print(result.status, result.error, result.keyboard_path)
        raise SystemExit(0 if result.status == "success" else 1)
    if "--install-gpu" in sys.argv:
        from app.gpu_setup import main as install_gpu_main

        install_gpu_main()
        return

    from PySide6.QtCore import QLibraryInfo, QTranslator
    from PySide6.QtWidgets import QApplication

    from app.ui.main_window import MainWindow

    app = QApplication([])
    # Fusion draws every control from our palette, so the light and dark themes look the same everywhere.
    app.setStyle("Fusion")
    translator = QTranslator(app)
    if translator.load("qtbase_zh_CN", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)  # 是 / 否, 复制 / 粘贴 in Qt's own dialogs and menus
    win = MainWindow()  # applies the remembered theme (跟随系统 / 浅色 / 深色) to the whole app
    screen = app.primaryScreen()
    if screen is not None:
        free = screen.availableGeometry()
        win.resize(min(1100, free.width() - 40), min(760, free.height() - 60))
    win.show()
    app.exec()


if __name__ == "__main__":
    main()
