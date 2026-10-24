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
    from app.icons import brand_icon
    return brand_icon()


def startup_pdf_paths(argv: list[str]) -> list[str]:
    """返回由资源管理器、快捷方式或命令行传入的本机 PDF / OFD。"""
    paths = []
    for value in argv[1:]:
        if not value or value.startswith("-"):
            continue
        path = os.path.abspath(value)
        if path.lower().endswith((".pdf", ".ofd")) and os.path.isfile(path):
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
    if len(sys.argv) == 3 and sys.argv[1] == "--pdf-worker":
        from app.pdfworker import worker_main
        worker_main(sys.argv[2])
        return
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
    app.setStyle("Fusion")
    from app.runtime import prepare_profile
    try:
        prepare_profile()
    except OSError as exc:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(None, '数据目录无法使用', str(exc))
        raise SystemExit(1)
    image_mode = {'--images-to-pdf': 'pdf', '--compress-image': 'compress'}.get(sys.argv[1] if len(sys.argv) > 1 else '')
    if image_mode:
        from app.imagequick import ImageQuickDialog
        app.setWindowIcon(_window_icon())
        win = ImageQuickDialog(sys.argv[2:], image_mode)
        win.show()
        sys.exit(app.exec())
    if len(sys.argv) > 1 and sys.argv[1] == "--pdf-shell-server":
        from PyQt6.QtWidgets import QMessageBox
        try:
            from app.pdfshell import serve
            clsid = None
            if '--context-id' in sys.argv:
                from app.contextmenus import registration_plan
                position = sys.argv.index('--context-id')
                clsid = sys.argv[position + 1] if position + 1 < len(sys.argv) else ''
                if clsid != registration_plan(sys.executable).clsid:
                    raise ValueError('此右键入口的位置已改变，请从“文件 → 右键快捷工具”修复。')
            code = serve(app, clsid=clsid)
        except Exception as exc:
            from app.runtime import portable_root
            hint = ('请从“文件 → 右键快捷工具”修复此版本的菜单。' if portable_root() else
                    '请重新运行安装程序，并勾选右键快捷工具。')
            QMessageBox.critical(None, "右键入口无法启动", f"{hint}\n{exc}")
            code = 1
        sys.exit(code)
    shell_mode = {"--split-pdf": "split", "--merge-pdf": "merge"}.get(sys.argv[1] if len(sys.argv) > 1 else "")
    paths = sys.argv[2:] if shell_mode else startup_pdf_paths(sys.argv)
    router = None
    if not shell_mode and (len(sys.argv) < 2 or sys.argv[1] not in ('--word-to-pdf', '--markdown-to-pdf')):
        from app.instance import InstanceRouter
        from PyQt6.QtWidgets import QMessageBox
        try:
            router = InstanceRouter(app)
            if not router.start_or_forward(paths):
                return
        except (RuntimeError, OSError) as exc:
            QMessageBox.warning(None, '无法打开文件', str(exc))
            sys.exit(1)
    app.setWindowIcon(_window_icon())
    if paths:
        _start_pdf_warmup()
    from app.ui import MainWindow, STYLESHEET
    mark("ui_import_ready")
    if len(sys.argv) > 1 and sys.argv[1] == "--word-to-pdf":
        from app.wordui import WordConverterWindow
        win = WordConverterWindow(sys.argv[2:])
        win.setStyleSheet(STYLESHEET)
        win.show()
        sys.exit(app.exec())
    if len(sys.argv) > 1 and sys.argv[1] == "--markdown-to-pdf":
        from app.outputui import MarkdownDialog
        win = MarkdownDialog(paths=sys.argv[2:])
        win.setStyleSheet(STYLESHEET)
        win.show()
        sys.exit(app.exec())
    win = MainWindow(opening_paths=paths)
    if router is not None:
        try:
            router.attach(win)
        except RuntimeError as exc:
            QMessageBox.warning(win, '无法打开文件', str(exc))
            win.close()
            sys.exit(1)
    if not paths:
        win.first_painted.connect(_start_pdf_warmup, Qt.ConnectionType.QueuedConnection)
    win.show()
    mark("window_shown")
    win.updates.start()
    if paths or shell_mode:
        # 首屏已经是阅读区的加载状态，实际读取继续在后台进行。
        QTimer.singleShot(0, lambda: win.open_startup_files(paths, shell_mode))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
