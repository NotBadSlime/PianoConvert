"""Geometry-checked recovery of an opening metronome mark above a scanned staff."""
from __future__ import annotations

import re
from pathlib import Path
import xml.etree.ElementTree as ET


def _tempo_candidates(image, scale):
    import cv2
    import numpy as np

    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    # Thin anti-aliased staff lines can be lighter than the noteheads.
    rows = np.where((gray < 190).sum(axis=1) > gray.shape[1] * 0.5)[0]
    lines = []
    for y in rows:
        if not lines or y - lines[-1] > 3:
            lines.append(int(y))
        if len(lines) == 5:
            break
    if len(lines) != 5:
        return []
    gaps = np.diff(lines)
    d = float(np.median(gaps))
    if d < 3 or max(abs(gaps - d)) > d * 0.25:
        return []
    top = max(0, int(lines[0] - 6 * d))
    band = gray[top:lines[0], :]
    mask = (band < 160).astype('uint8')
    _, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    boxes = [(i, *map(int, s)) for i, s in enumerate(stats[1:], 1) if s[4] >= 4]
    # Engravers often print metronome symbols smaller than the staff notes.
    d *= scale
    bars = [b for b in boxes if .6*d <= b[3] <= 3.5*d and b[4] <= .5*d]
    crops = []
    for upper in bars:
        _, x, y, w, h, _ = upper
        lower = [b for b in bars if .25*d <= b[2]-y <= 1.6*d and abs(b[1]-x) < .2*d and abs(b[3]-w) < .2*d]
        if len(lower) != 1:
            continue
        beats = [b for b in boxes if x-4*d < b[1] < x and b[1]+b[3] < x
                 and 2.2*d < b[4] < 5*d and .6*d < b[3] < 1.8*d
                 and abs(b[2]+b[4] - (y+d)) < 1.5*d]
        if len(beats) != 1:
            continue
        ident, bx, by, bw, bh, _ = beats[0]
        beat = (labels[by:by+bh, bx:bx+bw] == ident).astype('uint8')
        contours, hierarchy = cv2.findContours(beat, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        hollow = hierarchy is not None and any(hh[3] >= 0 and cv2.contourArea(c) > max(3,.02*d*d)
                                               for c, hh in zip(contours, hierarchy[0]))
        unit = 2.0 if hollow else 1.0
        stem_x = np.where(beat[:max(1, int(bh-d))].sum(axis=0) > bh * .55)[0]
        if not len(stem_x) or max(stem_x) < bw - .35*d:
            continue  # a flag to the right of the stem changes the beat unit
        dots = [b for b in boxes if bx+bw <= b[1] < x
                and .15*d < b[4] < .6*d and .15*d < b[3] < .6*d
                and abs(b[2] - (by+bh-.5*d)) < .7*d]
        if len(dots) > 1:
            continue  # ambiguous/double-dotted unit is left unguessed
        if dots:
            unit *= 1.5
        digits = [b for b in boxes if x+w < b[1] < x+w+6*d and .9*d < b[4] < 4*d
                  and abs(b[2]-y) < 1.5*d]
        if not 1 <= len(digits) <= 3:
            continue
        right = max(b[1]+b[3] for b in digits)
        bottom = max(by+bh, *(b[2]+b[4] for b in digits))
        # The beat unit is established by geometry. OCR only the '=' and digits,
        # so a music glyph cannot depress otherwise reliable digit confidence.
        number_top = min(y, *(b[2] for b in digits))
        crop = image[top+max(0, number_top-5):top+min(band.shape[0], bottom+5), max(0,x-5):right+5]
        crops.append((crop,unit))
    return crops


def tempo_crops(image):
    """An unflagged note, optional dot, '=' and digits, with its quarter-note unit."""
    crops = {}
    for scale in (.45,.6,.8,1.0,1.2):
        for crop, unit in _tempo_candidates(image,scale):
            crops.setdefault((crop.shape,crop.tobytes(),unit),(crop,unit))
    return list(crops.values())


def quarter_tempo_crops(image):
    """Only undotted quarter marks; retained for callers that require that unit."""
    return [crop for crop,unit in tempo_crops(image) if unit == 1]


def opening_tempo(image_path: Path, reader=None) -> float | None:
    from PIL import Image
    import numpy as np

    with Image.open(image_path) as image:
        crops = tempo_crops(np.array(image.convert('RGB')))
    if not crops:
        return None
    if reader is None:
        from app.jianpu import _reader
        reader = _reader().ocr
    values = set()
    for crop, unit in crops:
        result = reader(crop, use_det=False, use_cls=False, use_rec=True)
        for txt, confidence in zip(getattr(result, 'txts', None) or (), getattr(result, 'scores', None) or ()):
            match = re.fullmatch(r'[^0-9=]{0,2}=(\d{2,3})', str(txt).replace(' ', '').replace('＝', '='))
            if match and confidence >= .85 and 20 <= int(match[1]) <= 300:
                values.add(float(match[1]) * unit)
    return values.pop() if len(values) == 1 else None


def supplement_opening_tempo(xml_path: Path, image_path: Path) -> bool:
    """Keep any existing tempo map; only fill a missing opening mark with strong evidence."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    if root.find('.//sound[@tempo]') is not None or root.find('.//metronome') is not None:
        return False
    bpm = opening_tempo(image_path)
    measure = root.find('part/measure')
    if bpm is None or measure is None:
        return False
    direction = ET.Element('direction', placement='above')
    metronome = ET.SubElement(ET.SubElement(direction, 'direction-type'), 'metronome')
    ET.SubElement(metronome, 'beat-unit').text = 'quarter'
    ET.SubElement(metronome, 'per-minute').text = f'{bpm:g}'
    ET.SubElement(direction, 'sound', tempo=f'{bpm:g}')
    at = next((i for i, child in enumerate(measure) if child.tag != 'attributes'), len(measure))
    measure.insert(at, direction)
    tree.write(xml_path, encoding='UTF-8', xml_declaration=True)
    return True


def has_tempo_mark(xml_path: Path) -> bool:
    root = ET.parse(xml_path).getroot()
    return root.find('.//sound[@tempo]') is not None or root.find('.//metronome') is not None
