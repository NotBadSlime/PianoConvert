import sys
import types
from pathlib import Path
from threading import Event

import pytest

from app.gpu_setup import download_file, format_size, progress_message


def test_progress_message_shows_percent_and_size():
    text = progress_message("PyTorch", 820 * 1024 * 1024, 1898 * 1024 * 1024)
    assert "PyTorch" in text
    assert "43%" in text
    assert "820 MB" in text
    assert "1.85 GB" in text
    assert "GB" in format_size(2 * 1024 ** 3)


def test_download_file_reports_progress(tmp_path, monkeypatch):
    payload = b"a" * 1000

    class _Resp:
        status = 200
        headers = {"Content-Length": str(len(payload))}

        def __init__(self):
            self._pos = 0

        def read(self, size):
            chunk = payload[self._pos : self._pos + size]
            self._pos += len(chunk)
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("app.gpu_setup.urlopen", lambda *_args, **_kwargs: _Resp())
    seen = []
    dest = tmp_path / "torch.whl"
    download_file("http://example/torch.whl", dest, None, lambda done, total: seen.append((done, total)))
    assert dest.read_bytes() == payload
    assert seen[-1] == (1000, 1000)
    assert seen[0][0] > 0


def test_install_script_prints_progress():
    script = Path("scripts/install_gpu.ps1").read_text(encoding="utf-8")
    assert "--install-gpu" in script
    assert "gpu-runtime" in script or "app.gpu_setup" in script


# ---------------------------------------------------------------- TransKun in the GPU runtime


def _fake_transkun_package(root: Path, weights: bytes = b"w" * 64) -> Path:
    src = root / "transkun_pkg"
    (src / "pretrained").mkdir(parents=True)
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "x.pyc").write_bytes(b"c")
    (src / "ModelTransformer.py").write_text("# model", encoding="utf-8")
    (src / "pretrained" / "2.0.conf").write_text("{}", encoding="utf-8")
    (src / "pretrained" / "2.0.pt").write_bytes(weights)
    return src


def test_ensure_transkun_runtime_copies_the_package_when_needed(tmp_path, monkeypatch):
    from app import gpu_setup

    src = _fake_transkun_package(tmp_path)
    monkeypatch.setattr(gpu_setup, "runtime_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(gpu_setup, "transkun_source_dir", lambda: src)
    dest = tmp_path / "runtime" / "Lib" / "site-packages" / "transkun"

    gpu_setup.ensure_transkun_runtime()
    assert (dest / "ModelTransformer.py").is_file()
    assert (dest / "pretrained" / "2.0.pt").read_bytes() == b"w" * 64
    assert not (dest / "__pycache__").exists()

    marker = dest / "marker.txt"
    marker.write_text("keep", encoding="utf-8")
    gpu_setup.ensure_transkun_runtime()  # already there: nothing is copied again
    assert marker.exists()

    (src / "pretrained" / "2.0.pt").write_bytes(b"w" * 80)  # a newer app brings other weights
    gpu_setup.ensure_transkun_runtime()
    assert (dest / "pretrained" / "2.0.pt").stat().st_size == 80
    assert not marker.exists()


def test_ensure_transkun_runtime_explains_a_missing_package(tmp_path, monkeypatch):
    from app import gpu_setup

    def missing():
        raise ModuleNotFoundError("No module named 'transkun'")

    monkeypatch.setattr(gpu_setup, "runtime_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(gpu_setup, "transkun_source_dir", missing)
    with pytest.raises(gpu_setup.GpuSetupError, match="TransKun"):
        gpu_setup.ensure_transkun_runtime()


class _FakeProcess:
    started: list = []

    def __init__(self, cmd, **_kwargs):
        _FakeProcess.started.append(cmd)
        self.stdout = iter(["PROGRESS\t0.5500\t正在用 GPU 转录\n"])
        self.returncode = 0

    def poll(self):
        return 0

    def kill(self):
        pass


@pytest.mark.parametrize("kind,prepares", [("transkun", True), ("piano", False)])
def test_gpu_transcribe_prepares_transkun_only_for_transkun(tmp_path, monkeypatch, kind, prepares):
    from app import gpu_setup

    prepared = []
    _FakeProcess.started = []
    monkeypatch.setattr(gpu_setup, "gpu_installed", lambda: True)
    monkeypatch.setattr(gpu_setup, "python_exe", lambda: tmp_path / "python.exe")
    monkeypatch.setattr(gpu_setup, "ensure_transkun_runtime", lambda: prepared.append(True))
    monkeypatch.setattr(gpu_setup.subprocess, "Popen", _FakeProcess)
    seen = []
    gpu_setup.transcribe_with_gpu(
        kind, tmp_path / "a.wav", tmp_path / "a.mid", None, Event(), lambda message, ratio: seen.append((message, ratio))
    )
    assert bool(prepared) is prepares
    command = _FakeProcess.started[0]
    assert command[2] == kind and command[3] == str(tmp_path / "a.wav")
    assert seen == [("正在用 GPU 转录", 0.55)]


def test_gpu_worker_runs_transkun_through_the_shared_helper(monkeypatch, capsys):
    from app import gpu_worker

    calls = {}
    fake = types.ModuleType("transkun_infer")
    fake.load_model = lambda device: calls.setdefault("device", device) or "model"

    def transcribe_file(model, audio, midi, device, should_stop=None, on_progress=None):
        calls["call"] = (audio, midi, device)
        on_progress(0.5)

    fake.transcribe_file = transcribe_file
    monkeypatch.setitem(sys.modules, "transkun_infer", fake)
    monkeypatch.setattr(sys, "path", list(sys.path))  # the worker puts its own folder first
    gpu_worker._transkun("a.wav", "a.mid", "cpu")
    assert calls["call"] == ("a.wav", "a.mid", "cpu")
    assert "PROGRESS\t0.5500\t" in capsys.readouterr().out
