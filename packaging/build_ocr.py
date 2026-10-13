"""Build a self-contained Windows CPU enhancement; downloads require --download-models."""
import argparse
import hashlib
import json
from importlib.metadata import version
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--deps', type=Path, help='Isolated pip --target directory')
    parser.add_argument('--models', type=Path, default=ROOT / 'build' / 'ocr-models')
    parser.add_argument('--download-models', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist-ocr')
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    if args.deps:
        sys.path.insert(0, str(args.deps.resolve()))
    from app import __version__
    from ocr_models import prepare_models
    import PyInstaller.__main__
    import rapidocr
    import onnxruntime
    if version('rapidocr') != '3.9.2' or onnxruntime.__version__ != '1.23.2':
        raise ValueError('Install packaging/requirements-ocr.txt before building')
    prepare_models(args.models, download=args.download_models)
    build = ROOT / 'build' / 'ocr-freeze'
    output = args.output.resolve()
    if (output / 'package').exists():
        raise ValueError('Choose a new --output directory; existing packages are never overwritten')
    options = [str(ROOT / 'app' / 'ocr' / 'worker.py'), '--name', 'ZhiXuOCR', '--onedir', '--console',
               '--noconfirm', '--distpath', str(output), '--workpath', str(build), '--specpath', str(build),
               '--add-data', str(Path(rapidocr.__file__).parent / 'config.yaml') + ';rapidocr',
               '--add-data', str(Path(rapidocr.__file__).parent / 'default_models.yaml') + ';rapidocr',
               '--collect-binaries', 'onnxruntime', '--copy-metadata', 'rapidocr',
               '--hidden-import', 'rapidocr.inference_engine.onnxruntime',
               '--exclude-module', 'PyQt6', '--exclude-module', 'pymupdf',
               '--exclude-module', 'torch', '--exclude-module', 'paddle', '--exclude-module', 'openvino',
               '--exclude-module', 'tensorrt', '--exclude-module', 'MNN', '--exclude-module', 'pytest',
               '--exclude-module', 'matplotlib', '--exclude-module', 'IPython']
    if args.deps:
        options += ['--paths', str(args.deps.resolve())]
    PyInstaller.__main__.run(options)
    # Package directories are new build artifacts; never operate on installed components.
    package = output / 'package'
    if package.exists():
        raise ValueError('dist-ocr/package already exists; use a clean build output directory')
    shutil.copytree(output / 'ZhiXuOCR', package / 'runtime')
    shutil.copytree(args.models, package / 'models')
    dependency_root = args.deps.resolve() if args.deps else Path(rapidocr.__file__).parent.parent
    for metadata in dependency_root.glob('*.dist-info'):
        for path in metadata.rglob('*'):
            if path.is_file() and any(word in path.name.lower() for word in ('license', 'copying', 'notice')):
                destination = package / 'licenses' / metadata.name / path.relative_to(metadata)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
    (package / 'MODEL-SOURCES.txt').write_text(
        'PP-OCRv6 small det/rec and PP-OCRv4 mobile cls\n'
        'https://github.com/RapidAI/RapidOCR/blob/v3.9.2/python/rapidocr/default_models.yaml\n'
        'https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE\n'
        'Model files are distributed under the upstream Apache-2.0 terms.\n', encoding='utf-8')
    files = {}
    for path in sorted(package.rglob('*')):
        if path.is_file():
            with path.open('rb') as stream:
                files[path.relative_to(package).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
    manifest = dict(protocol=1, platform='win_amd64', app_series='.'.join(__version__.split('.')[:2]),
                    rapidocr='3.9.2', onnxruntime='1.23.2', model_release='RapidOCR-v3.9.2', files=files)
    (package / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    archive = output / 'ZhiXuPDF-OCR-win-x64.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in package.rglob('*'):
            if path.is_file():
                bundle.write(path, path.relative_to(package).as_posix())
    print(json.dumps({'archive': str(archive), 'compressed_bytes': archive.stat().st_size,
                      'installed_bytes': sum(p.stat().st_size for p in package.rglob('*') if p.is_file())}))


if __name__ == '__main__':
    main()
