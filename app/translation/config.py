"""Translation providers, local preferences, and request validation."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import re
from urllib.parse import parse_qsl, unquote, urlsplit

from PyQt6.QtCore import QUrl

from app.i18n import current_language, mark_text, tr
from app.translation import keystore


SETTINGS_PREFIX = 'translation/'
_LEGACY_KEY = SETTINGS_PREFIX + 'apiKeyProtected'
_SESSION_ONLY = SETTINGS_PREFIX + 'sessionOnlyKey'
_SECRET_QUERY_FIELDS = {'apikey', 'key', 'secret', 'token', 'accesstoken', 'auth', 'authorization', 'password', 'signature'}
DEFAULT_PROVIDER = 'deepseek'
COMPATIBLE_PROVIDER = 'custom'
DEEPSEEK_ENDPOINT = 'https://api.deepseek.com/chat/completions'
DEEPSEEK_MODELS = ('deepseek-flash', 'deepseek-v4-pro')


@dataclass(frozen=True)
class ProviderPreset:
    key: str
    name: str
    endpoint: str
    model: str
    description: str
    console_url: str = ''


PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        key=DEFAULT_PROVIDER,
        name=mark_text('DeepSeek'),
        endpoint=DEEPSEEK_ENDPOINT,
        model=DEEPSEEK_MODELS[0],
        description=mark_text('DeepSeek 官方接口，默认使用 V4.1 Flash。'),
        console_url='https://platform.deepseek.com/api_keys',
    ),
    ProviderPreset(
        key=COMPATIBLE_PROVIDER,
        name=mark_text('OpenAI 兼容'),
        endpoint='',
        model='',
        description=mark_text('填写任意兼容 OpenAI Chat Completions 协议的 HTTPS 或本机地址。'),
    ),
)

PRESETS_BY_KEY = {preset.key: preset for preset in PRESETS}


TARGET_LANGUAGES: tuple[tuple[str, str, str], ...] = (
    ('zh-CN', mark_text('简体中文'), 'Simplified Chinese'),
    ('zh-TW', mark_text('繁體中文'), 'Traditional Chinese'),
    ('en', mark_text('英语'), 'English'),
    ('ja', mark_text('日语'), 'Japanese'),
    ('ko', mark_text('韩语'), 'Korean'),
    ('de', mark_text('德语'), 'German'),
    ('fr', mark_text('法语'), 'French'),
    ('es', mark_text('西班牙语'), 'Spanish'),
)
TARGET_LANGUAGE_MAP = {code: (label, english_name) for code, label, english_name in TARGET_LANGUAGES}


def default_target_language() -> str:
    lang = current_language()
    return lang if lang in TARGET_LANGUAGE_MAP else 'zh-CN'


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    lowered = host.strip().lower().strip('[]')
    return lowered in ('127.0.0.1', 'localhost', '::1')


def normalize_endpoint(raw: str) -> str:
    """Accept base URLs (e.g. https://api.deepseek.com or .../v1) and normalize to chat/completions."""
    value = (raw or '').strip()
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.scheme or not parsed.netloc:
        return value
    path = parsed.path.rstrip('/')
    if not path:
        path = '/chat/completions' if parsed.hostname == 'api.deepseek.com' else '/v1/chat/completions'
    elif re.search(r'/v[0-9]+$', path):
        path += '/chat/completions'
    return parsed._replace(path=path).geturl()


def endpoint_origin(raw: str) -> tuple[str, str, int] | None:
    url = QUrl((raw or '').strip(), QUrl.ParsingMode.StrictMode)
    scheme, host = url.scheme(), url.host()
    if not url.isValid() or not host or '@' in url.authority() or url.hasFragment():
        return None
    port = url.port(443 if scheme == 'https' else 80)
    return scheme, host, port


def _is_deepseek_chat_endpoint(url: str) -> bool:
    if endpoint_origin(url) != ('https', 'api.deepseek.com', 443):
        return False
    parsed = urlsplit(url)
    return parsed.path in ('/chat/completions', '/v1/chat/completions') and not parsed.query


def validate_endpoint(raw: str, *, provider: str = COMPATIBLE_PROVIDER) -> tuple[str, bool]:
    """Validate URL safety. Returns (normalized_url, is_local). Raises ValueError if unsafe."""
    normalized = normalize_endpoint(raw)
    if not normalized:
        raise ValueError(tr('请填写接口地址。'))
    origin = endpoint_origin(raw)
    if origin is None:
        raise ValueError(tr('接口地址格式无效，请检查是否包含完整域名或本机地址。'))
    if any(name.casefold().replace('-', '').replace('_', '') in _SECRET_QUERY_FIELDS
           for name, _ in parse_qsl(urlsplit(normalized).query, keep_blank_values=True)):
        raise ValueError(tr('请把密钥填在 API Key 输入框。'))
    if provider == DEFAULT_PROVIDER:
        if not _is_deepseek_chat_endpoint(normalized):
            raise ValueError(tr('其他服务地址请填写在“OpenAI 兼容”中。'))
        return DEEPSEEK_ENDPOINT, False
    scheme, host, _ = origin
    local = is_loopback_host(host)
    if scheme == 'https':
        return normalized, local
    if scheme == 'http' and local:
        return normalized, True
    raise ValueError(tr('为保护密钥和文档内容，在线接口必须使用 https://。本机地址可以使用 http://。'))


def validate_api_key(api_key: str, endpoint: str, model: str) -> None:
    key = api_key.strip()
    if key and not endpoint.lower().startswith('https://'):
        raise ValueError(tr('使用 API Key 时，接口必须使用 HTTPS。'))
    if key and (key in unquote(endpoint) or key in model):
        raise ValueError(tr('请把密钥填在 API Key 输入框。'))
    if any(not 32 < ord(character) < 127 for character in key):
        raise ValueError(tr('API Key 包含空格或无效字符。'))


@dataclass(frozen=True)
class TranslationSettings:
    provider: str = DEFAULT_PROVIDER
    endpoint: str = PRESETS_BY_KEY[DEFAULT_PROVIDER].endpoint
    model: str = PRESETS_BY_KEY[DEFAULT_PROVIDER].model
    api_key: str = field(default='', repr=False)
    session_only_key: bool = True
    privacy_accepted: bool = False
    target_language: str = 'zh-CN'
    view_mode: str = 'side'  # 'side' | 'overlay'
    auto_translate_selection: bool = False
    show_bilingual: bool = False
    skip_header_footer: bool = True
    auto_translate_on_page_turn: bool = False

    @property
    def is_local(self) -> bool:
        try:
            _, local = validate_endpoint(self.endpoint, provider=self.provider)
            return local
        except ValueError:
            return False

    @property
    def requires_key(self) -> bool:
        return self.provider == DEFAULT_PROVIDER or endpoint_origin(self.endpoint) == ('https', 'api.openai.com', 443)

    @property
    def is_ready(self) -> bool:
        try:
            _, local = validate_endpoint(self.endpoint, provider=self.provider)
            validate_api_key(self.api_key, self.endpoint, self.model)
        except ValueError:
            return False
        if not self.model.strip():
            return False
        if self.requires_key and not self.api_key.strip():
            return False
        if not local and not self.privacy_accepted:
            return False
        return True

    @property
    def needs_api_key(self) -> bool:
        return self.privacy_accepted and self.requires_key and not self.api_key.strip()

    @property
    def provider_label(self) -> str:
        preset = PRESETS_BY_KEY.get(self.provider)
        return preset.name if preset else self.provider

    @property
    def cache_signature(self) -> tuple[str, str, str, str]:
        return (self.provider, normalize_endpoint(self.endpoint), self.model.strip(), self.target_language)

    @property
    def credential_recipient(self) -> str:
        origin = endpoint_origin(self.endpoint)
        if origin is None:
            return ''
        scheme, host, port = origin
        return f'{self.provider} {scheme}://{host}:{port}'


def load_settings(qsettings, *, runtime_key: str = '') -> TranslationSettings:
    stored_provider = str(qsettings.value(SETTINGS_PREFIX + 'provider', DEFAULT_PROVIDER) or DEFAULT_PROVIDER)
    provider = stored_provider if stored_provider in PRESETS_BY_KEY else COMPATIBLE_PROVIDER
    preset = PRESETS_BY_KEY[provider]
    endpoint = str(qsettings.value(SETTINGS_PREFIX + 'endpoint', preset.endpoint) or preset.endpoint).strip()
    model = str(qsettings.value(SETTINGS_PREFIX + 'model', preset.model) or preset.model).strip()
    if provider == DEFAULT_PROVIDER:
        if _is_deepseek_chat_endpoint(normalize_endpoint(endpoint)):
            endpoint = DEEPSEEK_ENDPOINT
            if model not in DEEPSEEK_MODELS:
                model = DEEPSEEK_MODELS[0]
        else:
            provider = COMPATIBLE_PROVIDER
    session_only = qsettings.value(_SESSION_ONLY, not qsettings.contains(_LEGACY_KEY), type=bool)
    privacy = qsettings.value(SETTINGS_PREFIX + 'privacyAccepted', False, type=bool)
    target_lang = str(qsettings.value(SETTINGS_PREFIX + 'targetLanguage', '') or '')
    if target_lang not in TARGET_LANGUAGE_MAP:
        target_lang = default_target_language()
    view_mode = str(qsettings.value(SETTINGS_PREFIX + 'viewMode', 'side') or 'side')
    if view_mode not in ('side', 'overlay'):
        view_mode = 'side'
    config = TranslationSettings(
        provider=provider,
        endpoint=endpoint,
        model=model,
        api_key=runtime_key,
        session_only_key=session_only,
        privacy_accepted=privacy and stored_provider == provider,
        target_language=target_lang,
        view_mode=view_mode,
        auto_translate_selection=qsettings.value(SETTINGS_PREFIX + 'autoTranslateSelection', False, type=bool),
        show_bilingual=qsettings.value(SETTINGS_PREFIX + 'showBilingual', False, type=bool),
        skip_header_footer=qsettings.value(SETTINGS_PREFIX + 'skipHeaderFooter', True, type=bool),
        auto_translate_on_page_turn=qsettings.value(SETTINGS_PREFIX + 'autoTranslateOnPageTurn', False, type=bool),
    )
    if session_only or not config.credential_recipient:
        return config
    profile = qsettings.fileName()
    # A retired preset's key can only be recovered from its original provider
    # and origin. Save will bind it to the compatible preset at the same origin.
    recipient = replace(config, provider=stored_provider).credential_recipient
    key = keystore.read_key(profile, recipient)
    if key:
        return replace(config, api_key=key, privacy_accepted=privacy)
    legacy = keystore.read_legacy_key(str(qsettings.value(_LEGACY_KEY, '') or ''))
    if not legacy:
        return config
    migrated = keystore.write_key(profile, recipient, legacy)
    if migrated:
        qsettings.remove(_LEGACY_KEY)
        qsettings.setValue(SETTINGS_PREFIX + 'privacyAccepted', False)
        qsettings.sync()
    return replace(config, api_key=legacy, privacy_accepted=False, session_only_key=not migrated)


def save_settings(qsettings, config: TranslationSettings, *, update_key: bool = True) -> TranslationSettings:
    normalized_endpoint = normalize_endpoint(config.endpoint) if config.endpoint.strip() else ''
    saved = replace(config, endpoint=normalized_endpoint, model=config.model.strip(), api_key=config.api_key.strip())
    if update_key:
        if saved.endpoint or saved.provider == DEFAULT_PROVIDER:
            endpoint, _ = validate_endpoint(saved.endpoint, provider=saved.provider)
            saved = replace(saved, endpoint=endpoint)
        validate_api_key(saved.api_key, saved.endpoint, saved.model)
        profile = qsettings.fileName()
        write_failed = False
        if saved.api_key and not saved.session_only_key:
            if not keystore.write_key(profile, saved.credential_recipient, saved.api_key):
                saved = replace(saved, session_only_key=True)
                write_failed = True
        if saved.session_only_key or not saved.api_key:
            had_saved = not qsettings.value(_SESSION_ONLY, True, type=bool)
            if had_saved and not keystore.clear_key(profile) and not write_failed:
                raise ValueError(tr('无法清除 Windows 中已保存的 API Key。请重试。'))
        qsettings.remove(_LEGACY_KEY)
        qsettings.setValue(_SESSION_ONLY, saved.session_only_key)
        qsettings.setValue(SETTINGS_PREFIX + 'provider', saved.provider)
        qsettings.setValue(SETTINGS_PREFIX + 'endpoint', saved.endpoint)
        qsettings.setValue(SETTINGS_PREFIX + 'model', saved.model)
        qsettings.setValue(SETTINGS_PREFIX + 'privacyAccepted', saved.privacy_accepted)
    qsettings.setValue(SETTINGS_PREFIX + 'targetLanguage', saved.target_language)
    qsettings.setValue(SETTINGS_PREFIX + 'viewMode', saved.view_mode)
    qsettings.setValue(SETTINGS_PREFIX + 'autoTranslateSelection', saved.auto_translate_selection)
    qsettings.setValue(SETTINGS_PREFIX + 'showBilingual', saved.show_bilingual)
    qsettings.setValue(SETTINGS_PREFIX + 'skipHeaderFooter', saved.skip_header_footer)
    qsettings.setValue(SETTINGS_PREFIX + 'autoTranslateOnPageTurn', saved.auto_translate_on_page_turn)
    qsettings.sync()
    return saved
