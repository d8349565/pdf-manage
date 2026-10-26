"""检查 PE 与目录式依赖；仅显式允许时复制完整目录进行运行验证。"""
from __future__ import annotations

import hashlib
import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pefile
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import APP_NAME, APP_EXE_NAME, APP_SLUG, __version__


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", nargs="?", type=Path, default=ROOT / "dist" / APP_SLUG / APP_EXE_NAME)
    parser.add_argument("--skip-runtime", action="store_true", help="Only inspect PE resources and packaged files")
    parser.add_argument("--run-runtime", action="store_true", help="Explicitly opt in to functional self-tests")
    parser.add_argument("--ocr-runtime", type=Path, help="Explicitly verify the optional local OCR runtime")
    parser.add_argument("--installer", type=Path, help="Verify installer version against the program")
    args = parser.parse_args()
    exe = args.executable.resolve()
    if args.installer:
        with pefile.PE(str(args.installer.resolve())) as setup:
            fixed = setup.VS_FIXEDFILEINFO[0]
            version = (fixed.FileVersionMS >> 16, fixed.FileVersionMS & 65535,
                       fixed.FileVersionLS >> 16, fixed.FileVersionLS & 65535)
            assert version == tuple(map(int, __version__.split('.'))) + (0,), "Installer file version mismatch"
            strings = next(info.StringTable[0].entries for block in setup.FileInfo for info in block
                           if info.Key == b"StringFileInfo")
            assert strings[b"ProductName"].decode('utf-8').strip() == APP_NAME
            assert strings[b"ProductVersion"].decode('utf-8').strip() == __version__
    with pefile.PE(str(exe), fast_load=False) as pe:
        assert pe.FILE_HEADER.Machine == 0x8664, "EXE must be x64"
        assert pe.OPTIONAL_HEADER.Subsystem == 2, "EXE must not open a console"
        strings = next(info.StringTable[0].entries for block in pe.FileInfo for info in block
                       if info.Key == b"StringFileInfo")
        assert strings[b"ProductName"].decode("utf-8") == APP_NAME
        assert strings[b"OriginalFilename"].decode("utf-8") == APP_EXE_NAME
        assert strings[b"ProductVersion"].decode("utf-8") == __version__
        assert strings[b"FileVersion"].decode("utf-8") == __version__
        parts = tuple(map(int, __version__.split("."))) + (0,)
        fixed = pe.VS_FIXEDFILEINFO[0]
        assert (fixed.FileVersionMS >> 16, fixed.FileVersionMS & 65535,
                fixed.FileVersionLS >> 16, fixed.FileVersionLS & 65535) == parts
        groups = next(e for e in pe.DIRECTORY_ENTRY_RESOURCE.entries if e.id == 14).directory.entries
        assert [g.id for g in groups] == [1, 2], "Application and PDF icons must be embedded"
        for group in groups:
            entry = group.directory.entries[0].data.struct
            data = pe.get_data(entry.OffsetToData, entry.Size)
            assert struct.unpack_from("<HHH", data) == (0, 1, 7)
        icon_images = next(e for e in pe.DIRECTORY_ENTRY_RESOURCE.entries if e.id == 3).directory.entries
        for index, name in enumerate(("app", "pdf")):
            source = (ROOT / "packaging" / f"{name}.ico").read_bytes()
            for frame in range(7):
                size, offset = struct.unpack_from("<II", source, 6 + frame * 16 + 8)
                entry = icon_images[index * 7 + frame].directory.entries[0].data.struct
                assert pe.get_data(entry.OffsetToData, entry.Size) == source[offset:offset + size]

    archive = CArchiveReader(str(exe))
    internal = exe.parent / "_internal"
    assert internal.is_dir(), "Missing _internal dependency directory"
    names = set(archive.toc) | {p.relative_to(internal).as_posix()
                                for p in internal.rglob("*") if p.is_file()}
    assert any("qwindows.dll" in n for n in names)
    assert any("Qt6PrintSupport.dll" in n for n in names)
    for required in ("qjpeg.dll", "qtiff.dll", "pythoncom", "pywintypes", "win32job", "win32process",
                     "Qt6Network.dll", "qschannelbackend.dll", "QtNetwork.pyd", "_imagingft"):
        assert any(required in n for n in names), f"Missing conversion dependency: {required}"
    python_modules = archive.open_embedded_archive("PYZ.pyz").toc
    for required in ("app.wordconvert", "app.wordui", "app.imaging", "app.pdfsecurity", "app.pdfshell", "app.pdfworker",
                     "win32com.client", "win32com.server.factory", "win32com.server.policy", "win32com.shell.shellcon",
                     "app.documentlayout", "app.documentimages", "app.ocr.export_input", "app.ocr.word_export", "app.ocr.scan_layout",
                     "app.pdf2excel.raster_grid", "app.pdf2excel.alignment_grid", "app.textselection",
                     "app.pdf2excel.page_builder", "PIL.Image", "PIL.ImageFont", "PIL.PngImagePlugin",
                     "app.rasterpdf", "app.rasterui", "app.storage", "app.storageui", "app.updates", "markdown"):
        assert required in python_modules, f"Missing module: {required}"
    assert any(n.startswith("win32com/shell/shell.") and n.endswith(".pyd") for n in names), "Missing native Shell extension"
    for unwanted in ("Qt6WebEngine", "Qt6Qml", "Qt6Quick", "numpy", "rapidocr", "onnxruntime", "cv2", "pandas", "lxml", "libssl", "libcrypto"):
        assert not any(unwanted.lower() in n.lower() for n in names), unwanted

    (ROOT / "build").mkdir(exist_ok=True)
    report = {"ok": True, "checks": ["x64 PE resources, version, icons and packaged dependencies"],
              "runtime_tests": "not_run", "functional_tests": "not_run"}
    if args.skip_runtime or not args.run_runtime:
        write_report(exe, archive, report)
        print(f"PASS: static package inspection only; runtime and functional tests NOT RUN ({report['size_mib']} MiB)")
        return
    with tempfile.TemporaryDirectory(prefix="exe-only-", dir=ROOT / "build") as temp:
        folder = Path(temp) / "中文 空目录"
        folder.mkdir()
        copy = folder / exe.name
        shutil.copytree(exe.parent, folder, dirs_exist_ok=True)
        environment = os.environ.copy()
        for key in ("PYTHONPATH", "PYTHONHOME", "QT_QPA_PLATFORM", "QT_PLUGIN_PATH",
                    "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH"):
            environment.pop(key, None)
        environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
        environment["TEMP"] = environment["TMP"] = str(folder)
        result_path = folder / "self-test.json"
        started = time.perf_counter()
        command = [str(copy), "--self-test", str(result_path)]
        if args.ocr_runtime:
            command.extend(["--ocr-runtime", str(args.ocr_runtime.resolve())])
        result = subprocess.run(command,
                                cwd=folder, env=environment, timeout=120, check=False)
        assert result_path.is_file(), f"EXE did not produce a report, exit={result.returncode}"
        report = json.loads(result_path.read_text(encoding="utf-8"))
        report["verification_seconds"] = round(time.perf_counter() - started, 2)
        (ROOT / "build" / "build-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        assert result.returncode == 0 and report["ok"] and report["frozen"], report

    report["runtime_tests"] = "passed"
    report["functional_tests"] = "passed"
    write_report(exe, archive, report)
    print(f"PASS: {exe} ({report['size_mib']} MiB), {len(report['checks'])} runtime checks")


def write_report(exe, archive, report):
    package_bytes = sum(p.stat().st_size for p in exe.parent.rglob("*") if p.is_file())
    report.update(package_bytes=package_bytes, package_size_mib=round(package_bytes / 1024**2, 2),
                  package_mode="onedir", product_name=APP_NAME, version=__version__,
                  executable=str(exe), bytes=exe.stat().st_size,
                  size_mib=round(exe.stat().st_size / 1024**2, 2),
                  sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),
                  components={"PyMuPDF": "1.28.2", "Qt": "6.11.2", "pywin32": "312",
                              "Word": "external - uses installed Microsoft Word"},
                  embedded_icon_groups=2, icon_sizes=[16, 24, 32, 48, 64, 128, 256],
                  embedded_uncompressed_bytes=sum(entry[2] for entry in archive.toc.values()))
    (ROOT / "build" / "build-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
