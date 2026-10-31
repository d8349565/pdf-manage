"""Qt file drops must fill the intended version without moving or opening files."""
from pathlib import Path

import pytest
from PyQt6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PyQt6.QtWidgets import QApplication

from app.pdfcompareui import PdfCompareDialog
from app.pdfcontentcompareui import PdfContentCompareDialog
from pdfcompare_acceptance_samples import NORMAL, paragraph, render


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def pdfs(tmp_path):
    folder = tmp_path / '文件夹 空格'; folder.mkdir()
    paths = [folder / name for name in ('旧版 文档.pdf', '新版文档.PDF', '第三份.pdf')]
    for path, amount in zip(paths, ('120000', '125000', '130000')):
        render(path, [paragraph('price', '付款条件', f'合同金额为{amount}元。')], NORMAL)
    return paths


def drop(target, values, qapp, *, leave=False):
    mime = QMimeData()
    mime.setUrls([v if isinstance(v, QUrl) else QUrl.fromLocalFile(str(v)) for v in values])
    actions = Qt.DropAction.CopyAction | Qt.DropAction.MoveAction
    enter = QDragEnterEvent(QPoint(8, 8), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, enter)
    if leave:
        QApplication.sendEvent(target, QDragLeaveEvent())
        return enter.isAccepted(), None
    event = QDropEvent(QPointF(8, 8), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    event.setDropAction(Qt.DropAction.MoveAction); event.ignore()
    if enter.isAccepted():
        QApplication.sendEvent(target, event)
    qapp.processEvents()
    if event.isAccepted():
        assert event.dropAction() == Qt.DropAction.CopyAction
    return enter.isAccepted(), event.isAccepted()


@pytest.mark.parametrize('dialog_type', [PdfContentCompareDialog, PdfCompareDialog])
def test_single_pdf_drops_fill_named_version_and_keep_sources(qapp, pdfs, dialog_type):
    dialog = dialog_type(); dialog.show(); qapp.processEvents()
    before = [path.read_bytes() for path in pdfs]
    try:
        assert drop(dialog.file_targets[1], [pdfs[1]], qapp) == (True, True)
        assert dialog.paths[0].text() == '' and Path(dialog.paths[1].text()) == pdfs[1]
        assert not dialog.start.isEnabled()
        assert drop(dialog.paths[0], [pdfs[0]], qapp) == (True, True)
        assert [Path(field.text()) for field in dialog.paths] == pdfs[:2]
        assert dialog.start.isEnabled() and dialog.summary is None
        assert all(not field.property('pdfDropActive') for field in dialog.paths)
        assert before == [path.read_bytes() for path in pdfs]
    finally:
        dialog.close(); dialog.deleteLater()


@pytest.mark.parametrize('dialog_type', [PdfContentCompareDialog, PdfCompareDialog])
def test_pair_order_feedback_password_reset_and_full_window_guard(qapp, pdfs, dialog_type):
    dialog = dialog_type(); dialog.show(); qapp.processEvents()
    try:
        for field in dialog.password_inputs:
            field.setText('previous-file-password')
        # Preserve the supplied file order; never silently guess versions from a name.
        assert drop(dialog, [pdfs[1], pdfs[0]], qapp) == (True, True)
        assert [Path(field.text()) for field in dialog.paths] == [pdfs[1], pdfs[0]]
        assert all(not field.text() for field in dialog.password_inputs)
        assert '旧版' in dialog.status.text() and '新版' in dialog.status.text() and '顺序' in dialog.status.text()
        previous = [field.text() for field in dialog.paths]
        assert drop(dialog, [pdfs[2]], qapp) == (True, False)
        assert [field.text() for field in dialog.paths] == previous
        assert '替换' in dialog.status.text()
        assert drop(dialog.paths[0], [pdfs[2]], qapp) == (True, True)
        assert Path(dialog.paths[0].text()) == pdfs[2]
    finally:
        dialog.close(); dialog.deleteLater()


@pytest.mark.parametrize('dialog_type', [PdfContentCompareDialog, PdfCompareDialog])
def test_invalid_duplicate_and_busy_drops_do_not_change_inputs(qapp, tmp_path, pdfs, dialog_type):
    text = tmp_path / 'not-pdf.txt'; text.write_text('fixture', encoding='utf-8')
    directory = tmp_path / 'folder.pdf'; directory.mkdir()
    missing = tmp_path / 'missing.pdf'
    dialog = dialog_type(); dialog.show(); qapp.processEvents()
    try:
        assert drop(dialog, [pdfs[0]], qapp) == (True, True)
        previous = [field.text() for field in dialog.paths]
        for values in ([text], [directory], [missing], pdfs, [pdfs[0], pdfs[0]],
                       [QUrl('https://example.invalid/document.pdf')]):
            _, accepted = drop(dialog.paths[1], values, qapp)
            assert not accepted
            assert [field.text() for field in dialog.paths] == previous
        assert drop(dialog, [pdfs[0]], qapp) == (True, False)
        assert dialog.paths[1].text() == ''  # Repeated background drops do not fill both sides.
        dialog._set_busy(True)
        assert drop(dialog, [pdfs[1]], qapp) == (False, False)
        assert [field.text() for field in dialog.paths] == previous
        dialog._set_busy(False)
        assert drop(dialog.paths[1], [pdfs[1]], qapp, leave=True)[0]
        assert not dialog.paths[1].property('pdfDropActive')
    finally:
        dialog.close(); dialog.deleteLater()


def test_dropped_pair_can_start_actual_content_comparison(qapp, pdfs):
    dialog = PdfContentCompareDialog(); dialog.show(); qapp.processEvents()
    try:
        assert drop(dialog, pdfs[:2], qapp) == (True, True)
        assert dialog.summary is None
        dialog.start.click()
        assert dialog.summary['counts'] == {'modified': 1}
        assert '120000元' in dialog.contexts[0].text() and '125000元' in dialog.contexts[1].text()
        assert dialog.preview['old']['page'] == dialog.preview['new']['page'] == 1
    finally:
        dialog.close(); dialog.deleteLater()
