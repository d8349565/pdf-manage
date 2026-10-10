"""Freeze signature appearances only; never flatten unrelated form widgets."""
from __future__ import annotations

import math
import pymupdf

from app.output import check_cancel


def freeze_signatures(doc, progress=None, cancel=None):
    m = pymupdf.mupdf
    pdf = pymupdf._as_pdf_document(doc)
    for index in range(doc.page_count):
        check_cancel(cancel)
        page = doc[index]
        # Retain ordinary fields, annotations, links and the original vector appearance.
        widgets = list(page.widgets(types=[pymupdf.PDF_WIDGET_TYPE_SIGNATURE]))
        for widget in widgets:
            annot = widget._annot.this
            ap = m.pdf_annot_ap(annot)
            ap_xref = m.pdf_to_num(ap)
            flags = m.pdf_annot_flags(annot)
            if not widget.rect.is_empty and not (flags & (1 | 2 | 32)):
                if ap_xref <= 0 or not doc.xref_is_stream(ap_xref):
                    raise ValueError(f"第 {index + 1} 页签名缺少可冻结的外观，已停止制作副本。")
                matrix = pymupdf.Matrix(m.pdf_annot_transform(annot))
                if not all(math.isfinite(v) for v in matrix) or abs(matrix.a * matrix.d - matrix.b * matrix.c) < 1e-12:
                    raise ValueError("签名外观坐标无法可靠转换，已停止制作副本。")
                pdfpage = m.pdf_page_from_fz_page(page.this)
                resources = m.pdf_dict_get_inheritable(pdfpage.obj(), pymupdf.PDF_NAME("Resources"))
                resources = m.pdf_copy_dict(resources) if m.pdf_is_dict(resources) else m.pdf_new_dict(pdf, 1)
                xobjects = m.pdf_dict_get(resources, pymupdf.PDF_NAME("XObject"))
                xobjects = m.pdf_copy_dict(xobjects) if m.pdf_is_dict(xobjects) else m.pdf_new_dict(pdf, 1)
                name = f"PageArkSignature{widget.xref}"
                while m.pdf_dict_gets(xobjects, name).m_internal:
                    name += "_"
                m.pdf_dict_puts(xobjects, name, ap)
                m.pdf_dict_put(resources, pymupdf.PDF_NAME("XObject"), xobjects)
                # A private resource dictionary avoids changing pages sharing /Resources.
                reference = m.pdf_add_object(pdf, resources)
                m.pdf_dict_put(pdfpage.obj(), pymupdf.PDF_NAME("Resources"), reference)
                page.wrap_contents()
                command = "q\n" + " ".join(f"{v:.9g}" for v in matrix) + f" cm\n/{name} Do\nQ\n"
                pymupdf.TOOLS._insert_contents(page, command.encode("ascii"), True)
            page.delete_widget(widget)
        if progress:
            progress(index + 1, doc.page_count, "正在冻结签名外观，保留其他表单")
    catalog = doc.pdf_catalog()
    for key in ("Perms", "DSS", "AcroForm/SigFlags"):
        doc.xref_set_key(catalog, key, "null")
    for xref in range(1, doc.xref_length()):
        check_cancel(cancel)
        kind = doc.xref_get_key(xref, "Type")[1]
        if kind in ("/Sig", "/DocTimeStamp") or doc.xref_get_key(xref, "ByteRange")[0] != "null":
            doc.update_object(xref, "<<>>")
        elif doc.xref_get_key(xref, "FT")[1] == "/Sig":
            doc.xref_set_key(xref, "V", "null")
