import os
import tempfile
import sys
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def generated_files_stay_in_test_directory(tmp_path, monkeypatch):
    """Keep exporter scratch files out of the host's personal TEMP directory."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    from PyQt6.QtCore import QSettings
    from app import storage
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    preferences = lambda *args: QSettings(str(tmp_path / 'storage-settings.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr(storage, '_settings', preferences)
    # UI persistence tests must never update the user's real window/sidebar choices.
    for name in ('app.ui', 'app.exportdialogs'):
        module = sys.modules.get(name)
        if module is not None:
            monkeypatch.setattr(module, 'QSettings', preferences)
