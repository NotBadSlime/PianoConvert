from __future__ import annotations

from pathlib import Path

import pretty_midi
from music21 import converter


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
    score = converter.parse(str(midi_path))
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = score.write("musicxml", fp=str(dest))
    # music21 may write a different suffix; ensure dest exists with requested name
    from pathlib import Path as P

    out = P(str(written)) if written is not None else dest
    if out != dest and out.exists():
        dest.write_bytes(out.read_bytes())
        if out != dest:
            try:
                out.unlink()
            except OSError:
                pass
