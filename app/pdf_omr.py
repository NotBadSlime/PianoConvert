from __future__ import annotations

import copy as copy_module
import hashlib
import json
import os
import queue
import re
import shutil
import threading
import time
import xml.etree.ElementTree as ET
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from threading import Event
from typing import Callable

from app import homr_models
from app.engines.base import CancelledError

PDF_ONLY_SUFFIXES = {".pdf"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
# Everything that goes through optical score recognition (the "PDF / 图片" button).
PDF_SUFFIXES = PDF_ONLY_SUFFIXES | IMAGE_SUFFIXES
# Shared raster resolution; Copista adapts the detector size to the observed staff spacing.
RENDER_WIDTH = 2400
PAGE_PREFIX = "page_"
DEFAULT_OMR_ENGINE = "copista-28m"

Progress = Callable[[str, float], None]


class PdfOmrError(ValueError):
    pass


def pick_musicxml(paths: list[Path]) -> Path:
    xmls = [path for path in paths if path.suffix.lower() == ".musicxml" and path.is_file()]
    merged = [path for path in xmls if path.name.endswith("_merged.musicxml")]
    if merged:
        return merged[0]
    if len(xmls) == 1:
        return xmls[0]
    if not xmls:
        raise PdfOmrError("没有识别出乐谱，请换一份更清晰的印刷五线谱 PDF")
    return max(xmls, key=lambda path: path.stat().st_size)


def model_dir() -> Path:
    """Per-user folder for the homr models that are not shipped with the app."""
    dest = homr_models.cache_dir()
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _redirect_homr_models() -> None:
    """Point homr at the shipped models, and at the per-user folder for those not shipped
    (the install folder is read-only, so nothing may be downloaded beside homr there)."""
    model_dir()
    import homr.main as homr_main
    import homr.segmentation.config as seg
    import homr.segmentation.inference_segnet as segnet_inference
    import homr.transformer.configs as cfg

    def _move(path: str) -> str:
        return str(homr_models.resolve(Path(path).name))

    seg.segnet_path_onnx = _move(seg.segnet_path_onnx)
    seg.segnet_path_onnx_fp16 = _move(seg.segnet_path_onnx_fp16)
    homr_main.segnet_path_onnx = seg.segnet_path_onnx
    homr_main.segnet_path_onnx_fp16 = seg.segnet_path_onnx_fp16
    # inference_segnet imports these paths by value, so updating only config.py
    # and homr.main leaves the inference class pointed at site-packages.
    segnet_inference.segnet_path_onnx = seg.segnet_path_onnx
    segnet_inference.segnet_path_onnx_fp16 = seg.segnet_path_onnx_fp16
    files = cfg.default_config.filepaths
    files.encoder_path = _move(files.encoder_path)
    files.decoder_path = _move(files.decoder_path)
    files.encoder_path_fp16 = _move(files.encoder_path_fp16)
    files.decoder_path_fp16 = _move(files.decoder_path_fp16)

    # HOMR creates a fresh Config for each staff; redirect those paths too, while
    # leaving tokenizer assets beside the installed package.
    original_config_init = cfg.Config.__init__
    if not getattr(original_config_init, "_pianoconvert_model_paths", False):
        def _config_init(self, *args, **kwargs):
            original_config_init(self, *args, **kwargs)
            for name in (
                "encoder_path",
                "decoder_path",
                "encoder_path_fp16",
                "decoder_path_fp16",
            ):
                setattr(self.filepaths, name, _move(getattr(self.filepaths, name)))

        _config_init._pianoconvert_model_paths = True
        cfg.Config.__init__ = _config_init


HOMR_FAILED = "PIANOCONVERT_HOMR_FAILED"
HOMR_EXIT_FAILED = 3


def homr_entry(image_path: str) -> None:
    """Run homr on one image or on a folder of page images (homr cannot read PDF itself).

    This is the whole homr process. Any error ends it with exit code HOMR_EXIT_FAILED and the
    traceback on stderr: an uncaught exception would open the packaged (windowed) app's modal
    "Unhandled exception in script" box, which keeps the process alive and the main window waiting.
    """
    try:
        _redirect_homr_models()
        import homr.main as homr_module
        from homr.main import main as homr_main

        original_list = homr_module.get_all_image_files_in_folder

        def _unique_images(folder: str) -> list[str]:
            # homr globs "*.png" and "*.PNG"; on Windows both match, so every page ran twice.
            seen: dict[str, str] = {}
            for item in original_list(folder):
                seen.setdefault(os.path.normcase(os.path.abspath(item)), item)
            return sorted(seen.values())

        homr_module.get_all_image_files_in_folder = _unique_images
        sys.argv = ["homr", image_path]
        homr_main()
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"{HOMR_FAILED}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(HOMR_EXIT_FAILED) from None


FINGERING_MAX_HEIGHT_RATIO = 0.012  # of page height; real note heads/lyrics are larger


def _fingering_boxes(page, page_height: float) -> list[tuple[float, float, float, float]]:
    """Boxes (PDF points) of printed fingering digits.

    Notation programs draw fingering either with the music font (glyphs U+0131..U+0135 for
    fingers 1-5) or as tiny ordinary digits 1-5. The recogniser mistakes them for notes, so
    they are painted out before recognition. Lyrics, titles and measure numbers are larger
    or use other characters and are left alone.
    """
    boxes: list[tuple[float, float, float, float]] = []
    try:
        textpage = page.get_textpage()
        count = textpage.count_chars()
    except Exception:  # noqa: BLE001
        return boxes
    limit = page_height * FINGERING_MAX_HEIGHT_RATIO
    for index in range(count):
        char = textpage.get_text_range(index, 1)
        if not char:
            continue
        code = ord(char[0])
        is_music_font_finger = 0x131 <= code <= 0x135
        is_small_digit = char in "12345"
        if not (is_music_font_finger or is_small_digit):
            continue
        left, bottom, right, top = textpage.get_charbox(index)
        if is_small_digit and (top - bottom) > limit:
            continue
        boxes.append((left, bottom, right, top))
    return boxes


def render_pages(pdf: Path, pages_dir: Path, width: int = RENDER_WIDTH, *, remove_fingering: bool = False) -> list[Path]:
    try:
        import pypdfium2 as pdfium
        from PIL import ImageDraw
    except Exception as exc:  # noqa: BLE001
        raise PdfOmrError("乐谱识别组件未就绪") from exc
    try:
        doc = pdfium.PdfDocument(str(pdf))
    except Exception as exc:  # noqa: BLE001
        raise PdfOmrError("无法打开这个 PDF，文件可能已损坏或有密码") from exc
    pages_dir.mkdir(parents=True, exist_ok=True)
    images: list[Path] = []
    try:
        count = len(doc)
        for index in range(count):
            page = doc[index]
            page_width, page_height = page.get_size()
            scale = width / page_width if page_width > 0 else 2.0
            bitmap = page.render(scale=scale, fill_color=(255, 255, 255, 255))
            image = bitmap.to_pil().convert("RGB")
            boxes = _fingering_boxes(page, page_height) if remove_fingering else []
            if boxes:
                draw = ImageDraw.Draw(image)
                for left, bottom, right, top in boxes:
                    draw.rectangle(
                        [left * scale - 1, (page_height - top) * scale - 1,
                         right * scale + 1, (page_height - bottom) * scale + 1],
                        fill=(255, 255, 255),
                    )
            out = pages_dir / f"{PAGE_PREFIX}{index + 1:03d}.png"
            image.save(out)
            images.append(out)
            page.close()
    finally:
        doc.close()
    if not images:
        raise PdfOmrError("这个 PDF 里没有页面")
    return images


def prepare_image(image_path: Path, pages_dir: Path, width: int = RENDER_WIDTH) -> list[Path]:
    """Copy one score photo/screenshot into the page folder as an RGB PNG on white."""
    try:
        from PIL import Image, ImageOps
    except Exception as exc:  # noqa: BLE001
        raise PdfOmrError("乐谱识别组件未就绪") from exc
    try:
        with Image.open(image_path) as raw:
            image = ImageOps.exif_transpose(raw)
            if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.split()[-1])
                image = background
            else:
                image = image.convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise PdfOmrError("无法打开这张图片，文件可能已损坏") from exc
    # Keep small screenshots readable without changing the original user file.
    if image.width < width * 0.75:
        ratio = width / image.width
        image = image.resize((width, round(image.height * ratio)), Image.LANCZOS)
    pages_dir.mkdir(parents=True, exist_ok=True)
    out = pages_dir / f"{PAGE_PREFIX}001.png"
    image.save(out)
    return [out]


def prepare_pages(source: Path, pages_dir: Path) -> list[Path]:
    if source.suffix.lower() in IMAGE_SUFFIXES:
        return prepare_image(source, pages_dir)
    return render_pages(source, pages_dir)


def _command(target: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--homr", str(target)]
    return [
        sys.executable,
        "-c",
        "from app.pdf_omr import homr_entry; import sys; homr_entry(sys.argv[1])",
        str(target),
    ]


def _page_of(line: str) -> int | None:
    if not line.startswith("Processing "):
        return None
    name = Path(line[len("Processing ") :].strip()).stem
    if not name.startswith(PAGE_PREFIX):
        return None
    try:
        return int(name[len(PAGE_PREFIX) :])
    except ValueError:
        return None


_HOMR_DOWNLOADED = re.compile(r"Downloaded (\d+) of (\d+) MB")
DOWNLOAD_FAILED = "无法下载识别模型，请检查网络后重试（已下载的部分会保留，下次接着下）"


def _ensure_homr_models(cancel: Event, progress: Progress) -> None:
    """Download the homr models that are neither shipped nor cached, showing the progress."""

    def _show(status: homr_models.DownloadStatus) -> None:
        progress(homr_models.status_text(status), 0.2 + 0.1 * status.fraction)

    try:
        homr_models.ensure_models(cancel, _show)
    except ImportError as exc:
        raise PdfOmrError("乐谱识别组件未就绪") from exc
    except homr_models.ModelDownloadError as exc:
        raise PdfOmrError(DOWNLOAD_FAILED) from exc


def _run_homr(pages_dir: Path, total: int, cancel: Event, progress: Progress) -> str:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    env["PIANOCONVERT_HOMR_MODELS"] = str(model_dir())
    try:
        # Run inside the page folder and pass "." so homr only sees relative ASCII paths:
        # OpenCV's imread/imwrite cannot open Windows paths with Chinese characters.
        proc = subprocess.Popen(
            _command(Path(".")),
            cwd=str(pages_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except OSError as exc:
        raise PdfOmrError("乐谱识别组件未就绪") from exc
    assert proc.stdout is not None
    lines: queue.Queue[str | None] = queue.Queue()

    def _pump(stream=proc.stdout) -> None:
        try:
            for item in stream:
                lines.put(item)
        except (OSError, ValueError):
            pass
        finally:
            lines.put(None)

    # Lines are read on a helper thread so that 取消 still works while homr prints nothing.
    threading.Thread(target=_pump, name="homr-output", daemon=True).start()
    output: list[str] = []
    while True:
        if cancel.is_set():
            proc.kill()
            raise CancelledError()
        try:
            line = lines.get(timeout=0.2)
        except queue.Empty:
            continue
        if line is None:
            break
        output.append(line)
        page = _page_of(line)
        if page is not None:
            progress(f"识别第 {page}/{total} 页（准确度有限）", 0.3 + 0.5 * (page - 1) / max(1, total))
            continue
        # homr downloads a model itself only if _ensure_homr_models did not provide it
        downloaded = _HOMR_DOWNLOADED.search(line)
        if downloaded:
            progress(f"下载识别模型（仅第一次）· {downloaded.group(1)}/{downloaded.group(2)} MB", 0.25)
    while proc.poll() is None:  # output closed: homr is exiting
        if cancel.is_set():
            proc.kill()
            raise CancelledError()
        time.sleep(0.05)
    text = "".join(output)
    if proc.returncode != 0:
        low = text.lower()
        if "no module named" in low and "homr" in low:
            raise PdfOmrError("乐谱识别组件未就绪")
        if "download" in low or "urlerror" in low or "timed out" in low:
            raise PdfOmrError(DOWNLOAD_FAILED)
        raise PdfOmrError("没有识别出乐谱，请换一份更清晰的印刷五线谱 PDF")
    return text


def _copista_command(target: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--copista", str(target)]
    return [sys.executable, "-m", "app", "--copista", str(target)]


def _run_copista(pages_dir: Path, total: int, cancel: Event, progress: Progress) -> str:
    from app import copista_models
    from app.copista_runtime import PAGE_MARKER

    try:
        copista_models.ensure_models(cancel, lambda message, fraction: progress(message, .2 + .1 * fraction))
    except copista_models.ModelDownloadError as exc:
        raise PdfOmrError(str(exc)) from exc
    if cancel.is_set():
        raise CancelledError()
    env = os.environ.copy()
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    # The worker has its own CWD: no adjacent user sidecars or stale detection caches.
    proc = subprocess.Popen(
        _copista_command(Path(".")), cwd=str(pages_dir), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert proc.stdout is not None
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        try:
            for line in proc.stdout:
                lines.put(line)
        finally:
            lines.put(None)

    threading.Thread(target=pump, daemon=True, name="copista-output").start()
    output = []
    progress("加载 Copista 28m 识谱模型", .3)
    try:
        while True:
            if cancel.is_set():
                raise CancelledError()
            try:
                line = lines.get(timeout=.2)
            except queue.Empty:
                continue
            if line is None:
                break
            output.append(line)
            if line.startswith(PAGE_MARKER):
                page = int(line[len(PAGE_MARKER):].split("/", 1)[0])
                progress(f"Copista 28m 识别第 {page}/{total} 页", .3 + .5 * (page - 1) / max(1, total))
        while proc.poll() is None:
            if cancel.is_set():
                raise CancelledError()
            time.sleep(.05)
        text = "".join(output)
        (pages_dir.parent / "copista.log").write_text(text, encoding="utf-8")
        if proc.returncode:
            if "No module named" in text or "operator torchvision::nms" in text:
                raise PdfOmrError("Copista 识谱组件不完整，请重新安装软件；源码环境请检查 torch/torchvision 版本")
            raise PdfOmrError("Copista 没有完成整曲识别，请检查谱面清晰度；详情见输出目录 copista.log")
        return text
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()


def _silent_copy(measure: ET.Element) -> ET.Element:
    """Same timing as ``measure`` but every note turned into a rest (pads a missing part)."""
    copy = copy_module.deepcopy(measure)
    for child in list(copy):
        if child.tag != "note":
            if child.tag not in {"attributes", "backup", "forward"}:
                copy.remove(child)
            continue
        if child.find("chord") is not None or child.find("grace") is not None:
            copy.remove(child)
            continue
        for tag in ("pitch", "unpitched", "tie", "notations", "lyric", "beam", "stem"):
            for sub in child.findall(tag):
                child.remove(sub)
        if child.find("rest") is None:
            child.insert(0, ET.Element("rest"))
    return copy


def _has_notes(root: ET.Element) -> bool:
    return any(n.find("pitch") is not None for n in root.iter("note"))


def _pitch_key(note_el: ET.Element) -> tuple[str, int, int] | None:
    pitch = note_el.find("pitch")
    if pitch is None:
        return None
    step = (pitch.findtext("step") or "").strip()
    octave = int((pitch.findtext("octave") or "0").strip() or 0)
    alter = int(float((pitch.findtext("alter") or "0").strip() or 0))
    return step, octave, alter


def _slur_types(note_el: ET.Element) -> set[str]:
    notations = note_el.find("notations")
    if notations is None:
        return set()
    return {s.get("type", "") for s in notations.findall("slur")}


def _add_tie(note_el: ET.Element, kind: str) -> None:
    if any(t.get("type") == kind for t in note_el.findall("tie")):
        return
    tie = ET.Element("tie", {"type": kind})
    children = list(note_el)
    index = len(children)
    for i, child in enumerate(children):
        if child.tag == "duration":
            index = i + 1
            break
    note_el.insert(index, tie)
    notations = note_el.find("notations")
    if notations is None:
        notations = ET.SubElement(note_el, "notations")
    notations.append(ET.Element("tied", {"type": kind}))


def slurs_to_ties(root: ET.Element) -> int:
    """homr writes ties as slurs. A slur from a note to the very next note of the same pitch
    in the same voice can be a tie only if their XML sounding intervals touch.

    XML list neighbours need not be adjacent in time: backups and forwards
    interleave voices, and a missing rest can leave a silent gap. Never make
    a tie span that gap or remove the later attack.
    """
    converted = 0
    for part in root.findall("part"):
        sequences: dict[tuple[str, str], list[list[ET.Element]]] = {}
        timings: dict[int, tuple[Fraction, Fraction]] = {}
        divisions = Fraction(1)
        meter_length = Fraction(4)
        part_start = Fraction(0)
        for measure in part.findall("measure"):
            cursor = extent = last_onset = Fraction(0)
            valid_clock = True
            measure_notes: list[ET.Element] = []
            for note_el in measure:
                if note_el.tag == "attributes":
                    raw_divisions = note_el.findtext("divisions")
                    signature = note_el.find("time")
                    try:
                        if raw_divisions is not None:
                            candidate_divisions = Fraction(raw_divisions)
                            if candidate_divisions <= 0:
                                raise ValueError("invalid divisions")
                            divisions = candidate_divisions
                        if signature is not None:
                            beats = sum(Fraction(n) for n in (signature.findtext("beats") or "4").split("+"))
                            denominator = Fraction(signature.findtext("beat-type") or "4")
                            candidate_meter = 4 * beats / denominator
                            if candidate_meter <= 0:
                                raise ValueError("invalid time signature")
                            meter_length = candidate_meter
                    except (ValueError, ZeroDivisionError):
                        valid_clock = False
                    continue
                if note_el.tag in {"backup", "forward"}:
                    try:
                        amount = Fraction(note_el.findtext("duration") or "0") / divisions
                        cursor += amount if note_el.tag == "forward" else -amount
                        if cursor < 0 or amount < 0:
                            valid_clock = False
                        if note_el.tag == "forward":
                            extent = max(extent, cursor)
                    except (ValueError, ZeroDivisionError):
                        valid_clock = False
                    continue
                if note_el.tag != "note":
                    continue
                if note_el.find("grace") is not None:
                    continue
                measure_notes.append(note_el)
                chord = note_el.find("chord") is not None
                try:
                    duration = Fraction(note_el.findtext("duration") or "0") / divisions
                    if duration < 0:
                        raise ValueError("invalid duration")
                    onset = last_onset if chord else cursor
                    if not chord:
                        last_onset = onset
                        cursor += duration
                    extent = max(extent, onset + duration, cursor)
                    if valid_clock:
                        timings[id(note_el)] = (part_start + onset, part_start + onset + duration)
                except (ValueError, ZeroDivisionError):
                    valid_clock = False
                voice = (note_el.findtext("staff") or "1", note_el.findtext("voice") or "1")
                seq = sequences.setdefault(voice, [])
                if chord and seq:
                    seq[-1].append(note_el)
                else:
                    seq.append([note_el])
            if not valid_clock:
                for note_el in measure_notes:
                    timings.pop(id(note_el), None)
            # An explicit pickup may be short. Otherwise leave room for the
            # whole bar, including an empty/missing-rest bar; overfull HOMR
            # measures still retain their actual written span.
            span = extent if measure.get("implicit") == "yes" and extent > 0 else max(extent, meter_length)
            part_start += span
        for seq in sequences.values():
            for before, after in zip(seq, seq[1:]):
                starts = set().union(*(_slur_types(n) for n in before))
                stops = set().union(*(_slur_types(n) for n in after))
                if "start" not in starts or "stop" not in stops:
                    continue
                after_by_pitch = {_pitch_key(n): n for n in after if _pitch_key(n) is not None}
                for n in before:
                    key = _pitch_key(n)
                    if key is None or key not in after_by_pitch:
                        continue
                    target = after_by_pitch[key]
                    before_time = timings.get(id(n))
                    after_time = timings.get(id(target))
                    if (before_time is None or after_time is None
                            or abs(before_time[1] - after_time[0]) > Fraction(1, 1_000_000)):
                        continue
                    _add_tie(n, "start")
                    _add_tie(target, "stop")
                    converted += 1
    return converted


def merge_pages(xmls: list[Path], dest: Path, *, homr_cleanup: bool = True, strict: bool = False) -> Path:
    """Join homr's per-page MusicXML files into one score by appending measures page after page.

    Works on the raw XML so homr's output is kept exactly (re-exporting through music21
    fails on some recognised tuplets).
    """
    base: ET.ElementTree | None = None
    base_parts: list[ET.Element] = []
    # Reference measures of every page merged so far: used to pad a part that is missing on
    # some pages (homr can split the same layout into 1 part on one page and 2 on the next).
    page_references: list[list[ET.Element]] = []
    for xml in xmls:
        try:
            tree = ET.parse(str(xml))
        except (ET.ParseError, OSError):
            if strict:
                raise PdfOmrError(f"识别页面无法读取：{xml.name}，未交付缺页结果") from None
            continue
        root = tree.getroot()
        if root.tag != "score-partwise" or not _has_notes(root):
            if strict:
                raise PdfOmrError(f"识别页面没有音符：{xml.name}，未交付缺页结果")
            continue
        parts = root.findall("part")
        if not parts:
            continue
        reference = parts[0].findall("measure")
        if base is None:
            base = tree
            base_parts = parts
            page_references.append(reference)
            continue
        base_root = base.getroot()
        if strict and len(parts) != len(base_parts):
            raise PdfOmrError("各页识别的乐器分组不一致，无法可靠合并；原始各页 XML 已保留")
        while len(base_parts) < len(parts):
            new_id = f"P{len(base_parts) + 1}"
            while any(p.get("id") == new_id for p in base_parts):
                new_id += "x"
            part_list = base_root.find("part-list")
            if part_list is not None:
                score_part = ET.SubElement(part_list, "score-part", {"id": new_id})
                ET.SubElement(score_part, "part-name").text = f"Part {len(base_parts) + 1}"
            new_part = ET.Element("part", {"id": new_id})
            for earlier in page_references:
                for measure in earlier:
                    new_part.append(_silent_copy(measure))
            base_root.append(new_part)
            base_parts.append(new_part)
        for index, target in enumerate(base_parts):
            source = parts[index] if index < len(parts) else None
            measures = source.findall("measure") if source is not None else []
            if source is None:
                measures = [_silent_copy(m) for m in reference]
            for measure in measures:
                target.append(measure)
        page_references.append(reference)
    if base is None:
        raise PdfOmrError("没有识别出乐谱，请换一份更清晰的印刷五线谱 PDF")
    for part in base_parts:
        for number, measure in enumerate(part.findall("measure"), start=1):
            measure.set("number", str(number))
    if homr_cleanup:
        slurs_to_ties(base.getroot())
        from app.omr_repeats import normalize_homr_repeats

        normalize_homr_repeats(base.getroot())
    base.write(str(dest), encoding="UTF-8", xml_declaration=True)
    return dest


def count_staves(xml: Path) -> int:
    """Total staves per system in a recognised score (2 = normal piano grand staff)."""
    try:
        root = ET.parse(str(xml)).getroot()
    except (ET.ParseError, OSError):
        return 0
    total = 0
    for part in root.findall("part"):
        staves = {n.findtext("staff") or "1" for n in part.iter("note")}
        total += max(1, len(staves))
    return total


def _is_jianpu(images: list[Path]) -> bool:
    """Decide from the first pages: jianpu has digit rows and bar lines but no staff lines."""
    from app.jianpu import looks_like_jianpu

    sample = images[: min(2, len(images))]
    try:
        return any(looks_like_jianpu(img) for img in sample)
    except Exception:  # noqa: BLE001
        return False


def _recognize_jianpu(images: list[Path], dest: Path, cancel: Event, progress: Progress) -> Path:
    from app import jianpu

    reader = jianpu._reader()
    pages = []
    for number, img in enumerate(images, start=1):
        if cancel.is_set():
            raise CancelledError()
        progress(f"识别简谱第 {number}/{len(images)} 页（准确度有限）", 0.3 + 0.5 * (number - 1) / max(1, len(images)))
        try:
            pages.append(jianpu.read_page(img, reader))
        except jianpu.JianpuError:
            continue
    try:
        score = jianpu.build_score(pages)
    except jianpu.JianpuError as exc:
        raise PdfOmrError("没有识别出简谱，请换一份更清晰的印刷简谱") from exc
    return jianpu.write_midi(score, dest)


VECTOR_SUFFIX = "_vector.mid"
JIANPU_SUFFIX = "_jianpu.mid"


def _archive_homr_xml(xmls: list[Path], work_dir: Path, *, engine: str = "homr", runtime: dict | None = None) -> Path:
    """Keep the recognizer's bytes before any XML cleanup, without page bitmaps."""
    from importlib import metadata

    pages = [(xml, xml.read_bytes()) for xml in xmls]
    fingerprints = [hashlib.sha256(data).hexdigest() for _xml, data in pages]
    run_id = hashlib.sha256(''.join(fingerprints).encode('ascii')).hexdigest()[:16]
    if engine != "homr":
        run_id = engine + "-" + run_id
    archive = work_dir / '_omr_raw' / run_id
    archive.mkdir(parents=True, exist_ok=True)
    records = []
    for index, ((xml, data), digest) in enumerate(zip(pages, fingerprints), start=1):
        target = archive / xml.name
        if target.exists() and target.read_bytes() != data:
            raise PdfOmrError('原始识别记录发生冲突，请使用新的输出目录')
        target.write_bytes(data)
        records.append(dict(page=index, file=xml.name, sha256=digest))
    try:
        version = metadata.version('homr') if engine == 'homr' else '1.3.4'
    except metadata.PackageNotFoundError:
        version = 'unknown'
    (archive / 'manifest.json').write_text(json.dumps(dict(
        schema_version=1, engine=engine, engine_version=version,
        stage='before merge, engine-specific normalization and tempo supplementation',
        runtime=runtime or {},
        pages=records, quality_report='quality.json',
    ), ensure_ascii=False, indent=2), encoding='utf-8')
    from app.omr_quality import write_quality_report
    write_quality_report([archive / record['file'] for record in records], archive / 'quality.json')
    return archive


def _read_vector(pdf: Path, dest: Path, cancel: Event, progress: Progress) -> Path | None:
    """Notes straight from the PDF drawing (exact) when the PDF draws its symbols with a
    music font; None when it does not, so image recognition is used instead."""
    from app import pdf_vector

    if pdf.suffix.lower() not in PDF_ONLY_SUFFIXES or not pdf_vector.is_vector_score(pdf):
        return None
    progress("读取 PDF 谱面", 0.4)
    try:
        # Vector pages are read incrementally; the raster-memory page cap must
        # not silently truncate long notation PDFs.
        score = pdf_vector.read_vector_pdf(pdf, max_pages=None, cancel=cancel)
    except pdf_vector.VectorScoreError:
        return None
    return pdf_vector.write_midi(score, dest)


def recognize_pdf(pdf: Path, work_dir: Path, cancel: Event, on_progress: Progress | None = None, *, engine: str | None = None) -> Path:
    """Recognise a score PDF / image.

    Returns MIDI read from the PDF's vector drawing when possible (``*_vector.mid``), MIDI for
    jianpu (``*_jianpu.mid``), otherwise MusicXML from image recognition of staff notation.
    """
    progress = on_progress or (lambda _m, _p: None)
    engine = engine or DEFAULT_OMR_ENGINE
    if engine not in {"copista-28m", "homr"}:
        raise PdfOmrError("未知的图像识谱引擎")
    work_dir.mkdir(parents=True, exist_ok=True)
    vector = _read_vector(pdf, work_dir / f"{pdf.stem}{VECTOR_SUFFIX}", cancel, progress)
    if vector is not None:
        return vector
    pages_dir = work_dir / "_omr_pages"
    if pages_dir.exists():
        shutil.rmtree(pages_dir, ignore_errors=True)
    try:
        progress("准备页面", 0.2)
        images = prepare_pages(pdf, pages_dir)
        if cancel.is_set():
            raise CancelledError()
        if _is_jianpu(images):
            return _recognize_jianpu(images, work_dir / f"{pdf.stem}{JIANPU_SUFFIX}", cancel, progress)
        if engine == "copista-28m":
            _run_copista(pages_dir, len(images), cancel, progress)
        else:
            _ensure_homr_models(cancel, progress)
            _run_homr(pages_dir, len(images), cancel, progress)
        page_xmls = [img.with_suffix(".musicxml") for img in images]
        if engine == "copista-28m" and not all(xml.is_file() for xml in page_xmls):
            _archive_homr_xml([xml for xml in page_xmls if xml.is_file()], work_dir, engine=engine)
            raise PdfOmrError("Copista 未识别完所有页面，未交付缺页结果；已完成的各页 XML 已保留")
        page_xmls = [xml for xml in page_xmls if xml.is_file()]
        if not page_xmls:
            raise PdfOmrError("没有识别出乐谱，请换一份更清晰的印刷五线谱 PDF")
        run_info = pages_dir / "copista-run.json"
        runtime = json.loads(run_info.read_text(encoding="utf-8")) if engine == "copista-28m" and run_info.is_file() else {}
        _archive_homr_xml(page_xmls, work_dir, engine=engine, runtime=runtime)
        (work_dir / "_omr_engine.json").write_text(json.dumps(dict(engine=engine, runtime=runtime), ensure_ascii=False, indent=2), encoding="utf-8")
        progress("合并各页", 0.8)
        merged = merge_pages(page_xmls, work_dir / f"{pdf.stem}_merged.musicxml", homr_cleanup=engine == "homr", strict=engine == "copista-28m")
        from app.omr_tempo import supplement_opening_tempo

        try:
            supplement_opening_tempo(merged, images[0])
        except Exception as exc:  # optional OCR metadata must not discard recognised notes
            import logging
            logging.getLogger(__name__).warning("Opening tempo could not be read: %s", exc)
        return merged
    except Exception:
        if engine == "copista-28m":
            completed = sorted(pages_dir.glob("page_*.musicxml"))
            if completed:
                info = pages_dir / "copista-run.json"
                runtime = json.loads(info.read_text(encoding="utf-8")) if info.is_file() else {}
                _archive_homr_xml(completed, work_dir, engine=engine, runtime=runtime)
        raise
    finally:
        shutil.rmtree(pages_dir, ignore_errors=True)
