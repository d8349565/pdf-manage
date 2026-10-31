"""Physical print dimensions are checked using generated pages and PDF output."""
from dataclasses import replace

import pymupdf
import pytest
from PyQt6.QtCore import QRectF, QThreadPool
from PyQt6.QtGui import QColor, QImage, QPainter, QPageSize
from PyQt6.QtPrintSupport import QPrinter
from PyQt6.QtWidgets import QApplication

from app.pdfio import PdfStore
from app.pdfworker import print_pdf
from app.printing import PrintDialog, PrintOptions, SheetPreview, render_pages_to_printer
from app.workspace import Workspace


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def _source(path, width_mm, height_mm):
    with pymupdf.open() as doc:
        page = doc.new_page(width=width_mm * 72 / 25.4, height=height_mm * 72 / 25.4)
        page.draw_rect(page.rect, color=None, fill=(.2, .5, .7))
        doc.save(path)


def _printed_size(path):
    with pymupdf.open(path) as doc:
        images = doc[0].get_image_info()
        assert len(images) == 1
        rect = pymupdf.Rect(images[0]['bbox'])
        return rect.width * 25.4 / 72, rect.height * 25.4 / 72


def _pdf_printer(path):
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(path))
    printer.setResolution(144)
    return printer


@pytest.mark.parametrize('orientation', ['auto', 'portrait'])
def test_default_print_keeps_small_invoice_physical_dimensions(qapp, tmp_path, orientation):
    source, output = tmp_path / 'generated-150x100mm.pdf', tmp_path / 'printed-a4.pdf'
    _source(source, 150, 100)
    original = source.read_bytes()
    store = PdfStore()
    try:
        pages = store.load(str(source))
        options = PrintOptions(page_indices=[0], paper=QPageSize.PageSizeId.A4, orientation=orientation)
        print_pdf(store, pages, options, output_path=str(output))
        assert _printed_size(output) == pytest.approx((150, 100), abs=.15)
        assert source.read_bytes() == original
    finally:
        store.close_all()


@pytest.mark.parametrize('scale_mode', ['shrink', 'fit', 'actual'])
@pytest.mark.parametrize('size', [(150, 100), (300, 400)])
def test_scale_modes_use_pdf_millimeters_instead_of_raster_pixel_size(qapp, tmp_path, scale_mode, size):
    source, output = tmp_path / 'generated.pdf', tmp_path / 'printed.pdf'
    _source(source, *size)
    store = PdfStore()
    try:
        pages = store.load(str(source))
        options = PrintOptions(page_indices=[0], paper=QPageSize.PageSizeId.A4,
                               orientation='portrait', margin_mm=5, scale_mode=scale_mode)
        render_pages_to_printer(_pdf_printer(output), store, pages, options)
        factor = min(200 / size[0], 287 / size[1])
        if scale_mode == 'actual':
            factor = 1
        elif scale_mode == 'shrink':
            factor = min(factor, 1)
        assert _printed_size(output) == pytest.approx(tuple(value * factor for value in size), abs=.25)
    finally:
        store.close_all()


def test_worker_keeps_explicit_fit_mode_and_rotated_crop_size(qapp, tmp_path):
    source, output = tmp_path / 'generated.pdf', tmp_path / 'printed.pdf'
    _source(source, 150, 100)
    with pymupdf.open(source) as doc:
        doc[0].set_cropbox(pymupdf.Rect(20, 10, 130, 90) * (72 / 25.4))
        cropped = tmp_path / 'cropped.pdf'
        doc.save(cropped)
    store = PdfStore()
    try:
        pages = [store.load(str(cropped))[0].rotated(90)]
        assert store.page_size(pages[0]) == pytest.approx((80 * 72 / 25.4, 110 * 72 / 25.4), abs=.01)
        options = PrintOptions(page_indices=[0], paper=QPageSize.PageSizeId.A4)
        print_pdf(store, pages, options, output_path=str(output))
        assert _printed_size(output) == pytest.approx((80, 110), abs=.15)
        enlarged = tmp_path / 'fit.pdf'
        print_pdf(store, pages, replace(options, scale_mode='fit'), output_path=str(enlarged))
        width, height = _printed_size(enlarged)
        assert width > 200 and height > 280
        assert width / height == pytest.approx(80 / 110, rel=.002)
    finally:
        store.close_all()


@pytest.mark.parametrize('scale_mode,expected_mm', [
    ('shrink', (150, 100)), ('actual', (150, 100)), ('fit', (210, 140)),
])
def test_preview_draws_the_same_physical_size_as_print(qapp, tmp_path, monkeypatch, scale_mode, expected_mm):
    source = tmp_path / 'generated.pdf'
    _source(source, 150, 100)
    store = PdfStore()
    canvas = SheetPreview(store)
    surface = QImage(480, 680, QImage.Format.Format_RGB32)
    surface.fill(QColor('white'))
    image = QImage(300, 200, QImage.Format.Format_RGB32)
    image.fill(QColor('blue'))
    drawn = []

    class ObservedPainter(QPainter):
        def drawImage(self, target, image):
            drawn.append(QRectF(target))
            super().drawImage(target, image)

    try:
        canvas.pages = store.load(str(source))
        canvas.scale = 2
        canvas.options = PrintOptions(scale_mode=scale_mode)
        monkeypatch.setattr(canvas, '_image', lambda *args: image)
        painter = ObservedPainter(surface)
        content = QRectF(20, 20, 420, 594)
        canvas._paint_content(painter, content, 0)
        painter.end()
        assert len(drawn) == 1
        target = drawn[0]
        assert (target.width() / canvas.scale, target.height() / canvas.scale) == pytest.approx(expected_mm)
        assert target.center() == content.center()
    finally:
        canvas.deleteLater()
        store.close_all()


def test_actual_size_clips_each_nup_cell_without_covering_its_neighbors(qapp, tmp_path):
    source, output = tmp_path / 'generated-two.pdf', tmp_path / 'printed.pdf'
    with pymupdf.open() as doc:
        for color in ((1, 0, 0), (0, 0, 1)):
            page = doc.new_page(width=150 * 72 / 25.4, height=200 * 72 / 25.4)
            page.draw_rect(page.rect, color=None, fill=color)
        doc.save(source)
    store = PdfStore()
    try:
        pages = store.load(str(source))
        options = PrintOptions(page_indices=[0, 1], paper=QPageSize.PageSizeId.A4, margin_mm=5,
                               pages_per_face=2, two_page_layout='vertical', scale_mode='actual')
        render_pages_to_printer(_pdf_printer(output), store, pages, options)
        with pymupdf.open(output) as doc:
            pixels = doc[0].get_pixmap(alpha=False)
            x = pixels.width // 2
            assert pixels.pixel(x, pixels.height // 4) == pytest.approx((255, 0, 0), abs=2)
            assert pixels.pixel(x, pixels.height * 3 // 4) == pytest.approx((0, 0, 255), abs=2)
            assert pixels.pixel(x, pixels.height // 2) == (255, 255, 255)
            assert pixels.pixel(2, pixels.height // 4) == (255, 255, 255)
    finally:
        store.close_all()


def test_scale_control_restores_selection_after_booklet_and_warns_about_clipping(qapp, tmp_path, monkeypatch):
    source = tmp_path / 'generated.pdf'
    _source(source, 150, 100)
    store, workspace = PdfStore(), Workspace()
    monkeypatch.setattr('app.printing.QPrinterInfo.availablePrinterNames', lambda: [])
    dialog = None
    try:
        workspace.open_replace(store.load(str(source)), str(source))
        dialog = PrintDialog(None, workspace, store)
        dialog.show()
        qapp.processEvents()
        assert dialog.combo_scale.currentData() == 'shrink'
        assert not dialog.label_scale_hint.isVisible()
        dialog.combo_scale.setCurrentIndex(dialog.combo_scale.findData('actual'))
        assert dialog._collect_options().scale_mode == dialog.canvas.options.scale_mode == 'actual'
        assert dialog.label_scale_hint.isVisible() and '不会打印' in dialog.label_scale_hint.text()
        assert '100%' in dialog.label_summary.text()
        dialog.combo_layout.setCurrentIndex(dialog.combo_layout.findData('booklet'))
        assert dialog._collect_options().scale_mode == 'fit'
        assert not dialog.scale_field.isVisible() and not dialog.label_scale_hint.isVisible()
        dialog.combo_layout.setCurrentIndex(dialog.combo_layout.findData('normal'))
        assert dialog.combo_scale.currentData() == dialog._collect_options().scale_mode == 'actual'
        assert dialog.scale_field.isVisible()
    finally:
        if dialog:
            dialog.close()
            dialog.deleteLater()
        QThreadPool.globalInstance().waitForDone(5000)
        workspace.reset()
        store.close_all()
