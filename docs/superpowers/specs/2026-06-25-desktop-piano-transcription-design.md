# Desktop Piano Transcription Product Design

Date: 2026-06-25
Status: Approved design draft

## Goal

Build the current piano transcription project into an offline Windows desktop application. The first version should feel like a usable product, not a script: users can import one or more piano recordings, run transcription locally, preview the generated notes in a piano-roll view, tune parameters, re-run transcription, and export MIDI files.

The first version targets:

- Pure piano recordings, piano covers, and piano-forward audio.
- Recordings with reasonable noise, reverb, or live-room character.
- Fully offline local processing, with no account system and no cloud upload.

The first version does not target full commercial-song separation from vocals, drums, bass, and other instruments.

## Product Shape

The application will be a Windows desktop app built with PySide6 / Qt.

The main window is a batch transcription workbench:

- Left panel: task list for imported audio files.
- Center panel: piano-roll preview of the selected task result.
- Right panel: transcription mode and advanced parameter controls.
- Top toolbar: import audio, import folder, start queue, stop current task, choose CUDA/CPU, and open output directory.
- Bottom transport area: play/pause, return to start, zoom, and export MIDI.

The first version supports preview and parameter tuning, but not manual note editing. Users improve results by switching presets or adjusting post-processing parameters, then re-running transcription.

## Core Workflow

1. User imports one or more audio files.
2. The app creates `TranscriptionTask` records and shows them in the queue.
3. User chooses device, preset, and optional advanced parameters.
4. User starts the queue.
5. Background worker loads audio and runs the selected transcription engine.
6. Engine returns note events, pedal events, MIDI output path, progress logs, and status.
7. The selected task opens in the piano-roll preview.
8. User can play back, inspect, adjust parameters, re-run, and export MIDI.

## Architecture

The application has three main layers:

### Desktop UI

PySide6 / Qt owns all visible interaction:

- Task queue view.
- File import and folder import.
- Piano-roll rendering.
- Parameter controls.
- Playback and export controls.
- Status messages and error dialogs.

The UI must not run transcription on the main thread. Long-running work goes through the job controller.

### Job Controller

The job controller manages background work:

- Queue ordering.
- Task state transitions.
- Progress messages.
- Cancellation.
- Device selection.
- Error capture.
- Communication between UI and engine.

Expected task states:

- `pending`
- `running`
- `completed`
- `failed`
- `cancelled`
- `needs_rerun`

### Transcription Engines

The transcription layer must be pluggable. The first engine wraps the existing ByteDance/Kong piano transcription model already present in this project. Future engines can implement the same interface without changing the UI.

Engine input:

- Audio path.
- Output directory.
- Device: CUDA or CPU.
- Preset: balanced or precise.
- Advanced parameters.

Engine output:

- MIDI path.
- Note events.
- Pedal events.
- Audio duration.
- Engine metadata.
- Log path.

## Data Model

### TranscriptionTask

```text
id
audio_path
output_dir
status
progress
selected_engine
selected_preset
parameters
result
error_message
created_at
updated_at
```

### TranscriptionResult

```text
midi_path
note_events
pedal_events
duration
engine_name
engine_version
created_at
log_path
```

### NoteEvent

```text
onset_time
offset_time
midi_note
velocity
```

### PedalEvent

```text
onset_time
offset_time
```

The piano-roll preview reads `note_events` directly instead of parsing MIDI. MIDI export uses the same event structure, so preview and export stay consistent.

## Presets And Parameters

The first version supports two main presets:

- Balanced: default mode, intended for everyday piano audio.
- Precise: accuracy-first mode, allowed to run slower and use more conservative cleanup.

Advanced parameters:

- Onset threshold.
- Frame threshold.
- Minimum note duration.
- Quantization grid, such as off, 1/8, 1/16, or triplet-friendly settings.
- Short-note filtering.
- Pedal output on/off.

Parameter changes do not mutate the audio file. They mark the task as needing a re-run and then generate a new result when the user clicks re-run.

## Accuracy Strategy

The first version should keep the current piano-specialized engine as the default production engine because it already runs locally, supports CUDA, supports pedal output, and matches the target input range.

Accuracy improvements should be productized in three layers:

1. Presets around the current model.
   Balanced and precise modes should tune segmentation, overlap, thresholds, and post-processing.

2. Post-processing.
   Add short-note filtering, quantization, broken-note merging, optional pedal cleanup, and parameterized thresholds.

3. Experimental engine slot.
   Keep room for Transkun or another local model as a later engine. Do not couple the UI to the current model.

Research notes:

- The current project is based on a high-resolution piano transcription model with note and pedal outputs. It is a good baseline for offline piano-only use.
- Transkun is a promising future offline transcription engine to evaluate for accuracy.
- Basic Pitch is easy to use and general-purpose, but it is not the first choice for piano-specialized accuracy.
- MT3 is valuable research for multi-instrument transcription, but it is heavier and less suitable for the first offline desktop version.
- Commercial tools such as AnthemScore, Klangio, and Melody Scanner are useful references for workflow and export experience, not dependencies.

## Error Handling

The app should handle these errors explicitly:

- CUDA unavailable: show a clear message and offer CPU mode.
- CUDA out of memory: suggest CPU mode or a lighter preset.
- Model file missing: show model path and repair guidance.
- Audio unreadable: show the file path and decoder error.
- Output directory not writable: ask the user to choose another directory.
- Task cancelled: preserve the task and mark it as cancelled.
- Engine failure: store a log file and show a concise error summary.

## Testing

The first version should include:

- Engine smoke test: a short audio clip produces note events and a MIDI file.
- Post-processing tests: quantization, short-note filtering, and pedal toggle behave predictably.
- Data model tests: task status transitions are valid.
- UI smoke test: import audio, start transcription, complete a task, preview notes, and export MIDI.

Manual verification should include:

- CUDA path.
- CPU fallback path.
- Invalid audio file.
- Missing model file.
- Batch import with multiple files.

## First Version Scope

Included:

- Windows desktop application.
- Batch audio import.
- CUDA/CPU selection.
- Balanced and precise modes.
- Advanced parameter re-run.
- Piano-roll preview.
- Playback controls.
- MIDI export.
- Open output directory.

Excluded:

- Staff notation PDF.
- MusicXML.
- Manual note editing.
- Full-song source separation.
- Cloud upload.
- Accounts.
- Sharing links.

## Implementation Notes

Refactor the current `start.py` script behavior into reusable services before building the UI. The UI should call services, not shell out to `start.py`.

Suggested module boundaries:

- `app/models.py`: task and result dataclasses.
- `app/engines/base.py`: transcription engine protocol.
- `app/engines/kong_piano.py`: adapter for the existing model.
- `app/jobs.py`: queue and worker orchestration.
- `app/postprocess.py`: quantization and cleanup.
- `app/ui/main_window.py`: main Qt window.
- `app/ui/piano_roll.py`: piano-roll widget.

Keep the existing `piano_transcription_inference` package as the low-level model package unless a later implementation step finds a reason to move it.
