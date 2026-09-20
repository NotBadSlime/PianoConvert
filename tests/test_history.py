from app.history import HistoryItem, append_item, load_items


def test_append_and_reload(tmp_path, monkeypatch):
    monkeypatch.setattr("app.history.history_path", lambda: tmp_path / "history.json")
    item = HistoryItem(
        id="abc",
        title="demo",
        source_path="C:/a.mp3",
        kind="piano",
        created_at="2026-01-02T03:04:05",
        status="success",
        midi_path="C:/out/a.mid",
        musicxml_path="C:/out/a.musicxml",
        folder="C:/out",
        error="",
    )
    append_item(item)
    append_item(item)
    loaded = load_items()
    assert len(loaded) == 2
    assert loaded[0].title == "demo"
    assert loaded[0].kind == "piano"


def test_load_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("app.history.history_path", lambda: tmp_path / "nope.json")
    assert load_items() == []
