from app.models import NoteEvent, PedalEvent, TranscriptionParameters
from app.postprocess import apply_postprocess, filter_short_notes, quantize_note_events


def test_filter_short_notes_removes_notes_below_threshold():
    notes = [
        NoteEvent(0.0, 0.02, 60, 70),
        NoteEvent(0.0, 0.20, 64, 80),
    ]

    result = filter_short_notes(notes, minimum_duration=0.05)

    assert result == [notes[1]]


def test_quantize_note_events_snaps_onset_and_offset():
    notes = [NoteEvent(0.03, 0.49, 60, 90)]

    result = quantize_note_events(notes, grid="1/4")

    assert result == [NoteEvent(0.0, 0.5, 60, 90)]


def test_apply_postprocess_can_disable_pedal_events():
    notes = [NoteEvent(0.0, 0.1, 60, 90)]
    pedals = [PedalEvent(0.0, 1.0)]
    params = TranscriptionParameters(minimum_note_duration=0.05, include_pedal=False)

    processed_notes, processed_pedals = apply_postprocess(notes, pedals, params)

    assert processed_notes == notes
    assert processed_pedals == []
