"""Watermark geometry and selective editing; generated documents only."""
import base64
import io

import pymupdf as p
import pytest
from PIL import Image

from app.pdfedits import apply_edits
from app.pdfmarks import WATERMARK_DATA, is_watermark, list_watermarks, watermark_base, watermark_id, watermark_layout
from app.workspace import PageEdit


def image_data():
    stream = io.BytesIO()
    Image.new("RGBA", (120, 60), (40, 120, 90, 180)).save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode("ascii")


def edit(identifier="zhixu-watermark-first", *, kind="watermark", image="", **options):
    settings = dict(size=50, angle=30, opacity=.15, position="center-center", tile=False, image=image)
    settings.update(options)
    return PageEdit(kind, text="仅供参考", color=(.4, .4, .4), annotation_id=identifier,
                    details=tuple(settings.items()))


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("angle", [0, 30, 90, -45])
@pytest.mark.parametrize("image", [False, True])
def test_saved_bounds_match_preview_after_stamp_update(rotation, angle, image):
    operation = edit(image=image_data() if image else "", rotation=rotation, angle=angle)
    options = dict(operation.details)
    with p.open() as doc:
        page = doc.new_page(width=420, height=600)
        page.set_rotation(rotation)
        _, bw, bh = watermark_base(operation.text, operation.color, options["image"])
        width, height, locations = watermark_layout(page.rect.width, page.rect.height, bw, bh, options)
        apply_edits(page, (operation,))
        data = doc.tobytes(garbage=4, deflate=True)
    with p.open(stream=data, filetype="pdf") as doc:
        page = doc[0]
        marks = [a for a in page.annots() if is_watermark(page, a)]
        assert len(marks) == len(locations)
        for annot, (x, y) in zip(marks, locations):
            visible = annot.rect * page.rotation_matrix
            assert tuple(visible) == pytest.approx((x, y, x + width, y + height), abs=.01)


@pytest.mark.parametrize("image", [False, True])
def test_reopened_tiled_watermark_keeps_settings_and_selective_update(image):
    first = edit(image=image_data() if image else "", tile=True, size=25, offset=(.2, .8))
    second = edit("zhixu-watermark-second", angle=-20)
    with p.open() as doc:
        page = doc.new_page(width=420, height=600)
        page.add_text_annot((30, 40), "Keep this note")
        apply_edits(page, (first, second))
        marks = [a for a in page.annots() if watermark_id(a) == first.annotation_id]
        assert len(marks) > 1
        assert len({doc.xref_get_key(a.xref, WATERMARK_DATA) for a in marks}) == 1
        data = doc.tobytes(garbage=4, deflate=True)
    with p.open(stream=data, filetype="pdf") as doc:
        page = doc[0]
        records = {entry["id"]: entry for entry in list_watermarks(page)}
        assert records[first.annotation_id]["settings"]["options"]["image"] == dict(first.details)["image"]
        assert records[first.annotation_id]["settings"]["options"]["offset"] == [.2, .8]
        replacement = edit("zhixu-watermark-replacement", kind="watermark_update", target=first.annotation_id,
                           size=40, angle=60)
        apply_edits(page, (replacement,))
        page = doc.reload_page(page)
        assert {entry["id"] for entry in list_watermarks(page)} == {replacement.annotation_id, second.annotation_id}
        apply_edits(page, (PageEdit("watermark_remove", annotation_id=replacement.annotation_id),))
        page = doc.reload_page(page)
        assert [entry["id"] for entry in list_watermarks(page)] == [second.annotation_id]
        assert any(a.info.get("content") == "Keep this note" for a in page.annots())


def test_legacy_watermark_settings_can_be_recovered():
    operation = edit(position="custom", offset=(.25, .7), angle=30)
    with p.open() as doc:
        page = doc.new_page(width=420, height=600)
        apply_edits(page, (operation,))
        page = doc.reload_page(page)
        for annot in page.annots():
            if is_watermark(page, annot):
                doc.xref_set_key(annot.xref, WATERMARK_DATA, "null")
        settings = list_watermarks(page)[0]["settings"]
        assert settings is not None
        assert settings["text"] == operation.text
        assert settings["options"]["angle"] == 30
        assert settings["options"]["offset"] == pytest.approx((.25, .7), abs=.01)
