"""License collection uses actual build dependencies; missing legal files block distribution."""
import importlib.util
from importlib.metadata import version
from pathlib import Path
import shutil
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("license_bundle", ROOT / "packaging/license_bundle.py")
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


@pytest.fixture
def legal_program(tmp_path):
    program = tmp_path / "program"
    internal = program / "_internal"
    for source, destination in bundle.collect_license_data(ROOT) + [(str(ROOT / "packaging/licenses"), "licenses")]:
        source = Path(source)
        target = internal / destination
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target / source.name)
    (program / "ZhiXuPDF.exe").write_bytes(b"generated archive entry, never executed")
    return program


def test_collect_real_notices_and_reject_an_incomplete_bundle(legal_program):
    internal = legal_program / "_internal"
    paths = bundle.verify_license_bundle(internal)
    assert "licenses/AGPL-3.0.txt" in paths
    sources = (internal / "licenses/SOURCES.txt").read_text(encoding="utf-8")
    from pymupdf import mupdf_version
    assert f"PyMuPDF {version('PyMuPDF')}" in sources and f"MuPDF {mupdf_version}" in sources
    (internal / "licenses/AGPL-3.0.txt").unlink()
    with pytest.raises(ValueError, match="AGPL-3.0"):
        bundle.verify_license_bundle(internal)


def test_portable_verifier_rejects_an_archive_missing_a_license(legal_program, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "packaging"))
    spec = importlib.util.spec_from_file_location("verify_portable", ROOT / "packaging/verify_portable.py")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    archive_path = tmp_path / "portable.zip"
    for omit_agpl in (False, True):
        with zipfile.ZipFile(archive_path, "w") as archive:
            for path in legal_program.rglob("*"):
                if path.is_file() and not (omit_agpl and path.name == "AGPL-3.0.txt"):
                    archive.write(path, "ZhiXuPDF-Portable/" + path.relative_to(legal_program).as_posix())
            archive.writestr("ZhiXuPDF-Portable/portable.ini", "[Portable]\nEnabled=true\n")
        if omit_agpl:
            with pytest.raises(AssertionError, match="Missing license"):
                verifier.inspect_archive(archive_path, legal_program / "ZhiXuPDF.exe")
        else:
            verifier.inspect_archive(archive_path, legal_program / "ZhiXuPDF.exe")
