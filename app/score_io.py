from __future__ import annotations

from pathlib import Path

import pretty_midi


def write_notes_midi(
    dest: Path,
    notes: list[tuple[int, float, float, int]],
    program: int = 0,
    is_drum: bool = False,
) -> None:
    pm = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(program=program, is_drum=is_drum)
    for pitch, start, end, velocity in notes:
        inst.notes.append(
            pretty_midi.Note(velocity=int(velocity), pitch=int(pitch), start=float(start), end=float(end))
        )
    pm.instruments.append(inst)
    dest.parent.mkdir(parents=True, exist_ok=True)
    pm.write(str(dest))


def midi_to_musicxml(midi_path: Path, dest: Path, quarter_length_divisors: tuple[int, ...] | None = None) -> None:
    """MusicXML of a MIDI file. quarter_length_divisors: the rhythm grid to quantize to
    (cells per quarter note); music21's default grid otherwise."""
    from music21 import converter

    if quarter_length_divisors:
        score = converter.parse(str(midi_path), quarterLengthDivisors=quarter_length_divisors)
    else:
        score = converter.parse(str(midi_path))
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = score.write("musicxml", fp=str(dest))
    # music21 may write a different suffix; ensure dest exists with requested name
    from pathlib import Path as P

    out = P(str(written)) if written is not None else dest
    if out != dest and out.exists():
        dest.write_bytes(out.read_bytes())
        if out != dest:
            try:
                out.unlink()
            except OSError:
                pass


def musicxml_to_midi(xml_path: Path, dest: Path) -> None:
    from music21 import converter

    score = converter.parse(str(xml_path))
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = score.write("midi", fp=str(dest))
    out = Path(str(written)) if written is not None else dest
    if out != dest and out.exists():
        dest.write_bytes(out.read_bytes())
        out.unlink(missing_ok=True)


# A4 at Verovio's resolution (tenths of a millimetre)
_PAGE_W, _PAGE_H = 2100, 2970


def _flatten_svg(svg: str) -> str:
    """Verovio nests an <svg> for its own scaling, which Qt's SVG renderer skips; turn it
    into a scaled group."""
    import re

    outer = re.search(r'<svg[^>]*viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    inner = re.search(r'<svg class="definition-scale"([^>]*?)viewBox="0 0 ([\d.]+) ([\d.]+)"([^>]*)>', svg)
    if not outer or not inner:
        return svg
    sx = float(outer.group(1)) / float(inner.group(2))
    sy = float(outer.group(2)) / float(inner.group(3))
    svg = svg[: inner.start()] + f'<g class="definition-scale"{inner.group(1)}transform="scale({sx},{sy})"{inner.group(4)}>' + svg[inner.end():]
    last = svg.rfind("</svg>")
    inner_end = svg.rfind("</svg>", 0, last)
    if inner_end < 0:
        return svg
    return svg[:inner_end] + "</g>" + svg[inner_end + len("</svg>"):]


def musicxml_to_pdf(xml_path: Path, dest: Path, title: str | None = None) -> None:
    """Engrave a MusicXML score as an A4 staff-notation PDF (Verovio + Qt). The title is
    printed by Qt (Verovio's text does not survive Qt's SVG renderer)."""
    import verovio
    from PySide6.QtCore import QByteArray, QLoggingCategory, QMarginsF, QRectF, QSizeF, Qt
    from PySide6.QtGui import QFont, QGuiApplication, QPageLayout, QPageSize, QPainter, QPdfWriter
    from PySide6.QtSvg import QSvgRenderer

    # Qt's SVG renderer complains about SVG features it skips (text styling); the notes are fine
    QLoggingCategory.setFilterRules("qt.svg*=false")
    tk = verovio.toolkit()
    tk.setOptions({
        "pageWidth": _PAGE_W,
        "pageHeight": _PAGE_H,
        "pageMarginLeft": 100,
        "pageMarginRight": 100,
        "pageMarginTop": 200,
        "pageMarginBottom": 80,
        "scale": 40,
        "adjustPageHeight": False,
        "breaks": "auto",
        "header": "none",
        "footer": "none",
        "svgViewBox": True,
    })
    # loadData, not loadFile: Verovio cannot open paths with Chinese characters on Windows
    if not tk.loadData(Path(xml_path).read_text(encoding="utf-8", errors="replace")):
        raise ValueError("五线谱 PDF 导出失败：读不了 MusicXML")
    pages = tk.getPageCount()
    if pages < 1:
        raise ValueError("五线谱 PDF 导出失败：谱子是空的")
    if QGuiApplication.instance() is None:  # fonts need a Qt application
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        QApplication([])  # a widgets application, so the GUI can still start later
    dest.parent.mkdir(parents=True, exist_ok=True)
    name = title or Path(xml_path).stem
    writer = QPdfWriter(str(dest))
    writer.setResolution(300)
    writer.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait, QMarginsF(0, 0, 0, 0)))
    writer.setTitle(name)
    writer.setCreator("PianoConvert")
    painter = QPainter(writer)
    try:
        area = writer.pageLayout().paintRectPixels(writer.resolution())
        for page in range(1, pages + 1):
            if page > 1:
                writer.newPage()
            renderer = QSvgRenderer(QByteArray(_flatten_svg(tk.renderToSVG(page)).encode("utf-8")))
            size = renderer.defaultSize()
            scale = min(area.width() / max(1, size.width()), area.height() / max(1, size.height()))
            target = QSizeF(size.width() * scale, size.height() * scale)
            renderer.render(painter, QRectF(area.x(), area.y(), target.width(), target.height()))
            if page == 1:
                font = QFont(["Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "Arial"])
                font.setPixelSize(int(area.width() * 0.026))
                font.setBold(True)
                painter.setFont(font)
                painter.setPen(Qt.GlobalColor.black)
                band = QRectF(area.x(), area.y() + area.height() * 0.022, area.width(), area.height() * 0.04)
                painter.drawText(band, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), name)
    finally:
        painter.end()
    if not dest.exists() or dest.stat().st_size == 0:
        raise ValueError("五线谱 PDF 导出失败")
