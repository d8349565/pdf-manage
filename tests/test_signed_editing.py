"""Verify signed copies through the real writer and the user-facing operations."""
import hashlib
import os
from pathlib import Path
import shutil

import pymupdf as p
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox, QToolButton

from app.pdfio import PdfError, PdfStore, WriteOptions
from app.pdfstructure import signature_state
from app.pdfworker import write_pdf
from app.ui import MainWindow
from app.workspace import PageEdit
from test_issues_9_11 import make_pdf
from test_merge_open import interact_with_dialog


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    host = MainWindow()
    host._window_settings.setValue('onboarding/contributionsV1Seen', True)
    host._window_settings.setValue('onboarding/quickStartSeen', True)
    host._window_settings.setValue('saving/default_mode', 'overwrite')
    host.show()
    yield host
    for tab in host._tabs:
        tab.ws.reset()
    host.close()
    host.deleteLater()
    qapp.processEvents()


@pytest.fixture(params=['generated', 'authorized_sample'])
def signed_source(request, tmp_path):
    if request.param == 'authorized_sample':
        value = os.environ.get('ZHIXU_SIGNED_PDF_SAMPLE')
        if not value:
            pytest.skip('Supply an authorized signed PDF to exercise the actual report')
        source = Path(value).resolve()
        root = Path(__file__).resolve().parents[1]
        assert not source.is_relative_to(root) and not tmp_path.resolve().is_relative_to(root)
    else:
        source = tmp_path / 'signed.pdf'
        make_pdf(source, signature='present')
    digest = hashlib.sha256(source.read_bytes()).digest()
    yield source
    assert hashlib.sha256(source.read_bytes()).digest() == digest


def page_fingerprint(page):
    return (hashlib.sha256(page.get_pixmap(dpi=120).samples).digest(),
            hashlib.sha256(page.get_text().encode()).digest())


@pytest.mark.parametrize('entry', ['tabs', 'file_picker', 'shell'])
def test_signatures_merge_directly_to_a_new_copy(window, signed_source, tmp_path, monkeypatch, entry):
    second = tmp_path / 'second.pdf'
    shutil.copyfile(signed_source, second)
    target = tmp_path / 'merged.pdf'
    paths = [str(signed_source), str(second)]
    requested = []

    def choose_path(parent, title, suggested, *args, **kwargs):
        requested.append(title)
        return ('' if len(requested) == 1 else str(target), 'PDF')

    monkeypatch.setattr(QFileDialog, 'getSaveFileName', choose_path)
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: pytest.fail('Unexpected completion popup'))
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: pytest.fail('Unexpected merge warning'))
    monkeypatch.setattr(QMessageBox, 'exec', lambda box: pytest.fail('Unexpected merge modal'))

    def merge(dialog):
        assert len(dialog.selected_ids()) == 2
        assert dialog.start.isEnabled() and dialog.start.text() == '合并为副本'
        assert dialog.summary.text().count('保留原文件，合并件不含原数字签名。') == 1
        assert not dialog.save_mode.isVisible()
        assert not dialog.save_mode.model().item(1).isEnabled()
        dialog.start.click()
        assert dialog.isVisible() and not target.exists()
        dialog.start.click()

    interact_with_dialog(monkeypatch, merge)
    if entry == 'tabs':
        assert window.open_files_in_tabs(paths)
        assert window.merge_open_files()
        assert [tab.ws.path for tab in window._tabs] == [*paths, str(target)]
    elif entry == 'file_picker':
        assert window.open_files_in_tabs(paths[:1])
        assert window.append_action.isEnabled()
        monkeypatch.setattr(QFileDialog, 'getOpenFileNames', lambda *args: (paths[1:], 'PDF'))
        window.append_files()
        assert [tab.ws.path for tab in window._tabs] == [paths[0], str(target)]
    else:
        assert window.open_shell_files(paths, 'merge')
        assert window.ws.path == str(target)
    assert requested == ['合并为副本', '合并为副本']
    assert window.ws.path == str(target)
    with p.open(signed_source) as source_doc, p.open(target) as output:
        assert signature_state(source_doc) == 'present' and signature_state(output) == 'none'
        assert [page_fingerprint(page) for page in output] == [page_fingerprint(page) for page in source_doc] * 2
    assert hashlib.sha256(second.read_bytes()).digest() == hashlib.sha256(signed_source.read_bytes()).digest()


def test_signed_pdf_supports_direct_editing_before_saving_a_copy(window, signed_source, tmp_path, monkeypatch):
    target = tmp_path / 'editable.pdf'
    original = str(signed_source)
    requested = []

    def choose_path(parent, title, suggested, *args, **kwargs):
        requested.append(title)
        return ('' if len(requested) == 1 else str(target), 'PDF')

    monkeypatch.setattr(QFileDialog, 'getSaveFileName', choose_path)
    monkeypatch.setattr(QMessageBox, 'exec', lambda *args: pytest.fail('Unexpected modal'))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: pytest.fail('Unexpected information popup'))
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: pytest.fail('Unexpected edit warning'))
    assert window.open_files_in_tabs([original])
    assert all(button.accessibleName() != '签名与保护信息' for button in window.statusBar().findChildren(QToolButton))
    assert '签名' not in window.statusBar().currentMessage()
    assert window.save_action.isEnabled() and window.page_rotate_actions[0].isEnabled()
    assert window.copy_pages_action.isEnabled() and window.split_action.isEnabled()
    window.toggle_annotation_tools()
    assert window.ws.path == original and window.annotation_tools.isVisible() and not requested
    window.rotate(90)
    window.ws.edit_page(0, PageEdit('note', ((70, 70),), text='review note'))
    assert window.ws.dirty and not requested and not target.exists()
    assert not window.save() and window.ws.path == original and window.ws.dirty
    assert window.save()
    assert window.ws.path == str(target)
    with p.open(signed_source) as source_doc, p.open(target) as output:
        assert output[0].rotation == (source_doc[0].rotation + 90) % 360
        assert signature_state(source_doc) == 'present' and signature_state(output) == 'none'
        assert any(annot.info['content'] == 'review note' for annot in output[0].annots())
        assert [hashlib.sha256(page.get_text().encode()).digest() for page in output] == [
            hashlib.sha256(page.get_text().encode()).digest() for page in source_doc]
    assert len(requested) == 2 and all('副本' in title for title in requested)
    window.undo_group.undo()
    assert window.save() and len(requested) == 2


def test_signed_pdf_exports_text_and_splits_without_a_separate_editable_file(window, signed_source, tmp_path, monkeypatch):
    target = tmp_path / 'export.txt'
    output = tmp_path / 'parts'
    output.mkdir()
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(target), 'TXT'))
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *args: str(output))
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: pytest.fail('Unexpected export warning'))
    def accept_bookmark_note(box):
        assert box.windowTitle() == '文件已写出' and '签名' not in box.text()
        return QMessageBox.StandardButton.Ok
    monkeypatch.setattr(QMessageBox, 'exec', accept_bookmark_note)
    monkeypatch.setattr(window, '_open_output_folder', lambda folder: None)
    assert window.open_files_in_tabs([str(signed_source)])
    window.export_text()
    assert target.read_text(encoding='utf-8-sig').strip()
    window._ensure_tools()
    window.split_each.setChecked(True)
    window.split()
    with p.open(signed_source) as original:
        for index, page in enumerate(original, 1):
            with p.open(output / f'{signed_source.stem}_{index}.pdf') as result:
                assert len(result) == 1 and signature_state(result) == 'none'
                assert page_fingerprint(result[0]) == page_fingerprint(page)
    assert window.ws.path == str(signed_source)


def test_saving_after_removing_signed_pages_still_protects_the_original_tab_path(window, tmp_path, monkeypatch):
    source, incoming, target = (tmp_path / name for name in ('signed.pdf', 'incoming.pdf', 'updated.pdf'))
    make_pdf(source, signature='present')
    make_pdf(incoming)
    original = source.read_bytes()
    assert window.open_files_in_tabs([str(source)])
    count = len(window.ws.pages)
    assert window.insert_dropped_files([str(incoming)])
    window.ws.set_selection(list(range(count)))
    window.delete_pages()
    assert not window.store.has_signed_pages(window.ws.pages)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(target), 'PDF'))
    assert window.save()
    assert window.ws.path == str(target) and source.read_bytes() == original


def test_merge_copy_notice_and_save_controls_follow_the_selection(window, tmp_path, monkeypatch):
    paths = [tmp_path / name for name in ('signed.pdf', 'first.pdf', 'second.pdf')]
    make_pdf(paths[0], signature='present')
    for path in paths[1:]:
        make_pdf(path)
    assert window.open_files_in_tabs([str(path) for path in paths])

    def change_selection(dialog):
        signed = dialog.files.item(0)
        assert dialog.start.text() == '合并为副本'
        signed.setCheckState(Qt.CheckState.Unchecked)
        assert dialog.save_mode.isVisible() and dialog.save_mode.model().item(1).isEnabled()
        assert '数字签名' not in dialog.summary.text()
        dialog.save_mode.setCurrentIndex(1)
        signed.setCheckState(Qt.CheckState.Checked)
        assert dialog.save_mode.currentData() == 'copy' and not dialog.save_mode.isVisible()
        assert dialog.start.isEnabled() and dialog.start.text() == '合并为副本'
        dialog.reject()

    interact_with_dialog(monkeypatch, change_selection)
    assert not window.merge_open_files()


@pytest.mark.parametrize('signature', ['present', 'unknown', 'restricted'])
def test_merge_copy_keeps_source_and_permission_protection(tmp_path, signature):
    source, plain = tmp_path / 'signed.pdf', tmp_path / 'plain.pdf'
    restricted = signature == 'restricted'
    make_pdf(source, signature='present' if restricted else signature, password='reader' if restricted else None,
             permissions=p.PDF_PERM_PRINT if restricted else 4095)
    make_pdf(plain)
    store = PdfStore()
    try:
        pages = store.load(str(source), password='reader' if restricted else None)
        others = store.load(str(plain))
        originals = [path.read_bytes() for path in (source, plain)]
        options = WriteOptions(operation='merge')
        target = tmp_path / 'target.pdf'
        if signature == 'present':
            assert not store.operation_reason(pages, 'modify')
            assert not store.operation_reason(pages, 'merge')
            with pytest.raises(PdfError, match='覆盖'):
                store.write(pages, str(source))
            for path in (source, plain):
                with pytest.raises(PdfError, match='覆盖'):
                    write_pdf(store, pages + others, str(path), options=options)
        else:
            assert store.operation_reason(pages, 'modify')
            assert store.operation_reason(pages, 'merge')
            with pytest.raises(PdfError):
                write_pdf(store, pages + others, str(target), options=options)
        assert not target.exists()
        assert [path.read_bytes() for path in (source, plain)] == originals
    finally:
        store.close_all()
