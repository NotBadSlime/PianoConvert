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
