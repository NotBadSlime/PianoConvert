from pathlib import Path
from threading import Event

import pretty_midi
import pytest

from app.engines.base import CancelledError
from app.engines.basic_pitch import BasicPitchEngine, onnx_providers


def test_onnx_providers_follow_device(monkeypatch):
    import onnxruntime as ort

    monkeypatch.setattr(ort, "get_available_providers", lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"])
    assert onnx_providers("cuda") == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    assert onnx_providers("cpu") == ["CPUExecutionProvider"]


def test_basic_pitch_writes_midi(tmp_path, monkeypatch):
    midi_src = tmp_path / "pred.mid"
    pm = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(program=40)
    inst.notes.append(pretty_midi.Note(velocity=80, pitch=72, start=0.0, end=0.2))
    pm.instruments.append(inst)
    pm.write(str(midi_src))

    def fake_predict(*_a, **_k):
        return None, pretty_midi.PrettyMIDI(str(midi_src)), []

    monkeypatch.setattr("app.engines.basic_pitch.predict", fake_predict)
    dest = tmp_path / "out.mid"
    BasicPitchEngine().transcribe(tmp_path / "a.mp3", dest, Event(), lambda *_: None)
    out = pretty_midi.PrettyMIDI(str(dest))
    assert out.instruments[0].notes[0].pitch == 72


def test_basic_pitch_cancel_before(tmp_path, monkeypatch):
    def fake_predict(*_a, **_k):
        return None, pretty_midi.PrettyMIDI(), []

    monkeypatch.setattr("app.engines.basic_pitch.predict", fake_predict)
    cancel = Event()
    cancel.set()
    with pytest.raises(CancelledError):
        BasicPitchEngine().transcribe(tmp_path / "a.mp3", tmp_path / "out.mid", cancel, lambda *_: None)
