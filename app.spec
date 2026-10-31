# -*- mode: python ; coding: utf-8 -*-
import importlib
import sys
from pathlib import Path

import music21
from PyInstaller.utils.hooks import collect_all, collect_data_files

root = Path(SPECPATH)
music21_dir = Path(music21.__file__).resolve().parent
sys.path.insert(0, str(root))

# homr's models (CPU set, ~150 MB) ship beside its modules, where homr looks first, so reading
# scans and screenshots needs no download. `python -m app.homr_models` puts them there.
from app.homr_models import missing_shipped, required_names  # noqa: E402
from app import copista_models  # noqa: E402

if copista_models.missing_shipped():
    raise SystemExit("Copista weights missing; run `python -m app.copista_models --package` first")

HOMR_MODELS = set(required_names())
_missing_homr_models = missing_shipped()
if _missing_homr_models:
    raise SystemExit(
        "homr models are not beside the homr package; run `python -m app.homr_models` first. Missing: "
        + ", ".join(_missing_homr_models)
    )


def _keep_homr_data(src: str) -> bool:
    """Only the models the app loads: no fp16 copies, no half-finished downloads."""
    name = Path(src).name
    if name.endswith((".zip", ".part", ".tmp")):
        return False
    return not name.endswith(".onnx") or name in HOMR_MODELS

# music21 is shipped as source (datas) and excluded from Analysis, so PyInstaller
# never sees its imports. Bundle declared runtime deps explicitly.
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
# setuptools 84+ dropped pkg_resources; resampy still needs it. Collect both.
COLLECT_ALL_PACKAGES = [
    "setuptools",
    "pkg_resources",
    "resampy",
    "librosa",
    "pretty_midi",
    "audioread",
    "soundfile",
    "pooch",
    "decorator",
    "soxr",
    "lazy_loader",
]


def _package_dir(modname: str) -> Path:
    mod = importlib.import_module(modname)
    path = Path(mod.__file__).resolve()
    return path.parent if path.name == "__init__.py" else path


hiddenimports = [
    "audioread.ffdec",
    "pkg_resources",
    "setuptools",
    "resampy",
    "resampy.filters",
    "app",
    "app.convert",
    "app.device",
    "app.frozen_smoke",
    "app.history",
    "app.homr_models",
    "app.copista_models",
    "app.copista_runtime",
    "app.jianpu",
    "app.keyboard_score",
    "app.paths",
    "app.pdf_omr",
    "app.pdf_vector",
    "app.score_io",
    "app.settings",
    "app.ui",
    "app.ui.icons",
    "app.ui.main_window",
    "app.ui.preview",
    "app.ui.styles",
    "app.ui.theme",
    "app.ui.widgets",
    "app.engines",
    "app.engines.base",
    "app.engines.kong_piano",
    "app.engines.basic_pitch",
    "app.engines.piano",
    "app.engines.transkun_piano",
    "app.transkun_infer",
    # transkun itself is shipped as source (see datas), so its own imports are listed here
    "torchaudio",
    "torch.distributed",
    "mir_eval",
    "basic_pitch",
    "basic_pitch.inference",
    "onnxruntime",
    "pretty_midi",
    "piano_transcription_inference",
    "torchlibrosa",
    "librosa",
    "soundfile",
    "homr",
    "homr.main",
    "musicxml",
    "cv2",
    "pypdfium2",
    "rapidocr",
    "torchvision",
    "torchvision.ops",
    "scipy.ndimage",
    "scipy.optimize",
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
datas.append((str(root / "assets" / "PianoConvert.ico"), "assets"))
datas.append((str(root / "assets" / "venti-background.png"), "assets"))
datas.append((str(root / "scripts" / "install_gpu.ps1"), "scripts"))
datas.append((str(root / "app" / "gpu_worker.py"), "app"))
# the GPU worker imports this file from beside itself
datas.append((str(root / "app" / "transkun_infer.py"), "app"))

# Copisteria is a pinned, unmodified source snapshot, including its taxonomy,
# runtime JSON assets, license and NOTICE. No verify/ folder is needed at runtime.
_vendor = root / "third_party" / "copisteria"
for _file in _vendor.rglob("*"):
    if _file.is_file() and "__pycache__" not in _file.parts and _file.suffix != ".pyc":
        datas.append((str(_file), str(_file.parent.relative_to(root))))
for _name in copista_models.FILES:
    _file = copista_models.shipped_dir() / _name
    datas.append((str(_file), str(Path("copista-models") / Path(_name).parent)))
datas.append((str(root / "piano_transcription_inference"), "piano_transcription_inference_src"))
# TransKun is shipped as plain source, not compiled into the exe (it is excluded from Analysis below):
# its CRF uses torch.jit.script, which reads the .py files at run time. The same folder holds the weights
# the app reads and the copy the GPU runtime gets.
datas.append((str(_package_dir("transkun")), "transkun"))
datas += collect_data_files("torchlibrosa")
datas += collect_data_files("basic_pitch")
datas += collect_data_files("resampy")

binaries = []
for _pkg in ("homr", "musicxml", "rapidocr", "pypdfium2"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(_pkg)
    except Exception:
        continue
    if _pkg == "homr":
        pkg_datas = [item for item in pkg_datas if _keep_homr_data(item[0])]
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

for _pkg in COLLECT_ALL_PACKAGES:
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(_pkg)
    except Exception:
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    ["app/__main__.py"],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(root / "packaging" / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
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
        "transkun",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [("X utf8", None, "OPTION")],
    exclude_binaries=True,
    name="PianoConvert",
    icon=str(root / "assets" / "PianoConvert.ico"),
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
