"""Generated pages only: physical size, live objects and incoming navigation."""
import hashlib

import pymupdf as p
import pytest
from PyQt6.QtWidgets import QApplication

from app.pagegeometry import geometry_edit
from app.pdfedits import apply_edits
from app.pdfio import PdfError, PdfStore
from app.workspace import Workspace


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def generated_pdf(path, rotation=0, crop=False, objects=False):
    with p.open() as doc:
        doc.new_page(width=300, height=400)
        doc.new_page(width=300, height=400)
        page = doc[0]
        page.insert_text((70, 95), 'SAMPLE TEXT')
        page.draw_rect(p.Rect(50, 60, 240, 330), color=(1, 0, 0))
        if objects:
            page.add_highlight_annot(page.search_for('SAMPLE'))
            note = page.add_text_annot((100, 130), 'keep note')
            note.update()
            widget = p.Widget()
            widget.field_name, widget.field_type = 'generated-field', p.PDF_WIDGET_TYPE_TEXT
            widget.field_value, widget.rect = 'keep value', p.Rect(60, 180, 180, 215)
            page.add_widget(widget)
        if crop:
            page.set_cropbox(p.Rect(20, 30, 280, 370))
        word = page.search_for('SAMPLE')[0]
        target = p.Point(word.x0 + 1, word.y1 - 1)
        raw_target = target * ~page.transformation_matrix
        page.set_rotation(rotation)
        doc[1].insert_link(dict(kind=p.LINK_GOTO, page=0, to=target,
                                **{'from': p.Rect(30, 30, 100, 55)}))
        doc.set_toc([[1, 'Target', 1, dict(kind=p.LINK_GOTO, page=0, to=target)]])
        action = p.utils.getDestStr(doc.page_xref(0), dict(kind=p.LINK_GOTO, page=0, to=raw_target))[2:]
        doc.xref_set_key(doc[1].get_links()[0]['xref'], 'A', action)
        doc.xref_set_key(doc.get_toc(False)[0][3]['xref'], 'A', action)
        doc.save(path)


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
@pytest.mark.parametrize('extra', [0, 90])
@pytest.mark.parametrize('crop', [False, True])
def test_resize_preserves_content_objects_and_navigation(tmp_path, qapp, rotation, extra, crop):
    path, output = tmp_path / 'generated.pdf', tmp_path / 'resized.pdf'
    generated_pdf(path, rotation, crop, objects=True)
    original = hashlib.sha256(path.read_bytes()).digest()
    store = PdfStore()
    try:
        refs = store.load(str(path))
        refs[0] = refs[0].rotated(extra)
        old_width, old_height = store.page_size(refs[0])
        size = (old_width / 2, old_height / 2)
        changed = refs[0].edited(geometry_edit(store, refs[0], size))
        assert store.page_size(changed) == pytest.approx(size)
        ws = Workspace()
        ws.open_replace(refs, str(path))
        ws.edit_pages({0: changed}, '调整页面尺寸')
        store.write(ws.pages, str(output))
        with p.open(output) as doc, p.open(path) as original_doc:
            page = doc[0]
            assert tuple(page.rect)[2:] == pytest.approx(size)
            assert 'SAMPLE TEXT' in page.get_text()
            assert len(list(page.annots())) == 2
            widget = next(page.widgets())
            assert widget.field_value == 'keep value'
            assert widget.rect.width == pytest.approx(60)
            assert len(doc[1].get_links()) == 1
            link = doc[1].get_links()[0]
            assert link['page'] == 0
            word = page.search_for('SAMPLE')[0]
            point = p.Point(link['to']) * page.derotation_matrix
            assert (word + (-3, -3, 3, 3)).contains(point)
            assert doc.get_toc()[0][1:] == ['Target', 1]
            # Form fields remain editable after resizing and reopening.
            widget.field_value = 'edited later'
            widget.update()
            assert next(doc.reload_page(page).widgets()).field_value == 'edited later'
        ws.undo_stack.undo()
        assert ws.pages == refs
        ws.undo_stack.redo()
        assert ws.pages[0] == changed
        assert hashlib.sha256(path.read_bytes()).digest() == original
    finally:
        store.close_all()


def test_size_letterboxes_without_stretch_or_revealing_crop(tmp_path, qapp):
    path = tmp_path / 'crop.pdf'
    with p.open() as doc:
        page = doc.new_page(width=300, height=400)
        page.draw_rect(p.Rect(0, 0, 30, 400), fill=(1, 0, 0), color=None)
        page.draw_rect(p.Rect(70, 100, 90, 120), fill=(0, 0, 1), color=None)
        page.set_cropbox(p.Rect(50, 50, 250, 350))
        doc.save(path)
    store = PdfStore()
    try:
        ref = store.load(str(path))[0]
        ref = ref.edited(geometry_edit(store, ref, (400, 300)))
        image = store.render_qimage(ref, 400)
        assert image.width() == 400 and image.height() == 300
        assert image.pixelColor(60, 150).getRgb()[:3] == (255, 255, 255)
        assert image.pixelColor(125, 55).blue() > 200
        with pytest.raises(PdfError, match='副本'):
            store.write([ref], str(path))
    finally:
        store.close_all()


def test_named_destinations_are_rejected_before_modifying_the_workspace(tmp_path, qapp):
    path = tmp_path / 'named.pdf'
    with p.open() as doc:
        doc.new_page(width=300, height=400)
        doc.xref_set_key(doc.pdf_catalog(), 'Names',
            f'<< /Dests << /Names [(generated-target) [{doc.page_xref(0)} 0 R /XYZ 30 320 0]] >> >>')
        doc.save(path)
    store = PdfStore()
    try:
        ref = store.load(str(path))[0]
        with pytest.raises(ValueError, match='命名跳转'):
            geometry_edit(store, ref, (150, 200))
        assert not ref.edits
    finally:
        store.close_all()
