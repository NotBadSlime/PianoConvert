# Inno Setup Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a full offline Windows installer for PianoConvert that includes the PySide6 app, Python runtime files, PyTorch dependencies, and the local piano transcription model checkpoint.

**Architecture:** Use PyInstaller onedir output as the application payload, then compile an Inno Setup installer from that payload. Add small packaging helpers so the installed app resolves bundled resources correctly and writes user output to a writable per-user Documents directory.

**Tech Stack:** Python 3.10, PyInstaller, PySide6, PyTorch, Inno Setup 6, PowerShell build scripts, pytest.

---

## File Structure

- Create `app/paths.py`: runtime path helpers for bundled resources and writable user output.
- Modify `app/ui/main_window.py`: default output directory uses `app.paths.default_output_dir`.
- Modify `app/engines/kong_piano.py`: pass bundled checkpoint path into the low-level transcriptor.
- Modify `requirements.txt`: add PyInstaller.
- Create `PianoConvert.spec`: PyInstaller onedir build config with model checkpoint bundled.
- Create `packaging/PianoConvert.iss`: Inno Setup installer definition.
- Create `scripts/build_exe.ps1`: verify model and run PyInstaller.
- Create `scripts/build_installer.ps1`: run PyInstaller build and then Inno Setup compiler.
- Modify `README.md`: document installer build flow.
- Modify `.gitignore`: ignore `build/`, `dist/`, and `installer/`.
- Create `tests/test_paths.py`: verifies resource path and output path helpers.

---

### Task 1: Runtime Paths

**Files:**
- Create: `app/paths.py`
- Modify: `app/ui/main_window.py`
- Modify: `app/engines/kong_piano.py`
- Test: `tests/test_paths.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_paths.py`:

```python
from pathlib import Path

from app.paths import default_output_dir, resource_path


def test_resource_path_resolves_relative_to_project_root():
    path = resource_path("piano_transcription_inference_data")

    assert path.name == "piano_transcription_inference_data"
    assert path.is_absolute()


def test_default_output_dir_uses_documents_pianoconvert(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    path = default_output_dir()

    assert path == tmp_path / "Documents" / "PianoConvert" / "Output"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_paths.py -v
```

Expected: FAIL because `app.paths` does not exist.

- [ ] **Step 3: Implement path helpers**

Create `app/paths.py`:

```python
from __future__ import annotations

import os
import sys
from pathlib import Path


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(sys.executable).parent
    return Path(__file__).resolve().parents[1]


def resource_path(*parts: str) -> Path:
    return app_base_dir().joinpath(*parts).resolve()


def default_output_dir() -> Path:
    profile = os.environ.get("USERPROFILE")
    if profile:
        base = Path(profile) / "Documents"
    else:
        base = Path.home() / "Documents"
    return base / "PianoConvert" / "Output"
```

- [ ] **Step 4: Use paths in UI and engine**

In `app/ui/main_window.py`, replace `self.output_dir = Path("Output")` with:

```python
from app.paths import default_output_dir
...
self.output_dir = default_output_dir()
```

In `app/engines/kong_piano.py`, pass the bundled checkpoint:

```python
from app.paths import resource_path
...
checkpoint_path=str(resource_path("piano_transcription_inference_data", "note_F1=0.9677_pedal_F1=0.9186.pth")),
```

- [ ] **Step 5: Verify path tests pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_paths.py -v
```

Expected: PASS.

---

### Task 2: Build Dependencies And Spec

**Files:**
- Modify: `requirements.txt`
- Create: `PianoConvert.spec`

- [ ] **Step 1: Add PyInstaller dependency**

Append to `requirements.txt`:

```text
pyinstaller>=6.11
```

- [ ] **Step 2: Install dependency**

Run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Expected: PyInstaller installs successfully.

- [ ] **Step 3: Create PyInstaller spec**

Create `PianoConvert.spec` that builds `PianoConvert.exe` from `app/__main__.py`, excludes tests, bundles `piano_transcription_inference_data`, and uses onedir output.

- [ ] **Step 4: Verify PyInstaller is importable**

Run:

```powershell
.\.venv\Scripts\python.exe -c "import PyInstaller; print('pyinstaller ok')"
```

Expected: prints `pyinstaller ok`.

---

### Task 3: Build Scripts

**Files:**
- Create: `scripts/build_exe.ps1`
- Create: `scripts/build_installer.ps1`

- [ ] **Step 1: Create build_exe.ps1**

Create a PowerShell script that:

- Fails if the model checkpoint is missing.
- Fails if `PianoConvert.spec` is missing.
- Runs `python -m PyInstaller --clean --noconfirm PianoConvert.spec`.
- Fails if `dist/PianoConvert/PianoConvert.exe` is missing.
- Prints the exe path.

- [ ] **Step 2: Create build_installer.ps1**

Create a PowerShell script that:

- Runs `scripts/build_exe.ps1`.
- Locates `ISCC.exe`.
- Fails if Inno Setup is missing.
- Runs `ISCC.exe packaging/PianoConvert.iss`.
- Fails if no `installer/PianoConvertSetup-*.exe` exists.
- Prints the installer path.

---

### Task 4: Inno Setup Script

**Files:**
- Create: `packaging/PianoConvert.iss`

- [ ] **Step 1: Create installer script**

Create `packaging/PianoConvert.iss` with:

- App name `PianoConvert`.
- Version `0.1.0`.
- Default dir `{autopf}\PianoConvert`.
- Source payload `dist\PianoConvert\*`.
- Start Menu shortcut.
- Optional desktop shortcut task.
- Optional launch after install.
- Installer output under `installer`.

- [ ] **Step 2: Verify script file exists**

Run:

```powershell
Test-Path packaging\PianoConvert.iss
```

Expected: `True`.

---

### Task 5: Documentation And Ignore Rules

**Files:**
- Modify: `README.md`
- Modify: `.gitignore`

- [ ] **Step 1: Ignore build artifacts**

Add:

```text
build/
dist/
installer/
*.spec.bak
```

- [ ] **Step 2: Document installer build**

Add README instructions:

```powershell
.\scripts\build_installer.ps1
```

and describe the expected output under `installer/`.

---

### Task 6: Verification, Commit, And Push

**Files:**
- All packaging files.

- [ ] **Step 1: Run test suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

Expected: all tests pass.

- [ ] **Step 2: Build PyInstaller app**

Run:

```powershell
.\scripts\build_exe.ps1
```

Expected: `dist/PianoConvert/PianoConvert.exe` exists.

- [ ] **Step 3: Build Inno installer**

Run:

```powershell
.\scripts\build_installer.ps1
```

Expected: `installer/PianoConvertSetup-0.1.0.exe` exists.

- [ ] **Step 4: Commit changes**

Run:

```powershell
git add .
git commit -m "build: add Inno Setup installer packaging"
```

Expected: commit succeeds.

- [ ] **Step 5: Push changes**

Run:

```powershell
git push
```

Expected: push succeeds.
