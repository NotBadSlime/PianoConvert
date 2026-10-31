"""Repair HOMR's repeat-barline placement before importing its MusicXML."""

from __future__ import annotations

import xml.etree.ElementTree as ET


def normalize_homr_repeats(root: ET.Element) -> int:
    """Put repeat starts on the left and repeat ends on the right of their measure.

    HOMR attaches a repeatStart symbol to the measure it introduces, but serializes
    its barline with location="right". music21 interprets that combination as an
    end repeat and can replay the whole prefix of the song several times. Keep
    the measure and all notation, including repeat counts and endings, intact.
    A fragment can end before its repeat end. Record those open starts and play
    the available fragment once, retaining every complete repeat. This repair
    is only for HOMR output, not arbitrary user-supplied MusicXML.
    """
    changed = 0
    for barline in root.iter("barline"):
        repeat = barline.find("repeat")
        if repeat is None:
            continue
        location = {"forward": "left", "backward": "right"}.get(repeat.get("direction"))
        if location is not None and barline.get("location") != location:
            barline.set("location", location)
            changed += 1
    unclosed = []
    for part in root.findall('part'):
        opened = []
        for measure in part.findall('measure'):
            for barline in measure.findall('barline'):
                repeat = barline.find('repeat')
                if repeat is None:
                    continue
                if repeat.get('direction') == 'forward':
                    opened.append((barline,repeat,measure.get('number','?')))
                elif repeat.get('direction') == 'backward' and opened:
                    opened.pop()
        for barline,repeat,number in opened:
            barline.remove(repeat)
            unclosed.append(number)
            changed += 1
    if unclosed:
        identification = root.find('identification')
        if identification is None:
            identification = ET.Element('identification')
            root.insert(0,identification)
        miscellaneous = identification.find('miscellaneous')
        if miscellaneous is None:
            miscellaneous = ET.SubElement(identification,'miscellaneous')
        field = ET.SubElement(miscellaneous,'miscellaneous-field',name='pianoconvert:unclosed-repeat')
        field.text = ','.join(dict.fromkeys(unclosed))
    return changed


def has_unclosed_repeat_warning(path) -> bool:
    return ET.parse(path).find(".//miscellaneous-field[@name='pianoconvert:unclosed-repeat']") is not None
