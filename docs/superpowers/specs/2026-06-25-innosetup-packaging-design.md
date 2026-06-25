# Inno Setup Packaging Design

Date: 2026-06-25
Status: Approved design draft

## Goal

Create a full offline Windows installer for PianoConvert. The installer should include the desktop application, Python runtime artifacts produced by PyInstaller, required dependencies, and the local piano transcription model checkpoint so the installed app can transcribe without additional downloads.

## Packaging Strategy

Use a two-stage build:

1. PyInstaller builds the PySide6 desktop app into an onedir distribution at `dist/PianoConvert/`.
2. Inno Setup packages `dist/PianoConvert/` into a Windows installer under `installer/`.

The app will use onedir packaging instead of onefile packaging. This keeps startup faster and is less fragile for PyTorch, PySide6, librosa, and the bundled model checkpoint.

## Included Assets

The installer includes:

- `PianoConvert.exe`
- Python libraries collected by PyInstaller
- PySide6 Qt plugins required by the desktop UI
- The existing `piano_transcription_inference` model code
- `piano_transcription_inference_data/note_F1=0.9677_pedal_F1=0.9186.pth`
- README and license-style project metadata if present

The installer does not include:

- `.venv/`
- `.git/`
- `.idea/`
- `Input/`
- `Output/`
- Test files
- Packaging build cache directories

## Installer Behavior

The Inno Setup installer should:

- Install to `{autopf}\PianoConvert` by default.
- Create a Start Menu shortcut.
- Offer an optional desktop shortcut.
- Launch `PianoConvert.exe` after installation when selected.
- Preserve user-created output files during uninstall.

## Files To Add

- `PianoConvert.spec`: PyInstaller build specification.
- `packaging/PianoConvert.iss`: Inno Setup installer script.
- `scripts/build_exe.ps1`: builds `dist/PianoConvert/`.
- `scripts/build_installer.ps1`: builds the PyInstaller app and then compiles the Inno installer.

## Requirements

Add PyInstaller as a development/build dependency in `requirements.txt`.

The build scripts should fail with clear messages when:

- The model checkpoint is missing.
- PyInstaller is unavailable.
- Inno Setup `ISCC.exe` is unavailable.
- The PyInstaller output executable is missing.

## Verification

Verification should include:

- `python -m pytest -v`
- PyInstaller build exits successfully.
- `dist/PianoConvert/PianoConvert.exe` exists.
- Inno Setup build exits successfully.
- `installer/PianoConvertSetup-*.exe` exists.

The installer executable itself can be large because it includes PyTorch, PySide6, and the model checkpoint.
