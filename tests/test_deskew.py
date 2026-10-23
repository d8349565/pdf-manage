"""Generated scan angles, unreliable inputs, OCR layers and cancellation."""
import hashlib
import io

from PIL import Image, ImageDraw, ImageFont
import pymupdf as p
import pytest
from PyQt6.QtWidgets import QApplication

from app.deskew import analyze_pages, estimate_angle, prepare_deskew
from app.output import OperationCancelled
from app.pdfio import PdfStore
from app.workspace import Workspace


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def scan_image(angle=0, table=False):
    image = Image.new('L', (720, 960), 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=22)
    for row in range(20):
        text = f'{row + 1:02} Generated scanned document for deskew check'
        draw.text((65, 90 + row * 36), text, font=font, fill=20)
    if table:
        for x in (55, 300, 650):
            draw.line((x, 65, x, 865), fill=30, width=2)
        for y in range(65, 866, 108):
            draw.line((55, y, 650, y), fill=30, width=2)
    return image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255)


@pytest.mark.parametrize('angle', [-7.2, -3, -1.4, 0, 1.8, 3, 6.5])
@pytest.mark.parametrize('table', [False, True])
def test_automatic_estimate_matches_known_scan_angle(angle, table):
    estimate = estimate_angle(scan_image(angle, table))
    assert estimate.angle == pytest.approx(angle, abs=.3), estimate
    assert estimate.status == 'ready', estimate


def test_blank_low_contrast_and_photo_like_pages_are_not_automatically_changed():
    assert estimate_angle(Image.new('L', (600, 800), 255)).status == 'blank'
    assert estimate_angle(Image.new('L', (600, 800), 200)).status == 'blank'
    image = Image.new('L', (600, 800), 255)
    ImageDraw.Draw(image).ellipse((100, 100, 500, 700), fill=10)
    assert estimate_angle(image).status != 'ready'


@pytest.mark.parametrize('rotation', [90, 180, 270])
def test_estimate_handles_sideways_scans_and_scanner_border(rotation):
    image = scan_image(-2.6, table=True)
    ImageDraw.Draw(image).rectangle((0, 0, image.width - 1, image.height - 1), outline=0, width=8)
    estimate = estimate_angle(image.rotate(rotation, expand=True))
    assert estimate.angle == pytest.approx(-2.6, abs=.3), estimate
    assert estimate.status == 'ready', estimate


def test_chinese_scan_and_detection_cancellation():
    from pathlib import Path
    font_path = Path('C:/Windows/Fonts/msyh.ttc')
    if not font_path.exists():
        pytest.skip('Chinese system font is unavailable')
    image = Image.new('L', (720, 960), 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path), 25)
    for row in range(19):
        draw.text((55, 75 + row * 40), f'第{row + 1}行 扫描文档文字与表格纠偏测试。', font=font, fill=15)
    estimate = estimate_angle(image.rotate(2.2, resample=Image.Resampling.BICUBIC, fillcolor=255))
    assert estimate.status == 'ready' and estimate.angle == pytest.approx(2.2, abs=.3)
    calls = []
    def cancel():
        calls.append(1)
        return len(calls) >= 4
    with pytest.raises(OperationCancelled):
        estimate_angle(image, cancel)


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_deskew_roundtrip_keeps_scan_pixels_text_and_paper(tmp_path, qapp, rotation):
    path, output = tmp_path / 'scan.pdf', tmp_path / 'corrected.pdf'
    image = scan_image(3)
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    with p.open() as doc:
        page = doc.new_page(width=360, height=480)
        page.insert_image(page.rect, stream=stream.getvalue())
        page.insert_text((40, 60), 'OCR SEARCHABLE TEXT', render_mode=3)
        page.set_rotation(rotation)
        doc.save(path)
    original_hash = hashlib.sha256(path.read_bytes()).digest()
    store = PdfStore()
    try:
        refs = store.load(str(path))
        sizes = store.page_size(refs[0])
        # The page orientation may be sideways, but correction is explicitly
        # small-angle only. Manual correction remains available in every layout.
        changed = prepare_deskew(store, refs, {0: 3})
        ws = Workspace()
        ws.open_replace(refs, str(path))
        ws.edit_pages(changed, '扫描纠偏')
        store.write(ws.pages, str(output))
        with p.open(path) as original, p.open(output) as doc:
            assert tuple(doc[0].rect)[2:] == pytest.approx(sizes)
            assert 'OCR SEARCHABLE TEXT' in doc[0].get_text()
            source_info, result_info = original[0].get_images()[0], doc[0].get_images()[0]
            assert source_info[2:4] == result_info[2:4] == (720, 960)
            assert original.xref_stream(source_info[0]) == doc.xref_stream(result_info[0])
            if rotation in (0, 180):
                pix = doc[0].get_pixmap(matrix=p.Matrix(2, 2), colorspace=p.csGRAY)
                corrected = Image.frombytes('L', (pix.width, pix.height), pix.samples)
                assert abs(estimate_angle(corrected).angle) <= .3
        ws.undo_stack.undo()
        assert ws.pages == refs
        ws.undo_stack.redo()
        assert ws.pages[0] == changed[0]
        assert hashlib.sha256(path.read_bytes()).digest() == original_hash
    finally:
        store.close_all()


def test_batch_analysis_and_cancel_are_atomic(tmp_path, qapp):
    path = tmp_path / 'batch.pdf'
    with p.open() as doc:
        for angle in (3, -2, 0):
            stream = io.BytesIO()
            scan_image(angle).save(stream, format='PNG')
            doc.new_page(width=360, height=480).insert_image(p.Rect(0, 0, 360, 480), stream=stream.getvalue())
        doc.new_page(width=360, height=480)
        doc[1].add_text_annot((40, 40), 'Do not silently flatten this note')
        doc.save(path)
    store = PdfStore()
    try:
        pages = store.load(str(path))
        estimates = analyze_pages(store, pages, [0, 1, 2, 3])
        assert estimates[0].angle == pytest.approx(3, abs=.3)
        assert estimates[1].status == 'unsupported'
        assert estimates[2].angle == 0
        assert estimates[3].status == 'blank'
        ws = Workspace()
        ws.open_replace(pages, str(path))
        with pytest.raises(ValueError, match='批注'):
            prepare_deskew(store, pages, {0: 3, 1: -2})
        calls = []
        with pytest.raises(OperationCancelled):
            prepare_deskew(store, pages, {0: 3, 2: -1}, progress=lambda *args: calls.append(args), cancel=lambda: bool(calls))
        assert ws.pages == pages and not ws.dirty
    finally:
        store.close_all()


def test_analysis_batch_keeps_gui_events_moving(tmp_path, qapp):
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QWidget
    from app.progress import run_operation
    path = tmp_path / 'batch-scans.pdf'
    with p.open() as doc:
        for angle in (2, -2, 3, -3, 1.5, -1.5):
            stream = io.BytesIO()
            scan_image(angle).save(stream, format='PNG')
            doc.new_page(width=360, height=480).insert_image(p.Rect(0, 0, 360, 480), stream=stream.getvalue())
        doc.save(path)
    store, parent = PdfStore(), QWidget()
    timer, pulses = QTimer(parent), []
    timer.setInterval(10)
    timer.timeout.connect(lambda: pulses.append(1))
    try:
        pages = store.load(str(path))
        timer.start()
        estimates = run_operation(parent, '分析生成扫描页', lambda progress, cancel:
                                  analyze_pages(store, pages, range(6), progress, cancel), cancellable=True)
        assert len(estimates) == 6 and all(value.status == 'ready' for value in estimates.values())
        assert len(pulses) >= 3
    finally:
        timer.stop()
        parent.deleteLater()
        store.close_all()
