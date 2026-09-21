from __future__ import annotations

from pathlib import Path
from threading import Event

from app.engines.base import CancelledError, ProgressCb


def predict(audio_path: str, model_or_model_path=None):
    from basic_pitch.inference import predict as bp_predict
    from basic_pitch import ICASSP_2022_MODEL_PATH

    return bp_predict(audio_path, model_or_model_path or ICASSP_2022_MODEL_PATH)


class BasicPitchEngine:
    def transcribe(self, audio_path: Path, dest_midi: Path, cancel: Event, on_progress: ProgressCb) -> None:
        if cancel.is_set():
            raise CancelledError()
        on_progress("转录", 0.4)
        _model_out, midi_data, _notes = predict(str(audio_path))
        if cancel.is_set():
            raise CancelledError()
        dest_midi.parent.mkdir(parents=True, exist_ok=True)
        midi_data.write(str(dest_midi))
