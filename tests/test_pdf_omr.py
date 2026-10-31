from pathlib import Path
from threading import Event

from music21 import note, stream

from app.convert import ConvertError, run_pdf
from app.pdf_omr import PdfOmrError, _command, merge_pages, pick_musicxml, render_pages


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


PAGE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef></attributes>
      <note><pitch><step>{a}</step><octave>4</octave></pitch><duration>4</duration><type>whole</type></note>
    </measure>
    <measure number="2">
      <note><pitch><step>{b}</step><octave>4</octave></pitch><duration>4</duration><type>whole</type></note>
    </measure>
  </part>
</score-partwise>
"""


def test_merge_pages_appends_every_page(tmp_path):
    from music21 import converter

    pages = []
    for i, (a, b) in enumerate([("C", "D"), ("E", "F"), ("G", "A")], start=1):
        page = tmp_path / f"page_{i:03d}.musicxml"
        page.write_text(PAGE_XML.format(a=a, b=b), encoding="utf-8")
        pages.append(page)
    empty = tmp_path / "page_004.musicxml"
    empty.write_text("<score-partwise><part id='P1'><measure/></part></score-partwise>", encoding="utf-8")
    pages.append(empty)

    merged = merge_pages(pages, tmp_path / "x_merged.musicxml")
    score = converter.parse(str(merged))
    names = [n.name for n in score.flatten().notes]
    assert names == ["C", "D", "E", "F", "G", "A"]
    numbers = [m.number for m in score.parts[0].getElementsByClass("Measure")]
    assert numbers == [1, 2, 3, 4, 5, 6]


def test_merge_pages_without_notes_fails(tmp_path):
    empty = tmp_path / "page_001.musicxml"
    empty.write_text("<score-partwise><part id='P1'><measure/></part></score-partwise>", encoding="utf-8")
    try:
        merge_pages([empty], tmp_path / "m.musicxml")
        assert False
    except PdfOmrError as exc:
        assert "没有识别出乐谱" in str(exc)


def test_render_pages_makes_one_png_per_page(tmp_path):
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument.new()
    doc.new_page(595, 842)
    doc.new_page(595, 842)
    pdf = tmp_path / "two.pdf"
    doc.save(str(pdf))
    doc.close()
    images = render_pages(pdf, tmp_path / "pages", width=600)
    assert [p.name for p in images] == ["page_001.png", "page_002.png"]
    assert all(p.is_file() for p in images)


def test_render_pages_bad_pdf(tmp_path):
    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(b"not a pdf")
    try:
        render_pages(pdf, tmp_path / "pages")
        assert False
    except PdfOmrError:
        pass


def test_homr_command_targets_page_folder(tmp_path):
    cmd = _command(tmp_path)
    assert cmd[-1] == str(tmp_path)
    assert "--no-title" not in cmd


SLUR_XML = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef></attributes>
      <note><pitch><step>C</step><octave>4</octave></pitch><duration>2</duration><voice>1</voice><type>half</type><staff>1</staff>
        <notations><slur type="start" number="1"/></notations></note>
      <note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration><voice>1</voice><type>quarter</type><staff>1</staff>
        <notations><slur type="stop" number="1"/></notations></note>
      <note><pitch><step>D</step><octave>4</octave></pitch><duration>1</duration><voice>1</voice><type>quarter</type><staff>1</staff>
        <notations><slur type="start" number="1"/></notations></note>
    </measure>
    <measure number="2">
      <note><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice><type>whole</type><staff>1</staff>
        <notations><slur type="stop" number="1"/></notations></note>
    </measure>
  </part>
</score-partwise>
"""


def test_same_pitch_slur_becomes_tie_and_is_not_replayed(tmp_path):
    from app.keyboard_score import notes_from_musicxml

    page = tmp_path / "page_001.musicxml"
    page.write_text(SLUR_XML, encoding="utf-8")
    merged = merge_pages([page], tmp_path / "m_merged.musicxml")
    text = merged.read_text(encoding="utf-8")
    assert text.count('<tie type="start"') == 1
    assert text.count('<tie type="stop"') == 1
    notes, _tempos, _meters = notes_from_musicxml(merged)
    # C (held over the tie), D, E — the tied C is pressed once; the D→E slur stays a slur
    assert [n.pitch for n in notes] == [60, 62, 64]


def test_recognize_runs_homr_with_relative_path(tmp_path, monkeypatch):
    """OpenCV cannot open Windows paths with Chinese characters, so homr must get '.'"""
    import app.pdf_omr as omr

    seen = {}

    class FakeProc:
        returncode = 0

        def __init__(self, cmd, cwd, **_kw):
            seen["cmd"], seen["cwd"] = cmd, cwd
            import io

            self.stdout = io.StringIO("")

        def poll(self):
            return 0

    monkeypatch.setattr(omr.subprocess, "Popen", FakeProc)
    pages = tmp_path / "中文目录"
    pages.mkdir()
    omr._run_homr(pages, 1, Event(), lambda _m, _p: None)
    assert seen["cmd"][-1] == "."
    assert seen["cwd"] == str(pages)


def _fake_homr(monkeypatch, code: str) -> None:
    """Make _run_homr start a small Python script instead of homr."""
    import sys

    import app.pdf_omr as omr

    monkeypatch.setattr(omr, "_command", lambda _target: [sys.executable, "-c", code])


def test_cancel_works_while_homr_prints_nothing(tmp_path, monkeypatch):
    """A silent or stuck homr process must not block 取消 (it used to wait in readline)."""
    import threading
    import time

    import app.pdf_omr as omr
    from app.engines.base import CancelledError

    _fake_homr(monkeypatch, "import time; time.sleep(60)")
    cancel = Event()
    threading.Timer(0.5, cancel.set).start()
    started = time.monotonic()
    try:
        omr._run_homr(tmp_path, 1, cancel, lambda _m, _p: None)
        assert False
    except CancelledError:
        pass
    assert time.monotonic() - started < 10


def test_homr_download_and_page_lines_show_progress(tmp_path, monkeypatch):
    import app.pdf_omr as omr

    _fake_homr(
        monkeypatch,
        "import sys\n"
        "e = sys.stderr\n"
        "print('Downloading 1 models - this is only required once', file=e)\n"
        "print('\\rDownloaded 5 of 10 MB (50%)', end='', file=e, flush=True)\n"
        "print('\\rDownloaded 10 of 10 MB (100%)', file=e)\n"
        "print('Processing ./page_001.png', file=e)\n",
    )
    seen = []
    omr._run_homr(tmp_path, 1, Event(), lambda message, _ratio: seen.append(message))
    assert "下载识别模型（仅第一次）· 5/10 MB" in seen
    assert seen[-1] == "识别第 1/1 页（准确度有限）"


def test_failed_homr_download_is_reported_as_network_problem(tmp_path, monkeypatch):
    import app.pdf_omr as omr

    _fake_homr(
        monkeypatch,
        "import sys\n"
        "print('Exception: Failed to download file https://github.com/x.zip.', file=sys.stderr)\n"
        "sys.exit(3)\n",
    )
    try:
        omr._run_homr(tmp_path, 1, Event(), lambda _m, _p: None)
        assert False
    except PdfOmrError as exc:
        assert str(exc) == omr.DOWNLOAD_FAILED


def test_homr_process_ends_with_an_exit_code_instead_of_a_crash_dialog(monkeypatch, capsys):
    """In the windowed app an uncaught error opens a modal box that keeps homr alive."""
    import app.pdf_omr as omr

    def _boom() -> None:
        raise RuntimeError("Failed to download file")

    monkeypatch.setattr(omr, "_redirect_homr_models", _boom)
    try:
        omr.homr_entry(".")
        assert False
    except SystemExit as exc:
        assert exc.code == omr.HOMR_EXIT_FAILED
    err = capsys.readouterr().err
    assert f"{omr.HOMR_FAILED}: RuntimeError: Failed to download file" in err
    assert "Traceback" in err


def _image_route(tmp_path, monkeypatch, calls):
    """recognize_pdf on a picture, with page preparation and homr replaced."""
    import app.pdf_omr as omr
    # These tests exercise the retained HOMR backend explicitly; default routing
    # to Copista is verified separately with native Copista XML.
    monkeypatch.setattr(omr, "DEFAULT_OMR_ENGINE", "homr")

    def fake_prepare(_source, pages_dir):
        pages_dir.mkdir(parents=True, exist_ok=True)
        page = pages_dir / "page_001.png"
        page.write_bytes(b"png")
        return [page]

    def fake_homr(pages_dir, total, cancel, progress):
        calls.append("homr")
        (pages_dir / "page_001.musicxml").write_text(PAGE_XML.format(a="C", b="D"), encoding="utf-8")
        return ""

    monkeypatch.setattr(omr, "prepare_pages", fake_prepare)
    monkeypatch.setattr(omr, "_is_jianpu", lambda _images: False)
    monkeypatch.setattr(omr, "_run_homr", fake_homr)
    src = tmp_path / "photo.png"
    src.write_bytes(b"png")
    return src


def test_models_are_downloaded_with_progress_before_homr_starts(tmp_path, monkeypatch):
    import app.pdf_omr as omr
    from app import homr_models

    calls, seen = [], []

    def fake_ensure(cancel, on_status=None, **_kw):
        calls.append("download")
        on_status(homr_models.DownloadStatus(2, 3, "m.onnx", 23 << 20, 46 << 20))
        return []

    monkeypatch.setattr(homr_models, "ensure_models", fake_ensure)
    src = _image_route(tmp_path, monkeypatch, calls)
    result = omr.recognize_pdf(src, tmp_path / "work", Event(), lambda m, r: seen.append((m, r)))
    assert calls == ["download", "homr"]
    assert any(m == "下载识别模型（仅第一次）2/3 · 23/46 MB" and abs(r - 0.25) < 1e-9 for m, r in seen)
    assert result.name == "photo_merged.musicxml"


def test_model_download_failure_stops_before_homr(tmp_path, monkeypatch):
    import app.pdf_omr as omr
    from app import homr_models

    calls = []

    def fake_ensure(cancel, on_status=None, **_kw):
        raise homr_models.ModelDownloadError("no network")

    monkeypatch.setattr(homr_models, "ensure_models", fake_ensure)
    src = _image_route(tmp_path, monkeypatch, calls)
    try:
        omr.recognize_pdf(src, tmp_path / "work", Event())
        assert False
    except PdfOmrError as exc:
        assert str(exc) == omr.DOWNLOAD_FAILED
    assert calls == []


def test_prepare_image_flattens_transparency_and_upscales(tmp_path):
    from PIL import Image

    from app.pdf_omr import RENDER_WIDTH, prepare_pages

    src = tmp_path / "截图.png"
    Image.new("RGBA", (800, 400), (0, 0, 0, 0)).save(src)
    pages = prepare_pages(src, tmp_path / "pages")
    assert [p.name for p in pages] == ["page_001.png"]
    with Image.open(pages[0]) as out:
        assert out.mode == "RGB"
        assert out.width == RENDER_WIDTH
        assert out.getpixel((10, 10)) == (255, 255, 255)


def test_prepare_image_keeps_large_jpg_size(tmp_path):
    from PIL import Image

    from app.pdf_omr import prepare_pages

    src = tmp_path / "photo.JPG"
    Image.new("RGB", (3000, 4000), (250, 250, 250)).save(src, format="JPEG")
    pages = prepare_pages(src, tmp_path / "pages")
    with Image.open(pages[0]) as out:
        assert out.size == (3000, 4000)


def test_prepare_image_bad_file(tmp_path):
    from app.pdf_omr import prepare_pages

    src = tmp_path / "bad.png"
    src.write_bytes(b"not an image")
    try:
        prepare_pages(src, tmp_path / "pages")
        assert False
    except PdfOmrError as exc:
        assert "图片" in str(exc)


def test_run_pdf_accepts_image_and_keeps_its_suffix(tmp_path):
    from PIL import Image

    src = tmp_path / "sheet.png"
    Image.new("RGB", (100, 100), (255, 255, 255)).save(src)
    seen = {}

    def fake_recognize(image: Path, work_dir: Path, cancel: Event) -> Path:
        seen["input"] = image
        xml = work_dir / "x_merged.musicxml"
        xml.write_text(PAGE_XML.format(a="C", b="D"), encoding="utf-8")
        return xml

    result = run_pdf(src, Event(), output_root=tmp_path, recognize=fake_recognize)
    assert result.status == "success"
    assert seen["input"].suffix == ".png"
    assert (result.folder / "sheet.png").is_file()
    text = result.keyboard_path.read_text(encoding="utf-8")
    assert "由图片自动识别" in text
    assert not (result.folder / "x_merged.musicxml").exists()


def test_count_staves_and_vocal_piano_warning(tmp_path):
    from app.pdf_omr import count_staves

    piano = tmp_path / "piano.musicxml"
    piano.write_text(SLUR_XML.replace("<staff>1</staff>", "<staff>1</staff>", 1), encoding="utf-8")
    assert count_staves(piano) == 1
    two = SLUR_XML.replace(
        '<note><pitch><step>D</step>', '<note><staff>2</staff><pitch><step>D</step>'
    )
    vocal = tmp_path / "vocal.musicxml"
    # one part with two staves plus a separate single-staff part => 3 staves per system
    vocal.write_text(
        two.replace(
            "</part>\n</score-partwise>",
            '</part>\n  <part id="P2"><measure number="1"><note><pitch><step>C</step><octave>5</octave></pitch>'
            "<duration>4</duration><voice>1</voice><type>whole</type><staff>1</staff></note></measure></part>\n</score-partwise>",
        ),
        encoding="utf-8",
    )
    assert count_staves(vocal) == 3

    def fake_recognize(image: Path, work_dir: Path, cancel: Event) -> Path:
        out = work_dir / "v_merged.musicxml"
        out.write_bytes(vocal.read_bytes())
        return out

    src = tmp_path / "song.pdf"
    src.write_bytes(b"%PDF-1.4")
    result = run_pdf(src, Event(), output_root=tmp_path, recognize=fake_recognize)
    assert result.status == "success"
    assert "人声 + 钢琴" in result.keyboard_path.read_text(encoding="utf-8")
