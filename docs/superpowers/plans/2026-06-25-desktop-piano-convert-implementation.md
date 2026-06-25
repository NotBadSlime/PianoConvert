# Desktop Piano Convert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved offline Windows desktop piano transcription workbench and prepare it for submission to `NotBadSlime/PianoConvert`.

**Architecture:** Refactor the existing command-line transcription flow into testable services, then add a PySide6 desktop UI on top. The transcription engine stays pluggable: first implementation wraps the existing Kong/ByteDance piano model, while UI and job code depend on the engine interface.

**Tech Stack:** Python 3.10, PySide6/Qt, PyTorch, librosa, mido, pytest, existing `piano_transcription_inference` package.

---

## File Structure

- Create `app/__init__.py`: desktop app package marker.
- Create `app/models.py`: dataclasses and enums for tasks, results, parameters, note events, and pedal events.
- Create `app/postprocess.py`: short-note filtering, quantization, pedal toggle, and event conversion helpers.
- Create `app/engines/__init__.py`: engine package marker.
- Create `app/engines/base.py`: engine protocol, progress callback types, cancellation exception.
- Create `app/engines/kong_piano.py`: adapter around the current model package.
- Create `app/jobs.py`: synchronous queue runner for tests and UI worker orchestration helpers.
- Create `app/ui/__init__.py`: UI package marker.
- Create `app/ui/piano_roll.py`: Qt widget that renders piano-roll notes and a playhead.
- Create `app/ui/main_window.py`: desktop workbench UI.
- Create `app/__main__.py`: `python -m app` entrypoint.
- Create `launch_desktop.bat`: Windows launcher for the desktop app.
- Create `README.md`: setup, run, and product summary.
- Create `tests/test_models.py`: data model tests.
- Create `tests/test_postprocess.py`: post-processing tests.
- Create `tests/test_jobs.py`: queue/status tests.
- Create `tests/test_kong_engine.py`: engine adapter tests using dependency injection.
- Modify `requirements.txt`: add PySide6 and pytest.
- Modify `piano_transcription_inference/inference.py`: accept configurable thresholds, checkpoint path, progress callback, and cancellation callback.
- Modify `piano_transcription_inference/pytorch_utils.py`: expose progress callback and cancellation check in the forward loop.

---

### Task 1: Dependencies And Baseline

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add desktop/test dependencies**

Add these lines to `requirements.txt`:

```text
PySide6>=6.7
pytest>=8.0
```

- [ ] **Step 2: Install dependencies in the project venv**

Run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Expected: install completes with no broken requirements.

- [ ] **Step 3: Verify baseline imports**

Run:

```powershell
.\.venv\Scripts\python.exe -c "import PySide6, pytest, torch; print('baseline imports ok')"
```

Expected: prints `baseline imports ok`.

---

### Task 2: Core Models

**Files:**
- Create: `app/__init__.py`
- Create: `app/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_models.py`:

```python
from pathlib import Path

from app.models import (
    NoteEvent,
    PedalEvent,
    TaskStatus,
    TranscriptionParameters,
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
```

- [ ] **Step 2: Verify tests fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_models.py -v
```

Expected: FAIL because `app.models` does not exist.

- [ ] **Step 3: Implement models**

Create `app/__init__.py` as an empty file.

Create `app/models.py` with dataclasses for the tested behavior:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from uuid import uuid4


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_RERUN = "needs_rerun"


@dataclass(frozen=True)
class NoteEvent:
    onset_time: float
    offset_time: float
    midi_note: int
    velocity: int

    @property
    def duration(self) -> float:
        return max(0.0, self.offset_time - self.onset_time)

    def to_legacy_dict(self) -> dict[str, float | int]:
        return {
            "onset_time": self.onset_time,
            "offset_time": self.offset_time,
            "midi_note": self.midi_note,
            "velocity": self.velocity,
        }

    @classmethod
    def from_legacy_dict(cls, event: dict[str, float | int]) -> "NoteEvent":
        return cls(
            onset_time=float(event["onset_time"]),
            offset_time=float(event["offset_time"]),
            midi_note=int(event["midi_note"]),
            velocity=int(event["velocity"]),
        )


@dataclass(frozen=True)
class PedalEvent:
    onset_time: float
    offset_time: float

    def to_legacy_dict(self) -> dict[str, float]:
        return {
            "onset_time": self.onset_time,
            "offset_time": self.offset_time,
        }

    @classmethod
    def from_legacy_dict(cls, event: dict[str, float]) -> "PedalEvent":
        return cls(
            onset_time=float(event["onset_time"]),
            offset_time=float(event["offset_time"]),
        )


@dataclass
class TranscriptionParameters:
    preset: str = "balanced"
    onset_threshold: float = 0.3
    offset_threshold: float = 0.3
    frame_threshold: float = 0.1
    pedal_offset_threshold: float = 0.2
    minimum_note_duration: float = 0.04
    quantization_grid: str = "off"
    include_pedal: bool = True

    @classmethod
    def for_preset(cls, preset: str) -> "TranscriptionParameters":
        if preset == "precise":
            return cls(
                preset="precise",
                onset_threshold=0.35,
                offset_threshold=0.35,
                frame_threshold=0.12,
                pedal_offset_threshold=0.25,
                minimum_note_duration=0.06,
                quantization_grid="off",
                include_pedal=True,
            )
        return cls(preset="balanced")


@dataclass
class TranscriptionResult:
    midi_path: Path
    note_events: list[NoteEvent]
    pedal_events: list[PedalEvent]
    duration: float
    engine_name: str
    engine_version: str
    created_at: datetime = field(default_factory=datetime.now)
    log_path: Path | None = None

    @property
    def note_count(self) -> int:
        return len(self.note_events)

    @property
    def effective_duration(self) -> float:
        if self.duration > 0:
            return self.duration
        note_end = max((event.offset_time for event in self.note_events), default=0.0)
        pedal_end = max((event.offset_time for event in self.pedal_events), default=0.0)
        return max(note_end, pedal_end)


@dataclass
class TranscriptionTask:
    audio_path: Path
    output_dir: Path | None = None
    id: str = field(default_factory=lambda: uuid4().hex)
    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0
    selected_engine: str = "kong_piano"
    parameters: TranscriptionParameters = field(default_factory=TranscriptionParameters)
    result: TranscriptionResult | None = None
    error_message: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        self.audio_path = Path(self.audio_path)
        self.output_dir = Path(self.output_dir) if self.output_dir else self.audio_path.parent

    @property
    def display_name(self) -> str:
        return self.audio_path.name

    def mark(self, status: TaskStatus, progress: int | None = None, error: str = "") -> None:
        self.status = status
        if progress is not None:
            self.progress = max(0, min(100, progress))
        self.error_message = error
        self.updated_at = datetime.now()
```

- [ ] **Step 4: Verify tests pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_models.py -v
```

Expected: PASS.

---

### Task 3: Post-Processing

**Files:**
- Create: `app/postprocess.py`
- Test: `tests/test_postprocess.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_postprocess.py`:

```python
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
```

- [ ] **Step 2: Verify tests fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_postprocess.py -v
```

Expected: FAIL because `app.postprocess` does not exist.

- [ ] **Step 3: Implement post-processing**

Create `app/postprocess.py`:

```python
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
```

- [ ] **Step 4: Verify tests pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_postprocess.py -v
```

Expected: PASS.

---

### Task 4: Engine Interface And Adapter

**Files:**
- Create: `app/engines/__init__.py`
- Create: `app/engines/base.py`
- Create: `app/engines/kong_piano.py`
- Modify: `piano_transcription_inference/inference.py`
- Modify: `piano_transcription_inference/pytorch_utils.py`
- Test: `tests/test_kong_engine.py`

- [ ] **Step 1: Write failing adapter test**

Create `tests/test_kong_engine.py`:

```python
from pathlib import Path

from app.engines.kong_piano import KongPianoEngine
from app.models import NoteEvent, PedalEvent, TranscriptionParameters


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
```

- [ ] **Step 2: Verify adapter test fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_kong_engine.py -v
```

Expected: FAIL because engine files do not exist.

- [ ] **Step 3: Implement engine base and adapter**

Create `app/engines/__init__.py` as an empty file.

Create `app/engines/base.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from app.models import TranscriptionParameters, TranscriptionResult

ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]


class EngineCancelled(Exception):
    pass


class TranscriptionEngine(Protocol):
    name: str
    version: str

    def transcribe(
        self,
        audio_path: Path,
        output_dir: Path,
        device: str,
        parameters: TranscriptionParameters,
        progress_callback: ProgressCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> TranscriptionResult:
        ...
```

Create `app/engines/kong_piano.py`:

```python
from __future__ import annotations

from pathlib import Path

from app.engines.base import CancelCallback, EngineCancelled, ProgressCallback
from app.models import NoteEvent, PedalEvent, TranscriptionParameters, TranscriptionResult
from app.postprocess import apply_postprocess
from piano_transcription_inference import PianoTranscription, load_audio, sample_rate
from piano_transcription_inference.utilities import write_events_to_midi


class KongPianoEngine:
    name = "kong_piano"
    version = "local"

    def __init__(
        self,
        transcriptor_factory=PianoTranscription,
        audio_loader=load_audio,
        midi_writer=write_events_to_midi,
    ) -> None:
        self._transcriptor_factory = transcriptor_factory
        self._audio_loader = audio_loader
        self._midi_writer = midi_writer

    def transcribe(
        self,
        audio_path: Path,
        output_dir: Path,
        device: str,
        parameters: TranscriptionParameters,
        progress_callback: ProgressCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> TranscriptionResult:
        audio_path = Path(audio_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        midi_path = output_dir / f"{audio_path.stem}.mid"

        if should_cancel and should_cancel():
            raise EngineCancelled("Task cancelled before audio loading")

        audio, _ = self._audio_loader(str(audio_path), sr=sample_rate, mono=True)
        duration = len(audio) / sample_rate if sample_rate else 0.0

        transcriptor = self._transcriptor_factory(
            device=device,
            onset_threshold=parameters.onset_threshold,
            offset_threshold=parameters.offset_threshold,
            frame_threshold=parameters.frame_threshold,
            pedal_offset_threshold=parameters.pedal_offset_threshold,
        )
        raw_result = transcriptor.transcribe(
            audio,
            midi_path=None,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
        )

        note_events = [
            NoteEvent.from_legacy_dict(event)
            for event in raw_result.get("est_note_events", [])
        ]
        pedal_events = [
            PedalEvent.from_legacy_dict(event)
            for event in raw_result.get("est_pedal_events") or []
        ]
        note_events, pedal_events = apply_postprocess(
            note_events,
            pedal_events,
            parameters,
        )

        self._midi_writer(
            start_time=0,
            note_events=[event.to_legacy_dict() for event in note_events],
            pedal_events=[event.to_legacy_dict() for event in pedal_events],
            midi_path=str(midi_path),
        )

        return TranscriptionResult(
            midi_path=midi_path,
            note_events=note_events,
            pedal_events=pedal_events,
            duration=duration,
            engine_name=self.name,
            engine_version=self.version,
        )
```

- [ ] **Step 4: Modify existing inference hooks**

Change `piano_transcription_inference/pytorch_utils.py` so `forward` accepts optional callbacks:

```python
def forward(model, x, batch_size, progress_callback=None, should_cancel=None):
    ...
    while True:
        total_progress = int(pointer / max(len(x), 1) * 100)
        if progress_callback:
            progress_callback(total_progress, '正在处理: {} / {}'.format(pointer, total_segments))
        print('正在处理: {} / {}'.format(pointer, total_segments))
        if pointer >= len(x):
            break
        if should_cancel and should_cancel():
            raise RuntimeError("Transcription cancelled")
```

Change `piano_transcription_inference/inference.py` so `PianoTranscription.__init__` accepts threshold arguments and `transcribe` accepts callbacks, then passes them to `forward`.

- [ ] **Step 5: Verify adapter test passes**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_kong_engine.py -v
```

Expected: PASS.

---

### Task 5: Job Runner

**Files:**
- Create: `app/jobs.py`
- Test: `tests/test_jobs.py`

- [ ] **Step 1: Write failing job tests**

Create `tests/test_jobs.py`:

```python
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
```

- [ ] **Step 2: Verify job tests fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_jobs.py -v
```

Expected: FAIL because `app.jobs` does not exist.

- [ ] **Step 3: Implement job runner**

Create `app/jobs.py`:

```python
from __future__ import annotations

from collections.abc import Iterable

from app.engines.base import EngineCancelled, TranscriptionEngine
from app.models import TaskStatus, TranscriptionTask


class TaskQueueRunner:
    def __init__(self, engine: TranscriptionEngine, device: str = "cuda") -> None:
        self.engine = engine
        self.device = device
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def should_cancel(self) -> bool:
        return self._cancel_requested

    def run_one(self, task: TranscriptionTask) -> TranscriptionTask:
        self._cancel_requested = False
        task.mark(TaskStatus.RUNNING, progress=0)

        def on_progress(percent: int, message: str) -> None:
            task.mark(TaskStatus.RUNNING, progress=percent)

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
            return task
        except Exception as exc:
            task.mark(TaskStatus.FAILED, error=str(exc))
            return task

        task.result = result
        task.mark(TaskStatus.COMPLETED, progress=100)
        return task

    def run_all(self, tasks: Iterable[TranscriptionTask]) -> list[TranscriptionTask]:
        completed: list[TranscriptionTask] = []
        for task in tasks:
            if self._cancel_requested:
                task.mark(TaskStatus.CANCELLED, error="Queue cancelled")
                completed.append(task)
                continue
            completed.append(self.run_one(task))
        return completed
```

- [ ] **Step 4: Verify job tests pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_jobs.py -v
```

Expected: PASS.

---

### Task 6: PySide6 UI

**Files:**
- Create: `app/ui/__init__.py`
- Create: `app/ui/piano_roll.py`
- Create: `app/ui/main_window.py`
- Create: `app/__main__.py`
- Create: `launch_desktop.bat`

- [ ] **Step 1: Implement piano-roll widget**

Create `app/ui/__init__.py` as an empty file.

Create `app/ui/piano_roll.py` with a `QWidget` that accepts a `TranscriptionResult`, draws keyboard rows, draws note rectangles by time/pitch, and exposes `set_playhead_time`, `zoom_in`, and `zoom_out`.

- [ ] **Step 2: Implement main window**

Create `app/ui/main_window.py` with:

- `MainWindow(QMainWindow)`
- left task list
- center `PianoRollWidget`
- right mode/parameter panel
- import files/folder actions
- start queue button
- cancel current task button
- CUDA/CPU selector
- export/open output controls
- `TranscriptionThread(QThread)` that runs `TaskQueueRunner`
- `QMediaPlayer` playback for the selected source audio

- [ ] **Step 3: Implement app entrypoint**

Create `app/__main__.py`:

```python
from app.ui.main_window import run


if __name__ == "__main__":
    run()
```

Create `launch_desktop.bat`:

```bat
@echo off
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" -m app
pause
```

- [ ] **Step 4: Verify UI imports**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -c "from app.ui.main_window import MainWindow; from PySide6.QtWidgets import QApplication; import sys; app = QApplication.instance() or QApplication(sys.argv); w = MainWindow(); print(w.windowTitle())"
```

Expected: prints `PianoConvert`.

---

### Task 7: Documentation And GitHub Preparation

**Files:**
- Create: `README.md`
- Modify: `.gitignore`

- [ ] **Step 1: Write README**

Create `README.md` with:

- What PianoConvert does.
- Offline Windows desktop scope.
- Setup command.
- Desktop launch command.
- CLI fallback command.
- Output location.
- Notes about CUDA/CPU and model file.

- [ ] **Step 2: Ensure ignore rules are safe**

Ensure `.gitignore` contains:

```text
.venv/
.superpowers/
__pycache__/
*.pyc
Output/
```

- [ ] **Step 3: Verify remote repository exists**

Run:

```powershell
git ls-remote https://github.com/NotBadSlime/PianoConvert.git
```

Expected: command reaches the repository. Empty output is acceptable for an empty repository.

---

### Task 8: Full Verification, Commit, And Push

**Files:**
- All implementation files.

- [ ] **Step 1: Run full unit test suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

Expected: all tests pass.

- [ ] **Step 2: Run import/UI smoke test**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -c "from app.ui.main_window import MainWindow; from PySide6.QtWidgets import QApplication; import sys; app = QApplication.instance() or QApplication(sys.argv); w = MainWindow(); print('ui smoke ok')"
```

Expected: prints `ui smoke ok`.

- [ ] **Step 3: Run engine smoke test**

Run:

```powershell
.\.venv\Scripts\python.exe -c "from pathlib import Path; from app.engines.kong_piano import KongPianoEngine; from app.models import TranscriptionParameters; result = KongPianoEngine().transcribe(Path('Input/vydji-0lw74.mp3'), Path('Output'), 'cuda', TranscriptionParameters.for_preset('balanced')); print(result.midi_path, result.note_count)"
```

Expected: command exits 0 and prints a MIDI path and note count.

- [ ] **Step 4: Initialize git if needed and commit**

Run:

```powershell
if (!(Test-Path '.git')) { git init -b main }
git remote remove origin 2>$null
git remote add origin https://github.com/NotBadSlime/PianoConvert.git
git add .
git commit -m "feat: add offline desktop transcription workbench"
```

Expected: commit succeeds.

- [ ] **Step 5: Push to GitHub**

Run:

```powershell
git push -u origin main
```

Expected: push succeeds if GitHub credentials are configured. If authentication fails, keep the local commit and report the exact failure.
