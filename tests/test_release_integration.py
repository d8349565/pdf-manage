"""Release integration protects new conversion controls and offline OCR routing."""
from types import SimpleNamespace

import pymupdf as p
import pytest
from PyQt6.QtWidgets import QApplication,QMessageBox

from app.wordui import WordConverterWindow


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def make_input(path):
    with p.open() as doc:
        doc.new_page().insert_text((50,100),'Independent recognized narrative.',render_mode=3)
        doc.save(path)


def test_ocr_conversion_window_runs_without_word_or_registry_prompt(qapp,tmp_path,monkeypatch):
    import app.wordui as ui
    import app.wordconvert as conversion
    source=tmp_path/'recognized.pdf'; make_input(source); before=source.read_bytes()
    monkeypatch.setattr(ui,'word_available',lambda:False)
    monkeypatch.setattr(conversion,'word_available',lambda:False)
    messages=[]
    monkeypatch.setattr(QMessageBox,'information',lambda *args:messages.append(args))
    monkeypatch.setattr(QMessageBox,'exec',lambda *args:1)
    def forbidden():
        raise AssertionError('OCR export must not change Word registry settings')
    monkeypatch.setattr(ui,'pdf_import_needs_confirmation',forbidden)
    window=WordConverterWindow(direction='pdf_to_word')
    try:
        window.inputs=[str(source)]; window.rescan(); window.convert()
        assert source.with_suffix('.docx').is_file()
        assert window.table.item(0,2).text().startswith('成功')
        assert not messages and source.read_bytes()==before
    finally:window.close()


def test_running_conversion_keeps_direction_sources_and_targets(qapp,tmp_path,monkeypatch):
    source=tmp_path/'recognized.pdf'; make_input(source)
    window=WordConverterWindow(direction='pdf_to_word')
    try:
        window.inputs=[str(source)]; window.rescan()
        target=window.table.item(0,1).text()
        window._task_dialog=SimpleNamespace()
        window.clear(); window.remove_selected(); window.switch_direction('word_to_pdf')
        window.browse_row_target(0); window.choose_output_dir(); window.reset_output_dir()
        window.add_files(); window.add_folder(); window.rescan()
        assert window.direction=='pdf_to_word' and window.inputs==[str(source)]
        assert window.table.rowCount()==1 and window.table.item(0,1).text()==target
    finally:
        window._task_dialog=None; window.close()
