from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Mapping

from app.engines.base import CancelledError, TranscriptionEngine
from app.keyboard_score import (
    SCORE_SUFFIXES,
    KeyboardScoreError,
    score_file_to_keyboard_text,
)
from app.paths import safe_stem
from app.pdf_omr import PDF_SUFFIXES, PdfOmrError, recognize_pdf
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
    keyboard_path: Path


def humanize_error(exc: BaseException) -> str:
    if isinstance(exc, PdfOmrError):
        return str(exc)
    if isinstance(exc, KeyboardScoreError):
        return str(exc)
    text = str(exc)
    low = text.lower()
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in {13, 28}:
        return "无法写入输出目录"
    if "cuda" in low or "out of memory" in low or "cublas" in low:
        return "显存不足或 GPU 出错，可关闭其他占用显卡的程序后重试"
    if "cannot open" in low or "failed to load" in low or "nobyteserror" in low or "soundfile" in low:
        return "无法读取这个音频文件"
    if isinstance(exc, ModuleNotFoundError) or "no module named" in low:
        return "安装包不完整，请安装官网最新版后重试"
    return f"转录失败：{text[:180]}"


def _stamp_dir(output_root: Path, source: Path) -> Path:
    from datetime import datetime

    folder = output_root / f"{safe_stem(source.name)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _work_name(source: Path) -> str:
    return f"{safe_stem(source.name)}{source.suffix.lower()}"


def _write_keyboard(midi_or_score: Path, dest: Path, title: str) -> None:
    dest.write_text(score_file_to_keyboard_text(midi_or_score, title), encoding="utf-8")


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
    work = safe_stem(source.name)
    midi_path = folder / f"{work}.mid"
    xml_path = folder / f"{work}.musicxml"
    keyboard_path = folder / f"{work}_键盘谱.txt"
    audio_copy = folder / _work_name(source)
    progress = on_progress or (lambda _m, _p: None)

    def _cancelled_result(err: str = "") -> ConvertResult:
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        return ConvertResult(
            "cancelled", folder, midi_path, xml_path, err, source.stem, kind, source, keyboard_path
        )

    def _pack(status: str, error: str) -> ConvertResult:
        return ConvertResult(status, folder, midi_path, xml_path, error, source.stem, kind, source, keyboard_path)

    try:
        if cancel.is_set():
            return _cancelled_result()
        progress("读取音频", 0.1)
        shutil.copy2(source, audio_copy)
        progress("转录", 0.3)
        engines[kind].transcribe(audio_copy, midi_path, cancel, progress)
        if cancel.is_set():
            return _cancelled_result()
        progress("写入 MIDI", 0.7)
        if not midi_path.exists():
            raise RuntimeError("引擎没有写出 MIDI")
        errors: list[str] = []
        try:
            progress("写入 MusicXML", 0.85)
            midi_to_musicxml(midi_path, xml_path)
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, (CancelledError, ConvertError)):
                raise
            if isinstance(exc, OSError) and getattr(exc, "errno", None) in {13, 28}:
                raise
            errors.append(f"谱面导出失败：{exc}"[:200])
        try:
            progress("写入键盘谱", 0.92)
            _write_keyboard(midi_path, keyboard_path, work)
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, CancelledError):
                raise
            if isinstance(exc, OSError) and getattr(exc, "errno", None) in {13, 28}:
                errors.append("无法写入输出目录")
            else:
                errors.append("键盘谱导出失败")
        if errors:
            return _pack("partial", "；".join(errors))
        progress("完成", 1.0)
        return _pack("success", "")
    except CancelledError:
        return _cancelled_result()
    except ConvertError:
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        raise
    except Exception as exc:  # noqa: BLE001
        return _pack("failed", humanize_error(exc))


def run_score(
    source: Path,
    cancel: Event,
    output_root: Path | None = None,
    on_progress=None,
) -> ConvertResult:
    source = Path(source)
    suffix = source.suffix.lower()
    if suffix not in SCORE_SUFFIXES or not source.exists():
        raise ConvertError("不支持这个乐谱文件")
    from app.paths import output_root as default_root

    root = output_root or default_root()
    root.mkdir(parents=True, exist_ok=True)
    folder = _stamp_dir(root, source)
    work = safe_stem(source.name)
    copied = folder / _work_name(source)
    keyboard_path = folder / f"{work}_键盘谱.txt"
    if suffix in {".mid", ".midi"}:
        midi_path = copied
        xml_path = folder / f"{work}.musicxml"
    else:
        midi_path = folder / f"{work}.mid"
        xml_path = copied
    kind = "score"
    progress = on_progress or (lambda _m, _p: None)

    def _cancelled_result(err: str = "") -> ConvertResult:
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        return ConvertResult(
            "cancelled", folder, midi_path, xml_path, err, source.stem, kind, source, keyboard_path
        )

    def _pack(status: str, error: str) -> ConvertResult:
        return ConvertResult(status, folder, midi_path, xml_path, error, source.stem, kind, source, keyboard_path)

    try:
        if cancel.is_set():
            return _cancelled_result()
        progress("读取乐谱", 0.2)
        shutil.copy2(source, copied)
        if cancel.is_set():
            return _cancelled_result()
        progress("写入键盘谱", 0.6)
        _write_keyboard(copied, keyboard_path, work)
        progress("完成", 1.0)
        return _pack("success", "")
    except CancelledError:
        return _cancelled_result()
    except ConvertError:
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        raise
    except KeyboardScoreError as exc:
        return _pack("failed", str(exc))
    except OSError:
        return _pack("failed", "无法写入输出目录")
    except Exception as exc:  # noqa: BLE001
        return _pack("failed", humanize_error(exc))


def run_pdf(
    source: Path,
    cancel: Event,
    output_root: Path | None = None,
    on_progress=None,
    recognize=None,
) -> ConvertResult:
    source = Path(source)
    if source.suffix.lower() not in PDF_SUFFIXES or not source.exists():
        raise ConvertError("不支持这个 PDF 文件")
    from app.paths import output_root as default_root

    root = output_root or default_root()
    root.mkdir(parents=True, exist_ok=True)
    folder = _stamp_dir(root, source)
    work = safe_stem(source.name)
    copied = folder / f"{work}.pdf"
    xml_path = folder / f"{work}.musicxml"
    keyboard_path = folder / f"{work}_键盘谱.txt"
    midi_path = folder / f"{work}.mid"
    kind = "pdf"
    progress = on_progress or (lambda _m, _p: None)
    run_recognize = recognize or recognize_pdf

    def _cancelled_result(err: str = "") -> ConvertResult:
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        return ConvertResult(
            "cancelled", folder, midi_path, xml_path, err, source.stem, kind, source, keyboard_path
        )

    def _pack(status: str, error: str) -> ConvertResult:
        return ConvertResult(status, folder, midi_path, xml_path, error, source.stem, kind, source, keyboard_path)

    try:
        if cancel.is_set():
            return _cancelled_result()
        progress("读取 PDF", 0.1)
        shutil.copy2(source, copied)
        progress("识别乐谱（准确度有限）", 0.3)
        produced = run_recognize(copied, folder, cancel)
        if cancel.is_set():
            return _cancelled_result()
        if produced.resolve() != xml_path.resolve():
            xml_path.write_bytes(produced.read_bytes())
        progress("写入键盘谱", 0.8)
        text = score_file_to_keyboard_text(xml_path, work)
        note = "# 由 PDF 自动识别，准确度有限，请对照原谱。\n"
        keyboard_path.write_text(note + text, encoding="utf-8")
        progress("完成", 1.0)
        return _pack("success", "")
    except CancelledError:
        return _cancelled_result()
    except ConvertError:
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        raise
    except (PdfOmrError, KeyboardScoreError) as exc:
        return _pack("failed", str(exc))
    except OSError:
        return _pack("failed", "无法写入输出目录")
    except Exception as exc:  # noqa: BLE001
        return _pack("failed", humanize_error(exc))
