"""Portable profiles are explicitly marked and never read the installed profile."""
from pathlib import Path
import sys

from PyQt6.QtCore import QSettings

from app import APP_EXE_NAME, APP_IS_PRERELEASE, APP_NAME, APP_ORGANIZATION, APP_SLUG

PORTABLE_MARKER = 'portable.ini'


def application_directory():
    return (Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False)
            else Path(__file__).resolve().parent.parent)


def portable_root(executable=None):
    folder = Path(executable).resolve().parent if executable else application_directory()
    return folder if (folder / PORTABLE_MARKER).is_file() else None


def portable_data():
    root = portable_root()
    return root / 'Data' if root else None


def installed_directory():
    """Only the frozen executable at this product's registered Inno Setup location."""
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False) or portable_root() is not None:
        return None
    exe = Path(sys.executable).resolve()
    if exe.name.casefold() != APP_EXE_NAME.casefold():
        return None
    import winreg
    key = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{0E4A38D0-8475-4A57-98C2-1AD3F468A0E8}_is1'
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as entry:
                folder, _ = winreg.QueryValueEx(entry, 'InstallLocation')
            if folder and Path(folder).resolve() == exe.parent:
                return exe.parent
        except (OSError, ValueError):
            continue
    return None


def display_name():
    from app.i18n import tr
    return tr(APP_NAME) + (tr('（绿色版）') if portable_root() else '') + (tr('（预览版）') if APP_IS_PRERELEASE else '')


def prepare_profile():
    folder = portable_data()
    if folder is not None:
        import tempfile
        try:
            folder.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=folder):
                pass
        except OSError as exc:
            raise OSError('绿色版的数据目录无法写入，请把整个程序文件夹移到允许写入的位置。') from exc


def preferences(factory=QSettings):
    folder = portable_data()
    if folder is None:
        return factory(QSettings.defaultFormat(), QSettings.Scope.UserScope, APP_ORGANIZATION, APP_SLUG)
    settings = factory(str(folder / 'preferences.ini'), QSettings.Format.IniFormat)
    settings.setFallbacksEnabled(False)
    return settings
