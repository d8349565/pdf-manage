"""Explicit build-time download of pinned public models; never called by the app."""
import argparse
import hashlib
from pathlib import Path
import urllib.request

MODEL_RELEASE = 'https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/'
MODELS = {
    'PP-OCRv6_det_small.onnx': ('PP-OCRv6/det/', '090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f'),
    'PP-OCRv6_rec_small.onnx': ('PP-OCRv6/rec/', '6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884'),
}


def prepare_models(target, *, download=False):
    import yaml
    import rapidocr
    import onnxruntime as ort
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    registry = yaml.safe_load((Path(rapidocr.__file__).parent / 'default_models.yaml').read_text(encoding='utf-8'))
    cls = registry['onnxruntime']['PP-OCRv4']['cls']['ch_ppocr_mobile_v2.0_cls_mobile']
    entries = {name: (MODEL_RELEASE + folder + name, digest) for name, (folder, digest) in MODELS.items()}
    entries['ch_ppocr_mobile_v2.0_cls_mobile.onnx'] = (cls['model_dir'], cls['SHA256'])
    for name, (url, digest) in entries.items():
        path = target / name
        if not path.exists() and download:
            with urllib.request.urlopen(url, timeout=60) as response, path.open('xb') as output:
                while block := response.read(1024 * 1024):
                    output.write(block)
        if not path.is_file() or hashlib.file_digest(path.open('rb'), 'sha256').hexdigest() != digest:
            raise ValueError(f'Missing or invalid model: {name}')
        print(f'Verified {name}')
    session = ort.InferenceSession(str(target / 'PP-OCRv6_rec_small.onnx'), providers=['CPUExecutionProvider'])
    characters = session.get_modelmeta().custom_metadata_map.get('character')
    if not characters:
        raise ValueError('Recognition model must contain its own character dictionary')
    (target / 'characters.txt').write_text(characters, encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    prepare_models(args.directory, download=args.download)
