"""Record size budgets for the base and optional package in Windows CI."""
import argparse
import json
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader


def size(directory):
    return sum(path.stat().st_size for path in directory.rglob('*') if path.is_file())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--ocr', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    archive = CArchiveReader(str(args.base / 'ZhiXuPDF.exe'))
    modules = archive.open_embedded_archive('PYZ.pyz').toc
    forbidden = ('rapidocr', 'onnxruntime', 'numpy', 'cv2', 'torch', 'paddle', 'transformers')
    assert not any(name.split('.')[0] in forbidden for name in modules), 'OCR runtime leaked into the base package'
    names = [str(path.relative_to(args.base)).lower() for path in args.base.rglob('*') if path.is_file()]
    assert not any(path.endswith('.onnx') or any(part in path for part in forbidden) for path in names)
    report = dict(base_installed_bytes=size(args.base), ocr_installed_bytes=size(args.ocr / 'package'),
                  ocr_compressed_bytes=(args.ocr / 'ZhiXuPDF-OCR-win-x64.zip').stat().st_size,
                  base_has_ocr_dependencies=False)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
