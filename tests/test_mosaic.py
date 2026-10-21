"""Generated documents only; run these checks after test authorization."""
import hashlib
import io

import pymupdf as p
import pytest
from PIL import Image, ImageDraw
from PyQt6.QtWidgets import QApplication

from app.mosaic import apply_mosaic, mosaic_edit, mosaic_picture
from app.pdfedits import apply_edits
from app.pdfio import PdfError, PdfStore
from app.workspace import Workspace


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def picture():
    image = Image.new("RGB", (120, 80), "white")
    draw = ImageDraw.Draw(image)
    for x in range(0, 120, 4):
        draw.rectangle((x, 8, x + 1, 72), fill=(x * 2, 40, 180))
    return image


@pytest.mark.parametrize("style", ["pixelate", "blur", "solid"])
def test_effects_shapes_and_strength(style):
    source = picture()
    edit = mosaic_edit((0, 0, 120, 80), style=style, color=(.2, .4, .6), strength=4)
    result = mosaic_picture(source, 1, edit)
    assert result.size == source.size and result.tobytes() != source.tobytes()
    stronger = mosaic_picture(source, 1, mosaic_edit(edit.points, style=style, strength=30))
    if style == "solid":
        assert result.getpixel((60, 40)) == (51, 102, 153)
    else:
        assert result.tobytes() != stronger.tobytes()
    ellipse = mosaic_picture(source, 1, mosaic_edit(edit.points, style=style, shape="ellipse", strength=4))
    assert ellipse.getpixel((0, 0)) == source.getpixel((0, 0))
    assert ellipse.getpixel((60, 40)) != source.getpixel((60, 40))
    assert source.tobytes() == picture().tobytes()


@pytest.mark.parametrize("style", ["pixelate", "blur", "solid"])
@pytest.mark.parametrize("rotation,crop,extra", [(0, False, 0), (90, True, 270), (180, True, 90), (270, False, 180)])
def test_save_reopen_removes_selected_content_and_undo_restores_source(qapp, tmp_path, style, rotation, crop, extra):
    source, target = tmp_path / "source.pdf", tmp_path / "mosaic.pdf"
    image = picture()
    data = io.BytesIO()
    image.save(data, "PNG")
    with p.open() as doc:
        page = doc.new_page(width=480, height=360)
        if crop:
            page.set_cropbox((20, 10, 460, 350))
        page.insert_text((55, 70), "SECRET", fontsize=14)
        page.insert_text((55, 90), "HIDDEN", fontsize=12, render_mode=3)
        page.insert_text((280, 230), "KEEP", fontsize=14)
        page.insert_image((55, 105, 175, 185), stream=data.getvalue())
        page.add_text_annot((80, 130), "private-note")
        page.add_text_annot((300, 260), "keep-note")
        page.set_rotation(rotation)
        doc.save(source)
    original = hashlib.sha256(source.read_bytes()).digest()
    store = PdfStore()
    try:
        ref = store.load(str(source))[0].rotated(extra)
        ws = Workspace()
        ws.open_replace([ref], str(source))
        edit = mosaic_edit((40, 45, 200, 195), style=style)
        assert edit.operation == "content_edit"
        candidate = ref.edited(edit)
        assert not store.render_qimage(candidate, 480).isNull()
        ws.edit_page(0, edit, "马赛克", prepared=candidate)
        ws.undo_stack.undo()
        assert ws.pages[0] is ref and not ws.pages[0].edits
        ws.undo_stack.redo()
        assert ws.pages[0] is candidate
        store.write(ws.pages, str(target))
        with p.open(target) as doc:
            page = doc[0]
            assert page.rotation == (rotation + extra) % 360
            assert "KEEP" in page.get_text()
            assert "SECRET" not in page.get_text() and "HIDDEN" not in page.get_text()
            assert [a.info["content"] for a in page.annots()] == ["keep-note"]
            # Garbage collection must not retain an unused original image stream.
            for xref in range(1, doc.xref_length()):
                if doc.xref_get_key(xref, "Subtype") == ("name", "/Image"):
                    assert p.Pixmap(doc, xref).samples != image.tobytes()
        assert hashlib.sha256(source.read_bytes()).digest() == original
    finally:
        store.close_all()


@pytest.mark.parametrize("unsafe", ["redaction", "widget"])
def test_unsafe_page_is_unchanged_on_failure(unsafe):
    with p.open() as doc:
        page = doc.new_page(width=300, height=300)
        page.insert_text((40, 60), "KEEP")
        if unsafe == "redaction":
            page.add_redact_annot((220, 220, 240, 240))
        else:
            widget = p.Widget()
            widget.field_name, widget.field_type = "private", p.PDF_WIDGET_TYPE_TEXT
            widget.field_value, widget.rect = "private-value", p.Rect(40, 40, 140, 80)
            page.add_widget(widget)
        page.set_rotation(90)
        before = page.read_contents()
        with pytest.raises(ValueError, match="涂黑批注|表单"):
            apply_mosaic(page, mosaic_edit((30, 30, 160, 100)))
        assert page.rotation == 90 and page.read_contents() == before


@pytest.mark.parametrize("options", [dict(style="unknown"), dict(shape="unknown"),
    dict(strength=0), dict(strength=float("nan")), dict(color=(1, 0)), dict(color=(2, 0, 0))])
def test_invalid_settings_are_rejected(options):
    with pytest.raises(ValueError):
        mosaic_edit((10, 10, 100, 60), **options)


def test_links_and_crossing_graphics_follow_redaction_scope():
    with p.open() as doc:
        page = doc.new_page(width=300, height=300)
        page.insert_link(dict(kind=p.LINK_URI, uri="https://example.invalid/private", **{"from": p.Rect(40, 40, 90, 70)}))
        page.insert_link(dict(kind=p.LINK_URI, uri="https://example.invalid/keep", **{"from": p.Rect(200, 200, 250, 230)}))
        page.draw_line((10, 60), (280, 60))
        page.draw_line((10, 250), (280, 250))
        page = doc.reload_page(page)
        apply_mosaic(page, mosaic_edit((30, 30, 100, 90), style="solid"))
        page = doc.reload_page(page)
        assert [link["uri"] for link in page.get_links()] == ["https://example.invalid/keep"]
        assert len(page.get_drawings()) == 1


def test_worker_roundtrip_and_multiple_overlapping_mosaics():
    import json
    from dataclasses import asdict
    from app.pdfworker import _tuples
    from app.workspace import PageEdit
    first = mosaic_edit((20, 20, 140, 100), style="pixelate")
    last = mosaic_edit((60, 40, 160, 120), style="solid", shape="ellipse", color=(.1, .5, .2))
    edits = tuple(PageEdit(**{key: _tuples(value) for key, value in item.items()})
                  for item in json.loads(json.dumps([asdict(first), asdict(last)])))
    with p.open() as doc:
        page = doc.new_page(width=300, height=300)
        page.insert_text((40, 60), "SECRET")
        apply_edits(page, edits)
        assert "SECRET" not in page.get_text()
        pix = page.get_pixmap(colorspace=p.csRGB)
        assert pix.pixel(110, 80) == (26, 128, 51)


def test_mosaic_requires_modification_permission(qapp, tmp_path):
    source = tmp_path / "restricted.pdf"
    with p.open() as doc:
        doc.new_page()
        doc.save(source, encryption=p.PDF_ENCRYPT_AES_256, user_pw="", owner_pw="owner",
                 permissions=p.PDF_PERM_ANNOTATE | p.PDF_PERM_PRINT)
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        store.require_operation([ref], "annotate")
        edit = mosaic_edit((10, 10, 80, 40))
        with pytest.raises(PdfError, match="不允许"):
            store.write([ref.edited(edit)], str(tmp_path / "blocked.pdf"))
    finally:
        store.close_all()
