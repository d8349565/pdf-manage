from __future__ import annotations
from dataclasses import dataclass
from app.pdf2excel.models import TableModel, ConversionResult

@dataclass
class TableQuality:
    table_index: int
    page_no: int
    structure_score: float   # 0.0 ~ 1.0 (regularity, valid cell ratio)
    text_coverage: float     # 0.0 ~ 1.0 (non-empty cell ratio)
    overall_score: float     # weighted score
    quality_level: str       # '优秀' (>=0.9), '良好' (>=0.75), '需检查' (<0.75)
    warnings: list[str]

@dataclass
class QualityReport:
    tables: list[TableQuality]
    overall_score: float
    high_quality_count: int
    warning_count: int
    summary_text: str

def assess_table(table: TableModel, index: int = 0) -> TableQuality:
    """
    评估单个表格的转换质量。
    """
    warnings: list[str] = []
    
    # 计算 structure_score
    expected_cells = table.row_count * table.col_count
    actual_cells = len(table.cells)
    
    if expected_cells > 0:
        covered = set()
        for cell in table.cells:
            for row in range(max(0, cell.row), min(table.row_count, cell.row + cell.row_span)):
                for col in range(max(0, cell.col), min(table.col_count, cell.col + cell.col_span)):
                    covered.add((row, col))
        structure_score = len(covered) / expected_cells
        if structure_score < 0.8:
            warnings.append("表格结构可能存在缺失（单元格数量明显不足）")
    else:
        structure_score = 0.0
        warnings.append("无效的表格结构（行数或列数为0）")
        
    # 计算 text_coverage
    non_empty_cells = sum(1 for c in table.cells if c.text.strip())
    text_coverage = non_empty_cells / actual_cells if actual_cells > 0 else 0.0
    
    if text_coverage < 0.1:
        warnings.append("表格文本稀疏，可能包含大量空单元格")
        
    # 计算 overall_score (基于 table 的置信度以及覆盖率和结构得分综合)
    conf = max(0.0, min(1.0, table.confidence))
    overall_score = round(conf * 0.4 + structure_score * 0.4 + text_coverage * 0.2, 3)
    
    if overall_score >= 0.9:
        quality_level = '优秀'
    elif overall_score >= 0.75:
        quality_level = '良好'
    else:
        quality_level = '需检查'
        
    return TableQuality(
        table_index=index,
        page_no=table.page_no,
        structure_score=structure_score,
        text_coverage=text_coverage,
        overall_score=overall_score,
        quality_level=quality_level,
        warnings=warnings
    )

def assess_conversion(result: ConversionResult) -> QualityReport:
    """
    评估整个转换结果的质量，生成报告。
    """
    table_qualities: list[TableQuality] = []
    total_score = 0.0
    high_quality_count = 0
    warning_count = len(result.warnings)
    
    for i, table in enumerate(result.tables):
        tq = assess_table(table, i)
        table_qualities.append(tq)
        total_score += tq.overall_score
        
        if tq.quality_level == '优秀':
            high_quality_count += 1
        if tq.warnings:
            warning_count += len(tq.warnings)
            
    num_tables = len(result.tables)
    overall_score = total_score / num_tables if num_tables > 0 else 0.0
    
    if result.include_all_content:
        summary_text = (
            f"已按页导出 {result.pages_exported} 页全部内容，包含 {num_tables} 个表格、"
            f"{result.text_blocks_exported} 段文字和 {result.images_exported} 个图像区域。"
            "表格单元格保留数值与合并结构，正文与图片按阅读顺序排列。"
            "图片中的文字需先 OCR 才能编辑；请对照原文核对内容与位置。"
        )
    else:
        summary_text = (
            f"成功分析了 {result.pages_analyzed} 页，"
            f"在 {result.pages_with_tables} 页中提取到 {num_tables} 个表格。"
            f"跳过页面: {result.pages_skipped}。"
            "结构检查仅供参考，不代表数据准确率，请对照原文核对。"
        )
    
    return QualityReport(
        tables=table_qualities,
        overall_score=overall_score,
        high_quality_count=high_quality_count,
        warning_count=warning_count,
        summary_text=summary_text
    )
