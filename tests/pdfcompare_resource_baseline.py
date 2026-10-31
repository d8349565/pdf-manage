"""Measure the existing visual core only, using explicitly selected generated fixtures.

Not a UI/installer measurement or a forecast for the future content comparison engine.
Run with: python tests/pdfcompare_resource_baseline.py --samples build/pdfcompare-acceptance-v1-ready --output build/compare-core-baseline.json
"""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time


def child(folder):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.pdfcompare import compare_files
    started = time.perf_counter()
    result = compare_files(folder / 'old.pdf', folder / 'new.pdf')
    elapsed = time.perf_counter() - started
    memory = None
    if os.name == 'nt':
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                        *[(name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                            'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                            'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage')]]
        counters = Counters(); counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        memory = dict(peak_working_set_mib=round(counters.PeakWorkingSetSize / 2**20, 2),
                      peak_commit_mib=round(counters.PeakPagefileUsage / 2**20, 2))
    print(json.dumps(dict(seconds=round(elapsed, 3), memory=memory, counts=result['counts'])))


def main(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    samples = args.samples.resolve(strict=True)
    manifest = json.loads((samples / 'manifest.json').read_text(encoding='utf-8'))
    records = []
    # Alternate scales between repetitions, keeping each run in a fresh process.
    for repeat in range(args.repeat):
        for code in ('P020', 'P100', 'P200'):
            started = time.perf_counter()
            run = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--child', str(samples / code)],
                                 capture_output=True, text=True, timeout=180)
            if run.returncode:
                raise RuntimeError(run.stderr)
            result = json.loads(run.stdout)
            result.update(case=code, repeat=repeat + 1, process_wall_seconds=round(time.perf_counter() - started, 3))
            records.append(result)
            print(json.dumps(result), flush=True)
    report = dict(scope='existing visual core, fresh process; no UI, OCR, installer or future content matcher',
                  python=platform.python_version(), platform=platform.platform(), logical_cpus=os.cpu_count(),
                  commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                  samples=[dict(id=c['id'], old_sha256=c['old']['sha256'], new_sha256=c['new']['sha256'])
                           for c in manifest['cases'] if c['id'].startswith('P')], runs=records, medians={})
    for code in ('P020', 'P100', 'P200'):
        runs = [r for r in records if r['case'] == code]
        report['medians'][code] = dict(seconds=statistics.median(r['seconds'] for r in runs),
            process_wall_seconds=statistics.median(r['process_wall_seconds'] for r in runs),
            peak_working_set_mib=max(r['memory']['peak_working_set_mib'] for r in runs) if runs[0]['memory'] else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--repeat', type=int, default=3, choices=range(1, 6))
    parser.add_argument('--child', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        child(args.child)
    elif args.samples is None or args.output is None:
        parser.error('--samples and --output are required')
    else:
        main(args)
