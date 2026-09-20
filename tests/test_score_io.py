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
