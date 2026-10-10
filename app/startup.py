"""可选启动诊断：仅记录阶段耗时，不记录文件路径或文档内容。"""
import json
import os
import time
from threading import Lock

_started = time.perf_counter()
_previous = _started
_path = os.environ.get("PAGEARK_STARTUP_LOG")
_seen = set()
_lock = Lock()


def mark(stage: str) -> None:
    """每个阶段每进程记录一次；日志不可写时不影响应用启动。"""
    global _previous
    if not _path:
        return
    with _lock:
        if stage in _seen:
            return
        _seen.add(stage)
        now = time.perf_counter()
        record = dict(pid=os.getpid(), stage=stage,
                      elapsed_ms=round((now - _started) * 1000, 3),
                      delta_ms=round((now - _previous) * 1000, 3))
        _previous = now
        try:
            with open(_path, "a", encoding="utf-8") as stream:
                stream.write(json.dumps(record) + "\n")
        except OSError:
            pass
