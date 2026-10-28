from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Mapping

from app.engines.base import CancelledError, TranscriptionEngine
from app.keyboard_score import (
    SCORE_SUFFIXES,
    STYLE_DETAILED,
    KeyboardScoreError,
    score_file_to_keyboard_text,
)
from app.paths import safe_stem
from app.pdf_omr import IMAGE_SUFFIXES, PDF_SUFFIXES, VECTOR_SUFFIX, PdfOmrError, count_staves, recognize_pdf
from app.score_io import midi_to_musicxml, musicxml_to_midi, musicxml_to_pdf

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
    pdf_path: Path | None = None  # the score as staff notation (the original PDF for a PDF)


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


def _keyboard_text(midi_or_score: Path, title: str, strict: bool = True, style: str = STYLE_DETAILED) -> str:
    return score_file_to_keyboard_text(midi_or_score, title, strict=strict, style=style)


def _write_keyboard(midi_or_score: Path, dest: Path, title: str, strict: bool = True, style: str = STYLE_DETAILED) -> str:
    text = _keyboard_text(midi_or_score, title, strict=strict, style=style)
    dest.write_text(text, encoding="utf-8")
    return text


def _with_note(text: str, note: str) -> str:
    """Put extra '#' lines after the keyboard score's header, before the music."""
    head, sep, rest = text.partition("\n\n【")
    if not sep:
        return note + text
    return head + "\n" + note.rstrip("\n") + sep + rest


def _grid_divisors(keyboard_text: str) -> tuple[int, ...] | None:
    """The keyboard score's grid as music21 quantization (cells per quarter note), so the
    MusicXML / PDF written from a MIDI file shows the same rhythm as the keyboard score."""
    head = re.search(r"(\d+)/(\d+)\s+♩=\S+\s+每拍 (\d+) 格", keyboard_text or "")
    if not head:
        return None
    per_quarter = int(head.group(3)) * int(head.group(2)) / 4
    return (int(per_quarter),) if per_quarter >= 1 and per_quarter == int(per_quarter) else None


def _fatal(exc: BaseException) -> bool:
    if isinstance(exc, (CancelledError, ConvertError)):
        return True
    return isinstance(exc, OSError) and getattr(exc, "errno", None) in {13, 28}


def _complete_outputs(
    midi_path: Path,
    xml_path: Path,
    pdf_path: Path,
    keyboard_text: str,
    progress,
    cancel: Event,
    errors: list[str],
    title: str = "",
) -> None:
    """Every conversion ends with all four files: MIDI, MusicXML, a staff-notation PDF and
    the keyboard score. Whatever the source did not give is made from the others."""
    if not midi_path.exists() and xml_path.exists():
        try:
            progress("写入 MIDI", 0.84)
            musicxml_to_midi(xml_path, midi_path)
        except Exception as exc:  # noqa: BLE001
            if _fatal(exc):
                raise
            errors.append("MIDI 导出失败")
    if cancel.is_set():
        raise CancelledError()
    if not xml_path.exists() and midi_path.exists():
        try:
            progress("写入 MusicXML", 0.88)
            midi_to_musicxml(midi_path, xml_path, _grid_divisors(keyboard_text))
        except Exception as exc:  # noqa: BLE001
            if _fatal(exc):
                raise
            errors.append(f"谱面导出失败：{exc}"[:200])
    if cancel.is_set():
        raise CancelledError()
    if not pdf_path.exists() and xml_path.exists():
        try:
            progress("写入五线谱 PDF", 0.94)
            musicxml_to_pdf(xml_path, pdf_path, title or None)
        except Exception as exc:  # noqa: BLE001
            if _fatal(exc):
                raise
            errors.append("五线谱 PDF 导出失败")


def run(
    source: Path,
    kind: str,
    engines: Mapping[str, TranscriptionEngine],
    cancel: Event,
    output_root: Path | None = None,
    on_progress=None,
    style: str = STYLE_DETAILED,
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
    pdf_path = folder / f"{work}.pdf"
    keyboard_path = folder / f"{work}_键盘谱.txt"
    audio_copy = folder / _work_name(source)
    progress = on_progress or (lambda _m, _p: None)

    def _cancelled_result(err: str = "") -> ConvertResult:
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        return ConvertResult(
            "cancelled", folder, midi_path, xml_path, err, source.stem, kind, source, keyboard_path, pdf_path
        )

    def _pack(status: str, error: str) -> ConvertResult:
        return ConvertResult(status, folder, midi_path, xml_path, error, source.stem, kind, source, keyboard_path, pdf_path)

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
        text = ""
        try:
            progress("写入键盘谱", 0.8)
            # transcribed from audio: onsets are not on a grid
            text = _write_keyboard(midi_path, keyboard_path, work, strict=False, style=style)
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, CancelledError):
                raise
            if isinstance(exc, OSError) and getattr(exc, "errno", None) in {13, 28}:
                errors.append("无法写入输出目录")
            else:
                errors.append("键盘谱导出失败")
        _complete_outputs(midi_path, xml_path, pdf_path, text, progress, cancel, errors, source.stem)
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
    style: str = STYLE_DETAILED,
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
    pdf_path = folder / f"{work}.pdf"
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
            "cancelled", folder, midi_path, xml_path, err, source.stem, kind, source, keyboard_path, pdf_path
        )

    def _pack(status: str, error: str) -> ConvertResult:
        return ConvertResult(status, folder, midi_path, xml_path, error, source.stem, kind, source, keyboard_path, pdf_path)

    try:
        if cancel.is_set():
            return _cancelled_result()
        progress("读取乐谱", 0.2)
        shutil.copy2(source, copied)
        if cancel.is_set():
            return _cancelled_result()
        progress("写入键盘谱", 0.5)
        text = _write_keyboard(copied, keyboard_path, work, style=style)
        errors: list[str] = []
        _complete_outputs(midi_path, xml_path, pdf_path, text, progress, cancel, errors, source.stem)
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
    style: str = STYLE_DETAILED,
) -> ConvertResult:
    source = Path(source)
    if source.suffix.lower() not in PDF_SUFFIXES or not source.exists():
        raise ConvertError("不支持这个文件，请选择 PDF 或 PNG / JPG 图片")
    from app.paths import output_root as default_root

    root = output_root or default_root()
    root.mkdir(parents=True, exist_ok=True)
    folder = _stamp_dir(root, source)
    work = safe_stem(source.name)
    copied = folder / _work_name(source)
    xml_path = folder / f"{work}.musicxml"
    keyboard_path = folder / f"{work}_键盘谱.txt"
    midi_path = folder / f"{work}.mid"
    is_image = source.suffix.lower() in IMAGE_SUFFIXES
    # a PDF is its own staff-notation PDF; a picture gets one engraved from the reading
    pdf_path = folder / f"{work}.pdf" if is_image else copied
    kind = "pdf"
    progress = on_progress or (lambda _m, _p: None)
    run_recognize = recognize or (
        lambda pdf, work_dir, stop: recognize_pdf(pdf, work_dir, stop, on_progress=progress)
    )

    def _cancelled_result(err: str = "") -> ConvertResult:
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        return ConvertResult(
            "cancelled", folder, midi_path, xml_path, err, source.stem, kind, source, keyboard_path, pdf_path
        )

    def _pack(status: str, error: str) -> ConvertResult:
        return ConvertResult(status, folder, midi_path, xml_path, error, source.stem, kind, source, keyboard_path, pdf_path)

    try:
        if cancel.is_set():
            return _cancelled_result()
        progress("读取图片" if is_image else "读取 PDF", 0.1)
        shutil.copy2(source, copied)
        progress("识别乐谱", 0.15)
        produced = run_recognize(copied, folder, cancel)
        if cancel.is_set():
            return _cancelled_result()
        is_midi = produced.suffix.lower() in {".mid", ".midi"}
        is_vector = produced.name.endswith(VECTOR_SUFFIX)
        result_path = midi_path if is_midi else xml_path
        if produced.resolve() != result_path.resolve():
            result_path.write_bytes(produced.read_bytes())
            if produced.parent.resolve() == folder.resolve():
                produced.unlink(missing_ok=True)
        progress("写入键盘谱", 0.8)
        text = score_file_to_keyboard_text(result_path, work, style=style)
        if is_vector:
            from app.pdf_vector import DEFAULT_BPM, tempo_printed

            note = "# 由 PDF 谱面上的音符直接读取（不是看图识别），请仍对照原谱弹奏。\n"
            if not tempo_printed(result_path):
                note += f"# 谱上没有标速度，♩={int(DEFAULT_BPM)} 只是默认值。\n"
        else:
            kind_text = "简谱" if is_midi else ""
            note = f"# 由{'图片' if is_image else ' PDF '}{kind_text}自动识别，准确度有限，请对照原谱。\n"
            if not is_midi and count_staves(xml_path) > 2:
                note += "# 注意：这份谱每行超过两行五线谱（多半是人声 + 钢琴），这类谱容易认错位，结果可能偏差较大。\n"
        keyboard_path.write_text(_with_note(text, note), encoding="utf-8")
        errors: list[str] = []
        _complete_outputs(midi_path, xml_path, pdf_path, text, progress, cancel, errors, source.stem)
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
    except (PdfOmrError, KeyboardScoreError) as exc:
        return _pack("failed", str(exc))
    except OSError:
        return _pack("failed", "无法写入输出目录")
    except Exception as exc:  # noqa: BLE001
        return _pack("failed", humanize_error(exc))
