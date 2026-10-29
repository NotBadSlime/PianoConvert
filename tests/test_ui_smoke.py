import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pretty_midi
import pytest
from PySide6.QtWidgets import QApplication

from app.convert import ConvertResult
from app.history import HistoryItem, append_item
from app.ui.main_window import MainWindow


def test_start_disabled_without_file():
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    assert win.start_button.isEnabled() is False
    assert win.windowTitle() == "PianoConvert"
    assert win.piano_radio.isChecked() is True
    assert win.gpu_radio.isEnabled() is True
    assert win.windowIcon().isNull() is False


def test_gpu_choice_shows_download_and_script(monkeypatch):
    monkeypatch.setattr("app.ui.main_window.gpu_installed", lambda: False)
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    assert win.cpu_radio.isChecked() is True
    assert win.gpu_download_button.isHidden() is True
    win.gpu_radio.setChecked(True)
    assert win.gpu_download_button.isHidden() is False
    assert win.gpu_script_button.isHidden() is False
    assert "2.3 GB" in win.gpu_note.text()
    assert "进度" in win.gpu_note.text()


def test_choosing_file_enables_start(tmp_path):
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    win.set_source_file(src)
    assert win.start_button.isEnabled() is True
    assert "a.mp3" in win.file_label.text()


def test_partial_row_disables_musicxml(tmp_path, monkeypatch):
    hist = tmp_path / "h.json"
    monkeypatch.setattr("app.history.history_path", lambda: hist)
    midi = tmp_path / "t.mid"
    midi.write_bytes(b"m")
    append_item(
        HistoryItem(
            id="1",
            title="t",
            source_path="x",
            kind="piano",
            created_at="2026-01-01T00:00:00",
            status="partial",
            midi_path=str(midi),
            musicxml_path=str(tmp_path / "missing.musicxml"),
            folder=str(tmp_path),
            error="谱面导出失败",
        )
    )
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.reload_history()
    row = win.history_list.itemWidget(win.history_list.item(0))
    assert row is not None
    assert row.xml_button.isEnabled() is False
    assert row.midi_button.isEnabled() is True
    assert row.keyboard_button.isEnabled() is False
    assert row.folder_button.text() == "打开文件夹"
    assert row.sizeHint().width() <= 360


def test_score_file_hides_instrument_and_changes_start(tmp_path):
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    src = tmp_path / "a.mid"
    src.write_bytes(b"x")
    win.set_source_file(src)
    assert win.piano_radio.isHidden() is True
    assert win.other_radio.isHidden() is True
    assert win.kind_label.isHidden() is True
    assert win.start_button.text() == "转换为键盘谱"
    assert win.start_button.isEnabled() is True


def test_pdf_file_shows_accuracy_note(tmp_path):
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF")
    win.set_source_file(src)
    assert win.piano_radio.isHidden() is True
    assert win.pdf_note.isHidden() is False
    assert "直接读取" in win.pdf_note.text() and "准确度有限" in win.pdf_note.text()
    assert win.start_button.text() == "转换为键盘谱"


def test_finished_resets_ui_if_history_save_fails(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("app.ui.main_window.append_item", boom)
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    win.set_source_file(src)
    win._busy = True
    win.start_button.setText("取消")
    win.pick_button.setEnabled(False)
    result = ConvertResult(
        status="success",
        folder=tmp_path,
        midi_path=tmp_path / "t.mid",
        musicxml_path=tmp_path / "t.musicxml",
        error="",
        title="t",
        kind="piano",
        source_path=src,
        keyboard_path=tmp_path / "t_键盘谱.txt",
    )
    with pytest.raises(OSError, match="disk full"):
        win._on_finished(result)
    assert win._busy is False
    assert win.start_button.text() == "开始转换"
    assert win.start_button.isEnabled() is True
    assert win.pick_button.isEnabled() is True


@pytest.fixture
def isolated_history(tmp_path, monkeypatch):
    hist = tmp_path / "history.json"
    monkeypatch.setattr("app.history.history_path", lambda: hist)
    return hist


def _item(tmp_path, ident, title, status="success", kind="piano", keyboard=True):
    kb = tmp_path / f"{ident}_键盘谱.txt"
    if keyboard:
        kb.write_text("k", encoding="utf-8")
    return HistoryItem(
        id=ident,
        title=title,
        source_path="x",
        kind=kind,
        created_at="2026-01-01T00:00:00",
        status=status,
        midi_path="",
        musicxml_path="",
        folder=str(tmp_path),
        error="" if status != "failed" else "坏了",
        keyboard_path=str(kb) if keyboard else "",
    )


@pytest.mark.parametrize(
    "name,label",
    [("a.mp3", "音频"), ("a.mid", "MIDI"), ("a.musicxml", "MusicXML"), ("a.pdf", "PDF"), ("a.png", "图片")],
)
def test_set_source_file_shows_type_label(tmp_path, isolated_history, name, label):
    app = QApplication.instance() or QApplication([])
    win = MainWindow(engines={})
    assert win.drop_filled.isHidden() is True
    src = tmp_path / name
    src.write_bytes(b"x" * 2048)
    win.set_source_file(src)
    assert win.file_type_label.text() == label
    assert win.drop_filled.isHidden() is False
    assert win.drop_empty.isHidden() is True
    assert "KB" in win.file_meta_label.text()
    assert win.options_box.isHidden() is (label != "音频")


def test_file_dialog_filter_lists_all_supported_first():
    from app.ui.main_window import all_files_filter

    filters = all_files_filter().split(";;")
    assert filters[0].startswith("全部支持的文件")
    assert "*.mp3" in filters[0] and "*.pdf" in filters[0] and "*.mid" in filters[0]
    assert len(filters) >= 5


def test_running_state_and_success_banner(tmp_path, isolated_history):
    app = QApplication.instance() or QApplication([])
    win = MainWindow(engines={})
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    win.set_source_file(src)
    assert win.result_banner.isHidden() is True
    win._enter_busy_ui()
    assert win.start_button.text() == "取消"
    assert win.start_button.property("danger") is True
    assert win.pick_button.isEnabled() is False
    win._on_progress("正在识别", 0.5)
    assert win.percent_label.text() == "50%"
    assert "已用" in win.elapsed_label.text()
    kb = tmp_path / "t_键盘谱.txt"
    kb.write_text("k", encoding="utf-8")
    result = ConvertResult(
        status="success",
        folder=tmp_path,
        midi_path=tmp_path / "t.mid",
        musicxml_path=tmp_path / "t.musicxml",
        error="",
        title="t",
        kind="piano",
        source_path=src,
        keyboard_path=kb,
    )
    win._on_finished(result)
    assert win._busy is False
    assert win.start_button.property("danger") is False
    assert win.result_banner.isHidden() is False
    assert win.banner_title.text() == "转换完成"
    assert win.banner_keyboard_button.isEnabled() is True
    assert win.banner_folder_button.isEnabled() is True
    assert win.history_list.count() == 1
    assert isolated_history.exists()
    # the new keyboard score opens in 预览 right away
    assert win.current_tab() == "preview"
    assert win.preview.showing_score() is True
    assert win.preview.text() == "k"


def test_failed_result_shows_error_inline(tmp_path, isolated_history, monkeypatch):
    def no_box(*_a, **_k):
        raise AssertionError("QMessageBox should not be used for failed results")

    monkeypatch.setattr("app.ui.main_window.QMessageBox.warning", no_box)
    app = QApplication.instance() or QApplication([])
    win = MainWindow(engines={})
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    win.set_source_file(src)
    win._enter_busy_ui()
    result = ConvertResult(
        status="failed",
        folder=tmp_path,
        midi_path=Path(),
        musicxml_path=Path(),
        error="解码失败",
        title="t",
        kind="piano",
        source_path=src,
        keyboard_path=Path(),
    )
    win._on_finished(result)
    assert win.result_banner.isHidden() is False
    assert win.result_banner.property("kind") == "error"
    assert "解码失败" in win.banner_detail.text()
    assert win.banner_keyboard_button.isHidden() is True


def test_history_empty_state(isolated_history):
    app = QApplication.instance() or QApplication([])
    win = MainWindow(engines={})
    assert win.history_list.count() == 0
    assert win.history_empty.isHidden() is False
    assert "还没有" in win.history_empty.text()


def test_history_search_filters_by_title(tmp_path, isolated_history):
    append_item(_item(tmp_path, "1", "月光奏鸣曲"))
    append_item(_item(tmp_path, "2", "卡农"))
    append_item(_item(tmp_path, "3", "月亮代表我的心", status="failed", keyboard=False))
    app = QApplication.instance() or QApplication([])
    win = MainWindow(engines={})
    assert win.history_list.count() == 3
    assert win.history_count.text() == "3"
    win.search_edit.setText("月")
    assert win.visible_history_count() == 2
    assert win.history_empty.isHidden() is True
    win.search_edit.setText("不存在")
    assert win.visible_history_count() == 0
    assert win.history_empty.isHidden() is False
    assert "不存在" in win.history_empty.text()
    win.search_edit.clear()
    assert win.visible_history_count() == 3


def test_history_row_chips_and_double_click(tmp_path, isolated_history, monkeypatch):
    opened = []
    monkeypatch.setattr("app.ui.main_window._startfile", lambda p: opened.append(p))
    append_item(_item(tmp_path, "1", "成功的", kind="pdf"))
    append_item(_item(tmp_path, "2", "失败的", status="failed", keyboard=False))
    app = QApplication.instance() or QApplication([])
    win = MainWindow(engines={})
    rows = win.history_rows()
    failed, ok = rows[0], rows[1]
    assert failed.status_chip.text() == "失败"
    assert failed.status_chip.property("tone") == "failed"
    assert failed.keyboard_button.isHidden() is True
    assert ok.status_chip.text() == "成功"
    assert ok.kind_chip.text() == "PDF"
    win._on_history_activated(win.history_list.item(1))
    assert opened and opened[0].endswith("1_键盘谱.txt")
    opened.clear()
    win._on_history_activated(win.history_list.item(0))
    assert opened == []

def test_score_style_switch_is_remembered(_isolated_settings):
    app = QApplication.instance() or QApplication([])
    win = MainWindow(engines={})
    assert win.detailed_radio.isChecked() is True
    assert win.score_style() == "detailed"
    assert "两行" in win.style_help.text()
    win.simple_radio.setChecked(True)
    assert win.score_style() == "simple"
    assert "一行" in win.style_help.text()
    assert '"simple"' in _isolated_settings.read_text(encoding="utf-8")
    again = MainWindow(engines={})
    assert again.simple_radio.isChecked() is True


def test_score_style_reaches_the_conversion(tmp_path, monkeypatch):
    seen = {}

    def fake_run_score(source, cancel, output_root=None, on_progress=None, style="detailed", **layout):
        seen["style"] = style
        return None

    monkeypatch.setattr("app.convert.run_score", fake_run_score)
    app = QApplication.instance() or QApplication([])
    win = MainWindow(engines={})
    win.simple_radio.setChecked(True)
    convert = win._make_default_convert()
    from threading import Event

    convert(tmp_path / "song.mid", "score", Event(), None)
    assert seen["style"] == "simple"


# ---------------------------------------------------------------- modern UI: theme, preview, history, toast

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QPalette, QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from app.history import load_items  # noqa: E402
from app.settings import load_settings  # noqa: E402
from app.ui import theme as theme_module  # noqa: E402
from app.ui.preview import ScoreView, line_roles, score_meta, split_sections, strip_dots  # noqa: E402
from app.ui.theme import DARK, LIGHT  # noqa: E402

DOT_ABOVE = "\u0307"
DOT_BELOW = "\u0323"
SCORE = (
    "# 测试曲\n"
    "# 1=C  4/4  ♩=100  每拍 1 格（一格 = 四分音符）  原神风物之诗琴 C3–B5 三排白键\n"
    "# 读法：| 是小节线\n"
    "\n"
    "【简谱】\n"
    f"1 旋律 | 1{DOT_ABOVE}     5 |\n"
    f"  伴奏 | (1{DOT_BELOW}3{DOT_BELOW}) 0 |\n"
    "\n"
    "【键盘谱】\n"
    "1 旋律 | Q     G |\n"
    "  伴奏 | (ZC) 0 |\n"
)
KEYBOARD_PART = "1 旋律 | Q     G |\n  伴奏 | (ZC) 0 |"
JIANPU_PART = f"1 旋律 | 1{DOT_ABOVE}     5 |\n  伴奏 | (1{DOT_BELOW}3{DOT_BELOW}) 0 |"


def _app():
    return QApplication.instance() or QApplication([])


def _score_item(tmp_path, ident, title, text=SCORE, created="2026-01-01T00:00:00"):
    kb = tmp_path / f"{ident}_键盘谱.txt"
    kb.write_text(text, encoding="utf-8")
    return HistoryItem(
        id=ident, title=title, source_path=f"{title}.mid", kind="score", created_at=created, status="success",
        midi_path="", musicxml_path="", folder=str(tmp_path), error="", keyboard_path=str(kb),
    )


def test_theme_switch_is_live_and_remembered(_isolated_settings, isolated_history):
    app = _app()
    win = MainWindow(engines={})
    assert win.theme.mode == "system"
    assert win.theme_system_radio.isChecked() is True
    win.theme_dark_radio.setChecked(True)
    assert win.theme.mode == "dark"
    assert win.theme.effective == "dark"
    assert DARK["surface"] in app.styleSheet()
    assert app.palette().color(QPalette.ColorRole.Window).name() == DARK["bg"]
    assert win.preview.view.highlighter._tokens["name"] == "dark"
    assert win.progress_bar._tokens["name"] == "dark"
    assert load_settings()["theme"] == "dark"
    again = MainWindow(engines={})
    assert again.theme_dark_radio.isChecked() is True
    assert again.theme.effective == "dark"
    again.theme_light_radio.setChecked(True)
    assert again.theme.effective == "light"
    assert LIGHT["surface"] in app.styleSheet()
    assert load_settings()["theme"] == "light"


def test_theme_follows_the_system_scheme(_isolated_settings, isolated_history, monkeypatch):
    _app()
    scheme = {"now": "dark"}
    monkeypatch.setattr(theme_module, "system_scheme", lambda: scheme["now"])
    win = MainWindow(engines={})
    assert win.theme.mode == "system"
    assert win.theme.effective == "dark"
    seen = []
    win.theme.changed.connect(seen.append)
    scheme["now"] = "light"
    win.theme._on_system_scheme_changed()  # what Qt calls when Windows switches
    assert win.theme.effective == "light"
    assert seen == ["light"]
    win.theme_light_radio.setChecked(True)
    scheme["now"] = "dark"
    win.theme._on_system_scheme_changed()
    assert win.theme.effective == "light"  # an explicit choice wins over the system


def test_score_text_helpers():
    sections = split_sections(SCORE)
    assert sections["keyboard"] == KEYBOARD_PART
    assert sections["jianpu"] == JIANPU_PART
    assert sections["all"] == SCORE.rstrip("\n")
    assert split_sections("只有一行")["all"] == "只有一行" and "keyboard" not in split_sections("只有一行")
    assert score_meta(SCORE) == ["4/4", "♩=100", "每拍 1 格"]
    shown, dots, origin = strip_dots(JIANPU_PART)
    assert DOT_ABOVE not in shown and DOT_BELOW not in shown
    assert dots == {0: [(7, "above")], 1: [(8, "below"), (9, "below")]}
    assert len(origin) == len(shown) + 1
    roles = line_roles("1 旋律 | 〔♩=76〕 Q- (ZC) 0 |")
    assert roles[0] == "number" and roles[2] == "melody" and roles[5] == "bar"
    assert roles[7] == "mark" and roles[12] == "mark"
    assert roles[14] == "key" and roles[15] == "rest" and roles[17] == "bracket" and roles[22] == "rest"
    assert line_roles("  伴奏 | z0 |")[2] == "accomp"


def test_preview_loads_sections_copies_and_remembers(tmp_path, isolated_history, _isolated_settings):
    app = _app()
    win = MainWindow(engines={})
    preview = win.preview
    assert preview.is_empty() is True
    kb = tmp_path / "测试曲_键盘谱.txt"
    kb.write_text(SCORE, encoding="utf-8")
    assert preview.show_file(kb, title="测试曲") is True
    assert preview.title_label.text() == "测试曲"
    assert "♩=100" in preview.meta_label.text()
    assert preview.section() == "keyboard"
    assert preview.text() == KEYBOARD_PART
    preview.jianpu_radio.setChecked(True)
    assert preview.section() == "jianpu"
    assert preview.text() == JIANPU_PART
    # dots are painted, not typed: the view holds the digits, copying gives the original back
    assert DOT_ABOVE not in preview.view.toPlainText()
    assert preview.view.dots() == {0: [(7, "above")], 1: [(8, "below"), (9, "below")]}
    preview.view.selectAll()
    assert preview.view.createMimeDataFromSelection().text() == JIANPU_PART
    preview.all_radio.setChecked(True)
    assert preview.text() == SCORE.rstrip("\n")
    assert load_settings()["preview_section"] == "all"
    preview.keyboard_radio.setChecked(True)
    preview.copy_button.click()
    assert app.clipboard().text() == KEYBOARD_PART
    assert win.toast.isHidden() is False
    assert win.toast.text() == "已复制"
    # a file without the two sections falls back to 全部
    plain = tmp_path / "旧格式_键盘谱.txt"
    plain.write_text("# 旧格式\nQ W E", encoding="utf-8")
    preview.show_file(plain)
    assert preview.shown_section() == "all" and preview.text() == "# 旧格式\nQ W E"
    assert preview.jianpu_radio.isEnabled() is False
    assert preview.section() == "keyboard"  # the remembered choice is kept for the next file


def test_preview_zoom_buttons_wheel_and_memory(tmp_path, isolated_history, _isolated_settings):
    _app()
    win = MainWindow(engines={})
    preview = win.preview
    assert preview.zoom() == 100
    preview.zoom_in_button.click()
    assert preview.zoom() == 110
    assert preview.zoom_label.text() == "110%"
    wheel = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.ControlModifier, Qt.ScrollPhase.NoScrollPhase, False)
    preview.view.wheelEvent(wheel)
    assert preview.zoom() == 125
    preview.zoom_out_button.click()
    assert preview.zoom() == 110
    assert load_settings()["preview_zoom"] == 110
    again = MainWindow(engines={})
    assert again.preview.zoom() == 110
    for _ in range(20):
        again.preview.zoom_out()
    assert again.preview.zoom() == 70 and again.preview.zoom_out_button.isEnabled() is False


def test_preview_states_for_failed_and_missing(tmp_path, isolated_history):
    _app()
    win = MainWindow(engines={})
    failed = _item(tmp_path, "9", "坏掉的录音", status="failed", keyboard=False)
    assert win.preview.show_item(failed) is False
    assert "坏了" in win.preview.message_state.text()
    assert win.preview.copy_button.isEnabled() is False
    gone = _score_item(tmp_path, "8", "不见了")
    Path(gone.keyboard_path).unlink()
    assert win.preview.show_item(gone) is False
    assert "不在了" in win.preview.message_state.text()
    win.preview.clear()
    assert win.preview.is_empty() is True


def test_history_single_click_previews_double_click_opens(tmp_path, isolated_history, monkeypatch):
    opened = []
    monkeypatch.setattr("app.ui.main_window._startfile", lambda p: opened.append(p))
    append_item(_score_item(tmp_path, "1", "小星星", created="2026-01-01T00:00:00"))
    append_item(_score_item(tmp_path, "2", "欢乐颂", created="2026-01-02T00:00:00"))
    _app()
    win = MainWindow(engines={})
    assert win.current_tab() == "history"
    assert win.preview.current_item_id() == "2"  # the newest score is ready in 预览
    star = win.history_list.item(1)
    win._on_history_clicked(star)
    assert win.preview.current_item_id() == "1"
    assert win.current_tab() == "history"  # waits: this might be the first half of a double click
    QTest.qWait(QApplication.doubleClickInterval() + 80)
    assert win.current_tab() == "preview"
    assert win.preview.text() == KEYBOARD_PART

    win.show_history_tab()
    win.show()
    QTest.qWaitForWindowExposed(win)
    win._fit_history_rows()
    QApplication.processEvents()
    row = win._row_for(star)
    corner = QPoint(8, 8)
    # what Windows sends for a double click: press, release, double-click, release
    QTest.mouseClick(row, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, corner)
    QTest.mouseDClick(row, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, corner)
    QTest.mouseRelease(row, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, corner)
    assert opened and opened[0].endswith("1_键盘谱.txt")
    QTest.qWait(QApplication.doubleClickInterval() + 80)
    assert win.current_tab() == "history"  # a double click does not jump to 预览
    win.close()


def test_history_context_menu_and_remove_with_undo(tmp_path, isolated_history, monkeypatch):
    opened = []
    monkeypatch.setattr("app.ui.main_window._startfile", lambda p: opened.append(p))
    first = _score_item(tmp_path, "1", "小星星")
    append_item(first)
    append_item(_score_item(tmp_path, "2", "欢乐颂"))
    _app()
    win = MainWindow(engines={})
    row = win._row_for(win.history_list.item(1))
    assert row.item.id == "1"
    menu = win._history_menu(row)
    labels = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert labels == ["预览", "打开键盘谱", "打开文件夹", "从记录中移除"]
    assert all(a.isEnabled() for a in menu.actions() if not a.isSeparator())
    menu.actions()[0].trigger()
    assert win.current_tab() == "preview" and win.preview.current_item_id() == "1"
    menu.actions()[1].trigger()
    assert opened and opened[-1].endswith("1_键盘谱.txt")

    remove = [a for a in menu.actions() if a.text() == "从记录中移除"][0]
    remove.trigger()
    assert [i.id for i in load_items()] == ["2"]
    assert Path(first.keyboard_path).exists()  # only the record goes, the files stay
    assert win.history_count.text() == "1"
    assert win.preview.is_empty() is True
    assert "已从记录中移除" in win.toast.text()
    assert win.toast.action_button.text() == "撤销"
    win.toast.action_button.click()
    assert [i.id for i in load_items()] == ["1", "2"]
    assert win.history_count.text() == "2"
    assert win.toast.text() == "已恢复"


def test_ctrl_f_search_and_delete_key(tmp_path, isolated_history):
    append_item(_score_item(tmp_path, "1", "小星星"))
    append_item(_score_item(tmp_path, "2", "欢乐颂"))
    _app()
    win = MainWindow(engines={})
    win.show_preview_tab()
    assert any(s.key().toString() == "Ctrl+F" for s in win._shortcuts)
    win.focus_search()
    assert win.current_tab() == "history"
    assert win.search_edit.isHidden() is False
    win.history_list.setCurrentRow(0)
    QTest.keyClick(win.history_list, Qt.Key.Key_Delete)
    assert [i.id for i in load_items()] == ["1"]


def test_finished_while_looking_at_history_shows_a_toast(tmp_path, isolated_history):
    _app()
    win = MainWindow(engines={})
    src = tmp_path / "小星星.mid"
    src.write_bytes(b"x")
    win.set_source_file(src)
    win._enter_busy_ui()
    win.show_preview_tab()
    win.show_history_tab()  # the user switches to 记录 while it runs
    kb = tmp_path / "小星星_键盘谱.txt"
    kb.write_text(SCORE, encoding="utf-8")
    win._on_finished(ConvertResult(
        status="success", folder=tmp_path, midi_path=src, musicxml_path=tmp_path / "x.musicxml", error="",
        title="小星星", kind="score", source_path=src, keyboard_path=kb,
    ))
    assert win.current_tab() == "history"
    assert "转换完成" in win.toast.text()
    assert win.toast.action_button.text() == "查看"
    win.toast.action_button.click()
    assert win.current_tab() == "preview"
    assert win.preview.text() == KEYBOARD_PART


def test_toast_shows_runs_action_and_fades(isolated_history):
    _app()
    win = MainWindow(engines={})
    assert win.toast.isHidden() is True
    win.show_toast("已复制", "success")
    assert win.toast.isHidden() is False
    assert (win.toast.text(), win.toast.tone()) == ("已复制", "success")
    assert win.toast.action_button.isHidden() is True
    called = []
    win.show_toast("已从记录中移除", "info", "撤销", lambda: called.append(1))
    assert win.toast.action_button.isHidden() is False
    win.toast.action_button.click()
    assert called == [1]
    QTest.qWait(400)
    assert win.toast.isHidden() is True
    win.show_toast("出错了", "error", duration_ms=800)
    QTest.qWait(1300)
    assert win.toast.isHidden() is True


def test_missing_file_is_reported_with_a_toast(tmp_path, isolated_history, monkeypatch):
    def no_box(*_a, **_k):
        raise AssertionError("a missing file should not open a dialog")

    monkeypatch.setattr("app.ui.main_window.QMessageBox.warning", no_box)
    _app()
    win = MainWindow(engines={})
    from app.ui.main_window import open_path

    assert open_path(win, tmp_path / "不见了.txt") is False
    assert win.toast.tone() == "error"
    assert "文件不在了" in win.toast.text()


def test_failed_worker_shows_the_reason_inline(tmp_path, isolated_history, monkeypatch):
    def no_box(*_a, **_k):
        raise AssertionError("errors are shown in the banner")

    monkeypatch.setattr("app.ui.main_window.QMessageBox.warning", no_box)
    _app()
    win = MainWindow(engines={})
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    win.set_source_file(src)
    win._enter_busy_ui()
    win._on_failed("不支持这个音频文件")
    assert win._busy is False
    assert win.result_banner.property("kind") == "error"
    assert "不支持这个音频文件" in win.banner_detail.text()


def test_progress_is_smooth_and_numeric(tmp_path, isolated_history):
    _app()
    win = MainWindow(engines={})
    win._enter_busy_ui()
    win._on_progress("转录", 0.3)
    assert win.progress_bar.ratio() == pytest.approx(0.3)
    assert win.percent_label.text() == "30%"
    assert win.status_label.text() == "转录"
    QTest.qWait(600)
    assert win.progress_bar.shown_ratio() == pytest.approx(0.3, abs=0.01)
    assert win.progress_bar._running is True
    win._reset_idle_ui("已取消")
    assert win.progress_bar._running is False


def test_score_rows_stay_aligned_with_real_fonts():
    """With the Windows fonts, every 「|」 of the 伴奏 row sits exactly under the 旋律 row's."""
    from PySide6.QtGui import QFontDatabase

    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    mono = [fonts / name for name in ("CascadiaMono.ttf", "consola.ttf") if (fonts / name).exists()]
    cjk = fonts / "msyh.ttc"
    if not mono or not cjk.exists():
        pytest.skip("needs the Windows monospace and CJK fonts")
    app = _app()
    for path in [*mono, cjk, fonts / "seguisym.ttf"]:
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    view = ScoreView()
    view.resize(1000, 300)
    view.show()
    view.set_score_text(
        f" 9 旋律 | 〔♩=76〕 1{DOT_ABOVE}     1{DOT_ABOVE} 5 | 6     -   |\n"
        f"   伴奏 |          {{1{DOT_BELOW}3{DOT_BELOW}5{DOT_BELOW}}} - 0 | (461) 2 0 |"
    )
    app.processEvents()

    def bar_x(number: int) -> list[float]:
        block = view.document().findBlockByNumber(number)
        line = block.layout().lineAt(0)
        xs = []
        for index, ch in enumerate(block.text()):
            if ch == "|":
                value = line.cursorToX(index)
                xs.append(float(value[0] if isinstance(value, tuple) else value))
        return xs

    top, bottom = bar_x(0), bar_x(1)
    assert len(top) == len(bottom) == 3
    assert top == pytest.approx(bottom, abs=0.75)
    view.close()

def test_arpeggio_and_chord_settings_are_remembered(_isolated_settings):
    app = QApplication.instance() or QApplication([])
    win = MainWindow(engines={})
    assert win.advanced_box.isHidden() is True
    win.advanced_button.setChecked(True)
    assert win.advanced_box.isHidden() is False
    assert (win.arpeggio_mode(), win.chord_mode()) == ("auto", "standard")
    win.arpeggio_chord_radio.setChecked(True)
    win.chord_loose_radio.setChecked(True)
    assert "( )" in win.arpeggio_help.text() and "80" in win.chord_help.text()
    again = MainWindow(engines={})
    assert (again.arpeggio_mode(), again.chord_mode()) == ("chord", "loose")


def test_layout_options_reach_the_conversion(tmp_path, monkeypatch):
    seen = {}

    def fake_run_score(source, cancel, output_root=None, on_progress=None, style="detailed", arpeggio="auto", chord="standard"):
        seen.update(style=style, arpeggio=arpeggio, chord=chord)
        return None

    monkeypatch.setattr("app.convert.run_score", fake_run_score)
    app = QApplication.instance() or QApplication([])
    win = MainWindow(engines={})
    win.arpeggio_spread_radio.setChecked(True)
    win.chord_strict_radio.setChecked(True)
    from threading import Event

    win._make_default_convert()(tmp_path / "song.mid", "score", Event(), None)
    assert seen == {"style": "detailed", "arpeggio": "spread", "chord": "strict"}


def test_changing_the_arpeggio_setting_lays_out_the_preview_again(tmp_path, isolated_history):
    from threading import Event

    from app.convert import run_score

    midi = tmp_path / "roll.mid"
    pm = pretty_midi.PrettyMIDI(initial_tempo=120)
    inst = pretty_midi.Instrument(program=0)
    inst.notes.append(pretty_midi.Note(80, 64, 0.0, 0.45))
    for p, t in zip((48, 52, 55, 60), (0.375, 0.4167, 0.4583, 0.5)):   # a roll played out
        inst.notes.append(pretty_midi.Note(80, p, t, 1.5))
    pm.instruments.append(inst)
    pm.write(str(midi))
    result = run_score(midi, Event(), output_root=tmp_path / "out", style="simple")
    app = QApplication.instance() or QApplication([])
    win = MainWindow(engines={})
    win.simple_radio.setChecked(True)
    item = win._save_history(result)
    win.reload_history()
    win.select_history_id(item.id)
    assert "{ZCBA}" in result.keyboard_path.read_text(encoding="utf-8")
    win.arpeggio_chord_radio.setChecked(True)
    text = result.keyboard_path.read_text(encoding="utf-8")
    assert "(ZCBA)" in text and "{" not in text.split("【键盘谱】")[1]
    assert "(ZCBA)" in win.preview.text()
