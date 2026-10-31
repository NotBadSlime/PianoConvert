import hashlib
import json
from pathlib import Path
from threading import Event

import pytest

from app.omr_quality import audit_page_xmls, quality_messages, write_quality_report


def _xml(path, counts, *, duration=4, namespace=''):
    ns = f' xmlns="{namespace}"' if namespace else ''
    parts = []
    for i,count in enumerate(counts,1):
        bars = ''.join(f'<measure number="{bar+1}"><attributes><divisions>1</divisions></attributes>'
                       f'<note><pitch><step>C</step><octave>4</octave></pitch><duration>{duration}</duration>'
                       '<type>whole</type></note></measure>' for bar in range(count))
        parts.append(f'<part id="P{i}">{bars}</part>')
    part_list = ''.join(f'<score-part id="P{i}"><part-name>Part {i}</part-name></score-part>'
                        for i in range(1,len(counts)+1))
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(f'<score-partwise version="4.0"{ns}><part-list>{part_list}</part-list>'
                    + ''.join(parts) + '</score-partwise>',encoding='utf-8')
    return path


def test_a_majority_part_count_identifies_the_page_and_anomalous_part(tmp_path):
    first = _xml(tmp_path/'page_001.musicxml',[8,8,8,8,8])
    second = _xml(tmp_path/'page_002.musicxml',[8,7,8,8,8])
    original = second.read_bytes()
    report = audit_page_xmls([first,second])
    assert report['warnings'] == [dict(code='part_measure_count_mismatch',page_number=2,
                                       part_id='P2',measure_count=7,consensus_measure_count=8,
                                       consensus_parts=['P1','P3','P4','P5'])]
    assert report['automatic_correction'] is False
    assert report['pages'][1]['sha256'] == hashlib.sha256(original).hexdigest()
    assert second.read_bytes() == original


@pytest.mark.parametrize('counts',[[8,7],[8,8,7,7],[8,7,6],[0,0,1]])
def test_no_warning_without_a_positive_strict_majority(tmp_path,counts):
    report = audit_page_xmls([_xml(tmp_path/'page.musicxml',counts)])
    assert report['warnings'] == []


def test_overfull_cadenza_and_one_part_scores_are_not_barline_evidence(tmp_path):
    paths = [_xml(tmp_path/'one.musicxml',[3],duration=128),
             _xml(tmp_path/'three.musicxml',[3,3,3],duration=128)]
    originals = [p.read_bytes() for p in paths]
    report = audit_page_xmls(paths)
    assert report['warnings'] == []
    assert [p.read_bytes() for p in paths] == originals


def test_namespaced_xml_is_audited_and_saved_even_without_warnings(tmp_path):
    page = _xml(tmp_path/'page.musicxml',[2,2,2],namespace='urn:musicxml:test')
    destination = tmp_path/'archive/quality.json'
    report = write_quality_report([page],destination)
    assert report['pages'][0]['parts'][1] == dict(part_id='P2',measure_count=2)
    assert report['warnings'] == []
    assert json.loads(destination.read_text(encoding='utf-8')) == report


def test_multiple_anomalous_parts_or_archives_only_show_one_note_per_page(tmp_path):
    pages = [_xml(tmp_path/'page_001.musicxml',[3,3,3]),
             _xml(tmp_path/'page_002.musicxml',[8,7,6,8,8])]
    for archive in ('a','b'):
        write_quality_report(pages,tmp_path/'_omr_raw'/archive/'quality.json')
    assert quality_messages(tmp_path) == ['第2页声部小节数不一致，可能漏小节线，后段节奏可能错位。']


def test_unreadable_xml_is_recorded_without_inventing_a_barline_warning(tmp_path):
    page = tmp_path/'bad.musicxml'
    page.write_text('<score-partwise>',encoding='utf-8')
    report = audit_page_xmls([page])
    assert report['warnings'] == []
    assert report['pages'][0]['status'] == 'unreadable'


def test_omr_pdf_output_shows_the_warning_and_keeps_quality_evidence(tmp_path):
    from app.convert import run_pdf
    source = tmp_path/'score.pdf'
    source.write_bytes(b'%PDF fake test input')

    def recognize(_pdf,work_dir,_stop):
        archive = work_dir/'_omr_raw'/'test_archive'
        pages = [_xml(archive/'page_001.musicxml',[2,2,2]),
                 _xml(archive/'page_002.musicxml',[8,7,8])]
        write_quality_report(pages,archive/'quality.json')
        return _xml(work_dir/'recognized.musicxml',[2])

    result = run_pdf(source,Event(),output_root=tmp_path/'output',recognize=recognize)
    assert result.status == 'success',result.error
    text = result.keyboard_path.read_text(encoding='utf-8')
    assert '# 注意：第2页声部小节数不一致，可能漏小节线，后段节奏可能错位。' in text
    assert (result.folder/'_omr_raw/test_archive/quality.json').exists()
    assert audit_page_xmls([result.folder/'_omr_raw/test_archive/page_002.musicxml'])['warnings'][0]['part_id']=='P2'


@pytest.mark.parametrize('filename',['recognized_vector.mid','recognized_jianpu.mid'])
def test_raw_quality_notes_do_not_leak_into_vector_or_jianpu_midi_output(tmp_path,filename):
    from app.convert import run_pdf
    from app.score_io import write_notes_midi
    source=tmp_path/'score.pdf'
    source.write_bytes(b'%PDF fake test input')

    def recognize(_pdf,work_dir,_stop):
        archive=work_dir/'_omr_raw/test_archive'
        raw=_xml(archive/'page_001.musicxml',[8,7,8])
        write_quality_report([raw],archive/'quality.json')
        midi=work_dir/filename
        write_notes_midi(midi,[(60,0,.5,80)])
        return midi

    result=run_pdf(source,Event(),output_root=tmp_path/'out',recognize=recognize)
    assert result.status=='success',result.error
    assert '声部小节数不一致' not in result.keyboard_path.read_text(encoding='utf-8')
