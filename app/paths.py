from __future__ import annotations

import os
import sys
from pathlib import Path


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(sys.executable).parent
    return Path(__file__).resolve().parents[1]


def resource_path(*parts: str) -> Path:
    return app_base_dir().joinpath(*parts).resolve()


def default_output_dir() -> Path:
    profile = os.environ.get("USERPROFILE")
    if profile:
        base = Path(profile) / "Documents"
    else:
        base = Path.home() / "Documents"
    return base / "PianoConvert" / "Output"
