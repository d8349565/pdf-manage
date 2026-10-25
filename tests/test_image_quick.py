"""Standalone image shortcuts use only generated images and isolated outputs."""
from pathlib import Path
import os
import random
import subprocess
import sys
from time import monotonic, sleep

import pytest
from PIL import Image, ImageDraw, ImageOps
from PyQt6.QtWidgets import QApplication

from app.imagecompress import compress_image
from app.output import OperationCancelled


def sample(path, kind='PNG', mode='RGB'):
    if kind in ('JPEG', 'WEBP'):
        image = Image.frombytes('RGB', (640, 480), random.Random(58).randbytes(640 * 480 * 3))
    else:
        image = Image.new(mode, (640, 480), (245, 250, 248, 0) if mode == 'RGBA' else 'white')
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 40, 500, 350), fill=(20, 140, 90, 140) if mode == 'RGBA' else '#208B65')
        draw.line((50, 50, 470, 320), fill=(220, 45, 30, 255) if mode == 'RGBA' else '#D52D1E', width=7)
    options = dict(quality=99, subsampling=0) if kind == 'JPEG' else dict(quality=100, method=0) if kind == 'WEBP' else dict(compress_level=0) if kind == 'PNG' else {}
    image.save(path, kind, **options)
    return image


@pytest.mark.parametrize('kind,suffix', [('JPEG','.jpg'), ('PNG','.png'), ('WEBP','.webp'), ('BMP','.bmp'), ('TIFF','.tif')])
def test_compression_keeps_original_and_smaller_readable_output(tmp_path, kind, suffix):
    source = tmp_path / ('照片 空格' + suffix)
    sample(source, kind)
    original, modified = source.read_bytes(), source.stat().st_mtime_ns
    result = compress_image(source)
    assert result.path and result.after < result.before
    assert source.read_bytes() == original and source.stat().st_mtime_ns == modified
    output = Path(result.path)
    assert output.parent == source.parent and output != source
    assert output.stat().st_size == result.after
    with Image.open(output) as compressed, Image.open(source) as before:
        assert compressed.size == before.size
        compressed.load()
        if kind in ('PNG', 'BMP', 'TIFF'):
            assert compressed.convert('RGBA').tobytes() == before.convert('RGBA').tobytes()
    assert not list(tmp_path.glob('.zhixu-image-*'))


@pytest.mark.parametrize('palette', [False, True])
def test_png_alpha_and_transparent_palette_keep_every_pixel(tmp_path, palette):
    source = tmp_path / 'alpha.png'
    image = sample(source, mode='RGBA')
    if palette:
        image = image.quantize(colors=8)
        image.save(source, 'PNG', compress_level=0)
    with Image.open(source) as before:
        pixels = before.convert('RGBA').tobytes()
    result = compress_image(source)
    assert result.path
    with Image.open(result.path) as after:
        assert after.convert('RGBA').tobytes() == pixels


def test_orientation_and_color_profile_are_preserved(tmp_path):
    from PIL import ImageCms
    source = tmp_path / 'portrait.jpg'
    image = Image.frombytes('RGB', (400, 600), random.Random(21).randbytes(400 * 600 * 3))
    exif = Image.Exif()
    exif[274] = 6
    exif[315] = 'Generated test'
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    image.save(source, 'JPEG', quality=99, subsampling=0, exif=exif, icc_profile=profile)
    original = source.read_bytes()
    result = compress_image(source)
    assert result.path and source.read_bytes() == original
    with Image.open(result.path) as after:
        assert ImageOps.exif_transpose(after).size == (600, 400)
        assert after.getexif().get(274, 1) == 1
        assert after.getexif()[315] == 'Generated test'
        assert after.info['icc_profile'] == profile


def test_no_gain_does_not_create_a_duplicate_file(tmp_path):
    source = tmp_path / 'tiny.png'
    Image.new('L', (1, 1), 255).save(source, 'PNG', optimize=True)
    result = compress_image(source)
    assert result.path is None and result.before == result.after
    assert list(tmp_path.glob('tiny_压缩*')) == []


def test_png_color_chunks_and_text_survive_lossless_optimization(tmp_path):
    import struct
    from PIL import PngImagePlugin
    source = tmp_path / 'metadata.png'
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text('Description', '生成的中文说明')
    metadata.add(b'gAMA', struct.pack('>I', 45455))
    metadata.add(b'sRGB', b'\x00')
    image = Image.new('RGB', (600, 400), '#AECA85')
    image.save(source, 'PNG', pnginfo=metadata, compress_level=0)
    result = compress_image(source)
    assert result.path
    with Image.open(result.path) as after:
        after.load()
        assert after.text['Description'] == '生成的中文说明'
        assert after.info['gamma'] == .45455 and after.info['srgb'] == 0
        assert after.convert('RGB').tobytes() == image.tobytes()


def test_jpeg_keeps_existing_chroma_sampling(tmp_path):
    from PIL import JpegImagePlugin
    source = tmp_path / 'photo.jpg'
    image = Image.frombytes('RGB', (640, 480), random.Random(19).randbytes(640 * 480 * 3))
    image.save(source, 'JPEG', quality=99, subsampling=2)
    result = compress_image(source)
    assert result.path and result.after < result.before
    with Image.open(result.path) as after:
        assert JpegImagePlugin.get_sampling(after) == 2


def test_existing_output_is_preserved_and_a_new_name_is_used(tmp_path):
    source = tmp_path / 'same.png'
    sample(source)
    existing = tmp_path / 'same_压缩.png'
    existing.write_bytes(b'keep existing user file')
    existing_webp = tmp_path / 'same_压缩.webp'
    existing_webp.write_bytes(b'keep existing WebP file')
    result = compress_image(source)
    assert Path(result.path).stem == 'same_压缩 (2)'
    assert existing.read_bytes() == b'keep existing user file'
    assert existing_webp.read_bytes() == b'keep existing WebP file'


@pytest.mark.parametrize('kind,suffix', [('TIFF','.tiff'), ('WEBP','.webp'), ('PNG','.png')])
def test_multiframe_images_are_never_flattened(tmp_path, kind, suffix):
    source = tmp_path / ('animated' + suffix)
    first, second = Image.new('RGB', (100, 120), 'red'), Image.new('RGB', (100, 120), 'blue')
    first.save(source, kind, save_all=True, append_images=[second], duration=100, loop=0)
    original = source.read_bytes()
    with pytest.raises(ValueError, match='多帧|动态'):
        compress_image(source)
    assert source.read_bytes() == original
    assert list(tmp_path.glob('animated_压缩*')) == []


def test_cancel_and_external_source_change_leave_no_output(tmp_path):
    source = tmp_path / 'protected.png'
    sample(source)
    original = source.read_bytes()
    cancelled = False
    def cancel_on_encode(current, total, message):
        nonlocal cancelled
        if '正在压缩' in message:
            cancelled = True
    with pytest.raises(OperationCancelled):
        compress_image(source, progress=cancel_on_encode, cancel=lambda: cancelled)
    assert source.read_bytes() == original
    def change_during_encode(current, total, message):
        if '正在压缩' in message:
            source.write_bytes(b'changed by an external application')
    with pytest.raises(OSError, match='原图已被其他程序修改'):
        compress_image(source, progress=change_during_encode)
    assert source.read_bytes() == b'changed by an external application'
    assert not list(tmp_path.glob('protected_压缩*'))


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def keep_pdf_open_requests_inside_the_test(monkeypatch):
    from PyQt6.QtGui import QDesktopServices
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: True)


def wait(qapp, predicate):
    deadline = monotonic() + 15
    while not predicate() and monotonic() < deadline:
        qapp.processEvents()
        sleep(.01)
    assert predicate()


def test_quick_window_starts_automatically_and_shows_result(qapp, tmp_path):
    from app.imagequick import ImageQuickDialog
    source = tmp_path / 'window.png'
    sample(source)
    dialog = ImageQuickDialog([str(source)], 'compress')
    dialog.show()
    try:
        wait(qapp, lambda: dialog.worker is not None and dialog.finish.text() == '完成')
        assert dialog.output and dialog.open_folder.isEnabled()
        assert '减少' in dialog.status.text() and Path(dialog.output).exists()
        assert dialog.status.text().splitlines()[-1] == Path(dialog.output).name
        assert dialog.status.toolTip() == dialog.output
        assert dialog.rect().contains(dialog.finish.geometry())
    finally:
        dialog.reject()
        dialog.deleteLater()
        qapp.processEvents()


def test_closing_before_start_does_not_process_an_image(qapp, tmp_path):
    from app.imagequick import ImageQuickDialog
    source = tmp_path / 'early.png'
    sample(source)
    dialog = ImageQuickDialog([str(source)], 'compress')
    dialog.reject()
    qapp.processEvents()
    assert dialog.worker is None and not list(tmp_path.glob('early_压缩*'))
    dialog.deleteLater()


@pytest.mark.parametrize('cancel',[False,True])
def test_image_merge_failure_or_cancel_never_opens_a_result(qapp,tmp_path,monkeypatch,cancel):
    from PyQt6.QtGui import QDesktopServices
    from app.imagequick import ImageQuickDialog
    source=tmp_path/'generated.png';sample(source)
    opened=[]
    monkeypatch.setattr(QDesktopServices,'openUrl',lambda url:opened.append(url) or True)
    dialog=ImageQuickDialog([str(source),str(tmp_path/'missing.jpg')],'pdf')
    dialog.show()
    try:
        if cancel:
            dialog.reject();qapp.processEvents();assert dialog.worker is None
        else:
            dialog.finish.click()
            wait(qapp,lambda:dialog.finish.text()=='完成')
            assert dialog.worker.error is not None
        assert not opened and dialog.output is None
        assert not list(tmp_path.glob('*.pdf'))
    finally:
        dialog.reject();dialog.deleteLater();qapp.processEvents()


def test_closing_a_running_quick_action_waits_for_cancellation(qapp, tmp_path, monkeypatch):
    from app.imagequick import ImageQuickDialog
    from threading import Event
    entered = Event()
    def work(self, report, cancel):
        entered.set()
        deadline = monotonic() + 5
        while not cancel() and monotonic() < deadline:
            sleep(.01)
        if cancel():
            raise OperationCancelled()
        raise AssertionError('Cancellation was not delivered')
    monkeypatch.setattr(ImageQuickDialog, 'work', work)
    dialog = ImageQuickDialog([str(tmp_path / 'generated.png')], 'compress')
    dialog.show()
    try:
        wait(qapp, entered.is_set)
        dialog.reject()
        wait(qapp, lambda: not dialog.worker.isRunning() and not dialog.isVisible())
        assert isinstance(dialog.worker.error, OperationCancelled)
    finally:
        dialog.deleteLater()
        qapp.processEvents()


def test_pdf_shortcut_does_not_overwrite_a_file_created_during_conversion(qapp, tmp_path, monkeypatch):
    from app.imagequick import ImageQuickDialog
    from app import imaging
    source = tmp_path / 'collision.png'
    sample(source)
    target = tmp_path / 'collision.pdf'
    original = imaging.images_to_pdf
    def concurrent_target(*args, **kwargs):
        result = original(*args, **kwargs)
        target.write_bytes(b'user file created during conversion')
        return result
    monkeypatch.setattr(imaging, 'images_to_pdf', concurrent_target)
    dialog = ImageQuickDialog([str(source)], 'pdf')
    try:
        with pytest.raises(OSError, match='目标文件'):
            dialog.work(lambda *args: None, lambda: False)
        assert target.read_bytes() == b'user file created during conversion'
        assert not list(tmp_path.glob('.zhixu-image-pdf-*'))
    finally:
        dialog.reject()
        dialog.deleteLater()


def test_pdf_shortcut_expands_all_tiff_frames(qapp, tmp_path):
    import pymupdf
    from app.imagequick import ImageQuickDialog
    source = tmp_path / 'scans.tiff'
    first, second = Image.new('RGB', (100, 120), 'red'), Image.new('RGB', (180, 100), 'blue')
    first.save(source, 'TIFF', save_all=True, append_images=[second])
    original = source.read_bytes()
    dialog = ImageQuickDialog([str(source)], 'pdf')
    try:
        result = dialog.work(lambda *args: None, lambda: False)
        with pymupdf.open(result.path) as doc:
            assert len(doc) == 2
            assert doc[0].rect.width < doc[0].rect.height and doc[1].rect.width > doc[1].rect.height
        assert source.read_bytes() == original
    finally:
        dialog.reject()
        dialog.deleteLater()


@pytest.mark.parametrize('mode', ['--compress-image', '--images-to-pdf'])
def test_cold_cli_uses_image_tool_without_loading_the_pdf_reader(tmp_path, mode):
    source = tmp_path / '冷启动 图片.webp'
    sample(source, 'WEBP')
    script = tmp_path / 'launch_image.py'
    script.write_text('''
import sys,time
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QDesktopServices
QDesktopServices.openUrl=staticmethod(lambda url: True)
from app import __main__ as entry
mode,path=sys.argv[1:]
sys.argv=['ZhiXuPDF.exe',mode,path]
def run():
    app=QApplication.instance()
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        app.processEvents()
        dialogs=[w for w in app.topLevelWidgets() if type(w).__name__=='ImageQuickDialog']
        if dialogs and dialogs[0].finish.text()=='完成':
            dialog=dialogs[0]
            assert dialog.worker.error is None, str(dialog.worker.error)
            assert dialog.output
            assert 'app.ui' not in sys.modules
            if mode=='--compress-image':
                assert 'pymupdf' not in sys.modules and 'app.pdfio' not in sys.modules
            print('IMAGE_ACTION_PASSED',flush=True)
            dialog.reject()
            return 0
        time.sleep(.01)
    raise AssertionError('Image shortcut did not finish')
QApplication.exec=staticmethod(run)
entry.main()
''', encoding='utf-8')
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), QT_QPA_PLATFORM='offscreen')
    completed = subprocess.run([sys.executable, str(script), mode, str(source)], env=environment,
                               cwd=tmp_path, capture_output=True, timeout=25, text=True, encoding='utf-8', errors='replace')
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'IMAGE_ACTION_PASSED' in completed.stdout


def mixed_image(path, *, alpha=False, **metadata):
    """A generated textured background with small text and sharp colored lines."""
    image = Image.frombytes('RGB', (160, 120), random.Random(47).randbytes(160 * 120 * 3))
    image = image.resize((640, 480), Image.Resampling.BICUBIC)
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 30, 610, 95), fill='white', outline='red', width=2)
    draw.text((30, 45), 'Small text 0123456789 ABC test', fill='black')
    if alpha:
        image.putalpha(Image.linear_gradient('L').resize(image.size))
    image.save(path, 'PNG', **metadata)
    return image


@pytest.mark.parametrize('alpha', [False, True])
def test_automatic_webp_is_lossless_including_invisible_rgb(tmp_path, alpha):
    source = tmp_path / 'texture.png'
    image = mixed_image(source, alpha=alpha)
    original, modified = source.read_bytes(), source.stat().st_mtime_ns
    result = compress_image(source)
    assert result.path and Path(result.path).suffix == '.webp'
    assert result.after < result.before and '无损' in result.description
    with Image.open(result.path) as after:
        assert after.size == image.size
        assert after.convert('RGBA').tobytes() == image.convert('RGBA').tobytes()
    assert source.read_bytes() == original and source.stat().st_mtime_ns == modified


def test_webp_keeps_icc_exif_and_resolution(tmp_path):
    from PIL import ImageCms
    source = tmp_path / 'color-managed.png'
    exif = Image.Exif()
    exif[315] = 'Generated test'
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    mixed_image(source, dpi=(144, 120), exif=exif, icc_profile=profile)
    with Image.open(source) as before:
        dpi = before.info['dpi']
    result = compress_image(source)
    assert result.path and Path(result.path).suffix == '.webp'
    with Image.open(result.path) as after:
        assert after.info['icc_profile'] == profile
        assert after.getexif()[315] == 'Generated test'
        assert float(after.getexif()[282]) == pytest.approx(dpi[0], abs=.001)
        assert float(after.getexif()[283]) == pytest.approx(dpi[1], abs=.001)
        assert after.getexif()[296] == 2


def test_high_bit_depth_and_wide_images_keep_png(tmp_path):
    import struct
    source = tmp_path / '16bit.png'
    image = Image.frombytes('I;16', (512, 64), struct.pack('<32768H', *(index * 2 for index in range(32768))))
    image.save(source, 'PNG', compress_level=0)
    result = compress_image(source)
    assert result.path and Path(result.path).suffix == '.png'
    with Image.open(result.path) as after:
        assert after.tobytes() == image.tobytes()
    with pytest.raises(ValueError, match='颜色模式'):
        compress_image(source, compact=True)
    wide = tmp_path / 'wide.png'
    Image.new('RGB', (16384, 1), '#AABBCC').save(wide, 'PNG', compress_level=0)
    result = compress_image(wide)
    assert result.path and Path(result.path).suffix == '.png'
    with pytest.raises(ValueError, match='16383'):
        compress_image(wide, compact=True)


def test_optional_webp_encoder_failure_keeps_png_candidate(tmp_path, monkeypatch):
    source = tmp_path / 'fallback.png'
    sample(source)
    original = Image.Image.save
    def unsupported_webp(self, file, format=None, **options):
        if format == 'WEBP':
            raise OSError('Generated encoder failure')
        return original(self, file, format, **options)
    monkeypatch.setattr(Image.Image, 'save', unsupported_webp)
    result = compress_image(source)
    assert result.path and Path(result.path).suffix == '.png'
    with Image.open(source) as before, Image.open(result.path) as after:
        assert before.convert('RGBA').tobytes() == after.convert('RGBA').tobytes()


@pytest.mark.parametrize('alpha', [False, True])
def test_explicit_compact_webp_is_smaller_and_keeps_dimensions_and_alpha(tmp_path, alpha):
    source = tmp_path / 'compact.png'
    image = mixed_image(source, alpha=alpha)
    original = source.read_bytes()
    lossless = compress_image(source)
    saved_lossless = Path(lossless.path).read_bytes()
    compact = compress_image(source, compact=True, smaller_than=lossless.after)
    assert compact.path and compact.after < lossless.after
    assert Path(compact.path).stem == 'compact_更小'
    assert '有损' in compact.description
    with Image.open(compact.path) as after:
        assert after.size == image.size
        assert after.convert('RGBA').getchannel('A').tobytes() == image.convert('RGBA').getchannel('A').tobytes()
        assert after.convert('RGB').tobytes() != image.convert('RGB').tobytes()
    assert source.read_bytes() == original and Path(lossless.path).read_bytes() == saved_lossless
    refused = compress_image(source, compact=True, smaller_than=1)
    assert refused.path is None and not list(tmp_path.glob('compact_更小 (2).*'))


def test_compact_does_not_discard_png_color_interpretation(tmp_path):
    import struct
    from PIL import PngImagePlugin
    source = tmp_path / 'linear.png'
    metadata = PngImagePlugin.PngInfo()
    metadata.add(b'gAMA', struct.pack('>I', 100000))
    mixed_image(source, pnginfo=metadata)
    original = source.read_bytes()
    with pytest.raises(ValueError, match='颜色信息'):
        compress_image(source, compact=True)
    result = compress_image(source)
    assert result.path and Path(result.path).suffix == '.png'
    assert source.read_bytes() == original and not list(tmp_path.glob('*_更小*'))


def test_quick_window_generates_smaller_version_with_one_button(qapp, tmp_path):
    from app.imagequick import ImageQuickDialog
    source = tmp_path / 'two-modes.png'
    mixed_image(source)
    dialog = ImageQuickDialog([str(source)], 'compress')
    dialog.show()
    try:
        wait(qapp, lambda: dialog.finish.text() == '完成')
        assert dialog.smaller.isVisible() and dialog.smaller.isEnabled()
        first = Path(dialog.output)
        data = first.read_bytes()
        dialog.smaller.click()
        assert dialog.finish.text() == '取消' and not dialog.smaller.isEnabled()
        assert dialog.progress.isVisible()
        wait(qapp, lambda: dialog.finish.text() == '完成')
        assert dialog.worker.error is None and Path(dialog.output) != first
        assert Path(dialog.output).stat().st_size < len(data)
        assert first.read_bytes() == data and '有损' in dialog.detail.text()
        assert dialog.open_folder.isEnabled() and not dialog.smaller.isEnabled()
    finally:
        dialog.reject()
        dialog.deleteLater()
        qapp.processEvents()


def test_quick_window_keeps_better_lossless_result_when_compact_is_larger(qapp, tmp_path):
    from app.imagequick import ImageQuickDialog
    source = tmp_path / 'flat.png'
    Image.new('RGB', (320, 240), '#AABBCC').save(source, 'PNG', compress_level=0)
    dialog = ImageQuickDialog([str(source)], 'compress')
    dialog.show()
    try:
        wait(qapp, lambda: dialog.finish.text() == '完成')
        first = Path(dialog.output)
        data = first.read_bytes()
        dialog.smaller.click()
        wait(qapp, lambda: dialog.finish.text() == '完成')
        assert dialog.worker.error is None and Path(dialog.output) == first
        assert first.read_bytes() == data and '没有比已保存结果更小' in dialog.status.text()
        assert not list(tmp_path.glob('flat_更小*'))
    finally:
        dialog.reject()
        dialog.deleteLater()
        qapp.processEvents()
