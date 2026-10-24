"""Use real Windows font metrics without showing a desktop window."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native font metrics')
@pytest.mark.parametrize('scale', ['1', '1.5', '2'])
def test_sidebar_wrapped_text_keeps_full_height(tmp_path, scale):
    script = r'''
import sys
from pathlib import Path
import pymupdf
from PyQt6.QtCore import QSettings, QThreadPool, Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication, QLabel
import app.ui as ui
from app import storage

folder = Path(sys.argv[1])
QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
app = QApplication([])
app.setStyle('Fusion')
preferences = lambda *args: QSettings(str(folder / 'preferences.ini'), QSettings.Format.IniFormat)
ui.QSettings = storage._settings = preferences
preferences().setValue('updates/automatic', False)
preferences().setValue('storage/cache', str(folder / 'cache'))
source = folder / 'generated.pdf'
with pymupdf.open() as doc:
    doc.new_page().insert_text((50, 80), 'GENERATED LAYOUT CHECK')
    doc.save(source)
window = ui.MainWindow()
window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
window.show()
try:
    assert window._load_one(str(source))
    for width, height in ((280, 680), (320, 840), (280, 840)):
        window.tools_panel.setFixedWidth(width)
        window.resize(1100, height)
        for index in (2, 6, -1, 2):
            window.open_export_tool(index)
            for _ in range(5):
                app.processEvents()
            if index < 0:
                continue
            assert window.tools_panel.isVisible()
            labels = [label for label in window.export_tools.pages[index].findChildren(QLabel)
                      if label.wordWrap() and not label.isHidden()]
            assert len(labels) >= (2 if index == 6 else 1)
            for label in labels:
                assert label.isVisible()
                required = label.heightForWidth(label.width())
                assert label.height() >= required, (
                    index, width, label.text(), label.height(), required)
finally:
    window.ws.reset()
    window.close()
    QThreadPool.globalInstance().waitForDone(10000)
    app.processEvents()
'''
    environment = os.environ.copy()
    environment.update(QT_QPA_PLATFORM='windows', QT_SCALE_FACTOR=scale,
                       LOCALAPPDATA=str(tmp_path), PYTHONIOENCODING='utf-8')
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[1], env=environment,
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
