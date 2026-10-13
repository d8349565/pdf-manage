import os
import tempfile
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def generated_files_stay_in_test_directory(tmp_path, monkeypatch):
    """Keep exporter scratch files out of the host's personal TEMP directory."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    from PyQt6.QtCore import QSettings
    from app import storage
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setattr(storage, '_settings', lambda: QSettings(
        str(tmp_path / 'storage-settings.ini'), QSettings.Format.IniFormat))
