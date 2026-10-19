"""Generate sparse CID fonts; never depend on the user's sample or fonts."""
from io import BytesIO
import struct

import pymupdf
import pytest
from fontTools.cffLib import FDArrayIndex, FDSelect, FontDict
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen
from PyQt6.QtCore import QRect

from app.fontpreview import cff_identity_map
from app.pdfio import PdfStore, _to_qimage
from app.workspace import PageEdit


def cid_font(cids=(0, 5, 9)):
    names = [".notdef", *(f"cid{cid:05d}" for cid in cids[1:])]
    builder = FontBuilder(1000, isTTF=False)
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap({ord("甲"): names[1], ord("乙"): names[2]})
    builder.setupHorizontalMetrics({name: (1000, 0) for name in names})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "GeneratedCID", "styleName": "Regular",
                            "psName": "GeneratedCID-Regular"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    charstrings = {}
    for index, name in enumerate(names):
        pen = T2CharStringPen(1000, None)
        if index:
            pen.moveTo((100, 100))
            pen.lineTo((100 + index * 200, 100))
            pen.lineTo((100 + index * 200, 700))
            pen.lineTo((100, 700))
            pen.closePath()
        charstrings[name] = pen.getCharString()
    builder.setupCFF("GeneratedCID-Regular", {}, charstrings, {})
    top = builder.font["CFF "].cff.topDictIndex[0]
    top.ROS = ("Adobe", "Identity", 0)
    top.CIDCount = max(cids) + 1
    font_dict = FontDict()
    font_dict.Private = top.Private
    top.FDArray = FDArrayIndex()
    top.FDArray.append(font_dict)
    top.FDSelect = FDSelect()
    top.FDSelect.gidArray = [0] * len(names)
    top.CharStrings.fdArray = top.FDArray
    top.CharStrings.fdSelect = top.FDSelect
    for charstring in charstrings.values():
        charstring.fdSelectIndex = 0
    del top.Private
    output = BytesIO()
    builder.font.save(output)
    return output.getvalue()


def cid_pdf(path, *, cids=(0, 5, 9), encrypted=False):
    with pymupdf.open() as doc:
        page = doc.new_page(width=240, height=180)
        xref = page.insert_font(fontname="GeneratedCID", fontbuffer=cid_font(cids))
        page.insert_text((20, 60), "甲乙", fontsize=32, fontname="GeneratedCID")
        descendant = int(doc.xref_get_key(xref, "DescendantFonts")[1].split()[0][1:])
        doc.xref_set_key(descendant, "CIDToGIDMap", "/Identity")
        options = dict(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="reader", owner_pw="owner") if encrypted else {}
        doc.save(path, **options)


def test_sparse_cff_mapping_and_unsupported_fonts():
    assert cff_identity_map(cid_font()) == struct.pack(">3H", 0, 5, 9)
    assert cff_identity_map(cid_font(), lambda gid: {5: ord("甲"), 9: ord("乙")}.get(gid, -1)) is None
    assert cff_identity_map(cid_font(), lambda gid: ord("错")) is None
    assert cff_identity_map(cid_font((0, 1, 2))) is None
    assert cff_identity_map(pymupdf.Font("cjk").buffer) is None
    assert cff_identity_map(b"OTTO damaged font") is None


@pytest.mark.parametrize("encrypted", [False, True])
def test_cff_preview_matches_identity_font_without_changing_source(tmp_path, encrypted):
    source = tmp_path / "sparse.pdf"
    reference = tmp_path / "identity.pdf"
    cid_pdf(source, encrypted=encrypted)
    cid_pdf(reference, cids=(0, 1, 2))
    before = source.read_bytes()
    with pymupdf.open(reference) as doc:
        expected = _to_qimage(doc[0].get_pixmap(alpha=False))
    store = PdfStore()
    try:
        ref = store.load(str(source), "reader" if encrypted else None)[0]
        source_handle = store._source(ref.source_id)
        assert source_handle.preview_doc is None
        assert store.render_qimage(ref, 240) == expected
        preview = source_handle.preview_doc
        assert preview is not None and preview is not source_handle.doc
        xref_count = preview.xref_length()
        assert store.render_qimage(ref, 240) == expected
        assert preview.xref_length() == xref_count
        assert store.search_page(ref, "甲乙")[0]
        snapshot = store.text_snapshot(ref)
        text = "".join(char["c"] for block in snapshot["raw"]["blocks"] if block["type"] == 0
                       for line in block["lines"] for span in line["spans"] for char in span["chars"])
        assert text == "甲乙"
        edited = ref.edited(PageEdit(kind="rect", points=(20, 120, 80, 160)))
        assert store.render_qimage(edited, 240).copy(QRect(0, 0, 240, 100)) == expected.copy(QRect(0, 0, 240, 100))
        region, (x, y), scale = store.render_region(ref, (0, 0, 240, 100), 1)
        assert region == expected.copy(round(x), round(y), region.width(), region.height())
        assert source_handle.data == before
        with pymupdf.open(stream=source_handle.data, filetype="pdf") as original:
            if encrypted:
                original.authenticate("reader")
            xref = original[0].get_fonts()[0][0]
            descendant = int(original.xref_get_key(xref, "DescendantFonts")[1].split()[0][1:])
            assert original.xref_get_key(descendant, "CIDToGIDMap")[1] == "/Identity"
    finally:
        store.close_all()
    assert preview.is_closed
    assert source.read_bytes() == before


def test_explicit_cid_mapping_is_used_without_a_preview_copy(tmp_path):
    source = tmp_path / "mapped.pdf"
    cid_pdf(source)
    with pymupdf.open(source) as doc:
        xref = doc[0].get_fonts()[0][0]
        descendant = int(doc.xref_get_key(xref, "DescendantFonts")[1].split()[0][1:])
        mapping = doc.get_new_xref()
        doc.update_object(mapping, "<<>>")
        doc.update_stream(mapping, struct.pack(">3H", 0, 5, 9))
        doc.xref_set_key(descendant, "CIDToGIDMap", f"{mapping} 0 R")
        data = doc.tobytes()
    source.write_bytes(data)
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        with pymupdf.open(source) as doc:
            expected = _to_qimage(doc[0].get_pixmap(alpha=False))
        assert store.render_qimage(ref, 240) == expected
        assert store._source(ref.source_id).preview_doc is None
    finally:
        store.close_all()
