from __future__ import annotations

from pathlib import Path
from threading import Event

from app.device import resolve_device
from app.engines.base import CancelledError, ProgressCb
from app.paths import model_checkpoint


def _make_transcriber(device: str, ckpt: Path):
    from piano_transcription_inference import PianoTranscription

    return PianoTranscription(device=device, checkpoint_path=str(ckpt))


class KongPianoEngine:
    def __init__(self, device: str = "auto") -> None:
        self.device = device

    def transcribe(self, audio_path: Path, dest_midi: Path, cancel: Event, on_progress: ProgressCb) -> None:
        if cancel.is_set():
            raise CancelledError()
        on_progress("转录", 0.4)
        if self.device == "cuda":
            from app.gpu_setup import transcribe_with_gpu

            transcribe_with_gpu("piano", audio_path, dest_midi, model_checkpoint(), cancel, on_progress)
            return
        transcriber = _make_transcriber(resolve_device(self.device), model_checkpoint())
        if cancel.is_set():
            raise CancelledError()
        transcriber.transcribe(str(audio_path), str(dest_midi))
