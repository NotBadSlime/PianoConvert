import xml.etree.ElementTree as ET

from app.omr_repeats import normalize_homr_repeats


def test_homr_repeat_start_stays_in_its_measure_but_moves_to_left():
    root = ET.fromstring('''<score-partwise><part id="P1">
      <measure number="50"><barline location="right"><repeat direction="forward"/></barline>
        <note><rest/><duration>2</duration></note></measure>
      <measure number="57"><note><rest/><duration>16</duration></note>
        <barline location="right"><ending number="1" type="stop"/>
          <repeat direction="backward" times="2"/></barline></measure>
    </part></score-partwise>''')
    assert normalize_homr_repeats(root) == 1
    first, last = root.findall("part/measure")
    assert first.get("number") == "50"
    assert first.find("barline").get("location") == "left"
    assert last.find("barline/repeat").get("times") == "2"
    assert last.find("barline/ending").get("number") == "1"
    assert normalize_homr_repeats(root) == 0


def test_homr_backward_repeat_has_right_location_and_unknown_marks_stay_intact():
    root = ET.fromstring('''<score-partwise><part id="P1"><measure number="1">
      <barline location="left"><repeat direction="backward"/></barline>
      <barline location="middle"><bar-style>light-light</bar-style></barline>
    </measure></part></score-partwise>''')
    assert normalize_homr_repeats(root) == 1
    assert root.find("part/measure/barline").get("location") == "right"
    assert root.findall("part/measure/barline")[1].get("location") == "middle"


def test_fragment_keeps_complete_repeats_and_reports_an_unclosed_start(tmp_path):
    from app.omr_repeats import has_unclosed_repeat_warning
    from app.score_io import load_musicxml_playback

    root = ET.fromstring('''<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list><part id="P1">
    <measure number="1"><attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time></attributes>
      <barline location="left"><repeat direction="forward"/></barline><note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration></note></measure>
    <measure number="2"><note><pitch><step>D</step><octave>4</octave></pitch><duration>4</duration></note><barline location="right"><repeat direction="backward"/></barline></measure>
    <measure number="3"><barline location="left"><repeat direction="forward"/></barline><note><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration></note></measure>
    </part></score-partwise>''')
    assert normalize_homr_repeats(root) == 1
    assert len(root.findall('.//repeat')) == 2
    assert normalize_homr_repeats(root) == 0
    path = tmp_path/'fragment.musicxml'
    ET.ElementTree(root).write(path,encoding='utf-8',xml_declaration=True)
    assert has_unclosed_repeat_warning(path)
    score = load_musicxml_playback(path)
    assert [int(n.pitch.midi) for n in score.parts[0].recurse().notes] == [60,62,60,62,64]
