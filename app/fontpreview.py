"""Adapt OpenType CID fonts for MuPDF previews without changing source PDFs."""
from io import BytesIO
import re
import struct


def cff_identity_map(data, unicode_for_gid=None):
    """Translate OpenType glyph indices to the CIDs expected by FreeType."""
    if not data.startswith(b"OTTO"):
        return None
    from fontTools.ttLib import TTFont, TTLibError
    try:
        with TTFont(BytesIO(data), lazy=True) as font:
            if "CFF " not in font:
                return None
            top = font["CFF "].cff.topDictIndex[0]
            if not hasattr(top, "ROS"):
                return None
            charset = top.charset
            if not charset or charset[0] != ".notdef" or len(charset) > 65536:
                return None
            if any(not name.startswith("cid") or not name[3:].isdigit() for name in charset[1:]):
                return None
            cids = [0, *(int(name[3:]) for name in charset[1:])]
            if any(cid > 65535 for cid in cids) or all(gid == cid for gid, cid in enumerate(cids)):
                return None
            if unicode_for_gid is not None:
                by_gid = {}
                for codepoint, name in (font.getBestCmap() or {}).items():
                    by_gid.setdefault(font.getGlyphID(name), set()).add(codepoint)
                confirmed = False
                for gid, codepoints in by_gid.items():
                    codepoint = unicode_for_gid(gid)
                    if codepoint < 0:
                        continue
                    if codepoint not in codepoints or gid >= len(cids):
                        return None
                    confirmed |= cids[gid] != gid
                if not confirmed:
                    return None
            return struct.pack(f">{len(cids)}H", *cids)
    except (TTLibError, ValueError, KeyError, IndexError, AssertionError, EOFError, struct.error):
        # Unrecognised or damaged fonts retain the normal renderer's behaviour.
        return None


def page_font_maps(page, checked):
    """Return preview-only mappings for explicitly identity-mapped CFF fonts."""
    doc = page.parent
    mappings = []
    for xref, extension, kind, *_ in page.get_fonts(full=True):
        if not xref or xref in checked:
            continue
        checked.add(xref)
        if extension != "otf" or kind != "Type0":
            continue
        if doc.xref_get_key(xref, "Encoding")[1] not in ("/Identity-H", "/Identity-V"):
            continue
        value_kind, value = doc.xref_get_key(xref, "DescendantFonts")
        if value_kind == "xref":
            value = doc.xref_object(int(value.split()[0]))
        match = re.fullmatch(r"\[\s*(\d+)\s+\d+\s+R\s*\]", value)
        if match is None:
            continue
        descendant = int(match[1])
        if (doc.xref_get_key(descendant, "Subtype")[1] != "/CIDFontType0"
                or doc.xref_get_key(descendant, "CIDToGIDMap")[1] != "/Identity"):
            continue
        if doc.xref_get_key(xref, "ToUnicode")[0] != "xref":
            continue
        import pymupdf
        pdf = pymupdf._as_pdf_document(doc)
        font_object = pymupdf.mupdf.pdf_load_object(pdf, xref)
        unicode_ref = pymupdf.mupdf.pdf_dict_gets(font_object, "ToUnicode")
        try:
            cmap = pymupdf.mupdf.pdf_load_embedded_cmap(pdf, unicode_ref)
            mapping = cff_identity_map(doc.extract_font(xref)[3],
                lambda gid: pymupdf.mupdf.pdf_lookup_cmap(cmap, gid))
        except (RuntimeError, pymupdf.mupdf.FzErrorBase):
            continue
        if mapping is not None:
            mappings.append((descendant, mapping))
    return mappings
