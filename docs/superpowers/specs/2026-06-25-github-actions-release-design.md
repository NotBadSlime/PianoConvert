# GitHub Actions Release Installer Design

Date: 2026-06-25
Status: Approved design draft

## Goal

Use GitHub Actions to build the Windows installer for PianoConvert and publish the generated `.exe` installer as a GitHub Release asset.

## Release Strategy

Add a single Windows workflow at `.github/workflows/release.yml`.

The workflow runs in two cases:

- A tag matching `v*` is pushed, for example `v0.1.0`.
- A maintainer manually starts the workflow with `workflow_dispatch` and enters a version such as `v0.1.0`.

Both paths produce the same output: `PianoConvertSetup-<version>.exe` attached to the matching GitHub Release.

## CI Build Flow

The workflow should:

1. Check out the repository.
2. Set up Python 3.10 on `windows-latest`.
3. Install Python dependencies from `requirements.txt`.
4. Download the model checkpoint into `piano_transcription_inference_data/`.
5. Install Inno Setup 6 on the runner.
6. Run the Python test suite.
7. Build the PyInstaller app directory.
8. Compile the Inno Setup installer.
9. Upload the installer as a workflow artifact.
10. Create or update the GitHub Release and attach the installer.

## Model Checkpoint

The model checkpoint should stay out of git. The workflow downloads it during the build from the existing public model URL and writes it to:

```text
piano_transcription_inference_data/note_F1=0.9677_pedal_F1=0.9186.pth
```

The download script should:

- Create the checkpoint directory if needed.
- Skip the download when a valid local file already exists.
- Retry transient network failures.
- Fail with a clear message if the downloaded file is missing or too small.

## Build Script Changes

The existing PowerShell scripts should remain usable locally and become CI-friendly:

- `scripts/build_exe.ps1` should accept either a path such as `.\.venv\Scripts\python.exe` or a command name such as `python`.
- `scripts/build_installer.ps1` should accept a version parameter and pass it into Inno Setup.
- `scripts/build_installer.ps1` should locate `ISCC.exe` through `Get-Command` and common install paths, without relying on a user-specific path.
- `packaging/PianoConvert.iss` should allow `MyAppVersion` to be supplied from the command line while defaulting to `0.1.0` for local builds.

## Release Behavior

For a version `v0.1.0`, the workflow should publish:

```text
installer/PianoConvertSetup-0.1.0.exe
```

to the GitHub Release named `PianoConvert v0.1.0`.

If the Release already exists, the workflow should replace the installer asset so a failed or partial release can be rerun.

## Documentation

Update `README.md` with:

- Local installer build command.
- CI release instructions.
- The tag command for publishing, for example `git tag v0.1.0 && git push origin v0.1.0`.
- A note that the installer is large because it bundles PyTorch, PySide6, and the model checkpoint.

## Verification

Verification should include:

- A focused test for the release-version helper.
- A local run proving the model download script skips an existing checkpoint.
- `python -m pytest -v`.
- A local installer build using `scripts/build_installer.ps1 -Version 0.1.0`.
- Git status showing only intended source changes before commit.

## Constraints

- Do not commit `build/`, `dist/`, `installer/`, `.venv/`, or the model checkpoint.
- Do not require secrets beyond the default GitHub token.
- Keep the workflow Windows-only because the output is a Windows Inno Setup installer.
- Keep the desktop app behavior unchanged.
