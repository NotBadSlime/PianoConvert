import pytest

from app import pdf_vector as pv
from app.pdf_vector import Glyph, Line
from tests.test_pdf_vector_timing import _head, _page


def digit(text,x,y=715):
    return Glyph(ord(text),x,y,x,y-3,x+3,y+3,16,False,text)


def test_adjacent_digits_are_one_tuplet_number_not_a_trailing_triplet():
    marks = pv._tuplet_numbers([digit('1',173),digit('3',177),digit('3',230)])
    assert [g.text for g in marks] == ['13','3']


def test_digits_at_different_heights_or_far_apart_are_not_joined():
    marks = pv._tuplet_numbers([digit('1',100),digit('3',104,725),digit('3',120)])
    assert sorted(g.text for g in marks) == ['1','3','3']


def test_small_baseline_rounding_does_not_reverse_a_number():
    marks=pv._tuplet_numbers([digit('1',100,715),digit('3',104,715.1)])
    assert [g.text for g in marks] == ['13']


def test_independent_labels_keep_their_original_claim_order():
    a,b=digit('3',200,715),digit('5',100,725)
    assert pv._tuplet_numbers([a,b]) == [a,b]


@pytest.mark.parametrize('count,normal',[(11,8),(13,8),(15,8),(12,8)])
def test_beamed_multidigit_tuplet_uses_all_members(count,normal):
    glyphs=[]
    lines=[]
    for i in range(count):
        x=100+12*i
        glyphs.append(_head(x,687.5))
        lines.append(Line(x+6,687.5,x+6,705,1))
    for y in (705,701,697):
        lines.append(Line(106,y,106+12*(count-1),y,5))
    center=(106+106+12*(count-1))/2
    for i,ch in enumerate(str(count)):
        glyphs.append(digit(ch,center-4+4*i))
    drawing=_page(glyphs,lines)
    state=pv.ReaderState()
    staff=pv.find_staves(drawing)
    bars=pv.analyze_system(drawing,staff,state)
    events=[e for b in bars for e in b.events if e.heads]
    assert len(events)==count
    assert all(e.group is not None and e.group.n==count for e in events)
    assert [e.length for e in events] == pytest.approx([.125*normal/count]*count)


def test_a_multidigit_label_without_a_complete_beam_group_is_not_a_tuplet():
    glyphs=[_head(100,687.5),_head(124,687.5),_head(148,687.5),digit('1',122),digit('3',126)]
    lines=[Line(x+6,687.5,x+6,705,1) for x in (100,124,148)]
    lines += [Line(106,705,154,705,5)]
    drawing=_page(glyphs,lines)
    bars=pv.analyze_system(drawing,pv.find_staves(drawing),pv.ReaderState())
    assert all(e.group is None for b in bars for e in b.events if e.heads)
