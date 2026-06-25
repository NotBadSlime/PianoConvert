from __future__ import annotations

from collections.abc import Callable, Iterable

from app.engines.base import EngineCancelled, TranscriptionEngine
from app.models import TaskStatus, TranscriptionTask


class TaskQueueRunner:
    def __init__(
        self,
        engine: TranscriptionEngine,
        device: str = "cuda",
        on_task_update: Callable[[TranscriptionTask], None] | None = None,
    ) -> None:
        self.engine = engine
        self.device = device
        self._cancel_requested = False
        self._on_task_update = on_task_update

    def _emit_update(self, task: TranscriptionTask) -> None:
        if self._on_task_update:
            self._on_task_update(task)

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def should_cancel(self) -> bool:
        return self._cancel_requested

    def run_one(self, task: TranscriptionTask) -> TranscriptionTask:
        self._cancel_requested = False
        task.mark(TaskStatus.RUNNING, progress=0)
        self._emit_update(task)

        def on_progress(percent: int, message: str) -> None:
            task.mark(TaskStatus.RUNNING, progress=percent)
            self._emit_update(task)

        try:
            result = self.engine.transcribe(
                audio_path=task.audio_path,
                output_dir=task.output_dir,
                device=self.device,
                parameters=task.parameters,
                progress_callback=on_progress,
                should_cancel=self.should_cancel,
            )
        except EngineCancelled as exc:
            task.mark(TaskStatus.CANCELLED, error=str(exc))
            self._emit_update(task)
            return task
        except Exception as exc:
            task.mark(TaskStatus.FAILED, error=str(exc))
            self._emit_update(task)
            return task

        task.result = result
        task.mark(TaskStatus.COMPLETED, progress=100)
        self._emit_update(task)
        return task

    def run_all(self, tasks: Iterable[TranscriptionTask]) -> list[TranscriptionTask]:
        completed: list[TranscriptionTask] = []
        for task in tasks:
            if self._cancel_requested:
                task.mark(TaskStatus.CANCELLED, error="Queue cancelled")
                self._emit_update(task)
                completed.append(task)
                continue
            completed.append(self.run_one(task))
        return completed
