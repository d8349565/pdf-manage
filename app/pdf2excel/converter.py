from __future__ import annotations
from typing import Callable
import os
import tempfile
import pymupdf
from app.output import commit_file, stamp
from app.pdf2excel.models import TableModel, ConversionResult
from app.pdf2excel.analyzer import analyze_page, extract_spans, extract_drawings
from app.pdf2excel.detector import detect_tables
from app.pdf2excel.page_merger import merge_cross_page_tables
from app.pdf2excel.excel_builder import build_workbook
from app.pdf2excel.quality import assess_conversion, QualityReport

ProgressCallback = Callable[[int, int, str], None]  # (current_page, total_pages, status_msg)
CancelCheck = Callable[[], bool]

def convert_pdf_to_excel(
    pdf_path: str,
    output_xlsx_path: str,
    page_indices: list[int] | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
    *,
    merge_tables: bool = False,
    include_all_content: bool = False,
) -> tuple[ConversionResult, QualityReport]:
    """
    转换电子版 PDF 为 Excel (XLSX) 格式的主入口。
    纯本地离线运行。默认仅提取表格；全部内容模式按原页保留正文、表格和图片。
    """
    def check_cancelled():
        if cancel_check and cancel_check():
            raise InterruptedError("转换过程已被用户取消。")

    check_cancelled()
    output_path = os.path.abspath(output_xlsx_path)
    expected_target = stamp(output_path)
    original_source = stamp(pdf_path)
    doc = pymupdf.open(pdf_path)
    total_pages = doc.page_count
    
    if page_indices is None:
        page_indices = list(range(total_pages))
        
    pages_analyzed = 0
    pages_skipped = 0
    pages_with_tables = 0
    warnings: list[str] = []
    
    all_tables: list[TableModel] = []
    content_pages: list[dict] = []
    
    try:
        for idx, page_idx in enumerate(page_indices):
            check_cancelled()
                
            if progress_callback:
                progress_callback(idx + 1, len(page_indices), f"正在分析第 {page_idx + 1} 页...")
                
            if page_idx < 0 or page_idx >= total_pages:
                warnings.append(f"无效的页码索引: {page_idx}")
                continue
                
            page = doc[page_idx]
            pages_analyzed += 1
            
            # 1. 分析页面，检查是否为电子版
            page_analysis = analyze_page(page)
            if include_all_content:
                from app.documentlayout import page_layout
                if page_analysis.text_length == 0 and page_analysis.image_count:
                    # Unrecognized scans still retain their appearance, with no invented OCR text.
                    matrix = pymupdf.Matrix(1.25, 1.25).prerotate(-page.rotation)
                    image = page.get_pixmap(matrix=matrix, alpha=False, annots=False)
                    layout = [dict(kind='image', bbox=(0, 0, page.cropbox.width, page.cropbox.height),
                                   data=image.tobytes('png'))]
                    page_warnings = [f"第 {page_idx + 1} 页没有可用文本层，已保留整页图片；需要可编辑文字时请先 OCR。"]
                else:
                    layout, page_warnings = page_layout(page, include_images=True)
                check_cancelled()
                warnings.extend(page_warnings)
                tables = [item['table'] for item in layout if item['kind'] == 'table']
                if tables:
                    pages_with_tables += 1
                    all_tables.extend(tables)
                content_pages.append(dict(page_no=page_idx + 1, width=page.cropbox.width,
                                          height=page.cropbox.height, items=layout))
                continue

            if not page_analysis.is_electronic:
                pages_skipped += 1
                warnings.append(f"第 {page_idx + 1} 页可能是扫描件，已跳过。")
                continue
                
            # 2. 提取信息
            spans = extract_spans(page)
            lines, fills = extract_drawings(page)
            
            # 3. 检测表格
            tables = detect_tables(page, spans, lines, fills, warnings=warnings)
            check_cancelled()
            if tables:
                pages_with_tables += 1
                all_tables.extend(tables)
            else:
                warnings.append(f"第 {page_idx + 1} 页未检测到表格。")
                
        # 4. 跨页表格合并
        if progress_callback:
            progress_callback(0, 0, "正在整理表格...")
        check_cancelled()
        merged_tables = merge_cross_page_tables(all_tables) if merge_tables and not include_all_content else all_tables
        if merge_tables and include_all_content:
            warnings.append("全部内容模式按页保留正文与表格位置，不合并跨页表格。")
        
        result = ConversionResult(
            tables=merged_tables,
            pages_analyzed=pages_analyzed,
            pages_with_tables=pages_with_tables,
            pages_skipped=pages_skipped,
            warnings=warnings,
            include_all_content=include_all_content,
            pages_exported=len(content_pages) if include_all_content else pages_with_tables,
            text_blocks_exported=sum(item['kind'] == 'text' for page in content_pages for item in page['items']),
            images_exported=sum(item['kind'] == 'image' for page in content_pages for item in page['items']),
        )
        
        # 6. 生成质量报告
        report = assess_conversion(result)

        # 无结果不创建空文件，也不覆盖已有文件。
        if merged_tables or content_pages:
            if progress_callback:
                progress_callback(0, 0, "正在生成 Excel 文件...")
            check_cancelled()
            wb = build_workbook(merged_tables, pages=content_pages if include_all_content else None)
            temp_path = None
            try:
                check_cancelled()
                fd, temp_path = tempfile.mkstemp(
                    prefix=".pdf2excel-", suffix=".xlsx", dir=os.path.dirname(output_path)
                )
                os.close(fd)
                wb.save(temp_path)
                check_cancelled()
                if stamp(pdf_path) != original_source:
                    raise OSError("源 PDF 在转换期间发生变化，未提交结果。")
                commit_file(temp_path, output_path, expected_target, cancel=cancel_check)
                temp_path = None
            finally:
                wb.close()
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)
        
        if progress_callback:
            progress_callback(len(page_indices), len(page_indices), "转换完成。")
            
        return result, report
        
    finally:
        doc.close()
