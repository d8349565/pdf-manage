"""资源管理器多选入口：进程外 COM 接收完整文件列表，不加载进 Explorer。"""
from __future__ import annotations

import pythoncom
import win32con
from win32com.shell import shell, shellcon
from win32com.server import factory
from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import QMessageBox

from app.windows_integration import IMAGE_PDF_CLSID, PDF_MERGE_CLSID

_controller = None
_FORMAT = (win32con.CF_HDROP, None, pythoncom.DVASPECT_CONTENT, -1, pythoncom.TYMED_HGLOBAL)


class MergeDropTarget:
    _reg_clsid_ = PDF_MERGE_CLSID
    _com_interfaces_ = [pythoncom.IID_IDropTarget]
    _public_methods_ = ["DragEnter", "DragOver", "DragLeave", "Drop"]

    def __init__(self):
        self._supported = False

    @staticmethod
    def _effect(effect):
        # 文件只读入工作区，绝不从资源管理器移动或删除来源。
        return (effect & shellcon.DROPEFFECT_COPY) or (effect & shellcon.DROPEFFECT_LINK)

    def DragEnter(self, data, key_state, point, effect):
        try:
            data.QueryGetData(_FORMAT)
            self._supported = True
        except pythoncom.com_error:
            self._supported = False
        return self._effect(effect) if self._supported else 0

    def DragOver(self, key_state, point, effect):
        return self._effect(effect) if self._supported else 0

    def DragLeave(self):
        self._supported = False

    def Drop(self, data, key_state, point, effect):
        if _controller is None:
            return 0
        try:
            medium = data.GetData(_FORMAT)
            paths = [shell.DragQueryFile(medium.data_handle, i)
                     for i in range(shell.DragQueryFile(medium.data_handle, -1))]
            if not paths:
                raise ValueError("没有收到所选文件，请重新选择文件。")
        except Exception as exc:
            _controller.failed.emit(f"无法接收资源管理器所选文件：{exc}")
            return 0
        _controller.requested.emit(paths)
        return self._effect(effect)


class _ShellController(QObject):
    requested = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, application, mode="merge"):
        super().__init__(application)
        self.application = application
        self.mode = mode
        self.windows = []
        self.requested.connect(self.open_merge, Qt.ConnectionType.QueuedConnection)
        self.failed.connect(self.show_error, Qt.ConnectionType.QueuedConnection)
        self.idle_timer = QTimer(self)
        self.idle_timer.setSingleShot(True)
        self.idle_timer.setInterval(60000)
        self.idle_timer.timeout.connect(application.quit)
        self.idle_timer.start()

    def open_merge(self, paths):
        self.idle_timer.stop()
        if self.mode == "images":
            from app.imagequick import ImageQuickDialog
            window = ImageQuickDialog(paths, "pdf")
        else:
            from app.ui import MainWindow
            window = MainWindow(opening_paths=paths)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.windows.append(window)
        window.destroyed.connect(lambda _=None, w=window: self.forget(w))
        window.show()
        if self.mode != "images":
            window.updates.start()
            # COM 回调立即返回，读取和错误反馈交给窗口的正常加载流程。
            QTimer.singleShot(0, lambda: window.open_startup_files(paths, "merge"))

    def forget(self, window):
        self.windows.remove(window)
        if not self.windows:
            self.idle_timer.start()

    def show_error(self, message):
        QMessageBox.warning(None, "无法打开合并入口", message)
        if not self.windows:
            self.idle_timer.start()


def serve(application, *, clsid=None, mode="merge") -> int:
    global _controller
    pythoncom.CoInitialize()
    clsid = clsid or (IMAGE_PDF_CLSID if mode == "images" else PDF_MERGE_CLSID)
    MergeDropTarget._reg_clsid_ = clsid
    registrations = []
    application.setQuitOnLastWindowClosed(False)
    _controller = _ShellController(application, mode)
    pump = QTimer(_controller)
    pump.setInterval(20)
    pump.timeout.connect(pythoncom.PumpWaitingMessages)
    try:
        registrations = factory.RegisterClassFactories([clsid])
        pythoncom.CoResumeClassObjects()
        pump.start()
        return application.exec()
    finally:
        pump.stop()
        factory.RevokeClassFactories(registrations)
        _controller = None
        pythoncom.CoUninitialize()
