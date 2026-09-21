# -*- mode: python ; coding: utf-8 -*-
import importlib
from pathlib import Path

import music21
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH)
music21_dir = Path(music21.__file__).resolve().parent

# music21 is shipped as source (datas) and excluded from Analysis, so PyInstaller
# never sees its imports. Bundle its declared runtime deps explicitly.
MUSIC21_RUNTIME_PACKAGES = [
    "webcolors",
    "jsonpickle",
    "more_itertools",
    "chardet",
    "joblib",
    "requests",
]
MUSIC21_DATAS_PACKAGES = [
    "webcolors",
    "jsonpickle",
    "more_itertools",
    "chardet",
]


def _package_dir(modname: str) -> Path:
    mod = importlib.import_module(modname)
    path = Path(mod.__file__).resolve()
    return path.parent if path.name == "__init__.py" else path


hiddenimports = [
    "audioread.ffdec",
    "app",
    "app.convert",
    "app.device",
    "app.history",
    "app.keyboard_score",
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
hiddenimports += MUSIC21_RUNTIME_PACKAGES

datas = [
    (
        str(root / "models" / "note_F1=0.9677_pedal_F1=0.9186.pth"),
        "piano_transcription_inference_data",
    ),
    (str(root / "README.md"), "."),
    (str(music21_dir), "music21"),
]
for _pkg in MUSIC21_DATAS_PACKAGES:
    datas.append((str(_package_dir(_pkg)), _pkg))
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
