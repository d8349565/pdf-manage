"""Choose arrowheads and strokes by their appearance before applying them."""
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup, QColorDialog, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from app.arrows import dash_pattern, geometry
from app.i18n import tr
from app.icons import toolbar_icon
from app.themes import set_theme_style, ui_color


def _luminance(color):
    return sum(weight * (v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4)
               for v, weight in zip(color.getRgbF()[:3], (.2126, .7152, .0722)))


def draw_arrow(painter, start, end, color, width, head, line):
    shaft, tips = geometry(start, end, width, head)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    dashes = dash_pattern(line, width)
    if dashes:
        dashed = QPen(pen)
        dashed.setDashPattern([value / width for value in dashes])
        painter.setPen(dashed)
    painter.drawLine(QPointF(*shaft[0]), QPointF(*shaft[1]))
    painter.setPen(pen)
    for left, tip, right, filled in tips:
        path = QPainterPath(QPointF(*left))
        path.lineTo(QPointF(*tip))
        path.lineTo(QPointF(*right))
        if filled:
            path.closeSubpath()
            painter.fillPath(path, color)
        painter.drawPath(path)


class _Sample(QWidget):
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self.setFixedHeight(44)
        self.setAccessibleName(tr('实时效果预览'))

    def paintEvent(self, event):
        painter = QPainter(self)
        # A light ink such as white needs a dark sample sheet to make its outline visible.
        paper = '#FFFFFF' if _luminance(self.dialog.preview_color) <= .30 else '#232A32'
        painter.fillRect(self.rect(), QColor(paper))
        painter.setPen(QColor(ui_color('#D7E8E4')))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        # Magnify thin strokes on the sample sheet; retain both tips at the maximum width.
        factor = min(2, (self.height() - 8) / (3.3 * self.dialog.stroke_width))
        painter.scale(factor, factor)
        middle = self.height() / (2 * factor)
        draw_arrow(painter, (22 / factor, middle), ((self.width() - 22) / factor, middle),
                   self.dialog.preview_color, self.dialog.stroke_width, **self.dialog.preview_style)


def arrow_icon(head, line, color, ratio, size=QSize(86, 24)):
    pix = QPixmap(size * ratio)
    pix.setDevicePixelRatio(ratio)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    ink, surface = _luminance(color), _luminance(QColor(ui_color('surface')))
    if (max(ink, surface) + .05) / (min(ink, surface) + .05) < 3:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#FFFFFF' if ink < .179 else '#232A32'))
        painter.drawRoundedRect(QRectF(0, 0, size.width(), size.height()), 3, 3)
        painter.setBrush(Qt.BrushStyle.NoBrush)
    middle = size.height() / 2
    draw_arrow(painter, (4, middle), (size.width() - 4, middle), color, 3, head, line)
    painter.end()
    return QIcon(pix)


def ink_icon(color, ratio, *, checked=False, size=18):
    """Show the actual PDF ink in every theme, with a check only on the applied color."""
    pix = QPixmap(QSize(size, size) * ratio)
    pix.setDevicePixelRatio(ratio)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(ui_color('#91B7A9')), 1))
    painter.setBrush(color)
    painter.drawRoundedRect(QRectF(.5, .5, size - 1, size - 1), 3, 3)
    if checked:
        ink = '#FFFFFF' if _luminance(color) < .179 else '#000000'
        toolbar_icon('check', ink, size - 4, themed=False).paint(painter, QRect(2, 2, size - 4, size - 4))
    painter.end()
    return QIcon(pix)


def add_ink_swatches(layout, choose_color):
    swatches = {}
    for value, name in (('#000000', tr('黑色')), ('#dc2626', tr('红色')), ('#ea580c', tr('橙色')),
                        ('#15803d', tr('绿色')), ('#2563eb', tr('蓝色')), ('#7c3aed', tr('紫色'))):
        button = QToolButton()
        button.setProperty('swatch', True)
        button.setProperty('inkColor', value)
        button.setCheckable(True)
        button.setAccessibleName(name)
        button.setToolTip(name)
        button.setFixedSize(26, 26)
        button.setIconSize(QSize(18, 18))
        button.clicked.connect(lambda _, v=value: choose_color(QColor(v)))
        layout.addWidget(button)
        swatches[value] = button
    return swatches


def update_ink_swatches(swatches, color):
    for value, button in swatches.items():
        checked = value == color.name()
        button.setChecked(checked)
        button.setIcon(ink_icon(QColor(value), button.devicePixelRatioF(), checked=checked))


class ArrowStyleDialog(QDialog):
    """An anchored one-click gallery for drawing, or an editor for an existing arrow."""
    def __init__(self, parent, style, color, width, *, quick=False):
        super().__init__(parent, Qt.WindowType.Popup if quick else Qt.WindowType.Dialog)
        self.setWindowTitle(tr('箭头样式'))
        self.setObjectName('arrowGallery')
        self.quick = quick
        self._anchor = None
        self.chosen = self.preview_style = dict(style)
        self.stroke_width = min(30, max(.1, width))
        self.color = QColor(color)
        self.preview_color = self.color
        self.custom_requested = False
        self.tiles = {}
        set_theme_style(self, '''
            QDialog#arrowGallery { background: #FFFFFF; border: 1px solid #D7E8E4; }
            QLabel[axis="true"] { color: #53635B; }
            QToolButton[tile="true"], QToolButton[swatch="true"] {
                min-width: 0; min-height: 0; padding: 0; border: 1px solid transparent;
                border-radius: 4px; background: transparent; }
            QToolButton[tile="true"]:hover, QToolButton[swatch="true"]:hover { background: #EAF1EE; }
            QToolButton[tile="true"]:focus, QToolButton[swatch="true"]:focus { border-color: #0F766E; }
            QToolButton[tile="true"]:checked { background: #E4F2ED; border-color: #72A699; }
            QPushButton#customInk { min-width: 0; min-height: 0; padding: 3px 7px; }
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 11, 12, 10)
        layout.setSpacing(6)
        self.heads = dict(filled=tr('单向实心'), open=tr('单向开口'),
                          double_filled=tr('双向实心'), double_open=tr('双向开口'))
        self.lines = dict(solid=tr('实线'), dashed=tr('虚线'), dotted=tr('点线'))
        title_row = QHBoxLayout()
        self.status_label = QLabel()
        set_theme_style(self.status_label, 'color: #173C36; font-weight: 600;')
        title_row.addWidget(self.status_label)
        title_row.addStretch()
        self.caption = QLabel()
        self.caption.setProperty('axis', True)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        title_row.addWidget(self.caption)
        layout.addLayout(title_row)
        self.sample = _Sample(self)
        layout.addWidget(self.sample)
        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(2)
        buttons = QButtonGroup(self)
        for col, label in enumerate(self.lines.values(), 1):
            axis = QLabel(label)
            axis.setAlignment(Qt.AlignmentFlag.AlignCenter)
            axis.setProperty('axis', True)
            grid.addWidget(axis, 0, col)
        for row, (head, head_label) in enumerate(self.heads.items(), 1):
            axis = QLabel(head_label)
            axis.setProperty('axis', True)
            grid.addWidget(axis, row, 0)
            for col, (line, line_label) in enumerate(self.lines.items(), 1):
                tile = QToolButton()
                tile.setProperty('tile', True)
                tile.setAccessibleName(f'{head_label} · {line_label}')
                tile.setCheckable(True)
                tile.setIconSize(QSize(69, 20))
                tile.setIcon(arrow_icon(head, line, QColor(ui_color('#173C36')), self.devicePixelRatioF(), tile.iconSize()))
                tile.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                tile.setFixedHeight(30)
                tile.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                buttons.addButton(tile)
                tile.setChecked((head, line) == (style['head'], style['line']))
                tile.clicked.connect(lambda _, h=head, l=line: self.choose(h, l))
                self.tiles[head, line] = tile
                tile.installEventFilter(self)
                grid.addWidget(tile, row, col)
        layout.addLayout(grid)
        palette = QHBoxLayout()
        palette.setSpacing(3)
        palette.addWidget(QLabel(tr('颜色')))
        self.swatches = add_ink_swatches(palette, self.choose_color)
        for button in self.swatches.values():
            button.installEventFilter(self)
        self.color_button = QPushButton(tr('自定义颜色'))
        self.color_button.setObjectName('customInk')
        self.color_button.clicked.connect(self.pick_color)
        palette.addWidget(self.color_button)
        palette.addStretch()
        layout.addLayout(palette)
        self.set_color(self.color)
        if not quick:
            controls = QHBoxLayout()
            self.width_input = QDoubleSpinBox()
            self.width_input.setRange(.1, 30)
            self.width_input.setSingleStep(.5)
            self.width_input.setSuffix(' pt')
            self.width_input.setValue(width)
            self.width_input.setAccessibleName(tr('批注线宽'))
            self.width_input.valueChanged.connect(self.set_width)
            controls.addWidget(QLabel(tr('批注线宽')))
            controls.addWidget(self.width_input)
            controls.addStretch()
            layout.addLayout(controls)
            box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            box.button(QDialogButtonBox.StandardButton.Ok).setText(tr('确定'))
            box.button(QDialogButtonBox.StandardButton.Cancel).setText(tr('取消'))
            box.accepted.connect(self.accept)
            box.rejected.connect(self.reject)
            layout.addWidget(box)
        self.preview(style['head'], style['line'])
        self.tiles[style['head'], style['line']].setFocus()

    def anchor_to(self, button):
        self._anchor = button
        self.ensurePolished()
        self.adjustSize()
        self._position_popup()

    def showEvent(self, event):
        super().showEvent(event)
        self._position_popup()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.isVisible():
            self._position_popup()

    def _position_popup(self):
        button = self._anchor
        if button is None:
            return
        anchor = QRect(button.mapToGlobal(QPoint()), button.size())
        screen = (QGuiApplication.screenAt(anchor.center()) or button.screen()).availableGeometry()
        owner = button.window()
        visible = QRect(owner.mapToGlobal(QPoint()), owner.size()).intersected(screen)
        left, right = (visible.left(), visible.right()) if visible.width() >= self.width() else (screen.left(), screen.right())
        top, bottom = (visible.top(), visible.bottom()) if visible.height() >= self.height() else (screen.top(), screen.bottom())
        x = anchor.left() if anchor.left() + self.width() <= right + 1 else anchor.right() - self.width() + 1
        y = anchor.bottom() + 4
        if y + self.height() > bottom + 1 and (anchor.top() - top > bottom - anchor.bottom()):
            y = anchor.top() - self.height() - 3
        self.move(max(left, min(x, right + 1 - self.width())), max(top, min(y, bottom + 1 - self.height())))

    def choose(self, head, line):
        self.chosen = dict(head=head, line=line)
        self.preview(head, line)
        if self.quick:
            self.accept()

    def preview(self, head, line):
        self.preview_style = dict(head=head, line=line)
        self.preview_color = self.color
        self.update_sample()

    def update_sample(self):
        trying = self.preview_style != self.chosen or self.preview_color != self.color
        self.status_label.setText(tr('正在试看') if trying else tr('当前样式'))
        swatch = self.swatches.get(self.preview_color.name())
        name = swatch.accessibleName() if swatch is not None else self.preview_color.name().upper()
        text = f"{name} · {self.heads[self.preview_style['head']]} · {self.lines[self.preview_style['line']]}"
        self.caption.setText(text)
        self.sample.setAccessibleDescription(f'{self.status_label.text()} · {text}')
        self.sample.update()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Enter, QEvent.Type.Leave, QEvent.Type.FocusIn, QEvent.Type.FocusOut, QEvent.Type.KeyPress):
            swatch = watched.property('inkColor')
            if swatch:
                if event.type() == QEvent.Type.KeyPress:
                    if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                        watched.click()
                        return True
                else:
                    self.preview_style = self.chosen
                    self.preview_color = self.color if event.type() in (QEvent.Type.Leave, QEvent.Type.FocusOut) else QColor(swatch)
                    self.update_sample()
            key = next((key for key, tile in self.tiles.items() if tile is watched), None)
            if key:
                if event.type() in (QEvent.Type.Leave, QEvent.Type.FocusOut):
                    self.preview(**self.chosen)
                elif event.type() == QEvent.Type.KeyPress:
                    offsets = {Qt.Key.Key_Left: (0, -1), Qt.Key.Key_Right: (0, 1),
                               Qt.Key.Key_Up: (-1, 0), Qt.Key.Key_Down: (1, 0)}
                    if event.key() in offsets:
                        dy, dx = offsets[event.key()]
                        heads, lines = list(self.heads), list(self.lines)
                        y = min(len(heads) - 1, max(0, heads.index(key[0]) + dy))
                        x = min(len(lines) - 1, max(0, lines.index(key[1]) + dx))
                        self.tiles[heads[y], lines[x]].setFocus()
                        return True
                    if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                        watched.click()
                        return True
                else:
                    self.preview(*key)
        return super().eventFilter(watched, event)

    def set_width(self, width):
        self.stroke_width = width
        self.sample.update()

    def pick_color(self):
        if self.quick:
            # Open the native picker from the toolbar after Qt releases the popup's mouse grab.
            self.custom_requested = True
            self.accept()
            return
        picked = QColorDialog.getColor(self.color, self)
        if picked.isValid():
            self.set_color(picked)

    def choose_color(self, color):
        self.set_color(color)
        if self.quick:
            self.accept()

    def set_color(self, color):
        self.color = self.preview_color = QColor(color)
        update_ink_swatches(self.swatches, self.color)
        custom = self.color.name() not in self.swatches
        self.color_button.setIcon(ink_icon(self.color, self.devicePixelRatioF(), checked=True) if custom else QIcon())
        self.color_button.setToolTip(self.color.name().upper() if custom else tr('自定义颜色'))
        self.update_sample()
