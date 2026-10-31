import pytest
from music21 import meter, note, stream, tempo

from app.keyboard_score import (
    MeterMark, NoteEvent, STYLE_DETAILED, STYLE_SIMPLE, keyboard_text,
    musicxml_to_keyboard_text, parse_keyboard_text, quarters_to_seconds,
    read_keyboard_cells,
)


def _section(text):
    return text.split('【键盘谱】', 1)[1]


def test_midbar_musicxml_tempos_are_shown_at_their_notes_not_the_next_bar(tmp_path):
    part = stream.Part()
    part.append(meter.TimeSignature('4/4'))
    part.insert(0, tempo.MetronomeMark(number=120))
    part.insert(2, tempo.MetronomeMark(number=60))
    part.insert(3, tempo.MetronomeMark(number=90))
    for q in range(8):
        part.insert(q, note.Note('C4'))
    path = tmp_path / 'midbar.musicxml'
    stream.Score([part]).write('musicxml', fp=str(path))
    original = path.read_bytes()
    text = musicxml_to_keyboard_text(path, 'midbar', style=STYLE_SIMPLE)
    bars = _section(text).split('|')
    assert 'A A 〔♩=60〕A 〔♩=90〕A' in bars[1]
    assert '♩=' not in bars[2]
    assert [q for q, _keys in parse_keyboard_text(text)] == list(range(8))
    assert path.read_bytes() == original


@pytest.mark.parametrize('style', [STYLE_SIMPLE, STYLE_DETAILED])
def test_a_tempo_change_between_attacks_keeps_its_halfbeat_cell(style):
    # No attack at q=.5: the speed still changes midway through the first
    # held note. Both rows must read back on the same quarter-note clock.
    tempos = [(0, 120), (.25, 60)]  # seconds -> q=0 and q=.5
    notes = [NoteEvent(72, 0, 1.75, part=0, exact_timing=True),
             NoteEvent(48, 0, 1.75, part=1, exact_timing=True),
             NoteEvent(74, 1.75, 2.75, part=0, exact_timing=True)]
    text = keyboard_text(notes, 'between', tempos, [MeterMark(0, 4, 4)], style=style)
    assert '〈2〉' in _section(text)
    assert '〔♩=60〕-' in _section(text)
    cells = read_keyboard_cells(text)
    assert sorted({c.q for c in cells if c.keys}) == [0, 2]
    for row in {c.row for c in cells}:
        assert [c.q for c in cells if c.row == row][:3] == [0, .5, 1]


def test_fractional_speed_changes_and_return_within_a_bar_are_visible():
    tempos = [(0, 50.25), (60 / 50.25, 50), (60 / 50.25 + 1.2, 50.25)]
    notes = [NoteEvent(60, quarters_to_seconds(q, tempos), quarters_to_seconds(q+1, tempos),
                       exact_timing=True) for q in range(8)]
    text = keyboard_text(notes, 'fractional', tempos, [MeterMark(0, 4, 4)], style=STYLE_SIMPLE)
    assert '♩=50.25' in text.splitlines()[1]
    assert 'A 〔♩=50〕A 〔♩=50.25〕A A' in _section(text)
    assert '♩=' not in _section(text).split('|')[2]
    assert [q for q, _keys in parse_keyboard_text(text)] == list(range(8))


def test_barline_tempo_is_not_duplicated_as_an_inline_mark():
    tempos = [(0, 120), (2, 60)]
    notes = [NoteEvent(60, quarters_to_seconds(q, tempos), quarters_to_seconds(q+1, tempos),
                       exact_timing=True) for q in range(8)]
    text = keyboard_text(notes, 'barline', tempos, [MeterMark(0, 4, 4)], style=STYLE_SIMPLE)
    assert _section(text).count('〔♩=60〕') == 1
    assert [q for q, _keys in parse_keyboard_text(text)] == list(range(8))
