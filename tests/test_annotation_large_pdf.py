"""Issue #33 workflows on a verified, generated 320-page PDF only."""
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event

import pymupdf as p
import pytest
from PyQt6.QtCore import QPointF, QThreadPool, QTimer, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app.pdfedits import annotation_summary
from app.ui import MainWindow
from app.workspace import PageEdit


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def generated_large_sample(tmp_path_factory):
    supplied = os.environ.get("ZHIXU_ISSUE33_GENERATED_SAMPLE")
    sample = Path(supplied) if supplied else tmp_path_factory.mktemp("generated-large") / "sample.pdf"
    if not supplied:
        generator = Path(__file__).resolve().parents[1] / "packaging" / "benchmark_annotations.py"
        subprocess.run([sys.executable, str(generator), "--generate", "--sample", str(sample)],
                       check=True, capture_output=True, timeout=60)
    manifest = json.loads(sample.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    assert manifest.get("generated") is True and manifest["pages"] == 320
    assert sample.stat().st_size > 40 * 1024 * 1024
    assert sha256(sample.read_bytes()).hexdigest() == manifest["sha256"]
    return sample, manifest["sha256"]


def settle(window):
    for _ in range(3000):
        QTest.qWait(10)
        if (QThreadPool.globalInstance().activeThreadCount() == 0
                and window.rendering.active is None and not window.rendering.pending):
            return
    pytest.fail("Generated PDF reader work did not finish")


@pytest.fixture
def large_reader(qapp, generated_large_sample):
    window = MainWindow()
    try:
        window.resize(1100, 760)
        assert window._load_one(str(generated_large_sample[0]))
        window.show()
        window.refresh()
        settle(window)
        yield window
    finally:
        settle(window)
        window.ws.reset()
        window.store.close_all()
        window.close()


def ids(window, index):
    with window.store._render_page(window.ws.pages[index]) as page:
        return {item["id"] for item in annotation_summary(page)}


@pytest.mark.parametrize("index", [2, 3])
def test_large_pdf_continuous_annotations_duplicate_guard_undo_save(large_reader, generated_large_sample,
                                                                  tmp_path, monkeypatch, index):
    window = large_reader
    window.go_to_page(index + 1)
    window.open_annotation_tools()
    settle(window)
    tools = window.annotation_tools
    original_ids = ids(window, index)
    raw = window.store.text_snapshot(window.ws.pages[index], include_tables=False)["raw"]
    line = next(line for block in raw["blocks"] for line in block.get("lines", ()))
    quad = tuple(tuple(point) for point in p.recover_line_quad(line))
    edits = (PageEdit("note", ((400, 120),), text="Generated review", annotation_id="large-note"),
             PageEdit("rect", (80, 180, 180, 240), annotation_id="large-rectangle"),
             PageEdit("highlight", (quad,), annotation_id="large-highlight"))
    duplicate = []
    QTimer.singleShot(0, lambda: duplicate.append(tools.commit(edits[0])))
    for edit in edits:
        assert tools.commit(edit)
    assert duplicate == [False]
    expected = original_ids | {edit.annotation_id for edit in edits}
    assert ids(window, index) == expected
    for _ in edits:
        window.ws.undo_stack.undo()
    assert ids(window, index) == original_ids
    for _ in edits:
        window.ws.undo_stack.redo()
    assert ids(window, index) == expected
    window.refresh()
    output = tmp_path / "generated-reviewed.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(output), "PDF"))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    assert window.save(True)
    assert not warnings and not window.ws.dirty
    with p.open(output) as reopened:
        assert len(reopened) == 320
        assert {item["id"] for item in annotation_summary(reopened[index])} == expected
        untouched = 3 if index == 2 else 2
        assert len(annotation_summary(reopened[untouched])) == (1200 if untouched == 3 else 0)
        assert ("R000" if index == 2 else "Generated page 4") in reopened[index].get_text()
    assert sha256(generated_large_sample[0].read_bytes()).hexdigest() == generated_large_sample[1]


@pytest.mark.parametrize("cancel", ["escape", "page"])
def test_large_pdf_pending_selection_discards_real_worker_result(large_reader, monkeypatch, cancel):
    window, gate = large_reader, Event()
    window.go_to_page(3)
    settle(window)
    selection, view = window.text_selection, window.preview_view
    window.store._clear_edit_caches()
    original = window.store.text_snapshot
    def delayed(ref, *, include_tables=True):
        assert gate.wait(10), "Selection worker was not released"
        return original(ref, include_tables=include_tables)
    monkeypatch.setattr(window.store, "text_snapshot", delayed)
    origin = view.page_rects[2].topLeft()
    first = view.mapFromScene(origin + QPointF(35, 35))
    last = view.mapFromScene(origin + QPointF(370, 120))
    try:
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=first)
        QTest.mouseMove(view.viewport(), last)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=last)
        assert selection._pending is not None
        if cancel == "escape":
            QTest.keyClick(view, Qt.Key.Key_Escape)
        else:
            window.go_to_page(1)
    finally:
        gate.set()
    settle(window)
    assert not selection.text and selection.overlay is None
    assert selection._pending is None
    assert window.ws.preview_index() == (2 if cancel == "escape" else 0)


def test_large_pdf_cold_snapshot_rejects_changed_revision(large_reader):
    window = large_reader
    window.go_to_page(3)
    window.open_annotation_tools()
    settle(window)
    tools = window.annotation_tools
    tools.snapshot = tools.snapshot_key = None
    window.store._clear_edit_caches()
    previous = window.ws.pages[2]
    QTimer.singleShot(0, lambda: window.ws.rotate(90))
    assert not tools.ensure_snapshot()
    assert window.ws.pages[2] is not previous
    assert tools.snapshot_key is None and tools.snapshot is None


def test_large_pdf_borderless_table_selection_and_copy(qapp, generated_large_sample, tmp_path):
    source = tmp_path / "generated-borderless-large.pdf"
    expected = []
    with p.open(generated_large_sample[0]) as document:
        page = document[5]
        assert not page.get_drawings() and not page.get_images()
        for row in range(4):
            values = []
            for column in range(3):
                value = (("Item", "Qty", "Amount")[column] if row == 0 else
                         f"Item {row}" if column == 0 else
                         str(row * 37) if column == 1 else f"{row * 1234.56:.2f}")
                page.insert_text((48 + column * 105, 204 + row * 50), value, fontsize=10)
                values.append(value)
            expected.append(values)
        document.save(source)
    window = MainWindow()
    try:
        window.resize(1100, 760)
        assert window._load_one(str(source))
        window.show()
        window.refresh()
        settle(window)
        window.go_to_page(6)
        QTest.qWait(200)
        settle(window)
        view, selection = window.preview_view, window.text_selection
        origin = view.page_rects[5].topLeft()
        first = view.mapFromScene(origin + QPointF(40, 180))
        last = view.mapFromScene(origin + QPointF(365, 380))
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=first)
        QTest.mouseMove(view.viewport(), last)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=last)
        settle(window)
        assert selection.text
        selection.copy()
        assert selection.snapshot["tables"]
        copied = QApplication.clipboard().text()
        for row in expected:
            assert "\t".join(row) in copied
        assert not window.ws.dirty
    finally:
        settle(window)
        window.ws.reset()
        window.store.close_all()
        window.close()
