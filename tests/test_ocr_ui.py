from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from app.ocrui import OCRDialog, open_ocr, unused_output
from app.ui import MainWindow


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def test_range_and_nonconflicting_output(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    source = tmp_path / 'scan.pdf'
    (tmp_path / 'scan_OCR.pdf').write_bytes(b'existing')
    dialog = OCRDialog(source, 8, 3)
    try:
        assert dialog.output.text().endswith('scan_OCR_2.pdf')
        assert dialog.indices() == list(range(8))
        assert not dialog.start.isEnabled()
        dialog.runtime_ready = True
        dialog.scope.setCurrentIndex(0)
        assert dialog.indices() == [3] and dialog.start.isEnabled()
        dialog.scope.setCurrentIndex(2)
        assert not dialog.start.isEnabled()
        dialog.range.setText('1-3,5,8')
        assert dialog.indices() == [0, 1, 2, 4, 7] and dialog.start.isEnabled()
        dialog.range.setText('9')
        assert not dialog.start.isEnabled()
        dialog.range.setText('1')
        dialog.output.setText(str(source))
        assert not dialog.start.isEnabled()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_dirty_workspace_requires_save(qapp, tmp_path, monkeypatch):
    import pymupdf as p
    source = tmp_path / 'source.pdf'
    with p.open() as doc:
        doc.new_page()
        doc.save(source)
    window = MainWindow()
    messages = []
    monkeypatch.setattr(QMessageBox, 'information', lambda parent, title, message: messages.append(message))
    try:
        window.ws.open_replace(window.store.load(str(source)), str(source))
        window.ws.select_index(0)
        window.ws.rotate(90)
        assert window.ws.dirty
        open_ocr(window)
        assert messages and '保存' in messages[0]
    finally:
        window.ws.reset()
        window.store.close_all()
        window.close()
        qapp.processEvents()


def test_word_conversion_exposes_local_component_install(qapp,monkeypatch):
    from app.wordui import WordConverterWindow
    from app import ocrui
    called=[]
    monkeypatch.setattr(ocrui,'install_local_runtime',lambda parent:called.append(parent) or True)
    window=WordConverterWindow(direction='pdf_to_word')
    try:
        assert not window.ocr_install.isHidden()
        window.ocr_install.click()
        assert called==[window] and '已安装' in window.summary.text()
        window.switch_direction('word_to_pdf')
        assert window.ocr_install.isHidden()
    finally:window.close()


def test_component_picker_cancel_does_not_start_install(qapp,monkeypatch):
    from app import ocrui
    from PyQt6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *args:('',''))
    def never_run(*args,**kwargs):
        raise AssertionError('Cancelled file picker must not create an install task')
    monkeypatch.setattr(ocrui,'run_operation',never_run)
    assert not ocrui.install_local_runtime(None)
