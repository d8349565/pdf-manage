"""Real Qt controls and PDF processes for the document-menu content comparison flow."""
import os
from pathlib import Path
from time import monotonic, sleep

import pytest
import pymupdf as pdf
from PyQt6.QtCore import QSettings, QTimer
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app.pdfcontentcompareui import PdfContentCompareDialog
from app.output import OperationCancelled
from pdfcompare_acceptance_samples import NORMAL, REFLOW, paragraph, render, scan, scenarios


@pytest.fixture(scope='session')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    return app


@pytest.fixture(scope='module')
def sources(tmp_path_factory):
    root = tmp_path_factory.mktemp('content-ui-sources')
    result = {}
    for case in scenarios():
        if case['id'] not in ('S02', 'S03', 'S05', 'S07', 'S08', 'S09', 'S14'):
            continue
        folder = root / case['id']; folder.mkdir()
        for side in ('old', 'new'):
            path = folder / f'{side}.pdf'
            render(path, case[side], REFLOW if side == 'new' and case.get('reflow') else NORMAL)
            if case.get('mixed'):
                scan(path, dpi=120 if side == 'new' else 144, noisy=side == 'new', mixed=True)
        result[case['id']] = folder
    return result


def host_window(qapp, tmp_path, monkeypatch):
    from app import storage, ui
    settings = QSettings(str(tmp_path / 'ui.ini'), QSettings.Format.IniFormat)
    settings.setValue('updates/automatic', False); settings.setValue('appearance/theme', 'forest')
    monkeypatch.setattr(storage, '_settings', lambda: settings)
    monkeypatch.setattr(ui, 'QSettings', lambda *args: settings)
    if os.environ.get('ZHIXU_UI_CAPTURE_DIR'):
        from PyQt6.QtGui import QFont, QFontDatabase
        for name in ('msyh.ttc', 'segoeui.ttf'):
            path = Path('C:/Windows/Fonts') / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
        qapp.setFont(QFont('Microsoft YaHei UI', 10))
    return ui.MainWindow(), settings


def capture(qapp, dialog, name):
    target = os.environ.get('ZHIXU_UI_CAPTURE_DIR')
    if target:
        folder = Path(target); folder.mkdir(parents=True, exist_ok=True)
        qapp.processEvents(); assert dialog.grab().save(str(folder / (name + '.png')))


def open_source(dialog, folder):
    for field, side in zip(dialog.paths, ('old', 'new')):
        field.setText(str(folder / f'{side}.pdf'))
    dialog.start.click()


def test_real_document_menu_shows_specific_change_not_pixel_overlay(qapp, tmp_path, monkeypatch, sources):
    host, _ = host_window(qapp, tmp_path, monkeypatch)
    chosen = iter((sources['S08'] / 'old.pdf', sources['S08'] / 'new.pdf'))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(next(chosen)), 'PDF'))
    errors, ticks = [], []
    timer = QTimer(host); timer.timeout.connect(lambda: ticks.append(1)); timer.start(10)
    def exercise():
        dialog = host.findChild(PdfContentCompareDialog)
        try:
            assert dialog is not None and not dialog.start.isEnabled()
            dialog.file_buttons[0].click(); dialog.file_buttons[1].click(); dialog.start.click()
            assert ticks and dialog.summary['counts'] == {'modified': 1}
            assert dialog.rows.count() == 1
            item = dialog.rows.item(0).text()
            assert all(value in item for value in ('第二标段付款', '120000元', '125000元'))
            assert '120000元' in dialog.contexts[0].text() and '125000元' in dialog.contexts[1].text()
            assert '第 2 页' in dialog.text_labels[0].text() and '第 2 页' in dialog.text_labels[1].text()
            assert dialog.preview['old']['page'] == dialog.preview['new']['page'] == 2
            assert all(v.scene().items() for v in dialog.views)
            capture(qapp, dialog, 's08-main-menu-content')
            dialog.resize(980, 680); qapp.processEvents()
            capture(qapp, dialog, 's08-compact')
            from app.themes import theme_manager
            theme_manager().apply('night')
            dialog.resize(1280, 840); qapp.processEvents()
            assert '120000元' in dialog.contexts[0].text() and '125000元' in dialog.contexts[1].text()
            capture(qapp, dialog, 's08-night')
            theme_manager().apply('forest')
        except Exception as exc:
            errors.append(exc)
        finally:
            if dialog:
                dialog.accept()
    QTimer.singleShot(0, exercise)
    try:
        host.open_pdf_compare()
        assert not errors, errors
    finally:
        timer.stop(); host.close()


def test_reflow_move_and_ambiguous_candidates_are_distinct(qapp, tmp_path, monkeypatch, sources):
    host, _ = host_window(qapp, tmp_path, monkeypatch)
    dialog = PdfContentCompareDialog(host); dialog.show()
    try:
        open_source(dialog, sources['S02'])
        assert dialog.rows.count() == 0 and '未检出正文修改' in dialog.result_summary.text()
        assert not any(view.marker for view in dialog.views)
        capture(qapp, dialog, 's02-reflow-no-content-change')
        open_source(dialog, sources['S05'])
        assert dialog.rows.count() == 1
        assert '1 页' in dialog.location.text() and '3 页' in dialog.location.text()
        assert dialog.preview['old']['page'] == 1 and dialog.preview['new']['page'] == 3
        capture(qapp, dialog, 's05-move-and-amount')
        open_source(dialog, sources['S09'])
        assert '待确认' in dialog.result_summary.text() and '尚未确认' in dialog.location.text()
        assert dialog.page_selectors[0].count() == 2
        dialog.page_selectors[0].setCurrentIndex(1)
        assert dialog.preview['old']['page'] == 2
        dialog.whole.click()
        assert dialog.page_selectors[0].currentData() == 2 and dialog.preview['old']['page'] == 2
    finally:
        dialog.close(); host.close()


def test_add_delete_hides_missing_side_and_cancel_keeps_result(qapp, tmp_path, monkeypatch, sources):
    host, _ = host_window(qapp, tmp_path, monkeypatch)
    dialog = PdfContentCompareDialog(host); dialog.show()
    try:
        open_source(dialog, sources['S07'])
        assert dialog.rows.count() == 2
        for i, item in enumerate(dialog.items):
            dialog.rows.setCurrentRow(i)
            missing = 0 if item['kind'] == 'added' else 1
            assert dialog.views[missing].isHidden() and not dialog.empty[missing].isHidden()
            assert not dialog.preview['old' if missing == 0 else 'new']['available']
        previous = dialog.summary
        def cancel():
            task = getattr(dialog, '_task_dialog', None)
            if task:
                task.request_cancel()
        QTimer.singleShot(1, cancel); dialog.start.click()
        assert dialog.summary is previous and '取消' in dialog.status.text()
        assert dialog.start.isEnabled()
    finally:
        dialog.close(); host.close()
    assert dialog.summary is None and not dialog.cache and dialog.images is None


def test_stale_source_cannot_reuse_cached_evidence(qapp, tmp_path, monkeypatch, sources):
    from shutil import copyfile
    host, _ = host_window(qapp, tmp_path, monkeypatch)
    folder = tmp_path / 'copy'; folder.mkdir()
    for side in ('old', 'new'):
        copyfile(sources['S03'] / f'{side}.pdf', folder / f'{side}.pdf')
    dialog = PdfContentCompareDialog(host)
    warnings = []; monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[-1]))
    try:
        open_source(dialog, folder)
        assert dialog.rows.count() == 4
        copyfile(sources['S02'] / 'new.pdf', folder / 'old.pdf')
        dialog.rows.setCurrentRow(1)
        assert warnings and dialog.stale and not dialog.rows.isEnabled()
        assert all(view.isHidden() for view in dialog.views)
        assert '重新比较' in dialog.result_summary.text()
    finally:
        dialog.close(); host.close()


def test_password_and_rotated_evidence_use_real_worker(qapp, tmp_path):
    from app.pdfworker import compare_pdf_contents, render_content_change
    old, new = tmp_path / 'old.pdf', tmp_path / 'new.pdf'
    for path, amount in ((old, '120000'), (new, '125000')):
        with pdf.open() as doc:
            page = doc.new_page(width=360, height=500)
            page.insert_text((45, 70), '第1条 付款条件', fontname='china-s', fontsize=16)
            page.insert_text((45, 120), '合同金额为' + amount + '元。', fontname='china-s', fontsize=12)
            page.set_rotation(90)
            doc.save(path, encryption=pdf.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='reader', permissions=pdf.PDF_PERM_PRINT)
    with pytest.raises(Exception, match='密码'):
        compare_pdf_contents(old, new, use_ocr=False)
    result = compare_pdf_contents(old, new, passwords=('owner', 'owner'), use_ocr=False)
    assert result['counts'] == {'modified': 1}
    meta, images = render_content_change(result, result['changes'][0], passwords=('owner', 'owner'))
    assert images['old'].width > images['old'].height
    for side in ('old', 'new'):
        assert meta[side]['width'] * meta[side]['height'] <= 2_000_000
        assert all(0 <= b[0] < b[2] <= images[side].width and 0 <= b[1] < b[3] <= images[side].height for b in meta[side]['boxes'])


def test_repeated_amount_highlights_actual_second_occurrence(qapp, tmp_path, monkeypatch):
    host, _ = host_window(qapp, tmp_path, monkeypatch)
    for side, amount in [('old', '120000'), ('new', '125000')]:
        render(tmp_path / f'{side}.pdf', [paragraph('a', '付款条件', f'合同金额120000元，定金{amount}元。')], NORMAL)
    dialog = PdfContentCompareDialog(host)
    try:
        open_source(dialog, tmp_path)
        assert dialog.summary['counts'] == {'modified': 1}
        assert dialog.contexts[0].text().index('<b ') > dialog.contexts[0].text().index('定金')
        from app.themes import theme_manager
        theme_manager().apply('night')
        assert dialog.contexts[0].text().index('<b ') > dialog.contexts[0].text().index('定金')
        theme_manager().apply('forest')
    finally:
        dialog.close(); host.close()


def test_move_with_uncertain_text_does_not_claim_text_unchanged(qapp, tmp_path, monkeypatch, sources):
    import app.pdfcontentcompareui as ui
    real_compare = ui.compare_pdf_contents
    def uncertain(*args, **kwargs):
        result = real_compare(*args, **kwargs)
        for change in result['changes']:
            if change['kind'] == 'modified':
                change['kind'] = 'review'; change['reason'] = 'OCR 文字差异需要核对'
        result['counts'] = {'review': 1, 'moved': 1}
        return result
    monkeypatch.setattr(ui, 'compare_pdf_contents', uncertain)
    host, _ = host_window(qapp, tmp_path, monkeypatch)
    dialog = PdfContentCompareDialog(host)
    try:
        open_source(dialog, sources['S05'])
        assert dialog.rows.count() == 1 and '待确认' in dialog.result_summary.text()
        assert '需要核对' in dialog.detail_title.text() and '需要核对' in dialog.location.text()
        assert '未改' not in dialog.location.text() and '待核对' in dialog.text_labels[1].text()
    finally:
        dialog.close(); host.close()


def test_optional_ocr_real_window_and_cancelled_process_tree(qapp, tmp_path, monkeypatch, sources):
    runtime = os.environ.get('ZHIXU_TEST_OCR_RUNTIME')
    if os.name != 'nt' or not runtime:
        pytest.skip('Windows with an existing optional OCR bundle is required')
    import win32api, win32con, win32job, win32process, pywintypes
    from app.pdfworker import compare_pdf_contents
    host, settings = host_window(qapp, tmp_path, monkeypatch)
    settings.setValue('storage/ocr', runtime); settings.sync()
    dialog = PdfContentCompareDialog(host); dialog.show()
    try:
        open_source(dialog, sources['S14'])
        assert dialog.summary['counts'] == {'modified': 1}
        assert '120000元' in dialog.contexts[0].text() and '125000元' in dialog.contexts[1].text()
        assert dialog.preview['old']['page'] == dialog.preview['new']['page'] == 3
        assert dialog.summary['complete']
        capture(qapp, dialog, 's14-real-ocr-amount')
        jobs, owned = [], set()
        create_job = win32job.CreateJobObject
        def create(*args):
            job = create_job(*args); jobs.append(job); return job
        monkeypatch.setattr(win32job, 'CreateJobObject', create)
        cancelled = []
        def report(*args):
            pids = win32job.QueryInformationJobObject(jobs[0], win32job.JobObjectBasicProcessIdList)
            if len(pids) >= 2:
                owned.update(pids); cancelled.append(True)
        with pytest.raises(OperationCancelled):
            compare_pdf_contents(sources['S14'] / 'old.pdf', sources['S14'] / 'new.pdf', report, lambda: bool(cancelled))
        assert len(owned) >= 2, 'OCR must belong to the comparison job'
        deadline = monotonic() + 5
        while owned and monotonic() < deadline:
            for pid in tuple(owned):
                try:
                    handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION, False, pid)
                except pywintypes.error as exc:
                    if exc.winerror not in (87, 1168):
                        raise
                    owned.remove(pid); continue
                try:
                    if win32process.GetExitCodeProcess(handle) != win32con.STILL_ACTIVE:
                        owned.remove(pid)
                finally:
                    handle.Close()
            sleep(.02)
        assert not owned, 'Cancelled comparison left worker processes running'
    finally:
        dialog.close(); host.close()
