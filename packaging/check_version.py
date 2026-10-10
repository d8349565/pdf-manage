"""Check that a Git checkout's version matches a tag on its current commit."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import __version__


def check_version():
    if not (ROOT / ".git").exists():
        return None  # GitHub source ZIPs do not contain Git metadata.
    result = subprocess.run(
        ["git", "tag", "--points-at", "HEAD"], cwd=ROOT,
        capture_output=True, text=True, check=True,
    )
    expected = f"v{__version__}"
    if expected not in result.stdout.splitlines():
        raise ValueError(f"程序版本为 {__version__}，当前提交必须有标签 {expected}。")
    return expected


if __name__ == "__main__":
    try:
        tag = check_version()
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise SystemExit(str(exc))
    print(f"Version: {__version__}; Git tag: {tag or 'source ZIP'}")
