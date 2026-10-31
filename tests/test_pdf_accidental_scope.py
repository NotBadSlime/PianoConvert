"""Accidental meaning follows engraving geometry, not PDF object order."""

import pytest

from app import pdf_vector as pv
from tests.test_pdf_vector import _glyph, _grand_staff_page, _quarter_up


def _read_accidentals(symbols):
    glyphs = list(symbols)
    lines = []
    # Same written F4, a different octave F5, then F4 after the barline.
    for x, y in [(100, 682.5), (130, 682.5), (160, 700), (205, 682.5)]:
        head, stem = _quarter_up(x, y)
        glyphs.append(head)
        lines.append(stem)
    drawing = _grand_staff_page(glyphs, lines)
    system = pv.group_systems(drawing, pv.find_staves(drawing))[0]
    return pv.analyze_system(drawing, system, pv.ReaderState())


@pytest.mark.parametrize("code,alter", [(pv.SHARP, 1), (pv.FLAT, -1)])
@pytest.mark.parametrize("reverse", [False, True])
def test_natural_then_single_accidental_uses_the_nearer_sign(code, alter, reverse):
    # Traditional cancellation of a double alteration can print a natural
    # followed by a sharp/flat. The sign next to the head determines the pitch.
    signs = [_glyph(pv.NATURAL, 87, 682.5, 4, 12), _glyph(code, 92, 682.5, 4, 12)]
    bars = _read_accidentals(signs[::-1] if reverse else signs)
    assert [h.pitch for b in bars for e in b.events for h in e.heads] == [
        65 + alter, 65 + alter, 77, 65,
    ]
    assert all(b.keys[0] == {} for b in bars)


@pytest.mark.parametrize("reverse", [False, True])
def test_a_nearer_natural_overrides_an_earlier_sharp(reverse):
    signs = [_glyph(pv.SHARP, 87, 682.5, 4, 12), _glyph(pv.NATURAL, 92, 682.5, 4, 12)]
    bars = _read_accidentals(signs[::-1] if reverse else signs)
    assert [h.pitch for b in bars for e in b.events for h in e.heads] == [65, 65, 77, 65]


@pytest.mark.parametrize("reverse", [False, True])
def test_accidental_fallback_matching_keeps_the_nearest_sign(reverse):
    # A crowded chord can put the alteration beyond the primary 16pt window;
    # the later fallback matching must use the same distance rule.
    signs = [_glyph(pv.NATURAL, 72, 682.5, 4, 12), _glyph(pv.SHARP, 83, 682.5, 4, 12)]
    bars = _read_accidentals(signs[::-1] if reverse else signs)
    assert [h.pitch for b in bars for e in b.events for h in e.heads] == [66, 66, 77, 65]


@pytest.mark.parametrize("code,alter", [(pv.SHARP, 1), (pv.FLAT, -1)])
def test_a_compound_accidental_is_not_a_global_key_change(code, alter):
    # Both signs are pushed into the key-signature search window by a crowded
    # first chord. They spell the same written pitch, not a circle-of-fifths run.
    bars = _read_accidentals([
        _glyph(pv.NATURAL, 72, 682.5, 4, 12), _glyph(code, 78, 682.5, 4, 12),
    ])
    assert [h.pitch for b in bars for e in b.events for h in e.heads] == [
        65 + alter, 65 + alter, 77, 65,
    ]
    assert all(b.keys[0] == {} for b in bars)


def test_close_repeated_notes_keep_their_own_accidentals():
    # There is a note between the natural and the following sharp. These are
    # two independent alterations, even when the symbols are only 6pt apart.
    glyphs = [_glyph(pv.SHARP, 72, 682.5, 4, 12),
              _glyph(pv.NATURAL, 86, 682.5, 4, 12), _glyph(pv.SHARP, 92, 682.5, 4, 12)]
    lines = []
    for x in (80, 91, 100, 130, 205):
        head, stem = _quarter_up(x, 682.5)
        glyphs.append(head)
        lines.append(stem)
    drawing = _grand_staff_page(glyphs, lines)
    system = pv.group_systems(drawing, pv.find_staves(drawing))[0]
    bars = pv.analyze_system(drawing, system, pv.ReaderState())
    assert [h.pitch for b in bars for e in b.events for h in e.heads] == [66, 65, 66, 66, 65]
