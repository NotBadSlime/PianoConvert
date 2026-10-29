from pathlib import Path
from threading import Event

import numpy as np
import pytest
import soundfile as sf

from app import transkun_infer
from app.engines.base import CancelledError
from app.engines.transkun_piano import TranskunPianoEngine


def _write_midi(path):
    import pretty_midi

    pm = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(program=0)
    inst.notes.append(pretty_midi.Note(velocity=70, pitch=60, start=0.0, end=0.4))
    pm.instruments.append(inst)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    pm.write(str(path))


# ---------------------------------------------------------------- the engine


def test_transkun_engine_runs_the_model_and_reports_progress(tmp_path, monkeypatch):
    calls = {}
    monkeypatch.setattr(transkun_infer, "load_model", lambda device: calls.setdefault("device", device) or "model")

    def fake_transcribe(model, audio, midi, device, should_stop=None, on_progress=None):
        calls["args"] = (model, Path(audio).name, device)
        on_progress(0.5)
        on_progress(1.0)
        _write_midi(midi)

    monkeypatch.setattr(transkun_infer, "transcribe_file", fake_transcribe)
    seen = []
    engine = TranskunPianoEngine(device="cpu")
    dest = tmp_path / "a.mid"
    engine.transcribe(tmp_path / "a.wav", dest, Event(), lambda message, ratio: seen.append((message, ratio)))
    assert dest.exists()
    assert calls["device"] == "cpu"
    assert calls["args"][1:] == ("a.wav", "cpu")
    # the engine's part of the bar runs from 30 % to 70 %
    ratios = [ratio for _message, ratio in seen]
    assert ratios[0] == pytest.approx(0.3)
    assert ratios[-1] == pytest.approx(0.7)
    assert ratios == sorted(ratios)


def test_transkun_engine_cuda_uses_the_downloaded_runtime(tmp_path, monkeypatch):
    seen = {}

    def fake(kind, audio, midi, checkpoint, cancel, on_progress):
        seen.update(kind=kind, checkpoint=checkpoint)
        Path(midi).write_bytes(b"MThd")

    monkeypatch.setattr("app.gpu_setup.transcribe_with_gpu", fake)
    monkeypatch.setattr(transkun_infer, "load_model", lambda device: pytest.fail("the GPU runtime loads the model"))
    engine = TranskunPianoEngine()
    engine.device = "cuda"
    engine.transcribe(tmp_path / "a.wav", tmp_path / "a.mid", Event(), lambda *_: None)
    assert seen == {"kind": "transkun", "checkpoint": None}


def test_transkun_engine_cancel_before_start(tmp_path, monkeypatch):
    monkeypatch.setattr(transkun_infer, "load_model", lambda device: pytest.fail("cancelled before loading"))
    cancel = Event()
    cancel.set()
    with pytest.raises(CancelledError):
        TranskunPianoEngine(device="cpu").transcribe(tmp_path / "a.wav", tmp_path / "a.mid", cancel, lambda *_: None)


def test_transkun_engine_turns_a_stop_between_segments_into_cancelled(tmp_path, monkeypatch):
    monkeypatch.setattr(transkun_infer, "load_model", lambda device: object())

    def fake_transcribe(model, audio, midi, device, should_stop=None, on_progress=None):
        assert should_stop is not None
        raise transkun_infer.Cancelled()

    monkeypatch.setattr(transkun_infer, "transcribe_file", fake_transcribe)
    with pytest.raises(CancelledError):
        TranskunPianoEngine(device="cpu").transcribe(tmp_path / "a.wav", tmp_path / "a.mid", Event(), lambda *_: None)


# ---------------------------------------------------------------- the helpers


def test_unavailable_reason_names_the_problem(monkeypatch):
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    assert "TransKun" in transkun_infer.unavailable_reason()


def test_read_audio_gives_frames_by_channels_at_the_model_rate(tmp_path):
    rate = 22050
    t = np.arange(rate) / rate
    stereo = np.stack([np.sin(2 * np.pi * 440 * t), np.sin(2 * np.pi * 660 * t)], axis=1) * 0.5
    path = tmp_path / "s.wav"
    sf.write(str(path), stereo, rate)
    audio = transkun_infer.read_audio(path, 44100)
    assert audio.dtype == np.float32
    assert audio.shape[1] == 2
    assert abs(audio.shape[0] - 44100) <= 4
    mono = tmp_path / "m.wav"
    sf.write(str(mono), np.sin(2 * np.pi * 440 * np.arange(44100) / 44100) * 0.5, 44100)
    assert transkun_infer.read_audio(mono, 44100).shape == (44100, 1)


def test_pedal_holds_notes_until_it_comes_up():
    pytest.importorskip("transkun")
    from transkun.Data import Note

    notes = [
        Note(0.0, 0.2, 60, 80),  # ends while the pedal is down: held until 1.0
        Note(1.2, 1.4, 62, 80),  # no pedal: unchanged
        Note(0.5, 0.6, -64, 127),  # pedal events are kept as they are
    ]
    pedal = Note(0.1, 1.0, -64, 127)
    out = transkun_infer.extend_by_pedal([notes[0], notes[1], pedal])
    keys = {n.pitch: n for n in out if n.pitch > 0}
    assert keys[60].end == pytest.approx(1.0)
    assert keys[62].end == pytest.approx(1.4)
    assert [n.pitch for n in out if n.pitch < 0] == [-64]
    # without any pedal event the notes come back untouched
    plain = transkun_infer.extend_by_pedal([Note(0.0, 0.2, 60, 80)])
    assert plain[0].end == pytest.approx(0.2)


def test_extend_by_pedal_survives_bad_pedal_data(monkeypatch):
    pytest.importorskip("transkun")
    import transkun.Data

    from transkun.Data import Note

    def boom(keys, pedals):
        raise AssertionError("overlap")

    monkeypatch.setattr(transkun.Data, "extendPedal", boom)
    out = transkun_infer.extend_by_pedal([Note(0.0, 0.2, 60, 80), Note(0.1, 1.0, -64, 127)])
    assert [n.pitch for n in out] == [60, -64]


# ---------------------------------------------------------------- the real model


def test_real_model_transcribes_a_short_recording(tmp_path):
    pytest.importorskip("transkun")
    assert transkun_infer.unavailable_reason() == ""
    rate = 44100
    t = np.arange(int(3.0 * rate)) / rate
    tone = sum(np.sin(2 * np.pi * 440 * k * t) / k for k in (1, 2, 3)) * np.exp(-1.2 * (t % 1.5))
    path = tmp_path / "tone.wav"
    sf.write(str(path), (tone / np.abs(tone).max() * 0.6).astype(np.float32), rate)
    model = transkun_infer.load_model("cpu")
    progress = []
    dest = tmp_path / "out" / "tone.mid"
    transkun_infer.transcribe_file(model, path, dest, "cpu", on_progress=progress.append)
    import pretty_midi

    assert dest.exists()
    assert len(pretty_midi.PrettyMIDI(str(dest)).instruments) == 1
    assert progress and progress[-1] == pytest.approx(1.0)
    assert "transcribeFrames" not in model.__dict__  # the progress hook is taken off again


def test_real_model_stops_between_segments(tmp_path):
    pytest.importorskip("transkun")
    rate = 44100
    path = tmp_path / "long.wav"
    sf.write(str(path), (np.random.default_rng(0).standard_normal(rate * 20) * 0.05).astype(np.float32), rate)
    model = transkun_infer.load_model("cpu")
    stop = Event()
    seen = []

    def progress(ratio):
        seen.append(ratio)
        stop.set()

    with pytest.raises(transkun_infer.Cancelled):
        transkun_infer.transcribe_file(model, path, tmp_path / "x.mid", "cpu", should_stop=stop.is_set, on_progress=progress)
    assert len(seen) == 1
    assert not (tmp_path / "x.mid").exists()
