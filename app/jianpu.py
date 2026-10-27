"""Jianpu (numbered notation) recognition: page images -> notes.

Read in layers from the ink (the grey watermark is lighter than anything we use; grey
digits are tie continuations and are kept but marked "held"):

1. Rows: bar lines are short vertical strokes. Strokes at the same height form one row
   (one hand of one system). Rows sharing bar-line positions form a system; the upper
   row is the right hand, the lower the left hand.
2. Text that is not music (title, "1=C", "♩=72", measure numbers "(4)", footer, "rit.")
   is found with OCR and masked out.
3. Digits: connected ink pieces of digit size, each read as 0-7 by OCR. Chord tones are
   stacked upward from the row's baseline.
4. Marks around each column: underlines under the bottom digit (1 = eighth, 2 = 16th),
   a dot right of a digit (dotted), dots above / below a digit (octave up / down), a dash
   "-" on the baseline (one more beat).
5. Timing: events of a bar are laid out left to right. Each bar takes the length of the
   meter (the most common bar length on the page), so both hands stay in step.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

MAJOR = {1: 0, 2: 2, 3: 4, 4: 5, 5: 7, 6: 9, 7: 11}
KEY_OFFSETS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
INK = 215  # anything darker than this is ink (watermarks are lighter)
GREY = 90  # a digit whose darkest pixel is lighter than this is printed grey (held)
OCR_FIX = {
    "l": "1", "I": "1", "i": "1", "|": "1", "!": "1", "j": "1", "J": "1", "]": "1", "[": "1",
    "O": "0", "o": "0", "D": "0", "Q": "0", "U": "0",
    "z": "2", "Z": "2", "S": "5", "s": "5", "b": "6", "G": "6", "T": "7", "q": "9", "B": "8",
}


class JianpuError(ValueError):
    pass


@dataclass
class Box:
    x0: int
    y0: int
    x1: int
    y1: int
    dark: int = 0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def w(self) -> int:
        return self.x1 - self.x0

    @property
    def h(self) -> int:
        return self.y1 - self.y0


@dataclass
class Digit(Box):
    value: int = -1
    octave: int = 0

    @property
    def grey(self) -> bool:
        return self.dark >= GREY


@dataclass
class Row:
    y0: int
    y1: int
    bars: list[int] = field(default_factory=list)
    baseline: float = 0.0
    digits: list[Digit] = field(default_factory=list)


@dataclass
class Event:
    x: float
    digits: list[Digit]
    beats: float
    dash: bool = False


@dataclass
class Note:
    pitch: int
    start_q: float
    end_q: float
    hand: int


@dataclass
class JianpuScore:
    notes: list[Note]
    bpm: float
    beats_per_bar: float
    key: int
    bars: int


# ---------------------------------------------------------------- image helpers


def _gray(path: Path) -> np.ndarray:
    from PIL import Image

    with Image.open(path) as im:
        return np.array(im.convert("L"))


def _components(mask: np.ndarray, gray: np.ndarray) -> list[Box]:
    import cv2

    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    boxes: list[Box] = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        sub = gray[y : y + h, x : x + w][lab[y : y + h, x : x + w] == i]
        boxes.append(Box(x, y, x + w, y + h, int(sub.min()) if sub.size else 255))
    return boxes


def _digit_height(boxes: list[Box]) -> float:
    cands = [b.h for b in boxes if 12 <= b.h <= 80 and 0.15 * b.h <= b.w <= 1.1 * b.h]
    if not cands:
        raise JianpuError("没有找到简谱数字")
    # the most common height among digit-like pieces (rounded) is the note-digit height
    common = Counter(round(h / 2) * 2 for h in cands).most_common(1)[0][0]
    near = [h for h in cands if abs(h - common) <= 3]
    return float(np.median(near))


def staff_line_count(gray: np.ndarray) -> int:
    """Long horizontal lines; a staff-notation page has many, a jianpu page has none."""
    import cv2

    ink = (gray < INK).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, gray.shape[1] // 4), 1))
    lines = cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel)
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(lines, 8)
    return int(sum(1 for s in stats[1:] if s[3] <= 6))


def looks_like_jianpu(path: Path) -> bool:
    gray = _gray(path)
    if staff_line_count(gray) >= 5:
        return False
    boxes = _components(gray < INK, gray)
    try:
        d = _digit_height(boxes)
    except JianpuError:
        return False
    digits = [b for b in boxes if 0.75 * d <= b.h <= 1.3 * d and 0.15 * d <= b.w <= 1.1 * d]
    return len(digits) >= 16 and len(find_rows(gray, d)) >= 1


# ---------------------------------------------------------------- layout


def find_rows(gray: np.ndarray, d: float) -> list[Row]:
    import cv2

    ink = (gray < INK).astype(np.uint8) * 255
    vert = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, int(1.3 * d))))
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(vert, 8)
    # Bar lines are thin, about 2-3 digit heights tall. Taller strokes are the system
    # brace/line at the left edge; shorter ones are digits like "1" or "4".
    segs = [
        (int(s[1]), int(s[1] + s[3]), int(s[0] + s[2] / 2))
        for s in stats[1:]
        if s[2] <= 0.3 * d and 1.8 * d <= s[3] <= 3.4 * d
    ]
    rows: list[Row] = []
    for y0, y1, x in sorted(segs):
        for row in rows:
            if abs(row.y0 - y0) <= 0.5 * d:
                row.bars.append(x)
                row.y0, row.y1 = min(row.y0, y0), max(row.y1, y1)
                break
        else:
            rows.append(Row(y0, y1, [x]))
    for row in rows:
        merged: list[int] = []
        for x in sorted(row.bars):
            if merged and x - merged[-1] <= 0.6 * d:  # double / repeat bar lines
                continue
            merged.append(x)
        row.bars = merged
    width = gray.shape[1]
    # A real row has bar lines spread across the page, not one stray stroke. The last line
    # of a piece may only have the final bar line at the right edge; keep that too.
    rows = [
        r for r in rows
        if (len(r.bars) >= 2 and r.bars[-1] - r.bars[0] >= 0.3 * width)
        or (len(r.bars) >= 1 and r.bars[-1] >= 0.75 * width)
    ]
    return sorted(rows, key=lambda r: r.y0)


def group_systems(rows: list[Row], d: float) -> list[list[Row]]:
    """Pair rows into (right hand, left hand) systems.

    The two hands of one system sit closer together than one system to the next, so rows
    are paired from the top whenever the gap below a row is smaller than the gap after the
    next row. Chords stacked above the right hand can make gaps vary a lot, so the decision
    is relative, not against a fixed threshold.
    """
    systems: list[list[Row]] = []
    i = 0
    while i < len(rows):
        if i + 1 < len(rows):
            gap = rows[i + 1].y0 - rows[i].y1
            next_gap = rows[i + 2].y0 - rows[i + 1].y1 if i + 2 < len(rows) else float("inf")
            overlap = min(rows[i].bars[-1], rows[i + 1].bars[-1]) - max(rows[i].bars[0], rows[i + 1].bars[0])
            if gap <= 8 * d and gap < next_gap and overlap > 0:
                systems.append([rows[i], rows[i + 1]])
                i += 2
                continue
        systems.append([rows[i]])
        i += 1
    return systems


# ---------------------------------------------------------------- OCR


class _Reader:
    def __init__(self) -> None:
        from rapidocr import RapidOCR

        self.ocr = RapidOCR()

    def text_boxes(self, path: Path) -> list[tuple[str, Box]]:
        res = self.ocr(str(path), use_det=True, use_cls=True, use_rec=True)
        out: list[tuple[str, Box]] = []
        if getattr(res, "txts", None) is None or getattr(res, "boxes", None) is None:
            return out
        for box, txt in zip(res.boxes, res.txts):
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            out.append((txt, Box(int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))))
        return out

    def digit(self, crop: np.ndarray) -> tuple[str, float]:
        pad = np.full((crop.shape[0] + 16, crop.shape[1] + 16), 255, dtype=np.uint8)
        pad[8:-8, 8:-8] = crop
        scale = 48 / pad.shape[0]
        from PIL import Image

        img = np.array(Image.fromarray(pad).resize((max(8, int(pad.shape[1] * scale)), 48)).convert("RGB"))
        res = self.ocr(img, use_det=False, use_cls=False, use_rec=True)
        txts = getattr(res, "txts", None) or ()
        scores = getattr(res, "scores", None) or ()
        if not txts:
            return "", 0.0
        return str(txts[0]), float(scores[0]) if scores else 0.0


_READER: _Reader | None = None


def _reader() -> _Reader:
    global _READER
    if _READER is None:
        _READER = _Reader()
    return _READER


def _is_non_music_text(text: str) -> bool:
    """Title, credits, "1=C", "♩=72", measure numbers "(4)", "rit." ... but not digits with dots.

    OCR often reads a note digit together with its octave dot as ":5" or "5." — those are
    music. Only text with Chinese characters, "=", brackets or several letters is masked.
    """
    t = text.replace(" ", "")
    if not t:
        return False
    if re.search(r"[\u4e00-\u9fff=()（）♩√%]", t):
        return True
    letters = [c for c in t if c.isalpha() and c not in "ilIsSoO"]
    return len(letters) >= 2


def parse_header(texts: list[str]) -> tuple[int, float]:
    """Key offset of "1" and tempo from header text like "1=C", "1=bE", "♩=72"."""
    key, bpm = 0, 0.0
    for t in texts:
        s = t.replace(" ", "").replace("＝", "=")
        m = re.search(r"1=([#b♯♭]?)([A-G])([#b♯♭]?)", s)
        if m:
            acc = (m.group(1) or m.group(3) or "")
            key = KEY_OFFSETS[m.group(2)] + (1 if acc in ("#", "♯") else 0) - (1 if acc in ("b", "♭") else 0)
        m = re.search(r"(?<!1)=(\d{2,3})", s)
        if m and not s.startswith("1="):
            bpm = float(m.group(1))
    return key % 12, bpm


# ---------------------------------------------------------------- page reading


def _classify_digits(gray: np.ndarray, boxes: list[Box], reader: _Reader) -> list[Digit]:
    """OCR every digit-sized piece; low-confidence ones are matched against confident ones."""
    digits: list[Digit] = []
    shapes: list[tuple[np.ndarray, int]] = []
    pending: list[tuple[Digit, np.ndarray]] = []
    for b in boxes:
        crop = gray[b.y0 : b.y1, b.x0 : b.x1]
        # print the piece in black on white whatever its grey level
        norm = np.where(crop < INK, 0, 255).astype(np.uint8)
        text, score = reader.digit(norm)
        text = "".join(OCR_FIX.get(c, c) for c in text)
        dig = Digit(b.x0, b.y0, b.x1, b.y1, b.dark)
        from PIL import Image

        shape = np.array(Image.fromarray(norm).resize((12, 18))) < 128
        if len(text) == 1 and text in "01234567" and score >= 0.8:
            dig.value = int(text)
            shapes.append((shape, dig.value))
        else:
            pending.append((dig, shape))
        digits.append(dig)
    for dig, shape in pending:
        if not shapes:
            continue
        best = min(shapes, key=lambda s: int(np.count_nonzero(s[0] != shape)))
        if np.count_nonzero(best[0] != shape) <= 0.18 * shape.size:
            dig.value = best[1]
    return [d for d in digits if d.value >= 0]


@dataclass
class PageResult:
    systems: list[list[Row]]
    events: list[list[list[list[Event]]]]  # system -> row -> bar -> events
    key: int
    bpm: float
    d: float


def read_page(path: Path, reader: _Reader | None = None) -> PageResult:
    reader = reader or _reader()
    gray = _gray(path)
    boxes = _components(gray < INK, gray)
    d = _digit_height(boxes)
    rows = find_rows(gray, d)
    if not rows:
        raise JianpuError("没有找到简谱的小节线")
    texts = reader.text_boxes(path)
    key, bpm = parse_header([t for t, _ in texts])
    masks = [b for t, b in texts if _is_non_music_text(t)]

    def masked(b: Box) -> bool:
        return any(m.x0 - 3 <= b.cx <= m.x1 + 3 and m.y0 - 3 <= b.cy <= m.y1 + 3 for m in masks)

    top_limit = rows[0].y0 - 4.5 * d
    digit_boxes = [
        b for b in boxes
        if 0.75 * d <= b.h <= 1.3 * d and 0.12 * d <= b.w <= 1.1 * d and b.cy >= top_limit and not masked(b)
    ]
    digits = _classify_digits(gray, digit_boxes, reader)
    dots = [b for b in boxes if max(b.w, b.h) <= 0.3 * d and min(b.w, b.h) >= 0.06 * d and max(b.w, b.h) <= 2.2 * max(1, min(b.w, b.h))]
    hlines = [b for b in boxes if b.h <= 0.25 * d and b.w >= 0.35 * d]
    # Under a group, two underlines touch the dots below them and merge into one piece; the
    # dots of low notes sit right under the lines. Split pieces that are taller than a line.
    hlines += _split_stacked_lines(gray, [b for b in boxes if 0.25 * d < b.h <= 0.8 * d and b.w >= 1.5 * d], d)

    # digits -> rows (chords stack upward, so a digit belongs to the nearest row at or below it)
    for dig in digits:
        for row in rows:
            if dig.cy <= row.y1 + 0.3 * d:
                if dig.cy >= row.y0 - 4.5 * d:
                    row.digits.append(dig)
                break
    for row in rows:
        inside = [g.cy for g in row.digits if row.y0 <= g.cy <= row.y1]
        row.baseline = float(np.median(inside)) if inside else (row.y0 + row.y1) / 2

    used_dots: set[int] = set()
    # dotted notes: dot just right of a digit, at its middle height
    dotted_digits: set[int] = set()
    for dig in digits:
        for i, dot in enumerate(dots):
            if i in used_dots:
                continue
            if dig.x1 - 1 <= dot.cx <= dig.x1 + 0.7 * d and dig.y0 + 0.3 * dig.h <= dot.cy <= dig.y1 - 0.1 * dig.h:
                dotted_digits.add(id(dig))
                used_dots.add(i)
                break
    # Octave dots: a dot belongs to the digit it is closest to vertically, as a high dot
    # (above it) or a low dot (below it). In chord columns every tone carries its own dot,
    # tight under or over it, so "nearest gap wins" reads stacked chords correctly.
    row_of = {id(g): r for r in rows for g in r.digits}
    for i, dot in enumerate(dots):
        if i in used_dots:
            continue
        best: tuple[float, Digit, int] | None = None
        for dig in digits:
            if abs(dot.cx - dig.cx) > 0.4 * d or id(dig) not in row_of:
                continue
            row = row_of[id(dig)]
            on_base = abs(dig.cy - row.baseline) <= 0.35 * d
            above = dig.y0 - dot.y1
            below = dot.y0 - dig.y1
            if -2 <= above <= 0.55 * d and (best is None or above < best[0]):
                best = (above, dig, +1)
            limit = 1.15 * d if on_base else 0.55 * d
            if -2 <= below <= limit and (best is None or below < best[0]):
                best = (below, dig, -1)
        if best is not None:
            best[1].octave += best[2]
            used_dots.add(i)

    systems = group_systems(rows, d)
    page_events: list[list[list[list[Event]]]] = []
    for system in systems:
        left = min((g.x0 for r in system for g in r.digits), default=0) - d
        # Bar edges come from the hand with the most bar lines; the other hand's bar lines
        # are drawn at the same places (small offsets are fine because events are binned).
        bars = sorted(max(system, key=lambda r: len(r.bars)).bars)
        edges = [left] + bars
        if edges[-1] < max((g.x1 for r in system for g in r.digits), default=0):
            edges.append(max(g.x1 for r in system for g in r.digits) + d)
        sys_events: list[list[list[Event]]] = []
        for row in system:
            cols = _columns(row.digits, d)
            dashes = [
                h for h in hlines
                if abs(h.cy - row.baseline) <= 0.3 * d and 0.35 * d <= h.w <= 1.6 * d and left <= h.cx <= edges[-1]
            ]
            evs: list[Event] = []
            for col in cols:
                base = max(col, key=lambda g: g.cy)
                under = [
                    h for h in hlines
                    if base.y1 - 2 <= h.cy <= base.y1 + 0.75 * d
                    and min(h.x1, base.x1) - max(h.x0, base.x0) >= 0.3 * max(1, base.w)
                ]
                levels: list[float] = []
                for h in sorted(under, key=lambda u: u.cy):
                    if not levels or h.cy - levels[-1] > 2:
                        levels.append(h.cy)
                beats = 0.5 ** min(len(levels), 4)
                if any(id(g) in dotted_digits for g in col):
                    beats *= 1.5
                evs.append(Event(base.cx, sorted(col, key=lambda g: -g.cy), beats))
            for h in dashes:
                evs.append(Event(h.cx, [], 1.0, dash=True))
            evs.sort(key=lambda e: e.x)
            bars_of_row: list[list[Event]] = []
            for a, b in zip(edges, edges[1:]):
                bars_of_row.append([e for e in evs if a <= e.x < b])
            # the space left of the first bar line is a pickup only if it holds notes
            if bars_of_row and not bars_of_row[0]:
                bars_of_row = bars_of_row[1:]
            sys_events.append(bars_of_row)
        page_events.append(sys_events)
    return PageResult(systems, page_events, key, bpm, d)


def _split_stacked_lines(gray: np.ndarray, boxes: list[Box], d: float) -> list[Box]:
    """Horizontal runs inside a wide piece: rows where most of the width is ink."""
    out: list[Box] = []
    for b in boxes:
        crop = gray[b.y0 : b.y1, b.x0 : b.x1] < INK
        rows = crop.mean(axis=1) >= 0.6
        y = 0
        while y < len(rows):
            if rows[y]:
                start = y
                while y < len(rows) and rows[y]:
                    y += 1
                out.append(Box(b.x0, b.y0 + start, b.x1, b.y0 + y, b.dark))
            y += 1
    return out


def _columns(digits: list[Digit], d: float) -> list[list[Digit]]:
    cols: list[list[Digit]] = []
    for dig in sorted(digits, key=lambda g: g.cx):
        if cols and abs(dig.cx - np.mean([g.cx for g in cols[-1]])) <= 0.35 * d:
            cols[-1].append(dig)
        else:
            cols.append([dig])
    return cols


# ---------------------------------------------------------------- timing


def _bar_beats(events: list[Event]) -> float:
    return sum(e.beats for e in events)


def build_score(pages: list[PageResult]) -> JianpuScore:
    if not pages:
        raise JianpuError("没有识别出简谱")
    totals = [
        round(_bar_beats(bar) * 2) / 2
        for page in pages for system in page.events for row in system for bar in row if bar
    ]
    if not totals:
        raise JianpuError("没有识别出简谱")
    meter = Counter(t for t in totals if t > 0).most_common(1)[0][0]
    key = pages[0].key
    bpm = next((p.bpm for p in pages if p.bpm > 0), 0.0) or 80.0
    notes: list[Note] = []
    bar_start = 0.0
    bars = 0
    open_notes: dict[tuple[int, int], Note] = {}
    for page in pages:
        for system in page.events:
            n_bars = max(len(row) for row in system)
            for b in range(n_bars):
                bars_here = [row[b] if b < len(row) else [] for row in system]
                # A stretch without inner bar lines (e.g. the last line of a piece) can hold
                # several bars; give it a whole number of bars instead of squeezing it.
                length_q = meter
                for bar in bars_here:
                    t = _bar_beats(bar)
                    k = round(t / meter)
                    if k >= 2 and abs(t - k * meter) <= 0.25 * k:
                        length_q = max(length_q, k * meter)
                for hand, bar in enumerate(bars_here):
                    total = _bar_beats(bar)
                    scale = length_q / total if total > length_q * 1.05 else 1.0
                    q = bar_start
                    for ev in bar:
                        length = ev.beats * scale
                        if ev.dash:
                            for k, note in list(open_notes.items()):
                                if k[0] == hand and abs(note.end_q - q) < 1e-6:
                                    note.end_q = q + length
                        else:
                            for k in [k for k in open_notes if k[0] == hand]:
                                del open_notes[k]
                            for dig in ev.digits:
                                if dig.value == 0:
                                    continue
                                pitch = 60 + key + MAJOR[dig.value] + 12 * dig.octave
                                if dig.grey:
                                    # a tie continuation: hold the earlier note longer
                                    held = [n for n in notes if n.hand == hand and n.pitch == pitch and abs(n.end_q - q) < 1e-6]
                                    if held:
                                        held[-1].end_q = q + length
                                        open_notes[(hand, pitch)] = held[-1]
                                        continue
                                note = Note(pitch, q, q + length, hand)
                                notes.append(note)
                                open_notes[(hand, pitch)] = note
                        q += length
                bar_start += length_q
                bars += max(1, round(length_q / meter))
    if not notes:
        raise JianpuError("没有识别出简谱音符")
    return JianpuScore(notes, bpm, meter, key, bars)


def recognize_jianpu(images: list[Path]) -> JianpuScore:
    reader = _reader()
    pages: list[PageResult] = []
    for img in images:
        try:
            pages.append(read_page(img, reader))
        except JianpuError:
            continue
    return build_score(pages)


def write_midi(score: JianpuScore, dest: Path) -> Path:
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(initial_tempo=score.bpm)
    beats = score.beats_per_bar
    numerator = int(beats) if float(beats).is_integer() else int(beats * 2)
    denominator = 4 if float(beats).is_integer() else 8
    pm.time_signature_changes.append(pretty_midi.TimeSignature(max(1, numerator), denominator, 0.0))
    sec = 60.0 / score.bpm
    for hand, name in ((0, "Right hand"), (1, "Left hand")):
        inst = pretty_midi.Instrument(program=0, name=name)
        for n in score.notes:
            if n.hand == hand:
                end = max(n.end_q, n.start_q + 0.125)
                inst.notes.append(pretty_midi.Note(velocity=80, pitch=n.pitch, start=n.start_q * sec, end=end * sec))
        if inst.notes:
            pm.instruments.append(inst)
    dest.parent.mkdir(parents=True, exist_ok=True)
    pm.write(str(dest))
    return dest
