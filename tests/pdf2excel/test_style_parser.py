from __future__ import annotations
import pytest
from app.pdf2excel.style_parser import clean_font_name, infer_alignment, match_borders, match_fill

def test_clean_font_name():
    assert clean_font_name("ABCDEF+SimSun") == ("宋体", False, False)
    assert clean_font_name("Arial-BoldMT") == ("Arial", True, False)
    assert clean_font_name("UnknownFont-Bold") == ("UnknownFont", True, False)
    assert clean_font_name("TimesNewRomanPS-ItalicMT") == ("Times New Roman", False, True)

def test_infer_alignment():
    cell_bbox = (0, 0, 100, 20)
    # text exactly in middle
    text_bbox = (40, 5, 60, 15)
    h, v = infer_alignment(cell_bbox, text_bbox)
    assert h == "center"
    assert v == "center"

    # text on left
    text_bbox_left = (5, 5, 45, 15)
    h_l, v_l = infer_alignment(cell_bbox, text_bbox_left)
    assert h_l == "left"
    
    # numeric should default to right
    h_num, v_num = infer_alignment(cell_bbox, None, is_numeric=True)
    assert h_num == "right"

def test_match_borders():
    cell_bbox = (10, 10, 50, 30)
    lines = [
        {"bbox": (10, 10, 50, 10), "width": 0.5},  # top thin
        {"bbox": (10, 30, 50, 30), "width": 1.0},  # bottom medium
        {"bbox": (10, 10, 10, 30), "width": 2.0},  # left thick
        {"bbox": (50, 10, 50, 30), "width": 0.5},  # right thin
    ]
    left, right, top, bottom = match_borders(cell_bbox, lines)
    assert left == "thick"
    assert right == "thin"
    assert top == "thin"
    assert bottom == "medium"

def test_match_fill():
    cell_bbox = (10, 10, 50, 30)
    fills = [
        {"bbox": (10, 10, 50, 30), "color": "#FF0000"}
    ]
    assert match_fill(cell_bbox, fills) == "#FF0000"
    
    # no fill
    assert match_fill(cell_bbox, []) is None
    
    # white fill should be ignored
    fills_white = [{"bbox": (10, 10, 50, 30), "color": "#FFFFFF"}]
    assert match_fill(cell_bbox, fills_white) is None
