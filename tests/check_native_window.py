"""独立 Windows 回归检查：python tests/check_native_window.py。"""
import json
import os
from pathlib import Path
import sys

os.environ['QT_QPA_PLATFORM'] = 'windows'
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packaging'))
from PyQt6.QtWidgets import QApplication
from app.ui import MainWindow
from native_window_check import check_native_window


if __name__ == '__main__':
    qapp = QApplication([])
    window = MainWindow()
    window.resize(1100, 760)
    try:
        result = check_native_window(window, qapp)
        print('PASS:', json.dumps(result))
    finally:
        window.close()
