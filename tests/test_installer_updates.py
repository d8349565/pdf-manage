"""Installed-only update flow using isolated settings, files and controlled GitHub responses."""
import hashlib
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QObject, QSettings, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from app import APP_SLUG
from app import installerupdate as install
from app import updates
from app.updateui import UpdateDialog


DATA = b'MZ' + b'installer fixture' * 60
DIGEST = hashlib.sha256(DATA).hexdigest()
TAG = 'v9999.0.1'
NAME = f'{APP_SLUG}-Setup.exe'
ASSET = updates.InstallerAsset(TAG, NAME, len(DATA), DIGEST)


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


class Reply(QObject):
    readyRead = pyqtSignal()
    finished = pyqtSignal()

    def __init__(self, url, status=200, location=None, length=None):
        super().__init__()
        self.address, self.status, self.location, self.length = url, status, location, length
        self.data = b''

    def attribute(self, key):
        if key == QNetworkRequest.Attribute.HttpStatusCodeAttribute:
            return self.status
        if key == QNetworkRequest.Attribute.RedirectionTargetAttribute and self.location:
            return QUrl(self.location)

    def header(self, key):
        return self.length

    def readAll(self):
        data, self.data = self.data, b''
        return data

    def url(self):
        return self.address

    def error(self):
        return QNetworkReply.NetworkError.NoError

    def setReadBufferSize(self, size):
        pass

    def abort(self):
        self.finished.emit()

    def feed(self, data):
        self.data = data
        self.readyRead.emit()


def transfer_for(folder, asset=ASSET):
    transfer = install.InstallerTransfer(asset, folder)
    requests = []
    def request(req):
        assert req.attribute(QNetworkRequest.Attribute.RedirectPolicyAttribute) == QNetworkRequest.RedirectPolicy.ManualRedirectPolicy
        reply = Reply(req.url())
        requests.append(reply)
        return reply
    transfer.manager = SimpleNamespace(get=request)
    transfer.start()
    return transfer, requests


def test_redirect_manifest_and_assets_are_pinned_to_tag(qapp, tmp_path):
    transfer, replies = transfer_for(tmp_path, updates.InstallerAsset(TAG, NAME))
    manifest = replies[-1]
    assert manifest.url().toString().endswith(f'/{TAG}/SHA256SUMS.txt')
    manifest.feed(f'{DIGEST}  {NAME}\n'.encode())
    manifest.finished.emit()
    assert replies[-1].url().toString() == f'{updates.RELEASES_URL}/download/{TAG}/{NAME}'
    replies[-1].status, replies[-1].location = 302, 'https://release-assets.githubusercontent.com/asset?token=fixture'
    replies[-1].feed(b'redirect body')
    replies[-1].finished.emit()
    replies[-1].length = len(DATA)
    replies[-1].feed(DATA[:50])
    assert transfer.received == 50 and not list(tmp_path.glob('*.exe'))
    replies[-1].feed(DATA[50:])
    replies[-1].finished.emit()
    assert transfer.done and not transfer.error
    assert transfer.result.path.read_bytes() == DATA and transfer.result.digest == DIGEST


@pytest.mark.parametrize('problem', ['size', 'digest', 'cancel', 'timeout', 'unsafe redirect', 'not found', 'duplicate hash'])
def test_failed_or_cancelled_transfer_never_exposes_partial_exe(qapp, tmp_path, problem):
    asset = updates.InstallerAsset(TAG, NAME) if problem == 'duplicate hash' else ASSET
    transfer, replies = transfer_for(tmp_path, asset)
    reply = replies[0]
    if problem == 'duplicate hash':
        reply.feed((f'{DIGEST}  {NAME}\n' * 2).encode())
    elif problem == 'unsafe redirect':
        reply.status, reply.location = 302, 'https://release-assets.githubusercontent.com.example.org/file'
    elif problem == 'not found':
        reply.status = 404
    else:
        reply.feed(DATA[:20])
        if problem == 'cancel':
            transfer.cancel()
        elif problem == 'timeout':
            transfer.deadline.timeout.emit()
        elif problem == 'digest':
            reply.feed(b'x' * (len(DATA) - 20))
    reply.finished.emit()
    assert transfer.done and not transfer.result and (transfer.error or transfer.cancelled)
    assert len(replies) == 1 and not list(tmp_path.iterdir())


def test_downloaded_package_launch_rechecks_bytes_and_install_type(qapp, tmp_path, monkeypatch):
    transfer, replies = transfer_for(tmp_path)
    replies[0].feed(DATA)
    replies[0].finished.emit()
    package = transfer.result
    monkeypatch.setattr(install, 'installer_cache', lambda: tmp_path)
    monkeypatch.setattr(install, 'installed_directory', lambda: None)
    started = []
    def launch(args, **kwargs):
        assert kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'] == '1'
        started.append(args)
    monkeypatch.setattr(install.subprocess, 'Popen', launch)
    with pytest.raises(ValueError):
        package.launch()
    location = tmp_path / 'installed copy'
    monkeypatch.setattr(install, 'installed_directory', lambda: location)
    package.path.write_bytes(b'MZ' + b'X' * (len(DATA) - 2))
    with pytest.raises(ValueError):
        package.launch()
    assert not started
    package.path.write_bytes(DATA)
    package.launch()
    assert started[0][0] == str(package.path) and '/SILENT' in started[0] and '/NORESTART' in started[0]
    assert f'/DIR={location}' in started[0] and any(arg.startswith('/ZhiXuUpdatePid=') for arg in started[0])


def test_installed_dialog_downloads_only_after_confirm(qapp, tmp_path, monkeypatch):
    from app import updateui
    from app.progress import TaskProgressDialog
    host = QDialog()
    host.install_update = lambda package: None
    checker = updates.UpdateChecker(QSettings(str(tmp_path/'settings.ini'), QSettings.Format.IniFormat), host)
    monkeypatch.setattr(updates, 'installed_directory', lambda: tmp_path)
    checker.latest_tag, checker.last_check_succeeded, checker.installer = TAG, True, ASSET
    dialog = UpdateDialog(checker, host)
    completed = []
    def deliver(transfer):
        progress = transfer.parent()
        assert isinstance(progress, TaskProgressDialog) and progress.isVisible()
        assert progress.windowTitle() == '正在远程下载更新包'
        transfer.progress.emit(20, 100)
        assert progress.percent.text() == '20%'
        completed.append(ASSET)
        transfer.result = install.DownloadedInstaller(ASSET, tmp_path/'setup.exe', DIGEST, len(DATA))
        transfer.finish()
    monkeypatch.setattr(install, 'installer_cache', lambda: tmp_path)
    monkeypatch.setattr(install.InstallerTransfer, 'start', deliver)
    monkeypatch.setattr(updateui.QDesktopServices, 'openUrl', lambda *_: pytest.fail('No browser on automatic update'))
    try:
        dialog.show()
        qapp.processEvents()
        assert dialog.github_button.text() == '确定更新' and not completed
        dialog.github_button.click()
        assert completed == [ASSET] and dialog.package == checker.downloaded and not checker.downloading
    finally:
        dialog.close()
        host.close()


@pytest.mark.parametrize('failure', [False, True])
def test_download_cancel_or_network_failure_keeps_dialog_open(qapp, tmp_path, monkeypatch, failure):
    host = QDialog()
    host.install_update = lambda *_: pytest.fail('No install after cancellation or failure')
    checker = updates.UpdateChecker(QSettings(str(tmp_path/'settings.ini'), QSettings.Format.IniFormat), host)
    monkeypatch.setattr(updates, 'installed_directory', lambda: tmp_path)
    checker.latest_tag, checker.last_check_succeeded, checker.installer = TAG, True, ASSET
    monkeypatch.setattr(install, 'installer_cache', lambda: tmp_path)
    monkeypatch.setattr(install.InstallerTransfer, 'start', lambda transfer:
                        transfer.finish('下载失败') if failure else transfer.parent().cancel_button.click())
    dialog = UpdateDialog(checker, host)
    try:
        dialog.show()
        dialog.github_button.click()
        assert dialog.isVisible() and not dialog.package and not checker.downloaded
        if failure:
            assert '蓝奏云' in dialog.empty_detail.text() and dialog.lanzou_button.isEnabled()
            assert dialog.lanzou_button.property('manualFallback')
    finally:
        dialog.close()
        host.close()


def test_close_for_update_respects_unsaved_tabs_and_launch_failure(qapp, monkeypatch):
    from app.ui import MainWindow
    host = MainWindow()
    host.show()
    events = []
    package = SimpleNamespace(launch=lambda: events.append('launched'))
    monkeypatch.setattr(host, '_confirm_leave_all_tabs', lambda: False)
    host.install_update(package)
    assert host.isVisible() and not events
    monkeypatch.setattr(host, '_confirm_leave_all_tabs', lambda: True)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: events.append('warning'))
    def failed_launch():
        raise OSError('fixture')
    host.install_update(SimpleNamespace(launch=failed_launch))
    assert host.isVisible() and events == ['warning']
    host.install_update(package)
    assert not host.isVisible() and events == ['warning', 'launched']


def test_installed_detection_requires_the_registered_executable(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from app import APP_EXE_NAME, runtime
    registry = pytest.importorskip('winreg')
    exe = tmp_path / APP_EXE_NAME
    monkeypatch.setattr(runtime.sys, 'executable', str(exe))
    monkeypatch.setattr(registry, 'OpenKey', lambda *a: nullcontext('product-key'))
    monkeypatch.setattr(registry, 'QueryValueEx', lambda *a: (str(tmp_path), registry.REG_SZ))
    assert runtime.installed_directory() is None
    monkeypatch.setattr(runtime.sys, 'frozen', True, raising=False)
    assert runtime.installed_directory() == tmp_path
    marker = tmp_path / runtime.PORTABLE_MARKER
    marker.write_text('[Portable]')
    assert runtime.installed_directory() is None
    marker.unlink()
    monkeypatch.setattr(registry, 'QueryValueEx', lambda *a: (str(tmp_path/'other-install'), registry.REG_SZ))
    assert runtime.installed_directory() is None


@pytest.mark.parametrize('changed_tag', [False, True])
def test_confirm_from_about_refreshes_cached_tag_and_installs_only_consented_version(qapp, tmp_path, monkeypatch, changed_tag):
    from app.updateui import show_update_dialog
    host = QDialog()
    installed = []
    host.install_update = installed.append
    about = QDialog(host)
    checker = updates.UpdateChecker(QSettings(str(tmp_path/'prefs.ini'), QSettings.Format.IniFormat), host)
    monkeypatch.setattr(updates, 'installed_directory', lambda: tmp_path)
    monkeypatch.setattr(install, 'installer_cache', lambda: tmp_path)
    checker.latest_tag = TAG
    def refresh():
        checker.busy = True
        checker.changed.emit()
        checker.busy = False
        checker.latest_tag = 'v9999.0.2' if changed_tag else TAG
        checker.installer = updates.InstallerAsset(checker.latest_tag, NAME, len(DATA), DIGEST)
        checker.last_check_succeeded = True
        checker.changed.emit()
        if changed_tag:
            QTimer.singleShot(0, about.findChild(UpdateDialog).reject)
    monkeypatch.setattr(checker, 'check', refresh)
    def deliver(transfer):
        transfer.result = install.DownloadedInstaller(transfer.asset, tmp_path/'fixture.exe', DIGEST, len(DATA))
        transfer.finish()
    monkeypatch.setattr(install.InstallerTransfer, 'start', deliver)
    show_update_dialog(about, checker, confirm=True)
    qapp.processEvents()
    assert [package.asset.tag for package in installed] == ([] if changed_tag else [TAG])
    about.close()
    host.close()
