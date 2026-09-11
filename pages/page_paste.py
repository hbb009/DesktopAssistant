# -*- coding: utf-8 -*-
"""
page_paste.py  —  粘贴助手（提示词仓库）

集成于「桌面助手」主程序，作为侧边栏「粘贴助手」页面（PagePaste）。
记录文件：<项目根>/data/paste_helper.txt

功能：
  · 录入：主题 + 内容 + 七色选色 + 保存/更新
  · 仓库：彩色主题卡片（流式排列，可拖拽改序）
  · 单击卡片：复制内容 + 选中 + 进入修改（填回录入区）
  · 再点同一张：只复制，不重刷表单（避免抖动）
  · 右键卡片：删除
"""

import os
import re
import sys
import json
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from styles.style_all import (
    theme, tk, install_card_title, apply_btn_download, make_card,
    CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP,
)

from PyQt5.QtCore import Qt, QTimer, QPoint, QRect, QRectF, QSize, QMimeData, QEvent, QPointF
from PyQt5.QtGui import (
    QFont, QFontMetrics, QColor, QDrag, QPixmap, QPainter, QPen, QBrush, QPolygonF,
)
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QAbstractButton,
    QLineEdit, QTextEdit, QScrollArea, QFrame, QMessageBox,
    QSizePolicy, QApplication, QLayout, QButtonGroup, QAbstractScrollArea,
)

# 色块基准边长（旧圆点 22px）；录入菱形 = 一半×1.5，筛选圆 = 30%/70%
_SWATCH_BASE = 22

# ── 路径 ─────────────────────────────────────────────────────────────────────
def _paste_record_file() -> str:
    try:
        from utils.app_paths import records_file
        return records_file("paste_helper.txt")
    except Exception:
        root = os.path.dirname(_HERE) if os.path.basename(_HERE).lower() == "pages" else _HERE
        return os.path.join(root, "data", "paste_helper.txt")


# 兼容旧引用：模块加载时定一次路径；写盘前也会再取
RECORD_FILE = _paste_record_file()

# 七种常用色：红、橙、黄、绿、青、蓝、紫
CARD_COLORS = [
    ("#ef4444", "红"),
    ("#f97316", "橙"),
    ("#eab308", "黄"),
    ("#22c55e", "绿"),
    ("#06b6d4", "青"),
    ("#3b82f6", "蓝"),
    ("#a855f7", "紫"),
]
DEFAULT_COLOR = CARD_COLORS[0][0]

# 卡片尺寸：一排稳放 4 个汉字，可排两行（固定，不随悬停变化）
# 边距须与 ThemeCard 内 layout margins 一致，否则会少算可用字宽
_CARD_MARGIN_X = 2
_CARD_MARGIN_Y = 2


def _card_font() -> QFont:
    """粘贴卡标题字体：15px / 600。"""
    f = QFont("微软雅黑", 10)
    f.setPixelSize(15)
    f.setWeight(QFont.DemiBold)
    return f


def _card_line_height() -> int:
    """标题行高：默认行距再收 2px（同提示词编辑器 _VTitle 的做法）。"""
    return max(10, int(QFontMetrics(_card_font()).lineSpacing()) - 2)


def _card_metrics():
    fm = QFontMetrics(_card_font())
    # 用真实四字串测宽（比单字×4 更准）。旧版只留 4px 余量，粗体+抗锯齿下
    # 四个汉字会被挤折行（如「处理背景」显示成「处理背/景」），这里把余量加到
    # 18px，并把最小宽度抬到 104，保证 4 个全角汉字稳稳排在一行。
    text_w = max(fm.horizontalAdvance("汉字汉字"), fm.boundingRect("汉字汉字").width())
    text_h = _card_line_height() * 2
    w = text_w + _CARD_MARGIN_X * 2 + 18
    h = text_h + _CARD_MARGIN_Y * 2
    return max(104, w), max(50, h)

MIME_CARD_ID = "application/x-paste-helper-card-id"
DRAG_THRESHOLD = 8  # 像素，超过才算拖拽，避免误触


def _tok(name, fallback):
    try:
        v = tk(name)
        return v if v else fallback
    except Exception:
        return fallback


def _transparent_bg(w: QWidget) -> QWidget:
    """中间层容器透明底：避免吃到全局 QWidget 画布色，与 GroupBox 功能区色不一致。"""
    w.setAttribute(Qt.WA_StyledBackground, True)
    w.setAutoFillBackground(False)
    w.setStyleSheet("background: transparent;")
    return w


class _FormHintLabel(QLabel):
    """录入区说明文字：可换行；高度锁死，避免默认/编辑提示换行数不同带动布局抖。"""

    # 窄栏约 2 行说明的高度；初始与点卡后提示文案都压在此槽位内
    FIXED_H = 40

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setFixedHeight(self.FIXED_H)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def minimumSizeHint(self):
        return QSize(0, self.FIXED_H)

    def sizeHint(self):
        return QSize(120, self.FIXED_H)

    def hasHeightForWidth(self):
        return False

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 高度始终固定，不随文案 / 宽度重算（避免点卡后录入区「内容」框被挤高挤矮）
        if self.height() != self.FIXED_H:
            self.setFixedHeight(self.FIXED_H)


# ── 存储 ─────────────────────────────────────────────────────────────────────
def _ensure_dir():
    global RECORD_FILE
    RECORD_FILE = _paste_record_file()
    os.makedirs(os.path.dirname(RECORD_FILE), exist_ok=True)


def _normalize_record(r: dict) -> dict:
    if "theme" not in r and "title" in r:
        r["theme"] = r.get("title") or ""
    if "color" not in r or not r.get("color"):
        r["color"] = DEFAULT_COLOR
    if "order" not in r:
        r["order"] = 0
    return r


def _load_records() -> list:
    if not os.path.exists(RECORD_FILE):
        return []
    records = []
    with open(RECORD_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(_normalize_record(json.loads(line)))
            except json.JSONDecodeError:
                continue
    # 旧数据无 order：按原文件顺序编号
    if records and all(int(r.get("order") or 0) == 0 for r in records):
        for i, r in enumerate(records):
            r["order"] = i
    return records


def _save_records(records: list):
    """原子写入 paste_helper.txt（写 .tmp 再 replace，避免半截损坏）。"""
    _ensure_dir()
    tmp = RECORD_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, RECORD_FILE)


def _sorted_by_order(records: list) -> list:
    return sorted(records, key=lambda r: int(r.get("order") or 0))


def _reindex(records: list) -> list:
    """按当前列表顺序重写 order：0..n-1"""
    for i, r in enumerate(records):
        r["order"] = i
    return records


# ── 流式布局 ─────────────────────────────────────────────────────────────────
class FlowLayout(QLayout):
    """
    卡片自动换行。

    注意：minimumSize / sizeHint 只按「单张卡片」算，绝不按
    heightForWidth(窄宽度) 把所有卡片竖着叠起来的高度上报——
    否则会一路顶到顶层窗口，把 minimumHeight 越撑越大，窗口再也缩不小
   （主程序里 wordWrap / FlowLayout 踩过的同类坑）。
    """

    def __init__(self, parent=None, margin=0, h_spacing=10, v_spacing=10):
        super().__init__(parent)
        self._h = h_spacing
        self._v = v_spacing
        self._items = []
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._layout(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, False)

    def sizeHint(self):
        # 给滚动区一个「单行高度」的合理提示，不把整列卡片高度上报
        return self.minimumSize()

    def minimumSize(self):
        # 只取子项中最大的那一个，不累加、不按窄宽叠高
        s = QSize(0, 0)
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
            s = s.expandedTo(it.sizeHint())
        if s.width() <= 0 or s.height() <= 0:
            cw, ch = _card_metrics()
            s = QSize(cw, ch)
        l, t, r, b = self.getContentsMargins()
        return s + QSize(l + r, t + b)

    def _layout(self, rect, test_only):
        l, t, r, b = self.getContentsMargins()
        effective = rect.adjusted(l, t, -r, -b)
        x, y = effective.x(), effective.y()
        line_h = 0
        # 宽度无效时按单行处理，避免测试布局时算出夸张高度
        if effective.width() <= 0:
            for it in self._items:
                hint = it.sizeHint()
                line_h = max(line_h, hint.height())
            return line_h + t + b

        for it in self._items:
            hint = it.sizeHint()
            next_x = x + hint.width() + self._h
            if next_x - self._h > effective.right() and line_h > 0:
                x = effective.x()
                y = y + line_h + self._v
                next_x = x + hint.width() + self._h
                line_h = 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + b


# ── 颜色工具 ─────────────────────────────────────────────────────────────────
def _lighten(hex_color: str, factor: float = 0.14) -> str:
    c = QColor(hex_color)
    return QColor(
        min(255, int(c.red() + (255 - c.red()) * factor)),
        min(255, int(c.green() + (255 - c.green()) * factor)),
        min(255, int(c.blue() + (255 - c.blue()) * factor)),
    ).name()


# ── 自绘色块：录入菱形 / 筛选圆点 ─────────────────────────────────────────────
class DiamondSwatch(QAbstractButton):
    """录入区色块：菱形；边长 = 旧圆点一半 × 1.5。

    选中态：菱形略放大 + 高对比描边（亮色用深环，避免白边在浅底上「变小」）。
    """

    # 旧基准 22 的一半 = 11，再 ×1.5 → 17
    SIZE = max(12, int(round((_SWATCH_BASE // 2) * 1.5)))  # 17
    # 槽位留足选中放大与描边，避免裁切
    SLOT = SIZE + 12  # 29

    def __init__(self, color_hex: str, parent=None):
        super().__init__(parent)
        self._color = color_hex
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(self.SLOT, self.SLOT)
        self.setProperty("swatch", color_hex)
        # 透明底：菱形外四角不套全局 QWidget 画布色
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent; border: none;")

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        checked = self.isChecked()
        hover = self.underMouse()
        # 选中略放大（约 +12%），悬停略抬一点；描边画在外侧不吃填充面积
        if checked:
            scale = 1.14
        elif hover:
            scale = 1.06
        else:
            scale = 1.0
        half = self.SIZE / 2.0 * scale
        poly = QPolygonF([
            QPointF(cx, cy - half),
            QPointF(cx + half, cy),
            QPointF(cx, cy + half),
            QPointF(cx - half, cy),
        ])

        # 亮色主题下选中用深色环；深色主题用亮环
        is_dark = True
        try:
            is_dark = bool(theme.is_dark)
        except Exception:
            is_dark = True

        p.setBrush(QBrush(QColor(self._color)))
        if checked:
            if is_dark:
                ring = QColor("#ffffff")
            else:
                # 浅底上白描边几乎看不见，还会像把色块「削小」
                ring = QColor("#0f172a")
            pen = QPen(ring, 2.2)
            pen.setJoinStyle(Qt.MiterJoin)
            p.setPen(pen)
            p.drawPolygon(poly)
            # 内圈高光：加深选中感，不缩小色面
            if not is_dark:
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(255, 255, 255, 180), 1.2))
                inset = half * 0.78
                poly_in = QPolygonF([
                    QPointF(cx, cy - inset),
                    QPointF(cx + inset, cy),
                    QPointF(cx, cy + inset),
                    QPointF(cx - inset, cy),
                ])
                p.drawPolygon(poly_in)
        elif hover:
            if is_dark:
                pen = QPen(QColor(255, 255, 255, 210), 1.4)
            else:
                pen = QPen(QColor(15, 23, 42, 140), 1.4)
            p.setPen(pen)
            p.drawPolygon(poly)
        else:
            if is_dark:
                pen = QPen(QColor(255, 255, 255, 70), 1.0)
            else:
                pen = QPen(QColor(15, 23, 42, 55), 1.0)
            p.setPen(pen)
            p.drawPolygon(poly)
        p.end()

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)


class CircleFilterSwatch(QAbstractButton):
    """
    仓库筛选色块：槽位固定，避免布局抖动。
    基准 22 × 1.5 = 33；未选中直径 ≈ 30%，选中 ≈ 70%。
    """

    SLOT = max(16, int(round(_SWATCH_BASE * 1.5)))  # 22 → 33
    R_OFF = 0.30
    R_ON = 0.70

    def __init__(self, color_hex: str, parent=None):
        super().__init__(parent)
        self._color = color_hex
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(self.SLOT, self.SLOT)
        self.setProperty("swatch", color_hex)
        # 透明底：圆外槽位不套全局 QWidget 画布色
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent; border: none;")

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        checked = self.isChecked()
        ratio = self.R_ON if checked else self.R_OFF
        d = self.SLOT * ratio
        x = (self.width() - d) / 2.0
        y = (self.height() - d) / 2.0
        p.setBrush(QBrush(QColor(self._color)))

        is_dark = True
        try:
            is_dark = bool(theme.is_dark)
        except Exception:
            pass

        if checked:
            # 亮色用深环，避免白边在浅底上「削小」选中圆
            ring = QColor("#ffffff") if is_dark else QColor("#0f172a")
            pen = QPen(ring, 2.0)
        elif self.underMouse():
            if is_dark:
                pen = QPen(QColor(255, 255, 255, 200), 1.4)
            else:
                pen = QPen(QColor(15, 23, 42, 140), 1.4)
        else:
            if is_dark:
                pen = QPen(QColor(255, 255, 255, 60), 1.0)
            else:
                pen = QPen(QColor(15, 23, 42, 50), 1.0)
        p.setPen(pen)
        p.drawEllipse(QRectF(x, y, d, d))
        p.end()

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)


# ── 录入区：七色选色条 ───────────────────────────────────────────────────────
class ColorPickerBar(QWidget):
    """七色菱形，单选。边长约为旧圆点一半 × 1.5。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        _transparent_bg(self)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._color = DEFAULT_COLOR

        wrap = QVBoxLayout(self)
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.setSpacing(6)

        lbl = QLabel("颜色")
        lbl.setObjectName("CalcFieldLabel")
        wrap.addWidget(lbl)

        lay = QHBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        for i, (hex_c, name) in enumerate(CARD_COLORS):
            btn = DiamondSwatch(hex_c)
            self._group.addButton(btn, i)
            lay.addWidget(btn, 0, Qt.AlignVCenter)

        lay.addStretch(1)
        wrap.addLayout(lay)
        self._group.buttonClicked.connect(self._on_clicked)
        first = self._group.button(0)
        if first:
            first.setChecked(True)

    def _on_clicked(self, btn):
        self._color = btn.property("swatch") or DEFAULT_COLOR
        # 菱形自绘，勾选态在 paintEvent 里读 isChecked()
        for b in self._group.buttons():
            b.update()

    def color(self) -> str:
        return self._color or DEFAULT_COLOR

    def set_color(self, color: str):
        color = (color or DEFAULT_COLOR).lower()
        matched = False
        for b in self._group.buttons():
            hex_c = (b.property("swatch") or "").lower()
            if hex_c == color:
                b.setChecked(True)
                self._color = b.property("swatch")
                matched = True
            else:
                b.setChecked(False)
        if not matched:
            b0 = self._group.button(0)
            if b0:
                b0.setChecked(True)
                self._color = DEFAULT_COLOR
        for b in self._group.buttons():
            b.update()


# ── 仓库区：七色快速过滤（支持多色同时选中）────────────────────────────────
class ColorFilterBar(QWidget):
    """
    [筛选/取消] + 七色圆点（可多选）。
    · 无文字标签；按钮文案：无色选中时「筛选」，有色选中时「取消」
    · 点「取消」：清空所有颜色筛选
    · 未选中小圆 30%，选中中圆 70%；槽位固定不抖布局
    """

    def __init__(self, on_change, parent=None):
        super().__init__(parent)
        _transparent_bg(self)
        self._on_change = on_change
        self._swatches = []  # CircleFilterSwatch 列表

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.btn_action = QPushButton("筛选")
        self.btn_action.setFixedHeight(26)
        self.btn_action.setMinimumWidth(52)
        self.btn_action.setCursor(Qt.PointingHandCursor)
        self.btn_action.setObjectName("ColorFilterAction")
        self.btn_action.clicked.connect(self._on_action_clicked)
        lay.addWidget(self.btn_action)

        for hex_c, name in CARD_COLORS:
            btn = CircleFilterSwatch(hex_c)
            btn.clicked.connect(self._on_swatch_clicked)
            self._swatches.append(btn)
            lay.addWidget(btn)

        lay.addStretch(1)
        self._sync_action_btn()

    def _action_qss(self, active: bool) -> str:
        """active=True 表示当前有颜色筛选，按钮显示「取消」。"""
        if active:
            return f"""
                QPushButton#ColorFilterAction {{
                    background: rgba(240,165,66,0.16);
                    border: 1px solid rgba(240,165,66,0.50);
                    border-radius: 6px;
                    color: {_tok('warn', '#f0a542')};
                    font-size: 12px;
                    font-weight: 700;
                    padding: 0 10px;
                }}
                QPushButton#ColorFilterAction:hover {{
                    background: rgba(240,165,66,0.28);
                }}
            """
        # 未筛选：半透明底，跟功能区同色系，不套全局画布色
        return f"""
            QPushButton#ColorFilterAction {{
                background: {_tok('hover_veil', 'rgba(255,255,255,0.05)')};
                border: 1px solid {_tok('border_soft', 'rgba(255,255,255,0.18)')};
                border-radius: 6px;
                color: {_tok('text_mut', '#9fb0d7')};
                font-size: 12px;
                font-weight: 600;
                padding: 0 10px;
            }}
            QPushButton#ColorFilterAction:hover {{
                background: {_tok('row_bg', 'rgba(255,255,255,0.09)')};
            }}
        """

    def selected_colors(self) -> list:
        """当前勾选的颜色 hex 列表（可多个）。"""
        return [
            b.property("swatch")
            for b in self._swatches
            if b.isChecked() and b.property("swatch")
        ]

    def _sync_action_btn(self):
        colors = self.selected_colors()
        active = len(colors) > 0
        self.btn_action.setText("取消" if active else "筛选")
        self.btn_action.setStyleSheet(self._action_qss(active))
        for b in self._swatches:
            b.update()

    def _emit(self):
        if callable(self._on_change):
            self._on_change(self.selected_colors())

    def _on_swatch_clicked(self):
        # 多选：每个色块独立勾选/取消，不互斥
        self._sync_action_btn()
        self._emit()

    def _on_action_clicked(self):
        if self.selected_colors():
            # 「取消」：清空所有颜色筛选
            self.clear()
            self._emit()
        # 无筛选时点「筛选」：无需操作（靠点色块开始筛）

    def clear(self):
        for b in self._swatches:
            b.setChecked(False)
            b.update()
        self._sync_action_btn()


# ── 主题卡片 ─────────────────────────────────────────────────────────────────
def _wrap_card_text(text: str, fm, width: int) -> list:
    """把标题按宽度逐字换行（最多两行，超出由卡片高度裁掉）。"""
    width = max(8, int(width))
    lines = []
    cur = ""
    for ch in (text or ""):
        trial = cur + ch
        if not cur or fm.horizontalAdvance(trial) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


class _CardTitle(QWidget):
    """粘贴卡标题：最多两行自动换行；行距比默认收 2px；配色走主题文字色
    （参考「图片处理 → 提高清晰度」的文字）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._fg = QColor("#d7def7")

    def set_title(self, text: str):
        self._text = (text or "").strip()
        self.update()
        self.updateGeometry()

    def set_color(self, color: str):
        try:
            c = QColor(color)
            if c.isValid():
                self._fg = c
        except Exception:
            pass
        self.update()

    def sizeHint(self):
        return QSize(4, _card_line_height() * 2)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        f = _card_font()
        p.setFont(f)
        p.setPen(self._fg)
        fm = QFontMetrics(f)
        step = _card_line_height()
        lines = _wrap_card_text(self._text, fm, self.width())[:2]
        if not lines:
            p.end()
            return
        total = step * len(lines)
        y0 = max(0, (self.height() - total) // 2)
        for i, ln in enumerate(lines):
            p.drawText(
                QRect(0, y0 + i * step, self.width(), step),
                Qt.AlignHCenter | Qt.AlignVCenter,
                ln,
            )
        p.end()


class ThemeCard(QFrame):
    """
    仓库卡片：只显示主题文字。
    · 单击 = 复制 + 进入修改
    · 拖拽 = 改顺序
    · 右键 = 删除
    热区：仅边框/亮度变化，尺寸始终不变。
    """

    def __init__(self, record, page, parent=None):
        super().__init__(parent)
        self._record = record
        self._page = page
        self._press_pos = None
        self._dragging = False
        self._selected = False

        self.setObjectName("ThemeCard")
        self.setAcceptDrops(True)
        self.setCursor(Qt.OpenHandCursor)
        # 固定尺寸：约 4 字宽 × 2 行高，杜绝悬停变大变小
        cw, ch = _card_metrics()
        self.setFixedSize(cw, ch)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        theme = (record.get("theme") or record.get("title") or "未命名").strip() or "未命名"

        lay = QVBoxLayout(self)
        lay.setContentsMargins(_CARD_MARGIN_X, _CARD_MARGIN_Y, _CARD_MARGIN_X, _CARD_MARGIN_Y)
        lay.setSpacing(0)

        self.lbl_theme = _CardTitle()
        self.lbl_theme.set_title(theme)
        lay.addWidget(self.lbl_theme, 1)

        self._apply_style(record.get("color") or DEFAULT_COLOR, selected=False)

    def record_id(self):
        return self._record.get("id")

    def set_selected(self, selected: bool):
        # 状态未变则跳过：避免重复 setStyleSheet 引发卡片/流式布局闪动
        if self._selected == selected:
            return
        self._selected = selected
        self._apply_style(self._record.get("color") or DEFAULT_COLOR, selected=selected)

    def _apply_style(self, color: str, selected: bool = False):
        hover = _lighten(color, 0.10)
        # 边框始终 2px，只改颜色——选中时若 1px↔2px 切换，流式排布会抖一下
        if selected:
            border = "#ffffff"
        else:
            border = "rgba(255,255,255,0.14)"
        border_w = 2
        # 注意：hover 不改变 width/height/padding/margin，只改颜色与边框色
        self.setStyleSheet(f"""
            QFrame#ThemeCard {{
                background-color: {color};
                border: {border_w}px solid {border};
                border-radius: 6px;
            }}
            QFrame#ThemeCard:hover {{
                background-color: {hover};
                border: {border_w}px solid rgba(255,255,255,0.55);
            }}
        """)
        try:
            self.lbl_theme.set_color("#ffffff")
        except Exception:
            self.lbl_theme.set_color("#ffffff")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press_pos = e.pos()
            self._dragging = False
            self.setCursor(Qt.ClosedHandCursor)
        elif e.button() == Qt.RightButton:
            self._page._delete(self._record)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if not (e.buttons() & Qt.LeftButton) or self._press_pos is None:
            return
        if (e.pos() - self._press_pos).manhattanLength() < DRAG_THRESHOLD:
            return
        self._dragging = True
        self._start_drag()

    def mouseReleaseEvent(self, e):
        self.setCursor(Qt.OpenHandCursor)
        if e.button() == Qt.LeftButton and not self._dragging:
            # 单击：复制 + 选中 + 进入录入区编辑
            self._page._click_card(self._record)
        self._press_pos = None
        self._dragging = False
        super().mouseReleaseEvent(e)

    def _start_drag(self):
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(MIME_CARD_ID, str(self.record_id()).encode("utf-8"))
        drag.setMimeData(mime)

        # 半透明拖影（尺寸与卡片一致，不放大）
        pix = QPixmap(self.size())
        pix.fill(Qt.transparent)
        self.render(pix)
        painter = QPainter(pix)
        painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        painter.fillRect(pix.rect(), QColor(0, 0, 0, 170))
        painter.end()
        drag.setPixmap(pix)
        drag.setHotSpot(self._press_pos if self._press_pos else QPoint(self.width() // 2, self.height() // 2))

        # 拖拽结束后可能仍会收到 release，保持 _dragging 避免误触发单击复制
        drag.exec_(Qt.MoveAction)
        self._dragging = True

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME_CARD_ID):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME_CARD_ID):
            e.acceptProposedAction()
            try:
                host = self._page.cards_host
                src_id = bytes(e.mimeData().data(MIME_CARD_ID)).decode("utf-8")
                if src_id == str(self.record_id()):
                    # 悬停在自身：不显示插入线（放下也是 no-op）
                    host.clear_drop_line()
                else:
                    host.show_drop_line_for_card(self, e.pos().x() > self.width() / 2)
            except Exception:
                pass
        else:
            e.ignore()

    def dropEvent(self, e):
        if not e.mimeData().hasFormat(MIME_CARD_ID):
            e.ignore()
            return
        src_id = bytes(e.mimeData().data(MIME_CARD_ID)).decode("utf-8")
        tgt_id = str(self.record_id())
        if src_id and src_id != tgt_id:
            # 放到目标卡片的左/右半：决定插在前还是后
            insert_after = e.pos().x() > self.width() / 2
            self._page._reorder_cards(src_id, tgt_id, insert_after=insert_after)
        try:
            self._page.cards_host.clear_drop_line()
        except Exception:
            pass
        e.acceptProposedAction()


# ── 卡片容器（空白处也可接住拖放）────────────────────────────────────────────
class CardsDropHost(QWidget):
    def __init__(self, page, parent=None):
        super().__init__(parent)
        self._page = page
        self.setAcceptDrops(True)
        self.setObjectName("RecordsContainer")
        _transparent_bg(self)
        # 不把卡片总高度/总宽度当成窗口最小尺寸（交给外层 QScrollArea 滚动）
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        # 拖拽重排时显示的白色预插入线（QRect；None = 不显示）
        self._drop_line = None

    def minimumSizeHint(self):
        # 阻断 FlowLayout.heightForWidth 把「竖着叠满」的高度传给主窗口
        cw, ch = _card_metrics()
        return QSize(cw, ch)

    def sizeHint(self):
        lay = self.layout()
        if lay is not None and lay.hasHeightForWidth():
            w = max(self.width(), self.minimumSizeHint().width())
            return QSize(w, lay.heightForWidth(w))
        return self.minimumSizeHint()

    def _card_at(self, pos):
        w = self.childAt(pos)
        while w is not None and w is not self:
            if isinstance(w, ThemeCard):
                return w
            w = w.parentWidget()
        return None

    # ── 拖拽白色预插入线 ─────────────────────────────────────
    def _set_drop_line(self, rect):
        self._drop_line = rect
        self.update()

    def clear_drop_line(self):
        if self._drop_line is not None:
            self._drop_line = None
            self.update()

    def _flow_spacing(self) -> int:
        """卡片横向间距（跟随仓库 FlowLayout 的 h_spacing）。"""
        try:
            return int(getattr(self.layout(), "_h", None) or 4)
        except Exception:
            return 4

    def show_drop_line_for_card(self, card, after: bool):
        """在某张卡片前/后显示白色竖线，线放在与相邻卡缝隙的正中间。"""
        g = card.geometry()
        if not g.isValid() or g.isEmpty():
            self.clear_drop_line()
            return
        sp = self._flow_spacing()
        # after → 右边缘进半个间距；before → 左边缘进半个间距（落点相同，线居中在缝隙里）
        x = g.right() + sp // 2 if after else g.left() - sp // 2 - 1
        self._set_drop_line(QRect(x, g.top(), 2, g.height()))

    def _end_card_geom(self):
        """流程末尾卡片（最后一行右下角）的 geometry；无卡片返回 None。"""
        best = None
        for c in self.findChildren(ThemeCard):
            g = c.geometry()
            if not g.isValid() or g.isEmpty():
                continue
            if (
                best is None
                or g.bottom() > best.bottom()
                or (g.bottom() == best.bottom() and g.right() > best.right())
            ):
                best = g
        return best

    def show_drop_line(self, pos, src_id=None):
        """按鼠标位置计算插入线：在卡片左/右半 → 插其前/后；空白 → 插到末尾。
        没有有效插入位置（悬停源卡片自身 / 源卡片已在末尾）时隐藏。"""
        card = self._card_at(pos)
        if card is not None:
            if src_id is not None and src_id == str(card.record_id()):
                self.clear_drop_line()
                return
            self.show_drop_line_for_card(card, pos.x() > card.geometry().center().x())
            return
        end = self._end_card_geom()
        if end is None:
            self.clear_drop_line()
            return
        if src_id is not None:
            end_card = None
            for c in self.findChildren(ThemeCard):
                if c.geometry() == end:
                    end_card = c
                    break
            if end_card is not None and src_id == str(end_card.record_id()):
                # 源卡片本身已在末尾：插到末尾 = 无变化，不显示插入线
                self.clear_drop_line()
                return
        sp = self._flow_spacing()
        self._set_drop_line(QRect(end.right() + sp // 2, end.top(), 2, end.height()))

    def paintEvent(self, _e):
        super().paintEvent(_e)
        r = self._drop_line
        if r is None or r.width() <= 0 or r.height() <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.fillRect(r, QColor(255, 255, 255))
        p.end()

    def mousePressEvent(self, e):
        # 仓库空白处左键 = 取消选中
        if e.button() == Qt.LeftButton and self._card_at(e.pos()) is None:
            self._page._cancel_edit()
        super().mousePressEvent(e)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME_CARD_ID):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME_CARD_ID):
            e.acceptProposedAction()
            src_id = bytes(e.mimeData().data(MIME_CARD_ID)).decode("utf-8")
            self.show_drop_line(e.pos(), src_id=src_id)
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self.clear_drop_line()
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        if not e.mimeData().hasFormat(MIME_CARD_ID):
            e.ignore()
            return
        src_id = bytes(e.mimeData().data(MIME_CARD_ID)).decode("utf-8")
        card = self._card_at(e.pos())
        if card is not None:
            insert_after = e.pos().x() > card.geometry().center().x()
            self._page._reorder_cards(src_id, str(card.record_id()), insert_after=insert_after)
        else:
            self._page._reorder_to_end(src_id)
        self.clear_drop_line()
        e.acceptProposedAction()


# ── 主页面 ───────────────────────────────────────────────────────────────────
class PagePaste(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        # 页面本身不向外强加最小尺寸，由外层窗口 setMinimumSize 说了算
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._selected_id = None   # 仓库卡片选中（高亮），不等于录入编辑
        self._editing_id = None    # 仅双击进入「主题/内容」编辑时才有值
        self._all_records = []
        self._card_widgets = {}  # id -> ThemeCard
        self._filter_colors = set()  # 空集合=不限颜色；可多色同时筛选

        self._build_ui()
        self._install_blank_click_filter()
        self._reload()

    def minimumSizeHint(self):
        # 防止子控件（FlowLayout / 标签）把最小高度顶破窗口
        return QSize(0, 0)

    def sizeHint(self):
        return QSize(1080, 720)

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self):
        page = QVBoxLayout(self)
        # 与系统总览一致：ContentRoot 已有左右内边距，页面不再叠第二层
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(12)
        # 录入约 25% · 仓库约 75%
        page.addWidget(self._build_entry_panel(), 25)
        page.addWidget(self._build_warehouse_panel(), 75)

    def _build_entry_panel(self) -> QWidget:
        """
        录入区布局：
          整体高度约占页面 35%
          左 75%：主题 + 内容
          中 12px：空隙
          右 25%：提示（最顶）/ 颜色 / 保存·删除（顶对齐；相对原 35% 收窄约 10% 总宽）
        """
        box = make_card("CardPasteEntry", borderless=True)  # 录入区不要外框
        box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        form = QVBoxLayout(box)
        form.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        # 标题空隙由 CARD_TITLE_BODY_GAP 统一；此处 spacing 只作用正文之间
        form.setSpacing(8)
        install_card_title(box, form, "录入")

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)  # 中间 12px 分隔
        # 不要 AlignTop：否则左侧 Expanding 也不吃满高度，内容框会跟着 sizeHint/右侧提示变高

        # ── 左 75%：主题 + 内容（标签并入 placeholder，不占侧栏宽度）────────
        left = _transparent_bg(QWidget())
        left.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(8)

        # 主题 / 内容：圆角浅色底（PasteThemeInput / PasteContentEdit，见 app.qss）
        # 注意：QTextEdit 默认 documentMargin=4，会比 QLineEdit 多缩进，占位「主题/内容」左不对齐
        self.inp_theme = QLineEdit()
        self.inp_theme.setObjectName("PasteThemeInput")
        self.inp_theme.setPlaceholderText("主题")
        self.inp_theme.setFixedHeight(32)
        self.inp_theme.setTextMargins(0, 0, 0, 0)
        left_l.addWidget(self.inp_theme)

        self.inp_content = QTextEdit()
        self.inp_content.setObjectName("PasteContentEdit")
        self.inp_content.setPlaceholderText("内容")
        self.inp_content.setMinimumHeight(60)
        self.inp_content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # 不按文档内容调整自身尺寸，避免 setPlainText 后高度与空态不一致
        try:
            from PyQt5.QtWidgets import QAbstractScrollArea
            self.inp_content.setSizeAdjustPolicy(QAbstractScrollArea.AdjustIgnored)
        except Exception:
            pass
        # 去掉文档默认内边距，与主题行 QLineEdit 共用同一套 QSS padding，首字左对齐
        try:
            self.inp_content.document().setDocumentMargin(0)
        except Exception:
            pass
        self.inp_content.setViewportMargins(0, 0, 0, 0)
        # 竖向滚动条样式对齐截图工具「截图与操作记录」(recordStyle dashed 标准)
        self.inp_content.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.inp_content.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_l.addWidget(self.inp_content, 1)

        # ── 右 25%：提示（固定高）+ 颜色 + 按钮（贴顶，不抢左侧高度）────────
        right = _transparent_bg(QWidget())
        right.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(10)
        right_l.setAlignment(Qt.AlignTop)

        # 说明文字：固定高度槽位，初始/点卡提示换行数不同也不改几何
        self.form_hint = _FormHintLabel(self._default_hint())
        self.form_hint.setObjectName("PasteFormHint")
        self.form_hint.setWordWrap(True)
        self.form_hint.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        right_l.addWidget(self.form_hint, 0, Qt.AlignTop)

        self.color_bar = ColorPickerBar()
        right_l.addWidget(self.color_bar, 0, Qt.AlignTop)

        self._row_btn_h = 28

        # 第 1 行：保存 / 更新（占满宽）
        row_save = QHBoxLayout()
        row_save.setContentsMargins(0, 0, 0, 0)
        row_save.setSpacing(0)
        self.btn_save = QPushButton("保存")
        apply_btn_download(self.btn_save)
        self.btn_save.setFixedHeight(self._row_btn_h)
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_save.clicked.connect(self._save)
        row_save.addWidget(self.btn_save, 1)
        right_l.addLayout(row_save)

        # 第 2 行：复制 | 删除（编辑态显示）
        # 槽位高度固定：隐藏按钮时仍占位，避免录入区高度涨缩带动整页抖动
        self.edit_actions = _transparent_bg(QWidget())
        self.edit_actions.setFixedHeight(self._row_btn_h)
        self.edit_actions.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row_edit = QHBoxLayout(self.edit_actions)
        row_edit.setContentsMargins(0, 0, 0, 0)
        row_edit.setSpacing(8)

        self.btn_copy = QPushButton("复制")
        self.btn_copy.setObjectName("RecordCopyBtn")
        self.btn_copy.setFixedHeight(self._row_btn_h)
        self.btn_copy.setCursor(Qt.PointingHandCursor)
        self.btn_copy.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_copy.clicked.connect(self._duplicate_selected)
        row_edit.addWidget(self.btn_copy, 1)

        self.btn_delete = QPushButton("删除")
        self.btn_delete.setObjectName("RecordDelBtn")
        self.btn_delete.setFixedHeight(self._row_btn_h)
        self.btn_delete.setCursor(Qt.PointingHandCursor)
        self.btn_delete.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_delete.clicked.connect(self._delete_selected)
        row_edit.addWidget(self.btn_delete, 1)

        right_l.addWidget(self.edit_actions)
        self._set_selection_actions_visible(False)

        # 左 75% 纵向吃满 · 右 25% 贴顶；勿给左侧 AlignTop，否则内容框高度不稳
        body.addWidget(left, 75)
        body.addWidget(right, 25, Qt.AlignTop)
        form.addLayout(body, 1)
        return box

    def _build_warehouse_panel(self) -> QWidget:
        box = make_card("CardPasteWarehouse")

        outer = QVBoxLayout(box)
        outer.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        # spacing 与标题空隙由 install_card_title 补偿；正文行距用 spacing=8
        outer.setSpacing(8)
        install_card_title(box, outer, "仓库")

        # 工具条：左 50% 七色筛选 | 右 50% 搜索 + 取消 + 计数
        tool = QHBoxLayout()
        tool.setContentsMargins(0, 0, 0, 0)
        tool.setSpacing(12)

        left_tool = _transparent_bg(QWidget())
        left_l = QHBoxLayout(left_tool)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(0)
        self.color_filter = ColorFilterBar(on_change=self._on_color_filter)
        left_l.addWidget(self.color_filter, 1)

        right_tool = _transparent_bg(QWidget())
        right_l = QHBoxLayout(right_tool)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(8)

        self.inp_search = QLineEdit()
        self.inp_search.setObjectName("CalcInput")
        self.inp_search.setPlaceholderText("搜索")
        self.inp_search.setFixedHeight(30)
        self.inp_search.textChanged.connect(self._on_search_changed)
        right_l.addWidget(self.inp_search, 1)

        # 有搜索内容时才显示；高度与搜索框一致，不超出
        self.btn_clear_search = QPushButton("取消")
        self.btn_clear_search.setObjectName("SearchClearBtn")
        self.btn_clear_search.setFixedHeight(30)
        self.btn_clear_search.setMinimumWidth(72)
        self.btn_clear_search.setCursor(Qt.PointingHandCursor)
        self.btn_clear_search.clicked.connect(self._clear_search)
        self.btn_clear_search.setVisible(False)
        right_l.addWidget(self.btn_clear_search)

        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("RecordDate")
        right_l.addWidget(self.lbl_count, 0)

        tool.addWidget(left_tool, 50)
        tool.addWidget(right_tool, 50)
        outer.addLayout(tool)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("RecordsScroll")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # 滚动区自己吃掉内容高度，不把内容最小高度上报给窗口
        scroll.setMinimumHeight(0)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        try:
            scroll.setSizeAdjustPolicy(QAbstractScrollArea.AdjustIgnored)
        except Exception:
            pass
        # 视口默认用调色板 Base 色 / 全局 QWidget 画布色，会与 RecordsBox 功能区色不一致
        scroll.viewport().setAutoFillBackground(False)
        scroll.setStyleSheet(
            "QScrollArea#RecordsScroll{background:transparent;border:none;}"
            "QScrollArea#RecordsScroll > QWidget > QWidget{background:transparent;}"
        )

        self.cards_host = CardsDropHost(self)
        self.cards_layout = FlowLayout(self.cards_host, margin=4, h_spacing=4, v_spacing=4)
        scroll.setWidget(self.cards_host)
        self._warehouse_scroll = scroll
        outer.addWidget(scroll, 1)

        # 仓库卡片区可压缩，录入区保持内容高度
        box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        return box

    # ── 刷新 ─────────────────────────────────────────────────────────────────
    def _reload(self):
        self._all_records = _load_records()
        self._refresh_cards()

    def _on_color_filter(self, colors):
        """colors: 选中的颜色 hex 列表，空列表表示不筛选。"""
        self._filter_colors = {(c or "").lower() for c in (colors or []) if c}
        self._refresh_cards()

    def _on_search_changed(self, text):
        has = bool((text or "").strip())
        self.btn_clear_search.setVisible(has)
        self._refresh_cards()

    def _clear_search(self):
        self.inp_search.clear()
        # textChanged 会触发隐藏取消按钮并刷新

    def _refresh_cards(self):
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._card_widgets = {}

        kw = (self.inp_search.text() or "").strip().lower()
        recs = _sorted_by_order(self._all_records)

        # 颜色多选筛选：卡片颜色属于任一勾选色即显示
        if self._filter_colors:
            recs = [
                r for r in recs
                if (r.get("color") or "").lower() in self._filter_colors
            ]

        if kw:
            recs = [
                r for r in recs
                if kw in (r.get("theme") or r.get("title") or "").lower()
                or kw in (r.get("content") or "").lower()
            ]

        self.lbl_count.setText(f"共 {len(recs)} 条")

        if not recs:
            if not self._all_records:
                empty_msg = "仓库为空"
            else:
                empty_msg = "没有匹配"
            empty = QLabel(empty_msg)
            empty.setObjectName("RecordEmpty")
            empty.setAlignment(Qt.AlignCenter)
            empty.setMinimumHeight(120)
            self.cards_layout.addWidget(empty)
            return

        for record in recs:
            card = ThemeCard(record, page=self, parent=self.cards_host)
            if self._selected_id is not None and str(record.get("id")) == str(self._selected_id):
                card.set_selected(True)
            self.cards_layout.addWidget(card)
            self._card_widgets[str(record.get("id"))] = card
            # 新建卡片也挂上空白点击过滤器（卡片本身会被排除，不影响选中）
            card.installEventFilter(self)
            for child in card.findChildren(QWidget):
                child.installEventFilter(self)

    def _mark_selected(self):
        sid = str(self._selected_id) if self._selected_id is not None else None
        for rid, card in self._card_widgets.items():
            card.set_selected(sid is not None and rid == sid)

    def _active_card_id(self):
        """当前业务目标卡：优先编辑中的，否则仓库选中的。"""
        if self._editing_id is not None:
            return self._editing_id
        return self._selected_id

    # ── 空白点击 = 取消选中 ───────────────────────────────────────────────────
    def _install_blank_click_filter(self):
        """子控件点击冒泡检测：点到非交互空白处时取消选中。"""
        self.installEventFilter(self)
        for w in self.findChildren(QWidget):
            w.installEventFilter(self)

    def eventFilter(self, obj, event):
        if (
            event.type() == QEvent.MouseButtonPress
            and event.button() == Qt.LeftButton
            and (self._selected_id or self._editing_id)
            and self._is_blank_deselect_target(obj)
        ):
            self._cancel_edit()
            return False
        return super().eventFilter(obj, event)

    def _is_blank_deselect_target(self, obj) -> bool:
        """判断这次点击是否落在“空白区”（非输入、非按钮、非卡片本体）。"""
        w = obj
        while w is not None:
            if isinstance(w, ThemeCard):
                return False
            if w in (
                self.btn_save, self.btn_copy, self.btn_delete,
                self.btn_clear_search,
                self.inp_theme, self.inp_content, self.inp_search,
            ):
                return False
            if isinstance(w, (QLineEdit, QTextEdit)):
                return False
            # 颜色菱形/圆点、以及其它功能按钮
            if isinstance(w, (QPushButton, QAbstractButton)):
                return False
            if w is self:
                break
            w = w.parentWidget()
        return True

    # ── 业务 ─────────────────────────────────────────────────────────────────
    def _sync_hint_height(self):
        """说明区高度已固定，仅兜底纠正被其它逻辑改掉的高度。"""
        if not hasattr(self, "form_hint"):
            return
        h = getattr(self.form_hint, "FIXED_H", 40)
        if self.form_hint.height() != h:
            self.form_hint.setFixedHeight(h)

    def _show_hint(self, text: str):
        if self.form_hint.text() == text:
            return  # 文案未变，跳过，避免无谓几何更新
        self.form_hint.setText(text)
        self._sync_hint_height()

    def _default_hint(self):
        return "填好后保存。点卡片复制。"

    def _edit_hint(self, copied: bool = False) -> str:
        # 两态字数接近，避免窄栏换行数不同带动布局抖
        if copied:
            return "已复制。改完点更新。"
        return "改完点更新。复制会另存一张。"

    def _set_selection_actions_visible(self, visible: bool):
        # 只显隐按钮，槽位高度始终固定，录入区不因显隐涨缩
        self.btn_copy.setVisible(visible)
        self.btn_delete.setVisible(visible)
        if hasattr(self, "edit_actions"):
            h = getattr(self, "_row_btn_h", 28)
            self.edit_actions.setFixedHeight(h)
            self.edit_actions.setVisible(True)

    def _clear_form(self):
        self._selected_id = None
        self._editing_id = None
        self.inp_theme.clear()
        self.inp_content.clear()
        self.color_bar.set_color(DEFAULT_COLOR)
        self.btn_save.setText("保存")
        self._set_selection_actions_visible(False)
        self._show_hint(self._default_hint())
        self._mark_selected()

    def _bump_use_count(self, record_id):
        records = _load_records()
        for r in records:
            if r.get("id") == record_id:
                r["use_count"] = int(r.get("use_count") or 0) + 1
                break
        _save_records(records)
        self._all_records = records

    def _click_card(self, record):
        """单击卡片：复制内容 + 选中 + 进入主题/内容编辑。"""
        content = record.get("content") or ""
        QApplication.clipboard().setText(content)
        self._bump_use_count(record.get("id"))

        rid = record.get("id")
        # 已在编辑同一张：只复制+提示，不重刷表单（避免抖动）
        if self._editing_id is not None and str(self._editing_id) == str(rid):
            self._selected_id = rid
            self._set_selection_actions_visible(True)
            self._mark_selected()
            self._show_hint(self._edit_hint(copied=True))
            return

        self._enter_edit(record, copied=True)

    def _enter_edit(self, record, copied: bool = False):
        same = (
            self._editing_id is not None
            and str(self._editing_id) == str(record.get("id"))
        )
        self._selected_id = record.get("id")
        self._editing_id = record.get("id")
        theme = record.get("theme") or record.get("title") or ""
        # 同卡重复进入时不要反复 setText/setPlainText（会重置光标并触发布局）
        if not same:
            self.inp_theme.setText(theme)
            self.inp_content.setPlainText(record.get("content") or "")
            self.color_bar.set_color(record.get("color") or DEFAULT_COLOR)
        self.btn_save.setText("更新")
        self._set_selection_actions_visible(True)
        self._show_hint(self._edit_hint(copied=copied))
        self._mark_selected()
        # 点卡：只选中并填入数据，不进入主题/内容文本编辑（不抢光标、不闪烁插入符）
        # 用户若要改字，再自己点对应输入框即可
        self.inp_theme.clearFocus()
        self.inp_content.clearFocus()
        self.setFocus(Qt.OtherFocusReason)

    def _save(self):
        theme = self.inp_theme.text().strip()
        content = self.inp_content.toPlainText().strip()
        color = self.color_bar.color()

        if not content:
            self._show_hint("内容不能为空")
            return
        if not theme:
            theme = content[:12] + ("…" if len(content) > 12 else "")

        records = _sorted_by_order(_load_records())
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        is_update = bool(self._editing_id)

        if is_update:
            found = False
            for r in records:
                if r.get("id") == self._editing_id:
                    r["theme"] = theme
                    r["title"] = theme
                    r["content"] = content
                    r["color"] = color
                    r["date"] = now
                    found = True
                    break
            if not found:
                is_update = False
                self._editing_id = None

        if not is_update:
            max_order = max((int(r.get("order") or 0) for r in records), default=-1)
            records.append({
                "id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
                "theme": theme,
                "title": theme,
                "content": content,
                "color": color,
                "date": now,
                "use_count": 0,
                "order": max_order + 1,
            })

        _save_records(records)
        hint = "已更新" if is_update else "已保存"
        self._selected_id = None
        self._editing_id = None
        self.inp_theme.clear()
        self.inp_content.clear()
        self.color_bar.set_color(DEFAULT_COLOR)
        self.btn_save.setText("保存")
        self._set_selection_actions_visible(False)
        self._show_hint(hint)
        QTimer.singleShot(1400, lambda: self._show_hint(self._default_hint()))
        self._all_records = records
        self._refresh_cards()

    def _cancel_edit(self):
        """空白处：取消选中 + 退出编辑。"""
        self._clear_form()

    @staticmethod
    def _next_copy_theme(base_theme: str, existing_themes: set) -> str:
        """生成「名字 + 序号」：豆绘1 → 豆绘2；Grok → Grok2（遇重名递增）。"""
        name = (base_theme or "未命名").strip() or "未命名"
        m = re.match(r"^(.*?)(\d+)$", name)
        if m:
            stem, start = m.group(1), int(m.group(2))
        else:
            stem, start = name, 1
        n = start + 1
        # 防止 stem 为空时只剩纯数字
        while True:
            candidate = f"{stem}{n}" if stem else str(n)
            if candidate not in existing_themes:
                return candidate
            n += 1

    def _duplicate_selected(self):
        """「复制」：在仓库中新增一张 名字+序号 的同内容卡片（基于选中/编辑中的卡）。"""
        rid = self._active_card_id()
        if not rid:
            self._show_hint("请先选卡片")
            return

        # 编辑态以表单为准；仅选中时以原卡为准
        theme = self.inp_theme.text().strip() if self._editing_id else ""
        content = self.inp_content.toPlainText().strip() if self._editing_id else ""
        color = self.color_bar.color() if self._editing_id else ""

        src = next(
            (r for r in self._all_records if r.get("id") == rid),
            None,
        )
        if src is None:
            records_disk = _load_records()
            src = next((r for r in records_disk if r.get("id") == rid), None)
        if src is None and not content:
            self._show_hint("找不到卡片")
            return

        if not content:
            content = (src or {}).get("content") or ""
        if not content:
            self._show_hint("内容为空")
            return
        if not theme:
            theme = (src or {}).get("theme") or (src or {}).get("title") or "未命名"
        if not color:
            color = (src or {}).get("color") or DEFAULT_COLOR

        records = _sorted_by_order(_load_records())
        existing = {
            (r.get("theme") or r.get("title") or "").strip()
            for r in records
        }
        new_theme = self._next_copy_theme(theme, existing)

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        # 插在当前卡后面
        src_order = int((src or {}).get("order") or 0)
        new_rec = {
            "id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
            "theme": new_theme,
            "title": new_theme,
            "content": content,
            "color": color,
            "date": now,
            "use_count": 0,
            "order": src_order + 1,
        }
        # 其后卡片 order +1
        for r in records:
            if int(r.get("order") or 0) > src_order:
                r["order"] = int(r.get("order") or 0) + 1
        records.append(new_rec)
        records = _reindex(_sorted_by_order(records))
        _save_records(records)
        self._all_records = records
        self._refresh_cards()
        # 进入新卡编辑态，方便继续改
        self._enter_edit(new_rec, copied=False)
        self._show_hint(f"已复制为 {new_theme}")
        QTimer.singleShot(1600, lambda: self._show_hint(self._edit_hint(copied=False)))

    def _delete_selected(self):
        """「删除」：删掉当前选中（或编辑中）的卡片。"""
        rid = self._active_card_id()
        if not rid:
            self._show_hint("请先选卡片")
            return
        record = next(
            (r for r in self._all_records if r.get("id") == rid),
            None,
        )
        if record is None:
            # 内存与磁盘不一致时兜底
            records = _load_records()
            record = next((r for r in records if r.get("id") == rid), None)
        if record is None:
            self._clear_form()
            self._show_hint("找不到卡片")
            return
        self._delete(record)

    def _delete(self, record):
        theme = record.get("theme") or record.get("title") or "此卡片"
        ret = QMessageBox.question(
            self, "删除",
            f"删除「{theme}」？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        records = [r for r in _load_records() if r.get("id") != record.get("id")]
        records = _reindex(_sorted_by_order(records))
        _save_records(records)
        rid = record.get("id")
        if self._editing_id == rid or self._selected_id == rid:
            self._clear_form()
        self._all_records = records
        self._refresh_cards()

    def _reorder_cards(self, src_id: str, tgt_id: str, insert_after: bool = False):
        records = _sorted_by_order(_load_records())
        src = next((r for r in records if str(r.get("id")) == str(src_id)), None)
        if src is None:
            return
        records = [r for r in records if str(r.get("id")) != str(src_id)]
        tgt_index = next((i for i, r in enumerate(records) if str(r.get("id")) == str(tgt_id)), None)
        if tgt_index is None:
            records.append(src)
        else:
            insert_at = tgt_index + 1 if insert_after else tgt_index
            records.insert(insert_at, src)
        records = _reindex(records)
        _save_records(records)
        self._all_records = records
        self._refresh_cards()

    def _reorder_to_end(self, src_id: str):
        records = _sorted_by_order(_load_records())
        src = next((r for r in records if str(r.get("id")) == str(src_id)), None)
        if src is None:
            return
        records = [r for r in records if str(r.get("id")) != str(src_id)]
        records.append(src)
        records = _reindex(records)
        _save_records(records)
        self._all_records = records
        self._refresh_cards()
