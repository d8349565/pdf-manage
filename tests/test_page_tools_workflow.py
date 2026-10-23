"""The actual process writer with all three tools used on the same document."""
import base64
import io

from PIL import Image, ImageDraw
import pymupdf as p
import pytest
from PyQt6.QtWidgets import QApplication

from app.deskew import prepare_deskew
from app.pagegeometry import geometry_edit
from app.pdfio import PdfStore, WriteOptions
from app.pdfworker import write_pdf
from app.seamstamp import list_groups, prepare_group


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def test_resize_deskew_seal_reorder_save_and_reopen_in_worker(tmp_path, qapp):
    path, output = tmp_path / 'generated.pdf', tmp_path / 'combined.pdf'
    with p.open() as doc:
        for i in range(4):
            page = doc.new_page(width=300 + i * 20, height=400 + i * 20)
            page.insert_text((45, 75), f'PAGE {i + 1} searchable')
            page.set_cropbox(p.Rect(10, 20, page.rect.width - 10, page.rect.height - 20))
            page.set_rotation(i * 90)
        doc[0].insert_link(dict(kind=p.LINK_GOTO, page=2, to=p.Point(40, 60),
                                **{'from': p.Rect(30, 120, 100, 150)}))
        doc[2].insert_link(dict(kind=p.LINK_URI, uri='https://example.com/generated',
                                **{'from': p.Rect(30, 100, 100, 120)}))
        doc.set_toc([[1, 'Generated target', 3]])
        doc.save(path)
    original = path.read_bytes()
    store = PdfStore()
    try:
        refs = store.load(str(path))
        outline = store.outline_entries(refs)
        refs = [ref.edited(geometry_edit(store, ref, (360, 480))) for ref in refs]
        changes = prepare_deskew(store, refs, {0: 2.4, 1: -1.5, 2: .8, 3: -2})
        refs = [changes[i] for i in range(4)]
        stream = io.BytesIO()
        picture = Image.new('RGBA', (120, 80), (0, 0, 0, 0))
        ImageDraw.Draw(picture).ellipse((4, 4, 115, 75), outline=(200, 0, 0, 210), width=6)
        picture.save(stream, format='PNG')
        options = dict(image=base64.b64encode(stream.getvalue()).decode('ascii'),
                       width=100, height=80, edge='right', position=.4)
        changed = prepare_group(store, refs, [0, 1, 2, 3], options, 'zhixu-seam-workflow')
        refs = [changed[i] for i in (2, 0, 3, 1)]
        saved = write_pdf(store, refs, str(output), options=WriteOptions(outline=outline), register_result=True)
        assert len(saved.pages) == 4
        assert list_groups(store, saved.pages)['zhixu-seam-workflow']['image'] == options['image']
        with p.open(output) as doc:
            assert all(tuple(page.rect)[2:] == (360, 480) for page in doc)
            assert [page.get_text().splitlines()[0] for page in doc] == [
                'PAGE 3 searchable', 'PAGE 1 searchable', 'PAGE 4 searchable', 'PAGE 2 searchable']
            assert doc[1].get_links()[0]['page'] == 0
            assert doc[0].get_links()[0]['uri'] == 'https://example.com/generated'
            assert doc.get_toc()[0][2] == 1
            assert all(len(list(page.annots())) == 1 for page in doc)
        assert path.read_bytes() == original
        # The main process registers a lightweight source after a worker save;
        # it must retain the metadata needed by the next page-tool operation.
        followup = saved.pages[0].edited(geometry_edit(store, saved.pages[0], (180, 240)))
        store.write([followup], str(tmp_path / 'followup.pdf'))
        assert store.page_size(followup) == (180, 240)
    finally:
        store.close_all()


def test_scaled_annotation_preview_matches_saved_appearance(tmp_path, qapp):
    source, output = tmp_path / 'annotated.pdf', tmp_path / 'resized.pdf'
    with p.open() as doc:
        page = doc.new_page(width=300, height=400)
        annot = page.add_rect_annot(p.Rect(60, 100, 100, 140))
        annot.set_colors(fill=(1, 0, 0), stroke=(1, 0, 0))
        annot.update()
        doc.save(source)
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        ref = ref.edited(geometry_edit(store, ref, (150, 200)))
        preview = store.render_qimage(ref, 150)
        store.write([ref], str(output))
        with p.open(output) as doc:
            pix = doc[0].get_pixmap()
            red, green, blue = pix.pixel(40, 60)
            assert red > 240 and green < 10 and blue < 10
        assert preview.pixelColor(40, 60).green() < 10
        assert preview.pixelColor(80, 120).green() > 240
    finally:
        store.close_all()
