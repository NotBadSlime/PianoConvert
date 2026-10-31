"""Timing inside a bar: how the reader lines up voices, tuplets and staves (MuseScore-style scores)."""
import pytest

from app import pdf_vector as pv
from app.pdf_vector import Bar, Event, Glyph, Head, Line, PageDrawing, Staff, Stem, Tuplet

TOP = Staff(40, 300, [700, 695, 690, 685, 680], 0)
BOTTOM = Staff(40, 300, [650, 645, 640, 635, 630], 1)


def _note(x: float, length: float, up: bool, staff: Staff = TOP, group: Tuplet | None = None) -> Event:
    head = Head(x, 690.0, 1.0, False, staff)
    sx = x + 6 if up else x
    head.stem = Stem(Line(sx, 690.0, sx, 710.0, 1.0), up)
    return Event(x, staff, length, [head], base=length, group=group)


def _rest(x: float, length: float, staff: Staff = TOP, full: bool = False) -> Event:
    return Event(x, staff, length, [], full_bar_rest=full, base=length)


# ---------------------------------------------------------------- columns


def test_a_note_moved_aside_by_another_voice_starts_with_its_column():
    lower = [_note(100, 1.0, False), _note(160, 1.0, False), _note(200, 2.0, False)]
    upper = [_note(106.5, 0.5, True), _note(130, 0.5, True), _note(160, 1.0, True), _note(200, 2.0, True)]
    events = lower + upper
    assert pv._layout_staff(events) > 4.5  # read plainly, the moved note comes a beat late
    assert pv._layout_staff_robust(events, 4.0) == pytest.approx(4.0)
    assert (upper[0].t, upper[1].t, upper[2].t) == pytest.approx((0.0, 0.5, 1.0))


def test_a_bar_that_adds_up_is_left_alone():
    events = [_note(100, 1.0, True), _note(106.5, 1.0, True), _note(160, 2.0, True)]
    # 6.5 apart but one voice: they follow each other, and the bar is full
    assert pv._layout_staff_robust(events, 4.0) == pytest.approx(4.0)
    assert [e.t for e in events] == pytest.approx([0.0, 1.0, 2.0])


def test_a_whole_bar_rest_next_to_notes_takes_no_part_in_their_timing():
    notes = [_note(100 + 30 * i, 1.0, True) for i in range(4)]
    centred = _rest(145, 4.0, full=True)  # the other voice rests the whole bar, rest centred
    bar = Bar(notes + [centred], meter=(4, 4))
    assert pv._layout_bar(bar) == pytest.approx(4.0)
    assert [e.t for e in notes] == pytest.approx([0.0, 1.0, 2.0, 3.0])
    assert bar.totals == [(0, pytest.approx(4.0))]


# ---------------------------------------------------------------- the other staff


def test_columns_of_a_staff_that_adds_up_time_a_voice_with_hidden_rests():
    # the bass plays eighths; the treble's second voice has notes between the first voice's
    # (its rests are not printed), so only the bass tells when they sound
    bass = [_note(100 + 30 * i, 0.5, False, BOTTOM) for i in range(8)]
    voice1 = [_note(100, 2.0, False), _note(220, 1.0, False), _note(280, 1.0, False)]
    voice2 = [_note(160, 0.5, True), _note(250, 0.5, True)]
    bar = Bar(bass + voice1 + voice2, meter=(4, 4))
    assert pv._layout_bar(bar) == pytest.approx(4.0)
    assert (voice2[0].t, voice2[1].t) == pytest.approx((1.0, 2.5))
    assert [e.t for e in voice1] == pytest.approx([0.0, 2.0, 3.0])
    assert all(abs(total - 4.0) < 1e-6 for _staff, total in bar.totals)


def test_a_note_moved_aside_is_timed_by_the_other_staff_when_its_own_neighbours_do_not_say():
    bass = [_note(100 + 25 * i, 0.5, False, BOTTOM) for i in range(8)]  # eighths 100 .. 275
    voice1 = [_note(100, 1.0, False), _note(150, 1.0, False), _note(200, 1.0, False), _note(250, 1.0, False)]
    moved = _note(205.0, 0.5, True)  # sounds with the bass note at 200 (t = 2.0), moved 5 to the right
    hidden = _note(250, 0.5, True)   # and one at 250 (t = 3.0)
    bar = Bar(bass + voice1 + [moved, hidden], meter=(4, 4))
    pv._layout_bar(bar)
    assert (moved.t, hidden.t) == pytest.approx((2.0, 3.0))


# ---------------------------------------------------------------- tuplets


def test_a_tuplet_that_is_not_a_triplet_is_read_with_the_ratio_that_fills_the_bar():
    group = Tuplet(3, 2 / 3)
    # 16th, 32nd, 16th under a "3": the usual 2/3 leaves the beat short; 4/5 makes an eighth
    members = [_note(100, 0.25, True, group=group), _note(120, 0.125, True, group=group), _note(140, 0.25, True, group=group)]
    for e in members:
        e.length = e.base * group.ratio
    events = [_note(70, 0.5, True)] + members
    assert pv._layout_staff(events) < 1.0 - 1e-3
    assert pv._layout_staff_robust(events, 1.0) == pytest.approx(1.0)
    assert group.ratio == pytest.approx(0.8)
    assert members[0].length == pytest.approx(0.2)


def test_a_triplet_stays_a_triplet_when_it_fits():
    group = Tuplet(3, 2 / 3)
    members = [_note(100 + 20 * i, 0.5, True, group=group) for i in range(3)]
    for e in members:
        e.length = e.base * group.ratio
    assert pv._layout_staff_robust(members, 1.0) == pytest.approx(1.0)
    assert group.ratio == pytest.approx(2 / 3)


def _beamed(x0: float, n: int, length: float, gid: int) -> list[Event]:
    out = []
    for i in range(n):
        e = _note(x0 + 8 * i, length, True)
        e.beam_group = gid
        out.append(e)
    return out


def test_beam_groups_of_six_16ths_without_a_number_are_sextuplets_when_the_bar_needs_it():
    # 4/4: three groups of six 16ths (unmarked sextuplets) and two plain 16ths... = 4 beats
    events = _beamed(50, 6, 0.25, 1) + _beamed(110, 6, 0.25, 2) + _beamed(170, 6, 0.25, 3) + _beamed(230, 4, 0.25, 4)
    assert pv._layout_staff(events) == pytest.approx(5.5)
    assert pv._layout_staff_robust(events, 4.0) == pytest.approx(4.0)
    assert events[6].t == pytest.approx(1.0)  # the second group starts on beat 2
    assert events[18].length == pytest.approx(0.25)  # the group of four stays plain 16ths


def test_three_16ths_opening_a_beam_group_with_an_eighth_are_an_unmarked_triplet():
    # 2/4: [16 16 16 8] x2 with the first three of each group a triplet, then a quarter
    events = []
    for gid, x0 in ((1, 50), (2, 110)):
        grp = _beamed(x0, 3, 0.25, gid)
        last = _note(x0 + 30, 0.5, True)
        last.beam_group = gid
        events += grp + [last]
    assert pv._layout_staff_robust(events, 2.0) == pytest.approx(2.0)
    assert events[3].t == pytest.approx(0.5)  # the eighth after the triplet of 16ths
    assert events[4].t == pytest.approx(1.0)


def test_five_16ths_in_a_group_are_a_triplet_and_two_plain_ones_when_the_bar_needs_it():
    # 6 + 5 + 6 + 5 sixteenths in 4/4 (Toccata in D minor): sextuplet, triplet + 2, ...
    events = _beamed(50, 6, 0.25, 1) + _beamed(110, 5, 0.25, 2) + _beamed(170, 6, 0.25, 3) + _beamed(230, 5, 0.25, 4)
    assert pv._layout_staff_robust(events, 4.0) == pytest.approx(4.0)
    assert events[6].t == pytest.approx(1.0)
    assert events[9].t == pytest.approx(1.5)  # the plain 16ths after the triplet


def test_beam_groups_that_fill_the_bar_are_not_made_tuplets():
    events = _beamed(50, 6, 0.25, 1) + _beamed(110, 6, 0.25, 2) + _beamed(170, 4, 0.25, 3)
    assert pv._layout_staff_robust(events, 4.0) == pytest.approx(4.0)
    assert all(e.length == pytest.approx(0.25) for e in events)


# ---------------------------------------------------------------- reading a drawing


def _glyph(code: int, x: float, y: float, w: float = 6, h: float = 8) -> Glyph:
    return Glyph(code, x, y, x, y - h / 2, x + w, y + h / 2, 20.0, True)


def _head(x: float, y: float) -> Glyph:
    return Glyph(pv.HEAD_BLACK, x, y, x, y - 2.5, x + 6, y + 2.5, 20.0, True)


def _staff_lines(top: float, x0: float = 40, x1: float = 300) -> list[Line]:
    return [Line(x0, top - 5 * k, x1, top - 5 * k, 1.0) for k in range(5)]


def _page(extra_glyphs=(), extra_lines=(), tops=(700,)) -> PageDrawing:
    """Treble staves at the given tops (E4 on the bottom line), 4/4, one barline at 300."""
    lines: list[Line] = []
    glyphs: list[Glyph] = []
    for top in tops:
        lines += _staff_lines(top)
        lines.append(Line(40, top - 20, 40, top, 1.0))
        lines.append(Line(300, top - 20, 300, top, 1.5))
        glyphs += [_glyph(pv.CLEF_TREBLE, 45, top - 15, 12, 36), _glyph(0x134, 62, top - 5), _glyph(0x134, 62, top - 15)]
    return PageDrawing(glyphs + list(extra_glyphs), lines + list(extra_lines), [], 595, 842)


def _read_systems(drawing: PageDrawing) -> list[list[pv.Bar]]:
    state = pv.ReaderState()
    systems = pv.group_systems(drawing, pv.find_staves(drawing))
    out = []
    for i, system in enumerate(systems):
        above = systems[i - 1][-1].bottom if i > 0 else None
        below = systems[i + 1][0].top if i + 1 < len(systems) else None
        out.append(pv.analyze_system(drawing, system, state, above, below))
    return out


def test_a_note_under_another_voices_stem_keeps_its_own_stem():
    # A4 sixteenths beamed together; under the first one a D4 quarter whose stem stands at
    # the same x and reaches up to A4. The beams belong to A4 alone.
    glyphs, lines = [], []
    for i in range(4):
        x = 100 + 25 * i
        glyphs.append(_head(x, 687.5))
        lines.append(Line(x + 6, 687.5, x + 6, 705.0, 1.0))
    lines += [Line(106, 705.0, 181, 705.0, 5.0), Line(106, 701.0, 181, 701.0, 5.0)]
    glyphs.append(_head(100, 677.5))
    lines.append(Line(106, 677.5, 106, 690.0, 1.0))
    glyphs += [_glyph(0x163, 215, 690, 5, 15), _glyph(0x163, 245, 690, 5, 15), _glyph(0x163, 275, 690, 5, 15)]
    drawing = _page(glyphs, lines)
    bars = _read_systems(drawing)[0]
    assert len(bars) == 1
    score = pv.build_score(bars, 90.0)
    a4 = sorted(n.start_q for n in score.notes if n.pitch == 69)
    d4 = [(n.start_q, n.end_q) for n in score.notes if n.pitch == 62]
    assert a4 == pytest.approx([0.0, 0.25, 0.5, 0.75])
    assert d4 == [(0.0, pytest.approx(1.0))]


def test_a_note_between_two_systems_belongs_to_the_nearer_one():
    # a quarter low below the first staff (4 spaces under it), 6 spaces above the second one
    low_head = _head(100, 659.0)  # first staff's bottom line is 680: 21 below it
    drawing = _page([low_head], [Line(106, 659.0, 106, 676.0, 1.0)], tops=(700, 633))
    first, second = _read_systems(drawing)
    assert sum(len(b.events) for b in first) == 1
    assert sum(len(b.events) for b in second) == 0


# ---------------------------------------------------------------- key signature at a line start


def _flat(x: float, y: float) -> Glyph:
    return Glyph(pv.FLAT, x, y, x, y - 6, x + 4, y + 6, 20.0, True)


def _four_flats_then_notes() -> PageDrawing:
    """Treble line in four flats; the first note (D5) stands right behind the last flat (Db) at
    the same height, a second D5 later in the line. Staff: bottom line 680, 2.5 per step."""
    glyphs = [
        _glyph(pv.CLEF_TREBLE, 45, 685, 12, 36),
        _flat(62, 690.0),   # Bb
        _flat(67, 697.5),   # Eb
        _flat(72, 687.5),   # Ab
        _flat(77, 695.0),   # Db
        _head(84, 695.0),   # D5, level with the last flat and close behind it
        _head(140, 695.0),  # D5 again
    ]
    lines = _staff_lines(700) + [
        Line(40, 680, 40, 700, 1.0),
        Line(300, 680, 300, 700, 1.5),
        Line(90, 695.0, 90, 712.0, 1.0),
        Line(146, 695.0, 146, 712.0, 1.0),
    ]
    return PageDrawing(glyphs, lines, [], 595, 842)


def test_the_key_signature_of_the_line_before_is_not_taken_for_the_first_notes_accidental():
    state = pv.ReaderState()
    state.staff(0).key = {6: -1, 2: -1, 5: -1, 1: -1}  # the line above ended in four flats
    drawing = _four_flats_then_notes()
    bars = []
    for system in pv.group_systems(drawing, pv.find_staves(drawing)):
        bars.extend(pv.analyze_system(drawing, system, state))
    pitches = sorted(h.pitch for b in bars for e in b.events for h in e.heads)
    assert pitches == [73, 73]  # both Db5: the key signature flats D for the whole line
    assert state.staff(0).key == {6: -1, 2: -1, 5: -1, 1: -1}


def test_a_key_state_with_double_flats_does_not_stop_the_reading():
    # the key carried over from the line above holds a misread double flat; a key has none
    state = pv.ReaderState()
    state.staff(0).key = {6: -2, 2: -2}
    drawing = _four_flats_then_notes()
    bars = []
    for system in pv.group_systems(drawing, pv.find_staves(drawing)):
        bars.extend(pv.analyze_system(drawing, system, state))
    assert [h.pitch for b in bars for e in b.events for h in e.heads]

def test_a_notes_own_flat_at_the_start_of_a_line_in_c_major_is_not_a_key_signature():
    state = pv.ReaderState()  # no key before: C major
    glyphs = [_glyph(pv.CLEF_TREBLE, 45, 685, 12, 36), _flat(72, 690.0), _head(80, 690.0), _head(140, 690.0)]
    lines = _staff_lines(700) + [Line(40, 680, 40, 700, 1.0), Line(110, 680, 110, 700, 1.5), Line(300, 680, 300, 700, 1.5),
                                 Line(86, 690.0, 86, 707.0, 1.0), Line(146, 690.0, 146, 707.0, 1.0)]
    drawing = PageDrawing(glyphs, lines, [], 595, 842)
    bars = []
    for system in pv.group_systems(drawing, pv.find_staves(drawing)):
        bars.extend(pv.analyze_system(drawing, system, state))
    pitches = [h.pitch for b in bars for e in b.events for h in e.heads]
    # Bb4 with its own flat in the first bar; the flat does not reach the B of the next bar
    assert pitches == [70, 71]
    assert state.staff(0).key == {}


# ---------------------------------------------------------------- ties


def _tied_pair(smufl: bool, curve_dy: float) -> pv.VectorScore:
    glyphs = [_glyph(pv.CLEF_TREBLE, 45, 685, 12, 36), _head(100, 690.0), _head(140, 690.0)]
    lines = _staff_lines(700) + [Line(40, 680, 40, 700, 1.0), Line(300, 680, 300, 700, 1.5),
                                 Line(106, 690.0, 106, 707.0, 1.0), Line(146, 690.0, 146, 707.0, 1.0)]
    drawing = PageDrawing(glyphs, lines, [pv.Curve(108.0, 690.0 - curve_dy, 139.0, 690.0 - curve_dy)], 595, 842, smufl=smufl)
    state = pv.ReaderState()
    bars = []
    for system in pv.group_systems(drawing, pv.find_staves(drawing)):
        bars.extend(pv.analyze_system(drawing, system, state))
    return pv.build_score(bars, 90.0)


def test_a_tie_that_starts_further_from_the_head_centre_counts_in_a_musescore_page():
    # MuseScore starts a tie about a space (5) under the head's centre; other programs hug the head
    tied = _tied_pair(smufl=True, curve_dy=4.9)
    assert len(tied.notes) == 1
    assert tied.notes[0].end_q - tied.notes[0].start_q > 1.5
    assert len(_tied_pair(smufl=False, curve_dy=4.9).notes) == 2

# ---------------------------------------------------------------- beams, tuplets and ties in a MuseScore-style page


def _digit(text: str, x: float, y: float, size: float = 15.0) -> Glyph:
    return Glyph(ord(text), x, y, x - 3.5, y - 4.5, x + 3.5, y + 4.5, size, False, text)


def _down_stem(x: float, head_y: float, tip_y: float) -> Line:
    return Line(x, tip_y, x, head_y, 1.0)  # a stem down stands at the head's left edge


def _read_page(drawing: PageDrawing) -> list[pv.Bar]:
    return [bar for system in _read_systems(drawing) for bar in system]


def _beamed_group_page(extra_glyphs=(), tops=(700,)) -> PageDrawing:
    """Treble staff; stems down. An eighth at x=100 and three 16ths at x=130, 145, 160, all on B4
    (y=690), beamed: one beam over all four (y=669.2), a second over the last three (y=672.9)."""
    glyphs = [_head(x, 690.0) for x in (100, 130, 145, 160)]
    lines = [_down_stem(x, 690.0, 668.0) for x in (100, 130, 145, 160)]
    lines += [Line(100, 669.2, 160, 669.2, 5.0), Line(130, 672.9, 160, 672.9, 5.0)]
    page = _page(glyphs + list(extra_glyphs), lines, tops)
    page.smufl = True
    return page


def test_the_triplet_number_counts_only_the_notes_it_is_centred_over():
    # an eighth and three 16ths in one beam group; the "3" stands under the last three
    page = _beamed_group_page([_digit("3", 145.0, 660.0)])
    events = [e for bar in _read_page(page) for e in bar.events if e.heads]
    by_x = {round(e.x): e for e in events}
    assert by_x[100].group is None and by_x[100].length == pytest.approx(0.5)
    for x in (130, 145, 160):
        assert by_x[x].group is not None
        assert by_x[x].length == pytest.approx(0.25 * 2 / 3)


def test_a_beam_of_other_notes_near_a_stems_end_is_not_its_beam():
    # the same group, with the beam of a group of the other staff passing 3.3 beyond the stems'
    # free ends: it must not turn the eighth into a 16th
    other = [Line(95, 665.0, 170, 665.0, 5.0)]
    page = _beamed_group_page()
    page.lines += other
    events = [e for bar in _read_page(page) for e in bar.events if e.heads]
    first = min(events, key=lambda e: e.x)
    assert first.length == pytest.approx(0.5)


def _two_chords_two_ties() -> PageDrawing:
    # two chords (B4 + D5) with up stems; two ties between them, each lying under its own note,
    # the second one nearer to B4's head than to D5's own
    glyphs = [_head(x, y) for x in (100, 140) for y in (690.0, 695.0)]
    lines = [Line(x + 6, 690.0, x + 6, 707.5, 1.0) for x in (100, 140)]
    page = _page(glyphs, lines)
    page.curves += [pv.Curve(108.0, 687.1, 139.0, 687.1), pv.Curve(108.0, 692.1, 139.0, 692.1)]
    return page


def test_every_note_of_a_tied_chord_is_tied():
    bars = _read_page(_two_chords_two_ties())
    heads = [h for bar in bars for e in bar.events for h in e.heads]
    tied = sorted(h.step for h in heads if h.tied)
    assert tied == [4, 6]  # B4 and D5 of the second chord


def _whole(x: float, y: float) -> Glyph:
    return Glyph(pv.HEAD_WHOLE, x, y, x, y - 2.5, x + 10, y + 2.5, 20.0, True)


def test_a_tie_between_two_whole_notes_is_found_though_their_heads_are_wide():
    # whole notes on B4 in two bars; the tie starts 11 right of the first head's origin (past a
    # black head's width) and ends 3.2 inside the second head
    glyphs = [_whole(70, 690.0), _whole(150, 690.0)]
    page = _page(glyphs, [Line(140, 680, 140, 700, 1.0)], tops=(700,))
    page.curves.append(pv.Curve(81.0, 686.0, 153.2, 686.0))
    heads = [h for bar in _read_page(page) for e in bar.events for h in e.heads]
    assert [h.tied for h in sorted(heads, key=lambda h: h.x)] == [False, True]


def test_the_staff_lines_of_the_system_above_are_not_an_ending_bracket():
    # two systems close together: the lower staff lines of the upper system lie 10 - 25 above
    # the top line of the lower one, where an ending bracket would be
    page = _page([_head(100, 690.0), _head(100, 650.0)], [Line(106, 690.0, 106, 707.5, 1.0), Line(106, 650.0, 106, 667.5, 1.0)], tops=(700, 660))
    systems = pv.group_systems(page, pv.find_staves(page))
    assert len(systems) == 2
    lower = systems[1]
    assert pv._voltas(page, lower, pv._barlines(page, lower)) == []


def test_a_tie_of_a_note_on_many_ledger_lines_is_found():
    # a note 8 spaces under the staff and its tie, 3 further down
    low = 680.0 - 40.0
    glyphs = [_head(100, low), _head(140, low)]
    lines = [Line(106, low, 106, low + 17.5, 1.0), Line(146, low, 146, low + 17.5, 1.0)]
    page = _page(glyphs, lines)
    page.curves.append(pv.Curve(108.0, low - 2.9, 139.0, low - 2.9))
    heads = [h for bar in _read_page(page) for e in bar.events for h in e.heads]
    assert [h.tied for h in sorted(heads, key=lambda h: h.x)] == [False, True]


def test_a_number_under_the_staff_above_is_not_a_tuplet_of_this_system():
    # two systems 8 spaces apart; the "3" lies 5 spaces under the upper one, 3 over the lower one,
    # whose three eighths stand 4 spaces below it
    lower_top = 640.0
    glyphs = [_head(x, lower_top - 15.0) for x in (100, 120, 140)]
    lines = [Line(x + 6, lower_top - 15.0, x + 6, lower_top + 2.5, 1.0) for x in (100, 120, 140)]
    lines.append(Line(106, lower_top + 2.5, 146, lower_top + 2.5, 5.0))
    page = _page(glyphs + [_digit("3", 125.0, 665.0)], lines, tops=(700, lower_top))
    page.smufl = True
    systems = _read_systems(page)
    events = [e for bar in systems[1] for e in bar.events if e.heads]
    assert events and all(e.group is None for e in events)

# ---------------------------------------------------------------- pages printed smaller than usual


def _scaled(drawing: PageDrawing, k: float) -> PageDrawing:
    """The same page drawn k times the size: positions and boxes scale, stroke widths and the
    font sizes stay what they were (as on a page that a generator shrank to fit the sheet)."""
    for g in drawing.glyphs:
        g.x, g.y, g.l, g.b, g.r, g.t = (v * k for v in (g.x, g.y, g.l, g.b, g.r, g.t))
    for ln in drawing.lines:
        ln.x0, ln.y0, ln.x1, ln.y1 = (v * k for v in (ln.x0, ln.y0, ln.x1, ln.y1))
    for cv in drawing.curves:
        cv.x0, cv.y0, cv.x1, cv.y1 = (v * k for v in (cv.x0, cv.y0, cv.x1, cv.y1))
    return drawing


def _page_with_a_quarter_note() -> PageDrawing:
    return _page([_head(100, 690.0)], [Line(106, 690.0, 106, 707.5, 1.0)])


def test_a_page_printed_smaller_than_usual_is_read_like_a_normal_one():
    normal = [h.pitch for bar in _read_page(_page_with_a_quarter_note()) for e in bar.events for h in e.heads]
    small = _scaled(_page_with_a_quarter_note(), 0.42)
    assert pv.find_staves(small) == []  # its staff spaces are 2.1: no staff to be found
    small = pv._normalized(small)
    assert len(pv.find_staves(small)) == 1
    assert [h.pitch for bar in _read_page(small) for e in bar.events for h in e.heads] == normal == [71]


def test_a_page_of_the_usual_size_is_left_alone():
    page = _page_with_a_quarter_note()
    before = [(g.x, g.y, g.l, g.r) for g in page.glyphs], [(ln.x0, ln.y0, ln.x1, ln.y1) for ln in page.lines]
    assert pv._normalized(page) is page
    assert ([(g.x, g.y, g.l, g.r) for g in page.glyphs], [(ln.x0, ln.y0, ln.x1, ln.y1) for ln in page.lines]) == before


def test_a_page_without_staves_is_left_alone():
    page = PageDrawing([], [Line(10, 20, 200, 20, 1.0), Line(10, 30, 200, 30, 1.0)], [], 595, 842)
    assert pv._normalized(page) is page
    assert page.lines[0].x1 == 200

def _read_one(drawing: PageDrawing, state: pv.ReaderState | None = None) -> list[pv.Bar]:
    state = state or pv.ReaderState()
    bars = []
    for system in pv.group_systems(drawing, pv.find_staves(drawing)):
        bars.extend(pv.analyze_system(drawing, system, state))
    return bars


def test_a_key_change_after_a_barline_is_not_taken_for_the_next_notes_accidentals():
    # bar 1 in C; after the barline at 150 a new key of four flats, the first note (Db5) close
    # behind the last flat, at the same height
    glyphs = [_glyph(pv.CLEF_TREBLE, 45, 685, 12, 36), _head(100, 690.0)]
    glyphs += [_flat(160, 690.0), _flat(165, 697.5), _flat(170, 687.5), _flat(175, 695.0)]
    glyphs += [_head(182, 695.0), _head(230, 690.0)]  # D5 then B4
    lines = _staff_lines(700) + [
        Line(40, 680, 40, 700, 1.0), Line(150, 680, 150, 700, 1.5), Line(300, 680, 300, 700, 1.5),
        Line(106, 690.0, 106, 707.0, 1.0), Line(188, 695.0, 188, 712.0, 1.0), Line(236, 690.0, 236, 707.0, 1.0),
    ]
    bars = _read_one(PageDrawing(glyphs, lines, [], 595, 842))
    pitches = [h.pitch for b in bars for e in sorted(b.events, key=lambda e: e.x) for h in e.heads]
    assert pitches == [71, 73, 70]  # B4, then Db5 and Bb4 in the new key
    assert bars[-1].keys[0] == {6: -1, 2: -1, 5: -1, 1: -1}


def test_a_chords_stem_starting_left_of_its_heads_is_not_a_barline():
    # a down stem of a chord drawn 3.1 left of its heads and reaching 4.2 past the lowest one
    # spans the whole staff like a thin barline; it must stay a stem
    glyphs = [_glyph(pv.CLEF_TREBLE, 45, 685, 12, 36), _head(150, 685.0), _head(150, 692.5)]
    lines = _staff_lines(700) + [
        Line(40, 680, 40, 700, 1.0), Line(300, 680, 300, 700, 1.5),
        Line(146.9, 680.8, 146.9, 703.0, 0.55),
    ]
    page = PageDrawing(glyphs, lines, [], 595, 842)
    system = pv.group_systems(page, pv.find_staves(page))[0]
    assert [round(b.x) for b in pv._barlines(page, system)] == [300]


def test_an_accidental_holds_on_over_a_tie_into_the_next_bar():
    # F#4 (sharp) tied over the barline to F4 without an accidental: still F#4; the F4 after
    # it, not tied, is F natural again
    glyphs = [_glyph(pv.CLEF_TREBLE, 45, 685, 12, 36), _glyph(pv.SHARP, 92, 682.5, 4, 12)]
    glyphs += [_head(100, 682.5), _head(160, 682.5), _head(220, 682.5)]
    lines = _staff_lines(700) + [
        Line(40, 680, 40, 700, 1.0), Line(150, 680, 150, 700, 1.5), Line(300, 680, 300, 700, 1.5),
        Line(106, 682.5, 106, 700.0, 1.0), Line(166, 682.5, 166, 700.0, 1.0), Line(226, 682.5, 226, 700.0, 1.0),
    ]
    drawing = PageDrawing(glyphs, lines, [pv.Curve(108.0, 679.5, 159.0, 679.5)], 595, 842)
    heads = sorted((h for b in _read_one(drawing) for e in b.events for h in e.heads), key=lambda h: h.x)
    assert [(h.pitch, h.tied) for h in heads] == [(66, False), (66, True), (65, False)]


def test_a_chords_far_accidental_after_a_barline_is_its_notes_not_a_new_key():
    # right after the barline at 150, a big chord with its accidentals stacked in columns: the
    # sharp for its C5 stands 25 left of the head (beyond the usual reach) - not a key of one sharp
    glyphs = [_glyph(pv.CLEF_TREBLE, 45, 685, 12, 36), _head(100, 690.0)]
    glyphs += [_glyph(pv.SHARP, 155, 692.5, 4, 12), _flat(162, 682.5)]
    glyphs += [_head(180, 692.5), _head(180, 682.5), _head(230, 692.5)]
    lines = _staff_lines(700) + [
        Line(40, 680, 40, 700, 1.0), Line(150, 680, 150, 700, 1.5), Line(300, 680, 300, 700, 1.5),
        Line(106, 690.0, 106, 707.0, 1.0), Line(186, 682.5, 186, 710.0, 1.0), Line(236, 692.5, 236, 710.0, 1.0),
    ]
    bars = _read_one(PageDrawing(glyphs, lines, [], 595, 842))
    assert bars[-1].keys[0] == {}
    heads = sorted((h for b in bars for e in b.events for h in e.heads), key=lambda h: (h.x, h.y))
    assert [h.pitch for h in heads] == [71, 64, 73, 73]  # B4, Fb4 + C#5, then C#5 again (the sharp lasts the bar)
