"""Jianpu (numbered notation) recognition on a synthetic page drawn with PIL."""
from pathlib import Path
from threading import Event

import pytest
from PIL import Image, ImageDraw, ImageFont

from app import jianpu


def _font(size: int):
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    pytest.skip("no TrueType font available to draw a test page")


def _draw_page(path: Path) -> None:
    """Two systems, two hands each, 4/4. Right hand: 1 2 3 4 | 5 - 5 - (quarters + dashes),
    then 1_ 2_ 3 'i 7 | i... ; left hand: ,5 ,1 ,5 ,1 | ..."""
    W, H = 1400, 1000
    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img)
    font = _font(40)
    small = _font(28)
    dr.text((80, 40), "1=C 4/4", font=small, fill="black")
    dh = 30  # digit height is ~30 px at this font size

    def bar_lines(y_mid: int) -> None:
        for x in (160, 700, 1300):
            dr.line([(x, y_mid - 40), (x, y_mid + 40)], fill="black", width=3)

    def digit(x: int, y_mid: int, ch: str, dot_above=False, dot_below=False, underlines=0) -> None:
        bbox = dr.textbbox((0, 0), ch, font=font)
        w = bbox[2] - bbox[0]
        top = y_mid - (bbox[3] - bbox[1]) // 2 - bbox[1]
        dr.text((x - w // 2 - bbox[0], top), ch, font=font, fill="black")
        if dot_above:
            dr.ellipse([x - 3, y_mid - dh // 2 - 14, x + 3, y_mid - dh // 2 - 8], fill="black")
        for k in range(underlines):
            y = y_mid + dh // 2 + 6 + k * 6
            dr.line([(x - 12, y), (x + 12, y)], fill="black", width=3)
        if dot_below:
            y = y_mid + dh // 2 + 6 + underlines * 6 + 6
            dr.ellipse([x - 3, y, x + 3, y + 6], fill="black")

    def dash(x: int, y_mid: int) -> None:
        dr.line([(x - 14, y_mid), (x + 14, y_mid)], fill="black", width=4)

    for sys_no, y_rh in enumerate((250, 650)):
        y_lh = y_rh + 160
        bar_lines(y_rh)
        bar_lines(y_lh)
        if sys_no == 0:
            for i, ch in enumerate("1234"):
                digit(230 + i * 120, y_rh, ch)
            digit(770, y_rh, "5")
            dash(890, y_rh)
            digit(1010, y_rh, "5")
            dash(1130, y_rh)
        else:
            digit(230, y_rh, "1", underlines=1)
            digit(290, y_rh, "2", underlines=1)
            digit(410, y_rh, "3")
            digit(530, y_rh, "1", dot_above=True)
            digit(770, y_rh, "7")
            dash(890, y_rh)
            dash(1010, y_rh)
            dash(1130, y_rh)
        for b, x0 in enumerate((230, 770)):
            for i, ch in enumerate("5151"):
                digit(x0 + i * 120, y_lh, ch, dot_below=True)
    img.save(path)


def test_jianpu_page_is_detected_and_read(tmp_path):
    page = tmp_path / "jp.png"
    _draw_page(page)
    assert jianpu.looks_like_jianpu(page)
    score = jianpu.recognize_jianpu([page])
    assert score.beats_per_bar == 4
    rh = sorted((n.start_q, n.pitch) for n in score.notes if n.hand == 0)
    lh = sorted((n.start_q, n.pitch) for n in score.notes if n.hand == 1)
    # bar 1 right hand: 1 2 3 4 on beats 0..3 (C4 D4 E4 F4)
    assert rh[:4] == [(0.0, 60), (1.0, 62), (2.0, 64), (3.0, 65)]
    # bar 2: 5 held over the dash, then 5 again
    fives = [n for n in score.notes if n.hand == 0 and n.pitch == 67]
    assert [(n.start_q, n.end_q) for n in fives] == [(4.0, 6.0), (6.0, 8.0)]
    # bar 3: two eighths, a quarter, then high do
    assert rh[6:10] == [(8.0, 60), (8.5, 62), (9.0, 64), (10.0, 72)]
    # left hand: low 5 / low 1 every beat
    assert lh[:4] == [(0.0, 55), (1.0, 48), (2.0, 55), (3.0, 48)]


def test_staff_page_is_not_jianpu(tmp_path):
    img = Image.new("RGB", (1400, 1000), "white")
    dr = ImageDraw.Draw(img)
    for system in range(4):
        for line in range(5):
            y = 150 + system * 200 + line * 14
            dr.line([(100, y), (1300, y)], fill="black", width=2)
        dr.ellipse([300, 150 + system * 200 + 20, 316, 150 + system * 200 + 32], fill="black")
    page = tmp_path / "staff.png"
    img.save(page)
    assert not jianpu.looks_like_jianpu(page)


def test_parse_header_key_and_tempo():
    assert jianpu.parse_header(["1=C", "♩=72"]) == (0, 72.0)
    assert jianpu.parse_header(["1=bE 4/4", "=96"]) == (3, 96.0)
    assert jianpu.parse_header(["1=G"])[0] == 7


def test_run_pdf_converts_jianpu_image(tmp_path):
    from app.convert import run_pdf

    src = tmp_path / "简谱.png"
    _draw_page(src)
    result = run_pdf(src, Event(), output_root=tmp_path / "out")
    assert result.status == "success", result.error
    assert result.midi_path.is_file()
    text = result.keyboard_path.read_text(encoding="utf-8")
    assert "简谱自动识别" in text
    num = text.split("【数字谱】")[1].split("【键盘谱】")[0].strip()
    # beat 1: right-hand 1 with left-hand low 5; beat 2: right-hand 2 with low 1
    assert num.startswith("(-51)  (-12)")
