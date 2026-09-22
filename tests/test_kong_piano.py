from pathlib import Path
from threading import Event

import pytest

from app.engines.base import CancelledError
from app.engines.kong_piano import KongPianoEngine


class FakeTranscriber:
    def transcribe(self, audio_path, midi_path):
        import pretty_midi

        pm = pretty_midi.PrettyMIDI()
        inst = pretty_midi.Instrument(program=0)
        inst.notes.append(pretty_midi.Note(velocity=70, pitch=67, start=0.0, end=0.3))
        pm.instruments.append(inst)
        Path(midi_path).parent.mkdir(parents=True, exist_ok=True)
        pm.write(str(midi_path))


def test_kong_writes_midi(tmp_path, monkeypatch):
    monkeypatch.setattr("app.engines.kong_piano._make_transcriber", lambda device, ckpt: FakeTranscriber())
    monkeypatch.setattr("app.engines.kong_piano.model_checkpoint", lambda: tmp_path / "fake.pth")
    src = tmp_path / "a.wav"
    src.write_bytes(b"xx")
    dest = tmp_path / "a.mid"
    KongPianoEngine().transcribe(src, dest, Event(), lambda *_: None)
    assert dest.exists()
    assert dest.stat().st_size > 0


def test_kong_cuda_uses_downloaded_runtime(tmp_path, monkeypatch):
    seen = {}

    def fake(kind, audio, midi, checkpoint, cancel, on_progress):
        seen["kind"] = kind
        seen["checkpoint"] = checkpoint
        Path(midi).write_bytes(b"MThd")

    monkeypatch.setattr("app.gpu_setup.transcribe_with_gpu", fake)
    monkeypatch.setattr("app.engines.kong_piano.model_checkpoint", lambda: tmp_path / "fake.pth")
    src = tmp_path / "a.wav"
    src.write_bytes(b"xx")
    engine = KongPianoEngine()
    engine.device = "cuda"
    engine.transcribe(src, tmp_path / "a.mid", Event(), lambda *_: None)
    assert seen["kind"] == "piano"
    assert seen["checkpoint"] is not None


def test_kong_cancel_before_transcribe(tmp_path, monkeypatch):
    monkeypatch.setattr("app.engines.kong_piano._make_transcriber", lambda device, ckpt: FakeTranscriber())
    monkeypatch.setattr("app.engines.kong_piano.model_checkpoint", lambda: tmp_path / "fake.pth")
    src = tmp_path / "a.wav"
    src.write_bytes(b"xx")
    dest = tmp_path / "a.mid"
    cancel = Event()
    cancel.set()
    with pytest.raises(CancelledError):
        KongPianoEngine().transcribe(src, dest, cancel, lambda *_: None)
