from __future__ import annotations

from pathlib import Path
from threading import Event

from app import transkun_infer
from app.engines.base import ProgressCb
from app.engines.kong_piano import KongPianoEngine
from app.engines.transkun_piano import TranskunPianoEngine

MODEL_TRANSKUN = "transkun"
MODEL_KONG = "kong"
PIANO_MODEL_LABELS = {MODEL_TRANSKUN: "TransKun", MODEL_KONG: "ByteDance"}
DEFAULT_PIANO_MODEL = MODEL_TRANSKUN


class PianoEngine:
    """The 钢琴 engine. ``model`` picks TransKun (the default) or the older ByteDance model; if TransKun
    is asked for but cannot run (its component is missing from a broken install), the ByteDance model
    does the job instead, so a piano recording is never left without a result."""

    def __init__(self, device: str = "auto", model: str = DEFAULT_PIANO_MODEL) -> None:
        self.device = device
        self.model = model

    def _engine(self, on_progress: ProgressCb):
        if self.model == MODEL_TRANSKUN:
            if not transkun_infer.unavailable_reason():
                engine = TranskunPianoEngine()
                engine.device = self.device
                return engine
            on_progress("TransKun 不可用，改用 ByteDance 模型", 0.3)
        engine = KongPianoEngine()
        engine.device = self.device
        return engine

    def transcribe(self, audio_path: Path, dest_midi: Path, cancel: Event, on_progress: ProgressCb) -> None:
        self._engine(on_progress).transcribe(audio_path, dest_midi, cancel, on_progress)
