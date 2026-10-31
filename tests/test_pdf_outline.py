"""Pages drawn as outlines (MuseScore.com sheet pages): symbols recognised from their shapes."""
import numpy as np

from app import pdf_outline as po
from app import pdf_vector as pv
from app.pdf_vector import Glyph, Line, PageDrawing

SP = 25.0  # staff space of the outline page


def _char(code, l, b, r, t, font="Leland", text=None):
    return (code, l, b, l, b, r, t, 4 * SP, font, text if text is not None else chr(code))


def _rect(x0, y0, x1, y1):
    c = np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], dtype=float)
    return ([c], (x0, y0, x1, y1))


# ---------------------------------------------------------------- dots


def test_a_dot_right_of_a_notehead_is_an_augmentation_dot():
    head = _char(0xE0A4, 100, 100, 130, 125)
    dot = _char(0xE044, 140, 110, 150, 120)  # same shape as a repeat dot
    out = po._dots([head, dot], SP)
    assert out[1][0] == 0xE1E7


def test_a_dot_far_from_any_note_stays_a_repeat_dot():
    head = _char(0xE0A4, 100, 100, 130, 125)
    dot = _char(0xE044, 300, 110, 310, 120)
    out = po._dots([head, dot], SP)
    assert out[1][0] == 0xE044


def test_the_second_dot_of_a_double_dotted_note_follows_the_first():
    head = _char(0xE0A4, 100, 100, 130, 125)
    d1 = _char(0xE044, 140, 110, 150, 120)
    d2 = _char(0xE044, 160, 110, 170, 120)
    out = po._dots([head, d1, d2], SP)
    assert [c[0] for c in out[1:]] == [0xE1E7, 0xE1E7]


def test_the_two_dots_of_a_repeat_sign_next_to_a_rest_stay_repeat_dots():
    # an eighth rest just before the closing repeat sign: its two dots, one space apart, are
    # the repeat sign's, not the rest's augmentation dot
    rest = _char(0xE4E6, 100, 100, 125, 140)
    d1 = _char(0xE044, 140, 110, 150, 120)
    d2 = _char(0xE044, 140, 135, 150, 145)
    out = po._dots([rest, d1, d2], SP)
    assert [c[0] for c in out[1:]] == [0xE044, 0xE044]


# ---------------------------------------------------------------- clefs with an 8


def test_a_bass_clef_with_an_8_below_sounds_an_octave_lower():
    clef = _char(0xE062, 100, 200, 133, 255)
    eight = _char(ord("8"), 110, 190, 122, 205, font="Edwin", text="8")
    out = po._octave_clefs([clef, eight], SP)
    assert [c[0] for c in out] == [0xE064]


def test_a_bass_clef_with_its_8_hanging_two_spaces_lower_sounds_an_octave_lower():
    # MuseScore 3 hangs the 8 of an 8vb bass clef about two spaces under the clef's bottom
    clef = _char(0xE062, 100, 200, 133, 255)
    eight = _char(ord("8"), 109, 150, 121, 165, font="Edwin", text="8")
    out = po._octave_clefs([clef, eight], SP)
    assert [c[0] for c in out] == [0xE064]


def test_a_clef_without_an_8_is_left_alone():
    clef = _char(0xE050, 100, 200, 133, 300)
    far_eight = _char(ord("8"), 400, 190, 412, 205, font="Edwin", text="8")
    out = po._octave_clefs([clef, far_eight], SP)
    assert [c[0] for c in out] == [0xE050, ord("8")]


# ---------------------------------------------------------------- short beams


def test_a_short_beam_between_two_stems_is_not_a_rest_or_tremolo():
    beam = _char(0xE223, 200, 300, 242, 312)
    paths = [
        ([(2, 200.5, 300.0), (0, 200.5, 360.0)], True, True, 1.0, 1.0),
        ([(2, 241.5, 300.0), (0, 241.5, 360.0)], True, True, 1.0, 1.0),
    ]
    shapes = [_rect(200, 300, 242, 312)]
    used = {0}
    out = po._beams_not_rests([beam], shapes, used, SP, paths)
    assert out == [] and used == set()  # left for the beam reading


def test_a_rest_away_from_stems_stays_a_rest():
    rest = _char(0xE4E3, 200, 300, 242, 312)
    paths = [([(2, 400.0, 300.0), (0, 400.0, 360.0)], True, True, 1.0, 1.0)]
    out = po._beams_not_rests([rest], [_rect(200, 300, 242, 312)], {0}, SP, paths)
    assert [c[0] for c in out] == [0xE4E3]


def test_a_beam_stub_at_one_stems_end_is_a_beam_not_a_rest():
    # the short second beam of a 16th next to a dotted 8th: one end on a stem, at its tip
    stub = _char(0xE4E3, 200, 302, 233, 314)
    paths = [([(2, 200.5, 300.0), (0, 200.5, 380.0)], True, True, 1.0, 1.0)]
    out = po._beams_not_rests([stub], [_rect(200, 302, 233, 314)], {0}, SP, paths)
    assert out == []


def test_a_tremolo_across_the_middle_of_a_stem_stays_a_tremolo():
    trem = _char(0xE222, 190, 330, 212, 350)
    paths = [([(2, 200.5, 280.0), (0, 200.5, 400.0)], True, True, 1.0, 1.0)]
    out = po._beams_not_rests([trem], [_rect(190, 330, 212, 350)], {0}, SP, paths)
    assert [c[0] for c in out] == [0xE222]


# ---------------------------------------------------------------- metronome marks


def test_the_equals_sign_of_a_metronome_mark_is_found_between_its_two_bars():
    note = _char(0xECA5, 100, 500, 116, 540)
    shapes = [_rect(125, 510, 158, 516), _rect(125, 524, 158, 530)]
    used: set[int] = set()
    out = po._equals_signs([note], shapes, used, SP)
    assert [c[9] for c in out] == ["="] and used == {0, 1}


def test_a_small_round_shape_after_the_metronome_note_is_its_dot():
    note = _char(0xECA5, 100, 500, 116, 540)
    shapes = [_rect(120, 502, 125, 507), _rect(130, 510, 163, 516), _rect(130, 524, 163, 530)]
    used: set[int] = set()
    out = po._equals_signs([note], shapes, used, SP)
    assert sorted(c[0] for c in out) == sorted([0xE1E7, ord("=")])


def _drawing(glyphs, lines=()):
    return PageDrawing(list(glyphs), list(lines), [], 612.0, 792.0, True)


def _text(ch, x, y):
    return Glyph(ord(ch), x, y, x, y - 1, x + 3, y + 4, 8.0, False, ch)


def test_a_dotted_quarter_metronome_mark_is_counted_in_quarters():
    note = Glyph(0x143, 76.8, 690.0, 76.7, 688.7, 79.9, 696.7, 7.6, True)
    texts = [_text("=", 85.8, 689.7), _text("2", 96.4, 688.7), _text("0", 102.5, 688.5), _text("2", 108.4, 688.7)]
    dot = Line(81.2, 689.4, 81.2, 689.4, 4.0)  # an outline page's dot as a point
    marks = pv._tempo_marks(_drawing([note] + texts, [dot]))
    assert marks and round(marks[0][2]) == 303


def test_a_plain_quarter_metronome_mark_keeps_its_number():
    note = Glyph(0x143, 76.8, 690.0, 76.7, 688.7, 79.9, 696.7, 7.6, True)
    texts = [_text("=", 85.8, 689.7), _text("9", 96.4, 688.7), _text("6", 102.5, 688.5)]
    assert [m[2] for m in pv._tempo_marks(_drawing([note] + texts))] == [96.0]


def test_a_digit_shaped_like_a_time_signature_digit_still_reads_in_a_metronome_mark():
    note = Glyph(0x143, 76.8, 690.0, 76.7, 688.7, 79.9, 696.7, 7.6, True)
    one = _text("1", 96.4, 688.7)
    one2 = _text("1", 100.4, 688.7)
    zero = Glyph(0x130, 105.0, 688.7, 105.0, 687.0, 108.0, 692.0, 8.0, True)
    marks = pv._tempo_marks(_drawing([note, _text("=", 85.8, 689.7), one, one2, zero]))
    assert [m[2] for m in marks] == [110.0]


# ---------------------------------------------------------------- page font


def _staff_paths(bottom: float) -> list:
    return [([(2, 100.0, bottom + k * SP), (0, 2000.0, bottom + k * SP)], True, True, 2.0, 1.0) for k in range(5)]


def _dashed(x0: float, x1: float, y: float):
    return ([(2, x0, y), (0, x1, y)], False, True, 1.0, 1.0)


def test_an_8_with_a_dashed_line_over_a_staff_is_an_8va():
    # staff lines 1000 - 1100; the "8" 40 over the top line, a dashed line from it
    eight = _char(ord("8"), 300, 1140, 315, 1165, font="Edwin", text="8")
    paths = _staff_paths(1000.0) + [_dashed(330, 1500, 1152)]
    assert [c[0] for c in po._ottava_marks([eight], paths, SP)] == [0xE510]


def test_an_8_with_a_dashed_line_under_a_staff_is_an_8vb():
    eight = _char(ord("8"), 300, 940, 315, 965, font="Edwin", text="8")
    paths = _staff_paths(1000.0) + [_dashed(330, 1500, 952)]
    assert [c[0] for c in po._ottava_marks([eight], paths, SP)] == [0xE51C]


def test_an_8_between_two_staves_is_the_8va_of_the_staff_below_unless_it_hugs_the_one_above():
    # lower staff 1000-1100, upper staff 1400-1500; the 8 at 1150 (50 over the lower, 250 under the upper)
    eight = _char(ord("8"), 300, 1140, 315, 1165, font="Edwin", text="8")
    paths = _staff_paths(1000.0) + _staff_paths(1400.0) + [_dashed(330, 1500, 1152)]
    assert [c[0] for c in po._ottava_marks([eight], paths, SP)] == [0xE510]
    # the 8 just under the upper staff: its 8vb
    eight = _char(ord("8"), 300, 1340, 315, 1365, font="Edwin", text="8")
    paths = _staff_paths(1000.0) + _staff_paths(1400.0) + [_dashed(330, 1500, 1352)]
    assert [c[0] for c in po._ottava_marks([eight], paths, SP)] == [0xE51C]


def test_a_digit_without_a_dashed_line_is_no_ottava():
    eight = _char(ord("8"), 300, 1140, 315, 1165, font="Edwin", text="8")
    assert [c[9] for c in po._ottava_marks([eight], _staff_paths(1000.0), SP)] == ["8"]


def test_the_page_font_is_the_one_whose_shapes_match_best():
    music, _text_t = po._templates()
    per_font = po._per_font(music)
    assert {"Leland", "MScore", "Gootville", "Bravura"} <= set(per_font)
    # shapes made from Gootville's own noteheads, quarter rests and clefs
    shapes, sigs = [], {}
    for t in per_font["Gootville"]:
        if t[1] in (0xE0A4, 0xE4E5, 0xE050, 0xE062, 0xE0A3):
            w, h = t[2] * SP, t[3] * SP
            for k in range(3):
                shapes.append(_rect(0, 0, w, h))
                sigs[len(shapes) - 1] = t[6]
    picked = po._page_font_templates(music, shapes, sigs, SP)
    assert {t[0] for t in picked} == {"Gootville"}
