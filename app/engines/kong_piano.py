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

        try:
            raw_result = transcriptor.transcribe(
                audio,
                midi_path=None,
                progress_callback=progress_callback,
                should_cancel=should_cancel,
            )
        except RuntimeError as exc:
            if "cancelled" in str(exc).lower():
                raise EngineCancelled(str(exc)) from exc
            raise

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
