"""Focused all-content export checks; run only when simulation tests are requested."""
from app.pdf2excel.excel_builder import build_workbook
from app.pdf2excel.models import CellModel, CellStyle, TableModel


def test_page_content_keeps_text_tables_and_blank_pages():
    table = TableModel(
        page_no=4, bbox=(50, 70, 250, 130), row_count=2, col_count=2,
        cells=[CellModel(0, 0, text='Merged header', col_span=2,
                         style=CellStyle(bold=True, fill_color='#336699')),
               CellModel(1, 0, text='00123', value='00123'),
               CellModel(1, 1, text='12.30', value=12.3, number_format='0.00')],
        row_heights=[30, 30], col_widths=[100, 100],
    )
    pages = [
        dict(page_no=4, width=300, height=400, items=[
            dict(kind='text', text='Title before table', bbox=(50, 20, 250, 40), size=14),
            dict(kind='table', table=table, bbox=table.bbox),
            dict(kind='text', text='=Original note', bbox=(50, 150, 250, 170), size=11),
        ]),
        dict(page_no=7, width=300, height=400, items=[
            dict(kind='text', text='No table, still exported', bbox=(30, 20, 250, 40), size=11),
        ]),
        dict(page_no=8, width=300, height=400, items=[]),
    ]
    workbook = build_workbook([table], pages=pages)
    try:
        assert workbook.sheetnames == ['第4页', '第7页', '第8页']
        ws = workbook.worksheets[0]
        cells = {cell.value: cell for row in ws for cell in row if cell.value is not None}
        assert cells['Title before table'].row < cells['Merged header'].row < cells['=Original note'].row
        assert cells['Merged header'].fill.fgColor.rgb.endswith('336699')
        assert cells['00123'].data_type == 's'
        assert cells[12.3].number_format == '0.00'
        assert cells['=Original note'].data_type == 's'
        assert any(cells['Merged header'].coordinate in merged for merged in ws.merged_cells.ranges)
        assert workbook.worksheets[1]['B2'].value == 'No table, still exported'
        assert workbook.worksheets[2].max_row == 1
        assert ws.max_row < 20
    finally:
        workbook.close()
