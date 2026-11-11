"""Application text catalogues. Chinese source text is the stable lookup key."""
import json
from pathlib import Path

from PyQt6.QtCore import QLibraryInfo, QLocale, QTranslator

LANGUAGE_KEY = 'interface/language'
LANGUAGES = {
    'auto': '跟随系统 / System default',
    'zh-CN': '简体中文',
    'en': 'English',
    'zh-TW': '繁體中文',
    'ja': '日本語',
    'ko': '한국어',
    'de': 'Deutsch',
    'fr': 'Français',
    'es': 'Español',
}
CATALOGUE_DIRECTORY = Path(__file__).resolve().parent / 'locales'
_language = 'zh-CN'
_catalogue = {}
_english = {}
_qt_translator = None


def resolve_language(preference='auto', system_languages=None):
    """Match UI language preferences, including Chinese script/region variants."""
    if preference in LANGUAGES and preference != 'auto':
        return preference
    for name in QLocale.system().uiLanguages() if system_languages is None else system_languages:
        parts = str(name).replace('_', '-').lower().split('-')
        if parts[0] == 'zh':
            if any(p in ('hant', 'cht') for p in parts):
                return 'zh-TW'
            if any(p in ('hans', 'chs') for p in parts):
                return 'zh-CN'
            return 'zh-TW' if any(p in ('tw', 'hk', 'mo') for p in parts) else 'zh-CN'
        if parts[0] in LANGUAGES:
            return parts[0]
    return 'en'


def current_language():
    return _language


def mark_text(source):
    """Mark untranslated metadata for extraction; call tr() only when displaying it."""
    return source


def _load(language):
    try:
        value = json.loads((CATALOGUE_DIRECTORY / f'{language}.json').read_text(encoding='utf-8'))
        return {key: text for key, text in value.items() if isinstance(key, str) and isinstance(text, str)} if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def initialize_language(settings, application=None, *, system_languages=None):
    global _language, _catalogue, _english, _qt_translator
    _language = resolve_language(settings.value(LANGUAGE_KEY, 'auto'), system_languages)
    _english = _load('en') if _language != 'zh-CN' else {}
    _catalogue = _load(_language) if _language not in ('zh-CN', 'en') else _english
    if application is not None:
        if _qt_translator is not None:
            application.removeTranslator(_qt_translator)
            _qt_translator = None
        translator = QTranslator(application)
        qt_locale = QLocale(_language.replace('-', '_'))
        if translator.load(qt_locale, 'qtbase', '_', QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
            application.installTranslator(translator)
            _qt_translator = translator
    return _language


def tr(source, **values):
    """Translate before interpolation; never translate file paths or document text."""
    translated = source
    if _language != 'zh-CN':
        suffix = '…' if source.endswith('…') else '...' if source.endswith('...') else ''
        key = source[:-len(suffix)] if suffix else source
        # Menus and shortcut metadata must use identical wording with or without ellipses.
        for catalogue in (_catalogue, _english):
            value = catalogue.get(key) or catalogue.get(key + '…') or catalogue.get(key + '...')
            if value:
                translated = value.removesuffix('…').removesuffix('...') + suffix
                break
    if not values:
        return translated
    try:
        return translated.format(**values)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        # A broken translation must not break an operation or hide its warning.
        return source.format(**values)
