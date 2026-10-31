from pathlib import Path

import pretty_midi
import pytest

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


def test_musicxml_playback_repeats_only_the_marked_passage_and_preserves_source(tmp_path):
    import xml.etree.ElementTree as ET
    from music21 import bar, meter, note, stream, tempo

    from app.omr_repeats import normalize_homr_repeats
    from app.score_io import load_musicxml_playback, musicxml_to_midi

    part = stream.Part()
    for number, name in enumerate(("C4", "D4", "E4", "F4"), 1):
        measure = stream.Measure(number=number)
        if number == 1:
            measure.insert(0, meter.TimeSignature("4/4"))
            measure.insert(0, tempo.MetronomeMark(number=67))
        if number == 2:
            measure.leftBarline = bar.Repeat(direction="start")
        measure.append(note.Note(name, quarterLength=4))
        if number == 3:
            measure.rightBarline = bar.Repeat(direction="end")
        part.append(measure)
    xml = tmp_path / "repeat.musicxml"
    stream.Score([part]).write("musicxml", fp=str(xml))
    tree = ET.parse(xml)
    tree.getroot().find(".//repeat[@direction='forward']/..").set("location", "right")
    assert normalize_homr_repeats(tree.getroot()) == 1
    tree.write(xml, encoding="utf-8", xml_declaration=True)
    original = xml.read_bytes()

    performed = load_musicxml_playback(xml)
    assert [int(n.pitch.midi) for n in performed.parts[0].recurse().notes] == [60, 62, 64, 62, 64, 65]
    musicxml_to_midi(xml, tmp_path / "repeat.mid")
    pm = pretty_midi.PrettyMIDI(str(tmp_path / "repeat.mid"))
    assert [n.pitch for inst in pm.instruments for n in inst.notes] == [60, 62, 64, 62, 64, 65]
    assert pm.get_end_time() == pytest.approx(24 * 60 / 67, abs=0.001)
    assert xml.read_bytes() == original


def test_musicxml_playback_restores_inherited_tempo_after_repeat_jump(tmp_path):
    from music21 import bar, key, meter, note, stream, tempo

    from app.score_io import load_musicxml_playback, musicxml_to_midi

    part = stream.Part()
    for number, name in enumerate(("C4", "D4", "E4"), 1):
        measure = stream.Measure(number=number)
        if number == 1:
            measure.insert(0, meter.TimeSignature("4/4"))
            measure.insert(0, key.KeySignature(0))
            measure.insert(0, tempo.MetronomeMark(number=60))
        if number == 2:
            measure.leftBarline = bar.Repeat(direction="start")
        if number == 3:
            measure.insert(0, key.KeySignature(1))
            measure.insert(0, tempo.MetronomeMark(number=120))
            measure.rightBarline = bar.Repeat(direction="end")
        measure.append(note.Note(name, quarterLength=4))
        part.append(measure)
    xml = tmp_path / "modulation.musicxml"
    stream.Score([part]).write("musicxml", fp=str(xml))
    performed = load_musicxml_playback(xml)
    second_start = list(performed.parts[0].getElementsByClass(stream.Measure))[3]
    assert list(second_start.getElementsByClass(key.KeySignature))[0].sharps == 0
    assert list(second_start.getElementsByClass(tempo.MetronomeMark))[0].number == 60
    musicxml_to_midi(xml, tmp_path / "modulation.mid")
    pm = pretty_midi.PrettyMIDI(str(tmp_path / "modulation.mid"))
    assert pm.get_end_time() == pytest.approx(16)


def test_repeat_restores_implicit_c_before_the_first_printed_key(tmp_path):
    from app.keyboard_score import arrange_events, notes_from_musicxml

    measures = []
    for number, step in enumerate(("C", "C", "G"), 1):
        key_xml = "<key><fifths>1</fifths></key>" if number == 3 else ""
        start = '<barline location="left"><repeat direction="forward"/></barline>' if number == 2 else ""
        end = '<barline location="right"><repeat direction="backward"/></barline>' if number == 3 else ""
        notes = (f"<note><pitch><step>{step}</step><octave>4</octave></pitch>"
                 "<duration>1</duration><type>quarter</type></note>") * 4
        measures.append(f'<measure number="{number}"><attributes><divisions>1</divisions>'
                        f'{key_xml}<time><beats>4</beats><beat-type>4</beat-type></time>'
                        f'</attributes>{start}{notes}{end}</measure>')
    xml = tmp_path / "implicit_c.musicxml"
    xml.write_text('<score-partwise version="4.0"><part-list><score-part id="P1">'
                   '<part-name>Piano</part-name></score-part></part-list><part id="P1">'
                   + "".join(measures) + '</part></score-partwise>', encoding="utf-8")
    notes, _, _ = notes_from_musicxml(xml)
    assert [n.key_fifths for n in notes] == [0] * 8 + [1] * 4 + [0] * 4 + [1] * 4
    arranged, _ = arrange_events(notes)
    assert [n.orig for n in arranged[:8]] == [60] * 8
    assert [n.orig for n in arranged[12:16]] == [60] * 4


def test_implicit_key_with_chromatic_notes_is_consistent_on_first_play_and_repeat(tmp_path):
    from app.keyboard_score import arrange_events, best_transpose, notes_from_musicxml

    pitches = [60, 64, 67] * 3 + [66] * 3
    assert best_transpose(pitches) == 5  # The old unknown-key heuristic changed this passage.
    patterns = [pitches, pitches, [67] * 12]
    spellings = {60: ("C", 0), 64: ("E", 0), 67: ("G", 0), 66: ("F", 1)}
    measures = []
    for number, pattern in enumerate(patterns, 1):
        key_xml = "<key><fifths>1</fifths></key>" if number == 3 else ""
        start = '<barline location="left"><repeat direction="forward"/></barline>' if number == 2 else ""
        end = '<barline location="right"><repeat direction="backward"/></barline>' if number == 3 else ""
        notes = ""
        for pitch in pattern:
            step, alter = spellings[pitch]
            notes += (f"<note><pitch><step>{step}</step><alter>{alter}</alter><octave>4</octave>"
                      "</pitch><duration>1</duration><type>quarter</type></note>")
        measures.append(f'<measure number="{number}"><attributes><divisions>1</divisions>'
                        f'{key_xml}<time><beats>12</beats><beat-type>4</beat-type></time>'
                        f'</attributes>{start}{notes}{end}</measure>')
    xml = tmp_path / "chromatic_repeat.musicxml"
    xml.write_text('<score-partwise version="4.0"><part-list><score-part id="P1">'
                   '<part-name>Piano</part-name></score-part></part-list><part id="P1">'
                   + "".join(measures) + '</part></score-partwise>', encoding="utf-8")
    original = xml.read_bytes()
    notes, _, _ = notes_from_musicxml(xml)
    arranged, _ = arrange_events(notes)
    assert [n.orig for n in arranged[12:24]] == pitches
    assert [n.orig for n in arranged[36:48]] == pitches
    assert xml.read_bytes() == original


def test_musicxml_playback_keeps_first_and_second_endings(tmp_path):
    from music21 import bar, meter, note, spanner, stream

    from app.score_io import load_musicxml_playback

    part = stream.Part()
    measures = []
    for number, name in enumerate(("C4", "D4", "E4"), 1):
        measure = stream.Measure(number=number)
        if number == 1:
            measure.insert(0, meter.TimeSignature("4/4"))
            measure.leftBarline = bar.Repeat(direction="start")
        if number == 2:
            measure.rightBarline = bar.Repeat(direction="end")
        measure.append(note.Note(name, quarterLength=4))
        part.append(measure)
        measures.append(measure)
    part.insert(0, spanner.RepeatBracket(measures[1], number=1))
    part.insert(0, spanner.RepeatBracket(measures[2], number=2))
    xml = tmp_path / "endings.musicxml"
    stream.Score([part]).write("musicxml", fp=str(xml))
    performed = load_musicxml_playback(xml)
    assert [int(n.pitch.midi) for n in performed.parts[0].recurse().notes] == [60, 62, 60, 64]
