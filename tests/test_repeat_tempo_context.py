"""A skipped first ending cannot change the tempo of the second ending."""
from pathlib import Path

import pretty_midi
import pytest
from music21 import bar as barline, meter, note, spanner, stream, tempo

from app import pdf_vector as pv
from app.score_io import load_musicxml_playback, musicxml_to_midi


def _pdf_bars(second_tempo: int | None) -> list[pv.Bar]:
    staff = pv.Staff(40, 300, [700, 695, 690, 685, 680], 0)
    bars = []
    for index, pitch in enumerate((60, 62, 64, 65)):
        head = pv.Head(100, 690, 4, False, staff, pitch=pitch)
        event = pv.Event(100, staff, 4, [head], base=4)
        bars.append(pv.Bar([event], meter=(4, 4), x0=index))
    bars[0].repeat_start = True
    bars[0].tempo = 120
    bars[2].repeat_end = True
    bars[2].volta = 1
    bars[2].tempo = 60
    bars[3].volta = 2
    bars[3].tempo = second_tempo or 0
    return bars


def _xml_ending_score(path: Path, second_tempo: int | None) -> Path:
    part = stream.Part()
    measures = []
    for index, pitch in enumerate((60, 62, 64, 65)):
        measure = stream.Measure(number=index + 1)
        if index == 0:
            measure.insert(0, meter.TimeSignature("4/4"))
            measure.insert(0, tempo.MetronomeMark(number=120))
            measure.leftBarline = barline.Repeat(direction="start")
        if index == 2:
            measure.insert(0, tempo.MetronomeMark(number=60))
            measure.rightBarline = barline.Repeat(direction="end")
        if index == 3 and second_tempo is not None:
            measure.insert(0, tempo.MetronomeMark(number=second_tempo))
        measure.insert(0, note.Note(pitch, quarterLength=4))
        part.append(measure)
        measures.append(measure)
    part.insert(0, spanner.RepeatBracket(measures[2], number="1"))
    part.insert(0, spanner.RepeatBracket(measures[3], number="2"))
    stream.Score([part]).write("musicxml", fp=str(path))
    return path


@pytest.mark.parametrize("second_tempo", [None, 90])
def test_pdf_second_ending_does_not_inherit_the_skipped_first_endings_tempo(tmp_path, second_tempo):
    bars = _pdf_bars(second_tempo)
    score = pv.build_score(bars, 120)
    assert [int(bar.x0) for _, bar in score.timeline] == [0, 1, 2, 0, 1, 3]
    expected = [(8, 60), (12, 120)]
    if second_tempo is not None:
        expected.append((20, second_tempo))
    assert score.tempos == expected
    midi_path = pv.write_midi(score, tmp_path / "pdf-endings.mid")
    expected_end = 12 + 4 * 60 / (second_tempo or 120)
    assert pretty_midi.PrettyMIDI(str(midi_path)).get_end_time() == pytest.approx(expected_end, abs=1e-5)


@pytest.mark.parametrize("second_tempo", [None, 90])
def test_musicxml_second_ending_keeps_the_performed_tempo_unless_it_prints_a_change(tmp_path, second_tempo):
    source = _xml_ending_score(tmp_path / "xml-endings.musicxml", second_tempo)
    original = source.read_bytes()
    played = load_musicxml_playback(source)
    assert [int(n.pitch.midi) for n in played.parts[0].recurse().notes] == [60, 62, 64, 60, 62, 65]
    marks = [(float(mark.offset), mark.number) for mark in played.flatten().getElementsByClass(tempo.MetronomeMark)]
    expected = [(0, 120), (8, 60), (12, 120)]
    if second_tempo is not None:
        expected.append((20, second_tempo))
    assert marks == expected
    midi_path = tmp_path / "xml-endings.mid"
    musicxml_to_midi(source, midi_path)
    expected_end = 12 + 4 * 60 / (second_tempo or 120)
    assert pretty_midi.PrettyMIDI(str(midi_path)).get_end_time() == pytest.approx(expected_end, abs=1e-5)
    assert source.read_bytes() == original
