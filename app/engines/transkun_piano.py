from __future__ import annotations

from pathlib import Path
from threading import Event

from app.device import resolve_device
from app.engines.base import CancelledError, ProgressCb


class TranskunPianoEngine:
    """Piano recordings with TransKun (see app/transkun_infer.py): higher note accuracy than the
    ByteDance model on MAESTRO, and faster on a CPU."""

    def __init__(self, device: str = "auto") -> None:
        self.device = device

    def transcribe(self, audio_path: Path, dest_midi: Path, cancel: Event, on_progress: ProgressCb) -> None:
        if cancel.is_set():
            raise CancelledError()
        on_progress("转录", 0.3)
        if self.device == "cuda":
            from app.gpu_setup import transcribe_with_gpu

            transcribe_with_gpu("transkun", audio_path, dest_midi, None, cancel, on_progress)
            return
        from app import transkun_infer

        device = resolve_device(self.device)
        model = transkun_infer.load_model(device)
        if cancel.is_set():
            raise CancelledError()
        try:
            transkun_infer.transcribe_file(
                model,
                audio_path,
                dest_midi,
                device,
                should_stop=cancel.is_set,
                on_progress=lambda ratio: on_progress("转录", 0.3 + 0.4 * ratio),
            )
        except transkun_infer.Cancelled:
            raise CancelledError() from None
