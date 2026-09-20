from __future__ import annotations

import json
from dataclasses import asdict, dataclass
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


def load_items() -> list[HistoryItem]:
    path = history_path()
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [HistoryItem(**row) for row in raw]


def append_item(item: HistoryItem) -> None:
    path = history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    items = load_items()
    items.append(item)
    path.write_text(json.dumps([asdict(x) for x in items], ensure_ascii=False, indent=2), encoding="utf-8")
