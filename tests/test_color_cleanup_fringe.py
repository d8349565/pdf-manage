"""Compression fringes must be tested against blank answers, not the old mask."""
from io import BytesIO
import json
import os
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw
import pytest

from app.colorcleanup import CleanupSettings, CleanupZones, clean_image, erasure_mask, zone_masks


def compressed_answers():
    image = Image.new('RGB', (960, 720), 'white')
    draw = ImageDraw.Draw(image)
    draw.text((40, 35), 'Question 1: (12 + 38) / 5 = ____', fill='black', font_size=25)
    draw.line((40, 100, 850, 100), fill='black', width=1)
    draw.text((100, 155), '10 - 4 = 6', fill=(165, 9, 25), font_size=48)
    draw.text((180, 330), '12 / 3 = 4', fill=(232, 139, 151), font_size=48)
    draw.text((80, 560), 'PRINT / KEEP', fill=(205, 20, 35), font_size=28)
    draw.text((550, 560), 'blue / KEEP', fill=(30, 65, 195), font_size=28)
    # The near-black colored dots are compression-like pen tips, not black text.
    tips = [(x, y) for y in range(155, 230) for x in range(100, 430)
            if image.getpixel((x, y)) == (165, 9, 25)]
    tip_x, tip_y = max(tips, key=lambda point: point[1])
    draw.point((tip_x, tip_y + 1), fill=(23, 0, 2))
    stream = BytesIO()
    image.save(stream, format='WEBP', quality=66)
    return Image.open(BytesIO(stream.getvalue())).convert('RGB'), (tip_x, tip_y + 1)


def test_compressed_handwritten_answers_leave_no_visible_fringe(tmp_path):
    image, (x, y) = compressed_answers()
    zones = CleanupZones(erased=(((x - 3) / image.width, (y - 3) / image.height,
                                  (x + 4) / image.width, (y + 4) / image.height),))
    result, mask = clean_image(image, CleanupSettings(remove_fringe=True), erase=erasure_mask(image.size, zones))
    result.save(tmp_path / 'cleaned.png')
    for box in ((75, 140, 420, 225), (155, 315, 490, 400)):
        pixels = list(result.crop(box).get_flattened_data())
        assert not [rgb for rgb in pixels if min(rgb) < 245], 'Colored / gray / dark pen fringe still visible'


def test_fringe_cleanup_keeps_black_symbols_blue_and_explicit_protection():
    image, _ = compressed_answers()
    allowed, protected = zone_masks(image.size, CleanupZones(protected=((.04, .76, .45, .86),)))
    result, mask = clean_image(image, CleanupSettings(remove_fringe=True), allowed=allowed)
    diff = ImageChops.difference(image, result)
    assert not ImageChops.multiply(diff, Image.merge('RGB', [ImageChops.invert(mask)] * 3)).getbbox()
    assert not ImageChops.multiply(diff, Image.merge('RGB', [protected] * 3)).getbbox()
    assert image.crop((30, 25, 880, 115)).tobytes() == result.crop((30, 25, 880, 115)).tobytes()
    assert image.crop((535, 550, 880, 610)).tobytes() == result.crop((535, 550, 880, 610)).tobytes()


@pytest.mark.parametrize('fringe', [False, True])
def test_manual_eraser_is_explicit_scoped_and_protected(fringe):
    image = Image.new('RGB', (240, 240), 'white')
    ImageDraw.Draw(image).rectangle((20, 20, 220, 220), fill='black')
    zones = CleanupZones(region=(.1, .1, .7, .8), protected=((.3, .3, .4, .4),), erased=((.15, .15, .9, .9),))
    allowed, protected = zone_masks(image.size, zones)
    result, mask = clean_image(image, CleanupSettings(remove_fringe=fringe), allowed=allowed,
                               erase=erasure_mask(image.size, zones))
    assert result.getpixel((50, 50)) == (255, 255, 255)
    assert result.getpixel((80, 80)) == (0, 0, 0)
    assert result.getpixel((210, 210)) == (0, 0, 0)
    diff = ImageChops.difference(image, result)
    assert not ImageChops.multiply(diff, Image.merge('RGB', [protected] * 3)).getbbox()
    assert not ImageChops.multiply(diff, Image.merge('RGB', [ImageChops.invert(allowed)] * 3)).getbbox()


def test_authorized_real_sample_has_no_visible_answer_marks(tmp_path):
    source = os.environ.get('ZHIXU_COLOR_CLEANUP_SAMPLE')
    regions = os.environ.get('ZHIXU_COLOR_CLEANUP_BLANK_REGIONS')
    if not source or not regions:
        pytest.skip('Real sample is opt-in and is never stored in the public repository')
    source = Path(source)
    original = source.read_bytes()
    image = Image.open(source).convert('RGB')
    erased = json.loads(os.environ.get('ZHIXU_COLOR_CLEANUP_ERASE_REGIONS', '[]'))
    zones = CleanupZones(erased=tuple(tuple(value / (image.width if i % 2 == 0 else image.height)
        for i, value in enumerate(box)) for box in erased))
    automatic, _ = clean_image(image, CleanupSettings(remove_fringe=True))
    automatic.save(tmp_path / 'real-automatic.png')
    result, mask = clean_image(image, CleanupSettings(remove_fringe=True), erase=erasure_mask(image.size, zones))
    result.save(tmp_path / 'real-cleaned.png')
    mask.save(tmp_path / 'real-mask.png')
    report = []
    for box in json.loads(regions):
        pixels = list(result.crop(tuple(box)).get_flattened_data())
        visible = sum(min(rgb) < 245 for rgb in pixels)
        report.append({'box': box, 'remaining_visible_pixels': visible})
    (tmp_path / 'real-result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    assert all(item['remaining_visible_pixels'] == 0 for item in report), report
    printed_regions = json.loads(os.environ.get('ZHIXU_COLOR_CLEANUP_PRINTED_REGIONS', '[]'))
    printed_report = []
    for box in printed_regions:
        before = image.crop(tuple(box))
        after = result.crop(tuple(box))
        originals = list(before.get_flattened_data())
        old_gray = list(before.convert('L').get_flattened_data())
        new_gray = list(after.convert('L').get_flattened_data())
        lost = sum(max(rgb) - min(rgb) <= 6 and gray < 160 and after_gray != gray
                   for rgb, gray, after_gray in zip(originals, old_gray, new_gray))
        added = sum(old >= 160 and new < 160 for old, new in zip(old_gray, new_gray))
        printed_report.append({'box': box, 'changed_black_pixels': lost, 'added_black_pixels': added})
    (tmp_path / 'real-printed-result.json').write_text(json.dumps(printed_report, indent=2), encoding='utf-8')
    assert not any(item['changed_black_pixels'] or item['added_black_pixels'] for item in printed_report), printed_report
    # Exercise the application's actual PDF edit and subprocess export, too.
    from PyQt6.QtWidgets import QApplication
    import pymupdf as p
    from app.colorcleanup import prepare_cleanup
    from app.pdfio import PdfStore
    from app.pdfworker import write_pdf
    application = QApplication.instance() or QApplication([])
    store = PdfStore()
    try:
        pages = store.load(str(source))
        changes = prepare_cleanup(store, pages, [0], CleanupSettings(remove_fringe=True), zones={0: zones})
        output = tmp_path / 'real-cleaned.pdf'
        write_pdf(store, [changes.get(i, ref) for i, ref in enumerate(pages)], str(output))
        with p.open(output) as document:
            pix = p.Pixmap(document, document[0].get_images()[0][0])
            assert (pix.width, pix.height) == image.size
            assert pix.samples == result.tobytes()
    finally:
        store.close_all()
    assert source.read_bytes() == original
