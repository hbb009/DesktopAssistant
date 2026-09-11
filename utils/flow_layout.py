# utils/flow_layout.py
# 通用流式布局：子控件从左到右依次摆放，一行放不下时自动换到下一行（纵向增高），
# 不会把父容器橕宽、也就不会在窗口变窄时出现横向滚动条/左右拖动条。
# 用来替换那些"一整排塞很多按钮/控件"的 QHBoxLayout（比如一排预设比例按钮、
# 一排汉字速查按钮、世界时钟的城市卡片网格），窗口越窄就自动换行越多、越高，
# 页面本身的纵向 QScrollArea 负责把多出来的高度滚动掉——刚好符合"不能左右拖动，
# 可以上下滚动"的原则。
#
# 移植自 Qt 官方 C++ "Flow Layout" 示例，精简为项目里实际会用到的部分。

from PyQt5.QtCore import Qt, QPoint, QRect, QSize
from PyQt5.QtWidgets import QLayout


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=0, h_spacing=8, v_spacing=8):
        super().__init__(parent)
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self._items = []
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)

    def __del__(self):
        item = self.takeAt(0)
        while item is not None:
            item = self.takeAt(0)

    def addItem(self, item):
        self._items.append(item)

    def horizontalSpacing(self):
        return self._h_spacing if self._h_spacing >= 0 else 6

    def verticalSpacing(self):
        return self._v_spacing if self._v_spacing >= 0 else 6

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            if item.isEmpty():
                continue
            size = size.expandedTo(item.minimumSize())
        left, top, right, bottom = self.getContentsMargins()
        size += QSize(left + right, top + bottom)
        return size

    def _do_layout(self, rect, test_only):
        """从左到右排布；同一行内高度不同的控件垂直居中（避免 checkbox/标签/下拉顶对齐参差）。

        跳过 isEmpty 项（显式 hide 的控件），否则 0 尺寸项仍会占间距/换行，
        并导致「隐藏完成」后高度与可见卡数对不上。
        """
        left, top, right, bottom = self.getContentsMargins()
        effective_rect = rect.adjusted(left, top, -right, -bottom)
        x = effective_rect.x()
        y = effective_rect.y()
        line_height = 0
        space_x = self.horizontalSpacing()
        space_y = self.verticalSpacing()
        # 当前行缓存：(item, sizeHint, x) — 行结束后再按 line_height 垂直居中落位
        line = []

        def _flush_line(advance=True):
            nonlocal x, y, line_height, line
            if not line:
                return
            if not test_only:
                for it, hint, ix in line:
                    iy = y + max(0, (line_height - hint.height()) // 2)
                    it.setGeometry(QRect(QPoint(ix, iy), hint))
            if advance:
                y = y + line_height + space_y
            line = []
            line_height = 0
            x = effective_rect.x()

        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            if not hint.isValid() or hint.width() <= 0 or hint.height() <= 0:
                continue
            next_x = x + hint.width() + space_x
            # 当前行放不下了（且已经放过至少一个控件）→ 换行
            if next_x - space_x > effective_rect.right() and line:
                _flush_line(advance=True)
                next_x = x + hint.width() + space_x

            line.append((item, hint, x))
            line_height = max(line_height, hint.height())
            x = next_x

        # 最后一行：不额外加 v_spacing
        if line:
            if not test_only:
                for it, hint, ix in line:
                    iy = y + max(0, (line_height - hint.height()) // 2)
                    it.setGeometry(QRect(QPoint(ix, iy), hint))
            y = y + line_height

        return y - rect.y() + bottom
