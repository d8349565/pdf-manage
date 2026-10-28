"""Region extraction works without writing document annotations."""
import hashlib

import pymupdf as p
import pytest
from PyQt6.QtCore import QPointF, Qt, QThreadPool
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app.annotcrop import export_annotation_crop
from app.annotcropui import AnnotationCropDialog
from app.ui import MainWindow
from test_annotcrop import make_annotated_pdf


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def editor(qapp, tmp_path):
    source = make_annotated_pdf(tmp_path / 'source.pdf', pages=3)
    window = MainWindow()
    window.ws.open_replace(window.store.load(str(source)), str(source))
    dialog = AnnotationCropDialog(window)
    dialog.resize(1100, 760)
    dialog.show()
    try:
        yield window, dialog, source
    finally:
        dialog.reject()
        QThreadPool.globalInstance().waitForDone(10000)
        qapp.processEvents()
        window.ws.reset()
        window.store.close_all()
        window.close()
        qapp.processEvents()


def ready(dialog, row=0):
    view = dialog.preview
    view.scroll_to_page(row)
    for _ in range(200):
        if view.image_key(row) in view.images:
            return
        QTest.qWait(10)
    pytest.fail('Generated page preview did not finish')


def position(dialog, row, x, y):
    view = dialog.preview
    item = view.page_model.index(view.rows.index(row))
    paper = view.paper_rect(row, view.visualRect(item))
    width, height = view.store.page_size(view.pages[row])
    return (paper.topLeft() + QPointF(x * paper.width() / width, y * paper.height() / height)).toPoint()


def draw(dialog, row=0, click=False):
    ready(dialog, row)
    viewport = dialog.preview.viewport()
    first, second = position(dialog, row, 80, 20), position(dialog, row, 240, 100)
    assert viewport.rect().contains(first) and viewport.rect().contains(second)
    if click:
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=first)
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=second)
    else:
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=first)
        QTest.mouseMove(viewport, second)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=second)


@pytest.mark.parametrize('click', [False, True])
def test_manual_regions_export_without_editing_source(editor, tmp_path, click):
    window, dialog, source = editor
    before = hashlib.sha256(source.read_bytes()).digest()
    assert dialog.source.currentData() == 'regions' and not dialog.start.isEnabled()
    assert dialog.indices() == [0, 1, 2]
    draw(dialog, click=click)
    assert dialog.start.isEnabled() and dialog.region_list.count() == 1
    assert not window.ws.dirty and not any(ref.edits for ref in window.ws.pages)
    output = tmp_path / 'extracted.pdf'
    result = export_annotation_crop(window.store, dialog.pages, dialog.options(), str(output))
    assert result['regions'] == 1
    with p.open(output) as saved:
        assert 'TARGET0' in saved[0].get_text() and 'OTHER' not in saved[0].get_text()
    assert hashlib.sha256(source.read_bytes()).digest() == before


def test_subset_uses_original_page_number_and_keeps_other_page_regions(editor):
    _, dialog, _ = editor
    draw(dialog)
    dialog.scope.mode.setCurrentIndex(dialog.scope.mode.findData('range'))
    dialog.scope.range.setText('2')
    assert dialog.indices() == [1]
    draw(dialog, 1)
    assert set(dialog.preview.regions) == {0, 1}
    assert dialog.options()['range'] == '2'
    assert dialog.region_list.count() == 1
    dialog.scope.range.setText('9')
    assert not dialog.start.isEnabled()
    dialog.layout_mode.setCurrentIndex(1)
    assert not dialog.start.isEnabled()


def test_move_resize_delete_and_undo_are_local(editor):
    window, dialog, _ = editor
    draw(dialog)
    view = dialog.preview
    original = view.regions[0][0]
    first, second = position(dialog, 0, 160, 60), position(dialog, 0, 185, 90)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=first)
    QTest.mouseMove(view.viewport(), second)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=second)
    moved = view.regions[0][0]
    assert moved.x() > original.x() + 20 and moved.y() > original.y() + 25
    assert moved.size() == original.size()
    corner = position(dialog, 0, moved.right(), moved.bottom())
    end = position(dialog, 0, moved.right() + 30, moved.bottom() + 20)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=corner)
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert view.regions[0][0].width() > moved.width() + 25
    dialog.delete.click()
    assert dialog.region_list.count() == 0 and not dialog.start.isEnabled()
    dialog.undo_button.click()
    assert dialog.region_list.count() == 1
    assert not window.ws.dirty and not any(ref.edits for ref in window.ws.pages)


def test_import_and_margin_switch_keep_their_own_regions(editor):
    _, dialog, _ = editor
    dialog.source.setCurrentIndex(dialog.source.findData('annot'))
    assert dialog.start.isEnabled() and dialog.region_list.count() == 3
    annotation = dialog.preview.reference_regions[0][0]
    dialog.source.setCurrentIndex(dialog.source.findData('margin'))
    assert dialog.preview.reference_regions[0][0] != annotation
    dialog.source.setCurrentIndex(dialog.source.findData('annot'))
    assert dialog.preview.reference_regions[0][0] == annotation
    dialog.source.setCurrentIndex(dialog.source.findData('regions'))
    assert not dialog.start.isEnabled()


def test_escape_cancels_unfinished_region_without_closing_tool(editor):
    _, dialog, _ = editor
    ready(dialog)
    view = dialog.preview
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=position(dialog, 0, 80, 20))
    assert view.drag is not None
    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert view.drag is None and dialog.isVisible() and not dialog.preview.regions


def test_delete_during_move_cancels_old_mouse_release(editor):
    _, dialog, _ = editor
    draw(dialog)
    view = dialog.preview
    first, second = position(dialog, 0, 160, 60), position(dialog, 0, 190, 80)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=first)
    QTest.mouseMove(view.viewport(), second)
    assert view.drag is not None
    dialog.delete.click()
    assert view.drag is None and not view.regions[0]
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=second)
    assert not view.regions[0]
    dialog.undo_button.click()
    assert len(view.regions[0]) == 1


def test_unfinished_second_region_prevents_export(editor):
    _, dialog, _ = editor
    draw(dialog)
    view = dialog.preview
    assert dialog.start.isEnabled()
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=position(dialog, 0, 310, 160))
    assert view.drag is not None and not dialog.start.isEnabled()
    dialog.accept()
    assert dialog.isVisible() and not view.closed
    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert dialog.start.isEnabled() and len(view.regions[0]) == 1
