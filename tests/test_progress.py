import threading
import time

import pymupdf
import pytest
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QWidget

from app.pdfio import PdfStore
from app.progress import SHOW_DELAY_MS, TaskProgressDialog, run_operation


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def test_progress_states_and_cancel_stays_visible(qapp):
    dialog = TaskProgressDialog('PDF 转 Excel', cancellable=True)
    dialog.show()
    qapp.processEvents()
    dialog.update_progress(2, 6, '正在分析第 3 页…')
    assert dialog.bar.value() == 2
    assert dialog.percent.text() == '33%'
    assert dialog.detail.text() == '已完成 2 / 6'
    dialog.update_progress(0, 0, '正在生成 Excel 文件…')
    assert dialog.bar.maximum() == 0
    assert dialog.percent.text() == '处理中'
    cancelled = []
    dialog.canceled.connect(lambda: cancelled.append(True))
    dialog.reject()
    dialog.close()
    assert dialog.isVisible()
    assert cancelled == [True]
    assert not dialog.cancel_button.isEnabled()
    dialog.update_progress(3, 6, '迟到的进度')
    assert dialog.percent.text() == '取消中'
    dialog.complete()
    assert not dialog.isVisible()
    dialog.deleteLater()


@pytest.mark.parametrize('fail', [False, True])
def test_slow_task_shows_dialog_and_modal_ui_keeps_ticking(qapp, fail):
    parent = QWidget()
    release = threading.Event()
    observations = []
    main_thread = threading.get_ident()
    def tick():
        observations.append(QApplication.activeModalWidget())
        release.set()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(tick)
    # 超过 SHOW_DELAY_MS 才采样：慢任务此时对话框必须已经露面。
    timer.start(SHOW_DELAY_MS + 150)
    def work(progress):
        assert threading.get_ident() != main_thread
        progress(1, 3, '处理中')
        assert release.wait(3)
        if fail:
            raise ValueError('test error')
        return 42
    try:
        if fail:
            with pytest.raises(ValueError, match='test error'):
                run_operation(parent, '保存 PDF', work)
        else:
            assert run_operation(parent, '保存 PDF', work) == 42
        assert observations and isinstance(observations[0], TaskProgressDialog)
        assert parent._task_dialog is None
        assert QApplication.activeModalWidget() is None
    finally:
        release.set()
        timer.stop()
        parent.deleteLater()


def test_quick_task_never_flashes_a_dialog(qapp):
    parent = QWidget()
    observations = []
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: observations.append(QApplication.activeModalWidget()))
    timer.start(60)
    def work(progress):
        time.sleep(.09)
        return 'done'
    try:
        assert run_operation(parent, '整理工作区', work) == 'done'
        assert all(widget is None for widget in observations)
        assert not parent.findChildren(TaskProgressDialog)
    finally:
        timer.stop()
        parent.deleteLater()


def test_pdf_operations_report_page_and_file_progress(tmp_path, qapp):
    source = tmp_path / 'source.pdf'
    with pymupdf.open() as doc:
        for _ in range(3):
            doc.new_page()
        doc.save(source)
    store = PdfStore()
    events = []
    report = lambda *args: events.append(args)
    try:
        pages = store.load(str(source), progress_callback=report)
        assert events[0][:2] == (0, 0)
        assert [event[0] for event in events if event[1] == 3] == [1, 2, 3]
        events.clear()
        store.write(pages, str(tmp_path / 'saved.pdf'), progress_callback=report)
        assert [event[0] for event in events if event[1] == 3] == [1, 2, 3]
        assert events[-1][:2] == (0, 0)  # 压缩写入不能伪报完成百分比
        events.clear()
        store.write_groups(pages, [[0], [1, 2]],
                           [str(tmp_path / 'a.pdf'), str(tmp_path / 'b.pdf')],
                           progress_callback=report)
        assert events[-1][:2] == (2, 2)
        assert all(events[i][0] <= events[i + 1][0] for i in range(len(events) - 1))
    finally:
        store.close_all()
