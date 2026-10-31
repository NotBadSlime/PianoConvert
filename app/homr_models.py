"""The homr models that read scans, photos and screenshots of staff notation.

homr looks for each model file in two places, in this order:

1. beside its own modules: the installer ships the models there (a dev venv may have them too);
2. the per-user cache ``%LOCALAPPDATA%\\PianoConvert\\homr-models``, filled by ``ensure_models``.

``ensure_models`` downloads whatever is missing before homr starts, so the main window can show
the progress and cancel it. Downloads resume: ``<model>.zip.part`` survives a dropped connection,
a cancel or a closed app, and the next attempt continues it. The official GitHub release is tried
first; when it fails or is too slow, GitHub mirrors that are reachable from mainland China take
over. A file is accepted only when its SHA-256 matches (mirrors are used only for files with a
known checksum).

Run ``python -m app.homr_models`` before packaging to put the models beside the homr package,
where PyInstaller collects them into the installer.
"""
from __future__ import annotations

import hashlib
import os
import sys
import time
import zipfile
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable

from app.engines.base import CancelledError

OFFICIAL_BASE = "https://github.com/liebharc/homr/releases/download/onnx_checkpoints/"
# GitHub download accelerators: each is a prefix put in front of the GitHub address. All of them
# support resuming (HTTP Range). PIANOCONVERT_MODEL_MIRRORS (comma separated) replaces the list;
# set it to an empty string to use GitHub only.
DEFAULT_MIRRORS = ("https://ghfast.top/", "https://gh-proxy.com/", "https://gh.llkk.cc/")
MIRRORS_ENV = "PIANOCONVERT_MODEL_MIRRORS"
# SHA-256 of the extracted models used by homr 0.7.0 (the CPU set the app ships). A newer homr
# names its models differently; those are then fetched from GitHub only, without a checksum.
SHA256 = {
    "segnet_308-3296ccd40960f90ca6ab9c035cca945675d30a0f.onnx":
        "6ed36640db4ef5d223098b6d5efe4eda97c66b24a2c72faab8a018c749003a8d",
    "encoder_pytorch_model_396-f6feedb42ff90087d898b0941a55d040fa6b2903.onnx":
        "4c16df852b3789f2676b0d49f0545dab0740e4005f7b472c5252add642f5d5eb",
    "decoder_pytorch_model_396-f6feedb42ff90087d898b0941a55d040fa6b2903.onnx":
        "3e10fd5ae52d0b86792721922fcd954c283a7ed365de7446425bdabe38f3e57d",
}

USER_AGENT = "PianoConvert (+https://github.com/NotBadSlime/PianoConvert)"
CONNECT_TIMEOUT = 10.0
# No data for this long counts as a dropped connection; the next attempt resumes.
READ_TIMEOUT = 20.0
CHUNK = 64 * 1024
# While some source has not been tried yet, one that stays slower than MIN_SPEED for
# PROBE_SECONDS is left for the next one (the download continues where it stopped). Once every
# source has been measured the fastest one is used, however slow.
MIN_SPEED = 200 * 1024
PROBE_SECONDS = 12.0
# Give up after this many attempts in a row that got no further, per source.
FAILURES_PER_SOURCE = 2
RETRY_PAUSE = 2.0
REPORT_INTERVAL = 0.25


class ModelDownloadError(Exception):
    pass


class _TooSlow(Exception):
    def __init__(self, speed: float) -> None:
        super().__init__(f"{speed / 1024:.0f} KB/s")
        self.speed = speed


@dataclass(frozen=True)
class DownloadStatus:
    index: int  # 1-based number of the file being downloaded
    count: int  # files to download in this run
    name: str
    done: int  # bytes of this file so far
    total: int | None  # size of this file, when the server tells

    @property
    def fraction(self) -> float:
        """Share of the whole run that is done, 0..1."""
        part = min(1.0, self.done / self.total) if self.total else 0.0
        return max(0.0, min(1.0, (self.index - 1 + part) / max(1, self.count)))


StatusCb = Callable[[DownloadStatus], None]


def status_text(status: DownloadStatus) -> str:
    """What the main window shows while downloading, e.g. 下载识别模型（仅第一次）2/3 · 23/46 MB."""
    done = _mb(status.done)
    size = f"{done}/{_mb(status.total)} MB" if status.total else f"{done} MB"
    return f"下载识别模型（仅第一次）{status.index}/{status.count} · {size}"


def _mb(size: int | None) -> int:
    return round((size or 0) / (1024 * 1024))


# ---------------------------------------------------------------- where the models live


def cache_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    return base / "PianoConvert" / "homr-models"


def package_paths() -> dict[str, Path]:
    """Where homr itself expects every model file (beside its modules), by file name.

    Built from homr's constants rather than its path variables, which ``pdf_omr`` redirects.
    """
    import homr.segmentation.config as seg
    from homr.transformer.configs import FilePaths

    seg_dir = Path(os.path.realpath(seg.__file__)).parent
    files = FilePaths()
    paths = [
        seg_dir / f"{seg.model_name}.onnx",
        seg_dir / f"{seg.model_name}_fp16.onnx",
        Path(files.encoder_path),
        Path(files.decoder_path),
        Path(files.encoder_path_fp16),
        Path(files.decoder_path_fp16),
    ]
    return {path.name: path for path in paths}


def resolve(name: str) -> Path:
    """The file homr should load: the shipped copy when there is one, else the cached copy."""
    shipped = package_paths().get(name)
    if shipped is not None and shipped.is_file():
        return shipped
    return cache_dir() / name


def detect_accelerators() -> tuple[bool, bool]:
    """(CUDA, CoreML) as homr sees them. The packaged app runs homr on the CPU: neither."""
    try:
        from homr.onnx_providers import coreml_available, cuda_available

        return bool(cuda_available()), bool(coreml_available())
    except Exception:  # noqa: BLE001
        return False, False


def required_names(cuda: bool = False, coreml: bool = False) -> list[str]:
    """The model files homr loads, chosen like ``homr.main``: fp32 on the CPU, fp16 on a GPU."""
    import homr.segmentation.config as seg
    from homr.transformer.configs import FilePaths

    files = FilePaths()
    segnet = f"{seg.model_name}_fp16.onnx" if (cuda or coreml) else f"{seg.model_name}.onnx"
    transformer = [files.encoder_path_fp16, files.decoder_path_fp16] if cuda else [files.encoder_path, files.decoder_path]
    return [segnet] + [Path(path).name for path in transformer]


def missing(names: list[str] | None = None) -> list[str]:
    """Model files that are neither shipped nor cached (default: the CPU set)."""
    wanted = required_names() if names is None else names
    return [name for name in wanted if not resolve(name).is_file()]


def missing_shipped() -> list[str]:
    """CPU model files not beside the homr package (a packaged app must have none missing)."""
    shipped = package_paths()
    return [name for name in required_names() if not shipped[name].is_file()]


# ---------------------------------------------------------------- download


def zip_name(name: str) -> str:
    # homr names each archive after the model file up to its first dot
    return name.split(".")[0] + ".zip"


def mirrors() -> list[str]:
    raw = os.environ.get(MIRRORS_ENV)
    if raw is None:
        return list(DEFAULT_MIRRORS)
    return [item.strip() for item in raw.split(",") if item.strip()]


def sources(name: str) -> list[str]:
    """Download addresses for one model, in the order they are tried."""
    official = OFFICIAL_BASE + zip_name(name)
    if name not in SHA256:
        return [official]
    return [official] + [prefix.rstrip("/") + "/" + official for prefix in mirrors()]


def ensure_models(
    cancel: Event,
    on_status: StatusCb | None = None,
    names: list[str] | None = None,
    into_package: bool = False,
) -> list[Path]:
    """Download the model files homr needs that are neither shipped nor cached yet.

    Returns the files it downloaded (none when everything is there). ``into_package`` puts
    them beside the homr package instead of the cache, so that packaging ships them.
    Raises CancelledError, or ModelDownloadError when no source works; what was downloaded so
    far is kept for the next attempt.
    """
    if names is None:
        names = required_names(*detect_accelerators())
    if into_package:
        shipped = package_paths()
        targets = {name: shipped[name] for name in names}
        todo = [name for name in names if not targets[name].is_file()]
    else:
        targets = {name: cache_dir() / name for name in names}
        todo = [name for name in names if not resolve(name).is_file()]
    report = on_status or (lambda _status: None)
    downloaded: list[Path] = []
    preferred = 0  # the next file starts with the source that delivered the last one
    for index, name in enumerate(todo, start=1):
        if cancel.is_set():
            raise CancelledError()
        preferred = _download_one(name, targets[name], index, len(todo), cancel, report, preferred)
        downloaded.append(targets[name])
    return downloaded


class _Reporter:
    def __init__(self, name: str, index: int, count: int, on_status: StatusCb) -> None:
        self.name, self.index, self.count = name, index, count
        self.on_status = on_status
        self.last = 0.0
        self.total: int | None = None

    def bytes(self, done: int, total: int | None) -> None:
        self.total = total or self.total
        now = time.monotonic()
        if now - self.last < REPORT_INTERVAL:
            return
        self.last = now
        self.on_status(DownloadStatus(self.index, self.count, self.name, done, self.total))

    def finished(self, size: int) -> None:
        self.on_status(DownloadStatus(self.index, self.count, self.name, size, size))


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _download_one(
    name: str, target: Path, index: int, count: int, cancel: Event, on_status: StatusCb, preferred: int
) -> int:
    """Download and install one model; returns the index of the source that delivered it."""
    urls = sources(name)
    order = [(preferred + step) % len(urls) for step in range(len(urls))]
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(zip_name(name) + ".part")
    reporter = _Reporter(name, index, count, on_status)
    slow: dict[int, float] = {}  # sources found too slow, with their speed
    broken: set[int] = set()  # sources whose last attempt got nowhere
    probing = len(urls) > 1
    current = order[0]
    failures = 0
    while True:
        probe = PROBE_SECONDS if probing and current not in slow else None
        before = _size(part)
        try:
            _fetch(urls[current], part, cancel, reporter.bytes, probe)
            size = _size(part)
            _install(part, name, target)
            reporter.finished(size)
            return current
        except CancelledError:
            raise
        except _TooSlow as exc:
            slow[current] = exc.speed
            fresh = [i for i in order if i not in slow and i not in broken]
            if fresh:
                current = fresh[0]
            else:  # every source that works is slow: settle for the fastest
                probing = False
                current = max(slow, key=slow.__getitem__)
            continue
        except Exception as exc:  # noqa: BLE001  (network, HTTP status, damaged archive, disk)
            last_error = exc
        if _size(part) > before:
            # it got further before the connection dropped: carry on with this source
            failures = 0
            broken.discard(current)
        else:
            failures += 1
            if failures >= FAILURES_PER_SOURCE * len(urls):
                raise ModelDownloadError(f"{name}: {last_error}") from last_error
            broken.add(current)
            if len(broken) >= len(order):
                broken.clear()  # every source failed once: go round again
            start = order.index(current)
            after = [order[(start + step) % len(order)] for step in range(1, len(order) + 1)]
            usable = [i for i in after if i not in broken]
            fresh = [i for i in usable if i not in slow] if probing else []
            current = (fresh or usable or after)[0]
        if cancel.wait(RETRY_PAUSE):
            raise CancelledError()


def _content_range(value: str | None) -> tuple[int | None, int | None]:
    """(first byte, total size) from a Content-Range header such as ``bytes 100-199/1000``."""
    if not value or not value.startswith("bytes "):
        return None, None
    span, _, total = value[len("bytes ") :].partition("/")
    first = span.split("-")[0]
    return (
        int(first) if first.isdigit() else None,
        int(total) if total.isdigit() else None,
    )


def _fetch(
    url: str,
    part: Path,
    cancel: Event,
    on_bytes: Callable[[int, int | None], None],
    probe: float | None,
) -> None:
    """Download ``url`` into ``part``, continuing after the bytes already there.

    Raises _TooSlow when ``probe`` is given and the speed over the last ``probe`` seconds stays
    below MIN_SPEED; CancelledError; ModelDownloadError or a requests error when it fails.
    """
    import requests

    have = _size(part)
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    if have:
        headers["Range"] = f"bytes={have}-"
    with requests.get(url, headers=headers, stream=True, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as response:
        if response.status_code == 416:  # nothing after `have`: complete already, or a bad part
            _first, total = _content_range(response.headers.get("Content-Range"))  # "bytes */size"
            if have and total == have:
                return
            part.unlink(missing_ok=True)
            raise ModelDownloadError("续传位置无效，重新下载")
        if response.status_code not in (200, 206):
            raise ModelDownloadError(f"HTTP {response.status_code}")
        if response.status_code == 206:
            first, total = _content_range(response.headers.get("Content-Range"))
            if first != have:
                part.unlink(missing_ok=True)
                raise ModelDownloadError("续传位置不一致，重新下载")
            mode = "ab"
        else:  # the whole file: this source ignores Range, start over
            have, mode = 0, "wb"
            length = response.headers.get("Content-Length", "")
            total = int(length) if length.isdigit() else None
        on_bytes(have, total)
        started = time.monotonic()
        samples = deque([(started, have)])
        with open(part, mode) as out:
            for chunk in response.iter_content(CHUNK):
                if cancel.is_set():
                    raise CancelledError()
                if not chunk:
                    continue
                out.write(chunk)
                have += len(chunk)
                on_bytes(have, total)
                if probe is None:
                    continue
                now = time.monotonic()
                samples.append((now, have))
                while len(samples) > 2 and now - samples[1][0] >= probe:
                    samples.popleft()
                if now - started >= probe:
                    then, had = samples[0]
                    speed = (have - had) / max(now - then, 1e-6)
                    if speed < MIN_SPEED:
                        raise _TooSlow(speed)
    if cancel.is_set():
        raise CancelledError()
    if total is not None and have < total:
        raise ModelDownloadError("下载中断")


def _install(part: Path, name: str, target: Path) -> None:
    """Extract the model from the downloaded archive, check it and move it into place."""
    tmp = target.with_name(target.name + ".tmp")
    try:
        with zipfile.ZipFile(part) as archive:
            members = [m for m in archive.namelist() if not m.endswith("/")]
            exact = [m for m in members if m.replace("\\", "/").rsplit("/", 1)[-1] == name]
            onnx = [m for m in members if m.lower().endswith(".onnx")]
            member = exact[0] if exact else (onnx[0] if len(onnx) == 1 else None)
            if member is None:
                raise ModelDownloadError(f"压缩包里没有 {name}")
            digest = hashlib.sha256()
            # ZipExtFile checks the CRC when it reaches the end: a damaged download raises here
            with archive.open(member) as src, open(tmp, "wb") as out:
                while True:
                    block = src.read(1 << 20)
                    if not block:
                        break
                    digest.update(block)
                    out.write(block)
        expected = SHA256.get(name)
        if expected and digest.hexdigest() != expected:
            raise ModelDownloadError(f"{name} 校验失败")
        os.replace(tmp, target)
    except Exception as exc:  # noqa: BLE001  (bad zip, bad CRC / deflate data, checksum, disk)
        part.unlink(missing_ok=True)  # damaged or wrong: the next attempt starts from scratch
        raise ModelDownloadError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)
    part.unlink(missing_ok=True)


# ---------------------------------------------------------------- command line (packaging)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m app.homr_models",
        description="Download the homr models (CPU set) used to read scans, photos and screenshots.",
    )
    parser.add_argument(
        "--into",
        choices=("package", "cache"),
        default="package",
        help="package: beside the homr package, so PyInstaller ships them (default); "
        "cache: the per-user folder the app downloads to",
    )
    args = parser.parse_args(argv)
    names = required_names()
    shown = {"at": 0.0}

    def _print(status: DownloadStatus) -> None:
        now = time.monotonic()
        complete = status.total is not None and status.done >= status.total
        if now - shown["at"] < 3 and not complete:
            return
        shown["at"] = now
        size = f"{_mb(status.done)}/{_mb(status.total)} MB" if status.total else f"{_mb(status.done)} MB"
        print(f"[{status.index}/{status.count}] {status.name}: {size}", flush=True)

    try:
        ensure_models(Event(), _print, names=names, into_package=args.into == "package")
    except ModelDownloadError as exc:
        print(f"homr model download failed: {exc}", file=sys.stderr, flush=True)
        return 1
    where = package_paths() if args.into == "package" else {n: cache_dir() / n for n in names}
    for name in names:
        print(f"ready: {where[name]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
