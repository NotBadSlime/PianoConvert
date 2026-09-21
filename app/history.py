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


def item_from_row(row: dict) -> HistoryItem:
    allowed = {f.name for f in fields(HistoryItem)}
    data = {key: row[key] for key in allowed if key in row}
    data.setdefault("keyboard_path", "")
    return HistoryItem(**data)


def load_items() -> list[HistoryItem]:
    path = history_path()
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [item_from_row(row) for row in raw]


def append_item(item: HistoryItem) -> None:
    path = history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    items = load_items()
    items.append(item)
    path.write_text(json.dumps([asdict(x) for x in items], ensure_ascii=False, indent=2), encoding="utf-8")
