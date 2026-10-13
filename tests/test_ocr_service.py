"""File safety tests use generated documents and deterministic OCR lines."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import zipfile

import pymupdf as p
import pytest

from app import __version__
from app.ocr.contracts import OCROptions, OCRLine, OCRError
from app.ocr.model_manager import MODEL_NAMES, install_runtime, safe_member, validate_runtime
from app.ocr.service import run_ocr
from app.output import OperationCancelled


def make_runtime(root):
    files = {}
    for name in ('runtime/ZhiXuOCR.exe', *(f'models/{name}' for name in MODEL_NAMES)):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'test-only-not-executable')
        files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = dict(protocol=1, platform='win_amd64', app_series='.'.join(__version__.split('.')[:2]),
                    rapidocr='3.9.2', onnxruntime='1.23.2', files=files)
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return root


def test_install_retries_transient_windows_directory_lock(tmp_path,monkeypatch):
    source=make_runtime(tmp_path/'package'); archive=tmp_path/'package.zip'
    with zipfile.ZipFile(archive,'w') as bundle:
        for path in source.rglob('*'):
            if path.is_file():bundle.write(path,path.relative_to(source).as_posix())
    target=tmp_path/'installed'; original=Path.rename; attempts=[]
    def locked(self,destination):
        if self.name.startswith('.ocr-install-') and len(attempts)<2:
            attempts.append(1)
            raise PermissionError('Temporary directory lock')
        return original(self,destination)
    monkeypatch.setattr(Path,'rename',locked)
    monkeypatch.setattr('app.ocr.model_manager.time.sleep',lambda seconds:None)
    install_runtime(archive,directory=target)
    validate_runtime(target)
    assert len(attempts)==2 and not list(tmp_path.glob('.ocr-install-*'))


def test_cancel_during_install_commit_restores_previous_directory(tmp_path,monkeypatch):
    source=make_runtime(tmp_path/'package'); archive=tmp_path/'package.zip'
    with zipfile.ZipFile(archive,'w') as bundle:
        for path in source.rglob('*'):
            if path.is_file():bundle.write(path,path.relative_to(source).as_posix())
    target=make_runtime(tmp_path/'installed'); (target/'previous.txt').write_bytes(b'preserve')
    cancelled=[False]; original=Path.rename
    def locked(self,destination):
        if self.name.startswith('.ocr-install-'):
            cancelled[0]=True
            raise PermissionError('Temporary directory lock')
        return original(self,destination)
    monkeypatch.setattr(Path,'rename',locked)
    monkeypatch.setattr('app.ocr.model_manager.time.sleep',lambda seconds:None)
    with pytest.raises(OperationCancelled):
        install_runtime(archive,directory=target,cancel=lambda:cancelled[0])
    assert (target/'previous.txt').read_bytes()==b'preserve'
    assert not list(tmp_path.glob('.ocr-install-*')) and not list(tmp_path.glob('.ocr-backup-*'))


def make_pdf(path, *, password=None, permissions=4095, signature=False):
    with p.open() as doc:
        doc.new_page()
        doc.new_page().insert_text((60, 80), 'Existing searchable electronic text')
        if signature:
            sig = doc.get_new_xref()
            doc.update_object(sig, '<< /Type /Sig /ByteRange [0 1 2 3] /Contents <11223344> >>')
            doc.xref_set_key(doc.pdf_catalog(), 'Perms', f'<< /DocMDP {sig} 0 R >>')
        doc.set_toc([[1, 'Synthetic bookmark', 1]])
        doc[0].add_text_annot((30, 30), 'Preserve this annotation')
        doc[0].insert_link({'kind': p.LINK_URI, 'from': p.Rect(20, 150, 150, 170), 'uri': 'https://example.com'})
        doc.set_page_labels([dict(startpage=0, style='D', prefix='Page-')])
        options = dict(encryption=p.PDF_ENCRYPT_AES_256, user_pw=password,
                       owner_pw='owner-test', permissions=permissions) if password else {}
        doc.save(path, **options)


class DeterministicEngine:
    closed = False
    fail = False
    def __init__(self, root, cancel=None):
        self.cancel = cancel
    def recognize(self, image, index):
        if self.fail:
            raise OCRError('合成单页失败')
        return [OCRLine('中文预算 12345.67', (100, 100, 550, 145),
                        (100, 100, 550, 100, 550, 145, 100, 145), .99, index)]
    def close(self):
        type(self).closed = True


def test_cancel_closes_image_reader_before_removing_scratch(job, monkeypatch):
    source, target, options = job
    cancelled = [False]
    class LockedReader(DeterministicEngine):
        stream = None
        def recognize(self, image, index):
            self.stream = Path(image).open('rb')
            cancelled[0] = True
            return super().recognize(image, index)
        def close(self):
            if self.stream:
                self.stream.close()
    monkeypatch.setattr('app.ocr.service.OCREngine', LockedReader)
    with pytest.raises(OperationCancelled):
        run_ocr(source, target, [0], options, cancel_check=lambda: cancelled[0])
    assert not target.exists()


@pytest.fixture
def job(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path / 'runtime')
    source = tmp_path / 'source.pdf'
    make_pdf(source)
    monkeypatch.setattr('app.ocr.service.OCREngine', DeterministicEngine)
    monkeypatch.setattr(DeterministicEngine, 'fail', False)
    monkeypatch.setattr(DeterministicEngine, 'closed', False)
    return source, tmp_path / 'output.pdf', OCROptions(runtime_dir=str(runtime))


def test_output_retains_source_structure_and_search(job):
    source, output, options = job
    before = source.read_bytes()
    result = run_ocr(source, output, [0, 1], options)
    assert result.processed_pages == (0,) and result.skipped_pages == (1,)
    assert not result.failed_pages and DeterministicEngine.closed
    assert source.read_bytes() == before
    with p.open(source) as original, p.open(output) as generated:
        assert '中文预算' in generated[0].get_text()
        assert generated[0].search_for('12345.67')
        assert original.get_toc() == generated.get_toc()
        assert original.get_page_labels() == generated.get_page_labels()
        assert len(list(generated[0].annots())) == 1
        assert generated[0].get_links()[0]['uri'] == 'https://example.com'
        assert original[0].get_pixmap().samples == generated[0].get_pixmap().samples


def test_cancellation_before_commit_removes_temporary_files(job):
    source, output, options = job
    cancelled = False
    def progress(current, total, message):
        nonlocal cancelled
        if current == total == 2:
            cancelled = True
    with pytest.raises(OperationCancelled):
        run_ocr(source, output, [0, 1], options, progress, lambda: cancelled)
    assert not output.exists() and not list(output.parent.glob('.zhixu-ocr-*'))
    assert DeterministicEngine.closed


def test_never_overwrites_source_existing_target_or_alias(job):
    source, output, options = job
    with pytest.raises(OCRError, match='原 PDF'):
        run_ocr(source, source, [0], options)
    output.write_bytes(b'existing-file')
    with pytest.raises(OCRError, match='已存在'):
        run_ocr(source, output, [0], options)
    assert output.read_bytes() == b'existing-file'


def test_target_created_during_processing_is_retained(job):
    source, output, options = job
    def progress(current, total, message):
        if current == total == 2:
            output.write_bytes(b'created-concurrently')
    with pytest.raises(OCRError):
        run_ocr(source, output, [0, 1], options, progress)
    assert output.read_bytes() == b'created-concurrently'
    assert not list(output.parent.glob('.zhixu-ocr-*'))


def test_single_page_failure_continues(job, monkeypatch):
    source, output, options = job
    def recognize(self, image, index):
        if index == 0:
            raise OCRError('合成单页失败')
        return [OCRLine('第二页新文字', (100, 300, 550, 345),
                        (100, 300, 550, 300, 550, 345, 100, 345), .99, index)]
    monkeypatch.setattr(DeterministicEngine, 'recognize', recognize)
    result = run_ocr(source, output, [0, 1], replace(options, skip_existing_text=False))
    assert result.processed_pages == (1,) and result.failed_pages == ((0, '合成单页失败'),)
    with p.open(output) as doc:
        assert not doc[0].get_text().strip()
        assert '第二页新文字' in doc[1].get_text()


def test_all_failed_pages_produce_no_final_pdf(job, monkeypatch):
    source, output, options = job
    monkeypatch.setattr(DeterministicEngine, 'fail', True)
    with pytest.raises(OCRError, match='未生成'):
        run_ocr(source, output, [0], options)
    assert not output.exists()


@pytest.mark.parametrize('signature,permissions,password', [(True, 4095, None), (False, p.PDF_PERM_PRINT, 'user')])
def test_signature_and_insufficient_permissions_are_rejected(job, signature, permissions, password):
    source, output, options = job
    source.unlink()
    make_pdf(source, signature=signature, permissions=permissions, password=password)
    with pytest.raises(OCRError, match='数字签名|不允许'):
        run_ocr(source, output, [0], replace(options, password=password))
    assert not output.exists()


def test_encryption_is_kept(job):
    source, output, options = job
    source.unlink()
    make_pdf(source, password='user')
    with pytest.raises(OCRError, match='密码'):
        run_ocr(source, output, [0], options)
    run_ocr(source, output, [0], replace(options, password='user'))
    with p.open(output) as doc:
        assert doc.needs_pass and doc.authenticate('user') > 0
        assert doc[0].search_for('中文预算')


@pytest.mark.parametrize('name', ['../bad', '/bad', 'C:/bad', 'a\\bad', 'a:bad', 'CON.txt', 'a./bad', 'a//b'])
def test_unsafe_bundle_paths_rejected(name):
    assert not safe_member(name)


def test_runtime_missing_corrupt_or_unlisted_files(tmp_path):
    runtime = make_runtime(tmp_path / 'runtime')
    validate_runtime(runtime)
    path = runtime / 'models' / MODEL_NAMES[0]
    path.write_bytes(b'bad')
    with pytest.raises(OCRError, match='校验'):
        validate_runtime(runtime)
    path.unlink()
    with pytest.raises(OCRError):
        validate_runtime(runtime)


def test_install_validates_before_replacing_and_retains_backup(tmp_path):
    source = make_runtime(tmp_path / 'source')
    archive = tmp_path / 'package.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        for path in source.rglob('*'):
            if path.is_file():
                bundle.write(path, path.relative_to(source).as_posix())
    target = tmp_path / 'installed'
    target.mkdir()
    (target / 'old').write_text('old installation')
    install_runtime(archive, directory=target)
    validate_runtime(target)
    backups = list(tmp_path.glob('.ocr-backup-*'))
    assert len(backups) == 1 and (backups[0] / 'old').read_text() == 'old installation'


def test_unsafe_zip_never_changes_existing_installation(tmp_path):
    target = tmp_path / 'installed'
    target.mkdir()
    (target / 'old').write_text('preserve')
    archive = tmp_path / 'unsafe.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('../escape', b'unsafe')
    with pytest.raises(OCRError, match='不安全'):
        install_runtime(archive, directory=target)
    assert (target / 'old').read_text() == 'preserve'
    assert not (tmp_path / 'escape').exists() and not list(tmp_path.glob('.ocr-install-*'))


def test_base_import_does_not_load_optional_libraries():
    result = subprocess.run([sys.executable, '-c',
        "import sys; from app.ui import MainWindow; import app.ocr; "
        "assert not {'rapidocr','onnxruntime','cv2','numpy'} & sys.modules.keys()"],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
