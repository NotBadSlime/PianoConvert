import json

from app.history import HistoryItem, append_item, item_from_row, load_items, remove_item, restore_item


def _record(ident: str, folder) -> HistoryItem:
    kb = folder / f"{ident}_键盘谱.txt"
    kb.write_text("k", encoding="utf-8")
    return HistoryItem(
        id=ident, title=f"曲{ident}", source_path="a.mid", kind="score", created_at="2026-01-01T00:00:00",
        status="success", midi_path="", musicxml_path="", folder=str(folder), error="", keyboard_path=str(kb),
    )


def test_remove_item_keeps_files_and_restore_puts_it_back(tmp_path, monkeypatch):
    monkeypatch.setattr("app.history.history_path", lambda: tmp_path / "history.json")
    for ident in ("a", "b", "c"):
        append_item(_record(ident, tmp_path))
    removed = remove_item("b")
    assert removed is not None
    item, index = removed
    assert (item.id, index) == ("b", 1)
    assert [x.id for x in load_items()] == ["a", "c"]
    assert (tmp_path / "b_键盘谱.txt").exists()  # the converted files are not deleted
    assert remove_item("nope") is None
    restore_item(item, index)
    assert [x.id for x in load_items()] == ["a", "b", "c"]
    restore_item(item, 99)  # restoring twice does not duplicate
    assert [x.id for x in load_items()] == ["a", "c", "b"]


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
