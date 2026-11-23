"""Compression preview prepared in temporary files, exported only on request."""
from __future__ import annotations
from app.i18n import tr
from app.themes import set_theme_style

import os
import tempfile
from pathlib import Path

from PyQt6.QtCore import QSignalBlocker, QSize, Qt
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from app.exportdialogs import hint, new_pdf_path
from app.icons import toolbar_icon
from app.output import OperationCancelled
from app.pdfio import PdfError, WriteOptions
from app.progress import run_operation
from app.compressionpreview import CompressionPreview
from app.pagetoolsui import ClearComboBox, ClearSpinBox
from app.storage import TemporaryWorkspace


def size_text(size):
    return f"{size / 1024 / 1024:.2f} MB" if size >= 1024 * 1024 else f"{size / 1024:.1f} KB"


class CompressionDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.folder = TemporaryWorkspace('compress')
        self.baseline = None
        self.lossless = None
        self.result = None
        self._preview_sources = set()
        self._busy = False
        self._closing_result = None
        self.setWindowTitle(tr('压缩 PDF'))
        self.resize(1100, 780)
        self.setMinimumSize(820, 580)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # Top control card -------------------------------------------------------
        control_card = QFrame()
        control_card.setObjectName("dialogCard")
        control_layout = QVBoxLayout(control_card)
        control_layout.setContentsMargins(14, 12, 14, 10)
        control_layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(10)
        profile_label = QLabel(tr('压缩档位'))
        profile_label.setObjectName("sectionTitle")
        row.addWidget(profile_label)
        self.profile = ClearComboBox()
        self.profile.setAccessibleName(tr('压缩档位'))
        for name, key in (("原画质（推荐）· 无损优化", "quality"), ("高质量 · 300 DPI", "high"),
                          ("均衡 · 200 DPI", "balanced"), ("适合外发 · 150 DPI", "send"),
                          ("小体积 · 120 DPI", "small"), ("自定义…", "custom")):
            self.profile.addItem(name, key)
        row.addWidget(self.profile, 1)
        self.calculate = QPushButton(tr('开始压缩'))
        self.calculate.setCursor(Qt.CursorShape.PointingHandCursor)
        self.calculate.clicked.connect(self.prepare)
        row.addWidget(self.calculate)
        control_layout.addLayout(row)

        self.advanced = QWidget()
        advanced_layout = QGridLayout(self.advanced)
        advanced_layout.setContentsMargins(0, 2, 0, 2)
        advanced_layout.setHorizontalSpacing(10)
        self.dpi = ClearSpinBox()
        self.dpi.setRange(72, 600)
        self.dpi.setValue(300)
        self.dpi.setSuffix(" DPI")
        self.dpi.setAccessibleName(tr('彩色和灰度图片的目标分辨率'))
        self.quality = ClearSpinBox()
        self.quality.setRange(40, 100)
        self.quality.setValue(92)
        self.quality.setAccessibleName(tr('图片编码质量'))
        self.grayscale = QCheckBox(tr('转为灰度（会移除颜色）'))
        advanced_layout.addWidget(QLabel(tr('图片分辨率')), 0, 0)
        advanced_layout.addWidget(self.dpi, 0, 1)
        advanced_layout.addWidget(QLabel(tr('图片质量')), 0, 2)
        advanced_layout.addWidget(self.quality, 0, 3)
        advanced_layout.addWidget(self.grayscale, 0, 4)
        control_layout.addWidget(self.advanced)
        self.advanced.hide()

        self.note = hint(control_layout, tr('保留图片像素、文字和矢量，自动比较更小的无损编码。需要更小体积时，可选择其他档位。'))

        summary_sep = QFrame()
        summary_sep.setFixedHeight(1)
        set_theme_style(summary_sep, "background: #E5EEEC; border: none;")
        control_layout.addWidget(summary_sep)

        status_row = QHBoxLayout()
        status_row.setSpacing(12)
        self.summary = QLabel(tr('点击「开始压缩」，查看体积变化与前后画质对照。'))
        self.summary.setWordWrap(False)
        set_theme_style(self.summary, 'font-size: 13px; font-weight: 600; color: #1E293B; background: transparent;')
        status_row.addWidget(self.summary)
        self.result_note = QLabel('')
        self.result_note.setObjectName("hint")
        self.result_note.setWordWrap(True)
        self.result_note.hide()
        status_row.addWidget(self.result_note, 1)
        control_layout.addLayout(status_row)
        layout.addWidget(control_card)

        # Unified comparison workspace card --------------------------------------
        compare_card = QFrame()
        compare_card.setObjectName("compareCard")
        set_theme_style(compare_card, """
            QFrame#compareCard {
                background: #FFFFFF;
                border: 1px solid #DFE8E3;
                border-radius: 10px;
            }
            QFrame#compareToolbar {
                background: #F8FBFA;
                border: none;
                border-bottom: 1px solid #E5EEEC;
                border-top-left-radius: 9px;
                border-top-right-radius: 9px;
            }
            QFrame#compareColumns {
                background: #FFFFFF;
                border: none;
                border-bottom: 1px solid #E5EEEC;
            }
            QFrame#compareColumns QWidget {
                background: transparent;
            }
            QFrame#compareToolbar QPushButton[toolZoom="true"] {
                min-height: 20px;
                padding: 2px 0;
                border-radius: 6px;
                font-size: 14px;
            }
            QLabel#colTitle {
                color: #1E293B;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#colMeta {
                color: #526B65;
                font-size: 12px;
            }
            QLabel#colSavings {
                color: #0F766E;
                background: #EAF4EF;
                border-radius: 4px;
                padding: 1px 6px;
                font-size: 11px;
                font-weight: 600;
            }
        """)
        compare_layout = QVBoxLayout(compare_card)
        compare_layout.setContentsMargins(0, 0, 0, 0)
        compare_layout.setSpacing(0)

        toolbar_frame = QFrame()
        toolbar_frame.setObjectName("compareToolbar")
        toolbar = QHBoxLayout(toolbar_frame)
        toolbar.setContentsMargins(12, 6, 12, 6)
        toolbar.setSpacing(6)
        page_label = QLabel(tr('页码'))
        page_label.setObjectName("hint")
        toolbar.addWidget(page_label)
        self.prev_page = QPushButton()
        self.prev_page.setIcon(toolbar_icon('chevron-left', '#526B65', 14))
        self.prev_page.setIconSize(QSize(14, 14))
        self.prev_page.setProperty("toolZoom", True)
        self.prev_page.setAccessibleName(tr('上一页'))
        self.prev_page.setToolTip(tr('上一页'))
        self.prev_page.setFixedWidth(30)
        self.prev_page.setEnabled(False)
        self.prev_page.setCursor(Qt.CursorShape.PointingHandCursor)
        toolbar.addWidget(self.prev_page)
        self.page = ClearSpinBox()
        self.page.setRange(1, len(owner.ws.pages))
        self.page.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page.setFixedWidth(48)
        self.page.setKeyboardTracking(False)
        self.page.setAccessibleName(tr('跳转预览页'))
        self.page.setEnabled(False)
        toolbar.addWidget(self.page)
        page_total = QLabel(f'/ {len(owner.ws.pages)}')
        page_total.setObjectName("hint")
        toolbar.addWidget(page_total)
        self.next_page = QPushButton()
        self.next_page.setIcon(toolbar_icon('chevron-right', '#526B65', 14))
        self.next_page.setIconSize(QSize(14, 14))
        self.next_page.setProperty("toolZoom", True)
        self.next_page.setAccessibleName(tr('下一页'))
        self.next_page.setToolTip(tr('下一页'))
        self.next_page.setFixedWidth(30)
        self.next_page.setEnabled(False)
        self.next_page.setCursor(Qt.CursorShape.PointingHandCursor)
        toolbar.addWidget(self.next_page)
        self.prev_page.clicked.connect(self.page.stepDown)
        self.next_page.clicked.connect(self.page.stepUp)
        toolbar.addStretch()
        self.zoom_out = QPushButton('−')
        self.zoom_out.setProperty("toolZoom", True)
        self.zoom_out.setAccessibleName(tr('缩小两侧预览'))
        self.zoom_out.setFixedWidth(34)
        self.zoom_out.setEnabled(False)
        toolbar.addWidget(self.zoom_out)
        self.zoom = ClearComboBox()
        self.zoom.setAccessibleName(tr('两侧预览缩放'))
        for name, value in (('适合页面', 'page'), ('适合宽度', 'width'), ('100%', 100),
                            ('150%', 150), ('200%', 200), ('300%', 300), ('400%', 400)):
            self.zoom.addItem(name, value)
        self.zoom.setMinimumWidth(116)
        self.zoom.setEnabled(False)
        toolbar.addWidget(self.zoom)
        self.zoom_in = QPushButton('+')
        self.zoom_in.setProperty("toolZoom", True)
        self.zoom_in.setAccessibleName(tr('放大两侧预览'))
        self.zoom_in.setFixedWidth(34)
        self.zoom_in.setEnabled(False)
        toolbar.addWidget(self.zoom_in)
        compare_layout.addWidget(toolbar_frame)

        columns_frame = QFrame()
        columns_frame.setObjectName("compareColumns")
        headings = QHBoxLayout(columns_frame)
        headings.setContentsMargins(0, 0, 0, 0)
        headings.setSpacing(0)

        left_col = QHBoxLayout()
        left_col.setContentsMargins(14, 6, 12, 6)
        left_col.setSpacing(8)
        before_lbl = QLabel(tr('压缩前'))
        before_lbl.setObjectName("colTitle")
        left_col.addWidget(before_lbl)
        self.before_meta = QLabel('')
        self.before_meta.setObjectName("colMeta")
        left_col.addWidget(self.before_meta)
        left_col.addStretch()
        left_box = QWidget()
        left_box.setLayout(left_col)
        headings.addWidget(left_box, 1)

        mid_sep = QFrame()
        mid_sep.setFixedWidth(1)
        set_theme_style(mid_sep, "background: #E5EEEC; border: none;")
        headings.addWidget(mid_sep)

        right_col = QHBoxLayout()
        right_col.setContentsMargins(14, 6, 14, 6)
        right_col.setSpacing(8)
        after_lbl = QLabel(tr('压缩后'))
        after_lbl.setObjectName("colTitle")
        right_col.addWidget(after_lbl)
        self.after_meta = QLabel('')
        self.after_meta.setObjectName("colMeta")
        right_col.addWidget(self.after_meta)
        self.savings_badge = QLabel('')
        self.savings_badge.setObjectName("colSavings")
        self.savings_badge.hide()
        right_col.addWidget(self.savings_badge)
        right_col.addStretch()
        right_box = QWidget()
        right_box.setLayout(right_col)
        headings.addWidget(right_box, 1)
        compare_layout.addWidget(columns_frame)

        self.comparison = CompressionPreview(self)
        self.comparison.stopped.connect(self._preview_stopped)
        self.page.valueChanged.connect(lambda page: (self.comparison.scroll_to_page(page - 1), self._sync_page_nav()))
        self.comparison.page_changed.connect(self._page_changed)
        self.zoom.currentIndexChanged.connect(lambda: self.comparison.set_zoom(self.zoom.currentData()))
        self.zoom_out.clicked.connect(lambda: self.comparison.step_zoom(-1))
        self.zoom_in.clicked.connect(lambda: self.comparison.step_zoom(1))
        self.comparison.zoom_changed.connect(self._zoom_changed)
        compare_layout.addWidget(self.comparison, 1)
        layout.addWidget(compare_card, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        hint(footer, tr('两侧同步 · 滚轮翻页 · Ctrl+滚轮缩放 · 拖动查看细节'))
        footer.setStretch(0, 1)
        close = QPushButton(tr('关闭'))
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        self.export = QPushButton(tr('另存压缩结果…'))
        self.export.setEnabled(False)
        self.export.clicked.connect(self.save_result)
        footer.addWidget(self.export)
        layout.addLayout(footer)
        self._sync_action_emphasis()
        self.calculate.setFocus()
        self.profile.currentIndexChanged.connect(self.invalidate)
        self.dpi.valueChanged.connect(self.invalidate)
        self.quality.valueChanged.connect(self.invalidate)
        self.grayscale.toggled.connect(self.invalidate)

    def _sync_action_emphasis(self):
        has_result = self.result is not None
        for btn, primary in ((self.calculate, not has_result), (self.export, has_result)):
            btn.setProperty("primary", primary)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _sync_page_nav(self):
        ready = not self._busy and self.result is not None
        self.prev_page.setEnabled(ready and self.page.value() > self.page.minimum())
        self.next_page.setEnabled(ready and self.page.value() < self.page.maximum())

    def invalidate(self):
        self.result = None
        self.export.setEnabled(False)
        self.page.setEnabled(False)
        self.prev_page.setEnabled(False)
        self.next_page.setEnabled(False)
        self.zoom.setEnabled(False)
        self.zoom_in.setEnabled(False)
        self.zoom_out.setEnabled(False)
        self.before_meta.setText('')
        self.after_meta.setText('')
        self.savings_badge.hide()
        self._sync_action_emphasis()
        self.comparison.clear(tr('档位已更改，点击「开始压缩」查看新结果'))
        self.result_note.hide()
        key = self.profile.currentData()
        self.advanced.setVisible(key == "custom")
        notes = {
            "quality": tr("原画质：保留全部图片像素，逐张比较无损编码；合并重复对象，精简静态文档中未使用的字体字符。"),
            "high": tr("高质量：高分辨率图片按需降至 300 DPI，JPEG 质量 92；无损线稿继续使用无损编码。"),
            "balanced": tr("均衡：高分辨率图片按需降至 200 DPI，JPEG 质量 85，兼顾阅读清晰度与文件大小。"),
            "send": tr("适合外发：高分辨率图片按需降至 150 DPI，JPEG 质量 80，请核对小字与细线。"),
            "small": tr("小体积：高分辨率图片按需降至 120 DPI，JPEG 质量 72，图片细节损失更明显。"),
            "custom": tr("自定义：图片按需降至 {p0} DPI，JPEG 质量 {p1}；质量 100 仍不等于无损。", p0=self.dpi.value(), p1=self.quality.value()),
        }
        self.note.setText(notes[key] + tr(' 文字和矢量内容保留；黑白图片不降采样。'))
        self.summary.setText(tr('档位已调整，点击「开始压缩」重新生成预览。'))

    def _set_busy(self, busy):
        self._busy = busy
        ready = not busy and self.result is not None
        self.calculate.setEnabled(not busy)
        self.profile.setEnabled(not busy)
        self.advanced.setEnabled(not busy)
        self.page.setEnabled(ready)
        self._sync_page_nav()
        self.zoom.setEnabled(ready)
        self.zoom_in.setEnabled(ready)
        self.zoom_out.setEnabled(ready)
        self.export.setEnabled(ready)
        self._sync_action_emphasis()

    def _release_old_results(self):
        keep = {page.source_id for result in (self.baseline, self.lossless, self.result) if result for page in result.pages}
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
        self.comparison.clear('正在压缩，完成后显示对照预览…')
        self.result_note.hide()
        self.result = None
        self._set_busy(True)
        self.summary.setText(tr('正在计算压缩效果…'))
        try:
            if self.baseline is None:
                self.baseline = self.owner._write_pdf(
                    list(self.owner.ws.pages), os.path.join(self.folder.name, "before.pdf"),
                    "准备原始效果", register_result=True, cancellable=True)
                if self.baseline is None:
                    self._show_cancelled()
                    return
                self._preview_sources.update(page.source_id for page in self.baseline.pages)
            if self.lossless is None:
                self.lossless = self.owner._write_pdf(
                    list(self.baseline.pages), os.path.join(self.folder.name, 'lossless.pdf'),
                    '无损优化 PDF', register_result=True, cancellable=True,
                    options=WriteOptions(compression='quality'))
                if self.lossless is None:
                    self._show_cancelled()
                    return
                self._preview_sources.update(page.source_id for page in self.lossless.pages)
            key = self.profile.currentData()
            path = os.path.join(self.folder.name, f"after-{key}.pdf")
            # Use a fresh target on repeated calculation; the old preview may still be loaded.
            if os.path.exists(path):
                fd, path = tempfile.mkstemp(suffix=".pdf", dir=self.folder.name)
                os.close(fd)
                Path(path).unlink()
            self.result = self.lossless if key == "quality" else self.owner._write_pdf(
                list(self.baseline.pages), path, "压缩 PDF", register_result=True,
                options=WriteOptions(compression=key, compression_dpi=self.dpi.value(),
                    compression_quality=self.quality.value(),
                    compression_grayscale=key == "custom" and self.grayscale.isChecked()),
                cancellable=True)
            if self.result is None:
                self._show_cancelled()
                return
            self._preview_sources.update(page.source_id for page in self.result.pages)
            before = os.path.getsize(self.baseline.path)
            if not self.owner.ws.dirty and self.owner.ws.path:
                sources = {p.source_id for p in self.owner.ws.pages}
                if len(sources) == 1:
                    before = len(self.owner.store._source(next(iter(sources))).data)
            fallback_note = ""
            after = os.path.getsize(self.result.path)
            if self.result is not self.lossless and after >= os.path.getsize(self.lossless.path):
                # Extra image recompression must not replace a smaller, clearer result.
                # Explicit grayscale requests keep the requested appearance.
                if not (key == "custom" and self.grayscale.isChecked()):
                    self.result = self.lossless
                    after = os.path.getsize(self.lossless.path)
                    fallback_note = tr("当前档位未进一步缩小，已保留更小的无损结果。")
            self.reduced = after < before
            change = tr("缩小 {p0}%", p0=f'{(1 - after / before) * 100:.1f}') if self.reduced else tr("没有缩小，可自行决定是否导出")
            baseline_note = tr("（工作区基准）") if self.owner.ws.dirty else ""
            self.summary.setText(f"{size_text(before)} → {size_text(after)} · {change}{baseline_note}")
            self.before_meta.setText(size_text(before))
            self.after_meta.setText(size_text(after))
            if self.reduced:
                self.savings_badge.setText(f"-{(1 - after / before) * 100:.1f}%")
                self.savings_badge.show()
            else:
                self.savings_badge.hide()
            self.result_note.setText(fallback_note or (tr('原画质结果：图片像素保留，可放大对照细节。') if self.result is self.lossless
                                                      else tr('当前档位可能改变图片细节，请放大核对小字与细线。')))
            self.result_note.show()
        except OperationCancelled:
            self._show_cancelled()
        except PdfError as exc:
            self.result = None
            self.summary.setText(tr('压缩未完成，可调整档位后重试。'))
            self.comparison.clear('压缩未完成，原文件保留')
            QMessageBox.warning(self, tr('压缩未完成'), str(exc))
        finally:
            self._release_old_results()
            self._set_busy(False)
        if self.result is not None:
            self.preview()

    def _show_cancelled(self):
        self.summary.setText(tr('计算已取消，原件保留；可重新计算。'))
        self.comparison.clear('计算已取消，可以重新开始')

    def preview(self):
        if self._busy or not self.result or not self.baseline:
            return
        self.comparison.set_documents(self.owner.store, self.baseline.pages, self.result.pages)

    def _page_changed(self, page):
        with QSignalBlocker(self.page):
            self.page.setValue(page + 1)
        self._sync_page_nav()

    def _zoom_changed(self, mode):
        with QSignalBlocker(self.zoom):
            index = self.zoom.findData(mode)
            if index < 0:
                if self.zoom.count() > 7:
                    self.zoom.removeItem(7)
                self.zoom.addItem(f'{mode}%', mode)
                index = self.zoom.count() - 1
            self.zoom.setCurrentIndex(index)

    def save_result(self):
        if self._busy or not self.result:
            return
        if not self.reduced and QMessageBox.question(self, tr('没有缩小'), tr('压缩结果没有变小，仍要另存吗？'),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        stem = Path(self.owner.ws.path or "文档.pdf").stem
        path = new_pdf_path(self, self.owner.store, tr('另存压缩结果'), stem + tr('_压缩.pdf'))
        if not path:
            return
        self._set_busy(True)
        try:
            result = self.owner._write_pdf(list(self.result.pages), path, "导出压缩结果", cancellable=True,
                                          options=WriteOptions(compression="quality"))
            if result is not None:
                QMessageBox.information(self, tr('已导出'), tr('已完整导出 1 个文件：\n{p0}\n大小：{p1}', p0=f'{result.path}', p1=f'{size_text(os.path.getsize(result.path))}'))
                self.owner._show_write_notes((self.baseline, self.result, result))
        except OperationCancelled:
            QMessageBox.information(self, tr('已取消'), tr('已取消，完成 0 个导出文件。'))
        except PdfError as exc:
            QMessageBox.warning(self, tr('导出失败'), str(exc))
        finally:
            self._set_busy(False)

    def done(self, result):
        if self._busy:
            dialog = getattr(self, "_task_dialog", None) or getattr(self.owner, "_task_dialog", None)
            if dialog:
                dialog.request_cancel()
            return
        self.comparison.shutdown()
        if self.comparison.busy:
            self._closing_result = result
            return
        self.owner._release_unused_docs()
        self.folder.cleanup()
        super().done(result)

    def _preview_stopped(self):
        if self._closing_result is not None:
            self.done(self._closing_result)
