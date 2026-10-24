"""Package the verified onedir build without user profiles or scratch files."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import APP_EXE_NAME, __version__

README = '''纸序 PDF 绿色版

1. 完整解压文件夹，运行 ZhiXuPDF.exe。请保留 _internal 和 portable.ini。
2. 设置、OCR 组件和任务缓存默认保存在 Data 中，和安装版分开。
3. 右键菜单默认关闭。在“文件 → 右键快捷工具”中可启用、修复或移除此版本的入口。
4. 移动整个文件夹后可点“修复”。删除绿色版前，请先移除已启用的右键菜单。
5. 安装版和绿色版可分别启用菜单，绿色版菜单名称带“绿色版”。
6. “转换 → 压缩图片”无需右键菜单；图片转 PDF、Word 与 Markdown 转 PDF 也有程序内入口。
7. Word 转换依赖本机 Microsoft Word；离线 OCR 增强包仍需另行导入。
8. 更新时完整解压新版，按需保留自己的 Data；不要覆盖正在运行的程序。
'''


def _is_link(path):
    return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)


def make_portable(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if not (source / APP_EXE_NAME).is_file() or not (source / '_internal').is_dir():
        raise ValueError('需要完整的目录式程序（EXE 和 _internal）。')
    if _is_link(source / '_internal'):
        raise ValueError('依赖目录不能是链接，请使用实际构建的完整程序目录。')
    target.parent.mkdir(parents=True, exist_ok=True)
    files = [source / APP_EXE_NAME]
    for folder, directories, names in os.walk(source / '_internal', followlinks=False):
        directories[:] = [name for name in directories if not _is_link(Path(folder) / name)]
        for name in names:
            path = Path(folder) / name
            if not _is_link(path) and path.resolve().is_relative_to(source):
                files.append(path)
    prefix = 'ZhiXuPDF-Portable/'
    descriptor, temporary = tempfile.mkstemp(prefix='.portable-', suffix='.zip', dir=target.parent)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in files:
                archive.write(path, prefix + path.relative_to(source).as_posix())
            archive.writestr(prefix + 'portable.ini', '[Portable]\nEnabled=true\n')
            archive.writestr(prefix + '绿色版使用说明.txt', README.encode('utf-8-sig'))
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError('绿色版 ZIP 校验失败。')
            if prefix + APP_EXE_NAME not in archive.namelist():
                raise ValueError('绿色版 ZIP 缺少主程序。')
            assert not any(name.startswith(prefix + 'Data/') for name in archive.namelist())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {'path': str(target), 'version': __version__, 'bytes': target.stat().st_size,
            'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'files': len(files) + 2,
            'executable_sha256': hashlib.sha256((source / APP_EXE_NAME).read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=ROOT / 'dist/ZhiXuPDF')
    parser.add_argument('--output', type=Path, default=ROOT / f'dist-portable/ZhiXuPDF-Portable-v{__version__}.zip')
    options = parser.parse_args()
    result = make_portable(options.source, options.output)
    (ROOT / 'build').mkdir(exist_ok=True)
    (ROOT / 'build/portable-report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))
