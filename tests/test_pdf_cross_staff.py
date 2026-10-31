import pytest

from app import pdf_vector as pv
from app.pdf_vector import Bar
from tests.test_pdf_vector_timing import _note, _rest, TOP, BOTTOM


def test_a_cross_staff_beam_is_one_voice_with_a_shared_clock():
    # Treble: quarter chord on each beat, and a 16th rest + three 16ths.
    # The first 16th of each group is printed on the bass staff.
    events=[]
    for beat in range(3):
        x=100+60*beat
        events += [_rest(x-2.5,.25),_note(x,1,False)]
        for j in range(3):
            n=_note(x+15*(j+1),.25,j==0,BOTTOM if j==0 else TOP)
            n.beam_group=beat+1
            events.append(n)
    events += [_note(280,.5,False),_note(310,.5,False)]
    events += [_rest(277.5,.25),_note(100,3,True,BOTTOM),_note(280,1,True,BOTTOM)]
    # The last three bass 16ths follow a rest printed above the treble staff.
    for x in (295,310,325):
        n=_note(x,.25,True,BOTTOM)
        n.beam_group=4
        events.append(n)
    bar=Bar(events,meter=(4,4))
    pv._layout_bar(bar)
    assert [e.t for e in events if e.beam_group==1] == pytest.approx([.25,.5,.75])
    assert [e.t for e in events if e.beam_group==2] == pytest.approx([1.25,1.5,1.75])
    assert [e.t for e in events if e.beam_group==3] == pytest.approx([2.25,2.5,2.75])
    assert [e.t for e in events if e.beam_group==4] == pytest.approx([3.25,3.5,3.75])
    assert all(total == pytest.approx(4) for _,total in bar.totals)
    first_times=[e.t for e in events]
    pv._layout_bar(bar)
    assert [e.t for e in events] == pytest.approx(first_times)


def test_beam_anchor_propagation_rejects_conflicting_times():
    notes=[_note(100+i*20,.25,True,BOTTOM) for i in range(3)]
    for n in notes:n.beam_group=1
    anchors=((100,0),(140,1))  # two quarter lengths disagree with this .5-quarter run
    assert pv._beam_time_anchors(notes,anchors,4) == anchors


def test_a_rest_inside_a_beam_run_prevents_time_inference():
    notes=[_note(100+i*20,.25,True,BOTTOM) for i in range(3)]
    for n in notes:n.beam_group=1
    notes.append(_rest(110,.25,BOTTOM))
    anchors=((140,1),)
    assert pv._beam_time_anchors(notes,anchors,4) == anchors


def test_touching_beams_of_simultaneous_voices_keep_separate_clocks():
    treble=[_note(100,1,False),_note(140,.5,False),_note(160,.5,False),_note(180,2,False)]
    bass=[_note(100+i*20,.5,True,BOTTOM) for i in range(4)]+[_note(180,2,False,BOTTOM)]
    for n in treble[1:3]+bass[:4]:
        n.beam_group=1
    bar=Bar(treble+bass,meter=(4,4))
    assert pv._layout_bar(bar) == pytest.approx(4)
    assert [e.t for e in treble] == pytest.approx([0,1,1.5,2])
    assert [e.t for e in bass] == pytest.approx([0,.5,1,1.5,2])
    assert all(total == pytest.approx(4) for _,total in bar.totals)


def test_a_complete_existing_reading_is_not_replaced_by_cross_staff_inference():
    treble=[_note(100+i*50,1,False) for i in range(4)]
    bass=[_note(115+i*50,1,True,BOTTOM) for i in range(4)]
    for n in treble[1:3]+bass[1:2]:
        n.beam_group=1
    bar=Bar(treble+bass,meter=(4,4))
    assert pv._layout_bar(bar) == pytest.approx(4)
    assert [n.t for n in treble] == pytest.approx([0,1,2,3])
    assert [n.t for n in bass] == pytest.approx([0,1,2,3])
