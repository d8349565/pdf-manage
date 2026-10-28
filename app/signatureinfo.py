"""Read-only signature metadata; no certificate or cryptographic validation."""
from dataclasses import dataclass
import re

import pymupdf

from app.output import check_cancel


@dataclass(frozen=True)
class SignatureInfo:
    xref: int
    name: str
    status: str
    signer: str
    signed_at: str
    reason: str
    location: str
    method: str
    pages: tuple[int, ...]
    note: str


def collect_signature_info(doc, progress=None, cancel=None):
    """Describe source objects without changing the store's protection decision."""
    pdf = pymupdf._as_pdf_document(doc)
    fields, values, issues = {}, set(), []
    field_candidates = set()
    total = doc.xref_length()
    for xref in range(1, total):
        check_cancel(cancel)
        if progress and xref % 256 == 0:
            progress(xref, total, '正在读取签名结构')
        entry = pymupdf.mupdf.ll_pdf_get_xref_entry_no_change(pdf.m_internal, xref)
        if entry is None or entry.type in ('\x00', 'f'):
            continue
        try:
            if (doc.xref_get_key(xref, 'FT')[0] != 'null'
                    or doc.xref_get_key(xref, 'T')[0] != 'null'
                    or (doc.xref_get_key(xref, 'Subtype')[1] == '/Widget'
                        and doc.xref_get_key(xref, 'V')[0] != 'null')):
                field_candidates.add(xref)
            if (doc.xref_get_key(xref, 'Type')[1] in ('/Sig', '/DocTimeStamp')
                    or doc.xref_get_key(xref, 'ByteRange')[0] != 'null'):
                values.add(xref)
        except Exception:
            issues.append(f'对象 {xref} 无法读取，签名列表可能不完整。')

    def ancestors(xref):
        visited = set()
        while xref:
            check_cancel(cancel)
            if xref in visited:
                raise ValueError('字段父级引用循环')
            visited.add(xref)
            yield xref
            kind, value = doc.xref_get_key(xref, 'Parent')
            xref = int(value.split()[0]) if kind == 'xref' else 0

    def inherited(xref, key):
        for owner in ancestors(xref):
            kind, value = doc.xref_get_key(owner, key)
            if kind != 'null':
                return kind, value, owner
        return 'null', 'null', xref

    for field in sorted(field_candidates):
        check_cancel(cancel)
        try:
            if inherited(field, 'FT')[1] != '/Sig':
                continue
            kind, kids = doc.xref_get_key(field, 'Kids')
            if kind == 'array' and any(int(child) in field_candidates
                                      for child in re.findall(r'(\d+)\s+\d+\s+R', kids)):
                continue  # A field container is not a separate blank signature.
            fields[field] = inherited(field, 'V')
        except Exception:
            check_cancel(cancel)
            issues.append(f'字段 {field} 的类型或签名值继承关系无法读取。')

    # One signature value may be shared by a field and several visible widgets.
    owners = {}
    for field, (kind, value, _) in fields.items():
        if kind == 'xref':
            owners.setdefault(int(value.split()[0]), []).append(field)
    values.update(owners)
    page_map = {}
    if fields:
        for index in range(doc.page_count):
            check_cancel(cancel)
            if progress:
                progress(index + 1, doc.page_count, '正在定位签名所在页')
            try:
                for annot, *_ in doc.page_annot_xrefs(index):
                    node, visited = annot, set()
                    while node and node not in visited:
                        visited.add(node)
                        if node in fields:
                            page_map.setdefault(node, set()).add(index)
                            break
                        kind, value = doc.xref_get_key(node, 'Parent')
                        node = int(value.split()[0]) if kind == 'xref' else 0
            except Exception:
                check_cancel(cancel)
                issues.append(f'第 {index + 1} 页的签名位置无法完整读取。')

    def text(xref, key):
        kind, value = doc.xref_get_key(xref, key)
        return value[:4096] if kind in ('string', 'name') else ''

    rows = []
    candidates = [(xref, '', owners.get(xref, []), False) for xref in sorted(values)]
    candidates += [(owner, 'V/' if kind == 'dict' else '', [field], kind == 'null')
                   for field, (kind, _, owner) in fields.items() if kind != 'xref' and field not in values]
    for xref, prefix, field_refs, blank in candidates:
        check_cancel(cancel)
        names = []
        for field in field_refs:
            try:
                parts = [text(node, 'T') for node in ancestors(field)]
                names.append('.'.join(part for part in reversed(parts) if part) or f'签名字段 {field}')
            except Exception:
                check_cancel(cancel)
                names.append(f'签名字段 {field}')
        pages = tuple(sorted({page for field in field_refs for page in page_map.get(field, ())}))
        name = '、'.join(names) or f'签名对象 {xref}'
        try:
            byte_range = doc.xref_get_key(xref, prefix + 'ByteRange')[0] != 'null'
            contents = doc.xref_get_key(xref, prefix + 'Contents')[1]
            filled = contents not in ('', 'null') and bool(contents.strip('\x00 <>0'))
            status = '空白签名字段' if blank else ('已填写 · 未验签' if byte_range and filled else '结构无法确认')
            note = ('尚未填写数字签名。' if blank else
                    '已读取签名数据，但未核验签名有效性。' if byte_range and filled else
                    '签名值缺失、类型不受支持，或缺少完整的签名数据与覆盖范围。')
            if prefix:
                note += ' 此字段内嵌签名值；是否允许编辑仍以原件保护检查为准。'
            if status == '结构无法确认':
                issues.append(f'{name}：{note}')
            rows.append(SignatureInfo(xref, name, status, text(xref, prefix + 'Name'),
                text(xref, prefix + 'M'), text(xref, prefix + 'Reason'),
                text(xref, prefix + 'Location'), text(xref, prefix + 'SubFilter'), pages, note))
        except Exception:
            note = '签名值引用的对象无法读取，不能确认其结构。'
            issues.append(f'{name}：{note}')
            rows.append(SignatureInfo(xref, name, '结构无法确认', '', '', '', '', '', pages, note))
    try:
        if doc.get_sigflags() == 3 and not fields and not values:
            issues.append('文档声明含签名，但未找到对应的签名字段或签名数据。')
        if doc.xref_get_key(doc.pdf_catalog(), 'Perms/DocMDP')[0] != 'null':
            issues.append('文档含认证签名引用；本面板未验证其允许的修改范围。')
    except Exception:
        issues.append('文档级签名标记无法完整读取。')
    return tuple(rows), tuple(dict.fromkeys(issues))
