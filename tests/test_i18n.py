"""Language matching and catalogue validation; no user documents or preferences."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import i18n


@pytest.fixture(scope='session')
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize(('languages', 'expected'), [
    (['en-US'], 'en'), (['en-GB'], 'en'), (['zh-Hans-CN'], 'zh-CN'),
    (['zh-Hant-HK'], 'zh-TW'), (['zh_MO'], 'zh-TW'), (['zh-TW'], 'zh-TW'),
    (['zh-Hans-HK'], 'zh-CN'), (['zh-Hant-CN'], 'zh-TW'),
    (['zh-CHT'], 'zh-TW'), (['zh-CHS'], 'zh-CN'),
    (['ja-JP'], 'ja'), (['ko-KR'], 'ko'), (['de-DE'], 'de'),
    (['fr-CA'], 'fr'), (['es-MX'], 'es'), (['ru-RU', 'fr-FR'], 'fr'),
    (['ru-RU'], 'en'), ([], 'en'),
])
def test_system_ui_language_matching(languages, expected):
    assert i18n.resolve_language('auto', languages) == expected


def test_explicit_choice_overrides_system_and_invalid_choice_follows_system():
    assert i18n.resolve_language('ja', ['en-US']) == 'ja'
    assert i18n.resolve_language('unknown', ['de-DE']) == 'de'


def test_fallback_and_interpolation_do_not_touch_document_values(monkeypatch):
    monkeypatch.setattr(i18n, '_language', 'ja')
    monkeypatch.setattr(i18n, '_catalogue', {'保存': '保存'})
    monkeypatch.setattr(i18n, '_english', {'保存': 'Save', '位置：{p0}': 'Location: {p0}'})
    assert i18n.tr('保存') == '保存'
    path = 'D:/业务/保存.pdf'
    assert i18n.tr('位置：{p0}', p0=path) == f'Location: {path}'
    assert i18n.tr('新文本') == '新文本'
    monkeypatch.setattr(i18n, '_catalogue', {'位置：{p0}': 'Broken: {wrong}'})
    assert i18n.tr('位置：{p0}', p0=path) == f'位置：{path}'


def test_ellipsis_variants_use_same_words_as_shortcut_metadata(monkeypatch):
    monkeypatch.setattr(i18n, '_language', 'en')
    monkeypatch.setattr(i18n, '_catalogue', {'合并文件': 'Merge files', '合并文件…': 'Combine documents…'})
    monkeypatch.setattr(i18n, '_english', {})
    assert i18n.tr('合并文件') == 'Merge files'
    assert i18n.tr('合并文件…') == 'Merge files…'


def test_broken_catalogue_falls_back_to_english(tmp_path, monkeypatch):
    (tmp_path / 'en.json').write_text(json.dumps({'保存': 'Save'}), encoding='utf-8')
    (tmp_path / 'ja.json').write_text('{broken', encoding='utf-8')
    monkeypatch.setattr(i18n, 'CATALOGUE_DIRECTORY', tmp_path)
    monkeypatch.setattr(i18n, '_language', 'zh-CN')
    monkeypatch.setattr(i18n, '_catalogue', {})
    monkeypatch.setattr(i18n, '_english', {})
    settings = SimpleNamespace(value=lambda key, default: 'ja')
    assert i18n.initialize_language(settings) == 'ja'
    assert i18n.tr('保存') == 'Save'


def test_language_choice_is_saved_and_applied_on_next_launch(qapp, tmp_path, monkeypatch):
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QMessageBox
    from app.languageui import LanguageDialog
    previous = i18n.current_language()
    settings = QSettings(str(tmp_path / 'language.ini'), QSettings.Format.IniFormat)
    i18n.initialize_language(settings, qapp, system_languages=['en-US'])
    dialog = LanguageDialog(None, settings)
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    try:
        dialog.language.setCurrentIndex(dialog.language.findData('ja'))
        dialog.accept()
        assert settings.value(i18n.LANGUAGE_KEY) == 'ja'
        assert i18n.current_language() == 'en'
        i18n.initialize_language(settings, qapp, system_languages=['fr-FR'])
        assert i18n.current_language() == 'ja'
    finally:
        dialog.deleteLater()
        settings.setValue(i18n.LANGUAGE_KEY, previous)
        i18n.initialize_language(settings, qapp)


@pytest.mark.parametrize('language', ['en', 'zh-CN', 'zh-TW', 'ja', 'ko', 'de', 'fr', 'es'])
def test_qt_standard_buttons_follow_selected_language(qapp, language):
    from PyQt6.QtWidgets import QDialogButtonBox
    previous = i18n.current_language()
    try:
        settings = SimpleNamespace(value=lambda key, default: language)
        i18n.initialize_language(settings, qapp)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        caption = box.button(QDialogButtonBox.StandardButton.Cancel).text().replace('&', '')
        assert (caption == 'Cancel') == (language == 'en')
        if language != 'en':
            assert i18n._qt_translator is not None
        box.deleteLater()
    finally:
        i18n.initialize_language(SimpleNamespace(value=lambda key, default: previous), qapp)


def test_catalogue_placeholders_and_source_index():
    import importlib.util
    script = Path(__file__).resolve().parents[1] / 'packaging' / 'sync_translations.py'
    spec = importlib.util.spec_from_file_location('sync_translations', script)
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    assert not sync.unmarked_messages()
    source = sync.messages()
    assert json.loads((i18n.CATALOGUE_DIRECTORY / 'source.json').read_text(encoding='utf-8')) == source
    for path in i18n.CATALOGUE_DIRECTORY.glob('*.json'):
        if path.name == 'source.json':
            continue
        catalogue = json.loads(path.read_text(encoding='utf-8'))
        assert not sync.validate(source, catalogue), path.name
        assert source.keys() <= catalogue.keys(), path.name
