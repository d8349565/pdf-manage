from __future__ import annotations
from app.pdf2excel.models import TableModel, CellModel

def merge_cross_page_tables(tables: list[TableModel]) -> list[TableModel]:
    """
    识别跨页延续的表格，并将其合并为一个逻辑表格。
    严格保证：只有前一页的【最后一张表】与后一页的【第一张表】才能参与跨页延续判定。
    """
    if not tables:
        return []

    # 按页码归类各页表格列表，并按纵坐标 y0 排序
    from collections import defaultdict
    page_tables: dict[int, list[TableModel]] = defaultdict(list)
    for t in tables:
        page_tables[t.page_no].append(t)

    for p in page_tables:
        page_tables[p].sort(key=lambda t: t.bbox[1])

    merged_tables: list[TableModel] = []
    sorted_pages = sorted(page_tables.keys())

    # 展开为有序表序列，同时标记每个表在自身页内是否为第一张和最后一张
    ordered_tables: list[tuple[TableModel, bool, bool]] = []
    for p in sorted_pages:
        t_list = page_tables[p]
        for idx, t in enumerate(t_list):
            is_first = (idx == 0)
            is_last = (idx == len(t_list) - 1)
            ordered_tables.append((t, is_first, is_last))

    i = 0
    while i < len(ordered_tables):
        current_table, is_first, is_last = ordered_tables[i]
        
        # 尝试向下连续合并
        j = i + 1
        while j < len(ordered_tables):
            next_table, next_is_first, next_is_last = ordered_tables[j]
            # 只有当 current 是本页末表，next 是下页首表，才允许合并
            if is_last and next_is_first and _is_continuation(current_table, next_table):
                current_table = _merge_two_tables(current_table, next_table, prior_index=len(merged_tables))
                # 合并后，current_table 在新页中的末尾状态继承自 next_table
                is_last = next_is_last
                j += 1
            else:
                break

        merged_tables.append(current_table)
        i = j

    return merged_tables


def _is_continuation(t1: TableModel, t2: TableModel) -> bool:
    """
    判断 t2 是否为 t1 的跨页延续表格。
    """
    if t1.col_count != t2.col_count:
        return False
    # 页码必须相邻
    end_page = getattr(t1, "_last_page_no", t1.page_no)
    if t2.page_no != end_page + 1:
        return False
    
    # X 坐标对齐误差检查
    width1 = t1.bbox[2] - t1.bbox[0]
    width2 = t2.bbox[2] - t2.bbox[0]
    
    if width1 <= 0 or width2 <= 0:
        return False
        
    x0_diff = abs(t1.bbox[0] - t2.bbox[0])
    x1_diff = abs(t1.bbox[2] - t2.bbox[2])
    if x0_diff > 0.15 * width1 or x1_diff > 0.15 * width1:
        return False

    # 列宽比例匹配
    sum_w1 = sum(t1.col_widths)
    sum_w2 = sum(t2.col_widths)
    if sum_w1 == 0 or sum_w2 == 0:
        return False
        
    for cw1, cw2 in zip(t1.col_widths, t2.col_widths):
        ratio1 = cw1 / sum_w1
        ratio2 = cw2 / sum_w2
        if abs(ratio1 - ratio2) > 0.12:
            return False

    # 几何相似不足以证明是同一张表，至少要求非空首行完全一致。
    return _has_repeated_header(t1, t2)

def _merge_two_tables(t1: TableModel, t2: TableModel, prior_index: int | None = None) -> TableModel:
    """
    合并两个相邻表格。
    """
    # PDF 没有可靠的表头语义；相同首行也可能是合法重复数据。
    skip = 0
    
    new_cells = list(t1.cells)
    for cell in t2.cells:
        if cell.row < skip:
            continue
        new_row = cell.row - skip + t1.row_count
        new_cell = CellModel(
            row=new_row,
            col=cell.col,
            text=cell.text,
            value=cell.value,
            number_format=cell.number_format,
            bbox=cell.bbox,
            row_span=cell.row_span,
            col_span=cell.col_span,
            style=cell.style,
            confidence=cell.confidence
        )
        new_cells.append(new_cell)
        
    new_row_count = t1.row_count + t2.row_count - skip
    new_row_heights = list(t1.row_heights) + list(t2.row_heights)[skip:]
    
    # bbox 合并，包含上下 y 坐标范围，及左右 x 坐标并集
    new_bbox = (
        min(t1.bbox[0], t2.bbox[0]),
        t1.bbox[1],
        max(t1.bbox[2], t2.bbox[2]),
        t2.bbox[3]
    )

    merged = TableModel(
        page_no=t1.page_no,
        bbox=new_bbox,
        row_count=new_row_count,
        col_count=t1.col_count,
        cells=new_cells,
        row_heights=new_row_heights,
        col_widths=t1.col_widths,
        confidence=round((t1.confidence + t2.confidence) / 2.0, 2),
        continuation_of=prior_index if prior_index is not None else t1.page_no
    )
    # 记录该连续表所延伸到达的最新物理页码
    merged._last_page_no = t2.page_no
    return merged

def _has_repeated_header(t1: TableModel, t2: TableModel) -> bool:
    """
    判断 t2 是否重复了 t1 的表头（通过比较第一行的文本）。
    """
    if t1.row_count == 0 or t2.row_count == 0:
        return False
        
    # 按照列排序以防乱序
    t1_first_row_sorted = sorted([c for c in t1.cells if c.row == 0], key=lambda x: x.col)
    t2_first_row_sorted = sorted([c for c in t2.cells if c.row == 0], key=lambda x: x.col)
    
    t1_texts = [c.text.strip() for c in t1_first_row_sorted]
    t2_texts = [c.text.strip() for c in t2_first_row_sorted]
    
    if not t1_texts or not t2_texts or len(t1_texts) != len(t2_texts):
        return False
        
    return any(t1_texts) and t1_texts == t2_texts
