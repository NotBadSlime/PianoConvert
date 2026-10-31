"""Outline augmentation dots can be displaced into either adjacent space."""
import pytest

from app import pdf_outline as po


def _char(code, box):
    l,b,r,t = box
    return (code,l,b,l,b,r,t,20,'Leland',chr(code))


def test_moonlight_dot_in_the_lower_adjacent_space_is_an_augmentation():
    # Fresh random ms04, bar 5: the upper-voice G#4 dotted eighth.
    head = _char(0xE0A4,(501.063,553.418,507.641,558.918))
    dot = _char(0xE044,(509.396,552.528,511.661,554.809))
    assert po._dots([head,dot],5)[1][0] == 0xE1E7


def test_a_double_dot_follows_a_first_dot_in_the_lower_space():
    head = _char(0xE0A4,(100,100,106.5,105.5))
    d1 = _char(0xE044,(108,99.1,110.3,101.4))
    d2 = _char(0xE044,(112,99.1,114.3,101.4))
    assert [c[0] for c in po._dots([head,d1,d2],5)[1:]] == [0xE1E7,0xE1E7]


@pytest.mark.parametrize('y',[94,108])
def test_a_staccato_above_or_below_the_head_is_not_an_augmentation(y):
    head = _char(0xE0A4,(100,100,106.5,105.5))
    # Articulation dot centred horizontally on the note, not to its right.
    dot = _char(0xE044,(102.2,y,104.5,y+2.3))
    assert po._dots([head,dot],5)[1][0] == 0xE044


def test_a_dot_a_full_space_below_the_head_is_not_an_augmentation():
    head = _char(0xE0A4,(100,100,106.5,105.5))
    dot = _char(0xE044,(108,96.6,110.3,98.9))
    assert po._dots([head,dot],5)[1][0] == 0xE044


def test_a_repeat_pair_beside_the_head_stays_a_repeat_pair():
    head = _char(0xE0A4,(100,100,106.5,105.5))
    lower = _char(0xE044,(108,99.1,110.3,101.4))
    upper = _char(0xE044,(108,104.1,110.3,106.4))
    assert [c[0] for c in po._dots([head,lower,upper],5)[1:]] == [0xE044,0xE044]


def test_a_lower_dot_far_to_the_right_keeps_its_original_class():
    head = _char(0xE0A4,(100,100,106.5,105.5))
    dot = _char(0xE044,(120,99.1,122.3,101.4))
    assert po._dots([head,dot],5)[1][0] == 0xE044


def test_a_staccato_below_the_next_note_cannot_lengthen_the_previous_note():
    # Fresh random Kreutzer: this point is right/down from the first head,
    # but horizontally centred below the next head. It belongs to that
    # note's articulation, rather than the first note's duration.
    previous = _char(0xE0A4,(2009.9100,1971.0400,2042.8199,1998.5699))
    following = _char(0xE0A4,(2064.4299,1996.0400,2097.3301,2023.5699))
    dot = _char(0xE044,(2075.9199,1967.3199,2085.8899,1977.2900))
    assert po._dots([previous,following,dot],25)[2][0] == 0xE044


def test_a_true_lower_dot_near_a_chord_keeps_its_augmentation_meaning():
    # Kreutzer page 4: another chord is nearby, but the dot is not centred
    # above/below one of its heads. It remains the preceding head's dot.
    head = _char(0xE0A4,(1317.7700,2183.7500,1350.6801,2211.2800))
    upper = _char(0xE0A4,(1317.7700,2271.2500,1350.6801,2298.7800))
    next_head = _char(0xE0A4,(1423.9000,2171.2500,1456.8101,2198.7800))
    dot = _char(0xE044,(1359.4399,2179.3400,1370.7700,2190.6899))
    assert po._dots([head,upper,next_head,dot],25)[3][0] == 0xE1E7
