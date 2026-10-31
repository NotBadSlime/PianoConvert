"""Keyboard scores (键盘谱) for the Genshin lyre.

Notes (from MIDI, MusicXML, or a PDF read by app.pdf_vector) are moved onto the lyre
(C major white keys C3–B5), laid out on a grid of cells and written as text with a
简谱 section and a 键盘谱 section. The notation is described in README section 7
("键盘谱怎么看、怎么弹").
"""
from __future__ import annotations

import bisect
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from statistics import median
from typing import Sequence

import pretty_midi


def _enable_lenient_key_signatures() -> None:
    """Some exported MIDI files store a key signature outside -7..+7 sharps/flats.
    The notes are still usable; treat that meta event as C major instead of failing the file.
    """
    from mido.midifiles.meta import MetaSpec_key_signature

    if getattr(MetaSpec_key_signature.decode, "_pianoconvert_lenient", False):
        return
    original = MetaSpec_key_signature.decode

    def decode(self, message, data):
        try:
            original(self, message, data)
            return
        except Exception:
            # A malformed signature is not evidence for a seven-sharp key.
            # Keep the documented C-major fallback so it cannot shift the tune.
            message.key = "C"

    decode._pianoconvert_lenient = True  # type: ignore[attr-defined]
    MetaSpec_key_signature.decode = decode  # type: ignore[method-assign]


_enable_lenient_key_signatures()

WHITE_PCS = {0, 2, 4, 5, 7, 9, 11}
SNAP_UP = {1: 2, 3: 4, 6: 7, 8: 9, 10: 11}
C3 = 48
B5 = 83
CLUSTER_SEC = 0.050

# ``modern`` folds every note independently after the global transposition and
# black-key snapping. ``smart`` retains the older phrase/chord octave handling.
RANGE_STRATEGY_MODERN = "modern"
RANGE_STRATEGY_SMART = "smart"
RANGE_STRATEGY_DEFAULT = RANGE_STRATEGY_MODERN
RANGE_STRATEGY_LABELS = {
    RANGE_STRATEGY_MODERN: "新版：逐音八度折叠",
    RANGE_STRATEGY_SMART: "旧版：智能乐句移调",
}

DOT_HIGH = "\u0307"  # jianpu dot above: high octave
DOT_LOW = "\u0323"   # jianpu dot below: low octave
_DEGREES = {0: "1", 2: "2", 4: "3", 5: "4", 7: "5", 9: "6", 11: "7"}
_SMALL_DEGREES = {"1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷"}
PITCH_TO_NUM = {
    pitch: _DEGREES[pitch % 12] + (DOT_LOW if pitch < 60 else DOT_HIGH if pitch >= 72 else "")
    for pitch in range(C3, B5 + 1)
    if pitch % 12 in _DEGREES
}
PITCH_TO_KEY = {
    48: "Z",
    50: "X",
    52: "C",
    53: "V",
    55: "B",
    57: "N",
    59: "M",
    60: "A",
    62: "S",
    64: "D",
    65: "F",
    67: "G",
    69: "H",
    71: "J",
    72: "Q",
    74: "W",
    76: "E",
    77: "R",
    79: "T",
    81: "Y",
    83: "U",
}
KEY_TO_PITCH = {key: pitch for pitch, key in PITCH_TO_KEY.items()}

MIDI_SUFFIXES = {".mid", ".midi"}
MUSICXML_SUFFIXES = {".musicxml", ".xml", ".mxl"}
SCORE_SUFFIXES = MIDI_SUFFIXES | MUSICXML_SUFFIXES

STYLE_DETAILED = "detailed"  # tune and accompaniment on two rows
STYLE_SIMPLE = "simple"      # everything on one row
STYLES = (STYLE_DETAILED, STYLE_SIMPLE)

# how rolled chords (arpeggios) are written
ARPEGGIO_AUTO = "auto"      # { }: arpeggio signs in a score, and rolls played out in a MIDI file
ARPEGGIO_CHORD = "chord"    # ( ): every rolled chord is written as keys pressed together
ARPEGGIO_SPREAD = "spread"  # rolls played out in a MIDI file keep each note's own time
ARPEGGIO_MODES = (ARPEGGIO_AUTO, ARPEGGIO_CHORD, ARPEGGIO_SPREAD)

# notes struck this close together (seconds) are one chord; matters for recordings and
# performed MIDI files, whose chords are never exactly together
CHORD_STRICT = "strict"
CHORD_STANDARD = "standard"
CHORD_LOOSE = "loose"
CHORD_WINDOWS = {CHORD_STRICT: 0.030, CHORD_STANDARD: CLUSTER_SEC, CHORD_LOOSE: 0.080}


def chord_window(name: str | None) -> float:
    return CHORD_WINDOWS.get(name or CHORD_STANDARD, CLUSTER_SEC)

# MIDI text events written by app.pdf_vector carry what the notes alone cannot say:
#   PianoConvert:grace:<pitch>             a grace note starting at the event time
#   PianoConvert:arpeggio:<p1>,<p2>,...    a rolled chord starting at the event time
#   PianoConvert:melody:<track name>       the track that has the tune
ANNOTATION = "PianoConvert:"


class KeyboardScoreError(ValueError):
    pass


@dataclass
class NoteEvent:
    pitch: int
    start: float              # seconds
    end: float                # seconds
    part: int = -1            # 0 = the part with the tune (top staff / right hand), -1 unknown
    grace: bool = False       # ornament, played quickly just before the following note
    arpeggio: bool = False    # member of a chord rolled from low to high
    key_fifths: int | None = None  # explicit signature at this onset; None means unknown
    exact_timing: bool = False  # decoded notation, rather than a performed/audio onset


def pitch_to_num(pitch: int) -> str:
    try:
        return PITCH_TO_NUM[int(pitch)]
    except KeyError as exc:
        raise KeyboardScoreError(f"音高不在诗琴键位上：{pitch}") from exc


def pitch_to_key(pitch: int) -> str:
    try:
        return PITCH_TO_KEY[int(pitch)]
    except KeyError as exc:
        raise KeyboardScoreError(f"音高不在诗琴键位上：{pitch}") from exc


def _white_count(pitches: list[int], shift: int) -> int:
    return sum(1 for p in pitches if ((p + shift) % 12) in WHITE_PCS)


C_MAJOR_RATIO = 0.95  # this share of white keys means the piece is already in C major / A minor


def best_transpose(pitches: list[int]) -> int:
    # A piece already in C major / A minor stays where it is; only its few accidentals are
    # moved onto white keys. (Pentatonic tunes are all-white in several keys; without this
    # rule they could be shifted by a fourth for no reason.)
    if pitches and _white_count(pitches, 0) >= C_MAJOR_RATIO * len(pitches):
        return 0
    best_shift = 0
    best_count = -1
    best_abs = 99
    for shift in range(-11, 12):
        count = _white_count(pitches, shift)
        ab = abs(shift)
        better = count > best_count
        if not better and count == best_count:
            if ab < best_abs:
                better = True
            elif ab == best_abs and shift >= 0 and best_shift < 0:
                better = True
        if better:
            best_shift = shift
            best_count = count
            best_abs = ab
    return best_shift


def _old_best_transpose(pitches: list[int]) -> int:
    """The pre-main1 transposition tie-breaking rule."""
    best_shift = 0
    best_count = -1
    best_abs = 99
    for shift in range(-11, 12):
        count = _white_count(pitches, shift)
        ab = abs(shift)
        better = count > best_count
        if not better and count == best_count:
            if ab < best_abs:
                better = True
            elif ab == best_abs and shift >= 0 and best_shift < 0:
                better = True
        if better:
            best_shift = shift
            best_count = count
            best_abs = ab
    return best_shift


def snap_pitch(pitch: int) -> int:
    pc = pitch % 12
    if pc in WHITE_PCS:
        return pitch
    return pitch + (SNAP_UP[pc] - pc)


def fold_pitch(pitch: int) -> int:
    while pitch < C3:
        pitch += 12
    while pitch > B5:
        pitch -= 12
    return pitch


def _validate_range_strategy(strategy: str) -> str:
    if strategy not in RANGE_STRATEGY_LABELS:
        raise KeyboardScoreError(f"未知的音域处理方案：{strategy}")
    return strategy


def cluster_notes(notes: list[NoteEvent]) -> list[tuple[float, list[int]]]:
    if not notes:
        return []
    ordered = sorted(notes, key=lambda n: (n.start, n.pitch))
    groups: list[list[NoteEvent]] = [[ordered[0]]]
    for note in ordered[1:]:
        if note.start - groups[-1][0].start <= CLUSTER_SEC:
            groups[-1].append(note)
        else:
            groups.append([note])
    clustered: list[tuple[float, list[int]]] = []
    for group in groups:
        pitches: list[int] = []
        for note in sorted(group, key=lambda n: n.pitch):
            if note.pitch not in pitches:
                pitches.append(note.pitch)
        clustered.append((group[0].start, pitches))
    return clustered


def _cluster_event_groups(notes: Sequence[NoteEvent]) -> list[list[NoteEvent]]:
    """Group attacks using the same 50 ms chord window as ``cluster_notes``."""
    if not notes:
        return []
    ordered = sorted(notes, key=lambda n: (n.start, n.pitch))
    groups: list[list[NoteEvent]] = [[ordered[0]]]
    for note in ordered[1:]:
        if note.start - groups[-1][0].start <= CLUSTER_SEC:
            groups[-1].append(note)
        else:
            groups.append([note])
    return groups


@dataclass(frozen=True)
class _VoicingCandidate:
    pitches: tuple[int, ...]
    mapped_voices: tuple[int, ...]
    octave_offsets: tuple[int, ...]
    local_cost: float


def _voicing_candidates(pitches: list[int]) -> list[_VoicingCandidate]:
    """Return old-style playable octave placements for one attack/chord."""
    original = tuple(sorted(set(pitches)))
    candidates: dict[tuple[int, ...], _VoicingCandidate] = {}
    for anchor_octave in range(-12, 13):
        mapped = tuple(fold_pitch(pitch + 12 * anchor_octave) for pitch in original)
        rendered = tuple(sorted(set(mapped)))
        offsets = tuple((target - source) // 12 for source, target in zip(original, mapped))
        register_cost = sum(abs(target - source) for target, source in zip(mapped, original)) / 12.0
        interval_cost = sum(
            abs((mapped[index + 1] - mapped[index]) - (original[index + 1] - original[index]))
            for index in range(len(original) - 1)
        )
        collapsed = len(original) - len(rendered)
        offset_spread = max(offsets) - min(offsets) if offsets else 0
        local_cost = (
            register_cost * 0.35
            + interval_cost * 2.5
            + collapsed * 24.0
            + offset_spread * 4.0
        )
        candidate = _VoicingCandidate(rendered, mapped, offsets, local_cost)
        previous = candidates.get(rendered)
        if previous is None or candidate.local_cost < previous.local_cost:
            candidates[rendered] = candidate
    return sorted(candidates.values(), key=lambda item: (item.local_cost, item.pitches))


def _median_octave_offset(candidate: _VoicingCandidate) -> float:
    return float(median(candidate.octave_offsets)) if candidate.octave_offsets else 0.0


def _transition_cost(
    previous_original: list[int],
    previous: _VoicingCandidate,
    current_original: list[int],
    current: _VoicingCandidate,
    gap: float,
) -> float:
    original_interval = current_original[-1] - previous_original[-1]
    mapped_interval = current.mapped_voices[-1] - previous.mapped_voices[-1]
    # A long gap starts a new phrase, so octave continuity matters less there.
    phrase_weight = 0.05 if gap >= 1.25 else 1.0
    interval_cost = abs(mapped_interval - original_interval) * 4.0 * phrase_weight
    octave_change = abs(_median_octave_offset(current) - _median_octave_offset(previous))
    change_cost = octave_change * 6.0 * phrase_weight
    jump_cost = max(0, abs(mapped_interval) - 7) * 0.15 * phrase_weight
    return interval_cost + change_cost + jump_cost


def _smart_fold_candidates(clusters: list[tuple[float, list[int]]]) -> list[_VoicingCandidate]:
    """Select the old smart strategy's best octave candidate for every attack."""
    if not clusters:
        return []
    originals = [sorted(set(pitches)) for _start, pitches in clusters]
    choices = [_voicing_candidates(pitches) for pitches in originals]
    costs: list[list[float]] = [[candidate.local_cost for candidate in choices[0]]]
    parents: list[list[int]] = [[-1 for _candidate in choices[0]]]

    for index in range(1, len(clusters)):
        row_costs: list[float] = []
        row_parents: list[int] = []
        gap = max(0.0, clusters[index][0] - clusters[index - 1][0])
        for current in choices[index]:
            best_cost = float("inf")
            best_parent = 0
            for parent_index, previous in enumerate(choices[index - 1]):
                cost = (
                    costs[index - 1][parent_index]
                    + current.local_cost
                    + _transition_cost(
                        originals[index - 1], previous, originals[index], current, gap
                    )
                )
                if cost < best_cost:
                    best_cost = cost
                    best_parent = parent_index
            row_costs.append(best_cost)
            row_parents.append(best_parent)
        costs.append(row_costs)
        parents.append(row_parents)

    selected = [0] * len(clusters)
    selected[-1] = min(range(len(costs[-1])), key=costs[-1].__getitem__)
    for index in range(len(clusters) - 1, 0, -1):
        selected[index - 1] = parents[index][selected[index]]
    return [choices[index][selected[index]] for index in range(len(clusters))]


@dataclass
class ArrangeInfo:
    shift: int = 0       # semitones the whole piece was moved
    snapped: int = 0     # black-key notes moved onto a white key
    folded: int = 0      # notes moved by octaves into C3–B5
    total: int = 0
    regional_shifts: tuple[int, ...] = ()  # differing shifts, rather than one whole-piece shift


@dataclass
class LyreNote:
    key: int                  # lyre pitch: a white key in C3..B5
    orig: int                 # pitch after the transposition, before snapping and folding
    start: float              # seconds
    end: float
    part: int = -1
    grace: bool = False
    arpeggio: bool = False
    exact_timing: bool = False


def arrange_events(
    notes: Sequence[NoteEvent],
    range_strategy: str = RANGE_STRATEGY_DEFAULT,
) -> tuple[list[LyreNote], ArrangeInfo]:
    """Move notes onto the lyre using the selected range strategy."""
    info = ArrangeInfo(total=len(notes))
    if not notes:
        return [], info
    _validate_range_strategy(range_strategy)
    shift_fn = best_transpose if range_strategy == RANGE_STRATEGY_MODERN else _old_best_transpose
    # A later modulation or a few local accidentals must not change an explicitly
    # C-major opening. Use the written signature where available, and retain pitch
    # inference for notes from sources that do not supply a signature.
    inferred_shift = shift_fn([n.pitch for n in notes if n.key_fifths is None])
    shifts = [
        inferred_shift if note.key_fifths is None else _key_transpose(note.key_fifths)
        for note in notes
    ]
    distinct_shifts = tuple(sorted(set(shifts)))
    if len(distinct_shifts) == 1:
        info.shift = distinct_shifts[0]
    else:
        info.regional_shifts = distinct_shifts
    transposed = [note.pitch + shift for note, shift in zip(notes, shifts)]
    mapped = [
        NoteEvent(
            snap_pitch(pitch),
            note.start,
            note.end,
            note.part,
            note.grace,
            note.arpeggio,
            note.key_fifths,
            note.exact_timing,
        )
        for note, pitch in zip(notes, transposed)
    ]
    for pitch, moved in zip(transposed, mapped):
        info.snapped += int(moved.pitch != pitch)

    if range_strategy == RANGE_STRATEGY_MODERN:
        folded_values = [fold_pitch(note.pitch) for note in mapped]
    else:
        groups = _cluster_event_groups(mapped)
        clusters = [
            (group[0].start, sorted({note.pitch for note in group})) for group in groups
        ]
        candidates = _smart_fold_candidates(clusters)
        folded_values = [0] * len(mapped)
        index_by_id = {id(note): index for index, note in enumerate(mapped)}
        for group, candidate in zip(groups, candidates):
            source_pitches = sorted({note.pitch for note in group})
            pitch_map = dict(zip(source_pitches, candidate.mapped_voices))
            for note in group:
                folded_values[index_by_id[id(note)]] = pitch_map[note.pitch]

    out: list[LyreNote] = []
    for note, pitch, folded in zip(mapped, transposed, folded_values):
        info.folded += int(folded != note.pitch)
        out.append(LyreNote(folded, pitch, note.start, note.end, note.part, note.grace, note.arpeggio,note.exact_timing))
    return out, info


def _key_transpose(fifths: int) -> int:
    """Map a signature to C major / A minor by the nearest equivalent shift."""
    shift = (-7 * fifths) % 12
    return shift if shift <= 6 else shift - 12


def arrange_with_info(
    notes: Sequence[NoteEvent],
    range_strategy: str = RANGE_STRATEGY_DEFAULT,
) -> tuple[list[tuple[float, list[int]]], ArrangeInfo]:
    """(onset in seconds, lyre pitches) groups; kept for scripts that only need onsets."""
    lyre, info = arrange_events(notes, range_strategy)
    return cluster_notes([NoteEvent(n.key, n.start, n.end) for n in lyre]), info


def arrange_notes(
    notes: Sequence[NoteEvent],
    range_strategy: str = RANGE_STRATEGY_DEFAULT,
) -> list[tuple[float, list[int]]]:
    return arrange_with_info(notes, range_strategy)[0]


@dataclass(frozen=True)
class MeterMark:
    quarters: float
    numerator: int
    denominator: int
    exact_timing: bool = field(default=False, compare=False)


def quarters_per_bar(numerator: int, denominator: int) -> float:
    denom = denominator or 4
    return max(1.0, float(numerator)) * 4.0 / float(denom)


def _prepare_tempos(tempos: list[tuple[float, float]]) -> list[tuple[float, float]]:
    cleaned: list[tuple[float, float]] = []
    for raw_time, raw_bpm in sorted(tempos, key=lambda item: item[0]):
        bpm = float(raw_bpm)
        if bpm <= 0:
            continue
        start = max(0.0, float(raw_time))
        if cleaned and abs(cleaned[-1][0] - start) < 1e-6:
            cleaned[-1] = (cleaned[-1][0], bpm)
        else:
            cleaned.append((start, bpm))
    if not cleaned or cleaned[0][0] > 1e-6:
        cleaned.insert(0, (0.0, 120.0))
    return cleaned


METER_SNAP = 0.1  # quarters: a meter change this close to a beat belongs on that beat


def _prepare_meters(meters: list[MeterMark]) -> list[MeterMark]:
    """Meter changes in time order, cleaned up.

    Exported MIDI files sometimes put a time signature a few ticks off the beat. A change
    to another meter that close to a beat is moved onto the beat; restating the current
    meter between two beats is dropped (it would only cut a bar short). Restating it ON a
    beat is kept: that is how a MIDI file marks a pickup bar.
    """
    ordered = sorted(meters, key=lambda item: item.quarters)
    cleaned: list[MeterMark] = []
    for meter in ordered:
        start = max(0.0, float(meter.quarters))
        mark = MeterMark(start, int(meter.numerator), int(meter.denominator or 4), meter.exact_timing)
        if cleaned and not mark.exact_timing:
            prev = cleaned[-1]
            beat = 4.0 / float(prev.denominator or 4)
            rel = (start - prev.quarters) / beat
            off = abs(rel - round(rel)) * beat
            if off > 0.01:
                if (mark.numerator, mark.denominator) == (prev.numerator, prev.denominator):
                    continue
                if off <= METER_SNAP:
                    start = prev.quarters + round(rel) * beat
                    mark = replace(mark, quarters=start)
        if cleaned and abs(cleaned[-1].quarters - start) < 1e-6:
            cleaned[-1] = mark
        else:
            cleaned.append(mark)
    if not cleaned or cleaned[0].quarters > 1e-6:
        cleaned.insert(0, MeterMark(0.0, 4, 4))
    return cleaned


def seconds_to_quarters(t: float, tempos: list[tuple[float, float]]) -> float:
    points = _prepare_tempos(tempos)
    t = max(0.0, t)
    quarters = 0.0
    for index, (start, bpm) in enumerate(points):
        end = points[index + 1][0] if index + 1 < len(points) else float("inf")
        if t <= start + 1e-12:
            break
        seg_end = min(t, end)
        if seg_end > start:
            quarters += (seg_end - start) * (bpm / 60.0)
    return quarters


def quarters_to_seconds(q: float, tempos: list[tuple[float, float]]) -> float:
    points = _prepare_tempos(tempos)
    remaining = max(0.0, q)
    for index, (start, bpm) in enumerate(points):
        end = points[index + 1][0] if index + 1 < len(points) else float("inf")
        span = float("inf") if end == float("inf") else (end - start) * (bpm / 60.0)
        if remaining <= span + 1e-9:
            return start + remaining * (60.0 / bpm)
        remaining -= span
    return points[-1][0]


def bpm_at(t: float, tempos: list[tuple[float, float]]) -> float:
    bpm = 120.0
    for start, value in _prepare_tempos(tempos):
        if start <= t + 1e-8:
            bpm = value
        else:
            break
    return bpm


def _meter_at(q: float, meters: list[MeterMark]) -> MeterMark:
    current = meters[0]
    for meter in meters:
        if meter.quarters <= q + 1e-8:
            current = meter
        else:
            break
    return current


def _bars_until(last_q: float, meters: list[MeterMark]) -> list[tuple[float, float, MeterMark]]:
    prepared = _prepare_meters(meters)
    bars: list[tuple[float, float, MeterMark]] = []
    q = 0.0
    for _ in range(100000):
        if q > last_q + 1e-8:
            break
        meter = _meter_at(q, prepared)
        length = quarters_per_bar(meter.numerator, meter.denominator)
        end = q + length
        for candidate in prepared:
            if q + 1e-6 < candidate.quarters < end - 1e-6:
                end = candidate.quarters
                break
        bars.append((q, end, meter))
        if end <= q:
            break
        q = end
    return bars


# ---------------------------------------------------------------- the grid layout
#
# One notation for every keyboard score (README section 7):
#   - "|" is a bar line; every line starts with the number of its first bar
#   - beats are separated by spaces; every beat has the same number of cells
#     ("每拍 k 格", fixed for the whole piece), written next to each other
#   - a cell holds one key, "( )" keys pressed together, "{ }" keys rolled quickly from
#     low to high, "-" when the earlier note still sounds, "0" when nothing sounds
#   - lowercase keys before a cell are grace notes, played quickly just before it
#   - tempo / meter changes and short bars are marked where they happen: 〔♩=60〕 〔3/4〕
#   - detailed style: every bar has two rows, 旋律 (the tune, one key at a time) above
#     伴奏 (everything else), aligned beat by beat
#   - the 简谱 section uses jianpu octave dots (1̣ low, 1 middle, 1̇ high) and small
#     digits for grace notes, laid out cell for cell exactly like the 键盘谱 section

HOLD = "-"
REST = "0"
ROW_MELODY = "旋律"
ROW_ACCOMP = "伴奏"
SLOT_CHOICES = (1, 2, 3, 4, 6, 8)
LOOSE_SLOT_CHOICES = (1, 2, 4)
KEEP_RATIO = 0.97
LOST_RATIO = 0.002  # share of presses a grid may lose to an earlier press of the same key
MERGE_RATIO = 0.01  # share of onsets a grid may merge into the cell of an earlier onset
ON_GRID_BEATS = 0.08
SLOT_NAMES = {
    1: "全音符",
    2: "二分音符",
    4: "四分音符",
    8: "八分音符",
    16: "十六分音符",
    32: "三十二分音符",
    64: "六十四分音符",
    128: "一百二十八分音符",
}
GRACE_LEAD = 0.125        # quarters: how long before its note a grace note is read back
GRACE_REACH = 0.5         # quarters: a grace note belongs to a note starting this soon after it
ROLL_GAP = (0.05, 0.10)   # quarters between the notes of a chord rolled in a MIDI file (32nds are a run)
ROLL_TOGETHER = 0.01      # quarters: notes this close are struck together (one step of a roll)
ROLL_SPAN = 0.26          # quarters: longest rolled chord of up to 4 steps taken from a MIDI file
ROLL_SPAN_LONG = 0.45     # the same for longer rolls
HOLD_ROUND = 0.8          # a note holds the cells it reaches into by at least 0.2 of a cell
STILL_SOUNDING = 0.1      # quarters: a melody note sounding this long past an onset holds on
ORNAMENT_OFF = 0.25       # cells: a short note this far off its cell is an ornament of the cell's note
ORNAMENT_LONG = 0.6       # cells: ... when it is at most this long


def _bpm_text(bpm: float) -> str:
    # Three decimals keep normalized dotted/tuplet units and fractional tempi,
    # while hiding insignificant MIDI microsecond rounding.
    return f"{bpm:.3f}".rstrip("0").rstrip(".")


def _change_mark(prev: MeterMark | None, meter: MeterMark, prev_bpm: float | None, bpm: float) -> str:
    if prev is None or prev_bpm is None:
        return ""
    parts: list[str] = []
    if (meter.numerator, meter.denominator) != (prev.numerator, prev.denominator):
        parts.append(f"{meter.numerator}/{meter.denominator}")
    if _bpm_text(bpm) != _bpm_text(prev_bpm):
        parts.append(f"♩={_bpm_text(bpm)}")
    if not parts:
        return ""
    return "〔" + " ".join(parts) + "〕"


def _beat_quarters(meter: MeterMark) -> float:
    return 4.0 / float(meter.denominator or 4)


def _bar_index(bars: list[tuple[float, float, MeterMark]], q: float) -> int:
    lo, hi = 0, len(bars) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if bars[mid][0] <= q + 1e-9:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _bar_slots(bar: tuple[float, float, MeterMark], k: int) -> int:
    start, end, meter = bar
    return max(1, int(round((end - start) / (_beat_quarters(meter) / k))))


def _snap(bars: list[tuple[float, float, MeterMark]], q: float, k: int) -> tuple[int, int, float]:
    """(bar index, cell index, distance in beats) of the cell nearest to onset q."""
    b = _bar_index(bars, q)
    start, _end, meter = bars[b]
    slot = _beat_quarters(meter) / k
    cell = int(math.floor((q - start) / slot + 0.5))
    dist = abs(q - (start + cell * slot)) / _beat_quarters(meter)
    if cell >= _bar_slots(bars[b], k):
        if b + 1 < len(bars):
            b, cell = b + 1, 0
        else:
            cell = _bar_slots(bars[b], k) - 1
    return b, max(0, cell), dist


def choose_slots_per_beat(
    onsets: list[float],
    bars: list[tuple[float, float, MeterMark]],
    strict: bool = True,
    keyed: list[tuple[float, int]] | None = None,
) -> int:
    """Cells per beat: the coarsest grid that keeps the rhythm.

    strict (notated music: MIDI / MusicXML / PDF): onsets must sit on the grid and stay apart.
    Loose (audio transcription, performed MIDI): only keep distinct onsets apart.
    keyed: (onset, key) of every note; a grid on which a few fast notes would land in
    the cell of an earlier press of the same key (losing a press) is avoided when a
    finer grid keeps them.
    """
    times = sorted({round(q, 6) for q in onsets})
    if not times:
        return 1

    def kept(k: int, need_on_grid: bool) -> float:
        if need_on_grid:
            # notated music: an onset keeps its rhythm only if it sits on a cell
            # (the allowed distance shrinks with the cell, or a fine grid would fit anything)
            limit = min(ON_GRID_BEATS, 0.25 / k)
            good = sum(1 for q in times if _snap(bars, q, k)[2] <= limit)
            return good / len(times)
        # loose timing: what matters is that distinct onsets stay in distinct cells
        cells = {_snap(bars, q, k)[:2] for q in times}
        return len(cells) / len(times)

    def lost_presses(k: int) -> int:
        taken: dict[tuple[int, int, int], float] = {}
        lost = 0
        for q, key in sorted({(round(q, 6), key) for q, key in keyed or []}):
            b, c, _dist = _snap(bars, q, k)
            first = taken.setdefault((b, c, key), q)
            if abs(first - q) > 1e-6:
                lost += 1
        return lost

    def merged_onsets(k: int) -> int:
        # onsets that would share a cell with an earlier onset: their rhythm is lost
        taken: set[tuple[int, int]] = set()
        merged = 0
        for q in times:
            cell = _snap(bars, q, k)[:2]
            merged += cell in taken
            taken.add(cell)
        return merged

    if strict:
        on_grid = [k for k in SLOT_CHOICES if kept(k, True) >= KEEP_RATIO]
        if on_grid:
            if keyed:
                allowed = max(1, int(LOST_RATIO * len(keyed)))
                allowed_merges = max(1, int(MERGE_RATIO * len(times)))
                for k in on_grid:
                    # below 4 cells per beat merged onsets are lost 16ths; on finer grids they are
                    # only quick ornaments, written in their neighbour's cell
                    if lost_presses(k) <= allowed and (k >= 4 or merged_onsets(k) <= allowed_merges):
                        return k
            return on_grid[0]
    best_k, best = LOOSE_SLOT_CHOICES[-1], -1.0
    for k in LOOSE_SLOT_CHOICES:
        ratio = kept(k, False)
        if ratio >= KEEP_RATIO:
            return k
        if ratio > best + 1e-9:
            best_k, best = k, ratio
    return best_k


def grid_phase(onsets: list[float], meters: list[MeterMark]) -> float:
    """Quarters to move all onsets back when the whole piece is offset from the beat.

    Some MIDI files start with a silence that is not a whole number of beats, so every
    note lies the same fraction of a beat late. Only a clear common offset is removed.
    """
    if len(onsets) < 8:
        return 0.0
    beat = _beat_quarters(_prepare_meters(meters)[0])
    grid = beat / 4
    on_grid = sum(1 for q in onsets if abs(q / grid - round(q / grid)) * grid <= ON_GRID_BEATS * beat)
    if on_grid / len(onsets) >= 0.6:
        return 0.0
    residues = Counter(round((q % grid) / grid, 2) for q in onsets)
    residue, count = residues.most_common(1)[0]
    near = sum(n for r, n in residues.items() if abs(r - residue) <= 0.03 or abs(r - residue) >= 0.97)
    if near / len(onsets) < 0.6 or residue in (0.0, 1.0):
        return 0.0
    return residue * grid


def slot_name(meter: MeterMark, k: int) -> str:
    value = (meter.denominator or 4) * k
    if value in SLOT_NAMES:
        return SLOT_NAMES[value]
    if k % 3 == 0:
        base = (meter.denominator or 4) * (k // 3) * 2
        return f"三连音{SLOT_NAMES.get(base, f'{base}分音符')}"
    return f"1/{value} 音符"


@dataclass
class _Placed:
    """A note on the grid."""

    note: LyreNote
    q: float                          # onset in quarters (a rolled chord's members share one)
    qe: float                         # end in quarters
    roll: int = 0                     # id of the rolled chord it belongs to, 0 = none
    cell: int = -1                    # global cell index
    melody: bool = False
    main: "_Placed | None" = None     # grace notes: the note they lead into
    ornament: bool = False            # short note squeezed into the cell of the note it decorates


def _grid_level(q: float) -> int:
    """0 on a beat, 1 on an eighth, 2 on a sixteenth, 3 elsewhere."""
    for level, step in enumerate((1.0, 0.5, 0.25)):
        if abs(q / step - round(q / step)) * step <= 0.02:
            return level
    return 3


def _find_rolls(placed: list[_Placed], played: bool = True) -> None:
    """Rolled chords (arpeggios).

    Marked ones come from the score (simultaneous notes flagged ``arpeggio``). MIDI files
    usually play them out instead: notes rising in pitch, struck one step after another
    a moment apart (evenly), all still sounding when the last one starts. Other notes
    struck at the same moments (the other hand) do not belong to the roll. A rolled
    chord belongs to the beat it is written on, which is the member closest to the grid.
    played=False leaves played-out rolls alone (each note keeps its own time).
    """
    next_id = 1
    flagged = sorted((p for p in placed if p.note.arpeggio and not p.note.grace), key=lambda p: p.q)
    i = 0
    while i < len(flagged):
        j = i
        while j + 1 < len(flagged) and flagged[j + 1].q - flagged[i].q <= 1e-3:
            j += 1
        if j > i:
            for p in flagged[i : j + 1]:
                p.roll = next_id
            next_id += 1
        i = j + 1
    if not played:
        return
    pool = sorted((p for p in placed if not p.note.grace and not p.roll), key=lambda p: (p.q, p.note.orig))
    steps: list[list[_Placed]] = []
    for p in pool:
        if steps and p.q - steps[-1][0].q <= ROLL_TOGETHER:
            steps[-1].append(p)
        else:
            steps.append([p])
    i = 0
    while i < len(steps):
        j = i
        first_gap: float | None = None
        while j + 1 < len(steps):
            gap = steps[j + 1][0].q - steps[j][0].q
            if not ROLL_GAP[0] <= gap <= ROLL_GAP[1]:
                break
            if first_gap is None:
                first_gap = gap
            elif abs(gap - first_gap) > 0.02:
                break
            j += 1
        members = _rising_members(steps[i : j + 1]) if j - i >= 2 else []
        if members and _is_played_roll(members, j - i + 1):
            anchor = min(members, key=lambda m: (_grid_level(m.q), m.q)).q
            for m in members:
                m.q = anchor
                m.roll = next_id
            next_id += 1
            i = j + 1
            continue
        i += 1


def _rising_members(steps: list[list[_Placed]]) -> list[_Placed]:
    """The notes of a played-out roll: each step adds notes above the previous step's.
    (Two hands rolling together add two notes per step.)"""
    members = list(steps[0])
    heads = list(steps[0])
    for step in steps[1:]:
        joined = [p for p in step if any(p.note.orig > h.note.orig for h in heads)]
        if not joined:
            return []
        members += joined
        heads = joined
    return members


def _is_played_roll(members: list[_Placed], steps: int) -> bool:
    """Notes played as a rolled chord by notation software: short enough that every note
    stays near the chord's beat, one of them on the beat grid, and all still sounding
    when the last one starts. (A performer's spread chord is irregular and stays as it is.)"""
    if len(members) < 3:
        return False
    first = min(m.q for m in members)
    last = max(m.q for m in members)
    if last - first > (ROLL_SPAN if steps <= 4 else ROLL_SPAN_LONG):
        return False
    if min(_grid_level(m.q) for m in members) > 2:
        return False
    return all(m.qe > last + 0.05 for m in members)


def _attach_graces(placed: list[_Placed]) -> None:
    """A grace note leads into the next note of its part (the nearest in pitch when that
    is a chord). A grace note with no note right after it is played as a normal note."""
    mains = sorted((p for p in placed if not p.note.grace), key=lambda p: p.q)
    starts = [p.q for p in mains]
    for g in sorted((p for p in placed if p.note.grace), key=lambda p: p.q):
        i = bisect.bisect_left(starts, g.q - 1e-6)
        cands = [m for m in mains[i : i + 24] if m.q - g.q <= GRACE_REACH]
        if g.note.part >= 0:
            cands = [m for m in cands if m.note.part == g.note.part] or cands
        if not cands:
            g.note.grace = False
            continue
        first = min(m.q for m in cands)
        chord = [m for m in cands if m.q - first <= 1e-6]
        g.main = min(chord, key=lambda m: (abs(m.note.orig - g.note.orig), -m.note.orig))


def _mark_melody(mains: list[_Placed], bar_of) -> None:
    """The tune: in each cell the highest note of the part that has the tune.

    A note under a higher tune note that is still sounding is an inner voice, and a short
    ornament squeezed into a cell does not push the cell's own note out of the tune. In
    bars where the tune part is silent (an intro before the voice comes in) the next part
    (the highest other one) carries the tune. When the parts are not known (one part
    only), the tune does not drop into the bass while it rests.
    """
    known = any(p.note.part > 0 for p in mains)
    if known:
        sounding: dict[int, set[int]] = defaultdict(set)
        for p in mains:
            for b in range(bar_of(p.q), bar_of(max(p.q, p.qe - 1e-6)) + 1):
                sounding[b].add(p.note.part)
        pool = [p for p in mains if p.note.part == min(sounding[bar_of(p.q)])]
    else:
        pool = mains
    by_cell: dict[int, list[_Placed]] = defaultdict(list)
    for p in pool:
        by_cell[p.cell].append(p)
    guard = not known and any(p.note.orig >= 64 for p in pool)
    current: _Placed | None = None
    for cell in sorted(by_cell):
        group = by_cell[cell]
        top = max([p for p in group if not p.ornament] or group, key=lambda p: p.note.orig)
        q = min(p.q for p in group)
        if current is not None and current.qe > q + STILL_SOUNDING and current.note.orig > top.note.orig:
            continue
        if guard and top.note.orig < 60:
            if current is None or current.note.orig - top.note.orig > 7 or q - current.qe > 2.0:
                continue
        top.melody = True
        current = top


@dataclass
class Cell:
    keys: list[int] = field(default_factory=list)
    graces: list[int] = field(default_factory=list)
    roll: bool = False
    held: bool = False
    rolled: set[int] = field(default_factory=set)  # keys of rolled-chord members in this cell


# A beat whose notes do not sit on the piece's cells (a triplet in a piece of 16ths, a
# quintuplet ...) is split into a number of cells of its own, written before it as 〈3〉.
TUPLET_CELLS = (3, 6, 5, 7, 9, 10, 12, 8, 16)
TUPLET_MAX = 16
NOTATED_TUPLET_MAX = 32  # includes a 3-against-4 subdivision without moving either voice
TUPLET_OPEN, TUPLET_CLOSE = "〈", "〉"
_TUPLET_PREFIX = re.compile(r"〈(\d+)〉")
_SHORT_BEAT_PREFIX = re.compile(r"〈(\d+)/(\d+)拍〉")


@dataclass
class Beat:
    """One beat of the grid: where it starts and how long it is (quarters), its cells."""

    start: float
    length: float
    cells: int
    tuplet: bool = False  # split into another number of cells than the piece's (marked 〈n〉)
    length_ratio: float = 1.0  # a final partial beat, relative to the meter's full beat


def _grid_beats(bars: list[tuple[float, float, MeterMark]], k: int) -> list[list[Beat]]:
    """The beats of every bar, each of k cells; the last beat of a short bar keeps the cells
    the bar has room for."""
    out: list[list[Beat]] = []
    for bar in bars:
        start, end, meter = bar
        full_beat = _beat_quarters(meter)
        slot = full_beat / k
        row: list[Beat] = []
        q = start
        while q < end - 1e-9:
            length = min(full_beat, end - q)
            c = max(1, min(k, int(round(length / slot))))
            row.append(Beat(q, length, c, length_ratio=length / full_beat))
            q += length
        out.append(row)
    return out


def _beat_fits(beat: Beat, times: list[float], m: int) -> bool:
    cell = beat.length / m
    limit = min(ON_GRID_BEATS, 0.25 / m) * beat.length
    return all(abs((q - beat.start) / cell - round((q - beat.start) / cell)) * cell <= limit for q in times)


def _split_tuplet_beats(beats: list[list[Beat]], onsets: list[float], k: int, exact_timing: bool = False) -> None:
    """Give a beat whose notes are off the k cells (a triplet, a quintuplet, a run of 32nds)
    the coarsest exact grid available, before considering approximate fits."""
    flat = [b for row in beats for b in row]
    if not flat:
        return
    starts = [b.start for b in flat]
    max_cells = NOTATED_TUPLET_MAX if exact_timing else TUPLET_MAX
    groups: dict[int, list[float]] = defaultdict(list)
    for q in onsets:
        i = max(0, bisect.bisect_right(starts, q + 1e-9) - 1)
        b = flat[i]
        # a note a hair before the next beat belongs to it
        if i + 1 < len(flat) and flat[i + 1].start - q <= min(ON_GRID_BEATS, 0.25 / max_cells) * b.length:
            i += 1
        groups[i].append(q)
    for i, times in groups.items():
        b = flat[i]
        def exact_fit(m: int) -> bool:
            return all(abs((q-b.start)/b.length*m-round((q-b.start)/b.length*m))*b.length/m <= .005*b.length
                       for q in times)
        if _beat_fits(b, times, b.cells) and (not exact_timing or exact_fit(b.cells)):
            continue
        cell = b.length / k
        limit = min(ON_GRID_BEATS, 0.25 / k) * b.length
        off = sum(1 for q in times if abs((q - b.start) / cell - round((q - b.start) / cell)) * cell > limit)
        if exact_timing:
            candidates=sorted(set(range(2,max_cells+1))|{k})
            def quality(m: int):
                cells=[int(math.floor((q-b.start)/b.length*m+.5)) for q in times]
                errors=[abs(q-b.start-c*b.length/m) for q,c in zip(times,cells)]
                return len(set(cells)),max(errors),sum(e*e for e in errors)
            qualities={m:quality(m) for m in candidates}
            # Keeping two nearly simultaneous attacks must not stretch them
            # far apart. Never exceed the finest grid's half-cell error.
            eligible=[m for m in candidates if qualities[m][1] <= b.length/(2*max_cells)+1e-9]
            most=max(qualities[m][0] for m in eligible)
            exact=[m for m in eligible if qualities[m][0]==most and exact_fit(m)]
            b.cells=exact[0] if exact else min(eligible,key=lambda m:(-qualities[m][0],qualities[m][1],qualities[m][2],m))
            b.tuplet=b.cells!=k
            continue
        fitting = []
        choices=set(TUPLET_CELLS) | {2*k,4*k}
        for m in sorted(choices):
            if m == k or m > TUPLET_MAX:
                continue
            # a finer plain grid (32nds in a piece of 16ths) only for a run of them; one
            # short note off the cells is an ornament squeezed into its neighbour's cell
            if m % k == 0 and (m // k) & (m // k - 1) == 0 and off < 2 and not exact_timing:
                continue
            if _beat_fits(b, times, m):
                fitting.append(m)
        if fitting:
            # Prefer a written rhythm that fits exactly (allowing MIDI tick
            # rounding). Otherwise 3/8 and 7/8 can be rounded to 2/6 and 5/6
            # merely because the smaller denominator was tried first.
            exact = [m for m in fitting if exact_fit(m)]
            b.cells = (exact or fitting)[0]
            b.tuplet = True


@dataclass
class GridLayout:
    slots_per_beat: int
    bars: list[tuple[float, float, MeterMark]]
    rows: list[tuple[str, list[list[Cell]]]]  # (label, bar -> cells); label "" for one row
    marks: list[str]                          # change mark at the start of every bar
    first_bpm: float
    first_meter: MeterMark
    style: str = STYLE_DETAILED
    beats: list[list[Beat]] = field(default_factory=list)  # bar -> its beats (empty: k cells each)
    cell_marks: list[dict[int, str]] = field(default_factory=list)  # inline tempo changes, by bar/cell


def _row_cells(
    notes: list[_Placed],
    graces: list[_Placed],
    n_cells: int,
    end_cell,
) -> list[Cell]:
    cells = [Cell() for _ in range(n_cells)]
    for p in sorted(notes, key=lambda p: p.note.key):
        cell = cells[p.cell]
        if p.note.key not in cell.keys:
            cell.keys.append(p.note.key)
        if p.roll:
            cell.rolled.add(p.note.key)
    for p in notes:
        for c in range(p.cell + 1, min(n_cells, end_cell(p))):
            cells[c].held = True
    for g in sorted(graces, key=lambda g: (g.q, g.note.key)):
        cells[g.main.cell].graces.append(g.note.key)
    # a rolled chord needs two keys of its own (octaves folded onto one key leave one)
    for cell in cells:
        cell.roll = len(cell.rolled) >= 2
    return cells


def layout_grid(
    notes: list[LyreNote],
    tempos: list[tuple[float, float]],
    meters: list[MeterMark],
    strict: bool = True,
    style: str = STYLE_DETAILED,
    arpeggio: str = ARPEGGIO_AUTO,
    chord_window: float = CLUSTER_SEC,
) -> GridLayout:
    placed = [
        _Placed(n, seconds_to_quarters(n.start, tempos), seconds_to_quarters(max(n.end, n.start), tempos))
        for n in notes
    ]
    exact_timing = bool(placed) and all(p.note.exact_timing for p in placed)
    if exact_timing:
        meters = [replace(mark, exact_timing=True) for mark in meters]
    shift = 0.0 if exact_timing else grid_phase([p.q for p in placed if not p.note.grace], meters)
    if shift:
        for p in placed:
            p.q = max(0.0, p.q - shift)
            p.qe = max(p.q, p.qe - shift)
    _find_rolls(placed, played=arpeggio != ARPEGGIO_SPREAD and not exact_timing)
    _attach_graces(placed)
    mains = [p for p in placed if p.main is None]
    graces = [p for p in placed if p.main is not None]
    # notes struck within chord_window seconds of each other are one chord (several
    # tracks, or a performance, are never exactly together)
    chord_first: _Placed | None = None
    for p in sorted((p for p in mains if not p.roll), key=lambda p: p.note.start):
        if not exact_timing and chord_first is not None and p.note.start - chord_first.note.start <= chord_window:
            p.q = chord_first.q
        else:
            chord_first = p
    # The final attack can sustain for several bars (including tied notes).
    # Include its release, without creating an extra bar at an exact barline.
    bars = _bars_until(max(max(p.q, p.qe - 1e-6) for p in mains), meters)
    tempo_points = [(seconds_to_quarters(t, tempos), bpm) for t, bpm in _prepare_tempos(tempos)]
    inside_tempos = [(q, bpm) for q, bpm in tempo_points if 1e-6 < q < bars[-1][1] - 1e-6]
    k = choose_slots_per_beat([p.q for p in mains], bars, strict, [(p.q, p.note.key) for p in mains])
    beats = _grid_beats(bars, k)
    if strict:
        _split_tuplet_beats(beats, [p.q for p in mains] + [q for q, _bpm in inside_tempos], k,exact_timing=exact_timing)
    flat = [b for row in beats for b in row]
    beat_starts = [b.start for b in flat]
    beat_first = [0]  # global index of every beat's first cell
    for b in flat:
        beat_first.append(beat_first[-1] + b.cells)
    offsets = [0]
    for row in beats:
        offsets.append(offsets[-1] + sum(b.cells for b in row))
    n_cells = offsets[-1]

    def position(q: float) -> float:
        """Where onset q lies, in (fractional) global cells."""
        if q >= flat[-1].start + flat[-1].length:
            last = flat[-1]
            return beat_first[-1] + (q - last.start - last.length) / (last.length / last.cells)
        i = max(0, bisect.bisect_right(beat_starts, q + 1e-9) - 1)
        b = flat[i]
        return beat_first[i] + (q - b.start) / (b.length / b.cells)

    cell_marks: list[dict[int, str]] = [{} for _bar in bars]
    previous_tempo = tempo_points[0][1]
    for q, bpm in inside_tempos:
        changed = _bpm_text(previous_tempo) != _bpm_text(bpm)
        previous_tempo = bpm
        bar_index = _bar_index(bars, q)
        if not changed or abs(q - bars[bar_index][0]) <= 1e-6:
            continue  # bar-start changes are already shown in layout.marks
        cell = int(math.floor(position(q) + .5)) - offsets[bar_index]
        if 0 <= cell < offsets[bar_index + 1] - offsets[bar_index]:
            cell_marks[bar_index][cell] = f"〔♩={_bpm_text(bpm)}〕"

    for p in mains:
        p.cell = min(n_cells - 1, max(0, int(math.floor(position(p.q) + 0.5))))

    def end_cell(p: _Placed) -> int:
        return max(p.cell + 1, int(math.floor(position(p.qe) + HOLD_ROUND + 1e-9)))

    for p in mains:
        here = position(p.q)
        p.ornament = abs(here - p.cell) >= ORNAMENT_OFF and position(p.qe) - here <= ORNAMENT_LONG

    _mark_melody(mains, lambda q: _bar_index(bars, q))
    melody = [p for p in mains if p.melody]
    tune_at = {(p.cell, p.note.key): p for p in melody}
    accomp: list[_Placed] = []
    for p in mains:
        if p.melody:
            continue
        twin = tune_at.get((p.cell, p.note.key))
        if twin is None:
            accomp.append(p)
            continue
        # a key already in the tune is not pressed (or held) again for the accompaniment;
        # the tune's note takes over how long the key sounds and whether it is rolled
        twin.qe = max(twin.qe, p.qe)
        if p.roll and not twin.roll:
            twin.roll = p.roll
    if style == STYLE_DETAILED and melody and accomp:
        rows_flat = [
            (ROW_MELODY, _row_cells(melody, [g for g in graces if g.main.melody], n_cells, end_cell)),
            (ROW_ACCOMP, _row_cells(accomp, [g for g in graces if not g.main.melody], n_cells, end_cell)),
        ]
        top, low = rows_flat[0][1], rows_flat[1][1]
        for a, b in zip(top, low):
            if b.graces and not b.keys:
                if a.keys:
                    a.graces = b.graces + a.graces
                else:
                    b.keys = sorted(set(b.graces))
                b.graces = []
            # the accompaniment's part of a rolled chord; the tune's key, played last, can
            # be its second key
            b.roll = bool(b.keys) and bool(b.rolled) and len(b.rolled | (a.rolled & set(a.keys))) >= 2
        for a in top:
            a.roll = False
    else:
        rows_flat = [("", _row_cells(mains, graces, n_cells, end_cell))]
    if arpeggio == ARPEGGIO_CHORD:
        # rolled chords are written as chords pressed together
        for _label, cells in rows_flat:
            for cell in cells:
                cell.roll = False
    rows = [(label, [cells[offsets[i] : offsets[i + 1]] for i in range(len(bars))]) for label, cells in rows_flat]

    marks: list[str] = []
    prev_meter: MeterMark | None = None
    prev_bpm: float | None = None
    first_meter = bars[0][2]
    if len(bars) > 1:
        # a short first bar in a meter of its own (a pickup written as 1/4 or 1/16) does
        # not set the piece's meter; it gets a mark like 〔1/4〕 instead
        (s0, e0, m0), (_s1, _e1, m1) = bars[0], bars[1]
        if (m0.numerator, m0.denominator) != (m1.numerator, m1.denominator) and (
            e0 - s0 < quarters_per_bar(m1.numerator, m1.denominator) - 1e-6
        ):
            first_meter = m1
    for start, end, meter in bars:
        bpm = bpm_at(quarters_to_seconds(start, tempos), tempos)
        # a bar cut short (a pickup, or a meter change in mid-bar) is marked with the
        # beats it really has, e.g. 〔1/4〕, so the bar lines are not misread
        n_beats = (end - start) / _beat_quarters(meter)
        full = abs(n_beats - meter.numerator) < 1e-6 or abs(n_beats - round(n_beats)) > 1e-6
        shown = meter if full else MeterMark(start, int(round(n_beats)), meter.denominator)
        reference = prev_meter if prev_meter is not None else first_meter
        marks.append(_change_mark(reference, shown, prev_bpm if prev_bpm is not None else bpm, bpm))
        # A later change inside this bar has already been displayed there.
        # Compare the following bar with the actual final tempo, so it does
        # not repeat the mark one bar late.
        prev_meter = shown
        prev_bpm = bpm_at(quarters_to_seconds(end - 1e-7, tempos), tempos)
    first_bpm = bpm_at(0.0, tempos)
    used_style = STYLE_DETAILED if len(rows) == 2 else STYLE_SIMPLE
    return GridLayout(k, bars, rows, marks, first_bpm, first_meter, used_style, beats, cell_marks)


# ---------------------------------------------------------------- text


def _display_width(text: str) -> int:
    width = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in "WF" else 1
    return width


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _key_text(pitch: int, kind: str) -> str:
    return pitch_to_num(pitch) if kind == "num" else pitch_to_key(pitch)


def _grace_text(pitch: int, kind: str) -> str:
    if kind == "num":
        num = pitch_to_num(pitch)
        return _SMALL_DEGREES[num[0]] + num[1:]
    return pitch_to_key(pitch).lower()


def _cell_token(cell: Cell, kind: str) -> str:
    graces = "".join(_grace_text(p, kind) for p in cell.graces)
    if cell.keys:
        tokens = "".join(_key_text(p, kind) for p in sorted(cell.keys))
        if cell.roll:
            main = "{" + tokens + "}"
        elif len(cell.keys) == 1:
            main = tokens
        else:
            main = "(" + tokens + ")"
    else:
        main = HOLD if cell.held else REST
    return graces + main


def _beat_tokens(cells: list[Cell], k: int, kind: str, beats: list[Beat] | None = None,
                 marks: dict[int, str] | None = None, show_marks: bool = True) -> list[str]:
    def token(index: int) -> str:
        mark = (marks or {}).get(index, "")
        return (mark if show_marks else " " * _display_width(mark)) + _cell_token(cells[index], kind)
    if not beats:
        return ["".join(token(j) for j in range(i, min(i + k, len(cells)))) for i in range(0, len(cells), k)]
    out: list[str] = []
    i = 0
    for b in beats:
        text = "".join(token(j) for j in range(i, i + b.cells))
        short = ""
        if abs(b.length_ratio - 1) > 1e-8:
            from fractions import Fraction
            ratio = Fraction(b.length_ratio).limit_denominator(4096)
            short = f"〈{ratio.numerator}/{ratio.denominator}拍〉"
        out.append(short + (f"{TUPLET_OPEN}{b.cells}{TUPLET_CLOSE}" if b.tuplet or short else "") + text)
        i += b.cells
    return out


def bars_per_line(slots_per_beat: int) -> int:
    return 4 if slots_per_beat <= 2 else 2


def render_grid(layout: GridLayout, kind: str) -> str:
    k = layout.slots_per_beat
    per_line = bars_per_line(k)
    count = len(layout.bars)
    width = len(str(count))
    label_width = max(_display_width(label) for label, _ in layout.rows)
    lines: list[str] = []
    for first in range(0, count, per_line):
        indexes = range(first, min(first + per_line, count))
        tokens = [
            [_beat_tokens(bars[b], k, kind, layout.beats[b] if layout.beats else None,
                          layout.cell_marks[b] if layout.cell_marks else None, row_index == 0) for b in indexes]
            for row_index, (_label, bars) in enumerate(layout.rows)
        ]
        row_bars: list[list[str]] = [[] for _ in layout.rows]
        for j, b in enumerate(indexes):
            mark = layout.marks[b]
            beats = len(tokens[0][j])
            widths = [max(_display_width(tokens[r][j][t]) for r in range(len(layout.rows))) for t in range(beats)]
            for r in range(len(layout.rows)):
                parts = []
                if mark:
                    parts.append(mark if r == 0 else " " * _display_width(mark))
                if len(layout.rows) > 1:
                    parts += [_pad(tokens[r][j][t], widths[t]) for t in range(beats)]
                else:
                    parts += tokens[r][j]
                row_bars[r].append(" ".join(parts))
        if lines and len(layout.rows) > 1:
            lines.append("")
        for r, (label, _bars) in enumerate(layout.rows):
            number = f"{first + 1:>{width}}" if r == 0 else " " * width
            prefix = number + (" " + _pad(label, label_width) if label_width else "")
            lines.append(prefix + " | " + " | ".join(row_bars[r]) + " |")
    return "\n".join(lines)


FINGERTIP_SUBDIVISIONS_PER_BEAT = 4


def _fingertip_group_token(pitches: list[int]) -> str:
    tokens = "".join(pitch_to_key(pitch) for pitch in pitches)
    return tokens if len(pitches) == 1 else "(" + tokens + ")"


def _fingertip_event_bar_index(
    q: float,
    bars: list[tuple[float, float, MeterMark]],
) -> int:
    for index, (start, end, _meter) in enumerate(bars):
        if start - 1e-8 <= q < end - 1e-9 or index == len(bars) - 1:
            return index
    return max(0, len(bars) - 1)


def _group_fingertip_events(
    located: list[tuple[float, list[int]]],
    bars: list[tuple[float, float, MeterMark]],
) -> dict[int, dict[int, list[list[int]]]]:
    """Place attacks on the old format's sixteenth-note grid."""
    grouped: dict[int, dict[int, list[list[int]]]] = defaultdict(lambda: defaultdict(list))
    for q, pitches in sorted(located, key=lambda item: item[0]):
        index = _fingertip_event_bar_index(q, bars)
        start, end, meter = bars[index]
        beat_q = _beat_quarters(meter)
        slot_q = beat_q / FINGERTIP_SUBDIVISIONS_PER_BEAT
        slot_count = max(1, int(round((end - start) / slot_q)))
        slot = int(math.floor((q - start) / slot_q + 0.5))
        if slot >= slot_count and index + 1 < len(bars):
            index += 1
            slot = 0
        else:
            slot = max(0, min(slot_count - 1, slot))
        grouped[index][slot].append(pitches)
    return grouped


def _render_fingertip_half(slots: dict[int, list[list[int]]], first_slot: int) -> str:
    first = "".join(_fingertip_group_token(pitches) for pitches in slots.get(first_slot, []))
    second = "".join(_fingertip_group_token(pitches) for pitches in slots.get(first_slot + 1, []))
    if first and second:
        return first + second
    if first:
        return first
    if second:
        return " " + second
    return "  "


def _render_fingertip_bar(
    start: float,
    end: float,
    meter: MeterMark,
    slots: dict[int, list[list[int]]],
    mark: str,
) -> str:
    beat_q = _beat_quarters(meter)
    beat_count = max(1, int(round((end - start) / beat_q)))
    parts = [mark]
    for beat in range(beat_count):
        base = beat * FINGERTIP_SUBDIVISIONS_PER_BEAT
        parts.append(
            _render_fingertip_half(slots, base)
            + " "
            + _render_fingertip_half(slots, base + 2)
            + "/"
        )
    return "".join(parts)


def fingertip_keyboard_section(
    notes: Sequence[NoteEvent],
    tempos: list[tuple[float, float]],
    meters: list[MeterMark],
    range_strategy: str = RANGE_STRATEGY_DEFAULT,
) -> str:
    """Render the legacy PianoConvert-main keyboard layout as an extra section."""
    lyre, _info = arrange_events(notes, range_strategy)
    clusters = cluster_notes([NoteEvent(n.key, n.start, n.end) for n in lyre])
    if not clusters:
        raise KeyboardScoreError("没有可转换的音符")
    located = [(seconds_to_quarters(start, tempos), pitches) for start, pitches in clusters]
    bars = _bars_until(max(q for q, _pitches in located), meters)
    grouped = _group_fingertip_events(located, bars)
    rendered: list[str] = []
    previous_meter: MeterMark | None = None
    previous_bpm: float | None = None
    for index, (start, end, meter) in enumerate(bars):
        bpm = bpm_at(quarters_to_seconds(start, tempos), tempos)
        mark = _change_mark(previous_meter, meter, previous_bpm, bpm)
        previous_meter, previous_bpm = meter, bpm
        rendered.append(
            _render_fingertip_bar(start, end, meter, grouped.get(index, {}), mark)
        )
        if (index + 1) % 4 == 0 and index + 1 < len(bars):
            rendered.append("")
    return "【指尖键盘谱】\n" + "\n".join(rendered) + "\n"


def _constant_timeline(beat_sec: float, beats_per_bar: int) -> tuple[list[tuple[float, float]], list[MeterMark]]:
    bpm = 60.0 / beat_sec if beat_sec > 0 else 120.0
    return [(0.0, bpm)], [MeterMark(0.0, max(1, int(beats_per_bar)), 4)]


LEGEND_CELLS = (
    "# 读法：| 是小节线；空格隔开每一拍，一拍里的格子紧挨着写；( ) 里的键一起按；{ } 里的键从低到高依次快速按（琶音）；"
    "小写字母是倚音（简谱里是小号数字），先快速带一下，再按紧跟着的键；- 是前面的音还在响；0 是不响（休止）\n"
)
LEGEND_TUPLET = "# 〈3〉这类记号：这一拍不按平常的格数分，而是平均分成 3 格（三连音；〈5〉是五连音、〈6〉是六连音……），只管这一拍\n"
LEGEND_SHORT_BEAT = "# 〈7/8拍〉这类记号：这组格子只占平常一拍的 7/8；后面的〈n〉仍表示均分成 n 格，不补满一拍\n"
LEGEND_TEMPO = "# 〔♩=60〕这类速度记号从紧跟着的格子起生效，小节中途也可能变速\n"
LEGEND_ROWS = "# 每个小节分两行：上面「旋律」一次一个键，下面「伴奏」；上下两行同一拍对齐，一起弹\n"
LEGEND_KEYS = (
    "# 键位：低音 Z X C V B N M = 1̣ 2̣ 3̣ 4̣ 5̣ 6̣ 7̣ ｜ 中音 A S D F G H J = 1 2 3 4 5 6 7 ｜ 高音 Q W E R T Y U = 1̇ 2̇ 3̇ 4̇ 5̇ 6̇ 7̇\n"
)


def _header(title: str, layout: GridLayout, info: ArrangeInfo | None) -> str:
    meter = layout.first_meter
    k = layout.slots_per_beat
    lines = [
        f"# {title}",
        f"# 1=C  {meter.numerator}/{meter.denominator}  ♩={_bpm_text(layout.first_bpm)}  "
        f"每拍 {k} 格（一格 = {slot_name(meter, k)}）  原神风物之诗琴 C3–B5 三排白键",
    ]
    notes: list[str] = []
    if info is not None:
        if info.regional_shifts:
            shifts = "、".join(f"{shift:+d}" for shift in info.regional_shifts)
            notes.append(f"按调号分段移调到 C 调（{shifts} 个半音）")
        elif info.shift:
            notes.append(f"整首移调 {info.shift:+d} 个半音到 C 调")
        if info.snapped:
            notes.append(f"{info.snapped} 个黑键音改成相邻白键")
        if info.folded:
            notes.append(f"{info.folded} 个超出音域的音按八度挪进 C3–B5")
    if notes:
        lines.append("# 处理：" + "；".join(notes))
    legend = (
        LEGEND_CELLS
        + (LEGEND_TUPLET if any(b.tuplet for row in layout.beats for b in row) else "")
        + (LEGEND_SHORT_BEAT if any(abs(b.length_ratio - 1) > 1e-8 for row in layout.beats for b in row) else "")
        + (LEGEND_TEMPO if any(layout.cell_marks) else "")
        + (LEGEND_ROWS if layout.style == STYLE_DETAILED else "")
        + LEGEND_KEYS
    )
    return "\n".join(lines) + "\n" + legend


def keyboard_text(
    notes: Sequence[NoteEvent],
    title: str,
    tempos: list[tuple[float, float]] | None = None,
    meters: list[MeterMark] | None = None,
    *,
    beat_sec: float = 0.5,
    beats_per_bar: int = 4,
    strict: bool = True,
    style: str = STYLE_DETAILED,
    range_strategy: str = RANGE_STRATEGY_DEFAULT,
    arpeggio: str = ARPEGGIO_AUTO,
    chord: str = CHORD_STANDARD,
) -> str:
    """The keyboard score of some notes (times in seconds). range_strategy: how notes are
    moved into the lyre's range; arpeggio: one of ARPEGGIO_MODES; chord: how close
    (CHORD_WINDOWS) notes must be struck to count as one chord."""
    if tempos is None or meters is None:
        tempos, meters = _constant_timeline(beat_sec, beats_per_bar)
    lyre, info = arrange_events(notes, range_strategy)
    if not lyre:
        raise KeyboardScoreError("没有可转换的音符")
    if arpeggio not in ARPEGGIO_MODES:
        arpeggio = ARPEGGIO_AUTO
    layout = layout_grid(lyre, tempos, meters, strict, style, arpeggio, chord_window(chord))
    return (
        _header(title, layout, info)
        + f"\n【简谱】\n{render_grid(layout, 'num')}\n\n"
        + f"【键盘谱】\n{render_grid(layout, 'key')}\n"
    )


# ---------------------------------------------------------------- reading a keyboard score back

_MARK = re.compile(r"〔([^〕]*)〕")
_HEAD = re.compile(r"(\d+)/(\d+)\s+♩=\S+\s+每拍 (\d+) 格")


@dataclass
class ReadCell:
    q: float                  # onset in quarter notes
    slot: float               # cell length in quarter notes
    row: str                  # "旋律", "伴奏" or "" (one row)
    keys: list[int]           # pitches pressed in this cell
    graces: list[int]         # grace notes leading into it
    roll: bool = False
    hold: bool = False        # "-"
    rest: bool = False        # "0"


def _read_cells(bar: str) -> list[tuple[list[int], list[int], bool, bool, bool]]:
    return [c for _cells, c in _read_beats(bar)]


def _read_beats(bar: str) -> list[tuple[float, tuple[list[int], list[int], bool, bool, bool]]]:
    """Cells of a bar, each with the number of cells of its beat (0: the piece's k; n: the
    beat is split into n cells, written 〈n〉 before it)."""
    cells: list[tuple[float, tuple[list[int], list[int], bool, bool, bool]]] = []
    # A tempo mark may sit between any two cells of a written beat. It
    # changes real-time playback speed, never the quarter-note clock.
    split = remaining = 0
    ratio = 1.0
    for beat in _MARK.sub("", bar).split():
        short = _SHORT_BEAT_PREFIX.match(beat)
        if short:
            ratio = int(short.group(1)) / int(short.group(2))
            beat = beat[short.end():]
        m = _TUPLET_PREFIX.match(beat)
        if m:
            remaining = int(m.group(1))
            split = remaining / ratio
            beat = beat[m.end() :]
        graces: list[int] = []
        i = 0
        while i < len(beat):
            ch = beat[i]
            if ch.islower() and ch.upper() in KEY_TO_PITCH:
                graces.append(KEY_TO_PITCH[ch.upper()])
                i += 1
                continue
            if ch in "({":
                j = beat.index(")" if ch == "(" else "}", i)
                keys = sorted(KEY_TO_PITCH[c] for c in beat[i + 1 : j])
                cells.append((split, (keys, graces, ch == "{", False, False)))
                i = j + 1
            elif ch == HOLD:
                cells.append((split, ([], graces, False, True, False)))
                i += 1
            elif ch == REST:
                cells.append((split, ([], graces, False, False, True)))
                i += 1
            else:
                cells.append((split, ([KEY_TO_PITCH[ch]], graces, False, False, False)))
                i += 1
            graces = []
            # The accompaniment contains padding under the melody's inline
            # mark. That whitespace does not start a new (unsplit) beat.
            if remaining:
                remaining -= 1
                if not remaining:
                    split = 0
                    ratio = 1.0
    return cells


def _place_cells(q: float, slot: float, k: int, beats) -> tuple[list[tuple[float, float]], float]:
    """Onset and length of every cell of a bar (slot: length of a normal cell; a split beat
    of n cells lasts k normal cells); returns them and the bar's end."""
    out: list[tuple[float, float]] = []
    for split, _c in beats:
        length = slot * k / split if split else slot
        out.append((q, length))
        q += length
    return out, q


def read_keyboard_cells(text: str) -> list[ReadCell]:
    """Every cell of the 【键盘谱】 section, both rows, with its onset in quarter notes."""
    head = _HEAD.search(text)
    if not head or "【键盘谱】" not in text:
        raise KeyboardScoreError("不是本软件的键盘谱")
    denominator, k = int(head.group(2)), int(head.group(3))
    body = text.split("【键盘谱】", 1)[1]
    out: list[ReadCell] = []
    q_end = 0.0
    system: list[tuple[float, float]] = []  # (onset, cell length) of each bar of the line
    for line in body.splitlines():
        if "|" not in line:
            continue
        prefix, rest = line.split("|", 1)
        found = re.match(r"\s*(\d+)?\s*(\S*)", prefix)
        number, label = found.group(1), found.group(2)
        bars = [b.strip() for b in rest.split("|")]
        if bars and not bars[-1]:
            bars.pop()
        if number is not None:
            system = []
            q = q_end
            for bar in bars:
                mark = _MARK.match(bar)
                if mark:
                    meter = re.search(r"(\d+)/(\d+)", mark.group(1))
                    if meter:
                        denominator = int(meter.group(2))
                    bar = bar[mark.end() :].strip()
                slot = 4.0 / denominator / k
                beats = _read_beats(bar)
                system.append((q, slot))
                placed, q_next = _place_cells(q, slot, k, beats)
                out += [ReadCell(cq, cl, label, *c) for (cq, cl), (_s, c) in zip(placed, beats)]
                q = q_next
            q_end = q
        else:
            for (q0, slot), bar in zip(system, bars):
                mark = _MARK.match(bar)
                if mark:
                    bar = bar[mark.end() :].strip()
                beats = _read_beats(bar)
                placed, _q = _place_cells(q0, slot, k, beats)
                out += [ReadCell(cq, cl, label, *c) for (cq, cl), (_s, c) in zip(placed, beats)]
    return out


def parse_keyboard_text(text: str) -> list[tuple[float, list[int]]]:
    """The 【键盘谱】 section as (onset in quarter notes, pitches), both rows together.

    Grace notes are read as sounding a moment (GRACE_LEAD) before their cell. Used by
    tests and accuracy checks.
    """
    merged: dict[float, set[int]] = defaultdict(set)
    for cell in read_keyboard_cells(text):
        if cell.keys:
            merged[round(cell.q, 6)].update(cell.keys)
        # grace notes are played one after another, each GRACE_LEAD long, right before the cell
        count = len(cell.graces)
        for i, g in enumerate(cell.graces):
            merged[round(cell.q - (count - i) * GRACE_LEAD, 6)].add(g)
    return [(q, sorted(pitches)) for q, pitches in sorted(merged.items())]


# ---------------------------------------------------------------- reading MIDI / MusicXML

_MELODY_NAME = re.compile(r"staff\s*1$|right|melody|lead|vocal|voice|soprano|旋律|右手|人声", re.I)


def _mean_pitch(notes: list[NoteEvent]) -> float:
    return sum(n.pitch for n in notes) / len(notes)


NAME_MATCH_BELOW = 5  # semitones: a part named like the tune may lie this far below the highest


def _melody_index(parts: list[tuple[str, list[NoteEvent]]], explicit: str | None = None) -> int | None:
    """Which part has the tune, or None when there is only one part with notes.

    A part named for it (the PDF reader names its top staff; "melody", "voice", "right"
    in a MIDI file) wins unless it lies well below the other parts (a track called
    "Voice" that only plays low broken chords); otherwise the highest sizeable part.
    """
    sized = [(i, notes) for i, (_name, notes) in enumerate(parts) if notes]
    if len(sized) < 2:
        return None
    if explicit:
        for i, (name, notes) in enumerate(parts):
            if notes and name == explicit:
                return i
    total = sum(len(notes) for _, notes in sized)
    eligible = [(i, notes) for i, notes in sized if len(notes) >= 0.1 * total] or sized
    highest = max(_mean_pitch(notes) for _, notes in eligible)
    for i, notes in eligible:
        if _MELODY_NAME.search(parts[i][0] or "") and _mean_pitch(notes) >= highest - NAME_MATCH_BELOW:
            return i
    return max(eligible, key=lambda item: _mean_pitch(item[1]))[0]


def _number_parts(parts: list[tuple[str, list[NoteEvent]]], melody: int | None) -> list[NoteEvent]:
    """Part 0 is the part with the tune, then the others from high to low; -1 when there
    is one part only."""
    out: list[NoteEvent] = []
    if melody is None:
        for _name, notes in parts:
            for n in notes:
                n.part = -1
                out.append(n)
        return out
    others = [i for i in range(len(parts)) if i != melody and parts[i][1]]
    others.sort(key=lambda i: -_mean_pitch(parts[i][1]))
    for rank, index in enumerate([melody] + others):
        for n in parts[index][1]:
            n.part = rank
            out.append(n)
    return out


def _merge_named_parts(parts: list[tuple[str, list[NoteEvent]]]) -> list[tuple[str, list[NoteEvent]]]:
    """pretty_midi splits a track that uses several programs or channels into several
    instruments with the same name; they are one part."""
    merged: dict[str, list[NoteEvent]] = {}
    out: list[tuple[str, list[NoteEvent]]] = []
    for name, notes in parts:
        if name and name in merged:
            merged[name].extend(notes)
            continue
        bucket = list(notes)
        if name:
            merged[name] = bucket
        out.append((name, bucket))
    return out


def _timeline_from_midi(pm: pretty_midi.PrettyMIDI, exact_timing: bool = False) -> tuple[list[tuple[float, float]], list[MeterMark]]:
    tempos: list[tuple[float, float]] = []
    try:
        times, values = pm.get_tempo_changes()
        tempos = [(float(t), float(bpm)) for t, bpm in zip(times, values) if float(bpm) > 0]
    except Exception:  # noqa: BLE001
        tempos = []
    tempos = _prepare_tempos(tempos)
    meters: list[MeterMark] = []
    for ts in pm.time_signature_changes:
        meters.append(
            MeterMark(
                seconds_to_quarters(float(ts.time), tempos),
                int(ts.numerator),
                int(ts.denominator or 4),
                exact_timing,
            )
        )
    return tempos, _prepare_meters(meters)


def _meta_outside_track0(path: Path) -> tuple[list[tuple[int, float]], list[tuple[int, int, int]], int] | None:
    """Tempo and time-signature events of every track, when some sit outside track 0.

    pretty_midi only reads them from the first track; some exported files put them on a
    later track, which would give the score the wrong tempo and bar length.
    """
    import mido

    try:
        mid = mido.MidiFile(str(path))
    except Exception:  # noqa: BLE001
        return None
    if mid.type == 0 or len(mid.tracks) < 2:
        return None
    tempos: list[tuple[int, float]] = []
    meters: list[tuple[int, int, int]] = []
    outside = False
    for index, track in enumerate(mid.tracks):
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.type == "set_tempo":
                tempos.append((tick, mido.tempo2bpm(msg.tempo)))
                outside |= index > 0
            elif msg.type == "time_signature":
                meters.append((tick, int(msg.numerator), int(msg.denominator)))
                outside |= index > 0
    if not outside:
        return None
    # at the same tick a later track's event wins (sort by tick only; the sort is stable)
    tempos.sort(key=lambda item: item[0])
    meters.sort(key=lambda item: item[0])
    return tempos, meters, mid.ticks_per_beat or 480


def _apply_annotations(notes: list[NoteEvent], events: list[tuple[float, str]]) -> str | None:
    """Mark grace notes and rolled chords named by PianoConvert text events; return the
    name of the track with the tune if one is named."""
    melody: str | None = None
    by_start = sorted(range(len(notes)), key=lambda i: notes[i].start)
    starts = [notes[i].start for i in by_start]

    def at(t: float) -> list[NoteEvent]:
        lo = bisect.bisect_left(starts, t - 0.004)
        hi = bisect.bisect_right(starts, t + 0.004)
        return [notes[by_start[i]] for i in range(lo, hi)]

    for t, text in events:
        if not text.startswith(ANNOTATION):
            continue
        kind, _, value = text[len(ANNOTATION) :].partition(":")
        if kind == "melody":
            melody = value
        elif kind == "grace":
            try:
                pitch = int(value)
            except ValueError:
                continue
            for n in at(t):
                if n.pitch == pitch:
                    n.grace = True
                    break
        elif kind == "arpeggio":
            pitches = {int(v) for v in value.split(",") if v.strip().isdigit()}
            for n in at(t):
                if n.pitch in pitches:
                    n.arpeggio = True
    return melody


def _midi_key_signatures(path: Path) -> list[tuple[int, int]]:
    """Explicit signatures in ticks, including metadata on non-conductor tracks."""
    import mido

    try:
        mid = mido.MidiFile(str(path))
    except Exception:  # noqa: BLE001
        return []
    # Enharmonic signatures have the same pitch-class transposition. Minor keys
    # use their relative major signature, so E minor maps to A minor, not C minor.
    fifths_by_major_pc = (0, -5, 2, -3, 4, -1, 6, 1, -4, 3, -2, 5)
    marks: list[tuple[int, int]] = []
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.type == "key_signature":
                number = pretty_midi.key_name_to_key_number(msg.key)
                relative_major = (number % 12 + (3 if number >= 12 else 0)) % 12
                marks.append((tick, fifths_by_major_pc[relative_major]))
    # At the same tick the later track's event wins, matching the tempo reader.
    return sorted(marks, key=lambda mark: mark[0])


def _apply_staff_key_annotations(parts: list[tuple[str, list[NoteEvent]]], events: list[tuple[float, str]]) -> None:
    """Restore each PDF staff's key instead of applying the conductor's key to all."""
    marks: dict[str, list[tuple[float, int | None]]] = defaultdict(list)
    prefix = f"{ANNOTATION}key:"
    for t, text in events:
        if not text.startswith(prefix):
            continue
        name, separator, value = text[len(prefix):].rpartition(":")
        if not separator:
            continue
        try:
            fifths = None if value == "?" else int(value)
        except ValueError:
            continue
        if fifths is None or -7 <= fifths <= 7:
            marks[name].append((t, fifths))
    for name, notes in parts:
        timeline = sorted(marks.get(name, []), key=lambda mark: mark[0])
        starts = [t for t, _ in timeline]
        for n in notes:
            index = bisect.bisect_right(starts, n.start + 1e-7) - 1
            if index >= 0:
                n.key_fifths = timeline[index][1]
                n.exact_timing = True


def notes_from_midi(path: Path) -> tuple[list[NoteEvent], list[tuple[float, float]], list[MeterMark]]:
    try:
        pm = pretty_midi.PrettyMIDI(str(path))
    except Exception as exc:  # noqa: BLE001
        raise KeyboardScoreError("无法读取这个乐谱文件") from exc
    key_marks = _midi_key_signatures(path)
    key_ticks = [tick for tick, _fifths in key_marks]
    parts: list[tuple[str, list[NoteEvent]]] = []
    for inst in pm.instruments:
        if inst.is_drum:
            continue
        part_notes: list[NoteEvent] = []
        for n in inst.notes:
            key_index = bisect.bisect_right(key_ticks, pm.time_to_tick(n.start)) - 1
            fifths = key_marks[key_index][1] if key_index >= 0 else None
            part_notes.append(NoteEvent(int(n.pitch), float(n.start), float(n.end), key_fifths=fifths))
        parts.append((inst.name, part_notes))
    parts = _merge_named_parts(parts)
    events = [(float(e.time), str(e.text)) for e in pm.text_events]
    notated = any(text == f"{ANNOTATION}timing:notated" or text.startswith(f"{ANNOTATION}key:")
                  for _t, text in events)
    tempos, meters = _timeline_from_midi(pm, exact_timing=notated)
    meta = _meta_outside_track0(path)
    if meta is not None:
        # re-time the notes with the tempo map of all tracks
        tick_tempos, tick_meters, ppq = meta
        quarter_tempos = [(tick / ppq, bpm) for tick, bpm in tick_tempos]
        tempos = _timeline_from_quarter_marks(quarter_tempos, [])

        def retime(t: float) -> float:
            return quarters_to_seconds(pm.time_to_tick(t) / ppq, tempos)

        parts = [(name, [replace(n, start=retime(n.start), end=retime(n.end)) for n in notes]) for name, notes in parts]
        events = [(retime(t), text) for t, text in events]
        meters = _prepare_meters([MeterMark(tick / ppq, num, den or 4, notated) for tick, num, den in tick_meters])
    _apply_staff_key_annotations(parts, events)
    every = [n for _name, notes in parts for n in notes]
    if any(text == f"{ANNOTATION}timing:notated" for _t,text in events):
        for n in every:
            n.exact_timing = True
    explicit = _apply_annotations(every, events)
    notes = _number_parts(parts, _melody_index(parts, explicit))
    return notes, tempos, meters


def _timeline_from_quarter_marks(
    tempo_marks: list[tuple[float, float]],
    meters: list[MeterMark],
) -> list[tuple[float, float]]:
    # sorted by time only: at the same moment the later mark wins
    marks = sorted(((max(0.0, q), bpm) for q, bpm in tempo_marks if bpm > 0), key=lambda m: m[0])
    if not marks or marks[0][0] > 1e-6:
        marks.insert(0, (0.0, 120.0))
    points: list[tuple[float, float]] = []
    q_cursor = 0.0
    t_cursor = 0.0
    prev_bpm = 120.0
    for q, bpm in marks:
        if q > q_cursor:
            t_cursor += (q - q_cursor) * 60.0 / prev_bpm
            q_cursor = q
        if points and abs(points[-1][0] - t_cursor) < 1e-6:
            points[-1] = (points[-1][0], bpm)
        else:
            points.append((t_cursor, bpm))
        prev_bpm = bpm
    return _prepare_tempos(points)


def _has_arpeggio(el) -> bool:
    return any("Arpeggio" in type(x).__name__ for x in getattr(el, "expressions", []) or [])


def _musicxml_meters(score) -> list[MeterMark]:
    """Keep a written pickup boundary even when the time signature is not repeated."""
    from fractions import Fraction
    from music21 import stream

    marks = [
        MeterMark(float(ts.offset), int(ts.numerator), int(ts.denominator or 4), True)
        for ts in score.flatten().getTimeSignatures()
    ]
    parts = list(score.parts)
    openings = [list(part.getElementsByClass(stream.Measure))[:2] for part in parts]
    if not openings or any(len(bars) < 2 for bars in openings):
        return _prepare_meters(marks)
    first, following = openings[0]
    length = float(following.offset - first.offset)
    signature = first.timeSignature
    if signature is None or float(first.offset) != 0 or float(first.paddingLeft) <= 0:
        return _prepare_meters(marks)
    # Different part offsets indicate uncertain recognition, not a reliable pickup.
    if any(abs(float(bars[1].offset) - length) > 1e-6 for bars in openings):
        return _prepare_meters(marks)
    full = float(signature.barDuration.quarterLength)
    if not 0 < length < full - 1e-6:
        return _prepare_meters(marks)
    beat = 4.0 / signature.denominator
    if abs(length / beat - round(length / beat)) < 1e-6:
        short = MeterMark(0, int(round(length / beat)), signature.denominator, True)
    else:
        fraction = Fraction(length / 4).limit_denominator(128)
        short = MeterMark(0, fraction.numerator, fraction.denominator, True)
    # Preserve a genuine signature change at the next bar rather than replacing it.
    if not any(abs(mark.quarters - length) < 1e-6 for mark in marks):
        marks.append(MeterMark(length, signature.numerator, signature.denominator, True))
    marks = [mark for mark in marks if mark.quarters > 1e-6] + [short]
    return _prepare_meters(marks)


def notes_from_musicxml(path: Path) -> tuple[list[NoteEvent], list[tuple[float, float]], list[MeterMark]]:
    try:
        from music21 import chord, key, note, stream, tempo
        from app.score_io import load_musicxml_playback
    except Exception as exc:  # noqa: BLE001
        raise KeyboardScoreError("无法读取这个乐谱文件") from exc
    try:
        score = load_musicxml_playback(path)
    except Exception as exc:  # noqa: BLE001
        raise KeyboardScoreError("无法读取这个乐谱文件") from exc
    flat = score.flatten()
    tempo_marks: list[tuple[float, float]] = []
    for mark in flat.getElementsByClass(tempo.MetronomeMark):
        bpm = mark.getQuarterBPM()
        if bpm:
            tempo_marks.append((float(mark.offset), float(bpm)))
    meters = _musicxml_meters(score)
    tempos = _timeline_from_quarter_marks(tempo_marks, meters)
    sources = list(getattr(score, "parts", []) or []) or [score]
    parts: list[tuple[str, list[NoteEvent]]] = []
    for source in sources:
        name = str(getattr(source, "partName", "") or "")
        part_notes: list[NoteEvent] = []
        source_flat = source.flatten()
        key_marks = sorted(
            ((float(mark.offset), int(mark.sharps)) for mark in source_flat.getElementsByClass(key.KeySignature)
             if mark.sharps is not None),
            key=lambda mark: mark[0],
        )
        key_offsets = [offset for offset, _fifths in key_marks]
        tied: dict[tuple[object, str], int] = {}
        for el in source_flat.notes:
            try:
                q = float(el.offset)
                dur_q = float(el.quarterLength)
                start = quarters_to_seconds(q, tempos)
                end = quarters_to_seconds(q + dur_q, tempos)
            except Exception:  # noqa: BLE001
                continue
            grace = bool(getattr(el.duration, "isGrace", False))
            rolled = _has_arpeggio(el)
            key_index = bisect.bisect_right(key_offsets, q) - 1
            fifths = key_marks[key_index][1] if key_index >= 0 else None
            members = el.notes if isinstance(el, chord.Chord) else [el] if isinstance(el, note.Note) else []
            voice = el.getContextByClass(stream.Voice)
            for n in members:
                tie_key = (voice.id if voice is not None else None, n.pitch.nameWithOctave)
                kind = n.tie.type if n.tie is not None else None
                previous = tied.get(tie_key)
                if (kind in ("stop", "continue") and previous is not None
                        and abs(part_notes[previous].end - start) < 1e-6):
                    part_notes[previous] = replace(part_notes[previous], end=end)
                    if kind == "stop":
                        tied.pop(tie_key, None)
                    continue
                if kind in ("stop", "continue"):
                    # Keep the previous non-reattack behavior for broken OMR ties;
                    # do not stretch an earlier note across a rest or a repeat jump.
                    tied.pop(tie_key, None)
                    continue
                # Only this pitch's tie matters: an untied chord member reattacks.
                part_notes.append(NoteEvent(int(n.pitch.midi), start, end, grace=grace,
                                            arpeggio=rolled, key_fifths=fifths,exact_timing=True))
                tied.pop(tie_key, None)
                if kind in ("start", "continue") and not grace:
                    tied[tie_key] = len(part_notes) - 1
        parts.append((name, part_notes))
    notes = _number_parts(parts, _melody_index(parts))
    return notes, tempos, _prepare_meters(meters)


def _is_tie_continuation(el) -> bool:
    """A tied note that only holds the previous note; it must not be pressed again."""
    tie = getattr(el, "tie", None)
    return tie is not None and tie.type in ("stop", "continue")


def notes_to_text(
    notes: list[NoteEvent],
    tempos: list[tuple[float, float]],
    meters: list[MeterMark],
    title: str,
    strict: bool = True,
    style: str = STYLE_DETAILED,
    range_strategy: str = RANGE_STRATEGY_DEFAULT,
    arpeggio: str = ARPEGGIO_AUTO,
    chord: str = CHORD_STANDARD,
) -> str:
    if not notes:
        raise KeyboardScoreError("没有可转换的音符")
    return keyboard_text(
        notes,
        title,
        tempos,
        meters,
        strict=strict,
        style=style,
        range_strategy=range_strategy,
        arpeggio=arpeggio,
        chord=chord,
    )


def midi_to_keyboard_text(
    path: Path,
    title: str,
    strict: bool = True,
    style: str = STYLE_DETAILED,
    range_strategy: str = RANGE_STRATEGY_DEFAULT,
    arpeggio: str = ARPEGGIO_AUTO,
    chord: str = CHORD_STANDARD,
) -> str:
    notes, tempos, meters = notes_from_midi(path)
    return notes_to_text(notes, tempos, meters, title, strict, style, range_strategy, arpeggio, chord)


def musicxml_to_keyboard_text(
    path: Path,
    title: str,
    strict: bool = True,
    style: str = STYLE_DETAILED,
    range_strategy: str = RANGE_STRATEGY_DEFAULT,
    arpeggio: str = ARPEGGIO_AUTO,
    chord: str = CHORD_STANDARD,
) -> str:
    notes, tempos, meters = notes_from_musicxml(path)
    return notes_to_text(notes, tempos, meters, title, strict, style, range_strategy, arpeggio, chord)


def score_file_to_keyboard_text(
    path: Path,
    title: str | None = None,
    strict: bool = True,
    style: str = STYLE_DETAILED,
    range_strategy: str = RANGE_STRATEGY_DEFAULT,
    arpeggio: str = ARPEGGIO_AUTO,
    chord: str = CHORD_STANDARD,
) -> str:
    """Keyboard score of a MIDI / MusicXML file.

    strict=False is for MIDI transcribed from audio: its onsets are not on a grid, so only
    distinct onsets are kept apart when choosing the cells per beat. style: STYLE_DETAILED
    (tune and accompaniment on two rows) or STYLE_SIMPLE (one row). range_strategy: how
    notes are moved into the lyre's range. arpeggio: how rolled chords are written
    (ARPEGGIO_MODES); chord: how close notes must be struck to count as one chord
    (CHORD_WINDOWS).
    """
    path = Path(path)
    stem = title or path.stem
    suffix = path.suffix.lower()
    if style not in STYLES:
        style = STYLE_DETAILED
    if suffix in MIDI_SUFFIXES:
        return midi_to_keyboard_text(path, stem, strict, style, range_strategy, arpeggio, chord)
    if suffix in MUSICXML_SUFFIXES:
        return musicxml_to_keyboard_text(path, stem, strict, style, range_strategy, arpeggio, chord)
    raise KeyboardScoreError("无法读取这个乐谱文件")


def fingertip_section_from_score_file(
    path: Path,
    range_strategy: str = RANGE_STRATEGY_DEFAULT,
) -> str:
    """Create the appended legacy-style fingertip keyboard section for a score file."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in MIDI_SUFFIXES:
        notes, tempos, meters = notes_from_midi(path)
    elif suffix in MUSICXML_SUFFIXES:
        notes, tempos, meters = notes_from_musicxml(path)
    else:
        raise KeyboardScoreError("无法读取这个乐谱文件")
    return fingertip_keyboard_section(notes, tempos, meters, range_strategy)
