from pathlib import Path

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
