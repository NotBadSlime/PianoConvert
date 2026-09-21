import json

from app.history import HistoryItem, append_item, item_from_row, load_items


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


def test_old_json_without_keyboard_path(tmp_path, monkeypatch):
    hist = tmp_path / "history.json"
    monkeypatch.setattr("app.history.history_path", lambda: hist)
    hist.write_text(
        json.dumps(
            [
                {
                    "id": "1",
                    "title": "old",
                    "source_path": "a.mp3",
                    "kind": "piano",
                    "created_at": "2026-01-01T00:00:00",
                    "status": "success",
                    "midi_path": "a.mid",
                    "musicxml_path": "a.musicxml",
                    "folder": "out",
                    "error": "",
                }
            ]
        ),
        encoding="utf-8",
    )
    items = load_items()
    assert items[0].keyboard_path == ""
    assert item_from_row({"id": "1", "title": "t", "source_path": "x", "kind": "piano",
                          "created_at": "t", "status": "success", "midi_path": "",
                          "musicxml_path": "", "folder": "", "error": ""}).keyboard_path == ""
