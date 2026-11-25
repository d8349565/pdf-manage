"""Download a tagged GitHub installer with Qt's Windows TLS backend and verify it before handoff."""
import hashlib
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from app import APP_SLUG, __version__
from app.i18n import tr
from app.runtime import installed_directory
from app.storage import data_directory
from app.updates import InstallerAsset, RELEASES_URL, installer_names, newer_than_current

MAX_INSTALLER = 512 * 1024 * 1024
MAX_MANIFEST = 64 * 1024
CDN_HOSTS = {'release-assets.githubusercontent.com', 'objects.githubusercontent.com', 'github-releases.githubusercontent.com'}


def asset_url(tag, name):
    allowed = installer_names(tag)
    if name not in (*allowed, 'SHA256SUMS.txt'):
        raise ValueError(tr('安装包信息不完整。'))
    return QUrl(f'{RELEASES_URL}/download/{tag}/{quote(name)}')


def allowed_redirect(url, original):
    return (url.isValid() and url.scheme() == 'https' and not url.userInfo() and url.port(443) == 443
            and not url.hasFragment() and (url.host() in CDN_HOSTS or url == original))


def manifest_digest(data, name):
    matches = [line.split(None, 1)[0].lower() for line in data.decode('utf-8-sig').splitlines()
               if re.fullmatch(r'[a-fA-F0-9]{64}[ \t]+\*?' + re.escape(name), line)]
    if len(matches) != 1:
        raise ValueError(tr('无法核对安装包的 SHA-256 校验值。'))
    return matches[0]


def installer_cache():
    return data_directory() / 'updates'


def open_for_launch(path):
    if os.name != 'nt':
        return path.open('rb')
    import _winapi
    import msvcrt
    # FILE_SHARE_READ alone prevents replacement or writes between hashing and CreateProcess.
    handle = _winapi.CreateFile(str(path), _winapi.GENERIC_READ, 1, 0, _winapi.OPEN_EXISTING, 0, 0)
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        _winapi.CloseHandle(handle)
        raise
    return os.fdopen(descriptor, 'rb')


def clear_installed_packages():
    """After a successful update, remove only this updater's obsolete packages."""
    if installed_directory() is None:
        return
    try:
        for file in installer_cache().glob(f'{APP_SLUG}-Setup-v*.exe'):
            match = re.fullmatch(re.escape(APP_SLUG) + r'-Setup-(v[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5})\.exe', file.name)
            if match and not newer_than_current(match[1]):
                file.unlink(missing_ok=True)
    except OSError:
        pass  # Setup may still hold the executable while the new reader starts.


@dataclass(frozen=True)
class DownloadedInstaller:
    asset: InstallerAsset
    path: Path
    digest: str
    size: int

    def launch(self):
        location = installed_directory()
        if location is None or not newer_than_current(self.asset.tag):
            raise ValueError(tr('此更新仅适用于已安装的旧版本。'))
        expected = installer_cache() / f'{APP_SLUG}-Setup-v{self.asset.tag.removeprefix("v")}.exe'
        if self.path != expected or self.path.is_symlink():
            raise ValueError(tr('安装包校验失败，请重新下载。'))
        with open_for_launch(self.path) as stream:
            if os.fstat(stream.fileno()).st_size != self.size or stream.read(2) != b'MZ':
                raise ValueError(tr('安装包校验失败，请重新下载。'))
            stream.seek(0)
            if hashlib.file_digest(stream, 'sha256').hexdigest() != self.digest:
                raise ValueError(tr('安装包校验失败，请重新下载。'))
            subprocess.Popen([str(self.path), '/SP-', '/SILENT', '/NORESTART', '/NOCLOSEAPPLICATIONS',
                              '/NORESTARTAPPLICATIONS', f'/DIR={location}', f'/ZhiXuUpdatePid={os.getpid()}'],
                             cwd=str(self.path.parent), close_fds=True,
                             env={**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'})


class InstallerTransfer(QObject):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal()

    def __init__(self, asset, folder, parent=None):
        super().__init__(parent)
        self.asset, self.folder = asset, Path(folder)
        self.manager = QNetworkAccessManager(self)
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(lambda: self.finish(tr('下载超时。')))
        self.reply = self.stream = self.partial = self.result = None
        self.received = self.redirects = 0
        self.expected = asset.size
        self.digest = asset.sha256
        self.hash = hashlib.sha256()
        self.manifest = bytearray()
        self.error = ''
        self.done = self.cancelled = False

    def start(self):
        if self.done or self.reply is not None:
            return
        try:
            if self.asset.name not in installer_names(self.asset.tag) or not 0 <= self.expected <= MAX_INSTALLER:
                raise ValueError(tr('安装包信息不完整。'))
            if self.digest and not re.fullmatch('[0-9a-f]{64}', self.digest):
                raise ValueError(tr('无法核对安装包的 SHA-256 校验值。'))
            self.deadline.start(20 * 60 * 1000)
            self.begin_file()
        except (OSError, ValueError) as exc:
            self.finish(str(exc))

    def begin_file(self):
        self.redirects = 0
        if self.digest:
            self.folder.mkdir(parents=True, exist_ok=True)
            stream = tempfile.NamedTemporaryFile(prefix='.installer-', suffix='.part', dir=self.folder, delete=False)
            self.stream, self.partial = stream, Path(stream.name)
        self.original = asset_url(self.asset.tag, self.asset.name if self.digest else 'SHA256SUMS.txt')
        self.request(self.original)

    def request(self, url):
        request = QNetworkRequest(url)
        request.setRawHeader(b'User-Agent', f'{APP_SLUG}/{__version__}'.encode('ascii'))
        request.setRawHeader(b'Accept-Encoding', b'identity')
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute, QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setTransferTimeout(30000)
        reply = self.reply = self.manager.get(request)
        reply.setReadBufferSize(256 * 1024)
        reply.readyRead.connect(lambda: self.read(reply))
        reply.finished.connect(lambda: self.complete_request(reply))

    def read(self, reply):
        if reply is not self.reply or self.done:
            return
        try:
            data = bytes(reply.readAll())
            if reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) != 200:
                return
            if not self.digest:
                if len(self.manifest) + len(data) > MAX_MANIFEST:
                    raise ValueError(tr('无法核对安装包的 SHA-256 校验值。'))
                self.manifest.extend(data)
                return
            length = reply.header(QNetworkRequest.KnownHeaders.ContentLengthHeader)
            if length is not None:
                length = int(length)
                if not 0 < length <= MAX_INSTALLER or self.expected and length != self.expected:
                    raise ValueError(tr('安装包大小与发布记录不符。'))
                self.expected = length
            self.received += len(data)
            if self.received > (self.expected or MAX_INSTALLER):
                raise ValueError(tr('安装包大小与发布记录不符。'))
            if self.stream.write(data) != len(data):
                raise OSError(tr('无法写入下载文件。'))
            self.hash.update(data)
            self.progress.emit(self.received, self.expected)
        except (ValueError, OSError) as exc:
            self.finish(str(exc))

    def complete_request(self, reply):
        if reply is not self.reply or self.done:
            return
        self.read(reply)
        if self.done:
            return
        self.reply = None
        try:
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise ValueError(tr('无法从 GitHub 获取更新文件。'))
            if status in (301, 302, 303, 307, 308):
                location = reply.attribute(QNetworkRequest.Attribute.RedirectionTargetAttribute)
                url = reply.url().resolved(location) if location else QUrl()
                self.redirects += 1
                if self.redirects > 5 or not allowed_redirect(url, self.original):
                    raise ValueError(tr('GitHub 下载地址无效。'))
                self.request(url)
            elif status != 200:
                raise ValueError(tr('无法从 GitHub 获取更新文件。'))
            elif not self.digest:
                self.digest = manifest_digest(self.manifest, self.asset.name)
                self.begin_file()
            else:
                if not self.received or self.expected and self.received != self.expected or self.hash.hexdigest() != self.digest:
                    raise ValueError(tr('安装包校验失败，请重新下载。'))
                self.stream.close()
                self.stream = None
                with self.partial.open('rb') as package:
                    if package.read(2) != b'MZ':
                        raise ValueError(tr('安装包校验失败，请重新下载。'))
                target = self.folder / f'{APP_SLUG}-Setup-v{self.asset.tag.removeprefix("v")}.exe'
                self.partial.replace(target)
                self.partial = None
                self.result = DownloadedInstaller(self.asset, target, self.digest, self.received)
                self.finish()
        except (ValueError, OSError) as exc:
            self.finish(str(exc))
        finally:
            reply.deleteLater()

    def cancel(self):
        if not self.done:
            self.cancelled = True
            self.finish()

    def finish(self, error=''):
        if self.done:
            return
        self.done, self.error = True, error
        self.deadline.stop()
        reply, self.reply = self.reply, None
        if reply is not None:
            reply.abort()
            reply.deleteLater()
        try:
            if self.stream is not None:
                self.stream.close()
        except OSError as exc:
            self.error = self.error or str(exc)
        finally:
            try:
                if self.partial is not None:
                    self.partial.unlink(missing_ok=True)
            except OSError:
                pass
            self.finished.emit()
