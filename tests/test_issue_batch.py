"""Regression cases for issues 17 / 12 / 16 / 15 / 4; generated inputs only."""
import threading
from types import SimpleNamespace

import pymupdf as p
import pytest
from PyQt6.QtCore import QRectF

from app.output import OperationCancelled, cancellable_lock
from app.pdfedits import apply_edits
from app.pdfmarks import is_watermark
from app.pdfmarks import set_appearance, watermark_base, watermark_layout
from app.printing import PrintOptions, build_sheets, face_cells, face_pages
from app.ui import MainWindow
from app.workspace import PageEdit, PageRef


def test_cancel_while_waiting_for_source_keeps_lock_owned():
    lock = threading.Lock()
    lock.acquire()
    try:
        with pytest.raises(OperationCancelled):
            with cancellable_lock(lock, lambda: True):
                pytest.fail("cancelled work must not enter")
        assert lock.locked()
    finally:
        lock.release()


@pytest.mark.parametrize("width,height", [(400, 600), (800, 300), (595, 842)])
def test_custom_watermark_position_adapts_to_each_page(width, height):
    mw, mh, locations = watermark_layout(width, height, 200, 72,
        dict(size=35, angle=45, position="custom", offset=(.2, .8)))
    x, y = locations[0]
    assert x / (width - mw) == pytest.approx(.2)
    assert y / (height - mh) == pytest.approx(.8)
    assert 0 <= x <= width - mw and 0 <= y <= height - mh


@pytest.mark.parametrize("text", ["仅供参考", "CONFIDENTIAL", "Agyp", "仅供\n内部参考"])
def test_text_watermark_uses_glyph_bounds_for_visual_center(text):
    data, width, height = watermark_base(text, (.4, .4, .4))
    with p.open(stream=data, filetype="pdf") as doc:
        page = doc[0]
        assert page.rect.width == pytest.approx(width)
        assert page.rect.height == pytest.approx(height)
        ink = p.Rect()
        for kind, bounds in page.get_bboxlog():
            if kind in ("fill-text", "stroke-text"):
                ink |= p.Rect(bounds)
        assert not ink.is_empty
        assert 0 <= width - ink.width <= 6.1
        assert 0 <= height - ink.height <= 6.1
        assert abs((ink.x0 + ink.x1) / 2 - width / 2) <= 3.1
        assert abs((ink.y0 + ink.y1) / 2 - height / 2) <= 3.1


def test_tiled_watermark_drag_moves_the_pattern_and_keeps_edge_coverage():
    options = dict(size=20, angle=0, tile=True)
    mw, mh, original = watermark_layout(600, 800, 200, 72, options)
    _, _, shifted = watermark_layout(600, 800, 200, 72, dict(options, tile_offset=(.8, .2)))
    assert shifted != original
    assert all(x < 600 and x + mw > 0 and y < 800 and y + mh > 0 for x, y in shifted)


def test_watermark_opacity_preserves_indirect_resources_and_existing_states(monkeypatch):
    show = p.Page.show_pdf_page
    imported = []
    def indirect(page, *args, **kwargs):
        xref = show(page, *args, **kwargs)
        doc = page.parent
        kind, value = doc.xref_get_key(xref, "Resources")
        resources = doc.get_new_xref()
        doc.update_object(resources, doc.xref_object(int(value.split()[0])) if kind == "xref" else value)
        state = doc.get_new_xref()
        doc.update_object(state, "<< /Type /ExtGState /ca 0.8 >>")
        states = doc.get_new_xref()
        doc.update_object(states, f"<< /Existing {state} 0 R >>")
        doc.xref_set_key(resources, "ExtGState", f"{states} 0 R")
        doc.xref_set_key(xref, "Resources", f"{resources} 0 R")
        imported.append((xref, state))
        return xref
    monkeypatch.setattr(p.Page, "show_pdf_page", indirect)
    with p.open() as source, p.open() as target:
        source.new_page(width=200, height=80).insert_text((15, 40), "Sample")
        page = target.new_page()
        annot = page.add_stamp_annot(p.Rect(50, 80, 250, 160))
        set_appearance(page, annot, source, .2)
        xref, state = imported[0]
        assert target.xref_get_key(xref, "Resources/ExtGState/Existing") == ("xref", f"{state} 0 R")
        assert float(target.xref_get_key(xref, "Resources/ExtGState/ZhiXuOpacity/ca")[1]) == pytest.approx(.2)
        assert target.xref_get_key(annot.xref, "AP/N") == ("xref", f"{xref} 0 R")


def test_plain_save_cancellation_returns_without_committing(monkeypatch):
    import app.ui as ui
    import app.pdfworker as worker
    from app.output import check_cancel
    messages, committed = [], []
    host = SimpleNamespace(ws=SimpleNamespace(outline=None), store=object(),
                           statusBar=lambda: SimpleNamespace(showMessage=lambda text, *_: messages.append(text)))
    def operation(parent, title, work, *, cancellable=False):
        return work(lambda *_: None, (lambda: True) if cancellable else None)
    def write(store, pages, path, *, cancel=None, **kwargs):
        check_cancel(cancel)
        committed.append(path)
        return object()
    monkeypatch.setattr(ui, "run_operation", operation)
    monkeypatch.setattr(worker, "write_pdf", write)
    assert MainWindow._write_pdf(host, [], "generated.pdf", "保存 PDF") is None
    assert messages and not committed


@pytest.mark.parametrize("count", [1, 2, 4, 6, 9, 16])
@pytest.mark.parametrize("duplex", ["none", "long_edge", "short_edge"])
def test_nup_preserves_custom_order_partial_face_and_reverse(count, duplex):
    pages = [PageRef("1", "generated.pdf", i) for i in range(19)]
    options = PrintOptions(page_indices=[0, 3, 4, 8, 12, 17, 18], pages_per_face=count,
                           duplex=duplex, reverse=True)
    class Sizes:
        def page_size(self, ref):
            return (595, 842)
    sheets = build_sheets(pages, options, Sizes())
    indices = [i for sheet in sheets for back in (False, True) for i in face_pages(sheet, back)]
    assert indices == list(reversed(options.page_indices))
    assert all(len(face_pages(sheet, back)) <= count for sheet in sheets for back in (False, True))
    if duplex == "none":
        assert all(not face_pages(sheet, True) for sheet in sheets)


@pytest.mark.parametrize("order", ["row", "column"])
def test_grid_cells_are_inside_printable_area_and_do_not_overlap(order):
    area = QRectF(12, 24, 640, 800)
    options = PrintOptions(pages_per_face=6, cell_order=order)
    cells = list(face_cells(area, range(6), options))
    assert all(area.contains(cell) for _, cell in cells)
    assert all(not cell.intersects(other) for i, (_, cell) in enumerate(cells)
               for _, other in cells[i + 1:])
    second = cells[1][1]
    assert (second.x() > cells[0][1].x()) if order == "row" else (second.y() > cells[0][1].y())


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_multiline_text_box_appearance_survives_reopen_edit_and_move(rotation):
    with p.open() as doc:
        page = doc.new_page(width=400, height=600)
        page.insert_text((30, 30), "Original body")
        page.set_rotation(rotation)
        area = tuple(p.Rect(40, 80, 280, 100) * page.derotation_matrix)
        edit = PageEdit("freetext", area, text="First line\nSecond line\nThird line",
                        color=(.8, .1, .2), annotation_id="zhixu-text-case",
                        details=(("font", "Helvetica"), ("fontsize", 16), ("rotation", rotation)))
        apply_edits(page, (edit,))
        page = doc.reload_page(page)
        annot = next(page.annots())
        assert annot.type[0] == p.PDF_ANNOT_FREE_TEXT
        assert (annot.rect * page.rotation_matrix).height > 20
        assert annot.flags & p.PDF_ANNOT_IS_PRINT
        assert page.get_text().strip() == "Original body"
        data = doc.tobytes()
    with p.open(stream=data, filetype="pdf") as doc:
        page = doc[0]
        annot = next(page.annots())
        bounds = tuple(annot.rect)
        apply_edits(page, (PageEdit("freetext_update", bounds, text="Updated\nText",
                    color=(.1, .2, .9), annotation_id="zhixu-text-case", details=edit.details),))
        page = doc.reload_page(page)
        annot = next(page.annots())
        appearance = doc.xref_get_key(annot.xref, "AP/N")
        apply_edits(page, (PageEdit("move", ((60, 160),), annotation_id="zhixu-text-case"),))
        page = doc.reload_page(page)
        annot = next(page.annots())
        assert annot.info["content"] == "Updated\nText"
        assert annot.rect.x0 == pytest.approx(60)
        assert doc.xref_get_key(annot.xref, "AP/N") == appearance
        pixmap = annot.get_pixmap()
        assert any(sample < 240 for sample in pixmap.samples)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_watermark_is_removable_after_reopen_and_preserves_other_content(rotation):
    with p.open() as doc:
        page = doc.new_page(width=400, height=600)
        page.set_rotation(rotation)
        page.insert_text((30, 40), "Original body")
        note = page.add_text_annot((50, 100), "Keep this note")
        doc.xref_set_key(note.xref, "NM", p.get_pdf_str("zhixu-watermark-unrelated"))
        edit = PageEdit("watermark", text="Sample", color=(.3, .4, .5),
                        annotation_id="zhixu-watermark-case",
                        details=(("size", 35), ("opacity", .2), ("angle", 45),
                                 ("position", "top-right"), ("tile", True), ("rotation", rotation)))
        apply_edits(page, (edit,))
        page = doc.reload_page(page)
        owned = [a for a in page.annots() if is_watermark(page, a)]
        assert owned
        assert "Sample" in owned[0].get_text()
        assert all(a.flags & p.PDF_ANNOT_IS_PRINT for a in owned)
        assert page.get_text().strip() == "Original body"
        data = doc.tobytes()
    with p.open(stream=data, filetype="pdf") as doc:
        page = doc[0]
        apply_edits(page, (PageEdit("watermark_remove"),))
        page = doc.reload_page(page)
        assert page.get_text().strip() == "Original body"
        assert [a.info["content"] for a in page.annots()] == ["Keep this note"]
