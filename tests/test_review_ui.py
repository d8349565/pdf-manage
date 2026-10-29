"""Review interactions use generated PDFs, never the user's documents."""
import pymupdf as p
import pytest
from PyQt6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, QThread, QThreadPool, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QToolButton

from app.pdfedits import cjk_font_buffer
from app.ui import MainWindow
from app.workspace import PageEdit


def wait_for_mosaic(qapp, dialog):
    from PyQt6.QtCore import QElapsedTimer
    timer = QElapsedTimer()
    timer.start()
    while dialog.ready is None and timer.elapsed() < 10000:
        QTest.qWait(10)
    assert dialog.ready is not None, dialog.feedback.text()


def test_mosaic_preview_settings_apply_cancel_and_undo(reader, qapp):
    from app.mosaicui import MosaicDialog
    reader.open_annotation_tools()
    tools = reader.annotation_tools
    tools.tool.setCurrentIndex(tools.tool.findData("mosaic"))
    assert tools.ensure_snapshot()
    ref = reader.ws.pages[0]
    dialog = MosaicDialog(tools, 0, ref, (45, 60, 220, 115), tools.mosaic_settings)
    try:
        dialog.show()
        assert not dialog.apply_button.isEnabled()
        wait_for_mosaic(qapp, dialog)
        dialog.style.setCurrentIndex(dialog.style.findData("blur"))
        dialog.strength.setValue(6)
        dialog.strength.setValue(22)
        assert dialog.ready is None and not dialog.apply_button.isEnabled()
        wait_for_mosaic(qapp, dialog)
        assert dict(dialog.ready[0].details)["strength"] == 22
        assert not dialog.previews[0].item.pixmap().isNull()
        dialog.shape.setCurrentIndex(dialog.shape.findData("ellipse"))
        dialog.style.setCurrentIndex(dialog.style.findData("solid"))
        wait_for_mosaic(qapp, dialog)
        assert dialog.color_button.isVisible() and not dialog.slider.isVisible()
        dialog.apply()
        assert reader.ws.pages[0].edits[-1].kind == "mosaic"
        assert tools.mosaic_settings["shape"] == "ellipse"
        reader.ws.undo_stack.undo()
        reader.refresh()
        assert reader.ws.pages[0] is ref
        reader.ws.undo_stack.redo()
        reader.refresh()
        assert reader.ws.pages[0].edits[-1].kind == "mosaic"
    finally:
        dialog.reject()
        if dialog.worker is not None:
            dialog.worker.wait(10000)
        dialog.deleteLater()
    before = reader.ws.pages[0]
    assert tools.ensure_snapshot()
    cancel = MosaicDialog(tools, 0, before, (45, 60, 220, 115), tools.mosaic_settings)
    cancel.reject()
    cancel.deleteLater()
    assert reader.ws.pages[0] is before


def test_mosaic_drag_on_neighbor_page_and_escape_cancel(reader, qapp, monkeypatch):
    from app.mosaicui import MosaicDialog
    calls = []
    monkeypatch.setattr(MosaicDialog, "exec", lambda self:
                        calls.append((self.index, self.ref, self.area)) or QDialog.DialogCode.Rejected)
    reader.open_annotation_tools()
    tools = reader.annotation_tools
    tools.tool.setCurrentIndex(tools.tool.findData("mosaic"))
    reader._set_zoom(.5)
    settle(qapp)
    view = reader.preview_view
    view.centerOn(view.page_rects[1].topLeft() + QPointF(160, 160))
    qapp.processEvents()
    top = view.page_rects[1].topLeft()
    start = view.mapFromScene(top + QPointF(50, 60))
    end = view.mapFromScene(top + QPointF(210, 120))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), end)
    assert tools.stroke and tools.overlay is not None
    QTest.keyClick(view, Qt.Key.Key_Escape)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert not calls and not tools.stroke
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert len(calls) == 1 and calls[0][0] == 1 and calls[0][1] is reader.ws.pages[1]
    assert not any(page.edits for page in reader.ws.pages)


def test_mosaic_failed_preview_and_stale_page_cannot_apply(reader, qapp, monkeypatch):
    from PyQt6.QtCore import QElapsedTimer
    from app.mosaicui import MosaicDialog
    reader.open_annotation_tools()
    tools = reader.annotation_tools
    tools.tool.setCurrentIndex(tools.tool.findData("mosaic"))
    assert tools.ensure_snapshot()
    ref = reader.ws.pages[0]
    dialog = MosaicDialog(tools, 0, ref, (45, 60, 220, 115), tools.mosaic_settings)
    render = reader.store.render_qimage
    def fail(*args):
        raise ValueError("preview unavailable")
    monkeypatch.setattr(reader.store, "render_qimage", fail)
    try:
        dialog.show()
        dialog.timer.stop()
        dialog.start_preview()
        timer = QElapsedTimer()
        timer.start()
        while dialog.worker is not None and timer.elapsed() < 10000:
            QTest.qWait(10)
        assert dialog.worker is None
        assert "preview unavailable" in dialog.feedback.text()
        assert not dialog.apply_button.isEnabled() and dialog.ready is None
        assert reader.ws.pages[0] is ref
        monkeypatch.setattr(reader.store, "render_qimage", render)
        dialog.changed()
        wait_for_mosaic(qapp, dialog)
        reader.ws.edit_page(0, PageEdit("note", ((300, 300),), text="other edit", annotation_id="other"))
        before = reader.ws.pages[0]
        dialog.apply()
        assert reader.ws.pages[0] is before and "页面已改变" in dialog.feedback.text()
    finally:
        dialog.reject()
        if dialog.worker is not None:
            dialog.worker.wait(10000)
        dialog.deleteLater()


def test_text_box_edits_in_page_and_commits_from_inline_controls(reader, qapp):
    win = reader
    win.open_annotation_tools()
    tools = win.annotation_tools
    tools.tool.setCurrentIndex(tools.tool.findData("freetext"))
    assert tools.ensure_snapshot()
    top = win.preview_view.page_rects[0].topLeft()
    before = win.ws.pages[0]
    tools.finish("freetext", [top + QPointF(60, 220), top + QPointF(320, 280)])
    box = tools._inline_text_box
    assert box.frame.parentWidget() is win.preview_view.viewport()
    assert box.toolbar.parentWidget() is win.preview_view.viewport()
    assert QApplication.activeModalWidget() is None
    box.editor.setPlainText("申请说明\n仅作测试")
    box.size.setCurrentIndex(box.size.findData(16.0))
    qapp.processEvents()
    QTest.keyClick(box.editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert box.closed and tools._inline_text_box is None
    assert any(edit.kind == "freetext" for edit in win.ws.pages[0].edits)
    win.ws.undo_stack.undo()
    assert win.ws.pages[0] is before


def test_text_box_escape_preserves_the_original_page(reader, qapp):
    win = reader
    win.open_annotation_tools()
    tools = win.annotation_tools
    assert tools.ensure_snapshot()
    before = win.ws.pages[0]
    tools.start_text_box((60, 220, 320, 280), 0, before)
    box = tools._inline_text_box
    box.editor.setPlainText("取消的文字")
    QTest.keyClick(box.editor, Qt.Key.Key_Escape)
    assert tools._inline_text_box is None
    assert win.ws.pages[0] is before


def test_text_box_resize_without_text_change_is_saved_and_undoable(reader, qapp):
    win = reader
    win.open_annotation_tools()
    tools = win.annotation_tools
    assert tools.ensure_snapshot()
    before = win.ws.pages[0]
    tools.start_text_box((60, 220, 240, 270), 0, before)
    box = tools._inline_text_box
    box.editor.setPlainText("调整宽度")
    assert box.apply()
    current = win.ws.pages[0]
    annot = next(a for a in win.store.text_snapshot(current)["annotations"] if a["kind"] == 2)
    old_width = p.Rect(annot["rect"]).width
    tools.start_text_box(annot["rect"], 0, current, annot["content"],
                         annot["text_settings"], annot["id"])
    box = tools._inline_text_box
    handle = box.handles["e"]
    start = handle.rect().center()
    QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(handle, start + QPoint(60, 0))
    QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=start + QPoint(60, 0))
    assert box.base_area.width > old_width
    assert box.apply()
    updated = win.ws.pages[0]
    assert updated.edits[-1].kind == "freetext_update"
    new_annot = next(a for a in win.store.text_snapshot(updated)["annotations"] if a["kind"] == 2)
    assert p.Rect(new_annot["rect"]).width > old_width
    win.ws.undo_stack.undo()
    assert win.ws.pages[0] is current


def test_text_box_resize_tracks_rotated_page(reader):
    win = reader
    win.ws.set_selection({0}, 0)
    win.ws.rotate(90)
    win.refresh()
    win.open_annotation_tools()
    tools = win.annotation_tools
    assert tools.ensure_snapshot()
    ref = win.ws.pages[0]
    tools.start_text_box((60, 220, 240, 270), 0, ref)
    box = tools._inline_text_box
    box.editor.setPlainText("旋转页面")
    assert box.apply()
    current = win.ws.pages[0]
    annot = next(a for a in win.store.text_snapshot(current)["annotations"] if a["kind"] == 2)
    tools.start_text_box(annot["rect"], 0, current, annot["content"],
                         annot["text_settings"], annot["id"])
    box = tools._inline_text_box
    before = (box.base_area * box.matrix).width
    handle = box.handles["e"]
    start = handle.rect().center()
    QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(handle, start + QPoint(40, 0))
    QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=start + QPoint(40, 0))
    assert (box.base_area * box.matrix).width > before
    assert box.apply()


@pytest.mark.parametrize("edge,delta", [("n", -15), ("s", 20)])
def test_resizing_auto_grown_text_box_keeps_the_opposite_edge(reader, qapp, edge, delta):
    reader.open_annotation_tools()
    tools = reader.annotation_tools
    assert tools.ensure_snapshot()
    tools.start_text_box((60, 120, 260, 145), 0, reader.ws.pages[0])
    box = tools._inline_text_box
    box.editor.setPlainText("\n".join(["Line"] * 8))
    qapp.processEvents()
    box.position()
    before = box.area * box.matrix
    assert before.height > (box.base_area * box.matrix).height
    handle = box.handles[edge]
    start = handle.rect().center()
    destination = handle.mapToGlobal(start) + QPoint(0, delta)
    QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(handle, handle.mapFromGlobal(destination))
    QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=handle.mapFromGlobal(destination))
    after = box.area * box.matrix
    scale = reader.preview_view.transform().m11()
    tolerance = 2 / scale
    if edge == "n":
        assert after.y0 == pytest.approx(before.y0 + delta / scale, abs=tolerance)
        assert after.y1 == pytest.approx(before.y1, abs=tolerance)
    else:
        assert after.y0 == pytest.approx(before.y0, abs=tolerance)
        assert after.y1 == pytest.approx(before.y1 + delta / scale, abs=tolerance)
    box.close()


def test_review_snapshot_skips_table_layout_until_needed(reader, monkeypatch):
    from app import documentlayout

    reader.store._clear_edit_caches()
    monkeypatch.setattr(documentlayout, "page_layout", lambda page: (_ for _ in ()).throw(AssertionError("table layout")))
    reader.open_annotation_tools()
    tools = reader.annotation_tools
    tools.snapshot_key = None
    assert tools.ensure_snapshot()
    assert "tables" not in tools.snapshot
    with pytest.raises(AssertionError, match="table layout"):
        reader.store.text_snapshot(reader.ws.pages[0])


def test_note_tool_uses_rendered_annotation_metadata(reader, monkeypatch):
    reader.open_annotation_tools()
    tools = reader.annotation_tools
    tools.snapshot = None
    tools.snapshot_key = None
    monkeypatch.setattr(reader.store, "text_snapshot", lambda *args, **kwargs:
                        (_ for _ in ()).throw(AssertionError("unexpected text parsing")))
    tools.tool.setCurrentIndex(tools.tool.findData("note"))
    assert tools.snapshot is not None and "raw" not in tools.snapshot


def test_cold_review_snapshot_runs_off_gui_thread_without_tables(reader, monkeypatch):
    from threading import get_ident
    reader.open_annotation_tools()
    tools = reader.annotation_tools
    reader.store._clear_edit_caches()
    tools.snapshot = tools.snapshot_key = None
    original = reader.store.text_snapshot
    calls = []
    def tracked(ref, *, include_tables=True):
        calls.append((get_ident(), include_tables))
        return original(ref, include_tables=include_tables)
    monkeypatch.setattr(reader.store, "text_snapshot", tracked)
    assert tools.ensure_snapshot()
    assert calls and all(thread != get_ident() and not tables for thread, tables in calls)
    assert "raw" in tools.snapshot and "tables" not in tools.snapshot


def test_cold_geometry_snapshot_never_extracts_raw_text(reader, monkeypatch):
    from threading import get_ident
    reader.open_annotation_tools()
    tools = reader.annotation_tools
    tools.snapshot = tools.snapshot_key = None
    with reader.store._annotation_lock:
        reader.store._annotation_cache.clear()
    original = reader.store.prepare_annotations
    threads = []
    def tracked(ref):
        threads.append(get_ident())
        return original(ref)
    monkeypatch.setattr(reader.store, "prepare_annotations", tracked)
    monkeypatch.setattr(reader.store, "text_snapshot", lambda *args, **kwargs: pytest.fail("Unexpected raw text parsing"))
    assert tools.ensure_snapshot(geometry_only=True)
    assert threads and all(thread != get_ident() for thread in threads)
    assert "annotations" in tools.snapshot and "raw" not in tools.snapshot


def test_annotation_summary_and_edits_walk_without_reloading_xrefs(monkeypatch):
    from app.pdfedits import annotation_summary, apply_edits
    with p.open() as doc:
        page = doc.new_page()
        page.add_text_annot((40, 70), "Keep first")
        page.add_text_annot((80, 110), "Keep second")
        page.add_rect_annot((120, 140, 160, 180))
        page.insert_link({"kind": p.LINK_URI, "uri": "https://example.invalid/", "from": p.Rect(10, 10, 30, 30)})
        widget = p.Widget()
        widget.field_name, widget.field_type = "KeepField", p.PDF_WIDGET_TYPE_TEXT
        widget.rect = p.Rect(30, 200, 150, 230)
        page.add_widget(widget)
        page = doc.reload_page(page)
        monkeypatch.setattr(p.Page, "load_annot", lambda *args, **kwargs: pytest.fail("Repeated xref lookup"))
        summary = annotation_summary(page)
        assert len(summary) == 3
        assert [item["content"] for item in summary[:2]] == ["Keep first", "Keep second"]
        apply_edits(page, (PageEdit("note", ((180, 220),), text="New note", annotation_id="new-note"),))
        assert len(annotation_summary(page)) == 4
        assert page.first_widget.field_name == "KeepField"


@pytest.mark.parametrize("kind", ["rect", "ellipse", "ink", "arrow"])
def test_escape_cancels_drawing_before_release(reader, kind):
    reader.open_annotation_tools()
    tools, view = reader.annotation_tools, reader.preview_view
    tools.tool.setCurrentIndex(tools.tool.findData(kind))
    before = reader.ws.pages
    origin = view.page_rects[0].topLeft()
    first = view.mapFromScene(origin + QPointF(50, 210))
    last = view.mapFromScene(origin + QPointF(150, 260))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=first)
    QTest.mouseMove(view.viewport(), last)
    try:
        assert tools.stroke
        QTest.keyClick(view, Qt.Key.Key_Escape)
        assert not tools.stroke and tools.overlay is None
    finally:
        tools.clear_overlay()
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=last)
    assert reader.ws.pages is before and not reader.ws.dirty


@pytest.mark.parametrize("cancel", [None, "escape", "page"])
def test_cold_text_selection_preserves_released_gesture_and_discards_stale_results(reader, monkeypatch, cancel):
    from app.rendering import RenderResult
    selection, view = reader.text_selection, reader.preview_view
    ref = reader.ws.pages[0]
    snapshot = reader.store.text_snapshot(ref, include_tables=False)
    box = p.Rect(45, 60, 330, 105) * p.Matrix(snapshot["matrix"])
    origin = view.page_rects[0].topLeft()
    start = view.mapFromScene(origin + QPointF(box.x0, box.y0))
    end = view.mapFromScene(origin + QPointF(box.x1, box.y1))
    requests = []
    original = reader.rendering.request
    def capture(ref, width, key, signals, stale, priority, **kwargs):
        if kwargs.get("kind") == "text":
            requests.append((key, stale))
        else:
            original(ref, width, key, signals, stale, priority, **kwargs)
    monkeypatch.setattr(reader.rendering, "request", capture)
    monkeypatch.setattr(reader.store, "cached_text_snapshot", lambda *args, **kwargs: None)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    # Moving after release must not change the pending selection's end point.
    QTest.mouseMove(view.viewport(), start)
    assert len(requests) == 1 and not selection.text
    key, stale = requests[0]
    if cancel == "escape":
        QTest.keyClick(view, Qt.Key.Key_Escape)
    elif cancel == "page":
        reader.ws.rotate(90)
        reader.refresh()
    selection._snapshot_ready(key, RenderResult(reader.rendering.generation, snapshot))
    if cancel:
        assert stale() and not selection.text and selection.overlay is None
    else:
        assert "系统技术规格" in selection.text
        assert selection.start is None and selection.overlay is not None


def test_manual_watermark_drag_uses_selected_grid(qapp):
    from app.watermarkpreview import PagePreview
    from PyQt6.QtGui import QImage
    preview = PagePreview()
    try:
        preview.resize(500, 650)
        image = QImage(420, 600, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        preview.set_image(image, (420, 600))
        preview.set_asset((image, 200, 72))
        preview.set_options(dict(tile=True, size=20, angle=30, tile_rows=3, tile_columns=4))
        assert len(preview.marks) == 12
        moved = []
        preview.moved.connect(lambda x, y: moved.append((x, y)))
        preview.translate(preview.paper.width() / 4 * .25, preview.paper.height() / 3 * .1)
        assert moved == [pytest.approx((.75, .6))]
    finally:
        preview.close()


def test_small_note_has_screen_sized_hit_area(reader):
    reader.open_annotation_tools()
    tools = reader.annotation_tools
    view = reader.preview_view
    assert tools.ensure_annotation_summary(0)
    assert view.note_markers[0]
    note = next(a for a in tools.snapshot["annotations"] if a["kind"] == 0)
    mapped = p.Rect(note["rect"]) * p.Matrix(tools.snapshot["matrix"])
    from PyQt6.QtGui import QTransform
    view.setTransform(QTransform.fromScale(.2, .2))
    center = (mapped.tl + mapped.br) / 2
    point = view.page_rects[0].topLeft() + QPointF(center.x, center.y) + QPointF(10 / .2, 0)
    assert tools.find_annotation_at(point)["id"] == note["id"]


def test_split_page_action_replaces_one_page_and_undoes(reader, monkeypatch):
    from app.pagesplitui import PageSplitDialog

    monkeypatch.setattr(PageSplitDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    before = tuple(reader.ws.pages)
    reader.stack.setCurrentIndex(1)
    reader.ws.set_selection({0}, 0)
    reader._sync_controls()
    assert reader.split_page_action.isEnabled() and reader.arrange_split_button.isEnabled()
    reader.split_page_action.trigger()
    assert len(reader.ws.pages) == len(before) + 1
    reader.ws.undo_stack.undo()
    assert tuple(reader.ws.pages) == before


def test_split_dialog_resets_cut_when_switching_page_count(reader):
    from PyQt6.QtGui import QImage
    from app.pagesplitui import PageSplitDialog

    image = QImage(300, 200, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    dialog = PageSplitDialog(reader, image)
    try:
        dialog.parts.setCurrentIndex(1)
        dialog.cut2.setValue(40)
        dialog.parts.setCurrentIndex(0)
        assert dialog.cuts == (50,)
    finally:
        dialog.reject()


def test_watermark_preview_discards_old_results(reader, qapp, monkeypatch):
    from app.rendering import RenderResult
    from app.watermarkui import WatermarkDialog
    from PyQt6.QtGui import QImage
    requests = []
    monkeypatch.setattr(reader.rendering, "request", lambda ref, width, key, *args, **kwargs: requests.append(key))
    dialog = WatermarkDialog(reader)
    try:
        assert dialog.color.name() == "#666666"
        assert dialog.size.value() == 50 and dialog.opacity.value() == 15
        assert dialog.angle.value() == 30
        reader._page_images.clear()
        color = (dialog.color.redF(), dialog.color.greenF(), dialog.color.blueF())
        sprite = QImage(200, 72, QImage.Format.Format_ARGB32_Premultiplied)
        sprite.fill(Qt.GlobalColor.transparent)
        asset_key = (dialog.text.text(), color, "")
        dialog._asset_cache[asset_key] = (sprite, 200, 72)
        dialog.preview()
        old = requests[-1]
        image = QImage(100, 150, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        dialog.preview_ready(old, RenderResult(reader.rendering.generation, value=image))
        assert dialog.apply_button.isEnabled()
        assert dialog.preview_label.image is image
        dialog.opacity.setValue(40)
        dialog.angle.setValue(90)
        dialog.preview()
        assert requests == [old]
        assert dialog._asset_job is None
        dialog.move_watermark(.2, .8)
        edit = dialog.edit(reader.ws.pages[0])
        assert dict(edit.details)["offset"] == (.2, .8)
        assert dict(edit.details)["position"] == "custom"
        dialog.kind.setCurrentIndex(dialog.kind.findData("image"))
        assert dialog.angle.value() == 0
        dialog.angle.setValue(-15)
        dialog.kind.setCurrentIndex(dialog.kind.findData("text"))
        assert dialog.angle.value() == 90
        dialog.kind.setCurrentIndex(dialog.kind.findData("image"))
        assert dialog.angle.value() == -15
        dialog.kind.setCurrentIndex(dialog.kind.findData("text"))
        dialog.force_preview()
        current = requests[-1]
        dialog.preview_ready(old, RenderResult(reader.rendering.generation, error="render_failed"))
        assert not dialog.apply_button.isEnabled()
        dialog.preview_ready(current, RenderResult(reader.rendering.generation, value=image))
        assert dialog.apply_button.isEnabled()
    finally:
        dialog.reject()


def test_watermark_selection_edit_delete_and_no_annotation_popup(reader, qapp, monkeypatch):
    from app.watermarkui import WatermarkDialog
    from PyQt6.QtGui import QImage
    first = PageEdit("watermark", text="第一处水印", color=(.4, .4, .4),
                     annotation_id="zhixu-watermark-first",
                     details=(("size", 40), ("opacity", .35), ("angle", 20), ("rotation", 0),
                              ("tile", True), ("tile_rows", 3), ("tile_columns", 4)))
    second = PageEdit("watermark", text="保留另一处", color=(.2, .3, .4),
                      annotation_id="zhixu-watermark-second", details=(("angle", -20),))
    reader.ws.edit_pages({i: ref.edited(first).edited(second) for i, ref in enumerate(reader.ws.pages)}, "添加水印")
    reader.refresh()
    reader.open_annotation_tools()
    tools = reader.annotation_tools
    assert tools.ensure_snapshot(0)
    mark = next(a for a in tools.snapshot["annotations"] if a.get("watermark"))
    assert tools.annotations.findData(mark["id"]) == -1
    tools.hover_bar.show_for_annot(mark, 0)
    assert not tools.hover_bar.isVisible()
    point = p.Rect(mark["rect"]) * p.Matrix(tools.snapshot["matrix"])
    center = (point.tl + point.br) / 2
    top = tools.view.page_rects[0].topLeft()
    assert tools.find_annotation_at(QPointF(top.x() + center.x, top.y() + center.y)) is None
    requests = []
    monkeypatch.setattr(reader.rendering, "request", lambda ref, width, key, *args, **kwargs: requests.append(ref))
    dialog = WatermarkDialog(reader)
    try:
        dialog.timer.stop()
        assert dialog.tabs.count() == 2
        assert dialog.watermark.count() == 3
        assert dialog.selected_watermark_id() == first.annotation_id
        assert dialog.delete_button.isEnabled()
        dialog.watermark.setCurrentRow(1)
        assert dialog.text.text() == first.text
        assert dialog.size.value() == 40 and dialog.opacity.value() == 35 and dialog.angle.value() == 20
        assert dialog.tile.isChecked() and dialog.tile_rows.value() == 3 and dialog.tile_columns.value() == 4
        assert dialog.indices() == [0, 1]
        replacement = dialog.edit(reader.ws.pages[0])
        assert replacement.kind == "watermark_update"
        assert dict(replacement.details)["target"] == first.annotation_id
        assert dict(replacement.details)["tile_rows"] == 3 and dict(replacement.details)["tile_columns"] == 4
        assert replacement.annotation_id != first.annotation_id
        sprite = QImage(200, 72, QImage.Format.Format_ARGB32_Premultiplied)
        sprite.fill(Qt.GlobalColor.transparent)
        color = (dialog.color.redF(), dialog.color.greenF(), dialog.color.blueF())
        dialog._asset_cache[(dialog.text.text(), color, "")] = (sprite, 200, 72)
        dialog.preview()
        assert requests[-1].edits[-1] == PageEdit("watermark_remove", annotation_id=first.annotation_id)
        dialog.range_mode.setCurrentIndex(dialog.range_mode.findData("custom"))
        dialog.range_text.setText("2")
        assert dialog.indices() == [1]
        before = reader.ws.pages
        dialog.apply(remove=True)
        assert reader.ws.pages[0] is before[0]
        assert reader.ws.pages[1].edits[-1] == PageEdit("watermark_remove", annotation_id=first.annotation_id)
        with reader.store._render_page(reader.ws.pages[1]) as page:
            from app.pdfmarks import list_watermarks
            assert [entry["id"] for entry in list_watermarks(page)] == [second.annotation_id]
        reader.ws.undo_stack.undo()
        assert reader.ws.pages == before
    finally:
        if not dialog._closed:
            dialog.reject()


@pytest.mark.parametrize("image", [False, True])
def test_watermark_saved_reopened_selected_and_deleted_through_ui(reader, qapp, tmp_path, monkeypatch, image):
    from pathlib import Path
    from PyQt6.QtCore import QElapsedTimer
    from PyQt6.QtWidgets import QFileDialog
    from app.pdfmarks import is_watermark, list_watermarks
    from app.watermarkui import WatermarkDialog
    original_path = Path(reader.ws.path)
    original_bytes = original_path.read_bytes()
    output = tmp_path / "watermark-roundtrip.pdf"
    addition = WatermarkDialog(reader)
    try:
        addition.show()
        assert addition.selected_watermark_id() is None
        assert addition.delete_button.isHidden()
        identifier = addition._new_identifier
        addition.text.setText("Roundtrip")
        if image:
            from PIL import Image
            picture = tmp_path / "generated-watermark.png"
            Image.new("RGBA", (120, 60), (40, 120, 90, 180)).save(picture)
            monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(picture), "PNG"))
            addition.tabs.setCurrentIndex(1)
            addition.choose_image()
        addition.size.setValue(20)
        addition.angle.setValue(30)
        addition.tile.setChecked(True)
        addition.tile_rows.setValue(3)
        addition.tile_columns.setValue(4)
        timer = QElapsedTimer()
        timer.start()
        while not addition.apply_button.isEnabled() and timer.elapsed() < 15000:
            QTest.qWait(10)
        assert addition.apply_button.isEnabled(), addition.status.text()
        assert len(addition.preview_label.marks) == 12
        QTest.mouseClick(addition.apply_button, Qt.MouseButton.LeftButton)
        assert addition._closed and reader.ws.dirty
    finally:
        if not addition._closed:
            addition.reject()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(output), "PDF"))
    assert reader.save(True)
    reopened = MainWindow()
    try:
        assert reopened._load_one(str(output))
        reopened.resize(1100, 760)
        reopened.show()
        reopened.refresh()
        settle(qapp)
        dialog = WatermarkDialog(reopened)
        try:
            dialog.show()
            assert dialog.watermark.count() == 2
            assert dialog.selected_watermark_id() == identifier
            assert dialog.delete_button.isEnabled()
            assert dialog.watermark.currentRow() == 1
            QTest.mouseClick(dialog.delete_button, Qt.MouseButton.LeftButton)
            assert dialog._closed and reopened.ws.dirty
        finally:
            if not dialog._closed:
                dialog.reject()
        for ref in reopened.ws.pages:
            with reopened.store._render_page(ref) as page:
                assert not list_watermarks(page)
        reopened.ws.undo_stack.undo()
        with reopened.store._render_page(reopened.ws.pages[0]) as page:
            assert list_watermarks(page)[0]["id"] == identifier
        reopened.ws.undo_stack.redo()
        reopened.refresh()
        assert reopened.save()
        with p.open(output) as doc:
            for page in doc:
                assert not any(is_watermark(page, annot) for annot in page.annots() or ())
                assert "系统技术规格" in page.get_text()
                assert any(annot.type[0] == p.PDF_ANNOT_TEXT for annot in page.annots() or ())
        assert original_path.read_bytes() == original_bytes
    finally:
        settle(qapp)
        reopened.ws.reset()
        reopened.store.close_all()
        reopened.close()


def test_note_hover_has_only_edit_move_delete_and_shape_keeps_style(reader):
    reader.open_annotation_tools()
    tools = reader.annotation_tools
    assert tools.ensure_snapshot(geometry_only=True)
    note = next(item for item in tools.snapshot["annotations"] if item["kind"] == 0)
    bar = tools.hover_bar
    bar.show_for_annot(note, 0)
    assert bar.style_btn.isHidden()
    assert all(not button.isHidden() for button in (bar.edit_btn, bar.move_btn, bar.delete_btn))
    assert tools.commit(PageEdit("rect", (50, 210, 180, 250), annotation_id="shape-style"))
    shape = next(item for item in tools.snapshot["annotations"] if item["id"] == "shape-style")
    bar.show_for_annot(shape, 0)
    assert not bar.style_btn.isHidden()


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtGui import QFontDatabase
    from app.systemfonts import installed_font_buffer
    application = QApplication.instance() or QApplication([])
    # The offscreen Qt platform cannot enumerate Windows fonts. Load the real
    # UI/CJK faces into this process so screenshots exercise legible labels.
    for name, flags in (('Microsoft YaHei', 0), ('Arial', 0), ('Arial', 16), ('Arial', 2)):
        buffer = installed_font_buffer(name, flags)
        if buffer:
            QFontDatabase.addApplicationFontFromData(buffer)
    QFontDatabase.addApplicationFontFromData(cjk_font_buffer())
    return application


def settle(qapp):
    for _ in range(60):
        QThreadPool.globalInstance().waitForDone(30000)
        QTest.qWait(5)


@pytest.fixture
def reader(qapp, tmp_path):
    source = tmp_path / "review.pdf"
    with p.open() as doc:
        for index in range(2):
            page = doc.new_page(width=595, height=842)
            page.insert_font(fontname="SourceCJK", fontbuffer=cjk_font_buffer())
            page.insert_text((50, 90), "附件一：系统技术规格", fontsize=24, fontname="SourceCJK", color=(.1, .4, .3))
            page.insert_text((50, 160), "内存推荐：至少 2GB", fontsize=12, fontname="SourceCJK")
            page.add_text_annot((340, 150), f"第 {index + 1} 页备注 <b>原样显示</b>")
        doc.subset_fonts()
        doc.save(source)
    win = MainWindow()
    try:
        win.resize(1100, 760)
        assert win._load_one(str(source))
        win.show()
        win.refresh()
        settle(qapp)
        yield win
    finally:
        settle(qapp)
        win.ws.reset()
        win.store.close_all()
        win.close()
        qapp.processEvents()


def start_edit(win, qapp, y=90):
    win.open_annotation_tools()
    tools = win.annotation_tools
    tools.tool.setCurrentIndex(tools.tool.findData("replace"))
    settle(qapp)
    win._apply_view_zoom()
    pos = win.preview_view.mapFromScene(win.preview_view.page_rects[0].topLeft() + QPointF(60, y - 6))
    QTest.mouseClick(win.preview_view.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    assert tools._inline_replace is not None
    return tools, tools._inline_replace


def test_original_font_menu_is_searchable_and_bounded(reader, qapp, tmp_path):
    from PyQt6.QtWidgets import QComboBox
    tools, state = start_edit(reader, qapp)
    family = state['actions'].findChild(QComboBox, 'replacementFont')
    assert family.isEditable(), 'Font selection should allow searching instead of scrolling a screen of previews'
    family.addItems([f'Example Font {index:03}' for index in range(100)])
    family.showPopup()
    qapp.processEvents()
    assert family.maxVisibleItems() <= 8
    assert family.view().window().height() <= 280
    assert family.view().window().width() <= 360
    reader.grab().save(str(tmp_path / 'font-menu.png'))
    family.view().window().grab().save(str(tmp_path / 'font-popup.png'))
    family.hidePopup()
    assert tools._inline_replace is state and not state['style']


def test_original_font_search_chooses_a_real_family_and_rejects_unknown_text(reader, qapp):
    from PyQt6.QtWidgets import QComboBox
    tools, state = start_edit(reader, qapp)
    family = state['actions'].findChild(QComboBox, 'replacementFont')
    field = family.lineEdit()
    QTest.mouseClick(field, Qt.MouseButton.LeftButton)
    QTest.keyClick(field, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClicks(field, 'ari')
    QTest.qWait(100)
    popup = family.completer().popup()
    assert popup.isVisible() and popup.height() <= 260
    QTest.keyClick(popup, Qt.Key.Key_Down)
    QTest.keyClick(popup, Qt.Key.Key_Return)
    assert state['style'].get('font') == 'Arial'
    QTest.mouseClick(field, Qt.MouseButton.LeftButton)
    QTest.keyClick(field, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClicks(field, 'ZhiXu nonexistent font')
    QTest.mouseClick(state['input'].viewport(), Qt.MouseButton.LeftButton)
    assert family.currentText() == 'Arial' and state['style']['font'] == 'Arial'
    QTest.keyClick(state['input'], Qt.Key.Key_Escape)
    assert tools._inline_replace is None and not reader.ws.pages[0].edits


def test_original_editor_does_not_apply_or_add_newline_during_ime_composition(reader, qapp):
    from PyQt6.QtGui import QInputMethodEvent
    tools, state = start_edit(reader, qapp)
    editor = state['input']
    original = editor.text()
    QTest.keyClick(editor, Qt.Key.Key_End)
    QApplication.sendEvent(editor, QInputMethodEvent('正在输入', []))
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert tools._inline_replace is state and not reader.ws.pages[0].edits
    assert editor.text() == original
    QTest.keyClick(editor, Qt.Key.Key_Escape)


@pytest.mark.parametrize('viewport_width', [None, 280, 220])
def test_original_style_toolbar_is_compact_and_stays_in_view(reader, qapp, tmp_path, viewport_width):
    from PyQt6.QtWidgets import QWidget
    tools, state = start_edit(reader, qapp)
    view = reader.preview_view
    if viewport_width:
        view.setFixedWidth(viewport_width)
        reader._set_zoom(.5)
        view.centerOn(view.page_rects[0].topLeft() + QPointF(*state['area'].tl))
    qapp.processEvents()
    panel = state['actions']
    assert view.viewport().rect().contains(panel.geometry())
    assert not panel.geometry().intersects(state['widget'].geometry())
    assert panel.height() <= (84 if viewport_width else 48)
    aligned = min(max(8, state['widget'].x()), view.viewport().width() - panel.width() - 8)
    assert (abs(panel.x() - aligned) <= 1 or abs(panel.geometry().left() - state['widget'].geometry().right() - 9) <= 1
            or abs(panel.geometry().right() - state['widget'].x() + 9) <= 1)
    for name in ('replacementFont', 'replacementSize', 'replacementBold', 'replacementItalic',
                 'replacementColor', 'replacementReset', 'apply', 'cancel'):
        control = panel.findChild(QWidget, name)
        assert control is not None and control.isVisible()
        assert panel.rect().contains(control.mapTo(panel, control.rect().topLeft()))
        assert panel.rect().contains(control.mapTo(panel, control.rect().bottomRight()))
    reader.grab().save(str(tmp_path / 'compact-editor.png'))


def test_original_editor_stays_usable_at_a_clipped_selection_edge(reader, qapp):
    tools, state = start_edit(reader, qapp)
    view = reader.preview_view
    view.setFixedWidth(280)
    reader._set_zoom(1.5)
    view.centerOn(view.page_rects[0].topLeft() + QPointF(*state['area'].br))
    qapp.processEvents()
    assert view.viewport().rect().contains(state['widget'].geometry())
    assert view.viewport().rect().contains(state['actions'].geometry())
    QTest.keyClick(state['input'], Qt.Key.Key_Home)
    assert state['input'].viewport().rect().contains(state['input'].cursorRect().center())
    QTest.keyClick(state['input'], Qt.Key.Key_Escape)
    assert tools._inline_replace is None


def test_original_editor_previews_mixed_colors_and_keeps_typing_undo(reader, qapp, tmp_path):
    from PyQt6.QtGui import QFontDatabase
    from app.systemfonts import installed_font_buffer
    for flags in (0, 16, 2):
        buffer = installed_font_buffer('Arial', flags)
        assert buffer is not None and QFontDatabase.addApplicationFontFromData(buffer) >= 0
    source = tmp_path / 'mixed-preview.pdf'
    with p.open() as doc:
        page = doc.new_page(width=595, height=400)
        x = 50
        for text, font, size, color in [('Due ', 'helv', 18, (0, 0, 0)),
                                      ('2020', 'hebo', 24, (1, 0, 0)),
                                      (' now', 'heit', 15, (0, 0, 1))]:
            page.insert_text((x, 100), text, fontname=font, fontsize=size, color=color)
            x += p.Font(font).text_length(text, fontsize=size)
        doc.save(source)
    reader.ws.reset()
    assert reader._load_one(str(source))
    reader.refresh()
    tools, state = start_edit(reader, qapp, y=100)
    from PyQt6.QtWidgets import QComboBox
    assert '混合' in state['actions'].findChild(QComboBox, 'replacementFont').lineEdit().placeholderText()
    editor = state['input']
    QTest.keyClick(editor, Qt.Key.Key_End)
    image = editor.grab().toImage()
    colors = [image.pixelColor(x, y) for y in range(image.height()) for x in range(image.width())]
    assert any(c.red() > 170 and c.green() < 80 and c.blue() < 80 for c in colors), 'The red bold run should remain visible while editing'
    assert any(c.blue() > 170 and c.red() < 80 and c.green() < 80 for c in colors), 'The blue italic run should remain visible while editing'
    QTest.keyClicks(editor, '!')
    assert editor.text() == 'Due 2020 now!'
    QTest.keyClick(editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert editor.text() == 'Due 2020 now'
    reader.grab().save(str(tmp_path / 'mixed-preview.png'))


def test_typing_the_first_size_explicitly_unifies_mixed_text_sizes(reader, qapp, tmp_path):
    from PyQt6.QtWidgets import QDoubleSpinBox
    source = tmp_path / 'mixed-sizes.pdf'
    with p.open() as doc:
        page = doc.new_page(width=595, height=400)
        page.insert_text((50, 100), 'A', fontname='helv', fontsize=18)
        page.insert_text((62.006, 100), 'B', fontname='hebo', fontsize=24)
        doc.save(source)
    reader.ws.reset()
    assert reader._load_one(str(source))
    reader.refresh()
    tools, state = start_edit(reader, qapp, y=100)
    size = state['actions'].findChild(QDoubleSpinBox, 'replacementSize')
    assert size.value() == 18
    QTest.mouseClick(size.lineEdit(), Qt.MouseButton.LeftButton)
    QTest.keyClick(size.lineEdit(), Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClicks(size.lineEdit(), '18')
    QTest.keyClick(size.lineEdit(), Qt.Key.Key_Return)
    QTest.mouseClick(state['actions'].findChild(QToolButton, 'apply'), Qt.MouseButton.LeftButton)
    assert tools._inline_replace is None and reader.ws.pages[0].edits
    output = tmp_path / 'uniform-size.pdf'
    reader.store.write(reader.ws.pages, str(output))
    with p.open(output) as doc:
        assert [(t['font'], t['size']) for t in doc[0].get_texttrace()] == [('Helvetica', 18), ('Helvetica-Bold', 18)]


@pytest.mark.parametrize('theme,zoom', [('forest', 1.72), ('graphite', 1.0), ('night', 1.72)])
def test_original_editor_is_legible_at_large_zoom_in_supported_themes(reader, qapp, tmp_path, theme, zoom):
    from app.themes import current_theme, theme_manager
    from PyQt6.QtWidgets import QComboBox
    source = tmp_path / 'mixed-style.pdf'
    with p.open() as doc:
        page = doc.new_page(width=600, height=280)
        page.insert_font(fontname='DemoCJK', fontbuffer=cjk_font_buffer())
        for baseline, segments in [(100, [('项目 ', 'DemoCJK', 18, (0, 0, 0)),
                                         ('2020', 'hebo', 24, (1, 0, 0)),
                                         (' 合计', 'DemoCJK', 16, (0, 0, 1))]),
                                   (170, [('Due ', 'helv', 18, (0, 0, 0)),
                                          ('2020', 'hebo', 24, (1, 0, 0)),
                                          (' now', 'heit', 15, (0, 0, 1))])]:
            x = 60
            for text, font, size, color in segments:
                page.insert_text((x, baseline), text, fontname=font, fontsize=size, color=color)
                face = p.Font('cjk') if font == 'DemoCJK' else p.Font(font)
                x += face.text_length(text, fontsize=size)
        page.insert_text((60, 225), 'KEEP: unchanged text', fontsize=12)
        doc.save(source)
    previous_theme = current_theme().key
    try:
        theme_manager().apply(theme)
        reader.ws.reset()
        assert reader._load_one(str(source))
        reader.refresh()
        tools, state = start_edit(reader, qapp, y=100)
        reader._set_zoom(zoom)
        area = p.Rect(state['area']) * p.Matrix(state['matrix'])
        top = reader.preview_view.page_rects[0].topLeft()
        reader.preview_view.ensureVisible(QRectF(top.x() + area.x0, top.y() + area.y0, area.width, area.height), 80, 80)
        QTest.keyClick(state['input'], Qt.Key.Key_End)
        settle(qapp)
        panel = state['actions']
        assert panel.height() <= 48
        assert reader.preview_view.viewport().rect().contains(panel.geometry())
        assert not panel.geometry().intersects(state['widget'].geometry())
        reader.grab().save(str(tmp_path / f'editor-{theme}.png'))
        reader.preview_view.viewport().grab().save(str(tmp_path / f'paper-{theme}.png'))
        focus = panel.geometry().united(state['widget'].geometry())
        for box in state['text_bounds'][:2]:
            box = p.Rect(box) * p.Matrix(state['matrix'])
            scene = QRectF(top.x() + box.x0, top.y() + box.y0, box.width, box.height)
            focus = focus.united(reader.preview_view.mapFromScene(scene).boundingRect())
        focus = focus.adjusted(-16, -16, 16, 16).intersected(reader.preview_view.viewport().rect())
        reader.preview_view.viewport().grab(focus).save(str(tmp_path / f'focus-{theme}.png'))
        family = panel.findChild(QComboBox, 'replacementFont')
        family.showPopup()
        qapp.processEvents()
        assert family.view().window().height() <= 280
        assert family.view().window().width() <= 360
        reader.grab().save(str(tmp_path / f'menu-{theme}.png'))
        family.view().window().grab().save(str(tmp_path / f'popup-{theme}.png'))
        family.hidePopup()
    finally:
        theme_manager().apply(previous_theme)


def test_inline_original_text_style_controls_preview_and_save(reader, qapp, tmp_path, monkeypatch):
    from PyQt6.QtGui import QColor, QFont, QFontDatabase
    from PyQt6.QtWidgets import QColorDialog, QDoubleSpinBox, QComboBox
    from app.systemfonts import font_face, installed_font_buffer
    # Qt's offscreen platform has no system font list. Register the actual
    # installed face and fixture face in this process for real selector input.
    buffer = installed_font_buffer('Arial')
    assert buffer is not None
    assert QFontDatabase.addApplicationFontFromData(buffer) >= 0
    assert QFontDatabase.addApplicationFontFromData(cjk_font_buffer()) >= 0
    tools, state = start_edit(reader, qapp)
    panel = state['actions']
    family = panel.findChild(QComboBox, 'replacementFont')
    assert family is not None, 'Original text editing needs a system font selector'
    family.setCurrentText('Arial')
    family.showPopup()
    QTest.keyClick(family.view(), Qt.Key.Key_Return)
    panel.findChild(QDoubleSpinBox, 'replacementSize').setValue(20)
    QTest.mouseClick(panel.findChild(QToolButton, 'replacementBold'), Qt.MouseButton.LeftButton)
    QTest.mouseClick(panel.findChild(QToolButton, 'replacementItalic'), Qt.MouseButton.LeftButton)
    monkeypatch.setattr(QColorDialog, 'getColor', lambda *args, **kwargs: QColor('#008000'))
    QTest.mouseClick(panel.findChild(QToolButton, 'replacementColor'), Qt.MouseButton.LeftButton)
    state['input'].setText('Updated 2026')
    assert state['input'].font().bold() and state['input'].font().italic()
    assert state['input'].font().family() == 'Arial'
    assert '#008000' in state['input'].styleSheet()
    reader.grab().save(str(tmp_path / 'original-text-style-controls.png'))
    QTest.mouseClick(panel.findChild(QToolButton, 'apply'), Qt.MouseButton.LeftButton)
    assert tools._inline_replace is None
    output = tmp_path / 'styled-original.pdf'
    reader.store.write(reader.ws.pages, str(output))
    with p.open(output) as doc:
        written = doc[0].get_texttrace()[-1]
        assert ''.join(chr(c[0]) for c in written['chars']).replace('\u00a0', ' ') == 'Updated 2026'
        assert font_face(written['font']) == ('Arial', True, True)
        assert written['size'] == pytest.approx(20)
        assert written['color'] == pytest.approx((0, 128 / 255, 0))


def test_box_selected_existing_text_can_change_style_without_retyping(reader, qapp, tmp_path, monkeypatch):
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import QColorDialog, QDoubleSpinBox
    source, output = tmp_path / 'selection.pdf', tmp_path / 'styled.pdf'
    with p.open() as doc:
        page = doc.new_page(width=595, height=400)
        page.insert_text((50, 90), 'LEFT STYLE RIGHT', fontname='cour', fontsize=24)
        doc.save(source)
        area = p.Rect(next(word[:4] for word in page.get_text('words') if word[4] == 'STYLE'))
        original_neighbors = [(c[0], tuple(c[2])) for t in page.get_texttrace() for c in t['chars']
                              if not area.x0 <= c[2][0] < area.x1 - .1]
    original = source.read_bytes()
    reader.ws.reset()
    assert reader._load_one(str(source))
    reader.refresh()
    reader.open_annotation_tools()
    tools = reader.annotation_tools
    tools.tool.setCurrentIndex(tools.tool.findData('replace'))
    settle(qapp)
    view = reader.preview_view
    top = view.page_rects[0].topLeft()
    start = view.mapFromScene(top + QPointF(area.x0 - 1, area.y0 - 1))
    end = view.mapFromScene(top + QPointF(area.x1 + 1, area.y1 + 1))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    state = tools._inline_replace
    assert state is not None and state['input'].text() == 'STYLE'
    panel = state['actions']
    panel.findChild(QDoubleSpinBox, 'replacementSize').setValue(20)
    QTest.mouseClick(panel.findChild(QToolButton, 'replacementBold'), Qt.MouseButton.LeftButton)
    monkeypatch.setattr(QColorDialog, 'getColor', lambda *a, **k: QColor('blue'))
    QTest.mouseClick(panel.findChild(QToolButton, 'replacementColor'), Qt.MouseButton.LeftButton)
    QTest.mouseClick(panel.findChild(QToolButton, 'apply'), Qt.MouseButton.LeftButton)
    assert tools._inline_replace is None and len(reader.ws.pages[0].edits) == 1
    reader.store.write(reader.ws.pages, str(output))
    with p.open(output) as doc:
        traces = doc[0].get_texttrace()
        styled = traces[-1]
        assert ''.join(chr(c[0]) for c in styled['chars']) == 'STYLE'
        assert (styled['font'], styled['size'], styled['color']) == ('Courier-Bold', 20, (0, 0, 1))
        neighbors = [(c[0], tuple(c[2])) for t in traces[:-1] for c in t['chars']]
        assert [c for c, _ in neighbors] == [c for c, _ in original_neighbors]
        assert all(pos == pytest.approx(old_pos, abs=.001)
                   for (_, pos), (_, old_pos) in zip(neighbors, original_neighbors))
    assert source.read_bytes() == original
    reader.ws.undo_stack.undo()
    reader.refresh()
    assert not reader.ws.pages[0].edits
    reader.ws.undo_stack.redo()
    reader.refresh()
    reader.store.write(reader.ws.pages, str(tmp_path / 'redone.pdf'))
    with p.open(tmp_path / 'redone.pdf') as doc:
        assert doc[0].get_texttrace()[-1]['font'] == 'Courier-Bold'


def test_inline_style_reset_cancel_and_escape_from_controls(reader, qapp):
    from PyQt6.QtWidgets import QDoubleSpinBox
    tools, state = start_edit(reader, qapp)
    panel = state['actions']
    size = panel.findChild(QDoubleSpinBox, 'replacementSize')
    size.setValue(18)
    QTest.mouseClick(panel.findChild(QToolButton, 'replacementReset'), Qt.MouseButton.LeftButton)
    assert size.value() == pytest.approx(state['span']['size'])
    QTest.mouseClick(panel.findChild(QToolButton, 'apply'), Qt.MouseButton.LeftButton)
    assert tools._inline_replace is None and not reader.ws.pages[0].edits
    tools, state = start_edit(reader, qapp)
    size = state['actions'].findChild(QDoubleSpinBox, 'replacementSize')
    size.setValue(18)
    QTest.keyClick(size, Qt.Key.Key_Escape)
    assert tools._inline_replace is None and not reader.ws.pages[0].edits


def test_black_text_color_indicator_is_visible_in_night_theme(reader, qapp, monkeypatch):
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import QColorDialog
    from app.themes import current_theme, theme_manager
    previous = current_theme().key
    try:
        theme_manager().apply('night')
        tools, state = start_edit(reader, qapp)
        button = state['actions'].findChild(QToolButton, 'replacementColor')
        monkeypatch.setattr(QColorDialog, 'getColor', lambda *a, **k: QColor('black'))
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        image = button.grab().toImage()
        colors = [image.pixelColor(x, y) for y in range(image.height() - 9, image.height() - 3)
                  for x in range(6, image.width() - 6)]
        assert any(min(c.red(), c.green(), c.blue()) > 110 for c in colors), 'Dark swatches need a contrasting boundary'
        assert any(max(c.red(), c.green(), c.blue()) < 30 for c in colors), 'The chosen black color must still be shown'
        assert state['style']['color'] == (0, 0, 0)
    finally:
        theme_manager().apply(previous)


@pytest.mark.parametrize('theme', ['forest', 'night'])
def test_original_font_placeholder_has_readable_contrast(reader, qapp, theme):
    from PyQt6.QtGui import QPalette
    from PyQt6.QtWidgets import QComboBox
    from app.themes import current_theme, theme_manager
    previous = current_theme().key
    try:
        theme_manager().apply(theme)
        _, state = start_edit(reader, qapp)
        field = state['actions'].findChild(QComboBox, 'replacementFont').lineEdit()
        palette = field.palette()
        ink = palette.color(QPalette.ColorRole.PlaceholderText)
        # The edit background is transparent; measure the composed toolbar,
        # rather than treating a transparent palette entry as black.
        image = state['actions'].grab().toImage()
        point = field.mapTo(state['actions'], QPoint(0, 0))
        paper = image.pixelColor(round(point.x() * image.devicePixelRatio()), round(point.y() * image.devicePixelRatio()))
        alpha = ink.alphaF()
        def luminance(channels):
            values = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
            return sum(value * weight for value, weight in zip(values, (.2126, .7152, .0722)))
        background = (paper.redF(), paper.greenF(), paper.blueF())
        foreground = tuple(value * alpha + base * (1 - alpha) for value, base in
                           zip((ink.redF(), ink.greenF(), ink.blueF()), background))
        light, dark = sorted((luminance(background), luminance(foreground)), reverse=True)
        assert (light + .05) / (dark + .05) >= 4.5
    finally:
        theme_manager().apply(previous)


def assert_anchored(win, state):
    area = p.Rect(state["area"]) * p.Matrix(state["matrix"])
    top = win.preview_view.page_rects[state["index"]].topLeft()
    center = win.preview_view.mapFromScene(top + QPointF(*area.tl))
    viewport = win.preview_view.viewport()
    assert abs(state['widget'].x() - max(4, min(center.x() - 5, viewport.width() - 36))) <= 1
    end = win.preview_view.mapFromScene(top + QPointF(*area.br))
    target_y = max(4 + state['widget'].height() / 2,
                   min((center.y() + end.y()) / 2, viewport.height() - 4 - state['widget'].height() / 2))
    assert abs(state['widget'].geometry().center().y() - target_y) <= 2


@pytest.mark.parametrize("y", [90, 160])
def test_inline_font_and_anchor_follow_zoom_scroll_resize(reader, qapp, y):
    tools, state = start_edit(reader, qapp, y)
    assert_anchored(reader, state)
    expected = round(state["span"]["size"] * reader.preview_view.transform().m11())
    assert state["input"].font().pixelSize() == max(8, expected)
    assert state["widget"].height() >= state["input"].fontMetrics().height() + 2
    assert state["widget"].height() <= state["input"].font().pixelSize() * 2 + 12
    assert state["actions"].parentWidget() is reader.preview_view.viewport()
    assert not state['actions'].geometry().intersects(state['widget'].geometry())

    reader._set_zoom(2)
    top = reader.preview_view.page_rects[0].topLeft()
    reader.preview_view.centerOn(top + QPointF(*state["area"].br))
    qapp.processEvents()
    assert tools._inline_replace is state
    assert state["widget"].isVisible()
    assert_anchored(reader, state)
    assert state["input"].font().pixelSize() > expected

    bar = reader.preview_view.verticalScrollBar()
    bar.setValue(bar.value() + 35)
    assert_anchored(reader, state)
    reader.resize(1280, 820)
    settle(qapp)
    assert_anchored(reader, state)
    QTest.keyClick(state["input"], Qt.Key.Key_Escape)
    assert tools._inline_replace is None
    assert state["widget"].isHidden() and state["actions"].isHidden()
    assert not reader.ws.pages[0].edits


def test_inline_failed_validation_keeps_input_then_save_and_undo(reader, qapp, tmp_path, monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[-1]) or 0)
    tools, state = start_edit(reader, qapp)
    state["input"].setText("过长文字" * 100)
    QTest.keyClick(state["input"], Qt.Key.Key_Return)
    assert messages
    assert tools._inline_replace is state
    assert state["input"].text() == "过长文字" * 100
    assert not reader.ws.pages[0].edits
    state["input"].setText("附件一：更新技术规格")
    QTest.keyClick(state["input"], Qt.Key.Key_Return)
    assert tools._inline_replace is None
    assert reader.ws.pages[0].edits[-1].kind == "replace"
    target = tmp_path / "edited.pdf"
    reader.store.write(reader.ws.pages, str(target))
    with p.open(target) as doc:
        assert "更新技术规格" in doc[0].get_text()
        assert "系统技术规格" not in doc[0].get_text()
    reader.ws.undo_stack.undo()
    reader.refresh()
    assert not reader.ws.pages[0].edits
    tools, state = start_edit(reader, qapp)
    reader.go_to_page(2)
    assert tools._inline_replace is None
    assert not reader.ws.pages[1].edits


def test_notes_hover_in_reader_and_single_click_does_not_open_dialog(reader, qapp, monkeypatch):
    tools = reader.annotation_tools
    assert tools.isHidden()
    assert reader.preview_view.hasMouseTracking()
    calls = []
    monkeypatch.setattr("app.editingui.NoteViewerDialog.exec", lambda self: calls.append(self) or QDialog.DialogCode.Rejected)
    pos = reader.preview_view.mapFromScene(reader.preview_view.page_rects[0].topLeft() + QPointF(348, 158))
    QTest.mouseMove(reader.preview_view.viewport(), pos)
    qapp.processEvents()
    assert tools.hover_bar.isVisible()
    assert "第 1 页备注" in tools.hover_bar.content_label.text()
    assert tools.hover_bar.content_label.textFormat() == Qt.TextFormat.PlainText
    QTest.mouseClick(reader.preview_view.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    assert not calls
    QTest.mouseDClick(reader.preview_view.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    assert len(calls) == 1
    QTest.mouseMove(reader.preview_view.viewport(), pos)
    QTest.mouseMove(reader.preview_view.viewport(), reader.preview_view.viewport().rect().bottomLeft())
    QTest.qWait(300)
    assert tools.hover_bar.isHidden()

    # Hovering a visible neighbouring page must read and edit that page's note.
    reader._set_zoom(.5)
    page_top = reader.preview_view.page_rects[1].topLeft()
    reader.preview_view.centerOn(page_top + QPointF(348, 158))
    QTest.qWait(160)
    settle(qapp)
    pos = reader.preview_view.mapFromScene(page_top + QPointF(348, 158))
    QTest.mouseMove(reader.preview_view.viewport(), pos)
    assert tools.snapshot_index == 1
    assert "第 2 页备注" in tools.hover_bar.content_label.text()
    tools.delete_annot(tools.hover_bar.annot_id)
    assert not reader.ws.pages[0].edits
    assert reader.ws.pages[1].edits[-1].kind == "delete"


def test_reader_hover_uses_cached_annotations_without_text_or_table_analysis(reader, qapp, monkeypatch):
    tools = reader.annotation_tools
    tools.snapshot = None
    tools.snapshot_key = None
    def unexpected_snapshot(ref):
        raise AssertionError('Hover must not parse a full text snapshot')
    monkeypatch.setattr(reader.store, 'text_snapshot', unexpected_snapshot)
    point = reader.preview_view.page_rects[0].topLeft() + QPointF(348, 158)
    pos = reader.preview_view.mapFromScene(point)
    QTest.mouseMove(reader.preview_view.viewport(), pos)
    qapp.processEvents()
    assert tools.hover_bar.isVisible()
    assert 'raw' not in tools.snapshot
    assert '第 1 页备注' in tools.hover_bar.content_label.text()


def test_explicit_edit_upgrades_annotation_summary_to_full_snapshot(reader):
    tools = reader.annotation_tools
    tools.snapshot_key = None
    assert tools.ensure_annotation_summary(0)
    assert 'raw' not in tools.snapshot
    assert tools.ensure_snapshot(0)
    assert 'raw' in tools.snapshot


def test_review_button_and_toolbar_alignment_at_minimum_window(reader, qapp):
    button = reader.review_button
    assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    assert not button.text() and button.toolTip()
    assert button.x() > reader.mode_switch.geometry().right()
    assert button.x() < reader.split_tool_button.x()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert button.isChecked() and reader.annotation_tools.isVisible()
    tools = reader.annotation_tools
    for size in ((980, 680), (1460, 1000)):
        reader.resize(*size)
        tools.tool.setCurrentIndex(tools.tool.findData("rect"))
        settle(qapp)
        assert tools.bar.x() == 0 and tools.bar.width() == tools.rect().width()
        assert tools.bar.height() <= 48
        controls = [control for control in (*tools.tool_buttons, tools.insert_image_button,
                                           tools.color_button, tools.width) if control.isVisible()]
        centers = [control.mapTo(tools.bar, control.rect().center()).y() for control in controls]
        assert max(centers) - min(centers) <= 1
        assert all(tools.bar.rect().contains(control.mapTo(tools.bar, control.rect().bottomRight()))
                   for control in controls)
        assert all(control.width() >= control.sizeHint().width() for control in tools.tool_buttons
                   if control.isVisible())
    tools.tool.setCurrentIndex(tools.tool.findData("replace"))
    assert tools.color_button.isHidden() and tools.width.isHidden()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert not button.isChecked() and tools.isHidden()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    reader.stack.setCurrentIndex(1)
    assert not button.isChecked() and tools.isHidden()


def test_review_overflow_keeps_hidden_tools_available_and_checked(reader, qapp):
    reader.open_annotation_tools()
    reader.resize(980, 720)
    reader.act_pages.setChecked(True)
    reader.act_tools.setChecked(True)
    tools = reader.annotation_tools
    tools.tool.setCurrentIndex(tools.tool.findData('arrow'))
    settle(qapp)
    more = tools.tool_bar.findChild(QToolButton, 'qt_toolbar_ext_button')
    assert more is not None and more.isVisible()
    assert more.accessibleName() == '更多审阅工具' and not more.icon().isNull()
    assert more.width() >= 24
    assert tools.tool_bar.rect().contains(more.geometry())
    menu = more.menu()
    assert menu is not None
    hidden = [action for action, button in zip(tools.tool_actions, tools.tool_buttons) if not button.isVisible()]
    assert hidden and all(action in menu.actions() for action in hidden)
    index = tools.tool.findData('ellipse')
    action = tools.tool_actions[index]
    assert action in hidden
    action.trigger()
    assert tools.tool.currentData() == 'ellipse'
    assert action.isChecked() and tools.tool_buttons[index].isChecked()
    assert not tools.tool_actions[tools.tool.findData('arrow')].isChecked()
    reader.act_pages.setChecked(False)
    reader.act_tools.setChecked(False)
    reader.resize(1700, 900)
    settle(qapp)
    assert tools.tool_buttons[index].isVisible() and tools.tool_buttons[index].isChecked()


def test_note_drag_preserves_pointer_offset(reader, qapp):
    tools = reader.annotation_tools
    QTest.mouseMove(reader.preview_view.viewport(), QPoint(1, 1))
    pos = reader.preview_view.mapFromScene(reader.preview_view.page_rects[0].topLeft() + QPointF(348, 158))
    QTest.mouseMove(reader.preview_view.viewport(), pos)
    qapp.processEvents()
    bar = tools.hover_bar
    original = bar.annot["rect"]
    scale = reader.preview_view.transform().m11()
    button = bar.move_btn
    center = button.rect().center()
    QTest.mousePress(button, Qt.MouseButton.LeftButton, pos=center)
    QTest.mouseMove(button, center + QPoint(40, 20))
    QTest.mouseRelease(button, Qt.MouseButton.LeftButton, pos=center)
    assert reader.ws.pages[0].edits[-1].kind == "move"
    snapshot = reader.store.text_snapshot(reader.ws.pages[0])
    rect = snapshot["annotations"][0]["rect"]
    assert rect[0] == pytest.approx(original[0] + 40 / scale, abs=1)
    assert rect[1] == pytest.approx(original[1] + 20 / scale, abs=1)
    assert not button.isDown()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_replacement_with_saved_subset_font_remains_visible(reader, qapp, tmp_path, rotation):
    original, updated = "附件一：系统技术规格", "附件一：更新技术规格"
    source = tmp_path / "subset-font.pdf"
    with p.open() as doc:
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 90), original, fontname="china-s", fontsize=24)
        # Simulate a saved earlier correction whose font contains only XYZ.
        page.insert_font(fontname="ZhiXuCJK", fontbuffer=cjk_font_buffer())
        page.insert_text((50, 160), "XYZ", fontname="ZhiXuCJK", fontsize=12)
        doc.subset_fonts()
        page.set_rotation(rotation)
        doc.save(source, garbage=4, deflate=True)
    reader.ws.reset()
    assert reader._load_one(str(source))
    reader.refresh()
    tools = reader.annotation_tools
    assert tools.ensure_snapshot()
    ref = reader.ws.pages[0]
    area = tools.snapshot["raw"]["blocks"][0]["lines"][0]["bbox"]
    edit = reader.store.prepare_replacement(ref, area, updated)
    candidate = ref.edited(edit)
    region = p.Rect(area) * p.Matrix(tools.snapshot["matrix"])
    image, _, _ = reader.store.render_region(candidate, tuple(region), 2)
    assert any(image.pixelColor(x, y).red() < 180
               for y in range(image.height()) for x in range(image.width()))
    if rotation == 0:
        tools, state = start_edit(reader, qapp)
        state["input"].setText(updated)
        QTest.mouseClick(state["actions"].findChild(QToolButton, "apply"), Qt.MouseButton.LeftButton)
        assert tools._inline_replace is None
        assert reader.ws.pages[0].edits[-1].text == updated
        candidate = reader.ws.pages[0]
    target = tmp_path / "corrected.pdf"
    reader.store.write([candidate], str(target))
    with p.open(target) as doc:
        page = doc[0]
        assert updated in page.get_text() and original not in page.get_text()
        assert "XYZ" in page.get_text()
        page.set_rotation(0)
        pix = page.get_pixmap(matrix=p.Matrix(2, 2), clip=p.Rect(area))
        assert any(min(r, g, b) < 180 for r, g, b in zip(
            pix.samples[0::3], pix.samples[1::3], pix.samples[2::3]))
    with p.open(source) as doc:
        assert original in doc[0].get_text()


def test_silent_text_insertion_failure_keeps_original_and_editor(reader, qapp, monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[-1]) or 0)
    tools, state = start_edit(reader, qapp)
    ref = reader.ws.pages[0]
    state["input"].setText("附件一：更新技术规格")
    monkeypatch.setattr(p.Page, "insert_text", lambda *args, **kwargs: 1)
    QTest.mouseClick(state["actions"].findChild(QToolButton, "apply"), Qt.MouseButton.LeftButton)
    assert messages and "原文已保留" in messages[-1]
    assert reader.ws.pages[0] is ref and not ref.edits
    assert tools._inline_replace is state and state["widget"].isVisible()
    assert state["input"].text() == "附件一：更新技术规格"
    assert "系统技术规格" in "".join(char["c"] for block in reader.store.text_snapshot(ref)["raw"]["blocks"]
        for line in block["lines"] for span in line["spans"] for char in span["chars"])


def test_highlight_is_one_even_band_and_partial_selection_stays_in_bounds(reader, tmp_path):
    from app.pdfedits import selection_quads
    ref = reader.ws.pages[0]
    raw = reader.store.text_snapshot(ref)["raw"]
    line = raw["blocks"][0]["lines"][0]
    quads = selection_quads(raw, line["bbox"])
    assert len(quads) == 1
    area = p.Quad(quads[0]).rect
    candidate = ref.edited(PageEdit("highlight", quads, color=(1, .8, 0), annotation_id="band"))
    image, _, _ = reader.store.render_region(candidate, tuple(area), 2)
    margin = round(area.height * 2)
    colors = [image.pixelColor(x, image.height() - 5)
              for x in range(margin, image.width() - margin)]
    assert colors and all(color.blue() < 230 for color in colors)
    assert max(color.green() for color in colors) - min(color.green() for color in colors) <= 2
    chars = line["spans"][0]["chars"]
    selected = p.Rect(chars[3]["bbox"]) | p.Rect(chars[6]["bbox"])
    partial = selection_quads(raw, selected)
    assert len(partial) == 1
    assert p.Quad(partial[0]).rect.x0 == pytest.approx(selected.x0, abs=.01)
    assert p.Quad(partial[0]).rect.x1 == pytest.approx(selected.x1, abs=.01)
    target = tmp_path / "highlight.pdf"
    reader.store.write([candidate], str(target))
    with p.open(target) as doc:
        page = doc[0]
        assert "系统技术规格" in page.get_text()
        annot = next(item for item in page.annots() if item.info["id"] == "band")
        assert annot.type[0] == p.PDF_ANNOT_HIGHLIGHT
        assert len(annot.vertices) == 4
        assert annot.opacity == pytest.approx(.22)


@pytest.mark.parametrize("rotation", [0, 90])
def test_compact_arrow_stays_native_and_style_keeps_tip_size(reader, tmp_path, rotation):
    ref = reader.ws.pages[0].rotated(rotation)
    arrow = PageEdit("arrow", ((80, 390), (250, 390)), color=(.85, .1, .1), width=2, annotation_id="arrow")
    for edits, max_height in (([arrow], 18), ([arrow, PageEdit("style", annotation_id="arrow", color=(0, .4, .8), width=3)], 26)):
        candidate = ref
        for edit in edits:
            candidate = candidate.edited(edit)
        target = tmp_path / f"arrow-{max_height}.pdf"
        reader.store.write([candidate], str(target))
        with p.open(target) as doc:
            page = doc[0]
            page.set_rotation(0)
            annot = next(item for item in page.annots() if item.info["id"] == "arrow")
            assert annot.type[0] == p.PDF_ANNOT_LINE
            assert annot.line_ends == (p.PDF_ANNOT_LE_NONE, p.PDF_ANNOT_LE_CLOSED_ARROW)
            assert annot.colors["fill"] == annot.colors["stroke"]
            pix = page.get_pixmap(matrix=p.Matrix(2, 2), clip=p.Rect(230, 370, 255, 410))
            occupied = [index // pix.width for index, (r, g, b) in enumerate(zip(
                pix.samples[0::3], pix.samples[1::3], pix.samples[2::3])) if min(r, g, b) < 180]
            assert occupied
            assert max(occupied) - min(occupied) + 1 <= max_height


@pytest.mark.parametrize("kind", ["rect", "arrow", "note"])
def test_adding_annotations_keeps_scroll_zoom_and_replaces_image_without_blank(reader, qapp, monkeypatch, kind):
    from PyQt6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *args: ("Test note", True))
    reader.open_annotation_tools()
    tools = reader.annotation_tools
    tools.tool.setCurrentIndex(tools.tool.findData(kind))
    reader._set_zoom(2)
    settle(qapp)
    view = reader.preview_view
    view.centerOn(view.page_rects[0].topLeft() + QPointF(240, 400))
    reader._reading_scrolled()
    before = (view.horizontalScrollBar().value(), view.verticalScrollBar().value(), view.transform().m11())
    calls = []
    resizes = []
    class ResizeObserver(QObject):
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Type.Resize:
                resizes.append((event.oldSize(), event.size()))
            return False
    observer = ResizeObserver(view)
    view.viewport().installEventFilter(observer)
    original_clear = view.clear_photo
    original_jump = view.jump_to_page
    def clear():
        calls.append("clear")
        original_clear()
    def jump(index):
        calls.append("jump")
        original_jump(index)
    monkeypatch.setattr(view, "clear_photo", clear)
    monkeypatch.setattr(view, "jump_to_page", jump)
    top = view.page_rects[0].topLeft()
    start = view.mapFromScene(top + QPointF(120, 380))
    end = view.mapFromScene(top + QPointF(200, 420))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), end)
    if kind == "arrow":
        assert tools.overlay.path().elementCount() > 2
        assert tools.width.value() == 1.5
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert reader.ws.pages[0].edits[-1].kind == kind
    settle(qapp)
    after = (view.horizontalScrollBar().value(), view.verticalScrollBar().value(), view.transform().m11())
    assert after == before
    assert not calls
    assert not resizes
    reader.ws.undo_stack.undo()
    reader.refresh()
    settle(qapp)
    qapp.processEvents()
    assert (view.horizontalScrollBar().value(), view.verticalScrollBar().value(), view.transform().m11()) == before
    assert not resizes


def test_annotation_on_visible_neighbour_page_stays_on_that_page(reader, qapp):
    """连续阅读时上一页露出的部分可以直接落笔，不能被「当前页」抢走。"""
    reader.open_annotation_tools()
    tools = reader.annotation_tools
    tools.tool.setCurrentIndex(tools.tool.findData("rect"))
    reader._set_zoom(.5)
    settle(qapp)
    view = reader.preview_view
    # 把视口挪到两页交界处，让第 1 页下沿与第 2 页上沿同屏
    view.centerOn(view.page_rects[0].center().x(), view.page_rects[0].bottom())
    settle(qapp)
    # 阅读位置会按视口中心自动判定，这里直接把「当前页」钉在第 2 页，模拟
    # 用户看着第 2 页、顺手在第 1 页露出的下沿上画框的情形。
    reader.go_to_page(2, reveal=False)
    qapp.processEvents()
    assert reader.ws.preview_index() == 1
    top = view.page_rects[0].topLeft()
    height = view.page_rects[0].height()
    start = view.mapFromScene(top + QPointF(60, height - 110))
    end = view.mapFromScene(top + QPointF(220, height - 50))
    assert view.viewport().rect().contains(start)
    assert view.viewport().rect().contains(end)
    before_scroll = (view.horizontalScrollBar().value(), view.verticalScrollBar().value(),
                     view.transform().m11())
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    settle(qapp)
    # 落笔不再切换当前页，也就不会跳视图
    assert (view.horizontalScrollBar().value(), view.verticalScrollBar().value(),
            view.transform().m11()) == before_scroll
    rects = [edit for edit in reader.ws.pages[0].edits if edit.kind == "rect"]
    assert len(rects) == 1
    area = p.Rect(rects[0].points)
    assert 0 <= area.x0 < area.x1 <= 595 and 0 <= area.y0 < area.y1 <= 842
    # 落在第 1 页的下半部分，而不是被算到第 2 页
    assert area.y0 > 400
    assert not reader.ws.pages[1].edits
    reader.ws.undo_stack.undo()
    reader.refresh()
    settle(qapp)


def test_visible_neighbour_pages_are_prepared_in_background(reader, qapp, monkeypatch):
    reader.open_annotation_tools()
    tools = reader.annotation_tools
    reader._set_zoom(.5)
    settle(qapp)
    view = reader.preview_view
    view.centerOn(view.page_rects[0].center().x(), view.page_rects[0].bottom())
    settle(qapp)
    visible = view.mapToScene(view.viewport().rect()).boundingRect()
    index = next((i for i, rect in enumerate(view.page_rects)
                  if i != reader.ws.preview_index() and rect.intersects(visible)), None)
    assert index is not None
    ref = reader.ws.pages[index]
    with reader.store._annotation_lock:
        reader.store._annotation_cache.pop(ref.render_key, None)
    assert reader.store.cached_annotations(ref) is None
    threads = []
    prepare = reader.store.prepare_annotations
    def record_thread(page):
        if page is ref:
            threads.append(QThread.currentThread() is qapp.thread())
        return prepare(page)
    monkeypatch.setattr(reader.store, "prepare_annotations", record_thread)
    reader._prepare_visible_page()
    for _ in range(100):
        if reader.store.cached_annotations(ref) is not None:
            break
        QTest.qWait(20)
    assert reader.store.cached_annotations(ref) is not None
    assert threads and not any(threads)


@pytest.mark.parametrize("kind", ["rect", "ink", "mosaic"])
def test_drawing_survives_scroll_to_another_page(reader, qapp, monkeypatch, tmp_path, kind):
    messages, mosaics = [], []
    monkeypatch.setattr(QMessageBox, "information", lambda _, title, text, *a:
                        messages.append(text))
    if kind == "mosaic":
        from app.mosaicui import MosaicDialog
        monkeypatch.setattr(MosaicDialog, "exec", lambda dialog:
            mosaics.append((dialog.index, dialog.ref, dialog.area)) or QDialog.DialogCode.Rejected)
    reader.open_annotation_tools()
    tools = reader.annotation_tools
    tools.tool.setCurrentIndex(tools.tool.findData(kind))
    reader._set_zoom(.5)
    settle(qapp)
    view = reader.preview_view
    view.centerOn(view.page_rects[0].center().x(), view.page_rects[0].bottom() + 60)
    settle(qapp)
    reader.go_to_page(2, reveal=False)
    top, bounds = view.page_rects[0].topLeft(), view.page_rects[0]
    start = view.mapFromScene(top + QPointF(60, bounds.height() - 110))
    end = view.mapFromScene(top + QPointF(220, bounds.height() - 50))
    assert view.viewport().rect().contains(start) and view.viewport().rect().contains(end)
    owner = reader.ws.pages[0]
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), end)
    view.verticalScrollBar().setValue(view.verticalScrollBar().value() + max(300, view.viewport().height()))
    QTest.qWait(250)
    assert reader.ws.preview_index() == 1
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    settle(qapp)
    assert not messages
    assert not reader.ws.pages[1].edits
    if kind == "mosaic":
        assert len(mosaics) == 1 and mosaics[0][0] == 0 and mosaics[0][1] is owner
        assert not reader.ws.pages[0].edits
    else:
        assert reader.ws.pages[0].edits[-1].kind == kind
        reader.ws.undo_stack.undo()
        reader.refresh()
        assert not reader.ws.pages[0].edits
        reader.ws.undo_stack.redo()
        reader.refresh()
        output = tmp_path / "neighbor-edited.pdf"
        reader.store.write(reader.ws.pages, str(output))
        with p.open(output) as saved:
            expected = p.PDF_ANNOT_SQUARE if kind == "rect" else p.PDF_ANNOT_INK
            assert any(annot.type[0] == expected for annot in saved[0].annots())
            assert not any(annot.type[0] == expected for annot in saved[1].annots())


@pytest.mark.parametrize("layout", ["single", "spread", "four"])
@pytest.mark.parametrize("kind", ["highlight", "underline"])
def test_neighbor_text_annotations_keep_their_page(reader, qapp, monkeypatch, layout, kind):
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda _, title, text, *a:
                        messages.append(text))
    reader.resize(1600, 1000)
    reader.act_pages.setChecked(False)
    reader.act_tools.setChecked(False)
    reader._set_reading_layout(layout)
    reader.open_annotation_tools()
    tools = reader.annotation_tools
    tools.tool.setCurrentIndex(tools.tool.findData(kind))
    assert tools.ensure_snapshot(1)
    assert tools.ensure_snapshot(0)
    reader._set_zoom(.5)
    settle(qapp)
    view = reader.preview_view
    top = view.page_rects[1].topLeft()
    view.centerOn(top + QPointF(180, 90))
    settle(qapp)
    reader.go_to_page(1, reveal=False)
    start = view.mapFromScene(top + QPointF(45, 65))
    end = view.mapFromScene(top + QPointF(285, 100))
    assert view.viewport().rect().contains(start) and view.viewport().rect().contains(end)
    before = (view.horizontalScrollBar().value(), view.verticalScrollBar().value(), view.transform().m11())
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    settle(qapp)
    assert not messages and not reader.ws.pages[0].edits
    assert reader.ws.pages[1].edits[-1].kind == kind
    assert before == (view.horizontalScrollBar().value(), view.verticalScrollBar().value(), view.transform().m11())


def test_neighbor_inline_replace_stays_open_and_can_save(reader, qapp, tmp_path, monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda _, title, text, *a: messages.append(text))
    reader.open_annotation_tools()
    tools = reader.annotation_tools
    tools.tool.setCurrentIndex(tools.tool.findData("replace"))
    assert tools.ensure_snapshot(0)
    reader._set_zoom(.5)
    settle(qapp)
    view = reader.preview_view
    view.centerOn(view.page_rects[0].topLeft() + QPointF(160, 90))
    settle(qapp)
    reader.go_to_page(2, reveal=False)
    pos = view.mapFromScene(view.page_rects[0].topLeft() + QPointF(60, 84))
    assert view.viewport().rect().contains(pos)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    state = tools._inline_replace
    assert state is not None and state["index"] == 0 and state["widget"].isVisible()
    state["input"].setText("附件一：更新技术规格")
    assert tools._inline_replace is state
    QTest.keyClick(state["input"], Qt.Key.Key_Return)
    assert not messages
    assert tools._inline_replace is None
    assert reader.ws.pages[0].edits[-1].kind == "replace" and not reader.ws.pages[1].edits
    output = tmp_path / "neighbor-text.pdf"
    reader.store.write(reader.ws.pages, str(output))
    with p.open(output) as saved:
        assert "更新技术规格" in saved[0].get_text()
        assert "系统技术规格" in saved[1].get_text()
    reader.ws.undo_stack.undo()
    reader.refresh()
    assert not reader.ws.pages[0].edits


def test_drawing_is_cancelled_when_its_page_is_replaced(reader, qapp):
    reader.open_annotation_tools()
    tools = reader.annotation_tools
    tools.tool.setCurrentIndex(tools.tool.findData("rect"))
    view = reader.preview_view
    top = view.page_rects[0].topLeft()
    start = view.mapFromScene(top + QPointF(80, 220))
    end = view.mapFromScene(top + QPointF(150, 250))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), end)
    assert tools.stroke
    reader.ws.edit_page(0, PageEdit("note", ((340, 300),), text="other edit", annotation_id="other-edit"))
    reader.refresh()
    assert not tools.stroke and tools.overlay is None
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert [edit.kind for edit in reader.ws.pages[0].edits] == ["note"]
