from pathlib import Path


def test_app_spec_bundles_music21_runtime_deps():
    spec = Path("app.spec").read_text(encoding="utf-8")
    for name in ("webcolors", "jsonpickle", "more_itertools", "chardet"):
        assert name in spec
    assert "app.keyboard_score" in spec
