from __future__ import annotations

REQUIRED_IMPORTS = (
    ("webcolors", "webcolors"),
    ("pkg_resources", "pkg_resources"),
    ("jsonpickle", "jsonpickle"),
    ("more_itertools", "more_itertools"),
    ("chardet", "chardet"),
    ("resampy", "resampy"),
    ("librosa", "librosa"),
    ("soundfile", "soundfile"),
    ("audioread", "audioread"),
    ("pretty_midi", "pretty_midi"),
    ("music21", "music21"),
    ("basic_pitch", "basic_pitch"),
    ("onnxruntime", "onnxruntime"),
    ("torch", "torch"),
    ("homr", "homr"),
    ("musicxml", "musicxml"),
)


def check_imports() -> list[str]:
    errors: list[str] = []
    for label, modname in REQUIRED_IMPORTS:
        try:
            __import__(modname)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {exc}")
    try:
        from music21 import converter  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        errors.append(f"music21.converter: {exc}")
    try:
        from basic_pitch.inference import predict  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        errors.append(f"basic_pitch.inference: {exc}")
    try:
        import pkg_resources
        pkg_resources.resource_filename("resampy.filters", "data/kaiser_fast.npz")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"resampy.filter_data: {exc}")
    return errors


def run_smoke() -> int:
    import os
    import sys
    from pathlib import Path

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    errors = check_imports()
    try:
        from PySide6.QtWidgets import QApplication

        from app.ui.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        win = MainWindow(engines={})
        win.close()
        app.processEvents()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"ui: {exc}")

    log = Path(os.environ.get("TEMP", ".")) / "PianoConvert-smoke.log"
    if errors:
        log.write_text("\n".join(errors), encoding="utf-8")
        print("SMOKE FAIL")
        print("\n".join(errors))
        return 1
    log.write_text("ok\n", encoding="utf-8")
    print("SMOKE OK")
    return 0
