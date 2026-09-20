# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH)
datas = [
    (
        str(root / "models" / "note_F1=0.9677_pedal_F1=0.9186.pth"),
        "piano_transcription_inference_data",
    ),
]
datas += collect_data_files("basic_pitch")
datas += collect_data_files("music21")
hidden = (
    collect_submodules("music21")
    + collect_submodules("basic_pitch")
    + collect_submodules("piano_transcription_inference")
)

a = Analysis(
    ["app/__main__.py"],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden
    + [
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
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PianoConvert",
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PianoConvert")
