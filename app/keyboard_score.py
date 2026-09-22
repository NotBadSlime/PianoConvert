from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pretty_midi


def _enable_lenient_key_signatures() -> None:
    """Some exported MIDI files store a key signature outside -7..+7 sharps/flats.
    The notes are still usable; treat that meta event as C major instead of failing the file.
    """
    from mido.midifiles.meta import MetaSpec_key_signature, _key_signature_decode, signed

    if getattr(MetaSpec_key_signature.decode, "_pianoconvert_lenient", False):
        return
    original = MetaSpec_key_signature.decode

    def decode(self, message, data):
        try:
            original(self, message, data)
            return
        except Exception:
            key = signed("byte", data[0]) if data else 0
            mode = data[1] if len(data) > 1 else 0
        key = max(-7, min(7, int(key)))
        if (key, mode) not in _key_signature_decode:
            key, mode = 0, 0
        message.key = _key_signature_decode[(key, mode)]

    decode._pianoconvert_lenient = True  # type: ignore[attr-defined]
    MetaSpec_key_signature.decode = decode  # type: ignore[method-assign]


_enable_lenient_key_signatures()

WHITE_PCS = {0, 2, 4, 5, 7, 9, 11}
SNAP_UP = {1: 2, 3: 4, 6: 7, 8: 9, 10: 11}
C3 = 48
B5 = 83
CLUSTER_SEC = 0.050
GRID_QUARTERS = 0.5

PITCH_TO_NUM = {
    48: "-1",
    50: "-2",
    52: "-3",
    53: "-4",
    55: "-5",
    57: "-6",
    59: "-7",
    60: "1",
    62: "2",
    64: "3",
    65: "4",
    67: "5",
    69: "6",
    71: "7",
    72: "+1",
    74: "+2",
    76: "+3",
    77: "+4",
    79: "+5",
    81: "+6",
    83: "+7",
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

MIDI_SUFFIXES = {".mid", ".midi"}
MUSICXML_SUFFIXES = {".musicxml", ".xml", ".mxl"}
SCORE_SUFFIXES = MIDI_SUFFIXES | MUSICXML_SUFFIXES


class KeyboardScoreError(ValueError):
    pass


@dataclass
class NoteEvent:
    pitch: int
    start: float
    end: float


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


def best_transpose(pitches: list[int]) -> int:
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


def arrange_notes(notes: Sequence[NoteEvent]) -> list[tuple[float, list[int]]]:
    if not notes:
        return []
    shift = best_transpose([n.pitch for n in notes])
    mapped = [
        NoteEvent(fold_pitch(snap_pitch(note.pitch + shift)), note.start, note.end) for note in notes
    ]
    return cluster_notes(mapped)


def _group_token(pitches: list[int], kind: str) -> str:
    tokens = [pitch_to_num(p) if kind == "num" else pitch_to_key(p) for p in pitches]
    if len(tokens) == 1:
        return tokens[0]
    return "(" + "".join(tokens) + ")"


@dataclass(frozen=True)
class MeterMark:
    quarters: float
    numerator: int
    denominator: int


def quarters_per_bar(numerator: int, denominator: int) -> float:
    denom = denominator or 4
    return max(1.0, float(numerator) * 4.0 / float(denom))


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


def _prepare_meters(meters: list[MeterMark]) -> list[MeterMark]:
    ordered = sorted(meters, key=lambda item: item.quarters)
    cleaned: list[MeterMark] = []
    for meter in ordered:
        start = max(0.0, float(meter.quarters))
        mark = MeterMark(start, int(meter.numerator), int(meter.denominator or 4))
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


def _gap_spaces(gap_quarters: float) -> str:
    spaces = 1
    if gap_quarters >= 0.5:
        spaces += min(4, int(gap_quarters + 1e-9))
    return " " * spaces


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


def _change_mark(prev: MeterMark | None, meter: MeterMark, prev_bpm: float | None, bpm: float) -> str:
    if prev is None or prev_bpm is None:
        return ""
    parts: list[str] = []
    if (meter.numerator, meter.denominator) != (prev.numerator, prev.denominator):
        parts.append(f"{meter.numerator}/{meter.denominator}")
    if abs(bpm - prev_bpm) >= 0.5:
        parts.append(f"{int(round(bpm))}拍")
    if not parts:
        return ""
    return "〔" + "·".join(parts) + "〕"


def _join_bars(bars: list[str]) -> str:
    lines: list[str] = []
    chunk: list[str] = []
    for i, bar in enumerate(bars, start=1):
        chunk.append(bar)
        if i % 4 == 0:
            lines.append(" / ".join(chunk) + " /")
            chunk = []
    if chunk:
        lines.append(" / ".join(chunk) + " /")
    return "\n".join(lines)


def snap_quarter(q: float) -> float:
    return math.floor(q / GRID_QUARTERS + 0.5) * GRID_QUARTERS


def quantize_onsets(located: list[tuple[float, list[int]]]) -> list[tuple[float, list[int]]]:
    merged: dict[float, list[int]] = {}
    for q, pitches in located:
        key = round(snap_quarter(q), 6)
        bucket = merged.setdefault(key, [])
        for pitch in pitches:
            if pitch not in bucket:
                bucket.append(pitch)
    return [(q, sorted(merged[q])) for q in sorted(merged)]


def _constant_timeline(beat_sec: float, beats_per_bar: int) -> tuple[list[tuple[float, float]], list[MeterMark]]:
    bpm = 60.0 / beat_sec if beat_sec > 0 else 120.0
    return [(0.0, bpm)], [MeterMark(0.0, max(1, int(beats_per_bar)), 4)]


def render_bodies(
    clusters: list[tuple[float, list[int]]],
    beat_sec: float,
    beats_per_bar: int,
    tempos: list[tuple[float, float]] | None = None,
    meters: list[MeterMark] | None = None,
) -> tuple[str, str]:
    if not clusters:
        return "", ""
    if tempos is None or meters is None:
        tempos, meters = _constant_timeline(beat_sec, beats_per_bar)
    located = quantize_onsets([(seconds_to_quarters(start, tempos), pitches) for start, pitches in clusters])
    last_q = max(item[0] for item in located)
    bars = _bars_until(last_q, meters)
    grouped: dict[int, list[tuple[float, list[int]]]] = defaultdict(list)
    for q, pitches in located:
        for index, (start, end, _meter) in enumerate(bars):
            if start - 1e-8 <= q < end - 1e-9 or index == len(bars) - 1:
                grouped[index].append((q, pitches))
                break
    num_bars: list[str] = []
    key_bars: list[str] = []
    prev_meter: MeterMark | None = None
    prev_bpm: float | None = None
    for index, (start, _end, meter) in enumerate(bars):
        bpm = bpm_at(quarters_to_seconds(start, tempos), tempos)
        mark = _change_mark(prev_meter, meter, prev_bpm, bpm)
        prev_meter = meter
        prev_bpm = bpm
        events = sorted(grouped.get(index, []), key=lambda item: item[0])
        num_parts: list[str] = [mark]
        key_parts: list[str] = [mark]
        prev_q: float | None = None
        for q, pitches in events:
            prefix = "" if prev_q is None else _gap_spaces(q - prev_q)
            num_parts.append(prefix + _group_token(pitches, "num"))
            key_parts.append(prefix + _group_token(pitches, "key"))
            prev_q = q
        num_bars.append("".join(num_parts))
        key_bars.append("".join(key_parts))
    return _join_bars(num_bars), _join_bars(key_bars)


def render_score(
    clusters: list[tuple[float, list[int]]],
    beat_sec: float,
    beats_per_bar: int,
    title: str,
    tempos: list[tuple[float, float]] | None = None,
    meters: list[MeterMark] | None = None,
) -> str:
    num_body, key_body = render_bodies(clusters, beat_sec, beats_per_bar, tempos, meters)
    return (
        f"# {title}\n"
        f"# 1=C  原神风物之诗琴  C3–B5 三排白键\n\n"
        f"【数字谱】\n{num_body}\n\n"
        f"【键盘谱】\n{key_body}\n"
    )


def _timeline_from_midi(pm: pretty_midi.PrettyMIDI) -> tuple[list[tuple[float, float]], list[MeterMark]]:
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
            )
        )
    return tempos, _prepare_meters(meters)


def notes_from_midi(path: Path) -> tuple[list[NoteEvent], list[tuple[float, float]], list[MeterMark]]:
    try:
        pm = pretty_midi.PrettyMIDI(str(path))
    except Exception as exc:  # noqa: BLE001
        raise KeyboardScoreError("无法读取这个乐谱文件") from exc
    notes: list[NoteEvent] = []
    for inst in pm.instruments:
        if inst.is_drum:
            continue
        for n in inst.notes:
            notes.append(NoteEvent(int(n.pitch), float(n.start), float(n.end)))
    tempos, meters = _timeline_from_midi(pm)
    return notes, tempos, meters


def _timeline_from_quarter_marks(
    tempo_marks: list[tuple[float, float]],
    meters: list[MeterMark],
) -> list[tuple[float, float]]:
    marks = sorted((max(0.0, q), bpm) for q, bpm in tempo_marks if bpm > 0)
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


def notes_from_musicxml(path: Path) -> tuple[list[NoteEvent], list[tuple[float, float]], list[MeterMark]]:
    try:
        from music21 import chord, converter, note, tempo
    except Exception as exc:  # noqa: BLE001
        raise KeyboardScoreError("无法读取这个乐谱文件") from exc
    try:
        score = converter.parse(str(path))
    except Exception as exc:  # noqa: BLE001
        raise KeyboardScoreError("无法读取这个乐谱文件") from exc
    flat = score.flatten()
    tempo_marks: list[tuple[float, float]] = []
    for mark in flat.getElementsByClass(tempo.MetronomeMark):
        if getattr(mark, "number", None):
            tempo_marks.append((float(mark.offset), float(mark.number)))
    meters = [
        MeterMark(float(ts.offset), int(ts.numerator), int(ts.denominator or 4))
        for ts in flat.getTimeSignatures()
    ]
    tempos = _timeline_from_quarter_marks(tempo_marks, meters)
    notes: list[NoteEvent] = []
    for el in flat.notes:
        try:
            q = float(el.offset)
            dur_q = float(el.quarterLength)
            start = quarters_to_seconds(q, tempos)
            end = quarters_to_seconds(q + dur_q, tempos)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(el, chord.Chord):
            for p in el.pitches:
                notes.append(NoteEvent(int(p.midi), start, end))
        elif isinstance(el, note.Note) and el.pitch is not None:
            notes.append(NoteEvent(int(el.pitch.midi), start, end))
    return notes, tempos, _prepare_meters(meters)


def notes_to_text(
    notes: list[NoteEvent],
    tempos: list[tuple[float, float]],
    meters: list[MeterMark],
    title: str,
) -> str:
    if not notes:
        raise KeyboardScoreError("没有可转换的音符")
    arranged = arrange_notes(notes)
    if not arranged:
        raise KeyboardScoreError("没有可转换的音符")
    return render_score(arranged, 0.5, 4, title, tempos, meters)


def midi_to_keyboard_text(path: Path, title: str) -> str:
    notes, tempos, meters = notes_from_midi(path)
    return notes_to_text(notes, tempos, meters, title)


def musicxml_to_keyboard_text(path: Path, title: str) -> str:
    notes, tempos, meters = notes_from_musicxml(path)
    return notes_to_text(notes, tempos, meters, title)


def score_file_to_keyboard_text(path: Path, title: str | None = None) -> str:
    path = Path(path)
    stem = title or path.stem
    suffix = path.suffix.lower()
    if suffix in MIDI_SUFFIXES:
        return midi_to_keyboard_text(path, stem)
    if suffix in MUSICXML_SUFFIXES:
        return musicxml_to_keyboard_text(path, stem)
    raise KeyboardScoreError("无法读取这个乐谱文件")
