"""Shared progress UI and worker for file operations."""
from __future__ import annotations
from app.themes import set_theme_style
from time import monotonic
from threading import Event

from PyQt6.QtCore import QEventLoop, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from app import APP_NAME


class TaskProgressDialog(QDialog):
    canceled = pyqtSignal()

    def __init__(self, title, parent=None, *, cancellable=False):
        super().__init__(parent)
        self._finished = False
        self._cancelling = False
        self._cancellable = cancellable
        self.setObjectName('taskProgress')
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setFixedWidth(460)
        set_theme_style(self, '''
            QDialog#taskProgress { background: #FFFFFF; }
            QDialog#taskProgress QLabel { background: transparent; border: none;
                color: #536762; font-family: "Microsoft YaHei UI"; font-size: 12px; }
            QDialog#taskProgress QLabel#taskTitle { color: #173C36; font-size: 20px; font-weight: 600; }
            QDialog#taskProgress QLabel#taskBadge { color: #0F766E; font-size: 11px; font-weight: 600; }
            QDialog#taskProgress QLabel#taskStage { color: #254C45; font-size: 14px; }
            QDialog#taskProgress QLabel#taskPercent { color: #0F766E; font-size: 14px; font-weight: 600; }
            QDialog#taskProgress QProgressBar { border: none; background: #E8F0ED;
                border-radius: 4px; min-height: 8px; max-height: 8px; }
            QDialog#taskProgress QProgressBar::chunk { background: #149587; border-radius: 4px; }
            QDialog#taskProgress QFrame#taskDivider { background: #E9EFEC; border: none; max-height: 1px; }
            QDialog#taskProgress QPushButton { background: #FFFFFF; color: #36564F;
                border: 1px solid #C9D9D3; border-radius: 6px; padding: 6px 18px;
                font-family: "Microsoft YaHei UI"; font-size: 12px; min-height: 20px; }
            QDialog#taskProgress QPushButton:hover { background: #F0F7F4; border-color: #0F766E; }
            QDialog#taskProgress QPushButton:disabled { color: #8B9B95; background: #F5F7F6; }
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(0)
        badge = QLabel(f'{APP_NAME}  /  任务进度')
        badge.setObjectName('taskBadge')
        layout.addWidget(badge)
        layout.addSpacing(10)
        heading = QLabel(title)
        heading.setObjectName('taskTitle')
        layout.addWidget(heading)
        layout.addSpacing(24)
        row = QHBoxLayout()
        self.stage = QLabel('正在准备…')
        self.stage.setObjectName('taskStage')
        self.stage.setWordWrap(True)
        row.addWidget(self.stage, 1)
        self.percent = QLabel('处理中')
        self.percent.setObjectName('taskPercent')
        row.addWidget(self.percent, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(row)
        layout.addSpacing(12)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 0)
        layout.addWidget(self.bar)
        layout.addSpacing(10)
        self.detail = QLabel('正在处理，请稍候')
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        layout.addSpacing(22)
        divider = QFrame()
        divider.setObjectName('taskDivider')
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        layout.addSpacing(16)
        footer = QHBoxLayout()
        self.hint = QLabel('本地处理 · 文件不会上传')
        footer.addWidget(self.hint, 1)
        self.cancel_button = QPushButton('取消任务')
        self.cancel_button.setVisible(cancellable)
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.request_cancel)
        footer.addWidget(self.cancel_button)
        layout.addLayout(footer)

    def update_progress(self, current, total, message):
        if self._finished or self._cancelling:
            return
        self.stage.setText(message)
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(min(current, total))
            self.percent.setText(f'{min(100, int(current * 100 / total))}%')
            self.detail.setText(f'已完成 {min(current, total)} / {total}')
        else:
            self.bar.setRange(0, 0)
            self.percent.setText('处理中')
            self.detail.setText('此步骤耗时取决于文件大小，请稍候')

    def request_cancel(self):
        if self._finished or self._cancelling or not self._cancellable:
            return
        self._cancelling = True
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText('正在取消')
        self.stage.setText('正在结束当前步骤…')
        self.detail.setText('结束后自动关闭，请勿重复操作')
        self.percent.setText('取消中')
        self.bar.setRange(0, 0)
        self.canceled.emit()

    def reject(self):
        self.request_cancel()

    def closeEvent(self, event):
        if self._finished:
            event.accept()
        else:
            self.request_cancel()
            event.ignore()

    def complete(self):
        if self._finished:
            return
        self._finished = True
        self.accept()


class OperationThread(QThread):
    progress = pyqtSignal(int, int, str)

    def __init__(self, work, parent=None, *, cancellable=False):
        super().__init__(parent)
        self.work = work
        self.result = None
        self.error = None
        self._last_progress = 0.0
        self.cancel_event = Event()
        self.cancellable = cancellable

    def report(self, current, total, message):
        now = monotonic()
        if current in (0, total) or now - self._last_progress >= .05:
            self._last_progress = now
            self.progress.emit(current, total, message)

    def run(self):
        try:
            self.result = self.work(self.report, self.cancel_event.is_set) if self.cancellable else self.work(self.report)
        except Exception as exc:
            self.error = exc


SHOW_DELAY_MS = 200


def run_operation(parent, title, work, *, cancellable=False, show_delay_ms=SHOW_DELAY_MS):
    """Keep existing synchronous call contracts while Qt runs a responsive modal event loop."""
    dialog = TaskProgressDialog(title, parent, cancellable=cancellable)
    thread = OperationThread(work, dialog, cancellable=cancellable)
    dialog.canceled.connect(thread.cancel_event.set)
    loop = QEventLoop()
    previous = getattr(parent, "_task_dialog", None)
    # 快操作不该闪一个模态框：先等一会儿，确实干不完才露面。
    opener = QTimer(dialog)
    opener.setSingleShot(True)
    def show_pending():
        if not dialog._finished and thread.isRunning() and parent._task_dialog is dialog:
            dialog.show()
    def finish():
        opener.stop()
        dialog.complete()
        loop.quit()
    opener.timeout.connect(show_pending)
    thread.progress.connect(dialog.update_progress, Qt.ConnectionType.QueuedConnection)
    thread.finished.connect(finish, Qt.ConnectionType.QueuedConnection)
    parent._task_dialog = dialog
    try:
        if show_delay_ms <= 0:
            # 打印等原生调用可能长时间占用解释器，先绘制反馈再启动工作线程。
            dialog.show()
            dialog.repaint()
        else:
            opener.start(show_delay_ms)
        # 延迟到事件循环内再启动，避免线程先结束、循环后启动导致 quit 落空。
        QTimer.singleShot(0, thread.start)
        loop.exec()
        thread.wait()
        if thread.error is not None:
            raise thread.error
        return thread.result
    finally:
        opener.stop()
        # Error dialogs may start another event loop before deferred deletion runs.
        # Always hide this dialog synchronously and reject late progress/show events.
        dialog.complete()
        parent._task_dialog = previous
        dialog.deleteLater()
