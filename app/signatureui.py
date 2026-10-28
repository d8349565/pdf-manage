"""Signature and protection information for the current workspace."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)
import pymupdf

from app.output import OperationCancelled
from app.pdfio import PdfError
from app.progress import run_operation


STATES = {'pending': '正在检查保护信息', 'none': '未发现已填写的数字签名',
          'present': '已发现数字签名 · 有效性未验证', 'unknown': '签名结构无法确认 · 原件只读'}


class SignatureInfoDialog(QDialog):
    def __init__(self, host, reports):
        super().__init__(host)
        self.host, self.reports = host, reports
        self.action = None
        self.target_page = None
        self.setWindowTitle('签名与保护信息')
        self.resize(820, 680)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        def label(text=''):
            widget = QLabel(text)
            widget.setTextFormat(Qt.TextFormat.PlainText)
            widget.setWordWrap(True)
            widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(widget)
            return widget

        self.source = QComboBox()
        self.source.setAccessibleName('查看来源文件的签名与保护信息')
        for index, report in enumerate(reports):
            self.source.addItem(f'{index + 1}. {report["name"]}')
        layout.addWidget(self.source)
        self.status = label()
        self.protection = label()
        self.permissions = label()
        self.warning = label('此处读取文件中的签名声明，尚未验证内容完整性、证书信任、证书撤销或可信时间戳。')
        self.signatures = QTreeWidget()
        self.signatures.setAccessibleName('数字签名与空白签名字段列表')
        self.signatures.setHeaderLabels(['签名或字段', '原文件页', '状态'])
        self.signatures.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.signatures.setColumnWidth(1, 90)
        self.signatures.setColumnWidth(2, 180)
        layout.addWidget(self.signatures, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName('签名声明与检查原因详情')
        layout.addWidget(self.details, 1)
        label('副本操作作用于整个当前文档，原文件保留。选择来源文件只切换查看内容。')
        self.action_hint = label()
        buttons = QHBoxLayout()
        self.jump = QPushButton('定位签名页')
        self.jump.clicked.connect(lambda: self.finish('jump'))
        buttons.addWidget(self.jump)
        self.editable = QPushButton('制作可编辑副本…')
        self.editable.clicked.connect(lambda: self.finish('editable'))
        buttons.addWidget(self.editable)
        self.raster = QPushButton('制作影像副本…')
        self.raster.clicked.connect(lambda: self.finish('raster'))
        buttons.addWidget(self.raster)
        buttons.addStretch()
        close = QPushButton('关闭')
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        for button in (self.jump, self.editable, self.raster, close):
            button.setAutoDefault(False)
        layout.addLayout(buttons)
        editable_reason = host.store.operation_reason(host.ws.pages, 'editable_copy')
        raster_reason = host.store.operation_reason(host.ws.pages, 'raster_copy')
        if editable_reason and all(report['safety'].signature == 'none' for report in reports):
            editable_reason = '未发现已填写签名，无需制作此类副本。'
        self.editable.setEnabled(not editable_reason)
        self.editable.setToolTip(editable_reason or '另存新文件，保留签名外观；不保留原数字签名验证结构。')
        self.raster.setEnabled(not raster_reason)
        self.raster.setToolTip(raster_reason or '另存页面影像，不保留原文字层、交互表单和数字签名。')
        self.action_hint.setText('\n'.join(filter(None, (
            '可编辑副本：' + editable_reason if editable_reason else '',
            '影像副本：' + raster_reason if raster_reason else ''))))
        self.source.currentIndexChanged.connect(self.show_source)
        self.signatures.currentItemChanged.connect(self.show_signature)
        self.show_source(0)

    def show_source(self, index):
        report = self.reports[index]
        safety = report['safety']
        self.status.setText(STATES.get(safety.signature, STATES['unknown']))
        protection = safety.read_only_reason or '原件保护检查未限制编辑；具体操作仍需满足文件权限。'
        if report['ofd']:
            protection = '以下仅为转换后的 PDF 工作副本信息，不代表 OFD 原件的签名状态。\n' + protection
        self.protection.setText(protection)
        permissions = [('打印', pymupdf.PDF_PERM_PRINT), ('复制', pymupdf.PDF_PERM_COPY),
                       ('修改内容', pymupdf.PDF_PERM_MODIFY), ('整理页面', pymupdf.PDF_PERM_ASSEMBLE),
                       ('批注', pymupdf.PDF_PERM_ANNOTATE)]
        access = '未加密' if not safety.encrypted else ('已用管理密码打开' if safety.owner_authenticated else '已加密 · 当前为阅读权限')
        self.permissions.setText(access + '\n文件权限：' + '；'.join(
            name + ('允许' if safety.allows(bit) else '受限') for name, bit in permissions)
            + '。签名保护仍可能限制修改。')
        self.signatures.blockSignals(True)
        self.signatures.clear()
        for row in report['signatures']:
            self.signatures.addTopLevelItem(QTreeWidgetItem([
                row.name, '、'.join(str(page + 1) for page in row.pages) or '未定位', row.status]))
        self.signatures.blockSignals(False)
        if self.signatures.topLevelItemCount():
            self.signatures.setCurrentItem(self.signatures.topLevelItem(0))
        else:
            self.show_signature()

    def show_signature(self, *_):
        report = self.reports[self.source.currentIndex()]
        item = self.signatures.currentItem()
        self.target_page = None
        lines = []
        if item:
            row = report['signatures'][self.signatures.indexOfTopLevelItem(item)]
            current = [index + 1 for index, page in enumerate(self.host.ws.pages)
                       if page.source_id == report['source_id'] and page.source_page in row.pages]
            self.target_page = current[0] if current else None
            lines = [row.note, '当前文档页：' + ('、'.join(map(str, current)) if current else '未定位或该页不在当前文档中'),
                     '署名（文件声明）：' + (row.signer or '未提供'),
                     '签署时间（文件声明，非可信时间验证）：' + (row.signed_at or '未提供'),
                     '签署原因：' + (row.reason or '未提供'), '签署地点：' + (row.location or '未提供'),
                     '签名格式：' + (row.method or '未提供'),
                     '证书详情：当前版本尚未解析证书，未验证签名有效性。']
        else:
            lines = ['未找到可列出的签名或空白签名字段。', '图片印章和手写外观不一定是数字签名。']
        if report['issues']:
            lines += ['', '检查说明：', *report['issues']]
        elif report['safety'].signature == 'unknown':
            lines += ['', '保护检查未能确认签名结构；当前未获得更具体的原因，原件继续只读。']
        self.details.setPlainText('\n'.join(lines))
        self.jump.setEnabled(self.target_page is not None)

    def finish(self, action):
        self.action = action
        self.accept()


def open_signature_info(host):
    if not host.ws.pages or getattr(host, '_task_dialog', None) is not None:
        return
    workspace = host.ws
    pages = list(workspace.pages)
    try:
        reports = run_operation(host, '读取签名与保护信息',
            lambda progress, cancel: host.store.protection_reports(pages, progress, cancel), cancellable=True)
    except OperationCancelled:
        host.statusBar().showMessage('已取消读取签名信息，原文件保持不变。', 5000)
        return
    except (PdfError, RuntimeError, ValueError) as exc:
        QMessageBox.warning(host, '无法读取保护信息', str(exc))
        return
    if workspace is not host.ws or pages != host.ws.pages:
        host.statusBar().showMessage('当前文档已变化，请重新打开签名与保护信息。', 5000)
        return
    host._sync_controls()
    dialog = SignatureInfoDialog(host, reports)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        if dialog.action == 'jump' and dialog.target_page is not None:
            host.stack.setCurrentIndex(0)
            host.go_to_page(dialog.target_page)
        elif dialog.action == 'editable':
            host.editable_copy()
        elif dialog.action == 'raster':
            host.rasterize_document(recovery=any(report['safety'].signature == 'unknown' for report in reports))
    dialog.deleteLater()
