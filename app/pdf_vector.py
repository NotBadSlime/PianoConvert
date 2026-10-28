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
import re
from collections import defaultdict
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


def _font_name(tp, index: int) -> str:
    import pypdfium2.raw as raw

    buf = ctypes.create_string_buffer(64)
    raw.FPDFText_GetFontInfo(tp.raw, index, buf, 64, None)
    return buf.value.decode("latin-1", errors="replace")


def read_drawing(page) -> PageDrawing:
    import pypdfium2.raw as raw

    width, height = page.get_size()
    tp = page.get_textpage()
    glyphs: list[Glyph] = []
    for i in range(tp.count_chars()):
        ch = tp.get_text_range(i, 1)
        if not ch or not ch.strip():
            continue
        l, b, r, t = tp.get_charbox(i)
        ox, oy = ctypes.c_double(), ctypes.c_double()
        raw.FPDFText_GetCharOrigin(tp.raw, i, ctypes.byref(ox), ctypes.byref(oy))
        font = _font_name(tp, i)
        glyphs.append(
            Glyph(ord(ch[0]), ox.value, oy.value, l, b, r, t, raw.FPDFText_GetFontSize(tp.raw, i), MUSIC_FONT in font, ch)
        )
    lines: list[Line] = []
    curves: list[Curve] = []
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
        if count == 2 and stroke.value and pts[1][0] == 0:
            lines.append(Line(pts[0][1], pts[0][2], pts[1][1], pts[1][2], float(w.value)))
        elif fill.value and count >= 4 and any(p[0] == 1 for p in pts):
            # slur / tie: a filled crescent; its two tips are the first point and the end
            # of the first bezier
            curves.append(Curve(pts[0][1], pts[0][2], pts[3][1], pts[3][2]))
    page_close = getattr(tp, "close", None)
    if page_close:
        page_close()
    return PageDrawing(glyphs, lines, curves, width, height)


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
class Stem:
    line: Line
    up: bool
    heads: list[Head] = field(default_factory=list)
    beams: int = 0
    flags: int = 0
    tuplet: float = 1.0


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

    def score(st: Staff) -> float:
        sp = st.space
        dist = (y - st.top) if y > st.top else (st.bottom - y)
        if dist < 1.25 * sp:
            return 1.0  # right next to the staff: no ledger line needed
        n = int(dist / sp + 0.3)
        ys = [st.top + sp * k for k in range(1, n + 1)] if y > st.top else [st.bottom - sp * k for k in range(1, n + 1)]
        found = sum(1 for ly in ys if any(abs(ln.y0 - ly) <= 0.7 and ln.xmin - 1 <= cx <= ln.xmax + 1 for ln in ledgers))
        return found / len(ys)

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

@dataclass
class StaffState:
    clef: int = CLEF_TREBLE
    key: dict[int, int] = field(default_factory=dict)  # step 0..6 -> alter
    pending_ties: set[int] = field(default_factory=set)  # pitches tied over the line end


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
        # a stem always has a notehead at one of its ends
        for h in heads:
            if -1.5 <= ln.x0 - h.x <= 8.0 and (abs(h.y - ln.ymin) <= 3.0 or abs(h.y - ln.ymax) <= 3.0):
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
    out: list[tuple[float, float, int, bool]] = []
    for ln in drawing.lines:
        if not ln.horizontal or ln.width > 1.6 or ln.xmax - ln.xmin < 4 * space:
            continue
        height = ln.y0 - top.top
        if not (1.2 * space <= height <= 14 * space) or not (top.x0 - 2 <= ln.xmin <= top.x1):
            continue
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
    attached: dict[int, int] = {}
    key_glyphs: list[Glyph] = []
    for g in accidental_glyphs:
        st = staff_of[id(g)]
        # matched by height, not by staff: a head on ledger lines between two staves may
        # belong to the farther staff while its accidental is nearer the other one
        target = [h for h in heads if abs(h.y - g.y) <= 0.3 * h.staff.space and 0 < h.x - g.x <= 16]
        if target:
            h = min(target, key=lambda hh: hh.x - g.x)
            staff_of[id(g)] = h.staff
            # keep the closest accidental per head
            prev = attached.get(id(h))
            attached[id(h)] = ACCIDENTALS[g.code] if prev is None else prev
        else:
            key_glyphs.append(g)

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
            ottavas.append((st, g.l, end + 2, OTTAVA[g.code]))

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

    def stems_touching(bm: Line) -> list[Stem]:
        out = []
        for s in stems:
            x = s.line.x0
            if bm.xmin - 1.0 <= x <= bm.xmax + 1.0:
                y = bm.y_at(min(max(x, bm.xmin), bm.xmax))
                if s.line.ymin - 3.5 <= y <= s.line.ymax + 3.5:
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
        ys: list[float] = []
        for bm in beams:
            if bm.xmin - 1.0 <= x <= bm.xmax + 1.0:
                y = bm.y_at(min(max(x, bm.xmin), bm.xmax))
                if s.line.ymin - 3.5 <= y <= s.line.ymax + 3.5:
                    # only beams near the free end, not ones crossing the heads
                    end = s.line.ymax if s.up else s.line.ymin
                    if abs(y - end) <= 2.8 + 4.6 * 4:
                        ys.append(round(y, 1))
        uniq: list[float] = []
        for y in sorted(ys):
            if not uniq or y - uniq[-1] > 1.5:
                uniq.append(y)
        s.beams = len(uniq)

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
    tuplet_marks = [
        g for g in glyphs
        if not g.music and g.text in "35679" and 13 <= g.size <= 17.5 and x_lo <= g.x <= x_hi and sys_bottom <= g.y <= sys_top
    ]
    for g in tuplet_marks:
        n = int(g.text)
        ratio = {3: 2 / 3, 5: 4 / 5, 6: 4 / 6, 7: 4 / 7, 9: 8 / 9}[n]
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
        if hi - lo < 8:
            # no bracket: the beam group nearest to the number
            near = [bm for bm in beams if bm.xmin - 2 <= g.cx <= bm.xmax + 2 and abs(bm.y_at(g.cx) - g.cy) <= 16]
            if near:
                bm = min(near, key=lambda b: abs(b.y_at(g.cx) - g.cy))
                lo, hi = bm.xmin, bm.xmax
                # extend through beams touching this one at the same height
                for other in beams:
                    if other is not bm and abs(other.y_at(other.xmin) - bm.y_at(other.xmin)) <= 6 and other.xmin <= hi + 1 and other.xmax >= lo - 1:
                        lo, hi = min(lo, other.xmin), max(hi, other.xmax)
            else:
                lo, hi = g.cx - 14, g.cx + 14
        affected = [s for s in stems if lo - 7 <= s.line.x0 <= hi + 1.5 and _nearest_staff(system, s.heads[0].y) is st]
        for s in affected:
            s.tuplet = ratio
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
            if abs(g.x - last.x) <= 1.5 and -1.0 <= g.b - last.t <= 3.0:
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
    for cv in drawing.curves:
        left_x, right_x = min(cv.x0, cv.x1), max(cv.x0, cv.x1)
        ly = cv.y0 if cv.x0 <= cv.x1 else cv.y1
        ry = cv.y1 if cv.x0 <= cv.x1 else cv.y0
        if not (x_lo <= left_x <= x_hi and sys_bottom <= ly <= sys_top):
            continue
        if abs(ly - ry) > 3.5:
            continue
        start = [h for h in heads if -2 <= left_x - h.x <= head_w + 4 and abs(h.y - ly) <= 4.2]
        end = [h for h in heads if -3 <= h.x - right_x <= 4 and abs(h.y - ry) <= 4.2]
        if start and end:
            s_h = min(start, key=lambda h: abs(h.y - ly))
            e_h = min(end, key=lambda h: abs(h.y - ry))
            between = [
                h for h in heads
                if h.staff is s_h.staff and h.step == s_h.step and s_h.x + 2 < h.x < e_h.x - 2
            ]
            # a curve over a repeated note is a slur (phrase), not a tie
            if s_h.step == e_h.step and s_h.staff is e_h.staff and e_h is not s_h and not between:
                e_h.tied = True
        elif start and not end and right_x >= start[0].staff.x1 - 12:
            s_h = min(start, key=lambda h: abs(h.y - ly))
            s_h._tie_out = True  # type: ignore[attr-defined]
        elif end and not start:
            st0 = end[0].staff
            first_x = min((h.x for h in heads if h.staff is st0), default=0)
            e_h = min(end, key=lambda h: abs(h.y - ry))
            if e_h.x <= first_x + 30:
                e_h._tie_in = True  # type: ignore[attr-defined]

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
        length *= s.tuplet
        normal = [h for h in s.heads if (h.x <= s.line.x0 - 2) == s.up] or s.heads
        x = min(h.x for h in normal) if s.up else min(h.x for h in normal)
        grace = all(h.grace for h in s.heads)
        events.append(Event(x, s.heads[0].staff, length, s.heads, grace=grace, y=sum(h.y for h in s.heads) / len(s.heads)))
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
        length = RESTS[g.code] * (2 - 0.5 ** n)
        events.append(Event(g.l, st, length, [], full_bar_rest=g.code == WHOLE_REST and n == 0, y=g.cy))

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
            for ev in sorted([e for e in in_bar if e.staff is st], key=lambda e: e.x):
                for cx, code in sorted(clef_changes.get(st.index, [])):
                    if cx <= ev.x:
                        stt.clef = code
                for kx, sig in key_changes.get(st.index, []):
                    if kx <= ev.x and kx >= a - 30:
                        stt.key = dict(sig)
                base = CLEFS[stt.clef][0]
                for h in ev.heads:
                    dia = base + h.step
                    if id(h) in attached:
                        alters[(dia, 0)] = attached[id(h)]
                    alter = alters.get((dia, 0), stt.key.get(dia % 7, 0))
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


def _layout_staff(events: list[Event]) -> float:
    """Onsets of one staff's events, read left to right.

    Events printed at the same x start together (chords and the voices of a two-voice
    staff). The next column starts when the earliest still-sounding event ends.
    """
    cols: list[list[Event]] = []
    for e in sorted(events, key=lambda ev: ev.x):
        if cols and e.x - cols[-1][0].x <= 2.2:
            cols[-1].append(e)
        else:
            cols.append([e])
    t = 0.0
    active: list[float] = []
    total = 0.0
    for i, col in enumerate(cols):
        for e in col:
            e.t = t
            active.append(t + e.length)
            total = max(total, t + e.length)
        if i + 1 < len(cols):
            later = [end for end in active if end > t + 1e-6]
            t = min(later) if later else t + min(e.length for e in col)
    return max(total, t)


def _fits(total: float, meter_q: float) -> bool:
    return abs(total - meter_q) <= 0.04 * meter_q


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


def _layout_staff_robust(events: list[Event], meter_q: float) -> float:
    total = _layout_staff(events)
    if total <= meter_q * 1.04:
        return total
    # Too long for the meter. Usually the staff holds two voices whose events interleave
    # (e.g. rests of a second voice printed above the staff): time each voice on its own.
    split = _voice_split(events)
    if split is not None:
        a, b = split
        ta = _layout_staff(a)
        tb = _layout_staff(b)
        sounding_a = any(e.heads for e in a)
        sounding_b = any(e.heads for e in b)
        ok_a = not sounding_a or _fits(ta, meter_q)
        ok_b = not sounding_b or _fits(tb, meter_q)
        if ok_a and ok_b:
            return meter_q
    # Rests printed out of place: lay out the notes alone
    notes = [e for e in events if e.heads]
    if notes and len(notes) < len(events):
        tn = _layout_staff(notes)
        if _fits(tn, meter_q):
            return tn
    return _layout_staff(events)


def _layout_bar(bar: Bar) -> float:
    """Give every event an onset (quarters from the bar start); return the bar length."""
    meter_q = bar.meter[0] * 4.0 / bar.meter[1]
    normal = [e for e in bar.events if not e.grace]
    if not normal:
        bar.length = meter_q
        return meter_q
    staves: dict[int, list[Event]] = defaultdict(list)
    for e in normal:
        staves[e.staff.index].append(e)
    voices = list(staves.values())
    # a whole rest alone on its staff fills the bar whatever the meter, but it must not
    # decide the bar's length (a pickup bar has a whole rest in the silent hand)
    rest_only = [len(v) == 1 and v[0].full_bar_rest for v in voices]
    totals = [_layout_staff_robust(voice, meter_q) for voice in voices]
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
    tempo_printed: bool = True


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


def build_score(all_bars: list[Bar], bpm: float) -> VectorScore:
    for bar in all_bars:
        _layout_bar(bar)
    # a short first bar is a pickup; a short bar elsewhere usually means missed notes
    for k, bar in enumerate(all_bars):
        meter_q = bar.meter[0] * 4.0 / bar.meter[1]
        if k > 0 and bar.length < meter_q * 0.96 and not bar.repeat_end and not bar.final and k != len(all_bars) - 1:
            bar.length = meter_q
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
    timeline: list = []
    for bar in played:
        timeline.append((q, bar))
        if not meters or meters[-1][1] != bar.meter:
            meters.append((q, bar.meter))
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
                            continue
                        n = ScoreNote(h.pitch, t0, t0 + sub, ev.staff.index)
                        notes.append(n)
                        held[(ev.staff.index, h.pitch)] = n
                continue
            for h in ev.heads:
                key = (ev.staff.index, h.pitch)
                if h.tied and key in held:
                    held[key].end_q = max(held[key].end_q, q + ev.t + ev.length)
                    continue
                if h.tied:
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
    return VectorScore(notes, meters, bpm or DEFAULT_BPM, len(played), timeline, tempo_printed=bpm > 0)


def _tempo_of(drawing: PageDrawing) -> float:
    """A metronome mark: a note glyph followed by '= 72'."""
    marks = [g for g in drawing.glyphs if g.music and g.code in (0x143, 0x144, 0x145)]
    for m in marks:
        texts = sorted(
            [g for g in drawing.glyphs if not g.music and abs(g.y - m.y) <= 4 and 0 <= g.l - m.r <= 40],
            key=lambda g: g.l,
        )
        s = "".join(g.text for g in texts)
        found = re.search(r"=\s*(\d{2,3})", s)
        if found:
            return float(found.group(1))
    return 0.0


def read_vector_pdf(pdf: Path, max_pages: int = 40, cancel=None) -> VectorScore:
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(str(pdf))
    except Exception as exc:  # noqa: BLE001
        raise VectorScoreError("无法打开这个 PDF") from exc
    state = ReaderState()
    all_bars: list[Bar] = []
    bpm = 0.0
    try:
        for index in range(min(len(doc), max_pages)):
            if cancel is not None and cancel.is_set():
                from app.engines.base import CancelledError

                raise CancelledError()
            drawing = read_drawing(doc[index])
            if not bpm:
                bpm = _tempo_of(drawing)
            staves = find_staves(drawing)
            if not staves:
                continue
            systems = group_systems(drawing, staves)
            for sys_index, system in enumerate(systems):
                above = systems[sys_index - 1][-1].bottom if sys_index > 0 else None
                below = systems[sys_index + 1][0].top if sys_index + 1 < len(systems) else None
                for bar in analyze_system(drawing, system, state, above, below):
                    bar.page, bar.system = index, sys_index
                    all_bars.append(bar)
    finally:
        doc.close()
    if not all_bars:
        raise VectorScoreError("没有找到五线谱")
    return build_score(all_bars, bpm)


TEMPO_NOT_PRINTED = "PianoConvert: tempo not printed"
ANNOTATION = "PianoConvert:"  # same prefix as app.keyboard_score.ANNOTATION


def write_midi(score: VectorScore, dest: Path) -> Path:
    """MIDI of the score, one track per staff ("Staff 1" is the top staff).

    Text events carry what the notes alone cannot say (read by app.keyboard_score):
    which track has the tune, grace notes, rolled chords, and a missing tempo mark (the
    MIDI still needs some tempo: DEFAULT_BPM).
    """
    import pretty_midi

    bpm = score.bpm
    sec = 60.0 / bpm
    pm = pretty_midi.PrettyMIDI(initial_tempo=bpm)
    for q, (num, den) in score.meters:
        pm.time_signature_changes.append(pretty_midi.TimeSignature(num, den, q * sec))
    if not score.tempo_printed:
        pm.text_events.append(pretty_midi.Text(TEMPO_NOT_PRINTED, 0.0))
    staves = sorted({n.staff for n in score.notes})
    if staves:
        pm.text_events.append(pretty_midi.Text(f"{ANNOTATION}melody:Staff {staves[0] + 1}", 0.0))
    rolled: dict[float, set[int]] = defaultdict(set)
    for n in score.notes:
        pitch = max(0, min(127, n.pitch))
        if n.grace:
            pm.text_events.append(pretty_midi.Text(f"{ANNOTATION}grace:{pitch}", n.start_q * sec))
        elif n.arpeggio:
            rolled[round(n.start_q, 6)].add(pitch)
    for q, pitches in sorted(rolled.items()):
        if len(pitches) >= 2:
            pm.text_events.append(pretty_midi.Text(f"{ANNOTATION}arpeggio:" + ",".join(map(str, sorted(pitches))), q * sec))
    for st in staves:
        inst = pretty_midi.Instrument(program=0, name=f"Staff {st + 1}")
        for n in score.notes:
            if n.staff == st:
                inst.notes.append(pretty_midi.Note(80, max(0, min(127, n.pitch)), n.start_q * sec, max(n.end_q, n.start_q + 0.0625) * sec))
        pm.instruments.append(inst)
    dest.parent.mkdir(parents=True, exist_ok=True)
    pm.write(str(dest))
    return dest


def tempo_printed(midi: Path) -> bool:
    """False for a MIDI written by write_midi from a PDF that prints no tempo."""
    import pretty_midi

    try:
        pm = pretty_midi.PrettyMIDI(str(midi))
    except Exception:  # noqa: BLE001
        return True
    return not any(t.text == TEMPO_NOT_PRINTED for t in pm.text_events)
