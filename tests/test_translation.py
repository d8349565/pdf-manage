"""Offline unit and GUI integration tests for document translation (OpenAI-compatible & DeepSeek default)."""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from threading import Event
from time import monotonic
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from PyQt6.QtCore import QByteArray, QObject, QPoint, QPointF, QRect, QRectF, QSettings, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QImage, QTextCursor
from PyQt6.QtNetwork import QNetworkReply, QNetworkRequest
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QAbstractButton, QApplication, QGraphicsScene, QGraphicsView, QMenu

from app.themes import THEMES, theme_manager
from app.translation import keystore
from app.translation.client import (
    OpenAICompatibleClient,
    clean_model_output,
    extract_openai_content,
    friendly_http_error,
    parse_batch_output,
)
from app.translation.config import (
    DEEPSEEK_ENDPOINT,
    DEEPSEEK_MODELS,
    DEFAULT_PROVIDER,
    PRESETS,
    TranslationSettings,
    load_settings,
    normalize_endpoint,
    save_settings,
    validate_endpoint,
)
from app.translation.extractor import (
    CachedPageTranslation,
    PageExtraction,
    PageSegment,
    TranslationCache,
    extract_page_segments,
    extract_region_for_translation,
    group_segments_into_batches,
)


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeReply(QObject):
    readyRead = pyqtSignal()
    finished = pyqtSignal()

    def __init__(self, status: int, body: bytes, error=QNetworkReply.NetworkError.NoError, *, delay_ms: int = 10):
        super().__init__()
        self._status = status
        self._body = bytes(body)
        self._error = error
        self._finished = False
        self._aborted = False
        self._offset = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._complete)
        self._timer.start(delay_ms)

    def setReadBufferSize(self, _size: int) -> None:
        pass

    def bytesAvailable(self) -> int:
        return max(0, len(self._body) - self._offset)

    def readAll(self) -> QByteArray:
        chunk = self._body[self._offset:]
        self._offset = len(self._body)
        return QByteArray(chunk)

    def attribute(self, attr):
        if attr == QNetworkRequest.Attribute.HttpStatusCodeAttribute:
            return self._status
        return None

    def error(self):
        return self._error

    def isFinished(self) -> bool:
        return self._finished

    def abort(self) -> None:
        if not self._finished:
            self._aborted = True
            self._timer.stop()
            self._error = QNetworkReply.NetworkError.OperationCanceledError
            self._finished = True
            self.finished.emit()

    def _complete(self) -> None:
        if self._finished:
            return
        if self._body:
            self.readyRead.emit()
        self._finished = True
        self.finished.emit()


class _FakeNetworkManager:
    def __init__(self, responder):
        self.responder = responder
        self.requests: list[tuple[QNetworkRequest, dict]] = []

    def post(self, request: QNetworkRequest, data: QByteArray):
        payload = json.loads(bytes(data).decode('utf-8'))
        self.requests.append((request, payload))
        return self.responder(request, payload)


def _openai_ok_bytes(text: str) -> bytes:
    return json.dumps({'choices': [{'message': {'content': text}}]}).encode('utf-8')


def _wait_until(condition, *, timeout=3):
    deadline = monotonic() + timeout
    while not condition() and monotonic() < deadline:
        QTest.qWait(10)
    assert condition()


def _translation_reply(_request, payload):
    ids = re.findall(r'<<<SEG:([^\s>]+)>>>', payload['messages'][-1]['content'])
    result = '\n'.join(f'<<<SEG:{sid}>>>\n译文-{sid}\n<<<END>>>' for sid in ids) if ids else '单段译文'
    return _FakeReply(200, _openai_ok_bytes(result))


def _make_text_pdf(path: Path) -> Path:
    import pymupdf
    with pymupdf.open() as doc:
        p1 = doc.new_page(width=595, height=842)
        p1.insert_text((50, 36), 'ACME Technical Spec v2.4', fontsize=9)
        p1.insert_text((50, 120), 'Thermal Management Overview', fontsize=18)
        p1.insert_text((50, 170), 'Intake airflow velocity must remain above 2.4 m/s under continuous load.', fontsize=11)
        p1.insert_text((50, 230), 'Dual redundant thermistors monitor MOSFET bridge temperature accurately.', fontsize=11)
        p1.insert_text((280, 815), 'Page 1 / 2', fontsize=9)

        p2 = doc.new_page(width=595, height=842)
        p2.insert_text((50, 120), 'Second page paragraph for cache testing.', fontsize=11)
        doc.save(path)
    return path


def _make_scan_pdf(path: Path) -> Path:
    import pymupdf
    img = QImage(600, 800, QImage.Format.Format_RGB32)
    img.fill(Qt.GlobalColor.white)
    png = path.with_suffix('.png')
    assert img.save(str(png), 'PNG')
    with pymupdf.open() as doc:
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, filename=str(png))
        doc.save(path)
    return path


# --- 1. Config, DeepSeek default, URL security, DPAPI secret storage ---

def test_default_provider_is_deepseek_openai_compatible(tmp_path):
    settings = QSettings(str(tmp_path / 'prefs.ini'), QSettings.Format.IniFormat)
    cfg = load_settings(settings)
    assert DEFAULT_PROVIDER == 'deepseek'
    assert [preset.key for preset in PRESETS] == ['deepseek', 'custom']
    assert cfg.provider == 'deepseek'
    assert cfg.endpoint == 'https://api.deepseek.com/chat/completions'
    assert cfg.model == 'deepseek-flash'
    assert not cfg.is_local
    assert cfg.session_only_key
    assert not cfg.is_ready  # requires key + privacy consent


def test_endpoint_normalization_and_https_safety():
    assert normalize_endpoint('https://api.deepseek.com') == 'https://api.deepseek.com/chat/completions'
    assert normalize_endpoint('https://api.deepseek.com/v1') == 'https://api.deepseek.com/v1/chat/completions'
    url, local = validate_endpoint('https://api.deepseek.com')
    assert url == 'https://api.deepseek.com/chat/completions' and not local
    assert normalize_endpoint('https://gateway.example.invalid/compatible-mode/v4') == (
        'https://gateway.example.invalid/compatible-mode/v4/chat/completions'
    )
    assert normalize_endpoint('https://gateway.example.invalid/v1/?api-version=123') == (
        'https://gateway.example.invalid/v1/chat/completions?api-version=123'
    )

    local_url, is_local = validate_endpoint('http://127.0.0.1:11434/v1')
    assert local_url == 'http://127.0.0.1:11434/v1/chat/completions' and is_local

    with pytest.raises(ValueError, match='https://'):
        validate_endpoint('http://api.deepseek.com/v1/chat/completions')
    with pytest.raises(ValueError, match='OpenAI 兼容'):
        validate_endpoint('https://gateway.example.invalid/v1', provider='deepseek')
    assert validate_endpoint('https://api.deepseek.com/v1', provider='deepseek') == (DEEPSEEK_ENDPOINT, False)
    for invalid in ('https://[::1', 'https://api.example.invalid:abc', 'https://key@api.example.invalid/v1',
                    'https://api.example.invalid/v1#fragment'):
        with pytest.raises(ValueError):
            validate_endpoint(invalid)


@pytest.mark.parametrize('old_model', ['deepseek-chat', 'deepseek-reasoner', 'deepseek-v4-flash'])
def test_retired_model_is_migrated_only_on_official_deepseek_default(tmp_path, old_model):
    settings = QSettings(str(tmp_path / 'prefs.ini'), QSettings.Format.IniFormat)
    settings.setValue('translation/model', old_model)
    settings.setValue('translation/endpoint', 'https://api.deepseek.com/v1/chat/completions')
    assert load_settings(settings).model == 'deepseek-flash'
    assert load_settings(settings).endpoint == 'https://api.deepseek.com/chat/completions'

    settings.setValue('translation/endpoint', 'https://gateway.example.invalid/v1/chat/completions')
    assert load_settings(settings).model == old_model
    assert load_settings(settings).provider == 'custom'


@pytest.fixture
def windows_vault(monkeypatch):
    if sys.platform != 'win32':
        pytest.skip('Windows Credential Manager')
    from pywintypes import error

    entries = {}

    def read(target, _type, _flags):
        if target not in entries:
            raise error(1168, 'CredRead', 'not found')
        return entries[target]

    def write(credential, _flags):
        blob = credential['CredentialBlob']
        if not isinstance(blob, str):
            raise TypeError('Windows CredWrite requires a string')
        entries[credential['TargetName']] = {**credential, 'CredentialBlob': blob.encode('utf-16-le')}

    def clear(target, _type, _flags):
        read(target, _type, _flags)
        entries.pop(target)

    monkeypatch.setattr(keystore, 'win32cred', SimpleNamespace(
        CredRead=read, CredWrite=write, CredDelete=clear, CRED_TYPE_GENERIC=1, CRED_PERSIST_LOCAL_MACHINE=2,
    ))
    return entries


def test_secret_storage_defaults_to_memory_and_uses_windows_vault_when_requested(tmp_path, windows_vault):
    secret = 'translation-unit-test-token'
    ini = tmp_path / 'preferences.ini'
    settings = QSettings(str(ini), QSettings.Format.IniFormat)
    config = TranslationSettings(api_key=secret, privacy_accepted=True)
    save_settings(settings, config)
    assert not windows_vault and not load_settings(settings).api_key
    assert load_settings(settings, runtime_key=secret).is_ready

    save_settings(settings, replace(config, session_only_key=False))
    entry = next(iter(windows_vault.values()))
    assert entry['Persist'] == 2 and entry['CredentialBlob'] == secret.encode('utf-16-le')
    assert secret not in ini.read_text(encoding='utf-8')
    assert not settings.contains('translation/apiKeyProtected')
    assert load_settings(settings).api_key == secret

    settings.setValue('translation/endpoint', 'https://different.example.invalid/v1')
    assert not load_settings(settings).api_key and not load_settings(settings).is_ready
    save_settings(settings, config)
    assert not load_settings(settings).api_key and not windows_vault


@pytest.mark.parametrize('old_provider', ['openai', 'qwen', 'deepseek'])
def test_removed_or_customized_presets_keep_settings_and_key_only_for_same_origin(tmp_path, windows_vault, old_provider):
    settings = QSettings(str(tmp_path / 'existing.ini'), QSettings.Format.IniFormat)
    endpoint = 'https://old-gateway.example.invalid/v1/chat/completions'
    key = 'synthetic-legacy-service-key'
    previous = TranslationSettings(provider=old_provider, endpoint=endpoint, model='existing-model')
    settings.setValue('translation/provider', old_provider)
    settings.setValue('translation/endpoint', endpoint)
    settings.setValue('translation/model', previous.model)
    settings.setValue('translation/sessionOnlyKey', False)
    settings.setValue('translation/privacyAccepted', True)
    assert keystore.write_key(settings.fileName(), previous.credential_recipient, key)

    settings.setValue('translation/endpoint', 'https://different.example.invalid/v1')
    assert not load_settings(settings).api_key and not load_settings(settings).is_ready
    settings.setValue('translation/endpoint', endpoint)
    migrated = load_settings(settings)
    assert migrated.provider == 'custom'
    assert migrated.endpoint == endpoint and migrated.model == previous.model
    assert migrated.api_key == key and migrated.is_ready
    assert settings.value('translation/provider') == old_provider

    save_settings(settings, migrated)
    assert load_settings(settings) == migrated
    assert not keystore.read_key(settings.fileName(), previous.credential_recipient)
    assert key not in Path(settings.fileName()).read_text(encoding='utf-8')


def test_existing_dpapi_key_moves_out_of_settings_and_requires_renewed_consent(tmp_path, windows_vault):
    import win32crypt

    settings = QSettings(str(tmp_path / 'legacy.ini'), QSettings.Format.IniFormat)
    key = 'legacy-unit-test-key'
    protected = win32crypt.CryptProtectData(key.encode(), 'Test', None, None, None, 1)
    settings.setValue('translation/apiKeyProtected', 'dpapi:' + base64.b64encode(protected).decode())
    settings.setValue('translation/privacyAccepted', True)
    config = load_settings(settings)
    assert config.api_key == key and not config.is_ready
    assert not settings.contains('translation/apiKeyProtected')
    assert windows_vault and not load_settings(settings).privacy_accepted


def test_protection_failure_keeps_key_in_session_only(tmp_path, monkeypatch):
    monkeypatch.setattr(keystore, 'write_key', lambda *_: False)
    settings = QSettings(str(tmp_path / 'prefs.ini'), QSettings.Format.IniFormat)
    saved = save_settings(settings, TranslationSettings(api_key='sk-test-session', session_only_key=False, privacy_accepted=True))
    assert saved.is_ready and saved.session_only_key
    assert not settings.contains('translation/apiKeyProtected')
    assert not load_settings(settings).is_ready


def test_local_compatible_endpoint_is_ready_without_key_or_cloud_consent():
    cfg = TranslationSettings(provider='custom', endpoint='http://127.0.0.1:9000/v1', model='local-model')
    assert cfg.is_local
    assert cfg.is_ready


# --- 2. Protocol parsing & async QtNetwork client ---

def test_batch_parsing_and_think_stripping():
    raw = '<think>internal reasoning</think>\n<<<SEG:s1>>>\n第一段译文\n<<<END>>>\n<<<SEG:s2>>>\n第二段译文\n<<<END>>>'
    parsed = parse_batch_output(raw, ['s1', 's2'])
    assert parsed == {'s1': '第一段译文', 's2': '第二段译文'}
    assert clean_model_output('<think>abc</think>你好') == '你好'


def test_friendly_http_errors():
    assert 'API Key' in friendly_http_error(401, b'', '', is_local=False)
    assert '余额' in friendly_http_error(402, b'', '', is_local=False)
    assert '模型' in friendly_http_error(404, b'', '', is_local=False)
    assert '频繁' in friendly_http_error(429, b'', '', is_local=False)
    assert '重定向' in friendly_http_error(302, b'', '', is_local=False)
    assert '本机翻译服务' in friendly_http_error(None, b'', 'ConnectionRefusedError', is_local=True)
    with pytest.raises(ValueError, match='bad key'):
        extract_openai_content(json.dumps({'error': {'message': 'bad key'}}).encode('utf-8'))


def test_client_single_batch_redirect_policy_and_cancel(qapp):
    def responder(req, payload):
        assert req.attribute(QNetworkRequest.Attribute.RedirectPolicyAttribute) == QNetworkRequest.RedirectPolicy.ManualRedirectPolicy
        assert bytes(req.rawHeader(b'Authorization')).decode('utf-8') == 'Bearer sk-unit'
        user_msg = payload['messages'][-1]['content']
        if '<<<SEG:s1>>>' in user_msg:
            return _FakeReply(200, _openai_ok_bytes('<<<SEG:s1>>>\n段落一\n<<<END>>>\n<<<SEG:s2>>>\n段落二\n<<<END>>>'))
        return _FakeReply(200, _openai_ok_bytes('你好世界'))

    mgr = _FakeNetworkManager(responder)
    client = OpenAICompatibleClient(network_manager=mgr)
    cfg = TranslationSettings(api_key='sk-unit', privacy_accepted=True)

    got = []
    client.translate_text(cfg, 'Hello world', on_success=lambda t, ms: got.append(t), on_error=lambda e: got.append(('err', e)))
    import time
    for _ in range(40):
        qapp.processEvents()
        if got:
            break
        time.sleep(0.01)
    assert got == ['你好世界']

    batch_got = []
    client.translate_batch(
        cfg, [('s1', 'One'), ('s2', 'Two')],
        on_success=lambda m, ms: batch_got.append(m),
        on_error=lambda e: batch_got.append(('err', e)),
    )
    for _ in range(40):
        qapp.processEvents()
        if batch_got:
            break
        time.sleep(0.01)
    assert batch_got == [{'s1': '段落一', 's2': '段落二'}]

    # Cancellation suppresses callback.
    slow_mgr = _FakeNetworkManager(lambda r, p: _FakeReply(200, _openai_ok_bytes('late'), delay_ms=200))
    slow_client = OpenAICompatibleClient(network_manager=slow_mgr)
    cancelled_out = []
    handle = slow_client.translate_text(cfg, 'Cancel me', on_success=lambda t, ms: cancelled_out.append(t), on_error=lambda e: cancelled_out.append(e))
    handle.cancel()
    qapp.processEvents()
    assert cancelled_out == []


@pytest.mark.parametrize('provider, model, endpoint, host, path, limit_name, limit', [
    ('deepseek', 'deepseek-flash', DEEPSEEK_ENDPOINT, 'api.deepseek.com', '/chat/completions', 'max_tokens', 64),
    ('deepseek', 'deepseek-v4-pro', 'https://api.deepseek.com/v1', 'api.deepseek.com', '/chat/completions', 'max_tokens', 64),
    ('custom', 'cloud-model', 'https://api.openai.com/v1', 'api.openai.com', '/v1/chat/completions', 'max_completion_tokens', 1024),
    ('custom', 'local-model', 'http://127.0.0.1:9000/v1', '127.0.0.1', '/v1/chat/completions', 'max_tokens', 64),
    ('custom', 'translation-model', 'https://gateway.example.invalid/proxy/v1', 'gateway.example.invalid',
     '/proxy/v1/chat/completions', 'max_tokens', 64),
])
def test_provider_test_connection_and_translation_payload(qapp, provider, model, endpoint, host, path, limit_name, limit):
    local = host == '127.0.0.1'
    config = TranslationSettings(
        provider=provider, endpoint=endpoint, model=model,
        api_key='' if local else 'sk-test', privacy_accepted=not local,
    )
    manager = _FakeNetworkManager(_translation_reply)
    client = OpenAICompatibleClient(network_manager=manager)
    got = []
    client.test_connection(config, on_success=lambda text, _: got.append(text), on_error=got.append)
    client.translate_text(config, 'Actual translation input', on_success=lambda text, _: got.append(text), on_error=got.append)
    _wait_until(lambda: len(got) == 2)
    assert got == ['单段译文', '单段译文']
    for request, payload in manager.requests:
        assert (request.url().host(), request.url().path()) == (host, path)
        assert bytes(request.rawHeader(b'Authorization')) == (b'' if local else b'Bearer sk-test')
        assert payload['model'] == model and not payload['stream']
        assert 'temperature' not in payload
        if provider == 'deepseek':
            assert payload['thinking'] == {'type': 'disabled'}
        else:
            assert 'thinking' not in payload and 'reasoning_effort' not in payload
    _, probe = manager.requests[0]
    _, translation = manager.requests[1]
    assert probe['messages'][-1]['content'] == 'Hello'
    assert translation['messages'][-1]['content'] == 'Actual translation input'
    assert probe[limit_name] == limit
    assert 'max_tokens' not in translation and 'max_completion_tokens' not in translation
    if limit_name == 'max_completion_tokens':
        assert 'max_tokens' not in probe


def test_key_appears_only_in_the_approved_https_header_and_is_hidden_in_provider_errors(qapp):
    secret = 'unit-credential-for-approved-endpoint'
    manager = _FakeNetworkManager(lambda *_: _FakeReply(400, json.dumps({
        'error': {'message': f'Invalid authentication echo {secret}'},
    }).encode()))
    client = OpenAICompatibleClient(network_manager=manager)
    config = TranslationSettings(api_key=secret, privacy_accepted=True)
    errors = []
    for risky in (
        replace(config, endpoint='https://gateway.example.invalid/v1'),
        replace(config, provider='custom', endpoint=f'https://gateway.example.invalid/path/{secret}'),
        replace(config, provider='custom', endpoint='https://gateway.example.invalid/v1?api_key=some-value'),
        replace(config, provider='custom', endpoint='http://127.0.0.1:9000/v1'),
        replace(config, model=secret),
    ):
        client.test_connection(risky, on_success=lambda *_: pytest.fail('unexpected request'), on_error=errors.append)
    client.translate_text(config, f'API key: {secret}', on_success=lambda *_: pytest.fail('unexpected request'), on_error=errors.append)
    assert not manager.requests and len(errors) == 6
    client.test_connection(config, on_success=lambda *_: pytest.fail('unexpected success'), on_error=errors.append)
    _wait_until(lambda: len(errors) == 7)
    request, body = manager.requests[0]
    assert request.url().scheme() == 'https' and request.url().host() == 'api.deepseek.com'
    assert bytes(request.rawHeader(b'Authorization')) == f'Bearer {secret}'.encode()
    assert request.attribute(QNetworkRequest.Attribute.RedirectPolicyAttribute) == QNetworkRequest.RedirectPolicy.ManualRedirectPolicy
    assert secret not in request.url().toString() and secret not in json.dumps(body)
    assert '[API Key]' in errors[-1] and secret not in errors[-1]


def test_long_selection_is_sent_in_full_and_incomplete_responses_are_errors(qapp):
    source = 'long text ' * 1000 + 'last line'
    manager = _FakeNetworkManager(lambda *_: _FakeReply(200, json.dumps({
        'choices': [{'message': {'content': 'incomplete'}, 'finish_reason': 'length'}],
    }).encode()))
    client = OpenAICompatibleClient(network_manager=manager)
    config = TranslationSettings(api_key='sk-test', privacy_accepted=True)
    successes, errors = [], []
    client.translate_text(config, source, on_success=lambda text, _: successes.append(text), on_error=errors.append)
    _wait_until(lambda: bool(errors))
    assert manager.requests[0][1]['messages'][-1]['content'] == source
    assert not successes and '截断' in errors[0]
    with pytest.raises(ValueError, match='翻译结果为空'):
        extract_openai_content(_openai_ok_bytes('<think>only reasoning</think>'))


def test_transport_blocks_send_before_consent_and_surfaces_timeout(qapp, monkeypatch):
    from app.translation import client as translation_client

    manager = _FakeNetworkManager(lambda *_: _FakeReply(200, _openai_ok_bytes('late'), delay_ms=300))
    client = OpenAICompatibleClient(network_manager=manager)
    config = TranslationSettings(api_key='sk-test')
    successes, errors = [], []
    client.translate_text(config, 'Test text', on_success=lambda text, _: successes.append(text), on_error=errors.append)
    assert errors and not manager.requests
    monkeypatch.setattr(translation_client, 'TEST_TIMEOUT_MS', 10)
    client.test_connection(replace(config, privacy_accepted=True),
                           on_success=lambda text, _: successes.append(text), on_error=errors.append)
    _wait_until(lambda: len(errors) == 2)
    assert '超时' in errors[-1]
    assert not successes and not client._active


# --- 3. Layout extraction, scan detection, region OCR routing, cache ---

def test_extract_page_segments_filters_header_footer_and_detects_scan(tmp_path):
    from app.pdfio import PdfStore
    store = PdfStore()
    try:
        text_pdf = _make_text_pdf(tmp_path / 'spec.pdf')
        pages = store.load(str(text_pdf))
        ext = extract_page_segments(store, pages[0], skip_header_footer=True)
        assert not ext.is_scan
        assert ext.skipped_count >= 2
        joined = '\n'.join(seg.text for seg in ext.segments)
        assert 'Thermal Management Overview' in joined
        assert 'Intake airflow velocity' in joined
        assert 'Page 1 / 2' not in joined

        ext_all = extract_page_segments(store, pages[0], skip_header_footer=False)
        assert len(ext_all.segments) > len(ext.segments)

        scan_pdf = _make_scan_pdf(tmp_path / 'scan.pdf')
        scan_pages = store.load(str(scan_pdf))
        scan_ext = extract_page_segments(store, scan_pages[0])
        assert scan_ext.is_scan
        assert scan_ext.segments == ()
    finally:
        store.close_all()


def test_region_extraction_native_vs_ocr_fallback(tmp_path, monkeypatch):
    from app.ocr.contracts import OCRError, OCRLine
    from app.pdfio import PdfStore
    from app.translation import extractor

    store = PdfStore()
    try:
        text_pdf = _make_text_pdf(tmp_path / 'region-text.pdf')
        pages = store.load(str(text_pdf))
        native = extract_region_for_translation(store, pages[0], 0, (40, 100, 520, 200))
        assert native.source == 'text'
        assert 'Thermal Management' in native.text

        scan_pdf = _make_scan_pdf(tmp_path / 'region-scan.pdf')
        scan_pages = store.load(str(scan_pdf))
        monkeypatch.setattr(
            extractor,
            '_run_local_ocr_on_image',
            lambda image, page_index, cancel=None: [
                OCRLine('Clause 1 Lead Time', (10, 10, 200, 30), (10, 10, 200, 10, 200, 30, 10, 30), 0.98, page_index)
            ],
        )
        ocr_res = extract_region_for_translation(store, scan_pages[0], 0, (20, 20, 300, 200))
        assert ocr_res.source == 'ocr'
        assert 'Clause 1 Lead Time' in ocr_res.text

        def raise_missing(*_a, **_kw):
            raise OCRError('尚未安装 OCR 增强包，请选择本地离线增强包安装。')
        monkeypatch.setattr(extractor, '_run_local_ocr_on_image', raise_missing)
        missing_res = extract_region_for_translation(store, scan_pages[0], 0, (20, 20, 300, 200))
        assert missing_res.ocr_missing
        assert not missing_res.text
    finally:
        store.close_all()


def test_batch_grouping_and_cache_lru():
    segs = [PageSegment(f's{i}', 'body', f'段 {i}', 'word ' * 120, (0, i * 20, 100, i * 20 + 15)) for i in range(8)]
    batches = group_segments_into_batches(segs, max_items=3, max_chars=1500)
    assert len(batches) >= 3
    assert sum(len(b) for b in batches) == 8

    cache = TranslationCache(max_entries=2)
    ext = PageExtraction(())
    cache.put(('k1', 'a'), CachedPageTranslation(ext, {'s1': '一'}))
    cache.put(('k2', 'a'), CachedPageTranslation(ext, {'s1': '二'}))
    cache.get(('k1', 'a'))  # refresh k1
    cache.put(('k3', 'a'), CachedPageTranslation(ext, {'s1': '三'}))
    assert cache.get(('k2', 'a')) is None
    assert cache.get(('k1', 'a')) is not None
    cache.invalidate_for_page('k1')
    assert cache.get(('k1', 'a')) is None


# --- 4. Cold startup isolation & MainWindow GUI end-to-end ---

def test_cold_window_does_not_eagerly_import_translation_or_qtnetwork(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            '-c',
            """
import sys
from pathlib import Path
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(Path(sys.argv[1])))
from app.ui import MainWindow
app = QApplication([])
win = MainWindow()
win.show()
app.processEvents()
assert 'app.translation' not in sys.modules
assert 'app.translation.ui' not in sys.modules
assert 'PyQt6.QtNetwork' not in sys.modules
assert 'pymupdf' not in sys.modules
win.close()
app.processEvents()
""",
            str(tmp_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows DLL search')
def test_source_qt_tls_does_not_load_poppler_openssl_from_inherited_path():
    from os import pathsep

    poppler = next((Path(value) for value in os.environ.get('PATH', '').split(pathsep)
                    if 'codexprimaryruntime' in value.lower() and 'poppler' in value.lower()), None)
    if poppler is None or not (poppler / 'libssl-3-x64.dll').is_file():
        pytest.skip('The conflicting Poppler libraries are unavailable')
    result = subprocess.run(
        [sys.executable, '-B', '-c', """
import ctypes
kernel = ctypes.windll.kernel32
kernel.SetErrorMode(0x8003)
from app import __main__
import hashlib
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtNetwork import QSslSocket
application = QCoreApplication([])
assert QSslSocket.activeBackend() == 'schannel'
assert not kernel.GetModuleHandleW('libssl-3-x64.dll')
"""],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, 'PATH': f"{poppler}{pathsep}{os.environ['PATH']}"},
        capture_output=True, text=True, timeout=12,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_settings_dialog_uses_official_models_and_allows_local_compatible_addresses(qapp):
    from app.translation.ui import TranslationSettingsDialog

    mgr = _FakeNetworkManager(lambda r, p: _FakeReply(200, _openai_ok_bytes('你好')))
    client = OpenAICompatibleClient(network_manager=mgr)
    cfg = TranslationSettings(api_key='sk-abc', privacy_accepted=False)
    dlg = TranslationSettingsDialog(cfg, client)
    dlg.show()
    qapp.processEvents()
    try:
        assert [dlg.provider_combo.itemText(index) for index in range(dlg.provider_combo.count())] == [
            'DeepSeek', 'OpenAI 兼容',
        ]
        assert dlg.provider_combo.currentData() == 'deepseek'
        assert dlg.endpoint_edit.isReadOnly() and dlg.endpoint_edit.text() == DEEPSEEK_ENDPOINT
        assert [dlg.deepseek_model_combo.itemText(index) for index in range(dlg.deepseek_model_combo.count())] == list(DEEPSEEK_MODELS)
        assert dlg.deepseek_model_combo.currentText() == 'deepseek-flash'
        assert dlg.privacy_box.isVisible()
        dlg._save()
        assert dlg.result_config is None  # blocked because privacy consent unchecked
        assert '隐私' in dlg.feedback.text() or '数据外发' in dlg.feedback.text()

        dlg.privacy_check.setChecked(True)
        dlg.deepseek_model_combo.setCurrentText('deepseek-v4-pro')
        dlg._save()
        assert dlg.result_config is not None
        assert dlg.result_config.provider == 'deepseek'
        assert dlg.result_config.model == 'deepseek-v4-pro'

        dlg2 = TranslationSettingsDialog(TranslationSettings(), client)
        dlg2.show()
        try:
            dlg2.provider_combo.setCurrentIndex(dlg2.provider_combo.findData('custom'))
            assert not dlg2.endpoint_edit.isReadOnly() and not dlg2.endpoint_edit.text()
            assert dlg2.model_edit.isVisible() and not dlg2.model_edit.text()
            dlg2.endpoint_edit.setText('http://127.0.0.1:9000/v1')
            dlg2.model_edit.setText('local-model')
            qapp.processEvents()
            assert not dlg2.privacy_box.isVisible()
            assert dlg2.local_box.isVisible()
            dlg2._save()
            assert dlg2.result_config is not None and dlg2.result_config.is_local
        finally:
            dlg2.deleteLater()
    finally:
        dlg.deleteLater()


def test_mainwindow_translation_workflow_themes_and_permissions(qapp, tmp_path):
    from app.ui import MainWindow

    source = _make_text_pdf(tmp_path / 'workflow.pdf')
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        win.ws.open_replace(win.store.load(str(source)), str(source))
        win.refresh()
        qapp.processEvents()

        # Open tools panel, then open translation panel -> mutually exclusive.
        win.act_tools.setChecked(True)
        qapp.processEvents()
        assert win.tools_panel.isVisible()

        win.act_translation.setChecked(True)
        qapp.processEvents()
        controller = win._translation
        assert controller.panel.isVisible()
        assert not win.act_tools.isChecked()

        # Inject offline fake network manager and configured DeepSeek settings.
        def responder(_req, payload):
            text = payload['messages'][-1]['content']
            if '<<<SEG:' in text:
                import re
                ids = re.findall(r'<<<SEG:([^\s>]+)>>>', text)
                body = '\n'.join(f'<<<SEG:{i}>>>\n中文译文-{i}\n<<<END>>>' for i in ids)
                return _FakeReply(200, _openai_ok_bytes(body), delay_ms=5)
            return _FakeReply(200, _openai_ok_bytes('单段译文'), delay_ms=5)

        mgr = _FakeNetworkManager(responder)
        controller.client._manager = mgr
        controller.config = TranslationSettings(api_key='sk-demo', privacy_accepted=True)
        controller.sync_page(force=True)

        # Full-page translation
        controller.translate_current_page()
        for _ in range(60):
            qapp.processEvents()
            if not controller.panel._busy and controller._current_cached_page() is not None and controller._current_cached_page()[1].complete:
                break
            import time
            time.sleep(0.02)

        pair = controller._current_cached_page()
        assert pair is not None and pair[1].complete
        assert len(controller.panel._cards) >= 2
        requests_after_page1 = len(mgr.requests)

        # Bidirectional highlight on hover/click
        first_sid = next(iter(controller.panel._cards))
        controller._on_segment_hovered(first_sid)
        assert controller.inline_layer._highlight is not None
        controller.inline_layer.clear_highlight()
        assert controller.inline_layer._highlight is None

        # Inline overlay mode + peek + hide
        controller._on_view_mode_changed('overlay')
        qapp.processEvents()
        assert len(controller.inline_layer._items) >= 2
        controller.inline_layer.set_peeking(True)
        assert controller.inline_layer._items[0].opacity() < 0.2
        controller.inline_layer.set_peeking(False)
        assert controller.inline_layer._items[0].opacity() == 1.0
        controller.inline_layer.set_hidden(True)
        assert not controller.inline_layer._items[0].isVisible()
        controller.inline_layer.set_hidden(False)

        # Page navigation & cache hit (no new network calls when returning to page 1)
        win.go_to_page(2)
        qapp.processEvents()
        win.go_to_page(1)
        qapp.processEvents()
        assert len(mgr.requests) == requests_after_page1

        # Selection mini-bar must not steal focus from preview_view
        win.preview_view.setFocus()
        _wait_until(win._reading_preload_ready)
        controller.on_text_selected('Thermal Management Overview', 0, None)
        qapp.processEvents()
        assert controller.mini_bar.isVisible()
        assert controller.mini_bar.focusPolicy() == Qt.FocusPolicy.NoFocus
        assert controller.mini_bar.translate_btn.focusPolicy() == Qt.FocusPolicy.NoFocus

        # Translate selection into bubble & pin to panel
        controller._translate_selection_text('Thermal Management Overview')
        for _ in range(30):
            qapp.processEvents()
            if controller.bubble._translated_text:
                break
            import time
            time.sleep(0.02)
        assert controller.bubble._translated_text == '单段译文'
        controller._pin_excerpt('划词翻译', 'Thermal Management Overview', '单段译文')
        assert len(controller._pinned) == 1

        # Switching all 5 ZhiXu themes renders cleanly without error
        for theme in THEMES:
            theme_manager().apply(theme.key)
            qapp.processEvents()

        # Copy-restricted encrypted PDF disables translation entry points
        import pymupdf
        locked = tmp_path / 'no-copy.pdf'
        with pymupdf.open() as doc:
            doc.new_page().insert_text((50, 70), 'Protected content')
            doc.save(
                locked,
                encryption=pymupdf.PDF_ENCRYPT_AES_256,
                owner_pw='owner123',
                user_pw='',
                permissions=pymupdf.PDF_PERM_PRINT,
            )
        win.ws.open_replace(win.store.load(str(locked)), str(locked))
        win.refresh()
        qapp.processEvents()
        assert not win.act_translation.isEnabled()
        assert not win.region_translate_action.isEnabled()
    finally:
        theme_manager().apply('graphite')
        win.ws.reset()
        win.close()
        win.deleteLater()
        qapp.processEvents()


@pytest.fixture
def translation_window(qapp, tmp_path):
    from app.ui import MainWindow

    window = MainWindow()
    window.resize(1200, 900)
    window.show()
    assert window.open_files_in_tabs([str(_make_text_pdf(tmp_path / 'reader.pdf'))])
    window.act_translation.trigger()
    controller = window._translation
    manager = _FakeNetworkManager(_translation_reply)
    controller.client._manager = manager
    controller.config = TranslationSettings(api_key='sk-test', privacy_accepted=True, session_only_key=True)
    controller.sync_page(force=True)
    QTest.qWait(30)
    try:
        yield window, controller, manager
    finally:
        window.close()
        window.deleteLater()


def _viewport_point(window, x, y):
    view = window.preview_view
    bounds = view.page_rects[window.ws.preview_index()]
    point = view.mapFromScene(bounds.topLeft() + QPointF(x, y))
    assert view.viewport().rect().contains(point)
    return point


def _drag_preview(window, first, last):
    view = window.preview_view.viewport()
    start = _viewport_point(window, *first)
    end = _viewport_point(window, *last)
    QTest.mousePress(view, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view, end)
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, pos=end)


def _toggle_preference(controller, checkbox):
    def change():
        dialog = QApplication.activeModalWidget()
        dialog.tabs.setCurrentIndex(1)
        getattr(dialog, checkbox).click()
        dialog.save_button.click()
    QTimer.singleShot(0, change)
    controller.panel.engine_pill.click()


def _button(root, text):
    def visible_button():
        return next((button for button in root.findChildren(QAbstractButton)
                     if button.isVisible() and text in (button.text(), button.accessibleName())), None)

    _wait_until(visible_button)
    return visible_button()


def test_settings_switch_cancels_test_and_resets_key_and_consent(qapp):
    from app.translation.ui import TranslationSettingsDialog

    manager = _FakeNetworkManager(lambda *_: _FakeReply(200, _openai_ok_bytes('测试译文'), delay_ms=300))
    client = OpenAICompatibleClient(network_manager=manager)
    dialog = TranslationSettingsDialog(TranslationSettings(api_key='sk-first', privacy_accepted=True), client)
    dialog.show()
    try:
        dialog.test_button.click()
        assert len(manager.requests) == 1
        assert not dialog.test_button.isEnabled()
        dialog.provider_combo.setCurrentIndex(dialog.provider_combo.findData('custom'))
        assert not dialog.key_edit.text()
        assert not dialog.privacy_check.isChecked()
        assert dialog.test_button.isEnabled()
        assert not client._active
        dialog.test_button.click()
        dialog.save_button.click()
        assert dialog.result_config is None
        assert len(manager.requests) == 1

        dialog.key_edit.setText('sk-second')
        dialog.privacy_check.setChecked(True)
        dialog.endpoint_edit.setText('https://gateway.example.invalid/v1')
        assert not dialog.key_edit.text()
        assert not dialog.privacy_check.isChecked()

        dialog.provider_combo.setCurrentIndex(dialog.provider_combo.findData('deepseek'))
        assert dialog.endpoint_edit.text() == DEEPSEEK_ENDPOINT and dialog.endpoint_edit.isReadOnly()
        assert dialog.deepseek_model_combo.currentText() == 'deepseek-flash'
    finally:
        dialog.close()


def test_settings_rejects_empty_credentials_and_can_explicitly_clear_existing_key(qapp):
    from app.translation.ui import TranslationSettingsDialog

    client = OpenAICompatibleClient(network_manager=_FakeNetworkManager(_translation_reply))
    dialog = TranslationSettingsDialog(TranslationSettings(), client)
    dialog.save_button.click()
    assert dialog.result_config is None
    assert 'API Key' in dialog.feedback.text()
    dialog.close()

    dialog = TranslationSettingsDialog(TranslationSettings(api_key='sk-test', privacy_accepted=True), client)
    dialog.clear_key_btn.click()
    dialog.save_button.click()
    assert dialog.result_config is not None
    assert not dialog.result_config.api_key
    assert not dialog.result_config.is_ready


@pytest.mark.parametrize('remember', (False, True))
def test_settings_button_tests_connection_and_saves_configuration(translation_window, request, remember):
    from app.translation.ui import TranslationSettingsDialog

    window, controller, manager = translation_window
    if remember:
        request.getfixturevalue('windows_vault')
    failures = []

    def configure_dialog():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, TranslationSettingsDialog)
            dialog.provider_combo.setCurrentIndex(dialog.provider_combo.findData('custom'))
            dialog.endpoint_edit.setText('https://api.openai.com/v1')
            dialog.model_edit.setText('cloud-model')
            dialog.key_edit.setText('sk-openai-test')
            dialog.privacy_check.setChecked(True)
            dialog.remember_key_check.setChecked(remember)
            dialog.lang_combo.setCurrentIndex(dialog.lang_combo.findData('ja'))
            dialog.test_button.click()
            _wait_until(lambda: '连接成功' in dialog.feedback.text())
            dialog.save_button.click()
        except (AssertionError, AttributeError) as exc:
            failures.append(exc)
            if dialog is not None:
                dialog.reject()

    QTimer.singleShot(0, configure_dialog)
    controller.panel.engine_pill.click()
    assert not failures
    assert len(manager.requests) == 1
    assert 'Japanese' in manager.requests[0][1]['messages'][0]['content']
    assert controller.config.is_ready and controller.config.provider == 'custom'
    persisted = load_settings(window._window_settings)
    assert persisted.model == 'cloud-model' and persisted.target_language == 'ja'
    if remember:
        assert not persisted.session_only_key and persisted.is_ready
        assert '自动恢复' in window.statusBar().currentMessage()
    else:
        assert persisted.session_only_key and persisted.needs_api_key
        assert '仅本次运行有效' in window.statusBar().currentMessage()
        controller.config = persisted
        controller.sync_page(force=True)
        assert '设置' in controller.panel.engine_pill.text()
        assert 'OpenAI 兼容' in controller.panel.engine_pill.toolTip()
        assert controller.panel.primary_btn.text() == '补填 API Key'


def test_scan_primary_and_retry_use_local_ocr(translation_window, tmp_path, monkeypatch):
    from app.ocr.contracts import OCRLine
    from app.translation import extractor

    window, controller, manager = translation_window
    scan = _make_scan_pdf(tmp_path / 'image-only.pdf')
    original = scan.read_bytes()
    assert window.open_files_in_tabs([str(scan)])
    monkeypatch.setattr(controller, '_is_ocr_installed', lambda: True)
    inputs = []

    def recognize(image, page_index, *, cancel=None):
        inputs.append((image.width(), image.height(), page_index))
        return [OCRLine('Scanned content', (30, 50, 270, 80), (30, 50, 270, 50, 270, 80, 30, 80), .99, page_index)]

    monkeypatch.setattr(extractor, '_run_local_ocr_on_image', recognize)
    manager.responder = lambda *_: _FakeReply(429, b'{}')

    controller.panel.primary_btn.click()
    _wait_until(lambda: controller.panel.primary_btn.text() == 'OCR 并译本页')
    assert not manager.requests
    controller.panel.primary_btn.click()
    _wait_until(lambda: len(manager.requests) == 1 and not controller.panel._busy)
    assert 'Scanned content' in manager.requests[0][1]['messages'][-1]['content']
    manager.responder = _translation_reply
    _button(controller.panel, '重试').click()
    _wait_until(lambda: (pair := controller._current_cached_page()) is not None and pair[1].complete)
    assert len(manager.requests) == 2
    assert inputs and all(page_index == 0 for _, _, page_index in inputs)
    extraction = controller._current_cached_page()[1].extraction
    assert extraction.via_ocr and extraction.selection_snapshot
    raw = extraction.selection_snapshot['raw']
    line = next(line for block in raw['blocks'] for line in block.get('lines', ()))
    chars = [char for span in line['spans'] for char in span['chars']]
    start, end = chars[0]['bbox'], chars[6]['bbox']
    _drag_preview(window, (start[0], (start[1] + start[3]) / 2), (end[2], (end[1] + end[3]) / 2))
    _wait_until(lambda: window.text_selection.text == 'Scanned')
    QTest.keyClick(window.preview_view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == 'Scanned'
    assert scan.read_bytes() == original and not window.ws.dirty


def test_scan_without_ocr_primary_opens_installer(translation_window, tmp_path, monkeypatch):
    from app import ocrui

    window, controller, manager = translation_window
    assert window.open_files_in_tabs([str(_make_scan_pdf(tmp_path / 'needs-ocr.pdf'))])
    monkeypatch.setattr(controller, '_is_ocr_installed', lambda: False)
    installs = []
    def install_preview(host):
        installs.append(host)
        monkeypatch.setattr(controller, '_is_ocr_installed', lambda: True)
        return True

    monkeypatch.setattr(ocrui, 'install_local_runtime', install_preview)
    controller.panel.primary_btn.click()
    _wait_until(lambda: not controller.panel._busy)
    assert '安装 OCR 增强包' in controller.panel.primary_btn.text()
    controller.panel.primary_btn.click()
    assert installs == [window]
    assert controller.panel.primary_btn.text() == 'OCR 并译本页'
    assert not manager.requests


def test_region_uses_real_drag_and_retries_corrected_ocr(translation_window, tmp_path, monkeypatch):
    from app.ocr.contracts import OCRLine
    from app.translation import extractor

    window, controller, manager = translation_window
    window.region_translate_action.trigger()
    assert controller.region_active
    QTest.keyClick(window.preview_view, Qt.Key.Key_Escape)
    assert not controller.region_active and not manager.requests

    window.region_translate_action.trigger()
    _drag_preview(window, (40, 97), (440, 183))
    _wait_until(lambda: bool(controller.bubble._translated_text))
    assert 'Thermal Management' in manager.requests[-1][1]['messages'][-1]['content']
    controller.bubble.close_btn.click()

    scan = _make_scan_pdf(tmp_path / 'region-image.pdf')
    assert window.open_files_in_tabs([str(scan)])
    monkeypatch.setattr(extractor, '_run_local_ocr_on_image', lambda image, page_index, *, cancel=None: [
        OCRLine('Original OCR line', (10, 10, 210, 30), (10, 10, 210, 10, 210, 30, 10, 30), .98, page_index),
    ])
    window.region_translate_action.trigger()
    _drag_preview(window, (40, 60), (400, 200))
    _wait_until(lambda: bool(controller.bubble._translated_text))
    assert controller.bubble.ocr_toggle_btn.isVisible()
    controller.bubble.ocr_toggle_btn.click()
    controller.bubble.ocr_editor.setPlainText('Corrected OCR line')
    controller.bubble.ocr_retranslate_btn.click()
    _wait_until(lambda: len(manager.requests) == 3 and controller.bubble.copy_btn.isEnabled())
    assert manager.requests[-1][1]['messages'][-1]['content'] == 'Corrected OCR line'
    controller.bubble.copy_btn.click()
    assert QApplication.clipboard().text() == '单段译文'
    assert not window.ws.dirty


@pytest.mark.parametrize('leave', ['close', 'page', 'document'])
def test_obsolete_region_extraction_does_not_send_text(translation_window, tmp_path, monkeypatch, leave):
    from app.translation import ui
    from app.translation.extractor import RegionExtraction

    window, controller, manager = translation_window
    started, released = Event(), Event()

    def delayed_extraction(*args, **kwargs):
        started.set()
        released.wait(3)
        return RegionExtraction('Text from the earlier region', 'text')

    monkeypatch.setattr(ui, 'extract_region_for_translation', delayed_extraction)
    window.region_translate_action.trigger()
    _drag_preview(window, (40, 98), (430, 183))
    try:
        _wait_until(started.is_set)
        if leave == 'close':
            controller.bubble.close_btn.click()
        elif leave == 'page':
            window.go_to_page(2)
        else:
            assert window.open_files_in_tabs([str(_make_text_pdf(tmp_path / 'different.pdf'))])
    finally:
        released.set()
    _wait_until(lambda: not controller._worker_callbacks)
    assert not manager.requests and not controller.bubble.isVisible()


def test_minibar_placement_does_not_cover_selections_at_viewport_edges():
    from app.translation.ui import _near_selection

    viewport = QRect(0, 0, 420, 220)
    size = QSize(174, 32)
    for selection in (QRect(14, 164, 389, 48), QRect(20, 8, 360, 40), QRect(245, 8, 164, 202)):
        position = _near_selection(viewport, selection, size)
        assert position is not None
        controls = QRect(position, size)
        assert viewport.contains(controls) and not controls.intersects(selection)
    assert _near_selection(viewport, viewport, size) is None


@pytest.mark.parametrize('near_bottom', (False, True))
def test_selection_result_stays_next_to_text_with_sidebar_closed(translation_window, tmp_path, near_bottom):
    import pymupdf

    window, controller, manager = translation_window
    path = tmp_path / 'selection-popup.pdf'
    with pymupdf.open() as document:
        page = document.new_page(width=580, height=1400)
        page.insert_text((60, 700), 'A locally generated example.', fontsize=12)
        document.save(path)
    assert window.open_files_in_tabs([str(path)])
    window.act_translation.setChecked(False)
    window._set_zoom(1.)
    view = window.preview_view
    viewport = view.viewport()
    short = '这是一条用于核对选区位置的译文。'
    result = short if near_bottom else short * 85 + '\nLAST-LINE'
    manager.responder = lambda *_: _FakeReply(200, _openai_ok_bytes(result))
    window.store.text_snapshot(window.ws.pages[0], include_tables=False)
    QApplication.processEvents()
    target_y = viewport.height() - 33 if near_bottom else 48
    point = view.mapFromScene(view.page_rects[0].topLeft() + QPointF(60, 695))
    bar = view.verticalScrollBar()
    bar.setValue(bar.value() + point.y() - target_y)
    QTest.qWait(60)
    _drag_preview(window, (60, 695), (190, 695))
    _wait_until(controller.mini_bar.isVisible)
    controller.mini_bar.translate_btn.click()
    _wait_until(controller.bubble.copy_btn.isEnabled)
    bubble = controller.bubble
    anchor = controller._selection_viewport_rect(0, window.text_selection.area)
    assert not window.act_translation.isChecked() and bubble.parentWidget() is viewport
    assert bubble.isVisible() and viewport.rect().contains(bubble.geometry())
    assert not bubble.geometry().intersects(anchor)
    if near_bottom:
        assert bubble.geometry().bottom() < anchor.top()
        assert bubble.result_edit.verticalScrollBar().maximum() == 0
    else:
        assert bubble.geometry().top() > anchor.bottom()
        assert bubble.result_edit.verticalScrollBar().maximum() > 0
        bubble.result_edit.moveCursor(QTextCursor.MoveOperation.End)
        _wait_until(lambda: bubble.result_edit.viewport().rect().contains(bubble.result_edit.cursorRect()))
    assert bubble.result_edit.toPlainText() == result
    bubble.close_btn.click()
    assert not bubble.isVisible() and not controller.panel.isVisible()


def test_drag_and_copy_continue_while_page_translation_finishes(translation_window):
    window, controller, manager = translation_window
    pending = []

    def hold(request, payload):
        reply = _translation_reply(request, payload)
        reply._timer.stop()
        pending.append(reply)
        return reply

    manager.responder = hold
    controller.panel.overlay_mode_btn.click()
    window.store.text_snapshot(window.ws.pages[0], include_tables=False)
    controller.panel.primary_btn.click()
    _wait_until(lambda: pending and controller.panel._busy)
    _drag_preview(window, (49, 115), (345, 115))
    _wait_until(lambda: window.text_selection.text == 'Thermal Management Overview')
    QTest.keyClick(window.preview_view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == window.text_selection.text
    pending[0]._complete()
    _wait_until(lambda: controller._current_cached_page()[1].complete)
    items = controller.inline_layer._items
    assert items and all(item.opacity() == 0 for item in items)
    assert window.text_selection.text == 'Thermal Management Overview'
    QTest.keyClick(window.preview_view, Qt.Key.Key_Escape)
    assert not window.text_selection.text and all(item.opacity() == 1 for item in items)


@pytest.mark.parametrize('mode', ('side', 'overlay'))
def test_source_click_and_wrapped_cell_drag_have_distinct_actions(translation_window, tmp_path, mode):
    import pymupdf
    from app.pdfedits import selection_range
    from app.documentlayout import selection_text

    window, controller, manager = translation_window
    path = tmp_path / 'wrapped-table.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=580, height=600)
        for x in (55, 220, 505):
            page.draw_line((x, 120), (x, 245))
        for y in (120, 155, 210, 245):
            page.draw_line((55, y), (505, y))
        for x, y, text in ((63, 141, 'Condition'), (228, 141, 'Response'),
                           (63, 177, 'Duplicate pilot'), (63, 193, 'movement'),
                           (228, 177, 'Stop the pilot and keep'), (228, 193, 'the movement IDs.'),
                           (63, 232, 'Queue delay'), (228, 232, 'Pause additional orders.')):
            page.insert_text((x, y), text, fontsize=11)
        doc.save(path)
    assert window.open_files_in_tabs([str(path)])
    controller.panel.primary_btn.click()
    _wait_until(lambda: (pair := controller._current_cached_page()) and pair[1].complete)
    controller._on_view_mode_changed(mode)
    _, cached = controller._current_cached_page()
    cell = next(seg for seg in cached.extraction.segments if seg.text.startswith('Stop the pilot'))
    row = [seg for seg in cached.extraction.segments if seg.cell and seg.cell.row == cell.cell.row]
    other = next(seg for seg in cached.extraction.segments if seg.text == 'Queue delay')
    snapshot = window.store.text_snapshot(window.ws.pages[0])
    expected = 'Stop the pilot and keep the movement IDs.'
    end_x = 229 + pymupdf.get_text_length('the movement IDs.', fontsize=11)
    first, last = (228, 173), (end_x, 189)
    for tables in ((), snapshot['tables']):
        for start, end in ((first, last), (last, first)):
            raw, rect = selection_range(snapshot['raw'], start, end, tables)
            assert ' '.join(selection_text(raw, rect, ()).split()) == expected

    requests = len(manager.requests)
    view = window.preview_view
    card = controller.panel._cards[cell.segment_id]
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=_viewport_point(window, 250, 173))
    _wait_until(lambda: card.property('linked'))
    assert controller.panel.scroll.viewport().rect().contains(card.dst_label.mapTo(controller.panel.scroll.viewport(), QPoint(2, 4)))
    assert not window.text_selection.text and len(manager.requests) == requests
    if mode == 'overlay':
        controller.page_original.click()
    _drag_preview(window, first, last)
    _wait_until(controller.mini_bar.isVisible)
    assert ' '.join(window.text_selection.text.split()) == expected
    if mode == 'overlay':
        assert controller.inline_layer._items and all(item.opacity() == 0 for item in controller.inline_layer._items)
    QTest.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert ' '.join(QApplication.clipboard().text().split()) == expected
    controller.mini_bar.translate_btn.click()
    _wait_until(lambda: controller.bubble.floating and controller.bubble.copy_btn.isEnabled())
    assert controller.bubble.result_edit.toPlainText() == cached.translations[cell.segment_id]
    assert len(manager.requests) == requests
    controller.bubble.close_btn.click()

    # Clicking selectable label text must work as well as clicking the card padding.
    if mode == 'overlay':
        controller.page_original.click()
    for selected in (controller.panel._cards[other.segment_id], card):
        controller.panel.scroll.ensureWidgetVisible(selected)
        QTest.mouseClick(selected.dst_label, Qt.MouseButton.LeftButton, pos=QPoint(4, 4))
        _wait_until(lambda: selected.property('linked'))
    assert not window.text_selection.text
    highlight = controller.inline_layer._highlight
    assert highlight.isVisible() and highlight.pen().style() == Qt.PenStyle.NoPen
    assert all(item.opacity() == 1 and item.zValue() < highlight.zValue() for item in controller.inline_layer._items)
    page_left = view.page_rects[0].left()
    assert highlight.rect().left() == pytest.approx(page_left + min(seg.bbox[0] for seg in row))
    assert highlight.rect().right() == pytest.approx(page_left + max(seg.bbox[2] for seg in row))
    if mode == 'overlay':
        controller.page_original.click()
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=_viewport_point(window, 70, 228))
        _wait_until(lambda: controller.panel._cards[other.segment_id].property('linked'))


@pytest.mark.parametrize('sidebar_state', ['closed', 'collapsed', 'borderless'])
def test_long_result_scrolls_to_last_line_beside_page(translation_window, sidebar_state):
    window, controller, manager = translation_window
    window.resize(980, 680)
    splitter = window.reader_split
    panel_index = splitter.indexOf(controller.panel)
    if sidebar_state == 'closed':
        window.act_translation.setChecked(False)
    elif sidebar_state == 'collapsed':
        sizes = splitter.sizes()
        sizes[panel_index] = 0
        splitter.setSizes(sizes)
        assert splitter.sizes()[panel_index] == 0
    else:
        window.immersive.borderless_action.setChecked(True)
        assert not controller.panel.isVisible()
    long_text = '这是可用于核对换行和行距的测试译文。' * 80 + '\nEND-OF-TRANSLATION'
    manager.responder = lambda *_: _FakeReply(200, _openai_ok_bytes(long_text))
    controller._translate_selection_text('Thermal Management Overview')
    _wait_until(controller.bubble.copy_btn.isEnabled)

    editor = controller.bubble.result_edit
    assert splitter.sizes()[panel_index] >= controller.panel.minimumWidth()
    viewport = window.preview_view.viewport()
    page = QRect(viewport.mapToGlobal(QPoint()), viewport.size())
    detail = QRect(controller.bubble.mapToGlobal(QPoint()), controller.bubble.size())
    assert controller.panel.detail_visible and not page.intersects(detail)
    assert editor.isVisible() and editor.toPlainText() == long_text
    assert editor.document().firstBlock().blockFormat().lineHeight() == 145
    scrollbar = editor.verticalScrollBar()
    _wait_until(lambda: scrollbar.maximum() > 0)
    editor.moveCursor(QTextCursor.MoveOperation.End)
    _wait_until(lambda: editor.viewport().rect().contains(editor.cursorRect()))
    scrollbar.setValue(scrollbar.maximum())
    assert editor.viewport().rect().contains(editor.cursorRect())

    controller.bubble.close_btn.click()
    assert not controller.bubble.isVisible()
    assert controller.panel.scroll.isVisible() == (sidebar_state != 'borderless')


def test_inline_text_is_either_fully_fitted_or_original_remains_visible(qapp):
    from app.translation.ui import InlineOverlayLayer

    scene = QGraphicsScene()
    view = QGraphicsView(scene)
    view.page_rects = [QRectF(0, 0, 440, 570)]
    layer = InlineOverlayLayer(view)
    short = PageSegment('short', 'body', '', 'Original short', (50, 50, 240, 82))
    long = PageSegment('long', 'body', '', 'Original narrow line', (50, 200, 355, 213))
    neighbor = PageSegment('neighbor', 'body', '', 'Other text', (50, 221, 355, 252))
    long_text = '不能把这段很长的译文挤进这个单行原文区域。' * 25
    assert layer.render_page(0, (short,), {'short': '简短译文'}, enabled=True)
    fitted, = layer._items
    assert fitted._doc.toPlainText() == '简短译文'
    assert fitted._doc.size().height() <= fitted._text_rect.height()
    assert not layer.render_page(0, (short, long, neighbor), {'short': '简短译文', 'long': long_text}, enabled=True)
    assert not layer._items



def test_inline_title_keeps_all_ink_before_and_after_highlighting(qapp):
    from PyQt6.QtGui import QPainter
    from app.translation.ui import InlineOverlayLayer

    scene = QGraphicsScene()
    view = QGraphicsView(scene)
    view.page_rects = [QRectF(0, 0, 300, 120)]
    layer = InlineOverlayLayer(view)
    title = PageSegment('title', 'heading', '', 'Source title', (20, 20, 270, 54),
                        font_size=22, ink='#166074')
    translations = {'title': 'Typography', 'next': 'Next line'}

    def title_ink(min_contrast=45):
        image = QImage(600, 240, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        painter = QPainter(image)
        scene.render(painter, QRectF(image.rect()), view.page_rects[0])
        painter.end()
        ink = set()
        for y in range(35, 110):
            paper = image.pixelColor(530, y)
            threshold = paper.green() - paper.red() + min_contrast
            for x in range(44, 540):
                color = image.pixelColor(x, y)
                if color.green() - color.red() > threshold:
                    ink.add((x, y))
        return ink

    assert layer.render_page(0, (title,), translations, enabled=True, via_ocr=True)
    expected = title_ink()
    assert expected
    # The next line's background starts just above the bottom of the title's ink.
    top = max(y for _, y in expected) / 2 - 1
    following = PageSegment('next', 'body', '', 'Source line', (20, top, 270, top + 30), font_size=16)
    assert layer.render_page(0, (title, following), translations, enabled=True, via_ocr=True)
    normal = title_ink()
    layer.highlight_segments(0, (title,))
    # The translucent highlight reduces contrast but leaves the ink in place.
    highlighted = title_ink(min_contrast=38)
    assert normal == expected
    assert expected <= highlighted


def test_wrapped_rollback_steps_are_sent_as_complete_instructions(tmp_path):
    import pymupdf
    from app.pdfio import PdfStore

    path = tmp_path / 'instructions.pdf'
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((55, 150), 'Keep both records.', fontsize=11, fontname='hebo')
        page.insert_text((55, 180), 'Export the movements before', fontsize=11)
        page.insert_text((55, 195), 'restoring operations.', fontsize=11)
        page.insert_text((55, 225), 'Reopen in order.', fontsize=11, fontname='hebo')
        document.save(path)
    store = PdfStore()
    try:
        ref, = store.load(str(path))
        segments = extract_page_segments(store, ref).segments
        assert [seg.text for seg in segments] == [
            'Keep both records.', 'Export the movements before restoring operations.', 'Reopen in order.',
        ]
    finally:
        store.close_all()


def test_ocr_form_line_order_tolerates_slightly_skewed_rows():
    from app.ocr.contracts import OCRLine
    from app.translation.extractor import _merge_ocr_lines_to_segments

    lines = [
        OCRLine('RCV-7421', (180, 102, 236, 115), (180, 102, 236, 102, 236, 115, 180, 115), .99, 0),
        OCRLine('Receipt reference', (30, 106, 134, 120), (30, 106, 134, 106, 134, 120, 30, 120), .99, 0),
        OCRLine('Item code', (30, 141, 98, 155), (30, 141, 98, 141, 98, 155, 30, 155), .99, 0),
    ]
    segments = _merge_ocr_lines_to_segments(lines, (0, 0), 1)
    assert [segment.text for segment in segments] == ['Receipt reference', 'RCV-7421', 'Item code']


def test_ocr_keeps_title_location_date_and_continuous_prose_in_their_own_boxes():
    from app.ocr.contracts import OCRLine
    from app.translation.extractor import _merge_ocr_lines_to_segments

    def line(text, top, bottom, right):
        return OCRLine(text, (40, top, right, bottom), (40, top, right, top, right, bottom, 40, bottom), .99, 0)

    lines = [line('Stock count card', 92, 116, 325), line('East Harbor | HOLD-02', 128, 145, 280),
             line('Saturday, 5 December | 08:12', 152, 169, 252), line('Keep the pallets until', 450, 467, 355),
             line('the ledger reopens.', 469, 485, 331)]
    segments = _merge_ocr_lines_to_segments(lines, (0, 0), 1)
    assert [s.text for s in segments] == [lines[0].text, lines[1].text, lines[2].text,
                                          'Keep the pallets until the ledger reopens.']
    assert segments[0].role == 'heading'
    assert [s.bbox for s in segments[:3]] == [line.bbox for line in lines[:3]]


def test_ocr_inline_uses_clear_line_spacing_or_falls_back_for_the_whole_page(qapp):
    from app.translation.ui import InlineOverlayLayer

    scene = QGraphicsScene()
    view = QGraphicsView(scene)
    view.page_rects = [QRectF(0, 0, 410, 560)]
    layer = InlineOverlayLayer(view)
    label = PageSegment('label', 'body', '', 'Receipt reference', (30, 106, 134, 119))
    code = PageSegment('code', 'body', '', 'RCV-7421', (180, 102, 236, 115))
    amount = PageSegment('amount', 'body', '', '4 pallets', (180, 140, 223, 153))
    segments = (label, code, amount)
    translations = {'label': '收货单号', 'code': 'RCV-7421', 'amount': '4 个托盘'}
    assert layer.render_page(0, segments, translations, enabled=True, via_ocr=True)
    assert [item.segment.segment_id for item in layer._items] == ['label', 'amount']
    for item in layer._items:
        assert item._doc is not None
        assert item._doc.size().height() <= item._text_rect.height()
        for other in segments:
            if other is not item.segment:
                x0, y0, x1, y1 = other.bbox
                assert not item._rect.intersects(QRectF(x0, y0, x1 - x0, y1 - y0))
    translations['amount'] = '这是一段无法装入表单一行的长译文。' * 22
    assert not layer.render_page(0, segments, translations, enabled=True, via_ocr=True)
    assert not layer._items


def test_overlay_click_and_hide_controls_follow_page(translation_window):
    window, controller, manager = translation_window
    assert controller.panel.side_mode_btn.text() == '侧栏对照'
    assert controller.panel.overlay_mode_btn.text() == '页内译文'
    assert controller.panel.side_mode_btn.isChecked()
    controller.panel.primary_btn.click()
    _wait_until(lambda: (pair := controller._current_cached_page()) is not None and pair[1].complete)
    controller.panel.overlay_mode_btn.click()
    assert controller.panel.overlay_mode_btn.isChecked() and not controller.panel.side_mode_btn.isChecked()
    assert len(controller.inline_layer._items) >= 2
    item = controller.inline_layer._items[0]
    point = window.preview_view.mapFromScene(item._rect.center())
    QTest.mouseClick(window.preview_view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert not controller.bubble.isVisible()
    assert controller.panel._cards[item.segment.segment_id].property('linked')

    assert controller.page_original.isVisible() and controller.page_original.parentWidget() is window.tab_bar
    assert window.tab_bar.rect().contains(controller.page_original.geometry())
    assert controller.page_original.text() == '查看原文'
    QTest.mousePress(controller.page_original, Qt.MouseButton.LeftButton)
    assert controller.inline_layer._items and all(i.opacity() == 0 for i in controller.inline_layer._items)
    QTest.qWait(QApplication.startDragTime() + 30)
    QTest.mouseRelease(controller.page_original, Qt.MouseButton.LeftButton)
    assert not controller.page_original.isChecked()
    assert all(i.opacity() == 1 and i.isVisible() for i in controller.inline_layer._items)
    assert controller.panel.selection_btn.property('primary') and not controller.panel.primary_btn.property('primary')
    controller.page_original.click()
    assert all(not i.isVisible() for i in controller.inline_layer._items)
    assert controller.page_original.text() == '返回译文'
    window.go_to_page(2)
    assert not controller.page_original.isVisible()
    window.go_to_page(1)
    _wait_until(lambda: bool(controller.inline_layer._items))
    assert not controller.page_original.isChecked()
    assert controller.inline_layer._items[0].isVisible()
    assert len(manager.requests) == 1
    controller.panel.side_mode_btn.click()
    assert controller.panel.side_mode_btn.isChecked() and not controller.panel.overlay_mode_btn.isChecked()
    assert not controller.inline_layer._items


def test_double_click_translated_word_keeps_sidebar_closed_and_copies(translation_window):
    window, controller, manager = translation_window
    ref = window.ws.pages[0]
    heading = PageSegment('title', 'heading', 'Title', 'Thermal Management Overview',
                          (50, 99, 340, 128), font_size=18)
    cached = CachedPageTranslation(PageExtraction((heading,)), {'title': 'Alpha beta'}, complete=True)
    controller.cache.put(controller._page_cache_key(ref), cached)
    controller.panel.overlay_mode_btn.click()
    controller.sync_page(force=True)
    window.set_reading_tool('select')
    window.act_translation.setChecked(False)
    window.store.prepare_annotations(ref)
    view = window.preview_view
    QTest.qWait(60)
    hit = _viewport_point(window, 62, 112)
    QApplication.clipboard().setText('Clipboard before selection')

    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=hit)
    QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=hit)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=hit)
    QTest.qWait(QApplication.doubleClickInterval() + 40)
    assert not controller.panel.isVisible()
    QTest.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == 'Alpha'
    assert not manager.requests

    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=hit)
    _wait_until(controller.panel.isVisible)


@pytest.mark.parametrize('auto', (False, True))
def test_drag_translated_text_keeps_page_and_copies_displayed_characters(translation_window, auto):
    window, controller, manager = translation_window
    controller.config = replace(controller.config, auto_translate_selection=auto)
    controller.panel.primary_btn.click()
    _wait_until(lambda: (pair := controller._current_cached_page()) and pair[1].complete)
    controller.panel.overlay_mode_btn.click()
    view = window.preview_view
    calls = len(manager.requests)
    item = controller.inline_layer._items[0]
    assert item.translated.startswith('译文-')
    line = item._doc.begin().layout().lineAt(0)
    start = item._text_rect.topLeft() + QPointF(line.cursorToX(0)[0], line.y() + line.height()/2)
    end = item._text_rect.topLeft() + QPointF(line.cursorToX(2)[0], line.y() + line.height()/2)
    view.ensureVisible(item.boundingRect())
    QTest.qWait(60)
    viewport = view.viewport()
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=view.mapFromScene(start))
    QTest.mouseMove(viewport, view.mapFromScene(end))
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=view.mapFromScene(end))
    QApplication.processEvents()
    assert all(i.opacity() == 1 and i.isVisible() for i in controller.inline_layer._items)
    assert window.text_selection.text == '译文'
    assert not controller.mini_bar.isVisible() and not controller.bubble.isVisible()
    QTest.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == '译文'
    assert len(manager.requests) == calls

    # Explicitly choosing the word tool lets the reader pick the original.
    controller.panel.selection_btn.click()
    assert controller.panel.selection_btn.isChecked()
    assert all(i.opacity() == 0 for i in controller.inline_layer._items)
    _drag_preview(window, (49, 115), (345, 115))
    _wait_until(lambda: controller.bubble.floating and controller.bubble.copy_btn.isEnabled())
    assert window.text_selection.text == 'Thermal Management Overview'
    assert len(manager.requests) == calls


@pytest.mark.parametrize('cold', (False, True))
def test_region_over_page_translation_reuses_visible_result(translation_window, monkeypatch, cold):
    window, controller, manager = translation_window
    controller.panel.primary_btn.click()
    _wait_until(lambda: (pair := controller._current_cached_page()) and pair[1].complete)
    controller.panel.overlay_mode_btn.click()
    calls = len(manager.requests)
    item = controller.inline_layer._items[0]
    rect = item.boundingRect().translated(-window.preview_view.page_rects[0].topLeft())
    if cold:
        monkeypatch.setattr(window.store, 'cached_text_snapshot', lambda *_, **__: None)
    controller.panel.region_btn.click()
    _drag_preview(window, (rect.left()-1, rect.top()-1), (rect.right()+1, rect.bottom()+1))
    _wait_until(controller.bubble.copy_btn.isEnabled)
    assert controller.bubble.result_edit.toPlainText() == item.translated
    assert len(manager.requests) == calls and not controller.mini_bar.isVisible()
    assert all(i.opacity() == 1 for i in controller.inline_layer._items)


def test_named_word_and_region_actions_and_one_shot_selection(translation_window):
    window, controller, manager = translation_window
    panel = controller.panel
    assert panel.selection_btn.text() == '划词翻译' and panel.region_btn.text() == '框选翻译'
    assert not panel.chk_bilingual.isVisible()
    window.set_reading_tool('hand')
    panel.selection_btn.click()
    assert panel.selection_btn.isChecked() and window.text_selection.active()
    QTest.keyClick(window.preview_view, Qt.Key.Key_Escape)
    assert not panel.selection_btn.isChecked()
    panel.selection_btn.click()
    panel.region_btn.click()
    assert controller.region_active and not panel.selection_btn.isChecked()
    QTest.keyClick(window.preview_view, Qt.Key.Key_Escape)
    assert not controller.region_active
    window.store.text_snapshot(window.ws.pages[0], include_tables=False)
    panel.selection_btn.click()
    _drag_preview(window, (49, 115), (345, 115))
    _wait_until(controller.bubble.copy_btn.isEnabled)
    assert not panel.selection_btn.isChecked() and controller.bubble.floating
    assert len(manager.requests) == 1
    controller.bubble.close_btn.click()
    panel.selection_btn.click()
    _wait_until(lambda: len(manager.requests) == 2 and controller.bubble.copy_btn.isEnabled())
    assert not panel.selection_btn.isChecked()


def test_preferences_can_be_saved_without_service_credentials(translation_window, monkeypatch):
    window, controller, manager = translation_window
    controller.config = TranslationSettings()
    controller.sync_page(force=True)
    monkeypatch.setattr(keystore, 'write_key', lambda *_: pytest.fail('preferences accessed the vault'))
    monkeypatch.setattr(keystore, 'clear_key', lambda *_: pytest.fail('preferences cleared the vault'))
    _toggle_preference(controller, 'auto_selection_check')
    assert controller.config.auto_translate_selection and not controller.config.is_ready
    assert load_settings(window._window_settings).auto_translate_selection
    assert not manager.requests


def test_monochrome_inline_text_keeps_neutral_edges_when_zoomed(qapp):
    from PyQt6.QtGui import QColor, QPainter
    from app.translation.ui import _InlineBlockItem

    scene = QGraphicsScene()
    view = QGraphicsView(scene)
    bounds = QRectF(0, 0, 250, 45)
    segment = PageSegment('body', 'body', '', 'Local sample', (0, 0, 250, 45), 11, '#111111')
    item = _InlineBlockItem(bounds, segment, '交接与沟通　30 分钟', view)
    assert item._doc is not None
    scene.addItem(item)
    for scale in (.8, 1.25):
        image = QImage(round(250 * scale), round(45 * scale), QImage.Format.Format_RGB32)
        image.fill(QColor('white'))
        painter = QPainter(image)
        scene.render(painter, QRectF(image.rect()), bounds)
        painter.end()
        colors = {image.pixel(x, y) & 0xffffff for x in range(image.width()) for y in range(image.height())}
        assert len(colors) > 3 and min(colors) < 0x777777
        assert all((color >> 16) == ((color >> 8) & 255) == (color & 255) for color in colors)


def test_technical_page_keeps_paragraphs_commands_and_first_body_line(tmp_path):
    import pymupdf
    from app.pdfio import PdfStore

    path = tmp_path / 'technical-notes.pdf'
    lines = [
        (44, 'Read a page and save the result.', 'helv'),
        (81, 'sample-tool read https://example.invalid --text', 'cour'),
        (139, 'List the pages in the directory.', 'helv'),
        (175, 'sample-tool list --limit 10', 'cour'),
        (230, 'Open an existing project', 'hebo'),
        (270, 'The server records the timestamp and processes each request before', 'helv'),
        (292, 'writing a complete response to the output directory.', 'helv'),
        (349, 'Start the local service.', 'helv'),
        (385, 'sample-tool serve --port 9000', 'cour'),
    ]
    with pymupdf.open() as doc:
        page = doc.new_page(width=595, height=842)
        for y, line, font in lines:
            page.insert_text((44, y + 12), line, fontname=font, fontsize=14 if font == 'hebo' else 11)
        doc.save(path)
    store = PdfStore()
    try:
        ref, = store.load(str(path))
        segments = extract_page_segments(store, ref).segments
        assert [seg.text for seg in segments] == [
            *(line for _, line, _ in lines[:5]), lines[5][1] + ' ' + lines[6][1], lines[7][1], lines[8][1],
        ]
        assert [seg.text for seg in segments if seg.role == 'code'] == [t for _, t, f in lines if f == 'cour']
        assert not any(s.cell for s in segments)
    finally:
        store.close_all()


@pytest.mark.parametrize('interrupt', ['stop', 'rate_limit'])
def test_parallel_page_batches_are_bounded_ordered_and_resumable(translation_window, monkeypatch, interrupt):
    window, controller, _ = translation_window
    segments = tuple(PageSegment(f'p{i}', 'body', '', f'Paragraph {i}.', (40, 40+i*15, 330, 54+i*15))
                     for i in range(20))
    cached = CachedPageTranslation(PageExtraction(segments), {})
    controller.cache.put(controller._page_cache_key(window.ws.pages[0]), cached)
    requests = []

    def send(_config, items, *, on_success, on_error):
        request = SimpleNamespace(ids=[sid for sid, _ in items], ok=on_success, error=on_error, cancelled=False)
        request.cancel = lambda: setattr(request, 'cancelled', True)
        requests.append(request)
        return request

    def succeed(request):
        request.ok({sid: '中文 ' + sid for sid in request.ids}, 200)

    monkeypatch.setattr(controller.client, 'translate_batch', send)
    controller.panel.primary_btn.click()
    assert len(requests) == 3 and not cached.complete
    if interrupt == 'stop':
        succeed(requests[2])
        _wait_until(lambda: len(requests) == 4)
        controller.panel.primary_btn.click()
        assert all(requests[i].cancelled for i in (0, 1, 3))
    else:
        requests[0].error('429')
        succeed(requests[2])
        succeed(requests[1])
        _wait_until(lambda: not controller.panel._busy)
        assert len(requests) == 3 and cached.error == '429'
    before = len(requests)
    retained = set(cached.translations)
    assert retained and not cached.complete
    controller.panel.primary_btn.click()
    pending = requests[before:]
    assert set(sid for req in pending for sid in req.ids) == {s.segment_id for s in segments} - retained
    # Late callbacks from the previous run, even for this same page, cannot replace results.
    requests[0].ok({sid: 'stale' for sid in requests[0].ids}, 800)
    requests[0].error('stale error')
    for req in reversed(pending[1:]):
        succeed(req)
    QApplication.processEvents()
    assert not cached.complete
    succeed(pending[0])
    _wait_until(lambda: cached.complete)
    assert not cached.error and len(cached.translations) == 20
    assert list(controller.panel._cards) == [s.segment_id for s in segments]
    assert all(cached.translations[s.segment_id] == '中文 ' + s.segment_id for s in segments)


def test_overflow_translation_is_readable_in_the_page_and_can_show_original(translation_window):
    window, controller, manager = translation_window
    long_text = ('这是用于验证重排、换行和滚动的完整译文。' * 80) + '<end>'
    def respond(_request, payload):
        ids = re.findall(r'<<<SEG:([^\s>]+)>>>', payload['messages'][-1]['content'])
        text = '\n'.join(f'<<<SEG:{sid}>>>\n{sid} {long_text}\n<<<END>>>' for sid in ids)
        return _FakeReply(200, _openai_ok_bytes(text))
    manager.responder = respond
    controller.panel.overlay_mode_btn.click()
    controller.panel.primary_btn.click()
    reader = controller.inline_layer.reflow
    _wait_until(lambda: reader.isVisible() and controller._current_cached_page()[1].complete)
    cached = controller._current_cached_page()[1]
    assert reader.parentWidget() is window.preview_view.viewport()
    assert all(cached.translations[s.segment_id] in reader.toPlainText() for s in cached.extraction.segments)
    first = cached.extraction.segments[0]
    assert first.segment_id in dict((sid, pos) for pos, sid in reader._anchors)
    cursor = reader.document().find(first.segment_id)
    cursor.setPosition(cursor.selectionStart())
    a = reader.cursorRect(cursor)
    QTest.mouseClick(reader.viewport(), Qt.MouseButton.LeftButton, pos=a.center())
    assert controller.panel._cards[first.segment_id].property('linked')
    assert reader.extraSelections()
    cursor.movePosition(QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.MoveAnchor, len(first.segment_id))
    b = reader.cursorRect(cursor)
    controller.panel.region_btn.click()
    assert controller.region_active
    QTest.mousePress(reader.viewport(), Qt.MouseButton.LeftButton, pos=a.topLeft())
    QTest.mouseMove(reader.viewport(), b.bottomLeft())
    QTest.mouseRelease(reader.viewport(), Qt.MouseButton.LeftButton, pos=b.bottomLeft())
    assert controller.bubble.result_edit.toPlainText().strip() == first.segment_id and len(manager.requests) == 1
    controller.bubble.close_btn.click()
    _wait_until(lambda: reader.verticalScrollBar().maximum() > 0)
    reader.moveCursor(QTextCursor.MoveOperation.End)
    assert reader.viewport().rect().contains(reader.cursorRect())
    controller.page_original.click()
    assert not reader.isVisible() and window.preview_view.isVisible()
    controller.page_original.click()
    assert reader.isVisible()
    controller.panel.side_mode_btn.click()
    assert not reader.isVisible()
    controller.panel.overlay_mode_btn.click()
    window.go_to_page(2)
    assert not reader.isVisible() and len(manager.requests) == 1


def test_page_partial_batch_resume_cancel_and_auto_page_turn(translation_window):
    window, controller, manager = translation_window

    def partial_or_limited(_req, payload):
        ids = re.findall(r'<<<SEG:([^\s>]+)>>>', payload['messages'][-1]['content'])
        if ids:
            return _FakeReply(200, _openai_ok_bytes(f'<<<SEG:{ids[0]}>>>\n已完成段落\n<<<END>>>'))
        return _FakeReply(429, b'{}')

    manager.responder = partial_or_limited
    controller.panel.primary_btn.click()
    # The two IDs missing from the first batch are retried concurrently.
    _wait_until(lambda: len(manager.requests) == 3 and not controller.panel._busy)
    _, partial = controller._current_cached_page()
    assert not partial.complete and len(partial.translations) == 1
    manager.responder = lambda *_: _FakeReply(200, _openai_ok_bytes('待取消的译文'), delay_ms=500)
    _button(controller.panel, '重试').click()
    _wait_until(lambda: len(manager.requests) == 4)
    assert 'Thermal Management Overview' not in manager.requests[-1][1]['messages'][-1]['content']
    assert controller.panel.primary_btn.text() == '停止翻译'
    controller.panel.primary_btn.click()
    assert not controller.panel._busy and controller.panel.primary_btn.text() != '停止翻译'
    assert partial.translations == controller._current_cached_page()[1].translations

    manager.responder = _translation_reply
    controller.panel.primary_btn.click()
    _wait_until(lambda: controller._current_cached_page()[1].complete)
    assert '已完成段落' in controller._current_cached_page()[1].translations.values()
    controller.panel.copy_all_btn.click()
    assert QApplication.clipboard().text().startswith('已完成段落')
    controller.panel.chk_bilingual.click()
    assert controller.config.show_bilingual
    _wait_until(lambda: controller.panel._cards and all(
        card.src_label.isVisible() for card in controller.panel._cards.values()
    ))
    _toggle_preference(controller, 'auto_page_check')
    window.go_to_page(2)
    _wait_until(lambda: (pair := controller._current_cached_page()) is not None and pair[0] == 1 and pair[1].complete)


def test_drag_selection_unconfigured_retry_pin_and_auto(translation_window):
    window, controller, manager = translation_window
    configured = controller.config
    controller.config = replace(configured, api_key='')
    assert not hasattr(controller.mini_bar, 'browser_btn')
    assert not hasattr(controller.bubble, 'browser_btn')
    assert not hasattr(controller, '_open_browser_translate')
    window.store.text_snapshot(window.ws.pages[0], include_tables=False)
    _drag_preview(window, (49, 115), (345, 115))
    _wait_until(controller.mini_bar.isVisible)
    selected_text = window.text_selection.text
    assert selected_text == 'Thermal Management Overview'
    controller.mini_bar.translate_btn.click()
    _wait_until(controller.bubble.settings_btn.isVisible)
    assert not manager.requests
    controller.bubble.close_btn.click()

    controller.config = configured
    manager.responder = lambda *_: _FakeReply(401, b'{}')
    _drag_preview(window, (49, 115), (345, 115))
    _wait_until(controller.mini_bar.isVisible)
    controller.mini_bar.translate_btn.click()
    _wait_until(controller.bubble.retry_btn.isVisible)
    assert 'API Key' in controller.bubble.result_edit.toPlainText()
    manager.responder = _translation_reply
    controller.bubble.retry_btn.click()
    _wait_until(controller.bubble.pin_btn.isEnabled)
    controller.bubble.pin_btn.click()
    assert len(controller._pinned) == 1 and not controller.bubble.isVisible()
    _button(controller.panel, '移除').click()
    assert not controller._pinned

    _toggle_preference(controller, 'auto_selection_check')
    _drag_preview(window, (49, 115), (345, 115))
    _wait_until(lambda: controller.bubble.isVisible() and controller.bubble.copy_btn.isEnabled())
    assert not controller.mini_bar.isVisible()
    assert len(manager.requests) == 3


def test_selection_right_click_and_configured_shortcut(translation_window, monkeypatch):
    window, controller, manager = translation_window
    window.store.text_snapshot(window.ws.pages[0], include_tables=False)
    _drag_preview(window, (49, 115), (345, 115))
    _wait_until(controller.mini_bar.isVisible)

    def trigger_translation(menu, _pos):
        next(action for action in menu.actions() if action.text().startswith('翻译所选文字')).trigger()

    monkeypatch.setattr(QMenu, 'exec', trigger_translation)
    window.text_selection.menu(QPoint(80, 80))
    _wait_until(controller.bubble.copy_btn.isEnabled)
    controller.bubble.close_btn.click()

    values = window.shortcut_manager.values()
    values['reader.translate_selection'] = ('Ctrl+Shift+Y',)
    assert not window.shortcut_manager.apply(values)
    window.preview_view.setFocus()
    QTest.keyClick(window.preview_view, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    _wait_until(lambda: len(manager.requests) == 2 and controller.bubble.copy_btn.isEnabled())
    assert all(data['messages'][-1]['content'] == 'Thermal Management Overview' for _, data in manager.requests)


def test_pinned_excerpts_stay_with_their_tab(translation_window, tmp_path):
    window, controller, _ = translation_window
    original_tab_id = window._active_tab_id
    window.store.text_snapshot(window.ws.pages[0], include_tables=False)
    _drag_preview(window, (49, 115), (345, 115))
    _wait_until(controller.mini_bar.isVisible)
    controller.mini_bar.translate_btn.click()
    _wait_until(controller.bubble.pin_btn.isEnabled)
    controller.bubble.pin_btn.click()
    assert len(controller._pinned) == 1
    assert window.open_files_in_tabs([str(_make_text_pdf(tmp_path / 'new-tab.pdf'))])
    assert not controller._pinned
    assert window.activate_tab(original_tab_id)
    assert len(controller._pinned) == 1


def test_page_language_header_option_and_table_region(translation_window, tmp_path):
    import pymupdf

    window, controller, manager = translation_window
    path = tmp_path / 'table.pdf'
    with pymupdf.open() as document:
        page = document.new_page(width=595, height=842)
        page.insert_text((50, 36), 'Testing header', fontsize=9)
        page.insert_text((50, 114), 'Test results for the latest run.', fontsize=12)
        for x in (80, 180, 280):
            page.draw_line((x, 240), (x, 332))
        for index, row in enumerate((('Item', 'Value'), ('Battery', '47'), ('Sensor', '26'))):
            y = 240 + 30 * index
            page.draw_line((80, y), (280, y))
            page.insert_text((87, y + 19), row[0])
            page.insert_text((187, y + 19), row[1])
        page.draw_line((80, 332), (280, 332))
        document.save(path)
    assert window.open_files_in_tabs([str(path)])
    controller.panel.primary_btn.click()
    _wait_until(lambda: (pair := controller._current_cached_page()) is not None and pair[1].complete)
    _, cached = controller._current_cached_page()
    cells = [seg for seg in cached.extraction.segments if seg.cell]
    assert [seg.text for seg in cells] == ['Item', 'Value', 'Battery', '47', 'Sensor', '26']
    assert cells[2].cell.row == cells[3].cell.row and cells[2].cell.col < cells[3].cell.col
    assert cached.translations[cells[3].segment_id] == '47'
    card = controller.panel._cards[cells[2].segment_id]
    assert controller.panel._cards[cells[3].segment_id] is card
    assert '47' in card.dst_label.text() and '26' not in card.dst_label.text()
    assert '26' in controller.panel._cards[cells[5].segment_id].dst_label.text()
    assert all('Testing header' not in entry['messages'][-1]['content'] for _, entry in manager.requests)
    initial_requests = len(manager.requests)

    controller.panel.lang_combo.setCurrentIndex(controller.panel.lang_combo.findData('ja'))
    _toggle_preference(controller, 'skip_hf_check')
    assert controller._current_cached_page() is None
    controller.panel.primary_btn.click()
    _wait_until(lambda: (pair := controller._current_cached_page()) is not None and pair[1].complete)
    assert len(manager.requests) > initial_requests
    assert 'Japanese' in manager.requests[-1][1]['messages'][0]['content']
    assert any('Testing header' in data['messages'][-1]['content'] for _, data in manager.requests[initial_requests:])

    window.region_translate_action.trigger()
    _drag_preview(window, (78, 237), (283, 335))
    _wait_until(controller.bubble.copy_btn.isEnabled)
    source = manager.requests[-1][1]['messages'][-1]['content']
    assert 'Battery' in source and '47' in source and 'Testing header' not in source
    assert not controller.bubble.ocr_toggle_btn.isVisible() and not window.ws.dirty


def test_borderless_exit_on_copy_protected_pdf_keeps_panel_closed(translation_window, tmp_path):
    import pymupdf

    window, controller, _ = translation_window
    protected = tmp_path / 'copy-protected.pdf'
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((60, 100), 'Restricted content')
        document.save(
            protected,
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw='owner-secret',
            user_pw='',
            permissions=pymupdf.PDF_PERM_ACCESSIBILITY,
        )

    assert controller.panel.isVisible() and window.act_translation.isChecked()
    window.immersive.borderless_action.setChecked(True)
    assert not controller.panel.isVisible()
    assert window.open_files_in_tabs([str(protected)])
    assert not window.act_translation.isEnabled()
    assert not window.act_translation.isChecked()
    window.immersive.borderless_action.setChecked(False)
    QApplication.processEvents()
    assert not controller.panel.isVisible()

    # Even if forced visible out-of-band, clicking close always hides the panel.
    controller.panel.show()
    controller._close_panel()
    assert not controller.panel.isVisible()


def test_floating_bubble_adaptive_size_and_no_scrollbar_clipping(translation_window, tmp_path):
    import pymupdf

    window, controller, manager = translation_window
    window.resize(1100, 760)
    path = tmp_path / 'two-paragraphs.pdf'
    with pymupdf.open() as document:
        page = document.new_page(width=595, height=842)
        page.insert_text((55, 110), 'Harborline Distribution will move the East Harbor site to the new ledger.', fontsize=11)
        page.insert_text((55, 128), 'Receiving will stay open while inventory control verifies all open stock.', fontsize=11)
        document.save(path)
    assert window.open_files_in_tabs([str(path)])
    window.act_translation.setChecked(False)
    window._set_zoom(1.0)
    window.preview_view.verticalScrollBar().setValue(0)
    window.store.text_snapshot(window.ws.pages[0], include_tables=False)
    QApplication.processEvents()

    two_paragraphs = (
        'Harborline Distribution将于12月5日将East Harbor站点迁移至新的库存台账。'
        '此次变更将库位余额、未结库存移动和订单分配纳入同一台账。\n'
        '收货将继续开放。码头团队将为到达的托盘贴标，并将其暂存于收货缓冲区，直至库存移动功能启用。'
    )
    manager.responder = lambda *_: _FakeReply(200, _openai_ok_bytes(two_paragraphs))
    _drag_preview(window, (55, 106), (480, 124))
    _wait_until(controller.mini_bar.isVisible)
    controller.mini_bar.translate_btn.click()
    _wait_until(controller.bubble.copy_btn.isEnabled)
    QApplication.processEvents()

    bubble = controller.bubble
    anchor = controller._selection_viewport_rect(0, window.text_selection.area)
    assert bubble.floating and bubble.isVisible()
    assert not bubble.title_label.wordWrap()
    assert bubble.width() == anchor.width()
    assert bubble.geometry().left() == anchor.left()
    assert bubble.close_btn.height() <= 28
    assert bubble.result_edit.viewport().geometry().top() <= 2
    assert bubble.result_edit.verticalScrollBar().maximum() == 0
    assert not bubble._foot_bar.isVisible()

    # Very long scrollable result must never wrap wider than the editor viewport (no clipping under scrollbar).
    long_text = (two_paragraphs + '\n') * 12
    bubble.show_result(long_text)
    QApplication.processEvents()
    _wait_until(lambda: bubble.result_edit.verticalScrollBar().maximum() > 0)
    assert bubble.result_edit.document().textWidth() <= bubble.result_edit.viewport().width() + 0.5

    # Docking an active floating bubble keeps Copy and Pin buttons visible.
    controller.panel.set_detail_visible(True)
    controller._reveal_sidebar()
    QApplication.processEvents()
    assert not bubble.floating and bubble.copy_btn.isVisible() and bubble.pin_btn.isVisible()


def test_inline_overlay_background_sampling_guards_contrast(qapp):
    from PyQt6.QtGui import QColor
    from app.translation.ui import _contrast_ratio, _sample_paper_color

    # Cream callout box with a dark teal left accent bar right beside the text.
    paper = QImage(300, 120, QImage.Format.Format_ARGB32)
    paper.fill(QColor('#FFFBEB'))
    for x in range(12, 20):
        for y in range(10, 110):
            paper.setPixelColor(x, y, QColor('#134E4A'))
    bounds = QRectF(0, 0, 300, 120)
    rect = QRectF(21, 30, 220, 40)
    sampled = _sample_paper_color(paper, bounds, rect, '#1E293B')
    assert _contrast_ratio(sampled, QColor('#1E293B')) >= 4.5
    assert sampled == QColor('#FFFBEB')

    # Solid dark image with dark ink falls back to readable white paper.
    dark = QImage(100, 100, QImage.Format.Format_ARGB32)
    dark.fill(QColor('#111827'))
    fallback = _sample_paper_color(dark, QRectF(0, 0, 100, 100), QRectF(20, 20, 60, 40), '#1E293B')
    assert _contrast_ratio(fallback, QColor('#1E293B')) >= 4.5


def test_save_settings_write_failure_falls_back_even_when_clear_key_fails(tmp_path, monkeypatch):
    ini = QSettings(str(tmp_path / 'fallback.ini'), QSettings.Format.IniFormat)
    ini.setValue('translation/sessionOnlyKey', False)
    ini.sync()
    monkeypatch.setattr(keystore, 'write_key', lambda *_: False)
    monkeypatch.setattr(keystore, 'clear_key', lambda *_: False)

    saved = save_settings(
        ini,
        TranslationSettings(api_key='sk-fallback-test', session_only_key=False, privacy_accepted=True),
    )
    assert saved.session_only_key is True
    assert saved.api_key == 'sk-fallback-test'


def test_client_errors_follow_ui_language(tmp_path):
    from app.i18n import LANGUAGE_KEY, current_language, initialize_language

    previous = current_language()
    settings = QSettings(str(tmp_path / 'lang.ini'), QSettings.Format.IniFormat)
    try:
        settings.setValue(LANGUAGE_KEY, 'en')
        initialize_language(settings)
        assert 'Translation settings' in friendly_http_error(401, b'{}', '', is_local=False)
        with pytest.raises(ValueError, match='OpenAI-compatible'):
            extract_openai_content(b'not-json')
    finally:
        settings.setValue(LANGUAGE_KEY, previous)
        initialize_language(settings)


def test_translated_short_line_is_selectable_from_its_trailing_whitespace(translation_window):
    window, controller, manager = translation_window
    from app.pdfedits import selection_range
    from app.translation.visibletext import excerpt

    ref = window.ws.pages[0]
    segments = (
        PageSegment('before', 'body', '', 'First line', (40, 90, 390, 116)),
        PageSegment('short', 'body', '', 'End.', (40, 116, 120, 142)),
        PageSegment('after', 'body', '', 'Next line', (40, 142, 390, 168)),
    )
    texts = {'before': '第一行有较长的内容可以选取。', 'short': '完', 'after': '下一行也有较长的内容可以选取。'}
    controller.cache.put(controller._page_cache_key(ref), CachedPageTranslation(PageExtraction(segments), texts, complete=True))
    controller.panel.overlay_mode_btn.click()
    controller.sync_page(force=True)
    view = window.preview_view
    view.ensureVisible(controller.inline_layer._items[0].boundingRect())
    QApplication.processEvents()
    source = window.store.text_snapshot(ref, include_tables=False)
    snapshot = controller.inline_layer.selection_snapshot(0, source)
    rows = {line['inline_id']: line for block in snapshot['raw']['blocks'] for line in block['lines'] if 'inline_id' in line}
    x0, y0, _, y1 = rows['before']['bbox']
    _, top, right, bottom = rows['short']['bbox']
    first, last = (x0, (y0+y1)/2), (right+130, (top+bottom)/2)
    for start, end in ((first, last), (last, first)):
        # The geometry used by the UI must terminate at the last translated glyph.
        raw, area = selection_range(snapshot['raw'], start, end, snapshot['tables'])
        wanted = (texts['before'] + texts['short'])
        assert ''.join(excerpt(raw, area, snapshot['tables']).split()) == wanted
        _drag_preview(window, start, end)
        assert window.text_selection.selecting_translation
        QTest.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        assert ''.join(QApplication.clipboard().text().split()) == wanted
        assert not controller.mini_bar.isVisible() and not manager.requests
