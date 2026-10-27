"""Printing settings regressions use independent samples and PDF output only."""
from pathlib import Path

import pymupdf
import pytest
from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, QThreadPool
from PyQt6.QtGui import QFontDatabase, QPageSize, QWheelEvent
from PyQt6.QtPrintSupport import QPrinter
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QComboBox, QStyle, QStyleOptionSpinBox

from app.pdfio import PdfStore
from app.printing import PrintDialog, PrintOptions, build_sheets, face_cells, face_pages, render_pages_to_printer
from app.workspace import Workspace


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    font = Path("C:/Windows/Fonts/msyh.ttc")
    if font.is_file():
        QFontDatabase.addApplicationFont(str(font))
    return app


@pytest.fixture
def printing_case(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("app.printing.QPrinterInfo.availablePrinterNames", lambda: [])
    source = tmp_path / "synthetic-invoices.pdf"
    colors = [(0.83, 0.91, 0.98), (0.87, 0.94, 0.86), (0.98, 0.91, 0.82)]
    with pymupdf.open() as document:
        for index, color in enumerate(colors):
            page = document.new_page(width=600, height=240)
            page.draw_rect(page.rect, color=color, fill=color)
            page.insert_text((24, 42), f"SYNTHETIC INVOICE {index + 1}", fontsize=22)
            page.insert_text((24, 72), "Independent layout sample - no valid tax information", fontsize=12)
            page.insert_text((24, 204), "Source file must remain unchanged", fontsize=12)
        document.save(source)
    store, workspace = PdfStore(), Workspace()
    workspace.open_replace(store.load(str(source)), str(source))
    yield workspace, store, source, colors
    QThreadPool.globalInstance().waitForDone(5000)
    workspace.reset()
    store.close_all()


def send_wheel(widget, delta):
    point = widget.rect().center()
    event = QWheelEvent(QPointF(point), QPointF(widget.mapToGlobal(point)), QPoint(), QPoint(0, delta),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


@pytest.mark.parametrize("orientation", ["auto", "portrait", "landscape"])
@pytest.mark.parametrize("order", ["row", "column"])
@pytest.mark.parametrize("reverse", [False, True])
def test_two_page_vertical_grid_preserves_order_and_partial_sheet(orientation, order, reverse):
    options = PrintOptions(page_indices=[0, 1, 2], pages_per_face=2, two_page_layout="vertical",
                           orientation=orientation, cell_order=order, reverse=reverse)
    sheets = build_sheets([None] * 3, options, None)
    assert [face_pages(sheet) for sheet in sheets] == ([(2, 1), (0,)] if reverse else [(0, 1), (2,)])
    assert all(sheet.back is None and not sheet.back_pages for sheet in sheets)
    assert all(sheet.front_portrait == (orientation != "landscape") for sheet in sheets)
    area = QRectF(10, 20, 200, 280)
    first = list(face_cells(area, face_pages(sheets[0]), options))
    last = list(face_cells(area, face_pages(sheets[1]), options))
    assert first[0][1].center().x() == first[1][1].center().x()
    assert first[0][1].bottom() < first[1][1].top()
    assert first[0][1] == last[0][1]
    assert all(area.contains(cell) for _, cell in first + last)


def test_vertical_invoice_pdf_output_matches_preview_slots(printing_case, tmp_path):
    workspace, store, source, colors = printing_case
    original = source.read_bytes()
    options = PrintOptions(page_indices=[0, 1, 2], pages_per_face=2, two_page_layout="vertical",
                           paper=QPageSize.PageSizeId.A4, margin_mm=5)
    output = tmp_path / "two-per-a4.pdf"
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(output))
    render_pages_to_printer(printer, store, workspace.pages, options)
    with pymupdf.open(output) as printed:
        assert len(printed) == 2
        for index, page in enumerate(printed):
            assert page.rect.width == pytest.approx(210 / 25.4 * 72, abs=1)
            assert page.rect.height == pytest.approx(297 / 25.4 * 72, abs=1)
            image = page.get_pixmap(colorspace=pymupdf.csRGB, alpha=False)
            expected = colors[:2] if index == 0 else [colors[2], (1, 1, 1)]
            for slot, color in enumerate(expected):
                pixel = image.pixel(image.width // 2, round(image.height * (.25 + .5 * slot)))
                assert max(abs(actual - round(wanted * 255)) for actual, wanted in zip(pixel, color)) < 5
            rectangles = [rect for info in page.get_images() for rect in page.get_image_rects(info[0])]
            assert len(rectangles) == (2 if index == 0 else 1)
            for rectangle in rectangles:
                assert rectangle.width / rectangle.height == pytest.approx(2.5, rel=.02)
                assert page.rect.contains(rectangle)
    assert source.read_bytes() == original


@pytest.mark.parametrize("booklet", [False, True])
@pytest.mark.parametrize("focused", [False, True])
@pytest.mark.parametrize("delta", [-120, 120])
def test_sidebar_wheel_scrolls_without_changing_focused_or_unfocused_controls(printing_case, qapp, booklet, focused, delta):
    workspace, store, _, _ = printing_case
    dialog = PrintDialog(None, workspace, store)
    dialog.resize(880, 600)
    dialog.advanced_toggle.setChecked(True)
    dialog.radio_double.setChecked(True)
    dialog.combo_nup.setCurrentIndex(dialog.combo_nup.findData(4))
    dialog.combo_layout.setCurrentIndex(dialog.combo_layout.findData("booklet" if booklet else "normal"))
    dialog.show()
    qapp.processEvents()
    moved = False
    try:
        initial = dialog._collect_options()
        bar = dialog.sidebar_scroll.verticalScrollBar()
        assert bar.maximum() > 0
        for control in dialog._wheel_controls:
            if not control.isVisible():
                continue
            dialog.sidebar_scroll.ensureWidgetVisible(control)
            control.setFocus() if focused else dialog.canvas.setFocus()
            qapp.processEvents()
            signal = control.currentIndexChanged if isinstance(control, QComboBox) else control.valueChanged
            spy = QSignalSpy(signal)
            old_scroll = bar.value()
            send_wheel(control, delta)
            moved |= bar.value() != old_scroll
            assert not spy
            assert dialog._collect_options() == initial
        assert moved
    finally:
        dialog.close()


@pytest.mark.parametrize("size", [(1120, 760), (880, 600)])
def test_common_controls_and_vertical_layout_remain_discoverable(printing_case, qapp, size):
    workspace, store, _, _ = printing_case
    dialog = PrintDialog(None, workspace, store)
    dialog.resize(*size)
    dialog.show()
    qapp.processEvents()
    try:
        viewport = dialog.sidebar_scroll.viewport()
        assert not dialog.advanced.isVisible()
        for control in (dialog.combo_printer, dialog.edit_range, dialog.spin_copies,
                        dialog.combo_paper, dialog.combo_orient, dialog.combo_nup):
            top = control.mapTo(viewport, QPoint(0, 0))
            bottom = top.y() + control.height()
            assert 0 <= top.y() and bottom <= viewport.height(), control.accessibleName()
        index = dialog.combo_nup.findText("2 页（上下）")
        dialog.combo_nup.setCurrentIndex(index)
        options = dialog._collect_options()
        assert options.pages_per_face == 2 and options.two_page_layout == "vertical"
        assert all(sheet.front_portrait for sheet in dialog.canvas.sheets)
        assert "上下两页" in dialog.label_summary.text() and "纵向 A4" in dialog.label_summary.text()
        assert dialog.btn_print.isVisible()
        dialog.combo_layout.setCurrentIndex(dialog.combo_layout.findData("booklet"))
        assert dialog._collect_options().two_page_layout == "horizontal"
        dialog.combo_layout.setCurrentIndex(dialog.combo_layout.findData("normal"))
        assert dialog._collect_options().two_page_layout == "vertical"
    finally:
        dialog.close()


def test_open_popup_keyboard_and_copy_steps_keep_working(printing_case, qapp):
    workspace, store, _, _ = printing_case
    dialog = PrintDialog(None, workspace, store)
    dialog.show()
    qapp.processEvents()
    try:
        combo = dialog.combo_paper
        combo.setMaxVisibleItems(2)
        combo.view().setFixedHeight(54)
        combo.showPopup()
        qapp.processEvents()
        assert combo.view().isVisible()
        scrollbar = combo.view().verticalScrollBar()
        assert scrollbar.maximum() > 0
        send_wheel(combo.view().viewport(), -120)
        assert scrollbar.value() > 0
        combo.hidePopup()
        before = combo.currentIndex()
        QTest.keyClick(combo, Qt.Key.Key_Down)
        assert combo.currentIndex() == before + 1
        dialog.spin_copies.setValue(2)
        dialog.spin_copies.stepUp()
        assert dialog.spin_copies.value() == 3
        option = QStyleOptionSpinBox()
        dialog.spin_copies.initStyleOption(option)
        up = dialog.spin_copies.style().subControlRect(
            QStyle.ComplexControl.CC_SpinBox, option,
            QStyle.SubControl.SC_SpinBoxUp, dialog.spin_copies)
        QTest.mouseClick(dialog.spin_copies, Qt.MouseButton.LeftButton, pos=up.center())
        assert dialog.spin_copies.value() == 4
    finally:
        dialog.close()
