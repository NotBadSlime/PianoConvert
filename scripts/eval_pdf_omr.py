"""Measure PDF recognition accuracy against reference MIDI files with the same name.

Usage:
    python scripts/eval_pdf_omr.py PDF_DIR MIDI_DIR OUT_DIR [--limit N] [--names a.pdf b.pdf] [--reuse]

For every PDF that has a same-named .mid in MIDI_DIR, run the app's PDF pipeline and compare
the recognised notes with the MIDI notes:

- seq:   similarity of the onset-ordered pitch sequences (robust to timing drift)
- f1:    note F1 with onsets in quarter notes (±0.26 quarter), exact pitch, after aligning the start
- pc_f1: same as f1 but pitch class only (ignores octave mistakes)
- lyre:  similarity of the final lyre keyboard tokens (what the user actually plays)
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import sys
import time
from pathlib import Path
from threading import Event

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.keyboard_score import NoteEvent, arrange_notes  # noqa: E402

Note = tuple[float, int]


def midi_notes(path: Path) -> list[Note]:
    import mido

    from app.keyboard_score import _enable_lenient_key_signatures

    _enable_lenient_key_signatures()
    mid = mido.MidiFile(str(path))
    ppq = mid.ticks_per_beat or 480
    notes: list[Note] = []
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0 and getattr(msg, "channel", 0) != 9:
                notes.append((tick / ppq, int(msg.note)))
    return sorted(notes)


def xml_notes(path: Path, expand: bool = False) -> list[Note]:
    """Onsets in quarters, read the same way the app reads MusicXML (tied notes are not replayed)."""
    from music21 import chord, converter, note

    from app.keyboard_score import _is_tie_continuation

    score = converter.parse(str(path))
    if expand:
        try:
            score = score.expandRepeats()
        except Exception:  # noqa: BLE001
            pass
    notes: list[Note] = []
    for el in score.flatten().notes:
        q = float(el.offset)
        if isinstance(el, chord.Chord):
            for n in el.notes:
                if not (_is_tie_continuation(n) or _is_tie_continuation(el)):
                    notes.append((q, int(n.pitch.midi)))
        elif isinstance(el, note.Note) and not _is_tie_continuation(el):
            notes.append((q, int(el.pitch.midi)))
    return sorted(set(notes))


def _shift(notes: list[Note]) -> list[Note]:
    if not notes:
        return notes
    first = notes[0][0]
    return [(q - first, p) for q, p in notes]


def seq_similarity(ref: list[Note], est: list[Note]) -> float:
    a = [p for _, p in ref]
    b = [p for _, p in est]
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def timed_f1(ref: list[Note], est: list[Note], tol: float = 0.26, pc: bool = False) -> float:
    if not ref or not est:
        return 0.0
    ref = _shift(ref)
    est = _shift(est)
    key = (lambda p: p % 12) if pc else (lambda p: p)
    pool: dict[int, list[float]] = {}
    for q, p in ref:
        pool.setdefault(key(p), []).append(q)
    hits = 0
    for q, p in est:
        cands = pool.get(key(p))
        if not cands:
            continue
        best = min(range(len(cands)), key=lambda i: abs(cands[i] - q))
        if abs(cands[best] - q) <= tol:
            hits += 1
            cands.pop(best)
    prec = hits / len(est)
    rec = hits / len(ref)
    return 0.0 if hits == 0 else 2 * prec * rec / (prec + rec)


def lyre_tokens(notes: list[Note]) -> list[str]:
    events = [NoteEvent(p, q * 0.5, q * 0.5 + 0.25) for q, p in notes]
    return ["".join(str(p) for p in pitches) for _, pitches in arrange_notes(events)]


def evaluate(pdf: Path, mid: Path, out: Path, reuse: bool, expand: bool = False) -> dict:
    import xml.etree.ElementTree as ET

    from app.pdf_omr import recognize_pdf, slurs_to_ties

    work = out / pdf.stem[:60]
    xml = next(iter(work.glob("*_merged.musicxml")), None) if reuse and work.exists() else None
    started = time.time()
    if xml is None:
        xml = recognize_pdf(pdf, work, Event())
    else:
        # bring XML recognised by an older build up to date (idempotent)
        tree = ET.parse(str(xml))
        slurs_to_ties(tree.getroot())
        tree.write(str(xml), encoding="UTF-8", xml_declaration=True)
    elapsed = time.time() - started
    ref = midi_notes(mid)
    est = xml_notes(xml, expand)
    lyre_ref = lyre_tokens(ref)
    lyre_est = lyre_tokens(est)
    return {
        "name": pdf.stem,
        "secs": round(elapsed, 1),
        "ref_notes": len(ref),
        "est_notes": len(est),
        "seq": round(seq_similarity(ref, est), 3),
        "f1": round(timed_f1(ref, est), 3),
        "pc_f1": round(timed_f1(ref, est, pc=True), 3),
        "lyre": round(difflib.SequenceMatcher(None, lyre_ref, lyre_est, autojunk=False).ratio(), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_dir", type=Path)
    parser.add_argument("midi_dir", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--names", nargs="*")
    parser.add_argument("--reuse", action="store_true", help="reuse recognised XML from a previous run")
    parser.add_argument("--expand", action="store_true", help="play repeats before comparing")
    args = parser.parse_args()

    pairs: list[tuple[Path, Path]] = []
    names = args.names or sorted(p.name for p in args.pdf_dir.glob("*.pdf"))
    for name in names:
        pdf = args.pdf_dir / name
        mid = args.midi_dir / (Path(name).stem + ".mid")
        if pdf.is_file() and mid.is_file():
            pairs.append((pdf, mid))
    pairs = pairs[: args.limit]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for pdf, mid in pairs:
        try:
            row = evaluate(pdf, mid, args.out_dir, args.reuse, args.expand)
        except Exception as exc:  # noqa: BLE001
            row = {"name": pdf.stem, "error": str(exc)[:200]}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    ok = [r for r in rows if "error" not in r]
    if ok:
        summary = {k: round(sum(r[k] for r in ok) / len(ok), 3) for k in ("seq", "f1", "pc_f1", "lyre")}
        summary["files"] = len(ok)
        summary["failed"] = len(rows) - len(ok)
        print("SUMMARY", json.dumps(summary), flush=True)
    with open(args.out_dir / "results.csv", "w", newline="", encoding="utf-8-sig") as fh:
        keys = ["name", "secs", "ref_notes", "est_notes", "seq", "f1", "pc_f1", "lyre", "error"]
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


if __name__ == "__main__":
    main()
