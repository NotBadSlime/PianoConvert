"""Tremolo's first subdivision follows the same tie rules as an ordinary note."""
import pytest

from app import pdf_vector as pv

STAFF = pv.Staff(40,300,[700,695,690,685,680],0)


def _event(x, pitch, length, *, tied=False, rest=False):
    heads = [] if rest else [pv.Head(x,690,1,False,STAFF,pitch=pitch,tied=tied)]
    return pv.Event(x,STAFF,length,heads,base=length)


def _tremolo(*, tied=True):
    event = _event(80,60,4,tied=tied)
    event.repeat_sub = .5
    return event


def test_an_orphan_tremolo_tie_keeps_its_first_attack():
    event = _tremolo()
    score = pv.build_score([pv.Bar([event])],120)
    assert [(n.start_q,n.end_q) for n in score.notes] == [(i*.5,(i+1)*.5) for i in range(8)]
    assert event.heads[0].tied is True


def test_a_continuous_tie_holds_through_the_first_tremolo_subdivision(tmp_path):
    previous = pv.Bar([_event(60,0,3,rest=True), _event(100,60,1)])
    score = pv.build_score([previous,pv.Bar([_tremolo()])],120)
    assert [(n.start_q,n.end_q) for n in score.notes] == [(3,4.5)]+[(4+i*.5,4+(i+1)*.5) for i in range(1,8)]
    from app.keyboard_score import notes_from_midi
    notes,_tempos,_meters = notes_from_midi(pv.write_midi(score,tmp_path/'held-tremolo.mid'))
    assert [(n.start,n.end) for n in notes] == pytest.approx([(1.5,2.25)]+[(2+i*.25,2+(i+1)*.25) for i in range(1,8)])


def test_a_tremolo_tie_cannot_delete_the_attack_after_an_explicit_rest():
    previous = pv.Bar([_event(60,60,1), _event(100,0,3,rest=True)])
    score = pv.build_score([previous,pv.Bar([_tremolo()])],120)
    assert [(n.start_q,n.end_q) for n in score.notes] == [(0,1)]+[(4+i*.5,4+(i+1)*.5) for i in range(8)]


def test_only_the_tied_member_of_a_tremolo_chord_is_sustained():
    previous = pv.Bar([_event(60,0,3,rest=True), _event(100,60,1)])
    tremolo = _tremolo()
    tremolo.heads.append(pv.Head(80,700,1,False,STAFF,pitch=64,tied=True))
    score = pv.build_score([previous,pv.Bar([tremolo])],120)
    assert [(n.start_q,n.end_q) for n in score.notes if n.pitch==60] == [(3,4.5)]+[(4+i*.5,4+(i+1)*.5) for i in range(1,8)]
    assert [(n.start_q,n.end_q) for n in score.notes if n.pitch==64] == [(4+i*.5,4+(i+1)*.5) for i in range(8)]
