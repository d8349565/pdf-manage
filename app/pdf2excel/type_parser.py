from __future__ import annotations
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation


def _safe_number(raw: str, percent: bool = False) -> int | float | None:
    """仅转换可在 Excel 的 15 位有效数字内往返保存的值。"""
    try:
        value = Decimal(raw.replace(',', ''))
        if percent:
            value = value.scaleb(-2)
        if not value.is_finite() or len(value.as_tuple().digits) > 15:
            return None
        number = float(value)
        if Decimal(format(number, '.15g')) != value:
            return None
        return int(value) if value == value.to_integral_value() else number
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _decimal_format(raw: str, grouped: bool = False) -> str:
    places = len(raw.rsplit('.', 1)[1]) if '.' in raw else 0
    return ('#,##0' if grouped else '0') + ('.' + '0' * places if places else '')

def parse_value(text: str) -> tuple[object, str | None]:
    """将文本解析为强类型值。返回 (解析后的值, Excel数字格式或None)。"""
    text = text.strip()
    
    # 1. 空/空白处理
    if not text:
        return '', None
        
    # 2. 前导零保护：例如 '00123'
    if re.match(r'^0\d+$', text):
        return text, None

    # 预处理括号括起来的负数，如 (123.45) -> -123.45
    process_text = text
    if process_text.startswith('(') and process_text.endswith(')'):
        inner = process_text[1:-1].strip()
        # 仅当括号内包含数字时才将其作为负数处理
        if re.search(r'\d', inner):
            process_text = '-' + inner

    # 8. 货币处理: ¥123.45, $123.45
    currency_match = re.match(r'^(-?)([$¥￥])\s*(\d+(?:\.\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?)$', process_text)
    if currency_match:
        sign = currency_match.group(1)
        symbol = currency_match.group(2)
        num_str = currency_match.group(3)
        clean_num = sign + num_str.replace(',', '')
        try:
            val = _safe_number(clean_num)
            if val is None:
                return text, None
            # 指定对应的 Excel 货币格式
            if symbol in ('¥', '￥'):
                prefix = '[$¥-804]'
            else:
                prefix = '[$$-409]'
            digits = _decimal_format(num_str, grouped=True)
            fmt = f'{prefix}{digits};[RED]-{prefix}{digits}'
            return val, fmt
        except ValueError:
            pass # 回退到普通处理

    # 3. 整数
    if re.match(r'^-?\d{1,15}$', process_text):
        try:
            return int(process_text), None
        except ValueError:
            return text, None
            
    # 千位分隔符整数 (部分 9)
    if re.match(r'^-?\d{1,3}(,\d{3})+$', process_text):
        try:
            val = _safe_number(process_text)
            return (val, '#,##0') if val is not None else (text, None)
        except ValueError:
            pass

    # 4 & 9. 小数和带有千位分隔符的小数
    if re.match(r'^-?\d+\.\d+$', process_text) or re.match(r'^-?\d{1,3}(,\d{3})+\.\d+$', process_text):
        has_comma = ',' in process_text
        try:
            val = _safe_number(process_text)
            if val is None:
                return text, None
            return val, _decimal_format(process_text, grouped=has_comma)
        except ValueError:
            pass

    # 5. 百分比
    pct_match = re.match(r'^(-?\d+\.?\d*)%$', process_text)
    if pct_match:
        try:
            num_str = pct_match.group(1)
            val = _safe_number(num_str, percent=True)
            if val is None:
                return text, None
            fmt = _decimal_format(num_str) + '%'
            return val, fmt
        except ValueError:
            pass

    # 6. 日期 (尝试常见格式)
    # V1: 优先 YYYY 格式，接着是 MM/DD/YYYY
    if any(c in process_text for c in ('-', '/', '年')):
        date_formats = [
            '%Y-%m-%d',
            '%Y/%m/%d',
            '%Y年%m月%d日',
            '%Y年%m月%d',
            '%m/%d/%Y',  # 美式
            '%d/%m/%Y'   # 欧式
        ]
        for fmt in date_formats:
            try:
                dt = datetime.strptime(process_text, fmt)
                # 基本范围验证以避免误判
                if 1900 <= dt.year <= 2100:
                    return dt, 'yyyy-mm-dd'
            except ValueError:
                continue

    # 10. 其他情况：作为字符串原样返回
    return text, None
