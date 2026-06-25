from pathlib import Path

from app.engines.kong_piano import KongPianoEngine
from app.models import NoteEvent, TranscriptionParameters


class FakeTranscriptor:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def transcribe(self, audio, midi_path=None, progress_callback=None, should_cancel=None):
        if progress_callback:
            progress_callback(50, "halfway")
        return {
            "est_note_events": [
                {"onset_time": 0.0, "offset_time": 0.02, "midi_note": 60, "velocity": 50},
                {"onset_time": 0.0, "offset_time": 0.25, "midi_note": 64, "velocity": 80},
            ],
            "est_pedal_events": [{"onset_time": 0.0, "offset_time": 1.0}],
        }


def fake_loader(path, sr, mono):
    return [0.0, 0.1, 0.2], sr


def fake_writer(start_time, note_events, pedal_events, midi_path):
    Path(midi_path).write_text(
        f"notes={len(note_events)} pedals={len(pedal_events or [])}",
        encoding="utf-8",
    )


def test_kong_engine_converts_legacy_events_and_writes_midi(tmp_path):
    engine = KongPianoEngine(
        transcriptor_factory=FakeTranscriptor,
        audio_loader=fake_loader,
        midi_writer=fake_writer,
    )
    params = TranscriptionParameters(minimum_note_duration=0.05, include_pedal=False)

    result = engine.transcribe(
        audio_path=tmp_path / "song.mp3",
        output_dir=tmp_path,
        device="cpu",
        parameters=params,
    )

    assert result.note_events == [NoteEvent(0.0, 0.25, 64, 80)]
    assert result.pedal_events == []
    assert result.midi_path.read_text(encoding="utf-8") == "notes=1 pedals=0"
