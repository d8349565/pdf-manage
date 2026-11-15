"""Engineering-style PDFs generated here, with no business documents or CAD programs."""
import pymupdf as p
import pytest

from app.pdfio import PdfStore
from app.workspace import PageEdit


def make_drawing(path, content):
    with p.open() as document:
        page = document.new_page(width=1191, height=842)
        page.draw_rect((30, 30, 1161, 812), width=.5)
        page.draw_rect((160, 220, 800, 650), width=2)
        page.draw_line((480, 220), (480, 650), width=2)
        page.draw_line((160, 170), (800, 170), width=.5)
        if content == 'outlines':
            # A dimension label drawn as line segments: visible, but no PDF text objects.
            for x in (400, 420, 440, 460):
                page.draw_polyline([(x, 140), (x + 10, 140), (x + 10, 160),
                                    (x, 160), (x, 140)], width=.5)
        else:
            page.insert_text((400, 160), 'L=3600', fontsize=12,
                             rotate=90 if content == 'vertical' else 0)
        if content == 'scan':
            image = page.get_pixmap().tobytes('png')
            with p.open() as scanned:
                target = scanned.new_page(width=1191, height=842)
                target.insert_image(target.rect, stream=image)
                scanned.save(path)
        else:
            document.save(path)


def test_drawing_text_can_be_corrected_without_changing_vector_geometry(tmp_path):
    source, output = tmp_path / 'drawing.pdf', tmp_path / 'corrected.pdf'
    make_drawing(source, 'text')
    original = source.read_bytes()
    with p.open(source) as document:
        page = document[0]
        area = page.search_for('L=3600')[0]
        geometry = [{key: value for key, value in item.items() if key != 'seqno'}
                    for item in page.get_drawings()]
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        edit = store.prepare_replacement(ref, area, 'L=4200')
        store.write([ref.edited(edit)], str(output))
        with p.open(output) as document:
            page = document[0]
            assert 'L=4200' in page.get_text() and 'L=3600' not in page.get_text()
            assert geometry == [{key: value for key, value in item.items() if key != 'seqno'}
                                for item in page.get_drawings()]
            assert page.rect == p.Rect(0, 0, 1191, 842)
        assert source.read_bytes() == original
    finally:
        store.close_all()


@pytest.mark.parametrize('content', ['outlines', 'scan', 'vertical'])
def test_drawing_editing_limits_still_allow_annotations(tmp_path, content):
    source, output = tmp_path / 'drawing.pdf', tmp_path / 'annotated.pdf'
    make_drawing(source, content)
    original = source.read_bytes()
    with p.open(source) as document:
        page = document[0]
        if content == 'vertical':
            area = page.search_for('L=3600')[0]
        else:
            assert not page.get_text().strip()
            area = p.Rect(390, 130, 490, 175)
        # A region away from the new annotation must retain its rendered content.
        untouched = page.get_pixmap(clip=p.Rect(150, 210, 810, 660)).samples
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        with pytest.raises(ValueError, match='一行横排可见文字'):
            store.prepare_replacement(ref, area, 'L=4200')
        marked = ref.edited(PageEdit('rect', tuple(area), color=(1, 0, 0), annotation_id='review'))
        store.write([marked], str(output))
        with p.open(output) as document:
            page = document[0]
            assert page.first_annot.type[0] == p.PDF_ANNOT_SQUARE
            assert page.get_pixmap(clip=p.Rect(150, 210, 810, 660)).samples == untouched
        assert source.read_bytes() == original
    finally:
        store.close_all()
