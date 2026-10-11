import os
import sys
from pathlib import Path

from app.startup import mark

mark("python_entry")

# Python 3.8+ already marks this process DPI-aware. Qt 6 then tries
# PER_MONITOR_AWARE_V2 and Windows returns Access Denied.
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "windows:dpiawareness=2")

from PyQt6.QtCore import QTimer, Qt, QtMsgType, qInstallMessageHandler
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtWidgets import QApplication

from app import APP_NAME, APP_ORGANIZATION, __version__
from app.icons import brand_icon


def _qt_messages(mode, context, message: str) -> None:
    if "SetProcessDpiAwarenessContext" in message:
        return
    stream = sys.stderr if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg) else sys.stdout
    if stream is None:
        return
    stream.write(message + "\n")


def _window_icon() -> QIcon:
    names = []
    if getattr(sys, "frozen", False):
        names.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "app.ico")
    names.append(Path(__file__).resolve().parent.parent / "packaging" / "app.ico")
    for path in names:
        if path.is_file():
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    return brand_icon()


def startup_pdf_paths(argv: list[str]) -> list[str]:
    """返回由资源管理器、快捷方式或命令行传入的本机 PDF。"""
    paths = []
    for value in argv[1:]:
        if not value or value.startswith("-"):
            continue
        path = os.path.abspath(value)
        if path.lower().endswith(".pdf") and os.path.isfile(path):
            paths.append(path)
    return paths


def _warm_pdf_engine() -> None:
    from app.pdfio import prepare_pdf_engine
    try:
        prepare_pdf_engine()
    except Exception:
        # 预热失败不挡住窗口；实际打开时沿用正常的错误提示。
        mark("pdf_engine_unavailable")


def _start_pdf_warmup() -> None:
    from threading import Thread
    Thread(target=_warm_pdf_engine, name="pdf-engine-warmup", daemon=True).start()


def main() -> None:
    mark("qt_imports_ready")
    if len(sys.argv) == 3 and sys.argv[1] == "--word-worker":
        from app.wordconvert import worker_main
        worker_main(sys.argv[2])
        return
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    qInstallMessageHandler(_qt_messages)
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication.instance() or QApplication(sys.argv)
    mark("qapplication_ready")
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APP_ORGANIZATION)
    app.setWindowIcon(_window_icon())
    app.setStyle("Fusion")
    if len(sys.argv) > 1 and sys.argv[1] == "--pdf-shell-server":
        from PyQt6.QtWidgets import QMessageBox
        try:
            from app.pdfshell import serve
            code = serve(app)
        except Exception as exc:
            QMessageBox.critical(None, "右键入口无法启动", f"请重新安装纸序 PDF，并勾选 PDF 右键入口。\n{exc}")
            code = 1
        sys.exit(code)
    from app.ui import MainWindow, STYLESHEET
    mark("ui_import_ready")
    if len(sys.argv) > 1 and sys.argv[1] == "--word-to-pdf":
        from app.wordui import WordConverterWindow
        win = WordConverterWindow(sys.argv[2:])
        win.setStyleSheet(STYLESHEET)
        win.show()
        sys.exit(app.exec())
    win = MainWindow()
    shell_mode = {"--split-pdf": "split", "--merge-pdf": "merge"}.get(sys.argv[1] if len(sys.argv) > 1 else "")
    paths = sys.argv[2:] if shell_mode else startup_pdf_paths(sys.argv)
    if not paths:
        win.first_painted.connect(_start_pdf_warmup, Qt.ConnectionType.QueuedConnection)
    if paths:
        win.showMaximized()
    else:
        win.show()
    mark("window_shown")
    if shell_mode:
        QTimer.singleShot(0, lambda: win.open_shell_files(paths, shell_mode))
    elif paths:
        # 窗口先显示，再在事件循环中打开文件，双击大 PDF 时立即有可见反馈。
        QTimer.singleShot(0, lambda: win._ingest(paths, replace=True))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
