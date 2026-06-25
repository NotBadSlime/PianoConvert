import importlib.util
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "resolve_release_version.py"


def load_release_version_module():
    if not MODULE_PATH.exists():
        pytest.fail(f"Missing release version helper: {MODULE_PATH}")
    spec = importlib.util.spec_from_file_location("resolve_release_version", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_uses_push_tag_as_release_version():
    release_version = load_release_version_module()

    version = release_version.resolve_release_version(
        event_name="push",
        ref_name="v0.1.0",
        input_version="",
    )

    assert version.tag == "v0.1.0"
    assert version.plain == "0.1.0"


def test_adds_v_prefix_for_manual_dispatch_input():
    release_version = load_release_version_module()

    version = release_version.resolve_release_version(
        event_name="workflow_dispatch",
        ref_name="main",
        input_version="0.2.0",
    )

    assert version.tag == "v0.2.0"
    assert version.plain == "0.2.0"


def test_rejects_empty_manual_dispatch_version():
    release_version = load_release_version_module()

    with pytest.raises(ValueError, match="Release version is required"):
        release_version.resolve_release_version(
            event_name="workflow_dispatch",
            ref_name="main",
            input_version="",
        )


def test_rejects_unsafe_version_names():
    release_version = load_release_version_module()

    with pytest.raises(ValueError, match="Invalid release version"):
        release_version.resolve_release_version(
            event_name="push",
            ref_name="feature/test",
            input_version="",
        )


def test_writes_github_output_file(tmp_path):
    release_version = load_release_version_module()
    output_path = tmp_path / "github_output.txt"

    release_version.write_github_output(
        output_path,
        release_version.ReleaseVersion(tag="v1.2.3", plain="1.2.3"),
    )

    assert output_path.read_text(encoding="utf-8") == "tag=v1.2.3\nplain=1.2.3\n"
