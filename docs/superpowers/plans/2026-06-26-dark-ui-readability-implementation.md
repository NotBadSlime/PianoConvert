# Dark UI Readability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the partially styled light UI with a readable dark PySide6 theme.

**Architecture:** Store the QSS in a small `app/ui/theme.py` module and have `MainWindow._apply_style()` use that stylesheet. Keep visual behavior contained to UI styling and protect the change with a focused UI test.

**Tech Stack:** Python 3.10, PySide6, pytest.

---

## Files

- Create `app/ui/theme.py`: dark stylesheet constants.
- Modify `app/ui/main_window.py`: import and apply the dark stylesheet.
- Modify `tests/test_ui_smoke.py`: assert key dark theme tokens exist on the main window stylesheet.

## Steps

- [ ] Write a failing UI test that checks `MainWindow.styleSheet()` contains dark background, light text, input styling, and disabled-state styling.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/test_ui_smoke.py -v` and confirm failure.
- [ ] Add `app/ui/theme.py` with `DARK_STYLESHEET`.
- [ ] Update `MainWindow._apply_style()` to apply `DARK_STYLESHEET`.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/test_ui_smoke.py -v` and confirm pass.
- [ ] Render an offscreen screenshot to `artifacts/dark-ui-smoke.png` and inspect it.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest -v`.
- [ ] Commit the UI theme change.
