"""Reconstruct generated transparent seal slices after save and rotation."""
import base64
import hashlib
import io

import pymupdf as p
import pytest
from PIL import Image, ImageChops, ImageDraw
from PyQt6.QtWidgets import QApplication

from app.output import OperationCancelled
from app.pdfio import PdfError, PdfStore
from app.seamstamp import list_groups, prepare_group, slice_rect
from app.workspace import Workspace


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def seal_image():
    image = Image.new('RGBA', (120, 80), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for x in range(120):
        draw.line((x, 3, x, 76), fill=(x * 2, 30, 240 - x * 2, 255))
    draw.rectangle((20, 25, 100, 55), fill=(0, 0, 0, 0))
    draw.rectangle((3, 3, 12, 12), fill=(0, 180, 0, 255))
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    return image, base64.b64encode(stream.getvalue()).decode('ascii')


def create_pages(path, count):
    with p.open() as doc:
        for index in range(count):
            page = doc.new_page(width=300 + index * 20, height=400 + index * 20)
            page.draw_rect(page.rect, fill=(.8, .9, 1), color=None)
            page.insert_text((45, 85), f'Generated page {index + 1}')
            page.set_cropbox(p.Rect(10, 20, page.rect.width - 10, page.rect.height - 20))
            page.set_rotation(index % 4 * 90)
        doc.save(path)


@pytest.mark.parametrize('edge', ['left', 'right', 'top', 'bottom'])
@pytest.mark.parametrize('count', [2, 4, 5])
def test_seal_slices_reassemble_with_alpha_on_mixed_rotated_pages(tmp_path, qapp, edge, count):
    path, output = tmp_path / 'generated.pdf', tmp_path / 'sealed.pdf'
    create_pages(path, count)
    original_hash = hashlib.sha256(path.read_bytes()).digest()
    original_image, encoded = seal_image()
    options = dict(image=encoded, width=120, height=80, edge=edge, position=.5)
    store = PdfStore()
    try:
        refs = store.load(str(path))
        changes = prepare_group(store, refs, range(count), options, 'zhixu-seam-generated')
        ws = Workspace()
        ws.open_replace(refs, str(path))
        ws.edit_pages(changes, '骑缝章')
        with pytest.raises(PdfError, match='副本'):
            store.write(ws.pages, str(path))
        store.write(ws.pages, str(output))
        with p.open(output) as doc:
            for index, page in enumerate(doc):
                width, height = page.rect.width, page.rect.height
                area, fraction = slice_rect(120, 80, count, index, edge, .5, width, height)
                pixels = page.get_pixmap(clip=area, alpha=False)
                actual = Image.frombytes('RGB', (pixels.width, pixels.height), pixels.samples)
                crop = (fraction[0] * 120, fraction[1] * 80, fraction[2] * 120, fraction[3] * 80)
                expected_rgba = original_image.crop(tuple(round(v) for v in crop))
                expected = Image.new('RGB', expected_rgba.size, (204, 229, 255))
                expected.paste(expected_rgba, mask=expected_rgba.getchannel('A'))
                if actual.size != expected.size:
                    expected = expected.resize(actual.size)
                # Exclude antialiased slice edges; compare every interior pixel.
                diff = ImageChops.difference(actual, expected).crop((2, 2, actual.width - 2, actual.height - 2))
                error = sum(sum(channel * count for channel, count in enumerate(diff.histogram()[i:i + 256]))
                            for i in (0, 256, 512)) / max(1, diff.width * diff.height * 3)
                assert error < 5, (edge, index, error)
                assert f'Generated page {index + 1}' in page.get_text()
                assert len(list(page.annots())) == 1
        assert hashlib.sha256(path.read_bytes()).digest() == original_hash
        ws.undo_stack.undo()
        assert ws.pages == refs
        ws.undo_stack.redo()
        assert ws.pages == [changes[i] for i in range(count)]
    finally:
        store.close_all()


def test_saved_group_can_change_range_and_be_deleted(tmp_path, qapp):
    path, output, updated = (tmp_path / name for name in ('generated.pdf', 'sealed.pdf', 'updated.pdf'))
    create_pages(path, 5)
    _, encoded = seal_image()
    options = dict(image=encoded, width=120, height=80, edge='right', position=.4)
    store = PdfStore()
    try:
        refs = store.load(str(path))
        changes = prepare_group(store, refs, [0, 1, 2, 3], options, 'zhixu-seam-generated')
        store.write([changes.get(i, ref) for i, ref in enumerate(refs)], str(output))
        reopened = store.load(str(output))
        group = list_groups(store, reopened)['zhixu-seam-generated']
        assert group['image'] == encoded
        assert group['members'] == [0, 1, 2, 3]
        changes = prepare_group(store, reopened, [1, 3, 4], dict(options, edge='top'),
                                group['group'], previous=group['members'])
        pages = [changes.get(i, ref) for i, ref in enumerate(reopened)]
        store.write(pages, str(updated))
        with p.open(updated) as doc:
            assert [len(list(page.annots() or ())) for page in doc] == [0, 1, 0, 1, 1]
        groups = list_groups(store, pages)
        changes = prepare_group(store, pages, [], {}, group['group'], previous=groups[group['group']]['members'], remove=True)
        assert not list_groups(store, [changes.get(i, ref) for i, ref in enumerate(pages)])
    finally:
        store.close_all()


def test_cancel_single_page_and_oversize_leave_workspace_unchanged(tmp_path, qapp):
    path = tmp_path / 'generated.pdf'
    create_pages(path, 4)
    _, encoded = seal_image()
    options = dict(image=encoded, width=120, height=80, edge='right', position=.5)
    store = PdfStore()
    try:
        pages = store.load(str(path))
        ws = Workspace()
        ws.open_replace(pages, str(path))
        with pytest.raises(ValueError, match='至少两页'):
            prepare_group(store, pages, [0], options, 'generated')
        with pytest.raises(ValueError, match='超出'):
            prepare_group(store, pages, [0, 1], dict(options, height=10000), 'generated')
        calls = []
        def progress(*args):
            calls.append(args)
        with pytest.raises(OperationCancelled):
            prepare_group(store, pages, range(4), options, 'generated', progress=progress, cancel=lambda: bool(calls))
        assert ws.pages == pages and not ws.dirty
        assert not list_groups(store, ws.pages)
    finally:
        store.close_all()


def test_many_narrow_slices_remain_one_manageable_group(tmp_path, qapp):
    path, output = tmp_path / 'many-generated.pdf', tmp_path / 'many-sealed.pdf'
    create_pages(path, 64)
    _, encoded = seal_image()
    store = PdfStore()
    try:
        pages = store.load(str(path))
        options = dict(image=encoded, width=120, height=80, edge='right', position=.5)
        changes = prepare_group(store, pages, range(64), options, 'zhixu-seam-many')
        store.write([changes[i] for i in range(64)], str(output))
        reopened = store.load(str(output))
        group = list_groups(store, reopened)['zhixu-seam-many']
        assert group['members'] == list(range(64)) and group['count'] == 64
        with p.open(output) as doc:
            for page in doc:
                annot = next(page.annots())
                area = annot.rect * page.rotation_matrix
                assert area.width == pytest.approx(120 / 64, abs=.002)
                assert area.x1 == pytest.approx(page.rect.width, abs=.002)
    finally:
        store.close_all()
