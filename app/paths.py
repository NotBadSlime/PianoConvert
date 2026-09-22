from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

CHECKPOINT_NAME = "note_F1=0.9677_pedal_F1=0.9186.pth"
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def safe_stem(name: str, max_len: int = 80) -> str:
    text = Path(name).stem
    text = _UNSAFE.sub("_", text)
    text = re.sub(r"_+", "_", text).strip(" ._")
    return (text or "audio")[:max_len]


def output_root() -> Path:
    return Path.home() / "Documents" / "PianoConvert" / "Output"


def history_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    return base / "PianoConvert" / "history.json"


def make_output_dir(source: Path) -> Path:
    folder = output_root() / f"{safe_stem(source.name)}_{_now_stamp()}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def app_icon_path() -> Path:
    import sys

    name = Path("assets") / "PianoConvert.ico"
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        frozen = Path(meipass) / name
        if frozen.exists():
            return frozen
    return repo_root() / name


def model_checkpoint() -> Path:
    bundled = repo_root() / "models" / CHECKPOINT_NAME
    if bundled.exists():
        return bundled
    meipass = getattr(__import__("sys"), "_MEIPASS", None)
    if meipass:
        frozen = Path(meipass) / "piano_transcription_inference_data" / CHECKPOINT_NAME
        if frozen.exists():
            return frozen
    raise FileNotFoundError(f"找不到钢琴模型: {CHECKPOINT_NAME}")
