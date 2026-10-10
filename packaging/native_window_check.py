"""在程序自己的测试窗口上验证 Windows 边缘命中和原生拖拽缩放。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import threading
import time


def check_native_window(window, qapp) -> dict:
    window.showMaximized()
    qapp.processEvents()
    assert window.isMaximized()
    window.showNormal()
    qapp.processEvents()
    user32 = ctypes.windll.user32
    hwnd = int(window.winId())
    user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    user32.GetWindowRect.restype = wt.BOOL
    user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.SendMessageW.restype = wt.LPARAM
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.PostMessageW.restype = wt.BOOL
    rect = wt.RECT()
    assert user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    assert user32.GetWindowLongPtrW(hwnd, -16) & 0x40000, "Missing native resizing style"
    points = [(1, 1, 13), (width // 2, 1, 12), (width - 2, 1, 14),
              (1, height // 2, 10), (width - 2, height // 2, 11),
              (1, height - 2, 16), (width // 2, height - 2, 15), (width - 2, height - 2, 17)]
    for x, y, expected in points:
        sx, sy = rect.left + x, rect.top + y
        hit = user32.SendMessageW(hwnd, 0x84, 0, (sx & 0xffff) | ((sy & 0xffff) << 16))
        assert hit == expected, (x, y, expected, hit)

    before = window.size()
    def finish_resize():
        # 定向发给这个空测试窗口，不发送全局键鼠输入，也不操作其他窗口。
        time.sleep(.1)
        for key in (0x27, 0x28, 0x27, 0x28):
            user32.PostMessageW(hwnd, 0x100, key, 1)
            user32.PostMessageW(hwnd, 0x101, key, 1)
            time.sleep(.03)
        user32.PostMessageW(hwnd, 0x100, 0x0D, 1)
        user32.PostMessageW(hwnd, 0x101, 0x0D, 1)
        time.sleep(.3)
        user32.PostMessageW(hwnd, 0x1F, 0, 0)
    worker = threading.Thread(target=finish_resize, daemon=True)
    worker.start()
    x, y = rect.right - 2, rect.bottom - 2
    # 与点击右下角相同的原生非客户区消息，启动 Windows 自己的缩放循环。
    user32.SendMessageW(hwnd, 0xA1, 17, (x & 0xffff) | ((y & 0xffff) << 16))
    worker.join(2)
    qapp.processEvents()
    after = window.size()
    assert after.width() > before.width() and after.height() > before.height(), "Native drag did not resize"
    window.hide()
    return {"edge_directions": 8, "restored_from_maximized": True, "dpi_scale": window.devicePixelRatioF(),
            "before": [before.width(), before.height()], "after": [after.width(), after.height()]}
