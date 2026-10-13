"""Synthetic geometry checks, independent of the optional inference runtime."""
import pymupdf as p
import pytest

from app.ocr.contracts import OCRLine, OCRError
from app.ocr.pdf_export import add_text_layer
from app.ocr.page_detector import has_effective_text


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
@pytest.mark.parametrize('dpi', [150, 200, 300])
def test_invisible_chinese_search_geometry(tmp_path, rotation, dpi):
    with p.open() as doc:
        page = doc.new_page(width=420, height=600)
        page.draw_rect((60, 80, 250, 140), color=(.3, .5, .7), fill=(.9, .9, .9))
        page.set_cropbox(p.Rect(20, 30, 400, 570))
        page.set_rotation(rotation)
        before = page.get_pixmap(dpi=dpi)
        box = p.Rect(50, 90, 240, 115)
        rotated = [point * page.rotation_matrix * p.Matrix(dpi / 72, dpi / 72)
                   for point in (box.tl, box.tr, box.br, box.bl)]
        polygon = tuple(v for point in rotated for v in point)
        line = OCRLine('纸序预算 ABC 123.45', (0, 0, 0, 0), polygon, .99, 0)
        written = add_text_layer(page, [line], before, dpi)
        assert written
        path = tmp_path / 'ocr.pdf'
        doc.save(path)
    with p.open(path) as check:
        page = check[0]
        assert page.rotation == rotation
        assert '纸序预算 ABC 123.45' in page.get_text()
        hits = page.search_for('纸序预算')
        assert hits and hits[0].intersects(box)
        assert abs(hits[0].y0 - box.y0) < 3
        assert abs(hits[0].y1 - box.y1) < 3
        assert page.get_pixmap(dpi=dpi).samples == before.samples


def test_bad_line_cannot_leave_partial_text():
    with p.open() as doc:
        page = doc.new_page()
        pix = page.get_pixmap(dpi=200)
        lines = [OCRLine('正常中文', (0, 0, 0, 0), (80, 80, 250, 80, 250, 110, 80, 110), .99, 0),
                 OCRLine('无效', (0, 0, 0, 0), (float('nan'),) * 8, .99, 0)]
        with pytest.raises(OCRError):
            add_text_layer(page, lines, pix)
        assert not page.get_text().strip()


def test_rotated_raster_keyword_starts_at_top():
    with p.open() as doc:
        page = doc.new_page()
        pix = page.get_pixmap(dpi=200)
        line = OCRLine('中文预算 ABC123', (100, 100, 145, 650),
                       (100, 100, 145, 100, 145, 650, 100, 650), .99, 0)
        add_text_layer(page, [line], pix)
        first = page.search_for('中文')
        last = page.search_for('ABC123')
        assert first and last and first[0].y1 < last[0].y0


def test_skewed_line_follows_ocr_baseline():
    with p.open() as doc:
        page = doc.new_page()
        pix = page.get_pixmap(dpi=200)
        line = OCRLine('中文预算 ABC123', (96, 100, 500, 160),
                       (100, 100, 500, 120, 496, 160, 96, 140), .99, 0)
        add_text_layer(page, [line], pix)
        direction = page.get_text('dict')['blocks'][0]['lines'][0]['dir']
        assert direction[0] > .99 and 0 < direction[1] < .1
        assert page.search_for('ABC123')


def test_short_ocr_layer_is_skipped_but_page_number_is_not():
    with p.open() as doc:
        page = doc.new_page()
        image = p.Pixmap(p.csRGB, p.IRect(0, 0, 10, 10))
        image.clear_with(255)
        page.insert_image(page.rect, stream=image.tobytes('png'))
        page.insert_text((50, 50), '1')
        assert not has_effective_text(page)
        pix = page.get_pixmap(dpi=200)
        line = OCRLine('短页文字 123', (100, 300, 500, 350),
                       (100, 300, 500, 300, 500, 350, 100, 350), .99, 0)
        add_text_layer(page, [line], pix)
        doc.subset_fonts()
        with p.open(stream=doc.tobytes(), filetype='pdf') as reopened:
            assert has_effective_text(reopened[0])
