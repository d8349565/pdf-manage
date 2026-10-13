"""Separate executable entry point. Only this process imports RapidOCR / ORT."""
import argparse
from collections import Counter
import contextlib
import json
import re
from importlib.metadata import version
import socket
import sys
from pathlib import Path

RAPIDOCR_VERSION = '3.9.2'
ORT_VERSION = '1.23.2'


def deny_network(*args, **kwargs):
    raise RuntimeError('OCR 增强包禁止网络访问，请重新安装完整的离线组件。')


def stabilize_short_directions(crops, result, threshold):
    # Short numbers are nearly symmetric. Their individual 0/180 prediction
    # must not contradict a strong page consensus from longer text regions.
    votes=[label for crop,(label,score) in zip(crops,result.cls_res)
           if crop.shape[1]/max(1,crop.shape[0])>=4 and score>=threshold]
    if len(votes)<3:
        return
    direction,count=Counter(votes).most_common(1)[0]
    if count/len(votes)<.8 or direction not in ('0','180'):
        return
    for index,(crop,(label,score)) in enumerate(zip(crops,result.cls_res)):
        if crop.shape[1]/max(1,crop.shape[0])<4 and label!=direction:
            result.img_list[index]=crop.copy() if direction=='0' else crop[::-1,::-1].copy()


def load_engine(models):
    socket.create_connection = deny_network
    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    from rapidocr import EngineType, ModelType, OCRVersion, RapidOCR
    from rapidocr.utils.download_file import DownloadFile
    import onnxruntime
    import requests
    requests.sessions.Session.request = deny_network
    DownloadFile.run = deny_network
    if version('rapidocr') != RAPIDOCR_VERSION or onnxruntime.__version__ != ORT_VERSION:
        raise RuntimeError('OCR 运行库版本不兼容，请重新安装增强包。')
    models = Path(models).resolve()
    paths = {key: models / name for key, name in (
        ('Det', 'PP-OCRv6_det_small.onnx'), ('Rec', 'PP-OCRv6_rec_small.onnx'),
        ('Cls', 'ch_ppocr_mobile_v2.0_cls_mobile.onnx'))}
    if not all(path.is_file() for path in paths.values()):
        raise RuntimeError('OCR 模型缺失，请重新安装增强包。')
    params = {'Global.log_level': 'critical', 'Global.use_cls': True,
              'Det.max_candidates': 10000,
              'EngineConfig.onnxruntime.intra_op_num_threads': 4,
              'EngineConfig.onnxruntime.inter_op_num_threads': 1}
    for key, path in paths.items():
        params[f'{key}.model_path'] = str(path)
        params[f'{key}.engine_type'] = EngineType.ONNXRUNTIME
    for key in ('Det', 'Rec'):
        params[f'{key}.ocr_version'] = OCRVersion.PPOCRV6
        params[f'{key}.model_type'] = ModelType.SMALL
    params['Rec.rec_keys_path'] = str(models / 'characters.txt')
    class StableDirectionOCR(RapidOCR):
        def cls_and_rotate(self, crops):
            _,result=super().cls_and_rotate(crops)
            stabilize_short_directions(crops,result,self.text_cls.cls_thresh)
            return result.img_list,result
    engine = StableDirectionOCR(params=params)
    if not engine.text_rec.session.have_key('character'):
        raise RuntimeError('识别模型缺少绑定的字符字典，拒绝使用其他字典。')
    for session in (engine.text_det.session, engine.text_cls.session, engine.text_rec.session):
        if session.session.get_providers() != ['CPUExecutionProvider']:
            raise RuntimeError('OCR 必须使用 CPU 推理组件。')
    return engine


def refine_currency_symbols(engine, image, lines):
    """Re-read isolated ambiguous symbols with adjacent digits; never rewrite digits."""
    candidates = []
    for index, line in enumerate(lines):
        if line['text'].strip() not in ('夫', '羊', '半', '丰', 'Y', 'y'):
            continue
        left, top, right, bottom = line['bbox']
        height = bottom - top
        if height <= 0 or right - left > height * 1.8:
            continue
        neighbours = [other for other in lines
                      if re.fullmatch(r'[+-]?\d[\d,]*(?:\.\d+)?', other['text'].strip())
                      and 0 <= other['bbox'][0] - right <= height * 12
                      and abs(sum(other['bbox'][1::2]) / 2 - (top + bottom) / 2) <= height * .35
                      and .65 * height <= other['bbox'][3] - other['bbox'][1] <= 1.5 * height]
        if neighbours:
            candidates.append((index, min(neighbours, key=lambda other: other['bbox'][0])))
    if not candidates:
        return
    import cv2
    import numpy as np
    from rapidocr.ch_ppocr_rec import TextRecInput
    from rapidocr.utils.process_img import get_rotate_crop_image
    pixels = engine.load_img(str(image)) if isinstance(image, (str, Path)) else image
    probes = []
    for index, neighbour in candidates:
        crops = []
        for line in (lines[index], neighbour):
            crop = get_rotate_crop_image(pixels, np.array(line['polygon'], dtype=np.float32).reshape(4, 2))
            crops.append(cv2.resize(crop, (max(1, round(crop.shape[1] * 48 / crop.shape[0])), 48)))
        combined = np.concatenate((crops[0], np.full((48, 5, 3), 255, dtype=np.uint8), crops[1]), axis=1)
        probes.extend((combined, cv2.rotate(combined, cv2.ROTATE_180)))
    reread = engine.text_rec(TextRecInput(img=probes))
    for offset, (index, neighbour) in enumerate(candidates):
        for probe in (offset * 2, offset * 2 + 1):
            text = ''.join(reread.txts[probe].split())
            score = float(reread.scores[probe])
            if score >= .9 and text[:1] in ('¥', '￥') and text[1:] == neighbour['text'].strip():
                lines[index]['text'] = text[0]
                lines[index]['confidence'] = score
                break


def recognize(engine, image, page_index):
    result = engine(str(image))
    if result.boxes is None or result.txts is None:
        return []
    lines = []
    for box, text, score in zip(result.boxes, result.txts, result.scores):
        polygon = [float(v) for point in box for v in point]
        xs, ys = polygon[::2], polygon[1::2]
        lines.append(dict(text=text, bbox=[min(xs), min(ys), max(xs), max(ys)],
                          polygon=polygon, confidence=float(score), page_index=page_index))
    refine_currency_symbols(engine, image, lines)
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--models', required=True)
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stdin.reconfigure(encoding='utf-8')
    try:
        with contextlib.redirect_stdout(sys.stderr):
            engine = load_engine(args.models)
        print(json.dumps({'ready': True}), flush=True)
    except Exception:
        print(json.dumps({'error': 'OCR 组件加载失败，请检查版本和模型，重新安装离线增强包。'}), flush=True)
        return 1
    for request in sys.stdin:
        try:
            data = json.loads(request)
            image = Path(data['image'])
            if not image.is_file() or image.suffix.lower() != '.png':
                raise ValueError('Only local PNG input is accepted')
            with contextlib.redirect_stdout(sys.stderr):
                lines = recognize(engine, image, data['page_index'])
            response = {'lines': lines}
        except Exception:
            response = {'error': '此页 OCR 推理失败，可重新尝试或更换扫描件。'}
        print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
