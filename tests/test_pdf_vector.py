from app import pdf_vector as pv
from app.pdf_vector import Bar, Glyph, Line, PageDrawing


def _head(code: int, x: float, y: float, size: float = 20.0) -> Glyph:
    return Glyph(code, x, y, x, y - 2.5, x + 6, y + 2.5, size, True)


def _glyph(code: int, x: float, y: float, w: float = 6, h: float = 8) -> Glyph:
    return Glyph(code, x, y, x, y - h / 2, x + w, y + h / 2, 20.0, True)


def _staff_lines(top: float, x0: float = 40, x1: float = 300) -> list[Line]:
    return [Line(x0, top - 5 * k, x1, top - 5 * k, 1.0) for k in range(5)]


def _grand_staff_page(extra_glyphs=(), extra_lines=()) -> PageDrawing:
    """Treble staff 700..680, bass staff 650..630, 4/4, barlines at 180 and 300."""
    lines = _staff_lines(700) + _staff_lines(650)
    lines.append(Line(40, 630, 40, 700, 1.0))  # system bracket line
    lines.append(Line(180, 630, 180, 700, 1.5))
    lines.append(Line(300, 630, 300, 700, 1.5))
    glyphs = [
        _glyph(pv.CLEF_TREBLE, 45, 685, 12, 36),
        _glyph(pv.CLEF_BASS, 45, 645, 12, 16),
        _glyph(0x134, 62, 695), _glyph(0x134, 62, 685),
        _glyph(0x134, 62, 645), _glyph(0x134, 62, 635),
    ]
    return PageDrawing(glyphs + list(extra_glyphs), lines + list(extra_lines), [], 595, 842)


def _quarter_up(x: float, y: float) -> tuple[Glyph, Line]:
    return _head(pv.HEAD_BLACK, x, y), Line(x + 6, y, x + 6, y + 17.5, 1.0)


def _read(drawing: PageDrawing) -> pv.VectorScore:
    state = pv.ReaderState()
    bars: list[Bar] = []
    for system in pv.group_systems(drawing, pv.find_staves(drawing)):
        bars.extend(pv.analyze_system(drawing, system, state))
    return pv.build_score(bars, 90.0)


def test_reads_pitches_and_rhythm_from_the_drawing():
    glyphs: list[Glyph] = []
    lines: list[Line] = []
    # bar 1 treble: C5 D5 E5 F5 quarters; bass: whole C3
    for i, y in enumerate([692.5, 695.0, 697.5, 700.0]):
        head, stem = _quarter_up(80 + 25 * i, y)
        glyphs.append(head)
        lines.append(stem)
    glyphs.append(_head(pv.HEAD_WHOLE, 80, 637.5))
    # bar 2 treble: half G4, half A4 (stems up); bass: whole rest
    for i, y in enumerate([685.0, 687.5]):
        glyphs.append(_head(pv.HEAD_HALF, 200 + 45 * i, y))
        lines.append(Line(206 + 45 * i, y, 206 + 45 * i, y + 17.5, 1.0))
    glyphs.append(_glyph(0x161, 230, 645, 6, 2.5))
    score = _read(_grand_staff_page(glyphs, lines))
    notes = sorted((n.start_q, n.pitch) for n in score.notes)
    assert notes == [(0.0, 48), (0.0, 72), (1.0, 74), (2.0, 76), (3.0, 77), (4.0, 67), (6.0, 69)]
    assert score.meters == [(0.0, (4, 4))]


def test_accidental_and_tie():
    glyphs: list[Glyph] = []
    lines: list[Line] = []
    # F#5 (sharp before the head), then a tie to the next F#5 which must not sound again
    head1, stem1 = _quarter_up(80, 700.0)
    head2, stem2 = _quarter_up(105, 700.0)
    glyphs += [_glyph(pv.SHARP, 72, 700.0, 4, 12), head1, head2]
    lines += [stem1, stem2]
    drawing = _grand_staff_page(glyphs, lines)
    drawing.curves.append(pv.Curve(87, 703, 104, 703))
    score = _read(drawing)
    # the only bar is two beats long: a pickup, written at the end of a full 4/4 bar
    assert [(n.start_q, n.pitch, n.end_q) for n in score.notes] == [(2.0, 78, 4.0)]


def _bars(*specs: str) -> list[Bar]:
    """'[' repeat start, ']' repeat end, '1' / '2' ending number, '?' unnumbered ending."""
    out = []
    for spec in specs:
        bar = Bar([], length=4.0, repeat_start="[" in spec, repeat_end="]" in spec)
        bar.volta = 1 if "1" in spec else 2 if "2" in spec else pv.VOLTA_UNKNOWN if "?" in spec else 0
        bar.x0 = len(out)
        out.append(bar)
    return out


def _order(bars: list[Bar]) -> list[int]:
    pv._number_voltas(bars)
    return [int(b.x0) for b in pv._expand_repeats(bars)]


def test_repeat_with_numbered_endings():
    assert _order(_bars("", "[", "", "1]", "2", "")) == [0, 1, 2, 3, 1, 2, 4, 5]


def test_unnumbered_endings_are_numbered_from_the_repeat_signs():
    assert _order(_bars("[", "", "?]", "?", "")) == [0, 1, 2, 0, 1, 3, 4]


def test_first_ending_lasts_until_its_repeat_sign():
    # the bracket is drawn over the first bar of the ending only
    assert _order(_bars("[", "?", "", "]", "?", "")) == [0, 1, 2, 3, 0, 4, 5]


def test_endings_without_repeat_signs_are_ignored():
    assert _order(_bars("", "?", "", "")) == [0, 1, 2, 3]


def test_a_repeat_to_the_start_skips_the_pickup_bar_that_the_short_first_ending_leads_into():
    # Für Elise: pickup bar (half a bar), 3 bars, a short 1st ending closing the repeat
    bars = _bars("", "", "", "", "1]", "2", "")
    bars[0].length = 2.0
    bars[4].length = 2.0
    assert _order(bars) == [0, 1, 2, 3, 4, 1, 2, 3, 5, 6]


def test_a_repeat_to_the_start_keeps_a_full_first_bar():
    assert _order(_bars("", "", "]", "")) == [0, 1, 2, 0, 1, 2, 3]


def test_non_vector_pdf_is_not_read_as_vector(tmp_path):
    from PIL import Image

    pdf = tmp_path / "scan.pdf"
    Image.new("RGB", (200, 200), "white").save(pdf)
    assert pv.is_vector_score(pdf) is False


def _whole_bar_with_chord(arpeggio: bool) -> pv.VectorScore:
    # treble: a quarter chord C5 E5 G5, then three quarter rests' worth of nothing but a
    # whole rest in the bass; the arpeggio sign stands just left of the chord
    glyphs: list[Glyph] = []
    lines: list[Line] = []
    for y in (692.5, 697.5, 702.5):
        glyphs.append(_head(pv.HEAD_BLACK, 100, y))
    lines.append(Line(106, 692.5, 106, 720.0, 1.0))
    for x in (125, 145, 165):
        glyphs.append(_glyph(0x163, x, 690, 5, 15))  # quarter rests
    glyphs.append(_glyph(0x161, 100, 645, 6, 2.5))
    if arpeggio:
        glyphs.append(Glyph(pv.ARPEGGIO, 92, 693.4, 92, 690.0, 94.9, 696.9, 16.0, True))
        glyphs.append(Glyph(pv.ARPEGGIO, 92, 700.4, 92, 697.0, 94.9, 703.9, 16.0, True))
    return _read(_grand_staff_page(glyphs, lines))


def test_arpeggio_sign_rolls_the_chord_after_it():
    rolled = _whole_bar_with_chord(True)
    assert sorted((n.pitch, n.arpeggio) for n in rolled.notes) == [(72, True), (76, True), (79, True)]
    plain = _whole_bar_with_chord(False)
    assert not any(n.arpeggio for n in plain.notes)


def test_midi_carries_graces_rolls_and_the_tune(tmp_path):
    from app.keyboard_score import keyboard_text, notes_from_midi

    S = pv.ScoreNote
    score = pv.VectorScore(
        notes=[
            S(72, 0.0, 1.0, 0),
            S(74, 0.875, 1.0, 0, grace=True),
            S(76, 1.0, 2.0, 0),
            S(48, 2.0, 4.0, 1, arpeggio=True),
            S(52, 2.0, 4.0, 1, arpeggio=True),
            S(55, 2.0, 4.0, 1, arpeggio=True),
            S(79, 2.0, 4.0, 0, arpeggio=True),
        ],
        meters=[(0.0, (4, 4))],
        bpm=120.0,
        bars=1,
    )
    midi = pv.write_midi(score, tmp_path / "carry.mid")
    notes, tempos, meters = notes_from_midi(midi)
    assert [n.pitch for n in notes if n.grace] == [74]
    assert sorted(n.pitch for n in notes if n.arpeggio) == [48, 52, 55, 79]
    assert {n.pitch for n in notes if n.part == 0} == {72, 74, 76, 79}
    text = keyboard_text(notes, "carry", tempos, meters)
    rows = text.split("【键盘谱】")[1].strip().splitlines()
    assert rows == ["1 旋律 | Q wE T     - |", "  伴奏 | 0 0  {ZCB} - |"]

def test_smufl_glyphs_get_musicfont_codes_and_sizes():
    font = "ABCDEF+Leland"
    chars = [
        (0xE0A4, 100.0, 700.0, 100.0, 697.4, 106.5, 702.6, 20.0, font, "\ue0a4"),   # black head
        (0xE0A4, 120.0, 700.0, 120.0, 698.2, 124.5, 701.8, 14.0, font, "\ue0a4"),   # small head
        (0xE1E7, 108.0, 700.0, 108.0, 699.0, 110.0, 701.0, 20.0, font, "\ue1e7"),   # dot
        (0xE044, 90.0, 702.5, 89.0, 701.5, 91.0, 703.5, 20.0, font, "\ue044"),      # repeat dot
        (0xE222, 104.0, 690.0, 101.0, 686.0, 107.0, 694.0, 20.0, font, "\ue222"),   # 3 tremolo strokes
        (0x33, 130.0, 720.0, 129.0, 716.0, 133.0, 724.0, 9.0, "Edwin-Italic", "3"),  # tuplet number
    ]
    glyphs, lines = pv._smufl_glyphs(chars)
    assert [(g.code, round(g.size, 1), g.music) for g in glyphs] == [
        (pv.HEAD_BLACK, 20.0, True), (pv.HEAD_BLACK, 14.0, True), (pv.DOT, 20.0, True), (0x33, 14.4, False)
    ]
    assert sum(1 for ln in lines if ln.width == 4.0 and ln.x0 == ln.x1) == 1   # repeat dot
    assert sum(1 for ln in lines if ln.width == 5.0) == 3                        # tremolo strokes


def test_smufl_shapes_become_reader_lines():
    lines: list[Line] = []
    curves: list = []
    # a filled beam (slanted parallelogram), a filled thick barline, a stroked tuplet bracket
    pv._smufl_shape([(2, 100, 700), (0, 140, 704), (0, 140, 701.5), (0, 100, 697.5)], True, False, 0.0, lines, curves)
    pv._smufl_shape([(2, 300, 640), (0, 302.7, 640), (0, 302.7, 700), (0, 300, 700)], True, False, 0.0, lines, curves)
    pv._smufl_shape([(2, 200, 726), (0, 200, 729), (0, 230, 729), (0, 230, 726)], False, True, 0.6, lines, curves)
    beam, barline = lines[0], lines[1]
    assert beam.width == 5.0 and beam.x0 == 100 and beam.x1 == 140 and abs(beam.y0 - 698.75) < 1e-6
    assert barline.vertical and abs(barline.width - 2.7) < 1e-6
    assert [(ln.x0, ln.y0, ln.x1, ln.y1) for ln in lines[2:]] == [(200, 726, 200, 729), (200, 729, 230, 729), (230, 729, 230, 726)]


def test_staff_lines_drawn_bar_by_bar_are_joined():
    pieces = [Line(40, 700, 150, 700, 0.55), Line(150, 700, 260, 700, 0.55), Line(260.2, 700, 300, 700, 0.55)]
    joined = pv._join_staff_segments(pieces + [Line(150, 690, 150, 710, 0.9)])
    flat = [ln for ln in joined if ln.horizontal]
    assert [(ln.xmin, ln.xmax) for ln in flat] == [(40, 300)]


def test_tempo_changes_are_written_to_the_midi(tmp_path):
    import pretty_midi

    S = pv.ScoreNote
    score = pv.VectorScore(
        notes=[S(60, float(i), i + 0.9, 0) for i in range(8)],
        meters=[(0.0, (4, 4))],
        bpm=120.0,
        bars=2,
        tempos=[(4.0, 60.0)],
    )
    pm = pretty_midi.PrettyMIDI(str(pv.write_midi(score, tmp_path / "tempo.mid")))
    times, bpms = pm.get_tempo_changes()
    assert [round(t, 3) for t in times] == [0.0, 2.0] and [round(b) for b in bpms] == [120, 60]
    starts = sorted(n.start for n in pm.instruments[0].notes)
    assert [round(s, 3) for s in starts] == [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0]

def test_tie_to_a_note_that_ended_long_before_is_played():
    staff = pv.Staff(40, 300, [700, 695, 690, 685, 680], 0)

    def ev(t: float, length: float, tied: bool = False, rest: bool = False) -> pv.Event:
        x = 100 + 25 * t
        heads = [] if rest else [pv.Head(x, 690, 1.0, False, staff, 0, 76, tied=tied)]
        return pv.Event(x, staff, length, heads)

    held_on = pv.Bar([ev(0, 1.0), ev(1, 1.0, tied=True), ev(2, 2.0)], length=4.0)        # a real tie
    rests = [pv.Bar([ev(0, 4.0, rest=True)], length=4.0) for _ in range(2)]
    far = pv.Bar([ev(0, 2.0, rest=True), ev(2, 1.0, rest=True), ev(3, 1.0, tied=True)], length=4.0)
    bars = [held_on] + rests + [far]
    for k, bar in enumerate(bars):
        bar.x0 = k
    score = pv.build_score(bars, 120.0)
    assert [(n.start_q, n.end_q) for n in score.notes] == [(0.0, 2.0), (2.0, 4.0), (15.0, 16.0)]
