from __future__ import annotations

import tempfile
import pymupdf
import pytest
from PyQt6.QtGui import QPageSize
from PyQt6.QtPrintSupport import QPrinter
from PyQt6.QtWidgets import QApplication

from app.pdfio import PdfStore
from app.printing import (
    DUPLEX_MODES,
    PRINT_DPI,
    PrintDialog,
    PrintOptions,
    apply_sheet_layout,
    back_binding_edge,
    back_is_upside_down,
    binding_flip_axis,
    build_sheets,
    front_binding_edge,
    render_pages_to_printer,
)
from app.ui import MainWindow
from app.workspace import Workspace


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _make_pdf(path, pages_wh: list[tuple[int, int]], label: str = "P") -> None:
    doc = pymupdf.open()
    for i, (w, h) in enumerate(pages_wh):
        p = doc.new_page(width=w, height=h)
        p.insert_text((40, 60), f"{label}-{i + 1}", fontsize=20)
    doc.save(path)
    doc.close()


def test_duplex_modes_mapping():
    assert DUPLEX_MODES["none"] == QPrinter.DuplexMode.DuplexNone
    assert DUPLEX_MODES["long_edge"] == QPrinter.DuplexMode.DuplexLongSide
    assert DUPLEX_MODES["short_edge"] == QPrinter.DuplexMode.DuplexShortSide


def test_render_pages_to_printer_auto_orientation(qapp, tmp_path):
    """自动横纵向：竖页出竖版纸张，横页出横版纸张。"""
    src = tmp_path / "mixed.pdf"
    # 第 1 页竖向 (300x400)，第 2 页横向 (400x300)
    _make_pdf(src, [(300, 400), (400, 300)])
    store = PdfStore()
    pages = store.load(str(src))

    printer = QPrinter()
    out_pdf = tmp_path / "printed_auto.pdf"
    printer.setOutputFileName(str(out_pdf))
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)

    options = PrintOptions(
        orientation="auto",
        duplex="none",
        copies=1,
        page_indices=[0, 1],
    )
    render_pages_to_printer(printer, store, pages, options)

    with pymupdf.open(out_pdf) as written:
        assert len(written) == 2
        # 第 1 页应为纵向（高 > 宽）
        p0 = written[0].rect
        assert p0.height > p0.width
        # 第 2 页应为横向（宽 > 高）
        p1 = written[1].rect
        assert p1.width > p1.height

    store.close_all()


def test_render_pages_to_printer_forced_orientation(qapp, tmp_path):
    """强制全部横向或全部纵向。"""
    src = tmp_path / "mixed.pdf"
    _make_pdf(src, [(300, 400), (400, 300)])
    store = PdfStore()
    pages = store.load(str(src))

    # 强制全部纵向
    printer = QPrinter()
    out_portrait = tmp_path / "forced_portrait.pdf"
    printer.setOutputFileName(str(out_portrait))
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    render_pages_to_printer(
        printer,
        store,
        pages,
        PrintOptions(orientation="portrait", page_indices=[0, 1]),
    )
    with pymupdf.open(out_portrait) as doc_p:
        assert doc_p[0].rect.height > doc_p[0].rect.width
        assert doc_p[1].rect.height > doc_p[1].rect.width

    # 强制全部横向
    printer_l = QPrinter()
    out_landscape = tmp_path / "forced_landscape.pdf"
    printer_l.setOutputFileName(str(out_landscape))
    printer_l.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    render_pages_to_printer(
        printer_l,
        store,
        pages,
        PrintOptions(orientation="landscape", page_indices=[0, 1]),
    )
    with pymupdf.open(out_landscape) as doc_l:
        assert doc_l[0].rect.width > doc_l[0].rect.height
        assert doc_l[1].rect.width > doc_l[1].rect.height

    store.close_all()


def test_render_pages_to_printer_page_subset(qapp, tmp_path):
    src = tmp_path / "five.pdf"
    _make_pdf(src, [(300, 400)] * 5)
    store = PdfStore()
    pages = store.load(str(src))

    printer = QPrinter()
    out_pdf = tmp_path / "subset.pdf"
    printer.setOutputFileName(str(out_pdf))
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)

    # 抽取第 1、3、5 页（索引 0, 2, 4）
    options = PrintOptions(
        orientation="portrait",
        duplex="none",
        copies=1,
        page_indices=[0, 2, 4],
    )
    render_pages_to_printer(printer, store, pages, options)

    with pymupdf.open(out_pdf) as written:
        assert len(written) == 3

    store.close_all()


def test_print_dialog_options_and_duplex_toggle(qapp, tmp_path):
    src = tmp_path / "doc.pdf"
    _make_pdf(src, [(300, 400)] * 3)
    store = PdfStore()
    ws = Workspace()
    ws.open_replace(store.load(str(src)), str(src))

    dlg = PrintDialog(None, ws, store)
    # 单面模式时，双面翻转方式置灰
    assert dlg.radio_single.isChecked()
    assert not dlg.combo_duplex_mode.isEnabled()
    opts = dlg._collect_options()
    assert opts.duplex == "none"

    # 切换为双面模式
    dlg.radio_double.setChecked(True)
    assert dlg.combo_duplex_mode.isEnabled()
    dlg.combo_duplex_mode.setCurrentIndex(1)  # 短边
    opts = dlg._collect_options()
    assert opts.duplex == "short_edge"

    dlg.combo_duplex_mode.setCurrentIndex(0)  # 长边
    opts = dlg._collect_options()
    assert opts.duplex == "long_edge"

    # 方向切换
    dlg.combo_orient.setCurrentIndex(2)  # landscape
    opts = dlg._collect_options()
    assert opts.orientation == "landscape"

    # 份数
    dlg.spin_copies.setValue(3)
    opts = dlg._collect_options()
    assert opts.copies == 3

    dlg.close()
    store.close_all()


def test_print_dialog_range_filtering(qapp, tmp_path):
    src = tmp_path / "doc.pdf"
    _make_pdf(src, [(300, 400)] * 4)
    store = PdfStore()
    ws = Workspace()
    ws.open_replace(store.load(str(src)), str(src))
    ws.selected = {1, 2}  # 选了第 2、3 页

    dlg = PrintDialog(None, ws, store)

    # 1. 全部
    assert dlg.radio_all.isChecked()
    assert dlg._collect_indices() == [0, 1, 2, 3]

    # 2. 已选页面
    dlg.radio_sel.setChecked(True)
    assert dlg._collect_indices() == [1, 2]

    # 3. 自定义有效范围
    dlg.radio_custom.setChecked(True)
    dlg.edit_range.setText("1, 4")
    assert dlg._collect_indices() == [0, 3]
    assert dlg.label_range_error.isHidden()

    # 4. 自定义无效范围
    dlg.edit_range.setText("abc")
    assert dlg._collect_indices() == []
    assert not dlg.label_range_error.isHidden()

    dlg.close()
    store.close_all()


def test_build_sheets_pairs_pages_per_physical_sheet(qapp, tmp_path):
    src = tmp_path / "five.pdf"
    _make_pdf(src, [(300, 400)] * 5)
    store = PdfStore()
    pages = store.load(str(src))

    single = build_sheets(
        pages, PrintOptions(duplex="none", page_indices=list(range(5))), store
    )
    assert [(s.front, s.back) for s in single] == [
        (0, None), (1, None), (2, None), (3, None), (4, None)
    ]

    double = build_sheets(
        pages, PrintOptions(duplex="long_edge", page_indices=list(range(5))), store
    )
    # 5 页要 3 张纸，最后一张背面空白
    assert [(s.front, s.back) for s in double] == [(0, 1), (2, 3), (4, None)]

    store.close_all()


def test_build_sheets_duplex_keeps_each_face_orientation(qapp, tmp_path):
    """双面时正反各自保留朝向：正面竖、背面横可以印在同一张纸上。"""
    src = tmp_path / "mixed.pdf"
    _make_pdf(src, [(300, 400), (400, 300)])
    store = PdfStore()
    pages = store.load(str(src))

    sheets = build_sheets(
        pages, PrintOptions(duplex="long_edge", orientation="auto", page_indices=[0, 1]), store
    )
    assert len(sheets) == 1
    assert sheets[0].front_portrait is True
    assert sheets[0].back_portrait is False
    assert sheets[0].mixed is True

    sheets = build_sheets(
        pages, PrintOptions(duplex="long_edge", orientation="auto", page_indices=[1, 0]), store
    )
    assert sheets[0].front_portrait is False
    assert sheets[0].back_portrait is True

    store.close_all()


def test_binding_flip_axis_matches_paper_geometry():
    # 纵向纸的长边在左右两侧，沿长边装订就是像翻书一样左右翻
    assert binding_flip_axis("long_edge", portrait=True) == "lr"
    assert binding_flip_axis("short_edge", portrait=True) == "ud"
    # 横向纸长短边互换，翻法跟着换
    assert binding_flip_axis("long_edge", portrait=False) == "ud"
    assert binding_flip_axis("short_edge", portrait=False) == "lr"
    assert binding_flip_axis("none", portrait=True) == ""


def test_back_face_upside_down_only_when_flip_mismatches_binding():
    # 长边装订左右翻 = 背面正立；同一张纸上下翻就是倒的
    assert not back_is_upside_down("long_edge", True, "lr")
    assert back_is_upside_down("long_edge", True, "ud")
    # 短边装订正好相反
    assert back_is_upside_down("short_edge", True, "lr")
    assert not back_is_upside_down("short_edge", True, "ud")
    assert not back_is_upside_down("none", True, "lr")


def test_binding_edge_moves_when_sheet_is_flipped():
    assert front_binding_edge("long_edge", True) == "left"
    # 左右翻过来，原来在左边的装订边看上去到了右边
    assert back_binding_edge("long_edge", True, "lr") == "right"
    # 上下翻不动左右，装订边还在左边
    assert back_binding_edge("long_edge", True, "ud") == "left"
    assert front_binding_edge("short_edge", True) == "top"
    assert back_binding_edge("short_edge", True, "ud") == "bottom"
    assert back_binding_edge("short_edge", True, "lr") == "top"


def test_apply_sheet_layout_reports_real_paper_and_margins(qapp, tmp_path):
    printer = QPrinter()
    printer.setOutputFileName(str(tmp_path / "layout.pdf"))
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)

    options = PrintOptions(paper=QPageSize.PageSizeId.A4, margin_mm=10.0)
    portrait = apply_sheet_layout(printer, options, True)
    assert round(portrait.width) == 210 and round(portrait.height) == 297
    assert round(portrait.left) == 10 and round(portrait.bottom) == 10
    assert round(portrait.content_width) == 190

    landscape = apply_sheet_layout(printer, options, False)
    assert round(landscape.width) == 297 and round(landscape.height) == 210
    assert landscape.portrait is False

    a5 = apply_sheet_layout(
        printer, PrintOptions(paper=QPageSize.PageSizeId.A5, margin_mm=0.0), True
    )
    assert round(a5.width) == 148 and round(a5.height) == 210
    assert a5.left == 0.0


def test_duplex_output_keeps_each_face_orientation(qapp, tmp_path):
    """双面 + 自动方向：正反两面按各自朝向出纸，不把背面强行扭成正面的方向。"""
    src = tmp_path / "mixed.pdf"
    _make_pdf(src, [(300, 400), (400, 300), (300, 400)])
    store = PdfStore()
    pages = store.load(str(src))

    printer = QPrinter()
    out_pdf = tmp_path / "duplex.pdf"
    printer.setOutputFileName(str(out_pdf))
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    render_pages_to_printer(
        printer,
        store,
        pages,
        PrintOptions(duplex="long_edge", orientation="auto", page_indices=[0, 1, 2]),
    )

    with pymupdf.open(out_pdf) as written:
        assert len(written) == 3
        assert written[0].rect.height > written[0].rect.width
        assert written[1].rect.width > written[1].rect.height
        assert written[2].rect.height > written[2].rect.width

    store.close_all()


def test_printed_page_is_rasterized_at_print_dpi(qapp, tmp_path):
    """回归：默认 QPrinter 按屏幕分辨率作画，纸面会发虚。这里锁住 300 dpi 的位图密度。"""
    src = tmp_path / "one.pdf"
    _make_pdf(src, [(595, 842)])
    store = PdfStore()
    pages = store.load(str(src))

    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    out_pdf = tmp_path / "dpi.pdf"
    printer.setOutputFileName(str(out_pdf))
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    render_pages_to_printer(
        printer,
        store,
        pages,
        PrintOptions(
            paper=QPageSize.PageSizeId.A4,
            margin_mm=0.0,
            orientation="portrait",
            page_indices=[0],
        ),
    )

    with pymupdf.open(out_pdf) as written:
        widths = [info["width"] for info in written[0].get_image_info()]
    # A4 宽 210 毫米，300 dpi 下约 2480 像素；留足余量只防「掉回屏幕分辨率」
    assert widths and max(widths) >= 210 / 25.4 * PRINT_DPI * 0.9

    store.close_all()


def test_print_dialog_preview_shows_physical_sheets(qapp, tmp_path):
    src = tmp_path / "doc.pdf"
    _make_pdf(src, [(300, 400)] * 5)
    store = PdfStore()
    ws = Workspace()
    ws.open_replace(store.load(str(src)), str(src))

    dlg = PrintDialog(None, ws, store)
    # 回归：默认的 QPrinter 只有屏幕分辨率（本机 144 dpi），打出来是虚的
    assert dlg.printer.resolution() > 200

    # 单面：5 页 5 张纸
    assert len(dlg.canvas.sheets) == 5
    assert "5 张纸" in dlg.label_summary.text()

    # 双面：5 页 3 张纸，省 2 张。竖版 + 长边装订只能左右翻，没有第二种翻面可选。
    dlg.radio_double.setChecked(True)
    assert len(dlg.canvas.sheets) == 3
    assert dlg.canvas.sheets[-1].back is None
    assert "3 张纸" in dlg.label_summary.text()
    assert "省 2 张" in dlg.label_summary.text()
    assert "左右翻开" in dlg.label_hint.text()
    assert not hasattr(dlg, "btn_flip_lr")
    assert not hasattr(dlg, "btn_flip_ud")

    dlg.close()
    store.close_all()


def test_print_dialog_flip_follows_landscape_binding(qapp, tmp_path):
    """横版：短边装订只能左右翻，长边装订只能上下翻。"""
    src = tmp_path / "land.pdf"
    _make_pdf(src, [(400, 300)] * 2)
    store = PdfStore()
    ws = Workspace()
    ws.open_replace(store.load(str(src)), str(src))

    dlg = PrintDialog(None, ws, store)
    dlg.radio_double.setChecked(True)

    dlg.combo_duplex_mode.setCurrentIndex(1)  # 短边
    assert "左右翻开" in dlg.label_hint.text()
    assert "上下翻开" not in dlg.label_hint.text()

    dlg.combo_duplex_mode.setCurrentIndex(0)  # 长边
    assert "上下翻开" in dlg.label_hint.text()
    assert "左右翻开" not in dlg.label_hint.text()

    dlg.close()
    store.close_all()


def test_print_dialog_duplex_mixed_orientation_keeps_both_shapes(qapp, tmp_path):
    src = tmp_path / "mixed.pdf"
    _make_pdf(src, [(300, 400), (400, 300)])
    store = PdfStore()
    ws = Workspace()
    ws.open_replace(store.load(str(src)), str(src))

    dlg = PrintDialog(None, ws, store)
    dlg.radio_double.setChecked(True)
    assert len(dlg.canvas.sheets) == 1
    sheet = dlg.canvas.sheets[0]
    assert sheet.mixed is True
    row = dlg.canvas._rows[0]
    assert row.front_h > row.front_w
    assert row.back_w > row.back_h
    assert "转 90°" in dlg.label_hint.text()

    dlg.close()
    store.close_all()


def test_print_dialog_paper_and_margin_reach_options(qapp, tmp_path):
    src = tmp_path / "doc.pdf"
    _make_pdf(src, [(300, 400)] * 2)
    store = PdfStore()
    ws = Workspace()
    ws.open_replace(store.load(str(src)), str(src))

    dlg = PrintDialog(None, ws, store)
    assert dlg.combo_paper.count() > 0
    assert dlg._collect_options().paper == dlg.combo_paper.currentData()
    assert dlg._collect_options().margin_mm == 0.0

    dlg.combo_margin.setCurrentIndex(2)
    assert dlg._collect_options().margin_mm == 10.0
    assert "10.0 毫米" in dlg.label_summary.text()

    dlg.close()
    store.close_all()


def test_print_dialog_blocks_print_without_pages(qapp, tmp_path):
    src = tmp_path / "doc.pdf"
    _make_pdf(src, [(300, 400)] * 2)
    store = PdfStore()
    ws = Workspace()
    ws.open_replace(store.load(str(src)), str(src))

    dlg = PrintDialog(None, ws, store)
    assert dlg.btn_print.isEnabled()
    # 页码范围写错时直接禁用打印，而不是等点了再报错
    dlg.radio_custom.setChecked(True)
    dlg.edit_range.setText("99")
    assert not dlg.btn_print.isEnabled()
    dlg.edit_range.setText("1")
    assert dlg.btn_print.isEnabled()

    dlg.close()
    store.close_all()


def test_main_window_print_document_empty_workspace(qapp):
    win = MainWindow()
    win.print_document()
    assert "工作区没有页面可供打印" in win.statusBar().currentMessage()
    win.close()


def test_print_dialog_defaults_to_fit_window_state(qapp, tmp_path):
    """打印预览默认显示适应窗口状态，且随窗口大小与排版设置自动保持适应。"""
    src = tmp_path / "doc.pdf"
    _make_pdf(src, [(300, 400)] * 3)
    store = PdfStore()
    ws = Workspace()
    ws.open_replace(store.load(str(src)), str(src))

    dlg = PrintDialog(None, ws, store)
    assert dlg._auto_fit is True
    assert dlg.btn_fit.isChecked() is True

    dlg.show()
    qapp.processEvents()
    initial_scale = dlg.canvas.scale
    assert initial_scale > 0.4
    assert dlg.btn_fit.isChecked() is True

    # 1. 改变窗口大小时自动重新适应窗口
    dlg.resize(1500, 950)
    qapp.processEvents()
    assert dlg._auto_fit is True
    assert dlg.btn_fit.isChecked() is True
    assert dlg.canvas.scale > initial_scale

    # 2. 更改排版参数（如切换双面）时自动保持适应窗口
    dlg.radio_double.setChecked(True)
    qapp.processEvents()
    assert dlg._auto_fit is True
    assert dlg.btn_fit.isChecked() is True
    assert len(dlg.canvas.sheets) > 1
    dlg.resize(880, 950)
    qapp.processEvents()
    stable = (dlg.scroll.viewport().size(), dlg.canvas.scale)
    for _ in range(4):
        dlg._fit_window()
        qapp.processEvents()
        assert (dlg.scroll.viewport().size(), dlg.canvas.scale) == stable

    # 3. 手动放大缩小后退出适应窗口状态
    dlg._zoom_in()
    assert dlg._auto_fit is False
    assert dlg.btn_fit.isChecked() is False
    manual_scale = dlg.canvas.scale

    # 4. 手动缩放下改变窗口，保留用户自定义倍率
    dlg.resize(1120, 760)
    qapp.processEvents()
    assert dlg.canvas.scale == manual_scale

    # 5. 再次点击适应窗口，恢复适应窗口状态并重新算缩放
    dlg.btn_fit.click()
    qapp.processEvents()
    assert dlg._auto_fit is True
    assert dlg.btn_fit.isChecked() is True
    assert dlg.canvas.scale != manual_scale

    dlg.close()
    store.close_all()


def test_apply_sheet_layout_orientation_robustness(qapp):
    """跨打印机驱动回归测试：横向时 SheetGeometry 宽高比必须为横向，纵向时必须为纵向。"""
    from PyQt6.QtPrintSupport import QPrinterInfo
    printers = QPrinterInfo.availablePrinterNames()
    if not printers:
        printers = [""]

    for name in printers:
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        if name:
            printer.setPrinterName(name)

        opts_landscape = PrintOptions(printer_name=name, orientation="landscape", margin_mm=0.0)
        geo_l = apply_sheet_layout(printer, opts_landscape, False)
        assert geo_l.width >= geo_l.height, f"Printer {name} landscape width must >= height"
        assert geo_l.portrait is False

        opts_portrait = PrintOptions(printer_name=name, orientation="portrait", margin_mm=0.0)
        geo_p = apply_sheet_layout(printer, opts_portrait, True)
        assert geo_p.height >= geo_p.width, f"Printer {name} portrait height must >= width"
        assert geo_p.portrait is True
