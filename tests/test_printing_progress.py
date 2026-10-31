from time import monotonic

import pymupdf
import pytest
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QPainter, QPageSize
from PyQt6.QtPrintSupport import QPrinter
from PyQt6.QtWidgets import QApplication, QWidget

from app.output import OperationCancelled
from app.pdfio import PdfError, PdfStore
from app.pdfworker import print_pdf
from app.printing import PrintDialog, PrintOptions, render_pages_to_printer
from app.progress import run_operation
from app.workspace import PageEdit, Workspace


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('phase', ['preparation', 'rendering', 'submission'])
def test_print_progress_covers_preparation_rendering_and_queue_submission(qapp, tmp_path, monkeypatch, phase):
    source = tmp_path / 'generated.pdf'
    with pymupdf.open() as doc:
        doc.new_page(width=100, height=140).insert_text((10, 30), 'Print progress')
        doc.save(source)
    store = PdfStore()
    events = []
    observed = {}
    try:
        pages = store.load(str(source))
        original_size = store.page_size
        original_render = store.render_qimage

        def page_size(ref):
            observed.setdefault('preparation', list(events))
            return original_size(ref)

        def render(ref, width):
            observed['rendering'] = list(events)
            return original_render(ref, width)

        class ObservedPainter(QPainter):
            def end(self):
                observed['submission'] = list(events)
                return super().end()

        monkeypatch.setattr(store, 'page_size', page_size)
        monkeypatch.setattr(store, 'render_qimage', render)
        monkeypatch.setattr('app.printing.QPainter', ObservedPainter)
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(tmp_path / 'printed.pdf'))
        printer.setResolution(72)
        render_pages_to_printer(printer, store, pages, PrintOptions(page_indices=[0]),
                                progress_callback=lambda *args: events.append(args))
        if phase == 'preparation':
            assert observed[phase], '打印排版开始前没有进度提示'
        elif phase == 'rendering':
            assert '渲染' in observed[phase][-1][2], '耗时渲染时仍显示上一页状态'
        else:
            assert observed[phase][-1][1] == 0 and '提交' in observed[phase][-1][2], '提交队列前已显示 100%'
            assert events[-1][:2] == (1, 1)
    finally:
        store.close_all()


@pytest.mark.parametrize('allow_print', [False, True])
def test_process_print_preserves_encrypted_source_print_permissions(qapp, tmp_path, allow_print):
    source, target = tmp_path / 'encrypted.pdf', tmp_path / 'printed.pdf'
    with pymupdf.open() as doc:
        doc.new_page(width=100, height=140)
        doc.save(source, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                 owner_pw='generated-owner', user_pw='generated-reader',
                 permissions=pymupdf.PDF_PERM_PRINT if allow_print else pymupdf.PDF_PERM_COPY)
    original = source.read_bytes()
    store = PdfStore()
    try:
        pages = store.load(str(source), 'generated-reader')
        options = PrintOptions(page_indices=[0], paper=QPageSize.PageSizeId.A7)
        if allow_print:
            print_pdf(store, pages, options, output_path=str(target))
            with pymupdf.open(target) as printed:
                assert len(printed) == 1
        else:
            with pytest.raises(PdfError, match='不允许打印'):
                print_pdf(store, pages, options, output_path=str(target))
            assert not target.exists()
        assert source.read_bytes() == original
        assert not list(tmp_path.rglob('task-pdfprint-*'))
    finally:
        store.close_all()


def test_print_worker_failure_closes_progress_and_keeps_source(qapp, tmp_path):
    source = tmp_path / 'generated.pdf'
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(source)
    original = source.read_bytes()
    store = PdfStore()
    parent = QWidget()
    try:
        pages = store.load(str(source))
        with pytest.raises(PdfError, match='无法启动打印任务'):
            run_operation(parent, '发送打印任务', lambda report:
                          print_pdf(store, pages, PrintOptions(page_indices=[0]), progress_callback=report,
                                    output_path=str(tmp_path / 'missing' / 'printed.pdf')), show_delay_ms=0)
        assert parent._task_dialog is None
        assert QApplication.activeModalWidget() is None
        assert not list(tmp_path.rglob('task-pdfprint-*'))
        assert source.read_bytes() == original
    finally:
        store.close_all()
        parent.deleteLater()


def _complex_pdf(path):
    with pymupdf.open() as doc:
        page = doc.new_page()
        contents = doc.get_new_xref()
        doc.update_object(contents, '<<>>')
        commands = [f'{i % 10 / 10} 0.3 0.7 rg 10 10 575 822 re f\n' for i in range(240)]
        doc.update_stream(contents, ''.join(commands).encode('ascii'))
        page.set_contents(contents)
        doc.save(path)


@pytest.mark.parametrize('cancel', [False, True])
def test_native_print_work_keeps_gui_alive_and_can_be_cancelled(qapp, tmp_path, cancel):
    source, target = tmp_path / 'complex.pdf', tmp_path / 'printed.pdf'
    _complex_pdf(source)
    original = source.read_bytes()
    store = PdfStore()
    parent = QWidget()
    pulses = []
    rendering = []
    visible = []
    cancelled_at = []
    timer = QTimer(parent)
    timer.setInterval(10)

    def tick():
        now = monotonic()
        pulses.append(now)
        dialog = getattr(parent, '_task_dialog', None)
        if dialog and '正在渲染' in dialog.stage.text():
            rendering.append(now)
            visible.append(dialog.isVisible())
            if cancel and not cancelled_at:
                cancelled_at.append(now)
                dialog.request_cancel()

    timer.timeout.connect(tick)
    try:
        pages = store.load(str(source))
        options = PrintOptions(page_indices=[0], paper=QPageSize.PageSizeId.A4)
        timer.start()
        def work(report, stop):
            print_pdf(store, pages, options, progress_callback=report, cancel=stop, output_path=str(target))
        if cancel:
            with pytest.raises(OperationCancelled):
                run_operation(parent, '发送打印任务', work, cancellable=True, show_delay_ms=0)
            assert monotonic() - cancelled_at[0] < 4
        else:
            run_operation(parent, '发送打印任务', work, cancellable=True, show_delay_ms=0)
            assert len(rendering) >= 10, 'PDF 原生渲染期间界面计时器必须持续响应'
            assert max(b - a for a, b in zip(rendering, rendering[1:])) < .5
            with pymupdf.open(target) as printed:
                assert len(printed) == 1
        assert pulses
        assert visible and all(visible)
        assert source.read_bytes() == original
        assert not list(tmp_path.rglob('task-pdfprint-*'))
        assert parent._task_dialog is None
    finally:
        timer.stop()
        store.close_all()
        parent.deleteLater()


def test_print_dialog_171_pages_uses_worker_and_preserves_workspace_edits(qapp, tmp_path, monkeypatch):
    from app import pdfworker
    source, target = tmp_path / 'generated-171.pdf', tmp_path / 'printed-171.pdf'
    with pymupdf.open() as doc:
        for number in range(171):
            doc.new_page(width=100, height=140).insert_text((10, 30), str(number + 1))
        doc.save(source)
    original = source.read_bytes()
    store = PdfStore()
    ws = Workspace()
    dialog = None
    timer = QTimer()
    timer.setInterval(10)
    observations = []
    requests = []
    try:
        pages = store.load(str(source))
        pages[0] = pages[0].edited(PageEdit('rect', (5, 50, 85, 125), color=(1, 0, 0), width=6))
        ws.open_replace(pages, str(source))
        dialog = PrintDialog(None, ws, store)
        options = PrintOptions(printer_name='Generated PDF sink', page_indices=list(range(171)),
                               paper=QPageSize.PageSizeId.A7, orientation='portrait')
        monkeypatch.setattr(dialog, '_collect_options', lambda: options)
        real_print = pdfworker.print_pdf
        def print_to_pdf(*args, **kwargs):
            requests.append(1)
            return real_print(*args, **kwargs, output_path=str(target))
        monkeypatch.setattr(pdfworker, 'print_pdf', print_to_pdf)
        def tick():
            task = getattr(dialog, '_task_dialog', None)
            if task and task.isVisible():
                observations.append((task.bar.maximum(), task.bar.value(), task.stage.text()))
        timer.timeout.connect(tick)
        monkeypatch.setattr('app.printing.QMessageBox.critical',
                            lambda parent, title, message: pytest.fail(message))
        timer.start()
        dialog.do_print()
        assert requests == [1], '整批打印只应启动一个工作进程'
        assert observations and any(total == 171 and 0 < value < 171 for total, value, _ in observations)
        assert dialog.result() == PrintDialog.DialogCode.Accepted
        assert ws.pages == pages and ws.pages[0].edits
        assert source.read_bytes() == original
        with pymupdf.open(target) as printed:
            assert len(printed) == 171
            first = printed[0].get_pixmap(matrix=pymupdf.Matrix(.2, .2))
            pixels = first.samples
            assert any(pixels[i] > pixels[i + 1] + 50 and pixels[i] > pixels[i + 2] + 50
                       for i in range(0, len(pixels), 3)), '未保存的红色矩形也必须进入打印结果'
    finally:
        timer.stop()
        if dialog:
            dialog.reject()
            dialog.deleteLater()
        store.close_all()
