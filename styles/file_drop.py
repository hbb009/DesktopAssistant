"""文件拖放区的配色、虚线框和空状态图标。"""

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QLabel, QStyle

from styles.side_nav_icons import side_nav_colors
from styles.style_all import theme, tk


def _border_color(page_key, active):
    bright, deep = side_nav_colors(page_key)
    color = QColor(bright if theme.is_dark else deep)
    if active:
        color = color.lighter(125) if theme.is_dark else color.darker(120)
    return color


def file_drop_style(page_key, *, active=False):
    color = _border_color(page_key, active).name()
    return f"background:{tk('input_bg')};border:1px dashed {color};border-radius:8px;"


def file_drop_pixmap(widget, size=32):
    return widget.style().standardIcon(QStyle.SP_DirIcon).pixmap(size, size)


def paint_file_drop_outline(painter, rect, page_key, *, active=False):
    width = 2 if active else 1
    inset = width / 2
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(_border_color(page_key, active), width, Qt.DashLine)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(QRectF(rect).adjusted(inset, inset, -inset, -inset), 8, 8)
    painter.restore()


class FileDropPlaceholder(QLabel):
    def __init__(self, page_key, description, parent=None):
        super().__init__(parent)
        self._page_key = page_key
        self.setObjectName("FileDropPlaceholder")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(48)
        self.setAccessibleName(description)
        self.setToolTip(description)
        self.setPixmap(file_drop_pixmap(self))
        self.refresh_theme()
        theme.changed.connect(self.refresh_theme)

    def refresh_theme(self, *_):
        self.setStyleSheet(f"QLabel#FileDropPlaceholder{{{file_drop_style(self._page_key)}}}")
