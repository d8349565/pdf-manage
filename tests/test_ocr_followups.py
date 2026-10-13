"""OCR copy, text-aware DOCX routing and menu grouping use generated files only."""
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pymupdf as p
import pytest
from PyQt6.QtCore import QPointF, QThreadPool, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app.ocr.contracts import OCRLine
from app.ocr.pdf_export import add_text_layer
from app.ocr.word_export import W, convert_ocr_pdf_to_docx, pdf_has_ocr_text
from app.output import OperationCancelled
from app.ui import MainWindow


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def make_ocr(path, *, rotation=0, extra_scan=False):
    with p.open() as doc:
        page = doc.new_page(width=400, height=500)
        pix = p.Pixmap(p.csRGB, p.IRect(0, 0, 20, 20))
        pix.clear_with(255)
        page.insert_image(page.rect, stream=pix.tobytes('png'))
        scan = page.get_pixmap(dpi=200)
        factor = 200 / 72
        values = [('采购合同 & ABC123', (45, 80, 260, 100)),
                  ('含税金额 12345.67 元', (45, 130, 280, 150))]
        lines = [OCRLine(text, box, tuple(v * factor for point in
                    (p.Rect(box).tl, p.Rect(box).tr, p.Rect(box).br, p.Rect(box).bl) for v in point), .99, 0)
                 for text, box in values]
        add_text_layer(page, lines, scan)
        page.set_rotation(rotation)
        if extra_scan:
            second = doc.new_page()
            second.insert_image(second.rect, stream=pix.tobytes('png'))
        doc.subset_fonts()
        doc.save(path)


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_ocr_drag_select_copy_and_no_edit(qapp, tmp_path, rotation):
    source = tmp_path / 'ocr.pdf'
    make_ocr(source, rotation=rotation)
    before = source.read_bytes()
    window = MainWindow()
    try:
        window.resize(1100, 760)
        window.show()
        window.ws.open_replace(window.store.load(str(source)), str(source))
        window.refresh()
        QThreadPool.globalInstance().waitForDone(30000)
        qapp.processEvents()
        snapshot = window.store.text_snapshot(window.ws.pages[0])
        box = p.Rect(40, 75, 290, 155) * p.Matrix(snapshot['matrix'])
        origin = window.preview_view.page_rects[0].topLeft()
        start = window.preview_view.mapFromScene(origin + QPointF(box.x0, box.y0))
        end = window.preview_view.mapFromScene(origin + QPointF(box.x1, box.y1))
        viewport = window.preview_view.viewport()
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(viewport, end)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
        assert '采购合同 & ABC123' in window.text_selection.text
        assert '12345.67' in window.text_selection.text
        assert window.text_selection.overlay is not None
        QApplication.clipboard().clear()
        QTest.keyClick(window.preview_view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        assert QApplication.clipboard().text() == window.text_selection.text
        assert not window.ws.dirty and source.read_bytes() == before
        window.ws.select_index(0)
        window.ws.rotate(90)
        window.refresh()
        assert not window.text_selection.text
    finally:
        QThreadPool.globalInstance().waitForDone(30000)
        window.ws.reset()
        window.store.close_all()
        window.close()
        qapp.processEvents()


def test_organization_and_directional_conversion_groups(qapp):
    window = MainWindow()
    try:
        menus = {action.text(): action.menu() for action in window._menubar.actions() if action.menu()}
        assert '组织' in menus and '转换' in menus and '工具' not in menus
        conversion = menus['转换'].actions()
        assert [action.text() for action in conversion] == [
            'Word 转 PDF…', '图片转 PDF…', 'Markdown 转 PDF…', '',
            'PDF 转 Word…', 'PDF 转 Excel…', 'PDF 转 TXT…', 'PDF 转图片…']
        assert conversion[3].isSeparator()
        assert all('批量' not in action.text() for action in conversion)
        assert all(action.isSeparator() or action.text().endswith('…') for action in menus['组织'].actions())
    finally:
        window.close()
        qapp.processEvents()


def read_docx(path):
    with ZipFile(path) as archive:
        for name in archive.namelist():
            if name.endswith(('.xml', '.rels')):
                ET.fromstring(archive.read(name))
        document = ET.fromstring(archive.read('word/document.xml'))
        return document, ''.join(value.text or '' for value in document.iter(f'{{{W}}}t'))


def test_ocr_word_export_contains_editable_text_and_routes_without_word(tmp_path, monkeypatch):
    from app import wordconvert
    source, output = tmp_path / 'scan.pdf', tmp_path / 'scan.docx'
    make_ocr(source)
    original = source.read_bytes()
    assert pdf_has_ocr_text(source)
    monkeypatch.setattr(wordconvert, 'word_available', lambda: False)
    results, cancelled, version = wordconvert.convert_batch([(str(source), str(output))], False,
                    lambda *args: None, lambda: False, direction='pdf_to_word')
    assert results[0]['state'] == 'success' and not cancelled and not version
    document, text = read_docx(output)
    assert '采购合同 & ABC123' in text and '含税金额 12345.67 元' in text
    assert list(document.iter(f'{{{W}}}r')) and source.read_bytes() == original
    with ZipFile(output) as archive:
        assert not any(name.startswith('word/media/') for name in archive.namelist())


def test_unrecognized_image_page_cannot_be_silently_dropped(tmp_path,monkeypatch):
    source, output = tmp_path / 'mixed.pdf', tmp_path / 'mixed.docx'
    make_ocr(source, extra_scan=True)
    def fail_recognition(*args):
        raise ValueError('第 2 页无法识别')
    monkeypatch.setattr('app.ocr.service.run_ocr',fail_recognition)
    with pytest.raises(ValueError, match='第 2 页'):
        convert_ocr_pdf_to_docx(source, output, False, None, lambda: False)
    assert not output.exists()


def test_word_cancel_and_existing_output_protection(tmp_path):
    source, output = tmp_path / 'ocr.pdf', tmp_path / 'ocr.docx'
    make_ocr(source)
    with pytest.raises(OperationCancelled):
        convert_ocr_pdf_to_docx(source, output, False, None, lambda: True)
    assert not output.exists() and not list(tmp_path.glob('.zhixu-ocr-word-*'))
    output.write_bytes(b'existing-output')
    result = convert_ocr_pdf_to_docx(source, output, False, None, lambda: False)
    assert result[0] == 'skipped' and output.read_bytes() == b'existing-output'


def test_plain_pdf_retains_native_word_requirement(tmp_path, monkeypatch):
    from app import wordconvert
    source = tmp_path / 'plain.pdf'
    with p.open() as doc:
        doc.new_page().insert_text((60, 80), 'Ordinary electronic PDF')
        doc.save(source)
    assert not pdf_has_ocr_text(source)
    monkeypatch.setattr(wordconvert, 'word_available', lambda: False)
    results, cancelled, version = wordconvert.convert_batch([(str(source), str(tmp_path / 'plain.docx'))],
                        False, lambda *args: None, lambda: False, direction='pdf_to_word')
    assert results[0]['state'] == 'failed' and 'Microsoft Word' in results[0]['message']


def test_ocr_table_exports_editable_cells_and_exact_amounts(tmp_path):
    source, output = tmp_path / 'table.pdf', tmp_path / 'table.docx'
    with p.open() as doc:
        page = doc.new_page(width=400, height=500)
        for x in (40, 180, 340):
            page.draw_line((x, 80), (x, 230))
        for y in (80, 130, 180, 230):
            page.draw_line((40, y), (340, y))
        values = [('项目', (50, 95, 105, 115)), ('金额', (195, 95, 255, 115)),
                  ('服务费', (50, 145, 125, 165)), ('12345.67', (195, 145, 305, 165)),
                  ('税费', (50, 195, 105, 215)), ('678.90', (195, 195, 290, 215))]
        scale = 200 / 72
        lines = [OCRLine(text, box, tuple(value * scale for point in
                (p.Rect(box).tl, p.Rect(box).tr, p.Rect(box).br, p.Rect(box).bl) for value in point), .99, 0)
                 for text, box in values]
        add_text_layer(page, lines, page.get_pixmap(dpi=200))
        doc.subset_fonts()
        doc.save(source)
    result = convert_ocr_pdf_to_docx(source, output, False, None, lambda: False)
    document, text = read_docx(output)
    assert result[0] == 'success' and '12345.67' in text and '678.90' in text
    tables = list(document.iter(f'{{{W}}}tbl'))
    assert len(tables) == 1
    rows = list(tables[0].iter(f'{{{W}}}tr'))
    assert len(rows) == 3
    cells = [[''.join(node.text or '' for node in cell.iter(f'{{{W}}}t'))
              for cell in row.findall(f'{{{W}}}tc')] for row in rows]
    assert cells == [['项目', '金额'], ['服务费', '12345.67'], ['税费', '678.90']]


def test_incomplete_table_structure_falls_back_without_losing_ocr_text(tmp_path, monkeypatch):
    from app.pdf2excel.models import CellModel, TableModel
    source, output = tmp_path / 'ocr.pdf', tmp_path / 'ocr.docx'
    make_ocr(source)
    incomplete = TableModel(1, (0, 0, 400, 500), 2, 2,
                          [CellModel(0, 0, text='缺失单元格')], [250, 250], [200, 200])
    monkeypatch.setattr('app.pdf2excel.detector.detect_tables', lambda *args: [incomplete])
    result = convert_ocr_pdf_to_docx(source, output, False, None, lambda: False)
    document, text = read_docx(output)
    assert '采购合同 & ABC123' in text and '12345.67' in text
    assert not list(document.iter(f'{{{W}}}tbl'))
    assert '按正文保留全部识别文字' in result[1]
