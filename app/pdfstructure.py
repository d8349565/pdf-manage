"""PDF 安全信息与页面重组时的导航保留，不修改已打开的源文档。"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from urllib.parse import quote

import pymupdf


@dataclass(frozen=True)
class PdfSafety:
    encrypted: bool
    owner_authenticated: bool
    permissions: int
    signature: str  # none / present / unknown；不代表证书验签结果
    special_structures: tuple[str, ...]

    def allows(self, permission: int) -> bool:
        return self.owner_authenticated or bool(self.permissions & permission)

    @property
    def read_only_reason(self) -> str:
        if self.signature == "present":
            return "检测到数字签名，未验证有效性 · 原件只读"
        if self.signature == "unknown":
            return "无法确认签名结构 · 为保护原件，以只读方式打开"
        if not self.allows(pymupdf.PDF_PERM_MODIFY | pymupdf.PDF_PERM_ASSEMBLE):
            return "当前密码不允许修改或整理页面 · 只读"
        return ""


def _present(doc, xref, key):
    kind, value = doc.xref_get_key(xref, key)
    return kind != "null" and value not in ("", "null", "[]", "<<>>")


def signature_state(doc) -> str:
    """检查已填写的签名值；空白 /Sig 字段本身不构成已签名。"""
    if doc.is_encrypted:
        return "unknown"
    uncertain = False
    blank_fields = False
    try:
        for xref in range(1, doc.xref_length()):
            field_type = doc.xref_get_key(xref, "FT")[1]
            object_type = doc.xref_get_key(xref, "Type")[1]
            has_byte_range = _present(doc, xref, "ByteRange")
            if object_type in ("/Sig", "/DocTimeStamp") or has_byte_range:
                contents = doc.xref_get_key(xref, "Contents")[1]
                if contents not in ("", "null") and contents.strip("\x00 <>0") and has_byte_range:
                    return "present"
                uncertain |= has_byte_range
            if field_type == "/Sig":
                if _present(doc, xref, "V"):
                    uncertain = True
                else:
                    blank_fields = True
        # 模板可能预设 SigFlags=3，但只有空白字段时不能据此判定已签名。
        uncertain |= doc.get_sigflags() == 3 and not blank_fields
        uncertain |= _present(doc, doc.pdf_catalog(), "Perms/DocMDP")
    except Exception:
        return "unknown"
    return "unknown" if uncertain else "none"


def inspect_safety(doc, authentication: int) -> PdfSafety:
    encrypted = _present(doc, -1, "Encrypt")
    special = []
    for key, label in (
        ("AcroForm/XFA", "XFA 动态表单"),
        ("AcroForm/CO", "表单计算关系"),
        ("Collection", "PDF 文件包"),
        ("OCProperties", "可选内容图层"),
        ("StructTreeRoot", "无障碍标签结构"),
        ("Names/JavaScript", "文档脚本"),
        ("OpenAction", "文档打开动作"),
        ("AA", "文档附加动作"),
        ("Threads", "文章阅读线索"),
    ):
        if _present(doc, doc.pdf_catalog(), key):
            special.append(label)
    return PdfSafety(encrypted, not encrypted or bool(authentication & 4),
                     doc.permissions, signature_state(doc), tuple(special))


def page_pdf_matrix(page):
    # 与固定版本 PyMuPDF insert_pdf 使用同一 MuPDF 坐标变换。
    # get_links 的坐标包含旋转和 CropBox，不能直接套不含旋转的 transformation_matrix。
    mupdf = pymupdf.mupdf
    matrix = mupdf.FzMatrix()
    mupdf.pdf_page_transform(mupdf.pdf_page_from_fz_page(page.this), mupdf.FzRect(0), matrix)
    return pymupdf.Matrix(matrix)


def _destination(doc, destination):
    result = dict(destination)
    result.pop("xref", None)
    result.pop("id", None)
    if result.get("kind") == pymupdf.LINK_NAMED:
        name = result.get("nameddest") or result.get("name")
        resolved = doc.resolve_link("#nameddest=" + quote(name or "", safe=""))
        if resolved and resolved[0] >= 0:
            result.update(kind=pymupdf.LINK_GOTO, page=resolved[0],
                          to=pymupdf.Point(resolved[1:3]))
        else:
            raise ValueError("存在无法解析的命名跳转")
    if result.get("kind") == pymupdf.LINK_GOTO:
        page_no = result.get("page", -1)
        if 0 <= page_no < doc.page_count:
            result["to"] = pymupdf.Point(result.get("to", (0, 0))) * ~page_pdf_matrix(doc[page_no])
    return result


def capture_navigation(doc, page_numbers, *, allow_structure_loss=False, warnings=None):
    warnings = warnings if warnings is not None else []
    toc = []
    for level, title, page_no, dest in doc.get_toc(False):
        try:
            destination = _destination(doc, dest)
        except ValueError:
            if not allow_structure_loss:
                raise
            destination = dict(dest, kind=pymupdf.LINK_NONE)
            warnings.append("无法解析的书签保留标题并取消跳转，请核对副本。")
        toc.append([level, title, page_no, destination])
    links = {}
    page_objects = {}
    for number in set(page_numbers):
        page = doc[number]
        captured = []
        inverse = ~page_pdf_matrix(page)
        for link in page.get_links():
            try:
                item = _destination(doc, link)
                if item.get("kind") not in (pymupdf.LINK_GOTO, pymupdf.LINK_GOTOR,
                                            pymupdf.LINK_URI, pymupdf.LINK_LAUNCH):
                    raise ValueError("存在无法重建的链接动作")
            except ValueError:
                if not allow_structure_loss:
                    raise
                warnings.append("无法解析的命名链接已从副本移除。")
                continue
            item["from"] = pymupdf.Rect(link["from"]) * inverse
            captured.append(item)
        raw_links = sum(kind == pymupdf.PDF_ANNOT_LINK for _, kind, _ in page.annot_xrefs())
        if len(captured) != raw_links:
            if not allow_structure_loss:
                raise ValueError("存在无法完整解析的链接，不能保证保存完整")
            warnings.append("无法完整解析的链接已从副本移除，请核对。")
        links[number] = captured
        page_objects[number] = page_object_signature(page)
    return {"toc": toc, "links": links, "labels": doc.get_page_labels(),
            "objects": page_objects}


def page_object_signature(page):
    annotations = sorted(annot.type[0] for annot in (page.annots() or ()))
    widgets = sorted((widget.field_name or "", widget.field_type,
                      str(widget.field_value)) for widget in (page.widgets() or ()))
    return (annotations, widgets)


def namespace_form_fields(doc, used_names, source_number, page_numbers):
    roots = set()
    for number in set(page_numbers):
        for widget in doc[number].widgets() or ():
            root = widget.xref
            visited = set()
            while root not in visited:
                visited.add(root)
                kind, parent = doc.xref_get_key(root, "Parent")
                if kind != "xref":
                    break
                root = int(parent.split()[0])
            else:
                raise ValueError("表单字段的父级结构包含循环")
            roots.add(root)
    renamed = 0
    for root in sorted(roots):
        kind, name = doc.xref_get_key(root, "T")
        if kind != "string":
            raise ValueError("表单字段缺少可识别的名称")
        candidate = name
        suffix = 1
        while candidate in used_names:
            candidate = f"来源{source_number}_{suffix}::{name}"
            suffix += 1
        if candidate != name:
            doc.xref_set_key(root, "T", pymupdf.get_pdf_str(candidate))
            renamed += 1
        used_names.add(candidate)
    return renamed


def copy_document_properties(out, documents, warnings):
    first = next(iter(documents.values()))
    out.set_metadata({key: value for key, value in first.metadata.items()
                      if key not in ("format", "encryption")})
    xml = first.get_xml_metadata()
    if xml:
        out.set_xml_metadata(xml)
    if len(documents) > 1:
        warnings.append("合并文件的文档属性采用第一个来源；各来源的附件分别保留。")
    used_names = set()
    for ordinal, doc in enumerate(documents.values(), 1):
        for name in doc.embfile_names():
            candidate = name
            suffix = 1
            while candidate in used_names:
                candidate = f"来源{ordinal}_{suffix}_{name}"
                suffix += 1
            info = doc.embfile_info(name)
            out.embfile_add(candidate, doc.embfile_get(name), filename=info.get("filename"),
                            ufilename=info.get("ufilename"), desc=info.get("description"))
            used_names.add(candidate)
            if candidate != name:
                warnings.append(f"同名附件已改名为「{candidate}」。")


def restore_navigation(out, pages, snapshots, warnings, merged):
    mappings = {}
    for index, ref in enumerate(pages):
        mappings.setdefault(ref.source_id, {}).setdefault(ref.source_page, index)
    toc = []
    toc_targets = []
    removed_links = 0
    inactive_bookmarks = 0
    for ordinal, (sid, snapshot) in enumerate(snapshots.items(), 1):
        mapping = mappings[sid]
        if merged and snapshot["toc"]:
            toc.append([1, f"来源 {ordinal}", min(mapping.values()) + 1])
            toc_targets.append(None)
        for level, title, old_page, original in snapshot["toc"]:
            destination = dict(original)
            kind = destination.get("kind", pymupdf.LINK_NONE)
            target = None
            new_page = -1
            if kind == pymupdf.LINK_GOTO:
                old_target = destination.get("page", old_page - 1)
                target = mapping.get(old_target)
                if target is None:
                    destination["kind"] = pymupdf.LINK_NONE
                    inactive_bookmarks += 1
                else:
                    new_page = target + 1
                    destination["page"] = target
            raw_destination = dict(destination)
            destination.pop("to", None)
            destination["kind"] = pymupdf.LINK_NONE
            toc.append([level + int(merged), title, new_page, destination])
            toc_targets.append((target, raw_destination))
    out.set_toc(toc, collapse=0)
    for entry, target_info in zip(out.get_toc(False), toc_targets):
        if target_info is None:
            continue
        target, destination = target_info
        xref = entry[3]["xref"]
        action = pymupdf.utils.getDestStr(out.page_xref(target) if target is not None else 0, destination)
        if action.startswith("/A"):
            out.xref_set_key(xref, "A", action[2:])
    labels = []
    has_labels = any(snapshot["labels"] for snapshot in snapshots.values())
    for index, ref in enumerate(pages):
        page = out[index]
        for xref, kind, _ in page.annot_xrefs():
            if kind == pymupdf.PDF_ANNOT_LINK:
                page.delete_link({"xref": xref})
        page = out.reload_page(page)
        snapshot = snapshots[ref.source_id]
        for original in snapshot["links"][ref.source_page]:
            link = dict(original)
            link["from"] = link["from"] * page.transformation_matrix
            if link.get("kind") == pymupdf.LINK_GOTO:
                target = mappings[ref.source_id].get(link.get("page", -1))
                if target is None:
                    removed_links += 1
                    continue
                link["page"] = target
                link["to"] = link.get("to", pymupdf.Point(0, 0)) * out[target].transformation_matrix
            page.insert_link(link)
        out.reload_page(page)
        if has_labels:
            rules = snapshot["labels"]
            starts = [rule["startpage"] for rule in rules]
            rule_index = bisect_right(starts, ref.source_page) - 1
            if rule_index >= 0:
                rule = dict(rules[rule_index])
                rule["firstpagenum"] = rule.get("firstpagenum", 1) + ref.source_page - rule["startpage"]
                rule["startpage"] = index
            else:
                rule = {"startpage": index, "prefix": "", "style": "D", "firstpagenum": index + 1}
            labels.append(rule)
    if has_labels:
        out.set_page_labels(labels)
    if removed_links:
        warnings.append(f"已移除 {removed_links} 个指向未保留页面的链接。")
    if inactive_bookmarks:
        warnings.append(f"{inactive_bookmarks} 个书签的目标页未保留，标题仍在，已取消跳转。")


def document_signature(doc):
    """写入前后检查结构及值，避免写盘成功却少了页面对象。"""
    result = []
    for page in doc:
        links = []
        inverse = ~page_pdf_matrix(page)
        for item in page.get_links():
            rect = pymupdf.Rect(item["from"]) * inverse
            links.append((item.get("kind"), item.get("page"), item.get("uri"),
                          item.get("file"), item.get("nameddest"), item.get("name"),
                          tuple(round(v, 3) for v in rect)))
        result.append((page.rotation, round(page.rect.width, 3), round(page.rect.height, 3),
                       page_object_signature(page), links))
    toc = [(row[0], row[1], row[2], row[3].get("kind")) for row in doc.get_toc(False)]
    attachments = [(name, doc.embfile_info(name).get("size")) for name in doc.embfile_names()]
    return (result, toc, doc.get_page_labels(), attachments)
