from __future__ import annotations
import pytest
from app.pdf2excel.models import CellStyle, CellModel, TableModel

def test_cell_style():
    style = CellStyle(
        font_name="Arial",
        bold=True,
        horizontal="center",
        vertical="top"
    )
    assert style.font_name == "Arial"
    assert style.bold is True
    assert style.horizontal_alignment == "center"
    assert style.vertical_alignment == "top"

def test_cell_model():
    cell = CellModel(
        row=0,
        col=1,
        text="Test",
        value="Test",
    )
    assert cell.row == 0
    assert cell.col == 1
    assert cell.text == "Test"
    assert cell.value == "Test"
    assert isinstance(cell.style, CellStyle)
    assert cell.row_span == 1
    assert cell.col_span == 1

def test_table_model():
    cell = CellModel(row=0, col=0, text="A1")
    table = TableModel(
        page_no=1,
        bbox=(0.0, 0.0, 100.0, 100.0),
        row_count=1,
        col_count=1,
        cells=[cell],
        row_heights=[20.0],
        col_widths=[50.0]
    )
    assert table.page_no == 1
    assert table.bbox == (0.0, 0.0, 100.0, 100.0)
    assert len(table.cells) == 1
    assert table.cells[0].text == "A1"
    assert table.row_heights[0] == 20.0
