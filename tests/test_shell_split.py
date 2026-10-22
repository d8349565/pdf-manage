"""Shell entry, split output and normal window shutdown use synthetic PDFs only."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest
from PyQt6.QtCore import QCoreApplication, QEvent, QThreadPool
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app.ui import MainWindow


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    win = MainWindow()
    yield win
    win.ws.reset()
    win.close()
    QThreadPool.globalInstance().waitForDone(10000)
    qapp.processEvents()


def make_pdf(path, count=2, *, permissions=None):
    with pymupdf.open() as doc:
        for index in range(count):
            doc.new_page(width=300, height=400).insert_text((40, 80), f"{path.stem}-{index + 1}")
        options = {} if permissions is None else dict(
            encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="", owner_pw="owner", permissions=permissions)
        doc.save(path, **options)
    return str(path)


def test_shell_entry_loads_and_opens_requested_mode(window, tmp_path):
    first = make_pdf(tmp_path / "一 空格.pdf")
    second = make_pdf(tmp_path / "二.pdf", count=3)
    assert window.open_shell_files([first], "split")
    assert window.ws.path == first
    assert window.stack.currentIndex() == 0
    assert window.export_tools.currentIndex() == 1
    assert window.act_tools.isChecked()
    assert window.open_shell_files([second, first], "merge")
    assert window.stack.currentIndex() == 1
    assert [page.source_path for page in window.ws.pages] == [second] * 3 + [first] * 2
    assert window.ws.path is None
    assert all(window.ws.blocks_save_to(path) for path in (first, second))


def test_shell_controller_opens_independent_windows_and_cleans_up(qapp, tmp_path):
    pytest.importorskip("pythoncom")
    from app.pdfshell import _ShellController
    first = make_pdf(tmp_path / "one.pdf")
    second = make_pdf(tmp_path / "two.pdf", count=3)
    controller = _ShellController(qapp)
    try:
        controller.requested.emit([first, second])
        for _ in range(3):
            qapp.processEvents()
        assert len(controller.windows) == 1
        assert len(controller.windows[0].ws.pages) == 5
        assert not controller.idle_timer.isActive()
        controller.requested.emit([second, first])
        for _ in range(3):
            qapp.processEvents()
        assert len(controller.windows) == 2
        assert controller.windows[0].ws.pages[0].source_path == first
        assert controller.windows[1].ws.pages[0].source_path == second
    finally:
        for win in list(controller.windows):
            win.ws.reset()
            win.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()
        controller.idle_timer.stop()
    assert not controller.windows


@pytest.mark.parametrize("mode, invalid", [("merge", "missing.pdf"), ("split", "notes.txt"), ("unknown", "valid.pdf")])
def test_shell_invalid_request_keeps_open_document(window, tmp_path, monkeypatch, mode, invalid):
    source = make_pdf(tmp_path / "original.pdf")
    assert window._load_one(source)
    before = list(window.ws.pages)
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args[-1]))
    assert not window.open_shell_files([str(tmp_path / invalid)], mode)
    assert window.ws.pages == before and window.ws.path == source
    assert messages


def test_merge_readonly_reports_file_and_preserves_workspace(window, tmp_path, monkeypatch):
    original = make_pdf(tmp_path / "original.pdf")
    incoming = make_pdf(tmp_path / "incoming.pdf")
    protected = make_pdf(tmp_path / "restricted.pdf", permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_COPY)
    assert window._load_one(original)
    before = list(window.ws.pages)
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args[-1]))
    assert not window.open_shell_files([incoming, protected], "merge")
    assert window.ws.pages == before and window.ws.path == original
    assert "restricted.pdf" in messages[0] and "不允许" in messages[0]
    assert "制作可编辑副本" not in messages[0]


@pytest.mark.parametrize("size, expected", [(1, [1, 1, 1, 1, 1, 1]), (2, [2, 2, 2]), (4, [4, 2]), (10, [6])])
def test_split_every_writes_correct_groups_without_changing_source(window, tmp_path, monkeypatch, size, expected):
    source = make_pdf(tmp_path / "source.pdf", count=6)
    original = Path(source).read_bytes()
    assert window._load_one(source)
    window.open_export_tool(1)
    before = list(window.ws.pages)
    window.split_every.setChecked(True)
    window.split_count.setValue(size)
    assert window.split_feedback.text() == f"预计输出 {len(expected)} 个文件"
    output = tmp_path / "output"
    output.mkdir()
    opened_folders = []
    monkeypatch.setattr(window, "_open_output_folder", opened_folders.append)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(output))
    window.split()
    assert opened_folders == [str(output)]
    text = []
    for index, count in enumerate(expected, 1):
        with pymupdf.open(output / f"source_{index}.pdf") as doc:
            assert len(doc) == count
            text.extend(page.get_text().strip() for page in doc)
    assert text == [f"source-{index}" for index in range(1, 7)]
    assert window.ws.pages == before and not window.ws.dirty
    assert Path(source).read_bytes() == original


def test_merge_save_opens_output_folder_once(window, tmp_path, monkeypatch):
    first = make_pdf(tmp_path / "first.pdf")
    second = make_pdf(tmp_path / "second.pdf", count=3)
    target = tmp_path / "merged.pdf"
    opened_folders = []
    monkeypatch.setattr(window, "_open_output_folder", opened_folders.append)
    # Close merge navigation notes in this output-folder behavior check.
    monkeypatch.setattr(window, "_show_write_notes", lambda results: None)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    assert window.open_shell_files([first, second], "merge")
    assert opened_folders == []
    assert window.save()
    assert opened_folders == [str(tmp_path)]
    with pymupdf.open(target) as doc:
        assert len(doc) == 5
    assert window.save()
    assert opened_folders == [str(tmp_path)]


def test_windows_exit_normally_after_loading_and_garbage_collection(tmp_path):
    source = make_pdf(tmp_path / "synthetic.pdf")
    result = subprocess.run([sys.executable, "-c", """
import gc, sys
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QApplication
from app.ui import MainWindow
app = QApplication([])
for _ in range(3):
    win = MainWindow()
    assert win.open_shell_files([sys.argv[1]], 'split')
    QThreadPool.globalInstance().waitForDone(10000)
    app.processEvents()
    win.close()
    del win
    gc.collect()
    app.processEvents()
""", source], cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Traceback" not in result.stderr
