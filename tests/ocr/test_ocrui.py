"""OCR 界面与交互行为测试。"""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from app.ocrui import OCRDialog, parse_page_range
from app.ui import MainWindow


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def test_parse_page_range():
    assert parse_page_range("1-3, 5, 8", total_pages=10) == [0, 1, 2, 4, 7]
    assert parse_page_range("4-2", total_pages=5) == [1, 2, 3]  # 反向自动校正
    assert parse_page_range("100", total_pages=5) == []  # 超出范围过滤
    assert parse_page_range("abc, 1, def", total_pages=5) == [0]
    assert parse_page_range("", total_pages=5) == []


def test_ocr_dialog_initial_state(qapp, tmp_path):
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    dlg = OCRDialog(None, str(pdf), total_pages=5, current_page_index=2)
    assert dlg.radio_all.isChecked()
    assert dlg.check_skip_existing.isChecked()
    assert "test_OCR.pdf" in dlg.edit_dest.text()
    assert not dlg.edit_range.isEnabled()

    # 切换到指定页码
    dlg.radio_custom.setChecked(True)
    assert dlg.edit_range.isEnabled()
    dlg.deleteLater()


def test_main_window_ocr_blocks_empty_and_dirty(monkeypatch):
    class MockWorkspace:
        pages = ()
        path = None
        dirty = False
        def preview_index(self): return 0

    class DummyWindow:
        ws = MockWorkspace()
        open_ocr_dialog = MainWindow.open_ocr_dialog

    win = DummyWindow()
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[2]))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args[2]))

    # 1. 空工作区时拦截
    win.open_ocr_dialog()
    assert len(messages) == 1
    assert "打开" in messages[0]

    # 2. 模拟脏工作区拦截
    win.ws.path = "doc.pdf"
    win.ws.pages = ("page1",)
    win.ws.dirty = True
    win.open_ocr_dialog()
    assert len(messages) == 2
    assert "未保存" in messages[1]
