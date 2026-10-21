"""Page splitting uses generated PDFs, never personal documents."""

import pymupdf as p
import pytest

from app.pdfio import EncryptionOptions, PdfError, PdfStore, WriteNeedsConfirmation, WriteOptions
from app.pdfworker import write_pdf
from app.workspace import Workspace


def test_split_keeps_visible_content_and_undo(tmp_path):
    source = tmp_path / "source.pdf"
    with p.open() as doc:
        page = doc.new_page(width=600, height=400)
        page.insert_text((30, 40), "LEFT")
        page.insert_text((370, 40), "RIGHT")
        mark = page.add_rect_annot(p.Rect(40, 80, 80, 120))
        mark.set_colors(stroke=(1, 0, 0), fill=(1, 0, 0))
        mark.update()
        doc.set_toc([[1, "开始", 1]])
        doc.save(source)
    original = source.read_bytes()
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        pages = store.split_page(ref, "vertical", (.5,))
        ws = Workspace()
        ws.open_replace([ref], str(source))
        ws.replace_page_with(0, pages, outline=store.outline_entries([ref]))
        assert len(ws.pages) == 2
        assert ws.outline[0].target_id == pages[0].page_id
        target = tmp_path / "split.pdf"
        saved = write_pdf(store, ws.pages, str(target),
                          options=WriteOptions(outline=ws.outline), register_result=True)
        assert len(saved.pages) == 2
        assert not store._source(saved.pages[0].source_id).needs_encryption_choice
        with p.open(target) as result:
            assert result.page_count == 2
            assert result.get_toc()[0][1:] == ["开始", 1]
            assert result[0].rect == p.Rect(0, 0, 300, 400)
            assert "LEFT" in result[0].get_text() and "RIGHT" not in result[0].get_text()
            assert "RIGHT" in result[1].get_text() and "LEFT" not in result[1].get_text()
            assert not list(result[0].annots() or ())
            pix = result[0].get_pixmap()
            red, green, blue = pix.pixel(60, 100)[:3]
            assert red > green * 2 and red > blue * 2
        ws.undo_stack.undo()
        assert ws.pages == [ref] and ws.outline is None
        ws.undo_stack.redo()
        assert ws.pages == pages
        assert source.read_bytes() == original
    finally:
        store.close_all()


@pytest.mark.parametrize("direction,cuts,fit_a4,expected", [
    ("horizontal", (.33, .67), False, (600, 132)),
    ("vertical", (.5,), True, tuple(p.paper_size("a4"))),
    ("vertical", (.10, .15), False, (60, 400)),
])
def test_split_layout_options(tmp_path, direction, cuts, fit_a4, expected):
    source = tmp_path / "source.pdf"
    with p.open() as doc:
        doc.new_page(width=600, height=400)
        doc.save(source)
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        pages = store.split_page(ref, direction, cuts, fit_a4=fit_a4)
        width, height = store.page_size(pages[0])
        assert len(pages) == len(cuts) + 1
        assert (width, height) == pytest.approx(expected)
    finally:
        store.close_all()


def test_split_rotated_page_with_other_pages_keeps_save_and_bookmark(tmp_path):
    source = tmp_path / "rotated.pdf"
    with p.open() as doc:
        first = doc.new_page(width=600, height=400)
        first.insert_text((30, 40), "FIRST")
        first.set_rotation(90)
        second = doc.new_page(width=600, height=400)
        second.insert_text((30, 40), "LAST")
        doc.set_toc([[1, "first", 1], [1, "last", 2]])
        doc.save(source)
    original = source.read_bytes()
    store = PdfStore()
    try:
        refs = store.load(str(source))
        parts = store.split_page(refs[0], "vertical", (.5,))
        assert store.page_size(parts[0]) == pytest.approx((200, 600))
        ws = Workspace()
        ws.open_replace(refs, str(source))
        ws.replace_page_with(0, parts, outline=store.outline_entries(refs))
        target = tmp_path / "result.pdf"
        store.write(ws.pages, str(target), options=WriteOptions(outline=ws.outline))
        with p.open(target) as doc:
            assert doc.page_count == 3
            assert "LAST" in doc[2].get_text()
            assert [item[2] for item in doc.get_toc()] == [1, 3]
        assert source.read_bytes() == original
    finally:
        store.close_all()


def test_split_flattens_form_visible_value(tmp_path):
    source = tmp_path / "form.pdf"
    with p.open() as doc:
        page = doc.new_page(width=600, height=400)
        field = p.Widget()
        field.field_name = "number"
        field.field_type = p.PDF_WIDGET_TYPE_TEXT
        field.field_value = "FORM42"
        field.rect = p.Rect(40, 90, 180, 120)
        page.add_widget(field)
        field.update()
        doc.save(source)
    store = PdfStore()
    try:
        parts = store.split_page(store.load(str(source))[0], "vertical", (.5,))
        target = tmp_path / "flat.pdf"
        store.write(parts, str(target))
        with p.open(target) as doc:
            assert "FORM42" in doc[0].get_text()
            assert not list(doc[0].widgets() or ())
    finally:
        store.close_all()


def test_split_encrypted_source_requires_output_protection_even_after_resplitting(tmp_path):
    source = tmp_path / "encrypted.pdf"
    target = tmp_path / "split.pdf"
    with p.open() as doc:
        doc.new_page(width=600, height=400).insert_text((40, 50), "PROTECTED")
        doc.save(source, encryption=p.PDF_ENCRYPT_AES_256, user_pw="open", owner_pw="owner")
    original = source.read_bytes()
    store = PdfStore()
    try:
        ref = store.load(str(source), "open")[0]
        parts = store.split_page(ref, "vertical", (.5,))
        parts = store.split_page(parts[0], "horizontal", (.5,))
        # The derived source must retain protection after the original is released.
        store.close_except({parts[0].source_id})
        with pytest.raises(WriteNeedsConfirmation) as error:
            write_pdf(store, parts, str(target))
        assert error.value.kind == "encryption" and not target.exists()
        with pytest.raises(PdfError, match="未加密输出只能另存副本"):
            write_pdf(store, parts, str(source), options=WriteOptions(preserve_encryption=False))
        setting = EncryptionOptions("new-open", "new-owner")
        saved = write_pdf(store, parts, str(target),
                          options=WriteOptions(preserve_encryption=False, encryption=setting),
                          register_result=True)
        assert store._source(saved.pages[0].source_id).safety.encrypted
        assert not store._source(saved.pages[0].source_id).needs_encryption_choice
        with p.open(target) as result:
            assert result.is_encrypted and result.authenticate("new-open") > 0
            assert len(result) == 2
        assert source.read_bytes() == original
    finally:
        store.close_all()


def test_generated_ofd_split_keeps_origin_protection(tmp_path):
    from test_ofd import test_generated_text_positioning_with_and_without_delta_x as make_ofd

    make_ofd(tmp_path, "", [10, 10, 10])
    source = tmp_path / "generated.ofd"
    original = source.read_bytes()
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        parts = store.split_page(ref, "horizontal", (.5,))
        assert not store._source(parts[0].source_id).import_warnings
        with pytest.raises(PdfError, match="OFD"):
            write_pdf(store, parts, str(source))
        target = tmp_path / "split.pdf"
        saved = write_pdf(store, parts, str(target), register_result=True)
        assert not store._source(saved.pages[0].source_id).needs_encryption_choice
        assert not store._source(saved.pages[0].source_id).import_warnings
        with p.open(target) as result:
            assert len(result) == 2 and "".join(result[0].get_text().split()) == "ABC"
        assert source.read_bytes() == original
    finally:
        store.close_all()


@pytest.mark.parametrize("direction", ["vertical", "horizontal"])
@pytest.mark.parametrize("source_rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("extra_rotation", [0, 90])
def test_cropped_rotated_split_keeps_visible_pixels(tmp_path, direction, source_rotation, extra_rotation):
    source = tmp_path / "crop.pdf"
    with p.open() as doc:
        page = doc.new_page(width=600, height=400)
        for rect, color in [((100, 50, 300, 200), (1, 0, 0)),
                            ((300, 50, 500, 200), (0, 1, 0)),
                            ((100, 200, 300, 350), (0, 0, 1)),
                            ((300, 200, 500, 350), (1, 1, 0))]:
            page.draw_rect(p.Rect(rect), color=None, fill=color)
        page.set_cropbox(p.Rect(100, 50, 500, 350))
        page.set_rotation(source_rotation)
        doc.save(source)
    original = source.read_bytes()
    store = PdfStore()
    try:
        ref = store.load(str(source))[0].rotated(extra_rotation)
        width, height = store.page_size(ref)
        expected_size = (width / 2, height) if direction == "vertical" else (width, height / 2)
        full = store.render_qimage(ref, 600)
        parts = store.split_page(ref, direction, (.5,))
        for index, part in enumerate(parts):
            assert store.page_size(part) == pytest.approx(expected_size)
            image = store.render_qimage(part, 300 if direction == "vertical" else 600)
            for fx in (.25, .75):
                for fy in (.25, .75):
                    x = round((image.width() - 1) * fx)
                    y = round((image.height() - 1) * fy)
                    full_x = x + (round(full.width() / 2) * index if direction == "vertical" else 0)
                    full_y = y + (round(full.height() / 2) * index if direction == "horizontal" else 0)
                    assert image.pixelColor(x, y) == full.pixelColor(full_x, full_y)
        assert source.read_bytes() == original
    finally:
        store.close_all()


@pytest.mark.parametrize("keep_original_page", [False, True])
def test_split_save_preserves_document_metadata_and_attachments(tmp_path, keep_original_page):
    source = tmp_path / "attached.pdf"
    xml = '<x:xmpmeta xmlns:x="adobe:ns:meta/"/>'
    with p.open() as doc:
        doc.new_page(width=600, height=400).insert_text((40, 50), "BODY")
        doc.new_page(width=600, height=400).insert_text((40, 50), "TAIL")
        doc.set_metadata({"title": "GENERATED TITLE", "author": "Generated"})
        doc.set_xml_metadata(xml)
        doc.embfile_add("generated.txt", b"generated attachment", filename="generated.txt")
        doc.save(source)
    original = source.read_bytes()
    store = PdfStore()
    try:
        refs = store.load(str(source))
        first = store.split_page(refs[0], "vertical", (.5,))
        pages = [*store.split_page(first[0], "horizontal", (.5,)), first[1]]
        pages.extend([refs[1]] if keep_original_page else store.split_page(refs[1], "vertical", (.5,)))
        target = tmp_path / "saved.pdf"
        write_pdf(store, pages, str(target))
        with p.open(target) as result:
            assert len(result) == len(pages)
            assert result.metadata["title"] == "GENERATED TITLE"
            assert result.metadata["author"] == "Generated"
            assert result.get_xml_metadata() == xml
            assert result.embfile_names() == ["generated.txt"]
            assert result.embfile_get("generated.txt") == b"generated attachment"
        assert source.read_bytes() == original
    finally:
        store.close_all()


@pytest.mark.parametrize("split_again", [False, True])
def test_split_does_not_silently_drop_special_document_structure(tmp_path, split_again):
    source = tmp_path / "open-action.pdf"
    with p.open() as doc:
        page = doc.new_page(width=600, height=400)
        page.insert_text((40, 50), "BODY")
        doc.xref_set_key(doc.pdf_catalog(), "OpenAction", f"<</S/GoTo/D[{page.xref} 0 R/Fit]>>")
        doc.save(source)
    original = source.read_bytes()
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        assert store._source(ref.source_id).safety.special_structures
        parts = store.split_page(ref, "vertical", (.5,))
        if split_again:
            parts = store.split_page(parts[0], "horizontal", (.5,))
        store.close_except({parts[0].source_id})
        target = tmp_path / "copy.pdf"
        with pytest.raises(WriteNeedsConfirmation) as error:
            write_pdf(store, parts, str(target))
        assert error.value.kind == "structure" and not target.exists()
        options = WriteOptions(allow_structure_loss=True)
        with pytest.raises(PdfError, match="特殊结构只能另存副本"):
            write_pdf(store, parts, str(source), options=options)
        saved = write_pdf(store, parts, str(target), options=options, register_result=True)
        assert saved.warnings and not store._source(saved.pages[0].source_id).lost_structures
        with p.open(target) as result:
            assert len(result) == 2
            assert result.xref_get_key(result.pdf_catalog(), "OpenAction")[0] == "null"
        assert source.read_bytes() == original
    finally:
        store.close_all()


def test_split_keeps_external_change_guard_after_original_source_released(tmp_path):
    source = tmp_path / "source.pdf"
    with p.open() as doc:
        doc.new_page(width=600, height=400).insert_text((40, 50), "FIRST")
        doc.save(source)
    store = PdfStore()
    try:
        parts = store.split_page(store.load(str(source))[0], "vertical", (.5,))
        store.close_except({parts[0].source_id})
        with p.open() as doc:
            doc.new_page().insert_text((40, 50), "EXTERNAL CHANGE")
            doc.save(source)
        changed = source.read_bytes()
        with pytest.raises(PdfError, match="目标文件.*修改"):
            write_pdf(store, parts, str(source))
        assert source.read_bytes() == changed
    finally:
        store.close_all()


def test_split_keeps_equal_attachments_from_distinct_sources(tmp_path):
    store = PdfStore()
    try:
        pages = []
        for index in range(2):
            source = tmp_path / f"source-{index}.pdf"
            with p.open() as doc:
                doc.new_page().insert_text((40, 50), "BODY")
                doc.embfile_add("generated.txt", b"same payload", filename="generated.txt")
                doc.save(source)
            pages.extend(store.split_page(store.load(str(source))[0], "vertical", (.5,)))
        target = tmp_path / "merged.pdf"
        write_pdf(store, pages, str(target))
        with p.open(target) as result:
            assert len(result.embfile_names()) == 2
            assert all(result.embfile_get(name) == b"same payload" for name in result.embfile_names())
    finally:
        store.close_all()
