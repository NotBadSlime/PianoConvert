"""A new repeat inherits its first performed entry, not a skipped ending."""
from pathlib import Path

import pretty_midi
import pytest
from music21 import bar as barline, meter, note, spanner, stream, tempo

from app import pdf_vector as pv
from app.keyboard_score import notes_from_musicxml, seconds_to_quarters
from app.score_io import load_musicxml_playback, musicxml_to_midi


ORDER = [0, 1, 2, 0, 1, 3, 4, 5, 4, 5, 6]


def _expected(entry, local):
    marks = [(0, 120), (8, 60), (12, 120)]
    if entry:
        marks.append((24, entry))
    if local:
        marks += [(26, local), (32, entry or 120), (34, local)]
    return marks


def _pdf_bars(entry, local):
    staff = pv.Staff(40, 300, [700, 695, 690, 685, 680], 0)
    bars = []
    for index in range(7):
        events = [pv.Event(x, staff, 1, [pv.Head(x, 690, 1, False, staff, pitch=60+index)], base=1)
                  for x in (80, 105, 130, 155)]
        bars.append(pv.Bar(events, meter=(4, 4), x0=index))
    bars[0].repeat_start = True
    bars[0].tempo = 120
    bars[2].repeat_end, bars[2].volta, bars[2].tempo = True, 1, 60
    bars[3].volta = 2
    bars[4].repeat_start, bars[4].tempo = True, entry or 0
    bars[4].tempo_positions = [(130, local)] if local else []
    bars[5].repeat_end = True
    return bars


def _write_xml(path: Path, entry, local, tempo_part=0):
    score = stream.Score()
    for part_index in range(2):
        part = stream.Part()
        measures = []
        for index in range(7):
            measure = stream.Measure(number=index+1)
            if index == 0:
                measure.insert(0, meter.TimeSignature('4/4'))
                measure.leftBarline = barline.Repeat(direction='start')
            if part_index == tempo_part:
                if index == 0: measure.insert(0, tempo.MetronomeMark(number=120))
                if index == 2: measure.insert(0, tempo.MetronomeMark(number=60))
                if index == 4 and entry: measure.insert(0, tempo.MetronomeMark(number=entry))
                if index == 4 and local: measure.insert(2, tempo.MetronomeMark(number=local))
            if index == 2: measure.rightBarline = barline.Repeat(direction='end')
            if index == 4: measure.leftBarline = barline.Repeat(direction='start')
            if index == 5: measure.rightBarline = barline.Repeat(direction='end')
            for q in range(4):
                measure.insert(q, note.Note(60+index+part_index*12))
            part.append(measure)
            measures.append(measure)
        part.insert(0, spanner.RepeatBracket(measures[2], number='1'))
        part.insert(0, spanner.RepeatBracket(measures[3], number='2'))
        score.insert(0, part)
    score.write('musicxml', fp=str(path))
    return path


@pytest.mark.parametrize('entry,local', [(None, None), (None, 80), (90, 80)])
def test_pdf_new_repeat_after_an_ending_restores_its_actual_first_entry(tmp_path, entry, local):
    score = pv.build_score(_pdf_bars(entry, local), 0)
    assert [bar.x0 for _q,bar in score.timeline] == ORDER
    assert [(0,score.bpm)] + score.tempos == _expected(entry, local)
    path = pv.write_midi(score, tmp_path/'new-repeat.mid')
    marks = _expected(entry, local)
    expected_end = sum((end-q)*60/bpm for (q,bpm),(end,_next) in zip(marks,marks[1:]+[(44,0)]))
    assert pretty_midi.PrettyMIDI(str(path)).get_end_time() == pytest.approx(expected_end,abs=1e-5)


@pytest.mark.parametrize('tempo_part', [0, 1])
@pytest.mark.parametrize('entry,local', [(None, None), (None, 80), (90, 80)])
def test_xml_new_repeat_uses_global_performed_entry_with_midbar_changes(tmp_path, entry, local, tempo_part):
    path = _write_xml(tmp_path/'new-repeat.musicxml', entry, local, tempo_part)
    original = path.read_bytes()
    played = load_musicxml_playback(path)
    first = list(played.parts[0].getElementsByClass(stream.Measure))
    assert [int(m.recurse().notes[0].pitch.midi)-60 for m in first] == ORDER
    events, tempos, _meters = notes_from_musicxml(path)
    actual = [(round(seconds_to_quarters(t,tempos),6),round(bpm,6)) for t,bpm in tempos]
    actual = [mark for i,mark in enumerate(actual) if i == 0 or mark[1] != actual[i-1][1]]
    assert actual == _expected(entry,local)
    dest = tmp_path/'new-repeat.mid'
    musicxml_to_midi(path,dest)
    marks = _expected(entry,local)
    expected_end = sum((end-q)*60/bpm for (q,bpm),(end,_next) in zip(marks,marks[1:]+[(44,0)]))
    assert max(n.end for n in events) == pytest.approx(expected_end,abs=1e-5)
    assert pretty_midi.PrettyMIDI(str(dest)).get_end_time() == pytest.approx(expected_end,abs=1e-5)
    assert path.read_bytes() == original
