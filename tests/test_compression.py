"""Generated images only: pixel fidelity, preserved PDF objects and cancellation."""
from io import BytesIO
import random

import pymupdf as p
import pytest
from PIL import Image, ImageDraw, JpegImagePlugin

from app.output import OperationCancelled
from app.pdfio import PdfStore, WriteOptions


def image_document(path, image, *, jpeg=False, size=(288, 384)):
    buffer = BytesIO()
    image.save(buffer, 'JPEG' if jpeg else 'PNG', **({'quality': 85, 'optimize': True} if jpeg else {}))
    with p.open() as doc:
        page = doc.new_page(width=size[0], height=size[1])
        page.insert_image(page.rect, stream=buffer.getvalue())
        page.insert_text((12, 20), 'Searchable original 123456789', fontsize=8, render_mode=3)
        page.insert_link({'kind': p.LINK_URI, 'uri': 'https://example.org', 'from': p.Rect(10, 10, 70, 30)})
        doc.set_toc([[1, 'Generated page', 1]])
        doc.save(path, deflate=True)


def compress(source, target, profile='quality', **kwargs):
    store = PdfStore()
    try:
        store.write(store.load(str(source)), str(target), options=WriteOptions(compression=profile, **kwargs))
    finally:
        store.close_all()


@pytest.mark.parametrize('mode', ['RGB', 'L', '1'])
def test_lossless_encodings_keep_pixels_text_links_outline_and_original(tmp_path, mode):
    image = Image.new(mode, (900, 1200), 'white')
    draw = ImageDraw.Draw(image)
    for y in range(50, 1150, 25):
        draw.text((30, y), 'Generated 0123456789 office table', font_size=16,
                  fill=(35, 78, 128) if mode == 'RGB' else 0)
        draw.line((20, y + 18, 850, y + 18), fill=(120, 80, 20) if mode == 'RGB' else 0)
    source, target = tmp_path / 'original.pdf', tmp_path / 'optimized.pdf'
    image_document(source, image)
    original = source.read_bytes()
    compress(source, target)
    assert source.read_bytes() == original
    assert target.stat().st_size <= source.stat().st_size
    with p.open(source) as before, p.open(target) as after:
        assert after[0].get_pixmap(matrix=p.Matrix(2, 2)).samples == before[0].get_pixmap(matrix=p.Matrix(2, 2)).samples
        assert after[0].get_text() == before[0].get_text()
        assert after.get_toc() == before.get_toc()
        assert after[0].get_links()[0]['uri'] == 'https://example.org'


def test_lossless_jpeg2000_keeps_gray_samples_and_icc_profile(tmp_path):
    rng = random.Random(21)
    image = Image.new('L', (600, 800))
    image.putdata([min(255, 160 + x // 10 + rng.randrange(12)) for y in range(800) for x in range(600)])
    source, target = tmp_path / 'gray.pdf', tmp_path / 'gray-out.pdf'
    image_document(source, image)
    compress(source, target)
    with p.open(source) as before, p.open(target) as after:
        original, replacement = before[0].get_images()[0][0], after[0].get_images()[0][0]
        assert after.xref_get_key(replacement, 'Filter')[1] == '/JPXDecode'
        assert p.Pixmap(before, original).samples == p.Pixmap(after, replacement).samples
        assert after[0].get_pixmap().samples == before[0].get_pixmap().samples
        assert 'ICCBased' in after.xref_object(int(after.xref_get_key(replacement, 'ColorSpace')[1].split()[0]))


@pytest.mark.parametrize('mode', ['L', 'RGB'])
def test_black_white_stored_as_eight_bit_keeps_resolution_in_small_profile(tmp_path, mode):
    image = Image.new(mode, (900, 1200), 'white')
    draw = ImageDraw.Draw(image)
    for y in range(20, 1180, 11):
        draw.line((20, y, 880, y), fill='black', width=1)
    source, target = tmp_path / 'scan.pdf', tmp_path / 'small.pdf'
    image_document(source, image)
    compress(source, target, 'small')
    with p.open(source) as before, p.open(target) as after:
        assert after[0].get_images()[0][2:4] == (900, 1200)
        assert after[0].get_pixmap().samples == before[0].get_pixmap().samples


def test_transparency_and_decode_masks_are_preserved(tmp_path):
    image = Image.new('RGBA', (400, 500), (220, 50, 30, 100))
    ImageDraw.Draw(image).rectangle((30, 30, 350, 450), fill=(10, 170, 60, 210))
    source, target = tmp_path / 'alpha.pdf', tmp_path / 'alpha-out.pdf'
    image_document(source, image)
    compress(source, target, 'small')
    with p.open(source) as before, p.open(target) as after:
        old, new = before[0].get_images()[0], after[0].get_images()[0]
        assert new[1] and after.xref_stream_raw(new[0]) == before.xref_stream_raw(old[0])
        assert after.xref_stream_raw(new[1]) == before.xref_stream_raw(old[1])
        assert after[0].get_pixmap().samples == before[0].get_pixmap().samples


def test_static_font_subset_is_lossless_but_form_fonts_remain_complete(tmp_path):
    for with_form in (False, True):
        source, target = tmp_path / f'font-{with_form}.pdf', tmp_path / f'font-out-{with_form}.pdf'
        with p.open() as doc:
            page = doc.new_page()
            page.insert_font(fontname='CJKTest', fontbuffer=p.Font('cjk').buffer)
            page.insert_text((50, 100), '纸序生成测试 12345', fontname='CJKTest', fontsize=20)
            if with_form:
                widget = p.Widget()
                widget.field_name, widget.field_type = 'editable', p.PDF_WIDGET_TYPE_TEXT
                widget.rect = p.Rect(50, 140, 300, 190)
                page.add_widget(widget)
            doc.save(source, deflate=True)
        compress(source, target)
        with p.open(source) as before, p.open(target) as after:
            assert after[0].get_pixmap().samples == before[0].get_pixmap().samples
            assert after[0].get_text() == before[0].get_text()
            if with_form:
                assert len(after.extract_font(after[0].get_fonts()[0][0])[-1]) == len(before.extract_font(before[0].get_fonts()[0][0])[-1])
                page = after[0]
                widget = next(page.widgets())
                widget.field_value = 'New content ABC 67890'
                widget.update()
                assert widget.field_value == 'New content ABC 67890'
            else:
                assert target.stat().st_size < source.stat().st_size / 10


def test_shared_image_uses_largest_placement_and_jpeg_keeps_full_chroma(tmp_path):
    rng = random.Random(15)
    image = Image.frombytes('RGB', (900, 1200), rng.randbytes(900 * 1200 * 3))
    source, target = tmp_path / 'shared.pdf', tmp_path / 'shared-out.pdf'
    buffer = BytesIO()
    image.save(buffer, 'PNG')
    with p.open() as doc:
        page = doc.new_page(width=216, height=288)
        xref = page.insert_image(page.rect, stream=buffer.getvalue())
        page = doc.new_page(width=648, height=864)
        page.insert_image(page.rect, xref=xref)
        doc.save(source, deflate=True)
    compress(source, target, 'balanced')
    with p.open(target) as after:
        info = after[0].get_images()[0]
        assert info[2:4] == (900, 1200)
        assert info[0] == after[1].get_images()[0][0]
        jpeg = Image.open(BytesIO(after.extract_image(info[0])['image']))
        assert JpegImagePlugin.get_sampling(jpeg) == 0


def test_custom_gray_applies_to_images_and_vectors(tmp_path):
    source, target = tmp_path / 'color.pdf', tmp_path / 'gray.pdf'
    image_document(source, Image.new('RGB', (400, 600), (220, 80, 20)))
    with p.open(source) as doc:
        doc[0].draw_rect(p.Rect(30, 40, 100, 100), color=(1, 0, 0), fill=(0, 1, 0))
        doc[0].insert_text((40, 140), 'Colored vector text', color=(0, 0, 1))
        doc.saveIncr()
    compress(source, target, 'custom', compression_grayscale=True)
    with p.open(target) as after:
        pixmap = after[0].get_pixmap(colorspace=p.csRGB)
        im = Image.frombytes('RGB', (pixmap.width, pixmap.height), pixmap.samples)
        red, green, blue = im.split()
        assert red.tobytes() == green.tobytes() == blue.tobytes()


def test_gray_icc_tones_do_not_shift_in_lossy_profile(tmp_path):
    from PIL import ImageStat
    image = Image.new('L', (1200, 1600), 235)
    draw = ImageDraw.Draw(image)
    for y in range(50, 1500, 60):
        draw.text((45, y), 'Generated gray scan 123456789', fill=40, font_size=28)
    source, target = tmp_path / 'gray-icc.pdf', tmp_path / 'gray-icc-out.pdf'
    image_document(source, image)
    compress(source, target, 'balanced')
    with p.open(source) as before, p.open(target) as after:
        means = []
        for doc in (before, after):
            pixmap = doc[0].get_pixmap(colorspace=p.csRGB, matrix=p.Matrix(2, 2))
            means.append(ImageStat.Stat(Image.frombytes('RGB', (pixmap.width, pixmap.height), pixmap.samples)).mean)
        assert max(abs(a - b) for a, b in zip(*means)) < 2


@pytest.mark.parametrize('special', ['decode', 'cmyk'])
def test_special_color_spaces_and_decode_arrays_keep_the_original_image(tmp_path, special):
    image = Image.new('CMYK' if special == 'cmyk' else 'L', (500, 700), (40, 160, 20, 5) if special == 'cmyk' else 70)
    if special == 'decode':
        image.putdata([(x // 4 + y // 7) % 256 for y in range(700) for x in range(500)])
    source, target = tmp_path / 'special.pdf', tmp_path / 'special-out.pdf'
    image_document(source, image, jpeg=special == 'cmyk')
    if special == 'decode':
        with p.open(source) as doc:
            doc.xref_set_key(doc[0].get_images()[0][0], 'Decode', '[1 0]')
            doc.saveIncr()
    compress(source, target, 'small')
    with p.open(source) as before, p.open(target) as after:
        assert after.xref_stream_raw(after[0].get_images()[0][0]) == before.xref_stream_raw(before[0].get_images()[0][0])
        assert after[0].get_pixmap().samples == before[0].get_pixmap().samples


def test_optional_jpeg2000_encoder_failure_keeps_a_valid_lossless_result(tmp_path, monkeypatch):
    original_save = Image.Image.save
    attempted = []
    def unavailable(image, fp, format=None, **kwargs):
        if format == 'JPEG2000':
            attempted.append(True)
            raise OSError('Optional codec unavailable')
        return original_save(image, fp, format=format, **kwargs)
    image = Image.frombytes('L', (450, 600), random.Random(11).randbytes(450 * 600))
    source, target = tmp_path / 'fallback.pdf', tmp_path / 'fallback-out.pdf'
    image_document(source, image)
    monkeypatch.setattr(Image.Image, 'save', unavailable)
    compress(source, target)
    assert attempted
    with p.open(source) as before, p.open(target) as after:
        assert before[0].get_pixmap().samples == after[0].get_pixmap().samples


def test_large_scan_downsamples_before_the_pillow_allocation_limit(tmp_path, monkeypatch):
    from app import pdfcompress
    image = Image.frombytes('RGB', (900, 1200), random.Random(81).randbytes(900 * 1200 * 3))
    source, target = tmp_path / 'large.pdf', tmp_path / 'large-out.pdf'
    image_document(source, image, size=(216, 288))
    monkeypatch.setattr(pdfcompress, 'MAX_PIXELS', 600_000)
    compress(source, target, 'balanced')
    with p.open(target) as doc:
        assert doc[0].get_images()[0][2:4] == (600, 800)
        assert target.stat().st_size < source.stat().st_size


def test_cancel_image_optimization_leaves_original_and_existing_destination(tmp_path):
    source, target = tmp_path / 'source.pdf', tmp_path / 'keep.pdf'
    image_document(source, Image.new('RGB', (800, 1000), (230, 240, 250)))
    original = source.read_bytes()
    with p.open() as existing:
        existing.new_page().insert_text((50, 50), 'Keep existing output')
        existing.save(target)
    old_target = target.read_bytes()
    stopped = False
    def progress(current, total, message):
        nonlocal stopped
        if '逐张优化' in message:
            stopped = True
    store = PdfStore()
    try:
        with pytest.raises(OperationCancelled):
            store.write(store.load(str(source)), str(target), options=WriteOptions(compression='quality'),
                        progress_callback=progress, cancel=lambda: stopped)
        assert source.read_bytes() == original and target.read_bytes() == old_target
    finally:
        store.close_all()
