from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from threading import Event

from app.engines.base import CancelledError

PDF_SUFFIXES = {".pdf"}


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
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    dest = base / "PianoConvert" / "homr-models"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _redirect_homr_models() -> None:
    dest = model_dir()
    import homr.main as homr_main
    import homr.segmentation.config as seg
    import homr.transformer.configs as cfg

    def _move(path: str) -> str:
        return str(dest / Path(path).name)

    seg.segnet_path_onnx = _move(seg.segnet_path_onnx)
    seg.segnet_path_onnx_fp16 = _move(seg.segnet_path_onnx_fp16)
    homr_main.segnet_path_onnx = seg.segnet_path_onnx
    homr_main.segnet_path_onnx_fp16 = seg.segnet_path_onnx_fp16
    files = cfg.default_config.filepaths
    files.encoder_path = _move(files.encoder_path)
    files.decoder_path = _move(files.decoder_path)
    files.encoder_path_fp16 = _move(files.encoder_path_fp16)
    files.decoder_path_fp16 = _move(files.decoder_path_fp16)


def homr_entry(pdf_path: str) -> None:
    _redirect_homr_models()
    from homr.main import main as homr_main

    sys.argv = ["homr", "--no-title", pdf_path]
    homr_main()


def _command(pdf: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--homr", str(pdf)]
    return [
        sys.executable,
        "-c",
        "from app.pdf_omr import homr_entry; import sys; homr_entry(sys.argv[1])",
        str(pdf),
    ]


def recognize_pdf(pdf: Path, work_dir: Path, cancel: Event) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    env["PIANOCONVERT_HOMR_MODELS"] = str(model_dir())
    try:
        proc = subprocess.Popen(
            _command(pdf),
            cwd=str(work_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except OSError as exc:
        raise PdfOmrError("乐谱识别组件未就绪") from exc
    output: list[str] = []
    assert proc.stdout is not None
    while proc.poll() is None:
        if cancel.is_set():
            proc.kill()
            raise CancelledError()
        line = proc.stdout.readline()
        if line:
            output.append(line)
    rest = proc.stdout.read()
    if rest:
        output.append(rest)
    text = "".join(output)
    if proc.returncode != 0:
        low = text.lower()
        if "no module named" in low and "homr" in low:
            raise PdfOmrError("乐谱识别组件未就绪")
        if "download" in low or "urlerror" in low or "timed out" in low:
            raise PdfOmrError("无法下载识别模型，请联网后重试")
        raise PdfOmrError("没有识别出乐谱，请换一份更清晰的印刷五线谱 PDF")
    return pick_musicxml(list(work_dir.rglob("*.musicxml")))
