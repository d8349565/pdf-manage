from dataclasses import replace
from pathlib import Path

import pymupdf as p
import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from app.output import OperationCancelled
from app.pdfio import EncryptionOptions, PdfError, PdfStore, WriteOptions
from app.workspace import OutlineEntry, PageEdit, Workspace


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def make_pdf(path, count=2, rotation=0, crop=False, permissions=None):
    with p.open() as doc:
        for index in range(count):
            page = doc.new_page(width=400, height=500)
            page.insert_text((50, 90), "DATE2020 KEEP", fontsize=12)
            page.draw_line((20, 110), (380, 110))
            if crop:
                page.set_cropbox(p.Rect(20, 30, 380, 470))
            page.set_rotation(rotation)
        args = {} if permissions is None else dict(encryption=p.PDF_ENCRYPT_AES_256, user_pw="", owner_pw="owner", permissions=permissions)
        doc.save(path, **args)


@pytest.mark.parametrize("algorithm", [p.PDF_ENCRYPT_AES_128, p.PDF_ENCRYPT_AES_256])
def test_permission_only_round_trip(qapp, tmp_path, algorithm):
    source, target = tmp_path / "source.pdf", tmp_path / "protected.pdf"
    make_pdf(source)
    permissions = 4095 & ~(p.PDF_PERM_PRINT | p.PDF_PERM_PRINT_HQ | p.PDF_PERM_COPY)
    store = PdfStore()
    pages = store.load(str(source))
    store.write(pages, str(target), options=WriteOptions(encryption=EncryptionOptions("", "owner", permissions, algorithm)))
    with p.open(target) as doc:
        assert not doc.needs_pass
        assert doc.authenticate("") == 2
        assert not doc.permissions & p.PDF_PERM_COPY
        assert not doc.permissions & p.PDF_PERM_PRINT
        assert doc.authenticate("owner") == 4
    assert store.load(str(target))
    with pytest.raises(PdfError):
        EncryptionOptions("", "").validate()
    store.close_all()


def test_protection_modes_use_same_validation(qapp, tmp_path, monkeypatch):
    from app import exportdialogs
    monkeypatch.setattr(exportdialogs, "QSettings", lambda *args: QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    dialog = exportdialogs.EncryptionDialog()
    assert not dialog.start.isEnabled()
    dialog.mode.setCurrentIndex(1)
    dialog.owner.setText("owner")
    assert dialog.start.isEnabled()
    dialog.accept()
    assert dialog.setting.user_password == ""
    dialog.setting.validate()
    dialog = exportdialogs.EncryptionDialog()
    dialog.mode.setCurrentIndex(1)
    dialog.owner.setText("owner")
    dialog.printing.setChecked(True)
    dialog.copying.setChecked(True)
    assert not dialog.start.isEnabled()
    dialog.mode.setCurrentIndex(2)
    dialog.password.setText("user")
    dialog.confirm.setText("different")
    assert not dialog.start.isEnabled()
    dialog.confirm.setText("user")
    assert dialog.start.isEnabled()
    dialog.reject()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("extra", [0, 90])
def test_page_numbers_match_rotated_preview(qapp, tmp_path, rotation, extra):
    from app.pagenumbers import PageNumberOptions
    source, target = tmp_path / "source.pdf", tmp_path / "numbered.pdf"
    make_pdf(source, rotation=rotation, crop=True)
    store = PdfStore()
    pages = [replace(ref, rotation=extra) for ref in store.load(str(source))]
    options = PageNumberOptions(start=7)
    assert not store.number_preview(pages, 0, options, 400).isNull()
    store.write(pages, str(target), options=WriteOptions(page_numbers=options))
    with p.open(target) as doc:
        page = doc[0]
        word = next(word for word in page.get_text("words") if word[4] == "7")
        visible = p.Rect(word[:4]) * page.rotation_matrix
        assert visible.y0 > page.rect.height * .8
        assert page.rotation == (rotation + extra) % 360
    with p.open(source) as doc:
        assert "7" not in doc[0].get_text()
    with pytest.raises(PdfError):
        store.write(pages, str(source), options=WriteOptions(page_numbers=options))
    store.close_all()


def test_page_labels_skip_cover_and_chinese_font_is_subset(qapp, tmp_path):
    from app.pagenumbers import PageNumberOptions
    source = tmp_path / "source.pdf"
    make_pdf(source, count=3)
    store = PdfStore()
    pages = store.load(str(source))
    labels = tmp_path / "labels.pdf"
    store.write(pages, str(labels), options=WriteOptions(page_numbers=PageNumberOptions(mode="labels", skip=1, start=2, label_style="r")))
    with p.open(labels) as doc:
        assert [page.get_label() for page in doc] == ["", "ii", "iii"]
        assert "DATE2020" in doc[1].get_text()
    labeled_pages = store.load(str(labels))
    locked_source = store._sources[labeled_pages[0].source_id]
    locked_source.lock.acquire()
    try:
        assert store.page_label(labeled_pages[0]) == ""
        assert store.page_label(labeled_pages[1]) == "ii"
    finally:
        locked_source.lock.release()
    numbered = tmp_path / "chinese.pdf"
    store.write(pages, str(numbered), options=WriteOptions(page_numbers=PageNumberOptions(skip=1, format="chinese")))
    with p.open(numbered) as doc:
        assert "第" not in doc[0].get_text()
        assert "第 1 页，共 2 页" in doc[1].get_text()
    assert numbered.stat().st_size < 100000
    store.close_all()


def test_annotations_are_independent_for_duplicate_pages_and_undo(qapp, tmp_path):
    source, target = tmp_path / "source.pdf", tmp_path / "annotated.pdf"
    make_pdf(source, count=1)
    store = PdfStore()
    ref = store.load(str(source))[0]
    ws = Workspace()
    ws.open_replace([ref, ref], str(source))
    assert ws.pages[0].page_id != ws.pages[1].page_id
    original_key = ws.pages[0].render_key
    edit = PageEdit("rect", (30, 150, 120, 210), color=(1, 0, 0), annotation_id="rectangle")
    ws.edit_page(0, edit)
    assert ws.dirty and ws.pages[0].render_key != original_key
    assert ws.pages[1].render_key == original_key
    ws.undo_stack.undo()
    assert not ws.dirty
    ws.undo_stack.redo()
    ws.edit_page(0, PageEdit("style", annotation_id="rectangle", color=(0, .5, 1), width=4))
    store.write(ws.pages, str(target), register_result=True)
    with p.open(target) as doc:
        page = doc[0]
        annotation = next(page.annots())
        assert annotation.type[0] == p.PDF_ANNOT_SQUARE
        assert annotation.border["width"] == 4
        assert annotation.colors["stroke"] == pytest.approx((0, .5, 1))
        assert not list(doc[1].annots() or ())
    with p.open(source) as doc:
        assert not list(doc[0].annots() or ())
    store.close_all()


def test_native_notes_highlights_ink_and_delete(qapp, tmp_path):
    from app.pdfedits import selection_quads
    source, target = tmp_path / "source.pdf", tmp_path / "review.pdf"
    make_pdf(source, count=1)
    store = PdfStore()
    ref = store.load(str(source))[0]
    snapshot = store.text_snapshot(ref)
    quads = selection_quads(snapshot["raw"], (40, 60, 150, 105))
    assert quads
    for edit in (
        PageEdit("highlight", quads, annotation_id="high"),
        PageEdit("underline", quads, annotation_id="under"),
        PageEdit("note", ((200, 80),), text="Review note", annotation_id="note"),
        PageEdit("ink", ((50, 200), (80, 230), (120, 220)), annotation_id="ink"),
        PageEdit("ellipse", (150, 200, 230, 260), annotation_id="ellipse"),
        PageEdit("arrow", ((100, 300), (200, 310)), annotation_id="arrow"),
        PageEdit("delete", annotation_id="under"),
    ):
        ref = ref.edited(edit)
    assert not store.render_qimage(ref, 400).isNull()
    assert store._text_cache_bytes <= 8 * 1024 * 1024
    store.write([ref], str(target))
    with p.open(target) as doc:
        kinds = sorted(a.type[0] for a in doc[0].annots())
        assert kinds == sorted([p.PDF_ANNOT_HIGHLIGHT, p.PDF_ANNOT_TEXT, p.PDF_ANNOT_INK, p.PDF_ANNOT_CIRCLE, p.PDF_ANNOT_LINE])
    store.close_all()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_original_text_is_removed_and_neighbors_preserved(qapp, tmp_path, rotation):
    source, target = tmp_path / "source.pdf", tmp_path / "corrected.pdf"
    make_pdf(source, count=1, rotation=rotation, crop=True)
    store = PdfStore()
    ref = store.load(str(source))[0]
    with p.open(source) as doc:
        rectangle = next(word[:4] for word in doc[0].get_text("words") if word[4] == "DATE2020")
        drawing_count = len(doc[0].get_drawings())
    edit = store.prepare_replacement(ref, rectangle, "DATE2026")
    candidate = ref.edited(edit)
    assert not store.render_qimage(candidate, 400).isNull()
    store.write([candidate], str(target))
    with p.open(target) as doc:
        text = doc[0].get_text()
        assert "DATE2020" not in text and "DATE2026" in text and "KEEP" in text
        assert len(doc[0].get_drawings()) == drawing_count
    with p.open(source) as doc:
        assert "DATE2020" in doc[0].get_text()
    with pytest.raises(ValueError, match="宽度"):
        store.prepare_replacement(ref, rectangle, "MUCH LONGER REPLACEMENT")
    store.close_all()


@pytest.mark.parametrize("unsafe", ["redact", "link", "widget", "invisible", "image"])
def test_correction_refuses_unsafe_regions(qapp, tmp_path, unsafe):
    source = tmp_path / "unsafe.pdf"
    make_pdf(source, count=1)
    with p.open(source) as doc:
        page = doc[0]
        rectangle = next(word[:4] for word in page.get_text("words") if word[4] == "DATE2020")
        if unsafe == "redact":
            page.add_redact_annot((200, 300, 250, 330))
        elif unsafe == "link":
            page.insert_link({"kind": p.LINK_URI, "from": p.Rect(rectangle), "uri": "https://example.invalid"})
        elif unsafe == "widget":
            widget = p.Widget()
            widget.field_name = "text"
            widget.field_type = p.PDF_WIDGET_TYPE_TEXT
            widget.rect = p.Rect(rectangle)
            page.add_widget(widget)
        elif unsafe == "invisible":
            page.insert_text((50, 90), "HIDDEN", fontsize=12, render_mode=3)
        else:
            pixmap = p.Pixmap(p.csRGB, p.IRect(0, 0, 10, 10), False)
            pixmap.clear_with(255)
            page.insert_image(p.Rect(rectangle), stream=pixmap.tobytes("png"), overlay=False)
        doc.saveIncr()
    store = PdfStore()
    ref = store.load(str(source))[0]
    with pytest.raises(ValueError):
        store.prepare_replacement(ref, rectangle, "DATE2026")
    store.close_all()


def test_permissions_distinguish_annotations_from_content(qapp, tmp_path):
    source = tmp_path / "restricted.pdf"
    permissions = 4095 & ~(p.PDF_PERM_MODIFY | p.PDF_PERM_ASSEMBLE)
    make_pdf(source, count=1, permissions=permissions)
    store = PdfStore()
    ref = store.load(str(source))[0]
    assert store.operation_reason([ref], "modify")
    assert not store.operation_reason([ref], "annotate")
    annotated = ref.edited(PageEdit("note", ((200, 80),), text="Allowed", annotation_id="note"))
    store.write([annotated], str(tmp_path / "notes.pdf"))
    with pytest.raises(PdfError, match="原文"):
        store.prepare_replacement(ref, (40, 60, 110, 100), "new")
    store.close_all()


def test_outlines_follow_page_instances_and_restore_after_undo(qapp, tmp_path):
    source, target = tmp_path / "source.pdf", tmp_path / "outline.pdf"
    make_pdf(source, count=3)
    store = PdfStore()
    ws = Workspace()
    ws.open_replace(store.load(str(source)), str(source))
    target_id = ws.pages[1].page_id
    ws.set_outline([OutlineEntry(1, "Chapter", target_id), OutlineEntry(2, "Child", ws.pages[2].page_id)])
    ws.move_page(1, 0)
    store.write(ws.pages, str(target), options=WriteOptions(outline=ws.outline))
    with p.open(target) as doc:
        assert doc.get_toc()[0] == [1, "Chapter", 1]
    ws.select_index(0)
    ws.delete_selected()
    deleted = tmp_path / "deleted.pdf"
    result = store.write(ws.pages, str(deleted), options=WriteOptions(outline=ws.outline))
    assert result.warnings
    with p.open(deleted) as doc:
        assert doc.get_toc(False)[0][3]["kind"] == p.LINK_NONE
    ws.undo_stack.undo()
    assert ws.pages[0].page_id == target_id
    result = store.write(ws.pages, str(tmp_path / "saved.pdf"), register_result=True, options=WriteOptions(outline=ws.outline))
    assert result.pages[0].page_id == target_id and not result.pages[0].edits
    store.close_all()


def test_merge_creates_filename_roots_without_source_bookmarks(qapp, tmp_path):
    store = PdfStore()
    pages = []
    for name in ("Alpha", "Beta"):
        source = tmp_path / (name + ".pdf")
        make_pdf(source, count=1)
        pages += store.load(str(source))
    output = tmp_path / "merged.pdf"
    store.write(pages, str(output))
    with p.open(output) as doc:
        assert doc.get_toc() == [[1, "Alpha", 1], [1, "Beta", 2]]
    store.close_all()


def test_markdown_layout_local_images_and_cancel(qapp, tmp_path):
    from app.markdownconvert import markdown_to_pdf
    image = QImage(80, 60, QImage.Format.Format_RGB32)
    image.fill(0xff008800)
    assert image.save(str(tmp_path / "local.png"))
    source = tmp_path / "article.md"
    source.write_text("# 纸序 PDF\n\n**Bold**\n\n| Field | Value |\n| --- | --- |\n| A | 123 |\n\n```python\nprint(123)\n```\n\n![local](local.png)\n\n![missing](missing.png)\n\n![remote](https://example.invalid/image.png)\n\n" +
                      "\n\n".join(f"Paragraph {i}" for i in range(180)), encoding="utf-8")
    target = tmp_path / "article.pdf"
    result = markdown_to_pdf(source, target)
    assert any("missing.png" in warning for warning in result.warnings)
    assert any("example.invalid" in warning for warning in result.warnings)
    with p.open(target) as doc:
        text = "".join("".join(page.get_text().split()) for page in doc)
        assert "纸序PDF" in text and "print(123)" in text and "Paragraph179" in text
        assert len(doc) > 1 and doc[0].get_images()
    assert target.stat().st_size < 100000
    cancelled = tmp_path / "cancelled.pdf"
    flag = [False]
    def progress(current, total, text):
        if current:
            flag[0] = True
    with pytest.raises(OperationCancelled):
        markdown_to_pdf(source, cancelled, progress_callback=progress, cancel=lambda: flag[0])
    assert not cancelled.exists()
    assert not list(tmp_path.glob(".zhixu-md-*"))


def test_word_direction_collection_docx_checks_and_preflight(tmp_path, monkeypatch):
    from app import wordconvert
    from zipfile import ZipFile
    source = tmp_path / "input.pdf"
    make_pdf(source, count=1)
    (tmp_path / "input.docx").write_bytes(b"not a docx")
    files, errors = wordconvert.collect_files([tmp_path], direction="pdf_to_word")
    assert files == [str(source)] and not errors
    wordconvert.validate_pdf_for_word(source)
    protected = tmp_path / "protected.pdf"
    make_pdf(protected, count=1, permissions=4095)
    with pytest.raises(ValueError, match="解密"):
        wordconvert.validate_pdf_for_word(protected)
    with pytest.raises(ValueError):
        wordconvert.validate_docx(tmp_path / "input.docx")
    valid = tmp_path / "valid.docx"
    with ZipFile(valid, "w") as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>')
    wordconvert.validate_docx(valid)
    monkeypatch.setattr(wordconvert, "word_available", lambda: True)
    calls = []
    def convert(source, target, overwrite, progress, cancel, *, direction):
        calls.append(direction)
        return "success", "done", "16.0"
    monkeypatch.setattr(wordconvert, "_convert_one", convert)
    results, cancelled, version = wordconvert.convert_batch([(str(source), str(valid))], True, lambda *args: None, lambda: False, direction="pdf_to_word")
    assert calls == ["pdf_to_word"] and results[0]["state"] == "success" and version == "16.0" and not cancelled


def test_text_correction_cjk_fallback_and_saved_undo(qapp, tmp_path):
    from app.pdfedits import cjk_font_buffer
    source = tmp_path / "chinese.pdf"
    with p.open() as doc:
        page = doc.new_page(width=400, height=500)
        page.insert_font(fontname="SourceCJK", fontbuffer=cjk_font_buffer())
        page.insert_text((50, 90), "纸 文字", fontsize=12, fontname="SourceCJK")
        doc.subset_fonts()
        doc.save(source, garbage=4, deflate=True)
    store = PdfStore()
    ws = Workspace()
    ws.open_replace(store.load(str(source)), str(source))
    with p.open(source) as doc:
        rectangle = next(word[:4] for word in doc[0].get_text("words") if word[4] == "纸")
    edit = store.prepare_replacement(ws.pages[0], rectangle, "序")
    original_id = ws.pages[0].page_id
    ws.edit_page(0, edit)
    result = store.write(ws.pages, str(tmp_path / "corrected.pdf"), register_result=True)
    with p.open(result.path) as doc:
        assert "序" in doc[0].get_text() and "纸" not in doc[0].get_text()
    ws.rebase_to_saved_file(result.path, list(result.pages))
    assert not ws.dirty and ws.pages[0].page_id == original_id
    ws.undo_stack.undo()
    assert ws.dirty and not ws.pages[0].edits
    assert "纸" in store.text_snapshot(ws.pages[0])["raw"]["blocks"][0]["lines"][0]["spans"][0]["chars"][0]["c"]
    store.close_all()


def test_outline_and_annotation_mouse_flow(qapp, tmp_path, monkeypatch):
    from PyQt6.QtCore import QPointF, QThreadPool, Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QInputDialog, QMessageBox
    from app.ui import MainWindow
    source = tmp_path / "source.pdf"
    make_pdf(source, count=1)
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[-1]) or 0)
    win = MainWindow()
    try:
        assert win._load_one(str(source))
        win.show()
        win.refresh()
        QThreadPool.globalInstance().waitForDone(30000)
        qapp.processEvents()
        win.sidebar_mode.setCurrentIndex(1)
        monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Chapter", True))
        win.outline_panel.add()
        assert win.outline_panel.tree.topLevelItem(0).text(0) == "Chapter"
        win.ws.undo_stack.undo()
        win.refresh()
        assert win.outline_panel.tree.topLevelItemCount() == 0
        win.open_annotation_tools()
        tools = win.annotation_tools
        tools.tool.setCurrentIndex(tools.tool.findData("rect"))
        QThreadPool.globalInstance().waitForDone(30000)
        qapp.processEvents()
        win._apply_view_zoom()
        page_rect = win.preview_view.page_rects[0]
        start = win.preview_view.mapFromScene(page_rect.topLeft() + QPointF(50, 150))
        end = win.preview_view.mapFromScene(page_rect.topLeft() + QPointF(130, 220))
        QTest.mousePress(win.preview_view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(win.preview_view.viewport(), end)
        QTest.mouseRelease(win.preview_view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        assert win.ws.pages[0].edits[-1].kind == "rect"
        output = tmp_path / "mouse.pdf"
        win.store.write(win.ws.pages, str(output))
        with p.open(output) as doc:
            page = doc[0]
            assert next(page.annots()).type[0] == p.PDF_ANNOT_SQUARE
        tools.annotations.setCurrentIndex(1)
        tools.delete()
        assert win.ws.pages[0].edits[-1].kind == "delete"
        tools.deactivate()
        assert win.preview_view.dragMode() == win.preview_view.DragMode.ScrollHandDrag
        assert not messages
    finally:
        QThreadPool.globalInstance().waitForDone(30000)
        win.ws.reset()
        win.store.close_all()
        win.close()
        qapp.processEvents()


def test_page_number_dialog_preview_modes(qapp, tmp_path):
    from PyQt6.QtCore import QThreadPool
    from app.outputui import PageNumbersDialog
    from app.ui import MainWindow
    source = tmp_path / "source.pdf"
    make_pdf(source)
    win = MainWindow()
    try:
        win._load_one(str(source))
        win.refresh()
        dialog = PageNumbersDialog(win)
        dialog.timer.stop()
        dialog.request_preview()
        QThreadPool.globalInstance().waitForDone(30000)
        qapp.processEvents()
        assert dialog.preview.pixmap() and not dialog.preview.pixmap().isNull()
        dialog.mode.setCurrentIndex(1)
        dialog.label_style.setCurrentIndex(1)
        dialog.request_preview()
        assert dialog.preview.text() == "当前页阅读器标签：i"
        dialog.reject()
    finally:
        QThreadPool.globalInstance().waitForDone(30000)
        win.ws.reset()
        win.store.close_all()
        win.close()
        qapp.processEvents()


def test_word_event_publication_tolerates_windows_reader_lock(tmp_path):
    import json
    import threading
    import time
    from app.wordconvert import _write_json
    path = tmp_path / "event.json"
    path.write_text("{}", encoding="utf-8")
    errors = []
    def publish():
        try:
            _write_json(path, {"phase": "complete"})
        except Exception as exc:
            errors.append(exc)
    with path.open("r", encoding="utf-8"):
        worker = threading.Thread(target=publish)
        worker.start()
        time.sleep(.08)
    worker.join(2)
    assert not errors and not worker.is_alive()
    assert json.loads(path.read_text(encoding="utf-8"))["phase"] == "complete"


def test_word_first_use_decline_does_not_change_settings(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    import app.wordui as wordui
    monkeypatch.setattr(wordui, "word_available", lambda: True)
    monkeypatch.setattr(wordui, "pdf_import_needs_confirmation", lambda: True)
    changes = []
    monkeypatch.setattr(wordui, "allow_pdf_import", lambda: changes.append(True))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    window = wordui.WordConverterWindow(direction="pdf_to_word")
    window.table.setRowCount(1)
    window._set_item(0, 0, str(tmp_path / "source.pdf"), editable=False)
    window._set_item(0, 1, str(tmp_path / "output.docx"), editable=True)
    window._set_item(0, 2, "待转换", editable=False)
    window.convert()
    assert not changes
    window.close()


def test_inline_text_replace_in_place_mouse_flow(qapp, tmp_path):
    from PyQt6.QtCore import QPointF, QThreadPool, Qt
    from PyQt6.QtTest import QTest
    from app.pdfedits import cjk_font_buffer
    from app.ui import MainWindow
    source = tmp_path / "inline_test.pdf"
    with p.open() as doc:
        page = doc.new_page(width=400, height=500)
        page.insert_font(fontname="SourceCJK", fontbuffer=cjk_font_buffer())
        page.insert_text((50, 90), "纸序测试文本", fontsize=12, fontname="SourceCJK")
        doc.subset_fonts()
        doc.save(source, garbage=4, deflate=True)
    win = MainWindow()
    try:
        assert win._load_one(str(source))
        win.show()
        win.refresh()
        QThreadPool.globalInstance().waitForDone(30000)
        qapp.processEvents()
        win.open_annotation_tools()
        tools = win.annotation_tools
        tools.tool.setCurrentIndex(tools.tool.findData("replace"))
        QThreadPool.globalInstance().waitForDone(30000)
        qapp.processEvents()
        win._apply_view_zoom()
        page_rect = win.preview_view.page_rects[0]
        pos = win.preview_view.mapFromScene(page_rect.topLeft() + QPointF(60, 85))
        QTest.mousePress(win.preview_view.viewport(), Qt.MouseButton.LeftButton, pos=pos)
        QTest.mouseRelease(win.preview_view.viewport(), Qt.MouseButton.LeftButton, pos=pos)
        assert getattr(tools, "_inline_replace", None) is not None
        container = tools._inline_replace["widget"]
        assert container.isVisible()
        from PyQt6.QtWidgets import QLineEdit
        edit = container.findChild(QLineEdit)
        assert edit is not None and "纸序测试文本" in edit.text()
        edit.setText("纸序更新成功")
        edit.returnPressed.emit()
        assert getattr(tools, "_inline_replace", None) is None
        assert win.ws.pages[0].edits[-1].kind == "replace"
    finally:
        QThreadPool.globalInstance().waitForDone(30000)
        win.ws.reset()
        win.store.close_all()
        win.close()
        qapp.processEvents()



def test_commit_file_tolerates_transient_permission_error(tmp_path, monkeypatch):
    import os
    from app.output import commit_file, stamp
    source = tmp_path / "temp.docx"
    target = tmp_path / "target.docx"
    source.write_text("content", encoding="utf-8")
    expected = stamp(str(target))

    attempts = 0
    orig_rename = os.rename
    def flaky_rename(src, dst):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("[WinError 32] 另一个程序正在使用此文件，进程无法访问。")
        return orig_rename(src, dst)

    monkeypatch.setattr(os, "rename", flaky_rename)
    commit_file(str(source), str(target), expected)

    assert target.exists()
    assert target.read_text(encoding="utf-8") == "content"
    assert attempts == 3


def test_word_worker_closes_document_before_commit(tmp_path, monkeypatch):
    import json
    from app import wordconvert
    folder = tmp_path / "work"
    folder.mkdir()
    source = tmp_path / "source.pdf"
    target = tmp_path / "target.docx"
    source.write_text("%PDF-1.4 dummy", encoding="utf-8")
    req = dict(source=str(source), target=str(target), overwrite=True,
               expected=None, job="PageArkWord-test", direction="pdf_to_word")
    (folder / "request.json").write_text(json.dumps(req), encoding="utf-8")

    closed = False
    committed_while_closed = False

    class DummyDoc:
        def SaveAs2(self, FileName, FileFormat, AddToRecentFiles):
            Path(FileName).write_text("docx dummy", encoding="utf-8")
        def Close(self, SaveChanges=False):
            nonlocal closed
            closed = True

    class DummyWord:
        Version = "16.0"
        Visible = False
        DisplayAlerts = 0
        ScreenUpdating = False
        AutomationSecurity = 0
        Options = type("Options", (), {"UpdateLinksAtOpen": False, "UpdateLinksAtPrint": False, "UpdateFieldsAtPrint": False})()
        class Documents:
            @staticmethod
            def Add(Visible=False):
                class Win:
                    Hwnd = 1234
                class Doc:
                    Windows = type("Windows", (), {"Item": staticmethod(lambda i: Win())})()
                    def Close(self, SaveChanges=False): pass
                return Doc()
            @staticmethod
            def Open(*args, **kwargs):
                return DummyDoc()
        def Quit(self, SaveChanges=False): pass

    monkeypatch.setattr(wordconvert, "validate_pdf_for_word", lambda s: None)
    monkeypatch.setattr(wordconvert, "validate_docx", lambda p: None)
    monkeypatch.setattr(wordconvert, "pdf_import_needs_confirmation", lambda v: False)

    orig_commit = wordconvert.commit_file
    def intercept_commit(temp, target, expected, cancel=None):
        nonlocal committed_while_closed
        committed_while_closed = closed
        return orig_commit(temp, target, expected, cancel=cancel)

    monkeypatch.setattr(wordconvert, "commit_file", intercept_commit)
    import pythoncom, win32process, win32job, win32api
    monkeypatch.setattr(pythoncom, "CoInitialize", lambda: None)
    monkeypatch.setattr(pythoncom, "CoUninitialize", lambda: None)
    monkeypatch.setattr(win32process, "EnumProcesses", lambda: [100])
    monkeypatch.setattr(win32process, "GetWindowThreadProcessId", lambda hwnd: (0, 200))
    monkeypatch.setattr(win32job, "OpenJobObject", lambda *args: type("Job", (), {"Close": lambda self: None})())
    monkeypatch.setattr(win32api, "OpenProcess", lambda *args: type("Handle", (), {"Close": lambda self: None})())
    monkeypatch.setattr(win32job, "AssignProcessToJobObject", lambda *args: None)
    from win32com import client
    monkeypatch.setattr(client, "DispatchEx", lambda progid: DummyWord())

    wordconvert.worker_main(str(folder))
    result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    assert result["state"] == "success"
    assert closed is True
    assert committed_while_closed is True
    assert target.exists()


def test_annotation_tools_replace_robustness(qapp, tmp_path):
    from app.editingui import AnnotationTools
    from app.pdfio import PdfStore
    from app.workspace import Workspace
    source = tmp_path / "test_replace.pdf"
    make_pdf(source, count=1)

    class DummyHost:
        def __init__(self):
            self.store = PdfStore()
            self.ws = Workspace()
            self.ws.load_first_or_merge(self.store.load(str(source)), str(source))
            self.preview_view = type("DummyPreviewView", (), {
                "viewport": lambda s: type("DummyVp", (), {
                    "installEventFilter": lambda s, f: None,
                    "removeEventFilter": lambda s, f: None,
                })(),
                "setDragMode": lambda s, m: None,
                "page_rects": [type("Rect", (), {"topLeft": lambda s: None})()],
                "_scene": type("Scene", (), {"removeItem": lambda s, i: None, "addItem": lambda s, i: None})(),
            })()
        def _allow_operation(self, op): return True
        def _preview_render_width(self): return 800
        def _cache_page_image(self, k, img): pass
        def refresh(self): pass
        def go_to_page(self, n): pass

    host = DummyHost()
    tools = AnnotationTools(host)
    tools.tool.setCurrentIndex(9)
    assert tools.tool.currentData() == "replace"
    assert tools.ensure_snapshot() is True
    tools.deactivate()
    host.store.close_all()


def test_replacement_allows_longer_text_when_space_permits(qapp, tmp_path):
    from app.pdfio import PdfStore
    source = tmp_path / "space_test.pdf"
    doc = p.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((100, 150), "短文本", fontsize=14)
    doc.save(str(source))
    doc.close()

    store = PdfStore()
    ref = store.load(str(source))[0]
    words = store.text_snapshot(ref)["raw"]
    chars = [c for b in words["blocks"] for l in b["lines"] for s in l["spans"] for c in s["chars"]]
    assert chars
    rect = p.Rect(chars[0]["bbox"])
    for c in chars[1:]:
        rect |= p.Rect(c["bbox"])

    # 较长的新文本（原文字长约 42pt，新文字长约 120pt，但右侧有 450pt 空间）
    longer_text = "短文本加上很长的后续内容2525"
    prepared = store.prepare_replacement(ref, rect, longer_text)
    assert prepared.text == longer_text
    applied = ref.edited(prepared)
    target = tmp_path / "replaced.pdf"
    store.write([applied], str(target))

    with p.open(target) as check_doc:
        text = check_doc[0].get_text()
        assert longer_text in text

    store.close_all()


