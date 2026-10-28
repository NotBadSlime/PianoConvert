from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from app.paths import history_path


@dataclass
class HistoryItem:
    id: str
    title: str
    source_path: str
    kind: str
    created_at: str
    status: str
    midi_path: str
    musicxml_path: str
    folder: str
    error: str
    keyboard_path: str = ""
    pdf_path: str = ""


def item_from_row(row: dict) -> HistoryItem:
    allowed = {f.name for f in fields(HistoryItem)}
    data = {key: row[key] for key in allowed if key in row}
    data.setdefault("keyboard_path", "")
    data.setdefault("pdf_path", "")
    return HistoryItem(**data)


def load_items() -> list[HistoryItem]:
    path = history_path()
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [item_from_row(row) for row in raw]


def _write(items: list[HistoryItem]) -> None:
    path = history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(x) for x in items], ensure_ascii=False, indent=2), encoding="utf-8")


def append_item(item: HistoryItem) -> None:
    items = load_items()
    items.append(item)
    _write(items)


def remove_item(item_id: str) -> tuple[HistoryItem, int] | None:
    """Drop one record from the history (the converted files stay where they are).

    Returns the removed item and its position, for ``restore_item``; None when no record has that id.
    """
    items = load_items()
    for index, item in enumerate(items):
        if item.id == item_id:
            del items[index]
            _write(items)
            return item, index
    return None


def restore_item(item: HistoryItem, index: int) -> None:
    """Put a removed record back where it was (undo of ``remove_item``)."""
    items = [x for x in load_items() if x.id != item.id]
    items.insert(max(0, min(int(index), len(items))), item)
    _write(items)
