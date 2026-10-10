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
    assert ws.path is None
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
