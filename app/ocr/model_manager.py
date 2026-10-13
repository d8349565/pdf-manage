"""Validate a complete local runtime before executing it; no downloading."""
import hashlib
import json
from pathlib import Path, PureWindowsPath
import shutil
import tempfile
import time
import uuid
import zipfile

from app import __version__
from app.ocr.contracts import OCRError
from app.output import check_cancel

PROTOCOL = 1
MODEL_NAMES = ('PP-OCRv6_det_small.onnx', 'PP-OCRv6_rec_small.onnx',
               'ch_ppocr_mobile_v2.0_cls_mobile.onnx', 'characters.txt')


def default_runtime_dir():
    from app.storage import directory, StorageError
    try:
        return directory('ocr')
    except StorageError as exc:
        raise OCRError(str(exc)) from None


def safe_member(name):
    windows = PureWindowsPath(name)
    return (bool(name) and '\\' not in name and ':' not in name
            and not windows.is_absolute() and not windows.drive
            and all(part not in ('', '.', '..') and not part.endswith((' ', '.'))
                    and not PureWindowsPath(part).is_reserved() for part in name.split('/')))


def validate_runtime(directory=None, *, cancel=None):
    root = Path(directory) if directory else default_runtime_dir()
    try:
        manifest_path = root / 'manifest.json'
        if not manifest_path.is_file():
            raise OCRError('尚未安装 OCR 增强包，请选择本地离线增强包安装。')
        if manifest_path.stat().st_size > 1024 * 1024:
            raise OCRError('OCR 增强包清单异常，请重新安装。')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if (manifest['protocol'] != PROTOCOL or manifest['platform'] != 'win_amd64'
                or manifest['app_series'] != '.'.join(__version__.split('.')[:2])
                or manifest['rapidocr'] != '3.9.2' or manifest['onnxruntime'] != '1.23.2'):
            raise OCRError('OCR 增强包与当前程序不兼容，请安装对应版本。')
        files = manifest['files']
        required = {'runtime/ZhiXuOCR.exe', *(f'models/{name}' for name in MODEL_NAMES)}
        if not required.issubset(files) or not isinstance(files, dict) or len(files) > 10000:
            raise OCRError('OCR 增强包清单不完整，请重新安装。')
        base = root.resolve()
        actual_files = {path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_file()}
        if actual_files != set(files) | {'manifest.json'}:
            raise OCRError('OCR 增强包存在未登记文件或文件缺失，请重新安装。')
        for name, digest in files.items():
            check_cancel(cancel)
            if not safe_member(name):
                raise OCRError('OCR 增强包包含不安全路径，已停止加载。')
            path = (root / name).resolve()
            if not path.is_relative_to(base) or not path.is_file():
                raise OCRError('OCR 组件缺失或路径异常，请重新安装增强包。')
            with path.open('rb') as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual != digest:
                raise OCRError('OCR 组件校验失败，请重新安装增强包。')
        return root.resolve(), manifest
    except (OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, OCRError):
            raise
        raise OCRError('OCR 增强包损坏或无法读取，请重新安装。') from None


def _rename_directory(source, destination, cancel=None):
    # Windows can briefly hold freshly scanned DLLs after extraction.
    for attempt in range(50):
        check_cancel(cancel)
        try:
            source.rename(destination)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(.1)


def install_runtime(archive, *, directory=None, progress=None, cancel=None):
    """Stage and validate a trusted local ZIP. Keep the previous installation as backup."""
    from app.storage import runtime_in_use
    target = Path(directory) if directory else default_runtime_dir()
    if runtime_in_use(target):
        raise OCRError('OCR 组件正在使用，请等待识别结束后再安装。')
    if Path(archive).resolve().is_relative_to(target.resolve()):
        raise OCRError('请从组件目录之外选择增强包，避免重装时移动原 ZIP 文件。')
    parent = target.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.ocr-install-', dir=parent))
    backup = parent / f'.ocr-backup-{uuid.uuid4().hex}'
    moved_old = False
    installed = False
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            if (len(entries) > 10000 or sum(item.file_size for item in entries) > 1024 ** 3
                    or len({item.filename.casefold() for item in entries}) != len(entries)):
                raise OCRError('增强包体积、文件数量或重复路径异常。')
            for current, entry in enumerate(entries):
                check_cancel(cancel)
                name = entry.filename.rstrip('/') if entry.is_dir() else entry.filename
                if not safe_member(name) or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise OCRError('增强包含不安全路径或符号链接，已停止安装。')
                path = staging / name
                if entry.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(entry) as source, path.open('xb') as destination:
                        while block := source.read(1024 * 1024):
                            check_cancel(cancel)
                            destination.write(block)
                if progress:
                    progress(current + 1, len(entries), '正在安装本地 OCR 增强包')
        validate_runtime(staging, cancel=cancel)
        check_cancel(cancel)
        if runtime_in_use(target):
            raise OCRError('OCR 组件正在使用，请等待识别结束后再安装。')
        if target.exists():
            _rename_directory(target, backup, cancel)
            moved_old = True
        _rename_directory(staging, target, cancel)
        installed = True
        return str(target)
    except (OSError, zipfile.BadZipFile) as exc:
        raise OCRError('OCR 安装失败，请检查增强包、磁盘空间及文件是否正在使用。') from None
    finally:
        if not installed and moved_old:
            _rename_directory(backup, target)
        if staging.exists():
            # Only this uniquely created, contained staging directory is removed.
            if staging.resolve().parent != parent or not staging.name.startswith('.ocr-install-'):
                raise OCRError('安装临时目录路径异常，请人工核对。')
            shutil.rmtree(staging)


def relocate_runtime(source, destination, progress=None, cancel=None):
    """Copy, verify, then publish. The old installation is kept for recovery."""
    from app.storage import tree_files, writable_directory
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        return str(destination)
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise OCRError('新位置不能包含原组件目录，也不能位于原组件目录内。')
    if destination.exists() and any(destination.iterdir()):
        try:
            _, existing = validate_runtime(destination, cancel=cancel)
            if (source / 'manifest.json').is_file():
                _, previous = validate_runtime(source, cancel=cancel)
                if previous['files'] != existing['files']:
                    raise OCRError('所选位置已有另一份组件，请选择空目录或在安装窗口更新该位置。')
            return str(destination)
        except OCRError:
            raise OCRError('新位置已有不同组件或其他文件，请选择空目录；原文件保留。') from None
    writable_directory(destination.parent)
    if not (source / 'manifest.json').is_file():
        writable_directory(destination)
        return str(destination)
    validate_runtime(source, cancel=cancel)
    paths = list(tree_files(source, cancel))
    staging = Path(tempfile.mkdtemp(prefix='.ocr-install-', dir=destination.parent))
    try:
        for index, path in enumerate(paths):
            check_cancel(cancel)
            copied = staging / path.relative_to(source)
            copied.parent.mkdir(parents=True, exist_ok=True)
            with path.open('rb') as reader, copied.open('xb') as writer:
                while chunk := reader.read(1024 * 1024):
                    check_cancel(cancel)
                    writer.write(chunk)
            if progress:
                progress(index + 1, len(paths), '正在复制 OCR 组件到新位置')
        validate_runtime(staging, cancel=cancel)
        check_cancel(cancel)
        if destination.exists():
            destination.rmdir()  # Only an empty destination may be removed.
        _rename_directory(staging, destination, cancel)
        return str(destination)
    except OSError:
        raise OCRError('组件迁移失败，请检查磁盘空间和写入权限；原组件保留。') from None
    finally:
        if staging.exists():
            shutil.rmtree(staging)
