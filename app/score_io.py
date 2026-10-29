from __future__ import annotations

from pathlib import Path

import pretty_midi


def write_notes_midi(
    dest: Path,
    notes: list[tuple[int, float, float, int]],
    program: int = 0,
    is_drum: bool = False,
) -> None:
    pm = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(program=program, is_drum=is_drum)
    for pitch, start, end, velocity in notes:
        inst.notes.append(
            pretty_midi.Note(velocity=int(velocity), pitch=int(pitch), start=float(start), end=float(end))
        )
    pm.instruments.append(inst)
    dest.parent.mkdir(parents=True, exist_ok=True)
    pm.write(str(dest))


def midi_to_musicxml(midi_path: Path, dest: Path) -> None:
    """MusicXML of a MIDI file.

    music21 cannot write some rhythms it reads (for example a note cut by a quick roll);
    then the file is read again on a coarser grid, so a MusicXML is always written.
    """
    from music21 import converter

    attempts: list[tuple[int, ...] | None] = [None, (4,), (2,)]
    dest.parent.mkdir(parents=True, exist_ok=True)
    error: Exception | None = None
    for divisors in attempts:
        try:
            if divisors:
                score = converter.parse(str(midi_path), forceSource=True, quarterLengthDivisors=divisors)
            else:
                score = converter.parse(str(midi_path), forceSource=True)
            written = score.write("musicxml", fp=str(dest))
        except OSError:
            raise
        except Exception as exc:  # noqa: BLE001 - try the next, coarser grid
            error = exc
            continue
        out = Path(str(written)) if written is not None else dest
        if out != dest and out.exists():
            dest.write_bytes(out.read_bytes())
            try:
                out.unlink()
            except OSError:
                pass
        return
    raise error if error is not None else ValueError("MusicXML 导出失败")
