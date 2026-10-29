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


def midi_to_musicxml(midi_path: Path, dest: Path, quarter_length_divisors: tuple[int, ...] | None = None) -> None:
    """MusicXML of a MIDI file. quarter_length_divisors: the rhythm grid to round the notes
    to (grid steps per quarter note), music21's own grid otherwise.

    music21 cannot write every MIDI file it reads (a note cut by a quick roll, some drum
    instruments, a measure it builds twice). Then the file is tried again as pretty_midi
    writes it (tracks, meters and tempi tidied up), then without its drum tracks (the lyre
    has no drums), and last on coarser grids, so a MusicXML is nearly always written.
    """
    import tempfile

    grid = tuple(quarter_length_divisors) if quarter_length_divisors else None
    plans: list[tuple[str, tuple[int, ...] | None]] = [("as is", grid), ("rewritten", grid), ("no drums", grid)]
    plans += [("no drums", coarse) for coarse in ((4,), (2,)) if coarse != grid]
    dest.parent.mkdir(parents=True, exist_ok=True)
    error: Exception | None = None
    with tempfile.TemporaryDirectory(prefix="pianoconvert-") as work:
        for variant, divisors in plans:
            try:
                source = midi_path if variant == "as is" else _rewritten_midi(midi_path, Path(work), variant == "no drums")
                _write_musicxml(source, dest, divisors)
                return
            except OSError:
                raise
            except Exception as exc:  # noqa: BLE001 - try the next way
                error = exc
    raise error if error is not None else ValueError("MusicXML 导出失败")


def _write_musicxml(midi_path: Path, dest: Path, divisors: tuple[int, ...] | None) -> None:
    from music21 import converter

    if divisors:
        score = converter.parse(str(midi_path), forceSource=True, quarterLengthDivisors=divisors)
    else:
        score = converter.parse(str(midi_path), forceSource=True)
    _move_written(score.write("musicxml", fp=str(dest)), dest)


def _rewritten_midi(midi_path: Path, work: Path, drop_drums: bool) -> Path:
    """The MIDI file as pretty_midi writes it again; drop_drums leaves out drum tracks."""
    out = work / ("no_drums.mid" if drop_drums else "rewritten.mid")
    if out.exists():
        return out
    pm = pretty_midi.PrettyMIDI(str(midi_path))
    if drop_drums:
        pm.instruments = [inst for inst in pm.instruments if not inst.is_drum]
    pm.write(str(out))
    return out


def musicxml_to_midi(xml_path: Path, dest: Path) -> None:
    """MIDI of a MusicXML file, as notated (repeats are not written out, the same as the
    keyboard score made from it)."""
    from music21 import converter

    score = converter.parse(str(xml_path), forceSource=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _move_written(score.write("midi", fp=str(dest)), dest)


def _move_written(written, dest: Path) -> None:
    """music21 may write to another name than asked for; make sure the file is at dest."""
    out = Path(str(written)) if written is not None else dest
    if out != dest and out.exists():
        dest.write_bytes(out.read_bytes())
        try:
            out.unlink()
        except OSError:
            pass
