"""Verify a portable ZIP; native integration tests are explicit and temporary."""
from pathlib import Path, PurePosixPath
import argparse
import gc
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import APP_EXE_NAME, __version__


def inspect_archive(archive_path, executable):
    prefix = 'ZhiXuPDF-Portable/'
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert len({name.casefold() for name in names}) == len(names)
        for item in archive.infolist():
            path = PurePosixPath(item.filename)
            assert item.filename.startswith(prefix) and not path.is_absolute() and '..' not in path.parts
            assert '\\' not in item.filename and ':' not in item.filename
            assert not stat.S_ISLNK(item.external_attr >> 16)
            assert not item.filename.startswith(prefix + 'Data/')
            relative = PurePosixPath(*path.parts[1:])
            if relative.parts[0] == '_internal':
                original = Path(executable).parent.joinpath(*relative.parts)
                assert original.is_file()
                assert hashlib.sha256(archive.read(item.filename)).digest() == hashlib.sha256(original.read_bytes()).digest()
            else:
                assert str(relative) in (APP_EXE_NAME, 'portable.ini', '绿色版使用说明.txt')
        assert prefix + APP_EXE_NAME in names and prefix + 'portable.ini' in names
        assert any(name.startswith(prefix + '_internal/') for name in names)
        assert archive.testzip() is None
        expected = hashlib.sha256(Path(executable).read_bytes()).hexdigest()
        assert hashlib.sha256(archive.read(prefix + APP_EXE_NAME)).hexdigest() == expected
    return expected


def native_integration(installed, portable, environment):
    """No Explorer verbs are created; two temporary COM identities only."""
    import pythoncom
    from PyQt6.QtCore import QCoreApplication, QProcess
    from app.contextmenus import WindowsRegistry, registration_plan

    application = QCoreApplication.instance() or QCoreApplication([])
    registry = WindowsRegistry()
    test_key = r'Software\ZhiXuPDF-Verification-' + uuid.uuid4().hex
    processes, interfaces, roots = [], [], []
    pythoncom.CoInitialize()
    try:
        registry.set(test_key, 'Unicode', '生成的隔离验证')
        registry.set(test_key + r'\child', '', 'before')
        snapshot = registry.snapshot(test_key)
        registry.set(test_key + r'\child', '', 'changed')
        registry.restore(test_key, snapshot)
        assert registry.get(test_key + r'\child') == 'before'
        for exe, portable_mode in ((installed, False), (portable, True)):
            plan = registration_plan(exe, portable=portable_mode)
            root = 'Software\\Classes\\CLSID\\' + plan.clsid
            assert registry.snapshot(root) is None, 'Temporary COM identity already exists; refusing to replace it'
            roots.append(root)
            # Omit LocalServer32 so COM cannot spawn an untracked process. The
            # known child below registers its real class factory across processes.
            registry.set(root + r'\PythonCOM', '', 'app.pdfshell.MergeDropTarget')
            process = QProcess()
            processes.append(process)
            process.setProgram(str(exe))
            process.setArguments(['--pdf-shell-server', '--context-id', plan.clsid])
            from PyQt6.QtCore import QProcessEnvironment
            env = QProcessEnvironment()
            for key, value in environment.items():
                env.insert(key, value)
            process.setProcessEnvironment(env)
            process.start()
            assert process.waitForStarted(5000), process.errorString()
            deadline = time.monotonic() + 10
            error = None
            while time.monotonic() < deadline:
                application.processEvents()
                pythoncom.PumpWaitingMessages()
                try:
                    interface = pythoncom.CoCreateInstance(plan.clsid, None,
                        pythoncom.CLSCTX_LOCAL_SERVER, pythoncom.IID_IDropTarget)
                    interfaces.append(interface)
                    break
                except pythoncom.com_error as exc:
                    error = exc
                    time.sleep(.05)
            else:
                raise AssertionError(f'COM factory did not become ready: {plan.clsid}: {error}')
        assert len(interfaces) == 2
        for interface in interfaces:
            interface.DragLeave()
        return ['Windows registry snapshot/restore in a unique temporary test key',
                'two isolated executable copies serve independent COM class factories simultaneously']
    finally:
        interface = None
        interfaces.clear()
        gc.collect()
        for process in processes:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()  # Only this verifier's idle, document-free children.
                process.waitForFinished(5000)
        for root in roots:
            registry.delete(root)
        assert test_key.startswith('Software\\ZhiXuPDF-Verification-')
        registry.delete(test_key)
        pythoncom.CoUninitialize()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--zip', type=Path, default=ROOT / f'dist-portable/ZhiXuPDF-Portable-v{__version__}.zip')
    parser.add_argument('--exe', type=Path, default=ROOT / 'dist/ZhiXuPDF' / APP_EXE_NAME)
    parser.add_argument('--run-runtime', action='store_true')
    parser.add_argument('--native-integration', action='store_true', help='Explicit temporary registry/COM checks; no context menus are enabled')
    parser.add_argument('--report', type=Path, help='Optional verification report path')
    args = parser.parse_args()
    report = {'ok': False, 'version': __version__, 'checks': [], 'runtime': 'not_run', 'native_integration': 'not_run'}
    report_path = args.report or ROOT / ('build/portable-native-verification.json'
        if args.native_integration and not args.run_runtime else 'build/portable-verification.json')
    try:
        report['executable_sha256'] = inspect_archive(args.zip, args.exe)
        report['checks'].append('ZIP paths, dependencies, portable marker, no Data profile, and identical executable bytes')
        if args.run_runtime or args.native_integration:
            build = ROOT / 'build'
            build.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='portable-verify-', dir=build) as temp:
                folder = Path(temp).resolve()
                assert folder.is_relative_to(build.resolve())
                with zipfile.ZipFile(args.zip) as archive:
                    archive.extractall(folder)
                portable = folder / 'ZhiXuPDF-Portable' / APP_EXE_NAME
                environment = os.environ.copy()
                for key in ('PYTHONPATH', 'PYTHONHOME', 'QT_QPA_PLATFORM', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH'):
                    environment.pop(key, None)
                environment.update(TEMP=str(folder), TMP=str(folder), LOCALAPPDATA=str(folder / 'local'),
                                   APPDATA=str(folder / 'roaming'), PATH=str(Path(os.environ['SystemRoot']) / 'System32'))
                if args.run_runtime:
                    result_path = folder / 'self-test.json'
                    result = subprocess.run([str(portable), '--self-test', str(result_path)],
                                            cwd=folder, env=environment, timeout=120)
                    native = json.loads(result_path.read_text(encoding='utf-8'))
                    assert result.returncode == 0 and native['ok'] and native['frozen'], native
                    assert native['edition'] == 'portable' and Path(native['portable_data']) == portable.parent / 'Data'
                    report['portable_runtime'] = native
                    report['runtime'] = 'passed'
                if args.native_integration:
                    installed = folder / 'installed' / APP_EXE_NAME
                    shutil.copytree(args.exe.parent, installed.parent,
                        ignore=lambda path, names: [name for name in names if Path(path) == args.exe.parent and name in ('Data', 'portable.ini')])
                    report['checks'].extend(native_integration(installed, portable, environment))
                    report['native_integration'] = 'passed'
        report['ok'] = True
        print(f"PASS: portable ZIP; runtime={report['runtime']}; native integration={report['native_integration']}")
    except Exception:
        import traceback
        report['error'] = traceback.format_exc()
        raise
    finally:
        report_path.parent.mkdir(exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
