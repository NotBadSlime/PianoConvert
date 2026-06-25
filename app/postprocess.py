from __future__ import annotations

from app.models import NoteEvent, PedalEvent, TranscriptionParameters


GRID_SECONDS = {
    "off": 0.0,
    "1/8": 0.25,
    "1/16": 0.125,
    "1/4": 0.5,
    "triplet": 1.0 / 3.0,
}


def filter_short_notes(
    note_events: list[NoteEvent],
    minimum_duration: float,
) -> list[NoteEvent]:
    return [event for event in note_events if event.duration >= minimum_duration]


def quantize_time(value: float, step: float) -> float:
    if step <= 0:
        return value
    return round(value / step) * step


def quantize_note_events(note_events: list[NoteEvent], grid: str) -> list[NoteEvent]:
    step = GRID_SECONDS.get(grid, 0.0)
    if step <= 0:
        return list(note_events)

    quantized: list[NoteEvent] = []
    for event in note_events:
        onset = max(0.0, quantize_time(event.onset_time, step))
        offset = max(onset + 0.01, quantize_time(event.offset_time, step))
        quantized.append(
            NoteEvent(
                onset_time=round(onset, 6),
                offset_time=round(offset, 6),
                midi_note=event.midi_note,
                velocity=event.velocity,
            )
        )
    return quantized


def apply_postprocess(
    note_events: list[NoteEvent],
    pedal_events: list[PedalEvent],
    parameters: TranscriptionParameters,
) -> tuple[list[NoteEvent], list[PedalEvent]]:
    notes = filter_short_notes(note_events, parameters.minimum_note_duration)
    notes = quantize_note_events(notes, parameters.quantization_grid)
    pedals = list(pedal_events) if parameters.include_pedal else []
    return notes, pedals
