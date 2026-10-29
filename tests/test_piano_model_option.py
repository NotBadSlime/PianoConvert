import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtWidgets import QApplication

from app.convert import ConvertResult
from app.engines import piano as piano_module
from app.engines.base import CancelledError
from app.engines.piano import MODEL_KONG, MODEL_TRANSKUN, PianoEngine
from app.settings import load_settings
from app.ui.main_window import MainWindow, default_engines


@pytest.fixture
def isolated_history(tmp_path, monkeypatch):
    hist = tmp_path / "history.json"
    monkeypatch.setattr("app.history.history_path", lambda: hist)
    return hist


class Recorder:
    """Stands in for an engine: remembers which engine class the router picked."""

    instances: list = []

    def __init__(self, name):
        self.name = name
        self.device = "auto"

    def transcribe(self, audio, midi, cancel, on_progress):
        Recorder.instances.append((self.name, self.device))


@pytest.fixture
def routed(monkeypatch):
    Recorder.instances = []
    monkeypatch.setattr(piano_module, "KongPianoEngine", lambda: Recorder("kong"))
    monkeypatch.setattr(piano_module, "TranskunPianoEngine", lambda: Recorder("transkun"))
    return Recorder


# ---------------------------------------------------------------- the router


def test_piano_engine_uses_transkun_by_default(routed, monkeypatch):
    monkeypatch.setattr(piano_module.transkun_infer, "unavailable_reason", lambda: "")
    engine = PianoEngine()
    assert engine.model == MODEL_TRANSKUN
    engine.device = "cuda"
    engine.transcribe(Path("a.wav"), Path("a.mid"), Event(), lambda *_: None)
    assert routed.instances == [("transkun", "cuda")]


def test_piano_engine_can_use_the_bytedance_model(routed, monkeypatch):
    monkeypatch.setattr(piano_module.transkun_infer, "unavailable_reason", lambda: "")
    engine = PianoEngine(device="cpu", model=MODEL_KONG)
    engine.transcribe(Path("a.wav"), Path("a.mid"), Event(), lambda *_: None)
    assert routed.instances == [("kong", "cpu")]


def test_piano_engine_falls_back_when_transkun_is_missing(routed, monkeypatch):
    monkeypatch.setattr(piano_module.transkun_infer, "unavailable_reason", lambda: "没有找到 TransKun 组件")
    seen = []
    PianoEngine(device="cpu").transcribe(Path("a.wav"), Path("a.mid"), Event(), lambda msg, ratio: seen.append(msg))
    assert routed.instances == [("kong", "cpu")]
    assert any("ByteDance" in message for message in seen)


def test_default_engines_use_the_router():
    engines = default_engines()
    assert isinstance(engines["piano"], PianoEngine)
    assert set(engines) == {"piano", "other"}


# ---------------------------------------------------------------- the option


def test_piano_model_defaults_to_transkun_and_is_remembered(_isolated_settings):
    QApplication.instance() or QApplication([])
    win = MainWindow(engines={})
    assert win.piano_model() == MODEL_TRANSKUN
    assert win.piano_transkun_radio.isChecked() is True
    assert "推荐" in win.piano_model_help.text()
    win.piano_kong_radio.setChecked(True)
    assert win.piano_model() == MODEL_KONG
    assert load_settings()["piano_model"] == MODEL_KONG
    assert "原来" in win.piano_model_help.text()
    again = MainWindow(engines={})
    assert again.piano_model() == MODEL_KONG
    assert again.piano_kong_radio.isChecked() is True


def test_unknown_saved_model_falls_back_to_transkun(_isolated_settings):
    QApplication.instance() or QApplication([])
    _isolated_settings.write_text('{"piano_model": "nonsense"}', encoding="utf-8")
    assert MainWindow(engines={}).piano_model() == MODEL_TRANSKUN


def test_piano_model_row_is_only_for_piano_recordings(tmp_path, _isolated_settings):
    QApplication.instance() or QApplication([])
    win = MainWindow(engines={})
    win.advanced_button.setChecked(True)
    assert "钢琴模型" in win.advanced_button.toolTip() or "钢琴识别模型" in win.advanced_button.toolTip()
    assert win.piano_model_box.isVisibleTo(win.advanced_box) is True
    win.other_radio.setChecked(True)
    assert win.piano_model_box.isVisibleTo(win.advanced_box) is False
    win.piano_radio.setChecked(True)
    assert win.piano_model_box.isVisibleTo(win.advanced_box) is True
    for name in ("a.mid", "a.musicxml", "a.pdf"):
        src = tmp_path / name
        src.write_bytes(b"x")
        win.set_source_file(src)
        assert win.piano_model_box.isVisibleTo(win.advanced_box) is False, name
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    win.set_source_file(audio)
    assert win.piano_model_box.isVisibleTo(win.advanced_box) is True


def test_the_chosen_model_reaches_the_piano_engine(tmp_path, isolated_history, _isolated_settings):
    app = QApplication.instance() or QApplication([])

    class Probe:
        device = ""
        model = ""

    class Other:
        device = ""

    probe, other = Probe(), Other()

    def fake_convert(source, kind, cancel, on_progress):
        return ConvertResult(
            status="cancelled",
            folder=tmp_path,
            midi_path=tmp_path / "t.mid",
            musicxml_path=tmp_path / "t.musicxml",
            error="",
            title="t",
            kind=kind,
            source_path=source,
            keyboard_path=tmp_path / "t_键盘谱.txt",
        )

    win = MainWindow(convert_fn=fake_convert, engines={"piano": probe, "other": other})
    win.piano_kong_radio.setChecked(True)
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    win.set_source_file(src)
    win._start_job()
    assert probe.model == MODEL_KONG
    assert probe.device == "cpu"
    assert not hasattr(other, "model")  # only the piano engine has a model to choose
    for _ in range(300):
        app.processEvents()
        if not win._busy:
            break
        time.sleep(0.01)
    assert win._busy is False
