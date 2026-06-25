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
