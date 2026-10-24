"""Local, per-user/session/installation handoff; no background resident."""
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from PyQt6.QtCore import QObject, QLockFile, QStandardPaths, QTimer
from app.runtime import portable_data


def instance_scope():
    installation = (Path(sys.executable) if getattr(sys, 'frozen', False)
                    else Path(__file__).resolve().parent.parent)
    session = 0
    if sys.platform == 'win32':
        import ctypes
        value = ctypes.c_ulong()
        if ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(value)):
            session = value.value
    folder = portable_data() or Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    identity = f'{folder}:{session}:{os.path.normcase(str(installation))}'
    name = 'ZhiXuPDF-' + hashlib.sha256(identity.encode()).hexdigest()[:24]
    folder.mkdir(parents=True, exist_ok=True)
    return name, folder / (name + '.lock')


class InstanceRouter(QObject):
    def __init__(self, application):
        super().__init__(application)
        self.name, path = instance_scope()
        self.lock = QLockFile(str(path))
        self.lock.setStaleLockTime(0)
        self.server = None
        self.pending = deque()
        self.seen = deque(maxlen=256)
        self.window = None
        self.dispatching = False
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._drain)
        application.aboutToQuit.connect(self.close)

    def start_or_forward(self, paths):
        deadline = time.monotonic() + 5
        payload = None
        while True:
            if self.lock.tryLock(0):
                return True
            from PyQt6.QtNetwork import QLocalSocket
            if payload is None:
                import uuid
                payload = dict(version=1, id=uuid.uuid4().hex, paths=paths)
            socket = QLocalSocket()
            socket.connectToServer(self.name)
            if socket.waitForConnected(100):
                socket.write(json.dumps(payload, ensure_ascii=False).encode('utf-8') + b'\n')
                remaining = max(1, int((deadline - time.monotonic()) * 1000))
                socket.waitForBytesWritten(remaining)
                data = bytearray()
                while time.monotonic() < deadline:
                    data.extend(bytes(socket.readAll()))
                    if b'\n' in data:
                        socket.disconnectFromServer()
                        try:
                            response = json.loads(data.split(b'\n', 1)[0])
                        except (ValueError, UnicodeError):
                            break
                        if (isinstance(response, dict) and response.get('id') == payload['id']
                                and response.get('accepted')):
                            if sys.platform == 'win32' and isinstance(response.get('pid'), int):
                                import ctypes
                                ctypes.windll.user32.AllowSetForegroundWindow(response['pid'])
                            return False
                        break
                    socket.waitForReadyRead(max(1, int((deadline - time.monotonic()) * 1000)))
                socket.abort()
                raise RuntimeError('正在运行的纸序 PDF 未确认接收文件，请稍后重新打开。')
            socket.abort()
            if time.monotonic() >= deadline:
                raise RuntimeError('正在运行的纸序 PDF 暂无响应，请关闭该窗口后重新打开。')
            time.sleep(.02)

    def attach(self, window):
        from PyQt6.QtNetwork import QLocalServer
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._connected)
        if not self.server.listen(self.name):
            self.lock.unlock()
            raise RuntimeError('无法准备本机文件打开入口，请重新启动纸序 PDF。')
        self.window = window
        if self.pending:
            self.timer.start()

    def _connected(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            buffer = bytearray()
            timeout = QTimer(socket)
            timeout.setSingleShot(True)
            timeout.timeout.connect(socket.abort)
            timeout.start(5000)
            def read(sock=socket, data=buffer, expiry=timeout):
                data.extend(bytes(sock.readAll()))
                if len(data) > 1024 * 1024:
                    sock.abort()
                    return
                if b'\n' not in data:
                    return
                expiry.stop()
                try:
                    request = json.loads(data.split(b'\n', 1)[0])
                    paths, identifier = request['paths'], request['id']
                    valid = (request.get('version') == 1 and isinstance(identifier, str)
                             and len(identifier) == 32 and isinstance(paths, list)
                             and len(paths) <= 512 and all(isinstance(path, str) for path in paths)
                             and not getattr(self.window, '_closing', False))
                except (ValueError, UnicodeError, KeyError, TypeError):
                    valid, identifier = False, ''
                if valid and identifier not in self.seen:
                    self.seen.append(identifier)
                    self.pending.append(paths)
                    self.timer.start()
                sock.write(json.dumps(dict(id=identifier, accepted=valid, pid=os.getpid())).encode() + b'\n')
                sock.disconnectFromServer()
            socket.readyRead.connect(read)
            socket.disconnected.connect(socket.deleteLater)
            read()

    def _drain(self):
        window = self.window
        if window is None or self.dispatching:
            return
        from PyQt6.QtWidgets import QApplication
        if (getattr(window, '_closing', False) or window._opening_paths
                or getattr(window, '_task_dialog', None) is not None
                or QApplication.activeModalWidget() is not None):
            return
        if not self.pending:
            self.timer.stop()
            return
        paths = self.pending.popleft()
        self.dispatching = True
        try:
            if window.isMinimized():
                if window.isMaximized():
                    window.showMaximized()
                else:
                    window.showNormal()
            window.show()
            window.raise_()
            window.activateWindow()
            if paths:
                window.open_files_in_tabs(paths)
        finally:
            self.dispatching = False
        if not self.pending:
            self.timer.stop()

    def close(self):
        self.timer.stop()
        if self.server is not None:
            self.server.close()
        if self.lock.isLocked():
            self.lock.unlock()
