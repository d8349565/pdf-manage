"""Storage tests use isolated preferences and generated files only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from app import storage, __version__
from app.ocr.contracts import OCRError
from app.ocr.model_manager import MODEL_NAMES, default_runtime_dir, relocate_runtime, validate_runtime
from app.output import OperationCancelled
from app.storageui import RuntimeInstallDialog, StorageDialog


def runtime(root):
    files = {}
    for name in ('runtime/ZhiXuOCR.exe', *(f'models/{name}' for name in MODEL_NAMES)):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'generated-component')
        files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / 'manifest.json').write_text(json.dumps(dict(protocol=1, platform='win_amd64',
        app_series='.'.join(__version__.split('.')[:2]), rapidocr='3.9.2', onnxruntime='1.23.2', files=files)), encoding='utf-8')
    return root


def abandoned(root, suffix='abcdefgh'):
    path = root / f'task-images-{suffix}'
    path.mkdir(parents=True)
    (path / '.zhixu-task').write_text('ZhiXuPDF scratch v1', encoding='ascii')
    (path / 'page.png').write_bytes(b'generated-image')
    return path


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def test_locations_persist_and_defaults_are_backward_compatible(tmp_path):
    assert default_runtime_dir() == tmp_path / 'ZhiXuPDF' / 'ocr'
    assert storage.output_directory() is None
    target = tmp_path / 'other-disk' / 'ZhiXuPDF' / 'ocr'
    storage.save_directory('ocr', target)
    assert default_runtime_dir() == target.resolve()
    assert storage.previous_directories('ocr') == [(tmp_path / 'ZhiXuPDF' / 'ocr').resolve()]
    output = tmp_path / 'exports'
    storage.save_directory('output', output)
    assert storage.export_path(tmp_path / 'source' / 'report.docx') == str(output / 'report.docx')
    storage.save_directory('output', None)
    assert storage.export_path(tmp_path / 'source' / 'report.docx') == str(tmp_path / 'source' / 'report.docx')


def test_active_cache_is_not_cleaned_and_normal_exit_removes_it(tmp_path):
    folder = tmp_path / 'large-cache'
    storage.save_directory('cache', folder)
    with storage.TemporaryWorkspace('ocr') as task:
        assert Path(task).parent == folder
        (Path(task) / 'page.png').write_bytes(b'generated-image')
        report = storage.inspect_storage()
        assert report['active'] == 1
        removed, skipped = storage.clean_storage('cache')
        assert removed == 0 and len(skipped) == 1
        assert (Path(task) / 'page.png').is_file()
    assert not Path(task).exists()


def test_cache_cleanup_preserves_unregistered_files_and_prior_locations(tmp_path):
    old = storage.directory('cache')
    old_task = abandoned(old)
    selected = tmp_path / 'chosen-cache'
    storage.save_directory('cache', selected)
    task = abandoned(selected)
    unrelated = selected / 'original.pdf'
    unrelated.write_bytes(b'preserve-user-output')
    foreign = selected / 'task-images-foreign'
    foreign.mkdir()
    (foreign / '.zhixu-task').write_bytes(b'unknown-owner')
    (foreign / 'original.pdf').write_bytes(b'preserve')
    assert storage.inspect_storage()['cache_count'] == 2
    assert storage.clean_storage('cache') == (2, [])
    assert not old_task.exists() and not task.exists()
    assert unrelated.read_bytes() == b'preserve-user-output' and (foreign / 'original.pdf').read_bytes() == b'preserve'


def test_linked_directory_is_never_followed_or_deleted(tmp_path):
    task = abandoned(storage.directory('cache'))
    outside = tmp_path / 'outside'
    outside.mkdir()
    original = outside / 'source.pdf'
    original.write_bytes(b'preserve-external-file')
    link = task / 'linked'
    if os.name == 'nt':
        result = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(link), str(outside)],
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0
    else:
        link.symlink_to(outside, target_is_directory=True)
    assert storage.clean_storage('cache')[0] == 0
    assert original.read_bytes() == b'preserve-external-file' and task.is_dir()


def test_replaced_cache_root_is_not_followed_during_cleanup(tmp_path):
    outside = tmp_path / 'other-location'
    task = abandoned(outside)
    root = storage.directory('cache')
    root.parent.mkdir(parents=True)
    if os.name == 'nt':
        result = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(root), str(outside)],
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0
    else:
        root.symlink_to(outside, target_is_directory=True)
    assert storage.clean_storage('cache') == (0, [])
    assert (task / 'page.png').read_bytes() == b'generated-image'


def test_verified_backup_cleanup_preserves_current_components_and_unknown_files(tmp_path):
    current = runtime(default_runtime_dir())
    safe = runtime(current.parent / ('.ocr-backup-' + 'a' * 32))
    unknown = runtime(current.parent / ('.ocr-backup-' + 'b' * 32))
    (unknown / 'unregistered.pdf').write_bytes(b'preserve')
    archive = current.parent / 'original.zip'
    archive.write_bytes(b'preserve-original-zip')
    assert storage.inspect_storage()['backup_count'] == 1
    removed, skipped = storage.clean_storage('backup')
    assert removed == 1 and len(skipped) == 1 and not safe.exists()
    validate_runtime(current)
    assert (unknown / 'unregistered.pdf').read_bytes() == b'preserve' and archive.read_bytes() == b'preserve-original-zip'


def test_copy_verify_switch_and_restore_keeps_both_directories(tmp_path, qapp):
    original = runtime(default_runtime_dir())
    target = tmp_path / 'selected-disk' / 'ZhiXuPDF' / 'ocr'
    dialog = StorageDialog()
    try:
        dialog.change_directory('ocr', target)
        assert default_runtime_dir() == target
        assert validate_runtime(original)[1] == validate_runtime(target)[1]
        dialog.restore_default('ocr')
        assert default_runtime_dir() == original and target.is_dir()
        assert dialog.previous.findData(str(target)) >= 0
    finally:
        dialog.close()


def test_migration_cancel_or_checksum_failure_does_not_switch_or_damage_source(tmp_path):
    source = runtime(default_runtime_dir())
    destination = tmp_path / 'new' / 'ocr'
    cancelled = [False]
    def progress(*args):
        cancelled[0] = True
    with pytest.raises(OperationCancelled):
        relocate_runtime(source, destination, progress, lambda: cancelled[0])
    assert default_runtime_dir() == source and not destination.exists()
    validate_runtime(source)
    assert not list(destination.parent.glob('.ocr-install-*'))
    (source / 'models' / MODEL_NAMES[0]).write_bytes(b'corrupt')
    with pytest.raises(OCRError):
        relocate_runtime(source, destination)
    assert not destination.exists()


def test_migration_refuses_unrelated_or_nested_destination(tmp_path):
    source = runtime(default_runtime_dir())
    target = tmp_path / 'other'
    target.mkdir()
    original = target / 'original.pdf'
    original.write_bytes(b'preserve')
    with pytest.raises(OCRError):
        relocate_runtime(source, target)
    assert original.read_bytes() == b'preserve'
    for invalid in (source / 'child', source.parent):
        with pytest.raises(OCRError):
            relocate_runtime(source, invalid)


def test_settings_failure_retains_old_location(tmp_path, monkeypatch):
    original = default_runtime_dir()
    real = storage._settings()
    class FailedSettings:
        value = real.value
        setValue = real.setValue
        sync = real.sync
        def status(self):
            return QSettings.Status.AccessError
    monkeypatch.setattr(storage, '_settings', FailedSettings)
    with pytest.raises(storage.StorageError, match='原位置'):
        storage.save_directory('ocr', tmp_path / 'new')
    assert default_runtime_dir() == original


def test_export_windows_and_install_dialog_show_selected_locations(tmp_path, qapp):
    from app.ocrui import OCRDialog
    from app.wordui import WordConverterWindow
    from app.outputui import MarkdownDialog
    output = tmp_path / 'exports'
    output.mkdir()
    storage.save_directory('output', output)
    ocr = OCRDialog(tmp_path / 'source.pdf', 1, 0)
    word = WordConverterWindow(direction='pdf_to_word')
    install = RuntimeInstallDialog(tmp_path / 'package.zip')
    markdown = MarkdownDialog()
    try:
        assert Path(ocr.output.text()).parent == output
        assert Path(word._target_for(str(tmp_path / 'source.pdf'))).parent == output
        word.reset_output_dir()
        assert Path(word._target_for(str(tmp_path / 'source.pdf'))).parent == tmp_path
        assert install.target.text() == str(default_runtime_dir())
        assert markdown.folder == output
    finally:
        ocr.close()
        word.close()
        install.close()
        markdown.close()


def test_component_cache_and_output_paths_cannot_overlap(tmp_path):
    cache = storage.directory('cache')
    for kind, target in (('ocr', cache / 'nested'), ('cache', default_runtime_dir() / 'nested'),
                         ('output', cache / 'exports')):
        with pytest.raises(storage.StorageError):
            storage.save_directory(kind, target)
    assert storage.output_directory() is None


def test_previous_components_are_kept_while_in_use_then_safely_cleaned(tmp_path):
    original = runtime(default_runtime_dir())
    target = tmp_path / 'new-disk' / 'ocr'
    relocate_runtime(original, target)
    storage.save_directory('ocr', target)
    with storage.TemporaryWorkspace('runtime') as task:
        (Path(task) / 'runtime-path.txt').write_text(str(original), encoding='utf-8')
        assert storage.runtime_in_use(original)
        removed, skipped = storage.clean_storage('previous-ocr')
        assert removed == 0 and len(skipped) == 1
        validate_runtime(original)
    assert not storage.runtime_in_use(original)
    assert storage.clean_storage('previous-ocr') == (1, [])
    assert not original.exists()
    validate_runtime(target)


def test_invalid_current_components_prevent_deleting_recovery_copy(tmp_path):
    original = runtime(default_runtime_dir())
    target = tmp_path / 'new-disk' / 'ocr'
    relocate_runtime(original, target)
    storage.save_directory('ocr', target)
    (target / 'models' / MODEL_NAMES[0]).write_bytes(b'damaged')
    with pytest.raises(OCRError):
        storage.clean_storage('previous-ocr')
    validate_runtime(original)
