# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

import music21
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH)
music21_dir = Path(music21.__file__).resolve().parent

hiddenimports = [
    "audioread.ffdec",
    "app",
    "app.convert",
    "app.device",
    "app.history",
    "app.paths",
    "app.score_io",
    "app.ui",
    "app.ui.main_window",
    "app.ui.styles",
    "app.engines",
    "app.engines.base",
    "app.engines.kong_piano",
    "app.engines.basic_pitch",
    "basic_pitch",
    "basic_pitch.inference",
    "onnxruntime",
    "pretty_midi",
    "piano_transcription_inference",
    "torchlibrosa",
    "librosa",
    "soundfile",
]

datas = [
    (
        str(root / "models" / "note_F1=0.9677_pedal_F1=0.9186.pth"),
        "piano_transcription_inference_data",
    ),
    (str(root / "README.md"), "."),
    (str(music21_dir), "music21"),
]
datas += collect_data_files("torchlibrosa")
datas += collect_data_files("basic_pitch")

a = Analysis(
    ["app/__main__.py"],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(root / "packaging" / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tests",
        "pytest",
        "pygments",
        "IPython",
        "notebook",
        "matplotlib.tests",
        "numpy.tests",
        "tensorflow",
        "tensorboard",
        "tkinter",
        "music21",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PianoConvert",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="PianoConvert",
)
