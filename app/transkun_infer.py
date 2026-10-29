"""TransKun piano transcription (Yan & Duan, ISMIR 2024), shared by the in-app engine and the GPU worker.

Kept self-contained on purpose: this file only imports third-party packages, so the GPU runtime's own
Python can load it straight from the app folder (see ``gpu_worker.py``) without importing ``app``.

The model comes from the ``transkun`` package (MIT, https://github.com/Yujia-Yan/Transkun), which ships
its weights (``pretrained/2.0.pt``, trained without extending notes by the sustain pedal). Notes are
extended by the pedal after transcription, so durations follow the same convention as the ByteDance model
and the keyboard score keeps its look (``-`` for a note that still rings, ``0`` only for real silence).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

CONF_NAME = "2.0.conf"
WEIGHTS_NAME = "2.0.pt"
SUSTAIN_PEDAL = -64  # TransKun writes control changes as negative "pitches"


class Cancelled(Exception):
    """Raised between two segments when the caller asked to stop."""


def package_dir() -> Path:
    """The folder with the transkun sources and its ``pretrained/`` weights. In the installed app that
    is a plain folder beside the exe (TorchScript needs the .py files); from source, the installed package."""
    import transkun

    return Path(transkun.__file__).resolve().parent


def unavailable_reason() -> str:
    """Why TransKun cannot run here, or an empty string when it can."""
    import importlib.util

    try:
        if importlib.util.find_spec("transkun") is None:
            return "没有找到 TransKun 组件"
        root = package_dir() / "pretrained"
    except Exception as exc:  # noqa: BLE001 - a broken install
        return f"TransKun 组件有问题：{exc}"
    for name in (CONF_NAME, WEIGHTS_NAME):
        if not (root / name).is_file():
            return f"TransKun 缺少模型文件 {name}"
    return ""


def is_available() -> bool:
    return not unavailable_reason()


def load_model(device: str = "cpu"):
    """The pretrained model, in eval mode on ``device``."""
    import torch
    from transkun.ModelTransformer import ModelConfig, TransKun

    root = package_dir() / "pretrained"
    settings = json.loads((root / CONF_NAME).read_text(encoding="utf-8"))["Model"]["config"]
    conf = ModelConfig()
    conf.__dict__.update(settings)
    checkpoint = torch.load(root / WEIGHTS_NAME, map_location="cpu", weights_only=True)
    model = TransKun(conf=conf)
    model.load_state_dict(checkpoint.get("best_state_dict") or checkpoint["state_dict"], strict=False)
    model.to(device)
    model.eval()
    return model


def read_audio(path, sample_rate: int):
    """(frames, channels) float32 at the model's sample rate; any channel count is fine, the model
    averages the channels' power spectra."""
    import librosa
    import numpy as np

    samples, rate = librosa.load(str(path), sr=None, mono=False)
    if samples.ndim == 1:
        samples = samples[None, :]
    audio = np.ascontiguousarray(samples.T, dtype=np.float32)
    if rate != sample_rate:
        import soxr

        audio = np.ascontiguousarray(soxr.resample(audio, rate, sample_rate), dtype=np.float32)
    return audio


def _segment_count(model, frames: int) -> int:
    """How many segments ``model.transcribe`` will run (same arithmetic as the model, for progress)."""
    step, size = model.segmentHopSizeInSecond, model.segmentSizeInSecond
    padded = frames + 2 * math.ceil((size - step) * model.fs)
    stride = math.ceil(step * model.fs / model.hopSize) * model.hopSize
    return max(1, len(range(0, padded, stride)))


def extend_by_pedal(notes: list) -> list:
    """Hold each note until the sustain pedal comes up, like the ByteDance model's notes.

    ``notes`` are TransKun events (note pitches > 0, pedal events < 0). Falls back to the notes as they
    are when the pedal data cannot be applied."""
    from transkun.Data import extendPedal

    keys = [n for n in notes if n.pitch > 0]
    pedals = [n for n in notes if n.pitch < 0]
    sustain = [n for n in pedals if n.pitch == SUSTAIN_PEDAL]
    if not keys or not sustain:
        return keys + pedals
    try:
        return extendPedal(keys, sustain) + pedals
    except AssertionError:
        return [n for n in notes if n.pitch > 0] + pedals


def transcribe_file(model, audio_path, midi_path, device: str = "cpu", should_stop=None, on_progress=None) -> None:
    """Transcribe ``audio_path`` into ``midi_path``.

    should_stop: callable returning True to stop (checked before every segment of about 8 s);
    on_progress: callable taking a ratio between 0 and 1."""
    import torch
    from transkun.Data import writeMidi

    audio = read_audio(audio_path, model.fs)
    total = _segment_count(model, audio.shape[0])
    done = 0
    original = model.transcribeFrames

    def hooked(*args, **kwargs):
        nonlocal done
        if should_stop is not None and should_stop():
            raise Cancelled()
        result = original(*args, **kwargs)
        done += 1
        if on_progress is not None:
            on_progress(min(1.0, done / total))
        return result

    model.transcribeFrames = hooked
    try:
        with torch.no_grad():
            x = torch.from_numpy(audio).to(device)
            notes = model.transcribe(x, stepInSecond=None, segmentSizeInSecond=None, discardSecondHalf=False)
    finally:
        del model.transcribeFrames
    midi = writeMidi(extend_by_pedal(notes))
    Path(midi_path).parent.mkdir(parents=True, exist_ok=True)
    midi.write(str(midi_path))
