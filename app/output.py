"""File commit helpers shared by PDF, image and Word exporters."""
from __future__ import annotations

import os
import time
from pathlib import Path
from contextlib import contextmanager


class OperationCancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel and cancel():
        raise OperationCancelled("任务已取消，未完成的临时文件已清理。")


def available_path(source, suffix, *, tag='', folder=None, cancel=None):
    source = Path(source)
    parent = Path(folder) if folder else source.parent
    stem = source.stem + tag
    target = parent / (stem + suffix)
    number = 2
    while os.path.lexists(target):
        check_cancel(cancel)
        target = parent / f'{stem} ({number}){suffix}'
        number += 1
    return target


@contextmanager
def cancellable_lock(lock, cancel=None):
    while not lock.acquire(timeout=.05):
        check_cancel(cancel)
    try:
        check_cancel(cancel)
        yield
    finally:
        lock.release()


def stamp(path):
    try:
        stat = os.stat(path)
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
    except FileNotFoundError:
        return None


def commit_file(temp, target, expected, *, cancel=None):
    # 新文件采用排他创建，避免检查之后出现的同名文件被替换。
    # Windows readers briefly hold newly closed files; retry handles transient locks.
    for attempt in range(20):
        check_cancel(cancel)
        if stamp(target) != expected:
            error = FileExistsError if expected is None else OSError
            raise error("目标文件在处理期间发生变化，未覆盖，请重新选择输出文件。")
        try:
            if expected is None:
                if os.name == "nt":
                    os.rename(temp, target)  # Windows rename refuses an existing target.
                else:
                    os.link(temp, target)
                    Path(temp).unlink()
            else:
                os.replace(temp, target)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(.05)
