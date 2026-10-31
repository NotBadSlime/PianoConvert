import pytest

from app import pdf_vector as pv
from tests.test_pdf_vector_timing import TOP, _beamed, _note, _rest


def test_a_sparse_triplet_label_does_not_leave_the_rest_of_a_complete_beam_uneven():
    # Moonlight's engraving has 12 eighths, two beams of six, and one 3
    # over the middle. Its initial local assignment can straddle the beams.
    events = _beamed(50,6,.5,1) + _beamed(110,6,.5,2)
    group = pv.Tuplet(3,2/3)
    for event in events[5:8]:
        event.group = group
        event.length = event.base*group.ratio
    assert pv._layout_staff(events) == pytest.approx(5.5)
    assert pv._layout_staff_robust(events,4) == pytest.approx(4)
    assert [event.t for event in events] == pytest.approx([i/3 for i in range(12)])
    assert [event.length for event in events] == pytest.approx([1/3]*12)


def test_a_sparse_triplet_repair_is_rejected_when_it_does_not_fill_the_meter():
    events = _beamed(50,6,.5,1) + [_note(120,2,True)]
    group = pv.Tuplet(3,2/3)
    for event in events[:3]:
        event.group = group
        event.length = event.base*group.ratio
    before = [(event.length,event.group) for event in events]
    assert pv._unmarked_tuplets(events,3) is None
    assert [(event.length,event.group) for event in events] == before


def test_an_explicit_other_tuplet_cannot_be_overwritten_as_triplets():
    events = _beamed(50,6,.5,1)
    group = pv.Tuplet(5,4/5)
    for event in events:
        event.group = group
        event.length = event.base*group.ratio
    before = [event.length for event in events]
    assert pv._unmarked_tuplets(events,2) is None
    assert [event.length for event in events] == before


def test_continuous_triplets_keep_their_clock_below_an_independent_melody():
    accompaniment = _beamed(50,6,.5,1)+_beamed(110,6,.5,2)
    for event in accompaniment:
        event.heads[0].stem.up = False
    melody = [_rest(50,2),_rest(110,1),_note(134,.75,True),_note(156,.25,True)]
    for event in melody[:2]:
        event.y = TOP.mid+1.5*TOP.space
    assert pv._layout_staff_robust(accompaniment+melody,4) == pytest.approx(4)
    assert [event.t for event in accompaniment] == pytest.approx([i/3 for i in range(12)])
    assert [event.t for event in melody] == pytest.approx([0,2,3,3.75])


def test_failed_two_voice_triplet_repair_does_not_leak_new_durations():
    accompaniment = _beamed(50,6,.5,1)+_beamed(110,6,.5,2)
    for event in accompaniment:
        event.heads[0].stem.up = False
    # This independent melody is itself incomplete: the combined repair
    # must not keep a plausible accompaniment change after rejecting it.
    melody = [_rest(50,2),_note(134,.25,True)]
    melody[0].y = TOP.mid+1.5*TOP.space
    before = [event.length for event in accompaniment+melody]
    pv._layout_staff_robust(accompaniment+melody,4)
    assert [event.length for event in accompaniment+melody] == before


def test_labelled_mixed_values_do_not_hide_the_adjacent_unmarked_triplet():
    # Real 6/8 regression: a labelled quarter+sixteenth is followed in the
    # same beam by three unmarked sixteenths. They need their own repair;
    # the mixed base values of the whole beam must not block it.
    labelled = _beamed(50,2,.25,1)
    labelled[0].base = .5
    group = pv.Tuplet(3,2/3)
    for event in labelled:
        event.group = group
        event.length = event.base*group.ratio
    following = _beamed(70,3,.25,1)
    last = _note(110,2,True)
    events = labelled+following+[last]
    assert pv._layout_staff(events) == pytest.approx(3.25)
    assert pv._layout_staff_robust(events,3) == pytest.approx(3)
    assert [event.t for event in events] == pytest.approx([0,1/3,.5,2/3,5/6,1])
    assert [event.length for event in following] == pytest.approx([1/6]*3)


def test_incomplete_triplet_does_not_compress_the_preceding_plain_pair_to_fill_a_bar():
    # A real bracket contains sixteenth+eighth, but only the sixteenth was
    # assigned. Converting its preceding ordinary pair can also fill the
    # bar numerically, and is the wrong repair (the 我落泪 regression).
    events = _beamed(50,3,.25,1)+[_note(90,.5,True)]
    group = pv.Tuplet(3,2/3)
    events[2].group = group
    events[2].length = 1/6
    before = [event.length for event in events]
    assert pv._unmarked_tuplets(events,1) is None
    assert [event.length for event in events] == before
