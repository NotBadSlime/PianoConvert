"""Read printed staff notation straight from the vector drawing of a PDF.

Many score sites export PDF by drawing every symbol with an embedded music font
(noteheads, clefs, accidentals, flags, rests ...) plus plain lines (staff lines, stems,
barlines, beams). When a PDF is built like that, the notes can be read exactly from the
drawing instead of guessing from pixels:

1. Staves: five equally spaced long horizontal lines. Staves joined by a vertical line at
   their left edge form one system.
2. Pitch: notehead height on its staff + clef + key signature + accidentals in the bar
   (+ ottava lines).
3. Rhythm: notehead type, stem, flags, beams, augmentation dots, tuplet numbers; rests.
   Within a bar every staff is laid out left to right; events of all staves sharing an x
   position sound together.
4. Barlines and repeat signs split bars; repeats and 1./2. endings are played out.

The result is written as MIDI so the rest of the app treats it like any other score.
"""
from __future__ import annotations

import bisect
import ctypes
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- glyph codes (MusicFont)

HEAD_BLACK = 0x174
HEAD_HALF = 0x17C
HEAD_WHOLE = 0x141
HEAD_BREVE = 0x140
HEADS = {HEAD_BLACK: 1.0, HEAD_HALF: 2.0, HEAD_WHOLE: 4.0, HEAD_BREVE: 8.0}
DOT = 0x1C7
CLEF_TREBLE = 0x180
CLEF_BASS = 0x181
CLEF_TREBLE_8VB = 0x186
CLEF_TREBLE_8VA = 0x185
CLEF_BASS_8VB = 0x184
CLEF_ALTO = 0x182
CLEFS = {
    CLEF_TREBLE: (30, 0),       # bottom line = E4 (diatonic index 4*7+2)
    CLEF_TREBLE_8VB: (23, 0),   # E3
    CLEF_TREBLE_8VA: (37, 0),   # E5
    CLEF_BASS: (18, 0),         # G2
    CLEF_BASS_8VB: (11, 0),     # G1
    CLEF_ALTO: (24, 0),         # F3
}
SHARP, FLAT, NATURAL, DOUBLE_FLAT, DOUBLE_SHARP, CAUTION_SHARP = 0x121, 0x122, 0x123, 0x125, 0x124, 0x126
ACCIDENTALS = {SHARP: 1, FLAT: -1, NATURAL: 0, DOUBLE_FLAT: -2, DOUBLE_SHARP: 2, CAUTION_SHARP: 1, 0x127: -1, 0x128: 0}
FLAGS = {0x149: 1, 0x14A: 2, 0x14B: 3, 0x14C: 4, 0x159: 1, 0x15A: 2, 0x15B: 3, 0x15C: 4}
RESTS = {0x161: 4.0, 0x162: 2.0, 0x163: 1.0, 0x164: 0.5, 0x165: 0.25, 0x166: 0.125, 0x167: 0.0625, 0x160: 8.0}
WHOLE_REST = 0x161
TIME_DIGITS = {0x130 + d: d for d in range(10)}
COMMON_TIME = 0x18A
CUT_TIME = 0x18B
OTTAVA = {0x1B4: 12, 0x1B5: -12, 0x1B6: 24, 0x1B7: -24}
ARPEGGIO = 0x1AC  # one piece of the wavy arpeggio line
MUSIC_FONT = "MusicFont"
DEFAULT_BPM = 90.0  # used when the score prints no tempo
MIN_TREMOLO = 0.125  # quarters: tremolos faster than 32nd notes are played as 32nd notes
GRACE_STEP = 0.125   # quarters: a grace note is played as a 32nd just before its note
TIE_GAP = 4.0        # quarters: a tie never reaches back further than about a bar

STEP_SEMITONES = [0, 2, 4, 5, 7, 9, 11]
_DEBUG = bool(__import__("os").environ.get("PDF_VECTOR_DEBUG"))


class VectorScoreError(ValueError):
    pass


# ---------------------------------------------------------------- raw drawing


@dataclass
class Glyph:
    code: int
    x: float          # origin
    y: float
    l: float          # box
    b: float
    r: float
    t: float
    size: float
    music: bool
    text: str = ""

    @property
    def cx(self) -> float:
        return (self.l + self.r) / 2

    @property
    def cy(self) -> float:
        return (self.b + self.t) / 2


@dataclass
class Line:
    x0: float
    y0: float
    x1: float
    y1: float
    width: float

    @property
    def vertical(self) -> bool:
        return abs(self.x1 - self.x0) < 0.3 and abs(self.y1 - self.y0) > 0.3

    @property
    def horizontal(self) -> bool:
        return abs(self.y1 - self.y0) < 0.3 and abs(self.x1 - self.x0) > 0.3

    @property
    def xmin(self) -> float:
        return min(self.x0, self.x1)

    @property
    def xmax(self) -> float:
        return max(self.x0, self.x1)

    @property
    def ymin(self) -> float:
        return min(self.y0, self.y1)

    @property
    def ymax(self) -> float:
        return max(self.y0, self.y1)

    def y_at(self, x: float) -> float:
        if abs(self.x1 - self.x0) < 1e-6:
            return self.y0
        return self.y0 + (self.y1 - self.y0) * (x - self.x0) / (self.x1 - self.x0)


@dataclass
class Curve:
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class PageDrawing:
    glyphs: list[Glyph]
    lines: list[Line]
    curves: list[Curve]
    width: float
    height: float
    smufl: bool = False  # drawn by MuseScore or another SMuFL program


def _font_name(tp, index: int) -> str:
    import pypdfium2.raw as raw

    buf = ctypes.create_string_buffer(64)
    raw.FPDFText_GetFontInfo(tp.raw, index, buf, 64, None)
    return buf.value.decode("latin-1", errors="replace")


# ---------------------------------------------------------------- SMuFL fonts (MuseScore ...)
#
# MuseScore and other notation programs draw with a SMuFL music font (Leland, Bravura ...)
# through a scaled coordinate system, and draw beams as filled shapes. Such a page is
# translated into the same drawing the reader knows: glyphs get their MusicFont codes and
# sizes (a normal notehead = 20), line widths are taken through the scaling, and beams,
# repeat dots and tremolo strokes become the lines the MusicFont pages use for them.

SMUFL_FONTS = ("Leland", "Bravura", "Petaluma", "Emmentaler", "MScore", "Gootville", "MuseJazz", "Finale Maestro", "FinaleMaestro", "Leipzig", "Sebastian")
SMUFL_CODES = {
    0xE0A4: HEAD_BLACK, 0xE0A3: HEAD_HALF, 0xE0A2: HEAD_WHOLE, 0xE0A0: HEAD_BREVE, 0xE0A1: HEAD_BREVE,
    0xE1E7: DOT,
    0xE050: CLEF_TREBLE, 0xE07A: CLEF_TREBLE, 0xE052: CLEF_TREBLE_8VB, 0xE053: CLEF_TREBLE_8VA,
    0xE062: CLEF_BASS, 0xE07C: CLEF_BASS, 0xE064: CLEF_BASS_8VB, 0xE05C: CLEF_ALTO, 0xE07B: CLEF_ALTO,
    0xE262: SHARP, 0xE260: FLAT, 0xE261: NATURAL, 0xE263: DOUBLE_SHARP, 0xE264: DOUBLE_FLAT,
    0xE240: 0x149, 0xE242: 0x14A, 0xE244: 0x14B, 0xE246: 0x14C,   # flags, stem up
    0xE241: 0x159, 0xE243: 0x15A, 0xE245: 0x15B, 0xE247: 0x15C,   # flags, stem down
    0xE4E2: 0x160, 0xE4E3: 0x161, 0xE4E4: 0x162, 0xE4E5: 0x163, 0xE4E6: 0x164, 0xE4E7: 0x165,
    0xE4E8: 0x166, 0xE4E9: 0x167, 0xE4F4: 0x161, 0xE4F5: 0x162,  # rests (also outside the staff)
    0xE08A: COMMON_TIME, 0xE08B: CUT_TIME,
    0xEAA9: ARPEGGIO, 0xEAAA: ARPEGGIO, 0xEAAB: ARPEGGIO, 0xEAAC: ARPEGGIO,
    0xECA5: 0x143, 0xECA3: 0x144, 0xECA7: 0x145,                  # metronome-mark notes
    0xE510: 0x1B4, 0xE511: 0x1B4, 0xE512: 0x1B5, 0xE51C: 0x1B5, 0xE514: 0x1B6, 0xE515: 0x1B6, 0xE516: 0x1B7,
}
SMUFL_CODES.update({0xE080 + d: 0x130 + d for d in range(10)})   # time-signature digits
SMUFL_HEADS = (0xE0A4, 0xE0A3, 0xE0A2)
SMUFL_REPEAT_DOT = 0xE044
SMUFL_TREMOLO = {0xE220: 1, 0xE221: 2, 0xE222: 3, 0xE223: 4, 0xE224: 5}
# text sizes are scaled so that MuseScore's tuplet numbers land where MusicFont pages put theirs
SMUFL_TEXT_SCALE = 1.6


def _is_smufl(font: str) -> bool:
    return any(name in font for name in SMUFL_FONTS)


def _smufl_glyphs(chars: list[tuple]) -> tuple[list[Glyph], list[Line]]:
    head_sizes = Counter(round(c[7], 1) for c in chars if _is_smufl(c[8]) and c[0] in SMUFL_HEADS)
    base = head_sizes.most_common(1)[0][0] if head_sizes else 20.0
    k = 20.0 / base if base > 0 else 1.0
    glyphs: list[Glyph] = []
    lines: list[Line] = []
    for code, ox, oy, l, b, r, t, size, font, ch in chars:
        if not _is_smufl(font):
            glyphs.append(Glyph(code, ox, oy, l, b, r, t, size * k * SMUFL_TEXT_SCALE, False, ch))
            continue
        if code == SMUFL_REPEAT_DOT:
            cx, cy = (l + r) / 2, (b + t) / 2
            lines.append(Line(cx, cy, cx, cy, 4.0))
            continue
        if code in SMUFL_TREMOLO:
            n = SMUFL_TREMOLO[code]
            for i in range(n):
                y = t - (t - b) * (i + 0.5) / n
                lines.append(Line(l, y, r, y, 5.0))
            continue
        mapped = SMUFL_CODES.get(code)
        if mapped is not None:
            glyphs.append(Glyph(mapped, ox, oy, l, b, r, t, size * k, True, ch))
    return glyphs, lines


def _smufl_shape(pts: list[tuple[int, float, float]], fill: bool, stroke: bool, width: float, lines: list[Line], curves: list[Curve]) -> None:
    """Straight pieces of a stroked path; beams, stems and thick barlines drawn as filled
    shapes; slurs and ties (filled shapes with curves)."""
    bezier = any(p[0] == 1 for p in pts)
    if stroke and not fill:
        prev = None
        for kind, x, y in pts:
            if kind == 0 and prev is not None:
                lines.append(Line(prev[0], prev[1], x, y, width))
            prev = (x, y)
        return
    if not fill:
        return
    if bezier:
        if len(pts) >= 4:
            curves.append(Curve(pts[0][1], pts[0][2], pts[3][1], pts[3][2]))
        return
    xs = [p[1] for p in pts]
    ys = [p[2] for p in pts]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    if len(pts) < 4 or w > 400 or w < 0.05 and h < 0.05:
        return  # the page background, or nothing
    if h > 2.5 * w:
        cx = (min(xs) + max(xs)) / 2
        lines.append(Line(cx, min(ys), cx, max(ys), w))   # a stem or a thick barline
        return
    left = sorted(pts, key=lambda p: p[1])[:2]
    right = sorted(pts, key=lambda p: p[1])[-2:]
    yl = sum(p[2] for p in left) / 2
    yr = sum(p[2] for p in right) / 2
    lines.append(Line(min(xs), yl, max(xs), yr, 5.0))         # a beam


def _smufl_object_chars(page, tp) -> list[tuple]:
    """Music glyphs of a SMuFL page, one per text object.

    PDFium's text page drops a glyph that repeats the previous one at an overlapping spot
    (it takes it for fake bold), which loses the second notehead of a chord in a third.
    The text objects are all there: such an object reads as empty text, and it is the
    same glyph as the object before it when its box has the same size.
    """
    import pypdfium2.raw as raw

    out: list[tuple] = []
    last: dict[str, tuple[int, float, float]] = {}
    for obj in page.get_objects(max_depth=5):
        if obj.type != raw.FPDF_PAGEOBJ_TEXT:
            continue
        buf = ctypes.create_string_buffer(256)
        raw.FPDFFont_GetBaseFontName(raw.FPDFTextObj_GetFont(obj.raw), buf, 256)
        font = buf.value.decode("latin-1", errors="replace")
        if not _is_smufl(font):
            continue
        l, b, r, t = (ctypes.c_float() for _ in range(4))
        raw.FPDFPageObj_GetBounds(obj.raw, ctypes.byref(l), ctypes.byref(b), ctypes.byref(r), ctypes.byref(t))
        box = (l.value, b.value, r.value, t.value)
        matrix = raw.FS_MATRIX()
        raw.FPDFPageObj_GetMatrix(obj.raw, ctypes.byref(matrix))
        size = ctypes.c_float()
        raw.FPDFTextObj_GetFontSize(obj.raw, ctypes.byref(size))
        n = raw.FPDFTextObj_GetText(obj.raw, tp.raw, None, 0)
        text_buf = ctypes.create_string_buffer(n * 2 + 2)
        raw.FPDFTextObj_GetText(obj.raw, tp.raw, ctypes.cast(text_buf, ctypes.POINTER(ctypes.c_ushort)), n * 2 + 2)
        text = text_buf.raw[: max(0, n * 2 - 2)].decode("utf-16-le", errors="replace")
        codes = [ord(c) for c in text if ord(c) > 0x20]
        w, h = box[2] - box[0], box[3] - box[1]
        if codes:
            code = codes[0]
        else:
            prev = last.get(font)
            if prev is None or abs(prev[1] - w) > 0.15 or abs(prev[2] - h) > 0.15:
                continue
            code = prev[0]
        last[font] = (code, w, h)
        # small notes (grace, cue) are drawn through a smaller matrix, not a smaller font size
        scale = math.sqrt(abs(matrix.a * matrix.d - matrix.b * matrix.c)) or 1.0
        out.append((code, matrix.e, matrix.f, box[0], box[1], box[2], box[3], size.value * scale, font, chr(code)))
    return out


def _outline_drawing(page, width: float, height: float) -> PageDrawing | None:
    """A page without text whose symbols are all outlines (see app.pdf_outline), read like a
    MuseScore page: the recognised shapes are its SMuFL glyphs, the remaining filled shapes
    its stems, beams, barlines and slurs, the strokes its staff and ledger lines."""
    try:
        from app.pdf_outline import outline_page
    except Exception:  # noqa: BLE001
        return None
    found = outline_page(page)
    if found is None:
        return None
    chars, left, paths, space = found
    if sum(1 for c in chars if c[0] in SMUFL_HEADS) < 4:
        return None
    # everything is brought to the reader's scale (a staff space of 5) here: these pages are
    # drawn at any scale, and their line widths with them
    k = 5.0 / space
    chars = [(c[0], c[1] * k, c[2] * k, c[3] * k, c[4] * k, c[5] * k, c[6] * k, c[7] * k) + tuple(c[8:]) for c in chars]
    paths = [([(kind, x * k, y * k) for kind, x, y in pts], fill, stroke, w * k, scale) for pts, fill, stroke, w, scale in paths]
    left = [([[(x * k, y * k) for x, y in c] for c in cs], tuple(v * k for v in box)) for cs, box in left]
    width, height = width * k, height * k
    # text sizes: MuseScore pages report them before the page scale; here they are drawn
    # sizes, which _smufl_glyphs scales like the other SMuFL pages' text
    glyphs, lines = _smufl_glyphs(chars)
    curves: list[Curve] = []
    for pts, fill, stroke, w, scale in paths:
        if fill and not (stroke and len(pts) == 2):
            continue
        # strokes (a two-point line drawn with fill set too is still a stroked line)
        _smufl_shape(pts, False, True, w * scale, lines, curves)
    for cs, box in left:
        # one filled shape: its outline as the path _smufl_shape reads
        outline = [(float(x), float(y)) for x, y in cs[0]]
        bw, bh = box[2] - box[0], box[3] - box[1]
        # a tie between two close notes can be little wider than a notehead (staff space 5)
        bezier = len(outline) > 12 and bw > 4 and bh < 0.4 * bw
        if bezier:
            # a slur or tie: a thin crescent; its tips are the leftmost and rightmost points
            a = min(outline, key=lambda p: p[0])
            b = max(outline, key=lambda p: p[0])
            curves.append(Curve(a[0], a[1], b[0], b[1]))
            continue
        # stems, barlines and beams are plain quadrilaterals; anything else left over is text
        # drawn as outlines (lyrics, chord names, titles) and must not pass for a beam
        if len(cs) > 1 or len(outline) > 6:
            continue
        if bh <= 2.5 * bw and bw < 4.0:
            continue  # too short for a beam
        pts = [(0, x, y) for x, y in outline]
        _smufl_shape(pts, True, False, 0.0, lines, curves)
    lines = _join_staff_segments(lines)
    return _normalized(PageDrawing(glyphs, lines, curves, width, height, True))


def read_drawing(page) -> PageDrawing:
    import pypdfium2.raw as raw

    width, height = page.get_size()
    tp = page.get_textpage()
    chars: list[tuple] = []
    for i in range(tp.count_chars()):
        ch = tp.get_text_range(i, 1)
        if not ch or not ch.strip():
            continue
        l, b, r, t = tp.get_charbox(i)
        ox, oy = ctypes.c_double(), ctypes.c_double()
        raw.FPDFText_GetCharOrigin(tp.raw, i, ctypes.byref(ox), ctypes.byref(oy))
        chars.append((ord(ch[0]), ox.value, oy.value, l, b, r, t, raw.FPDFText_GetFontSize(tp.raw, i), _font_name(tp, i), ch))
    smufl = any(_is_smufl(c[8]) for c in chars) and not any(MUSIC_FONT in c[8] for c in chars)
    if not chars:
        outlined = _outline_drawing(page, width, height)
        if outlined is not None:
            if page_close := getattr(tp, "close", None):
                page_close()
            return outlined
    if smufl:
        music = _smufl_object_chars(page, tp)
        # the text page reports font sizes before the page's scaling; measure that scaling
        # on the noteheads and apply it to the text too
        raw_heads = Counter(round(c[7], 1) for c in chars if _is_smufl(c[8]) and c[0] in SMUFL_HEADS)
        eff_heads = Counter(round(c[7], 2) for c in music if c[0] in SMUFL_HEADS)
        text_scale = 1.0
        if raw_heads and eff_heads:
            text_scale = eff_heads.most_common(1)[0][0] / raw_heads.most_common(1)[0][0]
        texts = [c[:7] + (c[7] * text_scale,) + c[8:] for c in chars if not _is_smufl(c[8])]
        chars = texts + music
    lines: list[Line] = []
    curves: list[Curve] = []
    if smufl:
        glyphs, lines = _smufl_glyphs(chars)
    else:
        glyphs = [Glyph(code, ox, oy, l, b, r, t, size, MUSIC_FONT in font, ch) for code, ox, oy, l, b, r, t, size, font, ch in chars]
    for obj in page.get_objects(max_depth=4):
        if obj.type != 2:  # path
            continue
        matrix = raw.FS_MATRIX()
        raw.FPDFPageObj_GetMatrix(obj.raw, ctypes.byref(matrix))
        count = raw.FPDFPath_CountSegments(obj.raw)
        pts = []
        for k in range(count):
            seg = raw.FPDFPath_GetPathSegment(obj.raw, k)
            x, y = ctypes.c_float(), ctypes.c_float()
            raw.FPDFPathSegment_GetPoint(seg, ctypes.byref(x), ctypes.byref(y))
            px = matrix.a * x.value + matrix.c * y.value + matrix.e
            py = matrix.b * x.value + matrix.d * y.value + matrix.f
            pts.append((raw.FPDFPathSegment_GetType(seg), px, py))
        fill, stroke = ctypes.c_int(), ctypes.c_int()
        raw.FPDFPath_GetDrawMode(obj.raw, ctypes.byref(fill), ctypes.byref(stroke))
        w = ctypes.c_float()
        raw.FPDFPageObj_GetStrokeWidth(obj.raw, ctypes.byref(w))
        if smufl:
            scale = math.sqrt(abs(matrix.a * matrix.d - matrix.b * matrix.c)) or 1.0
            _smufl_shape(pts, bool(fill.value), bool(stroke.value), float(w.value) * scale, lines, curves)
            continue
        if count == 2 and stroke.value and pts[1][0] == 0:
            lines.append(Line(pts[0][1], pts[0][2], pts[1][1], pts[1][2], float(w.value)))
        elif fill.value and count >= 4 and any(p[0] == 1 for p in pts):
            # slur / tie: a filled crescent; its two tips are the first point and the end
            # of the first bezier
            curves.append(Curve(pts[0][1], pts[0][2], pts[3][1], pts[3][2]))
    page_close = getattr(tp, "close", None)
    if page_close:
        page_close()
    if smufl:
        lines = _join_staff_segments(lines)
    return _normalized(PageDrawing(glyphs, lines, curves, width, height, smufl))


def _page_staff_gap(lines: list[Line]) -> float | None:
    """The distance between the lines of the staves of a page, when staves are on it:
    five long horizontal lines evenly spaced (the commonest such distance)."""
    long_lines = [ln for ln in lines if ln.horizontal and ln.width <= 1.6 and (ln.xmax - ln.xmin) >= 25]
    by_extent: dict[tuple[int, int], list[float]] = defaultdict(list)
    for ln in long_lines:
        by_extent[(int(round(ln.xmin)), int(round(ln.xmax)))].append(ln.y0)
    votes: Counter = Counter()
    for ys in by_extent.values():
        ys = sorted(set(round(y, 1) for y in ys), reverse=True)
        for i in range(len(ys) - 4):
            group = ys[i : i + 5]
            gaps = [group[k] - group[k + 1] for k in range(4)]
            mean = sum(gaps) / 4
            if mean >= 0.3 and max(gaps) - min(gaps) <= 0.12 * mean + 0.1:
                votes[round(mean, 2)] += 1
    if not votes:
        return None
    return votes.most_common(1)[0][0]


def _normalized(drawing: PageDrawing) -> PageDrawing:
    """A page printed smaller (or larger) than usual - a generator that shrinks a crowded page
    to fit it on the sheet - is scaled so that a staff space is 5 again, which is what every
    size in the reader assumes. Pages that are as usual are returned as they are."""
    gap = _page_staff_gap(drawing.lines)
    if gap is None or 4.0 <= gap <= 7.0 or not (0.3 <= gap < 4.0 or 7.0 < gap <= 40.0):
        return drawing
    k = 5.0 / gap
    for g in drawing.glyphs:
        g.x *= k
        g.y *= k
        g.l *= k
        g.b *= k
        g.r *= k
        g.t *= k
        if drawing.smufl:
            g.size *= k  # sizes of a MuseScore page follow its scaling; the others' are the font's own
    for ln in drawing.lines:
        ln.x0 *= k
        ln.y0 *= k
        ln.x1 *= k
        ln.y1 *= k
        if drawing.smufl:
            ln.width *= k  # widths of a MuseScore page are in page units; the others' are not scaled
    for cv in drawing.curves:
        cv.x0 *= k
        cv.y0 *= k
        cv.x1 *= k
        cv.y1 *= k
    drawing.width *= k
    drawing.height *= k
    return drawing

def _join_staff_segments(lines: list[Line]) -> list[Line]:
    """MuseScore draws staff lines bar by bar; join pieces that continue each other."""
    flat = sorted((ln for ln in lines if ln.horizontal and ln.width <= 1.6), key=lambda ln: (round(ln.y0, 1), ln.xmin))
    others = [ln for ln in lines if not (ln.horizontal and ln.width <= 1.6)]
    joined: list[Line] = []
    for ln in flat:
        last = joined[-1] if joined else None
        if (
            last is not None
            and abs(last.y0 - ln.y0) <= 0.05
            and abs(last.width - ln.width) <= 0.05
            and -0.5 <= ln.xmin - last.xmax <= 0.6
        ):
            joined[-1] = Line(last.xmin, last.y0, max(last.xmax, ln.xmax), last.y0, last.width)
        else:
            joined.append(Line(ln.xmin, ln.y0, ln.xmax, ln.y0, ln.width))
    return joined + others


def is_vector_score(pdf: Path, pages: int = 2) -> bool:
    """True when the PDF draws notes with an embedded music font on real staff lines."""
    try:
        import pypdfium2 as pdfium
    except Exception:  # noqa: BLE001
        return False
    try:
        doc = pdfium.PdfDocument(str(pdf))
    except Exception:  # noqa: BLE001
        return False
    try:
        for index in range(min(pages, len(doc))):
            drawing = read_drawing(doc[index])
            heads = sum(1 for g in drawing.glyphs if g.music and g.code in HEADS)
            if heads >= 8 and find_staves(drawing):
                return True
        return False
    finally:
        doc.close()


# ---------------------------------------------------------------- layout


@dataclass
class Staff:
    x0: float
    x1: float
    lines: list[float]  # y of the 5 lines, top first
    index: int = 0      # position inside its system (0 = top)

    @property
    def top(self) -> float:
        return self.lines[0]

    @property
    def bottom(self) -> float:
        return self.lines[-1]

    @property
    def space(self) -> float:
        return (self.top - self.bottom) / 4

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2

    def step_of(self, y: float) -> int:
        """Half-spaces above the bottom line."""
        return int(round((y - self.bottom) / (self.space / 2)))


def find_staves(drawing: PageDrawing) -> list[Staff]:
    long_lines = [
        ln for ln in drawing.lines
        if ln.horizontal and ln.width <= 1.6 and (ln.xmax - ln.xmin) >= 60
    ]
    by_extent: dict[tuple[int, int], list[float]] = defaultdict(list)
    for ln in long_lines:
        key = (int(round(ln.xmin)), int(round(ln.xmax)))
        by_extent[key].append(ln.y0)
    staves: list[Staff] = []
    for (x0, x1), ys in by_extent.items():
        ys = sorted(set(round(y, 1) for y in ys), reverse=True)
        used: set[int] = set()
        for i in range(len(ys)):
            if i in used or i + 4 >= len(ys):
                continue
            group = ys[i : i + 5]
            gaps = [group[k] - group[k + 1] for k in range(4)]
            if min(gaps) < 2.5 or max(gaps) - min(gaps) > 0.6 or max(gaps) > 12:
                continue
            staves.append(Staff(float(x0), float(x1), list(group)))
            used.update(range(i, i + 5))
    staves.sort(key=lambda s: (-s.top, s.x0))
    return staves


def group_systems(drawing: PageDrawing, staves: list[Staff]) -> list[list[Staff]]:
    """Staves joined by a vertical line at their left edge belong to one system."""
    systems: list[list[Staff]] = []
    verticals = [ln for ln in drawing.lines if ln.vertical]
    i = 0
    while i < len(staves):
        group = [staves[i]]
        j = i + 1
        while j < len(staves):
            a, b = group[-1], staves[j]
            joined = any(
                abs(v.x0 - a.x0) <= 2.5 and abs(b.x0 - a.x0) <= 3
                and v.ymax >= a.bottom - 1 and v.ymin <= b.top + 1
                for v in verticals
            )
            if not joined:
                break
            group.append(b)
            j += 1
        for k, st in enumerate(group):
            st.index = k
        systems.append(group)
        i = j
    return systems


# ---------------------------------------------------------------- symbols on a staff


@dataclass
class Head:
    x: float
    y: float
    kind: float        # base length from the head shape (1 = black)
    grace: bool
    staff: Staff
    step: int = 0
    pitch: int = 0
    tied: bool = False  # continuation of a tie: not played again
    stem: "Stem | None" = None
    dots: int = 0
    arpeggio: bool = False  # under an arpeggio sign: the chord is rolled from low to high


@dataclass
class Tuplet:
    """Notes and rests under one tuplet number. ``ratio`` is how much of its written length
    each member lasts (2/3 for a triplet); a group that does not add up to anything sensible
    with the usual ratio can be re-read with another one (see ``TUPLET_RATIOS``)."""
    n: int
    ratio: float
    default: float = 0.0

    def __post_init__(self) -> None:
        self.default = self.default or self.ratio


# tuplet ratios tried, after the usual one, for a group that makes its bar too long or short
TUPLET_RATIOS = (2 / 3, 4 / 5, 4 / 7, 8 / 9, 4 / 6, 3 / 4, 6 / 7, 1.0)


def _tuplet_numbers(glyphs: list[Glyph]) -> list[Glyph]:
    """Join neighbouring digits before interpreting a tuplet label (13 is not 3)."""
    digits = [g for g in glyphs if not g.music and g.text.isascii() and g.text.isdecimal()
              and 13 <= g.size <= 17.5]
    used: set[int] = set()
    order={id(g):i for i,g in enumerate(glyphs)}
    result = []
    for first in sorted(digits,key=lambda g:(g.l,-g.y)):
        if id(first) in used:
            continue
        run = [first]
        used.add(id(first))
        while True:
            last = run[-1]
            following = [g for g in digits if id(g) not in used and abs(g.y-first.y)<1
                         and abs(g.size-first.size)<.5 and -.2 <= g.l-last.r <= max(1,.15*first.size)]
            if not following:
                break
            following.sort(key=lambda g:g.l)
            run.append(following[0])
            used.add(id(following[0]))
        if len(run)==1:
            result.append((order[id(first)],first))
        else:
            result.append((min(order[id(g)] for g in run),
                           Glyph(-1,first.x,first.y,min(g.l for g in run),min(g.b for g in run),
                                 max(g.r for g in run),max(g.t for g in run),first.size,False,
                                 ''.join(g.text for g in run))))
    # Preserve the engraver's order for independent labels. Reordering them
    # changes which of two nearby tuplet groups claims an overlapping beam.
    return [g for _,g in sorted(result,key=lambda item:item[0])]


@dataclass
class Stem:
    line: Line
    up: bool
    heads: list[Head] = field(default_factory=list)
    beams: int = 0
    flags: int = 0
    tuplet: float = 1.0
    group: "Tuplet | None" = None
    beam_group: int = 0  # stems joined by beams share it (0: not beamed)


@dataclass
class Event:
    x: float
    staff: Staff
    length: float          # quarters
    heads: list[Head]      # empty for rests
    grace: bool = False
    full_bar_rest: bool = False
    t: float = 0.0
    trem_partner: "Event | None" = None  # two-note tremolo: alternate with this event
    trem_second: bool = False            # the second note of a two-note tremolo
    repeat_sub: float = 0.0              # tremolo: repeat (or alternate) every this many quarters
    y: float = 0.0                       # height (rest centre / mean notehead)
    base: float = 0.0                    # written length, before any tuplet ratio
    group: "Tuplet | None" = None        # the tuplet this event belongs to
    beam_group: int = 0                  # events whose stems are joined by beams share it
    key_signature: dict[int, int] | None = None  # signature at this event, excluding local accidentals

    def set_ratio(self) -> None:
        """Length from the written length and the tuplet ratio."""
        if self.group is not None and self.base:
            self.length = self.base * self.group.ratio


def _nearest_staff(staves: list[Staff], y: float) -> Staff:
    def dist(st: Staff) -> float:
        if st.bottom <= y <= st.top:
            return 0.0
        return min(abs(y - st.bottom), abs(y - st.top))

    return min(staves, key=dist)


def _diatonic_to_midi(diatonic: int, alter: int) -> int:
    octave, step = divmod(diatonic, 7)
    return 12 * (octave + 1) + STEP_SEMITONES[step] + alter


def _staff_by_ledgers(system: list[Staff], head: "Glyph", ledgers: list[Line], nearest: Staff) -> Staff:
    """Staff of a notehead outside the staff lines, judged by its ledger lines."""
    y = head.y
    if any(st.bottom - 0.3 <= y <= st.top + 0.3 for st in system):
        return nearest
    lower = [st for st in system if st.top < y]     # staves below the head
    upper = [st for st in system if st.bottom > y]  # staves above the head
    if not lower or not upper:
        return nearest
    cands = [min(lower, key=lambda st: y - st.top), min(upper, key=lambda st: st.bottom - y)]
    cx = head.x + 3
    nearest_ledger_y = nearest.top + nearest.space if y > nearest.top else nearest.bottom - nearest.space
    nearest_has_ledger = any(abs(ln.y0-nearest_ledger_y) <= .7 and ln.xmin-1 <= cx <= ln.xmax+1
                             for ln in ledgers)

    def score(st: Staff) -> tuple[float, int]:
        sp = st.space
        dist = (y - st.top) if y > st.top else (st.bottom - y)
        if dist < 1.25 * sp:
            return 1.0, 0  # right next to the staff: no ledger line needed
        n = int(dist / sp + 0.3)
        ys = [st.top + sp * k for k in range(1, n + 1)] if y > st.top else [st.bottom - sp * k for k in range(1, n + 1)]
        found = sum(1 for ly in ys if any(abs(ln.y0 - ly) <= 0.7 and ln.xmin - 1 <= cx <= ln.xmax + 1 for ln in ledgers))
        # A complete chain growing out of the farther staff is positive
        # evidence. Merely being in the space beside the nearer staff is not:
        # both used to score 1, so a high bass note could be read in treble.
        # If both staves have supporting ledgers (e.g. overlapping chords),
        # their chain lengths cannot decide which chord this head belongs to.
        evidence = found if found == len(ys) and found >= 2 and not nearest_has_ledger else 0
        return found / len(ys), evidence

    return max(cands, key=lambda st: (score(st), st is nearest))


# ---------------------------------------------------------------- page analysis


@dataclass
class Barline:
    x: float
    repeat_start: bool = False
    repeat_end: bool = False
    final: bool = False


@dataclass
class Bar:
    events: list[Event]
    length: float = 0.0
    meter: tuple[int, int] = (4, 4)
    repeat_start: bool = False
    repeat_end: bool = False
    volta: int = 0
    final: bool = False
    page: int = 0
    system: int = 0
    x0: float = 0.0
    x1: float = 0.0
    start_q: float = 0.0  # onset in the played-out score (first pass)
    volta_open: bool = False  # its ending bracket runs on past the end of the line
    tempo: float = 0.0  # a metronome mark printed over this bar
    totals: list = field(default_factory=list)  # (staff index, length of what it holds), once laid out
    keys: list = field(default_factory=list)  # key signature each staff's notes were read in (letter -> alter)
    tempo_positions: list[tuple[float, float]] = field(default_factory=list)  # printed (x, quarter BPM), before timing is laid out

@dataclass
class StaffState:
    clef: int = CLEF_TREBLE
    key: dict[int, int] = field(default_factory=dict)  # step 0..6 -> alter
    pending_ties: set[int] = field(default_factory=set)  # pitches tied over the line end
    last_alter: dict[int, int] = field(default_factory=dict)  # diatonic step -> alter of its last note


@dataclass
class ReaderState:
    staff_states: dict[int, StaffState] = field(default_factory=dict)
    meter: tuple[int, int] = (4, 4)
    bpm: float = 0.0

    def staff(self, index: int) -> StaffState:
        if index not in self.staff_states:
            # a new staff below the others defaults to bass clef (piano left hand)
            clef = CLEF_BASS if index >= 1 and index == max(self.staff_states, default=0) + 1 and index % 2 == 1 else CLEF_TREBLE
            self.staff_states[index] = StaffState(clef=clef)
        return self.staff_states[index]


def _time_signature(glyphs: list[Glyph], staff: Staff) -> list[tuple[float, tuple[int, int]]]:
    """Time signatures on a staff: digits stacked above / below the middle line."""
    digits = [g for g in glyphs if g.code in TIME_DIGITS and g.size >= 17]
    found: list[tuple[float, tuple[int, int]]] = []
    for g in glyphs:
        if g.code == COMMON_TIME:
            found.append((g.x, (4, 4)))
        elif g.code == CUT_TIME:
            found.append((g.x, (2, 2)))
    cols: dict[int, list[Glyph]] = defaultdict(list)
    for g in digits:
        cols[int(round(g.x / 3))].append(g)
    done: set[int] = set()
    for g in sorted(digits, key=lambda d: d.x):
        if id(g) in done:
            continue
        near = [d for d in digits if abs(d.x - g.x) <= 12 and id(d) not in done]
        upper = sorted([d for d in near if d.y > staff.mid + 0.5], key=lambda d: d.x)
        lower = sorted([d for d in near if d.y < staff.mid - 0.5], key=lambda d: d.x)
        if not upper or not lower:
            continue
        num = int("".join(str(TIME_DIGITS[d.code]) for d in upper))
        den = int("".join(str(TIME_DIGITS[d.code]) for d in lower))
        for d in upper + lower:
            done.add(id(d))
        if num > 0 and den in (1, 2, 4, 8, 16, 32):
            found.append((min(d.x for d in upper + lower), (num, den)))
    return sorted(found)


def _barlines(drawing: PageDrawing, system: list[Staff]) -> list[Barline]:
    x0 = min(st.x0 for st in system)
    heads = [g for g in drawing.glyphs if g.music and g.code in HEADS]

    def is_stem(ln: Line) -> bool:
        # a stem always has a notehead at one of its ends (a down stem of a chord starts a
        # little left of, and reaches a little past, its lowest head)
        for h in heads:
            if -4.5 <= ln.x0 - h.x <= 8.0 and (abs(h.y - ln.ymin) <= 4.5 or abs(h.y - ln.ymax) <= 4.5):
                return True
        return False

    candidates: list[Line] = []
    for ln in drawing.lines:
        if not ln.vertical:
            continue
        covered = [
            st for st in system
            if ln.ymin <= st.bottom + 0.8 and ln.ymax >= st.top - 0.8 and st.x0 - 1 <= ln.x0 <= st.x1 + 1
        ]
        if not covered:
            continue
        # normal barlines are drawn a bit heavier than stems; thin ones (double barlines)
        # are told apart from stems by the missing notehead
        if ln.width >= 1.2 or len(covered) >= 2 or not is_stem(ln):
            candidates.append(ln)
    dots = [
        ln for ln in drawing.lines
        if ln.width >= 2.5 and abs(ln.x1 - ln.x0) < 0.3 and abs(ln.y1 - ln.y0) < 0.3
        and any(st.bottom <= ln.y0 <= st.top for st in system)
    ]
    clusters: list[list[Line]] = []
    for ln in sorted(candidates, key=lambda l: l.x0):
        if clusters and ln.x0 - clusters[-1][-1].x0 <= 6:
            clusters[-1].append(ln)
        else:
            clusters.append([ln])
    out: list[Barline] = []
    for cl in clusters:
        xs = [ln.x0 for ln in cl]
        lo, hi = min(xs), max(xs)
        thick = any(ln.width >= 2.5 for ln in cl)
        right = any(hi < d.x0 <= hi + 7 for d in dots)
        left = any(lo - 7 <= d.x0 < lo for d in dots)
        bar = Barline((lo + hi) / 2, repeat_start=thick and right, repeat_end=thick and left)
        bar.final = thick and not right and not left
        if abs(bar.x - x0) <= 2.5 and not bar.repeat_start:
            continue  # the system's opening line
        out.append(bar)
    return out


VOLTA_UNKNOWN = -1
VOLTA_CONTINUED = -2  # an ending bracket carried over from the previous line


def _voltas(drawing: PageDrawing, system: list[Staff], barlines: list[Barline]) -> list[tuple[float, float, int, bool]]:
    """1st / 2nd endings: a bracket above the top staff, hooked down at its left end, that
    starts at a barline. The number ("1." "2.") is printed by some exports and left out by
    others; without it the ending is numbered later from the repeat signs around it. A
    bracket that opens a line without a hook continues the ending of the previous line.
    The last value tells whether the bracket runs open past the end of the line."""
    top = system[0]
    space = top.space
    starts = [min(st.x0 for st in system)] + [b.x for b in barlines]
    inner = sorted(b.x for b in barlines if b.x > top.x0 + 4 * space)
    first_bar_end = inner[0] if inner else top.x1
    hooks = [ln for ln in drawing.lines if ln.vertical and ln.width <= 1.6 and 0.8 * space <= ln.ymax - ln.ymin <= 5 * space]
    texts = [g for g in drawing.glyphs if not g.music and top.top < g.y < top.top + 14 * space]
    ottavas = [g for g in drawing.glyphs if g.music and g.code in OTTAVA]
    # staff lines of the system above, when systems stand close together, are not brackets
    staff_line_ys = [y for ln in drawing.lines if ln.horizontal and ln.xmax - ln.xmin > 0.6 * (top.x1 - top.x0) for y in (ln.y0,)]
    out: list[tuple[float, float, int, bool]] = []
    for ln in drawing.lines:
        if not ln.horizontal or ln.width > 1.6 or ln.xmax - ln.xmin < 4 * space:
            continue
        height = ln.y0 - top.top
        if not (1.2 * space <= height <= 14 * space) or not (top.x0 - 2 <= ln.xmin <= top.x1):
            continue
        if ln.xmax - ln.xmin > 0.6 * (top.x1 - top.x0) and sum(1 for y in staff_line_ys if 0.5 * space <= abs(y - ln.y0) <= 1.5 * space) >= 1 \
                and sum(1 for y in staff_line_ys if abs(abs(y - ln.y0) - space) <= 0.3 or abs(abs(y - ln.y0) - 2 * space) <= 0.3) >= 2:
            continue  # a line of a staff (staff lines evenly one space apart next to it)
        hooked = any(abs(v.x0 - ln.xmin) <= 1.0 and abs(v.ymax - ln.y0) <= 1.0 for v in hooks)
        right_hooked = any(abs(v.x0 - ln.xmax) <= 1.0 and abs(v.ymax - ln.y0) <= 1.0 for v in hooks)
        # it starts at a barline, or (first bar of a line) after the clef and key signature
        at_barline = any(abs(ln.xmin - x) <= 6 for x in starts)
        opens_line = ln.xmin < first_bar_end - 4 * space and ln.xmax >= first_bar_end - 2 * space
        if hooked and not (at_barline or opens_line):
            continue
        if not hooked:
            near_ottava = any(abs(g.cy - ln.y0) <= 2 * space and ln.xmin - 40 <= g.x <= ln.xmin + 2 for g in ottavas)
            if not opens_line or near_ottava:
                continue
        number = VOLTA_UNKNOWN if hooked else VOLTA_CONTINUED
        for g in texts:
            if g.text in "123" and ln.xmin - 2 <= g.l <= ln.xmin + 16 and ln.y0 - 4 * space <= g.y <= ln.y0:
                number = int(g.text)
        open_right = not right_hooked and ln.xmax >= top.x1 - 8
        out.append((ln.xmin, ln.xmax, number, open_right))
    return out


CUE_SPACING = 2.0  # head widths per note: a run of small notes spaced wider takes up time
CUE_SPACING_ALONE = 3.0  # the same for a single small note
CUE_STATS: list | None = None  # set to [] to collect (spacing, notes, cue) for tuning


def _mark_cue_notes(heads, barlines, rest_glyphs, staff_of, head_w: float) -> None:
    """Small noteheads that take up time of their own are cue-size notes (an optional
    part, notes printed small for an 8va run, an ossia), not grace notes.

    A grace note is an eighth or shorter, never dotted, and stands close in front of a
    normal note or rest of its staff in the same bar; a run of grace notes is packed
    tighter than normal notes. Anything else small keeps its own rhythm.
    """
    bar_xs = sorted(b.x for b in barlines)
    normal_at: dict[int, list[float]] = defaultdict(list)
    for h in heads:
        if not h.grace:
            normal_at[id(h.staff)].append(h.x)
    for g in rest_glyphs:
        normal_at[id(staff_of[id(g)])].append(g.l)
    for xs in normal_at.values():
        xs.sort()
    runs: dict[tuple[int, float], list] = defaultdict(list)
    cue: set[int] = set()
    for h in sorted((h for h in heads if h.grace), key=lambda h: (h.staff.index, h.x)):
        xs = normal_at.get(id(h.staff), [])
        i = bisect.bisect_right(xs, h.x + 1.5)
        nxt = xs[i] if i < len(xs) else None
        j = bisect.bisect_right(bar_xs, h.x + 1.0)
        bar_end = bar_xs[j] if j < len(bar_xs) else float("inf")
        short = h.stem is not None and h.kind < 2.0 and max(h.stem.beams, h.stem.flags) >= 1
        if h.dots or not short or nxt is None or nxt > bar_end:
            cue.add(id(h))
            continue
        runs[(h.staff.index, nxt)].append(h)
    for (_staff, nxt), run in runs.items():
        columns = sorted({round(h.x, 1) for h in run})
        spacing = (nxt - columns[0]) / len(columns) / head_w
        # a lone grace note can stand a little apart from its note; a run of them cannot
        is_cue = spacing > (CUE_SPACING_ALONE if len(columns) == 1 else CUE_SPACING)
        if CUE_STATS is not None:
            CUE_STATS.append((round(spacing, 2), len(columns), is_cue))
        if is_cue:
            cue.update(id(h) for h in run)
    for h in heads:
        if id(h) in cue:
            h.grace = False


# the order in which a key signature adds its flats / sharps (diatonic letter, C = 0)
KEY_ORDER = {-1: (6, 2, 5, 1, 4, 0, 3), 1: (3, 0, 4, 1, 5, 2, 6)}


def _assign_curves(curves: list[list[tuple[float, tuple, tuple]]]) -> list[tuple]:
    """Give each curve one of its options so that no two curves take the same resource
    (a notehead's one tie out, one tie in), as many curves as possible get one, and the
    options chosen are the nearest ones. ``curves[i]`` lists (cost, resources, payload);
    returns the payloads chosen.

    Curves that share no resource do not influence each other, so the search runs on the small
    groups of curves that do (the ties of one chord)."""
    parent = list(range(len(curves)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: dict = {}
    for i, options in enumerate(curves):
        for _cost, resources, _payload in options:
            for key in resources:
                if key in owner:
                    parent[find(i)] = find(owner[key])
                else:
                    owner[key] = i
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(curves)):
        groups[find(i)].append(i)

    chosen: list[tuple] = []
    for members in groups.values():
        if len(members) == 1:
            chosen.append(min(curves[members[0]], key=lambda o: o[0])[2])
            continue
        order = sorted(members, key=lambda i: len(curves[i]))
        if len(order) <= 9:
            best: list = [-1, 0.0, {}]

            def search(k: int, used: frozenset, count: int, cost: float, picks: dict) -> None:
                if count + (len(order) - k) < best[0]:
                    return
                if k == len(order):
                    if count > best[0] or (count == best[0] and cost < best[1] - 1e-9):
                        best[0], best[1], best[2] = count, cost, dict(picks)
                    return
                i = order[k]
                for ci, (c, resources, _payload) in sorted(enumerate(curves[i]), key=lambda t: t[1][0]):
                    if any(r in used for r in resources):
                        continue
                    picks[i] = ci
                    search(k + 1, used | frozenset(resources), count + 1, cost + c, picks)
                    del picks[i]
                search(k + 1, used, count, cost, picks)

            search(0, frozenset(), 0, 0.0, {})
            chosen.extend(curves[i][ci][2] for i, ci in best[2].items())
        else:  # a huge cluster: nearest options first
            used = set()
            done: set[int] = set()
            for c, resources, payload, i in sorted(
                ((c, r, p, i) for i in members for c, r, p in curves[i]), key=lambda t: t[0]
            ):
                if i in done or any(r in used for r in resources):
                    continue
                done.add(i)
                used.update(resources)
                chosen.append(payload)
    return chosen


def _line_start_key_glyphs(
    system: list[Staff],
    accidental_glyphs: list[Glyph],
    heads: list[Head],
    music: list[Glyph],
    staff_of: dict[int, Staff],
    clef_changes: dict[int, list[tuple[float, int]]],
    state: "ReaderState",
) -> set[int]:
    """The accidentals of the key signature at the start of a line, by id.

    A line repeats the key signature in force. A first note can stand level with, and close
    behind, the last accidental of that key signature; looking only at what is next to a
    notehead would take the accidental for the note's own and leave the line with a shorter
    key. So the accidentals in front of the first note or rest of a staff that spell out the
    key signature of the line before it (the same flats or sharps, in the order the circle of
    fifths adds them) are the key signature. A line that changes the key is not covered; its
    accidentals are told apart as before.
    """
    found: set[int] = set()
    for st in system:
        prior = {letter: alter for letter, alter in state.staff(st.index).key.items() if alter}
        signs = set(prior.values())
        if len(signs) != 1:
            continue
        sign = signs.pop()
        if sign not in KEY_ORDER:  # a key has no double sharps / flats: the state holds a misread
            continue
        expected = KEY_ORDER[sign][: len(prior)]
        if set(prior) != set(expected):
            continue
        firsts = [h.x for h in heads if h.staff is st] + [
            g.x for g in music if g.code in RESTS and staff_of[id(g)] is st
        ]
        if not firsts:
            continue
        first_x = min(firsts)
        cands = sorted(
            (g for g in accidental_glyphs if staff_of[id(g)] is st and g.x < first_x - 0.5 and ACCIDENTALS[g.code] != 0),
            key=lambda g: g.x,
        )
        if len(cands) < len(expected):
            continue
        clef_code = state.staff(st.index).clef
        for cx, code in sorted(clef_changes.get(st.index, [])):
            if cx <= cands[0].x:
                clef_code = code
        base = CLEFS[clef_code][0]
        run = cands[: len(expected)]
        spelled = [(base + st.step_of(g.y)) % 7 for g in run]
        signs_ok = all((1 if ACCIDENTALS[g.code] > 0 else -1) == sign for g in run)
        gaps_ok = all(0.5 <= b.x - a.x <= 9 for a, b in zip(run, run[1:]))
        if spelled == list(expected) and signs_ok and gaps_ok:
            found.update(id(g) for g in run)
    return found


def _key_change_glyphs(
    system: list[Staff],
    accidental_glyphs: list[Glyph],
    heads: list[Head],
    staff_of: dict[int, Staff],
    clef_changes: dict[int, list[tuple[float, int]]],
    state: "ReaderState",
    starts: list[float],
) -> dict[int, int]:
    """The accidentals of a new key signature, by id, with the key (fifths: -4 = four flats):
    one printed at the start of a line or right after a barline (``starts``), before the first
    note there.

    Its flats (or sharps) come in the order the circle of fifths adds them, evenly spaced.
    The first note after it can stand close behind it, and then the last accidental looks
    like that note's own. A change of key is printed on every staff of the system, so a
    run counts only when every staff with notes there shows the same one (or, on a single
    staff, when it has at least three accidentals); the accidentals of a chord are not
    mistaken for a key."""
    found: dict[int, int] = {}
    for x0 in starts:
        per_staff: dict[int, tuple[int, list[Glyph]]] = {}
        staves_with_notes = 0
        for st in system:
            after = [h.x for h in heads if h.staff is st and h.x > x0]
            if not after:
                continue
            staves_with_notes += 1
            first_x = min(after)
            cands = sorted(
                (g for g in accidental_glyphs if staff_of[id(g)] is st and x0 < g.x < first_x - 0.5 and ACCIDENTALS[g.code] in (-1, 1)),
                key=lambda g: g.x,
            )
            if len(cands) < 2 or cands[0].x - x0 > 40:
                continue
            # the clef the key is written in: the one in force, or one printed just before
            # it (a clef change at a barline is drawn in front of the new key; some
            # generators still place that key for the old clef)
            clefs = {state.staff(st.index).clef}
            for cx, code in sorted(clef_changes.get(st.index, [])):
                if cx <= cands[-1].x + 2:
                    clefs = {code} if cx <= cands[0].x - 30 else clefs | {code}
            sign = ACCIDENTALS[cands[0].code]
            order = KEY_ORDER[sign]
            best_run: list[Glyph] = []
            for clef_code in clefs:
                base = CLEFS[clef_code][0]
                run: list[Glyph] = []
                for g in cands:
                    if ACCIDENTALS[g.code] != sign or len(run) >= 7:
                        break
                    if run and not (2.0 <= g.x - run[-1].x <= 9.0):
                        break
                    if (base + st.step_of(g.y)) % 7 != order[len(run)]:
                        break
                    run.append(g)
                if len(run) > len(best_run):
                    best_run = run
            run = best_run
            if len(run) >= 2:
                per_staff[st.index] = (sign * len(run), run)
        if not per_staff:
            continue
        kinds = {f for f, _run in per_staff.values()}
        lone = len(system) == 1 or staves_with_notes == 1
        if len(kinds) == 1 and (len(per_staff) == staves_with_notes > 1 or (lone and abs(next(iter(kinds))) >= 3)):
            for f, run in per_staff.values():
                for g in run:
                    found[id(g)] = f
    return found


def analyze_system(
    drawing: PageDrawing,
    system: list[Staff],
    state: ReaderState,
    above: float | None = None,
    below: float | None = None,
) -> list[Bar]:
    """Bars of one system. ``above`` / ``below``: the nearest staff lines of the systems
    above and below it, so notes on many ledger lines are kept but not taken from a
    neighbouring system."""
    glyphs = drawing.glyphs
    # Everything is looked for within 6 staff spaces of the system, except noteheads and
    # what belongs to them (accidentals, dots, flags): notes on many ledger lines reach
    # further, up to halfway to the neighbouring system.
    sys_top = system[0].top + 6 * system[0].space
    sys_bottom = system[-1].bottom - 6 * system[-1].space
    far_top = system[0].top + 12 * system[0].space
    far_bottom = system[-1].bottom - 12 * system[-1].space
    if above is not None:
        far_top = max(sys_top, min(far_top, (above + system[0].top) / 2))
    if below is not None:
        far_bottom = min(sys_bottom, max(far_bottom, (below + system[-1].bottom) / 2))
    x_lo = min(st.x0 for st in system) - 2
    x_hi = max(st.x1 for st in system) + 2
    note_parts = set(HEADS) | set(ACCIDENTALS) | set(FLAGS) | {DOT}

    def in_system(g: Glyph) -> bool:
        if not x_lo <= g.x <= x_hi:
            return False
        if g.code in note_parts:
            # a note between two systems belongs to the nearer one
            if above is not None and g.y > (above + system[0].top) / 2:
                return False
            if below is not None and g.y < (below + system[-1].bottom) / 2:
                return False
            return far_bottom <= g.y <= far_top
        return sys_bottom <= g.y <= sys_top

    music = [g for g in glyphs if g.music and in_system(g)]
    staff_of: dict[int, Staff] = {}
    for g in music:
        staff_of[id(g)] = _nearest_staff(system, g.y)
    # A notehead between two staves (on ledger lines) belongs to the staff its ledger lines
    # grow from, which is not always the nearer one.
    ledgers = [ln for ln in drawing.lines if ln.horizontal and ln.width <= 1.6 and 4 <= ln.xmax - ln.xmin <= 18]
    for g in music:
        if g.code in HEADS and len(system) > 1:
            staff_of[id(g)] = _staff_by_ledgers(system, g, ledgers, staff_of[id(g)])

    # --- clefs, key signatures, time signatures, ottava per staff
    clef_changes: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for g in music:
        if g.code in CLEFS:
            clef_changes[staff_of[id(g)].index].append((g.x, g.code))
    meters: list[tuple[float, tuple[int, int]]] = []
    for st in system:
        own = [g for g in music if staff_of[id(g)] is st]
        meters.extend(_time_signature(own, st))
    meters.sort()

    heads: list[Head] = []
    for g in music:
        if g.code in HEADS:
            st = staff_of[id(g)]
            heads.append(Head(g.x, g.y, HEADS[g.code], g.size < 18.5, st, st.step_of(g.y)))
    head_w = 6.0

    # accidentals attached to a head: right next to its left side at the same height
    accidental_glyphs = [g for g in music if g.code in ACCIDENTALS]
    leading_key = _line_start_key_glyphs(system, accidental_glyphs, heads, music, staff_of, clef_changes, state)
    key_starts = [min(st.x0 for st in system)] + [bl.x for bl in _barlines(drawing, system)]
    recognised_keys = _key_change_glyphs(system, accidental_glyphs, heads, staff_of, clef_changes, state, key_starts)
    leading_key |= set(recognised_keys)
    attached: dict[int, int] = {}
    attached_distance: dict[int, float] = {}

    def attach(g: Glyph, h: Head) -> None:
        # A cancellation natural followed by a sharp/flat can both target the
        # same head. PDF painting order is arbitrary; the nearer sign wins.
        distance = h.x - g.x
        if distance < attached_distance.get(id(h), float("inf")):
            attached[id(h)] = ACCIDENTALS[g.code]
            attached_distance[id(h)] = distance

    # A natural plus a single sharp/flat is an older spelling of cancelling a
    # double alteration. In crowded chords both may stand beyond the primary
    # matching window: do not turn that same-height pair into a global key.
    compound: set[int] = set()
    for natural in accidental_glyphs:
        if ACCIDENTALS[natural.code] != 0 or id(natural) in leading_key:
            continue
        following = [g for g in accidental_glyphs if id(g) not in leading_key
                     and ACCIDENTALS[g.code] in (-1, 1)
                     and 2 <= g.x - natural.x <= 9
                     and abs(g.y - natural.y) <= 0.3 * staff_of[id(g)].space]
        if not following:
            continue
        sign = min(following, key=lambda g: g.x)
        target = [h for h in heads if abs(h.y - sign.y) <= 0.3 * h.staff.space
                  and abs(h.y - natural.y) <= 0.3 * h.staff.space
                  and 0 < h.x - sign.x < h.x - natural.x <= 32]
        if target:
            h = min(target, key=lambda hh: hh.x - sign.x)
            if any(abs(other.y - natural.y) <= 0.3 * other.staff.space
                   and natural.x < other.x < h.x for other in heads):
                continue  # an earlier note owns the natural; not a compound sign
            attach(sign, h)
            staff_of[id(natural)] = staff_of[id(sign)] = h.staff
            compound.update((id(natural), id(sign)))
    key_glyphs: list[Glyph] = []
    for g in accidental_glyphs:
        st = staff_of[id(g)]
        if id(g) in compound:
            continue
        if id(g) in leading_key:
            key_glyphs.append(g)
            continue
        # matched by height, not by staff: a head on ledger lines between two staves may
        # belong to the farther staff while its accidental is nearer the other one
        target = [h for h in heads if abs(h.y - g.y) <= 0.3 * h.staff.space and 0 < h.x - g.x <= 16]
        if not target and not any(0 <= g.x - x0 <= 40 for x0 in key_starts):
            # the accidentals of a large chord are stacked in columns, the outer ones far
            # from their heads; away from a barline a lone run is no key signature
            target = [h for h in heads if abs(h.y - g.y) <= 0.3 * h.staff.space and 0 < h.x - g.x <= 32]
        if target:
            h = min(target, key=lambda hh: hh.x - g.x)
            staff_of[id(g)] = h.staff
            attach(g, h)
        elif st.bottom - 2.5 * st.space <= g.y <= st.top + 2.5 * st.space:
            key_glyphs.append(g)
        # (an accidental far outside the staff with no note beside it belongs to a note of
        # another system, or is a stray: no key signature is drawn there)

    # key signatures: runs of unattached sharps / flats (naturals cancel)
    key_changes: dict[int, list[tuple[float, dict[int, int]]]] = defaultdict(list)
    for st in system:
        own = sorted([g for g in key_glyphs if staff_of[id(g)] is st], key=lambda g: g.x)
        runs: list[list[Glyph]] = []
        for g in own:
            if runs and g.x - runs[-1][-1].x <= 9:
                runs[-1].append(g)
            else:
                runs.append([g])
        clef_here = sorted(clef_changes.get(st.index, []))
        for run in runs:
            clef_code = state.staff(st.index).clef
            for cx, code in clef_here:
                if cx <= run[0].x:
                    clef_code = code
            base = CLEFS[clef_code][0]
            sig: dict[int, int] = {}
            known = recognised_keys.get(id(run[0]))
            if known is not None and all(recognised_keys.get(id(g)) == known for g in run[: abs(known)]):
                # a key signature recognised as such: its letters follow the circle of fifths
                sign = 1 if known > 0 else -1
                sig = {letter: sign for letter in KEY_ORDER[sign][: abs(known)]}
                key_changes[st.index].append((run[0].x, sig))
                continue
            if not any(0 <= run[0].x - x0 <= 60 for x0 in key_starts) and not any(id(g) in leading_key for g in run):
                # a key signature stands after a barline (or the clef); stray accidentals in
                # the middle of a bar (of a rolled chord's notes moved aside) are none
                continue
            if not any(id(g) in leading_key for g in run) and len(run) >= 2 and len({round(g.x) for g in run}) < len(run):
                # two accidentals one over the other: the stacked accidentals of a chord
                # whose notes they did not reach (a key signature runs left to right)
                continue
            if len({ACCIDENTALS[g.code] for g in run} - {0}) > 1 and not any(id(g) in leading_key for g in run):
                # sharps and flats mixed: a chord's accidentals, not a key; each belongs to
                # the note at its height a little further on
                for g0 in run:
                    near = [h for h in heads if abs(h.y - g0.y) <= 0.3 * h.staff.space and 0 < h.x - g0.x <= 32]
                    if near:
                        attach(g0, min(near, key=lambda hh: hh.x - g0.x))
                continue
            if len(run) == 1 and not any(id(g) in leading_key for g in run):
                # a lone accidental with a note at its height a little further on is that
                # note's (pushed left by the accidentals of the rest of its chord)
                g0 = run[0]
                near = [h for h in heads if abs(h.y - g0.y) <= 0.3 * h.staff.space and 0 < h.x - g0.x <= 32]
                if near:
                    h = min(near, key=lambda hh: hh.x - g0.x)
                    attach(g0, h)
                    continue
            for g in run:
                alter = ACCIDENTALS[g.code]
                if alter == 0:
                    continue
                sig[(base + st.step_of(g.y)) % 7] = alter
            key_changes[st.index].append((run[0].x, sig))

    # Every line repeats its key signature after the clef. A line without one is in
    # C major / A minor: an earlier key was cancelled (e.g. by naturals at the end of the
    # previous line, which belong to no bar).
    for st in system:
        firsts = [h.x for h in heads if h.staff is st]
        firsts += [g.x for g in music if g.code in RESTS and staff_of[id(g)] is st]
        if firsts and not any(kx < min(firsts) for kx, _sig in key_changes.get(st.index, [])):
            state.staff(st.index).key = {}

    # ottava lines: 8va / 8vb text followed by a dashed line (drawn as many short dashes
    # or as one dashed stroke)
    staff_ys = [y for st in system for y in st.lines]
    ottavas: list[tuple[Staff, float, float, int]] = []
    for g in music:
        if g.code in OTTAVA:
            st = staff_of[id(g)]
            dashes = [
                ln for ln in drawing.lines
                if ln.horizontal and ln.width <= 1.6 and abs(ln.y0 - g.cy) <= 6 and ln.xmin >= g.l - 1
                and not any(abs(ln.y0 - y) < 0.6 for y in staff_ys)
            ]
            end = g.r
            ordered = sorted(dashes, key=lambda l: l.xmin)
            for ln in ordered:
                if ln.xmin - end <= 12:
                    end = max(end, ln.xmax)
            # a vertical hook marks the end; otherwise run to the dashes' end
            # (the "8" stands over the first note's head, which can start a little left of it)
            start = g.l - 3
            # The label can align with a notehead's stem/right edge rather
            # than its left-origin x. Include that actual head, without
            # widening the range far enough to catch an earlier note.
            edge_heads = [h for h in music if h.code in HEADS and staff_of[id(h)] is st
                          and h.x < g.l and abs(h.r - g.l) <= 0.15 * st.space]
            if edge_heads:
                start = min(start, min(h.x for h in edge_heads) + 2)
            ottavas.append((st, start, end + 2, OTTAVA[g.code]))

    # --- stems
    verticals = [ln for ln in drawing.lines if ln.vertical and ln.width < 1.3 and ln.ymax - ln.ymin >= 6]
    stems: list[Stem] = []
    stem_members: list[tuple[Stem, list[Head]]] = []

    def normal_side(h: Head, ln: Line, up: bool) -> bool:
        # an up stem stands at the head's right edge, a down stem at its left edge
        w = head_w * (0.7 if h.grace else 1.0)
        return (ln.x0 - h.x >= w / 2) == up

    for ln in verticals:
        members = []
        for h in heads:
            if not (ln.ymin - 3.5 <= h.y <= ln.ymax + 3.5):
                continue
            w = head_w * (0.7 if h.grace else 1.0)
            dx = ln.x0 - h.x
            if -1.2 <= dx <= w + 1.2:
                members.append(h)
        if not members:
            continue
        lowest = min(h.y for h in members)
        highest = max(h.y for h in members)
        # the stem leaves the heads at one end; heads sit near that end
        up = (ln.ymax - highest) > (lowest - ln.ymin)
        anchor = lowest if up else highest
        if up and abs(anchor - ln.ymin) > 4.5:
            continue
        if not up and abs(anchor - ln.ymax) > 4.5:
            continue
        stem_members.append((Stem(ln, up), members))
    # Two stems standing at one x, one over the other (a second voice's note under the first
    # voice's stem): a head belongs to the stem that ends at it.
    for i, (s1, m1) in enumerate(stem_members):
        for s2, m2 in stem_members[i + 1 :]:
            if abs(s1.line.x0 - s2.line.x0) > 0.3 or s1.up != s2.up:
                continue
            ids2 = {id(h) for h in m2}
            for h in list(m1):
                if id(h) not in ids2:
                    continue
                d1 = abs(h.y - (s1.line.ymin if s1.up else s1.line.ymax))
                d2 = abs(h.y - (s2.line.ymin if s2.up else s2.line.ymax))
                if d1 < d2 - 0.5:
                    m2.remove(h)
                elif d2 < d1 - 0.5:
                    m1.remove(h)
    # Closely spaced notes (16th runs) touch the next note's stem too: a head first joins a
    # stem on its normal side; a head on the other side of a stem is only the displaced
    # head of a second in a chord, so it joins only if nothing claimed it normally.
    for stem, members in stem_members:
        for h in members:
            if h.stem is None and h.kind <= 2.0 and normal_side(h, stem.line, stem.up):
                h.stem = stem
                stem.heads.append(h)
    for stem, members in stem_members:
        if not stem.heads:
            continue
        for h in members:
            if h.stem is None and h.kind <= 2.0 and any(o.staff is h.staff and abs(o.step - h.step) <= 1 for o in stem.heads):
                h.stem = stem
                stem.heads.append(h)
    for stem, members in stem_members:
        if not stem.heads:
            # only heads on the wrong side (e.g. direction misjudged): keep the old rule
            for h in members:
                if h.stem is None and h.kind <= 2.0:
                    h.stem = stem
                    stem.heads.append(h)
        if stem.heads:
            stems.append(stem)

    # flags
    for g in music:
        if g.code in FLAGS:
            best = None
            for s in stems:
                if abs(s.line.x0 - g.l) <= 2.0 and s.line.ymin - 3 <= g.cy <= s.line.ymax + 3:
                    d = abs(s.line.x0 - g.l)
                    if best is None or d < best[0]:
                        best = (d, s)
            if best:
                best[1].flags = max(best[1].flags, FLAGS[g.code])

    # beams: thick slanted strokes; count the beams crossing each stem's free end
    beams = [
        ln for ln in drawing.lines
        if ln.width >= 2.5 and (ln.xmax - ln.xmin) >= 2.5 and abs(ln.y1 - ln.y0) <= 0.8 * (ln.xmax - ln.xmin)
    ]

    def reaches(bm: Line, s: Stem) -> float | None:
        """Height of the beam at the stem, when the beam reaches the stem. Some generators
        stop a beam a little short of the group's last stem (by up to half a space): such a
        stem still belongs to it when its free end lies on the beam's line."""
        x = s.line.x0
        if bm.xmin - 1.0 <= x <= bm.xmax + 1.0:
            return bm.y_at(min(max(x, bm.xmin), bm.xmax))
        if bm.xmin - 2.5 <= x <= bm.xmax + 2.5:
            y = bm.y_at(x)
            end = s.line.ymax if s.up else s.line.ymin
            if abs(y - end) <= 1.5:
                return y
        return None

    def stems_touching(bm: Line) -> list[Stem]:
        out = []
        for s in stems:
            y = reaches(bm, s)
            if y is not None and s.line.ymin - 3.5 <= y <= s.line.ymax + 3.5:
                out.append(s)
        return out

    # Tremolos. Strokes centred on a single stem (or floating over a stemless head) are
    # single-note tremolo slashes; strokes floating between two notes without touching
    # either stem make a two-note tremolo. Neither counts as a beam.
    slashes: dict[int, int] = defaultdict(int)       # id(Head) -> strokes
    trem_pairs: list[tuple[list[Head], list[Head], list[Line]]] = []
    floating: list[Line] = []
    plain_beams: list[Line] = []
    for bm in beams:
        touch = stems_touching(bm)
        if len(touch) == 1 and bm.xmin < touch[0].line.x0 - 1.5 and bm.xmax > touch[0].line.x0 + 1.5 and bm.xmax - bm.xmin <= 14:
            for h in touch[0].heads:
                slashes[id(h)] += 1
            continue
        if not touch:
            mid_y = (bm.y0 + bm.y1) / 2
            near = _nearest_staff(system, mid_y)
            if near.bottom - 5 * near.space <= mid_y <= near.top + 5 * near.space and x_lo <= bm.xmin <= x_hi:
                floating.append(bm)
            continue
        plain_beams.append(bm)
    loose_heads = [h for h in heads if h.stem is None]
    used_float: set[int] = set()
    for bm in sorted(floating, key=lambda l: l.xmin):
        if id(bm) in used_float:
            continue
        group = [o for o in floating if id(o) not in used_float and abs(o.xmin - bm.xmin) <= 2 and abs(o.xmax - bm.xmax) <= 2]
        st = _nearest_staff(system, (bm.y0 + bm.y1) / 2)
        cx = (bm.xmin + bm.xmax) / 2
        over_whole = [h for h in loose_heads if h.staff is st and abs(h.x + 3 - cx) <= 4.5]
        if bm.xmax - bm.xmin <= 12 and over_whole:
            for h in over_whole:
                slashes[id(h)] += len(group)
            used_float.update(id(o) for o in group)
            continue
        # anchors: stems or stemless heads just left / right of the strokes
        left: list[Head] = []
        right: list[Head] = []
        best_l = best_r = 99.0
        for s in stems:
            if _nearest_staff(system, s.heads[0].y) is not st:
                continue
            dl = bm.xmin - s.line.x0
            dr = s.line.x0 - bm.xmax
            if 0 < dl <= 12 and dl < best_l:
                best_l, left = dl, s.heads
            if 0 < dr <= 12 and dr < best_r:
                best_r, right = dr, s.heads
        for h in loose_heads:
            if h.staff is not st:
                continue
            dl = bm.xmin - (h.x + 6)
            dr = h.x - bm.xmax
            if -1 < dl <= 12 and dl < best_l:
                best_l, left = dl, [o for o in loose_heads if o.staff is st and abs(o.x - h.x) <= 2.5]
            if -1 < dr <= 12 and dr < best_r:
                best_r, right = dr, [o for o in loose_heads if o.staff is st and abs(o.x - h.x) <= 2.5]
        if left and right:
            trem_pairs.append((left, right, group))
            used_float.update(id(o) for o in group)
            if _DEBUG:
                print("TREM", [round(v, 1) for v in (bm.x0, bm.y0, bm.x1, bm.y1)], bm.width, "left", [round(h.x, 1) for h in left], "right", [round(h.x, 1) for h in right])
    beams = plain_beams

    for s in stems:
        x = s.line.x0
        end = s.line.ymax if s.up else s.line.ymin
        ys: list[float] = []
        for bm in beams:
            y = reaches(bm, s)
            if y is not None:
                if s.line.ymin - 3.5 <= y <= s.line.ymax + 3.5:
                    # only beams near the free end, not ones crossing the heads
                    if abs(y - end) <= 2.8 + 4.6 * 4:
                        ys.append(round(y, 1))
        if ys:
            # The beam nearest the free end starts the stack, and the others lie inward of it.
            # One further out is the beam of other notes (the other staff's, another voice's)
            # that comes close; counting it would turn an eighth into a sixteenth.
            sign = 1 if s.up else -1
            first = min(ys, key=lambda yy: (round(abs(yy - end), 1), (yy - end) * sign))
            ys = [yy for yy in ys if (yy - first) * sign <= 1.5]
        uniq: list[float] = []
        for y in sorted(ys):
            if not uniq or y - uniq[-1] > 1.5:
                uniq.append(y)
        s.beams = len(uniq)

    # beam groups: stems joined by beams (for tuplets printed without their number)
    parent: dict[int, int] = {id(s): id(s) for s in stems}

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for bm in beams:
        touch = stems_touching(bm)
        for a, b in zip(touch, touch[1:]):
            parent[find(id(a))] = find(id(b))
    for s in stems:
        if s.beams:
            s.beam_group = find(id(s))

    # small noteheads beamed together with normal ones are cue-size notes with their own
    # rhythm (e.g. optional inner notes), not grace notes
    for bm in beams:
        touch = stems_touching(bm)
        if len(touch) >= 2 and any(not all(h.grace for h in s.heads) for s in touch):
            for s in touch:
                for h in s.heads:
                    h.grace = False
    # Small noteheads standing in the same column as normal ones of their staff (a second
    # voice or an optional chord printed small) sound with them. A grace note always
    # stands to the left of the note it leads into. (Other staves do not count: a grace
    # note can line up with a quick note of the other hand.)
    normal_x: dict[int, list[float]] = defaultdict(list)
    for h in heads:
        if not h.grace:
            normal_x[id(h.staff)].append(h.x)
    for xs in normal_x.values():
        xs.sort()
    for h in heads:
        if h.grace:
            xs = normal_x.get(id(h.staff), [])
            i = bisect.bisect_left(xs, h.x - 1.5)
            if i < len(xs) and xs[i] <= h.x + 1.5:
                h.grace = False

    # tuplet numbers ("3", "6", "5" in small text) over a beamed group or a bracket
    def own_mark(g: Glyph) -> bool:
        """On this system's side of the line halfway to the neighbouring system."""
        if above is not None and g.y > (above + system[0].top) / 2:
            return False
        if below is not None and g.y < (below + system[-1].bottom) / 2:
            return False
        return True

    # Duplets and quadruplets (2 or 4 eighths in the time of 3 or 6 in 6/8, 12/8 ...) are
    # numbered too, but a 2 or 4 is also a fingering: they are taken only in a compound meter,
    # from MuseScore pages (tuplet numbers in their own size), over a beamed group of exactly
    # that many equal notes centred under or over the number.
    compound = state.meter[1] == 8 and state.meter[0] % 3 == 0 and state.meter[0] > 3
    tuplet_marks = [
        g for g in _tuplet_numbers(glyphs)
        if (g.text in ("3","5","6","7","9") or (g.text in ("2","4") and compound and drawing.smufl)
            or (10 <= int(g.text) <= 32))
        and x_lo <= g.x <= x_hi and sys_bottom <= g.y <= sys_top
    ]
    all_rests = [g for g in music if g.code in RESTS]
    rest_group: dict[int, Tuplet] = {}
    for g in tuplet_marks:
        n = int(g.text)
        even = n in (2, 4)
        ratio = {2: 3 / 2, 4: 3 / 4}.get(n,2 ** int(math.log2(n-1)) / n)
        # a number between two systems (e.g. under the lower staff of the system above) is
        # not this system's unless notes here make a tuplet right by it
        foreign = drawing.smufl and not own_mark(g)
        st = _nearest_staff(system, g.cy)
        # bracket: horizontal line pieces at the number's height
        pieces = [
            ln for ln in drawing.lines
            if ln.horizontal and ln.width <= 1.6 and abs(ln.y0 - g.cy) <= 4 and (ln.xmax - ln.xmin) < 120
        ]
        lo, hi = g.l, g.r
        grown = True
        while grown:
            grown = False
            for ln in pieces:
                if ln.xmin < lo - 0.1 and ln.xmax >= lo - 4:
                    lo = ln.xmin
                    grown = True
                if ln.xmax > hi + 0.1 and ln.xmin <= hi + 4:
                    hi = ln.xmax
                    grown = True
        group_beams: list[Line] = []
        if hi - lo < 8:
            # no bracket: the beam group nearest to the number (a duplet / quadruplet number
            # of MuseScore stands further off its beam, on the far side of a high stem)
            reach = 40 if even else 16
            near = [bm for bm in beams if bm.xmin - 2 <= g.cx <= bm.xmax + 2 and abs(bm.y_at(g.cx) - g.cy) <= reach]
            if near:
                def group_of(bm: Line) -> list[Line]:
                    # the beam and those touching it at the same height
                    out = [bm]
                    for other in beams:
                        if (
                            other is not bm and abs(other.y_at(other.xmin) - bm.y_at(other.xmin)) <= 6
                            and other.xmin <= bm.xmax + 1 and other.xmax >= bm.xmin - 1
                        ):
                            out.append(other)
                    return out

                def centring(gb: list[Line]) -> float:
                    """How far the number is off the middle of the ``n`` stems of this beam group
                    that it sits nearest to (a tuplet number is centred over its notes)."""
                    xs = sorted({id(s): s.line.x0 for b in gb for s in stems_touching(b)}.values())
                    if not xs:
                        return 999.0
                    if len(xs) < n:
                        return abs((xs[0] + xs[-1]) / 2 - g.cx)
                    return min(abs((xs[i] + xs[i + n - 1]) / 2 - g.cx) for i in range(len(xs) - n + 1))

                if drawing.smufl:
                    scored = [(centring(group_of(b)), abs(b.y_at(g.cx) - g.cy), b) for b in near]
                    best_err = min(s[0] for s in scored)
                    bm = min((s for s in scored if s[0] <= best_err + 1.5), key=lambda s: s[1])[2]
                else:
                    bm = min(near, key=lambda b: abs(b.y_at(g.cx) - g.cy))
                lo, hi = bm.xmin, bm.xmax
                group_beams = [bm]
                # extend through beams touching this one at the same height
                for other in beams:
                    if other is not bm and abs(other.y_at(other.xmin) - bm.y_at(other.xmin)) <= 6 and other.xmin <= hi + 1 and other.xmax >= lo - 1:
                        lo, hi = min(lo, other.xmin), max(hi, other.xmax)
                        group_beams.append(other)
            else:
                lo, hi = g.cx - 14, g.cx + 14

        def unit_of(s: Stem | None, r: Glyph | None) -> float:
            if s is not None:
                base = min(h.kind for h in s.heads)
                return base if base >= 2.0 else 0.5 ** max(s.beams, s.flags)
            return RESTS[r.code]

        def fills_units(total: float) -> bool:
            """``total`` quarters make ``n`` equal units of a plain note value (a tuplet)."""
            if total <= 0:
                return False
            unit = total / n
            return abs(math.log2(unit) - round(math.log2(unit))) < 0.01

        def pool_for(staff: Staff) -> list[tuple[float, Stem | None, Glyph | None]]:
            if group_beams:
                beamed = {id(s) for b in group_beams for s in stems_touching(b)}
                pool = [s for s in stems if id(s) in beamed]
            else:
                pool = [s for s in stems if lo - 7 <= s.line.x0 <= hi + 1.5]
                # a bracket stands at the stem ends of its notes: in a staff of two voices
                # the notes of the other voice (stems pointing away from it) are not its
                toward = [s for s in pool if (s.up and s.line.ymax <= g.cy + 2) or (not s.up and s.line.ymin >= g.cy - 2)]
                away = [s for s in pool if s not in toward]
                if toward and away and drawing.smufl:
                    pool = toward
            return [(s.line.x0, s, None) for s in pool if s.heads[0].staff is staff]

        def best_run(staff: Staff) -> tuple[float, list[tuple[float, Stem | None, Glyph | None]], float] | None:
            """The run of neighbouring notes (and rests) of ``staff`` that this number counts,
            as (how well it fits, the run, its vertical distance from the number). A tuplet
            number is centred over its notes, close above or below them, and they fill ``n``
            equal units. Of the beam group's notes and the rests among them that is the run to
            take: the number of a group of eighth + 16th + 16th + 16th that has a triplet of
            16ths at its end counts only those three."""
            notes = pool_for(staff)
            rests = [(r.cx, None, r) for r in all_rests if lo - 2 <= r.cx <= hi + 2 and staff_of.get(id(r)) is staff]
            items = sorted(notes + rests, key=lambda it: it[0])
            best: tuple[float, list[tuple[float, Stem | None, Glyph | None]], float] | None = None
            for i in range(len(items)):
                total = 0.0
                for j in range(i, len(items)):
                    total += unit_of(items[j][1], items[j][2])
                    if j == i or not fills_units(total):
                        continue
                    err = abs((items[i][0] + items[j][0]) / 2 - g.cx)
                    if err > 14:
                        continue
                    run = items[i : j + 1]
                    # how far the number stands from the run, vertically
                    ends: list[float] = []
                    for _x, s, r in run:
                        if s is not None:
                            ends += [abs(g.cy - s.line.ymin), abs(g.cy - s.line.ymax)]
                        else:
                            ends.append(abs(g.cy - r.cy))
                    fit = min(ends) + 2.0 * err
                    if best is None or fit < best[0] - 1.0 or (fit <= best[0] + 1.0 and len(run) < len(best[1])):
                        best = (fit, run, min(ends))
            return best

        def items_for(staff: Staff) -> list[tuple[float, Stem | None, Glyph | None]]:
            """The notes (stems) and rests of ``staff`` that the number counts (without a run
            that fills the units: the stems of the beam group, or the ``n`` of them in a row
            centred on the number when they are all of one length)."""
            run = best_run(staff)
            if run is not None:
                return run[1]
            its = pool_for(staff)
            if len(its) < n:  # rests are members too
                its += [
                    (r.cx, None, r) for r in all_rests
                    if lo - 2 <= r.cx <= hi + 2 and staff_of.get(id(r)) is staff
                ]
            its.sort(key=lambda it: it[0])
            if len(its) > n and len({round(unit_of(s, r), 4) for _x, s, r in its}) == 1:
                first = min(range(len(its) - n + 1), key=lambda i: abs((its[i][0] + its[i + n - 1][0]) / 2 - g.cx))
                its = its[first : first + n]
            return its

        # The number belongs to the staff whose notes under it make a tuplet (3 eighths, 3
        # quarters ...) and are centred under it. A bracket under the upper staff's notes can
        # be nearer to the lower staff; when neither staff has such notes, the staff of the
        # beam (or the nearest staff) keeps it.
        def adds_up(staff: Staff) -> bool:
            return best_run(staff) is not None

        fitting = [x for x in system if adds_up(x)]
        if not fitting and group_beams:
            # the beam nearest to the number holds no tuplet under it: the number may be the
            # one of other notes it is centred over (a 16th triplet of the upper staff whose
            # number stands by the lower staff's beam)
            saved = (group_beams, lo, hi)
            group_beams, lo, hi = [], g.cx - 30, g.cx + 30
            fitting = [x for x in system if adds_up(x)]
            if not fitting:
                group_beams, lo, hi = saved
        if foreign:
            fitting = [x for x in fitting if best_run(x)[2] <= 16.0]  # type: ignore[index]
            if not fitting:
                continue
        if len(fitting) == 1:
            st = fitting[0]
        elif len(fitting) > 1:
            st = min(fitting, key=lambda x: (best_run(x)[0], abs(x.mid - g.cy)))  # type: ignore[index]
        elif group_beams:
            # no staff adds up: the number sits by a beam, and the beam's own staff owns it
            owners = [s.heads[0].staff for b in group_beams for s in stems_touching(b)]
            if owners:
                st = max(owners, key=lambda o: sum(1 for p in owners if p is o))
        members = items_for(st)
        if n >= 10:
            beam_members = pool_for(st)
            if len(beam_members) == n and len({unit_of(s,r) for _x,s,r in beam_members}) == 1:
                members = beam_members
            # Multi-digit text needs a complete musical group, not merely a
            # nearby fingering, measure number or the last digit of a label.
            if not group_beams or not fills_units(sum(unit_of(s,r) for _x,s,r in members)):
                continue
        if even:
            # the beam group's own notes, all of them: n equal eighths (or 16ths)
            if not group_beams:
                continue
            beamed = sorted(
                {id(s): s for b in group_beams for s in stems_touching(b)}.values(), key=lambda s: s.line.x0
            )
            if len(beamed) != n or len({s.heads[0].staff.index for s in beamed}) != 1:
                continue
            units = {round(unit_of(s, None), 4) for s in beamed}
            if len(units) != 1 or next(iter(units)) > 0.5:
                continue
            xs = [s.line.x0 for s in beamed]
            if abs((xs[0] + xs[-1]) / 2 - g.cx) > 10:
                continue  # not centred over them: a fingering
            st = beamed[0].heads[0].staff
            members = [(s.line.x0, s, None) for s in beamed]
        group = Tuplet(n, ratio)
        if _DEBUG:
            print(
                "TUPLET", n, "at", round(g.cx, 1), round(g.cy, 1), "extent", round(lo, 1), round(hi, 1),
                "beams", len(group_beams), "fitting", [x.index for x in fitting], "staff", st.index,
                "members", [round(m[0], 1) for m in members],
            )
        for _x, s, r in members:
            if s is not None:
                s.tuplet = ratio
                s.group = group
            elif r is not None:
                rest_group[id(r)] = group
        for h in heads:
            if h.stem is None and h.staff is st and lo - 7 <= h.x <= hi + 1.5:
                h.dots = h.dots  # whole/breve inside tuplet: rare, ignore

    # dots (augmentation): right of a head or rest, at its height or half a space above
    dot_glyphs = [g for g in music if g.code == DOT]
    rest_glyphs = [g for g in music if g.code in RESTS]

    def count_dots(x_right: float, y: float, st: Staff, used: set[int], y_lo: float | None = None, y_hi: float | None = None) -> int:
        n = 0
        cursor = x_right
        lo = y - st.space * 0.8 if y_lo is None else y_lo
        hi = y + st.space * 0.8 if y_hi is None else y_hi
        for d in sorted(dot_glyphs, key=lambda d: d.x):
            if id(d) in used:
                continue
            if cursor - 0.5 <= d.l <= cursor + 7.5 and lo <= d.cy <= hi:
                used.add(id(d))
                n += 1
                cursor = d.r
        return n

    used_dots: set[int] = set()
    for h in sorted(heads, key=lambda h: h.x):
        n = count_dots(h.x + head_w * (0.7 if h.grace else 1.0), h.y, h.staff, used_dots)
        h.dots = n

    # arpeggio signs: a wavy vertical line (stacked glyph pieces) left of a chord; the
    # chord right after it is rolled from low to high
    pieces = sorted((g for g in glyphs if g.music and g.code == ARPEGGIO and x_lo <= g.x <= x_hi), key=lambda g: (g.x, g.b))
    lines_: list[list[Glyph]] = []
    for g in pieces:
        for line in lines_:
            last = line[-1]
            if abs(g.x - last.x) <= 1.5 and -0.6 * (last.t - last.b) <= g.b - last.t <= 3.0:
                line.append(g)
                break
        else:
            lines_.append([g])
    for line in lines_:
        x_right = max(g.r for g in line)
        y_lo = min(g.b for g in line) - 3.0
        y_hi = max(g.t for g in line) + 3.0
        near = [h for h in heads if x_right - 1.0 <= h.x <= x_right + 16.0 and y_lo <= h.y <= y_hi and not h.grace]
        if not near:
            continue
        first = min(h.x for h in near)
        for h in near:
            if h.x <= first + 3.0:
                h.arpeggio = True

    # ties: a curve from just right of a head to just left of a head at the same height
    # (MuseScore ties start further from the head's centre than the ones of other programs)
    tie_dy = 5.6 if drawing.smufl else 4.2
    tie_curves: list[list[tuple[float, tuple, tuple]]] = []
    for cv in drawing.curves:
        left_x, right_x = min(cv.x0, cv.x1), max(cv.x0, cv.x1)
        ly = cv.y0 if cv.x0 <= cv.x1 else cv.y1
        ry = cv.y1 if cv.x0 <= cv.x1 else cv.y0
        # (as far out as the noteheads are looked for: the tie of a note on many ledger lines)
        if not (x_lo <= left_x <= x_hi and far_bottom - 8 <= ly <= far_top + 8):
            continue
        if abs(ly - ry) > 3.5:
            continue
        # (a whole note's head is about 1.6 times as wide as a black one)
        start = [h for h in heads if -2 <= left_x - h.x <= head_w * (1.6 if h.kind >= 4.0 else 1.0) + 4 and abs(h.y - ly) <= tie_dy]
        # (a tie under a whole note's wide head can end a little inside it)
        end = [h for h in heads if -(5.0 if h.kind >= 4.0 else 3.0) <= h.x - right_x <= 4 and abs(h.y - ry) <= tie_dy]
        options: list[tuple[float, tuple, tuple]] = []
        if start and end:
            # the pairs of one pitch that this curve could join; a curve over a repeated note
            # is a slur (phrase), not a tie
            for a in start:
                for b in end:
                    if a.staff is b.staff and a.step == b.step and b.x > a.x and not any(
                        h.staff is a.staff and h.step == a.step and a.x + 2 < h.x < b.x - 2 for h in heads
                    ):
                        options.append((abs(a.y - ly) + abs(b.y - ry), (("s", id(a)), ("e", id(b))), ("tie", a, b)))
        elif start and not end and right_x >= start[0].staff.x1 - 12:
            options = [(abs(a.y - ly), (("s", id(a)),), ("out", a, None)) for a in start]
        elif end and not start:
            st0 = end[0].staff
            first_x = min((h.x for h in heads if h.staff is st0), default=0)
            options = [(abs(b.y - ry), (("e", id(b)),), ("in", None, b)) for b in end if b.x <= first_x + 30]
        if options:
            tie_curves.append(options)
    # Several ties between the notes of two chords each sit about as near to two heads: let
    # every head take part in one tie at most, and as many curves as possible find theirs.
    for kind, a, b in _assign_curves(tie_curves):
        if kind == "tie":
            b.tied = True
        elif kind == "out":
            a._tie_out = True  # type: ignore[attr-defined]
        else:
            b._tie_in = True  # type: ignore[attr-defined]

    # --- barlines and bars
    barlines = _barlines(drawing, system)
    voltas = _voltas(drawing, system, barlines)
    start_x = min(st.x0 for st in system)
    end_x = max(st.x1 for st in system)
    edges = [start_x] + [b.x for b in barlines if start_x + 3 < b.x < end_x - 3] + [end_x]
    bar_info: list[Barline | None] = [None] + [b for b in barlines if start_x + 3 < b.x < end_x - 3] + [None]
    for b in barlines:
        if b.x >= end_x - 3:
            bar_info[-1] = b
        elif b.x <= start_x + 3:
            bar_info[0] = b

    # --- events per staff
    _mark_cue_notes(heads, barlines, rest_glyphs, staff_of, head_w)
    events: list[Event] = []
    for s in stems:
        base = min(h.kind for h in s.heads)
        if base >= 2.0:
            length = base
        else:
            n = max(s.beams, s.flags)
            length = 0.5 ** n
        dots = max(h.dots for h in s.heads)
        length *= 2 - 0.5 ** dots
        written = length
        length *= s.tuplet
        normal = [h for h in s.heads if (h.x <= s.line.x0 - 2) == s.up] or s.heads
        x = min(h.x for h in normal) if s.up else min(h.x for h in normal)
        grace = all(h.grace for h in s.heads)
        events.append(Event(
            x, s.heads[0].staff, length, s.heads, grace=grace, y=sum(h.y for h in s.heads) / len(s.heads),
            base=written, group=s.group, beam_group=s.beam_group,
        ))
    loose = [h for h in heads if h.stem is None]
    loose.sort(key=lambda h: (h.staff.index, h.x))
    groups: list[list[Head]] = []
    for h in loose:
        if groups and groups[-1][0].staff is h.staff and abs(groups[-1][0].x - h.x) <= 2.5 and groups[-1][0].kind == h.kind:
            groups[-1].append(h)
        else:
            groups.append([h])
    for grp in groups:
        length = grp[0].kind * (2 - 0.5 ** max(h.dots for h in grp))
        events.append(Event(min(h.x for h in grp), grp[0].staff, length, grp, grace=all(h.grace for h in grp), y=sum(h.y for h in grp) / len(grp)))
    for g in rest_glyphs:
        st = staff_of[id(g)]
        # a rest's dot sits in the space by the rest's upper part (an 8th rest's dot is level
        # with its top), not at the glyph's centre
        n = count_dots(g.r, g.cy, st, used_dots, min(g.cy - st.space * 0.8, g.b), max(g.cy + st.space * 0.8, g.t + st.space * 0.5))
        written = RESTS[g.code] * (2 - 0.5 ** n)
        group = rest_group.get(id(g))
        length = written * (group.ratio if group is not None else 1.0)
        events.append(Event(
            g.l, st, length, [], full_bar_rest=g.code == WHOLE_REST and n == 0, y=g.cy,
            base=written, group=group,
        ))

    # tremolos: single-note slashes repeat the note; a two-note tremolo alternates two
    # notes that together last the value written on each of them
    event_of = {id(h): e for e in events for h in e.heads}
    for e in events:
        strokes = max((slashes.get(id(h), 0) for h in e.heads), default=0)
        if strokes:
            stem = e.heads[0].stem
            flags = max(stem.beams, stem.flags) if stem is not None else 0
            e.repeat_sub = max(MIN_TREMOLO, 0.5 ** (strokes + flags))
    for left, right, group in trem_pairs:
        a, b = event_of.get(id(left[0])), event_of.get(id(right[0]))
        if a is None or b is None or a is b or a.trem_partner is not None or b.trem_second:
            continue
        levels: list[float] = []
        for ln in sorted(group, key=lambda l: l.y_at(l.xmin)):
            y = ln.y_at(ln.xmin)
            if not levels or y - levels[-1] > 1.5:
                levels.append(y)
        a.length = b.length = a.length / 2
        a.trem_partner, b.trem_second = b, True
        a.repeat_sub = max(MIN_TREMOLO, 0.5 ** max(1, len(levels)))

    # --- pitches (clef, key, accidentals in the bar, ottava)
    bars: list[Bar] = []
    meter_iter = list(meters)
    # A barline drawn just short of the end of the staff lines, with a note squeezed onto
    # it: that note belongs to the bar before the barline.
    if (
        len(edges) >= 3
        and edges[-1] - edges[-2] < 2.5 * system[0].space
        and any(edges[-2] - 1 <= e.x < edges[-1] - 1 for e in events)
    ):
        mid, right = bar_info[-2], bar_info[-1]
        merged = right if right is not None and (right.repeat_end or right.final or mid is None) else mid
        del edges[-2]
        del bar_info[-2]
        bar_info[-1] = merged
    for bi, (a, b) in enumerate(zip(edges, edges[1:])):
        in_bar = [e for e in events if a - 1 <= e.x < b - 1]
        if not in_bar:
            # a segment with no notes and no rests is not a bar: the area after the last
            # barline holding a courtesy clef / key / time signature, or a gap
            continue
        for st in system:
            stt = state.staff(st.index)
            alters: dict[tuple[int, int], int] = {}
            stt.key_in_bar = dict(stt.key)  # type: ignore[attr-defined]
            for ev in sorted([e for e in in_bar if e.staff is st], key=lambda e: e.x):
                for cx, code in sorted(clef_changes.get(st.index, [])):
                    if cx <= ev.x:
                        stt.clef = code
                for kx, sig in key_changes.get(st.index, []):
                    if kx <= ev.x and kx >= a - 30:
                        stt.key = dict(sig)
                        stt.key_in_bar = dict(sig)  # type: ignore[attr-defined]
                ev.key_signature = dict(stt.key)
                base = CLEFS[stt.clef][0]
                for h in ev.heads:
                    dia = base + h.step
                    if id(h) in attached:
                        alters[(dia, 0)] = attached[id(h)]
                    alter = alters.get((dia, 0), stt.key.get(dia % 7, 0))
                    # a note tied over from an altered note keeps that alteration in the next
                    # bar too (an accidental lasts its bar, but a tie holds the note on)
                    tied_in = h.tied or getattr(h, "_tie_in", False) or (ev.x <= a + 40 and bi <= 1 and dia in stt.last_alter
                                                                           and _diatonic_to_midi(dia, stt.last_alter[dia]) in stt.pending_ties)
                    if tied_in and (dia, 0) not in alters and dia in stt.last_alter:
                        alter = stt.last_alter[dia]  # the tied note only, not the rest of the bar
                    stt.last_alter[dia] = alter
                    shift = 0
                    for ost, ox0, ox1, osh in ottavas:
                        if ost is st and ox0 - 2 <= h.x <= ox1:
                            shift = osh
                    h.pitch = _diatonic_to_midi(dia, alter) + shift
                    # a tie running in from the end of the previous line holds the first
                    # note of that pitch only
                    tie_in = getattr(h, "_tie_in", False) or (
                        h.pitch in stt.pending_ties and ev.x <= a + 40 and bi <= 1
                    )
                    if tie_in:
                        h.tied = True
                        stt.pending_ties.discard(h.pitch)
            # apply clef / key changes standing at the end of this bar to later bars
            for cx, code in sorted(clef_changes.get(st.index, [])):
                if cx < b:
                    stt.clef = code
            for kx, sig in key_changes.get(st.index, []):
                if a - 30 <= kx < b and not [e for e in in_bar if e.staff is st and e.x > kx]:
                    stt.key = dict(sig)
        meter = state.meter
        for mx, m in meter_iter:
            if mx < b:
                meter = m
        state.meter = meter
        bar = Bar(sorted(in_bar, key=lambda e: e.x), meter=meter, x0=a, x1=b)
        bar.keys = [dict(getattr(state.staff(st.index), "key_in_bar", {})) for st in system]
        left = bar_info[bi] if bi < len(bar_info) else None
        right = bar_info[bi + 1] if bi + 1 < len(bar_info) else None
        bar.repeat_start = bool(left and left.repeat_start)
        bar.repeat_end = bool(right and right.repeat_end)
        bar.final = bool(right and right.final)
        for vx0, vx1, num, open_right in voltas:
            # the bracket covers most of this bar (the first bar of a line starts after
            # the clef, so its bracket starts later than the bar)
            if min(vx1, b) - max(vx0, a) >= 0.4 * (b - a):
                bar.volta = num
                bar.volta_open = open_right and b >= end_x - 3
        bars.append(bar)
    # ties running over the end of the line
    for st in system:
        stt = state.staff(st.index)
        stt.pending_ties = {h.pitch for h in heads if h.staff is st and getattr(h, "_tie_out", False)}
    return bars


# ---------------------------------------------------------------- timing inside bars


DISPLACE_MAX = 7.2  # a note moved sideways to clear another voice's head: at most a head width
MAX_READINGS = 1200  # readings tried for one staff of one bar before giving up
TIME_EPS = 1e-4      # quarters: times closer than this are the same time


def _columns(events: list[Event], merge: frozenset[int] = frozenset()) -> list[list[Event]]:
    """Events by column: those printed at (nearly) one x start together. ``merge`` closes up
    column boundaries (indexes into the plain columns), see ``_merge_candidates``."""
    cols: list[list[Event]] = []
    for e in sorted(events, key=lambda ev: ev.x):
        if cols and e.x - cols[-1][0].x <= 2.2:
            cols[-1].append(e)
        else:
            cols.append([e])
    if not merge:
        return cols
    out: list[list[Event]] = []
    for i, col in enumerate(cols):
        if i - 1 in merge and out:
            out[-1].extend(col)
        else:
            out.append(list(col))
    return out


def _stem_dir(e: Event) -> str:
    stem = e.heads[0].stem if e.heads else None
    return "" if stem is None else ("u" if stem.up else "d")


def _merge_candidates(events: list[Event]) -> list[int]:
    """Column boundaries that may be one column: where voices of a staff collide, one voice's
    notes are moved sideways (by up to a head width) to clear the other's heads or stems, so
    a note a little to the right of a column can start with it. Column boundaries closer
    than that are candidates; only a reading that makes the bar add up uses them."""
    cols = _columns(events)
    return [i for i in range(len(cols) - 1) if cols[i + 1][0].x - cols[i][0].x <= DISPLACE_MAX]


def _anchor_time(x: float, anchors: tuple[tuple[float, float], ...]) -> float | None:
    """The time of a column at ``x`` known from the columns of a staff that adds up: events of
    any staff printed at one x start together. A column a little off an anchor counts when no
    other anchor is nearly as close (a note moved aside by another voice)."""
    if not anchors:
        return None
    ranked = sorted(anchors, key=lambda a: abs(a[0] - x))
    d1 = abs(ranked[0][0] - x)
    if d1 > DISPLACE_MAX:
        return None
    if d1 <= 2.2:
        return ranked[0][1]
    others = [a for a in ranked[1:] if abs(a[1] - ranked[0][1]) > 1e-6]
    if others and abs(others[0][0] - x) - d1 < 6.0:
        return None
    return ranked[0][1]


def _layout_staff(
    events: list[Event],
    merge: frozenset[int] = frozenset(),
    anchors: tuple[tuple[float, float], ...] = (),
) -> float:
    """Onsets of one staff's events, read left to right.

    Events printed at the same x start together (chords and the voices of a two-voice
    staff). The next column starts when the earliest still-sounding event ends. ``anchors``
    ((x, time) of the columns of another staff that adds up) fix the time of the columns
    that stand at the same x.
    """
    cols = _columns(events, merge)
    t = 0.0
    prev_t = -1.0
    active: list[float] = []
    total = 0.0
    for i, col in enumerate(cols):
        if anchors:
            xs = sorted(e.x for e in col)
            forced = _anchor_time(xs[len(xs) // 2], anchors)
            if forced is not None and forced > prev_t + TIME_EPS:
                t = forced
        for e in col:
            e.t = t
            active.append(t + e.length)
            total = max(total, t + e.length)
        prev_t = t
        if i + 1 < len(cols):
            later = [end for end in active if end > t + TIME_EPS]
            t = min(later) if later else t + min(e.length for e in col)
    return max(total, t)


def _fits(total: float, meter_q: float) -> bool:
    return abs(total - meter_q) <= 0.04 * meter_q


def _exact(total: float, meter_q: float) -> bool:
    return abs(total - meter_q) <= 1e-3


def _tuplet_complete(group: Tuplet, events: list[Event]) -> bool:
    """True when the notes and rests found under a tuplet number add up to n equal units
    (three eighths under a 3, a quarter and an eighth under a 3 ...): nothing is missing, so
    its usual ratio is right. Otherwise a member went unseen or the number belongs to
    something else, and another ratio may be what the bar needs."""
    total = sum(e.base for e in events if e.group is group)
    if total <= 0 or group.n <= 0:
        return False
    unit = total / group.n
    return abs(math.log2(unit) - round(math.log2(unit))) < 0.01


def _repair_staff(
    events: list[Event],
    meter_q: float,
    anchors: tuple[tuple[float, float], ...] = (),
) -> float | None:
    """Re-read a staff that does not add up to its bar, in the ways a score is likely to be
    misread, and keep the first reading that fills the bar exactly (the fewest changes
    first): notes moved aside by another voice count as starting with their neighbours, and
    a tuplet may be a quintuplet or the like rather than a triplet. ``anchors``: times of
    columns known from another staff (see ``_layout_staff``).

    Returns the staff's length, or None (the events then keep the plain reading)."""
    groups: list[Tuplet] = []
    for e in events:
        if e.group is not None and all(e.group is not g for g in groups):
            groups.append(e.group)
    merges = _merge_candidates(events)
    if not merges and not groups and not anchors:
        return None
    # boundaries between voices that stand apart (opposite stems) are the likelier merges
    cols0 = _columns(events)

    def crossed(i: int) -> bool:
        a = {_stem_dir(e) for e in cols0[i]} - {""}
        b = {_stem_dir(e) for e in cols0[i + 1]} - {""}
        return ("u" in a and "d" in b) or ("d" in a and "u" in b)

    merge_sets: list[frozenset[int]] = [frozenset()]
    if merges:
        if len(merges) <= 6:
            for mask in range(1, 1 << len(merges)):
                merge_sets.append(frozenset(m for i, m in enumerate(merges) if mask >> i & 1))
        else:
            # many candidates: one or two at a time, or all
            merge_sets.append(frozenset(merges))
            merge_sets.extend(frozenset([m]) for m in merges)
            merge_sets.extend(frozenset([a, b]) for i, a in enumerate(merges) for b in merges[i + 1 :])
    merge_sets.sort(key=lambda ms: (sum(1 for m in ms if not crossed(m)), len(ms)))
    # (group, ratio) changes: none first, then one group at a time. With anchors the times of
    # the columns are given by another staff, and a different ratio would only make the end fit.
    changes: list[tuple[Tuplet, float] | None] = [None]
    if not anchors:
        for g in groups:
            if _tuplet_complete(g, events):
                continue  # a tuplet with all its notes is read as written
            for r in TUPLET_RATIOS:
                if abs(r - g.default) > 1e-9:
                    changes.append((g, r))
    order = [(m, c) for c in changes for m in merge_sets]

    def cost(mc: tuple[frozenset[int], tuple[Tuplet, float] | None]) -> int:
        m, c = mc
        return sum(1 for i in m if not crossed(i)) * 2 + len(m) + (0 if c is None else 3)

    order.sort(key=cost)

    def set_ratio(group: Tuplet, ratio: float) -> None:
        group.ratio = ratio
        for ev in events:
            if ev.group is group:
                ev.set_ratio()

    for merge, change in order[:MAX_READINGS]:
        if not merge and change is None and not anchors:
            continue  # the plain reading is the caller's
        if change is not None:
            set_ratio(*change)
        total = _layout_staff(events, merge, anchors)
        if _exact(total, meter_q):
            return total
        if change is not None:
            set_ratio(change[0], change[0].default)
    return None


def _voice_split(events: list[Event]) -> tuple[list[Event], list[Event]] | None:
    """Upper / lower voice of a two-voice staff: stems up / down, rests by height."""
    up: list[Event] = []
    down: list[Event] = []
    amb: list[Event] = []
    for e in events:
        stem = e.heads[0].stem if e.heads else None
        if stem is not None:
            (up if stem.up else down).append(e)
        elif e.heads:
            (up if e.y >= e.staff.mid else down).append(e)
        elif e.y > e.staff.mid + 0.9 * e.staff.space:
            up.append(e)
        elif e.y < e.staff.mid - 0.9 * e.staff.space:
            down.append(e)
        else:
            amb.append(e)
    if not up or not down:
        return None
    if len(amb) > 4:
        return None
    best: tuple[float, list[Event], list[Event]] | None = None
    for mask in range(1 << len(amb)):
        a = up + [e for i, e in enumerate(amb) if mask >> i & 1]
        b = down + [e for i, e in enumerate(amb) if not mask >> i & 1]
        # both voices of a bar last equally long
        diff = abs(_layout_staff(a) - _layout_staff(b))
        if best is None or diff < best[0]:
            best = (diff, a, b)
    assert best is not None
    return best[1], best[2]


def _unmarked_tuplets(events: list[Event], meter_q: float) -> float | None:
    # Preserve a complete reading of unmarked members first. Combining a
    # labelled quarter+sixteenth with neighbouring unmarked sixteenths can
    # obscure the latter's three equal units in an otherwise mixed beam.
    result = _unmarked_tuplet_pass(events,meter_q,include_partial=False)
    if result is not None:
        return result
    return _unmarked_tuplet_pass(events,meter_q,include_partial=True)


def _unmarked_tuplet_pass(events: list[Event], meter_q: float, *, include_partial: bool) -> float | None:
    """A staff too long for its bar whose beam groups of 3 (or 6) equal notes are triplets
    (sextuplets) printed without their number, as after the first few bars of a run of
    triplets. The groups are read as tuplets, all of them or, failing that, as few as
    needed; the reading must fill the bar exactly. Returns the staff's length, or None
    (the events are left as they were)."""
    by_group: dict[int, list[Event]] = defaultdict(list)
    for e in events:
        compatible = e.group is None or (include_partial and e.group.n == 3 and abs(e.group.ratio-2/3) < 1e-8)
        if e.beam_group and compatible and e.heads and not e.grace:
            by_group[e.beam_group].append(e)
    cands: list[list[Event]] = []
    for grp in by_group.values():
        cols = _columns(grp)
        if any(e.group is not None for e in grp):
            # Sparse labels may cover a continuous beam of two triples.
            # An incomplete label in only three notes or a mixed-value beam
            # is ambiguous: don't alter preceding plain notes to compensate
            # for a missing member elsewhere in that labelled tuplet.
            if len(cols)==6 and len({round(e.base,4) for e in grp})==1 and grp[0].base<=.5:
                cands.append(grp)
            continue
        if len(cols) in (3, 6) and len({round(e.base, 4) for e in grp}) == 1 and grp[0].base <= 0.5:
            cands.append(grp)
            continue
        # a run of 3 (or 6) equal short notes opening a longer beam group (16th 16th 16th
        # eighth: the triplet of 16ths and a plain eighth)
        for n in (6, 3):
            if len(cols) > n:
                head = [e for c in cols[:n] for e in c]
                if len({round(e.base, 4) for e in head}) != 1 or head[0].base > 0.25:
                    continue
                if cols[n][0].base != head[0].base or (n == 3 and len(cols) == 5):
                    # (5 equal notes: a triplet and two plain ones, as 3 + 2 16ths make a beat
                    # and a half of a beat)
                    cands.append(head)
                    break
    if not cands:
        return None
    saved = [(e, e.length, e.t) for e in events]

    def apply(chosen: list[list[Event]]) -> float:
        for e, length, _t in saved:
            e.length = length
        for grp in chosen:
            for e in grp:
                e.length = e.base * 2 / 3
        return _layout_staff(events)

    options: list[list[list[Event]]] = [cands]
    if len(cands) <= 8:
        from itertools import combinations

        for k in range(1, len(cands)):
            options.extend([list(c) for c in combinations(cands, k)])
    for chosen in options[:256]:
        total = apply(chosen)
        if _exact(total, meter_q):
            return total
    for e, length, t in saved:
        e.length, e.t = length, t
    return None


def _layout_staff_robust(events: list[Event], meter_q: float) -> float:
    total = _layout_staff(events)
    if _exact(total, meter_q):
        return total
    repaired = _repair_staff(events, meter_q)
    if repaired is not None:
        return repaired
    _layout_staff(events)  # back to the plain reading (a failed repair leaves its own onsets)
    if total > meter_q * 1.04:
        unmarked = _unmarked_tuplets(events, meter_q)
        if unmarked is not None:
            return unmarked
        # a whole-bar rest of a resting voice, printed above or below the staff and read as
        # a half rest (on an outline page the two are told apart only by the ledger line):
        # it takes no part in the timing of the voice that plays
        outside = [
            e for e in events
            if not e.heads and e.base >= 2.0 and e.group is None
            and (e.y > e.staff.top + 0.2 * e.staff.space or e.y < e.staff.bottom - 0.2 * e.staff.space)
        ]
        for r in outside:
            rest = [e for e in events if e is not r]
            if _exact(_layout_staff(rest), meter_q):
                r.t, r.length, r.full_bar_rest = 0.0, meter_q, True
                return meter_q
        _layout_staff(events)
    if total <= meter_q * 1.04:
        return total
    # Too long for the meter. Usually the staff holds two voices whose events interleave
    # (e.g. rests of a second voice printed above the staff): time each voice on its own.
    voice_state = [(e,e.length,e.t) for e in events]
    split = _voice_split(events)
    if split is not None:
        a, b = split
        ta = _layout_staff(a)
        tb = _layout_staff(b)
        # A continuous triplet accompaniment can share its staff with a
        # separately timed melody/rests. It must fill the bar on its own;
        # trying to repair the combined interleaving cannot establish this.
        if ta > meter_q*1.04:
            fixed = _unmarked_tuplets(a,meter_q)
            if fixed is not None:
                ta = fixed
        if tb > meter_q*1.04:
            fixed = _unmarked_tuplets(b,meter_q)
            if fixed is not None:
                tb = fixed
        sounding_a = any(e.heads for e in a)
        sounding_b = any(e.heads for e in b)
        ok_a = not sounding_a or _fits(ta, meter_q)
        ok_b = not sounding_b or _fits(tb, meter_q)
        if ok_a and ok_b:
            return meter_q
        for event,length,start in voice_state:
            event.length,event.t = length,start
    # Rests printed out of place: lay out the notes alone
    notes = [e for e in events if e.heads]
    if notes and len(notes) < len(events):
        tn = _layout_staff(notes)
        if _fits(tn, meter_q):
            return tn
    return _layout_staff(events)


def _anchors(staves: list[list[Event]]) -> tuple[tuple[float, float], ...]:
    """(x, time) of the columns of staves that add up. Where two staves disagree about a
    column the column is left out."""
    found: list[tuple[float, float]] = []
    for voice in staves:
        for col in _columns(voice):
            xs = sorted(e.x for e in col)
            ts = [e.t for e in col]
            if max(ts) - min(ts) <= TIME_EPS:
                found.append((xs[len(xs) // 2], ts[0]))
    found.sort()
    out: list[tuple[float, float]] = []
    clash = False
    for x, t in found:
        if out and abs(x - out[-1][0]) <= 2.2:
            if abs(t - out[-1][1]) > 1e-3:
                clash = True
            continue
        if clash:
            out.pop()
            clash = False
        out.append((x, t))
    if clash:
        out.pop()
    return tuple(out)


def _beam_time_anchors(events: list[Event], anchors: tuple[tuple[float,float],...],
                       meter_q: float) -> tuple[tuple[float,float],...]:
    """A known time inside one beam fixes the neighbouring notes of that voice."""
    groups: dict[int,list[Event]] = defaultdict(list)
    for e in events:
        if e.beam_group and e.heads and not e.grace:
            groups[e.beam_group].append(e)
    extra = []
    for members in groups.values():
        cols = _columns(members)
        if len(cols)<2:
            continue
        if any(not e.heads and cols[0][0].x < e.x < cols[-1][0].x for e in events):
            continue
        if any(max(e.length for e in c)-min(e.length for e in c)>TIME_EPS for c in cols):
            continue
        offsets=[]
        elapsed=0.0
        for col in cols:
            offsets.append(elapsed)
            elapsed += col[0].length
        starts=[]
        for col,offset in zip(cols,offsets):
            x=col[0].x
            known=[(ax,t) for ax,t in anchors if abs(ax-x)<=2.2]
            if known:
                starts.append(min(known,key=lambda a:abs(a[0]-x))[1]-offset)
        if not starts or max(starts)-min(starts)>.005:
            continue
        start=sum(starts)/len(starts)
        if start < -TIME_EPS or start+elapsed>meter_q+TIME_EPS:
            continue
        extra.extend((col[0].x,max(0.0,start+offset)) for col,offset in zip(cols,offsets))
    return tuple(sorted(set(anchors+tuple(extra)))) if extra else anchors


def _layout_bar(bar: Bar, *, _allow_cross_staff: bool = True) -> float:
    """Give every event an onset (quarters from the bar start); return the bar length."""
    meter_q = bar.meter[0] * 4.0 / bar.meter[1]
    normal = [e for e in bar.events if not e.grace]
    if not normal:
        bar.length = meter_q
        return meter_q
    staves: dict[int, list[Event]] = defaultdict(list)
    for e in normal:
        staves[e.staff.index].append(e)
    # One beam can carry a voice between the two staves. Its printed staff is
    # still needed for pitch and part assignment, but must not split its clock.
    by_beam: dict[int,list[Event]] = defaultdict(list)
    for e in normal:
        if e.beam_group and e.heads:
            by_beam[e.beam_group].append(e)
    routes=[]
    for members in by_beam.values():
        counts=Counter(e.staff.index for e in members)
        ranked=counts.most_common()
        if len(ranked)<2 or ranked[0][1]==ranked[1][1]:
            continue
        # Nearby beams of two independent voices can touch in the drawing.
        # A cross-staff run proceeds from one staff to the other; simultaneous
        # columns on both staves are evidence of independent voices instead.
        if any(len({e.staff.index for e in col}) > 1 for col in _columns(members)):
            continue
        routes.append((ranked[0][0],members))
    cross_staff=bool(routes) and _allow_cross_staff
    original=[]
    groups={}
    if cross_staff:
        original=[(e,e.length,e.t) for e in bar.events]
        groups={id(e.group):(e.group,e.group.ratio) for e in bar.events if e.group is not None}
        baseline_length=_layout_bar(bar,_allow_cross_staff=False)
        # A complete existing reading takes precedence over geometric inference.
        if all(_exact(total,meter_q) for _,total in bar.totals):
            return baseline_length
        for e,length,t in original:
            e.length,e.t=length,t
        for group,ratio in groups.values():
            group.ratio=ratio
    for owner,members in routes if cross_staff else []:
        ids={id(e) for e in members}
        for staff,voice in staves.items():
            if staff != owner:
                voice[:]=[e for e in voice if id(e) not in ids]
        staves[owner].extend(e for e in members if e.staff.index!=owner)
    if cross_staff:
        # A rest introducing a cross-staff run can itself be printed near the
        # other staff. Match it to the following beam's rhythmic value and
        # spacing, not just to the closest five staff lines.
        for rest in (e for e in normal if not e.heads and 0 < e.length <= .5):
            candidates=[]
            for members in by_beam.values():
                counts=Counter(e.staff.index for e in members).most_common()
                if len(counts)>1 and counts[0][1]==counts[1][1]:
                    continue
                cols=_columns(members)
                if len(cols)<2 or any(abs(e.length-rest.length)>TIME_EPS for e in members):
                    continue
                gaps=sorted(b[0].x-a[0].x for a,b in zip(cols,cols[1:]))
                step=gaps[len(gaps)//2]
                gap=cols[0][0].x-rest.x
                if step>0 and .5*step <= gap <= 1.8*step:
                    candidates.append((gap,counts[0][0]))
            if not candidates:
                continue
            _,owner=min(candidates)
            for staff,voice in staves.items():
                if staff!=owner:
                    voice[:]=[e for e in voice if e is not rest]
            if not any(e is rest for e in staves[owner]):
                staves[owner].append(rest)
    staves={staff:voice for staff,voice in staves.items() if voice}
    voices = list(staves.values())
    # A voice that rests the whole bar shows one rest, centred in the bar: next to the notes
    # of another voice of its staff it takes no part in their timing.
    for voice in voices:
        others = [e for e in voice if not e.full_bar_rest]
        if others and len(others) < len(voice):
            for e in voice:
                if e.full_bar_rest:
                    e.t = 0.0
            voice[:] = others
    # a whole rest alone on its staff fills the bar whatever the meter, but it must not
    # decide the bar's length (a pickup bar has a whole rest in the silent hand)
    rest_only = [len(v) == 1 and v[0].full_bar_rest for v in voices]
    totals = [_layout_staff_robust(voice, meter_q) for voice in voices]
    # Events of every staff printed at one x start together: the staves that add up give the
    # columns the others can be timed by (a voice with hidden rests, a tuplet of unusual size).
    reliable = [i for i, t in enumerate(totals) if not rest_only[i] and _exact(t, meter_q)]
    if reliable and len(reliable) < len([r for r in rest_only if not r]):
        anchors = _anchors([voices[i] for i in reliable])
        for i, voice in enumerate(voices):
            if rest_only[i] or _exact(totals[i], meter_q):
                continue
            before = [(e, e.t) for e in voice]
            voice_anchors = _beam_time_anchors(voice,anchors,meter_q) if cross_staff else anchors
            repaired = _repair_staff(voice, meter_q, voice_anchors)
            if repaired is None:
                for e, t0 in before:
                    e.t = t0
            else:
                totals[i] = repaired
    if cross_staff and any(not rest_only[i] and not _exact(total,meter_q) for i,total in enumerate(totals)):
        # Moving a voice must resolve the entire bar. A partly repaired bar
        # would otherwise squeeze unrelated notes or shorten the next bar.
        for e,length,t in original:
            e.length,e.t=length,t
        for group,ratio in groups.values():
            group.ratio=ratio
        return _layout_bar(bar,_allow_cross_staff=False)
    bar.totals = list(zip(staves,totals))
    sounding = [t for t, r in zip(totals, rest_only) if not r]
    longest = max(sounding) if sounding else meter_q
    if longest > meter_q * 1.04:
        # more written than the meter allows (an unmarked tuplet, a missed dot elsewhere):
        # squeeze only the staves that overflow
        for voice, total in zip(voices, totals):
            if total > meter_q * 1.04:
                scale = meter_q / total
                for e in voice:
                    e.t *= scale
                    e.length *= scale
        length = meter_q
    elif longest < meter_q * 0.96:
        length = longest  # pickup / short bar: keep its own length
    else:
        length = meter_q
    # grace notes: just before the next normal event of the same staff; several grace
    # notes before one note are played one after another, each a 32nd long
    runs: dict[int, list[Event]] = defaultdict(list)
    targets: dict[int, float] = {}
    for g in [e for e in bar.events if e.grace]:
        after = [e for e in normal if e.staff is g.staff and e.x > g.x]
        target = min(after, key=lambda e: e.x) if after else None
        key = id(target) if target is not None else -id(g.staff)
        runs[key].append(g)
        targets[key] = target.t if target is not None else length
    for key, run in runs.items():
        columns = sorted({round(g.x, 1) for g in run})
        for g in run:
            steps = len(columns) - columns.index(round(g.x, 1))
            g.t = max(0.0, targets[key] - GRACE_STEP * steps)
            g.length = GRACE_STEP
    bar.length = length
    return length


@dataclass
class ScoreNote:
    pitch: int
    start_q: float
    end_q: float
    staff: int
    grace: bool = False
    arpeggio: bool = False


@dataclass
class VectorScore:
    notes: list[ScoreNote]
    meters: list[tuple[float, tuple[int, int]]]
    bpm: float
    bars: int
    timeline: list = field(default_factory=list)  # (start_q, Bar) as played
    tempo_printed: bool = True  # the opening tempo is recognized, rather than defaulted
    tempos: list = field(default_factory=list)  # (start_q, bpm) changes after the first
    key_signatures: list[tuple[float, int, int | None]] = field(default_factory=list)  # q, staff, fifths


def _signature_fifths(signature: dict[int, int]) -> int | None:
    """A conventional signature, kept distinct from note-local accidentals."""
    signature = {step: alter for step, alter in signature.items() if alter}
    if not signature:
        return 0
    for sign, order in ((1, (3, 0, 4, 1, 5, 2, 6)), (-1, (6, 2, 5, 1, 4, 0, 3))):
        for count in range(1, 8):
            if signature == {step: sign for step in order[:count]}:
                return sign * count
    return None


def _number_voltas(bars: list[Bar]) -> None:
    """Give unnumbered endings their number: the ending closed by a repeat sign is the 1st,
    an ending right after a repeat sign is the next one."""
    if not any(bar.repeat_end for bar in bars):
        for bar in bars:
            bar.volta = 0  # endings only mean something with repeat signs
        return
    # a bracket carried over to a new line belongs to the ending that ran to the end of the
    # previous line; any other unhooked line there is not an ending
    carried: set[int] = set()
    for k, bar in enumerate(bars):
        if bar.volta == VOLTA_CONTINUED:
            prev = bars[k - 1] if k > 0 else None
            new_line = prev is not None and (prev.page, prev.system) != (bar.page, bar.system)
            ok = prev is not None and prev.volta and (new_line or k - 1 in carried) and not prev.repeat_end
            bar.volta = prev.volta if ok else 0
            if ok:
                carried.add(k)
    # A 1st ending lasts until its closing repeat sign. Some exports draw its bracket over
    # the first bar(s) only, or leave its continuation on the next line undrawn, so it is
    # carried on to the repeat sign (when nothing else intervenes).
    for k, bar in enumerate(bars):
        if not bar.volta or bar.repeat_end or k + 1 >= len(bars) or bars[k + 1].volta:
            continue
        first = bar.volta == 1 or (bar.volta == VOLTA_UNKNOWN and not any(b.repeat_end for b in bars[max(0, k - 1) : k]))
        if not (first or bar.volta_open):
            continue
        limit = len(bars) if first else min(len(bars), k + 9)
        stop = next((j for j in range(k + 1, limit) if bars[j].repeat_end or bars[j].volta or bars[j].repeat_start), None)
        if stop is not None and bars[stop].repeat_end and not bars[stop].volta and not bars[stop].repeat_start:
            for j in range(k + 1, stop + 1):
                bars[j].volta = bar.volta
    i = 0
    last = 0
    while i < len(bars):
        if not bars[i].volta:
            i += 1
            continue
        j = i
        while j + 1 < len(bars) and bars[j + 1].volta == bars[i].volta and not bars[j].repeat_end:
            j += 1
        number = bars[i].volta
        after_repeat = i > 0 and bars[i - 1].repeat_end
        if number == VOLTA_UNKNOWN:
            number = last + 1 if after_repeat and last else 1
        # a 1st ending needs a repeat sign at or after it; a later ending one before it
        valid = any(b.repeat_end for b in bars[i:]) if number == 1 else any(b.repeat_end for b in bars[:i])
        for bar in bars[i : j + 1]:
            bar.volta = number if valid else 0
        if valid:
            last = number
        i = j + 1


def _expand_repeats(bars: list[Bar]) -> list[Bar]:
    """Play out repeat signs and 1st / 2nd endings.

    An ending bar is played only on its pass: "1" the first time through the repeated
    section, "2" the second time; 2nd-ending bars right after the closing repeat sign
    still belong to the second pass.
    """
    order: list[Bar] = []
    i = 0
    start = 0
    # a repeat back to the beginning skips a pickup bar: its notes are the upbeat that the
    # 1st ending (made short to match) leads into again
    if len(bars) > 1 and not bars[0].repeat_start:
        first = bars[0]
        full = first.meter[0] * 4.0 / first.meter[1]
        if first.length < full * 0.96 and any(
            b.volta == 1 and b.length < full * 0.96 for b in bars[1:]
        ):
            start = 1
    pass_no = 1
    used_ends: set[int] = set()
    after_section = False
    guard = 0
    while i < len(bars) and guard < 20000:
        guard += 1
        bar = bars[i]
        if bar.repeat_start and not (i == start and pass_no == 2):
            start, pass_no = i, 1
        if after_section and not bar.volta:
            pass_no, after_section = 1, False
        if bar.volta and bar.volta != pass_no:
            if bar.repeat_end and i in used_ends:
                # the skipped 1st ending closed the section: what follows is new music
                start, after_section = i + 1, True
            i += 1
            continue
        order.append(bar)
        if bar.repeat_end and i not in used_ends:
            used_ends.add(i)
            pass_no = 2
            i = start
            continue
        if bar.repeat_end:
            start = i + 1
            after_section = True
        i += 1
    return order


def _bar_tempo_changes(bar: Bar) -> list[tuple[float, float]]:
    """Metronome marks at the nearest decoded onset, without assuming linear spacing.

    Engraving stretches space for accidentals, lyrics and short notes. Its x coordinate
    is not a musical clock. An aligned note/rest column gives us its decoded time; a
    crowded mark between columns is conservatively snapped to the closest column.
    A whole-bar rest is centred, so it cannot anchor a later speed over another voice.
    """
    changes = {0.0: bar.tempo} if bar.tempo > 0 else {}
    normal = [e for e in bar.events if not e.grace and not e.full_bar_rest]
    anchors = _anchors([normal]) if normal else ()
    for x, mark in sorted(bar.tempo_positions, key=lambda pair: pair[0]):
        if mark <= 0:
            continue
        if not anchors or x <= anchors[0][0]:
            onset = 0.0
        else:
            onset = min(anchors, key=lambda pair: (abs(pair[0] - x), pair[1]))[1]
        changes[max(0.0, onset)] = mark
    return sorted(changes.items())


def build_score(all_bars: list[Bar], bpm: float) -> VectorScore:
    for bar in all_bars:
        _layout_bar(bar)
    # a short first bar is a pickup; a short bar elsewhere usually means missed notes
    for k, bar in enumerate(all_bars):
        meter_q = bar.meter[0] * 4.0 / bar.meter[1]
        if k > 0 and bar.length < meter_q * 0.96 and not bar.repeat_end and not bar.final and k != len(all_bars) - 1:
            bar.length = meter_q
    local_tempos = {id(bar): _bar_tempo_changes(bar) for bar in all_bars}
    first_changes = local_tempos[id(all_bars[0])] if all_bars else []
    opening_mark = next((mark for offset, mark in first_changes if offset <= TIME_EPS), 0.0)
    tempo_printed = opening_mark > 0 or bpm > 0
    opening_bpm = opening_mark or bpm or DEFAULT_BPM
    # The entry speed and later changes are distinct. Repeating a bar with a
    # change on beat three must restore its entry speed before replaying that change.
    source_tempos: dict[int, float] = {}
    inherited = opening_bpm
    for bar in all_bars:
        entry = inherited
        for offset, mark in local_tempos[id(bar)]:
            if offset <= TIME_EPS:
                entry = mark
            inherited = mark
        source_tempos[id(bar)] = entry
    _number_voltas(all_bars)
    played = _expand_repeats(all_bars)
    notes: list[ScoreNote] = []
    meters: list[tuple[float, tuple[int, int]]] = []
    # A pickup (short first bar) is written as a full bar that starts with silence, so the
    # bar lines of the MIDI (and of the keyboard score made from it) match the print.
    q = 0.0
    if played:
        first = played[0]
        full = first.meter[0] * 4.0 / first.meter[1]
        if first.length < full * 0.96:
            q = full - first.length
    meters.append((0.0, played[0].meter if played else (4, 4)))
    held: dict[tuple[int, int], ScoreNote] = {}
    silent_rests: dict[int, list[tuple[float, float]]] = defaultdict(list)
    timeline: list = []
    tempos: list[tuple[float, float]] = []
    current = opening_bpm
    performed_entries: dict[int, float] = {}
    key_signatures: list[tuple[float, int, int | None]] = []
    active_keys: dict[int, int | None] = {}
    source_indexes = {id(bar): index for index, bar in enumerate(all_bars)}
    previous_source_index: int | None = None
    for bar in played:
        timeline.append((q, bar))
        source_index = source_indexes[id(bar)]
        backward_jump = previous_source_index is None or source_index <= previous_source_index
        # Restore inherited tempo only on a backward repeat. A forward skip
        # over the first ending must not import a tempo that was not played.
        desired_tempo = performed_entries.get(id(bar), source_tempos[id(bar)]) if backward_jump else current
        previous_source_index = source_index
        if abs(desired_tempo - current) > 1e-6:
            tempos.append((q, desired_tempo))
            current = desired_tempo
        # A later repeat may begin after a skipped first ending. Its actual
        # first entry can differ from the linear written-source context.
        # Snapshot the entry before mid-bar changes, with explicit q=0 first.
        entry_tempo = next((mark for offset, mark in local_tempos[id(bar)] if offset <= TIME_EPS), current)
        performed_entries.setdefault(id(bar), entry_tempo)
        for offset, mark in local_tempos[id(bar)]:
            if abs(mark - current) > 1e-6:
                tempos.append((q + offset, mark))
                current = mark
        explicit_staffs = {ev.staff.index for ev in bar.events if ev.key_signature is not None}
        key_candidates = [(q, staff, _signature_fifths(signature)) for staff, signature in enumerate(bar.keys)
                          if staff not in explicit_staffs]
        key_candidates += [(q+ev.t, ev.staff.index, _signature_fifths(ev.key_signature))
                           for ev in bar.events if ev.key_signature is not None]
        for key_q, staff, fifths in sorted(key_candidates, key=lambda mark: (mark[0], mark[1])):
            if staff not in active_keys or active_keys[staff] != fifths:
                key_signatures.append((key_q, staff, fifths))
                active_keys[staff] = fifths
        if not meters or meters[-1][1] != bar.meter:
            meters.append((q, bar.meter))
        normal = [e for e in bar.events if not e.grace]
        for rest in (e for e in normal if not e.heads and e.length > TIME_EPS):
            # A second voice's rest must not stop the voice that is sounding.
            # Only a rest with no overlapping notes on its staff is evidence
            # of silence across which a tie cannot continue.
            total = dict(bar.totals).get(rest.staff.index)
            if total is None or not _exact(total, bar.meter[0]*4.0/bar.meter[1]):
                continue  # uncertain/repaired timing is not evidence of a real gap
            overlaps = any(e.heads and e.staff.index == rest.staff.index
                           and max(e.t, rest.t) < min(e.t+e.length, rest.t+rest.length)-TIME_EPS
                           for e in normal)
            if not overlaps:
                silent_rests[rest.staff.index].append((q+rest.t, q+rest.t+rest.length))
        for ev in sorted(bar.events, key=lambda e: (e.t, e.x)):
            if ev.trem_second:
                continue  # played by its partner
            if ev.repeat_sub > 0 and ev.heads and not ev.grace:
                total = ev.length + (ev.trem_partner.length if ev.trem_partner else 0.0)
                sub = ev.repeat_sub
                steps = max(1, int(round(total / sub)))
                chords = [ev.heads, ev.trem_partner.heads] if ev.trem_partner else [ev.heads]
                for k in range(steps):
                    t0 = q + ev.t + k * sub
                    for h in chords[k % len(chords)]:
                        if k == 0 and h.tied:
                            previous = held.get((ev.staff.index, h.pitch))
                            interrupted = previous is not None and any(
                                start < t0-TIME_EPS and end > previous.end_q+TIME_EPS
                                for start,end in silent_rests[ev.staff.index])
                            if previous is not None and previous.end_q >= t0-TIE_GAP and not interrupted:
                                previous.end_q = max(previous.end_q, t0 + sub)
                                continue
                        n = ScoreNote(h.pitch, t0, t0 + sub, ev.staff.index)
                        notes.append(n)
                        held[(ev.staff.index, h.pitch)] = n
                continue
            for h in ev.heads:
                key = (ev.staff.index, h.pitch)
                onset = q + ev.t
                # Allow small recognition gaps in tied note values, but never
                # fill an explicitly silent rest or delete an orphan note.
                previous = held.get(key)
                interrupted = h.tied and previous is not None and any(
                    start < onset-TIME_EPS and end > previous.end_q+TIME_EPS
                    for start,end in silent_rests[ev.staff.index])
                if h.tied and previous is not None and previous.end_q >= onset-TIE_GAP and not interrupted:
                    held[key].end_q = max(held[key].end_q, onset + ev.length)
                    continue
                n = ScoreNote(
                    h.pitch,
                    q + ev.t,
                    q + ev.t + max(ev.length, 0.0625),
                    ev.staff.index,
                    grace=ev.grace,
                    arpeggio=h.arpeggio and not ev.grace,
                )
                notes.append(n)
                held[key] = n
        q += bar.length
    if not notes:
        raise VectorScoreError("没有读出音符")
    return VectorScore(notes, meters, opening_bpm, len(played), timeline,
                       tempo_printed=tempo_printed, tempos=tempos, key_signatures=key_signatures)


def _tempo_marks(drawing: PageDrawing) -> list[tuple[float, float, float]]:
    """Metronome marks of a page as (x, y, bpm), in reading order: a note glyph followed
    by '= 72'. The bpm is in quarter notes (what a MIDI tempo counts): a half-note, eighth-note
    or dotted mark is converted."""
    out: list[tuple[float, float, float]] = []
    marks = [g for g in drawing.glyphs if g.music and g.code in (0x143, 0x144, 0x145)]
    for m in marks:
        texts = sorted(
            [
                g for g in drawing.glyphs
                if (not g.music or 0x130 <= g.code <= 0x139) and abs(g.y - m.y) <= 4 and 0 <= g.l - m.r <= 40
            ],
            key=lambda g: g.l,
        )
        # a page drawn as outlines may give a digit the shape of a time-signature digit
        s = "".join(str(g.code - 0x130) if g.music else g.text for g in texts)
        found = re.search(r"=\s*(\d{2,3})", s)
        if found:
            beat = {0x143: 1.0, 0x144: 2.0, 0x145: 0.5}[m.code]
            eq = next((g for g in texts if not g.music and g.text == "="), None)
            right = eq.l if eq is not None else m.r + 8
            h = max(m.t - m.b, 1.0)
            dotted = any(
                g.music and g.code == DOT and m.r - 0.5 <= g.l and g.r <= right and m.b - 0.1 * h <= g.y <= m.b + 0.5 * h
                for g in drawing.glyphs
            ) or any(
                ln.x1 - ln.x0 < 0.5 and abs(ln.y1 - ln.y0) < 0.5 and m.r - 0.5 <= ln.x0 <= right and m.b - 0.1 * h <= ln.y0 <= m.b + 0.5 * h
                for ln in drawing.lines
            )
            if dotted:
                beat *= 1.5
            out.append((m.x, m.y, float(found.group(1)) * beat))
    return sorted(out, key=lambda mark: (-mark[1], mark[0]))


def _tempo_of(drawing: PageDrawing) -> float:
    """The first metronome mark of a page, 0 when there is none."""
    marks = _tempo_marks(drawing)
    return marks[0][2] if marks else 0.0


def _mark_tempo_bars(marks: list[tuple[float, float, float]], systems: list[list[Staff]], bars: list[Bar]) -> None:
    """Keep every mark's horizontal position until note/rest onsets are laid out."""
    for x, y, bpm in marks:
        over = [(i, s) for i, s in enumerate(systems) if s[0].top - 1 <= y <= s[0].top + 16 * s[0].space]
        if not over:
            continue
        i, _system = min(over, key=lambda item: y - item[1][0].top)
        line = [b for b in bars if b.system == i]
        if not line:
            continue
        target = next((b for b in line if b.x0 - 2 <= x < b.x1), line[0] if x < line[0].x0 else None)
        if target is not None:
            target.tempo_positions.append((x, bpm))


def read_vector_pdf(pdf: Path, max_pages: int | None = None, cancel=None) -> VectorScore:
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(str(pdf))
    except Exception as exc:  # noqa: BLE001
        raise VectorScoreError("无法打开这个 PDF") from exc
    state = ReaderState()
    all_bars: list[Bar] = []
    try:
        for index in range(len(doc) if max_pages is None else min(len(doc), max_pages)):
            if cancel is not None and cancel.is_set():
                from app.engines.base import CancelledError

                raise CancelledError()
            drawing = read_drawing(doc[index])
            marks = _tempo_marks(drawing)
            staves = find_staves(drawing)
            if not staves:
                continue
            systems = group_systems(drawing, staves)
            page_bars: list[Bar] = []
            for sys_index, system in enumerate(systems):
                above = systems[sys_index - 1][-1].bottom if sys_index > 0 else None
                below = systems[sys_index + 1][0].top if sys_index + 1 < len(systems) else None
                for bar in analyze_system(drawing, system, state, above, below):
                    bar.page, bar.system = index, sys_index
                    page_bars.append(bar)
            _mark_tempo_bars(marks, systems, page_bars)
            all_bars += page_bars
    finally:
        doc.close()
    if not all_bars:
        raise VectorScoreError("没有找到五线谱")
    return build_score(all_bars, 0.0)


TEMPO_NOT_PRINTED = "PianoConvert: tempo not printed"
ANNOTATION = "PianoConvert:"  # same prefix as app.keyboard_score.ANNOTATION


def write_midi(score: VectorScore, dest: Path) -> Path:
    """MIDI of the score, one track per staff ("Staff 1" is the top staff).

    Text events carry what the notes alone cannot say (read by app.keyboard_score):
    which track has the tune, grace notes, rolled chords, and a missing tempo mark (the
    MIDI still needs some tempo: DEFAULT_BPM). Tempo changes printed later in the score
    are written as tempo changes.
    """
    import pretty_midi

    marks = [(0.0, score.bpm)] + [(q, bpm) for q, bpm in score.tempos if q > 1e-6 and bpm > 0]
    starts = [0.0]
    for (q0, bpm0), (q1, _bpm1) in zip(marks, marks[1:]):
        starts.append(starts[-1] + (q1 - q0) * 60.0 / bpm0)

    def seconds(q: float) -> float:
        i = bisect.bisect_right([m[0] for m in marks], q) - 1
        i = max(0, i)
        return starts[i] + (q - marks[i][0]) * 60.0 / marks[i][1]

    pm = pretty_midi.PrettyMIDI(initial_tempo=score.bpm)
    pm.text_events.append(pretty_midi.Text(f"{ANNOTATION}timing:notated",0.0))
    if len(marks) > 1:
        res = pm.resolution
        pm._tick_scales = [(int(round(q * res)), 60.0 / (bpm * res)) for q, bpm in marks]
        last_q = max((n.end_q for n in score.notes), default=0.0)
        pm._update_tick_to_time(int(round((last_q + 8) * res)))
    for q, (num, den) in score.meters:
        pm.time_signature_changes.append(pretty_midi.TimeSignature(num, den, seconds(q)))
    # Standard MIDI carries the upper staff's key. Per-staff annotations keep
    # differing staff signatures and unknown/nonstandard keys from leaking.
    major_pc = (11, 6, 1, 8, 3, 10, 5, 0, 7, 2, 9, 4, 11, 6, 1)
    for q, staff, fifths in score.key_signatures:
        value = str(fifths) if fifths is not None else "?"
        pm.text_events.append(pretty_midi.Text(f"{ANNOTATION}key:Staff {staff+1}:{value}", seconds(q)))
        if staff == 0 and fifths is not None:
            pm.key_signature_changes.append(pretty_midi.KeySignature(major_pc[fifths+7], seconds(q)))
    if not score.tempo_printed:
        pm.text_events.append(pretty_midi.Text(TEMPO_NOT_PRINTED, 0.0))
    staves = sorted({n.staff for n in score.notes})
    if staves:
        pm.text_events.append(pretty_midi.Text(f"{ANNOTATION}melody:Staff {staves[0] + 1}", 0.0))
    rolled: dict[float, set[int]] = defaultdict(set)
    for n in score.notes:
        pitch = max(0, min(127, n.pitch))
        if n.grace:
            pm.text_events.append(pretty_midi.Text(f"{ANNOTATION}grace:{pitch}", seconds(n.start_q)))
        elif n.arpeggio:
            rolled[round(n.start_q, 6)].add(pitch)
    for q, pitches in sorted(rolled.items()):
        if len(pitches) >= 2:
            pm.text_events.append(pretty_midi.Text(f"{ANNOTATION}arpeggio:" + ",".join(map(str, sorted(pitches))), seconds(q)))
    for st in staves:
        inst = pretty_midi.Instrument(program=0, name=f"Staff {st + 1}")
        for n in score.notes:
            if n.staff == st:
                end = max(n.end_q, n.start_q + 0.0625)
                inst.notes.append(pretty_midi.Note(80, max(0, min(127, n.pitch)), seconds(n.start_q), seconds(end)))
        pm.instruments.append(inst)
    dest.parent.mkdir(parents=True, exist_ok=True)
    pm.write(str(dest))
    return dest


def tempo_printed(midi: Path) -> bool:
    """False when the PDF's opening tempo was not recognized (later changes may exist)."""
    import pretty_midi

    try:
        pm = pretty_midi.PrettyMIDI(str(midi))
    except Exception:  # noqa: BLE001
        return True
    return not any(t.text == TEMPO_NOT_PRINTED for t in pm.text_events)
