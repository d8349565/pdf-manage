# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

from PyInstaller.building.api import COLLECT, EXE, PYZ
from PyInstaller.building.build_main import Analysis
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable,
    VarFileInfo, VarStruct, VSVersionInfo,
)

ROOT = Path(SPECPATH).resolve()
sys.path.insert(0, str(ROOT))
from app import APP_NAME, APP_SLUG, __version__

VERSION_PARTS = tuple(map(int, __version__.split(".")))
if len(VERSION_PARTS) != 3 or any(v < 0 or v > 65535 for v in VERSION_PARTS):
    raise ValueError("发布版本必须是三段数字，例如 0.3.0，各段不超过 65535")
PE_VERSION = VSVersionInfo(
    ffi=FixedFileInfo(filevers=VERSION_PARTS + (0,), prodvers=VERSION_PARTS + (0,),
                      mask=0x3F, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable("080404B0", [
        StringStruct("FileDescription", APP_NAME),
        StringStruct("FileVersion", __version__),
        StringStruct("InternalName", APP_SLUG),
        StringStruct("OriginalFilename", f"{APP_SLUG}.exe"),
        StringStruct("ProductName", APP_NAME),
        StringStruct("ProductVersion", __version__),
    ])]), VarFileInfo([VarStruct("Translation", [2052, 1200])])],
)

DROP = (
    # QtNetwork's hook can discover unrelated OpenSSL DLLs in the build environment.
    # The packaged Windows TLS backend only needs system Schannel.
    "libssl",
    "libcrypto",
    "qt6quick",
    "qt6qml",
    "qt6pdf",
    "qt6designer",
    "qt6help",
    "qt6multimedia",
    "qt6bluetooth",
    "qt6positioning",
    "qt6sensors",
    "qt6webengine",
    "qt6webview",
    "qt6websockets",
    "qt6remoteobjects",
    "qt6nfc",
    "qt6serialport",
    "qt6texttospeech",
    "qt6test",
    "qt6sql",
    "qt6xml",
    "qt6dbus",
    "qt6opengl",
    "qt6charts",
    "qt6datavisualization",
    "qt6graphs",
    "qt6spatialaudio",
    "qt6httpserver",
    "qt6networkauth",
    "qt6quick3d",
    "qt6shadertools",
    "qt6virtualkeyboard",
    "opengl32sw",
    "avcodec",
    "avformat",
    "avutil",
    "swscale",
    "swresample",
    "ffmpeg",
    "assimp",
)

KEEP_PLUGIN = (
    "plugins/platforms/qwindows",
    "plugins/imageformats/qico",
    "plugins/imageformats/qsvg",
    "plugins/imageformats/qjpeg",
    "plugins/imageformats/qtiff",
    "plugins/iconengines/qsvgicon",
    "plugins/styles/qmodernwindowsstyle",
    "plugins/tls/qschannelbackend",
)


def _hay(item) -> str:
    dest = str(item[0]).replace("\\", "/").lower()
    src = str(item[1]).replace("\\", "/").lower() if len(item) > 1 else ""
    return dest + " " + src


def keep_binary(item) -> bool:
    hay = _hay(item)
    name = Path(item[0]).name.lower()
    # Windows 10/11 自带这些系统组件。开发环境 PATH 中其他软件的 ICU/UCRT
    # 可能被误收集，覆盖系统版本后会使 QtCore 缺少导出函数而无法启动。
    if (name in {"icuuc.dll", "icuin.dll", "ucrtbase.dll"}
            or name.startswith(("api-ms-win-", "ext-ms-win-", "icudt"))):
        return False
    if any(tok in hay for tok in DROP):
        return False
    if "translations" in hay or "/qml/" in hay or hay.startswith("qml/"):
        return False
    if "pyqt6" in hay and "plugins" in hay:
        return any(part in hay for part in KEEP_PLUGIN)
    return True


def keep_data(item) -> bool:
    hay = _hay(item)
    if any(tok in hay for tok in DROP):
        return False
    if "translations" in hay or "/qml/" in hay:
        return False
    return True


DONATION_IMAGE = ROOT / "app" / "assets" / "donation.png"

a = Analysis(
    [str(ROOT / "packaging" / "run.py")],
    pathex=[str(ROOT)],
    binaries=[],
    # PyMuPDF 的 Python 模块和运行 DLL 会由 Analysis 收集。不要使用
    # collect_all()，否则会重复打包 25 MB DLL，并带入仅供开发的头文件和 .lib。
    datas=[(str(ROOT / "packaging" / "app.ico"), "."),
           (str(ROOT / "packaging" / "pdf.ico"), "."),
           # 只选择应用资源，不把目录内的其他本机文件一起打包。
           (str(ROOT / "app" / "assets" / "app.png"), "app/assets"),
           (str(ROOT / "app" / "assets" / "pdf.svg"), "app/assets"),
           (str(ROOT / "packaging" / "licenses"), "licenses")]
          + ([(str(DONATION_IMAGE), "app/assets")] if DONATION_IMAGE.is_file() else []),
    hiddenimports=collect_submodules("app")
    + [
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "PyQt6.QtSvg",
        "PyQt6.QtPrintSupport",
        "PyQt6.QtNetwork",
        "PyQt6.sip",
        # fontTools loads OpenType table readers by name.
        "fontTools.ttLib.tables.C_F_F_",
        "fontTools.ttLib.tables._c_m_a_p",
        "fontTools.ttLib.tables._m_a_x_p",
        "ctypes.wintypes",
        "win32com.client",
        "win32com.server.factory",
        "win32com.server.policy",
        "win32com.server.util",
        "win32com.shell.shell",
        "pythoncom",
        "pywintypes",
        "win32api",
        "win32con",
        "win32job",
        "win32process",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "tkinter",
        "unittest",
        "numpy",
        "rapidocr",
        "onnxruntime",
        "cv2",
        "pandas",
        # Excel embeds the locally rendered PNG regions through Pillow.
        "PIL.AvifImagePlugin",
        "PIL._avif",
        "PIL._imagingft",
        "PIL._imagingcms",
        "PIL._webp",
        "lxml",
        "IPython",
        # 更新检查使用 Qt Network + Windows Schannel；不携带 Python TLS / OpenSSL。
        # hashlib 可使用 CPython 自带的算法实现，MuPDF 的 PDF 加密不受影响。
        "ssl",
        "_ssl",
        "_hashlib",
        "PyQt6.QtWebEngineCore",
        "PyQt6.QtWebEngineWidgets",
        "PyQt6.QtQuick",
        "PyQt6.QtQml",
        "PyQt6.QtMultimedia",
        "PyQt6.QtBluetooth",
        "PyQt6.QtSql",
        "PyQt6.QtTest",
        "PyQt6.QtDesigner",
        "PyQt6.QtPdf",
        "PyQt6.QtPdfWidgets",
        "PyQt6.QtNfc",
        "PyQt6.QtSensors",
        "PyQt6.QtPositioning",
        "PyQt6.QtTextToSpeech",
        "PyQt6.QtWebSockets",
        "PyQt6.QtOpenGL",
        "PyQt6.QtOpenGLWidgets",
        "PyQt6.Qt3DCore",
        "PyQt6.QtCharts",
        "PyQt6.QtDBus",
        "PyQt6.QtXml",
    ],
    noarchive=False,
    # 保留依赖库的断言校验；体积主要来自 DLL，不以删除校验换体积。
    optimize=0,
)
a.binaries = [b for b in a.binaries if keep_binary(b)]
a.datas = [d for d in a.datas if keep_data(d)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_SLUG,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # EXE 内的第 0 组是应用图标，第 1 组是 PDF 文档图标。
    # 文件关联只引用持久的 EXE，不引用单文件模式的临时解包目录。
    icon=[str(ROOT / "packaging" / "app.ico"), str(ROOT / "packaging" / "pdf.ico")],
    version=PE_VERSION,
)

# Keep dependencies installed beside the EXE: no per-launch extraction.
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, name=APP_SLUG,
)
