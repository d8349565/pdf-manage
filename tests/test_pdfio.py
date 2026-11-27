from __future__ import annotations

import os

import pymupdf
import pytest
from PyQt6.QtWidgets import QApplication

from app.pdfio import NeedPassword, PdfError, PdfStore, apply_output_conflicts, is_encrypted_file
from app.workspace import Workspace, parse_range_segments


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def _make_pdf(path: str, n: int, label: str) -> None:
    doc = pymupdf.open()
    for i in range(n):
        page = doc.new_page(width=300, height=400)
        page.insert_text((40, 80), f"{label}-{i + 1}", fontsize=24)
    doc.save(path)
    doc.close()


@pytest.fixture
def late_repair_source(tmp_path, request):
    source = tmp_path / "source.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 80), "Original page")
        old = page.add_text_annot((40, 100), "Before editing")
        old_id = old.info["id"]
        private = doc.get_new_xref()
        doc.update_object(private, "<< /Label (example) >>")
        doc.xref_set_key(doc.pdf_catalog(), "PrivateInfo", f"{private} 0 R")
        password = getattr(request, "param", None)
        encryption = dict(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                          permissions=pymupdf.PDF_PERM_ANNOTATE | pymupdf.PDF_PERM_MODIFY,
                          owner_pw="owner", user_pw=password) if password else {}
        raw = doc.tobytes(**encryption)
    # A reachable object is only loaded when the writer visits the whole file.
    offset = raw.index(f"{private} 0 obj".encode())
    entry = f"{offset:010d} 00000 n".encode()
    assert raw.count(entry) == 1
    source.write_bytes(raw.replace(entry, b"0000000009 00000 n"))
    return source, old_id, password


@pytest.mark.parametrize("late_repair_source", [None, "reader"], indirect=True)
def test_save_annotations_after_late_index_repair(tmp_path, late_repair_source):
    from app.pdfworker import write_pdf
    from app.workspace import PageEdit

    source, old_id, password = late_repair_source
    original = source.read_bytes()
    copy = tmp_path / "notes.pdf"
    store = PdfStore()
    try:
        ref, = store.load(str(source), password=password)
        ref = ref.edited(PageEdit("note_text", annotation_id=old_id, text="Updated note"))
        ref = ref.edited(PageEdit("note", ((120, 160),), text="新批注", annotation_id="new-note"))
        ref = ref.edited(PageEdit("freetext", (50, 200, 250, 230), text="See 5.1.8",
                                  color=(1, 0, 0), annotation_id="red-text",
                                  details=(("font", "Helvetica"), ("fontsize", 16))))
        with pytest.raises(PdfError, match="新文件名"):
            write_pdf(store, [ref], str(source))
        assert source.read_bytes() == original
        result = write_pdf(store, [ref], str(copy), register_result=True)
        assert result.pages[0].page_id == ref.page_id
        with pymupdf.open(copy) as saved:
            assert saved.needs_pass == bool(password)
            if password:
                assert saved.authenticate(password)
                assert saved.permissions & pymupdf.PDF_PERM_ANNOTATE
                assert not saved.permissions & pymupdf.PDF_PERM_COPY
            assert not saved.is_repaired
            page = saved[0]
            assert "Original page" in page.get_text()
            assert [(a.info["id"], a.info["content"]) for a in page.annots()] == [
                (old_id, "Updated note"), ("new-note", "新批注"), ("red-text", "See 5.1.8")]
            pixels = page.get_pixmap(clip=pymupdf.Rect(50, 200, 250, 230)).samples
            assert any(r > g + 80 and r > b + 80 for r, g, b in zip(pixels[::3], pixels[1::3], pixels[2::3]))
            assert saved.xref_get_key(saved.pdf_catalog(), "PrivateInfo/Label") == ("string", "example")
        assert source.read_bytes() == original
    finally:
        store.close_all()


def test_recovery_refuses_same_count_with_lost_note_text(tmp_path, late_repair_source, monkeypatch):
    from app import pdfedits
    from app.workspace import PageEdit

    source, old_id, _ = late_repair_source
    original = source.read_bytes()
    dest = tmp_path / "incomplete.pdf"
    apply_edits = pdfedits.apply_edits

    def lose_edit_on_replay(page, edits, **kwargs):
        return False if page.parent.is_repaired else apply_edits(page, edits, **kwargs)

    monkeypatch.setattr(pdfedits, "apply_edits", lose_edit_on_replay)
    store = PdfStore()
    try:
        ref, = store.load(str(source))
        ref = ref.edited(PageEdit("note_text", annotation_id=old_id, text="Updated note"))
        with pytest.raises(PdfError, match="本次编辑不符"):
            store.write([ref], str(dest))
        assert not dest.exists() and not list(tmp_path.glob(".pdfasm-*"))
        assert source.read_bytes() == original
    finally:
        store.close_all()


def test_external_change_distinguishes_own_save_from_replaced_file(qapp, tmp_path):
    source, replacement = tmp_path / 'source.pdf', tmp_path / 'replacement.pdf'
    _make_pdf(str(source), 1, 'Original')
    store = PdfStore()
    try:
        pages = store.load(str(source))
        assert not store.has_external_change(str(source))
        store.write(pages, str(source))
        assert not store.has_external_change(str(source))
        _make_pdf(str(replacement), 2, 'Changed externally')
        os.replace(replacement, source)
        assert store.has_external_change(str(source))
    finally:
        store.close_all()


def _make_rotated_pdf(path, rotations: list[int], w: int = 300, h: int = 400) -> None:
    """每页按给定 /Rotate 写盘，模拟扫描件、手机拍的照片这类自带旋转的文件。"""
    doc = pymupdf.open()
    for i, rot in enumerate(rotations):
        page = doc.new_page(width=w, height=h)
        page.insert_text((40, 80), f"R-{i + 1}", fontsize=24)
        if rot:
            page.set_rotation(rot)
    doc.save(path)
    doc.close()


@pytest.mark.parametrize(("payload", "stroke", "rgb"), [
    ("null", "[0.5]", (.5, .5, .5)),
    ("[]", "[0 1 1 0]", (1, 0, 0)),
    ('"text"', "[0 0 1]", (0, 0, 1)),
])
def test_text_box_with_non_object_settings_uses_default_style(payload, stroke, rgb):
    from app.pdfmarks import text_settings
    with pymupdf.open() as doc:
        page = doc.new_page()
        annot = page.add_freetext_annot((30, 40, 160, 72), "text")
        doc.xref_set_key(annot.xref, "ZhiXuText", pymupdf.get_pdf_str(payload))
        doc.xref_set_key(annot.xref, "C", stroke)
        settings = text_settings(page, annot)
        assert settings["fontsize"] == 12
        assert tuple(settings["color"]) == rgb


def test_text_box_preview_preserves_underlying_form(qapp, tmp_path):
    from app.workspace import PageEdit
    source = tmp_path / "field.pdf"
    area = (30, 40, 160, 72)
    with pymupdf.open() as doc:
        page = doc.new_page()
        field = pymupdf.Widget()
        field.field_name = "name"
        field.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        field.field_value = "Field value"
        field.fill_color = (1, 1, 0)
        field.rect = pymupdf.Rect(area)
        page.add_widget(field)
        annot = page.add_freetext_annot(area, "moving")
        identifier = annot.info["id"]
        doc.save(source)
    store = PdfStore()
    try:
        ref, = store.load(str(source))
        actual, _ = store.annotation_move_preview(ref, identifier, area, 1)
        expected = store.render_region(ref.edited(PageEdit("delete", annotation_id=identifier)), area, 1)
        assert actual == expected
        assert actual[0].pixelColor(3, 3).blue() < 20
    finally:
        store.close_all()


def _loaded_workspace(store: PdfStore, path) -> Workspace:
    ws = Workspace()
    ws.open_replace(store.load(str(path)), str(path))
    return ws


@pytest.mark.parametrize("source_rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("app_rotation", [0, 90, 180, 270])
def test_export_orientation_matches_preview(qapp, tmp_path, source_rotation, app_rotation):
    """写出页的朝向必须等于预览口径：源文件自带的 /Rotate 加上本次旋转。"""
    src = tmp_path / "scan.pdf"
    _make_rotated_pdf(src, [source_rotation])
    store = PdfStore()
    ws = _loaded_workspace(store, src)
    ws.select_index(0)
    for _ in range(app_rotation // 90):
        ws.rotate(90)
    ref = ws.pages[0]
    out = tmp_path / "out.pdf"
    store.write([ref], str(out))
    with pymupdf.open(out) as doc:
        assert doc[0].rotation == (source_rotation + app_rotation) % 360
        assert (doc[0].rect.width, doc[0].rect.height) == store.page_size(ref)
    store.close_all()


def test_write_keeps_source_rotation_when_user_did_not_rotate(qapp, tmp_path):
    src = tmp_path / "scan.pdf"
    _make_rotated_pdf(src, [90])
    store = PdfStore()
    ws = _loaded_workspace(store, src)
    assert store.source_rotation(ws.pages[0]) == 90
    out = tmp_path / "one.pdf"
    store.write(ws.pages, str(out))
    with pymupdf.open(out) as doc:
        assert doc[0].rotation == 90
    store.close_all()


def test_split_each_file_keeps_its_own_rotation(qapp, tmp_path):
    src = tmp_path / "mixed.pdf"
    _make_rotated_pdf(src, [0, 90])
    store = PdfStore()
    pages = store.load(str(src))
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    dests = [str(out_dir / f"src_{i + 1}.pdf") for i in range(2)]
    store.write_groups(pages, [[0], [1]], dests)
    assert pymupdf.open(dests[0])[0].rotation == 0
    assert pymupdf.open(dests[1])[0].rotation == 90
    store.close_all()


def test_save_overwrite_and_undo_still_reads_original(qapp, tmp_path):
    src = tmp_path / "合同.pdf"
    _make_pdf(src, 3, "P")
    store = PdfStore()
    ws = Workspace()
    ws.open_replace(store.load(str(src)), str(src))
    ws.select_index(2)
    ws.move_page(2, 0)
    ws.rotate(90)
    store.write(ws.pages, str(src))
    saved = store.load(str(src))
    ws.rebase_to_saved_file(str(src), saved)
    assert not ws.dirty
    with pymupdf.open(src) as written:
        assert written.page_count == 3
        assert written[0].rotation == 90
    ws.undo_stack.undo()
    ws.undo_stack.undo()
    assert ws.dirty
    store.close_all()


def test_merge_does_not_rewrite_sources(qapp, tmp_path):
    a = tmp_path / "A.pdf"
    b = tmp_path / "B.pdf"
    _make_pdf(a, 1, "A")
    _make_pdf(b, 1, "B")
    a_bytes = a.read_bytes()
    b_bytes = b.read_bytes()
    store = PdfStore()
    ws = Workspace()
    ws.open_replace(store.load(str(a)), str(a))
    ws.merge_pages(store.load(str(b)))
    assert ws.path == str(a)
    out = tmp_path / "合订.pdf"
    store.write(ws.pages, str(out))
    assert a.read_bytes() == a_bytes
    assert b.read_bytes() == b_bytes
    assert pymupdf.open(out).page_count == 2
    store.close_all()


def test_split_uses_workspace_including_deletes(qapp, tmp_path):
    src = tmp_path / "src.pdf"
    _make_pdf(src, 3, "S")
    store = PdfStore()
    ws = Workspace()
    ws.open_replace(store.load(str(src)), str(src))
    ws.select_index(1)
    ws.delete_selected()
    assert ws.dirty
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    groups = [[i] for i in range(len(ws.pages))]
    dests = [str(out_dir / f"src_{i + 1}.pdf") for i in range(len(groups))]
    store.write_groups(ws.pages, groups, dests)
    assert len(list(out_dir.glob("*.pdf"))) == 2
    assert ws.dirty
    assert ws.path.endswith("src.pdf")
    store.close_all()


def test_extract_does_not_change_workspace(qapp, tmp_path):
    src = tmp_path / "src.pdf"
    _make_pdf(src, 3, "S")
    store = PdfStore()
    ws = Workspace()
    ws.open_replace(store.load(str(src)), str(src))
    ws.select_index(0)
    ws.rotate(90)
    before = (tuple(ws.pages), ws.path, ws.dirty)
    dest = tmp_path / "extract.pdf"
    store.write([ws.pages[i] for i in ws.extract_indices("")], str(dest))
    assert (tuple(ws.pages), ws.path, ws.dirty) == before
    store.close_all()


def test_split_range_segments(qapp, tmp_path):
    src = tmp_path / "src.pdf"
    _make_pdf(src, 5, "S")
    store = PdfStore()
    pages = store.load(str(src))
    groups = parse_range_segments("1-2,5", 5)
    dests = [str(tmp_path / f"p{i}.pdf") for i in range(len(groups))]
    store.write_groups(pages, groups, dests)
    assert pymupdf.open(dests[0]).page_count == 2
    assert pymupdf.open(dests[1]).page_count == 1
    store.close_all()


def test_split_conflict_modes(tmp_path):
    existing = tmp_path / "x_1.pdf"
    existing.write_bytes(b"%PDF")
    dests = [str(tmp_path / "x_1.pdf"), str(tmp_path / "x_2.pdf")]
    groups = [[0], [1]]
    assert apply_output_conflicts(groups, dests, "cancel") is None
    over = apply_output_conflicts(groups, dests, "overwrite")
    assert over is not None and over[1] == dests
    skipped = apply_output_conflicts(groups, dests, "skip")
    assert skipped is not None
    assert skipped[1] == [str(tmp_path / "x_2.pdf")]
    only = apply_output_conflicts([[0]], [str(existing)], "skip")
    assert only is None


def test_is_encrypted_file(qapp, tmp_path):
    plain = tmp_path / "plain.pdf"
    _make_pdf(str(plain), 1, "P")
    enc = tmp_path / "enc.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(enc, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="owner")
    doc.close()
    assert not is_encrypted_file(str(plain))
    assert is_encrypted_file(str(enc))
    assert not is_encrypted_file(str(tmp_path / "missing.pdf"))


def test_encrypted_needs_password(qapp, tmp_path):
    path = tmp_path / "enc.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((40, 80), "secret")
    doc.save(
        path,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="pw",
        owner_pw="owner",
    )
    doc.close()
    store = PdfStore()
    with pytest.raises(NeedPassword):
        store.load(str(path))
    pages = store.load(str(path), "pw")
    assert len(pages) == 1
    store.close_all()


def test_write_failure_leaves_no_temp(qapp, tmp_path):
    store = PdfStore()
    with pytest.raises(PdfError):
        store.write([], str(tmp_path / "x.pdf"))
    leftovers = list(tmp_path.glob(".pdfasm-*"))
    assert leftovers == []
    store.close_all()


def test_render_qimage_not_null(qapp, tmp_path):
    src = tmp_path / "page.pdf"
    _make_pdf(src, 1, "P")
    store = PdfStore()
    pages = store.load(str(src))
    image = store.render_qimage(pages[0], 120)
    assert not image.isNull()
    assert image.width() > 0
    store.close_all()


@pytest.mark.parametrize("source_rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("app_rotation", [0, 90, 180, 270])
def test_region_matches_pdf_render_with_crop_rotation_and_annotations(qapp, tmp_path, source_rotation, app_rotation):
    src = tmp_path / "region.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=300, height=400)
        page.insert_text((50, 70), "Small VECTOR text", fontsize=9)
        page.draw_rect((30, 80, 260, 320), color=(0, 0, 1), fill=(1, 0.8, 0.2))
        page.add_rect_annot((100, 150, 200, 250)).update()
        page.set_cropbox(pymupdf.Rect(25, 30, 285, 385))
        page.set_rotation(source_rotation)
        doc.save(src)
    store = PdfStore()
    ref = store.load(str(src))[0].rotated(app_rotation)
    width, height = store.page_size(ref)
    full = store.render_qimage(ref, int(width * 4))
    image, (x, y), scale = store.render_region(ref, (20, 30, width - 15, height - 10), 4)
    assert scale == 4
    expected = full.copy(round(x * 4), round(y * 4), image.width(), image.height())
    assert image == expected
    assert store.source_rotation(ref) == source_rotation
    store.close_all()
