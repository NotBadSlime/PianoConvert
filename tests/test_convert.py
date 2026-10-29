from pathlib import Path
from threading import Event

import pretty_midi
import pytest

from app.convert import AUDIO_SUFFIXES, ConvertError, run, run_score
from app.engines.base import CancelledError


class FakeEngine:
    def __init__(
        self,
        fail: Exception | None = None,
        hang_until_cancel: bool = False,
        raise_cancel_after_write: bool = False,
    ):
        self.fail = fail
        self.hang_until_cancel = hang_until_cancel
        self.raise_cancel_after_write = raise_cancel_after_write
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
        if self.raise_cancel_after_write:
            raise CancelledError()


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
    assert result.keyboard_path.exists()
    assert "【简谱】" in result.keyboard_path.read_text(encoding="utf-8")
    assert "<score-partwise" in result.musicxml_path.read_text(encoding="utf-8")
    assert result.folder.name.startswith("tune_")


def test_unicode_filename_writes(tmp_path):
    src = tmp_path / "你永远无法回到过去.mp3"
    src.write_bytes(b"xx")
    result = run(src, "piano", engines={"piano": FakeEngine()}, cancel=Event(), output_root=tmp_path)
    assert result.status == "success"
    assert result.midi_path.exists()
    assert "你永远无法回到过去" in result.folder.name


def test_partial_when_musicxml_fails(tmp_path, monkeypatch):
    src = tmp_path / "tune.wav"
    src.write_bytes(b"xx")

    def boom(midi, dest, *args):
        raise RuntimeError("xml down")

    monkeypatch.setattr("app.convert.midi_to_musicxml", boom)
    result = run(src, "piano", engines={"piano": FakeEngine()}, cancel=Event(), output_root=tmp_path)
    assert result.status == "partial"
    assert result.midi_path.exists()
    assert result.keyboard_path.exists()
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
    assert result.folder.exists()


def test_oserror_write_humanized(tmp_path, monkeypatch):
    src = tmp_path / "tune.wav"
    src.write_bytes(b"xx")

    def boom(midi, dest, *args):
        raise OSError(28, "No space")

    monkeypatch.setattr("app.convert.midi_to_musicxml", boom)
    result = run(src, "piano", engines={"piano": FakeEngine()}, cancel=Event(), output_root=tmp_path)
    assert result.status == "failed"
    assert "无法写入输出目录" in result.error
    assert result.folder.exists()


def test_musicxml_cancelled_error_does_not_keep_folder(tmp_path, monkeypatch):
    src = tmp_path / "tune.wav"
    src.write_bytes(b"xx")

    def boom(midi, dest, *args):
        raise CancelledError()

    monkeypatch.setattr("app.convert.midi_to_musicxml", boom)
    result = run(src, "piano", engines={"piano": FakeEngine()}, cancel=Event(), output_root=tmp_path)
    assert result.status == "cancelled"
    assert not result.folder.exists()


def test_cancel_does_not_keep_folder(tmp_path):
    src = tmp_path / "tune.flac"
    src.write_bytes(b"xx")
    engine = FakeEngine(hang_until_cancel=True)
    result = run(src, "other", engines={"other": engine}, cancel=Event(), output_root=tmp_path)
    assert result.status == "cancelled"
    assert not result.folder.exists()
    assert engine.calls == 1


def test_cancel_before_start(tmp_path):
    src = tmp_path / "tune.mp3"
    src.write_bytes(b"xx")
    cancel = Event()
    cancel.set()
    engine = FakeEngine()
    result = run(src, "piano", engines={"piano": engine}, cancel=cancel, output_root=tmp_path)
    assert result.status == "cancelled"
    assert not result.folder.exists()


def test_audio_suffixes():
    assert ".mp3" in AUDIO_SUFFIXES
    assert ".m4a" in AUDIO_SUFFIXES


def test_run_score_from_midi(tmp_path):
    src = tmp_path / "song.mid"
    pm = pretty_midi.PrettyMIDI(initial_tempo=120)
    inst = pretty_midi.Instrument(program=0)
    inst.notes.append(pretty_midi.Note(velocity=80, pitch=60, start=0.0, end=0.4))
    pm.instruments.append(inst)
    pm.write(str(src))
    result = run_score(src, Event(), output_root=tmp_path)
    assert result.status == "success"
    assert result.kind == "score"
    assert result.keyboard_path.exists()
    text = result.keyboard_path.read_text(encoding="utf-8")
    assert "【简谱】" in text
    assert "【键盘谱】" in text
    assert result.midi_path.exists()
    # every conversion leaves both a MIDI and a MusicXML file
    assert "<score-partwise" in result.musicxml_path.read_text(encoding="utf-8")


def test_run_score_rejects_audio(tmp_path):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    with pytest.raises(ConvertError, match="乐谱"):
        run_score(src, Event(), output_root=tmp_path)


def test_rerender_keeps_title_and_notes_and_applies_settings(tmp_path):
    from app.convert import rerender_keyboard, run_pdf

    src = tmp_path / "rolled.pdf"
    src.write_bytes(b"%PDF-1.4 fake")

    def recognize(pdf, work_dir, stop):
        out = work_dir / "rolled_vector.mid"
        pm = pretty_midi.PrettyMIDI(initial_tempo=120)
        pm.text_events.append(pretty_midi.Text("PianoConvert:arpeggio:48,52,55", 0.0))
        inst = pretty_midi.Instrument(program=0, name="Staff 1")
        for p in (48, 52, 55):
            inst.notes.append(pretty_midi.Note(80, p, 0.0, 1.9))
        pm.instruments.append(inst)
        pm.write(str(out))
        return out

    result = run_pdf(src, Event(), output_root=tmp_path / "out", recognize=recognize, style="simple")
    assert result.status == "success", result.error
    assert "{ZCB}" in result.keyboard_path.read_text(encoding="utf-8")
    text = rerender_keyboard("pdf", src, result.midi_path, result.musicxml_path, result.keyboard_path,
                             style="simple", arpeggio="chord")
    assert "{ZCB}" not in text and "(ZCB)" in text
    assert text.splitlines()[0] == "# rolled"
    assert "直接读取" in text                      # the note about where the score came from
    assert result.keyboard_path.read_text(encoding="utf-8") == text

def test_run_score_from_musicxml_also_writes_midi(tmp_path):
    from music21 import note, stream

    part = stream.Part()
    for name in ("C4", "D4", "E4", "F4"):
        part.append(note.Note(name, quarterLength=1))
    src = tmp_path / "tune.musicxml"
    stream.Score([part]).write("musicxml", fp=str(src))
    result = run_score(src, Event(), output_root=tmp_path / "out")
    assert result.status == "success", result.error
    pm = pretty_midi.PrettyMIDI(str(result.midi_path))
    assert [n.pitch for inst in pm.instruments for n in inst.notes] == [60, 62, 64, 65]


def _read_as_midi(work_dir):
    out = work_dir / "sheet_vector.mid"
    pm = pretty_midi.PrettyMIDI(initial_tempo=120)
    inst = pretty_midi.Instrument(program=0, name="Staff 1")
    for i, p in enumerate((60, 62, 64, 65)):
        inst.notes.append(pretty_midi.Note(80, p, i * 0.5, i * 0.5 + 0.45))
    pm.instruments.append(inst)
    pm.write(str(out))
    return out


def test_run_pdf_read_as_midi_also_writes_musicxml(tmp_path):
    from app.convert import run_pdf

    src = tmp_path / "sheet.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    result = run_pdf(src, Event(), output_root=tmp_path / "out", recognize=lambda pdf, work_dir, stop: _read_as_midi(work_dir))
    assert result.status == "success", result.error
    assert result.midi_path.exists() and result.keyboard_path.exists()
    assert "<score-partwise" in result.musicxml_path.read_text(encoding="utf-8")


def test_run_pdf_read_as_musicxml_also_writes_midi(tmp_path):
    from music21 import note, stream

    from app.convert import run_pdf

    src = tmp_path / "photo.png"
    src.write_bytes(b"fake")

    def recognize(pdf, work_dir, stop):
        part = stream.Part()
        for name in ("C4", "E4", "G4", "C5"):
            part.append(note.Note(name, quarterLength=1))
        out = work_dir / "recognized.musicxml"
        stream.Score([part]).write("musicxml", fp=str(out))
        return out

    result = run_pdf(src, Event(), output_root=tmp_path / "out", recognize=recognize)
    assert result.status == "success", result.error
    pm = pretty_midi.PrettyMIDI(str(result.midi_path))
    assert [n.pitch for inst in pm.instruments for n in inst.notes] == [60, 64, 67, 72]


def test_grid_divisors_follow_the_keyboard_grid():
    from app.convert import _grid_divisors

    head = "# x\n# 1=C  {meter}  ♩=120  每拍 {k} 格（一格 = 十六分音符）  原神风物之诗琴 C3–B5 三排白键\n"
    assert _grid_divisors(head.format(meter="4/4", k=4)) == (4,)
    assert _grid_divisors(head.format(meter="6/8", k=2)) == (4,)
    assert _grid_divisors(head.format(meter="4/4", k=3)) == (3,)
    assert _grid_divisors(head.format(meter="2/2", k=1)) == (1,)
    assert _grid_divisors(head.format(meter="2/2", k=3)) == (3,)
    changed = head.format(meter="3/4", k=4) + "【键盘谱】\n 9 | 〔6/8〕 A--- |\n"
    assert _grid_divisors(changed) == (4, 8)
    assert _grid_divisors(changed + "【指尖键盘谱】\n〔2/16〕\n") == (4, 8)   # not the appended section
    assert _grid_divisors("no header") is None


def test_musicxml_from_midi_is_rounded_to_the_given_grid(tmp_path):
    from app.score_io import midi_to_musicxml

    src = tmp_path / "loose.mid"
    pm = pretty_midi.PrettyMIDI(initial_tempo=120)
    inst = pretty_midi.Instrument(program=0)
    for q in (0.0, 0.31, 0.5, 1.0, 1.31, 1.5, 2.0, 3.0):   # a little late, as played
        inst.notes.append(pretty_midi.Note(80, 60, q * 0.5, q * 0.5 + 0.1))
    pm.instruments.append(inst)
    pm.write(str(src))
    midi_to_musicxml(src, tmp_path / "default.musicxml")
    midi_to_musicxml(src, tmp_path / "grid.musicxml", (4,))
    assert "<time-modification>" in (tmp_path / "default.musicxml").read_text(encoding="utf-8")   # triplets
    assert "<time-modification>" not in (tmp_path / "grid.musicxml").read_text(encoding="utf-8")
