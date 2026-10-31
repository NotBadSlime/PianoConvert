import hashlib
import io
import random
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event

import pytest

from app import homr_models as hm
from app.engines.base import CancelledError

MODEL = "segnet_test-0000.onnx"
PAYLOAD = random.Random(7).randbytes(600_000)
BLOCK = 16 * 1024


def _zip(payload: bytes, member: str = MODEL) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr(member, payload)
    return buf.getvalue()


GOOD = _zip(PAYLOAD)


class _Server:
    """Serves archives by path; supports Range, slow sending and dropping the connection."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.requests: list[tuple[str, str | None]] = []
        self.drop_after: dict[str, int] = {}  # path -> bytes sent before the next response breaks off
        self.delay: dict[str, float] = {}  # path -> pause after every 16 KiB
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args) -> None:
                pass

            def do_GET(self) -> None:
                wanted = self.headers.get("Range")
                server.requests.append((self.path, wanted))
                body = server.files.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start = int(wanted[len("bytes=") :].split("-")[0]) if wanted else 0
                if start >= len(body):
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{len(body)}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if wanted:
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
                else:
                    self.send_response(200)
                rest = body[start:]
                self.send_header("Content-Length", str(len(rest)))
                self.end_headers()
                limit = server.drop_after.pop(self.path, None)
                pause = server.delay.get(self.path, 0.0)
                sent = 0
                try:
                    for offset in range(0, len(rest), BLOCK):
                        block = rest[offset : offset + BLOCK]
                        if limit is not None and sent + len(block) > limit:
                            self.wfile.write(block[: limit - sent])
                            self.wfile.flush()
                            self.close_connection = True
                            return
                        self.wfile.write(block)
                        sent += len(block)
                        if pause:
                            time.sleep(pause)
                except (ConnectionError, OSError):
                    pass  # the client went away (cancel / switching sources)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.httpd.server_port}{path}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    srv = _Server()
    yield srv
    srv.close()


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """A fresh install: nothing shipped beside homr, an empty per-user cache."""
    folder = tmp_path / "cache"
    shipped = tmp_path / "shipped"
    monkeypatch.setattr(hm, "cache_dir", lambda: folder)
    monkeypatch.setattr(hm, "package_paths", lambda: {MODEL: shipped / MODEL})
    monkeypatch.setattr(hm, "SHA256", {MODEL: hashlib.sha256(PAYLOAD).hexdigest()})
    monkeypatch.setattr(hm, "RETRY_PAUSE", 0.01)
    monkeypatch.setattr(hm, "REPORT_INTERVAL", 0.0)
    monkeypatch.setattr(hm, "CONNECT_TIMEOUT", 5.0)
    monkeypatch.setattr(hm, "READ_TIMEOUT", 5.0)
    return folder


def _use(monkeypatch, server, *paths):
    monkeypatch.setattr(hm, "sources", lambda _name: [server.url(p) for p in paths])


def _part(cache):
    return cache / (hm.zip_name(MODEL) + ".part")


def test_downloads_checks_and_installs_the_model(server, cache, monkeypatch):
    server.files["/ok/m.zip"] = GOOD
    _use(monkeypatch, server, "/ok/m.zip")
    statuses = []
    got = hm.ensure_models(Event(), statuses.append, names=[MODEL])
    assert got == [cache / MODEL]
    assert (cache / MODEL).read_bytes() == PAYLOAD
    assert not _part(cache).exists()
    last = statuses[-1]
    assert last.done == last.total == len(GOOD) and last.fraction == 1.0
    assert hm.status_text(last).startswith("下载识别模型（仅第一次）1/1 · ")
    asked = len(server.requests)
    assert hm.ensure_models(Event(), names=[MODEL]) == []  # already there: no request
    assert len(server.requests) == asked


def test_continues_an_earlier_partial_download(server, cache, monkeypatch):
    server.files["/ok/m.zip"] = GOOD
    _use(monkeypatch, server, "/ok/m.zip")
    cache.mkdir(parents=True)
    _part(cache).write_bytes(GOOD[:300_000])
    hm.ensure_models(Event(), names=[MODEL])
    assert server.requests == [("/ok/m.zip", "bytes=300000-")]
    assert (cache / MODEL).read_bytes() == PAYLOAD


def test_dropped_connection_resumes_where_it_stopped(server, cache, monkeypatch):
    server.files["/ok/m.zip"] = GOOD
    server.drop_after["/ok/m.zip"] = 200_000
    _use(monkeypatch, server, "/ok/m.zip")
    hm.ensure_models(Event(), names=[MODEL])
    assert (cache / MODEL).read_bytes() == PAYLOAD
    (first, first_range), (second, second_range) = server.requests
    assert first == second == "/ok/m.zip" and first_range is None
    assert second_range is not None and int(second_range[len("bytes=") : -1]) >= 100_000


def test_failing_source_falls_back_to_the_next_one(server, cache, monkeypatch):
    server.files["/mirror/m.zip"] = GOOD
    _use(monkeypatch, server, "/github/m.zip", "/mirror/m.zip")
    hm.ensure_models(Event(), names=[MODEL])
    assert [path for path, _ in server.requests] == ["/github/m.zip", "/mirror/m.zip"]
    assert (cache / MODEL).read_bytes() == PAYLOAD


def test_wrong_file_is_rejected_and_the_next_source_used(server, cache, monkeypatch):
    server.files["/bad/m.zip"] = _zip(b"not the model" * 1000)
    server.files["/ok/m.zip"] = GOOD
    _use(monkeypatch, server, "/bad/m.zip", "/ok/m.zip")
    hm.ensure_models(Event(), names=[MODEL])
    assert (cache / MODEL).read_bytes() == PAYLOAD
    assert server.requests[-1] == ("/ok/m.zip", None)  # the bad bytes were thrown away


def test_slow_source_is_left_for_a_faster_one(server, cache, monkeypatch):
    server.files["/slow/m.zip"] = GOOD
    server.files["/fast/m.zip"] = GOOD
    server.delay["/slow/m.zip"] = 0.05
    monkeypatch.setattr(hm, "MIN_SPEED", 10 * 1024 * 1024)
    monkeypatch.setattr(hm, "PROBE_SECONDS", 0.3)
    _use(monkeypatch, server, "/slow/m.zip", "/fast/m.zip")
    hm.ensure_models(Event(), names=[MODEL])
    assert (cache / MODEL).read_bytes() == PAYLOAD
    (slow, _), (fast, resumed) = server.requests
    assert (slow, fast) == ("/slow/m.zip", "/fast/m.zip")
    assert resumed is not None  # it carried on from the slow source's bytes


def test_when_every_source_is_slow_the_fastest_finishes(server, cache, monkeypatch):
    server.files["/slower/m.zip"] = GOOD
    server.files["/slow/m.zip"] = GOOD
    server.delay["/slower/m.zip"] = 0.08
    server.delay["/slow/m.zip"] = 0.02
    monkeypatch.setattr(hm, "MIN_SPEED", 100 * 1024 * 1024)
    monkeypatch.setattr(hm, "PROBE_SECONDS", 0.3)
    _use(monkeypatch, server, "/slower/m.zip", "/slow/m.zip")
    hm.ensure_models(Event(), names=[MODEL])
    assert (cache / MODEL).read_bytes() == PAYLOAD
    assert [path for path, _ in server.requests] == ["/slower/m.zip", "/slow/m.zip", "/slow/m.zip"]


def test_cancel_keeps_the_partial_download_for_next_time(server, cache, monkeypatch):
    server.files["/ok/m.zip"] = GOOD
    server.delay["/ok/m.zip"] = 0.02
    _use(monkeypatch, server, "/ok/m.zip")
    cancel = Event()
    threading.Timer(0.3, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(CancelledError):
        hm.ensure_models(cancel, names=[MODEL])
    assert time.monotonic() - started < 3
    kept = _part(cache).stat().st_size
    assert 0 < kept < len(GOOD)
    assert not (cache / MODEL).exists()
    server.delay.clear()
    hm.ensure_models(Event(), names=[MODEL])
    assert server.requests[-1] == ("/ok/m.zip", f"bytes={kept}-")
    assert (cache / MODEL).read_bytes() == PAYLOAD


def test_gives_up_when_no_source_works(server, cache, monkeypatch):
    _use(monkeypatch, server, "/a/m.zip", "/b/m.zip")
    with pytest.raises(hm.ModelDownloadError):
        hm.ensure_models(Event(), names=[MODEL])
    assert len(server.requests) == hm.FAILURES_PER_SOURCE * 2
    assert not (cache / MODEL).exists()


def test_into_package_puts_the_model_beside_homr(server, cache, tmp_path, monkeypatch):
    server.files["/ok/m.zip"] = GOOD
    _use(monkeypatch, server, "/ok/m.zip")
    got = hm.ensure_models(Event(), names=[MODEL], into_package=True)
    assert got == [tmp_path / "shipped" / MODEL]
    assert hm.resolve(MODEL) == tmp_path / "shipped" / MODEL
    assert hm.missing([MODEL]) == []


def test_resolve_prefers_the_shipped_copy(tmp_path, monkeypatch):
    shipped = tmp_path / "shipped" / MODEL
    monkeypatch.setattr(hm, "cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(hm, "package_paths", lambda: {MODEL: shipped})
    assert hm.resolve(MODEL) == tmp_path / "cache" / MODEL
    assert hm.missing([MODEL]) == [MODEL]
    shipped.parent.mkdir()
    shipped.write_bytes(b"x")
    assert hm.resolve(MODEL) == shipped
    assert hm.missing([MODEL]) == []


def test_mirrors_are_used_only_for_checksummed_files(monkeypatch):
    name = hm.required_names()[0]
    official = hm.OFFICIAL_BASE + hm.zip_name(name)
    monkeypatch.delenv(hm.MIRRORS_ENV, raising=False)
    assert hm.sources(name) == [official] + [m + official for m in hm.DEFAULT_MIRRORS]
    assert hm.sources("unknown_model.onnx") == [hm.OFFICIAL_BASE + "unknown_model.zip"]
    monkeypatch.setenv(hm.MIRRORS_ENV, "https://mirror.example")
    assert hm.sources(name) == [official, "https://mirror.example/" + official]
    monkeypatch.setenv(hm.MIRRORS_ENV, "")
    assert hm.sources(name) == [official]


def test_checksums_cover_the_models_the_app_ships():
    """Pinned homr version: the CPU set must have checksums, or no mirror would be used."""
    cpu = hm.required_names()
    assert sorted(cpu) == sorted(hm.SHA256)
    gpu = hm.required_names(cuda=True)
    assert all(name.endswith("_fp16.onnx") for name in gpu)
    assert [hm.zip_name(n) for n in cpu] == [n.split(".")[0] + ".zip" for n in cpu]


def test_status_text_without_size():
    status = hm.DownloadStatus(1, 3, MODEL, 5 * 1024 * 1024, None)
    assert hm.status_text(status) == "下载识别模型（仅第一次）1/3 · 5 MB"
    assert status.fraction == 0.0
    assert hm.DownloadStatus(2, 3, MODEL, 23 << 20, 46 << 20).fraction == pytest.approx(0.5)


def test_pdf_note_mentions_the_download_only_while_models_are_missing(monkeypatch):
    from app import copista_models
    from app.ui.main_window import _pdf_note_text

    monkeypatch.setattr(copista_models, "missing", lambda: ["evidence-2m.pt"])
    assert "下载 Copista 模型" in _pdf_note_text()
    monkeypatch.setattr(copista_models, "missing", lambda: [])
    text = _pdf_note_text()
    assert "下载" not in text and "直接读取" in text and "准确度有限" in text
