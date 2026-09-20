from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Callable, Protocol

ProgressCb = Callable[[str, float], None]


class CancelledError(Exception):
    pass


class TranscriptionEngine(Protocol):
    def transcribe(self, audio_path: Path, dest_midi: Path, cancel: Event, on_progress: ProgressCb) -> None:
        ...
