"""Generated regressions and opt-in checks against an external OFD sample.

For sample-based checks, set ZHIXU_OFD_SAMPLE to its absolute path. Never copy a business document into
this repository; choose an external pytest --basetemp for all derived outputs.
Sample-based checks skip when no sample was explicitly supplied.
"""
import hashlib
import os
import zipfile
from pathlib import Path
from threading import Event, Timer

import pymupdf
import pytest
from PyQt6.QtCore import QMimeData, QSettings, QTimer, QUrl, Qt
from PyQt6.QtPrintSupport import QPrinter
from PyQt6.QtWidgets import QApplication, QFileDialog, QLineEdit

from app.output import OperationCancelled
from app.pdfio import PdfError, PdfStore
from app.pdfworker import write_pdf


@pytest.mark.parametrize('delta_x,expected_x', [('', [10, 10, 10]), (' DeltaX="2 3"', [10, 12, 15])])
def test_generated_text_positioning_with_and_without_delta_x(tmp_path, delta_x, expected_x):
    from app.ofd import MM, convert_ofd
    source = tmp_path / 'generated.ofd'
    namespace = 'xmlns="http://www.ofdspec.org/2016"'
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('OFD.xml', f'<OFD {namespace}><DocBody><DocRoot>Doc/Document.xml</DocRoot></DocBody></OFD>')
        archive.writestr('Doc/Document.xml', f'<Document {namespace}><CommonData><MaxUnitID>4</MaxUnitID>'
            '<PageArea><PhysicalBox>0 0 100 100</PhysicalBox></PageArea><DocumentRes>Res.xml</DocumentRes>'
            '</CommonData><Pages><Page ID="2" BaseLoc="Page.xml"/></Pages></Document>')
        archive.writestr('Doc/Res.xml', f'<Res {namespace} BaseLoc="Res"><Fonts><Font ID="1" FontName="Generated">'
            '<FontFile>font.bin</FontFile></Font></Fonts></Res>')
        archive.writestr('Doc/Res/font.bin', pymupdf.Font('helv').buffer)
        archive.writestr('Doc/Page.xml', f'<Page {namespace}><Content><Layer ID="3">'
            '<TextObject ID="4" Boundary="10 10 20 30" Font="1" Size="4">'
            f'<TextCode X="0" Y="5" DeltaY="5 5"{delta_x}>ABC</TextCode>'
            '</TextObject></Layer></Content></Page>')
    data, warnings = convert_ofd(source)
    assert not warnings
    with pymupdf.open(stream=data) as doc:
        chars = [char for block in doc[0].get_text('rawdict')['blocks'] if 'lines' in block
                 for line in block['lines'] for span in line['spans'] for char in span['chars']]
        assert ''.join(char['c'] for char in chars) == 'ABC'
        assert [char['origin'][0] / MM for char in chars] == pytest.approx(expected_x)
        assert [char['origin'][1] / MM for char in chars] == pytest.approx([15, 20, 25])


@pytest.fixture
def sample(tmp_path):
    value = os.environ.get('ZHIXU_OFD_SAMPLE')
    if not value:
        pytest.skip('An authorized external OFD sample was not supplied')
    source = Path(value).resolve()
    repository = Path(__file__).resolve().parents[1]
    assert source.is_file() and source.suffix.lower() == '.ofd'
    assert not source.is_relative_to(repository), 'Keep the sample outside the repository'
    assert not tmp_path.resolve().is_relative_to(repository), 'Use an external --basetemp'
    digest = hashlib.sha256(source.read_bytes()).digest()
    yield source
    assert hashlib.sha256(source.read_bytes()).digest() == digest, 'The original must not change'


@pytest.fixture(scope='session')
def application():
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
    return QApplication.instance() or QApplication([])


def test_sample_preview_save_and_print(sample, tmp_path, application):
    from app.printing import PrintOptions, render_pages_to_printer
    store = PdfStore()
    progress = []
    try:
        pages = store.load(str(sample), progress_callback=lambda c, n, m: progress.append((c, n)))
        assert pages and any(n > 0 for c, n in progress)
        image = store.render_qimage(pages[0], 1000)
        assert not image.isNull() and image.width() == 1000
        source = store._source(pages[0].source_id)
        assert source.data.startswith(b'%PDF-')
        expected = source.doc[0].get_pixmap().samples
        target = tmp_path / 'saved.pdf'
        result = write_pdf(store, pages, str(target), register_result=True)
        with pymupdf.open(target) as doc:
            assert len(doc) == len(pages)
            assert doc[0].get_pixmap().samples == expected
        assert result.pages
        for destination in (sample, tmp_path / 'blocked.ofd'):
            with pytest.raises(PdfError, match='OFD'):
                store.write(pages, str(destination))
        assert not (tmp_path / 'blocked.ofd').exists()
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(tmp_path / 'printed.pdf'))
        render_pages_to_printer(printer, store, pages, PrintOptions(page_indices=list(range(len(pages)))))
        with pymupdf.open(tmp_path / 'printed.pdf') as doc:
            assert len(doc) == len(pages) and doc[0].get_images()
    finally:
        store.close_all()


def test_sample_cancel_does_not_register_partial_document(sample, tmp_path):
    store = PdfStore()
    event = Event()
    timer = Timer(.15, event.set)
    timer.start()
    try:
        with pytest.raises(OperationCancelled):
            store.load(str(sample), cancel=event.is_set)
        assert not store._sources
        assert not list(tmp_path.rglob('imported.pdf'))
    finally:
        timer.join()
        store.close_all()


def test_sample_window_open_save_dialog_and_reload(sample, tmp_path, application):
    from app.__main__ import startup_pdf_paths
    from app.ui import MainWindow, _pdf_paths_from_mime
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(sample))])
    assert [Path(p) for p in _pdf_paths_from_mime(mime)] == [sample]
    assert startup_pdf_paths(['app', str(sample)]) == [str(sample)]
    window = MainWindow()
    # Store all window preferences locally to this test, never in the user's registry.
    window.show()
    timer = QTimer(window)
    target = tmp_path / 'ui-saved.pdf'
    selected = []
    def choose_destination():
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, QFileDialog):
            selected.append(dialog.windowTitle())
            dialog.setDirectory(str(tmp_path))
            dialog.findChild(QLineEdit, 'fileNameEdit').setText(target.name)
            dialog.accept()
    timer.timeout.connect(choose_destination)
    try:
        cancelled = []
        cancel_timer = QTimer(window)
        def cancel_import():
            dialog = getattr(window, '_task_dialog', None)
            if dialog is not None and dialog.windowTitle() == '打开 OFD':
                cancelled.append(True)
                dialog.request_cancel()
                cancel_timer.stop()
        cancel_timer.timeout.connect(cancel_import)
        cancel_timer.start(30)
        assert window._try_load(str(sample)) is None
        cancel_timer.stop()
        assert cancelled and not window.store._sources and not window.ws.pages
        assert window.open_files_in_tabs([str(sample)])
        assert window.ws.path == str(sample)
        assert window.print_action.isEnabled()
        assert window.save_action.text() == '另存为 PDF'
        timer.start(30)
        assert window.save()
        timer.stop()
        assert selected == ['另存为 PDF']
        assert window.ws.path == str(target) and target.is_file()
        assert window.save_action.text() == '保存'
        with pymupdf.open(target) as doc:
            assert len(doc) == len(window.ws.pages)
    finally:
        timer.stop()
        window.ws.reset()
        window.close()
        application.processEvents()
