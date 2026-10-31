"""Pages that draw every symbol as an outline.

Some generators turn a whole score into filled shapes: MuseScore.com's sheet pages (SVG,
converted to PDF in the browser), webmscore's PDF export, a score printed through a
"save as outlines" step. There is no text on such a page, so app.pdf_vector finds no
glyphs. This module recognises the shapes instead: each filled outline (one closed contour
with its holes) is compared with the shapes of the music font glyphs the reader uses -
Leland (MuseScore 4) and MScore (MuseScore 3) - at the size the staff lines give, and the
shapes that match become glyphs with their SMuFL code. Digits (tuplet numbers, the
metronome mark's "= 72") are matched against MuseScore's text font. Thin filled rectangles
are the stems, barlines and beams, and filled crescents the slurs and ties, as on other
MuseScore pages. The result is the chars / lines / curves app.pdf_vector reads.
"""
from __future__ import annotations

import base64
import ctypes
import math
from collections import Counter
from functools import lru_cache

import numpy as np

GRID = 16          # shape signature: GRID x GRID cells of the shape's bounding box
CURVE_STEPS = 6    # points sampled along each bezier piece
MATCH_DIST = 0.15  # at most this share of the cells may differ for a match
SIZE_TOL = 0.22    # the shape's size may differ this much (log) from the glyph at the page's size


def cubic(p0, p1, p2, p3, t):
    u = 1 - t
    return (
        u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
        u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
    )


def _box(c: np.ndarray) -> tuple[float, float, float, float]:
    return float(c[:, 0].min()), float(c[:, 1].min()), float(c[:, 0].max()), float(c[:, 1].max())


def _inside(pt, c: np.ndarray) -> bool:
    x, y = pt
    a, b = c, np.roll(c, -1, axis=0)
    cross = (a[:, 1] <= y) != (b[:, 1] <= y)
    if not cross.any():
        return False
    xs = a[cross, 0] + (y - a[cross, 1]) * (b[cross, 0] - a[cross, 0]) / (b[cross, 1] - a[cross, 1])
    return int((xs > x).sum()) % 2 == 1


def components(contours: list[np.ndarray]) -> list[tuple[list[np.ndarray], tuple[float, float, float, float]]]:
    """Contours grouped into shapes: a contour lying inside another one is its hole (or an
    island in a hole: the even-odd fill decides the shape)."""
    items = [(c, _box(c)) for c in contours if len(c) >= 3]
    items.sort(key=lambda cb: -(cb[1][2] - cb[1][0]) * (cb[1][3] - cb[1][1]))
    shapes: list[list] = []
    for c, b in items:
        home = None
        for s in shapes:
            ob = s[1]
            if b[0] >= ob[0] - 0.01 and b[1] >= ob[1] - 0.01 and b[2] <= ob[2] + 0.01 and b[3] <= ob[3] + 0.01:
                if _inside(tuple(c[0]), s[0][0]):
                    home = s
                    break
        if home is None:
            shapes.append([[c], b])
        else:
            home[0].append(c)
    return [(cs, b) for cs, b in shapes]


def signature(contours: list[np.ndarray], box) -> np.ndarray:
    """Which cells of a GRID x GRID raster over ``box`` the shape covers (even-odd fill,
    sampled at the cell centres)."""
    x0, y0, x1, y1 = box
    w, h = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    grid = np.zeros((GRID, GRID), dtype=bool)
    ys = y0 + (np.arange(GRID) + 0.5) * h / GRID
    xs = x0 + (np.arange(GRID) + 0.5) * w / GRID
    for c in contours:
        a, b = c, np.roll(c, -1, axis=0)
        for j, y in enumerate(ys):
            cross = (a[:, 1] <= y) != (b[:, 1] <= y)
            if not cross.any():
                continue
            xa = a[cross, 0] + (y - a[cross, 1]) * (b[cross, 0] - a[cross, 0]) / (b[cross, 1] - a[cross, 1])
            for xv in xa:
                grid[j] ^= xs < xv
    return grid


@lru_cache(maxsize=1)
def _templates():
    from app import pdf_outline_data as D

    def unpack(b64: str) -> np.ndarray:
        bits = np.unpackbits(np.frombuffer(base64.b64decode(b64), dtype=np.uint8))[: D.GRID * D.GRID]
        return bits.reshape(D.GRID, D.GRID).astype(bool)

    music = [(f, code, w, h, ox, oy, unpack(s), n) for f, code, w, h, ox, oy, s, n in D.MUSIC]
    # the tremolo glyphs with 2 - 5 strokes are matched by one stroke (their largest part):
    # on an outline page every stroke is its own shape, so each is a 1-stroke tremolo
    music = [t for t in music if t[1] not in (0xE221, 0xE222, 0xE223, 0xE224)]
    text = [(f, ch, w, h, ox, oy, unpack(s), n) for f, ch, w, h, ox, oy, s, n in D.TEXT]
    return music, text


def _path_contours(page) -> tuple[list[np.ndarray], list[tuple[list[tuple[int, float, float]], bool, bool, float, float]]]:
    """Contours of the filled paths (page coordinates), and every path as the point list
    app.pdf_vector reads (kind, x, y), with its fill / stroke / width / scale."""
    import pypdfium2.raw as raw

    contours: list[np.ndarray] = []
    paths = []
    for obj in page.get_objects(max_depth=8):
        if obj.type != raw.FPDF_PAGEOBJ_PATH:
            continue
        m = raw.FS_MATRIX()
        raw.FPDFPageObj_GetMatrix(obj.raw, ctypes.byref(m))
        fill, stroke = ctypes.c_int(), ctypes.c_int()
        raw.FPDFPath_GetDrawMode(obj.raw, ctypes.byref(fill), ctypes.byref(stroke))
        w = ctypes.c_float()
        raw.FPDFPageObj_GetStrokeWidth(obj.raw, ctypes.byref(w))
        scale = math.sqrt(abs(m.a * m.d - m.b * m.c)) or 1.0
        pts: list[tuple[int, float, float]] = []
        cur: list[tuple[float, float]] = []
        pend: list[tuple[float, float]] = []
        sub: list[list[tuple[int, float, float]]] = []
        for k in range(raw.FPDFPath_CountSegments(obj.raw)):
            seg = raw.FPDFPath_GetPathSegment(obj.raw, k)
            x, y = ctypes.c_float(), ctypes.c_float()
            raw.FPDFPathSegment_GetPoint(seg, ctypes.byref(x), ctypes.byref(y))
            p = (m.a * x.value + m.c * y.value + m.e, m.b * x.value + m.d * y.value + m.f)
            kind = raw.FPDFPathSegment_GetType(seg)
            if kind == 2:  # move: a new contour
                if fill.value and len(cur) > 2:
                    contours.append(np.array(cur))
                if pts:
                    sub.append(pts)
                cur, pts, pend = [p], [(2, p[0], p[1])], []
            elif kind == 0:
                cur.append(p)
                pts.append((0, p[0], p[1]))
            elif kind == 1:
                pend.append(p)
                pts.append((1, p[0], p[1]))
                if len(pend) == 3:
                    p0 = cur[-1]
                    for t in np.linspace(1 / CURVE_STEPS, 1.0, CURVE_STEPS):
                        cur.append(cubic(p0, pend[0], pend[1], pend[2], t))
                    pend = []
        if fill.value and len(cur) > 2:
            contours.append(np.array(cur))
        if pts:
            sub.append(pts)
        for s in sub:
            paths.append((s, bool(fill.value), bool(stroke.value), float(w.value), scale))
    return contours, paths


def _staff_space(paths) -> float | None:
    """The staff space from the stroked (or thin filled) long horizontal lines."""
    from app.pdf_vector import Line, _page_staff_gap

    lines = []
    for pts, fill, stroke, w, scale in paths:
        if stroke and not fill:
            prev = None
            for kind, x, y in pts:
                if kind == 0 and prev is not None:
                    lines.append(Line(prev[0], prev[1], x, y, min(w * scale, 1.5)))
                prev = (x, y)
        elif fill:
            xs = [p[1] for p in pts]
            ys = [p[2] for p in pts]
            if len(pts) >= 4 and max(ys) - min(ys) < 2.0 and max(xs) - min(xs) >= 25:
                y = (max(ys) + min(ys)) / 2
                lines.append(Line(min(xs), y, max(xs), y, max(ys) - min(ys)))
    return _page_staff_gap(lines)


def outline_page(page):
    """(chars, filled-shape paths left over, stroked paths, staff space) of an outline-only
    page: chars as app.pdf_vector's (code, ox, oy, l, b, r, t, size, font, text) with SMuFL
    codes for music (font "Leland") and digits for text (font "Edwin"); paths not taken as
    glyphs are returned for the beam / stem / curve reading. None when the page has no
    staves or no music shapes."""
    contours, paths = _path_contours(page)
    width, height = page.get_size()
    # the page's background (a filled rectangle as large as the page) holds no symbol
    contours = [
        c for c in contours
        if not ((c[:, 0].max() - c[:, 0].min()) > 0.8 * width and (c[:, 1].max() - c[:, 1].min()) > 0.8 * height)
    ]
    space = _staff_space(paths)
    if not space or not contours:
        return None
    music, text = _templates()
    shapes = components(contours)
    chars: list[tuple] = []
    used: set[int] = set()
    sigs: dict[int, np.ndarray] = {}
    for i, (cs, box) in enumerate(shapes):
        w, h = box[2] - box[0], box[3] - box[1]
        if w < 0.25 * space and h < 0.25 * space:
            continue
        if w > 6 * space or h > 9 * space:
            continue
        sigs[i] = signature(cs, box)
    page_music = _page_font_templates(music, shapes, sigs, space)
    for i, (cs, box) in enumerate(shapes):
        if i not in sigs:
            continue
        w, h = box[2] - box[0], box[3] - box[1]
        sig = sigs[i]
        best = _best_music(page_music, w, h, sig, space)
        if best is None and page_music is not music:
            best = _best_music(music, w, h, sig, space)
        if best is not None:
            d, code, ox, oy, k, _font = best
            size = 4 * space * k  # a SMuFL em is 4 spaces
            x, y = box[0] + ox * space * k, box[1] + oy * space * k
            chars.append((code, x, y, box[0], box[1], box[2], box[3], size, "Leland", chr(code)))
            used.add(i)
            continue
        # text: digits of tuplet numbers and metronome marks (about 1.2 - 3 spaces high)
        if not 0.9 * space <= h <= 3.6 * space:
            continue
        tbest = None
        for f, ch, tw, th, ox, oy, tsig, _n in text:
            if abs(math.log((w / h) / (tw / th))) > 0.18:
                continue
            d = float(np.mean(tsig != sig))
            if d <= MATCH_DIST and (tbest is None or d < tbest[0]):
                tbest = (d, ch, h / th)
        if tbest is not None:
            d, ch, em = tbest
            chars.append((ord(ch), box[0], box[1], box[0], box[1], box[2], box[3], em, "Edwin", ch))
            used.add(i)
    chars += _equals_signs(chars, shapes, used, space)
    chars = _beams_not_rests(chars, shapes, used, space, paths)
    chars = _dots(chars, space)
    chars = _octave_clefs(chars, space)
    chars = _ottava_marks(chars, paths, space)
    left = [s for i, s in enumerate(shapes) if i not in used]
    chars = _whole_or_half_rests(chars, paths, space)
    return chars, left, paths, space


REST_BLOCKS = (0xE4E3, 0xE4E4, 0xE4F4, 0xE4F5)
TREMOLOS = (0xE220, 0xE221, 0xE222, 0xE223, 0xE224)


def _beams_not_rests(chars: list[tuple], shapes, used: set[int], space: float, paths=()) -> list[tuple]:
    """A short beam (the second beam of two 16ths, or a short beam stub) has the shape of a
    whole or half rest, or of a tremolo stroke: a block whose left and right ends both meet
    stems is a beam, left for the beam reading. (A tremolo crosses its stem in the middle.)"""
    stems = [
        box for i, (cs, box) in enumerate(shapes)
        if i not in used and box[2] - box[0] <= 0.3 * space and box[3] - box[1] >= 1.5 * space
    ]
    for pts, fill, stroke, w, scale in paths:
        # stems drawn as strokes
        if stroke and len(pts) == 2 and abs(pts[0][1] - pts[1][1]) < 0.05 * space and abs(pts[0][2] - pts[1][2]) >= 1.5 * space:
            x = pts[0][1]
            stems.append((x, min(pts[0][2], pts[1][2]), x, max(pts[0][2], pts[1][2])))
    if not stems:
        return chars
    out = []
    for c in chars:
        if c[0] in REST_BLOCKS or c[0] in TREMOLOS:
            l, b, r, t = c[3], c[4], c[5], c[6]

            def meets(x: float) -> bool:
                return any(s[0] - 0.3 * space <= x <= s[2] + 0.3 * space and s[1] - 0.2 * space <= t and b <= s[3] + 0.2 * space for s in stems)

            def stub(x: float) -> bool:
                # a beam stub (the hook of a dotted 16th's partner): one end on a stem, and
                # lying along that stem's end, a beam's width from it at most
                return any(
                    s[0] - 0.3 * space <= x <= s[2] + 0.3 * space
                    and (abs(b - s[1]) <= 0.9 * space or abs(t - s[3]) <= 0.9 * space)
                    for s in stems
                )

            if (meets(l) and meets(r)) or stub(l) or stub(r):
                # back to an unrecognised shape: the beam reading takes it
                k = next((i for i, (cs, box) in enumerate(shapes) if i in used and abs(box[0] - l) < 1e-6 and abs(box[1] - b) < 1e-6), None)
                if k is not None:
                    used.discard(k)
                continue
        out.append(c)
    return out


DOTTABLE = (0xE0A4, 0xE0A3, 0xE0A2, 0xE0A0) + tuple(range(0xE4E2, 0xE4EA)) + (0xE4F4, 0xE4F5)
OCTAVE_CLEFS = {0xE050: (0xE052, 0xE053), 0xE062: (0xE064, 0xE065)}  # clef: (8 below, 8 above)


def _ottava_marks(chars: list[tuple], paths, space: float) -> list[tuple]:
    """An "8" (or "15") followed by a long dashed line at its height is an ottava mark: 8va
    over a staff, 8vb under one (which side is left to the reader: the glyph is the 8va /
    15ma one above a staff line's middle height, otherwise the 8vb / 15mb one). Drawn as
    outlines its digits are text shapes."""
    digits = sorted((c for c in chars if c[8] != "Leland" and c[9] in "158"), key=lambda c: c[3])
    if not digits:
        return chars
    dashes = []
    for pts, fill, stroke, w, scale in paths:
        if stroke and not fill and len(pts) == 2 and abs(pts[0][2] - pts[1][2]) < 0.05 * space:
            x0, x1 = sorted((pts[0][1], pts[1][1]))
            if x1 - x0 >= 4 * space and w * scale <= 0.3 * space:
                dashes.append((x0, x1, pts[0][2]))
    if not dashes:
        return chars
    lines_y = sorted(set(round(y, 1) for y in _staff_line_ys(paths)))
    # staves: five lines one space apart (not the ottava's own dashed line, a volta bracket)
    staves: list[tuple[float, float]] = []
    for i in range(len(lines_y) - 4):
        run = lines_y[i : i + 5]
        if all(abs(run[k + 1] - run[k] - space) <= 0.2 * space for k in range(4)):
            staves.append((run[0], run[-1]))
    out, taken = [], set()
    for i, c in enumerate(digits):
        if id(c) in taken:
            continue
        group = [c]
        if c[9] == "1" and i + 1 < len(digits) and digits[i + 1][9] == "5" and digits[i + 1][3] - c[5] <= 0.6 * space:
            group.append(digits[i + 1])
        text = "".join(g[9] for g in group)
        if text not in ("8", "15"):
            continue
        l, b, r, t = group[0][3], min(g[4] for g in group), group[-1][5], max(g[6] for g in group)
        cy = (b + t) / 2
        if not any(0 <= d[0] - r <= 1.5 * space and abs(d[2] - cy) <= 0.8 * space for d in dashes):
            continue
        # 8va stands over the staff it shifts, 8vb under it. Between two staves it is the
        # 8va of the staff below unless it hugs the staff above much closer (MuseScore puts
        # an 8vb about as close under its staff as an 8va over its own).
        # (staves are (bottom line y, top line y); page y grows upwards)
        below_st = [s for s in staves if s[1] < cy]   # staves under the mark
        above_st = [s for s in staves if s[0] > cy]   # staves over it
        gap_down = cy - max(s[1] for s in below_st) if below_st else 1e9
        gap_up = min(s[0] for s in above_st) - cy if above_st else 1e9
        above = gap_down <= 1.6 * gap_up
        code = {("8", True): 0xE510, ("8", False): 0xE51C, ("15", True): 0xE514, ("15", False): 0xE516}[(text, above)]
        out.append((code, l, b, l, b, r, t, 4 * space, "Leland", chr(code)))
        taken.update(id(g) for g in group)
    if not out:
        return chars
    return [c for c in chars if id(c) not in taken] + out


def _octave_clefs(chars: list[tuple], space: float) -> list[tuple]:
    """A clef with a small 8 under (or over) it sounds an octave lower (higher); drawn as
    outlines the 8 is a shape of its own, taken for a digit."""
    eights = [c for c in chars if c[8] != "Leland" and c[9] == "8" and c[6] - c[4] <= 1.2 * space]
    if not eights:
        return chars
    out = []
    taken: set[int] = set()
    for c in chars:
        if c[0] in OCTAVE_CLEFS:
            cx = (c[3] + c[5]) / 2
            for e in eights:
                if id(e) in taken or abs((e[3] + e[5]) / 2 - cx) > 0.8 * space:
                    continue
                ecy = (e[4] + e[6]) / 2
                # (the 8 of a bass clef hangs up to about two spaces under the clef's dots)
                below = e[4] < c[4] and c[4] - 2.2 * space <= ecy <= c[4] + 0.4 * space
                above = e[6] > c[6] and c[6] - 0.4 * space <= ecy <= c[6] + 1.5 * space
                if below or above:
                    code = OCTAVE_CLEFS[c[0]][0 if below else 1]
                    c = (code,) + tuple(c[1:9]) + (chr(code),)
                    taken.add(id(e))
                    break
        out.append(c)
    return [c for c in out if id(c) not in taken]


def _dots(chars: list[tuple], space: float) -> list[tuple]:
    """An augmentation dot and a repeat dot have the same shape: a dot standing just right of
    a note head or a rest (at about its height, or the adjacent space) is an augmentation dot,
    or one of a row of dots after it; the others stay repeat dots."""
    dots = [c for c in chars if c[0] in (0xE044, 0xE1E7)]
    if not dots:
        return chars
    owners = [c for c in chars if c[0] in DOTTABLE]
    aug: set[int] = set()
    # a repeat sign's dots stand in a pair, one space apart, one above the other
    paired = {
        id(c) for c in dots for d in dots
        if c is not d and abs(c[3] - d[3]) <= 0.15 * space and 0.8 * space <= abs(c[4] - d[4]) <= 1.2 * space
    }
    for c in sorted(dots, key=lambda c: c[3]):
        if id(c) in paired:
            continue
        cy = (c[4] + c[6]) / 2
        cx = (c[3] + c[5]) / 2
        centred_articulation = any(
            o[0] in DOTTABLE[:4] and abs(cx - (o[3]+o[5])/2) <= 0.3 * space
            and 0.8 * space <= abs(cy - (o[4]+o[6])/2) <= 3 * space
            and (cy < o[4] - 0.2 * space or cy > o[6] + 0.2 * space)
            for o in owners
        )
        lower_articulation = False
        for o in owners:
            oy = (o[4] + o[6]) / 2
            # In polyphony a line note's dot can use the lower adjacent
            # space. Keep the horizontal-right requirement, so a centred
            # staccato above/below the head cannot lengthen the note.
            if 0.1 * space <= c[3] - o[5] <= 1.6 * space and -0.65 * space <= cy - oy <= 0.8 * space:
                if cy - oy < -0.4 * space and centred_articulation:
                    # A dot directly below the next note can also stand
                    # right/down from the previous head. Its clear vertical
                    # articulation attachment takes precedence in this new
                    # lower-space case.
                    lower_articulation = True
                    continue
                aug.add(id(c))
                break
        else:
            # a second (or third) dot of a double-dotted note follows the first
            if not lower_articulation and any(id(p) in aug and 0.2 * space <= c[3] - p[5] <= 0.9 * space and abs(cy - (p[4] + p[6]) / 2) <= 0.2 * space for p in dots):
                aug.add(id(c))
    out = []
    for c in chars:
        if c[0] in (0xE044, 0xE1E7):
            code = 0xE1E7 if id(c) in aug else 0xE044
            c = (code,) + tuple(c[1:9]) + (chr(code),)
        out.append(c)
    return out


def _best_music(music, w: float, h: float, sig: np.ndarray, space: float):
    """(distance, code, origin x, origin y, scale, font) of the music template the shape
    matches best, None when none is close enough."""
    arr = _music_arrays(music)
    tw, th = arr["w"], arr["h"]
    sw, sh = w / (tw * space), h / (th * space)
    k = np.sqrt(sw * sh)
    met = arr["met"]
    # a metronome mark's note is drawn at the size of the text it stands in (0.3 - 1 staff
    # size); a clef changing the clef in the middle of a line at about 0.8 of the staff size
    clef_small = arr["clef"] & (k >= 0.65) & (k <= 0.9)
    norm = met | clef_small
    sw = np.where(norm, sw / k, sw)
    sh = np.where(norm, sh / k, sh)
    ok = (np.abs(np.log(sw)) <= SIZE_TOL) & (np.abs(np.log(sh)) <= SIZE_TOL) & (np.abs(np.log(sw / sh)) <= 0.15)
    ok &= ~met | ((k >= 0.3) & (k <= 1.05))
    if not ok.any():
        return None
    d = (arr["sig"][ok] != sig.ravel()).mean(axis=1)
    j = int(np.argmin(d))
    if d[j] > MATCH_DIST:
        return None
    t = [music[i] for i in np.flatnonzero(ok)][j]
    f, code, tw1, th1, ox, oy = t[:6]
    return (float(d[j]), code, ox, oy, (w * h / (tw1 * th1)) ** 0.5 / space, f)


_ARRAYS: dict = {}


def _music_arrays(music):
    """The templates as arrays for matching many at once (cached per template list; the
    list is kept with them so its id is not reused)."""
    got = _ARRAYS.get(id(music))
    if got is not None and got[1] is music:
        return got[0]
    arrays = {
        "w": np.array([t[2] for t in music], dtype=float),
        "h": np.array([t[3] for t in music], dtype=float),
        "met": np.array([t[1] in METRONOME_NOTES for t in music], dtype=bool),
        "clef": np.array([t[1] in CLEFS for t in music], dtype=bool),
        "sig": np.array([t[6].ravel() for t in music]),
    }
    if len(_ARRAYS) > 32:
        _ARRAYS.clear()
    _ARRAYS[id(music)] = (arrays, music)
    return arrays


def _page_font_templates(music, shapes, sigs: dict, space: float):
    """The templates of the music font the page is engraved in (a page uses one: MuseScore's
    Leland, Bravura, Gootville, ...): each font scores the shapes it matches closely, and the
    best font's templates are used for every shape, so that shapes of one font are not
    taken for other symbols of another. All templates when no font stands out."""
    fonts = sorted({t[0] for t in music})
    if len(fonts) < 2:
        return music
    per_font = _per_font(music)
    score = Counter()
    for i, sig in list(sigs.items())[:1500]:
        box = shapes[i][1]
        bw, bh = box[2] - box[0], box[3] - box[1]
        found = {f: _best_music(ts, bw, bh, sig, space) for f, ts in per_font.items()}
        found = {f: b for f, b in found.items() if b is not None}
        if not found:
            continue
        low = min(b[0] for b in found.values())
        if low <= 0.06:
            # every font about as close as the best one gets the vote
            for f, b in found.items():
                if b[0] <= low + 0.01:
                    score[f] += 1
    if not score:
        return music
    top, n = score.most_common(1)[0]
    return per_font[top] if n >= 4 else music


@lru_cache(maxsize=1)
def _per_font_cached():
    music, _text = _templates()
    out: dict[str, list] = {}
    for t in music:
        out.setdefault(t[0], []).append(t)
    return out


def _per_font(music):
    full, _text = _templates()
    if music is full:
        return _per_font_cached()
    out: dict[str, list] = {}
    for t in music:
        out.setdefault(t[0], []).append(t)
    return out


METRONOME_NOTES = (0xECA5, 0xECA3, 0xECA7, 0xECA6, 0xECA2)
CLEFS = (0xE050, 0xE07A, 0xE052, 0xE053, 0xE062, 0xE07C, 0xE064, 0xE05C, 0xE07B)


def _equals_signs(chars: list[tuple], shapes, used: set[int], space: float) -> list[tuple]:
    """The '=' of a metronome mark ("♩ = 72"): two short bars, one over the other, just
    right of the mark's note. Each bar is its own shape, too flat for the digit test."""
    notes = [c for c in chars if c[0] in METRONOME_NOTES]
    if not notes:
        return []
    bars = [
        (i, box) for i, (cs, box) in enumerate(shapes)
        if i not in used and len(cs) == 1
        and box[3] - box[1] < 0.45 * space and 0.5 * space < box[2] - box[0] < 2.5 * space
    ]
    out = []
    for n in notes:
        # the dot of a dotted mark: a small round shape between the note and the '='
        nh = n[6] - n[4]
        for i, (cs, box) in enumerate(shapes):
            if i in used:
                continue
            bw, bh = box[2] - box[0], box[3] - box[1]
            if 0.05 * nh <= bw <= 0.3 * nh and 0.7 <= bw / max(bh, 1e-9) <= 1.4 and 0 <= box[0] - n[5] <= 0.6 * nh and n[4] - 0.1 * nh <= box[1] <= n[4] + 0.5 * nh:
                out.append((0xE1E7, box[0], box[1], box[0], box[1], box[2], box[3], 4 * space, "Leland", chr(0xE1E7)))
                used.add(i)
                break
        near = [(i, b) for i, b in bars if 0 <= b[0] - n[5] <= 3 * space and n[4] - space <= b[1] <= n[6] + space]
        for (i, a), (j, b) in ((p, q) for p in near for q in near if p[1][1] < q[1][1]):
            if i in used or j in used:
                continue
            if abs(a[0] - b[0]) <= 0.2 * space and abs(a[2] - b[2]) <= 0.2 * space and 0.15 * space <= b[1] - a[3] <= 1.0 * space:
                box = (min(a[0], b[0]), a[1], max(a[2], b[2]), b[3])
                h = box[3] - box[1]
                out.append((ord("="), box[0], box[1], box[0], box[1], box[2], box[3], h * 2.5, "Edwin", "="))
                used.update((i, j))
    return out


def _staff_line_ys(paths) -> list[float]:
    ys = []
    for pts, fill, stroke, w, scale in paths:
        if stroke and len(pts) == 2 and abs(pts[0][2] - pts[1][2]) < 0.3 and abs(pts[1][1] - pts[0][1]) > 40:
            ys.append(pts[0][2])
    return sorted(ys)


def _whole_or_half_rests(chars: list[tuple], paths, space: float) -> list[tuple]:
    """A whole rest and a half rest are the same block; the whole rest hangs below a staff
    line, the half rest sits on one. (Page y grows upwards.)"""
    lines = _staff_line_ys(paths)
    if not lines:
        return chars
    out = []
    for c in chars:
        if c[0] in (0xE4E3, 0xE4E4, 0xE4F4, 0xE4F5):
            top, bottom = c[6], c[4]
            hangs = any(abs(top - y) <= 0.25 * space for y in lines)
            sits = any(abs(bottom - y) <= 0.25 * space for y in lines)
            ledger = c[0] in (0xE4F4, 0xE4F5)
            if hangs and not sits:
                code = 0xE4F4 if ledger else 0xE4E3
            elif sits and not hangs:
                code = 0xE4F5 if ledger else 0xE4E4
            else:
                code = c[0]
            c = (code,) + tuple(c[1:9]) + (chr(code),)
        out.append(c)
    return out
