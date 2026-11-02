from __future__ import annotations

import os
import pymupdf
import pytest
from PyQt6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PyQt6.QtGui import QCursor, QDrag, QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog, QFrame

from app.ui import MIME, MainWindow


def _make_pdf(path, n: int, label: str) -> None:
    doc = pymupdf.open()
    for i in range(n):
        page = doc.new_page(width=300, height=400)
        page.insert_text((40, 80), f"{label}-{i + 1}", fontsize=24)
    doc.save(str(path))
    doc.close()


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    yield win
    for tab in list(win._tabs):
        tab.ws.reset()
    win.store.close_all()
    win.close()


def test_tab_bar_hidden_for_single_file_and_shown_for_multiple_files(window, tmp_path, monkeypatch, qapp):
    doc_a = tmp_path / "A.pdf"
    doc_b = tmp_path / "B.pdf"
    _make_pdf(doc_a, 2, "A")
    _make_pdf(doc_b, 3, "B")

    # 0 files open: tab bar hidden
    assert not window.tab_bar.isVisible()
    assert len(window._tabs) == 1

    # Open 1st file: reuses initial empty tab, tab bar stays hidden
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a, **k: ([str(doc_a)], "PDF (*.pdf)"))
    window.open_files()
    qapp.processEvents()
    assert len(window._tabs) == 1
    assert not window.tab_bar.isVisible()
    assert len(window.ws.pages) == 2

    # Open 2nd file: creates 2nd tab, 32px tab bar becomes visible
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a, **k: ([str(doc_b)], "PDF (*.pdf)"))
    window.open_files()
    qapp.processEvents()
    assert len(window._tabs) == 2
    assert window.tab_bar.isVisible()
    assert window.tab_bar.height() == 32
    assert len(window.ws.pages) == 3
    assert os.path.basename(window.ws.path) == "B.pdf"

    # Close current tab (B.pdf): returns to A.pdf and hides tab bar again
    window.close_current_file()
    qapp.processEvents()
    assert len(window._tabs) == 1
    assert not window.tab_bar.isVisible()
    assert len(window.ws.pages) == 2
    assert os.path.basename(window.ws.path) == "A.pdf"


@pytest.mark.parametrize("modified", [False, True])
def test_reopening_file_reuses_tab_and_preserves_edits(window, tmp_path, monkeypatch, qapp, modified):
    doc_a = tmp_path / "A.pdf"
    doc_b = tmp_path / "B.pdf"
    _make_pdf(doc_a, 2, "A")
    _make_pdf(doc_b, 1, "B")
    window.open_files_in_tabs([str(doc_a), str(doc_b)])
    tab_a, tab_b = window._tabs
    window.activate_tab(tab_a.tab_id)
    if modified:
        window.ws.select_index(0)
        window.rotate(90)
    pages = list(tab_a.ws.pages)
    undo_count = tab_a.ws.undo_stack.count()
    window.activate_tab(tab_b.tab_id)

    monkeypatch.setattr(window, "_try_load", lambda *args, **kwargs: pytest.fail(
        "Reopening an existing tab must not reload its PDF"))
    # Reopen both an inactive tab and the already active tab in the same batch.
    assert window.open_files_in_tabs([str(doc_a), str(doc_a)])
    qapp.processEvents()

    assert len(window._tabs) == 2
    assert window._active_tab_id == tab_a.tab_id
    assert window.ws is tab_a.ws
    assert window.ws.pages == pages
    assert window.ws.dirty is modified
    assert window.ws.undo_stack.count() == undo_count
    if modified:
        assert window.undo_action.isEnabled()
        window.undo_action.trigger()
        assert not window.ws.dirty
        assert window.ws.pages[0].rotation == 0


def test_reading_and_arranging_are_shared_across_document_tabs(window, tmp_path, qapp):
    doc_a, doc_b, doc_c = (tmp_path / name for name in ("A.pdf", "B.pdf", "C.pdf"))
    for path in (doc_a, doc_b, doc_c):
        _make_pdf(path, 2, path.stem)
    assert window.open_files_in_tabs([str(doc_a), str(doc_b)])

    window.mode_buttons[1].click()
    window.step_tab(-1)
    qapp.processEvents()
    assert window.arrange_view.isVisible(), "切换文件应保持整理模式"
    assert window.mode_buttons[1].isChecked()

    assert window.open_files_in_tabs([str(doc_c)])
    qapp.processEvents()
    assert window.arrange_view.isVisible(), "打开新文件应沿用窗口的整理模式"
    window.close_current_file()
    qapp.processEvents()
    assert window.arrange_view.isVisible(), "关闭标签后也应保持整理模式"

    window.mode_buttons[0].click()
    window.step_tab(1)
    qapp.processEvents()
    assert window.preview_view.isVisible(), "切换文件应保持阅读模式"
    assert window.mode_buttons[0].isChecked()


def test_per_tab_independent_state_and_undo_group(window, tmp_path, qapp):
    doc_a = tmp_path / "A.pdf"
    doc_b = tmp_path / "B.pdf"
    _make_pdf(doc_a, 2, "A")
    _make_pdf(doc_b, 3, "B")

    window.open_files_in_tabs([str(doc_a), str(doc_b)])
    qapp.processEvents()
    assert len(window._tabs) == 2
    tab_a, tab_b = window._tabs[0], window._tabs[1]
    assert window._active_tab_id == tab_b.tab_id

    # Switch to Tab A, enter Arrange mode, rotate page 0
    window.activate_tab(tab_a.tab_id)
    window.stack.setCurrentIndex(1)
    window.ws.select_index(0)
    window.rotate(90)
    qapp.processEvents()
    assert tab_a.ws.dirty
    assert tab_a.ws.pages[0].rotation == 90
    assert "● A.pdf" in window.tab_bar.pill_for(tab_a.tab_id).title_label.text()

    # Switching files keeps Arrange mode, while edits and undo history stay independent.
    window.step_tab(1)
    qapp.processEvents()
    assert window._active_tab_id == tab_b.tab_id
    assert window.stack.currentIndex() == 1
    assert not window.ws.dirty
    assert not window.undo_action.isEnabled()

    # Switch back to Tab A: undo_action undoes only Tab A's rotation.
    window.step_tab(-1)
    qapp.processEvents()
    assert window._active_tab_id == tab_a.tab_id
    assert window.stack.currentIndex() == 1
    assert window.undo_action.isEnabled()
    window.undo_action.trigger()
    qapp.processEvents()
    assert not tab_a.ws.dirty
    assert tab_a.ws.pages[0].rotation == 0


def test_tabs_keep_document_state_and_share_window_controls(window, tmp_path, qapp):
    doc_a, doc_b = tmp_path / "A.pdf", tmp_path / "B.pdf"
    _make_pdf(doc_a, 4, "A")
    _make_pdf(doc_b, 3, "B")
    assert window.open_files_in_tabs([str(doc_a)])
    window.reading_zoom_actions["width"].trigger()
    window.go_to_page(3)
    window.reader_tool_actions["hand"].trigger()
    window.immersive.auto_action.setChecked(False)
    qapp.processEvents()

    assert window.open_files_in_tabs([str(doc_b)])
    window.go_to_page(2)
    window.rotate(90)
    window.step_tab(-1)
    qapp.processEvents()
    assert window.ws.path == str(doc_a) and len(window.ws.pages) == 4
    assert window.page_number.value() == 3
    assert window.reading_zoom_actions["width"].isChecked()
    assert not window.ws.dirty and not window.undo_action.isEnabled()
    assert window.reader_tool_actions["hand"].isChecked()
    assert window.left_view.isVisible() and not window.immersive.auto_action.isChecked()

    window.step_tab(1)
    qapp.processEvents()
    assert window.ws.path == str(doc_b) and len(window.ws.pages) == 3
    assert window.page_number.value() == 2
    assert window.reading_zoom_actions["page"].isChecked()
    assert window.ws.pages[1].rotation == 90 and window.undo_action.isEnabled()
    assert window.reader_tool_actions["hand"].isChecked()
    assert window.left_view.isVisible() and not window.immersive.auto_action.isChecked()
    window.undo_action.trigger()
    assert not window.ws.dirty and window.ws.pages[1].rotation == 0


def test_new_pdf_fits_page_while_existing_tabs_keep_their_zoom(window, tmp_path, qapp):
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    _make_pdf(first, 2, "First")
    _make_pdf(second, 2, "Second")
    assert window.open_files_in_tabs([str(first)])
    qapp.processEvents()
    assert window._zoom_mode == "page"
    first_tab = window._active_tab_id
    window._set_zoom_mode("width")
    assert window.open_files_in_tabs([str(second)])
    qapp.processEvents()
    assert window._zoom_mode == "page"
    assert window.activate_tab(first_tab)
    assert window._zoom_mode == "width"
    assert window.open_files_in_tabs([str(first)])
    assert window._zoom_mode == "width" and len(window._tabs) == 2


def test_zone_aware_external_pdf_drop(window, tmp_path, qapp):
    doc_a = tmp_path / "A.pdf"
    doc_b = tmp_path / "B.pdf"
    doc_c = tmp_path / "C.pdf"
    _make_pdf(doc_a, 2, "A")
    _make_pdf(doc_b, 2, "B")
    _make_pdf(doc_c, 1, "C")

    window.open_files_in_tabs([str(doc_a)])
    qapp.processEvents()
    assert len(window._tabs) == 1

    # 1. Drop B.pdf onto Reading view / MainWindow -> opens in a new tab
    mime_b = QMimeData()
    mime_b.setUrls([QUrl.fromLocalFile(str(doc_b))])
    drop_on_reader = QDropEvent(
        QPointF(window.preview_view.rect().center()),
        Qt.DropAction.CopyAction,
        mime_b,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.dropEvent(drop_on_reader)
    qapp.processEvents()
    assert len(window._tabs) == 2
    assert os.path.basename(window.ws.path) == "B.pdf"
    assert len(window._tabs[0].ws.pages) == 2
    assert len(window._tabs[1].ws.pages) == 2

    # 2. Drop C.pdf onto Arrange view at index 1 -> inserts into current document (B.pdf)
    window.stack.setCurrentIndex(1)
    qapp.processEvents()
    view = window.arrange_view
    mime_c = QMimeData()
    mime_c.setUrls([QUrl.fromLocalFile(str(doc_c))])
    target_pos = QPointF(view.visualRect(window.model.index(1)).center())
    drop_on_arrange = QDropEvent(
        target_pos,
        Qt.DropAction.CopyAction,
        mime_c,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    view.dropEvent(drop_on_arrange)
    qapp.processEvents()
    assert drop_on_arrange.isAccepted()
    assert len(window._tabs) == 2
    assert len(window.ws.pages) == 3
    assert [os.path.basename(p.source_path) for p in window.ws.pages] == ["B.pdf", "C.pdf", "B.pdf"]

    # Undo restores B.pdf to 2 pages
    window.undo_action.trigger()
    qapp.processEvents()
    assert len(window.ws.pages) == 2


def test_cross_tab_page_drop_onto_pill_and_hover_into_view(window, tmp_path, qapp):
    doc_a = tmp_path / "A.pdf"
    doc_b = tmp_path / "B.pdf"
    _make_pdf(doc_a, 2, "A")
    _make_pdf(doc_b, 2, "B")

    window.open_files_in_tabs([str(doc_a), str(doc_b)])
    qapp.processEvents()
    tab_a, tab_b = window._tabs[0], window._tabs[1]

    # Activate Tab A and start dragging page 0
    window.activate_tab(tab_a.tab_id)
    window.mode_buttons[1].click()
    qapp.processEvents()
    mime = window.model.mimeData([window.model.index(0)])
    assert window.model.drag_source_ws is tab_a.ws

    # 1. Drop directly onto Tab B's pill -> appends page into Tab B and keeps Tab A intact
    pill_b = window.tab_bar.pill_for(tab_b.tab_id)
    drop_pill = QDropEvent(
        QPointF(pill_b.rect().center()),
        Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    pill_b.dropEvent(drop_pill)
    qapp.processEvents()
    assert drop_pill.isAccepted()
    assert window._active_tab_id == tab_b.tab_id
    assert window.arrange_view.isVisible()
    assert len(tab_a.ws.pages) == 2
    assert len(tab_b.ws.pages) == 3
    assert [(os.path.basename(p.source_path), p.source_page) for p in tab_b.ws.pages] == [
        ("B.pdf", 0),
        ("B.pdf", 1),
        ("A.pdf", 0),
    ]

    # Undo on Tab B removes the appended page
    window.undo_action.trigger()
    qapp.processEvents()
    assert len(tab_b.ws.pages) == 2

    # 2. Drag from Tab A, hover over Tab B's pill to auto-switch, then drop into Tab B's arrange_view at index 1
    window.activate_tab(tab_a.tab_id)
    qapp.processEvents()

    # Verify +2 pages preview and drag leave restoration
    mime_two = window.model.mimeData([window.model.index(0), window.model.index(1)])
    enter_two = QDragEnterEvent(
        pill_b.rect().center(),
        Qt.DropAction.MoveAction,
        mime_two,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    pill_b.dragEnterEvent(enter_two)
    assert pill_b.title_label.text() == "+2页到此文件"
    pill_b.dragLeaveEvent(QDragLeaveEvent())
    assert pill_b.title_label.text() == "B.pdf"

    # Drag 1 page onto pill B: shows +1 page, stays visible during hover switch
    mime2 = window.model.mimeData([window.model.index(1)])
    enter_event = QDragEnterEvent(
        pill_b.rect().center(),
        Qt.DropAction.MoveAction,
        mime2,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    pill_b.dragEnterEvent(enter_event)
    assert pill_b.title_label.text() == "+1页到此文件"
    assert pill_b._hover_timer.isActive()
    pill_b._hover_timer.stop()
    pill_b._on_drag_hover_timeout()
    qapp.processEvents()
    assert window._active_tab_id == tab_b.tab_id
    assert pill_b.title_label.text() == "+1页到此文件"

    qapp.processEvents()
    assert window.arrange_view.isVisible(), "悬停切换文件后应继续停留在整理模式"
    view = window.arrange_view
    drop_view = QDropEvent(
        QPointF(view.visualRect(window.model.index(1)).center()),
        Qt.DropAction.MoveAction,
        mime2,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    view.dropEvent(drop_view)
    qapp.processEvents()
    assert drop_view.isAccepted()
    assert len(tab_a.ws.pages) == 2
    assert [(os.path.basename(p.source_path), p.source_page) for p in tab_b.ws.pages] == [
        ("B.pdf", 0),
        ("A.pdf", 1),
        ("B.pdf", 1),
    ]


@pytest.mark.parametrize("ending", ["drop", "cancel"])
@pytest.mark.parametrize("auto_hide", [True, False])
def test_sidebar_stays_open_until_cross_tab_page_drag_finishes(window, tmp_path, monkeypatch, qapp, ending, auto_hide):
    doc_a, doc_b = tmp_path / "A.pdf", tmp_path / "B.pdf"
    _make_pdf(doc_a, 3, "A")
    _make_pdf(doc_b, 2, "B")
    assert window.open_files_in_tabs([str(doc_a), str(doc_b)])
    window.step_tab(-1)
    window.resize(1100, 900)
    control = window.immersive
    if auto_hide:
        control.edge.click()
    else:
        control.auto_action.setChecked(False)
    QTest.qWait(240)
    view = window.left_view
    assert view.isVisible() and control.auto_action.isChecked() == auto_hide
    for row in (0, 1):
        point = view.visualRect(window.model.index(row)).center()
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.ControlModifier if row else Qt.KeyboardModifier.NoModifier, point)
    assert {index.row() for index in view.selectedIndexes()} == {0, 1}
    source_ws = window.ws
    source_pages = list(source_ws.pages)
    away = window.preview_view.mapToGlobal(QPoint(window.preview_view.width() - 10, 10))
    monkeypatch.setattr(QCursor, "pos", lambda: away)

    # Stub the OS drag loop and cursor position; widget events and application code remain real.
    def native_drag(drag, actions):
        QApplication.sendEvent(window.left_panel, QEvent(QEvent.Type.Leave))
        QApplication.sendEvent(window, QEvent(QEvent.Type.WindowDeactivate))
        QTest.qWait(500)
        assert view.isVisible(), "离开侧栏后，拖拽中的页面栏不能收起"

        pill = next(item for item in window.tab_bar.findChildren(QFrame) if item.accessibleName() == "B.pdf")
        enter = QDragEnterEvent(pill.rect().center(), actions, drag.mimeData(),
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(pill, enter)
        assert enter.isAccepted()
        QTest.qWait(460)
        assert window.ws.path == str(doc_b)
        assert view.isVisible(), "悬停切换到目标标签后，页面栏仍须展开"
        assert control.auto_action.isChecked() == auto_hide
        assert control.pin.isChecked() != auto_hide
        if ending == "cancel":
            QApplication.sendEvent(pill, QDragLeaveEvent())
            return Qt.DropAction.IgnoreAction

        position = view.visualRect(window.model.index(1)).center()
        enter = QDragEnterEvent(position, actions, drag.mimeData(),
                               Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(view.viewport(), enter)
        drop = QDropEvent(QPointF(position), actions, drag.mimeData(),
                          Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(view.viewport(), drop)
        assert drop.isAccepted() and drop.dropAction() == Qt.DropAction.CopyAction
        assert view.isVisible(), "落下页面时的刷新也不能提前收起侧栏"
        return drop.dropAction()

    monkeypatch.setattr(QDrag, "exec", native_drag)
    view.startDrag(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction)
    assert source_ws.pages == source_pages
    expected = [("B.pdf", 0), ("A.pdf", 0), ("A.pdf", 1), ("B.pdf", 1)] if ending == "drop" else [("B.pdf", 0), ("B.pdf", 1)]
    assert [(os.path.basename(ref.source_path), ref.source_page) for ref in window.ws.pages] == expected
    assert control.auto_action.isChecked() == auto_hide
    assert control.pin.isChecked() != auto_hide
    QApplication.sendEvent(window.left_panel, QEvent(QEvent.Type.Leave))
    QTest.qWait(700)
    assert view.isVisible() != auto_hide, "放下或取消拖拽后应恢复原来的自动隐藏或固定设置"


def test_page_drag_thumbnail_is_scaled_down(window, tmp_path, monkeypatch, qapp):
    doc_a = tmp_path / "A.pdf"
    _make_pdf(doc_a, 2, "A")
    window.open_files_in_tabs([str(doc_a)])
    qapp.processEvents()
    window.left_view.setCurrentIndex(window.model.index(0))
    window.left_view.selectionModel().select(
        window.model.index(0),
        window.left_view.selectionModel().SelectionFlag.Select,
    )

    dragged_pixmaps = []

    def fake_exec(self, action):
        dragged_pixmaps.append(self.pixmap())
        return action

    monkeypatch.setattr(QDrag, "exec", fake_exec)
    window.left_view.startDrag(Qt.DropAction.MoveAction)
    assert len(dragged_pixmaps) == 1
    assert dragged_pixmaps[0].width() <= 56
    assert dragged_pixmaps[0].height() <= 72
