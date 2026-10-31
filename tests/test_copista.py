import hashlib
import json
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from threading import Event

import pytest

from app import copista_models as models, pdf_omr as omr
from app.engines.base import CancelledError
from tests.test_pdf_omr import PAGE_XML, SLUR_XML


def fake_images(tmp_path, monkeypatch, count=2):
    source = tmp_path / "image.png"
    source.write_bytes(b"input")

    def prepare(_source, folder):
        folder.mkdir(parents=True, exist_ok=True)
        result = [folder / f"page_{i:03d}.png" for i in range(1, count + 1)]
        for path in result:
            path.write_bytes(b"png")
        return result

    monkeypatch.setattr(omr, "prepare_pages", prepare)
    monkeypatch.setattr(omr, "_is_jianpu", lambda _images: False)
    monkeypatch.setattr("app.omr_tempo.supplement_opening_tempo", lambda *_: False)
    monkeypatch.setattr(omr, "_run_homr", lambda *_: pytest.fail("default image route called HOMR"))
    return source


def test_default_copista_keeps_native_slurs_and_all_pages(tmp_path, monkeypatch):
    source = fake_images(tmp_path, monkeypatch)

    def run(folder, *_):
        for index in [1, 2]:
            (folder / f"page_{index:03d}.musicxml").write_bytes(SLUR_XML.encode("utf-8"))
        (folder / 'copista-run.json').write_text(json.dumps(dict(engine='copista-28m', source_revision='pinned')), encoding='utf-8')

    monkeypatch.setattr(omr, "_run_copista", run)
    work = tmp_path / "out"
    result = omr.recognize_pdf(source, work, Event())
    root = ET.parse(result).getroot()
    assert root.findall('.//slur')
    assert not root.findall('.//tie'), "HOMR's slur conversion must not affect Copista"
    assert len(root.findall('part/measure')) == 4
    manifest = next((work / '_omr_raw').glob('*/manifest.json'))
    record = json.loads(manifest.read_text(encoding='utf-8'))
    assert record['engine'] == 'copista-28m'
    assert record['runtime']['source_revision'] == 'pinned'
    assert len(record['pages']) == 2
    for page in record['pages']:
        assert (manifest.parent / page['file']).read_bytes() == SLUR_XML.encode('utf-8')
    assert not (work / '_omr_pages').exists()


def test_missing_page_fails_instead_of_shortening_song(tmp_path, monkeypatch):
    source = fake_images(tmp_path, monkeypatch)
    monkeypatch.setattr(omr, '_run_copista', lambda folder, *_: (folder / 'page_001.musicxml').write_text(PAGE_XML.format(a='C', b='D'), encoding='utf-8'))
    work = tmp_path / 'out'
    with pytest.raises(omr.PdfOmrError, match='未识别完所有页面'):
        omr.recognize_pdf(source, work, Event())
    assert len(list((work / '_omr_raw').glob('*/page_*.musicxml'))) == 1
    assert not list(work.glob('*merged.musicxml'))


def test_worker_failure_still_preserves_completed_xml(tmp_path, monkeypatch):
    source = fake_images(tmp_path, monkeypatch)

    def fail(folder, *_):
        (folder / 'page_001.musicxml').write_text(SLUR_XML, encoding='utf-8')
        raise omr.PdfOmrError('model failed')

    monkeypatch.setattr(omr, '_run_copista', fail)
    with pytest.raises(omr.PdfOmrError, match='model failed'):
        omr.recognize_pdf(source, tmp_path / 'out', Event())
    assert len(list((tmp_path / 'out/_omr_raw').glob('*/page_*.musicxml'))) == 1


def test_copista_never_receives_parseable_vector_pdf(tmp_path, monkeypatch):
    source = tmp_path / 'score.pdf'
    source.write_bytes(b'pdf')
    midi = tmp_path / 'vector.mid'
    monkeypatch.setattr(omr, '_read_vector', lambda *_: midi)
    monkeypatch.setattr(omr, '_run_copista', lambda *_: pytest.fail('vector PDF sent to model'))
    assert omr.recognize_pdf(source, tmp_path / 'out', Event()) == midi


def test_copista_never_receives_jianpu(tmp_path, monkeypatch):
    source = fake_images(tmp_path, monkeypatch)
    monkeypatch.setattr(omr, '_is_jianpu', lambda _: True)
    midi = tmp_path / 'jianpu.mid'
    monkeypatch.setattr(omr, '_recognize_jianpu', lambda *_: midi)
    monkeypatch.setattr(omr, '_run_copista', lambda *_: pytest.fail('jianpu sent to Copista'))
    assert omr.recognize_pdf(source, tmp_path / 'out', Event()) == midi


def test_strict_merge_rejects_changing_part_topology(tmp_path):
    first, second = tmp_path / '1.musicxml', tmp_path / '2.musicxml'
    first.write_text(PAGE_XML.format(a='C', b='D'), encoding='utf-8')
    root = ET.fromstring(PAGE_XML.format(a='E', b='F'))
    import copy
    root.append(copy.deepcopy(root.find('part')))
    ET.ElementTree(root).write(second, encoding='utf-8')
    with pytest.raises(omr.PdfOmrError, match='乐器分组不一致'):
        omr.merge_pages([first, second], tmp_path / 'merged.musicxml', homr_cleanup=False, strict=True)


def test_copista_subprocess_can_cancel_while_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(models, 'ensure_models', lambda *_: [])
    marker = tmp_path / 'pid.txt'
    monkeypatch.setattr(omr, '_copista_command', lambda _: [sys.executable, '-c',
        f"import os,time,pathlib; pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)"])
    cancel = Event()
    timer = threading.Timer(.7, cancel.set)
    timer.start()
    began = time.monotonic()
    try:
        with pytest.raises(CancelledError):
            omr._run_copista(tmp_path, 1, cancel, lambda *_: None)
    finally:
        timer.cancel()
    assert time.monotonic() - began < 5
    if sys.platform == 'win32' and marker.exists():
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(marker.read_text()))
        if handle:
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(handle)
            assert code.value != 259, 'cancel left the worker alive'


def test_copista_progress_and_nonzero_error_are_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(models, 'ensure_models', lambda *_: [])
    monkeypatch.setattr(omr, '_copista_command', lambda _: [sys.executable, '-c',
        "print('PIANOCONVERT_COPISTA_PAGE 1/2', flush=True); print('recognition error'); raise SystemExit(3)"])
    progress = []
    with pytest.raises(omr.PdfOmrError, match='copista.log'):
        omr._run_copista(tmp_path, 2, Event(), lambda m, _: progress.append(m))
    assert 'Copista 28m 识别第 1/2 页' in progress
    assert 'recognition error' in (tmp_path.parent / 'copista.log').read_text(encoding='utf-8')


def test_model_loader_never_unpickles_a_corrupt_checkpoint(tmp_path, monkeypatch):
    from app.copista_runtime import load_models
    bad = tmp_path / 'bad.pt'
    bad.write_bytes(b'corrupt')
    monkeypatch.setattr(models, 'resolve', lambda _: bad)
    monkeypatch.setattr(models, 'cache_dir', lambda: tmp_path)
    monkeypatch.setattr('torch.load', lambda *_a, **_k: pytest.fail('unverified checkpoint reached torch.load'))
    with pytest.raises(RuntimeError, match='校验失败'):
        load_models('cpu')


def test_shipped_weights_and_user_cache_are_independent(tmp_path, monkeypatch):
    monkeypatch.setattr(models, 'shipped_dir', lambda: tmp_path / 'shipped')
    monkeypatch.setattr(models, 'cache_dir', lambda: tmp_path / 'user')
    monkeypatch.setattr(models, 'FILES', {'small/d.pt': (3, hashlib.sha256(b'abc').hexdigest())})
    path = tmp_path / 'user/small/d.pt'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'abc')
    assert models.missing() == []
    assert models.missing_shipped() == ['small/d.pt']
    monkeypatch.setattr(models, '_download', lambda *_: pytest.fail('already cached model was downloaded'))
    assert models.ensure_models(Event()) == [path]


@pytest.mark.parametrize('server_resumes', [True, False])
def test_download_resumes_or_replaces_without_duplicating_bytes(tmp_path, monkeypatch, server_resumes):
    import io
    content = b'abcdef'
    name = 'd.pt'
    monkeypatch.setattr(models, 'FILES', {name: (len(content), hashlib.sha256(content).hexdigest())})
    target = tmp_path / name
    target.with_suffix('.pt.part').write_bytes(content[:2])

    def response(request, **_kwargs):
        assert request.get_header('Range') == 'bytes=2-'
        stream = io.BytesIO(content[2:] if server_resumes else content)
        stream.status = 206 if server_resumes else 200
        stream.headers = {'Content-Range': 'bytes 2-5/6'} if server_resumes else {}
        return stream

    monkeypatch.setattr(models, 'urlopen', response)
    models._download(name, target, Event(), lambda *_: None)
    assert target.read_bytes() == content
    assert not target.with_suffix('.pt.part').exists()


def test_wrong_model_hash_never_replaces_a_good_existing_file(tmp_path, monkeypatch):
    import io
    monkeypatch.setattr(models, 'FILES', {'d.pt': (3, hashlib.sha256(b'abc').hexdigest())})
    target = tmp_path / 'd.pt'
    target.write_bytes(b'abc')
    response = io.BytesIO(b'bad')
    response.status, response.headers = 200, {}
    monkeypatch.setattr(models, 'urlopen', lambda *_a, **_k: response)
    with pytest.raises(models.ModelDownloadError, match='校验失败'):
        models._download('d.pt', target, Event(), lambda *_: None)
    assert target.read_bytes() == b'abc'


def test_render_includes_pages_after_forty(tmp_path):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument.new()
    for _ in range(41):
        pdf.new_page(20, 20)
    path = tmp_path / 'long.pdf'
    pdf.save(path)
    pdf.close()
    assert len(omr.render_pages(path, tmp_path / 'pages', width=20)) == 41


def test_all_vendored_files_match_upstream_and_compile_for_app_python():
    from app.copista_runtime import vendor_dir
    folder = vendor_dir()
    manifest = json.loads((folder / 'UPSTREAM.json').read_text(encoding='utf-8'))
    for name, digest in manifest['files'].items():
        path = folder / name
        assert models.sha256(path) == digest
        if path.suffix == '.py':
            compile(path.read_bytes(), str(path), 'exec')


def test_builds_include_copista_models_and_native_assets():
    spec = Path('app.spec').read_text(encoding='utf-8')
    assert 'third_party' in spec and 'copisteria' in spec and 'copista-models' in spec
    assert 'torchvision.ops' in spec and 'scipy.ndimage' in spec
    for file in ['scripts/build_exe.ps1', '.github/workflows/release.yml']:
        text = Path(file).read_text(encoding='utf-8')
        assert text.index('-m app.copista_models --package') < text.index('PyInstaller app.spec')
