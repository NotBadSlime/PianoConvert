# GitHub Actions Release Installer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a GitHub Actions workflow that builds PianoConvert's Windows installer and uploads it to GitHub Releases.

**Architecture:** Keep heavy packaging in the existing PowerShell scripts, add a small tested Python helper for release version normalization, and add a Windows-only workflow that downloads the model, runs tests, builds the installer, and publishes the release asset.

**Tech Stack:** GitHub Actions, Windows runner, Python 3.10, pytest, PowerShell, PyInstaller, Inno Setup 6, GitHub CLI.

---

## File Structure

- Create `scripts/resolve_release_version.py`: normalizes workflow version input into a tag and plain installer version, and can write GitHub Actions outputs.
- Create `tests/test_release_version.py`: unit tests for tag/manual version normalization and invalid values.
- Create `scripts/download_model.ps1`: downloads or skips the transcription checkpoint for CI and local builds.
- Modify `scripts/build_exe.ps1`: accept a Python command name as well as a filesystem path.
- Modify `scripts/build_installer.ps1`: accept `-Version`, locate `ISCC.exe` in CI-friendly ways, and pass the version into Inno Setup.
- Modify `packaging/PianoConvert.iss`: allow `MyAppVersion` to be overridden by `/DMyAppVersion=...`.
- Create `.github/workflows/release.yml`: release workflow for tag push and manual dispatch.
- Modify `README.md`: document local and CI release flows.

---

### Task 1: Release Version Helper

**Files:**
- Create: `scripts/resolve_release_version.py`
- Create: `tests/test_release_version.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_release_version.py`:

```python
import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "resolve_release_version.py"
SPEC = importlib.util.spec_from_file_location("resolve_release_version", MODULE_PATH)
release_version = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(release_version)


def test_uses_push_tag_as_release_version():
    version = release_version.resolve_release_version(
        event_name="push",
        ref_name="v0.1.0",
        input_version="",
    )

    assert version.tag == "v0.1.0"
    assert version.plain == "0.1.0"


def test_adds_v_prefix_for_manual_dispatch_input():
    version = release_version.resolve_release_version(
        event_name="workflow_dispatch",
        ref_name="main",
        input_version="0.2.0",
    )

    assert version.tag == "v0.2.0"
    assert version.plain == "0.2.0"


def test_rejects_empty_manual_dispatch_version():
    with pytest.raises(ValueError, match="Release version is required"):
        release_version.resolve_release_version(
            event_name="workflow_dispatch",
            ref_name="main",
            input_version="",
        )


def test_rejects_unsafe_version_names():
    with pytest.raises(ValueError, match="Invalid release version"):
        release_version.resolve_release_version(
            event_name="push",
            ref_name="feature/test",
            input_version="",
        )
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_release_version.py -v
```

Expected: FAIL because `scripts/resolve_release_version.py` does not exist.

- [ ] **Step 3: Implement the helper**

Create `scripts/resolve_release_version.py` with:

```python
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


VERSION_RE = re.compile(r"^v?\d+(?:\.\d+){1,3}(?:[-.][A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?$")


@dataclass(frozen=True)
class ReleaseVersion:
    tag: str
    plain: str


def resolve_release_version(event_name: str, ref_name: str, input_version: str | None) -> ReleaseVersion:
    raw = input_version if event_name == "workflow_dispatch" else ref_name
    value = (raw or "").strip()
    if not value:
        raise ValueError("Release version is required.")
    if not VERSION_RE.match(value):
        raise ValueError(f"Invalid release version: {value}")
    tag = value if value.startswith("v") else f"v{value}"
    return ReleaseVersion(tag=tag, plain=tag[1:])


def write_github_output(path: Path, version: ReleaseVersion) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"tag={version.tag}\n")
        handle.write(f"plain={version.plain}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--ref-name", required=True)
    parser.add_argument("--input-version", default="")
    parser.add_argument("--github-output", default="")
    args = parser.parse_args()

    version = resolve_release_version(args.event_name, args.ref_name, args.input_version)
    if args.github_output:
        write_github_output(Path(args.github_output), version)
    else:
        print(f"tag={version.tag}")
        print(f"plain={version.plain}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_release_version.py -v
```

Expected: PASS.

---

### Task 2: CI-Friendly Build Scripts

**Files:**
- Create: `scripts/download_model.ps1`
- Modify: `scripts/build_exe.ps1`
- Modify: `scripts/build_installer.ps1`
- Modify: `packaging/PianoConvert.iss`

- [ ] **Step 1: Add the model download script**

Create `scripts/download_model.ps1` with parameters for `-Url`, `-Output`, and `-MinimumBytes`. It should resolve paths from the repo root, skip an existing valid checkpoint, retry three times, and fail when the output is below the expected size.

- [ ] **Step 2: Make `build_exe.ps1` accept command names**

Resolve `$Python` by first checking `Test-Path`, then `Get-Command`. Use the resolved command for PyInstaller checks and the PyInstaller build.

- [ ] **Step 3: Make `build_installer.ps1` version-aware**

Add `[string]$Version = $env:PIANOCONVERT_VERSION`, default it to `0.1.0`, run `build_exe.ps1`, find `ISCC.exe`, and invoke:

```powershell
& $ISCC "/DMyAppVersion=$Version" $ScriptPath
```

Then require:

```powershell
installer/PianoConvertSetup-$Version.exe
```

- [ ] **Step 4: Allow Inno version overrides**

Replace the fixed `MyAppVersion` define in `packaging/PianoConvert.iss` with:

```pascal
#ifndef MyAppVersion
#define MyAppVersion "0.1.0"
#endif
```

---

### Task 3: GitHub Actions Workflow

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Add workflow**

Create `.github/workflows/release.yml` with:

```yaml
name: Release Installer

on:
  workflow_dispatch:
    inputs:
      version:
        description: Release version, for example v0.1.0
        required: true
        default: v0.1.0
  push:
    tags:
      - "v*"

permissions:
  contents: write

jobs:
  build-release:
    runs-on: windows-latest
    env:
      MODEL_URL: https://zenodo.org/record/4034264/files/CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth?download=1

    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.10"
          cache: pip
      - name: Install Python dependencies
        run: python -m pip install -r requirements.txt
      - name: Resolve release version
        id: version
        shell: pwsh
        run: >
          python scripts/resolve_release_version.py
          --event-name "${{ github.event_name }}"
          --ref-name "${{ github.ref_name }}"
          --input-version "${{ github.event.inputs.version }}"
          --github-output "$env:GITHUB_OUTPUT"
      - name: Download model checkpoint
        shell: pwsh
        run: .\scripts\download_model.ps1 -Url "$env:MODEL_URL"
      - name: Install Inno Setup
        shell: pwsh
        run: choco install innosetup --no-progress -y
      - name: Run tests
        run: python -m pytest -v
      - name: Build installer
        shell: pwsh
        run: .\scripts\build_installer.ps1 -Python python -Version "${{ steps.version.outputs.plain }}"
      - name: Upload workflow artifact
        uses: actions/upload-artifact@v4
        with:
          name: PianoConvertSetup-${{ steps.version.outputs.plain }}
          path: installer/PianoConvertSetup-${{ steps.version.outputs.plain }}.exe
      - name: Publish GitHub Release
        shell: pwsh
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          $tag = "${{ steps.version.outputs.tag }}"
          $plain = "${{ steps.version.outputs.plain }}"
          $asset = "installer/PianoConvertSetup-$plain.exe"
          gh release view $tag *> $null
          if ($LASTEXITCODE -eq 0) {
            gh release upload $tag $asset --clobber
          } else {
            gh release create $tag $asset --target "${{ github.sha }}" --title "PianoConvert $tag" --notes "Offline Windows installer for PianoConvert."
          }
```

---

### Task 4: README Release Instructions

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document CI release**

Add a section explaining:

- Local installer builds still use `.\scripts\build_installer.ps1`.
- GitHub Releases are produced by pushing a tag such as `v0.1.0`.
- Manual release builds can be started from the GitHub Actions tab with the same version format.
- The installer is large because it bundles Python runtime files, PyTorch, PySide6, and the checkpoint.

---

### Task 5: Verification, Commit, And Push

**Files:**
- All files above.

- [ ] **Step 1: Verify focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_release_version.py -v
```

Expected: PASS.

- [ ] **Step 2: Verify full tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

Expected: all tests pass.

- [ ] **Step 3: Verify download script skip path**

Run:

```powershell
.\scripts\download_model.ps1
```

Expected: it skips the existing checkpoint or downloads a valid checkpoint.

- [ ] **Step 4: Verify installer build with explicit version**

Run:

```powershell
.\scripts\build_installer.ps1 -Version 0.1.0
```

Expected: `installer/PianoConvertSetup-0.1.0.exe` exists.

- [ ] **Step 5: Review git diff**

Run:

```powershell
git status --short
git diff --check
```

Expected: only intended source changes, no whitespace errors.

- [ ] **Step 6: Commit**

Run:

```powershell
git add .github README.md docs/superpowers/plans scripts tests packaging/PianoConvert.iss
git commit -m "ci: publish installer releases from GitHub Actions"
```

Expected: commit succeeds.

- [ ] **Step 7: Push**

Run:

```powershell
git push
```

Expected: push succeeds.
