"""Read public Release metadata; downloads and installation stay in the browser."""
from __future__ import annotations

import json
import re
import time

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices

from app import APP_PROJECT_URL, APP_SLUG, __version__

RELEASES_URL = f"{APP_PROJECT_URL}/releases"
LATEST_RELEASE_API = "https://api.github.com/repos/" + APP_PROJECT_URL.split("github.com/", 1)[1] + "/releases/latest"
CHECK_INTERVAL = 24 * 60 * 60
MAX_RESPONSE_BYTES = 1024 * 1024


def version_parts(tag: str) -> tuple[int, int, int]:
    if not isinstance(tag, str) or not re.fullmatch(r"v?[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}", tag):
        raise ValueError("发布版本号格式不受支持，请打开发布页查看。")
    return tuple(map(int, tag.removeprefix("v").split(".")))


def release_tag(payload: bytes) -> str:
    """Only accept a stable release with a completed Windows installer upload."""
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("更新信息过大，请打开发布页查看。")
    try:
        release = json.loads(payload)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("无法读取更新信息，请稍后重试或打开发布页。") from exc
    if not isinstance(release, dict) or release.get("draft") is not False or release.get("prerelease") is not False:
        raise ValueError("暂未获取到正式版本，请打开发布页查看。")
    tag = release.get("tag_name")
    version_parts(tag)
    # Older Releases used a version suffix; current builds use the fixed name.
    installer_names = (f"{APP_SLUG}-Setup.exe", f"{APP_SLUG}-Setup v{tag.removeprefix('v')}.exe")
    assets = release.get("assets")
    if not isinstance(assets, list) or not any(
        isinstance(asset, dict) and asset.get("name") in installer_names
        and asset.get("state") == "uploaded" and type(asset.get("size")) is int and asset["size"] > 0
        for asset in assets
    ):
        raise ValueError("正式版安装包尚未就绪，请稍后重试或打开发布页。")
    return tag


class UpdateChecker(QObject):
    changed = pyqtSignal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.busy = False
        self.message = "点击检查最新正式版。"
        self.latest_tag = str(settings.value("updates/latestTag", ""))
        try:
            version_parts(self.latest_tag)
        except ValueError:
            self.latest_tag = ""
        self._manager = None
        self._reply = None
        self._buffer = bytearray()
        self._failure = ""
        self._automatic_timer = QTimer(self)
        self._automatic_timer.setInterval(60 * 60 * 1000)
        self._automatic_timer.timeout.connect(self.check_if_due)
        self._startup_timer = QTimer(self)
        self._startup_timer.setSingleShot(True)
        self._startup_timer.setInterval(15000)
        self._startup_timer.timeout.connect(self.check_if_due)
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(15000)
        self._deadline.timeout.connect(self._timeout)

    @property
    def update_available(self) -> bool:
        return bool(self.latest_tag) and version_parts(self.latest_tag) > version_parts(__version__)

    @property
    def automatic(self) -> bool:
        return self.settings.value("updates/automatic", False, type=bool)

    def set_automatic(self, enabled: bool) -> None:
        self.settings.setValue("updates/automatic", enabled)
        self.settings.sync()
        if enabled:
            self.start()
        else:
            self._startup_timer.stop()
            self._automatic_timer.stop()
        self.changed.emit()

    def start(self) -> None:
        # Called by production entry points, never implicitly by a test window.
        if not self.automatic:
            return
        self._startup_timer.start()
        self._automatic_timer.start()

    def stop(self) -> None:
        self._startup_timer.stop()
        self._automatic_timer.stop()
        if self._reply is not None:
            self._reply.abort()

    def check_if_due(self) -> None:
        if not self.automatic or self.busy:
            return
        try:
            last = float(self.settings.value("updates/lastAttempt", 0))
        except (TypeError, ValueError):
            last = 0
        elapsed = time.time() - last
        if 0 <= elapsed < CHECK_INTERVAL:
            return
        self.check()

    def check(self) -> None:
        if self.busy:
            return
        # Lazy import keeps the first window light; Qt uses Windows Schannel TLS.
        from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest

        if self._manager is None:
            self._manager = QNetworkAccessManager(self)
        request = QNetworkRequest(QUrl(LATEST_RELEASE_API))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"User-Agent", f"{APP_SLUG}/{__version__}".encode("ascii"))
        request.setRawHeader(b"X-GitHub-Api-Version", b"2026-03-10")
        # Fail closed on redirects; never follow a server-provided download URL.
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setTransferTimeout(10000)
        self.settings.setValue("updates/lastAttempt", time.time())
        self.settings.sync()
        self.busy = True
        self.message = "正在检查更新…"
        self._buffer.clear()
        self._failure = ""
        self._reply = self._manager.get(request)
        self._reply.setReadBufferSize(64 * 1024)
        self._reply.readyRead.connect(self._read_response)
        self._reply.finished.connect(self._finished)
        self._deadline.start()
        self.changed.emit()

    def _read_response(self) -> None:
        if self._reply.bytesAvailable():
            self._buffer.extend(bytes(self._reply.readAll()))
        if len(self._buffer) > MAX_RESPONSE_BYTES:
            self._failure = "更新信息过大，请打开发布页查看。"
            if not self._reply.isFinished():
                self._reply.abort()

    def _timeout(self) -> None:
        if self._reply is not None:
            self._failure = "检查超时，请稍后重试或打开发布页。"
            self._reply.abort()

    def _finished(self) -> None:
        from PyQt6.QtNetwork import QNetworkReply, QNetworkRequest

        self._deadline.stop()
        self._read_response()
        reply = self._reply
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        try:
            if self._failure:
                raise ValueError(self._failure)
            if status in (403, 429):
                raise ValueError("GitHub 暂时限制了请求，请稍后重试或打开发布页。")
            if status == 404:
                raise ValueError("暂未找到正式发布版本，请打开发布页查看。")
            if reply.error() == QNetworkReply.NetworkError.SslHandshakeFailedError:
                raise ValueError("安全连接验证失败，请检查系统时间或网络，或打开发布页。")
            if reply.error() != QNetworkReply.NetworkError.NoError or status != 200:
                raise ValueError("无法连接 GitHub，请检查网络后重试，或打开发布页。")
            self.latest_tag = release_tag(bytes(self._buffer))
            self.settings.setValue("updates/latestTag", self.latest_tag)
            self.settings.sync()
            self.message = (f"发现新版本 {self.latest_tag}。" if self.update_available
                            else f"当前已是最新版本（v{__version__}）。")
        except ValueError as exc:
            self.message = str(exc)
        finally:
            self._reply = None
            self._buffer.clear()
            reply.deleteLater()
            self.busy = False
            self.changed.emit()

    def open_release_page(self) -> bool:
        # Construct from the fixed repository and a validated tag, not html_url.
        url = f"{RELEASES_URL}/tag/{self.latest_tag}" if self.update_available else f"{RELEASES_URL}/latest"
        if QDesktopServices.openUrl(QUrl(url)):
            return True
        self.message = f"无法打开浏览器，请手动访问：{url}"
        self.changed.emit()
        return False
