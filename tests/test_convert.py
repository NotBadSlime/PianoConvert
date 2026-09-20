from pathlib import Path
from threading import Event

import pretty_midi
import pytest

from app.convert import AUDIO_SUFFIXES, ConvertError, run
from app.engines.base import CancelledError


class FakeEngine:
    def __init__(self, fail: Exception | None = None, hang_until_cancel: bool = False):
        self.fail = fail
        self.hang_until_cancel = hang_until_cancel
        self.calls = 0

    def transcribe(self, audio_path: Path, dest_midi: Path, cancel: Event, on_progress) -> None:
        self.calls += 1
        if self.hang_until_cancel:
            raise CancelledError()
        if self.fail:
            raise self.fail
        on_progress("转录", 0.5)
        dest_midi.parent.mkdir(parents=True, exist_ok=True)
        pm = pretty_midi.PrettyMIDI()
        inst = pretty_midi.Instrument(program=0)
        inst.notes.append(pretty_midi.Note(velocity=80, pitch=64, start=0.0, end=0.4))
        pm.instruments.append(inst)
        pm.write(str(dest_midi))


def test_rejects_bad_suffix(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("x")
    with pytest.raises(ConvertError, match="不支持"):
        run(src, "piano", engines={"piano": FakeEngine()}, cancel=Event(), output_root=tmp_path)


def test_success_writes_midi_and_musicxml(tmp_path):
    src = tmp_path / "tune.mp3"
    src.write_bytes(b"xx")
    result = run(src, "piano", engines={"piano": FakeEngine()}, cancel=Event(), output_root=tmp_path)
    assert result.status == "success"
    assert result.midi_path.exists()
    assert result.musicxml_path.exists()
    assert "<score-partwise" in result.musicxml_path.read_text(encoding="utf-8")
    assert result.folder.name.startswith("tune_")


def test_partial_when_musicxml_fails(tmp_path, monkeypatch):
    src = tmp_path / "tune.wav"
    src.write_bytes(b"xx")

    def boom(midi, dest):
        raise RuntimeError("xml down")

    monkeypatch.setattr("app.convert.midi_to_musicxml", boom)
    result = run(src, "piano", engines={"piano": FakeEngine()}, cancel=Event(), output_root=tmp_path)
    assert result.status == "partial"
    assert result.midi_path.exists()
    assert result.error


def test_failed_transcribe_humanizes_cuda(tmp_path):
    src = tmp_path / "tune.mp3"
    src.write_bytes(b"xx")
    result = run(
        src,
        "piano",
        engines={"piano": FakeEngine(fail=RuntimeError("CUDA out of memory"))},
        cancel=Event(),
        output_root=tmp_path,
    )
    assert result.status == "failed"
    assert "显存不足" in result.error


def test_cancel_does_not_keep_folder(tmp_path):
    src = tmp_path / "tune.flac"
    src.write_bytes(b"xx")
    cancel = Event()
    cancel.set()
    result = run(src, "other", engines={"other": FakeEngine(hang_until_cancel=True)}, cancel=cancel, output_root=tmp_path)
    assert result.status == "cancelled"
    assert not result.folder.exists()


def test_audio_suffixes():
    assert ".mp3" in AUDIO_SUFFIXES
    assert ".m4a" in AUDIO_SUFFIXES
