import pytest

from scripts.resolve_release_version import resolve_release_version, write_github_output


@pytest.mark.parametrize("value", ["1.0.0-beta.1", "v1.0.0-beta.1"])
def test_beta_has_numeric_windows_version_and_prerelease_flag(value):
    version = resolve_release_version("workflow_dispatch", "main", value)
    assert version.tag == "v1.0.0-beta.1"
    assert version.plain == "1.0.0-beta.1"
    assert version.numeric == "1.0.0"
    assert version.prerelease is True


def test_tag_release_uses_tag_instead_of_dispatch_input():
    version = resolve_release_version("push", "v1.0.0", "9.9.9-beta.1")
    assert version.numeric == "1.0.0"
    assert version.prerelease is False


def test_github_output_includes_packaging_and_release_metadata(tmp_path):
    output = tmp_path / "output"
    write_github_output(output, resolve_release_version("push", "v1.0.0-beta.1", None))
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert values == {"tag": "v1.0.0-beta.1", "plain": "1.0.0-beta.1", "numeric": "1.0.0", "prerelease": "true"}


@pytest.mark.parametrize("value", ["", "1.0.0; echo bad", "../1.0.0"])
def test_release_version_rejects_invalid_inputs(value):
    with pytest.raises(ValueError):
        resolve_release_version("workflow_dispatch", "main", value)
