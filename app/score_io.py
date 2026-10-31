from __future__ import annotations

import bisect
import copy
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


def _preserve_staff_measure_spans(score) -> None:
    """Keep the shared bar clock when music21 expands a multi-staff part.

    The XML importer places both staves at the same bar offsets, but repeat
    expansion appends each staff's measures using its own highestTime. An OMR
    bar of 5.5 quarters on one staff and 5.25 on the other then drifts by .25.
    Fill only unused time up to the existing shared boundary; never shorten
    notes, infer missing notes, or align independently written parts.
    """
    from music21 import layout, note, stream

    for group in score.recurse().getElementsByClass(layout.StaffGroup):
        parts = list(group.getSpannedElements())
        if len(parts) < 2 or not all(isinstance(part, stream.PartStaff) for part in parts):
            continue
        source_ids = {str(part.id).rpartition('-Staff')[0] for part in parts}
        if len(source_ids) != 1 or not next(iter(source_ids)):
            continue
        measures = [list(part.getElementsByClass(stream.Measure)) for part in parts]
        if not measures[0] or any(len(bars) != len(measures[0]) for bars in measures):
            continue
        if any(any(a.number != b.number or abs(float(a.offset - b.offset)) > 1e-6
                   for a, b in zip(measures[0], bars)) for bars in measures[1:]):
            continue
        for index in range(len(measures[0]) - 1):
            span = measures[0][index+1].offset - measures[0][index].offset
            for bars in measures:
                bar = bars[index]
                if span - bar.highestTime > 1e-6:
                    rest = note.Rest(quarterLength=span - bar.highestTime)
                    rest.style.hideObjectOnPrint = True
                    bar.insert(bar.highestTime, rest)


def _restore_performed_tempos(performed, written_tempos) -> None:
    """Restore each repeat's actual first entry, shared across all parts.

    A second repeat can start after an alternate ending. Its entry tempo is
    the tempo that was performed there first, rather than a mark in the
    skipped first ending. Mid-measure marks apply after the entry snapshot.
    """
    from music21 import stream, tempo

    if not written_tempos:
        return
    parts = list(performed.parts)
    measures = list(parts[0].getElementsByClass(stream.Measure)) if parts else []
    if not measures:
        return
    explicit = sorted(
        ((float(mark.offset), mark) for mark in performed.flatten().getElementsByClass(tempo.MetronomeMark)
         if mark.getQuarterBPM() and mark.getQuarterBPM() > 0),
        key=lambda item: item[0],
    )
    written_offsets = [q for q, _mark in written_tempos]
    current = tempo.MetronomeMark(number=120)
    first_entries = {}
    cursor = 0
    previous = -1
    for measure in measures:
        q = float(measure.getOffsetInHierarchy(performed))
        index = measure._pianoconvert_playback_index
        while cursor < len(explicit) and explicit[cursor][0] < q - 1e-9:
            current = explicit[cursor][1]
            cursor += 1
        at_entry = cursor < len(explicit) and abs(explicit[cursor][0] - q) <= 1e-9
        if (previous == -1 or index <= previous) and not at_entry:
            source_q = measure._pianoconvert_playback_source_q
            source_index = bisect.bisect_right(written_offsets, source_q + 1e-9) - 1
            inherited = written_tempos[source_index][1] if source_index >= 0 else tempo.MetronomeMark(number=120)
            current = first_entries.get(index, inherited)
            measure.insert(0, copy.deepcopy(current))
        while cursor < len(explicit) and explicit[cursor][0] <= q + 1e-9:
            current = explicit[cursor][1]
            cursor += 1
        first_entries.setdefault(index, copy.deepcopy(current))
        previous = index


def load_musicxml_playback(xml_path: Path):
    """Read the performed order once, shared by MIDI and keyboard-score export.

    music21's MIDI writer expands repeats implicitly; keyboard extraction must
    use the same order. On a repeat jump, also restore the source measure's
    inherited tempo, meter and key instead of keeping a later modulation from
    the end of the repeated passage. A part with later explicit key changes
    starts in implicit C; a wholly keyless part retains unknown-key inference.
    The source MusicXML is never rewritten.
    """
    from music21 import converter, key, meter, stream, tempo

    score = converter.parse(str(xml_path), forceSource=True)
    _preserve_staff_measure_spans(score)
    # Tempo is shared by simultaneous parts. A part that prints its first
    # tempo later must inherit the opening mark from the other part, not
    # inject a default 120 that overrides it after flattening the score.
    global_tempos = sorted(
        ((float(mark.offset), mark) for mark in score.flatten().getElementsByClass(tempo.MetronomeMark)
         if mark.getQuarterBPM() and mark.getQuarterBPM() > 0),
        key=lambda item: item[0],
    )
    marker_types = (key.KeySignature, meter.TimeSignature)
    for part in score.parts:
        has_key = bool(list(part.recurse().getElementsByClass(key.KeySignature)))
        has_meter = bool(list(part.recurse().getElementsByClass(meter.TimeSignature)))
        active = {
            key.KeySignature: key.KeySignature(0) if has_key else None,
            meter.TimeSignature: meter.TimeSignature("4/4") if has_meter else None,
        }
        for index, measure in enumerate(part.getElementsByClass(stream.Measure)):
            source_q = float(measure.getOffsetInHierarchy(score))
            flattened = measure.flatten()
            marks = [(float(mark.offset), marker_type, mark)
                     for marker_type in marker_types
                     for mark in flattened.getElementsByClass(marker_type)]
            marks.sort(key=lambda item: item[0])
            for offset, marker_type, mark in marks:
                if offset <= 0:
                    active[marker_type] = mark
            measure._pianoconvert_playback_index = index
            measure._pianoconvert_playback_source_q = source_q
            measure._pianoconvert_playback_context = tuple(
                copy.deepcopy(active[marker_type]) for marker_type in marker_types
            )
            for _offset, marker_type, mark in marks:
                active[marker_type] = mark

    performed = score.expandRepeats()
    _restore_performed_tempos(performed, global_tempos)
    for part in performed.parts:
        previous = -1
        for measure in part.getElementsByClass(stream.Measure):
            index = measure._pianoconvert_playback_index
            # Establish the same initial context on the first play and on a
            # repeat jump. Otherwise a missing initial key is inferred once
            # from chromatic notes, but restored as C on the second play.
            if previous == -1 or index != previous + 1:
                for marker_type, mark in zip(marker_types, measure._pianoconvert_playback_context):
                    if mark is None:
                        continue
                    already_printed = any(
                        float(item.getOffsetInHierarchy(measure)) == 0
                        for item in measure.recurse().getElementsByClass(marker_type)
                    )
                    if not already_printed:
                        measure.insert(0, copy.deepcopy(mark))
            previous = index
            del measure._pianoconvert_playback_index
            del measure._pianoconvert_playback_source_q
            del measure._pianoconvert_playback_context
    return performed


def musicxml_to_midi(xml_path: Path, dest: Path) -> None:
    """MIDI in the same performed order as keyboard-score extraction."""
    score = load_musicxml_playback(xml_path)
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
