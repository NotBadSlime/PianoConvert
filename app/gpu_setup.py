from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from threading import Event, Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.engines.base import CancelledError

EMBED_URL = "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
TORCH_URL = "https://download.pytorch.org/whl/cu130/torch-2.14.0%2Bcu130-cp310-cp310-win_amd64.whl"
TORCHAUDIO_URL = "https://download.pytorch.org/whl/cu130/torchaudio-2.11.0%2Bcu130-cp310-cp310-win_amd64.whl"
ORT_URL = (
    "https://files.pythonhosted.org/packages/21/c9/"
    "47abd3ec1f34498224d2a8f5cc4d1445eb5cc7dee8e3644b1a972619c0d2/"
    "onnxruntime_gpu-1.23.2-cp310-cp310-win_amd64.whl"
)

DOWNLOADS = (
    ("Python", EMBED_URL, "python-embed.zip", 0.00, 0.02),
    ("PyTorch", TORCH_URL, "torch-cu130.whl", 0.02, 0.78),
    ("torchaudio", TORCHAUDIO_URL, "torchaudio-cu130.whl", 0.78, 0.80),
    ("ONNX GPU", ORT_URL, "onnxruntime-gpu.whl", 0.80, 0.96),
    ("pip", GET_PIP_URL, "get-pip.py", 0.96, 0.97),
)

_CREATE_NO_WINDOW = 0x08000000


class GpuSetupError(RuntimeError):
    pass


def runtime_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    return base / "PianoConvert" / "gpu-runtime"


def python_exe() -> Path:
    return runtime_dir() / "python.exe"


def gpu_installed() -> bool:
    return python_exe().is_file() and (runtime_dir() / "READY").is_file()


def install_script_path() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "scripts" / "install_gpu.ps1"
        if bundled.is_file():
            return bundled
    return Path(__file__).resolve().parent.parent / "scripts" / "install_gpu.ps1"


def worker_path() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "app" / "gpu_worker.py"
        if bundled.is_file():
            return bundled
    return Path(__file__).resolve().parent / "gpu_worker.py"


def piano_source_dir() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "piano_transcription_inference_src"
        if bundled.is_dir():
            return bundled
    return Path(__file__).resolve().parent.parent / "piano_transcription_inference"


def explain_text() -> str:
    return (
        "音频用 GPU 转换需要 NVIDIA 显卡，并额外下载显卡组件（大约 2.3 GB）。\n"
        f"文件会装到：\n{runtime_dir()}\n\n"
        "下载时，进度条会显示百分比和已下载大小。\n"
        "也可以自己运行安装脚本，脚本窗口里同样会显示进度：\n"
        f"{install_script_path()}"
    )


def format_size(num_bytes: int) -> str:
    if num_bytes >= 1024 ** 3:
        return f"{num_bytes / 1024 ** 3:.2f} GB"
    if num_bytes >= 1024 ** 2:
        return f"{num_bytes / 1024 ** 2:.0f} MB"
    return f"{num_bytes / 1024:.0f} KB"


def progress_message(label: str, done: int, total: int) -> str:
    if total > 0:
        pct = min(100, int(done * 100 / total))
        return f"正在下载 {label}  {pct}%（{format_size(done)} / {format_size(total)}）"
    return f"正在下载 {label}  {format_size(done)}"


def _blend(start: float, end: float, frac: float) -> float:
    frac = min(1.0, max(0.0, frac))
    return start + (end - start) * frac


def _check_cancel(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise CancelledError()


def download_file(url: str, dest: Path, cancel: Event | None, on_bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    existing = dest.stat().st_size if dest.is_file() else 0
    headers = {"User-Agent": "PianoConvert"}
    if existing > 0:
        headers["Range"] = f"bytes={existing}-"
    request = Request(url, headers=headers)
    try:
        response_cm = urlopen(request, timeout=120)
    except HTTPError as exc:
        if exc.code == 416 and existing > 0:
            on_bytes(existing, existing)
            return
        raise
    with response_cm as response:
        status = getattr(response, "status", 200)
        length = int(response.headers.get("Content-Length") or 0)
        if status == 206:
            total = existing + length
            mode = "ab"
            done = existing
        else:
            total = length
            mode = "wb"
            done = 0
            if existing and total and existing == total:
                on_bytes(total, total)
                return
        with dest.open(mode) as handle:
            while True:
                _check_cancel(cancel)
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                on_bytes(done, total or 0)


def _run(cmd: list[str], cancel: Event | None) -> None:
    flags = _CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=flags,
    )
    output: list[str] = []
    assert proc.stdout is not None
    while proc.poll() is None:
        if cancel is not None and cancel.is_set():
            proc.kill()
            raise CancelledError()
        line = proc.stdout.readline()
        if line:
            output.append(line)
    rest = proc.stdout.read()
    if rest:
        output.append(rest)
    if cancel is not None and cancel.is_set():
        proc.kill()
        raise CancelledError()
    if proc.returncode != 0:
        tail = "".join(output)[-1500:].strip()
        raise GpuSetupError(tail or f"命令失败：{cmd[0]}")


def _prepare_embed(root: Path) -> None:
    pth = next(root.glob("python*._pth"))
    lines = pth.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if line.strip() not in {"import site", "#import site"}]
    if "Lib\\site-packages" not in kept and "Lib/site-packages" not in kept:
        kept.append("Lib\\site-packages")
    kept.append("import site")
    pth.write_text("\n".join(kept) + "\n", encoding="utf-8")


def _copy_piano_sources() -> None:
    dest = runtime_dir() / "Lib" / "site-packages" / "piano_transcription_inference"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        piano_source_dir(),
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def install(cancel: Event | None = None, on_progress=None) -> None:
    report = on_progress or (lambda _message, _ratio: None)
    if gpu_installed():
        report("GPU 组件已安装", 1.0)
        return
    root = runtime_dir()
    cache = root / "cache"
    cache.mkdir(parents=True, exist_ok=True)

    paths: dict[str, Path] = {}
    for label, url, name, start, end in DOWNLOADS:
        dest = cache / name
        paths[name] = dest

        def on_bytes(done: int, total: int, _label=label, _start=start, _end=end) -> None:
            frac = (done / total) if total else 0.0
            report(progress_message(_label, done, total), _blend(_start, _end, frac))

        download_file(url, dest, cancel, on_bytes)
        report(progress_message(label, dest.stat().st_size, dest.stat().st_size), end)

    _check_cancel(cancel)
    if not python_exe().is_file():
        report("正在解压 Python", 0.972)
        with zipfile.ZipFile(paths["python-embed.zip"]) as archive:
            archive.extractall(root)
    _prepare_embed(root)
    _check_cancel(cancel)
    py = str(python_exe())
    report("正在安装 pip", 0.975)
    _run([py, str(paths["get-pip.py"])], cancel)
    report("正在安装 GPU 组件", 0.98)
    _run([py, "-m", "pip", "install", "setuptools>=65,<81"], cancel)
    _run(
        [py, "-m", "pip", "install", "--no-deps", "--force-reinstall", str(paths["torch-cu130.whl"]), str(paths["torchaudio-cu130.whl"])],
        cancel,
    )
    _run(
        [py, "-m", "pip", "install", "--no-deps", "--force-reinstall", str(paths["onnxruntime-gpu.whl"])],
        cancel,
    )
    _run(
        [
            py,
            "-m",
            "pip",
            "install",
            "numpy",
            "librosa",
            "soundfile",
            "audioread",
            "resampy",
            "mido",
            "matplotlib",
            "torchlibrosa",
            "pretty_midi",
            "scipy",
            "basic-pitch",
        ],
        cancel,
    )
    _run(
        [py, "-m", "pip", "install", "--no-deps", "--force-reinstall", str(paths["torch-cu130.whl"]), str(paths["onnxruntime-gpu.whl"])],
        cancel,
    )
    report("正在放入钢琴模型代码", 0.99)
    _copy_piano_sources()
    report("正在检查 GPU 组件", 0.995)
    _run(
        [
            py,
            "-c",
            "import torch,basic_pitch,piano_transcription_inference,onnxruntime as ort;"
            "assert torch.version.cuda;"
            "assert 'CUDAExecutionProvider' in ort.get_available_providers()",
        ],
        cancel,
    )
    (root / "READY").write_text("ok\n", encoding="utf-8")
    report("GPU 组件已安装", 1.0)


def transcribe_with_gpu(kind: str, audio: Path, midi: Path, checkpoint: Path | None, cancel: Event, on_progress) -> None:
    if not gpu_installed():
        raise GpuSetupError("还没有安装 GPU 组件。请先点「下载 GPU 组件」。")
    _check_cancel(cancel)
    cmd = [str(python_exe()), str(worker_path()), kind, str(audio), str(midi)]
    if checkpoint is not None:
        cmd.append(str(checkpoint))
    flags = _CREATE_NO_WINDOW if os.name == "nt" else 0
    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=flags,
        env=env,
    )
    lines: list[str] = []
    assert proc.stdout is not None

    def _read() -> None:
        for line in proc.stdout:
            lines.append(line)
            if line.startswith("PROGRESS\t"):
                parts = line.strip().split("\t")
                if len(parts) >= 3:
                    on_progress(parts[2], float(parts[1]))

    reader = Thread(target=_read, daemon=True)
    reader.start()
    while proc.poll() is None:
        if cancel is not None and cancel.is_set():
            proc.kill()
            reader.join(timeout=2)
            raise CancelledError()
        time.sleep(0.2)
    reader.join(timeout=5)
    if cancel is not None and cancel.is_set():
        proc.kill()
        raise CancelledError()
    if proc.returncode != 0:
        text = "".join(lines).strip()
        for line in reversed(lines):
            if line.startswith("ERROR\t"):
                text = line.split("\t", 1)[1].strip()
                break
        raise GpuSetupError(text or "GPU 转换失败")


def main() -> None:
    def on_progress(message: str, ratio: float) -> None:
        print(f"PROGRESS\t{ratio:.4f}\t{message}", flush=True)

    try:
        install(on_progress=on_progress)
    except CancelledError:
        print("已取消下载", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        print(str(exc) or exc.__class__.__name__, file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
