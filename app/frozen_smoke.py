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
    ("transkun", "transkun"),
    ("mir_eval", "mir_eval"),
)


def _check_transkun() -> str:
    """Empty when TransKun can load its model and transcribe a second of audio, else the problem."""
    import tempfile
    from pathlib import Path

    from app import transkun_infer

    reason = transkun_infer.unavailable_reason()
    if reason:
        return reason
    import numpy as np
    import soundfile as sf

    model = transkun_infer.load_model("cpu")  # the weights are there and the model builds
    with tempfile.TemporaryDirectory() as work:
        wav, midi = Path(work) / "t.wav", Path(work) / "t.mid"
        noise = np.random.default_rng(0).standard_normal(44100) * 0.01
        sf.write(str(wav), noise.astype("float32"), 44100)
        transkun_infer.transcribe_file(model, wav, midi, "cpu")  # audio in, segments decoded, MIDI out
        if not midi.is_file():
            return "no MIDI was written"
    return ""


def check_imports() -> list[str]:
    errors: list[str] = []
    for label, modname in REQUIRED_IMPORTS:
        try:
            __import__(modname)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {exc}")
    try:
        problem = _check_transkun()
        if problem:
            errors.append(f"transkun: {problem}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"transkun: {exc}")
    try:
        from music21 import converter  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        errors.append(f"music21.converter: {exc}")
    try:
        from basic_pitch.inference import predict  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        errors.append(f"basic_pitch.inference: {exc}")
    try:
        import cv2  # noqa: F401
        import pypdfium2  # noqa: F401
        from rapidocr import RapidOCR  # noqa: F401

        from app import jianpu, pdf_omr, pdf_vector  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        errors.append(f"score recognition: {exc}")
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
        from app.paths import app_icon_path, asset_path
        from PySide6.QtGui import QPixmap

        if not app_icon_path().is_file() or win.windowIcon().isNull():
            errors.append("assets: missing or invalid app icon")
        if QPixmap(str(asset_path("venti-background.png"))).isNull():
            errors.append("assets: missing or invalid Venti background")
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
