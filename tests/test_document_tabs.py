from __future__ import annotations

import os
import pymupdf
import pytest
from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl
from PyQt6.QtGui import QDrag, QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PyQt6.QtWidgets import QApplication, QFileDialog

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

    # Switch to Tab B via step_tab: Tab B is in Reading mode, clean, and has empty undo stack
    window.step_tab(1)
    qapp.processEvents()
    assert window._active_tab_id == tab_b.tab_id
    assert window.stack.currentIndex() == 0
    assert not window.ws.dirty
    assert not window.undo_action.isEnabled()

    # Switch back to Tab A: Arrange mode restored, undo_action enabled and undoes Tab A's rotation
    window.step_tab(-1)
    qapp.processEvents()
    assert window._active_tab_id == tab_a.tab_id
    assert window.stack.currentIndex() == 1
    assert window.undo_action.isEnabled()
    window.undo_action.trigger()
    qapp.processEvents()
    assert not tab_a.ws.dirty
    assert tab_a.ws.pages[0].rotation == 0


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

    window.stack.setCurrentIndex(1)
    qapp.processEvents()
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
