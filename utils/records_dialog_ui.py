# -*- coding: utf-8 -*-
"""已下记录弹窗：记录文件名 + 「共 N 条」共用 UI。

文件名可点击 → 资源管理器选中该记录文件。
"""

from __future__ import annotations

import os
import subprocess

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QWidget

from styles.style_all import tk
from utils.logger import get_logger

log = get_logger(__name__)


def reveal_in_explorer(path: str) -> bool:
    """打开资源管理器：文件则选中，文件夹则打开；不存在则打开父目录。"""
    path = os.path.normpath(path or "")
    if not path:
        return False
    try:
        if os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", path])
            return True
        if os.path.isdir(path):
            subprocess.Popen(["explorer", path])
            return True
        folder = os.path.dirname(path)
        if os.path.isdir(folder):
            # 文件尚未创建时仍打开所在目录，方便用户看到 userdata
            subprocess.Popen(["explorer", folder])
            return True
    except Exception:
        log.warning("打开文件位置失败 %s", path, exc_info=True)
    return False


class _ClickableFileLabel(QLabel):
    """显示记录文件名；点击在资源管理器中定位。"""

    def __init__(self, file_path: str, parent=None):
        super().__init__(parent)
        self._path = os.path.normpath(file_path or "")
        name = os.path.basename(self._path) if self._path else ""
        self.setText(name or "（无文件）")
        self.setToolTip(self._path or "")
        self.setCursor(Qt.PointingHandCursor)
        font = self.font()
        font.setUnderline(True)
        self.setFont(font)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._path:
            reveal_in_explorer(self._path)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class RecordsTotalBar(QWidget):
    """`other_video.txt  共 12 条 · …`：文件名在前且可点击。"""

    def __init__(self, file_path: str, parent=None, object_name: str = None):
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._file_lbl = _ClickableFileLabel(file_path, self)
        self._count_lbl = QLabel("", self)
        self._count_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self._file_lbl, 0, Qt.AlignVCenter)
        lay.addWidget(self._count_lbl, 1, Qt.AlignVCenter)
        self.apply_theme()

    def apply_theme(self):
        self._file_lbl.setStyleSheet(
            f"QLabel{{color:{tk('accent')};font-size:12px;background:transparent;"
            f"border:none;padding:0 2px;}}"
        )
        # 下划线靠 QFont，样式表里再钉一次以防主题刷新丢
        font = self._file_lbl.font()
        font.setUnderline(True)
        self._file_lbl.setFont(font)
        self._count_lbl.setStyleSheet(
            f"QLabel{{color:{tk('text_mut')};font-size:12px;background:transparent;"
            f"border:none;padding:0 2px;}}"
        )

    def set_count_text(self, count_text: str):
        """count_text 形如「共 12 条」或「共 12 条 · 即梦 5」。"""
        self._count_lbl.setText(count_text or "")

    def setText(self, text: str):
        """兼容旧代码把整串塞进 QLabel.setText 的写法。"""
        self.set_count_text(text)
