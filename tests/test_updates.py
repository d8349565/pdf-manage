"""Update boundaries; no live requests, downloads, registry writes or installers."""
import json
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtNetwork import QNetworkReply
from PyQt6.QtWidgets import QApplication

from app import APP_PROJECT_URL, APP_SLUG
from app import updates


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def checker(qapp, tmp_path):
    settings = QSettings(str(tmp_path / "updates.ini"), QSettings.Format.IniFormat)
    instance = updates.UpdateChecker(settings)
    yield instance
    instance.stop()


def release_payload(**changes):
    payload = dict(tag_name="v99999.0.0", draft=False, prerelease=False,
                   assets=[dict(name=f"{APP_SLUG}-Setup.exe", state="uploaded", size=100)])
    payload.update(changes)
    return json.dumps(payload).encode()


def test_version_comparison_uses_numbers():
    assert updates.version_parts("v0.5.10") > updates.version_parts("0.5.9")
    assert updates.version_parts("1.0.0") > updates.version_parts("v0.99.99")


def test_legacy_installer_name_matches_the_release_version():
    legacy = dict(name=f"{APP_SLUG}-Setup v99999.0.0.exe", state="uploaded", size=100)
    assert updates.release_tag(release_payload(assets=[legacy])) == "v99999.0.0"
    with pytest.raises(ValueError):
        updates.release_tag(release_payload(tag_name="v1.0.0", assets=[legacy]))


@pytest.mark.parametrize("tag", [None, 100, "v1.0", "v1.0.0-rc1", "v1.0.0/../../x", "v1.0.0?x=1",
                                    "https://example.com", "v100000.0.0", "v1.0.0\n"])
def test_untrusted_tags_rejected(tag):
    with pytest.raises(ValueError):
        updates.version_parts(tag)


@pytest.mark.parametrize("changes", [dict(draft=True), dict(prerelease=True), dict(prerelease=None),
                                        dict(assets=[]), dict(assets=None), dict(assets=[dict(name=[])]),
                                        dict(assets=[dict(name=f"{APP_SLUG}-Setup.exe", state="new", size=0)])])
def test_only_ready_stable_installers_are_offered(changes):
    with pytest.raises(ValueError):
        updates.release_tag(release_payload(**changes))


@pytest.mark.parametrize("payload", [b"null", b"[]", b"<html>error</html>", b"\xff",
                                        b"[" * 2000 + b"]" * 2000,
                                        b" " * (updates.MAX_RESPONSE_BYTES + 1)])
def test_invalid_or_excessive_metadata_is_recoverable(payload):
    with pytest.raises(ValueError):
        updates.release_tag(payload)


def test_auto_checks_require_opt_in_and_stop_when_disabled(checker, monkeypatch):
    calls = []
    monkeypatch.setattr(checker, "check", lambda: calls.append(True))
    assert checker.automatic is False
    checker.start()
    checker.check_if_due()
    assert calls == [] and checker._manager is None
    assert not checker._startup_timer.isActive() and not checker._automatic_timer.isActive()
    checker.set_automatic(True)
    assert checker._startup_timer.isActive() and checker._automatic_timer.isActive()
    checker.check_if_due()
    assert calls == [True]
    checker.set_automatic(False)
    checker.check_if_due()
    assert calls == [True]
    assert not checker._startup_timer.isActive() and not checker._automatic_timer.isActive()
    assert updates.UpdateChecker(checker.settings).automatic is False


def test_auto_checks_respect_interval_opt_out_and_clock_rollback(checker, monkeypatch):
    calls = []
    monkeypatch.setattr(updates.time, "time", lambda: 200000)
    monkeypatch.setattr(checker, "check", lambda: calls.append(True))
    checker.set_automatic(True)
    checker.settings.setValue("updates/lastAttempt", 199999)
    checker.check_if_due()
    assert calls == []
    checker.settings.setValue("updates/lastAttempt", 1)
    checker.set_automatic(False)
    checker.check_if_due()
    assert calls == []
    checker.set_automatic(True)
    checker.check_if_due()
    assert calls == [True]
    checker.settings.setValue("updates/lastAttempt", 200001)
    checker.check_if_due()
    assert calls == [True, True]


@pytest.mark.parametrize("status,error,expected", [
    (200, QNetworkReply.NetworkError.NoError, "发现新版本"),
    (403, QNetworkReply.NetworkError.ContentAccessDenied, "限制"),
    (404, QNetworkReply.NetworkError.ContentNotFoundError, "暂未找到"),
    (None, QNetworkReply.NetworkError.SslHandshakeFailedError, "验证失败"),
    (None, QNetworkReply.NetworkError.TimeoutError, "无法连接"),
    (302, QNetworkReply.NetworkError.NoError, "无法连接"),
])
def test_completion_recovers_and_only_caches_success(checker, status, error, expected):
    disposed = []
    checker._reply = SimpleNamespace(bytesAvailable=lambda: 0, attribute=lambda key: status,
                                     error=lambda: error, deleteLater=lambda: disposed.append(True))
    checker._buffer.extend(release_payload())
    checker.busy = True
    checker._finished()
    assert expected in checker.message
    assert checker.busy is False and checker._reply is None and disposed == [True]
    assert checker.update_available == (status == 200)
    assert bool(checker.settings.value("updates/latestTag", "")) == (status == 200)


def test_download_opens_only_fixed_repository_and_restores_notice(checker, monkeypatch):
    checker.latest_tag = updates.release_tag(release_payload(html_url="https://example.com/malware.exe"))
    checker.settings.setValue("updates/latestTag", checker.latest_tag)
    opened = []
    monkeypatch.setattr(updates.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    assert checker.open_release_page()
    assert opened == [f"{APP_PROJECT_URL}/releases/tag/v99999.0.0"]
    assert updates.UpdateChecker(checker.settings).update_available
    monkeypatch.setattr(updates.QDesktopServices, "openUrl", lambda url: False)
    assert not checker.open_release_page()
    assert "无法打开浏览器" in checker.message
