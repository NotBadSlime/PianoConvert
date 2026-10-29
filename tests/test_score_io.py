from pathlib import Path

import pretty_midi

from app.score_io import midi_to_musicxml, write_notes_midi


def _one_note_midi(path: Path) -> None:
    pm = pretty_midi.PrettyMIDI(initial_tempo=120)
    inst = pretty_midi.Instrument(program=0)
    inst.notes.append(pretty_midi.Note(velocity=80, pitch=60, start=0.0, end=0.5))
    pm.instruments.append(inst)
    pm.write(str(path))


def test_midi_to_musicxml_contains_score_partwise(tmp_path):
    midi = tmp_path / "n.mid"
    xml = tmp_path / "n.musicxml"
    _one_note_midi(midi)
    midi_to_musicxml(midi, xml)
    text = xml.read_text(encoding="utf-8")
    assert "<score-partwise" in text
    assert xml.exists()


def test_write_notes_midi_roundtrip(tmp_path):
    dest = tmp_path / "out.mid"
    write_notes_midi(dest, [(60, 0.0, 0.5, 90)], program=0, is_drum=False)
    pm = pretty_midi.PrettyMIDI(str(dest))
    assert len(pm.instruments[0].notes) == 1
    assert pm.instruments[0].notes[0].pitch == 60

def test_musicxml_falls_back_to_the_file_rewritten_without_drums(tmp_path, monkeypatch):
    from app import score_io

    src = tmp_path / "odd.mid"
    pm = pretty_midi.PrettyMIDI(initial_tempo=120)
    piano = pretty_midi.Instrument(program=0)
    piano.notes.append(pretty_midi.Note(velocity=80, pitch=60, start=0.0, end=0.5))
    drums = pretty_midi.Instrument(program=0, is_drum=True)
    drums.notes.append(pretty_midi.Note(velocity=80, pitch=42, start=0.0, end=0.1))
    pm.instruments += [piano, drums]
    pm.write(str(src))
    real = score_io._write_musicxml
    tried = []

    def picky(path, dest, divisors):
        tried.append(Path(path).name)
        if Path(path).name != "no_drums.mid":
            raise RuntimeError("music21 cannot write this one")
        real(path, dest, divisors)

    monkeypatch.setattr(score_io, "_write_musicxml", picky)
    score_io.midi_to_musicxml(src, tmp_path / "out.musicxml", (4,))
    assert tried == ["odd.mid", "rewritten.mid", "no_drums.mid"]
    assert "<score-partwise" in (tmp_path / "out.musicxml").read_text(encoding="utf-8")


def test_musicxml_to_midi_keeps_the_notes(tmp_path):
    from music21 import note, stream

    from app.score_io import musicxml_to_midi

    part = stream.Part()
    for name in ("C4", "E4", "G4"):
        part.append(note.Note(name, quarterLength=1))
    xml = tmp_path / "t.musicxml"
    stream.Score([part]).write("musicxml", fp=str(xml))
    musicxml_to_midi(xml, tmp_path / "t.mid")
    pm = pretty_midi.PrettyMIDI(str(tmp_path / "t.mid"))
    assert [n.pitch for inst in pm.instruments for n in inst.notes] == [60, 64, 67]
