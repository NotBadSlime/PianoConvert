"""Pinned Copista 28m weights, shipped with builds or cached per Windows user."""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path
from threading import Event
from typing import Callable
from urllib.request import Request, urlopen

from app.engines.base import CancelledError

REVISION = "44464d11234a22ba23634ec8eb1e31e3b012021a"
BASE_URL = f"https://huggingface.co/kobimusic/copista-28m/resolve/{REVISION}/"
# name: (bytes, SHA-256). Never unpickle an unverified checkpoint.
FILES = {
    "evidence-2m.pt": (8145299, "7a3eb0362b44016e941680c5df1e133239576592ab134057c3dbac8e9f3b8838"),
    "small/v7_obj-recall-30m_best.pt": (115352175, "9f1db282271fb1064461387da4a2e15de14b099c49b619c21429cdc45be85cfe"),
}
TOTAL_BYTES = sum(size for size, _digest in FILES.values())
Progress = Callable[[str, float], None]


class ModelDownloadError(RuntimeError):
    pass


def shipped_dir() -> Path:
    if getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS) / "copista-models"
    return Path(__file__).resolve().parent.parent / "models" / "copista-28m"


def cache_dir() -> Path:
    override = os.environ.get("PIANOCONVERT_COPISTA_MODELS")
    if override:
        return Path(override)
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    return base / "PianoConvert" / "copista-28m"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def valid(path: Path, name: str, *, checksum: bool = True) -> bool:
    size, digest = FILES[name]
    try:
        return path.is_file() and path.stat().st_size == size and (not checksum or sha256(path) == digest)
    except OSError:
        return False


def resolve(name: str) -> Path:
    if name not in FILES:
        raise ValueError(f"Unknown Copista checkpoint: {name}")
    shipped = shipped_dir() / name
    return shipped if valid(shipped, name, checksum=False) else cache_dir() / name


def missing_shipped() -> list[str]:
    return [name for name in FILES if not valid(shipped_dir() / name, name, checksum=False)]


def missing() -> list[str]:
    return [name for name in FILES if not valid(resolve(name), name, checksum=False)]


def _download(name: str, target: Path, cancel: Event, report: Progress) -> None:
    if cancel.is_set():
        raise CancelledError()
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    size, _digest = FILES[name]
    offset = partial.stat().st_size if partial.exists() else 0
    if offset >= size:
        if valid(partial, name):
            partial.replace(target)
            report(name, 1.0)
            return
        partial.unlink()
        offset = 0
    if cancel.is_set():
        raise CancelledError()
    headers = {"User-Agent": "PianoConvert-Copista/1.0"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    with urlopen(Request(BASE_URL + name, headers=headers), timeout=15) as response:
        if offset and response.status != 206:
            offset = 0  # server ignored Range: replace, never append a second file
        if response.status == 206:
            content_range = response.headers.get("Content-Range", "")
            if not content_range.startswith(f"bytes {offset}-"):
                raise ModelDownloadError("模型下载的续传位置不正确，请重试")
        with partial.open("ab" if offset else "wb") as stream:
            count = offset
            while True:
                if cancel.is_set():
                    raise CancelledError()
                block = response.read(1 << 18)
                if not block:
                    break
                stream.write(block)
                count += len(block)
                report(name, min(1.0, count / size))
                if count > size:
                    raise ModelDownloadError("识别模型大小异常，请重试")
    if cancel.is_set():
        raise CancelledError()
    if not valid(partial, name):
        # Retain incomplete bytes for a later Range request, discard only a complete bad hash.
        if partial.stat().st_size >= size:
            partial.unlink()
        raise ModelDownloadError("识别模型未下载完整或校验失败，请重试")
    partial.replace(target)


def ensure_models(cancel: Event, progress: Progress | None = None, *, into_package: bool = False) -> list[Path]:
    report = progress or (lambda _name, _fraction: None)
    result = []
    for index, name in enumerate(FILES):
        if cancel.is_set():
            raise CancelledError()
        target = shipped_dir() / name if into_package else resolve(name)
        if not valid(target, name):
            # A bad shipped file is replaced in the writable user cache, never the install directory.
            if not into_package:
                target = cache_dir() / name
            def update(_name: str, fraction: float) -> None:
                report(f"下载 Copista 28m 模型（{index + 1}/{len(FILES)}）", (index + fraction) / len(FILES))
            try:
                if not valid(target, name):
                    _download(name, target, cancel, update)
            except (OSError, ValueError) as exc:
                raise ModelDownloadError("无法下载 Copista 模型，请检查网络后重试；下次会继续下载") from exc
        result.append(target)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", action="store_true", help="Put verified weights in the source build's models folder")
    args = parser.parse_args()
    paths = ensure_models(Event(), lambda message, fraction: print(f"{message} {fraction:.0%}", flush=True), into_package=args.package)
    for path in paths:
        print(path, flush=True)


if __name__ == "__main__":
    main()
