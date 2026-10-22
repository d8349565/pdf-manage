"""Reading-first preparation and local handoff, using generated documents only."""
import os
from pathlib import Path
import subprocess
import sys
import time

import pymupdf as p
import pytest
from PyQt6.QtWidgets import QApplication, QWidget

from app.pdfio import PdfError, PdfStore
from app.ui import MainWindow


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def pdf(path, *, objects=0, signed=False):
    with p.open() as doc:
        doc.new_page().insert_text((50, 80), 'Readable document 12345')
        for _ in range(objects):
            doc.update_object(doc.get_new_xref(), '<< /Unused true >>')
        if signed:
            doc.update_object(doc.get_new_xref(),
                '<< /Type /Sig /ByteRange [0 1 2 3] /Contents <12345678> >>')
        doc.save(path)
    return path


def wait_until(qapp, condition, seconds=8):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.005)
    assert condition()


@pytest.mark.parametrize('signed', [False, True])
def test_incremental_check_preserves_complete_scan_and_blocks_writes(tmp_path, signed):
    from app.pdfstructure import signature_state
    path = pdf(tmp_path / 'generated.pdf', objects=600, signed=signed)
    store = PdfStore()
    try:
        pages = store.load(str(path), defer_safety=True)
        ref = pages[0]
        source = store._source(ref.source_id)
        assert source.safety.signature == 'pending'
        assert store.operation_reason(pages, 'modify')
        assert store.operation_reason(pages, 'editable_copy')
        assert not store.render_preview(ref, 500).isNull()
        target = tmp_path / 'must-not-write.pdf'
        with pytest.raises(PdfError):
            store.write(pages, str(target))
        assert not target.exists()
        assert not store.prepare_safety_step(ref), 'large object scan must yield'
        while not store.prepare_safety_step(ref):
            pass
        assert source.safety.signature == signature_state(source.doc)
        assert source.safety.signature == ('present' if signed else 'none')
    finally:
        store.close_all()


def test_first_image_does_not_wait_for_text_metadata_and_reuses_displaylist(tmp_path, monkeypatch):
    store = PdfStore()
    calls = []
    original = p.Page.get_displaylist
    monkeypatch.setattr(p.Page, 'get_displaylist',
        lambda page, *args, **kw: calls.append(page.number) or original(page, *args, **kw))
    try:
        ref = store.load(str(pdf(tmp_path / 'generated.pdf')))[0]
        assert not store.render_preview(ref, 500).isNull()
        assert store.cached_annotations(ref) is None
        assert not store.render_preview(ref, 800).isNull()
        store.render_region(ref, (0, 0, 200, 200), 2)
        assert len(calls) == 1
        summary = store.prepare_annotations(ref)
        assert summary['text_boxes']
        assert len(calls) == 1
        store.render_preview(ref.rotated(90), 600)
        assert len(calls) == 2
    finally:
        store.close_all()
    assert store._display_list is None


def test_small_pdf_finishes_the_bounded_check_without_transient_readonly(tmp_path):
    store = PdfStore()
    try:
        pages = store.load(str(pdf(tmp_path / 'small.pdf')), defer_safety=True)
        assert store._source(pages[0].source_id).safety.signature == 'none'
        assert not store.operation_reason(pages, 'modify')
        assert store.cached_annotations(pages[0]) is None
    finally:
        store.close_all()


def test_reading_keeps_hidden_panels_unbuilt_and_creates_them_on_demand(tmp_path, qapp):
    window = MainWindow()
    try:
        window.show()
        assert not window._tools_ready and not window._arrange_ready
        assert window.open_files_in_tabs([str(pdf(tmp_path / 'generated.pdf'))])
        wait_until(qapp, lambda: window._readable_painted
                   and window.store._source(window.ws.pages[0].source_id).safety.signature != 'pending')
        assert not window._tools_ready and not window._arrange_ready
        window.open_export_tool(1)
        assert window._tools_ready and window.export_tools.currentIndex() == 1
        assert not window._arrange_ready
        window.stack.setCurrentIndex(1)
        assert window._arrange_ready and window.arrange_preview is not None
        assert window.arrange_view.model() is window.model
    finally:
        window.ws.reset()
        window.close()
        qapp.processEvents()


def test_local_handoff_acks_before_opening_and_preserves_queued_order(tmp_path, qapp, monkeypatch):
    from app import instance
    endpoint = 'ZhiXuPDF-test-' + tmp_path.name
    lock = tmp_path / 'reader.lock'
    monkeypatch.setattr(instance, 'instance_scope', lambda: (endpoint, lock))
    router = instance.InstanceRouter(qapp)
    class Window(QWidget):
        _opening_paths = ()
        _task_dialog = object()
        _closing = False
        def __init__(self):
            super().__init__()
            self.paths = []
        def open_files_in_tabs(self, paths):
            self.paths.extend(paths)
    window = Window()
    process = None
    try:
        assert router.start_or_forward([])
        router.attach(window)
        source = pdf(tmp_path / 'generated.pdf')
        command = '''
import sys
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from app import instance
app = QApplication([])
instance.instance_scope = lambda: (sys.argv[1], Path(sys.argv[2]))
router = instance.InstanceRouter(app)
assert not router.start_or_forward([sys.argv[3]])
'''
        process = subprocess.Popen([sys.executable, '-c', command, endpoint, str(lock), str(source)],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        wait_until(qapp, lambda: process.poll() is not None)
        out, err = process.communicate()
        assert process.returncode == 0, (out + err).decode(errors='replace')
        assert list(router.pending) == [[str(source)]] and not window.paths
        window._task_dialog = None
        wait_until(qapp, lambda: window.paths == [str(source)])
        assert not router.pending
    finally:
        router.close()
        window.close()
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
    assert not lock.exists()
