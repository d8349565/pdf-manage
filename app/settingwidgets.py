"""Controls for preferences that apply as soon as they are changed."""

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QCheckBox, QLabel, QSizePolicy, QVBoxLayout

from app.themes import current_theme, set_theme_style


class SettingSwitch(QCheckBox):
    _TRACK_WIDTH = 36
    _TRACK_HEIGHT = 20
    _TRACK_INSET = 4

    def __init__(self, title: str, detail: str = "", parent=None):
        super().__init__(title, parent)
        self.setAccessibleName(title)
        self.setAccessibleDescription(detail)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.setMinimumHeight(36)

        labels = QVBoxLayout(self)
        labels.setContentsMargins(0, 5, self._TRACK_WIDTH + self._TRACK_INSET + 12, 5)
        labels.setSpacing(2)
        for text, style, wrap in (
            (title, "color: #134E4A;", False),
            (detail, "color: #4D6B67; font-size: 12px;", True),
        ):
            if text:
                label = QLabel(text)
                label.setTextFormat(Qt.TextFormat.PlainText)
                label.setWordWrap(wrap)
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                set_theme_style(label, f"QLabel {{ {style} }} QLabel:disabled {{ color: #94A3B8; }}")
                labels.addWidget(label)

    def sizeHint(self):
        return self.layout().sizeHint()

    def minimumSizeHint(self):
        return self.layout().minimumSize()

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        colors = current_theme().colors
        checked = self.isChecked()
        if not self.isEnabled():
            fill, border, thumb = "subtle", "strong_border", "disabled"
        elif checked:
            fill = "accent_pressed" if self.isDown() else "accent_hover" if self.underMouse() else "accent"
            border, thumb = fill, "on_accent"
        else:
            fill = "selected" if self.isDown() else "hover" if self.underMouse() else "surface"
            border = thumb = "muted"

        track = QRectF(self.width() - self._TRACK_WIDTH - self._TRACK_INSET,
                       (self.height() - self._TRACK_HEIGHT) / 2,
                       self._TRACK_WIDTH, self._TRACK_HEIGHT)
        radius = track.height() / 2
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.hasFocus():
            painter.setPen(QPen(QColor(colors["accent"]), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track.adjusted(-2.5, -2.5, 2.5, 2.5), radius + 2.5, radius + 2.5)
        painter.setPen(QPen(QColor(colors[border])))
        painter.setBrush(QColor(colors[fill]))
        painter.drawRoundedRect(track, radius, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colors[thumb]))
        x = track.right() - radius if checked else track.left() + radius
        painter.drawEllipse(QRectF(x - 6, track.center().y() - 6, 12, 12))
        painter.end()
