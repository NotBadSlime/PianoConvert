# PianoConvert 简易转换工具 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `E:\PianoConvertApp` 做一个中文 Windows 小工具：选音频 → 钢琴/其他乐器 → 进度 → 历史列表，输出 `.mid` + `.musicxml`，并提供 Inno Setup 安装包与 GitHub Release 工作流。

**Architecture:** 界面只调用 `convert.run()` 与 `history`；`convert` 按 `kind` 选 `kong_piano` 或 `basic_pitch` 写出 MIDI，再用 `music21` 转 MusicXML。历史是 `%LOCALAPPDATA%\PianoConvert\history.json`。引擎可注入，单测不跑真实模型。

**Tech Stack:** Python 3.10、PySide6、piano_transcription_inference、basic-pitch（ONNX）、music21、pretty_midi、pytest、PyInstaller、Inno Setup 6、GitHub Actions。

**Spec:** `E:\PianoConvert\docs\superpowers\specs\2026-09-20-simple-audio-to-score-design.md`（Task 1 复制进新仓库）。

**工作目录：** 所有相对路径相对 `E:\PianoConvertApp`。系统 Python：`C:\Users\12849\AppData\Local\Programs\Python\Python310\python.exe`。本机 `gh` 未登录；Task 11 只写工作流，不强制发布。

---

## File map

| 文件 | 职责 |
|---|---|
| `app/paths.py` | 输出目录、历史文件、模型权重路径 |
| `app/device.py` | CUDA 则 `cuda` 否则 `cpu` |
| `app/history.py` | 读写 `history.json` |
| `app/score_io.py` | 最小 MIDI 工具 + MIDI→MusicXML |
| `app/engines/base.py` | `TranscriptionEngine` 协议与 `CancelledError` |
| `app/engines/kong_piano.py` | 钢琴转录 |
| `app/engines/basic_pitch.py` | 其他乐器转录 |
| `app/convert.py` | 校验、建目录、调引擎、转谱、取消、人话错误 |
| `app/ui/styles.py` | 浅色 QSS |
| `app/ui/main_window.py` | 主窗口 |
| `app/__main__.py` | 入口 |
| `tests/` | 单测 |
| `scripts/build_exe.ps1` | PyInstaller |
| `scripts/build_installer.ps1` | Inno Setup |
| `installer/PianoConvert.iss` | 安装脚本 |
| `.github/workflows/release.yml` | tag 发 Release |

---

### Task 1: 仓库骨架

**Files:**
- Create: `E:\PianoConvertApp\` 下的 `README.md`、`requirements.txt`、`.gitignore`、`app/__init__.py`、`app/engines/__init__.py`、`app/ui/__init__.py`、`tests/__init__.py`、`models/.gitkeep`
- Copy: spec 与本 plan 到新仓库 `docs/superpowers/`

- [ ] **Step 1: 建目录并 git init**

```powershell
$root = "E:\PianoConvertApp"
New-Item -ItemType Directory -Force -Path $root, "$root\app\engines", "$root\app\ui", "$root\tests", "$root\models", "$root\scripts", "$root\installer", "$root\docs\superpowers\specs", "$root\docs\superpowers\plans" | Out-Null
Set-Location $root
git init
git checkout -b main
Copy-Item "E:\PianoConvert\docs\superpowers\specs\2026-09-20-simple-audio-to-score-design.md" "$root\docs\superpowers\specs\"
Copy-Item "E:\PianoConvert\docs\superpowers\plans\2026-09-20-simple-audio-to-score.md" "$root\docs\superpowers\plans\"
Copy-Item "E:\PianoConvert\_internal\piano_transcription_inference_data\note_F1=0.9677_pedal_F1=0.9186.pth" "$root\models\"
```

Expected: `E:\PianoConvertApp` 存在，models 下有约 164MB 的 `.pth`。

- [ ] **Step 2: 写 `.gitignore`、`requirements.txt`、包 init、README**

`.gitignore`:

```gitignore
.venv/
__pycache__/
*.pyc
.pytest_cache/
build/
dist/
installer/output/
Output/
*.spec
```

`requirements.txt`:

```text
PySide6>=6.6
numpy>=1.26
librosa>=0.10
soundfile>=0.12
pretty_midi>=0.2.10
mido>=1.3
music21>=9.1
basic-pitch>=0.3
onnxruntime>=1.17
piano_transcription_inference>=0.0.5
torch
torchaudio
pytest>=8.0
pyinstaller>=6.0
```

`app/__init__.py`、`app/engines/__init__.py`、`app/ui/__init__.py`、`tests/__init__.py` 均为空文件。`models/.gitkeep` 空文件。`.pth` 用 Git LFS 或本地不强制推送；README 写明开发时从安装目录复制。

`README.md`:

```markdown
# PianoConvert

离线音频转 MIDI + MusicXML。钢琴用 Kong 模型，其他乐器用 basic-pitch。

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app
.\.venv\Scripts\python.exe -m pytest -v
```
```

- [ ] **Step 3: 建 venv 并安装轻依赖（先保证 pytest 能跑；torch 若已有系统包可稍后装）**

```powershell
cd E:\PianoConvertApp
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install pytest PySide6 pretty_midi mido music21
```

若后续引擎测试需要，再 `pip install -r requirements.txt`。

- [ ] **Step 4: Commit**

```powershell
cd E:\PianoConvertApp
git add .gitignore requirements.txt README.md app tests models/.gitkeep docs
git add -f models/.gitkeep
git commit -m "chore: bootstrap PianoConvertApp repository"
```

不要 `git add` 大 `.pth`（可留在工作区不提交）。

---

### Task 2: `paths` 与 `device`

**Files:**
- Create: `app/paths.py`
- Create: `app/device.py`
- Test: `tests/test_paths.py`

- [ ] **Step 1: 写失败测试**

`tests/test_paths.py`:

```python
from pathlib import Path

from app.device import resolve_device
from app.paths import history_path, make_output_dir, model_checkpoint, output_root


def test_output_root_ends_with_pianoconvert_output(tmp_path, monkeypatch):
    monkeypatch.setattr("app.paths.Path.home", classmethod(lambda cls: tmp_path))
    root = output_root()
    assert root == tmp_path / "Documents" / "PianoConvert" / "Output"


def test_history_path_under_localappdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert history_path() == tmp_path / "PianoConvert" / "history.json"


def test_make_output_dir_uses_stem_and_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr("app.paths.output_root", lambda: tmp_path)
    monkeypatch.setattr("app.paths._now_stamp", lambda: "20260102-030405")
    folder = make_output_dir(Path("song name.mp3"))
    assert folder.name == "song name_20260102-030405"
    assert folder.is_dir()


def test_model_checkpoint_prefers_repo_models(tmp_path, monkeypatch):
    models = tmp_path / "models"
    models.mkdir()
    pth = models / "note_F1=0.9677_pedal_F1=0.9186.pth"
    pth.write_bytes(b"x")
    monkeypatch.setattr("app.paths.repo_root", lambda: tmp_path)
    assert model_checkpoint() == pth


def test_resolve_device_cpu_when_cuda_false(monkeypatch):
    monkeypatch.setattr("app.device.torch.cuda.is_available", lambda: False)
    assert resolve_device() == "cpu"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `E:\PianoConvertApp\.venv\Scripts\python.exe -m pytest tests/test_paths.py -v`

Expected: FAIL，`ModuleNotFoundError: app.paths`

- [ ] **Step 3: 最小实现**

`app/paths.py`:

```python
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

CHECKPOINT_NAME = "note_F1=0.9677_pedal_F1=0.9186.pth"


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def output_root() -> Path:
    return Path.home() / "Documents" / "PianoConvert" / "Output"


def history_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    return base / "PianoConvert" / "history.json"


def make_output_dir(source: Path) -> Path:
    folder = output_root() / f"{source.stem}_{_now_stamp()}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def model_checkpoint() -> Path:
    bundled = repo_root() / "models" / CHECKPOINT_NAME
    if bundled.exists():
        return bundled
    meipass = getattr(__import__("sys"), "_MEIPASS", None)
    if meipass:
        frozen = Path(meipass) / "piano_transcription_inference_data" / CHECKPOINT_NAME
        if frozen.exists():
            return frozen
    raise FileNotFoundError(f"找不到钢琴模型: {CHECKPOINT_NAME}")
```

`app/device.py`:

```python
from __future__ import annotations

import torch


def resolve_device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"
```

`tests/test_paths.py` 里 `test_resolve_device_cpu_when_cuda_false` 依赖 torch。若 venv 还没装 torch，改成：

```python
def test_resolve_device_cpu_when_cuda_false(monkeypatch):
    import app.device as device_mod

    class _Cuda:
        @staticmethod
        def is_available():
            return False

    class _Torch:
        cuda = _Cuda()

    monkeypatch.setattr(device_mod, "torch", _Torch())
    assert device_mod.resolve_device() == "cpu"
```

同时把 `app/device.py` 改成延迟 import，便于无 torch 时测 paths：

```python
from __future__ import annotations


def resolve_device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"
```

无 torch 时跳过 device 测试：`pytest.importorskip` 不用于 resolve_device 单测，因为上面用假 torch。`app/device.py` 不要在模块顶层 import torch。

- [ ] **Step 4: 再跑测试**

Run: `E:\PianoConvertApp\.venv\Scripts\python.exe -m pytest tests/test_paths.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add app/paths.py app/device.py tests/test_paths.py
git commit -m "feat: add output, history, and model paths"
```

---

### Task 3: 历史记录

**Files:**
- Create: `app/history.py`
- Test: `tests/test_history.py`

- [ ] **Step 1: 写失败测试**

`tests/test_history.py`:

```python
from app.history import HistoryItem, append_item, load_items


def test_append_and_reload(tmp_path, monkeypatch):
    monkeypatch.setattr("app.history.history_path", lambda: tmp_path / "history.json")
    item = HistoryItem(
        id="abc",
        title="demo",
        source_path="C:/a.mp3",
        kind="piano",
        created_at="2026-01-02T03:04:05",
        status="success",
        midi_path="C:/out/a.mid",
        musicxml_path="C:/out/a.musicxml",
        folder="C:/out",
        error="",
    )
    append_item(item)
    append_item(item)
    loaded = load_items()
    assert len(loaded) == 2
    assert loaded[0].title == "demo"
    assert loaded[0].kind == "piano"


def test_load_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("app.history.history_path", lambda: tmp_path / "nope.json")
    assert load_items() == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `E:\PianoConvertApp\.venv\Scripts\python.exe -m pytest tests/test_history.py -v`

Expected: FAIL，`ModuleNotFoundError: app.history`

- [ ] **Step 3: 最小实现**

`app/history.py`:

```python
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from app.paths import history_path


@dataclass
class HistoryItem:
    id: str
    title: str
    source_path: str
    kind: str
    created_at: str
    status: str
    midi_path: str
    musicxml_path: str
    folder: str
    error: str


def load_items() -> list[HistoryItem]:
    path = history_path()
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [HistoryItem(**row) for row in raw]


def append_item(item: HistoryItem) -> None:
    path = history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    items = load_items()
    items.append(item)
    path.write_text(json.dumps([asdict(x) for x in items], ensure_ascii=False, indent=2), encoding="utf-8")
```

- [ ] **Step 4: 再跑测试**

Run: `E:\PianoConvertApp\.venv\Scripts\python.exe -m pytest tests/test_history.py tests/test_paths.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add app/history.py tests/test_history.py
git commit -m "feat: persist conversion history as JSON"
```

---

### Task 4: MIDI → MusicXML

**Files:**
- Create: `app/score_io.py`
- Test: `tests/test_score_io.py`

- [ ] **Step 1: 写失败测试**

`tests/test_score_io.py`:

```python
from pathlib import Path

import pretty_midi

from app.score_io import midi_to_musicxml, write_notes_midi


def _one_note_midi(path: Path) -> None:
    pm = pretty_midi.PrettyMIDI(initial_tempo=120)
    inst = pretty_midi.Instrument(program=0)
    inst.notes.append(pretty_midi.Note(velocity=80, pitch=60, start=0.0, end=0.5))
    pm.instruments.append(inst)
    pm.write(str(path))


def test_midi_to_musicxml_contains_score_partwise(tmp_path):
    midi = tmp_path / "n.mid"
    xml = tmp_path / "n.musicxml"
    _one_note_midi(midi)
    midi_to_musicxml(midi, xml)
    text = xml.read_text(encoding="utf-8")
    assert "<score-partwise" in text
    assert xml.exists()


def test_write_notes_midi_roundtrip(tmp_path):
    dest = tmp_path / "out.mid"
    write_notes_midi(dest, [(60, 0.0, 0.5, 90)], program=0, is_drum=False)
    pm = pretty_midi.PrettyMIDI(str(dest))
    assert len(pm.instruments[0].notes) == 1
    assert pm.instruments[0].notes[0].pitch == 60
```

- [ ] **Step 2: 跑测试确认失败**

Run: `E:\PianoConvertApp\.venv\Scripts\python.exe -m pytest tests/test_score_io.py -v`

Expected: FAIL，`ModuleNotFoundError: app.score_io`

- [ ] **Step 3: 最小实现**

`app/score_io.py`:

```python
from __future__ import annotations

from pathlib import Path

import pretty_midi
from music21 import converter


def write_notes_midi(
    dest: Path,
    notes: list[tuple[int, float, float, int]],
    program: int = 0,
    is_drum: bool = False,
) -> None:
    pm = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(program=program, is_drum=is_drum)
    for pitch, start, end, velocity in notes:
        inst.notes.append(
            pretty_midi.Note(velocity=int(velocity), pitch=int(pitch), start=float(start), end=float(end))
        )
    pm.instruments.append(inst)
    dest.parent.mkdir(parents=True, exist_ok=True)
    pm.write(str(dest))


def midi_to_musicxml(midi_path: Path, dest: Path) -> None:
    score = converter.parse(str(midi_path))
    dest.parent.mkdir(parents=True, exist_ok=True)
    score.write("musicxml", fp=str(dest))
```

- [ ] **Step 4: 再跑测试**

Run: `E:\PianoConvertApp\.venv\Scripts\python.exe -m pytest tests/test_score_io.py -v`

Expected: PASS（music21 可能写出 `.musicxml`；若实际写成 `.xml`，把 `midi_to_musicxml` 改成写完后把文件 rename 到 `dest`）。

- [ ] **Step 5: Commit**

```powershell
git add app/score_io.py tests/test_score_io.py
git commit -m "feat: convert MIDI notes to MusicXML"
```

---

### Task 5: 引擎协议 + `convert`（注入假引擎）

**Files:**
- Create: `app/engines/base.py`
- Create: `app/convert.py`
- Test: `tests/test_convert.py`

- [ ] **Step 1: 写失败测试**

`tests/test_convert.py`:

```python
from pathlib import Path
from threading import Event

import pretty_midi
import pytest

from app.convert import AUDIO_SUFFIXES, ConvertError, run
from app.engines.base import CancelledError, TranscriptionEngine


class FakeEngine:
    def __init__(self, fail: Exception | None = None, hang_until_cancel: bool = False):
        self.fail = fail
        self.hang_until_cancel = hang_until_cancel
        self.calls = 0

    def transcribe(self, audio_path: Path, dest_midi: Path, cancel: Event, on_progress) -> None:
        self.calls += 1
        if self.hang_until_cancel:
            if cancel.is_set():
                raise CancelledError()
            raise CancelledError()
        if self.fail:
            raise self.fail
        on_progress("转录", 0.5)
        pm = pretty_midi.PrettyMIDI()
        inst = pretty_midi.Instrument(program=0)
        inst.notes.append(pretty_midi.Note(velocity=80, pitch=64, start=0.0, end=0.4))
        pm.instruments.append(inst)
        dest_midi.write_bytes(b"")  # placeholder overwritten
        pm.write(str(dest_midi))


def test_rejects_bad_suffix(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("x")
    with pytest.raises(ConvertError, match="不支持"):
        run(src, "piano", engines={"piano": FakeEngine()}, cancel=Event(), output_root=tmp_path)


def test_success_writes_midi_and_musicxml(tmp_path):
    src = tmp_path / "tune.mp3"
    src.write_bytes(b"xx")
    result = run(src, "piano", engines={"piano": FakeEngine()}, cancel=Event(), output_root=tmp_path)
    assert result.status == "success"
    assert result.midi_path.exists()
    assert result.musicxml_path.exists()
    assert "<score-partwise" in result.musicxml_path.read_text(encoding="utf-8")
    assert result.folder.name.startswith("tune_")


def test_partial_when_musicxml_fails(tmp_path, monkeypatch):
    src = tmp_path / "tune.wav"
    src.write_bytes(b"xx")

    def boom(midi, dest):
        raise RuntimeError("xml down")

    monkeypatch.setattr("app.convert.midi_to_musicxml", boom)
    result = run(src, "piano", engines={"piano": FakeEngine()}, cancel=Event(), output_root=tmp_path)
    assert result.status == "partial"
    assert result.midi_path.exists()
    assert result.error


def test_failed_transcribe_humanizes_cuda(tmp_path):
    src = tmp_path / "tune.mp3"
    src.write_bytes(b"xx")
    result = run(
        src,
        "piano",
        engines={"piano": FakeEngine(fail=RuntimeError("CUDA out of memory"))},
        cancel=Event(),
        output_root=tmp_path,
    )
    assert result.status == "failed"
    assert "显存不足" in result.error


def test_cancel_does_not_keep_folder(tmp_path):
    src = tmp_path / "tune.flac"
    src.write_bytes(b"xx")
    cancel = Event()
    cancel.set()
    result = run(src, "other", engines={"other": FakeEngine(hang_until_cancel=True)}, cancel=cancel, output_root=tmp_path)
    assert result.status == "cancelled"
    assert not result.folder.exists()


def test_audio_suffixes():
    assert ".mp3" in AUDIO_SUFFIXES
    assert ".m4a" in AUDIO_SUFFIXES
```

- [ ] **Step 2: 跑测试确认失败**

Run: `E:\PianoConvertApp\.venv\Scripts\python.exe -m pytest tests/test_convert.py -v`

Expected: FAIL，`ModuleNotFoundError: app.convert`

- [ ] **Step 3: 最小实现**

`app/engines/base.py`:

```python
from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Callable, Protocol

ProgressCb = Callable[[str, float], None]


class CancelledError(Exception):
    pass


class TranscriptionEngine(Protocol):
    def transcribe(self, audio_path: Path, dest_midi: Path, cancel: Event, on_progress: ProgressCb) -> None:
        ...
```

`app/convert.py`:

```python
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Mapping

from app.engines.base import CancelledError, TranscriptionEngine
from app.score_io import midi_to_musicxml

AUDIO_SUFFIXES = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}


class ConvertError(ValueError):
    pass


@dataclass
class ConvertResult:
    status: str
    folder: Path
    midi_path: Path
    musicxml_path: Path
    error: str
    title: str
    kind: str
    source_path: Path


def humanize_error(exc: BaseException) -> str:
    text = str(exc)
    low = text.lower()
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in {13, 28}:
        return "无法写入输出目录"
    if "cuda" in low or "out of memory" in low or "cublas" in low:
        return "显存不足或 GPU 出错，可关闭其他占用显卡的程序后重试"
    if "cannot open" in low or "failed to load" in low or "nobyteserror" in low or "soundfile" in low:
        return "无法读取这个音频文件"
    return f"转录失败：{text[:180]}"


def _stamp_dir(output_root: Path, source: Path) -> Path:
    from datetime import datetime

    folder = output_root / f"{source.stem}_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def run(
    source: Path,
    kind: str,
    engines: Mapping[str, TranscriptionEngine],
    cancel: Event,
    output_root: Path | None = None,
    on_progress=None,
) -> ConvertResult:
    source = Path(source)
    if source.suffix.lower() not in AUDIO_SUFFIXES or not source.exists():
        raise ConvertError("不支持这个音频文件")
    if kind not in engines:
        raise ConvertError("未知的转换类型")
    from app.paths import output_root as default_root

    root = output_root or default_root()
    root.mkdir(parents=True, exist_ok=True)
    folder = _stamp_dir(root, source)
    midi_path = folder / f"{source.stem}.mid"
    xml_path = folder / f"{source.stem}.musicxml"
    progress = on_progress or (lambda _m, _p: None)

    def _cancelled_result(err: str = "") -> ConvertResult:
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        return ConvertResult("cancelled", folder, midi_path, xml_path, err, source.stem, kind, source)

    try:
        if cancel.is_set():
            return _cancelled_result()
        progress("读取音频", 0.1)
        progress("转录", 0.3)
        engines[kind].transcribe(source, midi_path, cancel, progress)
        if cancel.is_set():
            return _cancelled_result()
        progress("写入 MIDI", 0.7)
        if not midi_path.exists():
            raise RuntimeError("引擎没有写出 MIDI")
        try:
            progress("写入 MusicXML", 0.85)
            midi_to_musicxml(midi_path, xml_path)
        except Exception as exc:  # noqa: BLE001
            return ConvertResult(
                "partial",
                folder,
                midi_path,
                xml_path,
                f"谱面导出失败：{exc}"[:200],
                source.stem,
                kind,
                source,
            )
        progress("完成", 1.0)
        return ConvertResult("success", folder, midi_path, xml_path, "", source.stem, kind, source)
    except CancelledError:
        return _cancelled_result()
    except ConvertError:
        raise
    except Exception as exc:  # noqa: BLE001
        return ConvertResult("failed", folder, midi_path, xml_path, humanize_error(exc), source.stem, kind, source)
```

失败时 **保留** 文件夹（spec：失败进历史）；取消才删目录。`test_failed_transcribe_humanizes_cuda` 不要求删目录。

- [ ] **Step 4: 再跑测试**

Run: `E:\PianoConvertApp\.venv\Scripts\python.exe -m pytest tests/test_convert.py -v`

Expected: PASS。若 FakeEngine 在 cancel 已 set 时仍走 `hang_until_cancel` 分支，确认一进来就 `raise CancelledError`。

- [ ] **Step 5: Commit**

```powershell
git add app/convert.py app/engines/base.py tests/test_convert.py
git commit -m "feat: orchestrate transcription, MusicXML, and cancel"
```

---

### Task 6: 钢琴引擎（mock `PianoTranscription`）

**Files:**
- Create: `app/engines/kong_piano.py`
- Test: `tests/test_kong_piano.py`

- [ ] **Step 1: 写失败测试**

`tests/test_kong_piano.py`:

```python
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from app.engines.kong_piano import KongPianoEngine


class FakeTranscriber:
    def transcribe(self, audio_path, midi_path):
        Path(midi_path).write_bytes(Path(__file__).with_name("not-used").read_bytes() if False else b"")
        import pretty_midi

        pm = pretty_midi.PrettyMIDI()
        inst = pretty_midi.Instrument(program=0)
        inst.notes.append(pretty_midi.Note(velocity=70, pitch=67, start=0.0, end=0.3))
        pm.instruments.append(inst)
        pm.write(str(midi_path))


def test_kong_writes_midi(tmp_path, monkeypatch):
    monkeypatch.setattr("app.engines.kong_piano._make_transcriber", lambda device, ckpt: FakeTranscriber())
    monkeypatch.setattr("app.engines.kong_piano.model_checkpoint", lambda: tmp_path / "fake.pth")
    src = tmp_path / "a.wav"
    src.write_bytes(b"xx")
    dest = tmp_path / "a.mid"
    KongPianoEngine().transcribe(src, dest, Event(), lambda *_: None)
    assert dest.exists()
    assert dest.stat().st_size > 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `E:\PianoConvertApp\.venv\Scripts\python.exe -m pytest tests/test_kong_piano.py -v`

Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 最小实现**

`app/engines/kong_piano.py`:

```python
from __future__ import annotations

from pathlib import Path
from threading import Event

from app.device import resolve_device
from app.engines.base import CancelledError, ProgressCb
from app.paths import model_checkpoint


def _make_transcriber(device: str, ckpt: Path):
    from piano_transcription_inference import PianoTranscription

    return PianoTranscription(device=device, checkpoint_path=str(ckpt))


class KongPianoEngine:
    def transcribe(self, audio_path: Path, dest_midi: Path, cancel: Event, on_progress: ProgressCb) -> None:
        if cancel.is_set():
            raise CancelledError()
        on_progress("转录", 0.4)
        transcriber = _make_transcriber(resolve_device(), model_checkpoint())
        if cancel.is_set():
            raise CancelledError()
        transcriber.transcribe(str(audio_path), str(dest_midi))
```

- [ ] **Step 4: 再跑测试**

Run: `E:\PianoConvertApp\.venv\Scripts\python.exe -m pytest tests/test_kong_piano.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add app/engines/kong_piano.py tests/test_kong_piano.py
git commit -m "feat: add Kong piano transcription engine"
```

---

### Task 7: 其他乐器引擎（mock basic-pitch）

**Files:**
- Create: `app/engines/basic_pitch.py`
- Test: `tests/test_basic_pitch.py`

- [ ] **Step 1: 写失败测试**

`tests/test_basic_pitch.py`:

```python
from pathlib import Path
from threading import Event

import pretty_midi

from app.engines.basic_pitch import BasicPitchEngine


def test_basic_pitch_writes_midi(tmp_path, monkeypatch):
    midi_src = tmp_path / "pred.mid"
    pm = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(program=40)
    inst.notes.append(pretty_midi.Note(velocity=80, pitch=72, start=0.0, end=0.2))
    pm.instruments.append(inst)
    pm.write(str(midi_src))

    def fake_predict(*_a, **_k):
        return None, pretty_midi.PrettyMIDI(str(midi_src)), []

    monkeypatch.setattr("app.engines.basic_pitch.predict", fake_predict)
    dest = tmp_path / "out.mid"
    BasicPitchEngine().transcribe(tmp_path / "a.mp3", dest, Event(), lambda *_: None)
    out = pretty_midi.PrettyMIDI(str(dest))
    assert out.instruments[0].notes[0].pitch == 72
```

- [ ] **Step 2: 跑测试确认失败**

Run: `E:\PianoConvertApp\.venv\Scripts\python.exe -m pytest tests/test_basic_pitch.py -v`

Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 最小实现**

`app/engines/basic_pitch.py`:

```python
from __future__ import annotations

from pathlib import Path
from threading import Event

from app.engines.base import CancelledError, ProgressCb


def predict(audio_path: str, model_or_model_path=None):
    from basic_pitch.inference import predict as bp_predict
    from basic_pitch import ICASSP_2022_MODEL_PATH

    return bp_predict(audio_path, model_or_model_path or ICASSP_2022_MODEL_PATH)


class BasicPitchEngine:
    def transcribe(self, audio_path: Path, dest_midi: Path, cancel: Event, on_progress: ProgressCb) -> None:
        if cancel.is_set():
            raise CancelledError()
        on_progress("转录", 0.4)
        _model_out, midi_data, _notes = predict(str(audio_path))
        if cancel.is_set():
            raise CancelledError()
        dest_midi.parent.mkdir(parents=True, exist_ok=True)
        midi_data.write(str(dest_midi))
```

测试里 mock 的是 `app.engines.basic_pitch.predict`，因此单测不 import 真 basic-pitch。

- [ ] **Step 4: 再跑测试**

Run: `E:\PianoConvertApp\.venv\Scripts\python.exe -m pytest tests/test_basic_pitch.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add app/engines/basic_pitch.py tests/test_basic_pitch.py
git commit -m "feat: add basic-pitch engine for non-piano audio"
```

---

### Task 8: 主窗口 UI（offscreen 冒烟）

**Files:**
- Create: `app/ui/styles.py`
- Create: `app/ui/main_window.py`
- Create: `app/__main__.py`
- Test: `tests/test_ui_smoke.py`

- [ ] **Step 1: 写失败测试**

`tests/test_ui_smoke.py`:

```python
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow


def test_start_disabled_without_file():
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    assert win.start_button.isEnabled() is False
    assert win.windowTitle() == "PianoConvert"
    assert win.piano_radio.isChecked() is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `$env:QT_QPA_PLATFORM='offscreen'; E:\PianoConvertApp\.venv\Scripts\python.exe -m pytest tests/test_ui_smoke.py -v`

Expected: FAIL，`ModuleNotFoundError: app.ui.main_window`

- [ ] **Step 3: 最小实现（现代浅色单窗，无卷帘）**

`app/ui/styles.py`:

```python
APP_QSS = """
QMainWindow, QWidget { background: #f6f7fb; font-size: 14px; color: #1f2430; }
QLabel#hero { font-size: 22px; font-weight: 600; }
QPushButton#primary {
  background: #2f6fed; color: white; border: none; border-radius: 10px;
  padding: 10px 18px; font-weight: 600;
}
QPushButton#primary:disabled { background: #b9c6e4; }
QPushButton { border-radius: 8px; padding: 8px 12px; background: #ffffff; border: 1px solid #d8dee9; }
QProgressBar { border: none; background: #e6eaf2; border-radius: 6px; height: 10px; }
QProgressBar::chunk { background: #2f6fed; border-radius: 6px; }
QListWidget { background: white; border: 1px solid #e2e6ef; border-radius: 12px; padding: 6px; }
QFrame#card { background: white; border: 1px solid #e2e6ef; border-radius: 16px; }
"""
```

`app/ui/main_window.py` 要点（完整实现，不要留空方法）：

- 接受可选 `history_store` / `convert_fn` 便于测
- `QFileDialog` + `dragEnterEvent`/`dropEvent` 只接受 `AUDIO_SUFFIXES`
- `QRadioButton` 钢琴（默认）/ 其他乐器
- `start_button` 无文件时 disabled；选中后 enabled
- 进行中：按钮文案「取消」、禁用选文件和 radio；`QThread` 调 `convert.run`
- 完成非 cancel：`append_item` 后刷新列表（倒序）
- 历史行：标题、类型中文、时间、状态；按钮打开 MIDI / MusicXML / 文件夹
- `partial`：MusicXML 按钮 `setEnabled(False)`
- `failed`：不显示打开文件按钮
- 文件不存在：`QMessageBox.warning(self, "PianoConvert", "文件不在了")`
- 状态栏：`resolve_device()=="cpu"` 时显示「使用 CPU，会比较慢」
- 打开：`os.startfile(path)`

线程：`ConvertWorker(QObject)`，`signals.finished(ConvertResult)` / `progress(str, float)` / `failed(str)`。取消：`self._cancel.set()`。

`app/__main__.py`:

```python
from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow
from app.ui.styles import APP_QSS


def main() -> None:
    app = QApplication([])
    app.setStyleSheet(APP_QSS)
    win = MainWindow()
    win.resize(720, 760)
    win.show()
    app.exec()


if __name__ == "__main__":
    main()
```

`MainWindow` 构造里组装默认 engines：

```python
from app.convert import run as convert_run
from app.engines.basic_pitch import BasicPitchEngine
from app.engines.kong_piano import KongPianoEngine

def default_engines():
    return {"piano": KongPianoEngine(), "other": BasicPitchEngine()}
```

历史刷新：`load_items()[::-1]`。

- [ ] **Step 4: 再跑测试**

Run: `$env:QT_QPA_PLATFORM='offscreen'; E:\PianoConvertApp\.venv\Scripts\python.exe -m pytest tests/test_ui_smoke.py tests/test_convert.py tests/test_history.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add app/ui app/__main__.py tests/test_ui_smoke.py
git commit -m "feat: add simple Chinese conversion window and history"
```

---

### Task 9: UI 行为补测（选文件启用开始、部分成功禁用 XML）

**Files:**
- Modify: `tests/test_ui_smoke.py`
- Modify: `app/ui/main_window.py`（若测试暴露缺口）

- [ ] **Step 1: 追加失败测试**

```python
from pathlib import Path

from PySide6.QtCore import Qt
from app.history import HistoryItem, append_item


def test_choosing_file_enables_start(tmp_path):
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    win.set_source_file(src)
    assert win.start_button.isEnabled() is True
    assert "a.mp3" in win.file_label.text()


def test_partial_row_disables_musicxml(tmp_path, monkeypatch):
    monkeypatch.setattr("app.ui.main_window.history_path", lambda: tmp_path / "h.json")
    midi = tmp_path / "t.mid"
    midi.write_bytes(b"m")
    append_item(
        HistoryItem(
            id="1",
            title="t",
            source_path="x",
            kind="piano",
            created_at="2026-01-01T00:00:00",
            status="partial",
            midi_path=str(midi),
            musicxml_path=str(tmp_path / "missing.musicxml"),
            folder=str(tmp_path),
            error="谱面导出失败",
        )
    )
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.reload_history()
    row = win.history_list.itemWidget(win.history_list.item(0))
    assert row.xml_button.isEnabled() is False
    assert row.midi_button.isEnabled() is True
```

`MainWindow.set_source_file`、`reload_history`、历史行 widget 的 `xml_button`/`midi_button` 必须存在。

- [ ] **Step 2: 跑测试确认失败（若已实现则会 PASS，那时不要改生产代码去迎合假失败）**

Run: `$env:QT_QPA_PLATFORM='offscreen'; E:\PianoConvertApp\.venv\Scripts\python.exe -m pytest tests/test_ui_smoke.py -v`

Expected: 若 Task 8 未暴露这些 API 则 FAIL；补上 API 后再 PASS。

- [ ] **Step 3: 补最小 API**

`set_source_file(self, path: Path)` 保存路径、更新标签、`start_button.setEnabled(True)`。  
历史行用自定义 `HistoryRow(QWidget)`。

- [ ] **Step 4: 全量单测**

Run: `$env:QT_QPA_PLATFORM='offscreen'; E:\PianoConvertApp\.venv\Scripts\python.exe -m pytest -v`

Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add tests/test_ui_smoke.py app/ui/main_window.py
git commit -m "test: cover start-button enable and partial MusicXML state"
```

---

### Task 10: PyInstaller + Inno Setup

**Files:**
- Create: `scripts/build_exe.ps1`
- Create: `scripts/build_installer.ps1`
- Create: `installer/PianoConvert.iss`
- Create: `app.spec`（PyInstaller）

- [ ] **Step 1: 写 `app.spec`**

```python
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH)
datas = [
    (str(root / "models" / "note_F1=0.9677_pedal_F1=0.9186.pth"), "piano_transcription_inference_data"),
]
datas += collect_data_files("basic_pitch")
datas += collect_data_files("music21")
hidden = collect_submodules("music21") + collect_submodules("basic_pitch") + collect_submodules("piano_transcription_inference")

a = Analysis(
    ["app/__main__.py"],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden + ["app", "app.ui.main_window", "app.engines.kong_piano", "app.engines.basic_pitch"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PianoConvert", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="PianoConvert")
```

- [ ] **Step 2: 写 `installer/PianoConvert.iss`**

```iss
#define MyAppName "PianoConvert"
#define MyAppVersion "0.2.0"
#define MyAppExeName "PianoConvert.exe"

[Setup]
AppId={{A7C2E4D1-9B18-4F3A-8C55-PIANOCONV02}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\PianoConvert
DefaultGroupName=PianoConvert
OutputDir=output
OutputBaseFilename=PianoConvertSetup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务"; Flags: checkedonce

[Files]
Source: "..\dist\PianoConvert\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PianoConvert"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\PianoConvert"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 PianoConvert"; Flags: nowait postinstall skipifsilent
```

若本机 Inno 没有 `ChineseSimplified.isl`，改成 `compiler:Default.isl`，界面文案仍可用英文编译器语言，**应用本身仍是中文**。

- [ ] **Step 3: 写构建脚本**

`scripts/build_exe.ps1`:

```powershell
$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\.."
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe -m PyInstaller app.spec --noconfirm
```

`scripts/build_installer.ps1`:

```powershell
param([string]$Version = "0.2.0")
$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\.."
& "$PSScriptRoot\build_exe.ps1"
$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { $iscc = "${env:ProgramFiles}\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { throw "未安装 Inno Setup 6" }
& $iscc /DMyAppVersion=$Version installer\PianoConvert.iss
Write-Host "Installer: $(Resolve-Path installer\output\PianoConvertSetup-$Version.exe)"
```

- [ ] **Step 4: 本机试编译（可在实现时跑；CI 同逻辑）**

```powershell
cd E:\PianoConvertApp
.\scripts\build_installer.ps1 -Version 0.2.0
```

Expected: 生成 `installer\output\PianoConvertSetup-0.2.0.exe`。若缺 Inno Setup，安装后再跑，不要假装成功。

- [ ] **Step 5: Commit**

```powershell
git add app.spec scripts installer
git commit -m "build: add PyInstaller and Inno Setup packaging"
```

---

### Task 11: GitHub Actions Release + README 发布说明

**Files:**
- Create: `.github/workflows/release.yml`
- Modify: `README.md`

- [ ] **Step 1: 写 workflow**

`.github/workflows/release.yml`:

```yaml
name: Release Installer
on:
  push:
    tags:
      - "v*"
  workflow_dispatch:
    inputs:
      version:
        description: "Version like v0.2.0"
        required: true

jobs:
  test:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.10"
      - run: python -m pip install -r requirements.txt
      - run: python -m pytest -v
        env:
          QT_QPA_PLATFORM: offscreen

  installer:
    needs: test
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.10"
      - run: python -m pip install -r requirements.txt pyinstaller
      - run: python -m PyInstaller app.spec --noconfirm
      - uses: Minionguyjschipper/inno-setup-action@v1.2.0
        # 若该 action 不可用，改为 choco install innosetup 后调用 ISCC
      - name: Compile installer
        run: |
          $ver = "${{ github.ref_name }}"
          if ($ver -notlike "v*") { $ver = "${{ github.event.inputs.version }}" }
          $ver = $ver.TrimStart("v")
          & "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /DMyAppVersion=$ver installer\PianoConvert.iss
      - uses: softprops/action-gh-release@v2
        with:
          files: installer/output/PianoConvertSetup-*.exe
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

`choco install innosetup -y` 更稳，把 “inno-setup-action” 换成：

```yaml
      - name: Install Inno Setup
        run: choco install innosetup -y
```

- [ ] **Step 2: README 增加发布步骤**

```markdown
## 发布

1. `gh auth login`（本机若未登录）
2. 建公开仓库 `PianoConvert` 并 `git remote add origin ...`
3. `git tag v0.2.0 && git push origin main --tags`

Actions 会挂上 `PianoConvertSetup-0.2.0.exe`。
模型权重需在仓库或 Release 资源中提供；若 `.pth` 不进 git，workflow 要先从安装缓存/`actions/cache` 或 GitHub Release 资产下载到 `models/`。
```

权重约 164MB：workflow 在 PyInstaller 前从现有 URL 或 `secrets` 下载。若没有公开 URL，文档写：**本机 `build_installer.ps1` 用本地 `models/*.pth`；GitHub Actions 在权重未上传前会失败。** 不要假装有下载地址。

- [ ] **Step 3: 确认 YAML 缩进合法（本地不跑 Actions）**

- [ ] **Step 4: Commit**

```powershell
git add .github/workflows/release.yml README.md
git commit -m "ci: build Windows installer on version tags"
```

---

## Spec coverage

| Spec 项 | Task |
|---|---|
| 新仓库 `E:\PianoConvertApp` | 1 |
| 路径 / 模型 / CUDA 检测 | 2 |
| 历史 JSON 字段 | 3 |
| MIDI + MusicXML | 4–5 |
| 取消删目录、不写历史（UI 不 append cancelled） | 5、8 |
| 失败人话、partial XML | 5、9 |
| 钢琴 / 其他引擎 | 6、7 |
| 单窗中文 UI、进度、历史按钮 | 8–9 |
| 一次一首、无文件禁用开始 | 8–9 |
| Inno Setup、Program Files、快捷方式 | 10 |
| GitHub tag Release | 11 |
| 不捆绑 MuseScore、无卷帘/阈值 | 全程不添加 |

## 实现时注意

- 提交信息用英文或中文均可，保持简短。
- 用户规则：未要求时不要 `git push`，不要改 git config。
- 真正发 Release 前让用户 `gh auth login`。
- 默认 CI 不跑真实长音频转录。
