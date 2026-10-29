"""Small user preferences kept between runs (settings.json next to the history)."""
from __future__ import annotations

import json

from app.paths import settings_path

SCORE_STYLE = "score_style"
RANGE_STRATEGY = "range_strategy"
THEME = "theme"                    # "system" (跟随系统) / "light" / "dark"
PREVIEW_ZOOM = "preview_zoom"      # percent, 70 … 200
PREVIEW_SECTION = "preview_section"  # "keyboard" / "jianpu" / "fingertip" / "all"
ARPEGGIO = "arpeggio"             # "auto" / "chord" / "spread": how rolled chords are written
CHORD = "chord"                   # "strict" / "standard" / "loose": what counts as struck together
PIANO_MODEL = "piano_model"       # "transkun" / "kong": which model transcribes a piano recording
DEFAULTS = {
    SCORE_STYLE: "detailed",
    RANGE_STRATEGY: "modern",
    THEME: "system",
    PREVIEW_ZOOM: 100,
    PREVIEW_SECTION: "keyboard",
    ARPEGGIO: "auto",
    CHORD: "standard",
    PIANO_MODEL: "transkun",
}


def load_settings() -> dict:
    data = dict(DEFAULTS)
    try:
        raw = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return data
    if isinstance(raw, dict):
        data.update(raw)
    return data


def save_setting(key: str, value) -> None:
    data = load_settings()
    data[key] = value
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # a preference that cannot be saved is not worth an error message
