"""Word shell checks use generated paths and an in-memory registry only."""
from copy import deepcopy
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from app import windows_integration
from test_portable_integration import MemoryRegistry, executable, manager


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('portable', [False, True], ids=['installed', 'portable'])
def test_word_menus_share_a_multiselect_target_and_repair_old_commands(tmp_path, portable):
    registry = MemoryRegistry()
    menus = manager(executable(tmp_path / 'program', portable), registry)
    plan = menus.plan
    roots = [root for root in plan.trees if root.endswith('.WordPDF')
             and '\\SystemFileAssociations\\' in root]
    assert len(roots) == 2
    for root in roots:
        tree = plan.trees[root]
        assert tree['']['MultiSelectModel'] == 'Player'
        assert 'command' not in tree
        assert tree['DropTarget']['CLSID'] == plan.word_clsid
    server_root = 'Software\\Classes\\CLSID\\' + plan.word_clsid
    assert plan.trees[server_root]['LocalServer32'][''] == (
        f'"{plan.executable}" --word-shell-server --context-id {plan.word_clsid}')
    folder = next(tree for root, tree in plan.trees.items() if '\\Directory\\' in root)
    assert folder['']['MultiSelectModel'] == 'Single'
    assert '--word-to-pdf "%1"' in folder['command']['']

    menus.enable()
    for root in roots:
        registry.delete(root + '\\DropTarget')
        registry.set(root, 'MultiSelectModel', 'Single')
        registry.set(root + '\\command', '', 'generated old single-file command')
    registry.delete(server_root)
    assert menus.status() == 'repair'
    before = deepcopy(registry.data)
    registry.fail_after = 1
    with pytest.raises(PermissionError):
        menus.repair()
    assert registry.data == before
    menus.repair()
    assert menus.status() == 'enabled'
    assert all(registry.snapshot(root + '\\command') is None for root in roots)
    menus.remove()
    assert menus.status() == 'off' and not registry.data


def test_installer_word_menus_share_a_target_and_remove_single_file_commands():
    text = (Path(__file__).resolve().parents[1] / 'packaging/pdf-manage.iss').read_text(encoding='utf-8')
    lines = text.splitlines()
    for extension in ('.doc', '.docx'):
        root = f'Software\\Classes\\SystemFileAssociations\\{extension}\\shell\\PageArkWordToPDF'
        entries = [line for line in lines if f'Subkey: "{root}";' in line]
        assert any('ValueName: "MultiSelectModel"; ValueData: "Player"' in line for line in entries)
        assert not any('ValueData: "Single"' in line for line in entries)
        assert any(f'Subkey: "{root}\\command";' in line and 'Flags: deletekey' in line
                   and 'Tasks: contextmenus' in line for line in lines)
        assert any(f'Subkey: "{root}\\DropTarget";' in line
                   and f'ValueData: "{{{windows_integration.WORD_PDF_CLSID}"' in line for line in lines)
    server_root = 'Software\\Classes\\CLSID\\{' + windows_integration.WORD_PDF_CLSID
    assert any(f'Subkey: "{server_root}\\LocalServer32";' in line
               and '--word-shell-server' in line for line in lines)
    assert any(f'Subkey: "{server_root}\\PythonCOM";' in line
               and 'app.pdfshell.MergeDropTarget' in line for line in lines)
    assert any(f'Subkey: "{server_root}";' in line and 'uninsdeletekey' in line for line in lines)
    assert any(f'Subkey: "{server_root}";' in line and 'Flags: deletekey' in line
               and 'Tasks: not contextmenus' in line for line in lines)


@pytest.mark.parametrize('suffixes', [('.doc',), ('.doc', '.doc'), ('.docx', '.docx'), ('.doc', '.DOCX')],
                         ids=['single', 'docs', 'docxs', 'mixed'])
def test_word_drop_opens_one_window_with_the_complete_selection(qapp, tmp_path, monkeypatch, suffixes):
    pythoncom = pytest.importorskip('pythoncom')
    from win32com.shell import shellcon
    from app import pdfshell, ui
    from app.wordui import WordConverterWindow

    # A routing regression must fail without opening the PDF loader or updater.
    class PDFWindow(QWidget):
        def __init__(self, **kwargs):
            super().__init__()
            self.updates = SimpleNamespace(start=lambda: None)
            self.open_startup_files = lambda *args: None
    monkeypatch.setattr(ui, 'MainWindow', PDFWindow)
    sources = [tmp_path / f'文档 & 方案 {index}{suffix}' for index, suffix in enumerate(suffixes)]
    original = b'Generated Word placeholder; conversion must not start.'
    for source in sources:
        source.write_bytes(original)
    paths = list(map(str, sources))
    medium = pythoncom.STGMEDIUM()
    medium.set(pythoncom.TYMED_HGLOBAL,
               struct.pack('<IiiII', 20, 0, 0, 0, 1) + ('\0'.join(paths) + '\0\0').encode('utf-16le'))
    data = SimpleNamespace(QueryGetData=lambda _: None, GetData=lambda _: medium)
    controller = pdfshell._ShellController(qapp, 'word')
    monkeypatch.setattr(pdfshell, '_controller', controller)
    try:
        target = pdfshell.MergeDropTarget()
        effect = shellcon.DROPEFFECT_COPY | shellcon.DROPEFFECT_MOVE
        assert target.DragEnter(data, 0, (0, 0), effect) == shellcon.DROPEFFECT_COPY
        assert target.Drop(data, 0, (0, 0), effect) == shellcon.DROPEFFECT_COPY
        for _ in range(3):
            qapp.processEvents()
        assert len(controller.windows) == 1
        window = controller.windows[0]
        assert isinstance(window, WordConverterWindow)
        assert window.direction == 'word_to_pdf' and window.inputs == paths
        assert [window.table.item(row, 0).text() for row in range(window.table.rowCount())] == paths
        assert not controller.idle_timer.isActive() and window._task_dialog is None
        assert all(source.read_bytes() == original and not source.with_suffix('.pdf').exists() for source in sources)
    finally:
        for window in list(controller.windows):
            window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()
        controller.idle_timer.stop()
        controller.deleteLater()
    assert not controller.windows


@pytest.mark.parametrize('portable, context', [(False, 'legacy'), (False, 'owned'),
                                               (True, 'owned'), (True, 'foreign')])
def test_word_shell_startup_routes_the_matching_edition(qapp, tmp_path, monkeypatch, portable, context):
    pytest.importorskip('pythoncom')
    from app import __main__, instance, pdfshell, runtime
    from app.contextmenus import registration_plan

    exe = executable(tmp_path / 'program', portable)
    plan = registration_plan(exe)
    clsid = None if context == 'legacy' else plan.word_clsid
    if context == 'foreign':
        clsid = registration_plan(tmp_path / 'other' / exe.name, portable=portable).word_clsid
    monkeypatch.setattr(sys, 'executable', str(exe))
    monkeypatch.setattr(sys, 'argv', [str(exe), '--word-shell-server']
                        + (['--context-id', clsid] if clsid else []))
    monkeypatch.setattr(runtime, 'application_directory', lambda: exe.parent)
    monkeypatch.setattr(instance, 'InstanceRouter', lambda *_: pytest.fail('Word shell entered the PDF router'))
    calls, messages = [], []
    monkeypatch.setattr(pdfshell, 'serve', lambda app, **kwargs: calls.append(kwargs) or 0)
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: messages.append(args[-1]))
    with pytest.raises(SystemExit) as result:
        __main__.main()
    if context == 'foreign':
        assert result.value.code == 1 and not calls and messages
    else:
        assert result.value.code == 0 and not messages
        assert calls == [{'clsid': clsid, 'mode': 'word'}]
