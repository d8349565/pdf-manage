from __future__ import annotations

import pymupdf
import pytest
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QApplication, QFileDialog

from app.ui import MainWindow
from app.workspace import PageRef, Workspace


def _make_pdf(path, n, label):
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
    yield win
    win.ws.reset()
    win.store.close_all()
    win.close()


def test_workspace_insert_pages_middle(tmp_path):
    ws = Workspace()
    p1 = PageRef("s1", str(tmp_path / "1.pdf"), 0)
    p2 = PageRef("s1", str(tmp_path / "1.pdf"), 1)
    p3 = PageRef("s1", str(tmp_path / "1.pdf"), 2)
    ws.open_replace([p1, p2, p3], str(tmp_path / "1.pdf"))

    inc1 = PageRef("s2", str(tmp_path / "2.pdf"), 0)
    inc2 = PageRef("s2", str(tmp_path / "2.pdf"), 1)

    # Insert after index 1 (at index 2)
    ws.insert_pages([inc1, inc2], at_index=2)
    assert len(ws.pages) == 5
    assert [(p.source_id, p.source_page) for p in ws.pages] == [
        ("s1", 0), ("s1", 1), ("s2", 0), ("s2", 1), ("s1", 2)
    ]

    # Test undo
    assert ws.undo_stack.undoText() == "插入"
    ws.undo_stack.undo()
    assert len(ws.pages) == 3
    assert [(p.source_id, p.source_page) for p in ws.pages] == [
        ("s1", 0), ("s1", 1), ("s1", 2)
    ]

    # Test redo
    ws.undo_stack.redo()
    assert len(ws.pages) == 5
    assert [(p.source_id, p.source_page) for p in ws.pages] == [
        ("s1", 0), ("s1", 1), ("s2", 0), ("s2", 1), ("s1", 2)
    ]


def test_workspace_insert_pages_start_and_end(tmp_path):
    ws = Workspace()
    p1 = PageRef("s1", str(tmp_path / "1.pdf"), 0)
    p2 = PageRef("s1", str(tmp_path / "1.pdf"), 1)
    ws.open_replace([p1, p2], str(tmp_path / "1.pdf"))

    # Insert at beginning
    head = PageRef("s2", str(tmp_path / "2.pdf"), 0)
    ws.insert_pages([head], at_index=0)
    assert [(p.source_id, p.source_page) for p in ws.pages] == [
        ("s2", 0), ("s1", 0), ("s1", 1)
    ]
    assert ws.undo_stack.undoText() == "插入"

    # Append at end
    tail = PageRef("s3", str(tmp_path / "3.pdf"), 0)
    ws.insert_pages([tail], at_index=None)
    assert [(p.source_id, p.source_page) for p in ws.pages] == [
        ("s2", 0), ("s1", 0), ("s1", 1), ("s3", 0)
    ]
    assert ws.undo_stack.undoText() == "合并"


def test_ui_insert_button_and_action_existence(window):
    window.stack.setCurrentIndex(1)
    assert hasattr(window, "insert_button")
    assert window.insert_button.text() == "插入页面"
    assert window.insert_button.toolTip() == "插入 PDF 页面（已选页面后或末尾）"
    assert not window.insert_button.icon().isNull()

    assert hasattr(window, "insert_action")
    assert window.insert_action.text() == "插入页面"


def test_ui_insert_files_after_selection(window, tmp_path, monkeypatch, qapp):
    doc_a = tmp_path / "A.pdf"
    doc_b = tmp_path / "B.pdf"
    _make_pdf(doc_a, 3, "A")
    _make_pdf(doc_b, 2, "B")

    assert window._load_one(str(doc_a))
    assert len(window.ws.pages) == 3

    # Select page index 1 (second page, "A-2")
    window.ws.select_index(1)
    assert window.ws.target_indices() == [1]

    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args, **kwargs: ([str(doc_b)], "PDF (*.pdf)"))

    window.insert_files()

    # Now total pages should be 5
    assert len(window.ws.pages) == 5
    # Order should be A-1, A-2, B-1, B-2, A-3
    sources = [(p.source_path, p.source_page) for p in window.ws.pages]
    assert sources[0] == (str(doc_a), 0)
    assert sources[1] == (str(doc_a), 1)
    assert sources[2] == (str(doc_b), 0)
    assert sources[3] == (str(doc_b), 1)
    assert sources[4] == (str(doc_a), 2)

    # The inserted pages should be selected
    assert window.ws.target_indices() == [2, 3]
    assert "已在第 2 页后插入 2 页" in window.statusBar().currentMessage()


def test_ui_insert_files_no_selection_appends(window, tmp_path, monkeypatch):
    doc_a = tmp_path / "A.pdf"
    doc_b = tmp_path / "B.pdf"
    _make_pdf(doc_a, 2, "A")
    _make_pdf(doc_b, 2, "B")

    assert window._load_one(str(doc_a))
    window.ws.set_selection([])
    assert window.ws.target_indices() == []

    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args, **kwargs: ([str(doc_b)], "PDF (*.pdf)"))

    window.insert_files()

    assert len(window.ws.pages) == 4
    sources = [(p.source_path, p.source_page) for p in window.ws.pages]
    assert sources[0] == (str(doc_a), 0)
    assert sources[1] == (str(doc_a), 1)
    assert sources[2] == (str(doc_b), 0)
    assert sources[3] == (str(doc_b), 1)
    assert "已在末尾插入 2 页" in window.statusBar().currentMessage()


def test_ui_insert_disabled_when_readonly(window, tmp_path, monkeypatch):
    doc_a = tmp_path / "A.pdf"
    _make_pdf(doc_a, 2, "A")
    assert window._load_one(str(doc_a))
    window.stack.setCurrentIndex(1)

    # Simulate read-only reason
    monkeypatch.setattr(window.store, "operation_reason", lambda pages, op: "只读保护" if op == "modify" else None)
    window.refresh()

    assert not window.insert_button.isEnabled()
    assert not window.insert_action.isEnabled()


def test_insert_multiple_files_undo_redo_and_save(window, tmp_path, monkeypatch):
    first, second, third = (tmp_path / name for name in ("A.pdf", "B.pdf", "C.pdf"))
    _make_pdf(first, 3, "A")
    _make_pdf(second, 2, "B")
    _make_pdf(third, 1, "C")
    originals = {path: path.read_bytes() for path in (first, second, third)}
    assert window._load_one(str(first))
    window.ws.set_selection([0, 1], current=1)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([str(second), str(third)], ""))
    window.insert_files()
    expected = ["A-1", "A-2", "B-1", "B-2", "C-1", "A-3"]
    assert window.ws.target_indices() == [2, 3, 4]
    assert all(window.ws.blocks_save_to(str(path)) for path in originals)
    window.ws.undo_stack.undo()
    window.ws.undo_stack.undo()
    assert len(window.ws.pages) == 3 and not window.ws.dirty
    window.ws.undo_stack.redo()
    window.ws.undo_stack.redo()
    target = tmp_path / "combined.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    monkeypatch.setattr(window, "_show_write_notes", lambda results: None)
    assert window.save(save_as=True)
    with pymupdf.open(target) as doc:
        assert [page.get_text().strip() for page in doc] == expected
    assert all(path.read_bytes() == data for path, data in originals.items())
