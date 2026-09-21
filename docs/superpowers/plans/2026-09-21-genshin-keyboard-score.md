# 原神键盘谱 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. User asked to implement immediately, then README + GitHub Release installer.

**Goal:** MIDI/MusicXML（及音频转出的 MIDI）生成指尖旋律风格数字谱+键盘谱，并发布 v0.3.0 安装包。

**Architecture:** 纯函数模块 `app/keyboard_score.py` 负责读谱、转调、去黑键、折音域、50ms 聚簇和排版。`convert.run` 在 MIDI 之后调用；`convert.run_score` 给单独选文件。历史增加 `keyboard_path`。

**Tech Stack:** Python 3.10、pretty_midi、music21、PySide6、pytest、Inno Setup。

---

### Task 1: keyboard_score 核心

**Files:** Create `tests/test_keyboard_score.py`, `app/keyboard_score.py`

覆盖 spec 测试 1–5。然后接 Task 2–5：history、convert.run_score、UI、README、v0.3.0 安装包。
