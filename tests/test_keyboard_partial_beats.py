import pytest

from app.keyboard_score import (
    MeterMark, NoteEvent, STYLE_DETAILED, STYLE_SIMPLE, _grid_beats,
    keyboard_text, parse_keyboard_text, read_keyboard_cells,
)
from app.convert import _grid_divisors


@pytest.mark.parametrize('length', [2.75, 3.75])
@pytest.mark.parametrize('style', [STYLE_SIMPLE, STYLE_DETAILED])
def test_incomplete_cut_time_beat_does_not_delay_the_next_meter(length, style):
    # The two real regressions were a 2/2 bar cut after 2.75q / 3.75q.
    # Rounding fixed cell counts used to add .25q at each meter boundary.
    qs = [0, .5, 1, 1.5, 2, 2.5, length, length+.25, length+1]
    notes = [NoteEvent(72, q, q+.2, part=0, exact_timing=True) for q in qs]
    notes += [NoteEvent(48, q, q+.2, part=1, exact_timing=True) for q in qs]
    text = keyboard_text(notes, 'short', [(0, 60)],
                         [MeterMark(0, 2, 2), MeterMark(length, 2, 4)], style=style)
    assert [q for q, _ in parse_keyboard_text(text)] == qs
    assert '拍〉' in text
    cells = read_keyboard_cells(text)
    for row in {c.row for c in cells}:
        own = [c for c in cells if c.row == row]
        assert any(c.q == pytest.approx(length) for c in own)
        assert all(c.q + c.slot <= length + 1e-8 for c in own if c.q < length - 1e-8)


def test_two_partial_beats_do_not_accumulate_drift():
    qs = [0, 2, 2.5, 2.75, 3.75, 4.75, 5.25, 5.5, 6.5]
    notes = [NoteEvent(60, q, q+.2, exact_timing=True) for q in qs]
    meters = [MeterMark(0, 2, 2), MeterMark(2.75, 2, 4),
              MeterMark(4.75, 2, 2), MeterMark(5.5, 2, 4)]
    text = keyboard_text(notes, 'twice', [(0, 60)], meters, style=STYLE_SIMPLE)
    assert [q for q, _ in parse_keyboard_text(text)] == qs


def test_partial_tail_geometry_ends_at_the_actual_boundary():
    beats = _grid_beats([(750, 753.75, MeterMark(750, 2, 2))], 4)[0]
    assert [(b.start, b.length) for b in beats] == [(750, 2), (752, 1.75)]
    assert sum(b.length for b in beats) == 3.75
    assert beats[-1].length_ratio == .875


def test_partial_beat_grid_is_included_when_exporting_musicxml():
    text = '# 1=C  2/2  ♩=90  每拍 4 格\n【键盘谱】\n1 | AAAA 〈7/8拍〉〈7〉AAAAAAA |\n'
    # Last cell is 7/8 of a half-note, divided in 7 = .25q.
    assert 4 in _grid_divisors(text)
    cells = read_keyboard_cells(text)
    assert cells[-1].q + cells[-1].slot == pytest.approx(3.75)


def test_short_tuplet_beat_and_inline_tempo_share_the_same_clock():
    text = ('# 1=C  2/2  ♩=90  每拍 4 格\n【键盘谱】\n'
            '1 旋律 | AAAA 〈7/8拍〉〈7〉AA〔♩=60〕AAAAA | 〔2/4〕AAAA AAAA |\n'
            '  伴奏 | ZZZZ 〈7/8拍〉〈7〉ZZ          ZZZZZ | ZZZZ ZZZZ |\n')
    cells = read_keyboard_cells(text)
    high = [c.q for c in cells if c.row == '旋律']
    low = [c.q for c in cells if c.row == '伴奏']
    assert high == low
    assert high[11] == 3.75


@pytest.mark.parametrize('boundary', [3.9375, 3.984375])
def test_exact_notation_does_not_snap_a_meter_change_to_the_next_beat(boundary):
    notes = [NoteEvent(60, q, q+.01, exact_timing=True)
             for q in [0, 2, 3, boundary, boundary+.5, boundary+1]]
    text = keyboard_text(notes, 'fine boundary', [(0, 60)],
                         [MeterMark(0, 2, 2), MeterMark(boundary, 2, 4)], style=STYLE_SIMPLE)
    actual = [q for q, _ in parse_keyboard_text(text)]
    # The local grid has a finite 32-cell limit; its internal attacks may
    # approximate an extreme fractional beat, but the next bar must not drift.
    assert actual == pytest.approx([0, 2, 3, boundary, boundary+.5, boundary+1], abs=.01)
    assert actual[3:] == [boundary, boundary+.5, boundary+1]


def test_pdf_midi_marker_preserves_a_written_meter_boundary_before_layout(tmp_path):
    from app.pdf_vector import ScoreNote, VectorScore, write_midi
    from app.keyboard_score import notes_from_midi

    score = VectorScore([ScoreNote(60, 0, 3.9375, 0), ScoreNote(62, 3.9375, 4.9375, 0)],
                        [(0, (2, 2)), (3.9375, (2, 4))], 60, 2, [])
    notes, tempos, meters = notes_from_midi(write_midi(score, tmp_path/'written.mid'))
    assert [m.quarters for m in meters] == pytest.approx([0, 3.9375], abs=1/220)
    assert all(m.exact_timing for m in meters)
    text = keyboard_text(notes, 'fine midi', tempos, meters, style=STYLE_SIMPLE)
    assert [q for q, _ in parse_keyboard_text(text)] == pytest.approx([0, 3.9375], abs=1/220)


def test_musicxml_rounding_keeps_the_phase_after_a_nonbinary_partial_beat():
    from music21 import note, stream
    text = ('# 1=C  2/2  ♩=90  每拍 4 格\n【键盘谱】\n'
            '1 | A--- 〈2/3拍〉〈3〉A00 | 〔2/4〕AAAA 0000 |\n')
    qs = [q for q, _ in parse_keyboard_text(text)]
    score = stream.Stream()
    for q in qs:
        score.insert(q, note.Note('C4', quarterLength=.25))
    rounded = score.quantize(quarterLengthDivisors=_grid_divisors(text), processDurations=False)
    assert [float(n.offset) for n in rounded.notes] == pytest.approx(qs, abs=1e-5)
