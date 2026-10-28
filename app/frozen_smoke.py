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
    ("verovio", "verovio"),
    ("PySide6.QtSvg", "PySide6.QtSvg"),
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


_SMOKE_MUSICXML = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1"><part-list><score-part id="P1"><part-name>P</part-name></score-part></part-list>
<part id="P1"><measure number="1"><attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time>
<clef><sign>G</sign><line>2</line></clef></attributes>
<note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration><type>whole</type></note></measure></part></score-partwise>
"""


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
    try:
        # the staff-notation PDF: Verovio's data and Qt's SVG renderer must be packaged
        import tempfile

        from app.score_io import musicxml_to_pdf

        work = Path(tempfile.mkdtemp())
        xml = work / "smoke.musicxml"
        xml.write_text(_SMOKE_MUSICXML, encoding="utf-8")
        musicxml_to_pdf(xml, work / "smoke.pdf")
        if (work / "smoke.pdf").stat().st_size < 1000:
            errors.append("staff-notation PDF: empty")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"staff-notation PDF: {exc}")

    log = Path(os.environ.get("TEMP", ".")) / "PianoConvert-smoke.log"
    if errors:
        log.write_text("\n".join(errors), encoding="utf-8")
        print("SMOKE FAIL")
        print("\n".join(errors))
        return 1
    log.write_text("ok\n", encoding="utf-8")
    print("SMOKE OK")
    return 0
