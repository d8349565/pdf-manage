"""Independent generated-fixture evaluation; criteria were recorded before source review.

Run with --basetemp=build/independent-compression-eval/pytest-temp so every
generated fixture, Qt setting, scratch file and report stays in the study folder.
"""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import random
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace

import pymupdf as pdf
import pytest
from PIL import Image, ImageChops, ImageDraw
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QImage, QWheelEvent
from PyQt6.QtWidgets import QApplication, QWidget

from app.compressionpreview import CompressionPreview
from app.output import OperationCancelled
from app.pdfio import PdfError, PdfStore, WriteOptions


ROOT = Path(__file__).resolve().parents[1] / 'build' / 'independent-compression-eval'


def record(name, **data):
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / f'{name}.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def stamp(path):
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns


def encode(image, kind='PNG', **options):
    stream = BytesIO()
    image.save(stream, kind, **options)
    return stream.getvalue()


def compress(source, destination, profile='quality', **kwargs):
    store = PdfStore()
    try:
        refs = store.load(str(source))
        store.write(refs, str(destination), options=WriteOptions(compression=profile), **kwargs)
    finally:
        store.close_all()


def compare_pixels(source, target):
    results = []
    with pdf.open(source) as before, pdf.open(target) as after:
        assert len(before) == len(after)
        for index in range(len(before)):
            for zoom in (1, 2):
                a = before[index].get_pixmap(matrix=pdf.Matrix(zoom, zoom), alpha=False)
                b = after[index].get_pixmap(matrix=pdf.Matrix(zoom, zoom), alpha=False)
                assert (a.width, a.height, a.n) == (b.width, b.height, b.n)
                difference = ImageChops.difference(Image.frombytes('RGB', (a.width, a.height), a.samples),
                                                  Image.frombytes('RGB', (b.width, b.height), b.samples))
                extrema = difference.getextrema()
                changed_channels = sum(a != b for a, b in zip(a.samples, b.samples))
                results.append(dict(page=index + 1, zoom=zoom, changed_channels=changed_channels,
                                    maximum_channel_difference=max(pair[1] for pair in extrema)))
    return results


def semantics(path):
    with pdf.open(path) as doc:
        return dict(
            pages=[dict(text=page.get_text(), rect=tuple(page.rect), crop=tuple(page.cropbox), rotation=page.rotation,
                        links=[{key: tuple(value) if key in ('from', 'to') else value
                                for key, value in link.items() if key not in ('xref', 'id')}
                               for link in page.get_links()],
                        widgets=[(w.field_name, w.field_type, w.field_value, tuple(w.rect))
                                 for w in (page.widgets() or [])],
                        annots=[(a.type, a.info, tuple(a.rect)) for a in (page.annots() or [])]) for page in doc],
            toc=doc.get_toc(), metadata=doc.metadata, labels=doc.get_page_labels(),
            attachments={name: hashlib.sha256(doc.embfile_get(name)).hexdigest() for name in doc.embfile_names()})


def make_interactive(path, *, interactive=True):
    with pdf.open() as doc:
        for index, size in enumerate(((432, 576), (600, 360), (360, 620))):
            page = doc.new_page(width=size[0], height=size[1])
            page.insert_font(fontname='GeneratedCJK', fontbuffer=pdf.Font('cjk').buffer)
            page.insert_text((35, 62), f'独立材料：合同金额 123.45 / 第 {index + 1} 页', fontname='GeneratedCJK', fontsize=15)
            page.insert_text((35, 92), 'Searchable Latin: ffi, 12345, ORIGINAL', fontsize=12)
            page.draw_rect(pdf.Rect(25, 25, size[0] - 25, size[1] - 25), color=(0.1, 0.5, 0.7), width=.35)
        page = doc[0]
        page.insert_link({'kind': pdf.LINK_URI, 'uri': 'https://example.invalid/generated-only', 'from': pdf.Rect(35, 100, 260, 120)})
        page.insert_link({'kind': pdf.LINK_GOTO, 'page': 2, 'to': pdf.Point(35, 50), 'from': pdf.Rect(35, 125, 180, 145)})
        if interactive:
            widget = pdf.Widget()
            widget.field_name, widget.field_type, widget.field_value = 'generated_name', pdf.PDF_WIDGET_TYPE_TEXT, 'ALPHA 123'
            widget.rect = pdf.Rect(35, 180, 280, 215)
            page.add_widget(widget)
            checkbox = pdf.Widget()
            checkbox.field_name, checkbox.field_type = 'generated_consent', pdf.PDF_WIDGET_TYPE_CHECKBOX
            checkbox.rect = pdf.Rect(35, 230, 55, 250)
            page.add_widget(checkbox)
            note = doc[1].add_text_annot((80, 170), 'Generated review note')
            note.set_info(title='Independent evaluator', subject='Keep editable')
            note.update()
        doc[1].set_rotation(90)
        doc[2].set_cropbox(pdf.Rect(15, 20, 345, 600))
        doc.set_toc([[1, '独立目录', 1], [2, 'Rotated appendix', 2], [1, 'Cropped appendix', 3]])
        doc.set_page_labels([{'startpage': 0, 'prefix': 'CASE-', 'style': 'D', 'firstpagenum': 7}])
        doc.set_metadata(dict(title='Generated independent evaluation', author='Synthetic author', subject='No business data'))
        doc.embfile_add('fixture.txt', b'Synthetic attachment: 0123456789\n', filename='fixture.txt', desc='Generated only')
        doc.save(path, deflate=False)


@pytest.mark.parametrize('interactive', [True, False], ids=['forms-annotations', 'font-subsetting'])
def test_semantics_rendering_and_original_protection(tmp_path, interactive):
    source, target = tmp_path / 'semantic-source.pdf', tmp_path / 'semantic-compressed.pdf'
    make_interactive(source, interactive=interactive)
    original = stamp(source)
    expected = semantics(source)
    compress(source, target)
    actual = semantics(target)
    pixels = compare_pixels(source, target)
    record(f'semantic-{interactive}', source=str(source), output=str(target), before_bytes=source.stat().st_size,
           after_bytes=target.stat().st_size, pixels=pixels, semantics_equal=actual == expected,
           original_unchanged=stamp(source) == original)
    assert stamp(source) == original
    assert actual == expected
    assert all(item['changed_channels'] == 0 for item in pixels)
    if not interactive:
        assert target.stat().st_size < source.stat().st_size * .2
    else:
        # Exercise continued editing rather than merely counting a widget object.
        edited = tmp_path / 'semantic-edited.pdf'
        with pdf.open(target) as doc:
            page = doc[0]
            widget = next(w for w in page.widgets() if w.field_name == 'generated_name')
            widget.field_value = 'NEW EDITABLE VALUE 6789'
            widget.update()
            doc.save(edited)
        with pdf.open(edited) as doc:
            page = doc[0]
            assert next(w for w in page.widgets() if w.field_name == 'generated_name').field_value == 'NEW EDITABLE VALUE 6789'


def make_image_stress(path):
    rng = random.Random(62917)
    with pdf.open() as doc:
        # An intentionally uncompressed two-color high-resolution chart; the
        # colors are not black/white, so an accidental grayscale conversion shows.
        chart = Image.new('RGB', (960, 720), (244, 226, 192))
        drawing = ImageDraw.Draw(chart)
        for index in range(180):
            x, y = rng.randrange(950), rng.randrange(710)
            drawing.rectangle((x, y, min(959, x + 20), min(719, y + 6)), fill=(12, 89, 181))
        page = doc.new_page(width=480, height=360)
        shared = page.insert_image(page.rect, stream=encode(chart))
        doc.xref_set_key(shared, 'Interpolate', 'true')
        page = doc.new_page(width=240, height=180)
        page.insert_image(page.rect, xref=shared)
        # Soft masks and a partially transparent overlay over colored vectors.
        page = doc.new_page(width=440, height=330)
        page.draw_rect(page.rect, color=None, fill=(.12, .7, .5))
        alpha = Image.new('RGBA', (500, 350), (180, 30, 220, 0))
        draw = ImageDraw.Draw(alpha)
        for x in range(0, 500, 5):
            draw.rectangle((x, 0, x + 4, 349), fill=(180, 30, 220, x % 256))
        page.insert_image(pdf.Rect(20, 20, 420, 310), stream=encode(alpha))
        # PDF Decode reverses sample polarity; this must survive unchanged.
        page = doc.new_page(width=420, height=300)
        gray = Image.linear_gradient('L').resize((720, 512))
        xref = page.insert_image(page.rect, stream=encode(gray))
        doc.xref_set_key(xref, 'Decode', '[1 0]')
        # CMYK JPEG uses different channel interpretation than ordinary RGB.
        page = doc.new_page(width=420, height=300)
        cmyk = Image.new('CMYK', (600, 420), (0, 140, 80, 20))
        ImageDraw.Draw(cmyk).ellipse((30, 30, 570, 390), fill=(210, 40, 0, 70))
        page.insert_image(page.rect, stream=encode(cmyk, 'JPEG', quality=91))
        page.set_rotation(270)
        # More than 256 exact colors, correlated pixels, and fine periodic marks.
        page = doc.new_page(width=432, height=288)
        data = bytes(channel for y in range(500) for x in range(750)
                     for channel in ((x + y) % 256, (x // 4 + 2 * y) % 256, (y // 3 + x) % 256))
        page.insert_image(page.rect, stream=encode(Image.frombytes('RGB', (750, 500), data)))
        for page in doc:
            page.insert_text((12, 18), f'Generated chart {page.number + 1}', fontsize=9)
        doc.save(path, deflate=False)


def test_image_color_mask_and_repeat_compression(tmp_path):
    source, target, repeated = (tmp_path / name for name in ('mixed-images.pdf', 'mixed-compressed.pdf', 'mixed-repeat.pdf'))
    make_image_stress(source)
    original = stamp(source)
    compress(source, target)
    compress(target, repeated)
    first_pixels, repeated_pixels = compare_pixels(source, target), compare_pixels(source, repeated)
    record('mixed-images', source=str(source), output=str(target), repeated=str(repeated),
           bytes=[path.stat().st_size for path in (source, target, repeated)],
           first_pixels=first_pixels, repeated_pixels=repeated_pixels,
           semantic_preservation=semantics(source) == semantics(target))
    assert stamp(source) == original
    assert semantics(source) == semantics(target) == semantics(repeated)
    assert all(row['changed_channels'] == 0 for row in first_pixels + repeated_pixels)
    assert target.stat().st_size < source.stat().st_size * .5
    assert repeated.stat().st_size <= target.stat().st_size


@pytest.mark.parametrize('kind', ['tiny-vector', 'optimized-jpeg'])
def test_already_small_files_and_repeat(tmp_path, kind):
    source, target, repeated = (tmp_path / name for name in ('small.pdf', 'small-out.pdf', 'small-repeat.pdf'))
    with pdf.open() as doc:
        page = doc.new_page(width=300, height=200)
        page.insert_text((30, 50), 'Already small: generated PDF', fontsize=11)
        if kind == 'optimized-jpeg':
            rng = random.Random(823)
            image = Image.frombytes('RGB', (480, 320), rng.randbytes(480 * 320 * 3))
            page.insert_image(pdf.Rect(10, 60, 290, 190), stream=encode(image, 'JPEG', quality=72, optimize=True))
        doc.save(source, garbage=4, deflate=True, use_objstms=1)
    compress(source, target)
    compress(target, repeated)
    pixels = compare_pixels(source, repeated)
    record(kind, paths=[str(path) for path in (source, target, repeated)],
           bytes=[path.stat().st_size for path in (source, target, repeated)], pixels=pixels)
    assert all(row['changed_channels'] == 0 for row in pixels)
    assert repeated.stat().st_size <= target.stat().st_size


def test_source_overwrite_rejected_and_cancel_keeps_target(tmp_path):
    source, target = tmp_path / 'protected-source.pdf', tmp_path / 'existing-target.pdf'
    make_image_stress(source)
    with pdf.open() as existing:
        existing.new_page().insert_text((40, 50), 'Generated existing target - preserve after cancellation')
        existing.save(target)
    source_stamp, target_stamp = stamp(source), stamp(target)
    store = PdfStore()
    progress = []
    try:
        refs = store.load(str(source))
        with pytest.raises(PdfError, match='不能覆盖'):
            store.write(refs, str(source), options=WriteOptions(compression='quality'))
        def report(current, total, text):
            progress.append(text)
        with pytest.raises(OperationCancelled):
            store.write(refs, str(target), options=WriteOptions(compression='quality'), progress_callback=report,
                        cancel=lambda: any('逐张优化' in message for message in progress))
    finally:
        store.close_all()
    record('source-safety', source_unchanged=stamp(source) == source_stamp, target_unchanged=stamp(target) == target_stamp,
           leftover_staging_files=[p.name for p in tmp_path.glob('.pdfasm-*')], progress=progress)
    assert stamp(source) == source_stamp and stamp(target) == target_stamp
    assert not list(tmp_path.glob('.pdfasm-*'))


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


def wait(qapp, condition, seconds=20):
    deadline = monotonic() + seconds
    while not condition() and monotonic() < deadline:
        qapp.processEvents()
        sleep(.01)
    assert condition(), 'Asynchronous preview did not reach the required state'


def make_preview_pages(path):
    with pdf.open() as doc:
        for index in range(30):
            width, height = ((300, 510), (620, 230), (260, 840))[index % 3]
            page = doc.new_page(width=width, height=height)
            page.insert_text((25, 40), f'INDEPENDENT PAGE {index + 1}', fontsize=14)
            page.draw_rect(pdf.Rect(25, 60, width - 25, height - 25), color=(.2, .3, .7), fill=(.85, .94, .88))
            for y in range(70, height - 25, 30):
                page.draw_line((25, y), (width - 25, y), color=(.6, .2, .3))
        doc.save(path)


def wheel(qapp, view, right, control=False, delta=-120):
    position = QPointF(view.viewport().width() * (.75 if right else .25), 120)
    event = QWheelEvent(position, position, QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.ControlModifier if control else Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    qapp.sendEvent(view.viewport(), event)
    qapp.processEvents()


def test_real_preview_mixed_sizes_both_columns_and_navigation(tmp_path, qapp):
    source, target = tmp_path / 'long-preview.pdf', tmp_path / 'long-preview-out.pdf'
    make_preview_pages(source)
    compress(source, target)
    store, view = PdfStore(), CompressionPreview()
    observations = []
    try:
        view.resize(1020, 630)
        view.show()
        before, after = store.load(str(source)), store.load(str(target))
        view.set_documents(store, before, after)
        wait(qapp, lambda: view.image_key(0) in view.images)
        assert view.images[view.image_key(0)][0] == view.images[view.image_key(0)][1]
        view.viewport().grab().save(str(ROOT / 'preview-start.png'))
        for page, zoom, size in ((8, 175, (1150, 700)), (17, 'width', (900, 590)), (26, 400, (1100, 680))):
            view.scroll_to_page(page)
            view.set_zoom(zoom)
            view.resize(*size)
            qapp.processEvents()
            wait(qapp, lambda: view.image_key(page) in view.images)
            assert view.current_page == page
            left, right = view.images[view.image_key(page)]
            assert left == right
            assert left.width() * left.height() <= 4_010_000
            observations.append(dict(page=page + 1, zoom=zoom, image_size=[left.width(), left.height()],
                                     cache_pairs=len(view.images), cache_bytes=sum(image.sizeInBytes() for pair in view.images.values() for image in pair)))
            assert len(view.images) <= max(4, len(view.visible_pages()) + 1)
        for right_side in (False, True):
            view.scroll_to_page(12)
            old = view.verticalScrollBar().value()
            wheel(qapp, view, right_side)
            assert view.verticalScrollBar().value() > old
            wheel(qapp, view, right_side, control=True, delta=-120)
            assert view.current_page == 12
        wait(qapp, lambda: view.image_key(view.current_page) in view.images)
        assert len(view.images) <= max(4, len(view.visible_pages()) + 1)
        assert all(key[1] != 0 for key in view.images), 'Old first-page images should be evicted after later navigation'
        view.viewport().grab().save(str(ROOT / 'preview-zoomed.png'))
        view.clear('Generated candidate is unavailable')
        assert not view.images and not view.pages
        record('preview-real', observations=observations, shared_images_equal=True, unavailable_candidate_cleared=True)
    finally:
        view.shutdown()
        wait(qapp, lambda: not view.busy)
        view.close()
        view.deleteLater()
        store.close_all()


def test_async_stale_result_and_close_cancel(tmp_path, qapp, monkeypatch):
    import app.compressionpreview as preview_module
    source = tmp_path / 'async-preview.pdf'
    make_preview_pages(source)
    store, view = PdfStore(), CompressionPreview()
    started, release = Event(), Event()
    image = QImage(64, 64, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.red)
    calls = []
    def blocked_render(refs, passwords, progress, cancel, **kwargs):
        calls.append(kwargs['width'])
        started.set()
        release.wait(5)
        # The first response deliberately arrives after invalidation despite
        # cancellation, as if rendering completed just before clear().
        if len(calls) > 1 and cancel():
            raise OperationCancelled()
        return [image, image]
    monkeypatch.setattr(preview_module, 'render_preview', blocked_render)
    try:
        refs = store.load(str(source))
        view.resize(980, 610)
        view.show()
        view.set_documents(store, refs, refs)
        wait(qapp, started.is_set)
        old_generation = view.generation
        view.clear('New calculation pending')
        release.set()
        wait(qapp, lambda: not view.busy)
        assert view.generation > old_generation and not view.images
        release.clear()
        started.clear()
        view.set_documents(store, refs, refs)
        wait(qapp, started.is_set)
        stopped = []
        view.stopped.connect(lambda: stopped.append(True))
        view.shutdown()
        release.set()
        wait(qapp, lambda: not view.busy)
        assert stopped and view.closed and not view.images and not view.pump.isActive()
        record('preview-async', stale_generation_rejected=True, pending_shutdown_cancelled=True, render_calls=len(calls))
    finally:
        release.set()
        view.shutdown()
        wait(qapp, lambda: not view.busy)
        view.close()
        view.deleteLater()
        store.close_all()


def test_dialog_default_export_and_pending_close(tmp_path, qapp, monkeypatch):
    from app.compression import CompressionDialog
    import app.compression as module
    import app.compressionpreview as preview_module
    source, target = tmp_path / 'dialog-source.pdf', tmp_path / 'dialog-export.pdf'
    make_interactive(source, interactive=False)
    store = PdfStore()
    refs = store.load(str(source))
    class Owner(QWidget):
        def __init__(self):
            super().__init__()
            self.store, self.ws = store, SimpleNamespace(pages=refs, path=str(source), dirty=False)
        def _write_pdf(self, pages, destination, title, **kwargs):
            kwargs.pop('cancellable', None)
            return self.store.write(pages, destination, **kwargs)
        def _release_unused_docs(self):
            self.store.close_except({ref.source_id for ref in refs})
        def _show_write_notes(self, results):
            pass
    owner, dialog = Owner(), None
    started, release = Event(), Event()
    def pending_render(refs, passwords, progress, cancel, **kwargs):
        started.set()
        release.wait(5)
        if cancel():
            raise OperationCancelled()
        raise RuntimeError('Generated preview failure')
    monkeypatch.setattr(preview_module, 'render_preview', pending_render)
    monkeypatch.setattr(module, 'new_pdf_path', lambda *args: str(target))
    monkeypatch.setattr(module.QMessageBox, 'information', lambda *args: None)
    original = stamp(source)
    try:
        dialog = CompressionDialog(owner)
        dialog.show()
        assert dialog.profile.currentData() == 'quality'
        dialog.prepare()
        assert dialog.result and dialog.reduced and dialog.export.isEnabled()
        dialog.save_result()
        pixels = compare_pixels(source, target)
        assert all(row['changed_channels'] == 0 for row in pixels)
        assert semantics(source) == semantics(target)
        wait(qapp, started.is_set)
        folder = Path(dialog.folder.name)
        dialog.reject()
        assert dialog.isVisible() and folder.exists()
        release.set()
        wait(qapp, lambda: not dialog.isVisible())
        assert not folder.exists()
        assert stamp(source) == original
        record('dialog-flow', default='quality', export_bytes=target.stat().st_size, source_bytes=source.stat().st_size,
               pixels=pixels, pending_close_deferred=True, temporary_folder_removed=True)
    finally:
        release.set()
        if dialog:
            dialog.comparison.shutdown()
            wait(qapp, lambda: not dialog.comparison.busy)
            dialog.reject()
            dialog.deleteLater()
        owner.close()
        owner.deleteLater()
        store.close_all()


@pytest.mark.parametrize('case,pixels,phase,direction', [
    ('zero-scroll-begin', 0, Qt.ScrollPhase.ScrollBegin, 0),
    ('zero-scroll-end', 0, Qt.ScrollPhase.ScrollEnd, 0),
    ('pixel-only-up', 40, Qt.ScrollPhase.ScrollUpdate, 1),
    ('pixel-only-down', -40, Qt.ScrollPhase.ScrollUpdate, -1),
])
def test_supplemental_ctrl_trackpad_semantics(tmp_path, qapp, monkeypatch, case, pixels, phase, direction):
    """Parent-requested supplement, separate from the original nine independent cases."""
    source = tmp_path / 'trackpad-preview.pdf'
    make_preview_pages(source)
    store, view = PdfStore(), CompressionPreview()
    # Exercise real Qt input dispatch while avoiding unrelated render work.
    monkeypatch.setattr(view, 'schedule', lambda: None)
    try:
        refs = store.load(str(source))
        view.resize(980, 610)
        view.show()
        view.set_documents(store, refs, refs)
        view.set_zoom(100)
        qapp.processEvents()
        position = QPointF(view.viewport().width() * .75, 120)
        event = QWheelEvent(position, position, QPoint(0, pixels), QPoint(0, 0), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.ControlModifier, phase, False)
        before = view.mode
        qapp.sendEvent(view.viewport(), event)
        qapp.processEvents()
        after = view.mode
        record(f'supplemental-trackpad-{case}', origin='parent-requested supplemental boundary',
               phase=phase.name, angle_delta_y=0, pixel_delta_y=pixels,
               before_percent=before, after_percent=after, expected_direction=direction)
        if direction == 0:
            assert after == before, 'Scroll begin/end without movement must not change zoom'
        elif direction > 0:
            assert after > before, 'Ctrl + upward pixel-only touchpad movement must zoom in'
        else:
            assert after < before, 'Ctrl + downward pixel-only touchpad movement must zoom out'
    finally:
        view.shutdown()
        view.close()
        view.deleteLater()
        store.close_all()
