"""在实际 EXE 内检查裁剪后仍可用的核心功能；只使用自行生成的数据。"""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path


def self_test(report_path: Path, ocr_runtime: Path | None = None) -> int:
    report = {"ok": False, "frozen": bool(getattr(sys, "frozen", False)), "checks": []}
    try:
        import hashlib
        import openpyxl
        import pymupdf
        from PyQt6.QtCore import QThreadPool
        from PyQt6.QtPrintSupport import QPrinter
        from PyQt6.QtWidgets import QApplication
        from app import APP_NAME, __version__
        from app.__main__ import _window_icon, startup_pdf_paths
        from app.icons import brand_icon
        from app.pdf2excel.converter import convert_pdf_to_excel
        from app.pdfio import NeedPassword
        from app.printing import PrintOptions, render_pages_to_printer
        from app.ui import MainWindow
        from app.windows_integration import association_entries, PROG_ID

        qapp = QApplication.instance() or QApplication([])
        qapp.setStyle("Fusion")
        report["platform"] = qapp.platformName()
        assert report["platform"] == "windows", "Windows platform plugin did not load"
        assert hashlib.sha256(b"abc").hexdigest() == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        assert hashlib.md5(b"abc").hexdigest() == "900150983cd24fb0d6963f7d28e17f72"
        for size in (16, 24, 32, 48, 64, 128, 256):
            assert not _window_icon().pixmap(size, size).isNull()
            assert not brand_icon().pixmap(size, size).isNull()
        report["checks"].append("application PNG artwork and ICO at seven sizes")

        with tempfile.TemporaryDirectory(prefix="smoke-", dir=report_path.parent) as temp:
            folder = Path(temp)
            source = folder / "中文 表格.pdf"
            with pymupdf.open() as doc:
                page = doc.new_page(width=400, height=400)
                for x in (50, 150, 250):
                    page.draw_line((x, 50), (x, 150))
                for y in (50, 100, 150):
                    page.draw_line((50, y), (250, y))
                for r, row in enumerate((("Name", "Price"), ("ABC", "123"))):
                    for c, value in enumerate(row):
                        page.insert_text((55 + c * 100, 75 + r * 50), value)
                doc.new_page(width=400, height=300).insert_text((40, 60), "Second page")
                doc.save(source)
            assert startup_pdf_paths([sys.executable, str(source)]) == [str(source)]
            window = MainWindow()
            assert window.windowTitle().endswith(f"{APP_NAME} v{__version__}（吾爱破解）")
            if ocr_runtime is None:
                from native_window_check import check_native_window
                report["native_resize"] = check_native_window(window, qapp)
                report["checks"].append("real Windows edge hit testing and native drag resize")
            else:
                report["native_resize"] = "not_run_in_ocr_pipeline_check"
            try:
                pages = window.store.load(str(source))
                window.ws.open_replace(pages, str(source))
                window.refresh()
                qapp.processEvents()
                from PyQt6.QtWidgets import QToolButton
                toolbar = window.navigation_bar.parentWidget()
                tools = {b.accessibleName(): b for b in toolbar.findChildren(QToolButton)}
                assert all(name in tools for name in ("左转所选页", "右转所选页", "删除所选页",
                                                       "提取所选页", "拆分文件", "表格转 Excel"))
                report["checks"].append("common page tools in the top toolbar")
                assert not window.store.render_qimage(pages[0], 800).isNull()
                report["checks"].append("main window, Chinese path, PDF load and rendering")
                window.ws.rotate(90)
                window.ws.undo_stack.undo()
                assert window.ws.pages[0].rotation == 0
                window.ws.undo_stack.redo()
                window.ws.move_page(0, 1)
                saved = folder / "组装.pdf"
                window.store.write(window.ws.pages, str(saved))
                with pymupdf.open(saved) as doc:
                    assert doc.page_count == 2 and doc[1].rotation == 90
                    assert "Second page" in doc[0].get_text()
                window.store.write([pages[0]], str(folder / "提取.pdf"))
                with pymupdf.open(folder / "提取.pdf") as doc:
                    assert doc.page_count == 1
                report["checks"].append("rotate, reorder, undo, redo, save and extract")

                encrypted = folder / "加密.pdf"
                with pymupdf.open(source) as doc:
                    doc.save(encrypted, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                             owner_pw="owner-test", user_pw="user-test")
                try:
                    window.store.load(str(encrypted))
                except NeedPassword:
                    pass
                else:
                    raise AssertionError("Encrypted PDF must request a password")
                unlocked = window.store.load(str(encrypted), password="user-test")
                assert len(unlocked) == 2
                assert not window.store.render_qimage(unlocked[0], 320).isNull()
                report["checks"].append("AES-256 PDF, password gate, decrypt and render")

                output = folder / "转换.xlsx"
                result, _ = convert_pdf_to_excel(str(source), str(output), page_indices=[0])
                assert result.pages_with_tables == 1
                wb = openpyxl.load_workbook(output)
                try:
                    assert list(wb.active.values) == [("Name", "Price"), ("ABC", 123)]
                finally:
                    wb.close()
                report["checks"].append("PDF table to Excel, write and reopen workbook")

                printed = folder / "打印.pdf"
                printer = QPrinter()
                printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
                printer.setOutputFileName(str(printed))
                render_pages_to_printer(printer, window.store, pages,
                                        PrintOptions(page_indices=[0, 1]))
                with pymupdf.open(printed) as doc:
                    assert doc.page_count == 2
                    assert doc[1].rect.width > doc[1].rect.height
                report["checks"].append("print to PDF and automatic landscape orientation")

                if ocr_runtime is not None:
                    from app.ocr.contracts import OCROptions
                    from app.ocr.service import run_ocr
                    from time import monotonic, sleep
                    scan = folder / "OCR 合成扫描.pdf"
                    recognized = folder / "OCR 搜索副本.pdf"
                    with pymupdf.open() as text_doc:
                        page = text_doc.new_page()
                        page.insert_font(fontname="SampleCJK", fontbuffer=pymupdf.Font("cjk").buffer)
                        page.insert_text((70, 120), "中文预算 12345.67", fontname="SampleCJK", fontsize=20)
                        image = page.get_pixmap(dpi=200).tobytes("png")
                    with pymupdf.open() as scanned:
                        page = scanned.new_page()
                        page.insert_image(page.rect, stream=image)
                        scanned.save(scan)
                    result = run_ocr(scan, recognized, [0], OCROptions(runtime_dir=str(ocr_runtime)))
                    assert result.processed_pages == (0,) and not result.failed_pages
                    with pymupdf.open(recognized) as doc:
                        assert doc[0].search_for("预算") and doc[0].search_for("12345.67")
                    window.ws.open_replace(window.store.load(str(recognized)), str(recognized))
                    window.refresh()
                    window.open_search()
                    window.search_bar.input.setText("预算")
                    window.search_bar.start()
                    deadline = monotonic() + 20
                    while window.search_bar.busy and monotonic() < deadline:
                        qapp.processEvents()
                        sleep(.005)
                    assert not window.search_bar.busy and window.search_bar.results
                    report["checks"].append("frozen base + offline OCR EXE + CJK extraction and existing search UI")
                    selector = window.text_selection
                    ref = window.ws.pages[0]
                    selector.snapshot = window.store.text_snapshot(ref)
                    selector.page_index, selector.key = 0, ref.render_key
                    selector.generation = window.preview_view.layout_generation
                    bounds = window.preview_view.page_rects[0]
                    selector.start = bounds.topLeft()
                    selector.select(bounds.bottomRight())
                    selector.start = None
                    # Verify the real copy handler without replacing the user's system clipboard.
                    from types import SimpleNamespace
                    import app.textselection as textselection
                    captured = []
                    real_application = textselection.QApplication
                    textselection.QApplication = SimpleNamespace(clipboard=lambda:
                        SimpleNamespace(setText=captured.append))
                    try:
                        selector.copy()
                    finally:
                        textselection.QApplication = real_application
                    assert captured and '中文预算' in captured[0] and '12345.67' in captured[0]
                    assert not window.ws.dirty
                    from app.wordconvert import convert_batch
                    from zipfile import ZipFile
                    from xml.etree import ElementTree
                    editable = folder / 'OCR 可编辑正文.docx'
                    results, cancelled, _ = convert_batch([(str(recognized), str(editable))], False,
                        lambda *args: None, lambda: False, direction='pdf_to_word')
                    assert results[0]['state'] == 'success' and not cancelled
                    with ZipFile(editable) as archive:
                        xml = ElementTree.fromstring(archive.read('word/document.xml'))
                        text = ''.join(node.text or '' for node in xml.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'))
                    assert '中文预算' in text and '12345.67' in text
                    report['checks'].append('OCR rectangle copy to an in-memory test target and editable DOCX using the recognized text layer')
            finally:
                QThreadPool.globalInstance().waitForDone()
                window.ws.reset()
                window.close()
                window.store.close_all()
            entries = association_entries(sys.executable)
            document = next(v for p, n, v in entries if p.endswith(PROG_ID + "\\DefaultIcon"))
            executable = Path(sys.executable).resolve()
            installed_icon = executable.parent / "_internal" / "pdf.ico"
            expected = f'"{installed_icon}",0' if installed_icon.is_file() else f'"{executable}",1'
            assert document == expected and "_MEI" not in document
            report["checks"].append("persistent PDF icon reference (no registry writes)")
        report["ok"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
