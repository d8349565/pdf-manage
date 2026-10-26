from __future__ import annotations

import pymupdf
from collections import Counter
from statistics import median
from app.pdf2excel.models import CellModel, CellStyle, TableModel
from app.pdf2excel.type_parser import parse_value
from app.pdf2excel.style_parser import build_cell_style

EPSILON = 1.5  # PDF 点数容差


def detect_tables(
    page: pymupdf.Page,
    spans: list[dict],
    lines: list[dict] | None = None,
    fills: list[dict] | None = None,
    warnings: list[str] | None = None,
) -> list[TableModel]:
    """使用级联策略检测页面上的表格，并构建高质量 TableModel。"""
    if lines is None:
        lines = []
    if fills is None:
        fills = []

    strategies = [
        ("lines_strict", {"vertical_strategy": "lines_strict", "horizontal_strategy": "lines_strict"}),
        ("lines", {"vertical_strategy": "lines", "horizontal_strategy": "lines"}),
        ("text", {"vertical_strategy": "text", "horizontal_strategy": "text"}),
    ]

    best_tables = []
    best_strategy_name = "lines"
    # Curved artwork (for example a seal crossing a cell) is not a table
    # boundary. Supply only measured straight rules to the native grid finder.
    measured=[((line['bbox'][0],line['bbox'][1]),(line['bbox'][2],line['bbox'][3]))
              for line in lines if min(abs(line['bbox'][2]-line['bbox'][0]),abs(line['bbox'][3]-line['bbox'][1]))<=EPSILON]

    # Use measured rules even when an optional layout model misses a scan.
    for name, strategy in strategies:
        if name == 'text' and page.get_images():
            from app.pdf2excel.raster_grid import raster_grid_lines
            raster_lines = raster_grid_lines(page)
            if raster_lines:
                detected = page.find_tables(use_layout=False, vertical_strategy='lines_strict', horizontal_strategy='lines_strict',
                                            add_lines=raster_lines)
                best_tables = [table for table in detected.tables if table.row_count >= 2 and table.col_count >= 2]
                if best_tables:
                    best_strategy_name = 'raster_grid'
                    lines = [*lines, *[{'bbox': (*start, *end), 'width': .8, 'color': '#606060'}
                                     for start, end in raster_lines]]
                    break
        if name == 'text':
            if any(span.get('alpha', 255) == 0 for span in spans):
                from app.pdf2excel.alignment_grid import alignment_grid_lines
                aligned_lines,header_fills=alignment_grid_lines(page,spans)
                if aligned_lines:
                    detected=page.find_tables(use_layout=False,vertical_strategy='lines_strict',horizontal_strategy='lines_strict',add_lines=aligned_lines)
                    best_tables=[table for table in detected.tables if table.row_count>=2 and table.col_count>=2]
                    if best_tables:
                        best_strategy_name='alignment_grid'
                        fills=[*fills,*header_fills]
                        lines=[*lines,*[{'bbox':(*start,*end),'width':.5,'color':'#808080'} for start,end in aligned_lines]]
                        if warnings is not None:
                            warnings.append(f'第 {page.number+1} 页弱边框表格按重复对齐及底纹推断，请核对行列。')
                        break
                if warnings is not None:
                    warnings.append(f'第 {page.number + 1} 页未确认扫描表格网格，保留正文，不按整页文字猜测表格。')
                break
            from app.documentlayout import layout_from_raw
            raw=page.get_text('rawdict',flags=pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
            if any(item.get('column_count',1)>1 for item in layout_from_raw(raw,[])):
                if warnings is not None:
                    warnings.append(f'第 {page.number + 1} 页存在正文分栏，不按整页文字猜测表格。')
                break
        try:
            detected = page.find_tables(use_layout=False, **strategy,
                                        **({'paths':[],'add_lines':measured} if name!='text' and measured else {}))
        except Exception:
            continue

        if not detected or not detected.tables:
            continue

        # 有完整线框时直接使用线框结构；文字策略仅在无可用线框时兜底。
        # 不能以单元格数量选优，碎裂的网格反而会获得最高分。
        best_tables = [table for table in detected.tables
                       if table.row_count >= 2 and table.col_count >= 2]
        if best_tables:
            best_strategy_name = name
            break

    # A valid local ruled grid does not prove that the surrounding OCR table
    # has been covered. Refine only when a stable aligned region materially
    # contains that grid; retain measured merges for already complete tables.
    if best_tables and best_strategy_name!='alignment_grid' and page.get_images() and any(span.get('alpha',255)==0 for span in spans):
        from app.pdf2excel.alignment_grid import alignment_grid_lines
        aligned_lines,header_fills=alignment_grid_lines(page,spans)
        if aligned_lines:
            detected=page.find_tables(use_layout=False,vertical_strategy='lines_strict',horizontal_strategy='lines_strict',add_lines=aligned_lines)
            for candidate in detected.tables:
                if candidate.row_count<2 or candidate.col_count<2:
                    continue
                area=pymupdf.Rect(candidate.bbox)
                contained=[table for table in best_tables
                           if (area&pymupdf.Rect(table.bbox)).get_area()>=pymupdf.Rect(table.bbox).get_area()*.95
                           and area.get_area()>=pymupdf.Rect(table.bbox).get_area()*1.25]
                # Never join two separately measured tables merely because
                # their fields line up across a page gutter.
                if len(contained)==1:
                    best_tables=[table for table in best_tables if table not in contained]+[candidate]
                    fills=[*fills,*header_fills]
                    lines=[*lines,*[{'bbox':(*start,*end),'width':.5,'color':'#808080'} for start,end in aligned_lines]]
                    if warnings is not None:
                        warning=f'第 {page.number+1} 页局部线框已结合重复对齐及表头底色补全，请核对行列。'
                        if warning not in warnings:warnings.append(warning)

    result_models = []
    page_no = page.number + 1  # 1-based 人类可读页码
    spans, excluded = _filter_watermarks(spans)
    if warnings is not None and excluded:
        warnings.append(f"第 {page_no} 页已忽略 {excluded} 段重复、浅色、倾斜的大字疑似水印，请核对原文。")
    if warnings is not None and best_tables and best_strategy_name == 'text':
        warnings.append(f"第 {page_no} 页未识别到完整表格线，使用文字对齐推断，请重点核对行列。")

    for table in best_tables:
        col_bounds, row_bounds = _build_grid(table.cells, table.bbox)
        if len(col_bounds) < 2 or len(row_bounds) < 2:
            continue

        # 计算每行行高与每列列宽 (PDF points)
        col_widths = [col_bounds[i + 1] - col_bounds[i] for i in range(len(col_bounds) - 1)]
        row_heights = [row_bounds[i + 1] - row_bounds[i] for i in range(len(row_bounds) - 1)]

        # 收集有效 cell_rect，并去重（避免重复单元格）
        raw_cell_rects = []
        seen_rects = set()
        for c_rect in table.cells:
            if c_rect is None:
                continue
            r_key = (round(c_rect[0], 1), round(c_rect[1], 1), round(c_rect[2], 1), round(c_rect[3], 1))
            if r_key not in seen_rects:
                seen_rects.add(r_key)
                raw_cell_rects.append(c_rect)

        # 如果没有识别出有效 cell 矩形，按网格逐格生成
        if not raw_cell_rects:
            for r_idx in range(len(row_bounds) - 1):
                for c_idx in range(len(col_bounds) - 1):
                    raw_cell_rects.append(
                        (col_bounds[c_idx], row_bounds[r_idx], col_bounds[c_idx + 1], row_bounds[r_idx + 1])
                    )

        # 先将 spans 归属到各个单元格 bbox
        cell_spans_map: dict[int, list[dict]] = {idx: [] for idx in range(len(raw_cell_rects))}
        for span in spans:
            fragments: dict[int, list[dict]] = {}
            # PDF 的一个 span 可能横跨多列，不能整段按中心点塞进一格。
            chars = span.get('chars') or [{'c': span.get('text', ''), 'bbox': span['bbox']}]
            for char in chars:
                x0, y0, x1, y1 = char['bbox']
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                for idx, (cx0, cy0, cx1, cy1) in enumerate(raw_cell_rects):
                    if cx0 <= cx < cx1 and cy0 <= cy < cy1:
                        fragments.setdefault(idx, []).append(char)
                        break
            for idx, chars in fragments.items():
                text = ''.join(char['c'] for char in chars)
                if not text.strip():
                    continue
                bbox = (min(c['bbox'][0] for c in chars), min(c['bbox'][1] for c in chars),
                        max(c['bbox'][2] for c in chars), max(c['bbox'][3] for c in chars))
                cell_spans_map[idx].append(dict(span, text=text, bbox=bbox))

        cells: list[CellModel] = []
        for idx, cell_rect in enumerate(raw_cell_rects):
            r, c, r_span, c_span = _detect_merges(cell_rect, col_bounds, row_bounds)
            c_spans = cell_spans_map[idx]

            # 排列单元格内多行文本
            text, text_bbox = _format_cell_text_and_bbox(c_spans)

            # 类型推断
            value, num_fmt = parse_value(text) if text else ("", None)
            is_numeric = isinstance(value, (int, float))

            # 样式推断
            style = build_cell_style(
                cell_bbox=cell_rect,
                text_bbox=text_bbox,
                spans=c_spans,
                lines=lines,
                fills=fills,
                is_numeric=is_numeric,
            )

            cells.append(
                CellModel(
                    row=r,
                    col=c,
                    row_span=r_span,
                    col_span=c_span,
                    text=text,
                    value=value,
                    number_format=num_fmt,
                    bbox=cell_rect,
                    style=style,
                    confidence=1.0,
                )
            )

        strategy_base = 0.95
        if best_strategy_name == "lines_strict":
            strategy_base = 0.98
        elif best_strategy_name == "text":
            strategy_base = 0.85

        expected_cells = max(1, (len(row_bounds) - 1) * (len(col_bounds) - 1))
        fill_ratio = min(1.0, len(cells) / expected_cells)
        tbl_confidence = round(strategy_base * (0.5 + 0.5 * fill_ratio), 2)

        result_models.append(
            TableModel(
                page_no=page_no,
                bbox=tuple(table.bbox),
                cells=cells,
                row_count=len(row_bounds) - 1,
                col_count=len(col_bounds) - 1,
                row_heights=row_heights,
                col_widths=col_widths,
                confidence=tbl_confidence,
            )
        )

    return result_models


def _build_grid(cells: list, table_bbox: tuple) -> tuple[list[float], list[float]]:
    """从单元格列表提取唯一的排序 X 和 Y 边界构成网格。"""
    xs, ys = [], []
    for cell in cells:
        if cell is not None and isinstance(cell, (list, tuple)) and len(cell) >= 4:
            xs.extend([cell[0], cell[2]])
            ys.extend([cell[1], cell[3]])

    if table_bbox and len(table_bbox) >= 4:
        xs.extend([table_bbox[0], table_bbox[2]])
        ys.extend([table_bbox[1], table_bbox[3]])

    def deduplicate(coords: list[float]) -> list[float]:
        sorted_coords = sorted(coords)
        if not sorted_coords:
            return []
        res = [sorted_coords[0]]
        for val in sorted_coords[1:]:
            if val - res[-1] > EPSILON:
                res.append(val)
        return res

    col_boundaries = deduplicate(xs)
    row_boundaries = deduplicate(ys)

    return col_boundaries, row_boundaries


def _detect_merges(
    cell_rect: tuple, col_bounds: list[float], row_bounds: list[float]
) -> tuple[int, int, int, int]:
    """根据单元格和网格边界，确定 (row, col, row_span, col_span)。"""
    x0, y0, x1, y1 = cell_rect

    def find_index(bounds: list[float], val: float) -> int:
        for i, b in enumerate(bounds):
            if abs(b - val) <= EPSILON:
                return i
        return min(range(len(bounds)), key=lambda i: abs(bounds[i] - val))

    c = find_index(col_bounds, x0)
    c_end = find_index(col_bounds, x1)

    r = find_index(row_bounds, y0)
    r_end = find_index(row_bounds, y1)

    return (r, c, max(1, r_end - r), max(1, c_end - c))


def _format_cell_text_and_bbox(spans: list[dict]) -> tuple[str, tuple[float, float, float, float] | None]:
    """按 Y 坐标分组合并同一行文本，并计算总的 text_bbox。"""
    if not spans:
        return "", None

    from app.pdf2excel.analyzer import text_rows

    min_x = min(s["bbox"][0] for s in spans)
    min_y = min(s["bbox"][1] for s in spans)
    max_x = max(s["bbox"][2] for s in spans)
    max_y = max(s["bbox"][3] for s in spans)
    text_bbox = (min_x, min_y, max_x, max_y)

    lines = [" ".join(span['text'] for span in row) for row in text_rows(spans)]

    return "\n".join(lines).strip(), text_bbox


def _filter_watermarks(spans: list[dict]) -> tuple[list[dict], int]:
    """保守排除重复、浅色、倾斜且明显大于正文的文字，保留普通灰字及竖排表头。"""
    sizes = [s.get('size', 0) for s in spans
             if s.get('text', '').strip() and abs(s.get('dir', (1, 0))[1]) < .1]
    if not sizes:
        return spans, 0
    body_size = median(sizes)
    repeats = Counter(s.get('text', '').strip() for s in spans)
    kept = []
    for span in spans:
        dx, dy = span.get('dir', (1, 0))
        color = span.get('color', 0)
        pale = min((color >> 16) & 255, (color >> 8) & 255, color & 255) >= 160
        watermark = (abs(dx) > .15 and abs(dy) > .15
                     and span.get('size', 0) >= body_size * 1.8
                     and (pale or span.get('alpha', 255) < 100)
                     and repeats[span.get('text', '').strip()] >= 2)
        if not watermark:
            kept.append(span)
    return kept, len(spans) - len(kept)
