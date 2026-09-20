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
    def transcribe(self, audio_path: Path, dest_midi: Path, cancel: Event, on_progress: ProgressCb) -> None:
        if cancel.is_set():
            raise CancelledError()
        on_progress("转录", 0.4)
        transcriber = _make_transcriber(resolve_device(), model_checkpoint())
        if cancel.is_set():
            raise CancelledError()
        transcriber.transcribe(str(audio_path), str(dest_midi))
