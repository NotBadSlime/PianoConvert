import pytest

from app import pdf_vector as pv


STAFF = pv.Staff(40,300,[700,695,690,685,680],0)


def bar(items, **kwargs):
    events = []
    for i,(pitch,length,tied) in enumerate(items):
        x = 80+35*i
        heads = [] if pitch is None else [pv.Head(x,690,1,False,STAFF,pitch=pitch,tied=tied)]
        events.append(pv.Event(x,STAFF,length,heads,base=length))
    return pv.Bar(events,**kwargs)


def test_a_tie_like_curve_without_a_sounding_predecessor_cannot_delete_the_note():
    first = bar([(60,2,True),(62,2,False)])
    score = pv.build_score([first],120)
    assert [(n.pitch,n.start_q,n.end_q) for n in score.notes] == [(60,0,2),(62,2,4)]


@pytest.mark.parametrize('rest_length',[.25,1,2])
def test_a_tie_like_curve_cannot_fill_a_rest_and_swallow_the_next_attack(rest_length):
    first = bar([(60,1,False),(None,rest_length,False),(60,1,True),
                 (None,2-rest_length,False)] if rest_length<2 else
                [(60,1,False),(None,2,False),(60,1,True)])
    score = pv.build_score([first],120)
    assert [(n.pitch,n.start_q,n.end_q) for n in score.notes] == [
        (60,0,1),(60,1+rest_length,2+rest_length)]


def test_a_real_tie_across_a_barline_and_tempo_change_keeps_one_attack(tmp_path):
    first = bar([(None,2,False),(60,2,False)],tempo=120)
    last = bar([(60,2,True),(None,2,False)],tempo=60)
    score = pv.build_score([first,last],0)
    assert [(n.pitch,n.start_q,n.end_q) for n in score.notes] == [(60,2,6)]
    from app.keyboard_score import notes_from_midi
    notes,_tempos,_meters = notes_from_midi(pv.write_midi(score,tmp_path/'tied.mid'))
    assert [(n.pitch,n.start,n.end) for n in notes] == [(60,1,4)]


def test_breaking_an_unconnected_tie_does_not_mutate_the_written_source_head():
    first = bar([(60,1,False),(None,3,False)])
    middle = bar([(None,4,False)])
    last = bar([(60,1,True),(None,3,False)])
    head = last.events[0].heads[0]
    score = pv.build_score([first,middle,last],120)
    assert [n.start_q for n in score.notes] == [0,8]
    assert head.tied is True


def test_a_rest_in_a_parallel_voice_does_not_break_a_real_tie():
    first = bar([(60,2,False),(60,2,True)])
    # Upper voice is silent throughout while the lower holds its note.
    first.events.append(pv.Event(100,STAFF,4,[],full_bar_rest=True,base=4))
    score = pv.build_score([first],120)
    assert [(n.start_q,n.end_q) for n in score.notes] == [(0,4)]
