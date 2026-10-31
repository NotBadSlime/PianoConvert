from pathlib import Path

import mido
import pytest

from app.keyboard_score import (
    NoteEvent,
    arrange_events,
    best_transpose,
    keyboard_text,
    notes_from_midi,
    notes_from_musicxml,
)


def _xml_note(step: str, alter: int = 0) -> str:
    return (
        f"<note><pitch><step>{step}</step><alter>{alter}</alter><octave>4</octave>"
        "</pitch><duration>1</duration><type>quarter</type></note>"
    )


def _measure(number: int, pitches: list[tuple[str, int]], fifths: int | None = None) -> str:
    signature = f"<key><fifths>{fifths}</fifths></key>" if fifths is not None else ""
    return (
        f'<measure number="{number}"><attributes><divisions>1</divisions>{signature}'
        "<time><beats>4</beats><beat-type>4</beat-type></time></attributes>"
        + "".join(_xml_note(step, alter) for step, alter in pitches)
        + "</measure>"
    )


def _write_xml(path: Path, parts: list[str]) -> Path:
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?><score-partwise version="4.0"><part-list>'
        + "".join(
            f'<score-part id="P{i}"><part-name>Part {i}</part-name></score-part>'
            for i in range(len(parts))
        )
        + "</part-list>"
        + "".join(f'<part id="P{i}">{part}</part>' for i, part in enumerate(parts))
        + "</score-partwise>",
        encoding="utf-8",
    )
    return path


def test_explicit_c_signature_keeps_local_accidentals_local():
    # The old histogram chooses +5 once three local F-sharps are added, moving
    # every C/E/G in the opening even though the written signature remains C.
    pitches = [60, 64, 67] * 6 + [66] * 3 + [65]
    assert best_transpose(pitches) == 5
    notes = [NoteEvent(p, i, i + 0.8, key_fifths=0) for i, p in enumerate(pitches)]
    arranged, info = arrange_events(notes)
    assert [n.orig for n in arranged] == pitches
    assert [n.key for n in arranged][-4:] == [67, 67, 67, 65]
    assert info.shift == 0
    assert info.snapped == 3


@pytest.mark.parametrize("range_strategy", ["modern", "smart"])
def test_musicxml_modulation_preserves_c_intro_and_return(tmp_path, range_strategy):
    part = (
        _measure(1, [("C", 0), ("E", 0), ("G", 0), ("C", 0)], 0)
        + _measure(2, [("G", 0), ("A", 0), ("B", 0), ("F", 1)], 1)
        + _measure(3, [("C", 0), ("E", 0), ("G", 0), ("C", 0)], 0)
    )
    notes, _, _ = notes_from_musicxml(_write_xml(tmp_path / "modulation.musicxml", [part]))
    assert [n.key_fifths for n in notes] == [0] * 4 + [1] * 4 + [0] * 4
    arranged, info = arrange_events(notes, range_strategy=range_strategy)
    assert [n.orig for n in arranged] == [60, 64, 67, 60, 72, 74, 76, 71, 60, 64, 67, 60]
    assert info.regional_shifts == (0, 5)
    text = keyboard_text(notes, "调号变化", range_strategy=range_strategy)
    assert "按调号分段移调" in text
    assert "整首移调" not in text


def test_musicxml_chromatic_note_does_not_change_signature(tmp_path):
    part = _measure(1, [("F", 1), ("F", 0), ("A", -1), ("A", 0)], 0)
    notes, _, _ = notes_from_musicxml(_write_xml(tmp_path / "accidentals.musicxml", [part]))
    assert [(n.pitch, n.key_fifths) for n in notes] == [(66, 0), (65, 0), (68, 0), (69, 0)]
    arranged, _ = arrange_events(notes)
    assert [n.orig for n in arranged] == [66, 65, 68, 69]
    assert [n.key for n in arranged] == [67, 65, 69, 69]


def test_musicxml_signatures_are_read_per_part(tmp_path):
    parts = [_measure(1, [("C", 0)] * 4, 0), _measure(1, [("G", 0)] * 4, 1)]
    notes, _, _ = notes_from_musicxml(_write_xml(tmp_path / "parts.musicxml", parts))
    arranged, _ = arrange_events(notes)
    assert [(n.orig, n.start) for n in arranged[:4]] == [(72, i * 0.5) for i in range(4)]
    assert [n.orig for n in arranged[4:]] == [60] * 4


def test_repeated_passage_recovers_inherited_key_and_tempo(tmp_path):
    def with_tempo(measure: str, bpm: int) -> str:
        direction = (
            "<direction><direction-type><metronome><beat-unit>quarter</beat-unit>"
            f"<per-minute>{bpm}</per-minute></metronome></direction-type></direction>"
        )
        return measure.replace("</attributes>", "</attributes>" + direction)

    intro = with_tempo(_measure(1, [("C", 0)] * 4, 0), 60)
    # This repeat starts under the earlier C signature, without reprinting it.
    start = _measure(2, [("C", 0)] * 4).replace(
        "</attributes>", '</attributes><barline location="left"><repeat direction="forward"/></barline>'
    )
    end = with_tempo(_measure(3, [("G", 0)] * 4, 1), 120).replace(
        "</measure>", '<barline location="right"><repeat direction="backward"/></barline></measure>'
    )
    path = _write_xml(tmp_path / "repeated_modulation.musicxml", [intro + start + end])
    notes, _, _ = notes_from_musicxml(path)
    assert [n.key_fifths for n in notes] == [0] * 8 + [1] * 4 + [0] * 4 + [1] * 4
    arranged, _ = arrange_events(notes)
    assert [n.orig for n in arranged] == [60] * 8 + [72] * 4 + [60] * 4 + [72] * 4
    assert notes[12].start == pytest.approx(10)
    assert notes[16].start == pytest.approx(14)
    assert notes[-1].end == pytest.approx(16)


def _write_midi(path: Path, *, signatures: bool, outside_track0: bool = False, minor: bool = False) -> Path:
    midi = mido.MidiFile(ticks_per_beat=480)
    conductor = mido.MidiTrack()
    track = mido.MidiTrack()
    midi.tracks.extend([conductor, track])
    conductor.append(mido.MetaMessage("set_tempo", tempo=500_000))
    if signatures and not outside_track0:
        conductor.append(mido.MetaMessage("key_signature", key="Am" if minor else "C"))
        conductor.append(mido.MetaMessage("key_signature", key="Em" if minor else "G", time=1440))
        conductor.append(mido.MetaMessage("key_signature", key="Am" if minor else "C", time=1920))
    for i, pitch in enumerate([60, 64, 67, 67, 69, 71, 66, 60]):
        if outside_track0:
            if signatures and i in (0, 3, 7):
                key_name = ("Em" if minor else "G") if i == 3 else ("Am" if minor else "C")
                track.append(mido.MetaMessage("key_signature", key=key_name))
            if i == 3:
                track.append(mido.MetaMessage("set_tempo", tempo=1_000_000))
        track.append(mido.Message("note_on", note=pitch, velocity=90))
        track.append(mido.Message("note_off", note=pitch, velocity=0, time=480))
    midi.save(path)
    return path


@pytest.mark.parametrize("outside_track0", [False, True])
@pytest.mark.parametrize("minor", [False, True])
def test_midi_key_regions_survive_loading_and_tempo_retiming(tmp_path, outside_track0, minor):
    path = _write_midi(tmp_path / "keys.mid", signatures=True, outside_track0=outside_track0, minor=minor)
    notes, _, _ = notes_from_midi(path)
    assert [n.key_fifths for n in notes] == [0, 0, 0, 1, 1, 1, 1, 0]
    arranged, info = arrange_events(notes)
    assert [n.orig for n in arranged] == [60, 64, 67, 72, 74, 76, 71, 60]
    assert info.regional_shifts == (0, 5)
    assert notes[-1].start == pytest.approx(5.5 if outside_track0 else 3.5)


def test_files_without_signatures_keep_existing_pitch_inference(tmp_path):
    midi_notes, _, _ = notes_from_midi(_write_midi(tmp_path / "unknown.mid", signatures=False))
    xml = _measure(1, [("D", 0), ("E", 0), ("F", 1), ("C", 1)])
    xml_notes, _, _ = notes_from_musicxml(_write_xml(tmp_path / "unknown.musicxml", [xml]))
    for notes in (midi_notes, xml_notes):
        assert all(n.key_fifths is None for n in notes)
        expected_shift = best_transpose([n.pitch for n in notes])
        arranged, info = arrange_events(notes)
        assert [n.orig for n in arranged] == [n.pitch + expected_shift for n in notes]
        assert info.shift == expected_shift
        assert info.regional_shifts == ()


def test_unknown_intro_is_not_inferred_from_later_key_region():
    notes = [NoteEvent(p, i, i + 0.8) for i, p in enumerate([60, 64, 67])]
    notes.extend(NoteEvent(66, i, i + 0.8, key_fifths=1) for i in range(3, 8))
    arranged, _ = arrange_events(notes)
    assert [n.orig for n in arranged[:3]] == [60, 64, 67]
    assert [n.orig for n in arranged[3:]] == [71] * 5
