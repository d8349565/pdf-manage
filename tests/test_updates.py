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


@pytest.mark.parametrize("prerelease,latest,expected", [
    (True, "v0.10.2", False), (True, "v0.10.3", True), (True, "v0.10.4", True),
    (False, "v0.10.3", False), (False, "v0.10.4", True),
])
def test_preview_can_upgrade_to_the_same_core_stable_version(checker, monkeypatch, prerelease, latest, expected):
    monkeypatch.setattr(updates, "APP_VERSION_PARTS", (0, 10, 3))
    monkeypatch.setattr(updates, "APP_IS_PRERELEASE", prerelease)
    checker.latest_tag = latest
    assert checker.update_available is expected


def test_preview_release_metadata_is_never_offered_as_a_stable_update():
    with pytest.raises(ValueError, match="正式版本"):
        updates.release_info(release_payload(tag_name="v0.10.3-preview", prerelease=True))


def test_public_and_windows_version_metadata_share_one_source():
    from app import APP_IS_PRERELEASE, APP_VERSION_CORE, APP_VERSION_DISPLAY, APP_VERSION_PARTS, __version__
    assert APP_VERSION_CORE == __version__.split("-", 1)[0]
    assert APP_VERSION_PARTS == tuple(map(int, APP_VERSION_CORE.split(".")))
    assert len(APP_VERSION_PARTS) == 3 and all(0 <= value <= 65535 for value in APP_VERSION_PARTS)
    assert APP_IS_PRERELEASE == ("-" in __version__)
    assert ("预览版" in APP_VERSION_DISPLAY) == APP_IS_PRERELEASE


def test_legacy_installer_name_matches_the_release_version():
    for name in (
        f"{APP_SLUG}-Setup v99999.0.0.exe",
        f"{APP_SLUG}-Setup.v99999.0.0.exe",
        f"{APP_SLUG}-Setup.99999.0.0.exe",
    ):
        legacy = dict(name=name, state="uploaded", size=100)
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
                                        b" " * (updates.MAX_RESPONSE_BYTES + 1)],
                         ids=["null", "empty_list", "html", "invalid_utf8", "nested", "oversized"])
def test_invalid_or_excessive_metadata_is_recoverable(payload):
    with pytest.raises(ValueError):
        updates.release_tag(payload)


def test_auto_checks_default_on_and_stop_when_disabled(checker, monkeypatch):
    calls = []
    monkeypatch.setattr(checker, "check", lambda: calls.append(True))
    assert checker.automatic is True
    assert not checker._startup_timer.isActive() and not checker._automatic_timer.isActive()
    checker.start()
    assert checker._startup_timer.isActive() and checker._automatic_timer.isActive()
    checker.check_if_due()
    assert calls == [True] and checker._manager is None
    checker.set_automatic(False)
    checker.start()
    checker.check_if_due()
    assert calls == [True]
    assert not checker._startup_timer.isActive() and not checker._automatic_timer.isActive()
    assert updates.UpdateChecker(checker.settings).automatic is False
    checker.set_automatic(True)
    assert checker._startup_timer.isActive() and checker._automatic_timer.isActive()


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


def test_redirect_fallback_extracts_valid_tag_and_rejects_untrusted_locations():
    from PyQt6.QtCore import QUrl

    assert updates.release_tag_from_redirect(f"{APP_PROJECT_URL}/releases/tag/v1.2.3") == "v1.2.3"
    assert updates.release_tag_from_redirect(QUrl("/d8349565/pdf-manage/releases/tag/v0.6.0")) == "v0.6.0"
    with pytest.raises(ValueError, match="暂未找到"):
        updates.release_tag_from_redirect(f"{APP_PROJECT_URL}/releases")
    for bad in (None, "", "https://evil.example/releases/tag/v1.0.0",
                f"{APP_PROJECT_URL}/releases/tag/v1.0.0?x=1",
                f"{APP_PROJECT_URL}/releases/tag/v1.0.0/../v2.0.0"):
        with pytest.raises(ValueError):
            updates.release_tag_from_redirect(bad)


def test_rate_limit_falls_back_to_release_redirect(checker):
    from PyQt6.QtCore import QUrl
    from PyQt6.QtNetwork import QNetworkRequest

    disposed = []
    head_urls = []

    def fake_head(request):
        head_urls.append(request.url().toString())
        attrs = {
            QNetworkRequest.Attribute.HttpStatusCodeAttribute: 302,
            QNetworkRequest.Attribute.RedirectionTargetAttribute: QUrl(f"{APP_PROJECT_URL}/releases/tag/v99999.0.0"),
        }
        return SimpleNamespace(
            bytesAvailable=lambda: 0,
            attribute=lambda key: attrs.get(key),
            error=lambda: QNetworkReply.NetworkError.NoError,
            deleteLater=lambda: disposed.append("head"),
            setReadBufferSize=lambda size: None,
            readyRead=SimpleNamespace(connect=lambda fn: None),
            finished=SimpleNamespace(connect=lambda fn: None),
        )

    checker._manager = SimpleNamespace(head=fake_head)
    checker._reply = SimpleNamespace(
        bytesAvailable=lambda: 0,
        attribute=lambda key: 403,
        error=lambda: QNetworkReply.NetworkError.ContentAccessDenied,
        deleteLater=lambda: disposed.append("api"),
    )
    checker.busy = True
    checker._finished()
    assert checker._fallback is True and checker.busy is True
    assert head_urls == [f"{APP_PROJECT_URL}/releases/latest"]
    assert disposed == ["api"]

    checker._finished()
    assert checker._fallback is False and checker.busy is False
    assert checker.latest_tag == "v99999.0.0"
    assert "发现新版本 v99999.0.0" in checker.message
    assert disposed == ["api", "head"]


def test_release_notes_are_optional_bounded_and_do_not_weaken_asset_validation():
    info = updates.release_info(release_payload(body="- 新增：软件内查看更新内容。", published_at="2026-10-26T12:21:34Z"))
    assert info.notes == "- 新增：软件内查看更新内容。"
    assert info.published_at == "2026-10-26T12:21:34Z"
    assert updates.release_info(release_payload(body=None, published_at=[])).notes == ""
    assert updates.release_info(release_payload(body={"html": "unexpected"})).notes == ""
    long = updates.release_info(release_payload(body="文" * (updates.MAX_NOTES_CHARS + 1)))
    assert "完整发布说明" in long.notes and len(long.notes) < updates.MAX_NOTES_CHARS + 100
    with pytest.raises(ValueError):
        updates.release_info(release_payload(body=info.notes, assets=[]))


def test_notes_cache_matches_its_tag_and_survives_failed_refresh(checker):
    checker._reply = SimpleNamespace(bytesAvailable=lambda: 0, attribute=lambda key: 200,
                                     error=lambda: QNetworkReply.NetworkError.NoError, deleteLater=lambda: None)
    checker._buffer.extend(release_payload(body="- 修复：更新窗口可读。", published_at="2026-10-26T12:21:34Z"))
    checker._finished()
    restored = updates.UpdateChecker(checker.settings)
    assert restored.release_notes == "- 修复：更新窗口可读。"
    assert restored.published_at == "2026-10-26T12:21:34Z"
    checker._reply = SimpleNamespace(bytesAvailable=lambda: 0, attribute=lambda key: 404,
                                     error=lambda: QNetworkReply.NetworkError.ContentNotFoundError, deleteLater=lambda: None)
    checker._finished()
    assert checker.error and checker.release_notes == restored.release_notes
    checker.settings.setValue("updates/latestTag", "v99999.0.1")
    mismatched = updates.UpdateChecker(checker.settings)
    assert mismatched.release_notes == "" and mismatched.published_at == ""


def test_redirect_fallback_never_attaches_old_notes_to_a_new_version(checker):
    from PyQt6.QtCore import QUrl
    from PyQt6.QtNetwork import QNetworkRequest

    checker.latest_tag = "v99998.0.0"
    checker.release_notes = "上一个版本的说明"
    checker.published_at = "2026-10-25T00:00:00Z"
    checker._fallback = True
    attrs = {
        QNetworkRequest.Attribute.HttpStatusCodeAttribute: 302,
        QNetworkRequest.Attribute.RedirectionTargetAttribute: QUrl(f"{APP_PROJECT_URL}/releases/tag/v99999.0.0"),
    }
    checker._reply = SimpleNamespace(bytesAvailable=lambda: 0, attribute=lambda key: attrs.get(key),
                                     error=lambda: QNetworkReply.NetworkError.NoError, deleteLater=lambda: None)
    checker._finished()
    assert checker.latest_tag == "v99999.0.0"
    assert checker.release_notes == "" and checker.published_at == ""
    assert updates.UpdateChecker(checker.settings).release_notes == ""


def test_release_entries_preserve_unfamiliar_copy_and_continuation_lines():
    from app.updateui import release_entries

    notes = "## 本次更新\n\n一段没有分类的说明。\n- 新增：第一行\n  补充条件。\n- 说明：需要组件。\n- 其他未分类内容"
    entries = release_entries(notes)
    assert ("新增", "第一行\n  补充条件。") in entries
    assert ("说明", "需要组件。") in entries
    assert "一段没有分类的说明。" in "\n".join(text for _, text in entries)
    assert ("", "其他未分类内容") in entries


def test_update_dialog_reads_safe_text_and_recovers_between_states(checker, qapp):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QLabel
    from app.updateui import UpdateDialog

    checker.latest_tag = "v99999.0.0"
    checker.release_notes = '- 新增：<img src="file:///private.png">\n- 说明：需要独立组件。'
    checker.published_at = "2026-10-26T12:21:34Z"
    dialog = UpdateDialog(checker)
    dialog.show()
    qapp.processEvents()
    try:
        entries = dialog.findChildren(QLabel, "releaseEntry")
        assert all(label.textFormat() == Qt.TextFormat.PlainText for label in entries)
        assert any('file:///private.png' in label.text() for label in entries)
        assert dialog.scroll.isVisible() and not dialog.empty.isVisible()
        assert not dialog.precautions_body.isVisible()
        dialog.precautions_button.click()
        assert dialog.precautions_body.isVisible()
        checker.busy = True
        checker.changed.emit()
        assert dialog.scroll.isVisible() and not dialog.refresh_button.isEnabled()
        assert dialog.precautions_body.isVisible()
        checker.busy = False
        checker.error = "无法连接 GitHub"
        checker.changed.emit()
        assert dialog.error_label.isVisible() and "已保存" in dialog.error_label.text()
        checker.release_notes = ""
        checker.changed.emit()
        assert dialog.empty.isVisible() and "无法连接" in dialog.empty_detail.text()
        checker.busy = True
        checker.changed.emit()
        assert "正在获取" in dialog.empty_title.text()
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_open_update_dialog_follows_theme_without_losing_notes(checker, qapp):
    from app.themes import current_theme, theme_manager
    from app.updateui import UpdateDialog

    previous = current_theme().key
    checker.latest_tag = "v99999.0.0"
    checker.release_notes = "- 新增：更新内容。\n- 说明：保留完整使用限制。"
    dialog = UpdateDialog(checker)
    dialog.show()
    try:
        dialog.precautions_button.click()
        for key in ("night", "forest"):
            theme_manager().apply(key)
            assert current_theme().colors["surface"] in dialog.styleSheet()
            assert current_theme().colors["accent"] in dialog.styleSheet()
            assert dialog.precautions_button.isChecked()
            assert "保留完整使用限制" in dialog.precautions_preview.text()
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()
        theme_manager().apply(previous)

