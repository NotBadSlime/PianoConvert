import pytest


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """Tests never read or write the user's real settings.json."""
    path = tmp_path / "settings.json"
    monkeypatch.setattr("app.settings.settings_path", lambda: path)
    return path
