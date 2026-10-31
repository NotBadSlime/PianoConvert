"""Numeric tempo belongs to its decoded note/rest column, including inside repeats."""
import pytest

from app import pdf_vector as pv
from app.keyboard_score import notes_from_midi, seconds_to_quarters
from tests.test_pdf_vector import _grand_staff_page, _quarter_up


STAFF = pv.Staff(40, 300, [700, 695, 690, 685, 680], 0)


def _bar(*, tempo=0, marks=(), start=False, end=False, xs=(80, 105, 130, 155)):
    events = []
    for i, x in enumerate(xs):
        head = pv.Head(x, 690, 1, False, STAFF, pitch=60+i)
        events.append(pv.Event(x, STAFF, 1, [head], base=1))
    return pv.Bar(events, tempo=tempo, x0=40, x1=180, repeat_start=start,
                  repeat_end=end, tempo_positions=list(marks))


def _roundtrip(score, path):
    midi = pv.write_midi(score, path)
    notes, tempos, _ = notes_from_midi(midi)
    return notes, [(round(seconds_to_quarters(t, tempos), 6), round(bpm, 6)) for t, bpm in tempos]


def test_two_numeric_marks_in_one_bar_preserve_both_positions_and_midi_duration(tmp_path):
    bar = _bar()
    pv._mark_tempo_bars([(80, 720, 120), (130, 720, 60)], [[STAFF]], [bar])
    score = pv.build_score([bar], 0)
    assert score.bpm == 120
    assert score.tempo_printed
    assert score.tempos == [(2, 60)]
    notes, marks = _roundtrip(score, tmp_path/'two-marks.mid')
    assert marks == [(0, 120), (2, 60)]
    assert [n.start for n in notes] == pytest.approx([0, .5, 1, 2])
    assert max(n.end for n in notes) == pytest.approx(3, abs=1e-6)


def test_only_a_midbar_mark_does_not_backdate_the_opening_or_claim_it_was_printed(tmp_path):
    score = pv.build_score([_bar(marks=[(130, 60)])], 0)
    assert score.bpm == pv.DEFAULT_BPM
    assert not score.tempo_printed
    assert score.tempos == [(2, 60)]
    notes, marks = _roundtrip(score, tmp_path/'midbar-only.mid')
    assert [q for q, _ in marks] == [0, 2]
    assert [bpm for _, bpm in marks] == pytest.approx([pv.DEFAULT_BPM, 60], abs=.0001)
    assert max(n.end for n in notes) == pytest.approx(2*60/pv.DEFAULT_BPM+2, abs=1e-5)


def test_mark_alignment_uses_decoded_rhythm_not_fraction_of_printed_bar_width():
    # Lyrics and accidentals can greatly widen a single beat.
    score = pv.build_score([_bar(tempo=120, marks=[(169.6, 60)], xs=(60, 70, 170, 174))], 0)
    assert score.tempos == [(2, 60)]


def test_rest_column_can_anchor_a_speed_change():
    bar = _bar(tempo=120, marks=[(130, 60)])
    bar.events[2].heads = []
    score = pv.build_score([bar], 0)
    assert score.tempos == [(2, 60)]


def test_centred_whole_bar_rest_cannot_pull_a_midbar_change_back_to_zero():
    bar = _bar(tempo=120, marks=[(130, 60)])
    bottom = pv.Staff(40, 300, [650, 645, 640, 635, 630], 1)
    bar.events.append(pv.Event(130, bottom, 4, [], full_bar_rest=True, base=4))
    score = pv.build_score([bar], 0)
    assert score.tempos == [(2, 60)]


def test_repeat_reenters_before_a_midbar_change_and_replays_each_local_mark(tmp_path):
    bars = [_bar(tempo=120), _bar(start=True, marks=[(130, 60)]), _bar(end=True)]
    score = pv.build_score(bars, 0)
    assert score.tempos == [(6, 60), (12, 120), (14, 60)]
    notes, marks = _roundtrip(score, tmp_path/'repeat-local.mid')
    assert marks == [(0, 120), (6, 60), (12, 120), (14, 60)]
    assert max(n.end for n in notes) == pytest.approx(16, abs=1e-5)


def test_skipping_first_ending_does_not_import_its_midbar_speed_change():
    bars = [_bar(tempo=120, start=True), _bar(), _bar(marks=[(130, 60)], end=True), _bar()]
    bars[2].volta = 1
    bars[3].volta = 2
    score = pv.build_score(bars, 0)
    assert score.tempos == [(10, 60), (12, 120)]


def test_repeating_an_opening_midbar_change_restores_the_default_entry_speed():
    score = pv.build_score([_bar(marks=[(130, 60)], start=True, end=True)], 0)
    assert score.bpm == pv.DEFAULT_BPM
    assert not score.tempo_printed
    assert score.tempos == [(2, 60), (4, pv.DEFAULT_BPM), (6, 60)]


def test_midbar_tempos_restore_together_with_meter_on_a_repeat(tmp_path):
    bars = [_bar(tempo=120), _bar(start=True, marks=[(105, 90), (130, 60)], xs=(80, 105, 130)),
            _bar(end=True, xs=(80, 105, 130))]
    bars[1].meter = (3, 4)
    bars[2].meter = (6, 8)
    score = pv.build_score(bars, 0)
    assert score.meters == [(0, (4, 4)), (4, (3, 4)), (7, (6, 8)), (10, (3, 4)), (13, (6, 8))]
    assert score.tempos == [(5, 90), (6, 60), (10, 120), (11, 90), (12, 60)]
    notes, marks = _roundtrip(score, tmp_path/'repeat-meters.mid')
    assert [q for q, _ in marks] == [0, 5, 6, 10, 11, 12]
    assert max(n.end for n in notes) == pytest.approx(12 + 1/3, abs=1e-5)


def test_read_pdf_keeps_a_later_note_aligned_speed_in_the_same_measure(monkeypatch, tmp_path):
    glyphs, lines = [], []
    for x in (80, 105, 130, 155):
        head, stem = _quarter_up(x, 692.5)
        glyphs.append(head)
        lines.append(stem)
    drawing = _grand_staff_page(glyphs, lines)

    class Document:
        def __len__(self): return 1
        def __getitem__(self, index): return index
        def close(self): pass

    monkeypatch.setattr('pypdfium2.PdfDocument', lambda _: Document())
    monkeypatch.setattr(pv, 'read_drawing', lambda _: drawing)
    monkeypatch.setattr(pv, '_tempo_marks', lambda _: [(80, 720, 120), (130, 720, 60)])
    score = pv.read_vector_pdf(tmp_path/'local.pdf')
    assert score.bpm == 120
    assert score.tempos == [(2, 60)]


def test_mark_printed_left_of_the_first_column_still_sets_the_opening_speed():
    score = pv.build_score([_bar(marks=[(60, 67)])], 0)
    assert score.bpm == 67
    assert score.tempo_printed
    assert score.tempos == []
