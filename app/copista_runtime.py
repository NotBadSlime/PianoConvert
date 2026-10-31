"""Copista's unmodified reader, isolated from the GUI in a cancellable subprocess."""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path

from app import copista_models

SOURCE_REVISION = "c685f280914d91a694494588d9f6a376d7917eba"
PAGE_MARKER = "PIANOCONVERT_COPISTA_PAGE "
FAILED = "PIANOCONVERT_COPISTA_FAILED"


def vendor_dir() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return root / "third_party" / "copisteria"


def activate() -> None:
    source = vendor_dir() / "src"
    if not (source / "copisteria" / "read.py").is_file():
        raise RuntimeError("缺少 Copista 识谱组件，请重新安装软件")
    # Prefer the pinned snapshot, never an arbitrary globally installed copisteria.
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))


def load_models(device: str = "auto"):
    from functools import partial
    activate()
    import torch
    from copisteria import paths, read
    from copisteria.detect import Detector
    from copisteria import detect, pipeline

    device = ("cuda" if torch.cuda.is_available() else "cpu") if device == "auto" else device
    if device not in {"cpu", "cuda"}:
        raise ValueError("Unknown Copista device")
    # Verify bytes before torch.load(weights_only=False), including in --copista mode.
    files = []
    for name in copista_models.FILES:
        candidates = [copista_models.resolve(name), copista_models.cache_dir() / name]
        verified = next((p for p in candidates if copista_models.valid(p, name)), None)
        if verified is None:
            raise RuntimeError("Copista 模型缺失或校验失败，请重新下载模型")
        files.append(verified)
    evidence, detector = files
    paths.DETECTORS["28m"] = (detector, "v7")
    os.environ["COPISTERIA_DETECTOR"] = "28m"
    detect.Detector = partial(Detector, device=device)
    model = read.load_model(evidence, device)
    state = torch.load(evidence, map_location="cpu", weights_only=False)
    model.load_state_dict(state["model"], strict=True)
    del state
    # inputs() reuses this detector for feature extraction. Explicit CPU keeps the
    # detector and evidence model together even on a machine with a CUDA runtime.
    pipeline._DET[:] = [Detector(detector, device=device)]
    return model, device


def run(pages_dir: Path, device: str = "auto") -> None:
    import torch

    torch.set_num_threads(min(4, os.cpu_count() or 1))
    pages = sorted(pages_dir.glob("page_*.png"))
    if not pages:
        raise ValueError("没有可识别的页面")
    model, device = load_models(device)
    from copisteria.pipeline import transcribe

    rows = []
    metadata = dict(engine="copista-28m", engine_version="1.3.4", source_revision=SOURCE_REVISION,
                    weights_revision=copista_models.REVISION, weights_sha256={n: v[1] for n, v in copista_models.FILES.items()},
                    device=device, torch=torch.__version__, pages=rows)
    for index, image in enumerate(pages, 1):
        print(PAGE_MARKER + f"{index}/{len(pages)}", flush=True)
        start = time.monotonic()
        # No adjacent user files, sidecar texts, detections or references enter this folder.
        xml = image.with_suffix(".musicxml")
        xml.write_text(transcribe(image, model, device=device), encoding="utf-8")
        root = ET.parse(xml).getroot()
        if root.tag != "score-partwise" or not root.findall("part/measure/note/pitch"):
            raise ValueError(f"第 {index} 页没有识别出音符，请检查图片清晰度")
        rows.append(dict(page=index, input_sha256=copista_models.sha256(image), xml_sha256=copista_models.sha256(xml),
                         seconds=round(time.monotonic() - start, 3), notes=len(root.findall('part/measure/note/pitch'))))
    (pages_dir / "copista-run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def entry(pages_dir: str, device: str = "auto") -> None:
    try:
        run(Path(pages_dir), device)
    except Exception as exc:
        traceback.print_exc()
        print(f"{FAILED}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(3) from exc
