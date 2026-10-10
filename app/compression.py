"""Compression preview prepared in temporary files, exported only on request."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout,
)

from app.exportdialogs import hint, new_pdf_path
from app.output import OperationCancelled
from app.pdfio import PdfError, WriteOptions
from app.progress import run_operation


def size_text(size):
    return f"{size / 1024 / 1024:.2f} MB" if size >= 1024 * 1024 else f"{size / 1024:.1f} KB"


class CompressionDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.folder = tempfile.TemporaryDirectory(prefix="pageark-compress-")
        self.baseline = None
        self.result = None
        self.setWindowTitle("压缩 PDF")
        self.resize(980, 760)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("压缩档位"))
        self.profile = QComboBox()
        self.profile.addItems(["保持画质（默认）", "适合外发 · 150 DPI / JPEG 80"])
        row.addWidget(self.profile, 1)
        self.calculate = QPushButton("计算压缩并预览")
        self.calculate.clicked.connect(self.prepare)
        row.addWidget(self.calculate)
        layout.addLayout(row)
        self.note = hint(layout, "保持画质：整理冗余对象、压缩数据流。默认另存，原件保留。")
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
        self.page.valueChanged.connect(self.preview)
        row.addWidget(self.page)
        hint(row, "可滚动查看细节；外发档画质可能下降。")
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

    def invalidate(self):
        self.result = None
        self.export.setEnabled(False)
        self.page.setEnabled(False)
        for image in self.images:
            image.clear()
            image.setText("档位已更改，请重新计算")
        self.note.setText("适合外发：图片降采样至 150 DPI、JPEG 质量 80，画质可能下降。"
                          if self.profile.currentIndex() else "保持画质：不降采样、不重新编码图片。默认另存，原件保留。")
        self.summary.setText("请重新计算当前档位。")

    def prepare(self):
        self.export.setEnabled(False)
        try:
            if self.baseline is None:
                self.baseline = self.owner._write_pdf(
                    list(self.owner.ws.pages), os.path.join(self.folder.name, "before.pdf"),
                    "准备压缩预览", register_result=True, cancellable=True)
                if self.baseline is None:
                    return
            path = os.path.join(self.folder.name, f"after-{self.profile.currentIndex()}.pdf")
            # Use a fresh target on repeated calculation; the old preview may still be loaded.
            if os.path.exists(path):
                fd, path = tempfile.mkstemp(suffix=".pdf", dir=self.folder.name)
                os.close(fd)
                Path(path).unlink()
            self.result = self.owner._write_pdf(
                list(self.baseline.pages), path, "压缩 PDF", register_result=True,
                options=WriteOptions(compression="send" if self.profile.currentIndex() else "quality"),
                cancellable=True)
            if self.result is None:
                return
            before = os.path.getsize(self.baseline.path)
            if not self.owner.ws.dirty and self.owner.ws.path:
                sources = {p.source_id for p in self.owner.ws.pages}
                if len(sources) == 1:
                    before = len(self.owner.store._source(next(iter(sources))).data)
            after = os.path.getsize(self.result.path)
            self.reduced = after < before
            change = f"缩小 {(1 - after / before) * 100:.1f}%" if self.reduced else "没有缩小，可自行决定是否导出"
            baseline_note = "（工作区基准）" if self.owner.ws.dirty else ""
            self.summary.setText(f"压缩前{baseline_note} {size_text(before)} → 压缩后 {size_text(after)} · {change}")
            self.export.setEnabled(True)
            self.page.setEnabled(True)
            self.preview()
        except OperationCancelled:
            self.summary.setText("已取消，完成 0 个导出文件，原件保留。")
        except PdfError as exc:
            QMessageBox.warning(self, "压缩未完成", str(exc))

    def preview(self):
        if not self.result or not self.baseline:
            return
        try:
            index = self.page.value() - 1
            store = self.owner.store
            refs = (self.owner.ws.pages[index], self.result.pages[index])
            images = run_operation(self, "更新页面预览", lambda report:
                                   [store.render_qimage(ref, 1100) for ref in refs])
            for label, image in zip(self.images, images):
                label.setPixmap(QPixmap.fromImage(image))
                label.resize(image.size())
        except PdfError as exc:
            QMessageBox.warning(self, "预览提示", str(exc))

    def save_result(self):
        if not self.result:
            return
        if not self.reduced and QMessageBox.question(self, "没有缩小", "压缩结果没有变小，仍要另存吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        stem = Path(self.owner.ws.path or "文档.pdf").stem
        path = new_pdf_path(self, self.owner.store, "另存压缩结果", stem + "_压缩.pdf")
        if not path:
            return
        try:
            result = self.owner._write_pdf(list(self.result.pages), path, "导出压缩结果", cancellable=True)
            if result is not None:
                QMessageBox.information(self, "已导出", f"已完整导出 1 个文件：\n{result.path}\n大小：{size_text(os.path.getsize(result.path))}")
                self.owner._show_write_notes((self.baseline, self.result, result))
        except OperationCancelled:
            QMessageBox.information(self, "已取消", "已取消，完成 0 个导出文件。")
        except PdfError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def done(self, result):
        self.owner._release_unused_docs()
        self.folder.cleanup()
        super().done(result)
