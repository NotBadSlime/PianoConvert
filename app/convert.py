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
