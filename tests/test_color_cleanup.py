"""Generated documents only: pixel invariants, PDF structure and actual Qt workflow."""
from dataclasses import replace
from io import BytesIO
from time import monotonic
import random

from PIL import Image, ImageChops, ImageDraw
import pymupdf as p
import pytest
from PyQt6.QtCore import QPoint, QThreadPool, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app.colorcleanup import (CleanupSettings, CleanupZones, clean_image, color_mask,
                              plan_page, prepare_cleanup, zone_masks)
from app.output import OperationCancelled
from app.pdfio import PdfError, PdfStore
from app.pdfworker import write_pdf
from app.workspace import Workspace


@pytest.fixture(scope='session')
def qapp():
    from pathlib import Path
    from PyQt6.QtGui import QFontDatabase
    app = QApplication.instance() or QApplication([])
    font = Path('C:/Windows/Fonts/msyh.ttc')
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    return app


def sample(size=(600, 800), noise=False):
    image = Image.new('RGB', size, (246, 246, 242))
    draw = ImageDraw.Draw(image)
    draw.text((35, 36), '1. (12 + 38) / 5 = ___   2. [6 x 7] = ___', fill='black', font_size=20)
    draw.line((40, 100, 350, 100), fill='black', width=1)
    draw.ellipse((380, 90, 470, 180), outline='black', width=1)
    draw.text((55, 135), 'ANSWER 10', fill=(185, 18, 33), font_size=26)
    draw.ellipse((40, 125, 245, 180), outline=(205, 15, 45), width=3)
    draw.text((55, 220), 'pale pink', fill=(243, 177, 197), font_size=24)
    draw.text((55, 290), 'blue answer', fill=(25, 60, 195), font_size=24)
    draw.text((370, 220), 'PRINT / STAMP', fill=(195, 22, 35), font_size=22)
    # A crossing, with true black strokes still visible above the color.
    draw.line((45, 380, 320, 380), fill=(205, 20, 45), width=5)
    draw.text((60, 365), '3. (123) + [456] = 579', fill='black', font_size=20)
    draw.line((45, 440, 400, 440), fill='black', width=1)
    if noise:
        rng = random.Random(824)
        data = image.load()
        for y in range(image.height):
            for x in range(image.width):
                rgb = data[x, y]
                shade = round(26 * x / image.width) + rng.randint(-2, 2)
                data[x, y] = tuple(max(0, min(255, c - shade)) for c in rgb)
        stream = BytesIO()
        image.save(stream, format='JPEG', quality=78)
        image = Image.open(BytesIO(stream.getvalue())).convert('RGB')
    return image


def png(image):
    stream = BytesIO()
    image.save(stream, format='PNG')
    return stream.getvalue()


def generated_pdf(path, *, rotation=0, hidden=False, mixed=False, shared=False):
    with p.open() as doc:
        xref = 0
        for i in range(3):
            page = doc.new_page(width=600, height=800)
            xref = page.insert_image(page.rect, xref=xref) if xref and shared else page.insert_image(page.rect, stream=png(sample()))
            if mixed:
                page.insert_text((330, 530), f'NATIVE page {i + 1} 42', fontsize=15)
                page.draw_line((300, 550), (530, 550), color=(0, 0, 0), width=.5)
                page.draw_line((300, 570), (530, 570), color=(1, 0, 0), width=2)
            if hidden:
                page.insert_text((55, 155), 'OLD ANSWER 10', render_mode=3)
            page.set_cropbox(p.Rect(12, 20, 588, 780))
            page.set_rotation(rotation)
        doc[0].insert_link(dict(kind=p.LINK_URI, uri='https://example.com/sample', **{'from': p.Rect(300, 600, 420, 620)}))
        doc[0].insert_link(dict(kind=p.LINK_GOTO, page=2, to=p.Point(20, 40), **{'from': p.Rect(300, 640, 420, 660)}))
        doc.set_toc([[1, 'Question sheet', 1], [1, 'Answer sheet', 3]])
        doc.set_page_labels([dict(startpage=0, prefix='Sheet ', style='D', firstpagenum=1)])
        doc.set_metadata({'title': 'Generated color cleanup acceptance'})
        doc.save(path)


@pytest.mark.parametrize('rgb', [(200, 10, 20), (110, 9, 25), (245, 165, 185),
                                      (220, 10, 50), (230, 45, 10), (30, 50, 180)])
def test_hue_wrap_dark_red_pink_and_blue(rgb):
    settings = CleanupSettings(color=(30, 65, 210) if rgb[2] > rgb[0] else (210, 25, 45))
    assert color_mask(Image.new('RGB', (5, 5), rgb), settings).getextrema() == (255, 255)


@pytest.mark.parametrize('noise', [False, True])
@pytest.mark.parametrize('factor', [.5, 1., 2.])
def test_exact_pixels_outside_mask_protection_black_symbols_and_local_scope(noise, factor):
    image = sample(noise=noise).resize((round(600 * factor), round(800 * factor)))
    zones = CleanupZones(region=(0, .08, 1, .62), protected=((.6, .24, .98, .34),),
                         strokes=((.02, ((.7, .15), (.8, .15))),))
    allowed, protected = zone_masks(image.size, zones)
    cleaned, mask = clean_image(image, allowed=allowed)
    assert mask.getbbox()
    diff = ImageChops.difference(image, cleaned)
    assert not ImageChops.multiply(diff, Image.merge('RGB', [ImageChops.invert(mask)] * 3)).getbbox()
    assert not ImageChops.multiply(diff, Image.merge('RGB', [protected] * 3)).getbbox()
    # Exact original black pixel set, not merely a count of recognized text.
    black = image.convert('L').point(lambda v: 255 if v < 35 else 0)
    assert not ImageChops.multiply(diff, Image.merge('RGB', [black] * 3)).getbbox()
    assert not diff.crop((0, round(.64 * image.height), image.width, image.height)).getbbox()
    blue_allowed = color_mask(image, CleanupSettings(color=(30, 65, 210)))
    assert not ImageChops.multiply(diff, Image.merge('RGB', [blue_allowed] * 3)).getbbox()


def test_faint_strength_background_estimation_and_eyedropper_parameters():
    faint = Image.new('RGB', (50, 50), (247, 234, 238))
    assert not color_mask(faint, CleanupSettings(faint=0)).getbbox()
    assert color_mask(faint, CleanupSettings(faint=100)).getbbox()
    image = sample(noise=True)
    allowed, _ = zone_masks(image.size, CleanupZones(region=(.05, .15, .45, .35)))
    cleaned, mask = clean_image(image, CleanupSettings(smooth_background=True), allowed=allowed)
    assert mask.getbbox() and cleaned != image
    with pytest.raises(ValueError, match='黑色'):
        clean_image(image, CleanupSettings(color=(30, 30, 30)))
    with pytest.raises(ValueError):
        zone_masks(image.size, CleanupZones(region=(.9, 0, .1, 1)))
    with pytest.raises(OperationCancelled):
        clean_image(image, cancel=lambda: True)


@pytest.mark.parametrize('rotation,extra', [(0, 0), (90, 0), (180, 0), (270, 0), (90, 90)])
def test_native_image_replacement_shared_pages_rotation_crop_navigation_and_undo(tmp_path, qapp, rotation, extra):
    source, output = tmp_path / 'source.pdf', tmp_path / 'cleaned.pdf'
    generated_pdf(source, rotation=rotation, mixed=True, shared=True)
    original = source.read_bytes()
    store, ws = PdfStore(), Workspace()
    try:
        refs = store.load(str(source))
        refs[0] = refs[0].rotated(extra)
        ws.open_replace(refs, str(source))
        before = tuple(ws.pages)
        result = plan_page(store, refs[0], preview=True)
        assert result['changed'] > 0 and result['mask'].getbbox()
        changes = prepare_cleanup(store, refs, [0])
        assert tuple(ws.pages) == before
        ws.edit_pages(changes, '去除彩色笔迹', as_copy=True)
        assert ws.path is None and ws.blocks_save_to(str(source))
        with pytest.raises(PdfError, match='另存副本'):
            store.write(ws.pages, str(source))
        # Exercises the actual subprocess edit serialization, validation and atomic commit.
        saved = write_pdf(store, ws.pages, str(output), register_result=True)
        assert len(saved.pages) == 3
        with p.open(source) as src, p.open(output) as out:
            assert out.metadata['title'] == src.metadata['title']
            assert out.get_toc() == src.get_toc()
            assert [pg.get_label() for pg in out] == [pg.get_label() for pg in src]
            for i in range(3):
                assert out[i].mediabox == src[i].mediabox
                assert out[i].cropbox == src[i].cropbox
                assert out[i].rotation == (src[i].rotation + (extra if i == 0 else 0)) % 360
                assert out[i].get_text() == src[i].get_text()
                assert len(out[i].get_drawings()) == len(src[i].get_drawings())
                if i:
                    assert out[i].get_pixmap().samples == src[i].get_pixmap().samples
                else:
                    assert out[i].get_pixmap().samples != src[i].get_pixmap().samples or extra
            assert out[0].get_links()[0]['uri'] == src[0].get_links()[0]['uri']
            assert out[0].get_links()[1]['page'] == 2
            assert out[0].get_images()[0][0] != out[1].get_images()[0][0]
            out[0].get_pixmap().save(str(tmp_path / 'cleaned.png'))
        result['before'].save(tmp_path / 'before.png')
        result['after'].save(tmp_path / 'preview.png')
        ws.undo_stack.undo()
        assert tuple(ws.pages) == before and ws.path == str(source)
        ws.undo_stack.redo()
        assert ws.path is None
        assert source.read_bytes() == original
    finally:
        store.close_all()


def test_pdf_display_scope_and_protected_stamp_are_exact(tmp_path, qapp):
    source = tmp_path / 'zones.pdf'
    with p.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_image(page.rect, stream=png(sample()))
        page.set_rotation(90)
        doc.save(source)
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        # Clockwise rotated stamp occupies the upper-right? Derive from engine coordinates.
        with store._render_page(ref) as pg:
            stamp = p.Rect(350, 200, 599, 265) * pg.rotation_matrix
            protected = (stamp.x0 / pg.rect.width, stamp.y0 / pg.rect.height, stamp.x1 / pg.rect.width, stamp.y1 / pg.rect.height)
        result = plan_page(store, ref, zones=CleanupZones(protected=(protected,)), preview=True)
        before, after = result['before'], result['after']
        box = tuple(round(v * (before.width if i % 2 == 0 else before.height)) for i, v in enumerate(protected))
        assert before.crop(box).tobytes() == after.crop(box).tobytes()
        scoped = plan_page(store, ref, zones=CleanupZones(region=(0, 0, .48, 1)), preview=True)
        assert scoped['before'].crop((round(scoped['before'].width * .5), 0, scoped['before'].width, scoped['before'].height)).tobytes() == scoped['after'].crop((round(scoped['after'].width * .5), 0, scoped['after'].width, scoped['after'].height)).tobytes()
    finally:
        store.close_all()


def annotated_pdf(path):
    with p.open() as doc:
        page = doc.new_page(width=300, height=400)
        page.insert_text((30, 35), 'Question 1: (42) + 6 = ____')
        ink = page.add_ink_annot([[(40, 80), (100, 90), (130, 75)]])
        ink.set_colors(stroke=(.8, .05, .1))
        ink.update()
        circle = page.add_circle_annot(p.Rect(40, 130, 150, 175))
        circle.set_colors(stroke=(.8, .05, .1))
        circle.update()
        note = page.add_text_annot((220, 230), 'Keep this note')
        note.update()
        page.insert_link(dict(kind=p.LINK_URI, uri='https://example.com/keep', **{'from': p.Rect(30, 300, 140, 325)}))
        widget = p.Widget()
        widget.field_name, widget.field_type, widget.rect = 'keep-field', p.PDF_WIDGET_TYPE_TEXT, p.Rect(30, 340, 160, 365)
        page.add_widget(widget)
        doc.save(path)


def test_native_annotations_only_explicit_choices_protection_links_forms(tmp_path, qapp):
    source, output = tmp_path / 'annotations.pdf', tmp_path / 'annotations-clean.pdf'
    annotated_pdf(source)
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        initial = plan_page(store, ref, mode='annotations', preview=True)
        assert len(initial['candidates']) == 2 and initial['edit'] is None
        assert initial['before'].tobytes() == initial['after'].tobytes()
        chosen = initial['candidates'][0]['id']
        changes = prepare_cleanup(store, [ref], [0], mode='annotations', selected={0: (chosen,)})
        write_pdf(store, [changes[0]], str(output))
        with p.open(source) as src, p.open(output) as out:
            assert len(list(src[0].annots())) - len(list(out[0].annots())) == 1
            assert out[0].get_text() == src[0].get_text()
            assert out[0].get_links()[0]['uri'] == 'https://example.com/keep'
            assert list(out[0].widgets())[0].field_name == 'keep-field'
        protect = initial['candidates'][0]['rect']
        filtered = plan_page(store, ref, mode='annotations', zones=CleanupZones(protected=(protect,)))
        assert chosen not in {a['id'] for a in filtered['candidates']}
        with pytest.raises(ValueError, match='所选批注'):
            plan_page(store, ref, mode='annotations', zones=CleanupZones(protected=(protect,)), selected=(chosen,))
    finally:
        store.close_all()


def test_hidden_ocr_requires_choice_keep_remove_and_visible_overlap_refusal(tmp_path, qapp):
    source = tmp_path / 'ocr.pdf'
    generated_pdf(source, hidden=True, mixed=True, shared=True)
    store = PdfStore()
    try:
        refs = store.load(str(source))
        with pytest.raises(ValueError, match='隐藏文字层'):
            prepare_cleanup(store, refs, [0])
        for policy in ('keep', 'remove'):
            changes = prepare_cleanup(store, refs, [0], ocr=policy)
            output = tmp_path / f'{policy}.pdf'
            store.write([changes.get(i, ref) for i, ref in enumerate(refs)], str(output))
            with p.open(output) as doc:
                assert ('OLD ANSWER 10' in doc[0].get_text()) == (policy == 'keep')
                assert 'NATIVE page 1' in doc[0].get_text()
                assert 'OLD ANSWER 10' in doc[1].get_text()
                assert len(doc[0].get_links()) == 2
        overlap = tmp_path / 'overlap.pdf'
        with p.open(source) as doc:
            hidden_origin = next(span for span in doc[0].get_texttrace() if span['type'] == 3)['chars'][0][2]
            doc[0].insert_text(hidden_origin, 'VISIBLE QUESTION', fontsize=11)
            doc.save(overlap)
        refs = store.load(str(overlap))
        with pytest.raises(ValueError, match='交叠'):
            prepare_cleanup(store, refs, [0], ocr='remove')
    finally:
        store.close_all()


def test_batch_recomputes_masks_and_failure_or_cancel_changes_nothing(tmp_path, qapp):
    source = tmp_path / 'batch.pdf'
    with p.open() as doc:
        for y in (70, 240):
            page = doc.new_page(width=300, height=400)
            image = Image.new('RGB', (300, 400), 'white')
            ImageDraw.Draw(image).line((30, y, 250, y), fill=(200, 15, 30), width=5)
            page.insert_image(page.rect, stream=png(image))
        doc.new_page(width=300, height=400).draw_line((20, 20), (80, 40), color=(1, 0, 0))
        doc.save(source)
    store, ws = PdfStore(), Workspace()
    try:
        refs = store.load(str(source))
        ws.open_replace(refs, str(source))
        before = tuple(ws.pages)
        changes = prepare_cleanup(store, before, [0, 1])
        for row in changes:
            with store._render_page(changes[row]) as page:
                assert color_mask(Image.frombytes('RGB', (300, 400), page.get_pixmap().samples), CleanupSettings()).getbbox() is None
        assert tuple(ws.pages) == before and not ws.dirty
        with pytest.raises(ValueError, match='第 3 页'):
            prepare_cleanup(store, before, [0, 2])
        with pytest.raises(OperationCancelled):
            prepare_cleanup(store, before, [0, 1], progress=lambda *args: None, cancel=lambda: True)
        canceled = []
        def report(step, total, text):
            if step == 1:
                canceled.append(True)
        with pytest.raises(OperationCancelled):
            prepare_cleanup(store, before, [0, 1], progress=report, cancel=lambda: bool(canceled))
        assert tuple(ws.pages) == before and not ws.dirty
        with pytest.raises(ValueError):
            prepare_cleanup(store, before, [-1])
    finally:
        store.close_all()


def test_complex_vector_requires_explicit_raster_only_selected_page_and_geometry(tmp_path, qapp):
    source, output = tmp_path / 'vectors.pdf', tmp_path / 'raster-choice.pdf'
    with p.open() as doc:
        for i in range(2):
            page = doc.new_page(width=260, height=390)
            page.insert_text((40, 60), 'Question 123 [45]')
            page.draw_line((50, 120), (190, 120), color=(.8, .05, .1), width=3)
            page.set_cropbox(p.Rect(10, 15, 250, 370))
            page.set_rotation(90)
        doc.set_toc([[1, 'Keep bookmark', 2]])
        doc.save(source)
    store = PdfStore()
    try:
        refs = store.load(str(source))
        with pytest.raises(ValueError, match='压平矢量'):
            prepare_cleanup(store, refs, [0])
        changes = prepare_cleanup(store, refs, [0], mode='raster')
        write_pdf(store, [changes[0], refs[1]], str(output))
        with p.open(source) as src, p.open(output) as out:
            assert not out[0].get_text() and out[0].get_images()
            assert out[0].cropbox == src[0].cropbox and out[0].mediabox == src[0].mediabox
            assert out[0].rotation == 90 and out[0].rect == src[0].rect
            assert out[1].get_pixmap().samples == src[1].get_pixmap().samples
            assert out.get_toc() == src.get_toc()
    finally:
        store.close_all()


def test_disk_failure_and_canceled_save_do_not_publish_partial_output(tmp_path, qapp, monkeypatch):
    source, output = tmp_path / 'source.pdf', tmp_path / 'failed.pdf'
    generated_pdf(source)
    original = source.read_bytes()
    store = PdfStore()
    try:
        refs = store.load(str(source))
        changes = prepare_cleanup(store, refs, [0])
        edited = [changes[0], *refs[1:]]
        def fail_commit(*args, **kwargs):
            raise OSError('generated disk failure')
        monkeypatch.setattr('app.pdfio.commit_file', fail_commit)
        with pytest.raises(PdfError, match='disk failure'):
            store.write(edited, str(output))
        assert not output.exists() and source.read_bytes() == original
        assert not list(tmp_path.glob('.pdfasm-*.pdf'))
        with pytest.raises(OperationCancelled):
            write_pdf(store, edited, str(output), cancel=lambda: True)
        assert not output.exists()
    finally:
        store.close_all()


def test_jpeg_decoding_shared_resources_blue_and_exact_exported_pixels(tmp_path, qapp):
    source, output = tmp_path / 'jpeg.pdf', tmp_path / 'blue-clean.pdf'
    image = sample(noise=True)
    stream = BytesIO()
    image.save(stream, format='JPEG', quality=82)
    with p.open() as doc:
        page = doc.new_page(width=600, height=800)
        xref = page.insert_image(page.rect, stream=stream.getvalue())
        page = doc.new_page(width=600, height=800)
        page.insert_image(page.rect, xref=xref)
        # Deliberately share the entire resources dictionary, not just the image.
        doc.xref_set_key(doc[1].xref, 'Resources', doc.xref_get_key(doc[0].xref, 'Resources')[1])
        doc.save(source)
    store = PdfStore()
    try:
        refs = store.load(str(source))
        settings = CleanupSettings(color=(30, 65, 210))
        changes = prepare_cleanup(store, refs, [0], settings)
        store.write([changes[0], refs[1]], str(output))
        with p.open(source) as src, p.open(output) as out:
            old = src[0].get_pixmap(colorspace=p.csRGB)
            new = out[0].get_pixmap(colorspace=p.csRGB)
            original = Image.frombytes('RGB', (old.width, old.height), old.samples)
            cleaned = Image.frombytes('RGB', (new.width, new.height), new.samples)
            mask = color_mask(original, settings)
            diff = ImageChops.difference(original, cleaned)
            assert diff.getbbox()
            assert not ImageChops.multiply(diff, Image.merge('RGB', [ImageChops.invert(mask)] * 3)).getbbox()
            assert original.crop((20, 120, 270, 270)).tobytes() == cleaned.crop((20, 120, 270, 270)).tobytes()
            assert out[1].get_pixmap().samples == src[1].get_pixmap().samples
    finally:
        store.close_all()


@pytest.mark.parametrize('kind', ['nested', 'transparent', 'repeated'])
def test_complex_image_layouts_refuse_direct_replacement(tmp_path, qapp, kind):
    source = tmp_path / f'{kind}.pdf'
    with p.open() as doc:
        page = doc.new_page(width=300, height=400)
        if kind == 'nested':
            with p.open() as inner:
                inner.new_page(width=300, height=400).insert_image(p.Rect(0, 0, 300, 400), stream=png(sample((600, 800))))
                page.show_pdf_page(page.rect, inner, 0)
        elif kind == 'transparent':
            image = sample().convert('RGBA')
            image.putalpha(200)
            page.insert_image(page.rect, stream=png(image))
        else:
            xref = page.insert_image(p.Rect(0, 0, 150, 200), stream=png(sample()))
            page.insert_image(p.Rect(150, 200, 300, 400), xref=xref)
        doc.save(source)
    store = PdfStore()
    try:
        refs = store.load(str(source))
        with pytest.raises(ValueError, match='暂不能|暂不'):
            prepare_cleanup(store, refs, [0])
    finally:
        store.close_all()


def test_grayscale_companion_image_is_preserved(tmp_path, qapp):
    source, output = tmp_path / 'rgb-gray.pdf', tmp_path / 'cleaned.pdf'
    with p.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_image(p.Rect(0, 0, 300, 400), stream=png(sample()))
        page.insert_image(p.Rect(300, 400, 600, 800), stream=png(sample().convert('L')))
        doc.save(source)
    store = PdfStore()
    try:
        refs = store.load(str(source))
        changes = prepare_cleanup(store, refs, [0])
        store.write([changes[0]], str(output))
        with p.open(source) as src, p.open(output) as out:
            assert src[0].get_pixmap(clip=p.Rect(300, 400, 600, 800)).samples == out[0].get_pixmap(clip=p.Rect(300, 400, 600, 800)).samples
    finally:
        store.close_all()


def test_inherited_resources_are_detached_on_selected_page(tmp_path, qapp):
    source, output = tmp_path / 'inherited.pdf', tmp_path / 'detached.pdf'
    with p.open() as doc:
        page = doc.new_page(width=600, height=800)
        xref = page.insert_image(page.rect, stream=png(sample()))
        page = doc.new_page(width=600, height=800)
        page.insert_image(page.rect, xref=xref)
        resources = doc.xref_get_key(page.xref, 'Resources')[1]
        parent = int(doc.xref_get_key(page.xref, 'Parent')[1].split()[0])
        doc.xref_set_key(parent, 'Resources', resources)
        for pg in doc:
            doc.xref_set_key(pg.xref, 'Resources', 'null')
        doc.save(source)
    store = PdfStore()
    try:
        refs = store.load(str(source))
        changes = prepare_cleanup(store, refs, [0])
        store.write([changes[0], refs[1]], str(output))
        with p.open(source) as src, p.open(output) as out:
            assert out[0].get_pixmap().samples != src[0].get_pixmap().samples
            assert out[1].get_pixmap().samples == src[1].get_pixmap().samples
    finally:
        store.close_all()


def test_merged_and_duplicated_source_page_keeps_unselected_copies(tmp_path, qapp):
    a, b, output = tmp_path / 'a.pdf', tmp_path / 'b.pdf', tmp_path / 'merged.pdf'
    generated_pdf(a, shared=True)
    generated_pdf(b, rotation=90)
    store = PdfStore()
    try:
        first, second = store.load(str(a)), store.load(str(b))
        refs = [first[0], second[0], first[0]]
        changes = prepare_cleanup(store, refs, [0])
        write_pdf(store, [changes[0], *refs[1:]], str(output))
        with p.open(a) as src, p.open(b) as other, p.open(output) as out:
            assert out[0].get_pixmap().samples != src[0].get_pixmap().samples
            assert out[1].get_pixmap().samples == other[0].get_pixmap().samples
            assert out[2].get_pixmap().samples == src[0].get_pixmap().samples
    finally:
        store.close_all()


def test_red_then_blue_replays_both_edits_and_continues_after_worker_save(tmp_path, qapp):
    source, output = tmp_path / 'both-colors.pdf', tmp_path / 'both-clean.pdf'
    generated_pdf(source, mixed=True)
    store = PdfStore()
    try:
        refs = store.load(str(source))
        red = prepare_cleanup(store, refs, [0])[0]
        blue = prepare_cleanup(store, [red, *refs[1:]], [0], CleanupSettings(color=(30, 65, 210)))[0]
        assert len(blue.edits) == 2
        saved = write_pdf(store, [blue, *refs[1:]], str(output), register_result=True)
        followup = plan_page(store, saved.pages[0], preview=True)
        assert followup['before'].size == followup['after'].size
        with p.open(source) as src, p.open(output) as out:
            assert out[0].get_text() == src[0].get_text()
            assert out[1].get_pixmap().samples == src[1].get_pixmap().samples
            old = p.Pixmap(src, src[0].get_images()[0][0])
            new = p.Pixmap(out, out[0].get_images()[0][0])
            assert old.samples != new.samples
            before = Image.frombytes('RGB', (old.width, old.height), old.samples)
            after = Image.frombytes('RGB', (new.width, new.height), new.samples)
            assert after.crop((40, 285, 260, 325)).tobytes() != before.crop((40, 285, 260, 325)).tobytes()
    finally:
        store.close_all()


@pytest.mark.parametrize('rotation', [0, 90, 270])
def test_hidden_text_under_image_and_intersecting_links_survive_ocr_removal(tmp_path, qapp, rotation):
    source, output = tmp_path / 'under-image.pdf', tmp_path / 'without-ocr.pdf'
    with p.open() as doc:
        page = doc.new_page(width=600, height=800)
        # A searchable old answer rendered normally, but hidden behind the scan.
        page.insert_text((55, 155), 'HIDDEN OLD ANSWER', fontsize=12)
        page.insert_image(page.rect, stream=png(sample()))
        doc.new_page(width=600, height=800)
        page = doc[0]
        page.insert_link(dict(kind=p.LINK_URI, uri='https://example.com/overlap', **{'from': p.Rect(45, 135, 210, 165)}))
        page.insert_link(dict(kind=p.LINK_GOTO, page=1, to=p.Point(40, 60), **{'from': p.Rect(50, 138, 205, 162)}))
        page.set_rotation(rotation)
        doc.save(source)
    store = PdfStore()
    try:
        refs = store.load(str(source))
        assert plan_page(store, refs[0], preview=True)['hidden']
        changes = prepare_cleanup(store, refs, [0], ocr='remove')
        store.write([changes[0], refs[1]], str(output))
        with p.open(source) as src, p.open(output) as out:
            assert 'HIDDEN' not in out[0].get_text()
            assert out[0].rotation == rotation
            for before, after in zip(src[0].get_links(), out[0].get_links()):
                assert before['from'] == after['from']
                assert before['kind'] == after['kind']
                assert before.get('uri') == after.get('uri')
                assert before.get('page') == after.get('page')
                assert before.get('to') == after.get('to')
            assert len(out[0].get_links()) == 2
    finally:
        store.close_all()


def wait(qapp, predicate, timeout=30):
    deadline = monotonic() + timeout
    while not predicate() and monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(10)
    assert predicate()


@pytest.mark.parametrize('cancel_at_commit', [False, True])
def test_main_entry_batch_apply_single_undo_and_actual_save_copy(tmp_path, qapp, monkeypatch, cancel_at_commit):
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QFileDialog, QMessageBox
    from app.colorcleanupui import ColorCleanupDialog
    from app.ui import MainWindow
    source, output = tmp_path / 'original.pdf', tmp_path / 'new-copy.pdf'
    generated_pdf(source, shared=True)
    original = source.read_bytes()
    host = MainWindow()
    host.ws.open_replace(host.store.load(str(source)), str(source))
    host.ws.rotate(90)
    host.refresh()
    original_workspace = host.ws
    before = tuple(host.ws.pages)
    before_dirty, before_commands = host.ws.dirty, host.ws.undo_stack.count()
    original_exec = ColorCleanupDialog.exec
    def reviewed_exec(dialog):
        dialog.scope.mode.setCurrentIndex(dialog.scope.mode.findData('range'))
        dialog.scope.range.setText('1-2')
        def reviewed():
            if dialog.sample_ready:
                dialog.accept()
                if cancel_at_commit:
                    assert '已取消' in dialog.status.text()
                    dialog.reject()
            else:
                QTimer.singleShot(20, reviewed)
        QTimer.singleShot(20, reviewed)
        return original_exec(dialog)
    warnings = []
    monkeypatch.setattr(ColorCleanupDialog, 'exec', reviewed_exec)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[-1]))
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(output), 'PDF (*.pdf)'))
    if cancel_at_commit:
        def cancel_after_prepare(parent, title, work, *, cancellable):
            from threading import Event
            event = Event()
            result = work(lambda *args: None, event.is_set)
            event.set()  # Cancel in the boundary between worker completion and GUI commit.
            return result
        monkeypatch.setattr('app.colorcleanupui.run_operation', cancel_after_prepare)
    try:
        host.open_color_cleanup()
        assert not warnings
        if cancel_at_commit:
            assert tuple(host.ws.pages) == before and host.ws.dirty == before_dirty
            assert host.ws.undo_stack.count() == before_commands and not output.exists()
            assert source.read_bytes() == original
            return
        assert tuple(original_workspace.pages) == before and original_workspace.dirty == before_dirty
        assert original_workspace.undo_stack.count() == before_commands
        assert host.ws is not original_workspace and host.ws.path == str(output)
        assert output.exists() and not host.ws.dirty and source.read_bytes() == original
        with p.open(source) as src, p.open(output) as out:
            assert out[2].get_pixmap().samples == src[2].get_pixmap().samples
    finally:
        QThreadPool.globalInstance().waitForDone(30000)
        for tab in host._tabs:
            tab.ws.reset()
        host.store.close_all()
        host.close()
        qapp.processEvents()


@pytest.mark.parametrize('shape', ['brush', 'rect', 'ellipse'])
def test_new_eraser_shapes_fill_only_their_pixels_and_respect_protection(shape):
    from app.colorcleanup import erasure_mask
    image = Image.new('RGB', (400, 400), (180, 180, 180))
    fields = {'erased': ((.2, .2, .8, .8),)} if shape == 'rect' else (
        {'erased_ellipses': ((.2, .2, .8, .8),)} if shape == 'ellipse' else
        {'erased_strokes': ((.04, ((.2, .4), (.8, .4))),)})
    zones = CleanupZones(protected=((.45, .3, .55, .6),), **fields)
    allowed, protected = zone_masks(image.size, zones)
    result, mask = clean_image(image, CleanupSettings(erase_fill='white'), allowed=allowed,
                               erase=erasure_mask(image.size, zones))
    assert result.getpixel((120, 160)) == (255, 255, 255)
    assert result.getpixel((200, 160)) == image.getpixel((200, 160))
    assert result.getpixel((20, 20)) == image.getpixel((20, 20))
    if shape == 'ellipse':
        assert result.getpixel((85, 85)) == image.getpixel((85, 85))
    diff = ImageChops.difference(image, result)
    assert not ImageChops.multiply(diff, Image.merge('RGB', [protected] * 3)).getbbox()
    assert not ImageChops.multiply(diff, Image.merge('RGB', [ImageChops.invert(mask)] * 3)).getbbox()


@pytest.mark.parametrize('shape', ['brush', 'ellipse'])
@pytest.mark.parametrize('rotation', [0, 90])
def test_brush_and_circle_survive_crop_rotation_and_actual_worker_export(tmp_path, qapp, shape, rotation):
    source, output = tmp_path / 'shapes.pdf', tmp_path / 'shapes-copy.pdf'
    with p.open() as doc:
        image = Image.new('RGB', (720, 480), (180, 180, 180))
        page = doc.new_page(width=360, height=240)
        xref = page.insert_image(page.rect, stream=png(image))
        doc.new_page(width=360, height=240).insert_image(p.Rect(0, 0, 360, 240), xref=xref)
        doc[0].set_cropbox(p.Rect(10, 15, 350, 225)); doc[0].set_rotation(rotation)
        doc.set_toc([[1, 'Kept bookmark', 2]]); doc.save(source)
    fields = {'erased_strokes': ((.035, ((.25, .4), (.75, .4))),)} if shape == 'brush' else {'erased_ellipses': ((.2, .2, .8, .8),)}
    store = PdfStore()
    try:
        refs = store.load(str(source))
        changes = prepare_cleanup(store, refs, [0], CleanupSettings(erase_fill='white'), zones={0: CleanupZones(**fields)})
        write_pdf(store, [changes[0], refs[1]], str(output))
        with p.open(source) as src, p.open(output) as out:
            assert out[0].cropbox == src[0].cropbox and out[0].rotation == rotation
            assert out[1].get_pixmap().samples == src[1].get_pixmap().samples
            assert out.get_toc() == src.get_toc()
            pix = out[0].get_pixmap(colorspace=p.csRGB)
            assert min(pix.pixel(round(pix.width * .3), round(pix.height * .4))) > 248
            assert max(pix.pixel(2, 2)) < 200
    finally:
        store.close_all()


def workbench_pdf(path, *, landscape=True, count=2, smudge=False):
    from pathlib import Path
    from PIL import ImageFont
    font_path = Path('C:/Windows/Fonts/msyh.ttc')
    font = ImageFont.truetype(str(font_path), 24) if font_path.exists() else ImageFont.load_default(size=24)
    size = (1600, 1132) if landscape else (800, 1132)
    image = Image.new('RGB', size, 'white'); draw = ImageDraw.Draw(image)
    columns = (65, 850) if landscape else (65,)
    for column, left in enumerate(columns):
        draw.text((left, 50), '数学练习' if column == 0 else '二、计算', fill='black', font=font)
        draw.text((left, 110), '班级：________   姓名：________', fill='black', font=font)
        for row, question in enumerate(('1. 12 ÷ 4 = （     ）', '2. 6 × （     ） = 18', '3. 20 ÷ 5 = （     ）',
                                         '4. 8 + （     ） = 15', '5. 看图列式，再填空。')):
            draw.text((left, 220 + row * 100), question, fill='black', font=font)
        draw.text((left + 230, 230), '3', fill=(190, 15, 35), font=font)
        draw.line((left + 20, 820, left + 600, 820), fill='black', width=2)
        for tick in range(7):
            x = left + 20 + tick * 90
            draw.line((x, 810, x, 830), fill='black', width=2)
            draw.text((x - 6, 840), str(tick * 5), fill='black', font=font)
    if smudge: draw.rectangle((round(size[0] * .3), round(size[1] * .35), round(size[0] * .7), round(size[1] * .65)), fill=(120, 120, 120))
    with p.open() as document:
        for _ in range(count):
            page = document.new_page(width=1190.55 if landscape else 595.28, height=841.89)
            page.insert_image(page.rect, stream=png(image))
        document.save(path)


def close_workbench(dialog, host, qapp):
    dialog.reject()
    QThreadPool.globalInstance().waitForDone(30000)
    for tab in host._tabs: tab.ws.reset()
    host.store.close_all(); host.close(); qapp.processEvents()


def test_workbench_live_brush_protection_keyboard_undo_redo_and_zoom_size(tmp_path, qapp):
    from app.colorcleanupui import ColorCleanupDialog
    from app.ui import MainWindow
    source = tmp_path / 'a3.pdf'; workbench_pdf(source, smudge=True)
    host = MainWindow(); host.ws.open_replace(host.store.load(str(source)), str(source)); host.refresh()
    dialog = ColorCleanupDialog(host); dialog.resize(1366, 900)
    try:
        dialog.show(); wait(qapp, lambda: dialog.sample_ready)
        dialog.change_zones(CleanupZones(protected=((.47, .45, .53, .55),)))
        wait(qapp, lambda: dialog.sample_ready)
        dialog.tool_buttons['erase'].click(); dialog.shape_buttons['brush'].click(); qapp.processEvents()
        radius = dialog.canvas.radius
        dialog.set_zoom(110); qapp.processEvents()
        assert dialog.canvas.radius == pytest.approx(radius)
        area = dialog.canvas.area()
        start = QPoint(round(area.x() + .4 * area.width()), round(area.y() + .5 * area.height()))
        end = QPoint(round(area.x() + .6 * area.width()), round(area.y() + .5 * area.height()))
        QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(dialog.canvas, end)
        assert not dialog.canvas.live.isNull() and not dialog.apply.isEnabled()
        live = dialog.canvas.live
        assert live.pixelColor(round(.4 * live.width()), round(.5 * live.height())).alpha() > 0
        assert live.pixelColor(round(.5 * live.width()), round(.5 * live.height())).alpha() == 0
        QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=end)
        wait(qapp, lambda: dialog.sample_ready)
        assert len(dialog.page_zones().erased_strokes) == 1
        image = dialog.result['cleaned']
        assert image.pixelColor(round(.4 * image.width()), round(.5 * image.height())).red() > 248
        assert image.pixelColor(round(.5 * image.width()), round(.5 * image.height())).red() < 150
        dialog.activateWindow(); dialog.canvas.setFocus(); qapp.processEvents()
        QTest.keyClick(dialog.canvas, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        assert not dialog.page_zones().erased_strokes
        QTest.keyClick(dialog.canvas, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        assert dialog.page_zones().erased_strokes
        wait(qapp, lambda: dialog.sample_ready)
    finally:
        close_workbench(dialog, host, qapp)


def test_workbench_circle_is_physical_circle_and_stays_on_its_page(tmp_path, qapp):
    from app.colorcleanupui import ColorCleanupDialog
    from app.ui import MainWindow
    source = tmp_path / 'two-pages.pdf'; workbench_pdf(source, smudge=True)
    host = MainWindow(); host.ws.open_replace(host.store.load(str(source)), str(source)); host.refresh()
    dialog = ColorCleanupDialog(host); dialog.resize(1280, 880)
    try:
        dialog.show(); wait(qapp, lambda: dialog.sample_ready)
        dialog.tool_buttons['erase'].click(); dialog.shape_buttons['ellipse'].click(); qapp.processEvents()
        area = dialog.canvas.area()
        start = QPoint(round(area.x() + .35 * area.width()), round(area.y() + .4 * area.height()))
        end = QPoint(round(area.x() + .48 * area.width()), round(area.y() + .58 * area.height()))
        QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(dialog.canvas, end)
        QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=end)
        wait(qapp, lambda: dialog.sample_ready)
        x0, y0, x1, y1 = dialog.page_zones().erased_ellipses[0]
        width, height = dialog.canvas.page_size
        assert (x1 - x0) * width == pytest.approx((y1 - y0) * height)
        dialog.next.click(); wait(qapp, lambda: dialog.sample_ready)
        assert not dialog.page_zones().erased_ellipses
        dialog.copy_zones.click(); wait(qapp, lambda: dialog.sample_ready)
        assert dialog.zones[0].erased_ellipses and not dialog.zones[1].erased_ellipses
        dialog.previous.click(); wait(qapp, lambda: dialog.sample_ready)
        assert dialog.page_zones().erased_ellipses
        dialog.next.click(); wait(qapp, lambda: dialog.sample_ready)
        dialog.scope.mode.setCurrentIndex(dialog.scope.mode.findData('current'))
        assert dialog.current == 1 and dialog.scope.indices() == [1]
    finally:
        close_workbench(dialog, host, qapp)


@pytest.mark.parametrize('mode', ['L', '1'])
def test_manual_eraser_handles_grayscale_and_bilevel_without_changing_shared_page(tmp_path, qapp, mode):
    source, output = tmp_path / 'gray.pdf', tmp_path / 'gray-copy.pdf'
    with p.open() as document:
        image = Image.new(mode, (600, 800), 0)
        page = document.new_page(width=300, height=400)
        xref = page.insert_image(page.rect, stream=png(image))
        document.new_page(width=300, height=400).insert_image(p.Rect(0, 0, 300, 400), xref=xref)
        document.save(source)
    store = PdfStore()
    try:
        pages = store.load(str(source))
        zones = CleanupZones(erased_strokes=((.03, ((.3, .3), (.7, .3))),), erased_ellipses=((.5, .5, .7, .7),))
        changes = prepare_cleanup(store, pages, [0], CleanupSettings(erase_fill='white'), zones={0: zones})
        write_pdf(store, [changes[0], pages[1]], str(output))
        with p.open(source) as before, p.open(output) as after:
            assert after[1].get_pixmap().samples == before[1].get_pixmap().samples
            pix = after[0].get_pixmap()
            assert min(pix.pixel(120, 120)) > 248
            assert max(pix.pixel(10, 10)) < 10
    finally:
        store.close_all()


def test_workbench_cancel_original_path_and_write_failure_keep_draft(tmp_path, qapp, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog, QMessageBox
    from app.colorcleanupui import ColorCleanupDialog
    from app.ui import MainWindow
    source = tmp_path / 'original.pdf'; workbench_pdf(source, count=1)
    original = source.read_bytes()
    host = MainWindow(); host.ws.open_replace(host.store.load(str(source)), str(source)); host.refresh()
    before = tuple(host.ws.pages)
    dialog = ColorCleanupDialog(host); warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[-1]))
    try:
        dialog.show(); wait(qapp, lambda: dialog.sample_ready)
        dialog.add_zone('erase', ('rect', (.4, .4, .45, .45)))
        wait(qapp, lambda: dialog.sample_ready)
        marks = dict(dialog.zones)
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: ('', ''))
        dialog.accept()
        assert dialog.isVisible() and not dialog.closed and dialog.zones == marks
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(source), 'PDF (*.pdf)'))
        dialog.accept()
        assert warnings and source.read_bytes() == original and dialog.isVisible()
        output = tmp_path / 'failed-copy.pdf'
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(output), 'PDF (*.pdf)'))
        def fail(*args, **kwargs): raise PdfError('generated disk failure')
        monkeypatch.setattr(host, '_write_pdf', fail)
        dialog.accept()
        assert 'disk failure' in warnings[-1]
        assert dialog.isVisible() and dialog.zones == marks and dialog.apply.isEnabled()
        assert tuple(host.ws.pages) == before and not host.ws.dirty and not output.exists()
        assert source.read_bytes() == original
    finally:
        close_workbench(dialog, host, qapp)


def test_workbench_zoom_refines_cached_edit_without_recalculating_color_mask(tmp_path, qapp, monkeypatch):
    from app.colorcleanupui import ColorCleanupDialog
    from app.ui import MainWindow
    source = tmp_path / 'a3-detail.pdf'; workbench_pdf(source, count=1)
    host = MainWindow(); host.ws.open_replace(host.store.load(str(source)), str(source)); host.refresh()
    dialog = ColorCleanupDialog(host); dialog.resize(1280, 850)
    try:
        dialog.show(); wait(qapp, lambda: dialog.sample_ready)
        previous_width = dialog.result['original'].width()
        def no_reanalysis(*args, **kwargs): raise AssertionError('Zoom must reuse the prepared edit')
        monkeypatch.setattr('app.colorcleanupui.plan_page', no_reanalysis)
        dialog.set_zoom(240)
        wait(qapp, lambda: dialog.result.get('original').width() > previous_width)
        assert dialog.sample_ready and dialog.apply.isEnabled()
        assert dialog.scroll.horizontalScrollBar().maximum() > 0
        assert dialog.scroll.verticalScrollBar().maximum() > 0
    finally:
        close_workbench(dialog, host, qapp)


def test_workbench_later_page_ocr_choice_is_required_without_losing_draft(tmp_path, qapp, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog, QMessageBox
    from app.colorcleanupui import ColorCleanupDialog
    from app.ui import MainWindow
    initial, source, output = tmp_path / 'base.pdf', tmp_path / 'with-ocr.pdf', tmp_path / 'cleaned.pdf'
    workbench_pdf(initial)
    with p.open(initial) as document:
        document[1].insert_text((40, 50), 'OLD ANSWER', render_mode=3)
        document.save(source)
    original = source.read_bytes()
    host = MainWindow(); host.ws.open_replace(host.store.load(str(source)), str(source)); host.refresh()
    before = tuple(host.ws.pages)
    dialog = ColorCleanupDialog(host); warnings = []
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(output), 'PDF (*.pdf)'))
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[-1]))
    try:
        dialog.show(); wait(qapp, lambda: dialog.sample_ready)
        dialog.add_zone('erase', ('rect', (.4, .4, .45, .45)))
        wait(qapp, lambda: dialog.sample_ready)
        marks = dict(dialog.zones)
        dialog.accept()
        assert warnings and dialog.force_ocr and dialog.ocr_box.isVisible()
        assert not dialog.apply.isEnabled() and dialog.zones == marks and not output.exists()
        assert tuple(host.ws.pages) == before
        dialog.ocr.setCurrentIndex(dialog.ocr.findData('remove'))
        wait(qapp, lambda: dialog.sample_ready)
        assert dialog.apply.isEnabled()
        dialog.accept()
        assert dialog.saved_result is not None and output.exists()
        with p.open(output) as document:
            assert 'OLD ANSWER' not in document[1].get_text()
        assert tuple(host.ws.pages) == before and not host.ws.dirty and source.read_bytes() == original
    finally:
        close_workbench(dialog, host, qapp)


@pytest.mark.parametrize('landscape,theme', [(True, 'forest'), (False, 'forest'), (True, 'night')])
def test_workbench_rendered_layout_a3_a4_and_themes(tmp_path, qapp, landscape, theme):
    from app.colorcleanupui import ColorCleanupDialog
    from app.themes import current_theme, theme_manager
    from app.ui import MainWindow
    source = tmp_path / 'layout.pdf'; workbench_pdf(source, landscape=landscape)
    previous_theme = current_theme().key
    host = MainWindow(); host.ws.open_replace(host.store.load(str(source)), str(source)); host.refresh()
    theme_manager().apply(theme)
    assert current_theme().key == theme
    dialog = ColorCleanupDialog(host); dialog.resize(1366, 900)
    try:
        dialog.show(); wait(qapp, lambda: dialog.sample_ready)
        assert not dialog.adjustments.isVisible() and not dialog.tolerance.isVisible()
        dialog.tool_buttons['erase'].click(); qapp.processEvents()
        area = dialog.canvas.area()
        QTest.mouseMove(dialog.canvas, QPoint(round(area.x() + .37 * area.width()), round(area.y() + .44 * area.height())))
        assert all(button.isVisible() for button in dialog.shape_buttons.values())
        assert dialog.apply.geometry().right() <= dialog.width()
        assert dialog.canvas.area().width() <= dialog.scroll.viewport().width()
        assert dialog.canvas.area().height() <= dialog.scroll.viewport().height()
        assert dialog.canvas.area().width() / dialog.canvas.area().height() == pytest.approx(dialog.canvas.page_size[0] / dialog.canvas.page_size[1])
        dialog.grab().save(str(tmp_path / f'workbench-{theme}-{landscape}.png'))
        dialog.resize(900, 680); qapp.processEvents()
        assert dialog.width() <= 920
        assert dialog.apply.mapTo(dialog, QPoint(dialog.apply.width(), 0)).x() <= dialog.width()
        dialog.grab().save(str(tmp_path / 'compact.png'))
    finally:
        close_workbench(dialog, host, qapp)
        theme_manager().apply(previous_theme)


def test_dialog_real_preview_pick_protect_scope_reset_and_cancel(tmp_path, qapp):
    from app.colorcleanupui import ColorCleanupDialog
    from app.ui import MainWindow
    source = tmp_path / 'source.pdf'
    generated_pdf(source)
    host = MainWindow()
    host.ws.open_replace(host.store.load(str(source)), str(source))
    host.refresh()
    before = tuple(host.ws.pages)
    dialog = ColorCleanupDialog(host)
    try:
        dialog.show()
        wait(qapp, lambda: dialog.sample_ready)
        assert dialog.apply.isEnabled()
        assert dialog.scope.indices() == [0, 1, 2]
        dialog.show_mask.setChecked(True)
        assert not dialog.canvas.image.isNull()
        dialog.tool_buttons['keep'].click()
        dialog.shape_buttons['rect'].click()
        qapp.processEvents()
        area = dialog.canvas.area()
        QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=QPoint(round(area.x() + .6 * area.width()), round(area.y() + .24 * area.height())))
        QTest.mouseMove(dialog.canvas, QPoint(round(area.x() + .95 * area.width()), round(area.y() + .33 * area.height())))
        QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=QPoint(round(area.x() + .95 * area.width()), round(area.y() + .33 * area.height())))
        assert dialog.page_zones().protected
        wait(qapp, lambda: dialog.sample_ready)
        dialog.undo_zone.click()
        assert not dialog.page_zones().protected
        dialog.scope.mode.setCurrentIndex(dialog.scope.mode.findData('range'))
        dialog.scope.range.setText('1-2')
        wait(qapp, lambda: dialog.sample_ready)
        assert dialog.scope.indices() == [0, 1]
        dialog.next.click()
        wait(qapp, lambda: dialog.sample_ready)
        assert dialog.current == 1
        dialog.pick_color((30, 60, 200))
        wait(qapp, lambda: dialog.sample_ready)
        assert dialog.settings().color == (30, 60, 200)
        dialog.add_zone('erase', ('rect', (.45, .4, .52, .45)))
        wait(qapp, lambda: dialog.sample_ready)
        dialog.advanced.click()
        qapp.processEvents()
        assert dialog.adjustments.isVisible()
        dialog.copy_zones.click()
        assert not dialog.zones[0].erased and dialog.zones[1].erased
        wait(qapp, lambda: dialog.sample_ready)
        dialog.clear_manual.click()
        dialog.reset.click()
        dialog.choose_color((210, 25, 45))
        wait(qapp, lambda: dialog.sample_ready)
        assert dialog.page_zones() == CleanupZones()
        assert dialog.settings() == CleanupSettings(remove_fringe=True, erase_fill='white')
        dialog.adjustments.hide()
        dialog.tool_buttons['erase'].click()
        dialog.shape_buttons['brush'].click()
        qapp.processEvents()
        dialog.grab().save(str(tmp_path / 'dialog.png'))
        dialog.reject()
        assert tuple(host.ws.pages) == before and not host.ws.dirty
    finally:
        dialog.close()
        QThreadPool.globalInstance().waitForDone(30000)
        host.ws.reset()
        host.store.close_all()
        host.close()
        qapp.processEvents()


def test_dialog_native_selection_and_type_filter_are_explicit(tmp_path, qapp):
    from app.colorcleanupui import ColorCleanupDialog
    from app.ui import MainWindow
    source = tmp_path / 'annotations.pdf'
    annotated_pdf(source)
    host = MainWindow()
    host.ws.open_replace(host.store.load(str(source)), str(source))
    host.refresh()
    dialog = ColorCleanupDialog(host)
    try:
        dialog.method.setCurrentIndex(dialog.method.findData('annotations'))
        dialog.show()
        wait(qapp, lambda: dialog.sample_ready)
        assert dialog.annotations.count() == 2
        assert not dialog.selected.get(0) and dialog.result['edit'] is None
        dialog.annotation_type.setCurrentIndex(dialog.annotation_type.findData('墨迹'))
        assert dialog.annotations.count() == 1
        dialog.select_annotations.click()
        wait(qapp, lambda: dialog.sample_ready)
        assert len(dialog.selected[0]) == 1 and dialog.result['mask'].getbbox()
        dialog.clear_annotations.click()
        wait(qapp, lambda: dialog.sample_ready)
        assert not dialog.selected[0] and not dialog.result['mask'].getbbox()
        assert not host.ws.dirty
    finally:
        dialog.reject()
        QThreadPool.globalInstance().waitForDone(30000)
        host.ws.reset()
        host.store.close_all()
        host.close()
        qapp.processEvents()


def test_dialog_brush_mark_and_cancel_running_preview(tmp_path, qapp):
    from app.colorcleanupui import ColorCleanupDialog
    from app.ui import MainWindow
    source = tmp_path / 'brush.pdf'
    generated_pdf(source)
    host = MainWindow()
    host.ws.open_replace(host.store.load(str(source)), str(source))
    host.refresh()
    before = tuple(host.ws.pages)
    dialog = ColorCleanupDialog(host)
    try:
        dialog.show()
        wait(qapp, lambda: dialog.sample_ready)
        dialog.tool_buttons['keep'].click()
        dialog.shape_buttons['brush'].click()
        qapp.processEvents()
        area = dialog.canvas.area()
        start = QPoint(round(area.x() + .08 * area.width()), round(area.y() + .17 * area.height()))
        end = QPoint(round(area.x() + .4 * area.width()), round(area.y() + .2 * area.height()))
        QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(dialog.canvas, end)
        QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=end)
        assert dialog.page_zones().strokes
        wait(qapp, lambda: dialog.sample_ready)
        dialog.tool_buttons['erase'].click()
        dialog.shape_buttons['rect'].click()
        qapp.processEvents()
        area = dialog.canvas.area()
        start = QPoint(round(area.x() + .45 * area.width()), round(area.y() + .4 * area.height()))
        end = QPoint(round(area.x() + .52 * area.width()), round(area.y() + .45 * area.height()))
        QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(dialog.canvas, end)
        QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=end)
        assert dialog.page_zones().erased
        wait(qapp, lambda: dialog.sample_ready)
        dialog.undo_zone.click()
        assert not dialog.page_zones().erased and dialog.page_zones().strokes
        wait(qapp, lambda: dialog.sample_ready)
        dialog.invalidate()
        dialog.timer.stop()
        dialog.start_preview()
        assert dialog.busy
        dialog.reject()
        assert dialog.cancel.is_set()
        wait(qapp, lambda: not dialog.busy)
        assert tuple(host.ws.pages) == before and not host.ws.dirty
    finally:
        dialog.reject()
        QThreadPool.globalInstance().waitForDone(30000)
        host.ws.reset()
        host.store.close_all()
        host.close()
        qapp.processEvents()
