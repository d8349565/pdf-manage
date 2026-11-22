"""Async OpenAI-compatible translation client built on PyQt6 QtNetwork (Windows Schannel TLS)."""
from __future__ import annotations

import json
import re
from time import monotonic

from PyQt6.QtCore import QByteArray, QObject, QTimer, QUrl

from app import APP_SLUG, __version__
from app.i18n import tr
from app.translation.config import (
    DEFAULT_PROVIDER, TARGET_LANGUAGE_MAP, TranslationSettings,
    validate_api_key, validate_endpoint,
)

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
DEFAULT_TIMEOUT_MS = 45_000
TEST_TIMEOUT_MS = 12_000

_THINK_RE = re.compile(r'<think>.*?</think>\s*', re.DOTALL | re.IGNORECASE)
_SEG_RE = re.compile(r'<<<SEG:([^\s>]+)>>>\s*(.*?)\s*<<<END>>>', re.DOTALL)


def target_language_prompt_name(code: str) -> str:
    pair = TARGET_LANGUAGE_MAP.get(code)
    return pair[1] if pair else 'Simplified Chinese'


def clean_model_output(text: str) -> str:
    cleaned = _THINK_RE.sub('', text or '').strip()
    if cleaned.startswith('```') and cleaned.endswith('```'):
        lines = cleaned.splitlines()
        if len(lines) >= 2:
            cleaned = '\n'.join(lines[1:-1]).strip()
    return cleaned


def build_single_messages(text: str, target_lang: str) -> list[dict[str, str]]:
    lang_name = target_language_prompt_name(target_lang)
    system = (
        f'You are a professional document translator. Translate the user text accurately into {lang_name}. '
        'Preserve paragraph breaks, list markers, numbers, units, formulas, code identifiers, URLs, and table structure. '
        'Output ONLY the translation without explanations, greetings, or quotation marks. '
        f'If the input is already in {lang_name}, keep it natural and clear.'
    )
    return [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': text},
    ]


def build_batch_messages(items: list[tuple[str, str]], target_lang: str) -> list[dict[str, str]]:
    lang_name = target_language_prompt_name(target_lang)
    system = (
        f'You are a professional PDF document translator. Translate every marked segment into {lang_name}, '
        'keeping terminology consistent across the page. Preserve numbers, units, formulas, and identifiers. '
        'Return EVERY segment using the exact wrapper format:\n'
        '<<<SEG:id>>>\n<translated text>\n<<<END>>>\n'
        'Do not merge, omit, or reorder segments, and do not output any text outside the wrappers.'
    )
    parts = [f'<<<SEG:{seg_id}>>>\n{text}\n<<<END>>>' for seg_id, text in items]
    return [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': '\n\n'.join(parts)},
    ]


def parse_batch_output(raw: str, expected_ids: list[str]) -> dict[str, str]:
    cleaned = clean_model_output(raw)
    result: dict[str, str] = {}
    expected_set = set(expected_ids)
    for match in _SEG_RE.finditer(cleaned):
        seg_id = match.group(1).strip()
        body = match.group(2).strip()
        if seg_id in expected_set and body:
            result[seg_id] = body
    if not result and len(expected_ids) == 1 and cleaned:
        result[expected_ids[0]] = cleaned
    return result


def extract_openai_content(payload: bytes) -> str:
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError(tr('翻译接口返回内容过大，已安全中止。'))
    try:
        data = json.loads(payload.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(tr('翻译接口返回的数据无法解析，请检查接口地址是否为 OpenAI 兼容协议。')) from exc
    if not isinstance(data, dict):
        raise ValueError(tr('翻译接口返回格式不受支持。'))
    if isinstance(data.get('error'), dict):
        detail = str(data['error'].get('message') or '').strip()
        raise ValueError(
            tr('服务商返回错误：{p0}', p0=detail[:180]) if detail else tr('服务商返回错误，请稍后重试。')
        )
    choices = data.get('choices')
    if not isinstance(choices, list) or not choices:
        raise ValueError(tr('翻译接口未返回译文内容，请检查模型名称是否正确。'))
    first = choices[0]
    if isinstance(first, dict) and first.get('finish_reason') == 'length':
        raise ValueError(tr('服务商截断了译文，请缩小选区或更换支持更长输出的模型。'))
    message = first.get('message') if isinstance(first, dict) else None
    content = message.get('content') if isinstance(message, dict) else None
    if isinstance(content, list):
        content = ''.join(
            part['text'] for part in content if isinstance(part, dict) and isinstance(part.get('text'), str)
        )
    cleaned = clean_model_output(content) if isinstance(content, str) else ''
    if not cleaned:
        raise ValueError(tr('翻译结果为空，请重试或更换模型。'))
    return cleaned


def friendly_http_error(status: int | None, payload: bytes, network_error_name: str, *, is_local: bool) -> str:
    detail = ''
    if payload:
        try:
            data = json.loads(payload.decode('utf-8', 'replace'))
            if isinstance(data, dict):
                err = data.get('error')
                if isinstance(err, dict) and err.get('message'):
                    detail = str(err['message']).strip()
                elif isinstance(err, str):
                    detail = err.strip()
        except ValueError:
            pass
    if status == 401:
        return tr('API Key 无效或已过期，请在“翻译设置”中核对密钥。')
    if status == 402:
        return tr('服务商账户余额不足，请充值或更换其他可用服务。')
    if status == 403:
        return tr('当前账号无权访问该模型或接口，请检查密钥权限。')
    if status == 404:
        return tr('接口地址或模型名称不存在，请在翻译设置中核对。')
    if status == 429:
        return tr('请求过于频繁或已超出速率限制，请稍等片刻后重试。')
    if status is not None and status >= 500:
        return tr('翻译服务暂时不可用（HTTP {p0}），请稍后重试。', p0=str(status))
    if status in (301, 302, 303, 307, 308):
        return tr('接口发生重定向，为保护 API Key 已停止请求，请填写最终接口地址。')
    if 'SslHandshake' in network_error_name:
        return tr('安全连接（TLS）验证失败，请检查系统时间、网络或代理设置。')
    if 'ConnectionRefused' in network_error_name:
        if is_local:
            return tr('无法连接本机翻译服务，请检查服务和端口。')
        return tr('连接被目标服务器拒绝，请检查接口地址与网络。')
    if 'HostNotFound' in network_error_name:
        return tr('无法解析接口域名，请检查网络连接或地址拼写。')
    if 'Timeout' in network_error_name:
        return tr('翻译请求超时，请检查网络或稍后重试。')
    if detail:
        return tr('翻译请求失败：{p0}', p0=detail[:180])
    if status:
        return tr('翻译请求失败（HTTP {p0}），请检查接口配置。', p0=str(status))
    return tr('无法连接翻译服务，请检查网络、代理或接口设置。')


class TranslationRequestHandle:
    """Cancellable handle for a single in-flight QtNetwork request."""

    def __init__(self, abort_fn, reply=None):
        self._abort_fn = abort_fn
        self._reply = reply
        self.cancelled = False

    def cancel(self) -> None:
        if not self.cancelled:
            self.cancelled = True
            self._abort_fn()


class OpenAICompatibleClient(QObject):
    """Non-blocking OpenAI-compatible client using QNetworkAccessManager."""

    def __init__(self, parent=None, *, network_manager=None):
        super().__init__(parent)
        self._manager = network_manager
        self._active: set[TranslationRequestHandle] = set()

    def _ensure_manager(self):
        if self._manager is None:
            from PyQt6.QtNetwork import QNetworkAccessManager
            self._manager = QNetworkAccessManager(self)
        return self._manager

    def cancel_all(self) -> None:
        for handle in list(self._active):
            handle.cancel()
        self._active.clear()

    def _post_chat(
        self,
        config: TranslationSettings,
        messages: list[dict[str, str]],
        *,
        on_raw,
        on_error,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        max_tokens: int | None = None,
    ) -> TranslationRequestHandle | None:
        key = config.api_key.strip()
        try:
            url, is_local = validate_endpoint(config.endpoint, provider=config.provider)
            validate_api_key(key, url, config.model)
        except ValueError as exc:
            on_error(str(exc))
            return None
        model = config.model.strip()
        if not model:
            on_error(tr('请填写模型名称。'))
            return None
        if config.requires_key and not key:
            on_error(tr('请先在“翻译设置”中填写 API Key。'))
            return None
        if not is_local and not config.privacy_accepted:
            on_error(tr('请先在“翻译设置”中阅读并勾选在线翻译数据外发确认。'))
            return None
        if key and any(key in message['content'] for message in messages):
            on_error(tr('待发送的文字包含当前 API Key，已停止发送。'))
            return None

        from PyQt6.QtNetwork import QNetworkReply, QNetworkRequest

        manager = self._ensure_manager()
        request = QNetworkRequest(QUrl(url))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, 'application/json; charset=utf-8')
        request.setRawHeader(b'Accept', b'application/json')
        request.setRawHeader(b'User-Agent', f'{APP_SLUG}/{__version__}'.encode('ascii'))
        if key:
            request.setRawHeader(b'Authorization', f'Bearer {key}'.encode('ascii'))
        # Never forward Authorization header across redirects.
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )
        request.setTransferTimeout(timeout_ms)

        body: dict[str, object] = {
            'model': model,
            'messages': messages,
            'stream': False,
        }
        if config.provider == DEFAULT_PROVIDER:
            body['thinking'] = {'type': 'disabled'}
        if max_tokens is not None:
            if request.url().host() == 'api.openai.com':
                body['max_completion_tokens'] = max(1024, max_tokens)
            else:
                body['max_tokens'] = max_tokens
        encoded = QByteArray(json.dumps(body, ensure_ascii=False).encode('utf-8'))

        started = monotonic()
        reply = manager.post(request, encoded)
        reply.setReadBufferSize(128 * 1024)
        buffer = bytearray()
        state = {'done': False, 'failure': '', 'timed_out': False}

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(timeout_ms)

        def cleanup():
            timer.stop()
            timer.deleteLater()
            self._active.discard(handle)
            reply.deleteLater()

        def abort():
            if not state['done']:
                state['done'] = True
                timer.stop()
                if not reply.isFinished():
                    reply.abort()

        handle = TranslationRequestHandle(abort, reply)
        self._active.add(handle)

        def on_timeout():
            if not state['done']:
                state['timed_out'] = True
                state['failure'] = tr('翻译请求超时，请检查网络或稍后重试。')
                if not reply.isFinished():
                    reply.abort()

        def on_ready_read():
            if reply.bytesAvailable():
                buffer.extend(bytes(reply.readAll()))
                if len(buffer) > MAX_RESPONSE_BYTES:
                    state['failure'] = tr('翻译接口返回内容过大，已安全中止。')
                    if not reply.isFinished():
                        reply.abort()

        def redact(message: str) -> str:
            return message.replace(key, '[API Key]') if key else message

        def on_finished():
            if reply.bytesAvailable():
                buffer.extend(bytes(reply.readAll()))
            cancelled = handle.cancelled
            if state['done'] and cancelled:
                cleanup()
                return
            state['done'] = True
            elapsed_ms = int((monotonic() - started) * 1000)
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            err = reply.error()
            err_name = err.name if hasattr(err, 'name') else str(err)
            payload = bytes(buffer)
            cleanup()

            if state['failure']:
                on_error(state['failure'])
                return
            if err != QNetworkReply.NetworkError.NoError or status != 200:
                on_error(redact(friendly_http_error(status, payload, err_name, is_local=is_local)))
                return
            try:
                content = extract_openai_content(payload)
            except ValueError as exc:
                on_error(redact(str(exc)))
                return
            on_raw(redact(content), elapsed_ms)

        timer.timeout.connect(on_timeout)
        reply.readyRead.connect(on_ready_read)
        reply.finished.connect(on_finished)
        timer.start()
        return handle

    def translate_text(self, config: TranslationSettings, text: str, *, on_success, on_error):
        snippet = (text or '').strip()
        if not snippet:
            on_error(tr('没有可翻译的文字内容。'))
            return None
        messages = build_single_messages(snippet, config.target_language)
        return self._post_chat(
            config,
            messages,
            on_raw=on_success,
            on_error=on_error,
        )

    def translate_batch(
        self,
        config: TranslationSettings,
        items: list[tuple[str, str]],
        *,
        on_success,
        on_error,
    ):
        if not items:
            on_success({}, 0)
            return None
        if len(items) == 1:
            seg_id, text = items[0]
            return self.translate_text(
                config,
                text,
                on_success=lambda translated, ms: on_success({seg_id: translated}, ms),
                on_error=on_error,
            )
        expected_ids = [seg_id for seg_id, _ in items]
        messages = build_batch_messages(items, config.target_language)

        def handle_raw(content: str, elapsed_ms: int):
            parsed = parse_batch_output(content, expected_ids)
            if not parsed:
                on_error(tr('模型未按分段格式返回译文，请重试或更换兼容模型。'))
                return
            on_success(parsed, elapsed_ms)

        return self._post_chat(config, messages, on_raw=handle_raw, on_error=on_error)

    def test_connection(self, config: TranslationSettings, *, on_success, on_error):
        messages = build_single_messages('Hello', config.target_language)
        return self._post_chat(
            config,
            messages,
            on_raw=on_success,
            on_error=on_error,
            timeout_ms=DEFAULT_TIMEOUT_MS if config.is_local else TEST_TIMEOUT_MS,
            max_tokens=64,
        )
