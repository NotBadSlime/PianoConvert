from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.omr_tempo import opening_tempo, quarter_tempo_crops, supplement_opening_tempo


def notation(*, hollow=False, dotted=False, flagged=False, equals=True):
    image = Image.new('RGB', (600, 330), 'white')
    draw = ImageDraw.Draw(image)
    for y in range(150, 231, 20):
        draw.line((20, y, 580, y), fill='black', width=1)
    draw.ellipse((80, 128, 102, 144), fill='white' if hollow else 'black', outline='black', width=3)
    draw.line((101, 72, 101, 137), fill='black', width=3)
    if dotted:
        draw.ellipse((107, 132, 113, 138), fill='black')
    if flagged:
        draw.polygon([(102,72),(120,90),(120,112),(113,96),(102,89)], fill='black')
    if equals:
        draw.rectangle((120, 112, 139, 114), fill='black')
        draw.rectangle((120, 121, 139, 123), fill='black')
    for x in (150, 173):
        draw.rectangle((x, 103, x+16, 134), fill='black')
    return image


def test_opening_quarter_tempo_requires_note_equals_and_confident_ocr(tmp_path):
    path = tmp_path/'page.png'
    notation().save(path)
    assert len(quarter_tempo_crops(np.array(notation()))) == 1
    good = lambda *a, **k: SimpleNamespace(txts=('J=67',), scores=(.96,))
    assert opening_tempo(path, good) == 67
    low = lambda *a, **k: SimpleNamespace(txts=('J=67',), scores=(.5,))
    assert opening_tempo(path, low) is None
    bare = lambda *a, **k: SimpleNamespace(txts=('67',), scores=(.99,))
    assert opening_tempo(path, bare) is None


@pytest.mark.parametrize('kwargs', [{'hollow':True}, {'dotted':True}, {'flagged':True}, {'equals':False}])
def test_does_not_assume_other_beat_units_or_page_numbers_are_quarter_tempo(kwargs):
    assert quarter_tempo_crops(np.array(notation(**kwargs))) == []


@pytest.mark.parametrize('kwargs,expected', [({'hollow':True},208),({'dotted':True},156),({'hollow':True,'dotted':True},312)])
def test_recovers_half_and_dotted_units_as_quarter_bpm(tmp_path,kwargs,expected):
    path = tmp_path/'unit.png'
    notation(**kwargs).save(path)
    reader = lambda *a,**k: SimpleNamespace(txts=('d=104',),scores=(.97,))
    assert opening_tempo(path,reader) == expected


def test_small_metronome_font_is_not_required_to_match_staff_size(tmp_path):
    page = Image.new('RGB',(600,330),'white')
    draw = ImageDraw.Draw(page)
    for y in range(150,231,20):
        draw.line((20,y,580,y),fill='black')
    symbol = notation().crop((75,65,195,145)).resize((72,48))
    page.paste(symbol,(75,98))
    path = tmp_path/'small.png'
    page.save(path)
    reader = lambda *a,**k: SimpleNamespace(txts=('J=160',),scores=(.97,))
    assert opening_tempo(path,reader) == 160


def test_flagged_beat_is_not_misread_as_quarter_or_half(tmp_path):
    path = tmp_path/'eighth.png'
    notation(flagged=True).save(path)
    reader = lambda *a,**k: SimpleNamespace(txts=('=104',),scores=(.99,))
    assert opening_tempo(path,reader) is None


def test_light_staff_lines_and_bold_touching_digits(tmp_path):
    page = Image.new('RGB',(600,330),'white')
    draw = ImageDraw.Draw(page)
    for y in range(150,231,20):
        draw.line((20,y,580,y),fill=(180,180,180))
    draw.ellipse((80,98,94,106),fill='black')
    draw.line((93,72,93,104),fill='black',width=2)
    draw.rectangle((105,89,131,94),fill='black')
    draw.rectangle((105,101,131,105),fill='black')
    draw.rectangle((145,82,156,115),fill='black')
    draw.rectangle((164,82,210,115),fill='black')  # two touching digits form one component
    path = tmp_path/'light-staff.png'
    page.save(path)
    reader = lambda *a,**k: SimpleNamespace(txts=('=160',),scores=(.99,))
    assert opening_tempo(path,reader) == 160


def test_add_tempo_at_start_before_notes_and_preserve_note_events(tmp_path, monkeypatch):
    path = tmp_path/'score.musicxml'
    path.write_text('<score-partwise><part id="P1"><measure number="1"><attributes><divisions>4</divisions></attributes><note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration></note></measure></part></score-partwise>')
    monkeypatch.setattr('app.omr_tempo.opening_tempo', lambda _: 67.)
    assert supplement_opening_tempo(path, Path('page.png'))
    root = ET.parse(path).getroot()
    assert root.find('.//sound').get('tempo') == '67'
    assert [e.tag for e in root.find('.//measure')] == ['attributes','direction','note']
    assert root.findtext('.//note/duration') == '4'
    before = path.read_bytes()
    assert not supplement_opening_tempo(path, Path('page.png'))
    assert path.read_bytes() == before
