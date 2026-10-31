from pathlib import Path

from app.frozen_smoke import REQUIRED_IMPORTS


def test_app_spec_bundles_music21_runtime_deps():
    spec = Path("app.spec").read_text(encoding="utf-8")
    for name in (
        "webcolors",
        "jsonpickle",
        "more_itertools",
        "chardet",
        "pkg_resources",
        "setuptools",
        "resampy",
        "homr",
    ):
        assert name in spec
    assert "app.keyboard_score" in spec


def test_smoke_import_list_covers_runtime_stack():
    names = {item[0] for item in REQUIRED_IMPORTS}
    for name in (
        "webcolors",
        "pkg_resources",
        "resampy",
        "librosa",
        "music21",
        "basic_pitch",
        "homr",
        "pretty_midi",
    ):
        assert name in names


def test_runtime_imports_succeed_in_dev_env():
    from app.frozen_smoke import check_imports

    assert check_imports() == []


def test_builds_ship_the_homr_models():
    """Reading scans must not need a download: every build fetches homr's models first."""
    spec = Path("app.spec").read_text(encoding="utf-8")
    assert "app.homr_models" in spec and "missing_shipped()" in spec and "_keep_homr_data" in spec
    for script in (Path(".github/workflows/release.yml"), Path("scripts/build_exe.ps1")):
        text = script.read_text(encoding="utf-8")
        assert "-m app.homr_models" in text, script
        assert text.index("-m app.homr_models") < text.index("PyInstaller app.spec"), script


def test_frozen_smoke_requires_shipped_homr_models(monkeypatch):
    import sys

    from app import frozen_smoke, homr_models

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(homr_models, "missing_shipped", lambda: ["segnet.onnx"])
    assert frozen_smoke._check_shipped_homr_models() == ["homr models: not shipped: segnet.onnx"]
    monkeypatch.setattr(homr_models, "missing_shipped", lambda: [])
    assert frozen_smoke._check_shipped_homr_models() == []
