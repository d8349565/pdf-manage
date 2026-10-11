"""Review interactions use generated PDFs, never the user's documents."""
import pymupdf as p
import pytest
from PyQt6.QtCore import QEvent, QObject, QPoint, QPointF, QThreadPool, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QToolButton

from app.pdfedits import cjk_font_buffer
from app.ui import MainWindow
from app.workspace import PageEdit


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def settle(qapp):
    QThreadPool.globalInstance().waitForDone(30000)
    qapp.processEvents()


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


def assert_anchored(win, state):
    area = p.Rect(state["area"]) * p.Matrix(state["matrix"])
    top = win.preview_view.page_rects[state["index"]].topLeft()
    center = win.preview_view.mapFromScene(top + QPointF(*area.tl))
    assert abs(state["widget"].x() - (center.x() - 5)) <= 1
    end = win.preview_view.mapFromScene(top + QPointF(*area.br))
    assert abs(state["widget"].geometry().center().y() - (center.y() + end.y()) / 2) <= 2


@pytest.mark.parametrize("y", [90, 160])
def test_inline_font_and_anchor_follow_zoom_scroll_resize(reader, qapp, y):
    tools, state = start_edit(reader, qapp, y)
    assert_anchored(reader, state)
    expected = round(state["span"]["size"] * reader.preview_view.transform().m11())
    assert state["input"].font().pixelSize() == max(8, expected)
    assert state["widget"].height() >= state["input"].fontMetrics().height() + 2
    assert state["widget"].height() < state["input"].font().pixelSize() * 2 + 12
    assert state["actions"].parentWidget() is reader.preview_view.viewport()
    assert state["actions"].geometry().top() > state["widget"].geometry().bottom()

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
    pos = reader.preview_view.mapFromScene(page_top + QPointF(348, 158))
    QTest.mouseMove(reader.preview_view.viewport(), pos)
    assert tools.snapshot_index == 1
    assert "第 2 页备注" in tools.hover_bar.content_label.text()
    tools.delete_annot(tools.hover_bar.annot_id)
    assert not reader.ws.pages[0].edits
    assert reader.ws.pages[1].edits[-1].kind == "delete"


def test_review_button_and_toolbar_alignment_at_minimum_window(reader, qapp):
    button = reader.review_button
    assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    assert not button.text() and button.toolTip()
    assert button.x() >= reader.mode_buttons[1].geometry().right()
    assert button.x() < reader.split_tool_button.x()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert button.isChecked() and reader.annotation_tools.isVisible()
    tools = reader.annotation_tools
    for size in ((980, 680), (1460, 1000)):
        reader.resize(*size)
        tools.tool.setCurrentIndex(tools.tool.findData("rect"))
        settle(qapp)
        assert tools.bar.x() == 0 and tools.bar.width() == tools.rect().width()
        controls = [*tools.tool_buttons, tools.color_button, tools.width]
        centers = [control.geometry().center().y() for control in controls]
        assert max(centers) - min(centers) <= 1
        assert all(control.geometry().right() < tools.bar.width() for control in controls)
    tools.tool.setCurrentIndex(tools.tool.findData("replace"))
    assert tools.color_button.isHidden() and tools.width.isHidden()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert not button.isChecked() and tools.isHidden()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    reader.stack.setCurrentIndex(1)
    assert not button.isChecked() and tools.isHidden()


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
