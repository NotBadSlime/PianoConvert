from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pretty_midi

WHITE_PCS = {0, 2, 4, 5, 7, 9, 11}
SNAP_UP = {1: 2, 3: 4, 6: 7, 8: 9, 10: 11}
C3 = 48
B5 = 83
CLUSTER_SEC = 0.050

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
MUSICXML_SUFFIXES = {".musicxml", ".xml"}
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


def _gap_spaces(gap: float, beat_sec: float) -> str:
    spaces = 1
    if beat_sec > 0 and gap >= 0.5 * beat_sec:
        spaces += min(4, int(gap / beat_sec))
    return " " * spaces


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


def render_bodies(
    clusters: list[tuple[float, list[int]]],
    beat_sec: float,
    beats_per_bar: int,
) -> tuple[str, str]:
    if not clusters:
        return "", ""
    bar_sec = max(beat_sec * max(beats_per_bar, 1), 1e-6)
    bars: dict[int, list[tuple[float, list[int]]]] = defaultdict(list)
    for start, pitches in clusters:
        idx = int(start / bar_sec + 1e-9)
        bars[idx].append((start, pitches))
    first = min(bars)
    last = max(bars)
    num_bars: list[str] = []
    key_bars: list[str] = []
    for idx in range(first, last + 1):
        events = bars.get(idx, [])
        num_parts: list[str] = []
        key_parts: list[str] = []
        prev: float | None = None
        for start, pitches in events:
            prefix = "" if prev is None else _gap_spaces(start - prev, beat_sec)
            num_parts.append(prefix + _group_token(pitches, "num"))
            key_parts.append(prefix + _group_token(pitches, "key"))
            prev = start
        num_bars.append("".join(num_parts))
        key_bars.append("".join(key_parts))
    return _join_bars(num_bars), _join_bars(key_bars)


def render_score(
    clusters: list[tuple[float, list[int]]],
    beat_sec: float,
    beats_per_bar: int,
    title: str,
) -> str:
    num_body, key_body = render_bodies(clusters, beat_sec, beats_per_bar)
    return (
        f"# {title}\n"
        f"# 1=C  原神风物之诗琴  C3–C6 三排白键\n\n"
        f"【数字谱】\n{num_body}\n\n"
        f"【键盘谱】\n{key_body}\n"
    )


def _tempo_and_meter(pm: pretty_midi.PrettyMIDI) -> tuple[float, int]:
    beat_sec = 0.5
    try:
        _times, tempos = pm.get_tempo_changes()
        if len(tempos):
            bpm = float(tempos[0])
            if bpm > 0:
                beat_sec = 60.0 / bpm
    except Exception:  # noqa: BLE001
        pass
    beats_per_bar = 4
    if pm.time_signature_changes:
        ts = pm.time_signature_changes[0]
        denom = ts.denominator or 4
        beats_per_bar = max(1, int(ts.numerator * (4 / denom)))
    return beat_sec, beats_per_bar


def notes_from_midi(path: Path) -> tuple[list[NoteEvent], float, int]:
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
    beat_sec, beats = _tempo_and_meter(pm)
    return notes, beat_sec, beats


def notes_from_musicxml(path: Path) -> tuple[list[NoteEvent], float, int]:
    try:
        from music21 import chord, converter, note, tempo
    except Exception as exc:  # noqa: BLE001
        raise KeyboardScoreError("无法读取这个乐谱文件") from exc
    try:
        score = converter.parse(str(path))
    except Exception as exc:  # noqa: BLE001
        raise KeyboardScoreError("无法读取这个乐谱文件") from exc
    bpm = 120.0
    marks = list(score.flatten().getElementsByClass(tempo.MetronomeMark))
    if marks and getattr(marks[0], "number", None):
        bpm = float(marks[0].number)
    beat_sec = 60.0 / bpm if bpm > 0 else 0.5
    beats_per_bar = 4
    ts_list = list(score.flatten().getTimeSignatures())
    if ts_list:
        ts = ts_list[0]
        denom = ts.denominator or 4
        beats_per_bar = max(1, int(ts.numerator * (4 / denom)))
    notes: list[NoteEvent] = []
    for el in score.flatten().notes:
        try:
            start = float(el.offset) * beat_sec
            dur = float(el.quarterLength) * beat_sec
        except Exception:  # noqa: BLE001
            continue
        if isinstance(el, chord.Chord):
            for p in el.pitches:
                notes.append(NoteEvent(int(p.midi), start, start + dur))
        elif isinstance(el, note.Note) and el.pitch is not None:
            notes.append(NoteEvent(int(el.pitch.midi), start, start + dur))
    return notes, beat_sec, beats_per_bar


def notes_to_text(notes: list[NoteEvent], beat_sec: float, beats_per_bar: int, title: str) -> str:
    if not notes:
        raise KeyboardScoreError("没有可转换的音符")
    arranged = arrange_notes(notes)
    if not arranged:
        raise KeyboardScoreError("没有可转换的音符")
    return render_score(arranged, beat_sec, beats_per_bar, title)


def midi_to_keyboard_text(path: Path, title: str) -> str:
    notes, beat_sec, beats = notes_from_midi(path)
    return notes_to_text(notes, beat_sec, beats, title)


def musicxml_to_keyboard_text(path: Path, title: str) -> str:
    notes, beat_sec, beats = notes_from_musicxml(path)
    return notes_to_text(notes, beat_sec, beats, title)


def score_file_to_keyboard_text(path: Path, title: str | None = None) -> str:
    path = Path(path)
    stem = title or path.stem
    suffix = path.suffix.lower()
    if suffix in MIDI_SUFFIXES:
        return midi_to_keyboard_text(path, stem)
    if suffix in MUSICXML_SUFFIXES:
        return musicxml_to_keyboard_text(path, stem)
    raise KeyboardScoreError("无法读取这个乐谱文件")
