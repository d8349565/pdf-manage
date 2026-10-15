"""Process-write regressions using only generated temporary PDFs."""
from io import BytesIO
from pathlib import Path
from threading import Event

import pymupdf as p
import pytest
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QWidget

from app.output import OperationCancelled
from app.pdfio import PdfError, PdfStore, WriteOptions
from app.pdfworker import write_pdf, write_pdf_groups
from app.progress import run_operation
from app.workspace import OutlineEntry, PageEdit


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def source_pdf(path, text="Alpha"):
    with p.open() as doc:
        doc.new_page().insert_text((40, 50), text)
        doc.new_page().insert_text((40, 50), "Beta")
        doc.set_toc([[1, "Second page", 2]])
        doc[0].insert_link(dict(kind=p.LINK_GOTO, page=1, to=p.Point(0, 0), **{"from": p.Rect(10, 10, 30, 30)}))
        doc.save(path)


def test_process_write_keeps_gui_events_edits_and_registered_metadata(qapp, tmp_path):
    parent = QWidget()
    source, target = tmp_path / "source.pdf", tmp_path / "output.pdf"
    source_pdf(source)
    original = source.read_bytes()
    store = PdfStore()
    timer = QTimer(parent)
    pulses = []
    timer.setInterval(10)
    timer.timeout.connect(lambda: pulses.append(1))
    try:
        pages = store.load(str(source))
        pages[0] = pages[0].rotated(90).edited(PageEdit("note", ((80, 80),), text="Review", annotation_id="note-one"))
        outline = (OutlineEntry(1, "Changed outline", pages[1].page_id,
                               (("kind", p.LINK_GOTO), ("to", (10, 10)))),)
        timer.start()
        result = run_operation(parent, "Save", lambda progress:
            write_pdf(store, pages, str(target), options=WriteOptions(compression="quality", outline=outline),
                      progress_callback=progress, register_result=True))
        assert pulses, "The GUI event loop must keep running while the child writes"
        assert source.read_bytes() == original
        assert len(result.pages) == 2
        assert store.page_size(result.pages[0]) == store.page_size(pages[0])
        with p.open(target) as doc:
            page = doc[0]
            assert page.rotation == 90
            assert "Alpha" in page.get_text()
            assert page.first_annot.info["content"] == "Review"
            assert page.get_links()[0]["page"] == 1
            assert doc.get_toc()[0][:3] == [1, "Changed outline", 2]
    finally:
        timer.stop()
        store.close_all()
        parent.close()


def test_split_reuses_one_worker_and_one_source_snapshot(tmp_path, monkeypatch):
    from app import pdfworker
    source = tmp_path / "source.pdf"
    source_pdf(source)
    original = source.read_bytes()
    store = PdfStore()
    worker = pdfworker._worker
    requests = []
    def record(folder, request, *args, **kwargs):
        requests.append(request)
        assert len(list(Path(folder).glob("source-*.pdf"))) == 1
        return worker(folder, request, *args, **kwargs)
    monkeypatch.setattr(pdfworker, "_worker", record)
    try:
        pages = store.load(str(source))
        targets = [str(tmp_path / f"part-{i}.pdf") for i in range(3)]
        results = write_pdf_groups(store, pages, [[0], [1], [1, 0]], targets)
        assert len(requests) == 1 and requests[0]["operation"] == "write_groups"
        assert [result.path for result in results] == targets
        for target, texts in zip(targets, [("Alpha",), ("Beta",), ("Beta", "Alpha")]):
            with p.open(target) as doc:
                assert len(doc) == len(texts)
                assert all(text in doc[i].get_text() for i, text in enumerate(texts))
        assert source.read_bytes() == original
        assert not list(tmp_path.glob(".zhixu-write-*"))
    finally:
        store.close_all()


def test_cancel_split_keeps_only_committed_outputs(tmp_path, monkeypatch):
    from app import pdfworker
    source = tmp_path / "source.pdf"
    source_pdf(source)
    targets = [str(tmp_path / f"part-{i}.pdf") for i in range(2)]
    stop = Event()
    worker = pdfworker._worker
    def cancel_after_first(folder, request, progress, cancel, *, on_prepared):
        def commit_then_cancel(index, prepared):
            on_prepared(index, prepared)
            stop.set()
        return worker(folder, request, progress, cancel, on_prepared=commit_then_cancel)
    monkeypatch.setattr(pdfworker, "_worker", cancel_after_first)
    store = PdfStore()
    try:
        with pytest.raises(OperationCancelled, match="已完整写出 1 个文件"):
            write_pdf_groups(store, store.load(str(source)), [[0], [1]], targets, cancel=stop.is_set)
        with p.open(targets[0]) as doc:
            assert "Alpha" in doc[0].get_text()
        assert not Path(targets[1]).exists()
        assert not list(tmp_path.glob(".zhixu-write-*"))
    finally:
        store.close_all()


def test_split_preserves_a_target_changed_before_parent_commit(tmp_path, monkeypatch):
    from app import pdfworker
    source, target = tmp_path / "source.pdf", tmp_path / "target.pdf"
    source_pdf(source)
    source_pdf(target)
    worker = pdfworker._worker
    def change_target(folder, request, progress, cancel, *, on_prepared):
        def change_then_commit(index, prepared):
            target.write_bytes(b"external change")
            on_prepared(index, prepared)
        return worker(folder, request, progress, cancel, on_prepared=change_then_commit)
    monkeypatch.setattr(pdfworker, "_worker", change_target)
    store = PdfStore()
    try:
        with pytest.raises(PdfError, match="目标文件"):
            write_pdf_groups(store, store.load(str(source)), [[0]], [str(target)])
        assert target.read_bytes() == b"external change"
        assert not list(tmp_path.glob(".zhixu-write-*"))
    finally:
        store.close_all()


@pytest.mark.parametrize("has_baseline", [False, True])
def test_cancelled_compression_clears_calculating_feedback(qapp, tmp_path, monkeypatch, has_baseline):
    from app.compression import CompressionDialog
    from app.pdfio import WriteResult
    from app.ui import MainWindow
    source = tmp_path / "source.pdf"
    source_pdf(source)
    window = MainWindow()
    dialog = None
    try:
        pages = window.store.load(str(source))
        window.ws.open_replace(pages, str(source))
        dialog = CompressionDialog(window)
        if has_baseline:
            dialog.baseline = WriteResult(str(source), tuple(pages))
        monkeypatch.setattr(window, "_write_pdf", lambda *args, **kwargs: None)
        dialog.prepare()
        assert "取消" in dialog.summary.text()
        assert all("取消" in label.text() for label in dialog.images)
        assert dialog.calculate.isEnabled() and not dialog.export.isEnabled() and not dialog._busy
    finally:
        if dialog is not None:
            dialog.reject()
        window.ws.reset()
        window.store.close_all()
        window.close()


@pytest.mark.parametrize("cancelled", [False, True])
def test_prepared_write_does_not_overwrite_cancelled_or_changed_target(tmp_path, monkeypatch, cancelled):
    from app import pdfworker
    source, target = tmp_path / "source.pdf", tmp_path / "target.pdf"
    source_pdf(source)
    source_pdf(target, "Keep this output")
    original = target.read_bytes()
    stop = Event()
    real_worker = pdfworker._worker
    def interrupt_before_commit(*args, **kwargs):
        prepared = real_worker(*args, **kwargs)
        if cancelled:
            stop.set()
        else:
            target.write_bytes(b"external content")
        return prepared
    monkeypatch.setattr(pdfworker, "_worker", interrupt_before_commit)
    store = PdfStore()
    try:
        with pytest.raises(OperationCancelled if cancelled else PdfError):
            write_pdf(store, store.load(str(source)), str(target), cancel=stop.is_set)
        assert target.read_bytes() == (original if cancelled else b"external content")
        assert not list(tmp_path.glob(".zhixu-write-*"))
    finally:
        store.close_all()


def test_compression_cannot_overwrite_source_in_worker(tmp_path):
    source = tmp_path / "source.pdf"
    source_pdf(source)
    original = source.read_bytes()
    store = PdfStore()
    try:
        with pytest.raises(PdfError, match="不能覆盖来源"):
            write_pdf(store, store.load(str(source)), str(source), options=WriteOptions(compression="high"))
        assert source.read_bytes() == original
    finally:
        store.close_all()


def test_opened_destination_guard_survives_export_of_other_source(tmp_path):
    source, target = tmp_path / "source.pdf", tmp_path / "opened.pdf"
    source_pdf(source)
    source_pdf(target, "Original target")
    store = PdfStore()
    try:
        pages = store.load(str(source))
        store.load(str(target))
        target.write_bytes(b"externally changed")
        with pytest.raises(PdfError, match="目标文件"):
            write_pdf(store, pages, str(target))
        assert target.read_bytes() == b"externally changed"
    finally:
        store.close_all()


def test_small_profile_keeps_monochrome_scan_resolution(tmp_path):
    from PIL import Image, ImageDraw
    image = Image.new("1", (1800, 2400), 1)
    draw = ImageDraw.Draw(image)
    for y in range(100, 2300, 100):
        draw.line((100, y, 1700, y), fill=0, width=2)
    stream = BytesIO()
    image.save(stream, format="PNG")
    source, target = tmp_path / "scan.pdf", tmp_path / "compressed.pdf"
    with p.open() as doc:
        doc.new_page(width=300, height=400).insert_image(p.Rect(0, 0, 300, 400), stream=stream.getvalue())
        doc.save(source)
    store = PdfStore()
    try:
        write_pdf(store, store.load(str(source)), str(target), options=WriteOptions(compression="small"))
        with p.open(target) as doc:
            assert doc[0].get_images()[0][2:4] == (1800, 2400)
    finally:
        store.close_all()


def test_balanced_resampling_reaches_target_between_power_of_two_sizes(tmp_path):
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (1200, 1600), "white")
    draw = ImageDraw.Draw(image)
    for y in range(0, 1600, 10):
        draw.line((0, y, 1200, y), fill=(20, 70, 150), width=2)
    stream = BytesIO()
    image.save(stream, format="PNG")
    source, target = tmp_path / "300dpi.pdf", tmp_path / "200dpi.pdf"
    with p.open() as doc:
        doc.new_page(width=288, height=384).insert_image(p.Rect(0, 0, 288, 384), stream=stream.getvalue())
        doc.save(source)
    store = PdfStore()
    try:
        write_pdf(store, store.load(str(source)), str(target), options=WriteOptions(compression="balanced"))
        with p.open(target) as doc:
            width, height = doc[0].get_images()[0][2:4]
            assert abs(width - 800) <= 1
            assert abs(height - 1067) <= 1
    finally:
        store.close_all()


def test_compression_dialog_runs_presets_and_keeps_only_current_results(qapp, tmp_path, monkeypatch):
    from PIL import Image
    from app import storage, ui
    from app.compression import CompressionDialog
    from PyQt6.QtCore import QThreadPool
    monkeypatch.setattr(ui, "QSettings", lambda *args: storage._settings())
    stream = BytesIO()
    Image.new("RGB", (1200, 1600), (210, 225, 245)).save(stream, format="PNG")
    source = tmp_path / "dialog-source.pdf"
    with p.open() as doc:
        doc.new_page(width=288, height=384).insert_image(p.Rect(0, 0, 288, 384), stream=stream.getvalue())
        doc.save(source)
    window = ui.MainWindow()
    dialog = None
    try:
        assert window._load_one(str(source))
        dialog = CompressionDialog(window)
        assert dialog.profile.currentData() == "balanced"
        for key in ("balanced", "quality", "high"):
            dialog.profile.setCurrentIndex(dialog.profile.findData(key))
            dialog.prepare()
            assert dialog.result is not None
            assert dialog.export.isEnabled() and not dialog._busy
            assert all(not label.pixmap().isNull() for label in dialog.images)
            assert len(dialog._preview_sources) <= 2
        dialog.reject()
        assert set(window.store._sources) == window.ws.live_sources()
    finally:
        QThreadPool.globalInstance().waitForDone(10000)
        if dialog is not None and not dialog.folder._temp._finalizer.alive:
            dialog.deleteLater()
        window.ws.reset()
        window.store.close_all()
        window.close()
