from __future__ import annotations
from typing import Callable
import os
import tempfile
import pymupdf
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
) -> tuple[ConversionResult, QualityReport]:
    """
    转换电子版 PDF 为 Excel (XLSX) 格式的主入口。
    纯本地离线运行。默认按页保留表格；显式合并时仍保留重复行。
    """
    def check_cancelled():
        if cancel_check and cancel_check():
            raise InterruptedError("转换过程已被用户取消。")

    check_cancelled()
    doc = pymupdf.open(pdf_path)
    total_pages = doc.page_count
    
    if page_indices is None:
        page_indices = list(range(total_pages))
        
    pages_analyzed = 0
    pages_skipped = 0
    pages_with_tables = 0
    warnings: list[str] = []
    
    all_tables: list[TableModel] = []
    
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
        merged_tables = merge_cross_page_tables(all_tables) if merge_tables else all_tables
        
        result = ConversionResult(
            tables=merged_tables,
            pages_analyzed=pages_analyzed,
            pages_with_tables=pages_with_tables,
            pages_skipped=pages_skipped,
            warnings=warnings
        )
        
        # 6. 生成质量报告
        report = assess_conversion(result)

        # 无结果不创建空文件，也不覆盖已有文件。
        if merged_tables:
            if progress_callback:
                progress_callback(0, 0, "正在生成 Excel 文件...")
            check_cancelled()
            wb = build_workbook(merged_tables)
            temp_path = None
            try:
                check_cancelled()
                output_path = os.path.abspath(output_xlsx_path)
                fd, temp_path = tempfile.mkstemp(
                    prefix=".pdf2excel-", suffix=".xlsx", dir=os.path.dirname(output_path)
                )
                os.close(fd)
                wb.save(temp_path)
                check_cancelled()
                os.replace(temp_path, output_path)
            finally:
                wb.close()
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)
        
        if progress_callback:
            progress_callback(len(page_indices), len(page_indices), "转换完成。")
            
        return result, report
        
    finally:
        doc.close()
