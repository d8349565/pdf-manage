"""Package/UI checks use generated files and a memory-only registry."""
from pathlib import Path
import importlib.util
import json
from time import monotonic
import zipfile

from PIL import Image
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog

from app.contextmenuui import ContextMenuDialog
from app.contextmenus import ContextMenus
from app.ui import MainWindow
from app import updates
from test_portable_integration import MemoryRegistry, executable, manager


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def test_context_controls_are_opt_in_and_only_change_the_injected_edition(qapp, tmp_path):
    registry = MemoryRegistry()
    installed = manager(executable(tmp_path / 'installed'), registry)
    portable = manager(executable(tmp_path / 'portable', True), registry)
    installed.enable()
    dialog = ContextMenuDialog(manager=portable)
    dialog.show()
    try:
        assert '未启用' in dialog.status.text() and portable.status() == 'off'
        assert dialog.actions['enable'].isEnabled() and not dialog.actions['remove'].isEnabled()
        QTest.mouseClick(dialog.actions['enable'], Qt.MouseButton.LeftButton)
        assert portable.status() == installed.status() == 'enabled'
        assert '已启用' in dialog.feedback.text() and dialog.actions['repair'].isEnabled()
        root = next(root for root, tree in portable.plan.trees.items() if 'command' in tree)
        registry.delete(root + '\\command')
        dialog.refresh()
        assert '修复' in dialog.status.text()
        QTest.mouseClick(dialog.actions['repair'], Qt.MouseButton.LeftButton)
        assert portable.status() == 'enabled'
        QTest.mouseClick(dialog.actions['remove'], Qt.MouseButton.LeftButton)
        assert portable.status() == 'off' and installed.status() == 'enabled'
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_image_compression_menu_runs_without_document_or_registry(qapp, tmp_path, monkeypatch):
    from app.imagequick import ImageQuickDialog
    source = tmp_path / 'generated.png'
    Image.new('RGB', (320, 240), '#39A480').save(source, 'PNG', compress_level=0)
    original = source.read_bytes()
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(source), ''))
    outputs = []
    def run(self):
        self.show()
        deadline = monotonic() + 10
        while self.finish.text() != '完成' and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        assert self.worker.error is None and self.output
        outputs.append(self.output)
        self.reject()
        return 0
    monkeypatch.setattr(ImageQuickDialog, 'exec', run)
    window = MainWindow()
    try:
        menu = next(action.menu() for action in window._menubar.actions() if action.text() == '转换')
        action = next(action for action in menu.actions() if action.text() == '压缩图片…')
        assert action.isEnabled() and not window.ws.pages
        action.trigger()
        assert outputs and Path(outputs[0]).is_file() and source.read_bytes() == original
        file_menu = next(action.menu() for action in window._menubar.actions() if action.text() == '文件')
        assert not any(action.text() == '右键快捷工具…' for action in file_menu.actions())
    finally:
        window.ws.reset()
        window.close()
        qapp.processEvents()


def test_portable_zip_contains_dependencies_and_marker_but_no_data(tmp_path):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('make_portable', root / 'packaging/make_portable.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / 'program'
    executable(source)
    (source / '_internal').mkdir()
    (source / '_internal/dependency.dll').write_bytes(b'generated dependency')
    (source / 'Data').mkdir()
    (source / 'Data/preferences.ini').write_text('private generated settings', encoding='utf-8')
    (source / 'private.pdf').write_bytes(b'must not be packaged')
    target = tmp_path / 'portable.zip'
    report = module.make_portable(source, target)
    assert report['bytes'] == target.stat().st_size
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        assert 'ZhiXuPDF-Portable/ZhiXuPDF.exe' in names
        assert 'ZhiXuPDF-Portable/_internal/dependency.dll' in names
        assert 'ZhiXuPDF-Portable/portable.ini' in names
        assert not any('Data/' in name or 'private.pdf' in name for name in names)


def test_portable_update_checks_the_matching_zip_instead_of_an_installer():
    base = dict(tag_name='v99999.0.0', draft=False, prerelease=False)
    payload = dict(base, assets=[dict(name='ZhiXuPDF-Setup.exe', state='uploaded', size=123)])
    assert updates.release_tag(json.dumps(payload).encode(), portable=False) == 'v99999.0.0'
    with pytest.raises(ValueError, match='绿色 ZIP'):
        updates.release_tag(json.dumps(payload).encode(), portable=True)
    payload['assets'].append(dict(name='ZhiXuPDF-Portable-v99999.0.0.zip', state='uploaded', size=123))
    assert updates.release_tag(json.dumps(payload).encode(), portable=True) == 'v99999.0.0'


def test_portable_about_offers_both_github_and_lanzou_downloads(qapp, tmp_path, monkeypatch):
    from PyQt6.QtCore import QSettings
    from app import runtime
    from app.aboutui import AboutDialog
    exe = executable(tmp_path / 'portable', True)
    monkeypatch.setattr(runtime, 'application_directory', lambda: exe.parent)
    checker = updates.UpdateChecker(QSettings(str(tmp_path / 'updates.ini'), QSettings.Format.IniFormat))
    dialog = AboutDialog(updates=checker)
    dialog.show()
    try:
        assert dialog.lanzou_button.isVisible()
        assert '绿色版' in dialog.get_update_button.text()
    finally:
        dialog.close()
        checker.stop()
        dialog.deleteLater()
        qapp.processEvents()


def test_installer_keeps_the_existing_installation_and_context_menu_flow():
    text = (Path(__file__).resolve().parents[1] / 'packaging/pdf-manage.iss').read_text(encoding='utf-8')
    task = next(line for line in text.splitlines() if line.startswith('Name: "contextmenus"'))
    assert '启用右键快捷工具' in task
    assert '--context-menu' not in text
    assert 'SystemFileAssociations' in text and 'PageArkWordToPDF' in text
    assert 'portable.ini' not in text
