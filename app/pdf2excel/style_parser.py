from __future__ import annotations

from app.pdf2excel.models import CellStyle, CellModel

EPSILON = 1.5  # PDF容差值

# PDF内部字体名称映射 -> (显示名称, 是否粗体, 是否斜体)
FONT_MAP: dict[str, tuple[str, bool, bool]] = {
    'ArialMT': ('Arial', False, False),
    'Arial-BoldMT': ('Arial', True, False),
    'Arial-ItalicMT': ('Arial', False, True),
    'Arial-BoldItalicMT': ('Arial', True, True),
    'TimesNewRomanPSMT': ('Times New Roman', False, False),
    'TimesNewRomanPS-BoldMT': ('Times New Roman', True, False),
    'TimesNewRomanPS-ItalicMT': ('Times New Roman', False, True),
    'SimSun': ('宋体', False, False),
    'SimHei': ('黑体', False, False),
    'KaiTi': ('楷体', False, False),
    'FangSong': ('仿宋', False, False),
    'MicrosoftYaHei': ('微软雅黑', False, False),
    'MicrosoftYaHei-Bold': ('微软雅黑', True, False),
    'MicrosoftYaHeiUI': ('微软雅黑', False, False),
    'MicrosoftYaHeiUI-Bold': ('微软雅黑', True, False),
    'DengXian': ('等线', False, False),
    'DengXian-Bold': ('等线', True, False),
    'Calibri': ('Calibri', False, False),
    'Calibri-Bold': ('Calibri', True, False),
}

def clean_font_name(raw: str) -> tuple[str, bool, bool]:
    """
    清理PDF字体名称。返回 (名称, 是否粗体, 是否斜体)。
    步骤:
    1. 去除类似 'AAAAAA+' 的子集前缀
    2. 在 FONT_MAP 中查找
    3. 如果未找到，尝试去除 '-Bold', '-Italic', ',Bold', ',Italic' 等后缀
    4. 如果仍未找到，原样返回清理后的名称
    """
    # 去除子集前缀
    if '+' in raw:
        raw = raw.split('+', 1)[-1]
        
    if raw in FONT_MAP:
        return FONT_MAP[raw]
        
    bold = False
    italic = False
    name = raw
    
    lower_name = name.lower()
    if 'bold' in lower_name:
        bold = True
        name = name.replace('-Bold', '').replace(',Bold', '').replace('Bold', '')
    if 'italic' in lower_name:
        italic = True
        name = name.replace('-Italic', '').replace(',Italic', '').replace('Italic', '')
        
    # 去除两端多余符号
    name = name.strip('-, ')
    return name, bold, italic

def parse_font_style(span: dict) -> tuple[str | None, float | None, bool, bool, str | None]:
    """
    从 span 字典中提取 (字体名称, 字体大小, 粗体, 斜体, 十六进制颜色)。
    span 包含：'font', 'size', 'flags', 'color'
    flags 第 4 位 = 粗体，第 1 位 = 斜体
    color 是类似 0x000000 的整数，转换为十六进制字符串 '#000000'
    """
    font_raw = span.get('font', '')
    size = span.get('size')
    if size and span.get('alpha', 255) == 0 and span.get('bbox'):
        # An affine OCR font's scalar size can be much larger than its visible height.
        size = min(size, (span['bbox'][3] - span['bbox'][1]) / 1.3)
    flags = span.get('flags', 0)
    color_int = span.get('color')
    
    font_name, bold_from_name, italic_from_name = clean_font_name(font_raw) if font_raw else (None, False, False)
    
    bold = bold_from_name or bool(flags & (1 << 4))
    italic = italic_from_name or bool(flags & (1 << 1))
    
    color_hex = None
    if color_int is not None:
        try:
            # 转换为带 # 的6位十六进制颜色值
            color_hex = f"#{int(color_int):06X}"
        except (ValueError, TypeError):
            pass
            
    return font_name, size, bold, italic, color_hex

def infer_alignment(cell_bbox: tuple, text_bbox: tuple, is_numeric: bool = False) -> tuple[str | None, str | None]:
    """
    从边界框位置推断水平和垂直对齐方式。
    cell_bbox 和 text_bbox 格式为 (x0, y0, x1, y1)。
    
    水平对齐：
    - 如果 abs(左侧间距 - 右侧间距) < 阈值 → 'center'
    - 如果 左侧间距 << 右侧间距 → 'left'
    - 如果 右侧间距 << 左侧间距 → 'right'
    - 如果是数字，默认为 'right'
    
    垂直对齐：
    - 如果 abs(顶部间距 - 底部间距) < 阈值 → 'center'
    - 如果 顶部间距 << 底部间距 → 'top'
    - 否则 → 'bottom' 或 'center'
    
    阈值 = max(3.0, 单元格高度 * 0.15)
    返回 (水平对齐, 垂直对齐)
    """
    if not cell_bbox or not text_bbox:
        return ('right' if is_numeric else 'left'), 'center'
        
    cx0, cy0, cx1, cy1 = cell_bbox
    tx0, ty0, tx1, ty1 = text_bbox
    
    cell_width = cx1 - cx0
    cell_height = cy1 - cy0
    
    left_gap = tx0 - cx0
    right_gap = cx1 - tx1
    top_gap = ty0 - cy0
    bottom_gap = cy1 - ty1
    
    threshold_h = max(3.0, cell_width * 0.1)
    threshold_v = max(3.0, cell_height * 0.15)
    
    horizontal = 'left'
    if abs(left_gap - right_gap) < threshold_h:
        horizontal = 'center'
    elif left_gap < right_gap - threshold_h:
        horizontal = 'left'
    elif right_gap < left_gap - threshold_h:
        horizontal = 'right'
    elif is_numeric:
        horizontal = 'right'
        
    vertical = 'center'
    if abs(top_gap - bottom_gap) < threshold_v:
        vertical = 'center'
    elif top_gap < bottom_gap - threshold_v:
        vertical = 'top'
    elif bottom_gap < top_gap - threshold_v:
        vertical = 'bottom'
        
    return horizontal, vertical

def _normalize_color(c: object) -> str | None:
    """将多种可能的颜色格式转换为 6 位十六进制 '#RRGGBB'"""
    if c is None:
        return None
    if isinstance(c, (list, tuple)):
        if len(c) >= 3:
            # 浮点数 (0.0~1.0)
            if all(isinstance(v, (int, float)) and v <= 1.0 for v in c[:3]):
                r, g, b = [max(0, min(255, int(round(v * 255)))) for v in c[:3]]
            else:
                r, g, b = [max(0, min(255, int(v))) for v in c[:3]]
            return f"#{r:02X}{g:02X}{b:02X}"
    elif isinstance(c, int):
        return f"#{c:06X}"
    elif isinstance(c, str):
        c_str = c.strip().lstrip('#')
        if len(c_str) == 6:
            return f"#{c_str.upper()}"
        elif len(c_str) == 8:
            return f"#{c_str[2:].upper()}"
    return None

def match_borders(cell_bbox: tuple, lines: list[dict]) -> tuple[str|None, str|None, str|None, str|None]:
    """
    检查页面绘图中的矢量线条是否与单元格边缘对齐。
    返回 (左, 右, 上, 下) 边框样式。
    """
    if not cell_bbox:
        return None, None, None, None
        
    cx0, cy0, cx1, cy1 = cell_bbox
    
    def get_style(width: float) -> str:
        if width < 0.7:
            return 'thin'
        if width <= 1.5:
            return 'medium'
        return 'thick'
        
    left, right, top, bottom = None, None, None, None
    
    for line in lines:
        width = line.get('width', 1.0)
        # 支持 bbox 或 pts
        if 'bbox' in line:
            lx, ty, rx, by = line['bbox']
        elif 'pts' in line and len(line['pts']) >= 2:
            p0, p1 = line['pts'][0], line['pts'][-1]
            lx, rx = min(p0[0], p1[0]), max(p0[0], p1[0])
            ty, by = min(p0[1], p1[1]), max(p0[1], p1[1])
        else:
            continue
            
        # 快速空间剔除：如果线条完全在 cell bbox 外，跳过
        if rx < cx0 - EPSILON or lx > cx1 + EPSILON or by < cy0 - EPSILON or ty > cy1 + EPSILON:
            continue

        is_h = abs(by - ty) <= EPSILON
        is_v = abs(rx - lx) <= EPSILON
        
        if is_h:
            y = (ty + by) / 2
            # 检查上边框
            if abs(y - cy0) <= EPSILON and rx >= cx0 - EPSILON and lx <= cx1 + EPSILON:
                top = get_style(width)
            # 检查下边框
            if abs(y - cy1) <= EPSILON and rx >= cx0 - EPSILON and lx <= cx1 + EPSILON:
                bottom = get_style(width)
                
        if is_v:
            x = (lx + rx) / 2
            # 检查左边框
            if abs(x - cx0) <= EPSILON and by >= cy0 - EPSILON and ty <= cy1 + EPSILON:
                left = get_style(width)
            # 检查右边框
            if abs(x - cx1) <= EPSILON and by >= cy0 - EPSILON and ty <= cy1 + EPSILON:
                right = get_style(width)
                
        # 4 边已全部找到时快速退出
        if top and bottom and left and right:
            break
                
    return left, right, top, bottom

def match_fill(cell_bbox: tuple, fills: list[dict]) -> str | None:
    """
    检查页面绘图中的填充矩形是否覆盖此单元格。
    返回填充颜色的十六进制字符串或 None。
    """
    if not cell_bbox:
        return None
        
    cx0, cy0, cx1, cy1 = cell_bbox
    cell_area = (cx1 - cx0) * (cy1 - cy0)
    if cell_area <= 0:
        return None
        
    for fill in fills:
        fx0, fy0, fx1, fy1 = fill.get('bbox', (0,0,0,0))
        
        # 计算相交面积
        ix0, iy0 = max(cx0, fx0), max(cy0, fy0)
        ix1, iy1 = min(cx1, fx1), min(cy1, fy1)
        
        if ix1 > ix0 and iy1 > iy0:
            inter_area = (ix1 - ix0) * (iy1 - iy0)
            if inter_area / cell_area > 0.7:
                color = _normalize_color(fill.get('color'))
                if color:
                    # 忽略接近纯白背景色
                    if color.upper() not in ('#FFFFFF', '#FEFEFE', '#FDFDFD'):
                        return color
    return None

def build_cell_style(
    cell_bbox: tuple,
    text_bbox: tuple | None,
    spans: list[dict],
    lines: list[dict],
    fills: list[dict],
    is_numeric: bool = False,
) -> CellStyle:
    """综合所有样式推断结果到单一 CellStyle。"""
    font_name, font_size, bold, italic, font_color = None, None, False, False, None
    
    if spans:
        # 使用最长文本的 span 来决定主要字体样式
        primary_span = max(spans, key=lambda s: len(s.get('text', '')))
        font_name, font_size, bold, italic, font_color = parse_font_style(primary_span)
        
    horz, vert = infer_alignment(cell_bbox, text_bbox, is_numeric)
    left_b, right_b, top_b, bottom_b = match_borders(cell_bbox, lines)
    fill_color = match_fill(cell_bbox, fills)
    
    return CellStyle(
        font_name=font_name,
        font_size=font_size,
        bold=bold,
        italic=italic,
        font_color=font_color,
        fill_color=fill_color,
        horizontal=horz,
        vertical=vert,
        border_left=left_b,
        border_right=right_b,
        border_top=top_b,
        border_bottom=bottom_b
    )
