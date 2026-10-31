import subprocess
import sys

import pytest
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from app.pdfcompareui import PdfCompareDialog
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
    code = "import sys; import app.ui; assert 'app.pdfcompare' not in sys.modules; assert 'app.pdfcompareui' not in sys.modules"
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
