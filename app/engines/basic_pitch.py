from __future__ import annotations

from pathlib import Path
from threading import Event

from app.device import resolve_device
from app.engines.base import CancelledError, ProgressCb


def onnx_providers(device: str) -> list[str]:
    if device != "cuda":
        return ["CPUExecutionProvider"]
    import onnxruntime as ort

    if "CUDAExecutionProvider" in ort.get_available_providers():
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def predict(audio_path: str, model_or_model_path=None, device: str = "cpu"):
    import basic_pitch.inference as bp_inf
    from basic_pitch import ICASSP_2022_MODEL_PATH
    from basic_pitch.inference import predict as bp_predict

    chosen = onnx_providers(device)
    real = bp_inf.ort.InferenceSession

    def _session(path, providers=None, **kwargs):
        return real(path, providers=chosen, **kwargs)

    bp_inf.ort.InferenceSession = _session
    try:
        return bp_predict(audio_path, model_or_model_path or ICASSP_2022_MODEL_PATH)
    finally:
        bp_inf.ort.InferenceSession = real


class BasicPitchEngine:
    def __init__(self, device: str = "auto") -> None:
        self.device = device

    def transcribe(self, audio_path: Path, dest_midi: Path, cancel: Event, on_progress: ProgressCb) -> None:
        if cancel.is_set():
            raise CancelledError()
        on_progress("转录", 0.4)
        if self.device == "cuda":
            from app.gpu_setup import transcribe_with_gpu

            transcribe_with_gpu("other", audio_path, dest_midi, None, cancel, on_progress)
            return
        _model_out, midi_data, _notes = predict(str(audio_path), device=resolve_device(self.device))
        if cancel.is_set():
            raise CancelledError()
        dest_midi.parent.mkdir(parents=True, exist_ok=True)
        midi_data.write(str(dest_midi))
