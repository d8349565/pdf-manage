from __future__ import annotations
import pytest
from datetime import datetime
from app.pdf2excel.type_parser import parse_value

def test_parse_value_empty():
    assert parse_value("") == ("", None)
    assert parse_value("   ") == ("", None)

def test_parse_value_leading_zero():
    assert parse_value("00123") == ("00123", None)

def test_parse_value_integer():
    assert parse_value("12345") == (12345, None)
    assert parse_value("-987") == (-987, None)

def test_parse_value_thousands_separator():
    assert parse_value("1,234") == (1234, "#,##0")
    assert parse_value("1,234,567.89") == (1234567.89, "#,##0.00")
    assert parse_value("-1,234.56") == (-1234.56, "#,##0.00")

def test_parse_value_float():
    assert parse_value("123.45") == (123.45, "0.00")
    assert parse_value("-0.99") == (-0.99, "0.00")

def test_parse_value_percentage():
    assert parse_value("12.5%") == (0.125, "0.0%")
    assert parse_value("50%") == (0.5, "0%")
    assert parse_value("-2.5%") == (-0.025, "0.0%")

def test_parse_value_date():
    dt, fmt = parse_value("2026-09-16")
    assert dt == datetime(2026, 9, 16)
    assert fmt == "yyyy-mm-dd"
    
    dt, fmt = parse_value("2026/09/16")
    assert dt == datetime(2026, 9, 16)
    
    dt, fmt = parse_value("2026年9月16日")
    assert dt == datetime(2026, 9, 16)

def test_parse_value_currency():
    assert parse_value("¥128.50")[0] == 128.50
    assert "[$¥" in parse_value("¥128.50")[1]
    
    assert parse_value("$99.99")[0] == 99.99
    assert "[$$" in parse_value("$99.99")[1]

def test_parse_value_negative_parentheses():
    assert parse_value("(123.45)") == (-123.45, "0.00")
    assert parse_value("(1,234)") == (-1234, "#,##0")

def test_parse_value_non_convertible():
    assert parse_value("Hello World") == ("Hello World", None)
    assert parse_value("123A") == ("123A", None)
