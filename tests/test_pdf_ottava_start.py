"""Ottava labels may align to a notehead's stem/right edge."""
import pytest

from app import pdf_vector as pv
from tests.test_pdf_vector import _glyph, _head, _staff_lines


def _read(code, *, scale=1, label_gap=0):
    # B4 at each head. The label is aligned to the second head's right edge,
    # rather than its left-origin coordinate. First/last heads stay outside.
    width=6.2
    label=100+width+label_gap
    y=720 if pv.OTTAVA[code]>0 else 665
    glyphs=[_glyph(pv.CLEF_TREBLE,45,685,12,36),_glyph(0x134,62,695),_glyph(0x134,62,685),
            _glyph(code,label,y,12,8)]
    for x in (78,100,140,200):
        glyphs.append(pv.Glyph(pv.HEAD_BLACK,x,690,x,687.5,x+width,692.5,20,True))
    lines=_staff_lines(700)+[pv.Line(300,680,300,700,1.5),pv.Line(label+13,y,175,y,.8)]
    for g in glyphs:
        for name in ('x','y','l','b','r','t','size'):
            setattr(g,name,getattr(g,name)*scale)
    for line in lines:
        for name in ('x0','y0','x1','y1'):
            setattr(line,name,getattr(line,name)*scale)
    drawing=pv.PageDrawing(glyphs,lines,[],595*scale,842*scale)
    staves=pv.find_staves(drawing)
    bars=pv.analyze_system(drawing,staves,pv.ReaderState())
    return [h.pitch for b in bars for e in b.events for h in e.heads]


@pytest.mark.parametrize('code,shift',[(0x1B4,12),(0x1B5,-12)])
@pytest.mark.parametrize('scale',[1,2])
def test_a_label_aligned_to_the_head_right_edge_includes_that_head(code,shift,scale):
    assert _read(code,scale=scale)==[71,71+shift,71+shift,71]


@pytest.mark.parametrize('code,shift',[(0x1B4,12),(0x1B5,-12)])
def test_a_label_separated_from_the_previous_head_does_not_pull_it_into_the_octave(code,shift):
    # An actual gap of a quarter staff space is not right-edge alignment.
    assert _read(code,label_gap=1.25)==[71,71,71+shift,71]
