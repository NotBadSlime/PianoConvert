"""A neighbouring XML note is tied only when its sounding interval touches."""
import xml.etree.ElementTree as ET
import pytest

from app.pdf_omr import slurs_to_ties


ATTR = '<attributes><divisions>8</divisions><time><beats>4</beats><beat-type>4</beat-type></time></attributes>'


def n(step='C',duration=8,slur=None,voice=1,staff=1,chord=False):
    return ('<note>'+('<chord/>' if chord else '')+
            f'<pitch><step>{step}</step><octave>4</octave></pitch><duration>{duration}</duration>'
            f'<voice>{voice}</voice><staff>{staff}</staff>'+
            (f'<notations><slur type="{slur}" number="1"/></notations>' if slur else '')+'</note>')


def root(*measures):
    return ET.fromstring('<score-partwise><part id="P1">'+''.join(
        f'<measure number="{i+1}">{ATTR if i==0 else ""}{body}</measure>'
        for i,body in enumerate(measures))+'</part></score-partwise>')


def test_real_liszt_b3_half_note_is_not_tied_across_a_half_quarter_gap():
    # Real raw HOMR page1/m2: B3 starts .75q, lasts 2q; the next B3
    # starts 3.25q after the other staff advances the shared XML cursor.
    score=root('<forward><duration>6</duration></forward>'+n('B',16,'start')+
               '<backup><duration>22</duration></backup>'+
               '<note><rest/><duration>26</duration><voice>5</voice><staff>2</staff></note>'+
               n('B',2,'stop'))
    assert slurs_to_ties(score)==0
    assert not score.findall('.//tie')


def test_a_forward_gap_does_not_turn_a_same_pitch_phrase_slur_into_a_tie():
    score=root(n(slur='start')+'<forward><duration>4</duration></forward>'+n(slur='stop'))
    assert slurs_to_ties(score)==0


def test_backup_and_another_staff_keep_a_legitimate_adjacent_tie():
    score=root(n(slur='start')+'<backup><duration>8</duration></backup>'+
               n('E',8,voice=5,staff=2)+n(slur='stop'))
    assert slurs_to_ties(score)==1
    assert len(score.findall('.//tie'))==2


def test_a_same_voice_rest_prevents_a_tie():
    score=root(n(slur='start')+'<note><rest/><duration>8</duration><voice>1</voice><staff>1</staff></note>'+n(slur='stop'))
    assert slurs_to_ties(score)==0


def test_a_whole_chord_slur_keeps_its_adjacent_common_members():
    # HOMR may put the slur on only the first member of a chord.
    score=root(n(slur='start')+n('E',chord=True)+n(slur='stop')+n('E',chord=True))
    assert slurs_to_ties(score)==2
    assert len(score.findall('.//tie'))==4


def test_only_the_chord_member_that_reaches_the_next_onset_can_be_tied():
    score=root(n(duration=16,slur='start')+n('E',8,chord=True)+n(slur='stop')+n('E',chord=True))
    assert slurs_to_ties(score)==1
    notes=score.findall('.//note')
    assert notes[0].find('tie') is not None and notes[2].find('tie') is not None
    assert notes[1].find('tie') is None and notes[3].find('tie') is None


def test_a_legitimate_tie_crosses_a_full_measure_boundary():
    score=root(n(duration=32,slur='start'),n(slur='stop'))
    assert slurs_to_ties(score)==1


def test_a_tie_does_not_skip_an_empty_measure():
    score=root(n(duration=32,slur='start'),'',n(slur='stop'))
    assert slurs_to_ties(score)==0


def test_an_underfilled_normal_measure_is_not_collapsed_for_a_tie():
    score=root(n(slur='start'),n(slur='stop'))
    assert slurs_to_ties(score)==0


def test_an_explicit_pickup_can_tie_to_the_next_measure():
    score=root(n(slur='start'),n(slur='stop'))
    score.find('part/measure').set('implicit','yes')
    assert slurs_to_ties(score)==1


def test_cross_measure_clock_uses_changed_divisions():
    score=root(n(duration=32,slur='start'),'<attributes><divisions>16</divisions></attributes>'+n(duration=16,slur='stop'))
    assert slurs_to_ties(score)==1


@pytest.mark.parametrize('invalid',['Infinity','NaN','-1'])
def test_invalid_measure_clock_does_not_create_ties(invalid):
    score=root(n(slur='start')+n(duration=invalid,slur='stop'))
    assert slurs_to_ties(score)==0
