import pytest

from app import pdf_vector as pv
from app.keyboard_score import arrange_events, notes_from_midi, seconds_to_quarters,midi_to_keyboard_text,parse_keyboard_text
from tests.test_pdf_vector import _grand_staff_page, _quarter_up, _glyph


TOP=pv.Staff(40,300,[700,695,690,685,680],0)
BOTTOM=pv.Staff(40,300,[650,645,640,635,630],1)


def bar(pitches, *, meter=(4,4), keys=None, tempo=0, start=False, end=False):
    events=[]
    for staff,voice in zip((TOP,BOTTOM),pitches):
        unit=meter[0]*4/meter[1]/len(voice)
        for i,pitch in enumerate(voice):
            head=pv.Head(100+20*i,690,1,False,staff,pitch=pitch)
            events.append(pv.Event(head.x,staff,unit,[head],base=unit))
    return pv.Bar(events,meter=meter,keys=keys or [],tempo=tempo,repeat_start=start,repeat_end=end)


def test_repeat_restores_the_source_tempo_before_a_later_change(tmp_path):
    bars=[bar([[60]*4],tempo=120),bar([[62]*4],start=True),bar([[64]*4],tempo=60,end=True),bar([[65]*4])]
    score=pv.build_score(bars,120)
    assert score.tempos == [(8,60),(12,120),(16,60)]
    notes,tempos,_=notes_from_midi(pv.write_midi(score,tmp_path/'repeat.mid'))
    assert max(n.end for n in notes) == pytest.approx(18,abs=1e-5)
    assert [(round(seconds_to_quarters(t,tempos)),round(bpm)) for t,bpm in tempos] == [(0,120),(8,60),(12,120),(16,60)]


def test_a_later_first_tempo_mark_does_not_change_the_unmarked_opening(tmp_path):
    score=pv.build_score([bar([[60]*4]),bar([[62]*4],tempo=60)],0)
    assert score.bpm == pv.DEFAULT_BPM
    assert not score.tempo_printed
    assert score.tempos == [(4,60)]
    notes,_,_=notes_from_midi(pv.write_midi(score,tmp_path/'later.mid'))
    assert min(n.start for n in notes if n.pitch==62) == pytest.approx(4*60/pv.DEFAULT_BPM,abs=1e-5)


def test_pdf_key_changes_and_local_accidentals_survive_midi_roundtrip(tmp_path):
    bars=[bar([[60,64,67,66]],keys=[{}]),
          bar([[67,69,71,66]],keys=[{3:1}]),
          bar([[60,64,67,65]],keys=[{}])]
    score=pv.build_score(bars,120)
    notes,_,_=notes_from_midi(pv.write_midi(score,tmp_path/'keys.mid'))
    assert [n.key_fifths for n in notes] == [0]*4+[1]*4+[0]*4
    arranged,info=arrange_events(notes)
    assert [n.orig for n in arranged] == [60,64,67,66,72,74,76,71,60,64,67,65]
    assert info.regional_shifts == (0,5)


def test_each_pdf_staff_keeps_its_own_key_signature(tmp_path):
    score=pv.build_score([bar([[60]*4,[55]*4],keys=[{},{3:1}])],120)
    notes,_,_=notes_from_midi(pv.write_midi(score,tmp_path/'staff-keys.mid'))
    assert {n.key_fifths for n in notes if n.pitch==60} == {0}
    assert {n.key_fifths for n in notes if n.pitch==55} == {1}


def test_repeat_restores_key_and_meter_at_the_same_boundary_as_tempo(tmp_path):
    bars=[bar([[60]*4],keys=[{}],tempo=120),
          bar([[62]*3],meter=(3,4),keys=[{}],start=True),
          bar([[66]*3],meter=(6,8),keys=[{3:1}],tempo=60,end=True)]
    score=pv.build_score(bars,120)
    notes,tempos,meters=notes_from_midi(pv.write_midi(score,tmp_path/'combined.mid'))
    assert [(m.quarters,m.numerator,m.denominator) for m in meters] == [(0,4,4),(4,3,4),(7,6,8),(10,3,4),(13,6,8)]
    assert [n.key_fifths for n in notes] == [0]*7+[1]*3+[0]*3+[1]*3
    assert score.tempos == [(7,60),(10,120),(13,60)]
    assert max(n.end for n in notes) == pytest.approx(11,abs=1e-5)


def test_meter_change_on_only_the_upper_staff_applies_to_the_whole_system():
    glyphs=[]
    lines=[]
    for x in (80,105,130,155,205,235,265):
        head,stem=_quarter_up(x,692.5)
        glyphs.append(head)
        lines.append(stem)
    glyphs += [_glyph(0x133,185,695),_glyph(0x134,185,685)]
    glyphs += [_glyph(pv.HEAD_WHOLE,80,637.5),_glyph(pv.HEAD_HALF,205,637.5),_glyph(pv.DOT,214,637.5,2,2)]
    lines.append(pv.Line(211,637.5,211,655,1))
    drawing=_grand_staff_page(glyphs,lines)
    state=pv.ReaderState()
    bars=[]
    for system in pv.group_systems(drawing,pv.find_staves(drawing)):
        bars.extend(pv.analyze_system(drawing,system,state))
    assert [b.meter for b in bars] == [(4,4),(3,4)]
    score=pv.build_score(bars,120)
    assert score.meters == [(0,(4,4)),(4,(3,4))]


def test_pdf_reader_does_not_backdate_a_second_page_tempo(monkeypatch,tmp_path):
    drawings=[]
    for _ in range(2):
        glyphs=[]
        lines=[]
        for x in (80,105,130,155):
            head,stem=_quarter_up(x,692.5)
            glyphs.append(head)
            lines.append(stem)
        glyphs.append(_glyph(pv.HEAD_WHOLE,80,637.5))
        drawings.append(_grand_staff_page(glyphs,lines))
    class Document:
        def __len__(self):return 2
        def __getitem__(self,index):return index
        def close(self):pass
    monkeypatch.setattr('pypdfium2.PdfDocument',lambda _:Document())
    monkeypatch.setattr(pv,'read_drawing',lambda index:drawings[index])
    monkeypatch.setattr(pv,'_tempo_marks',lambda d:[(80,720,60)] if d is drawings[1] else [])
    score=pv.read_vector_pdf(tmp_path/'later.pdf')
    assert score.bpm == pv.DEFAULT_BPM
    assert not score.tempo_printed
    assert score.tempos == [(4,60)]


@pytest.mark.parametrize('symbol,alter',[(pv.SHARP,1),(pv.FLAT,-1),(pv.DOUBLE_SHARP,2),(pv.DOUBLE_FLAT,-2)])
def test_accidental_applies_to_its_pitch_and_octave_until_the_barline(symbol,alter):
    glyphs=[_glyph(symbol,92,682.5,4,12)]
    lines=[]
    for x,y in [(100,682.5),(130,682.5),(160,700),(205,682.5)]:
        head,stem=_quarter_up(x,y)
        glyphs.append(head)
        lines.append(stem)
    drawing=_grand_staff_page(glyphs,lines)
    system=pv.group_systems(drawing,pv.find_staves(drawing))[0]
    bars=pv.analyze_system(drawing,system,pv.ReaderState())
    assert [h.pitch for b in bars for e in b.events for h in e.heads] == [65+alter,65+alter,77,65]
    assert all(b.keys[0]=={} for b in bars)


def test_natural_cancels_a_key_accidental_locally_and_resets_at_the_barline():
    glyphs=[_glyph(pv.SHARP,70,700,4,12),_glyph(pv.NATURAL,92,682.5,4,12)]
    lines=[]
    for x,y in [(100,682.5),(130,682.5),(160,700),(205,682.5)]:
        head,stem=_quarter_up(x,y)
        glyphs.append(head)
        lines.append(stem)
    drawing=_grand_staff_page(glyphs,lines)
    system=pv.group_systems(drawing,pv.find_staves(drawing))[0]
    bars=pv.analyze_system(drawing,system,pv.ReaderState())
    assert [h.pitch for b in bars for e in b.events for h in e.heads] == [65,65,78,66]
    assert [b.keys[0] for b in bars] == [{3:1},{3:1}]


def test_a_key_change_inside_a_bar_does_not_transpose_earlier_notes(tmp_path):
    source=bar([[60,64,67,66]],keys=[{3:1}])
    for index,event in enumerate(source.events):
        event.key_signature={} if index<2 else {3:1}
    score=pv.build_score([source],120)
    assert score.key_signatures == [(0,0,0),(2,0,1)]
    notes,_,_=notes_from_midi(pv.write_midi(score,tmp_path/'midbar-key.mid'))
    assert [n.key_fifths for n in notes] == [0,0,1,1]


def test_nonstandard_signature_does_not_inherit_a_previous_standard_key(tmp_path):
    score=pv.build_score([bar([[66]*4],keys=[{3:1}]),bar([[67]*4],keys=[{3:2}]),bar([[65]*4],keys=[{}])],120)
    notes,_,_=notes_from_midi(pv.write_midi(score,tmp_path/'nonstandard-key.mid'))
    assert [n.key_fifths for n in notes] == [1]*4+[None]*4+[0]*4


def test_fast_written_notes_are_not_merged_as_a_performed_chord(tmp_path):
    score=pv.VectorScore([pv.ScoreNote(p, q, q+.1,0) for p,q in zip((60,62,64,65),(0,.125,.25,.375))],
                         [(0,(4,4))],240,1)
    path=pv.write_midi(score,tmp_path/'fast.mid')
    assert [q for q,_ in parse_keyboard_text(midi_to_keyboard_text(path,'fast'))] == [0,.125,.25,.375]


def test_written_rising_notes_are_not_inferred_to_be_a_rolled_chord(tmp_path):
    score=pv.VectorScore([pv.ScoreNote(p,q,1,0) for p,q in zip((60,64,67),(0,1/12,2/12))],
                         [(0,(4,4))],120,1)
    path=pv.write_midi(score,tmp_path/'written-run.mid')
    text=midi_to_keyboard_text(path,'run')
    assert [q for q,_ in parse_keyboard_text(text)] == pytest.approx([0,1/12,2/12],abs=1e-6)
    assert '{' not in text.split('【键盘谱】')[1]


def test_pdf_thirteen_tuplet_attacks_survive_the_keyboard_score(tmp_path):
    score=pv.VectorScore([pv.ScoreNote(60,i/13,(i+1)/13,0) for i in range(13)],[(0,(4,4))],120,1)
    path=pv.write_midi(score,tmp_path/'thirteen.mid')
    text=midi_to_keyboard_text(path,'thirteen')
    assert '〈13〉' in text
    assert [q for q,_ in parse_keyboard_text(text)] == pytest.approx([i/13 for i in range(13)],abs=1e-6)


def test_fractional_quarter_bpm_change_is_not_dropped(tmp_path):
    score=pv.build_score([bar([[60]*4],tempo=50.25),bar([[62]*4],tempo=50)],0)
    assert score.bpm == 50.25
    assert score.tempos == [(4,50)]
