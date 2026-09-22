from pathlib import Path
from threading import Event

from music21 import note, stream

from app.convert import ConvertError, run_pdf
from app.pdf_omr import PdfOmrError, pick_musicxml


def test_pick_musicxml_prefers_merged(tmp_path):
    page = tmp_path / "a_0.musicxml"
    merged = tmp_path / "a_0_merged.musicxml"
    page.write_text("<score/>", encoding="utf-8")
    merged.write_text("<score/>", encoding="utf-8")
    assert pick_musicxml([page, merged]) == merged


def test_pick_musicxml_empty():
    try:
        pick_musicxml([])
        assert False
    except PdfOmrError as exc:
        assert "没有识别出乐谱" in str(exc)


def test_run_pdf_writes_keyboard_with_warning(tmp_path):
    src = tmp_path / "sheet.pdf"
    src.write_bytes(b"%PDF-1.4")

    def fake_recognize(pdf: Path, work_dir: Path, cancel: Event) -> Path:
        xml = work_dir / "sheet.musicxml"
        score = stream.Score()
        part = stream.Part()
        part.append(note.Note("C4", quarterLength=1))
        score.append(part)
        written = Path(str(score.write("musicxml", fp=str(xml))))
        if written != xml and written.exists():
            xml.write_bytes(written.read_bytes())
        return xml

    result = run_pdf(src, Event(), output_root=tmp_path, recognize=fake_recognize)
    assert result.status == "success"
    assert result.kind == "pdf"
    text = result.keyboard_path.read_text(encoding="utf-8")
    assert "准确度有限" in text
    assert "【键盘谱】" in text
    assert result.musicxml_path.exists()


def test_run_pdf_rejects_non_pdf(tmp_path):
    src = tmp_path / "a.mid"
    src.write_bytes(b"x")
    try:
        run_pdf(src, Event(), output_root=tmp_path)
        assert False
    except ConvertError as exc:
        assert "PDF" in str(exc)
