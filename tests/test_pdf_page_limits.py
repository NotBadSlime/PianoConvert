from pathlib import Path

import pytest

from app import pdf_vector as pv
from tests.test_pdf_vector import _grand_staff_page, _quarter_up


@pytest.mark.parametrize('limit,last_page', [(None,42),(40,39)])
def test_vector_reader_includes_last_page_unless_explicitly_limited(monkeypatch,limit,last_page):
    head, stem = _quarter_up(80,692.5)
    drawing = _grand_staff_page([head],[stem])

    class Document:
        closed = False
        def __len__(self):
            return 43
        def __getitem__(self,index):
            return index
        def close(self):
            self.closed = True

    doc = Document()
    monkeypatch.setattr('pypdfium2.PdfDocument',lambda _:doc)
    monkeypatch.setattr(pv,'read_drawing',lambda _:drawing)
    score = pv.read_vector_pdf(Path('long.pdf'),max_pages=limit)
    assert max(bar.page for _,bar in score.timeline) == last_page
    assert doc.closed
