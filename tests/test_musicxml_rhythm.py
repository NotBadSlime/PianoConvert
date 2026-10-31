from pathlib import Path

import pretty_midi
import pytest
from music21 import chord, duration, meter, note, stream, tempo, tie

from app.keyboard_score import (
    NoteEvent, STYLE_SIMPLE, keyboard_text, musicxml_to_keyboard_text,
    notes_from_musicxml, parse_keyboard_text, read_keyboard_cells,
)
from app.score_io import musicxml_to_midi


def _write(tmp_path: Path, part: stream.Part) -> Path:
    path = tmp_path / 'rhythm.musicxml'
    stream.Score([part]).write('musicxml', fp=str(path))
    return path


@pytest.mark.parametrize('unit,expected', [(2, 120), (1.5, 90), (0.5, 30)])
def test_tempo_unit_matches_exported_midi_seconds(tmp_path, unit, expected):
    part = stream.Part()
    part.append(meter.TimeSignature('4/4'))
    part.append(tempo.MetronomeMark(number=60, referent=duration.Duration(unit)))
    part.append(note.Note('C4', quarterLength=4))
    path = _write(tmp_path, part)
    events, tempos, _ = notes_from_musicxml(path)
    musicxml_to_midi(path, tmp_path / 'rhythm.mid')
    midi_end = pretty_midi.PrettyMIDI(str(tmp_path / 'rhythm.mid')).get_end_time()
    assert tempos[0][1] == expected
    assert events[-1].end == pytest.approx(midi_end, abs=1e-5)


def test_tied_notes_hold_across_barline_and_tempo_change(tmp_path):
    part = stream.Part()
    for i, bpm in enumerate((120, 60, 120)):
        bar = stream.Measure(number=i+1)
        if i == 0:
            bar.append(meter.TimeSignature('4/4'))
        bar.append(tempo.MetronomeMark(number=bpm))
        n = note.Note('C4', quarterLength=4)
        n.tie = tie.Tie(('start', 'continue', 'stop')[i])
        bar.append(n)
        part.append(bar)
    path = _write(tmp_path, part)
    original = path.read_bytes()
    events, _, _ = notes_from_musicxml(path)
    assert [(n.pitch, n.start, n.end) for n in events] == [(60, 0, 8)]
    text = musicxml_to_keyboard_text(path, 'ties', style=STYLE_SIMPLE)
    assert parse_keyboard_text(text) == [(0, [60])]
    assert max(c.q + c.slot for c in read_keyboard_cells(text)) == 12
    assert all(c.keys or c.hold for c in read_keyboard_cells(text))
    assert path.read_bytes() == original


def test_only_tied_chord_member_is_sustained(tmp_path):
    part = stream.Part()
    part.append(meter.TimeSignature('4/4'))
    for kind in ('start', 'stop'):
        c = chord.Chord(['C4', 'E4'], quarterLength=2)
        c.notes[0].tie = tie.Tie(kind)
        part.append(c)
    events, _, _ = notes_from_musicxml(_write(tmp_path, part))
    assert sorted((n.pitch, n.start, n.end) for n in events) == [
        (60, 0, 2), (64, 0, 1), (64, 1, 2),
    ]


def test_tie_does_not_join_a_different_voice(tmp_path):
    part = stream.Part()
    bar = stream.Measure(number=1)
    bar.append(meter.TimeSignature('4/4'))
    for number in (1, 2):
        voice = stream.Voice(id=number)
        for i in range(4):
            n = note.Note('C4')
            if number == 1 and i in (0, 1):
                n.tie = tie.Tie('start' if i == 0 else 'stop')
            voice.append(n)
        bar.insert(0, voice)
    part.append(bar)
    events, _, _ = notes_from_musicxml(_write(tmp_path, part))
    assert len(events) == 7
    assert sum(n.start == 0 and n.end == 1 for n in events) == 1
    assert sum(n.start == 0 and n.end == .5 for n in events) == 1


def test_broken_tie_does_not_extend_a_note_across_a_rest(tmp_path):
    part = stream.Part()
    part.append(meter.TimeSignature('4/4'))
    first = note.Note('C4')
    first.tie = tie.Tie('start')
    last = note.Note('C4')
    last.tie = tie.Tie('stop')
    part.append([first, note.Rest(), last, note.Note('D4')])
    events, _, _ = notes_from_musicxml(_write(tmp_path, part))
    assert events[0].end == .5


def test_tempo_unit_change_keeps_the_later_segment_in_time(tmp_path):
    part = stream.Part()
    part.append(meter.TimeSignature('4/4'))
    part.append(tempo.MetronomeMark(number=60, referent=duration.Duration(1.5)))
    part.append(note.Note('C4', quarterLength=4))
    part.append(tempo.MetronomeMark(number=60))
    part.append(note.Note('D4', quarterLength=4))
    path = _write(tmp_path, part)
    events, tempos, _ = notes_from_musicxml(path)
    assert tempos[1] == pytest.approx((8/3, 60))
    assert events[1].start == pytest.approx(8/3)
    assert events[1].end == pytest.approx(20/3)


@pytest.mark.parametrize('pickup', [.25, 1, 2])
def test_pickup_keeps_the_next_bar_at_its_written_boundary(tmp_path, pickup):
    part = stream.Part()
    first = stream.Measure(number=0)
    first.append(meter.TimeSignature('4/4'))
    first.append(note.Note('G4', quarterLength=pickup))
    first.padAsAnacrusis()
    part.append(first)
    following = stream.Measure(number=1)
    following.repeatAppend(note.Note('C4'), 4)
    part.append(following)
    path = _write(tmp_path, part)
    text = musicxml_to_keyboard_text(path, 'pickup', style=STYLE_SIMPLE)
    cells = read_keyboard_cells(text)
    assert '4/4' in text.splitlines()[1]
    assert max(c.q + c.slot for c in cells) == pytest.approx(pickup + 4)
    assert parse_keyboard_text(text) == [(0, [67])] + [(pickup + i, [60]) for i in range(4)]


def test_last_sustained_note_is_not_cut_at_the_bar_of_its_attack():
    text = keyboard_text([NoteEvent(60, 0, 6)], 'long', style=STYLE_SIMPLE)
    cells = read_keyboard_cells(text)
    assert max(c.q + c.slot for c in cells) == 12
    assert sum(c.slot for c in cells if c.keys or c.hold) == 12


def test_repeated_untied_notes_remain_separate_attacks(tmp_path):
    part = stream.Part()
    part.append(meter.TimeSignature('4/4'))
    part.repeatAppend(note.Note('C4'), 4)
    events, _, _ = notes_from_musicxml(_write(tmp_path, part))
    assert [(n.start, n.end) for n in events] == [(0, .5), (.5, 1), (1, 1.5), (1.5, 2)]


@pytest.mark.parametrize('repeat', [False, True])
def test_two_staves_share_measure_boundaries_even_when_omr_lengths_disagree(tmp_path, repeat):
    from app.score_io import load_musicxml_playback

    measures = []
    for number, upper_length in enumerate((5, 4, 4), 1):
        attributes = ('<attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type>'
                      '</time><staves>2</staves><clef number="1"><sign>G</sign><line>2</line></clef>'
                      '<clef number="2"><sign>F</sign><line>4</line></clef></attributes>') if number == 1 else ''
        start = '<barline location="left"><repeat direction="forward"/></barline>' if repeat and number == 2 else ''
        end = '<barline location="right"><repeat direction="backward"/></barline>' if repeat and number == 3 else ''
        measures.append(f'<measure number="{number}">{attributes}{start}'
                        f'<note><pitch><step>C</step><octave>5</octave></pitch><duration>{upper_length}</duration><voice>1</voice><staff>1</staff></note>'
                        f'<backup><duration>{upper_length}</duration></backup>'
                        '<note><pitch><step>C</step><octave>3</octave></pitch><duration>4</duration><voice>2</voice><staff>2</staff></note>'
                        f'{end}</measure>')
    path = tmp_path / 'staves.musicxml'
    path.write_text('<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>'
                    '<part id="P1">' + ''.join(measures) + '</part></score-partwise>', encoding='utf-8')
    original = path.read_bytes()
    score = load_musicxml_playback(path)
    offsets = [[float(m.offset) for m in p.getElementsByClass(stream.Measure)] for p in score.parts]
    assert offsets == [[0, 5, 9, 13, 17] if repeat else [0, 5, 9]] * 2
    assert path.read_bytes() == original


@pytest.mark.parametrize('referent,expected_bpm',[(1,67),(2,134)])
def test_a_later_tempo_in_another_part_does_not_inject_120_at_the_opening(tmp_path,referent,expected_bpm):
    score=stream.Score()
    for part_index in range(2):
        part=stream.Part()
        for bar_index in range(2):
            bar=stream.Measure(number=bar_index+1)
            if bar_index==0:
                bar.append(meter.TimeSignature('4/4'))
            if part_index==0 and bar_index==0:
                bar.append(tempo.MetronomeMark(number=67,referent=duration.Duration(referent)))
            if part_index==1 and bar_index==1:
                bar.append(tempo.MetronomeMark(number=120))
            bar.append(note.Note('C5' if part_index==0 else 'C3',quarterLength=4))
            part.append(bar)
        score.insert(0,part)
    path=tmp_path/'cross-part-tempo.musicxml'
    score.write('musicxml',fp=str(path))
    original=path.read_bytes()
    events,tempos,_=notes_from_musicxml(path)
    assert tempos[0][1] == expected_bpm
    assert tempos[1] == pytest.approx((4*60/expected_bpm,120))
    assert max(n.end for n in events) == pytest.approx(4*60/expected_bpm+2)
    dest=tmp_path/'cross-part-tempo.mid'
    musicxml_to_midi(path,dest)
    midi=pretty_midi.PrettyMIDI(str(dest))
    assert midi.get_tempo_changes()[1][0] == pytest.approx(expected_bpm,abs=1e-4)
    assert midi.get_end_time() == pytest.approx(4*60/expected_bpm+2,abs=1e-5)
    assert path.read_bytes() == original


def test_cross_part_tempo_inheritance_is_restored_after_a_repeat(tmp_path):
    from music21 import bar as barline
    from app.keyboard_score import seconds_to_quarters

    score=stream.Score()
    for part_index in range(2):
        part=stream.Part()
        for index in range(3):
            measure=stream.Measure(number=index+1)
            if index==0:
                measure.append(meter.TimeSignature('4/4'))
            if part_index==0 and index==0:
                measure.insert(0,tempo.MetronomeMark(number=67))
                measure.insert(2,tempo.MetronomeMark(number=100))
            if part_index==1 and index==2:
                measure.insert(0,tempo.MetronomeMark(number=120))
            if index==1:measure.leftBarline=barline.Repeat(direction='start')
            if index==2:measure.rightBarline=barline.Repeat(direction='end')
            measure.insert(0,note.Note('C5' if part_index==0 else 'C3',quarterLength=4))
            part.append(measure)
        score.insert(0,part)
    path=tmp_path/'cross-part-repeat.musicxml'
    score.write('musicxml',fp=str(path))
    events,tempos,_=notes_from_musicxml(path)
    assert [(round(seconds_to_quarters(t,tempos)),bpm) for t,bpm in tempos] == [(0,67),(2,100),(8,120),(12,100),(16,120)]
    assert max(n.end for n in events) == pytest.approx(2*60/67+10)
