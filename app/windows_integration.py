"""按用户请求登记 PDF 打开方式，不写入 Windows 的 UserChoice 默认选择。"""
from __future__ import annotations

from pathlib import Path

from app import APP_NAME

APP_REG_NAME = "PDFManage"
# 保留既有注册标识，以便品牌更新后仍沿用已有的 PDF 打开方式。
PROG_ID = "PDFManage.Document"
DEFAULT_APPS_URL = "ms-settings:defaultapps?registeredAppUser=PDFManage"


def pdf_icon_path(executable: str) -> Path:
    exe = Path(executable).resolve()
    folder = exe.parent
    bundled = folder / "_internal" / "pdf.ico"
    if bundled.is_file():
        return bundled
    external = folder / "pdf.ico"
    return external if external.is_file() else exe


def association_entries(executable: str) -> list[tuple[str, str, str]]:
    exe = Path(executable).resolve()
    icon = pdf_icon_path(executable)
    document_icon = f'"{icon}",{1 if icon == exe else 0}'
    command = f'"{exe}" "%1"'
    classes = "Software\\Classes"
    application = f"{classes}\\Applications\\{exe.name}"
    capabilities = f"Software\\{APP_REG_NAME}\\Capabilities"
    return [
        (f"{classes}\\{PROG_ID}", "", "PDF 文档"),
        (f"{classes}\\{PROG_ID}\\DefaultIcon", "", document_icon),
        (f"{classes}\\{PROG_ID}\\shell\\open\\command", "", command),
        (f"{classes}\\.pdf\\OpenWithProgids", PROG_ID, ""),
        (application, "FriendlyAppName", APP_NAME),
        (f"{application}\\DefaultIcon", "", document_icon),
        (f"{application}\\SupportedTypes", ".pdf", ""),
        (f"{application}\\shell\\open\\command", "", command),
        (capabilities, "ApplicationName", APP_NAME),
        (capabilities, "ApplicationIcon", f'"{exe}",0'),
        (capabilities, "ApplicationDescription", "在本机阅读、整理和打印 PDF"),
        (f"{capabilities}\\FileAssociations", ".pdf", PROG_ID),
        ("Software\\RegisteredApplications", APP_REG_NAME, capabilities),
    ]


def register_pdf_application(executable: str) -> None:
    import winreg

    if not Path(executable).is_file():
        raise OSError("程序位置已改变，请从当前安装位置重新打开软件。")
    for path, name, value in association_entries(executable):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    # 通知资源管理器刷新“打开方式”，不会改变现有默认 PDF 应用。
    import ctypes
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
