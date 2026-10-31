"""Continuous wheel scrolling must not trigger an extra pan or zoom at a page boundary."""
import pymupdf
import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app.ui import MainWindow
from test_reading_stability import settle


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('borderless', [False, True])
@pytest.mark.parametrize('mode', ['page', 'width', 'custom'])
def test_wheel_crossing_different_page_sizes_keeps_the_document_anchor(qapp, tmp_path, mode, borderless):
    path = tmp_path / 'generated-mixed-scans.pdf'
    with pymupdf.open() as doc:
        for number, (width, height) in enumerate(((768, 1150), (765, 1155))):
            page = doc.new_page(width=width, height=height)
            page.insert_text((50, 80), f'Generated page {number + 1}', fontsize=24)
            for line in range(25):
                page.draw_line((50, 120 + line * 35), (width - 50, 120 + line * 35))
        doc.save(path)
    window = MainWindow()
    try:
        window.resize(1100, 1000)
        window.show()
        assert window.open_files_in_tabs([str(path)])
        window.immersive.borderless_action.setChecked(borderless)
        window._set_zoom(1.4) if mode == 'custom' else window._set_zoom_mode(mode)
        settle(window, qapp)
        view = window.preview_view
        scale = view.transform().m11()
        visited = {window.ws.preview_index()}
        for direction in (-1, 1):
            for step in range(60):
                position = QPointF(view.viewport().rect().center())
                wheel = QWheelEvent(position, QPointF(view.viewport().mapToGlobal(position.toPoint())),
                    QPoint(), QPoint(0, direction * 120), Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
                qapp.sendEvent(view.viewport(), wheel)
                anchor = view.mapFromScene(QPointF(0, 0))
                def check_anchor():
                    current = view.mapFromScene(QPointF(0, 0))
                    visited.add(window.ws.preview_index())
                    assert abs(current.x() - anchor.x()) <= 1 and abs(current.y() - anchor.y()) <= 1, (
                        mode, borderless, direction, step, anchor, current)
                    assert abs(view.transform().m11() - scale) < 1e-6
                for _ in range(7):
                    QTest.qWait(5)
                    check_anchor()
                bar = view.verticalScrollBar()
                if bar.value() == (bar.maximum() if direction < 0 else bar.minimum()):
                    break
            settle(window, qapp, check_anchor)
        assert visited == {0, 1}
    finally:
        window.ws.reset()
        window.close()
        qapp.processEvents()
