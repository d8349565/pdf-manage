import subprocess
import sys

import pytest
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from app.pdfcompareui import PairingDialog, PdfCompareDialog, STATUS
from app.pdfcompare_demo import vector_pdf


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def test_real_dialog_worker_navigation_originals_and_zoom(qapp, tmp_path):
    old, new = tmp_path / 'old.pdf', tmp_path / 'new.pdf'
    specs = [dict(title='Section ALPHA'), dict(title='Section BRAVO')]
    vector_pdf(old, specs); vector_pdf(new, [dict(specs[0], amount='125000'), dict(title='Addendum ONLY')] + specs[1:])
    dialog = PdfCompareDialog(); dialog.show(); qapp.processEvents()
    ticks = []; timer = QTimer(); timer.timeout.connect(lambda: ticks.append(1)); timer.start(10)
    dialog.paths[0].setText(str(old)); dialog.paths[1].setText(str(new))
    try:
        dialog.start.click()
        assert dialog.summary is not None and dialog.row == 0
        assert dialog.images is not None and dialog.rows.count() == 3
        assert ticks  # event loop is alive while the child renders
        first_image = dialog.images['old'].tobytes()
        dialog.mode.setCurrentIndex(1)
        assert dialog.images['old'].tobytes() == first_image
        dialog.navigate(1)
        assert dialog.regions.currentIndex() == 0
        assert dialog.view.marker is not None
        assert dialog.view.transform().m11() <= 3
        dialog.rows.setCurrentRow(1)
        assert dialog.summary['rows'][dialog.row]['old_page'] is None
        assert '未配对' in dialog.identity.text()
        dialog.navigate(-1)
        assert dialog.row == 0
        assert all(widget.text() for widget in dialog.paths)
    finally:
        timer.stop(); dialog.close(); dialog.deleteLater()


def test_progress_cancel_keeps_previous_complete_result(qapp, tmp_path):
    path = tmp_path / 'source.pdf'; vector_pdf(path, [{}])
    dialog = PdfCompareDialog()
    for widget in dialog.paths:
        widget.setText(str(path))
    dialog.compare(); previous = dialog.summary
    assert previous is not None
    def cancel():
        task = getattr(dialog, '_task_dialog', None)
        if task is not None:
            task.request_cancel()
    QTimer.singleShot(1, cancel)
    dialog.compare()
    assert dialog.summary is previous
    assert '已取消' in dialog.status.text()
    dialog.close(); dialog.deleteLater()


def test_main_window_import_does_not_load_comparison_engine():
    code = "import sys; import app.ui; assert 'app.pdfcompare' not in sys.modules; assert 'app.pdfcompareui' not in sys.modules; assert 'app.pdfcontentcompare' not in sys.modules; assert 'app.pdfcontentcompareui' not in sys.modules"
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_preview_cache_reuses_pages_but_rejects_changed_sources(qapp, tmp_path, monkeypatch):
    import app.pdfcompareui as ui
    old, new = tmp_path / 'old.pdf', tmp_path / 'new.pdf'
    vector_pdf(old, [dict(amount=str(i)) for i in range(3)])
    vector_pdf(new, [dict(amount=str(i + 10)) for i in range(3)])
    dialog = PdfCompareDialog()
    dialog.paths[0].setText(str(old)); dialog.paths[1].setText(str(new))
    original = ui.compare_pdf_versions
    calls = []
    def record(*args, **kwargs):
        if kwargs.get('summary') is not None:
            calls.append(kwargs['row'])
        return original(*args, **kwargs)
    monkeypatch.setattr(ui, 'compare_pdf_versions', record)
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[-1]))
    try:
        dialog.pairing.setCurrentIndex(1); dialog.compare()
        dialog.rows.setCurrentRow(1); dialog.rows.setCurrentRow(0)
        assert calls == [0, 1]
        dialog.regions.setCurrentIndex(0)
        dialog.mode.setCurrentIndex(1)
        assert dialog.view.marker is not None  # 原页切换保留定位
        dialog.rows.setCurrentRow(2)
        assert len(dialog.preview_cache) <= 2
        assert sum(image.width * image.height * 4 for _, images in dialog.preview_cache.values()
                   for image in images.values()) <= dialog.preview_cache_limit
        vector_pdf(old, [dict(amount='9999999')])
        dialog.rows.setCurrentRow(0)
        assert warnings and '变化' in warnings[-1]
        assert dialog.row == dialog.rows.currentRow() == 2
        assert not dialog.preview_cache
    finally:
        dialog.close(); dialog.deleteLater()
    assert dialog.images is None and dialog.summary is None


def test_pair_editor_validation_apply_reset_and_cancel(qapp, tmp_path):
    old, new = tmp_path / 'old.pdf', tmp_path / 'new.pdf'
    vector_pdf(old, [dict(blank=True)] * 2); vector_pdf(new, [dict(blank=True)] * 3)
    dialog = PdfCompareDialog(); dialog.show()
    assert not dialog.start.isEnabled() and not dialog.mode.isEnabled()
    dialog.paths[0].setText(str(old)); dialog.paths[1].setText(str(new))
    assert dialog.start.isEnabled()
    try:
        assert dialog.options_panel.isHidden()
        dialog.advanced.click(); assert not dialog.options_panel.isHidden()
        dialog.compare()
        assert STATUS['equal'] == '当前设置下未检出差异'
        def accept_editor():
            editor = dialog.findChild(PairingDialog)
            editor.table.cellWidget(0, 0).setValue(1); editor.table.cellWidget(0, 1).setValue(2)
            editor.add_pair([2, 1]); editor.accept()
            assert editor.error.text() and editor.result() != QDialog.DialogCode.Accepted
            editor.table.removeRow(1); editor.accept()
        QTimer.singleShot(0, accept_editor); dialog.edit_pairing()
        assert dialog.summary['manual_pairs'] == [[1, 2]]
        assert '手工指定' in dialog.rows.item(1).text()
        dialog.strict.setChecked(True); dialog.start.click()
        assert dialog.summary['manual_pairs'] == [[1, 2]]
        assert dialog.summary['options']['threshold'] == 0
        previous = dialog.summary
        def cancel_operation():
            task = getattr(dialog, '_task_dialog', None)
            if task is not None:
                task.request_cancel()
        QTimer.singleShot(1, cancel_operation)
        dialog.compare(manual_pairs=[[1, 1]], reuse=True)
        assert dialog.summary is previous and dialog.summary['manual_pairs'] == [[1, 2]]
        vector_pdf(new, [dict(blank=True)] * 4)
        dialog.start.click()
        assert dialog.summary['manual_pairs'] == []
        assert '来源文件已变化' in dialog.status.text()
        dialog.compare(manual_pairs=[[1, 2]], reuse=True)
        dialog.compare(manual_pairs=[], reuse=True)
        assert dialog.summary['manual_pairs'] == []
        assert all('手工指定' not in dialog.rows.item(i).text() for i in range(dialog.rows.count()))
    finally:
        dialog.close(); dialog.deleteLater()
