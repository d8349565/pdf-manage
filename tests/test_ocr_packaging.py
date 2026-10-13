"""Slim packaging must keep active binaries, models and license information."""
from pathlib import Path
import importlib.util


def test_prune_runtime_removes_only_unused_video_and_identical_dlls(tmp_path):
    path=Path(__file__).resolve().parents[1]/'packaging/build_ocr.py'
    spec=importlib.util.spec_from_file_location('ocr_build_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    files={
        '_internal/cv2/opencv_videoio_ffmpeg500_64.dll':b'unused-video',
        '_internal/cv2/cv2.pyd':b'keep-image-processing',
        '_internal/numpy.libs/equal.dll':b'identical',
        '_internal/equal.dll':b'identical',
        '_internal/numpy.libs/different.dll':b'version-one',
        '_internal/different.dll':b'version-two',
        'models/model.onnx':b'keep-model',
        'licenses/NOTICE.txt':b'keep-license',
    }
    for name,data in files.items():
        target=tmp_path/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    removed=module.prune_runtime(tmp_path)
    assert {item['path'] for item in removed}=={'_internal/cv2/opencv_videoio_ffmpeg500_64.dll','_internal/equal.dll'}
    for name,data in files.items():
        if name not in {item['path'] for item in removed}:assert (tmp_path/name).read_bytes()==data
