"""PDF link regressions: generated documents and intercepted external launches only."""
from dataclasses import replace

import pymupdf as p
import pytest
from PyQt6.QtCore import QPoint, QPointF, QThreadPool, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app.pagegeometry import geometry_edit
from app.pdfio import PdfStore, WriteOptions
from app.ui import MainWindow
from app.workspace import PageEdit


WEB = p.Rect(30, 40, 170, 65)
GOTO = p.Rect(30, 90, 170, 115)
MAIL = p.Rect(30, 140, 170, 165)
SCRIPT = p.Rect(30, 190, 170, 215)
FILE = p.Rect(30, 240, 170, 265)
SELF = p.Rect(30, 290, 170, 315)
NAMED = p.Rect(30, 340, 170, 365)


def generated_pdf(path, rotation=0, cropped=False, named=True):
    with p.open() as doc:
        for _ in range(3):
            doc.new_page(width=300, height=600)
        page = doc[0]
        for area, text, uri in ((WEB, 'Generated web link', 'https://example.invalid/generated'),
                                (MAIL, 'Generated email', 'mailto:test@example.invalid'),
                                (SCRIPT, 'Unsupported script', 'javascript:alert(1)'),
                                (FILE, 'Unsupported file', 'file:///generated-only.exe')):
            page.insert_text((area.x0 + 4, area.y1 - 7), text)
            page.insert_link(dict(kind=p.LINK_URI, uri=uri, **{'from': area}))
        destinations = [(GOTO, 2, (45, 500)), (SELF, 0, (45, 500))]
        if named:
            destinations.append((NAMED, 2, (45, 500)))
        for area, target, point in destinations:
            page.insert_text((area.x0 + 4, area.y1 - 7), 'Go to generated target')
            page.insert_link(dict(kind=p.LINK_GOTO, page=target, to=p.Point(point), **{'from': area}))
        page = doc.reload_page(page)
        if named:
            named_link = next(link for link in page.get_links() if link['from'] == NAMED)
            doc.xref_set_key(doc.pdf_catalog(), 'Names/Dests',
                             f'<< /Names [(generated-target) [{doc.page_xref(2)} 0 R /XYZ 45 100 0]] >>')
            doc.xref_set_key(named_link['xref'], 'A', '<< /S /GoTo /D (generated-target) >>')
        if cropped:
            page.set_cropbox(p.Rect(10, 20, 290, 580))
        page.set_rotation(rotation)
        doc.save(path)
    return path


@pytest.fixture(scope='session')
def link_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def link_window(link_app, tmp_path, monkeypatch):
    path = generated_pdf(tmp_path / 'generated-links.pdf')
    window = MainWindow()
    refs = window.store.load(str(path))
    window.ws.open_replace(refs, str(path))
    window.resize(1100, 850)
    window.show()
    window.refresh()
    link_app.processEvents()
    for ref in refs:
        window.store.prepare_annotations(ref)
    opened = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(url.toString()) or True)
    window.opened_links = opened
    yield window
    window.ws.reset()
    window.close()
    QThreadPool.globalInstance().waitForDone(30000)
    link_app.processEvents()


def position(window, area, page=0):
    origin = window.preview_view.page_rects[page].topLeft()
    return window.preview_view.mapFromScene(origin + QPointF((area.x0 + area.x1) / 2, (area.y0 + area.y1) / 2))


def click(window, area, page=0, modifier=Qt.KeyboardModifier.NoModifier):
    QTest.mouseClick(window.preview_view.viewport(), Qt.MouseButton.LeftButton, modifier, position(window, area, page))


@pytest.mark.parametrize('area,uri', [(WEB, 'https://example.invalid/generated'), (MAIL, 'mailto:test@example.invalid')])
def test_link_hover_and_click_open_only_on_release(link_window, area, uri):
    window = link_window
    point = position(window, area)
    window.reader_interaction.text_cursor(point)
    assert window.preview_view.viewport().cursor().shape() == Qt.CursorShape.PointingHandCursor
    assert window.preview_view.viewport().toolTip() == uri
    QTest.mousePress(window.preview_view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert not window.opened_links
    QTest.mouseRelease(window.preview_view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert window.opened_links == [uri]
    assert window.text_selection.start is None and window.text_selection._pending is None


def test_dragging_link_text_selects_without_opening(link_window):
    window = link_window
    window.store.text_snapshot(window.ws.pages[0], include_tables=False)
    start = position(window, p.Rect(31, 41, 33, 43))
    end = position(window, p.Rect(167, 63, 169, 65))
    QTest.mousePress(window.preview_view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(window.preview_view.viewport(), end)
    QTest.mouseRelease(window.preview_view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert not window.opened_links
    assert 'Generated web link' in window.text_selection.text


def test_dragging_away_and_back_does_not_activate_link(link_window):
    window = link_window
    point = position(window, WEB)
    QTest.mousePress(window.preview_view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseMove(window.preview_view.viewport(), point + QPoint(40, 20))
    QTest.mouseMove(window.preview_view.viewport(), point)
    QTest.mouseRelease(window.preview_view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert not window.opened_links


@pytest.mark.parametrize('modifier', [Qt.KeyboardModifier.AltModifier, Qt.KeyboardModifier.ControlModifier,
                                      Qt.KeyboardModifier.ShiftModifier])
def test_modified_gestures_do_not_open_links(link_window, modifier):
    click(link_window, WEB, modifier=modifier)
    assert not link_window.opened_links


@pytest.mark.parametrize('area', [SCRIPT, FILE])
def test_document_cannot_launch_scripts_or_local_files(link_window, area):
    click(link_window, area)
    assert not link_window.opened_links
    assert '暂不支持' in link_window.statusBar().currentMessage()


@pytest.mark.parametrize('area', [GOTO, NAMED])
def test_internal_and_named_links_follow_reordered_pages(link_window, area):
    window = link_window
    window.ws.move_page(2, 1)
    window.refresh()
    window.go_to_page(1)
    click(window, area)
    assert window.ws.preview_index() == 1
    assert window.ws.pages[1].source_page == 2
    assert not window.opened_links


def test_internal_link_prefers_its_own_copy_group(link_window):
    window = link_window
    originals = list(window.ws.pages)
    copies = [replace(ref, page_id=ref.page_id + 10000, copy_group=99) for ref in originals]
    window.ws.open_replace([*originals, *copies], window.ws.path)
    window.refresh()
    window.go_to_page(4)
    click(window, GOTO, 3)
    assert window.ws.preview_index() == 5


@pytest.mark.parametrize('copies_first', [False, True])
def test_saved_links_keep_copy_groups_and_fall_back_for_uncopied_targets(tmp_path, copies_first):
    from app.workspace import Workspace
    source = generated_pdf(tmp_path / 'generated-copy-links.pdf')
    original_bytes = source.read_bytes()
    store = PdfStore()
    try:
        originals = store.load(str(source))
        copies = Workspace._unique_pages(originals, originals)
        partial = Workspace._unique_pages(originals[:1], originals)
        pages = [*copies, *originals, *partial] if copies_first else [*originals, *copies, *partial]
        target = tmp_path / 'saved-copy-links.pdf'
        store.write(pages, str(target))
        with p.open(target) as doc:
            for row, target_page in ((0, 2), (3, 5), (6, 2)):
                links = doc[row].get_links()
                for area in (GOTO, NAMED):
                    link = next(link for link in links if link['from'] == area)
                    assert link['page'] == target_page, (row, area, link)
                self_link = next(link for link in links if link['from'] == SELF)
                assert self_link['page'] == row
        assert source.read_bytes() == original_bytes
    finally:
        store.close_all()


def test_removed_target_has_feedback_without_wrong_page_jump(link_window):
    window = link_window
    window.ws.select_index(2)
    window.ws.delete_selected()
    window.refresh()
    window.go_to_page(1)
    click(window, GOTO)
    assert window.ws.preview_index() == 0
    assert '已不在' in window.statusBar().currentMessage()


def test_first_click_awaits_metadata_without_requiring_a_second_click(link_window, monkeypatch, link_app):
    window = link_window
    QThreadPool.globalInstance().waitForDone(10000)
    link_app.processEvents()
    window._preparation_timer.stop()
    with window.store._annotation_lock:
        window.store._annotation_cache.clear()
    requests = []
    original = window.rendering.request
    def tracked_request(*args, **kwargs):
        if kwargs.get('kind') == 'metadata' and args[2][0] == 'reader_link':
            requests.append(args[2])
        return original(*args, **kwargs)
    monkeypatch.setattr(window.rendering, 'request', tracked_request)
    click(window, WEB)
    for _ in range(200):
        if window.opened_links:
            break
        QTest.qWait(10)
    assert requests
    assert window.opened_links == ['https://example.invalid/generated']


def test_late_metadata_does_not_open_a_link_after_document_change(link_window, monkeypatch, link_app):
    from app.rendering import RenderResult
    window = link_window
    QThreadPool.globalInstance().waitForDone(10000)
    link_app.processEvents()
    with window.store._annotation_lock:
        window.store._annotation_cache.clear()
    requests = []
    original = window.rendering.request
    def delayed_request(*args, **kwargs):
        if kwargs.get('kind') == 'metadata' and args[2][0] == 'reader_link':
            requests.append(args)
            return
        return original(*args, **kwargs)
    monkeypatch.setattr(window.rendering, 'request', delayed_request)
    click(window, WEB)
    assert requests
    window.ws.reset()
    window.refresh()
    window.reader_interaction._metadata_ready(requests[0][2], RenderResult(window.rendering.generation))
    assert not window.opened_links


@pytest.mark.parametrize('rotation,extra', [(0, 90), (90, 180), (180, 270), (270, 0)])
@pytest.mark.parametrize('edit_kind', ['none', 'note', 'resize'])
def test_links_survive_rotation_crop_and_page_edits(tmp_path, rotation, extra, edit_kind):
    path = generated_pdf(tmp_path / 'rotated-links.pdf', rotation, cropped=True, named=edit_kind != 'resize')
    store = PdfStore()
    try:
        ref = store.load(str(path))[0].rotated(extra)
        if edit_kind == 'note':
            ref = ref.edited(PageEdit('note', points=((220, 400),), text='Generated note'))
        elif edit_kind == 'resize':
            size = store.page_size(ref)
            ref = ref.edited(geometry_edit(store, ref, size=(size[0] * 2, size[1] * 2)))
        summary = store.prepare_annotations(ref)
        links = summary['links']
        assert sum(link['kind'] == p.LINK_GOTO for link in links) == (2 if edit_kind == 'resize' else 3)
        assert sum(link.get('uri') == 'https://example.invalid/generated' for link in links) == 1
        assert not any(link.get('uri', '').startswith('about:blank') for link in links)
        target = next(link for link in links if link.get('page') == 2)
        assert target['to'] == pytest.approx((45, 100))
        with p.open(path) as original:
            original_web = next(link for link in original[0].get_links()
                                if link.get('uri') == 'https://example.invalid/generated')
            raw_center = original_web['from'].tl + (original_web['from'].br - original_web['from'].tl) / 2
            from app.pdfstructure import page_pdf_matrix
            raw_center *= ~page_pdf_matrix(original[0])
        mapped = raw_center * p.Matrix(summary['source_pdf_matrix'])
        web = next(link for link in links if link.get('uri') == 'https://example.invalid/generated')
        assert p.Rect(web['from']).contains(mapped)
    finally:
        store.close_except(set())


def test_edited_source_and_target_keep_exact_internal_anchor(link_window, tmp_path, link_app):
    window = link_window
    path = generated_pdf(tmp_path / 'resizable.pdf', named=False)
    window.ws.open_replace(window.store.load(str(path)), str(path))
    window.ws.edit_page(0, PageEdit('note', points=((220, 400),), text='Generated source note'), '测试批注')
    target = window.ws.pages[2]
    window.ws.edit_page(2, geometry_edit(window.store, target, size=(600, 1200)), '测试调整页面')
    window.refresh()
    window._set_zoom(1)
    window.go_to_page(1)
    link_app.processEvents()
    window.store.prepare_annotations(window.ws.pages[0])
    click(window, GOTO)
    for _ in range(200):
        if window.reader_interaction.link_jump is None:
            break
        QTest.qWait(10)
    assert window.ws.preview_index() == 2
    # (45, 500) in the original page becomes (90, 1000) after 2x resizing.
    scene_point = window.preview_view.page_rects[2].topLeft() + QPointF(90, 1000)
    visible = window.preview_view.mapToScene(window.preview_view.viewport().rect()).boundingRect()
    assert visible.contains(scene_point)


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_internal_anchor_matches_rotated_cropped_target_content(link_window, tmp_path, link_app, rotation):
    window = link_window
    path = generated_pdf(tmp_path / 'rotated-target.pdf', named=False)
    with p.open(path) as doc:
        page = doc[2]
        page.insert_text((45, 500), 'TARGET')
        page.set_cropbox(p.Rect(10, 20, 290, 580))
        page.set_rotation(rotation)
        doc.save(path, incremental=True, encryption=p.PDF_ENCRYPT_KEEP)
        rectangle = page.search_for('TARGET')[0] * page.rotation_matrix
        matrix = p.Matrix(1, 1).prerotate(90)
        rotated = page.rect * matrix
        matrix *= p.Matrix(1, 0, 0, 1, -rotated.x0, -rotated.y0)
        marker = rectangle * matrix
    refs = window.store.load(str(path))
    refs[2] = refs[2].rotated(90)
    window.ws.open_replace(refs, str(path))
    window.refresh()
    window._set_zoom(2)
    window.go_to_page(1)
    link_app.processEvents()
    window.store.prepare_annotations(refs[0])
    click(window, GOTO)
    for _ in range(200):
        if window.reader_interaction.link_jump is None:
            break
        QTest.qWait(10)
    assert window.ws.preview_index() == 2
    scene_point = window.preview_view.page_rects[2].topLeft() + QPointF(marker.x0, marker.y1)
    visible = window.preview_view.mapToScene(window.preview_view.viewport().rect()).boundingRect()
    assert visible.contains(scene_point)


def test_redacted_links_do_not_remain_clickable(tmp_path):
    from app.mosaic import mosaic_edit
    store = PdfStore()
    try:
        ref = store.load(str(generated_pdf(tmp_path / 'redacted-links.pdf')))[0]
        ref = ref.edited(mosaic_edit((20, 30, 180, 120), style='solid'))
        links = store.prepare_annotations(ref)['links']
        assert not any(link.get('uri') == 'https://example.invalid/generated' for link in links)
        assert sum(link.get('page') == 2 for link in links) == 1  # Named link survives elsewhere.
    finally:
        store.close_except(set())


def test_unparsed_action_does_not_break_other_links_after_edit(tmp_path):
    path = generated_pdf(tmp_path / 'unsupported-action.pdf')
    with p.open(path) as doc:
        page = doc[0]
        page.insert_link(dict(kind=p.LINK_URI, uri='https://example.invalid/placeholder',
                              **{'from': p.Rect(30, 390, 170, 415)}))
        page = doc.reload_page(page)
        unsupported = next(link for link in page.get_links()
                           if link.get('uri') == 'https://example.invalid/placeholder')
        doc.xref_set_key(unsupported['xref'], 'A', r'<< /S /JavaScript /JS (void\(0\)) >>')
        doc.save(path, incremental=True, encryption=p.PDF_ENCRYPT_KEEP)
    store = PdfStore()
    try:
        ref = store.load(str(path))[0].edited(PageEdit('note', points=((220, 400),), text='Generated note'))
        links = store.prepare_annotations(ref)['links']
        assert sorted(link['page'] for link in links if link['kind'] == p.LINK_GOTO) == [0, 2, 2]
        assert any(link.get('uri') == 'https://example.invalid/generated' for link in links)
        assert not any(link.get('uri', '').startswith('about:blank') for link in links)
    finally:
        store.close_except(set())


def test_save_rebuilds_navigation_after_page_reordering(tmp_path):
    store = PdfStore()
    try:
        refs = store.load(str(generated_pdf(tmp_path / 'source.pdf')))
        before = store.prepare_annotations(refs[0])
        reordered = [refs[0], refs[2], refs[1]]
        store.write(reordered, str(tmp_path / 'saved.pdf'), options=WriteOptions())
        saved = store.load(str(tmp_path / 'saved.pdf'))
        mapping = {old.render_key: new.render_key for old, new in zip(reordered, saved)}
        store.remap_annotations(mapping)
        assert store.cached_annotations(saved[0]) is None
        after = store.prepare_annotations(saved[0])
        assert next(link['page'] for link in before['links'] if link['from'] == tuple(GOTO)) == 2
        assert next(link['page'] for link in after['links'] if link['from'] == tuple(GOTO)) == 1
    finally:
        store.close_except(set())
