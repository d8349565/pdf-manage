from __future__ import annotations

import pymupdf
from app.pdf2excel.models import PageAnalysis

def analyze_page(page: pymupdf.Page) -> PageAnalysis:
    """
    分析页面，判断它是电子版还是扫描版。
    """
    # 获取文本统计
    text = page.get_text('text')
    blocks = page.get_text('dict').get('blocks', [])
    text_blocks = sum(1 for b in blocks if b.get('type') == 0)  # 类型0表示文本
    image_blocks = [b for b in blocks if b.get('type') == 1]  # 类型1表示图片
    
    # 计算图片面积占比
    page_area = page.rect.width * page.rect.height
    image_area = sum((b['bbox'][2] - b['bbox'][0]) * (b['bbox'][3] - b['bbox'][1]) for b in image_blocks if 'bbox' in b)
    image_area_ratio = image_area / page_area if page_area > 0 else 0.0
    
    # 电子版与扫描版判断逻辑：
    # 纯扫描版通常整页被一张大图覆盖 (>80% 面积) 且文字几乎为 0。
    clean_text = text.strip()
    is_scanned = (image_area_ratio > 0.8 and len(clean_text) < 10) or (len(clean_text) == 0 and len(image_blocks) > 0)
    is_electronic = not is_scanned and (len(clean_text) > 0 or len(page.get_drawings()) > 0)
    
    return PageAnalysis(
        page_no=page.number + 1,
        text_length=len(text.strip()),
        text_blocks=text_blocks,
        drawing_count=len(page.get_drawings()),
        image_count=len(image_blocks),
        image_area_ratio=image_area_ratio,
        is_electronic=is_electronic,
    )

def extract_spans(page: pymupdf.Page) -> list[dict]:
    """
    提取所有的文本跨度（spans），包含完整的元数据。
    返回包含字典的列表，每个字典具有：
    {'text': str, 'bbox': (x0, y0, x1, y1), 'font': str, 'size': float, 
     'flags': int, 'color': int}
    """
    spans_list = []
    # 表格检测返回显示方向坐标，而 get_text/get_drawings 返回未旋转坐标。
    matrix = page.rotation_matrix
    blocks = page.get_text('rawdict').get('blocks', [])
    for block in blocks:
        if block.get('type') == 0:  # 文本块
            for line in block.get('lines', []):
                for span in line.get('spans', []):
                    chars = [dict(char, bbox=tuple(pymupdf.Rect(char['bbox']) * matrix))
                             for char in span.get('chars', [])]
                    dx, dy = line.get('dir', (1, 0))
                    spans_list.append({
                        'text': ''.join(char['c'] for char in chars),
                        'bbox': tuple(pymupdf.Rect(span['bbox']) * matrix),
                        'chars': chars,
                        'dir': (dx * matrix.a + dy * matrix.c,
                                dx * matrix.b + dy * matrix.d),
                        'font': span.get('font', ''),
                        'size': span.get('size', 0.0),
                        'flags': span.get('flags', 0),
                        'color': span.get('color', 0),
                        'alpha': span.get('alpha', 255),
                    })
    return spans_list

def extract_drawings(page: pymupdf.Page) -> tuple[list[dict], list[dict]]:
    """
    从页面的绘图中提取矢量线条和填充矩形。
    返回 (lines, fills)，其中：
    - lines: [{'bbox': (x0,y0,x1,y1), 'width': float, 'color': str}, ...]
    - fills: [{'bbox': (x0,y0,x1,y1), 'color': str}, ...]
    """
    lines = []
    fills = []
    
    drawings = page.get_drawings()
    for draw in drawings:
        color = draw.get('color')
        fill = draw.get('fill')
        width = draw.get('width', 0.0)
        items = draw.get('items', [])
        
        for item in items:
            op = item[0]
            if op == 'l':
                p1, p2 = item[1], item[2]
                x0, x1 = min(p1.x, p2.x), max(p1.x, p2.x)
                y0, y1 = min(p1.y, p2.y), max(p1.y, p2.y)
                bbox = (x0, y0, x1, y1)
                lines.append({
                    'bbox': bbox,
                    'width': width,
                    'color': color
                })
            elif op == 're':
                rect = item[1]
                bbox = (rect.x0, rect.y0, rect.x1, rect.y1)
                if fill is not None:
                    fills.append({
                        'bbox': bbox,
                        'color': fill
                    })
                if color is not None:
                    x0, y0, x1, y1 = bbox
                    for edge in ((x0, y0, x1, y0), (x0, y1, x1, y1),
                                 (x0, y0, x0, y1), (x1, y0, x1, y1)):
                        lines.append({'bbox': edge, 'width': width, 'color': color})
                    
    matrix = page.rotation_matrix
    for item in lines + fills:
        item['bbox'] = tuple(pymupdf.Rect(item['bbox']) * matrix)
    return lines, fills
