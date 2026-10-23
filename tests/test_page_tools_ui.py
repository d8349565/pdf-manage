"""Real Qt dialogs and atomic apply paths, using generated files exclusively."""
import base64
import io

import pymupdf as p
import pytest
from PIL import Image
from PyQt6.QtCore import QPoint, QPointF, QTimer, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFileDialog,
                             QMessageBox, QStyle, QStyleOptionComboBox, QStyleOptionSpinBox)

from app.deskewui import DeskewDialog, open_deskew
from app.pagetoolsui import PageSizeDialog, resize_pages
from app.seamstamp import list_groups
from app.seamstampui import SeamStampDialog, open_seam_stamps
from app.ui import MainWindow


@pytest.fixture(scope='session')
def qapp():
    from pathlib import Path
    from PyQt6.QtGui import QFontDatabase
    app = QApplication.instance() or QApplication([])
    font = Path('C:/Windows/Fonts/msyh.ttc')
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    return app


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    path = tmp_path / 'generated.pdf'
    with p.open() as doc:
        for width, height in ((300, 400), (420, 300), (300, 400)):
            doc.new_page(width=width, height=height).insert_text((50, 90), 'Generated UI check')
        doc.save(path)
    host = MainWindow()
    host.ws.open_replace(host.store.load(str(path)), str(path))
    host.resize(1180, 800)
    host.refresh()
    host.show()
    qapp.processEvents()
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
    yield host, warnings
    host.ws.reset()
    monkeypatch.setattr(host, '_confirm_leave_all_tabs', lambda: True)
    host.close()
    qapp.processEvents()


def asset():
    image = Image.new('RGBA', (80, 80), (200, 20, 20, 170))
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def wait_for(qapp, condition):
    from time import monotonic
    deadline = monotonic() + 5
    while not condition() and monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(10)
    assert condition()


def populated_seam_dialog(host):
    from PyQt6.QtGui import QImage
    dialog = SeamStampDialog(host, {}, [0, 1, 2])
    data = asset()
    dialog.image, dialog.asset = base64.b64encode(data).decode('ascii'), QImage.fromData(data)
    dialog.resize_asset(True)
    dialog.show()
    return dialog


def test_seam_simple_defaults_and_visible_native_controls(window, qapp):
    host, warnings = window
    dialog = populated_seam_dialog(host)
    assert dialog.group.isHidden() and dialog.remove.isHidden()
    assert dialog.advanced.isHidden() and dialog.scope.range.isHidden()
    assert dialog.height_input.isHidden()
    QTest.mouseClick(dialog.advanced_toggle, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert dialog.advanced.isVisible()
    assert dialog.height() <= 650 and dialog.buttons.isVisible()
    combo_option = QStyleOptionComboBox()
    dialog.edge.initStyleOption(combo_option)
    arrow = dialog.edge.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, combo_option,
                                               QStyle.SubControl.SC_ComboBoxArrow, dialog.edge)
    QTest.mouseClick(dialog.edge, Qt.MouseButton.LeftButton, pos=arrow.center())
    assert dialog.edge.view().isVisible()
    QTest.keyClick(dialog.edge, Qt.Key.Key_Escape)
    spin_option = QStyleOptionSpinBox()
    dialog.width_input.initStyleOption(spin_option)
    up = dialog.width_input.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, spin_option,
                                                  QStyle.SubControl.SC_SpinBoxUp, dialog.width_input)
    before = dialog.width_input.value()
    QTest.mouseClick(dialog.width_input, Qt.MouseButton.LeftButton, pos=up.center())
    assert dialog.width_input.value() == before + 1
    assert dialog.height_input.value() == pytest.approx(before + 1)
    assert not warnings
    dialog.reject()
    dialog.deleteLater()


def test_seam_wheel_scrolls_pages_and_reuses_backgrounds(window, qapp):
    host, warnings = window
    dialog = populated_seam_dialog(host)
    preview = dialog.preview
    wait_for(qapp, lambda: len(preview.images) >= 2 and not preview.busy)
    original_keys = {key: id(image) for key, image in preview.images.items()}
    generation = preview.generation
    dialog.width_input.setValue(44)
    dialog.position.setValue(65)
    qapp.processEvents()
    assert preview.generation == generation
    assert all(id(preview.images[key]) == value for key, value in original_keys.items())
    assert not hasattr(dialog, 'page')
    point = preview.viewport().rect().center()
    for _ in range(8):
        event = QWheelEvent(QPointF(point), QPointF(preview.viewport().mapToGlobal(point)), QPoint(),
                            QPoint(0, -120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.ScrollUpdate, False)
        QApplication.sendEvent(preview.viewport(), event)
    wait_for(qapp, lambda: preview.current_source_row is not None and preview.current_source_row > 0)
    assert preview.verticalScrollBar().value() > 0 and '滚轮' in dialog.preview_caption.text()
    assert len(preview.images) <= 8 and not warnings
    dialog.reject()
    dialog.deleteLater()


def test_continuous_preview_cache_is_bounded_and_closing_cancels(window, qapp, tmp_path):
    from app.pagepreviewlist import ScrollablePagePreview
    host, _ = window
    path = tmp_path / 'generated-preview-pages.pdf'
    with p.open() as doc:
        for index in range(12):
            doc.new_page(width=300, height=400).insert_text((30, 60), f'Page {index + 1}')
        doc.save(path)
    pages = host.store.load(str(path))
    preview = ScrollablePagePreview(host.store, pages)
    preview.set_rows(range(12))
    preview.resize(500, 400)
    preview.show()
    for row in range(12):
        preview.scroll_to_page(row)
        wait_for(qapp, lambda row=row: preview.image_key(row) in preview.images)
        assert len(preview.images) <= 8
    preview.shutdown()
    assert preview.cancel.is_set()
    preview.close()
    preview.deleteLater()


def test_size_dialog_reference_and_real_apply_cancel(window, qapp, monkeypatch):
    host, warnings = window
    host.ws.set_selection([1])
    host._sync_selection()
    original = tuple(host.ws.pages)
    def configure(dialog):
        assert dialog.reference.value() == 1
        assert dialog.target_size() == (300, 400)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(PageSizeDialog, 'exec', configure)
    resize_pages(host)
    assert not warnings
    assert host.store.page_size(host.ws.pages[1]) == (300, 400)
    assert host.ws.pages[0] is original[0] and host.ws.pages[2] is original[2]
    host.ws.undo_stack.undo()
    assert tuple(host.ws.pages) == original
    monkeypatch.setattr(PageSizeDialog, 'exec', lambda self: QDialog.DialogCode.Rejected)
    resize_pages(host)
    assert tuple(host.ws.pages) == original


def test_size_dialog_requires_explicit_target_on_first_page(window):
    host, _ = window
    host.ws.set_selection([0])
    host._sync_selection()
    image = host.store.render_qimage(host.ws.pages[0], 500)
    dialog = PageSizeDialog(host, image)
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    dialog.preset.setCurrentIndex(dialog.preset.findData('a4'))
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    assert dialog.width_input.value() == 210
    dialog.reject()
    dialog.deleteLater()


def test_seam_dialog_apply_reopen_manage_and_cancel(window, qapp, monkeypatch):
    host, warnings = window
    before = tuple(host.ws.pages)
    def configure(dialog):
        from PyQt6.QtGui import QImage
        data = asset()
        dialog.image, dialog.asset = base64.b64encode(data).decode('ascii'), QImage.fromData(data)
        dialog.scope.mode.setCurrentIndex(dialog.scope.mode.findData('all'))
        dialog.resize_asset(True)
        assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
        assert dialog.preview.model().rowCount() == 3
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(SeamStampDialog, 'exec', configure)
    open_seam_stamps(host)
    assert not warnings
    groups = list_groups(host.store, host.ws.pages)
    assert len(groups) == 1
    assert next(iter(groups.values()))['members'] == [0, 1, 2]
    def remove(dialog):
        assert dialog.group.currentData() in groups
        dialog.remove_requested = True
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(SeamStampDialog, 'exec', remove)
    open_seam_stamps(host)
    assert not warnings and not list_groups(host.store, host.ws.pages)
    host.ws.undo_stack.undo()
    assert len(list_groups(host.store, host.ws.pages)) == 1
    host.ws.undo_stack.undo()
    assert tuple(host.ws.pages) == before
    monkeypatch.setattr(SeamStampDialog, 'exec', lambda self: QDialog.DialogCode.Rejected)
    open_seam_stamps(host)
    assert tuple(host.ws.pages) == before


def test_deskew_manual_ui_previews_and_atomic_apply_cancel(window, qapp, monkeypatch):
    host, warnings = window
    before = tuple(host.ws.pages)
    def configure(dialog):
        dialog.controls[dialog.rows[0]].setValue(2.5)
        assert dialog.angles() == {dialog.rows[0]: 2.5}
        assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
        assert dialog.preview.model().rowCount() >= 1
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(DeskewDialog, 'exec', configure)
    open_deskew(host)
    assert not warnings and host.ws.pages[0].edits[-1].kind == 'page_geometry'
    assert host.store.page_size(host.ws.pages[0]) == (300, 400)
    host.ws.undo_stack.undo()
    assert tuple(host.ws.pages) == before
    monkeypatch.setattr(DeskewDialog, 'exec', lambda self: QDialog.DialogCode.Rejected)
    open_deskew(host)
    assert tuple(host.ws.pages) == before


def test_deskew_automatic_review_requires_confirmation(window, qapp, monkeypatch):
    from app.deskew import DeskewEstimate
    import app.deskewui as module
    host, warnings = window
    host.ws.select_every_page()
    host._sync_selection()
    monkeypatch.setattr(module, 'analyze_pages', lambda *args: {
        0: DeskewEstimate(2, .9, 'ready', '建议旋转 +2°'),
        1: DeskewEstimate(-1.2, .1, 'review', '待确认'),
        2: DeskewEstimate(0, 0, 'unsupported', '含批注，已跳过')})
    dialog = DeskewDialog(host)
    dialog.show()
    dialog.analyze()
    assert dialog.angles() == {0: 2}
    assert not dialog.controls[2].isEnabled()
    dialog.table.item(1, 0).setCheckState(Qt.CheckState.Checked)
    assert dialog.angles() == {0: 2, 1: -1.2}
    assert tuple(host.ws.pages) == dialog.pages
    dialog.reject()
    dialog.deleteLater()
    assert not warnings


def test_real_scan_analysis_reaches_dialog_angle_controls(window, qapp, tmp_path):
    from PIL import ImageDraw, ImageFont
    host, warnings = window
    path = tmp_path / 'generated-scans.pdf'
    with p.open() as doc:
        for angle in (2.8, -2.2):
            image = Image.new('L', (720, 960), 255)
            draw = ImageDraw.Draw(image)
            font = ImageFont.load_default(size=22)
            for row in range(20):
                draw.text((65, 90 + row * 36), f'{row + 1:02} Generated scan for automatic deskew', font=font, fill=20)
            stream = io.BytesIO()
            image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255).save(stream, format='PNG')
            page = doc.new_page(width=360, height=480)
            page.insert_image(page.rect, stream=stream.getvalue())
        doc.save(path)
    host.ws.open_replace(host.store.load(str(path)), str(path))
    host.refresh()
    qapp.processEvents()
    host.select_all_pages()
    dialog = DeskewDialog(host)
    dialog.show()
    dialog.analyze()
    assert len(dialog.estimates) == 2 and len(dialog.angles()) == 2
    assert dialog.controls[0].value() == pytest.approx(2.8, abs=.3)
    assert dialog.controls[1].value() == pytest.approx(-2.2, abs=.3)
    assert not warnings
    dialog.reject()
    dialog.deleteLater()


def test_close_during_deskew_analysis_requests_cancellation(window, qapp, monkeypatch):
    import time
    import app.deskewui as module
    from app.output import check_cancel
    host, warnings = window
    before = tuple(host.ws.pages)
    def slow_analysis(store, pages, indices, progress, cancel):
        for _ in range(500):
            check_cancel(cancel)
            time.sleep(.002)
        raise AssertionError('Cancellation did not reach the worker')
    monkeypatch.setattr(module, 'analyze_pages', slow_analysis)
    dialog = DeskewDialog(host)
    dialog.show()
    QTimer.singleShot(30, dialog.reject)
    dialog.analyze()
    assert '取消' in dialog.status.text()
    assert tuple(host.ws.pages) == before and not warnings
    dialog.reject()
    dialog.deleteLater()


def test_seal_save_defaults_to_copy_and_keeps_original(window, tmp_path, monkeypatch):
    from app.seamstamp import prepare_group
    host, warnings = window
    source = host.ws.path
    with open(source, 'rb') as stream:
        original = stream.read()
    options = dict(image=base64.b64encode(asset()).decode('ascii'), width=90, height=90,
                   edge='right', position=.5)
    changes = prepare_group(host.store, host.ws.pages, [0, 1, 2], options, 'zhixu-seam-ui-save')
    host.ws.edit_pages(changes, '骑缝章')
    output = tmp_path / 'saved-copy.pdf'
    suggestions = []
    def choose(*args):
        suggestions.append(args[2])
        return str(output), 'PDF (*.pdf)'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', choose)
    assert host.save()
    assert suggestions and suggestions[0].endswith('_副本.pdf')
    assert output.exists() and not warnings
    assert len(list_groups(host.store, host.ws.pages)) == 1
    with open(source, 'rb') as stream:
        assert stream.read() == original


def test_dialogs_fit_a_desktop_and_capture_generated_previews(window, qapp, tmp_path):
    from PyQt6.QtGui import QImage
    host, warnings = window
    host.ws.select_every_page()
    host._sync_selection()
    image = host.store.render_qimage(host.ws.pages[0], 600)
    dialogs = [('page-size', PageSizeDialog(host, image)),
               ('seam-stamp', SeamStampDialog(host, {})), ('deskew', DeskewDialog(host))]
    dialogs[0][1].preset.setCurrentIndex(1)
    seam = dialogs[1][1]
    data = asset()
    seam.image, seam.asset = base64.b64encode(data).decode('ascii'), QImage.fromData(data)
    seam.resize_asset(True)
    for name, dialog in dialogs:
        dialog.resize(1040, 620)
        dialog.show()
        qapp.processEvents()
        if dialog.preview.rows:
            row = dialog.preview.rows[0]
            wait_for(qapp, lambda: dialog.preview.image_key(row) in dialog.preview.images)
        assert dialog.width() <= 1060 and dialog.height() <= 650, (name, dialog.size())
        assert dialog.buttons.isVisible() and dialog.preview.isVisible()
        assert dialog.grab().save(str(tmp_path / f'{name}.png'))
        dialog.reject()
        dialog.deleteLater()
    assert not warnings
