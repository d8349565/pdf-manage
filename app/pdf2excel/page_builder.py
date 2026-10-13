"""Write each PDF page's readable content without inventing data tables."""
from bisect import bisect_left, bisect_right
from io import BytesIO
from math import ceil

from openpyxl.drawing.image import Image
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.pdf2excel.excel_builder import _write_cells


def _table_edges(table):
    edges = [table.bbox[0]]
    widths = table.col_widths or [(table.bbox[2] - table.bbox[0]) / table.col_count] * table.col_count
    for width in widths:
        edges.append(edges[-1] + width)
    return edges


def _columns(page):
    tables = [item['table'] for item in page['items'] if item['kind'] == 'table']
    values = [0.0, page['width']]
    if tables:
        values.extend(edge for table in tables for edge in _table_edges(table))
    else:
        # A small fixed grid keeps ordinary text pages compact and editable.
        values.extend(page['width'] * column / 12 for column in range(1, 12))
    edges = []
    for value in sorted(values):
        if not edges or value - edges[-1] > .1:
            edges.append(value)
    return edges


def _height_rows(ws, start, height):
    count = max(1, ceil(height / 200))
    for row in range(start, start + count):
        ws.row_dimensions[row].height = max(18, height / count)
    return count


def write_page(wb, page):
    """Keep native table grids; place other blocks between them in reading order."""
    edges = _columns(page)
    last_column = len(edges) - 1
    ws = wb.create_sheet(title=f"第{page['page_no']}页")
    ws.sheet_view.showGridLines = False
    for column, (left, right) in enumerate(zip(edges, edges[1:]), start=1):
        ws.column_dimensions[get_column_letter(column)].width = max(.1, (right - left) / 5.625)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_column)
    header = ws.cell(1, 1, f"PDF 第 {page['page_no']} 页")
    header.font = Font(name='微软雅黑', size=10, bold=True, color='5A6571')
    header.fill = PatternFill(fill_type='solid', fgColor='EEF1F4')
    header.alignment = Alignment(vertical='center')
    ws.row_dimensions[1].height = 24
    ws.freeze_panes = 'A2'

    row = 2
    previous_bottom = None
    for item in page['items']:
        box = item['bbox']
        if previous_bottom is not None and box[1] - previous_bottom > 12:
            # Compress empty PDF space instead of creating a huge sparse worksheet.
            row += min(2, max(1, int((box[1] - previous_bottom) / 24)))
        if item['kind'] == 'table':
            table = item['table']
            columns = [min(range(len(edges)), key=lambda index: abs(edges[index] - edge)) + 1
                       for edge in _table_edges(table)]
            _write_cells(ws, table, row_offset=row - 1, columns=columns)
            for offset in range(table.row_count):
                height = table.row_heights[offset] if offset < len(table.row_heights) else 18
                ws.row_dimensions[row + offset].height = min(409, max(18, height))
            row += table.row_count
        elif item['kind'] == 'text':
            start = max(1, min(last_column, bisect_right(edges, box[0])))
            end = max(start, min(last_column, bisect_left(edges, box[2])))
            size = max(6, min(72, item.get('size', 11)))
            count = _height_rows(ws, row, max(box[3] - box[1] + 4,
                                             size * 1.4 * item.get('line_count', 1)))
            cell = ws.cell(row, start, item['text'].replace('\t', '    '))
            cell.data_type = 's'  # Text such as "=..." must never become a formula.
            cell.font = Font(name='微软雅黑', size=size, bold=item.get('bold', False))
            cell.alignment = Alignment(wrap_text=True, vertical='top')
            if end > start or count > 1:
                ws.merge_cells(start_row=row, start_column=start, end_row=row + count - 1,
                               end_column=end)
            row += count
        elif item['kind'] == 'image':
            image = Image(BytesIO(item['data']))
            image.width = (box[2] - box[0]) * 4 / 3
            image.height = (box[3] - box[1]) * 4 / 3
            column = max(1, min(last_column, bisect_right(edges, box[0])))
            ws.add_image(image, f'{get_column_letter(column)}{row}')
            row += _height_rows(ws, row, box[3] - box[1] + 4)
        previous_bottom = box[3]

    ws.print_title_rows = '1:1'
    ws.print_area = f'A1:{get_column_letter(last_column)}{max(1, row - 1)}'
    ws.page_setup.orientation = 'landscape' if page['width'] > page['height'] else 'portrait'
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.oddFooter.center.text = f"原 PDF 第 {page['page_no']} 页"
