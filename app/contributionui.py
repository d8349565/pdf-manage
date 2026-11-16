"""Public acknowledgements for feedback and suggestions, without a ranking."""
from random import sample

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QGridLayout, QLabel,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from app.i18n import tr
from app.icons import brand_icon
from app.themes import set_theme_style


# Public nicknames only. Keep feedback records and review notes out of the app.
CONTRIBUTORS = (
    'andywja', 'chiker', 'CraneJ1a', 'fly_hong', 'hao2001', 'hengzwu',
    'hqu8808', 'jie2478', 'katapy', 'ningyil', 'nonfree', 'picoyiyi',
    'ptwnsd', 'qiangmobile', 'qing124A', 'schuhuie', 'simple025', 'skysoga',
    'sundog2008', 'thghx', 'ui1840', 'wfg155', 'WZFF2004', 'xmqph',
    'xvkong', 'yi0147', 'Yu__Geng', 'zcdm', 'z小伟style', '泡菜久坛',
    '红色有角三倍速', '舞动十指间', 'hualishu', 'JDom', 'Randy_zhu',
    '嘿佬', 'L-Yume', 'wl21c', '纸飞机的旅程', '笑看风云V8', '兰登登Latin',
    'lawchou', 'lcxzst', 'rainy9', 'zhczhczhcz',
    'minglimath', 'sjzy137', 'toulanzhu', 'dongyajie1dongyajie0320',
)


class ContributionDialog(QDialog):
    def __init__(self, parent=None, *, first_run=False):
        super().__init__(parent)
        self.setWindowTitle(tr('贡献'))
        self.setWindowIcon(brand_icon())
        self.setMinimumSize(420, 320)
        set_theme_style(self, '''
            QDialog { background: #F3F6F5; color: #134E4A; }
            QLabel#contributionTitle { font-size: 24px; font-weight: 600; }
            QLabel#contributionNote { color: #4D6B67; font-size: 13px; }
            QWidget#contributionNames { background: #FFFFFF; }
            QLabel#contributorName { font-size: 14px; padding: 8px 4px; }
            QScrollArea { background: #FFFFFF; border: 1px solid #D7E8E4; }
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)
        title = QLabel(tr('感谢每一份参与'))
        title.setObjectName('contributionTitle')
        title.setWordWrap(True)
        layout.addWidget(title)
        note = QLabel(tr('纸序 PDF 走到 1.0.0，离不开大家的建议、反馈与支持。感谢每一位参与其中的朋友。\n名单随机展示，不分先后。'))
        note.setObjectName('contributionNote')
        note.setWordWrap(True)
        layout.addWidget(note)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setAccessibleName(tr('贡献名单'))
        body = QWidget()
        body.setObjectName('contributionNames')
        names = QGridLayout(body)
        names.setContentsMargins(4, 12, 4, 12)
        names.setHorizontalSpacing(8)
        names.setVerticalSpacing(4)
        names.setAlignment(Qt.AlignmentFlag.AlignTop)
        for column in range(2):
            names.setColumnStretch(column, 1)
        for index, nickname in enumerate(sample(CONTRIBUTORS, len(CONTRIBUTORS))):
            label = QLabel(nickname)
            label.setObjectName('contributorName')
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                          | Qt.TextInteractionFlag.TextSelectableByKeyboard)
            names.addWidget(label, index // 2, index % 2)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox()
        if first_run:
            close_button = buttons.addButton(tr('开始使用'), QDialogButtonBox.ButtonRole.AcceptRole)
        else:
            close_button = buttons.addButton(tr('关闭'), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        close_button.setDefault(True)
        layout.addWidget(buttons)
        self.resize(560, min(680, self.screen().availableGeometry().height() - 64))
