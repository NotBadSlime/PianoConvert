from app.models import (
    NoteEvent,
    PedalEvent,
    TaskStatus,
    TranscriptionResult,
    TranscriptionTask,
)


def test_transcription_task_defaults_to_pending(tmp_path):
    task = TranscriptionTask(audio_path=tmp_path / "song.mp3")

    assert task.status is TaskStatus.PENDING
    assert task.progress == 0
    assert task.output_dir == tmp_path
    assert task.parameters.preset == "balanced"


def test_note_event_round_trips_to_legacy_dict():
    event = NoteEvent(onset_time=1.25, offset_time=2.5, midi_note=60, velocity=88)

    assert event.to_legacy_dict() == {
        "onset_time": 1.25,
        "offset_time": 2.5,
        "midi_note": 60,
        "velocity": 88,
    }


def test_result_reports_duration_from_notes(tmp_path):
    result = TranscriptionResult(
        midi_path=tmp_path / "song.mid",
        note_events=[
            NoteEvent(0.0, 1.0, 60, 80),
            NoteEvent(0.5, 3.25, 64, 90),
        ],
        pedal_events=[PedalEvent(0.0, 2.0)],
        duration=0.0,
        engine_name="test",
        engine_version="1",
    )

    assert result.note_count == 2
    assert result.effective_duration == 3.25
