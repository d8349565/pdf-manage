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
    completed = []
    dialog.finished.connect(lambda result: completed.append(result))
    dialog.complete()
    dialog.complete()
    assert not dialog.isVisible()
    assert len(completed) == 1
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
        assert not any(dialog.isVisible() for dialog in parent.findChildren(TaskProgressDialog))
        from PyQt6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
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


@pytest.mark.parametrize('fail', [False, True])
@pytest.mark.parametrize('offset', [-.01, 0, .01])
def test_finished_task_cannot_reopen_when_delayed_events_arrive(qapp, monkeypatch, fail, offset):
    """Replay a late timeout inside the error dialog's nested event loop."""
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QMessageBox
    from app import progress
    dialogs = []
    class CapturedDialog(TaskProgressDialog):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            dialogs.append(self)
        def deleteLater(self):
            pass  # Keep it alive to reproduce Qt's deferred-delete timing.
    monkeypatch.setattr(progress, 'TaskProgressDialog', CapturedDialog)
    parent = QWidget()
    def work(report):
        report(0, 0, '正在核对待保存文件')
        time.sleep(SHOW_DELAY_MS / 1000 + offset)
        if fail:
            raise ValueError('目标文件已改变')
        return 42
    try:
        if fail:
            with pytest.raises(ValueError, match='目标文件已改变'):
                run_operation(parent, '保存 PDF', work)
        else:
            assert run_operation(parent, '保存 PDF', work) == 42
        dialog = dialogs[-1]
        old_stage = dialog.stage.text()
        error_box = QMessageBox(parent)
        def delayed_events():
            dialog.findChild(QTimer).timeout.emit()
            dialog.update_progress(1, 1, '迟到的进度')
            assert not dialog.isVisible()
            assert dialog.stage.text() == old_stage
            assert parent._task_dialog is None
            error_box.accept()
        QTimer.singleShot(0, delayed_events)
        error_box.exec()
        QTest.qWait(20)
        assert not dialog.isVisible()
        assert QApplication.activeModalWidget() is None
        assert run_operation(parent, '再次保存', lambda report: 'ok') == 'ok'
    finally:
        for dialog in dialogs:
            TaskProgressDialog.deleteLater(dialog)
        parent.deleteLater()


def test_cancelled_task_closes_before_exception_returns(qapp):
    from app.output import OperationCancelled, check_cancel
    parent = QWidget()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: parent._task_dialog.request_cancel())
    timer.start(SHOW_DELAY_MS + 50)
    def work(report, cancel):
        while not cancel():
            time.sleep(.005)
        check_cancel(cancel)
    try:
        with pytest.raises(OperationCancelled):
            run_operation(parent, '可取消任务', work, cancellable=True)
        assert parent._task_dialog is None
        assert QApplication.activeModalWidget() is None
        assert not any(dialog.isVisible() for dialog in parent.findChildren(TaskProgressDialog))
    finally:
        timer.stop()
        parent.deleteLater()
