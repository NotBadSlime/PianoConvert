from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Mapping

from app.engines.base import CancelledError, TranscriptionEngine
from app.keyboard_score import (
    ARPEGGIO_AUTO,
    CHORD_STANDARD,
    RANGE_STRATEGY_DEFAULT,
    SCORE_SUFFIXES,
    STYLE_DETAILED,
    KeyboardScoreError,
    fingertip_section_from_score_file,
    score_file_to_keyboard_text,
)
from app.paths import safe_stem
from app.pdf_omr import IMAGE_SUFFIXES, PDF_SUFFIXES, VECTOR_SUFFIX, PdfOmrError, count_staves, recognize_pdf
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


def _write_keyboard(
    midi_or_score: Path,
    dest: Path,
    title: str,
    strict: bool = True,
    style: str = STYLE_DETAILED,
    range_strategy: str = RANGE_STRATEGY_DEFAULT,
    arpeggio: str = ARPEGGIO_AUTO,
    chord: str = CHORD_STANDARD,
) -> None:
    text = score_file_to_keyboard_text(
        midi_or_score,
        title,
        strict=strict,
        style=style,
        range_strategy=range_strategy,
        arpeggio=arpeggio,
        chord=chord,
    )
    fingertip = fingertip_section_from_score_file(midi_or_score, range_strategy)
    dest.write_text(text.rstrip() + "\n\n" + fingertip, encoding="utf-8")


def _with_note(text: str, note: str) -> str:
    """Put extra '#' lines after the keyboard score's header, before the music."""
    head, sep, rest = text.partition("\n\n【")
    if not sep:
        return note + text
    return head + "\n" + note.rstrip("\n") + sep + rest


# provenance lines run_pdf puts after the header; kept when a score is laid out again
_NOTE_PREFIXES = ("# 由", "# 谱上没有标速度", "# 注意：")
_FROM_MIDI_NOTES = ("直接读取", "简谱自动识别")


def rerender_keyboard(
    kind: str,
    source_path: Path | str,
    midi_path: Path | str,
    xml_path: Path | str,
    keyboard_path: Path | str,
    *,
    style: str = STYLE_DETAILED,
    range_strategy: str = RANGE_STRATEGY_DEFAULT,
    arpeggio: str = ARPEGGIO_AUTO,
    chord: str = CHORD_STANDARD,
) -> str:
    """Lay out the keyboard score of an earlier conversion again with other settings.

    The conversion's own files are used, so nothing is recognized or transcribed again:
    the MIDI of a recording (laid out loosely), the original MIDI / MusicXML file, or the
    MIDI / MusicXML a PDF or picture was read into. The title and the notes about where
    the score came from are kept, and the fingertip section is written again too.
    Returns the new text, which is also written to the file.
    """
    keyboard_path = Path(keyboard_path)
    midi_path, xml_path = Path(midi_path), Path(xml_path)
    old = keyboard_path.read_text(encoding="utf-8") if keyboard_path.exists() else ""
    lines = old.splitlines()
    title = lines[0][2:].strip() if lines and lines[0].startswith("# ") else Path(source_path).stem
    notes = [line for line in lines if line.startswith(_NOTE_PREFIXES)]
    strict = True
    if kind in ("piano", "other"):
        source, strict = midi_path, False
    elif kind == "score":
        source = midi_path if Path(source_path).suffix.lower() in {".mid", ".midi"} else xml_path
    else:
        from_midi = any(mark in line for line in notes for mark in _FROM_MIDI_NOTES)
        source = midi_path if from_midi else xml_path
    if not source.exists():
        source = midi_path if midi_path.exists() else xml_path
    if not source.exists():
        raise KeyboardScoreError("这次转换的乐谱文件不在了，没法重新排版")
    text = score_file_to_keyboard_text(
        source,
        title,
        strict=strict,
        style=style,
        range_strategy=range_strategy,
        arpeggio=arpeggio,
        chord=chord,
    )
    if notes:
        text = _with_note(text, "\n".join(notes) + "\n")
    fingertip = fingertip_section_from_score_file(source, range_strategy)
    text = text.rstrip() + "\n\n" + fingertip
    keyboard_path.write_text(text, encoding="utf-8")
    return text


def run(
    source: Path,
    kind: str,
    engines: Mapping[str, TranscriptionEngine],
    cancel: Event,
    output_root: Path | None = None,
    on_progress=None,
    style: str = STYLE_DETAILED,
    range_strategy: str = RANGE_STRATEGY_DEFAULT,
    arpeggio: str = ARPEGGIO_AUTO,
    chord: str = CHORD_STANDARD,
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
            # transcribed from audio: onsets are not on a grid
            _write_keyboard(
                midi_path,
                keyboard_path,
                work,
                strict=False,
                style=style,
                range_strategy=range_strategy,
                arpeggio=arpeggio,
                chord=chord,
            )
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
    style: str = STYLE_DETAILED,
    range_strategy: str = RANGE_STRATEGY_DEFAULT,
    arpeggio: str = ARPEGGIO_AUTO,
    chord: str = CHORD_STANDARD,
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
        _write_keyboard(copied, keyboard_path, work, style=style, range_strategy=range_strategy, arpeggio=arpeggio, chord=chord)
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
    range_strategy: str = RANGE_STRATEGY_DEFAULT,
    arpeggio: str = ARPEGGIO_AUTO,
    chord: str = CHORD_STANDARD,
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
    kind = "pdf"
    progress = on_progress or (lambda _m, _p: None)
    run_recognize = recognize or (
        lambda pdf, work_dir, stop: recognize_pdf(pdf, work_dir, stop, on_progress=progress)
    )

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
        is_image = source.suffix.lower() in IMAGE_SUFFIXES
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
        text = score_file_to_keyboard_text(
            result_path,
            work,
            style=style,
            range_strategy=range_strategy,
            arpeggio=arpeggio,
            chord=chord,
        )
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
        text = _with_note(text, note)
        fingertip = fingertip_section_from_score_file(result_path, range_strategy)
        keyboard_path.write_text(text.rstrip() + "\n\n" + fingertip, encoding="utf-8")
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
