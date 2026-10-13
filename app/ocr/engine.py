"""Cancellable local IPC. The main application never imports inference packages."""
import json
import os
from queue import Empty, Queue
import subprocess
from threading import Thread
from time import monotonic

from app.ocr.contracts import OCRError, OCRLine
from app.output import check_cancel


class OCREngine:
    def __init__(self, root, cancel=None):
        self.cancel = cancel
        self.messages = Queue()
        self.process = None
        try:
            self.process = subprocess.Popen(
                [str(root / 'runtime' / 'ZhiXuOCR.exe'), '--models', str(root / 'models')],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                cwd=str(root), env={k: v for k, v in os.environ.items()
                                   if k not in ('PYTHONPATH', 'PYTHONHOME')})
            self.reader = Thread(target=self._read, daemon=True)
            self.reader.start()
            if self._receive().get('ready') is not True:
                raise OCRError('OCR 运行组件未就绪，请重新安装增强包。')
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            while line := self.process.stdout.readline(8 * 1024 * 1024):
                self.messages.put(line)
        finally:
            self.messages.put(None)

    def _receive(self):
        deadline = monotonic() + 300
        while monotonic() < deadline:
            check_cancel(self.cancel)
            try:
                line = self.messages.get(timeout=.1)
            except Empty:
                continue
            if not line:
                raise OCRError('OCR 推理进程意外退出，请检查增强包。')
            try:
                data = json.loads(line)
            except (ValueError, TypeError):
                raise OCRError('OCR 推理结果无效，请重新安装增强包。') from None
            if not isinstance(data, dict):
                raise OCRError('OCR 推理结果格式不兼容。')
            if data.get('error'):
                raise OCRError(data['error'])
            return data
        raise OCRError('OCR 单页处理超过 5 分钟，已停止任务。')

    def recognize(self, image, page_index):
        check_cancel(self.cancel)
        try:
            self.process.stdin.write(json.dumps({'image': str(image), 'page_index': page_index}) + '\n')
            self.process.stdin.flush()
            result = self._receive()
            return [OCRLine(item['text'], tuple(item['bbox']), tuple(item['polygon']),
                            item['confidence'], item['page_index']) for item in result['lines']]
        except (OSError, KeyError, TypeError):
            raise OCRError('OCR 组件通信失败，请重新安装增强包。') from None

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            for stream in (self.process.stdin, self.process.stdout):
                if stream:
                    stream.close()
            reader = getattr(self, 'reader', None)
            if reader:
                reader.join(timeout=1)
