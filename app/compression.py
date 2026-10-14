"""Compression preview prepared in temporary files, exported only on request."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from app.exportdialogs import hint, new_pdf_path
from app.output import OperationCancelled
from app.pdfio import PdfError, WriteOptions
from app.progress import run_operation
from app.pdfworker import render_preview
from app.storage import TemporaryWorkspace


def size_text(size):
    return f"{size / 1024 / 1024:.2f} MB" if size >= 1024 * 1024 else f"{size / 1024:.1f} KB"


class CompressionDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.folder = TemporaryWorkspace('compress')
        self.baseline = None
        self.result = None
        self._preview_sources = set()
        self._busy = False
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(180)
        self._preview_timer.timeout.connect(self.preview)
        self.setWindowTitle("压缩 PDF")
        self.resize(980, 760)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("压缩档位"))
        self.profile = QComboBox()
        for name, key in (("无损优化", "quality"), ("高质量 · 300 DPI", "high"),
                          ("均衡（推荐）· 200 DPI", "balanced"), ("适合外发 · 150 DPI", "send"),
                          ("小体积 · 120 DPI", "small"), ("自定义…", "custom")):
            self.profile.addItem(name, key)
        self.profile.setCurrentIndex(2)
        row.addWidget(self.profile, 1)
        self.calculate = QPushButton("计算压缩并预览")
        self.calculate.clicked.connect(self.prepare)
        row.addWidget(self.calculate)
        layout.addLayout(row)
        self.advanced = QWidget()
        advanced_layout = QGridLayout(self.advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        self.dpi = QSpinBox()
        self.dpi.setRange(72, 600)
        self.dpi.setValue(300)
        self.dpi.setSuffix(" DPI")
        self.dpi.setAccessibleName("彩色和灰度图片的目标分辨率")
        self.quality = QSpinBox()
        self.quality.setRange(40, 100)
        self.quality.setValue(92)
        self.quality.setAccessibleName("图片编码质量")
        self.grayscale = QCheckBox("转为灰度（会移除颜色）")
        advanced_layout.addWidget(QLabel("图片分辨率"), 0, 0)
        advanced_layout.addWidget(self.dpi, 0, 1)
        advanced_layout.addWidget(QLabel("图片质量"), 0, 2)
        advanced_layout.addWidget(self.quality, 0, 3)
        advanced_layout.addWidget(self.grayscale, 0, 4)
        layout.addWidget(self.advanced)
        self.advanced.hide()
        self.note = hint(layout, "均衡：图片按需优化至 200 DPI / 质量 85，兼顾清晰度与体积。文字和矢量内容保留；黑白图片不降采样。")
        self.summary = hint(layout, "点击计算后，查看压缩前后大小和页面效果。")
        previews = QHBoxLayout()
        self.images = []
        for title in ("压缩前", "压缩后"):
            column = QVBoxLayout()
            column.addWidget(QLabel(title))
            area = QScrollArea()
            area.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label = QLabel("等待计算")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumSize(300, 380)
            area.setWidget(label)
            column.addWidget(area, 1)
            previews.addLayout(column)
            self.images.append(label)
        layout.addLayout(previews, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("预览页"))
        self.page = QSpinBox()
        self.page.setRange(1, len(owner.ws.pages))
        self.page.setEnabled(False)
        self.page.valueChanged.connect(lambda: self._preview_timer.start())
        row.addWidget(self.page)
        hint(row, "预览清晰度相同，可滚动核对小字与图片；高质量及以下档位可能改变图片细节。")
        layout.addLayout(row)
        footer = QHBoxLayout()
        footer.addStretch()
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        self.export = QPushButton("另存压缩结果…")
        self.export.setObjectName("primary")
        self.export.setEnabled(False)
        self.export.clicked.connect(self.save_result)
        footer.addWidget(self.export)
        layout.addLayout(footer)
        self.profile.currentIndexChanged.connect(self.invalidate)
        self.dpi.valueChanged.connect(self.invalidate)
        self.quality.valueChanged.connect(self.invalidate)
        self.grayscale.toggled.connect(self.invalidate)

    def invalidate(self):
        self._preview_timer.stop()
        self.result = None
        self.export.setEnabled(False)
        self.page.setEnabled(False)
        for image in self.images:
            image.clear()
            image.setText("档位已更改，请重新计算")
        key = self.profile.currentData()
        self.advanced.setVisible(key == "custom")
        notes = {
            "quality": "无损优化：合并重复数据、压缩对象和字体流，不降低图片分辨率或质量。",
            "high": "高质量：高分辨率图片按需降至 300 DPI，JPEG 质量 92；无损线稿继续使用无损编码。",
            "balanced": "均衡：高分辨率图片按需降至 200 DPI，JPEG 质量 85，兼顾阅读清晰度与文件大小。",
            "send": "适合外发：高分辨率图片按需降至 150 DPI，JPEG 质量 80，请核对小字与细线。",
            "small": "小体积：高分辨率图片按需降至 120 DPI，JPEG 质量 72，图片细节损失更明显。",
            "custom": f"自定义：图片按需降至 {self.dpi.value()} DPI，JPEG 质量 {self.quality.value()}；质量 100 仍不等于无损。",
        }
        self.note.setText(notes[key] + " 文字和矢量内容保留；黑白图片不降采样。")
        self.summary.setText("请重新计算当前档位。")

    def _set_busy(self, busy):
        self._busy = busy
        self.calculate.setEnabled(not busy)
        self.profile.setEnabled(not busy)
        self.advanced.setEnabled(not busy)
        self.page.setEnabled(not busy and self.result is not None)
        self.export.setEnabled(not busy and self.result is not None)

    def _release_old_results(self):
        keep = {page.source_id for result in (self.baseline, self.result) if result for page in result.pages}
        unused = self._preview_sources - keep
        if unused:
            def release(progress):
                for sid in unused:
                    source = self.owner.store._sources.pop(sid, None)
                    if source is not None:
                        source.close()
            run_operation(self, "释放旧压缩预览", release)
        self._preview_sources.intersection_update(keep)

    def prepare(self):
        if self._busy:
            return
        self._preview_timer.stop()
        self.result = None
        self._set_busy(True)
        self.summary.setText("正在计算压缩效果…")
        for image in self.images:
            image.clear()
            image.setText("正在计算…")
        try:
            if self.baseline is None:
                self.baseline = self.owner._write_pdf(
                    list(self.owner.ws.pages), os.path.join(self.folder.name, "before.pdf"),
                    "准备无损压缩基准", register_result=True, cancellable=True,
                    options=WriteOptions(compression="quality"))
                if self.baseline is None:
                    return
                self._preview_sources.update(page.source_id for page in self.baseline.pages)
            key = self.profile.currentData()
            path = os.path.join(self.folder.name, f"after-{key}.pdf")
            # Use a fresh target on repeated calculation; the old preview may still be loaded.
            if os.path.exists(path):
                fd, path = tempfile.mkstemp(suffix=".pdf", dir=self.folder.name)
                os.close(fd)
                Path(path).unlink()
            self.result = self.baseline if key == "quality" else self.owner._write_pdf(
                list(self.baseline.pages), path, "压缩 PDF", register_result=True,
                options=WriteOptions(compression=key, compression_dpi=self.dpi.value(),
                    compression_quality=self.quality.value(),
                    compression_grayscale=key == "custom" and self.grayscale.isChecked()),
                cancellable=True)
            if self.result is None:
                return
            self._preview_sources.update(page.source_id for page in self.result.pages)
            before = os.path.getsize(self.baseline.path)
            if not self.owner.ws.dirty and self.owner.ws.path:
                sources = {p.source_id for p in self.owner.ws.pages}
                if len(sources) == 1:
                    before = len(self.owner.store._source(next(iter(sources))).data)
            fallback_note = ""
            after = os.path.getsize(self.result.path)
            if self.result is not self.baseline and after >= os.path.getsize(self.baseline.path):
                # Extra image recompression must not replace a smaller, clearer result.
                # Explicit grayscale requests keep the requested appearance.
                if not (key == "custom" and self.grayscale.isChecked()):
                    self.result = self.baseline
                    after = os.path.getsize(self.baseline.path)
                    fallback_note = " · 当前档位未进一步缩小，已保留更小的无损结果"
            self.reduced = after < before
            change = f"缩小 {(1 - after / before) * 100:.1f}%" if self.reduced else "没有缩小，可自行决定是否导出"
            baseline_note = "（工作区基准）" if self.owner.ws.dirty else ""
            self.summary.setText(f"压缩前{baseline_note} {size_text(before)} → 压缩后 {size_text(after)} · {change}{fallback_note}")
        except OperationCancelled:
            self.summary.setText("已取消，完成 0 个导出文件，原件保留。")
        except PdfError as exc:
            QMessageBox.warning(self, "压缩未完成", str(exc))
        finally:
            self._release_old_results()
            self._set_busy(False)
        if self.result is not None:
            self.preview()

    def preview(self):
        if self._busy or not self.result or not self.baseline:
            return
        self._set_busy(True)
        try:
            index = self.page.value() - 1
            store = self.owner.store
            refs = (self.baseline.pages[index], self.result.pages[index])
            passwords = [store._source(ref.source_id).password for ref in refs]
            images = run_operation(self, "更新页面预览", lambda report, cancel:
                                   render_preview(refs, passwords, report, cancel), cancellable=True)
            for label, image in zip(self.images, images):
                label.setPixmap(QPixmap.fromImage(image))
                label.resize(image.size())
        except PdfError as exc:
            QMessageBox.warning(self, "预览提示", str(exc))
        except OperationCancelled:
            for image in self.images:
                image.setText("预览已取消，可切换页码重试")
        finally:
            self._set_busy(False)

    def save_result(self):
        if self._busy or not self.result:
            return
        self._preview_timer.stop()
        if not self.reduced and QMessageBox.question(self, "没有缩小", "压缩结果没有变小，仍要另存吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        stem = Path(self.owner.ws.path or "文档.pdf").stem
        path = new_pdf_path(self, self.owner.store, "另存压缩结果", stem + "_压缩.pdf")
        if not path:
            return
        self._set_busy(True)
        try:
            result = self.owner._write_pdf(list(self.result.pages), path, "导出压缩结果", cancellable=True,
                                          options=WriteOptions(compression="quality"))
            if result is not None:
                QMessageBox.information(self, "已导出", f"已完整导出 1 个文件：\n{result.path}\n大小：{size_text(os.path.getsize(result.path))}")
                self.owner._show_write_notes((self.baseline, self.result, result))
        except OperationCancelled:
            QMessageBox.information(self, "已取消", "已取消，完成 0 个导出文件。")
        except PdfError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
        finally:
            self._set_busy(False)

    def done(self, result):
        if self._busy:
            dialog = getattr(self, "_task_dialog", None) or getattr(self.owner, "_task_dialog", None)
            if dialog:
                dialog.request_cancel()
            return
        self._preview_timer.stop()
        self.owner._release_unused_docs()
        self.folder.cleanup()
        super().done(result)
