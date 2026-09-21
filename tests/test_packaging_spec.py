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
        "pretty_midi",
    ):
        assert name in names


def test_runtime_imports_succeed_in_dev_env():
    from app.frozen_smoke import check_imports

    assert check_imports() == []
