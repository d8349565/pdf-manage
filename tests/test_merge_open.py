"""Merge opened workspaces through the real writer and keep source tabs intact."""
from dataclasses import replace

import pymupdf
import pytest
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app.mergeopenui import MergeOpenDialog
from app.output import OperationCancelled
from app.pdfio import PdfError
from app.ui import MainWindow
from app.themes import ui_color
from app.workspace import OutlineEntry


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def opened(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: QMessageBox.StandardButton.Ok)
    paths = []
    for name, count in (('A', 2), ('B', 1), ('C', 1)):
        path = tmp_path / f'{name}.pdf'
        with pymupdf.open() as doc:
            for number in range(count):
                page = doc.new_page(width=240, height=320)
                page.insert_text((30, 60), f'{name}-{number + 1}')
            doc.set_toc([[1, f'{name} chapter', 1]])
            doc.save(path)
        paths.append(path)
    window = MainWindow()
    window.resize(1300, 850)
    window.show()
    assert window.open_files_in_tabs([str(path) for path in paths])
    qapp.processEvents()
    yield window, paths
    for tab in window._tabs:
        tab.ws.reset()
    window.close()
    qapp.processEvents()


def interact_with_dialog(monkeypatch, interaction):
    real_exec = MergeOpenDialog.exec
    def execute(dialog):
        QTimer.singleShot(0, lambda: interaction(dialog))
        return real_exec(dialog)
    monkeypatch.setattr(MergeOpenDialog, 'exec', execute)


def test_merge_uses_selected_order_current_edits_and_current_bookmarks(opened, tmp_path, monkeypatch):
    window, paths = opened
    tabs = list(window._tabs)
    first = tabs[0].ws
    first.select_index(0)
    first.rotate(90)
    first.move_page(0, 1)
    first.set_outline((OutlineEntry(1, 'Edited A bookmark', first.pages[1].page_id),))
    before = [tab.ws._fingerprint() for tab in tabs]
    history = [tab.ws.undo_stack.count() for tab in tabs]
    originals = [path.read_bytes() for path in paths]
    output = tmp_path / 'merged.pdf'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(output), 'PDF'))
    monkeypatch.setattr(QFileDialog, 'getOpenFileNames', lambda *args, **kwargs: pytest.fail('Must use opened workspaces'))
    def choose(dialog):
        assert dialog.selected_ids() == [tab.tab_id for tab in tabs]
        assert '未保存修改' in dialog.files.item(0).text()
        dialog.files.item(1).setCheckState(Qt.CheckState.Unchecked)
        dialog.files.setCurrentRow(2)
        dialog.move_up.click()
        dialog.move_up.click()
        assert dialog.selected_ids() == [tabs[2].tab_id, tabs[0].tab_id]
        dialog.start.click()
    interact_with_dialog(monkeypatch, choose)
    assert window.merge_open_files()
    with pymupdf.open(output) as doc:
        assert [page.get_text().strip() for page in doc] == ['C-1', 'A-2', 'A-1']
        assert doc[2].rotation == 90
        toc = doc.get_toc()
        assert [row[1] for row in toc] == ['C', 'C chapter', 'A', 'Edited A bookmark']
        assert toc[-1][2] == 3
    assert window._tabs[:3] == tabs
    assert len(window._tabs) == 4
    assert window.ws.path == str(output)
    assert [tab.ws._fingerprint() for tab in tabs] == before
    assert [tab.ws.undo_stack.count() for tab in tabs] == history
    assert [path.read_bytes() for path in paths] == originals
    assert first.dirty


def test_save_cancel_returns_to_selection_and_leaves_tabs_unchanged(opened, monkeypatch):
    window, paths = opened
    before = [tab.ws._fingerprint() for tab in window._tabs]
    active = window._active_tab_id
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: ('', ''))
    def cancel(dialog):
        dialog.start.click()
        assert dialog.isVisible()
        assert dialog.output_path is None
        dialog.select_all_button.click()
        assert not dialog.start.isEnabled()
        assert '至少选择 2 份' in dialog.summary.text()
        dialog.reject()
    interact_with_dialog(monkeypatch, cancel)
    assert not window.merge_open_files()
    assert window._active_tab_id == active
    assert [tab.ws._fingerprint() for tab in window._tabs] == before
    assert not window._saving


def test_cannot_overwrite_even_an_unselected_source(opened, monkeypatch):
    window, paths = opened
    original = paths[1].read_bytes()
    warnings = []
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(paths[1]), 'PDF'))
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
    def choose(dialog):
        dialog.files.item(1).setCheckState(Qt.CheckState.Unchecked)
        dialog.start.click()
        assert dialog.output_path is None
        assert dialog.isVisible()
        dialog.reject()
    interact_with_dialog(monkeypatch, choose)
    assert not window.merge_open_files()
    assert warnings and '不能覆盖' in warnings[0]
    assert paths[1].read_bytes() == original


@pytest.mark.parametrize('error', [OperationCancelled('已取消'), PdfError('目标写入失败')])
def test_writer_cancel_or_failure_preserves_workspaces_and_has_no_output(opened, tmp_path, monkeypatch, error):
    import app.pdfworker
    window, paths = opened
    before = [tab.ws._fingerprint() for tab in window._tabs]
    originals = [path.read_bytes() for path in paths]
    output = tmp_path / 'not-written.pdf'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(output), 'PDF'))
    def stop(*args, **kwargs):
        raise error
    monkeypatch.setattr(app.pdfworker, 'write_pdf', stop)
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
    interact_with_dialog(monkeypatch, lambda dialog: dialog.start.click())
    assert not window.merge_open_files()
    assert [tab.ws._fingerprint() for tab in window._tabs] == before
    assert [path.read_bytes() for path in paths] == originals
    assert len(window._tabs) == 3
    assert not output.exists()
    assert not window._saving
    assert bool(warnings) == isinstance(error, PdfError)


def test_protected_sources_are_visible_with_reasons_and_not_selected(opened, monkeypatch):
    window, paths = opened
    window._finish_document_checks([page for tab in window._tabs for page in tab.ws.pages])
    ref = window._tabs[1].ws.pages[0]
    source = window.store._source(ref.source_id)
    source.safety = replace(source.safety, signature='present')
    def inspect(dialog):
        item = dialog.files.item(1)
        assert 'B.pdf' in item.text()
        assert not item.flags() & Qt.ItemFlag.ItemIsEnabled
        assert item.checkState() == Qt.CheckState.Unchecked
        assert dialog.sources[window._tabs[1].tab_id]['reason']
        dialog.select_all(True)
        assert window._tabs[1].tab_id not in dialog.selected_ids()
        dialog.reject()
    interact_with_dialog(monkeypatch, inspect)
    assert not window.merge_open_files()


def test_merge_entry_requires_two_pdf_tabs_and_explains_image_source(opened, tmp_path, monkeypatch):
    window, paths = opened
    assert window.tab_bar.merge_button.isVisible() and window.merge_open_action.isEnabled()
    assert window.close_tab(window._tabs[-1].tab_id)
    image_path = tmp_path / 'generated.png'
    image = QImage(80, 100, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    assert image.save(str(image_path))
    assert window.open_files_in_tabs([str(image_path)])
    def inspect(dialog):
        image_row = dialog.files.item(dialog.files.count() - 1)
        assert not image_row.flags() & Qt.ItemFlag.ItemIsEnabled
        assert '先另存为 PDF' in image_row.text()
        assert len(dialog.selected_ids()) == 2
        dialog.reject()
    interact_with_dialog(monkeypatch, inspect)
    assert not window.merge_open_files()
    assert window.close_tab(window._tabs[1].tab_id)
    assert not window.tab_bar.merge_button.isVisible()
    assert not window.merge_open_action.isEnabled()


def test_file_name_and_metadata_both_render_on_separate_lines(opened, qapp):
    window, paths = opened
    sources = [dict(tab_id=index, name='Visible filename.pdf', path=str(path), pages=7,
                    dirty=True, reason='') for index, path in enumerate(paths)]
    dialog = MergeOpenDialog(window, sources)
    try:
        dialog.show()
        qapp.processEvents()
        image = dialog.files.viewport().grab().toImage()
        row = dialog.files.visualItemRect(dialog.files.item(0))
        ratio = image.devicePixelRatio()
        muted = ui_color('#4D6B67').lower()
        assert any(image.pixelColor(x, y).name() == muted
                   for y in range(round((row.top() + 30) * ratio), round((row.top() + 50) * ratio))
                   for x in range(round(75 * ratio), round((row.right() - 10) * ratio)))
    finally:
        dialog.close()
