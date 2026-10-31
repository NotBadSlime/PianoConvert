from app.pdf_vector import Glyph, Line, Staff, _staff_by_ledgers


def test_complete_bass_ledger_chain_beats_closeness_to_treble_staff():
    top=Staff(40,300,[750,745,740,735,730],0)
    bottom=Staff(40,300,[702.5,697.5,692.5,687.5,682.5],1)
    head=Glyph(0x174,100,725,100,722,106,728,20,True)
    ledgers=[Line(98,y,110,y,1) for y in (707.5,712.5,717.5,722.5)]
    assert _staff_by_ledgers([top,bottom],head,ledgers,top) is bottom


def test_incomplete_ledger_chain_does_not_override_the_nearest_staff():
    top=Staff(40,300,[750,745,740,735,730],0)
    bottom=Staff(40,300,[702.5,697.5,692.5,687.5,682.5],1)
    head=Glyph(0x174,100,725,100,722,106,728,20,True)
    ledgers=[Line(98,y,110,y,1) for y in (707.5,717.5,722.5)]
    assert _staff_by_ledgers([top,bottom],head,ledgers,top) is top


def test_no_ledger_evidence_preserves_the_nearest_staff():
    top=Staff(40,300,[750,745,740,735,730],0)
    bottom=Staff(40,300,[700,695,690,685,680],1)
    head=Glyph(0x174,100,725,100,722,106,728,20,True)
    assert _staff_by_ledgers([top,bottom],head,[],top) is top


def test_overlapping_ledger_chains_do_not_prefer_the_farther_chord():
    top=Staff(40,300,[750,745,740,735,730],0)
    bottom=Staff(40,300,[700,695,690,685,680],1)
    head=Glyph(0x174,100,725,100,722,106,728,20,True)
    ledgers=[Line(98,y,110,y,1) for y in (705,710,715,720,725)]
    assert _staff_by_ledgers([top,bottom],head,ledgers,top) is top
