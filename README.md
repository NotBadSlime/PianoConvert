# PianoConvert

PianoConvert is an offline Windows desktop tool for converting piano-forward audio into MIDI. It wraps a local piano transcription model in a PySide6 workbench with batch import, CUDA/CPU selection, transcription presets, parameter tuning, piano-roll preview, playback controls, and MIDI export.

## Scope

This first version is designed for:

- Pure piano recordings.
- Piano covers and piano-forward MP3/WAV files.
- Piano recordings with reasonable room noise, reverb, or live-recording character.
- Fully offline local processing.

It does not currently include vocal/accompaniment source separation, staff notation PDF export, MusicXML export, or manual MIDI note editing.

## Setup

Use the project virtual environment on Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The model checkpoint must exist at:

```text
piano_transcription_inference_data/note_F1=0.9677_pedal_F1=0.9186.pth
```

## Run The Desktop App

```powershell
.\.venv\Scripts\python.exe -m app
```

You can also double-click:

```text
launch_desktop.bat
```

## CLI Fallback

The original batch script is still available:

```powershell
.\.venv\Scripts\python.exe start.py
```

It reads audio files from `Input/` and writes MIDI files to `Output/`.

## Desktop Workflow

1. Import one or more audio files.
2. Choose `cuda` or `cpu`.
3. Choose `balanced` or `precise`.
4. Adjust thresholds, minimum note duration, quantization, and pedal output if needed.
5. Start the queue.
6. Inspect the result in the piano-roll preview.
7. Re-run selected tasks after parameter changes.
8. Export MIDI.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

For headless UI smoke tests:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest tests/test_ui_smoke.py -v
```

## Build Windows Installer

The installer build bundles the desktop app and the local model checkpoint into a full offline installer. Make sure Inno Setup 6 is installed and the model checkpoint exists at `piano_transcription_inference_data/`.

Build the PyInstaller app directory:

```powershell
.\scripts\build_exe.ps1
```

Build the full Inno Setup installer:

```powershell
.\scripts\build_installer.ps1
```

The installer is written to:

```text
installer/PianoConvertSetup-0.1.0.exe
```

## Notes

- CUDA is used when available and selected.
- CPU mode is slower but useful as a fallback.
- Generated MIDI files are ignored by git through `Output/`.
- Installer build artifacts are ignored by git through `build/`, `dist/`, and `installer/`.
- The UI reads note events directly for piano-roll preview and writes MIDI from the same event data, keeping preview and export aligned.
