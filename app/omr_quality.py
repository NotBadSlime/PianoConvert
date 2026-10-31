"""Conservative structural checks on HOMR's untouched per-page MusicXML.

This audit records evidence and warnings only. It never splits measures,
stretches notes, or treats an overfull measure as proof of a missed barline.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Sequence
import xml.etree.ElementTree as ET


def _children(element, name: str):
    return [child for child in element if child.tag.rpartition('}')[2] == name]


def audit_page_xmls(xmls: Sequence[Path]) -> dict:
    """Count each page's measures per part, using a strict-majority quorum.

    A warning needs at least two parts agreeing on a positive count and
    more than half of the page's parts agreeing. Two conflicting parts,
    tied votes, and a one-part cadenza are deliberately inconclusive.
    The input order is the PDF page order used by the raw-XML archive.
    """
    pages = []
    warnings = []
    for page_number, xml in enumerate(xmls, start=1):
        xml = Path(xml)
        record = dict(page_number=page_number, file=xml.name)
        try:
            data = xml.read_bytes()
            record['sha256'] = hashlib.sha256(data).hexdigest()
            root = ET.fromstring(data)
            parts = _children(root, 'part')
            if root.tag.rpartition('}')[2] != 'score-partwise':
                record.update(status='unsupported_format', parts=[])
                pages.append(record)
                continue
            counts = [dict(part_id=part.get('id') or f'part_{index}',
                           measure_count=len(_children(part, 'measure')))
                      for index, part in enumerate(parts, start=1)]
            record.update(status='audited', parts=counts)
            votes = Counter(part['measure_count'] for part in counts)
            consensus = next((count for count, votes_for in votes.items()
                              if count > 0 and votes_for >= 2 and votes_for > len(parts)/2), None)
            record['consensus_measure_count'] = consensus
            if consensus is not None:
                for part in counts:
                    if part['measure_count'] != consensus:
                        warnings.append(dict(
                            code='part_measure_count_mismatch', page_number=page_number,
                            part_id=part['part_id'], measure_count=part['measure_count'],
                            consensus_measure_count=consensus,
                            consensus_parts=[p['part_id'] for p in counts if p['measure_count']==consensus],
                        ))
        except (OSError, ET.ParseError) as exc:
            # Diagnostics must not manufacture a mismatch from an unreadable
            # page. The recognition/merge pipeline handles invalid XML itself.
            record.update(status='unreadable', error=type(exc).__name__)
        pages.append(record)
    return dict(schema_version=1, audit='raw_page_part_measure_counts',
                automatic_correction=False, pages=pages, warnings=warnings)


def write_quality_report(xmls: Sequence[Path], destination: Path) -> dict:
    report = audit_page_xmls(xmls)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def quality_messages(work_dir: Path) -> list[str]:
    """One user-facing note per affected page from the saved raw-stage audit."""
    pages = set()
    for report_path in sorted((Path(work_dir)/'_omr_raw').glob('*/quality.json')):
        try:
            report = json.loads(report_path.read_text(encoding='utf-8'))
            if report.get('schema_version') != 1:
                continue
            for warning in report.get('warnings', []):
                page = warning.get('page_number')
                if warning.get('code') == 'part_measure_count_mismatch' and type(page) is int and page > 0:
                    pages.add(page)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return [f'第{page}页声部小节数不一致，可能漏小节线，后段节奏可能错位。' for page in sorted(pages)]
