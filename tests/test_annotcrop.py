"""批注裁剪（矩形框批注 → 矢量裁剪导出）的行为测试。"""

import pymupdf as p
import pytest
from PyQt6.QtWidgets import QApplication

from app.annotcrop import (AnnotationCropError, collect_markers, layout_flow,
                           layout_per_region, export_annotation_crop)
from app.pdfio import PdfStore
from app.workspace import PageEdit

# 批注 /Rect 会按边框宽度向外扩约 1 磅，裁剪取整条边框，因此断言留 2 磅容差
RECT = 2


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def make_annotated_pdf(path, *, rotation=0, hidden=False, two_marks=False, pages=1):
    """生成测试 PDF：每页带 TARGET 文字与矩形批注标记。"""
    with p.open() as doc:
        for number in range(pages):
            page = doc.new_page(width=600, height=400)
            page.insert_text((100, 50), f"TARGET{number}", fontsize=20)
            if number == 0:
                page.insert_text((400, 350), "OTHER", fontsize=20)
            mark = page.add_rect_annot(p.Rect(80, 20, 220, 70))
            mark.set_colors(stroke=(1, 0, 0))
            mark.update()
            if hidden:
                mark.set_flags(mark.flags | p.PDF_ANNOT_IS_HIDDEN)
                mark.update()
            if two_marks and number == 0:
                second = page.add_rect_annot(p.Rect(380, 320, 480, 380))
                second.update()
            page.set_rotation(rotation)
        doc.save(path)
    return path


def export(tmp_path, name, options, *, rotation=0, hidden=False, two_marks=False, pages=1,
           edits=None, source=None):
    """跑一次导出，返回 (store, pages, 结果 dict, 输出路径)。"""
    if source is None:
        source = make_annotated_pdf(tmp_path / "source.pdf", rotation=rotation, hidden=hidden,
                                    two_marks=two_marks, pages=pages)
    store = PdfStore()
    try:
        pages = store.load(str(source))
        if edits:
            pages = [pages[0].edited(edit) for edit in edits]
        dest = tmp_path / f"{name}.pdf"
        result = export_annotation_crop(store, pages, options, str(dest))
    finally:
        store.close_all()
    return dest, result


def test_collect_markers_reads_square_annotations(tmp_path):
    source = make_annotated_pdf(tmp_path / "source.pdf")
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        with store._render_page(ref) as page:
            markers = collect_markers(page)
    finally:
        store.close_all()
    assert len(markers) == 1
    assert tuple(markers[0]) == pytest.approx((80, 20, 220, 70), abs=RECT)


def test_collect_markers_skips_hidden_annotations(tmp_path):
    source = make_annotated_pdf(tmp_path / "source.pdf", hidden=True)
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        with store._render_page(ref) as page:
            assert collect_markers(page) == []
    finally:
        store.close_all()


def test_collect_markers_skips_watermarks(tmp_path):
    source = tmp_path / "water.pdf"
    with p.open() as doc:
        page = doc.new_page(width=600, height=400)
        mark = page.add_rect_annot(p.Rect(80, 20, 220, 70))
        mark.update()
        doc.xref_set_key(mark.xref, "NM", "(zhixu-watermark-mark-1)")
        doc.xref_set_key(mark.xref, "ZhiXuWatermark", "(1)")
        doc.save(source)
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        with store._render_page(ref) as page:
            assert collect_markers(page) == []
    finally:
        store.close_all()


def test_collect_markers_ignores_other_annotation_types(tmp_path):
    source = tmp_path / "highlights.pdf"
    with p.open() as doc:
        page = doc.new_page(width=600, height=400)
        page.insert_text((100, 50), "TARGET", fontsize=20)
        highlight = page.add_highlight_annot(p.Rect(95, 30, 200, 60))
        highlight.update()
        doc.save(source)
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        with store._render_page(ref) as page:
            assert collect_markers(page) == []
    finally:
        store.close_all()


def test_layout_per_region_sizes_pages_to_regions():
    regions = [p.Rect(0, 0, 100, 50), p.Rect(0, 0, 30, 80)]
    layouts = layout_per_region(regions)
    assert [(round(w), round(h)) for w, h, _ in layouts] == [(100, 50), (30, 80)]
    assert [placements[0][0] for _, _, placements in layouts] == [0, 1]


def test_layout_flow_uses_a4_and_fills_from_top():
    regions = [p.Rect(0, 0, 200, 100), p.Rect(0, 0, 200, 100)]
    pages = layout_flow(regions)
    assert len(pages) == 1
    page_w, page_h, placements = pages[0]
    assert (round(page_w), round(page_h)) == (595, 842)
    first, second = placements[0][1], placements[1][1]
    # 左上原点、y 向下：第一块贴页顶留边，第二块在它右边
    assert first.y0 == pytest.approx(36, abs=1)
    assert second.x0 > first.x1
    assert second.y0 == pytest.approx(first.y0, abs=1)


def test_layout_flow_starts_new_row_and_new_page():
    wide = [p.Rect(0, 0, 400, 100)] * 2
    rows = layout_flow(wide)[0][2]
    assert rows[0][1].y0 == pytest.approx(36, abs=1)
    assert rows[1][1].x0 == pytest.approx(36, abs=1)
    assert rows[1][1].y0 > rows[0][1].y1

    many = [p.Rect(0, 0, 500, 700)] * 3
    pages = layout_flow(many)
    assert len(pages) == 3
    assert all(len(placements) == 1 for _, _, placements in pages)


def test_export_per_region_keeps_vector_text_and_drops_marker(tmp_path):
    dest, result = export(tmp_path, "per", dict(source="annot", layout="per", expand=False))
    assert result["regions"] == 1 and result["out_pages"] == 1
    assert result["path"] == str(dest)
    with p.open(dest) as doc:
        page = doc[0]
        assert page.rect.width == pytest.approx(140, abs=RECT)
        assert page.rect.height == pytest.approx(50, abs=RECT)
        text = page.get_text()
        assert "TARGET" in text and "OTHER" not in text
        assert page.first_annot is None  # 标记批注不进输出


def test_export_flow_layout_puts_region_on_a4(tmp_path):
    dest, result = export(tmp_path, "flow", dict(source="annot", layout="flow", expand=False))
    assert result["out_pages"] == 1
    with p.open(dest) as doc:
        page = doc[0]
        assert page.rect.width == pytest.approx(595, abs=1)
        assert page.rect.height == pytest.approx(842, abs=1)
        assert "TARGET" in page.get_text()
        assert result["regions"] == 1


def test_export_margin_source_needs_no_annotations(tmp_path):
    plain = tmp_path / "plain.pdf"
    with p.open() as doc:
        page = doc.new_page(width=600, height=400)
        page.insert_text((100, 50), "TARGET", fontsize=20)
        doc.save(plain)
    dest, result = export(tmp_path, "margin", dict(source="margin", layout="per", expand=False),
                          source=plain)
    assert result["regions"] == 1
    with p.open(dest) as doc:
        page = doc[0]
        assert page.rect.width == pytest.approx(528, abs=RECT)
        assert page.rect.height == pytest.approx(328, abs=RECT)
        assert "TARGET" in page.get_text()


def test_export_expand_merges_marks_on_same_page(tmp_path):
    dest, result = export(tmp_path, "expand",
                          dict(source="annot", layout="per", expand=True), two_marks=True)
    assert result["regions"] == 1
    with p.open(dest) as doc:
        page = doc[0]
        assert page.rect.width == pytest.approx(400, abs=RECT)
        assert page.rect.height == pytest.approx(360, abs=RECT)
        text = page.get_text()
        assert "TARGET" in text and "OTHER" in text


def test_export_keeps_marks_separate_without_expand(tmp_path):
    _, result = export(tmp_path, "separate",
                       dict(source="annot", layout="per", expand=False), two_marks=True)
    assert result["regions"] == 2 and result["out_pages"] == 2


def test_export_respects_page_range(tmp_path):
    dest, result = export(tmp_path, "range", dict(source="annot", layout="per", expand=False,
                                                 range="2"), pages=2)
    assert result["regions"] == 1
    with p.open(dest) as doc:
        assert "TARGET1" in doc[0].get_text()


def test_export_without_regions_raises(tmp_path):
    plain = tmp_path / "plain.pdf"
    with p.open() as doc:
        doc.new_page(width=600, height=400)
        doc.save(plain)
    store = PdfStore()
    try:
        pages = store.load(str(plain))
        with pytest.raises(AnnotationCropError):
            export_annotation_crop(store, pages, dict(source="annot"), str(tmp_path / "out.pdf"))
    finally:
        store.close_all()


def test_export_rejects_unknown_options(tmp_path):
    source = make_annotated_pdf(tmp_path / "source.pdf")
    store = PdfStore()
    try:
        pages = store.load(str(source))
        with pytest.raises(AnnotationCropError):
            export_annotation_crop(store, pages, dict(source="guess"), str(tmp_path / "a.pdf"))
        with pytest.raises(AnnotationCropError):
            export_annotation_crop(store, pages, dict(layout="guess"), str(tmp_path / "b.pdf"))
    finally:
        store.close_all()


def test_export_normalizes_rotated_pages(tmp_path):
    dest, result = export(tmp_path, "rot90", dict(source="annot", layout="per", expand=False),
                          rotation=90)
    assert result["regions"] == 1
    with p.open(dest) as doc:
        page = doc[0]
        # 页面旋转 90° 后，标记区域宽高互换
        assert page.rect.width == pytest.approx(50, abs=RECT)
        assert page.rect.height == pytest.approx(140, abs=RECT)
        assert "TARGET" in page.get_text()


def test_uses_unsaved_annotation_edits(tmp_path):
    # 未保存的矩形批注（页面编辑稿）也能作为裁剪标记：源文件本身不带标记
    source = tmp_path / "plain.pdf"
    with p.open() as doc:
        page = doc.new_page(width=600, height=400)
        page.insert_text((100, 50), "TARGET", fontsize=20)
        page.insert_text((400, 350), "OTHER", fontsize=20)
        doc.save(source)
    edit = PageEdit("rect", (80, 20, 220, 70), color=(1, 0, 0), width=2)
    dest, result = export(tmp_path, "draft", dict(source="annot", layout="per", expand=False),
                          edits=[edit], source=source)
    assert result["regions"] == 1
    with p.open(dest) as doc:
        page = doc[0]
        assert "TARGET" in page.get_text() and "OTHER" not in page.get_text()
        assert page.first_annot is None


def test_dialog_defaults_to_all_pages_flow_annot(qapp):
    from app.annotcropui import AnnotationCropDialog
    dialog = AnnotationCropDialog(5, [1, 3])
    try:
        options = dialog.options()
        assert options == dict(source="annot", layout="flow", expand=False, range="")
        assert dialog.indices() == [0, 1, 2, 3, 4]
        assert dialog.start.isEnabled()
    finally:
        dialog.deleteLater()


def test_dialog_selected_and_range_modes(qapp):
    from app.annotcropui import AnnotationCropDialog
    dialog = AnnotationCropDialog(5, [1, 3])
    try:
        dialog.selected_mode.setChecked(True)
        assert dialog.options()["range"] == "2,4"
        assert dialog.indices() == [1, 3]

        dialog.range_mode.setChecked(True)
        dialog.range.setText("1-2,5")
        assert dialog.options()["range"] == "1-2,5"
        assert dialog.indices() == [0, 1, 4]

        dialog.range.setText("9")
        assert not dialog.start.isEnabled()
        assert dialog.feedback.text()
    finally:
        dialog.deleteLater()


def test_dialog_margin_and_per_page_options(qapp):
    from app.annotcropui import AnnotationCropDialog
    dialog = AnnotationCropDialog(3, [])
    try:
        dialog.source_margin.setChecked(True)
        dialog.layout_per.setChecked(True)
        dialog.expand.setChecked(True)
        options = dialog.options()
        assert options["source"] == "margin"
        assert options["layout"] == "per"
        assert options["expand"] is True
        assert not dialog.selected_mode.isEnabled()  # 没有选中页时不可用
    finally:
        dialog.deleteLater()
