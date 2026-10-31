import hashlib
import json
from threading import Event
import xml.etree.ElementTree as ET

from app import pdf_omr as omr
from tests.test_pdf_omr import SLUR_XML, _image_route


def test_original_homr_xml_survives_cleanup_that_changes_ties(tmp_path, monkeypatch):
    source = _image_route(tmp_path, monkeypatch, [])
    original = SLUR_XML.encode('utf-8')
    monkeypatch.setattr(omr, '_ensure_homr_models', lambda *_args: None)

    def homr(pages_dir, *_args):
        (pages_dir/'page_001.musicxml').write_bytes(original)
    monkeypatch.setattr(omr, '_run_homr', homr)
    work = tmp_path/'work'
    merged = omr.recognize_pdf(source, work, Event())
    archives = list((work/'_omr_raw').glob('*/manifest.json'))
    assert len(archives) == 1
    manifest = json.loads(archives[0].read_text(encoding='utf-8'))
    raw = archives[0].parent/manifest['pages'][0]['file']
    assert raw.read_bytes() == original
    assert manifest['pages'][0]['sha256'] == hashlib.sha256(original).hexdigest()
    assert not ET.fromstring(original).findall('.//tie')
    assert ET.parse(merged).findall('.//tie')
    assert not (work/'_omr_pages').exists()
    assert not list((work/'_omr_raw').rglob('*.png'))


def test_another_recognition_in_the_same_workdir_preserves_both_raw_results(tmp_path, monkeypatch):
    source = _image_route(tmp_path, monkeypatch, [])
    monkeypatch.setattr(omr, '_ensure_homr_models', lambda *_args: None)
    documents = [SLUR_XML, SLUR_XML.replace('<octave>4</octave>', '<octave>5</octave>')]
    def homr(pages_dir, *_args):
        (pages_dir/'page_001.musicxml').write_text(documents.pop(0), encoding='utf-8')
    monkeypatch.setattr(omr, '_run_homr', homr)
    work = tmp_path/'work'
    omr.recognize_pdf(source, work, Event())
    first = next((work/'_omr_raw').glob('*/page_001.musicxml'))
    original = first.read_bytes()
    omr.recognize_pdf(source, work, Event())
    assert len(list((work/'_omr_raw').glob('*/page_001.musicxml'))) == 2
    assert first.read_bytes() == original
