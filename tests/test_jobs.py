from app.jobs import TaskQueueRunner
from app.models import TaskStatus, TranscriptionResult, TranscriptionTask


class FakeEngine:
    def transcribe(self, audio_path, output_dir, device, parameters, progress_callback=None, should_cancel=None):
        if progress_callback:
            progress_callback(100, "done")
        return TranscriptionResult(
            midi_path=output_dir / "song.mid",
            note_events=[],
            pedal_events=[],
            duration=0.0,
            engine_name="fake",
            engine_version="1",
        )


def test_runner_marks_task_completed(tmp_path):
    task = TranscriptionTask(audio_path=tmp_path / "song.mp3")
    runner = TaskQueueRunner(engine=FakeEngine(), device="cpu")

    runner.run_one(task)

    assert task.status is TaskStatus.COMPLETED
    assert task.progress == 100
    assert task.result is not None


def test_runner_marks_task_failed_on_exception(tmp_path):
    class BrokenEngine:
        def transcribe(self, *args, **kwargs):
            raise ValueError("bad input")

    task = TranscriptionTask(audio_path=tmp_path / "bad.mp3")
    runner = TaskQueueRunner(engine=BrokenEngine(), device="cpu")

    runner.run_one(task)

    assert task.status is TaskStatus.FAILED
    assert "bad input" in task.error_message
