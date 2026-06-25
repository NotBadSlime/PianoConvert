from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from app.models import TranscriptionParameters, TranscriptionResult

ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]


class EngineCancelled(Exception):
    """Raised when a transcription task is cancelled."""


class TranscriptionEngine(Protocol):
    name: str
    version: str

    def transcribe(
        self,
        audio_path: Path,
        output_dir: Path,
        device: str,
        parameters: TranscriptionParameters,
        progress_callback: ProgressCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> TranscriptionResult:
        ...
