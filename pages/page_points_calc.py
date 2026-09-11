"""
page_points_calc.py  —  积分计算页
放在 pages/ 目录下，与其他页面同级。
记录文件存到：项目根目录 / records / points_calc.txt
"""

import os
import json
from datetime import datetime

from styles.style_all import (
    install_card_title, theme, apply_btn_download, make_card,
    apply_mini_button, tk,
    CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP,
)

from PyQt5.QtCore import (
    Qt, QUrl, QTimer, QMimeData, pyqtSignal, QEvent,
    QRectF, QPointF, QPropertyAnimation, QEasingCurve, pyqtProperty,
)
from PyQt5.QtGui import (
    QColor, QPalette, QDrag, QPainter, QMouseEvent, QFont, QFontMetrics,
    QPen, QBrush, QLinearGradient, QRadialGradient,
)
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QScrollArea, QFrame, QMessageBox,
    QSizePolicy, QComboBox, QStackedWidget,
    QApplication, QAbstractButton, QScrollBar, QAbstractSpinBox,
)


def _record_zebra_bg() -> str:
    """历史记录斑马纹底色：暗/亮两套都要能看清深浅差。"""
    if theme.is_dark:
        # 在 #0f1430 功能区上略提亮（比 hover_veil 更明显一点）
        return "rgba(255, 255, 255, 0.07)"
    # 白底上用实色浅灰，半透明几乎看不出来
    return "#e8ecf2"


def _style_toggle_btn(btn: QPushButton):
    """切换按钮反色选中态。

    不用全局 QSS：`* { color:… }` 会把选中态文字仍刷成浅色，叠在浅底上像“字消失了”。
    这里用控件级 stylesheet + palette 双保险，暗/亮各自反色。
    禁用（编辑记录时冻结用途类型）走降饱和色，避免看起来仍可点。
    """
    checked = btn.isChecked()
    enabled = btn.isEnabled()
    if theme.is_dark:
        if checked:
            bg, fg, bd = "#e8eefc", "#0b1124", "#c5d0ea"
            dis_bg, dis_fg, dis_bd = "rgba(232,238,252,0.22)", "#7a8aab", "rgba(197,208,234,0.22)"
        else:
            bg, fg, bd = "rgba(255,255,255,0.06)", "#9fb0d7", "rgba(255,255,255,0.22)"
            dis_bg, dis_fg, dis_bd = "rgba(255,255,255,0.03)", "#5a6a8a", "rgba(255,255,255,0.10)"
    else:
        if checked:
            bg, fg, bd = "#1f2937", "#f9fafb", "#111827"
            dis_bg, dis_fg, dis_bd = "rgba(31,41,55,0.28)", "#9ca3af", "rgba(17,24,39,0.18)"
        else:
            bg, fg, bd = "rgba(0,0,0,0.04)", "#4b5563", "rgba(0,0,0,0.14)"
            dis_bg, dis_fg, dis_bd = "rgba(0,0,0,0.02)", "#9ca3af", "rgba(0,0,0,0.08)"

    if not enabled:
        bg, fg, bd = dis_bg, dis_fg, dis_bd
    # 字重固定，避免选中/禁用切换时 sizeHint 带动整行左右抖
    weight = "700"
    # padding 要配合 setFixedHeight(28)，过大竖向 padding 会把字裁没
    btn.setStyleSheet(
        f"QPushButton#ToggleBtn {{"
        f"  background: {bg};"
        f"  color: {fg};"
        f"  border: 1px solid {bd};"
        f"  border-radius: 6px;"
        f"  padding: 2px 8px;"
        f"  font-size: 13px;"
        f"  font-weight: {weight};"
        f"}}"
        f"QPushButton#ToggleBtn:hover:!disabled {{"
        f"  background: {bg};"
        f"  color: {fg};"
        f"  border: 1px solid {bd};"
        f"}}"
        f"QPushButton#ToggleBtn:checked {{"
        f"  background: {bg};"
        f"  color: {fg};"
        f"  border: 1px solid {bd};"
        f"}}"
        f"QPushButton#ToggleBtn:disabled {{"
        f"  background: {dis_bg};"
        f"  color: {dis_fg};"
        f"  border: 1px solid {dis_bd};"
        f"}}"
    )
    btn.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
    # Windows 原生样式有时仍读 palette 画字色，这里同步写上
    pal = btn.palette()
    c_fg = QColor(fg)
    c_bg = QColor(bg) if not str(bg).startswith("rgba") else QColor(0, 0, 0, 0)
    pal.setColor(QPalette.ButtonText, c_fg)
    pal.setColor(QPalette.WindowText, c_fg)
    pal.setColor(QPalette.Text, c_fg)
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(dis_fg))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor(dis_fg))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(dis_fg))
    if c_bg.isValid() and c_bg.alpha() > 0:
        pal.setColor(QPalette.Button, c_bg)
    btn.setPalette(pal)


# ── 生图 / 生视频：词夹滑块，二选一（左蓝右绿）────────────────────────────────
_USAGE_BLUE = QColor("#3B82F6")
_USAGE_GREEN = QColor("#22C55E")


def _lerp_qcolor(a: QColor, b: QColor, t: float) -> QColor:
    t = 0.0 if t < 0 else (1.0 if t > 1 else float(t))
    return QColor(
        int(a.red()   + (b.red()   - a.red())   * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue()  + (b.blue()  - a.blue())  * t),
        int(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


class _UsageSlider(QWidget):
    """圆角轨道 + 白滑块。0=左（生图/蓝），1=右（生视频/绿）。"""

    W, H = 40, 18
    snapped = pyqtSignal(bool)  # True=生视频

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._knob = 0.0
        self._frozen = False
        self._press = None       # (x, knob) ；None=未按下
        self._dragging = False
        self._anim = QPropertyAnimation(self, b"knobPos", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def getKnobPos(self):
        return self._knob

    def setKnobPos(self, v):
        self._knob = max(0.0, min(1.0, float(v)))
        self.update()

    knobPos = pyqtProperty(float, fget=getKnobPos, fset=setKnobPos)

    def setFrozen(self, frozen: bool):
        frozen = bool(frozen)
        if self._frozen == frozen:
            return
        self._frozen = frozen
        self.setCursor(Qt.ForbiddenCursor if frozen else Qt.PointingHandCursor)
        self.update()

    def set_pos(self, video: bool, animate: bool):
        end = 1.0 if video else 0.0
        if abs(self._knob - end) < 0.001:
            return
        if animate and self.isVisible():
            self._anim.stop()
            self._anim.setStartValue(self._knob)
            self._anim.setEndValue(end)
            self._anim.start()
        else:
            self._anim.stop()
            self._knob = end
            self.update()

    def mousePressEvent(self, e):
        if self._frozen or e.button() != Qt.LeftButton:
            e.accept()
            return
        self._anim.stop()
        self._press = (e.x(), self._knob)
        self._dragging = False
        self.grabMouse()
        e.accept()

    def mouseMoveEvent(self, e):
        if self._press is None:
            return
        if not self._dragging and abs(e.x() - self._press[0]) < 4:
            return
        self._dragging = True
        pad = 2.5
        kn = max(8.0, self.height() - 2.0 * pad)
        x0 = pad
        x1 = self.width() - pad - kn
        span = max(1.0, x1 - x0)
        t = (float(e.x()) - kn / 2.0 - x0) / span
        self._knob = max(0.0, min(1.0, t))
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        try:
            self.releaseMouse()
        except Exception:
            pass
        if self._frozen or self._press is None:
            self._press = None
            self._dragging = False
            e.accept()
            return
        if self._dragging:
            video = self._knob >= 0.5
        else:
            video = self._knob < 0.5  # 单击翻转
        self._press = None
        self._dragging = False
        self.set_pos(video, animate=True)
        self.snapped.emit(bool(video))
        e.accept()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        t = self._knob
        frozen = bool(self._frozen)
        if frozen:
            p.setOpacity(0.55)

        track = QRectF(1.0, 1.5, w - 2.0, h - 3.0)
        radius = track.height() / 2.0
        if frozen:
            c0 = QColor("#64748B")
            c1 = QColor("#475569")
        else:
            c0 = _lerp_qcolor(_USAGE_BLUE, _USAGE_GREEN, t)
            c1 = QColor(c0).darker(112)
        g = QLinearGradient(track.topLeft(), track.topRight())
        g.setColorAt(0, c0)
        g.setColorAt(1, c1)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        p.drawRoundedRect(track, radius, radius)
        # 内高光
        hi = QLinearGradient(track.topLeft(), QPointF(track.left(), track.center().y()))
        hi.setColorAt(0, QColor(255, 255, 255, 48 if frozen else 70))
        hi.setColorAt(1, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(hi))
        p.drawRoundedRect(
            track.adjusted(0.5, 0.5, -0.5, -track.height() * 0.35), radius, radius,
        )

        pad = 2.5
        kn = h - 2.0 * pad
        x0 = pad
        x1 = w - pad - kn
        kx = x0 + (x1 - x0) * t
        knob = QRectF(kx, pad, kn, kn)

        sh = QRadialGradient(knob.center() + QPointF(0, 1), kn * 0.7)
        sh.setColorAt(0, QColor(0, 0, 0, 50))
        sh.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(sh))
        p.drawEllipse(knob.adjusted(-1, 0, 1, 2))

        kg = QLinearGradient(knob.topLeft(), knob.bottomLeft())
        if frozen:
            kg.setColorAt(0, QColor("#E2E8F0"))
            kg.setColorAt(1, QColor("#CBD5E1"))
        else:
            kg.setColorAt(0, QColor("#FFFFFF"))
            kg.setColorAt(1, QColor("#F1F5F9"))
        p.setBrush(QBrush(kg))
        p.setPen(QPen(QColor(0, 0, 0, 28), 1.0))
        p.drawEllipse(knob)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(255, 255, 255, 100 if frozen else 160)))
        p.drawEllipse(QRectF(kx + kn * 0.22, pad + kn * 0.15, kn * 0.45, kn * 0.28))
        p.end()


class UsageTypeSwitch(QFrame):
    """用途类型整组：生图 ←滑块→ 生视频。点词或拖滑块均可，左蓝右绿。"""

    changed = pyqtSignal(str)  # "image" | "video"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("UsageTypeSwitch")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFrameShape(QFrame.NoFrame)
        self.setFixedHeight(28)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._video = False
        self._frozen = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.lbl_image = QLabel("生图")
        self.lbl_video = QLabel("生视频")
        for lbl in (self.lbl_image, self.lbl_video):
            lbl.setObjectName("UsageTypeWord")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setCursor(Qt.PointingHandCursor)
            lbl.installEventFilter(self)

        self.slider = _UsageSlider(self)
        self.slider.snapped.connect(self._on_slider)

        lay.addWidget(self.lbl_image)
        lay.addWidget(self.slider, 0, Qt.AlignVCenter)
        lay.addWidget(self.lbl_video)

        self.refresh_theme()
        self._lock_label_widths()

    def eventFilter(self, obj, event):
        if obj in (self.lbl_image, self.lbl_video) and event.type() == QEvent.MouseButtonPress:
            if isinstance(event, QMouseEvent) and event.button() == Qt.LeftButton:
                self._apply(obj is self.lbl_video, animate=True, move_slider=True)
                return True
        return super().eventFilter(obj, event)

    def _on_slider(self, video: bool):
        # 滑块自己在播动画，这里只同步文字与对外信号
        self._apply(bool(video), animate=False, move_slider=False)

    def _apply(self, video: bool, animate: bool, move_slider: bool):
        video = bool(video)
        if self._frozen:
            if move_slider:
                self.slider.set_pos(self._video, animate=True)
            return
        changed = bool(self._video) != video
        self._video = video
        if move_slider:
            self.slider.set_pos(video, animate=animate)
        if changed:
            self._refresh_labels()
            self._refresh_chrome()
            self.changed.emit("video" if video else "image")

    def usage_type(self) -> str:
        return "video" if self._video else "image"

    def set_usage_type(self, t: str):
        """程序回填：无动画、不发信号。"""
        video = t == "video"
        if self._video == video:
            self.slider.set_pos(video, animate=False)
            return
        self._video = video
        self.slider.set_pos(video, animate=False)
        self._refresh_labels()
        self._refresh_chrome()

    def setFrozen(self, frozen: bool):
        frozen = bool(frozen)
        if self._frozen == frozen:
            return
        self._frozen = frozen
        self.slider.setFrozen(frozen)
        cur = Qt.ForbiddenCursor if frozen else Qt.PointingHandCursor
        self.setCursor(cur)
        self.lbl_image.setCursor(cur)
        self.lbl_video.setCursor(cur)
        tip = "编辑记录时不可更改用途" if frozen else ""
        self.setToolTip(tip)
        self.lbl_image.setToolTip(tip)
        self.lbl_video.setToolTip(tip)
        self.slider.setToolTip(tip)
        self._refresh_labels()
        self._refresh_chrome()

    def refresh_theme(self):
        self._refresh_labels()
        self._refresh_chrome()
        self.slider.update()

    def _refresh_chrome(self):
        self.setStyleSheet(
            "QFrame#UsageTypeSwitch {"
            "  background: transparent;"
            "  border: none;"
            "}"
        )

    def _lock_label_widths(self):
        """按加粗字宽锁死，进出编辑 / 切换生图生视频都不改外框。"""
        f = QFont(self.lbl_image.font())
        f.setPixelSize(13)
        f.setWeight(QFont.Bold)
        fm = QFontMetrics(f)
        self.lbl_image.setFixedWidth(fm.horizontalAdvance("生图") + 4)
        self.lbl_video.setFixedWidth(fm.horizontalAdvance("生视频") + 4)
        self.setFixedHeight(28)
        self.setFixedWidth(
            self.lbl_image.width() + 6 + _UsageSlider.W + 6 + self.lbl_video.width()
        )

    def _refresh_labels(self):
        mut = tk("text_faint") if self._frozen else tk("text_mut")
        if self._frozen:
            img_c = vid_c = mut
        elif self._video:
            img_c, vid_c = mut, _USAGE_GREEN.name()
        else:
            img_c, vid_c = _USAGE_BLUE.name(), mut
        # 字重始终 700：选中只换颜色，避免 600/700 切换撑缩宽度
        for lbl, color in (
            (self.lbl_image, img_c),
            (self.lbl_video, vid_c),
        ):
            lbl.setStyleSheet(
                f"QLabel#UsageTypeWord {{"
                f"  color: {color};"
                f"  font-size: 13px;"
                f"  font-weight: 700;"
                f"  background: transparent;"
                f"  border: none;"
                f"  padding: 0;"
                f"}}"
            )


# ── 记录文件路径 ─────────────────────────────────────────────────────────────
def _points_record_file() -> str:
    try:
        from utils.app_paths import records_file
        return records_file("points_calc.txt")
    except Exception:
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "points_calc.txt",
        )


RECORD_FILE = _points_record_file()

# ── 工具函数 ─────────────────────────────────────────────────────────────────
def _ensure_dir():
    global RECORD_FILE
    RECORD_FILE = _points_record_file()
    os.makedirs(os.path.dirname(RECORD_FILE), exist_ok=True)

def _load_records() -> list:
    if not os.path.exists(RECORD_FILE):
        return []
    records = []
    with open(RECORD_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records

def _save_records(records: list):
    """原子写入 points_calc.txt（写 .tmp 再 replace，避免半截损坏）。"""
    _ensure_dir()
    tmp = RECORD_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, RECORD_FILE)


# ── 单行省略号标签 ───────────────────────────────────────────────────────────
class _ElideLabel(QLabel):
    """记录行里用：宽度不够时用"…"省略，而不是 setWordWrap(True) 自动换行。
    v9.9.6 修复：wordWrap(True) 配合 QSizePolicy.Ignored 会让这个标签的高度
    依赖当前宽度（hasHeightForWidth），窗口在被拖动/跨屏幕 DPI 重新布局时，
    Qt 有时会拿一个瞬时的、不准确的宽度去算这次的高度，算出来的高度又没被
    正确地重新收敛回去，日积月累就把主窗口的最小高度越撑越高（拖一次窗口，
    内容往下掉一截）——这跟之前"速存图文"页面遇到的是同一类问题。
    改成单行 + 手动省略号后，标签高度只取决于字体，跟宽度完全无关，
    从根上不会再有这种高度传染问题；原文完整内容放到 tooltip 里，鼠标悬停可看全。"""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full_text = text
        self.setWordWrap(False)
        # 允许被布局压窄：否则 sizeHint 按全文宽度算，会撑破 7:3 分栏
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        super().setText(text)

    def setText(self, text):
        self._full_text = text or ""
        self._refresh_elided()

    def sizeHint(self):
        from PyQt5.QtCore import QSize
        h = super().sizeHint().height()
        return QSize(40, h)

    def minimumSizeHint(self):
        from PyQt5.QtCore import QSize
        h = super().minimumSizeHint().height()
        return QSize(0, h)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refresh_elided()

    def _refresh_elided(self):
        fm = self.fontMetrics()
        elided = fm.elidedText(self._full_text, Qt.ElideRight, max(0, self.width()))
        super().setText(elided)


# 行尾操作区：删除钮 28px；「复制」两字贴边同宽，进出编辑不挤前面文字
_ROW_ACTION_W = 28
_ROW_ACTION_H = 26


# ── 记录行（单行）────────────────────────────────────────────────────────────
class RecordRow(QFrame):
    """一行显示一条记录：[平台] [费用] [每月积分] [积分/次] [每张/每秒] [月成本]  [删除]
    单击整行进入编辑；拖拽仍可排序。"""
    def __init__(self, record: dict, on_edit, on_delete, on_copy, is_even=False, is_editing=False, parent=None):
        super().__init__(parent)
        self._is_even = bool(is_even)
        self._record_id = record.get("id")
        self._record = record
        self._on_edit = on_edit
        self._drag_start_pos = None
        self._did_drag = False
        self._is_editing = False
        self.setObjectName("RecordCard")          # hover/编辑态 都靠这个名字
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFrameShape(QFrame.NoFrame)
        self.setMinimumHeight(38)
        self.setCursor(Qt.PointingHandCursor)
        # 斑马纹：控件级背景（暗/亮都可见）。QSS 属性选择器在部分 Qt 版本上不可靠，
        # 这里用主题色 + refresh_theme 双保险，保证两套主题都有深浅差。
        self._apply_zebra_bg()

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 4, 8, 4)
        row.setSpacing(6)

        fee_period = "/月" if record.get("fee_type") == "monthly" else "/年"
        currency  = record.get("currency", "CNY")
        amount    = record.get("amount", 0)
        rate      = record.get("rate", 1.0)
        amt_disp  = int(amount) if float(amount) == int(float(amount)) else amount
        # USD：显示折算汇率；CNY：不额外带汇率。月/年用后缀，表头写「费用 月&年」
        if currency == "USD":
            fee_str = f"${amt_disp}（×{rate}）{fee_period}"
        else:
            fee_str = f"¥{amt_disp}{fee_period}"

        cost_per    = record.get("cost_per", 0)
        consume     = record.get("consume_pts", 0)
        is_video    = record.get("usage_type", "image") == "video"
        video_secs  = record.get("video_secs", 10)
        # 积分/次：表头已标明单位；生视频补时长
        usage_label = (
            f"{consume}({video_secs}秒)" if is_video else f"{consume}"
        )
        cost_str    = f"{cost_per:.4f}".rstrip("0").rstrip(".")
        cost_label  = f"¥{cost_str}"
        monthly_pts = record.get("monthly_pts", 0)
        monthly_pts_str = str(monthly_pts) if monthly_pts else "-"
        monthly_cost = record.get("monthly_cost_cny", 0)
        monthly_cost_str = f"¥{monthly_cost:.0f}" if monthly_cost else "-"

        def _cell(text, obj_name, stretch=1):
            lbl = _ElideLabel(text)
            lbl.setObjectName(obj_name)
            lbl.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            lbl.setMinimumWidth(0)
            lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            lbl.setCursor(Qt.PointingHandCursor)
            row.addWidget(lbl, stretch)
            return lbl

        _cell(record.get("platform", "未命名"), "RecordName",   23)
        _cell(fee_str,                           "RecordSub",    20)
        _cell(monthly_pts_str,                   "RecordSub",    15)
        _cell(usage_label,                       "RecordSub",    18)
        _cell(cost_label,                        "RecordResult", 15)
        _cell(monthly_cost_str,                  "RecordResult", 9)

        # 删除 / 复制叠在同一固定槽里（无布局），显隐不改变列宽
        act = QWidget()
        act.setFixedSize(_ROW_ACTION_W, _ROW_ACTION_H)
        act.setStyleSheet("background: transparent;")

        self.btn_del = QPushButton("✕", act)
        self.btn_del.setObjectName("RecordDelBtn")
        self.btn_del.setFixedSize(28, _ROW_ACTION_H)
        self.btn_del.move(_ROW_ACTION_W - 28, 0)
        self.btn_del.setCursor(Qt.PointingHandCursor)
        self.btn_del.clicked.connect(lambda: on_delete(record))

        self.btn_copy = QPushButton("复制", act)
        self.btn_copy.setObjectName("RecordCopyBtn")
        self.btn_copy.setFixedSize(_ROW_ACTION_W, _ROW_ACTION_H)
        self.btn_copy.move(0, 0)
        self.btn_copy.setCursor(Qt.PointingHandCursor)
        self.btn_copy.clicked.connect(lambda: on_copy())

        row.addWidget(act, 0)

        self.set_editing(is_editing)

    def _apply_zebra_bg(self):
        """斑马纹 + 固定宽度边框（选中只换颜色，文字不左右挪）。"""
        bg = _record_zebra_bg() if self._is_even else "transparent"
        if self._is_editing:
            bd = (
                "border-top:1px solid #f0a542;"
                "border-right:1px solid #f0a542;"
                "border-bottom:1px solid #f0a542;"
                "border-left:3px solid #f0a542;"
                "border-radius:6px;"
            )
        else:
            bd = (
                "border-top:1px solid transparent;"
                "border-right:1px solid transparent;"
                "border-bottom:1px solid transparent;"
                "border-left:3px solid transparent;"
                "border-radius:4px;"
            )
        self.setStyleSheet(
            f"QFrame#RecordCard {{ background-color: {bg}; {bd} }}"
            f"QFrame#RecordCard:hover {{ border-left-color: #f0a542; }}"
        )

    def refresh_theme(self, *_):
        """主题切换后重刷斑马纹（内联色不会随 app.qss 自动变）。"""
        self.style().unpolish(self)
        self.style().polish(self)
        self._apply_zebra_bg()

    def set_editing(self, editing: bool):
        """切换到编辑态：删除 ↔ 复制，同时驱动橙色外框选择器。"""
        self._is_editing = bool(editing)
        self.setProperty("editing", "true" if editing else "false")
        self.btn_del.setVisible(not editing)
        self.btn_copy.setVisible(editing)
        self.style().unpolish(self)
        self.style().polish(self)
        # polish 之后再写斑马纹，避免被冲掉
        self._apply_zebra_bg()

    def _hit_button(self, pos):
        child = self.childAt(pos)
        while child is not None and child is not self:
            if isinstance(child, QPushButton):
                return True
            child = child.parentWidget()
        return False

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._did_drag = False
            if self._hit_button(event.pos()):
                self._drag_start_pos = None
            else:
                self._drag_start_pos = event.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start_pos is None:
            super().mouseMoveEvent(event)
            return
        if not (event.buttons() & Qt.LeftButton):
            super().mouseMoveEvent(event)
            return
        if (event.pos() - self._drag_start_pos).manhattanLength() < 10:
            super().mouseMoveEvent(event)
            return
        self._did_drag = True
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData("application/x-pts-record", self._record_id.encode())
        drag.setMimeData(mime)
        pixmap = self.grab()
        pixmap = pixmap.scaledToWidth(min(self.width(), 420), Qt.SmoothTransformation)
        drag.setPixmap(pixmap)
        drag.setHotSpot(event.pos())
        drag.exec_(Qt.MoveAction)
        self._drag_start_pos = None

    def mouseReleaseEvent(self, event):
        if (
            event.button() == Qt.LeftButton
            and not self._did_drag
            and self._drag_start_pos is not None
            and not self._is_editing
            and callable(self._on_edit)
        ):
            self._on_edit(self._record)
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)



# ── 拖拽排序容器 ───────────────────────────────────────────────────────────────
class _DragContainer(QWidget):
    """RecordRow 容器，支持内部拖拽重排。"""
    reordered = pyqtSignal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_idx = -1
        self._indicator_y = 0
        self.setAcceptDrops(True)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drag_idx >= 0:
            painter = QPainter(self)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(74, 158, 255, 70))
            y = max(0, self._indicator_y - 1)
            painter.drawRect(0, y, self.width(), 2)
            painter.end()

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-pts-record"):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-pts-record"):
            idx, y = self._calc_position(event.pos())
            if idx != self._drag_idx or y != self._indicator_y:
                self._drag_idx = idx
                self._indicator_y = y
                self.update()
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._drag_idx = -1
        self.update()

    def dropEvent(self, event):
        if event.mimeData().hasFormat("application/x-pts-record"):
            rid = bytes(event.mimeData().data("application/x-pts-record")).decode()
            target_idx = self._drag_idx
            self._drag_idx = -1
            self.update()
            event.acceptProposedAction()
            if target_idx >= 0:
                QTimer.singleShot(0, lambda: self.reordered.emit(rid, target_idx))

    def _calc_position(self, pos):
        layout = self.layout()
        if layout is None:
            return 0, 4
        children = []
        for i in range(layout.count()):
            item = layout.itemAt(i)
            w = item.widget() if item is not None else None
            if w and w.isVisible() and hasattr(w, "_record_id"):
                children.append(w)
        if not children:
            return 0, 4
        y = pos.y()
        for i, child in enumerate(children):
            mid_y = child.geometry().center().y()
            if y < mid_y:
                return i, child.geometry().top()
        last = children[-1]
        return len(children), last.geometry().bottom()

    def _record_widgets(self):
        layout = self.layout()
        if layout is None:
            return []
        result = []
        for i in range(layout.count()):
            item = layout.itemAt(i)
            w = item.widget() if item is not None else None
            if w and w.isVisible() and hasattr(w, "_record_id"):
                result.append(w)
        return result


# ── 主页面 ───────────────────────────────────────────────────────────────────
class PagePointsCalc(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._editing_id  = None
        self._block_row_edit = False  # 取消编辑后挡住同一次鼠标松手，避免点到另一行又进编辑
        self._record_rows = {}
        self._cost_sort_img = 0       # 生图：0=默认 1=↑ 2=↓
        self._cost_sort_vid = 0       # 生视频：0=默认 1=↑ 2=↓
        self._cost_sort_btns = {}     # key: img/vid → QPushButton（3态循环：无→升→降→无）
        self._toggle_btns = []   # 月费/年费、CNY/USD 等切换按钮，主题切换时重刷反色
        self._last_result = None
        self._fee_type    = "monthly"
        self._currency    = "CNY"
        self._usage_type  = "image"    # 同时控制计算结果与记录区子页
        self._video_secs  = 10
        # 输入变更后防抖自动计算（不再需要「立即计算」按钮）
        self._calc_timer = QTimer(self)
        self._calc_timer.setSingleShot(True)
        self._calc_timer.setInterval(280)
        self._calc_timer.timeout.connect(self._calc)
        self._auto_calc_suspended = False  # 批量回填编辑数据时暂停

        self._build_ui()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self._wire_auto_calc()
        self._auto_calc_suspended = True
        try:
            self._set_usage_type("image")   # 初始化结果卡片与时长选择器的显隐
        finally:
            self._auto_calc_suspended = False
        self._refresh_records()
        self._restore_remembered_rate()
        # 主题切换：斑马纹 + 切换按钮反色
        theme.changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, *_):
        for row in getattr(self, "_record_rows", {}).values():
            if hasattr(row, "refresh_theme"):
                row.refresh_theme()
        for b in getattr(self, "_toggle_btns", []):
            _style_toggle_btn(b)
        sw = getattr(self, "usage_switch", None)
        if sw is not None:
            sw.refresh_theme()
        for b in self._cost_sort_btns.values():
            self._style_cost_sort_btn(b)

    def _style_cost_sort_btn(self, btn):
        section_key = btn.property("sectionKey")
        sort_attr = "_cost_sort_img" if section_key == "img" else "_cost_sort_vid"
        state = getattr(self, sort_attr, 0)
        texts = {0: "\u2022", 1: "\u25b2", 2: "\u25bc"}  # • ▲ ▼
        text = texts.get(state, "")
        active = state != 0
        bg = tk("accent") if active else "transparent"
        fg = "#fff" if active else tk("text_mut")
        border = tk("accent_hover") if active else tk("text_faint")
        radius = "6px"
        btn.setText(text)
        btn.setStyleSheet(
            f"QPushButton#CostSortBtn{{border:1px solid {border};border-radius:{radius};"
            f"background:{bg};color:{fg};font-size:11px;padding:0;margin:0;}}"
            f"QPushButton#CostSortBtn:hover{{border-color:{tk('accent_hover')};}}"
        )

    # ── 汇率偏好（user.txt · points_calc）────────────────────────────────
    DEFAULT_RATE = 7.25

    def _parse_rate_text(self, text=None) -> float:
        """当前汇率；非法则返回 DEFAULT_RATE。可传入数值或字串覆盖。"""
        if text is None:
            try:
                r = float(getattr(self, "_fx_rate", self.DEFAULT_RATE))
                if r > 0:
                    return r
            except (TypeError, ValueError):
                pass
            return float(self.DEFAULT_RATE)
        try:
            r = float(str(text).strip())
            if r > 0:
                return r
        except (TypeError, ValueError):
            pass
        return float(self.DEFAULT_RATE)

    def _format_rate(self, r: float) -> str:
        r = float(r)
        # 最多 4 位小数，去掉无意义尾零：7.25 / 7.2512
        s = f"{r:.4f}".rstrip("0").rstrip(".")
        return s if s else str(self.DEFAULT_RATE)

    def _set_rate_text(self, r: float, *, block_signal: bool = True):
        """更新汇率显示（「汇率 7.25」）；block_signal 仅为兼容旧调用。"""
        try:
            val = float(r)
        except (TypeError, ValueError):
            val = float(self.DEFAULT_RATE)
        if val <= 0:
            val = float(self.DEFAULT_RATE)
        self._fx_rate = round(val, 4)
        lbl = getattr(self, "lbl_rate", None)
        if lbl is not None:
            lbl.setText(f"汇率 {self._format_rate(val)}")

    def _remember_rate(self, rate=None):
        """把当前汇率立刻写入 user.txt（不依赖主窗口防抖）。"""
        r = self._parse_rate_text(rate if rate is not None else None)
        try:
            from utils.user_prefs import load_user_prefs, save_user_prefs, default_prefs
            prefs, _ = load_user_prefs()
            # 保证段存在
            if not isinstance(prefs.get("points_calc"), dict):
                prefs["points_calc"] = dict(default_prefs()["points_calc"])
            prefs["points_calc"]["rate"] = round(r, 6)
            prefs["points_calc"]["currency"] = self._currency or "CNY"
            ok = save_user_prefs(prefs)
            if not ok:
                from utils.logger import get_logger
                get_logger(__name__).warning("积分汇率写入 user.txt 失败 rate=%s", r)
        except Exception:
            from utils.logger import get_logger
            get_logger(__name__).exception("积分汇率落盘异常")

    def _schedule_remember_rate(self, *_args):
        """输入过程中防抖写盘（约 400ms）。"""
        t = getattr(self, "_rate_save_timer", None)
        if t is None:
            self._rate_save_timer = QTimer(self)
            self._rate_save_timer.setSingleShot(True)
            self._rate_save_timer.setInterval(400)
            self._rate_save_timer.timeout.connect(self._remember_rate)
            t = self._rate_save_timer
        t.start()

    def _restore_remembered_rate(self):
        """启动 / 取消编辑时恢复上次汇率。"""
        r = self.DEFAULT_RATE
        try:
            from utils.user_prefs import load_user_prefs
            prefs, _ = load_user_prefs()
            raw = (prefs.get("points_calc") or {}).get("rate")
            if raw is not None:
                r = float(raw)
                if r <= 0:
                    r = self.DEFAULT_RATE
        except Exception:
            r = self.DEFAULT_RATE
        self._set_rate_text(r, block_signal=True)

    def export_settings(self) -> dict:
        """供主窗口 user_prefs 落盘。"""
        return {
            "rate": round(self._parse_rate_text(), 6),
            "currency": self._currency or "CNY",
            "history_view": self._usage_type or "image",
        }

    def apply_settings(self, d: dict):
        """从 user.txt 恢复。主窗口启动时调用；务必覆盖默认 7.25。"""
        if not isinstance(d, dict):
            return
        if "rate" in d and d.get("rate") is not None:
            try:
                r = float(d.get("rate"))
                if r > 0:
                    self._set_rate_text(r, block_signal=True)
            except (TypeError, ValueError):
                pass
        cur = d.get("currency")
        if cur in ("CNY", "USD"):
            # 恢复币种时不要触发自动计算连环写盘
            suspended = getattr(self, "_auto_calc_suspended", False)
            self._auto_calc_suspended = True
            try:
                self._set_currency(cur)
            finally:
                self._auto_calc_suspended = suspended
        view = d.get("history_view")
        if view in ("image", "video"):
            suspended = getattr(self, "_auto_calc_suspended", False)
            self._auto_calc_suspended = True
            try:
                self._set_usage_type(view)
            finally:
                self._auto_calc_suspended = suspended

    # ── UI 构建 ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        page_layout = QVBoxLayout(self)
        # 与系统总览一致：ContentRoot 已有左右内边距，页面不再叠第二层
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(6)

        # 上半：四行输入 + 结果，顶对齐不拉伸
        self._top_panel = self._build_top_panel()
        page_layout.addWidget(self._top_panel, 0)

        # 下半：历史记录吃掉剩余全部空间
        page_layout.addWidget(self._build_records_panel(), 1)

    # ── 顶部：四行紧贴，顶对齐；标签贴输入，组与组之间不拉空 ────────────────
    def _build_top_panel(self) -> QWidget:
        box = make_card("CardPointsTop")

        form = QVBoxLayout(box)
        form.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        form.setSpacing(4)
        form.setAlignment(Qt.AlignTop)

        # 刷功能区样式；空标题头立刻拆掉，四行直接顶对齐
        title_inp = install_card_title(box, form, "")
        head = title_inp.parentWidget()
        title_inp.setParent(None)
        if head is not None:
            form.removeWidget(head)
            head.setParent(None)

        _H = 28
        _PAIR = 4     # 标签 ↔ 输入
        _GROUP = 8    # 组与组

        def _add_row():
            """四行同高：每行一个固定高度容器，内容垂直居中。"""
            wrap = QWidget(box)
            wrap.setFixedHeight(_H)
            wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            h = QHBoxLayout(wrap)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(_GROUP)
            h.setAlignment(Qt.AlignVCenter)
            form.addWidget(wrap)
            return h

        def _field_lbl(text):
            l = self._lbl(text)
            l.setFixedHeight(_H)
            l.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            return l

        def _glue(*widgets, spacing=_PAIR):
            """一组控件水平贴紧，不参与拉伸。"""
            g = QHBoxLayout()
            g.setContentsMargins(0, 0, 0, 0)
            g.setSpacing(spacing)
            g.setAlignment(Qt.AlignVCenter)
            for w in widgets:
                g.addWidget(w, 0, Qt.AlignVCenter)
            return g

        def _metric_chip(label_text, value_init="—"):
            w = QFrame(box)
            w.setObjectName("MetricCard")
            w.setAttribute(Qt.WA_StyledBackground, True)
            w.setFrameShape(QFrame.NoFrame)
            w.setFixedHeight(_H)
            w.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            w.setStyleSheet(
                "QFrame#MetricCard { background: transparent; border: none; }"
            )
            hl = QHBoxLayout(w)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(_PAIR)
            lbl = QLabel(label_text)
            lbl.setObjectName("MetricLabel")
            lbl.setAttribute(Qt.WA_StyledBackground, True)
            lbl.setStyleSheet("background: transparent;")
            lbl.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
            val = QLabel(value_init)
            val.setObjectName("MetricValue")
            val.setAttribute(Qt.WA_StyledBackground, True)
            val.setStyleSheet("background: transparent; font-size: 13px; font-weight: 700;")
            val.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            val.setMinimumWidth(48)
            val.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            hl.addWidget(lbl, 0)
            hl.addWidget(val, 0)
            return w, val

        # ── 行1：生图/生视频 左对齐 · 「填好…」提示右对齐 ──────────────────
        self.usage_switch = UsageTypeSwitch(self)
        self.usage_switch.changed.connect(self._set_usage_type)
        self.usage_switch.setFixedHeight(_H)

        self.result_hint = _ElideLabel("")
        self.result_hint.setObjectName("ResultHint")
        self.result_hint.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.result_hint.setMinimumWidth(0)
        self.result_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.result_hint.setFixedHeight(_H)

        row1 = _add_row()
        row1.addWidget(self.usage_switch, 0, Qt.AlignVCenter)
        row1.addWidget(self.result_hint, 1, Qt.AlignVCenter)

        # ── 行2：平台+月费/年费 左 · 订阅金额+币种汇率 紧随 ────────────────
        lbl_p = _field_lbl("平台")
        self.inp_platform = self._inp("例：可灵、即梦…")
        self.inp_platform.setMinimumWidth(120)
        self.inp_platform.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.btn_monthly = self._toggle("月费", True,  lambda: self._set_fee_type("monthly"))
        self.btn_yearly  = self._toggle("年费", False, lambda: self._set_fee_type("yearly"))
        self.btn_monthly.setFixedWidth(56)
        self.btn_yearly.setFixedWidth(56)

        lbl_a = _field_lbl("订阅金额")
        self.inp_amount = self._inp("输入金额")
        self.inp_amount.setMinimumWidth(72)
        self.inp_amount.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.lbl_amount_unit = _field_lbl("人民币")
        self.lbl_amount_unit.setObjectName("CalcAmountUnit")
        # 人民币/美元同宽，切币种不挤后面的 CNY/USD
        self.lbl_amount_unit.setFixedWidth(
            self.lbl_amount_unit.fontMetrics().horizontalAdvance("人民币") + 4
        )
        self.btn_cny = self._toggle("CNY", True,  lambda: self._set_currency("CNY"))
        self.btn_usd = self._toggle("USD", False, lambda: self._set_currency("USD"))
        self.btn_cny.setFixedWidth(48)
        self.btn_usd.setFixedWidth(48)
        self._fx_rate = float(self.DEFAULT_RATE)
        self.lbl_rate = _field_lbl(f"汇率 {self._format_rate(self._fx_rate)}")
        self.lbl_rate.setFixedWidth(
            self.lbl_rate.fontMetrics().horizontalAdvance("汇率 000.0000") + 4
        )
        self.btn_rate_refresh = apply_mini_button(QPushButton("↻"))
        self.btn_rate_refresh.setObjectName("RateRefreshBtn")
        self.btn_rate_refresh.setFixedSize(_H, _H)
        self.btn_rate_refresh.clicked.connect(self._fetch_usd_cny_rate)

        row2 = _add_row()
        row2.addWidget(lbl_p, 0, Qt.AlignVCenter)
        row2.addWidget(self.inp_platform, 1, Qt.AlignVCenter)
        row2.addWidget(self.btn_monthly, 0, Qt.AlignVCenter)
        row2.addWidget(self.btn_yearly, 0, Qt.AlignVCenter)
        row2.addWidget(lbl_a, 0, Qt.AlignVCenter)
        row2.addWidget(self.inp_amount, 1, Qt.AlignVCenter)
        row2.addWidget(self.lbl_amount_unit, 0, Qt.AlignVCenter)
        row2.addWidget(self.btn_cny, 0, Qt.AlignVCenter)
        row2.addWidget(self.btn_usd, 0, Qt.AlignVCenter)

        self._rate_nam = QNetworkAccessManager(self)
        self._rate_nam.finished.connect(self._on_rate_reply)

        # ── 行3：每月积分 + 单次消耗 + 时长步进，全部贴紧 ─────────────────
        def _stepper_btn(symbol):
            b = QPushButton(symbol)
            b.setObjectName("StepperBtn")
            b.setFixedSize(26, 28)
            b.setCursor(Qt.PointingHandCursor)
            return b

        self._video_secs = 10
        self.lbl_secs_display = QLabel("10 秒")
        self.lbl_secs_display.setObjectName("SecsDisplay")
        self.lbl_secs_display.setAlignment(Qt.AlignCenter)
        self.lbl_secs_display.setFixedWidth(48)
        self.lbl_secs_display.setFixedHeight(28)
        self.btn_secs_min = _stepper_btn("◀◀")
        self.btn_secs_dec = _stepper_btn("◀")
        self.btn_secs_inc = _stepper_btn("▶")
        self.btn_secs_max = _stepper_btn("▶▶")

        def _set_secs(s):
            self._video_secs = max(4, min(60, s))
            self.lbl_secs_display.setText(f"{self._video_secs} 秒")
            idx = self.cmb_video_secs.findData(self._video_secs)
            if idx >= 0:
                self.cmb_video_secs.blockSignals(True)
                self.cmb_video_secs.setCurrentIndex(idx)
                self.cmb_video_secs.blockSignals(False)
            self._schedule_calc()

        self.btn_secs_min.clicked.connect(lambda: _set_secs(4))
        self.btn_secs_dec.clicked.connect(lambda: _set_secs(self._video_secs - 1))
        self.btn_secs_inc.clicked.connect(lambda: _set_secs(self._video_secs + 1))
        self.btn_secs_max.clicked.connect(lambda: _set_secs(60))

        self.cmb_video_secs = QComboBox(self)
        self.cmb_video_secs.setVisible(False)
        for s in range(4, 61):
            self.cmb_video_secs.addItem(f"{s} 秒", s)
        self.cmb_video_secs.setCurrentIndex(6)

        self._video_dur_widgets = [
            self.btn_secs_min, self.btn_secs_dec,
            self.lbl_secs_display,
            self.btn_secs_inc, self.btn_secs_max,
        ]

        lbl_mp = _field_lbl("每月到账积分")
        self.inp_monthly_pts = self._inp("例：3000")
        self.inp_monthly_pts.setMinimumWidth(80)
        self.inp_monthly_pts.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        lbl_cp = _field_lbl("单次消耗积分")
        self.inp_consume = self._inp("例：100")
        self.inp_consume.setMinimumWidth(72)
        self.inp_consume.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

        row3 = _add_row()
        row3.addWidget(lbl_mp, 0, Qt.AlignVCenter)
        row3.addWidget(self.inp_monthly_pts, 1, Qt.AlignVCenter)
        row3.addWidget(lbl_cp, 0, Qt.AlignVCenter)
        row3.addWidget(self.inp_consume, 1, Qt.AlignVCenter)
        for w in self._video_dur_widgets:
            row3.addWidget(w, 0, Qt.AlignVCenter)
        row3.addLayout(_glue(self.lbl_rate, self.btn_rate_refresh), 0)

        # ── 行4：每张图片 + 单支视频 + 月成本 + 保存 ──────────────────────
        self.lbl_cost_image = _metric_chip("每张图片", "—")
        self.lbl_cost_video = _metric_chip("每秒视频", "—")
        self.lbl_cost_video[0].hide()
        self.lbl_cost_one_video = _metric_chip("单支视频", "—")
        self.lbl_monthly = _metric_chip("月成本", "—")

        self.btn_save = QPushButton("保存到记录")
        apply_btn_download(self.btn_save)
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.setFixedHeight(_H)
        _save_fm = self.btn_save.fontMetrics()
        self.btn_save.setMinimumWidth(
            max(
                _save_fm.horizontalAdvance("保存到记录"),
                _save_fm.horizontalAdvance("保存修改"),
            ) + 28
        )
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self._save_record)

        for chip, _val in (
            self.lbl_cost_image,
            self.lbl_cost_one_video,
            self.lbl_monthly,
        ):
            chip.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.btn_save.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

        row4 = _add_row()
        row4.addWidget(self.lbl_cost_image[0], 1, Qt.AlignVCenter)
        row4.addWidget(self.lbl_cost_one_video[0], 1, Qt.AlignVCenter)
        row4.addWidget(self.lbl_monthly[0], 1, Qt.AlignVCenter)
        row4.addWidget(self.btn_save, 1, Qt.AlignVCenter)

        self._set_metric_active(self.lbl_cost_image, True)
        self._set_one_video_metric_active(False)
        self._show_hint()
        return box


    def _metric(self, label_text, value_text):
        """兼容旧调用（未使用，保留避免报错）"""
        c = QWidget(); c.setObjectName("MetricCard"); c.setAttribute(Qt.WA_StyledBackground, True)
        v = QVBoxLayout(c); v.setContentsMargins(12, 8, 12, 8); v.setSpacing(2)
        lbl = QLabel(label_text); lbl.setObjectName("MetricLabel")
        val = QLabel(value_text); val.setObjectName("MetricValue")
        v.addWidget(lbl); v.addWidget(val)
        return c, val

    # ── 历史记录面板 ─────────────────────────────────────────────────────────
    def _build_records_panel(self) -> QWidget:
        box = make_card("CardPointsRecords")

        outer = QVBoxLayout(box)
        outer.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        outer.setSpacing(0)

        def _make_section(section_key):
            sec = QWidget()
            sec.setAttribute(Qt.WA_StyledBackground, True)
            sec.setStyleSheet("background: transparent;")
            vl = QVBoxLayout(sec)
            vl.setContentsMargins(0, 0, 0, 0)
            vl.setSpacing(0)

            header = QWidget()
            header.setObjectName("RecordHeader")
            header.setAttribute(Qt.WA_StyledBackground, True)
            hh = QHBoxLayout(header)
            hh.setContentsMargins(10, 4, 8, 4)
            hh.setSpacing(6)
            # 首列是平台名；生图/生视频由「用途类型」按钮切页
            cost_title = "每张" if section_key == "img" else "每秒"
            columns = [("平台", 23, "RecordHeaderCell")] + [
                (txt, stretch, "RecordHeaderCell")
                for txt, stretch in [
                    ("费用 月&年", 20), ("每月积分", 15), ("积分/次", 18),
                    (cost_title, 15), ("月成本", 9),
                ]
            ]
            for txt, stretch, obj in columns:
                if txt == cost_title:
                    cost_wrap = QWidget()
                    cost_wrap.setStyleSheet("background: transparent;")
                    cost_layout = QHBoxLayout(cost_wrap)
                    cost_layout.setContentsMargins(0, 0, 0, 0)
                    cost_layout.setSpacing(4)
                    hl = QLabel(txt); hl.setObjectName(obj)
                    hl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                    hl.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
                    cost_layout.addWidget(hl)

                    def _make_sort_btn():
                        b = QPushButton("")
                        b.setObjectName("CostSortBtn")
                        b.setFixedSize(22, 22)
                        b.setCursor(Qt.PointingHandCursor)
                        b.setCheckable(False)
                        b.setProperty("sectionKey", section_key)
                        b.clicked.connect(lambda: self._set_cost_sort(section_key))
                        self._cost_sort_btns[section_key] = b
                        self._style_cost_sort_btn(b)
                        return b

                    cost_layout.addWidget(_make_sort_btn())
                    cost_layout.addStretch(1)
                    cost_wrap.setMinimumWidth(0)
                    cost_wrap.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
                    hh.addWidget(cost_wrap, stretch)
                else:
                    hl = _ElideLabel(txt); hl.setObjectName(obj)
                    hl.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                    hl.setMinimumWidth(0)
                    hl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
                    hh.addWidget(hl, stretch)
            # 与记录行操作槽同宽，表头和正文列对齐
            sp = QWidget(); sp.setFixedWidth(_ROW_ACTION_W)
            hh.addWidget(sp, 0)
            vl.addWidget(header)

            sep = QFrame(); sep.setFrameShape(QFrame.HLine)
            sep.setObjectName("RecordDivider")
            vl.addWidget(sep)

            # 滚动区（竖向滚动条：记录区标准，同截图工具）
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setObjectName("RecordsScroll")
            # 内容不足时隐藏滚轨，记录为空时不显示空滚动条
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            container = _DragContainer()
            container.setObjectName("RecordsContainer")
            container.setAttribute(Qt.WA_StyledBackground, True)
            rec_layout = QVBoxLayout(container)
            rec_layout.setContentsMargins(0, 4, 0, 4)
            rec_layout.setSpacing(3)
            rec_layout.addStretch(1)
            scroll.setWidget(container)
            vl.addWidget(scroll, 1)
            return sec, rec_layout, container

        sec_img, self.layout_image, self._container_image = _make_section("img")
        sec_vid, self.layout_video, self._container_video = _make_section("vid")

        self._container_image.reordered.connect(lambda r, i: self._reorder_records(r, i, self.layout_image))
        self._container_video.reordered.connect(lambda r, i: self._reorder_records(r, i, self.layout_video))

        self._hist_stack = QStackedWidget()
        self._hist_stack.setStyleSheet("background: transparent;")
        self._hist_stack.addWidget(sec_img)
        self._hist_stack.addWidget(sec_vid)
        outer.addWidget(self._hist_stack, 1)
        self._hist_stack.setCurrentIndex(0 if self._usage_type != "video" else 1)
        return box

    # ── 控件工厂 ─────────────────────────────────────────────────────────────
    def _lbl(self, text):
        l = QLabel(text)
        l.setObjectName("CalcFieldLabel")
        # 禁止被压扁裁字；对齐由 setFixedWidth 的行决定
        l.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        l.setMinimumWidth(0)
        return l

    def _inp(self, placeholder=""):
        e = QLineEdit(); e.setObjectName("CalcInput")
        e.setPlaceholderText(placeholder); e.setFixedHeight(28); return e

    def _toggle(self, text, checked, slot):
        b = QPushButton(text)
        b.setObjectName("ToggleBtn")
        b.setCheckable(True)
        b.setChecked(checked)
        b.setFixedHeight(28)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(slot)
        # 选中态变化时重刷反色（含程序里 setChecked 联动的另一颗按钮）
        b.toggled.connect(lambda _c, btn=b: _style_toggle_btn(btn))
        _style_toggle_btn(b)
        self._toggle_btns.append(b)
        return b

    def _hrow(self, *widgets):
        h = QHBoxLayout(); h.setSpacing(6)
        for w in widgets: h.addWidget(w)
        h.addStretch(1); return h

    def _wire_auto_calc(self):
        """输入/切换变更 → 防抖自动计算。"""
        for w in (
            self.inp_platform,
            self.inp_amount,
            self.inp_monthly_pts,
            self.inp_consume,
        ):
            try:
                w.textChanged.connect(self._schedule_calc)
            except Exception:
                pass

    def _schedule_calc(self, *_args):
        if getattr(self, "_auto_calc_suspended", False):
            return
        try:
            self._calc_timer.start()
        except Exception:
            self._calc()

    # ── 状态切换 ─────────────────────────────────────────────────────────────
    def _set_fee_type(self, t):
        self._fee_type = t
        self.btn_monthly.setChecked(t == "monthly")
        self.btn_yearly.setChecked(t == "yearly")
        _style_toggle_btn(self.btn_monthly)
        _style_toggle_btn(self.btn_yearly)
        self._schedule_calc()

    def _set_currency(self, c):
        self._currency = c
        self.btn_cny.setChecked(c == "CNY")
        self.btn_usd.setChecked(c == "USD")
        _style_toggle_btn(self.btn_cny)
        _style_toggle_btn(self.btn_usd)
        # 金额旁单位：仅「人民币」/「美元」
        unit = getattr(self, "lbl_amount_unit", None)
        if unit is not None:
            unit.setText("美元" if c == "USD" else "人民币")
        self._schedule_calc()

    def _set_usage_btns_enabled(self, enabled: bool):
        """新建记录时可切生图/生视频；编辑记录时冻结，避免改用途。"""
        sw = getattr(self, "usage_switch", None)
        if sw is not None:
            sw.setFrozen(not bool(enabled))

    def _set_usage_type(self, t):
        self._usage_type = t
        sw = getattr(self, "usage_switch", None)
        if sw is not None:
            sw.set_usage_type(t)
        # 每张图片 / 单支视频始终占位：非当前用途灰显
        self._set_metric_active(getattr(self, "lbl_cost_image", None), t == "image")
        self._set_one_video_metric_active(t == "video")
        vid_pair = getattr(self, "lbl_cost_video", None)
        if vid_pair:
            vid_pair[0].hide()
        # 生图：冻结视频时长；生视频：解冻
        self._set_video_dur_enabled(t == "video")
        # 记录区跟着用途类型切子页
        stack = getattr(self, "_hist_stack", None)
        if stack is not None:
            stack.setCurrentIndex(0 if t == "image" else 1)
        self._schedule_calc()

    def _set_metric_active(self, pair, active: bool):
        """结果芯片：当前用途正常色，另一项灰显占位。"""
        if not pair:
            return
        row, val = pair
        try:
            row.setProperty("muted", "false" if active else "true")
            st = row.style()
            if st is not None:
                st.unpolish(row)
                st.polish(row)
            row.update()
            # 子标签也刷一遍，确保 QSS 属性选择器生效
            for child in row.findChildren(QLabel):
                if st is not None:
                    st.unpolish(child)
                    st.polish(child)
                child.update()
        except Exception:
            pass
        if not active:
            try:
                val.setText("—")
            except Exception:
                pass

    def _set_one_video_metric_active(self, active: bool):
        """单支视频：生视频=正常色；生图=灰色占位（不写入记录）。"""
        self._set_metric_active(getattr(self, "lbl_cost_one_video", None), active)

    def _set_video_dur_enabled(self, enabled: bool):
        """生图时禁用时长步进器，生视频时恢复。不改 stylesheet，避免边框被冲掉后整行抖。"""
        for w in getattr(self, "_video_dur_widgets", []):
            try:
                w.setEnabled(enabled)
            except Exception:
                pass

    # ── 实时汇率（USD → CNY） ────────────────────────────────────────────────
    def _fetch_usd_cny_rate(self):
        """点 ↻：从 open.er-api.com 取 1 USD = ? CNY，写入汇率框。"""
        if getattr(self, "_rate_fetching", False):
            return
        self._rate_fetching = True
        self.btn_rate_refresh.setEnabled(False)
        self._show_hint("⏳ 正在获取 USD→CNY 实时汇率…")
        req = QNetworkRequest(QUrl("https://open.er-api.com/v6/latest/USD"))
        self._rate_nam.get(req)

    def _on_rate_reply(self, reply):
        self._rate_fetching = False
        try:
            self.btn_rate_refresh.setEnabled(True)
        except Exception:
            pass
        try:
            if reply.error():
                raise RuntimeError(reply.errorString())
            data = bytes(reply.readAll()).decode("utf-8")
            obj = json.loads(data)
            rates = obj.get("rates") or {}
            cny = rates.get("CNY")
            if cny is None:
                raise ValueError("响应中无 CNY 汇率")
            rate = float(cny)
            if rate <= 0:
                raise ValueError("汇率无效")
            # 更新显示并立刻落盘 user.txt
            self._set_rate_text(rate)
            self._remember_rate(rate)
            self._show_hint(f"🟢 已更新汇率：1 USD ≈ {rate:.4f} CNY")
            self._schedule_calc()
        except Exception as e:
            self._show_hint(f"❌ 汇率获取失败，仍用当前值（{e}）")
        finally:
            try:
                reply.deleteLater()
            except Exception:
                pass

    # ── 计算（自动触发） ─────────────────────────────────────────────────────
    def _missing_required(self):
        """返回尚未填好的必填项说明列表；齐了返回 []。"""
        missing = []
        try:
            amount = float(self.inp_amount.text().strip())
            assert amount > 0
        except Exception:
            missing.append("订阅金额")
        try:
            fx = self._parse_rate_text()
            assert fx > 0
        except Exception:
            missing.append("汇率")
        try:
            monthly_pts = int(self.inp_monthly_pts.text().strip())
            assert monthly_pts > 0
        except Exception:
            missing.append("每月积分")
        try:
            consume = int(self.inp_consume.text().strip())
            assert consume > 0
        except Exception:
            missing.append("单次消耗")
        return missing

    def _clear_result_metrics(self):
        self.lbl_cost_image[1].setText("—")
        self.lbl_cost_video[1].setText("—")
        self.lbl_cost_one_video[1].setText("—")
        self.lbl_monthly[1].setText("—")
        self._last_result = None
        self.btn_save.setEnabled(False)

    def _calc(self):
        """必填齐了就算出结果；否则只更新提示，不弹错、不刷屏。"""
        missing = self._missing_required()
        if missing:
            self._clear_result_metrics()
            if any(self.inp_amount.text().strip() or
                   self.inp_monthly_pts.text().strip() or
                   self.inp_consume.text().strip()):
                self._show_hint(f"还差：{'、'.join(missing)}")
            else:
                self._show_hint()  # 默认引导语，不留空
            return

        platform = self.inp_platform.text().strip() or "未命名平台"
        amount = float(self.inp_amount.text().strip())
        is_usd = (self._currency == "USD")
        fx_rate = self._parse_rate_text()
        monthly_pts = int(self.inp_monthly_pts.text().strip())
        consume = int(self.inp_consume.text().strip())

        # 算成本：仅 USD 订阅用汇率折合人民币；CNY 金额直接用
        amount_cny       = amount * fx_rate if is_usd else amount
        monthly_cost_cny = amount_cny if self._fee_type == "monthly" else amount_cny / 12
        uses_per_mo      = monthly_pts // consume
        cost_per_use     = monthly_cost_cny / uses_per_mo if uses_per_mo > 0 else float("inf")

        is_video = self._usage_type == "video"
        if is_video:
            video_secs   = self._video_secs
            cost_per_sec = cost_per_use / video_secs if video_secs > 0 else float("inf")
            cost_str     = f"{cost_per_sec:.4f}".rstrip("0").rstrip(".")
            self.lbl_cost_video[1].setText(f"¥ {cost_str}")
            self.lbl_cost_image[1].setText("—")
            if cost_per_use == float("inf"):
                self.lbl_cost_one_video[1].setText("—")
            else:
                one_str = f"{cost_per_use:.4f}".rstrip("0").rstrip(".")
                self.lbl_cost_one_video[1].setText(f"¥ {one_str}")
            cost_per = cost_per_sec
        else:
            video_secs = 0
            cost_str   = f"{cost_per_use:.4f}".rstrip("0").rstrip(".")
            self.lbl_cost_image[1].setText(f"¥ {cost_str}")
            self.lbl_cost_one_video[1].setText("—")
            self.lbl_cost_video[1].setText("—")
            cost_per = cost_per_use

        monthly_str = f"{monthly_cost_cny:.2f}".rstrip("0").rstrip(".")
        self.lbl_monthly[1].setText(f"¥ {monthly_str}")
        if self._editing_id:
            self._show_hint("✓ 已更新，可保存修改")
        else:
            self._show_hint("✓ 已自动计算，可保存")
        self.btn_save.setEnabled(True)

        # 注意：单支视频仅 UI 展示，不进入 _last_result / 历史记录
        self._last_result = {
            "id":               self._editing_id or datetime.now().strftime("%Y%m%d%H%M%S%f"),
            "platform":         platform,
            "fee_type":         self._fee_type,
            "currency":         "USD" if is_usd else "CNY",
            "amount":           amount,
            # 始终保存界面上的 USD→CNY 汇率（旧逻辑在 CNY 时写死 1.0，导致 7.25 丢进记录）
            "rate":             round(fx_rate, 6),
            "monthly_pts":      monthly_pts,
            "consume_pts":      consume,
            "usage_type":       self._usage_type,
            "video_secs":       video_secs,
            "monthly_cost_cny": round(monthly_cost_cny, 4),
            "cost_per":         round(cost_per, 6),
            "uses_per_mo":      uses_per_mo,
        }

    def _default_hint(self) -> str:
        """引导提示默认文案：始终一行、始终有字。"""
        if self._editing_id:
            return "📝 编辑中，改数后自动重算"
        return "填好金额、每月积分、单次消耗后自动计算"

    def _show_hint(self, text=None):
        """更新第 1 行右侧提示：强制单行；空/None 回退默认文案。

        使用 _ElideLabel：宽度不够时显示省略号，
        避免长文案抬高 minimumWidth 破坏分栏。
        """
        if text is None or not str(text).strip():
            text = self._default_hint()
        # 压成一行，避免撑高布局
        text = " ".join(str(text).replace("\n", " ").split())
        self.result_hint.setText(text)

    # ── 保存 / 编辑 / 删除 ──────────────────────────────────────────────────
    def _save_record(self):
        if not self._last_result: return
        records = _load_records()
        if self._editing_id:
            records = [r for r in records if r.get("id") != self._editing_id]
            self._editing_id = None
            self.btn_save.setText("保存到记录")
        is_image = self._last_result.get("usage_type") != "video"
        if is_image:
            records.insert(0, self._last_result)
        else:
            img_count = len([r for r in records if r.get("usage_type", "image") == "image"])
            records.insert(img_count, self._last_result)
        _save_records(records)
        # 计算用到的汇率一并记住
        try:
            self._remember_rate(self._last_result.get("rate"))
        except Exception:
            pass
        self._last_result = None
        self.btn_save.setEnabled(False)
        self._show_hint("✓ 已写入历史记录")
        self._set_usage_btns_enabled(True)
        self._refresh_records()

    def _edit_record(self, record):
        rid = record.get("id")
        if rid and rid == self._editing_id:
            return
        # 同一次点击里：空白取消会先发生，松手再点到行时不得重新进编辑
        if getattr(self, "_block_row_edit", False):
            return
        # 已经在编别的记录：点到其它行只结束编辑，不切过去，避免误操作
        if self._editing_id:
            self._cancel_edit()
            return
        self._editing_id = rid
        # 批量回填时暂停自动计算，填完再算一次
        try:
            self._calc_timer.stop()
        except Exception:
            pass
        self._auto_calc_suspended = True
        try:
            self.inp_platform.setText(record.get("platform", ""))
            self._set_fee_type(record.get("fee_type", "monthly"))
            currency = record.get("currency", "CNY")
            self._set_currency(currency)
            # ① 金额不显示小数
            amount = record.get("amount", "")
            self.inp_amount.setText(
                str(int(amount))
                if isinstance(amount, float) and amount == int(amount)
                else str(amount)
            )
            # 汇率：记录里应始终是 USD→CNY；旧 CNY 记录曾写死 1.0，那种情况回退默认 7.25
            try:
                r = float(record.get("rate", 7.25))
            except (TypeError, ValueError):
                r = 7.25
            if r <= 0 or (currency == "CNY" and r <= 1.5):
                # 旧 CNY 记录写死 1.0：改回当前 user 偏好汇率，而不是永远 7.25
                r = self._parse_rate_text()
            self._set_rate_text(r, block_signal=True)
            self.inp_monthly_pts.setText(str(record.get("monthly_pts", "")))
            self.inp_consume.setText(str(record.get("consume_pts", "")))
            self._set_usage_type(record.get("usage_type", "image"))
            video_secs = record.get("video_secs", 10)
            self._video_secs = video_secs
            self.lbl_secs_display.setText(f"{video_secs} 秒")
            idx = self.cmb_video_secs.findData(video_secs)
            if idx >= 0:
                self.cmb_video_secs.blockSignals(True)
                self.cmb_video_secs.setCurrentIndex(idx)
                self.cmb_video_secs.blockSignals(False)
        finally:
            self._auto_calc_suspended = False
        self.btn_save.setText("保存修改")
        self.btn_save.setEnabled(False)
        self._set_usage_btns_enabled(False)
        self._mark_editing_row(self._editing_id)
        self._calc()  # 立刻用当前数据算出结果，方便直接改一项后保存

    def _mark_editing_row(self, record_id):
        """把记录行的“删除 ↔ 复制”状态，切换到 record_id 对应的那一行（None 表示全部取消）。"""
        editing = record_id is not None
        for rid, row in getattr(self, "_record_rows", {}).items():
            row.set_editing(rid == record_id)
            # 编辑中冻住其它行的删除，避免误点删掉别的记录
            btn = getattr(row, "btn_del", None)
            if btn is not None:
                btn.setEnabled(not editing)

    def eventFilter(self, obj, event):
        """编辑中：点「输入参数 / 计算结果」以外的本页区域 → 取消编辑并吞掉点击。

        侧栏等外部点击不吞，由主窗口切页时结束编辑再跳转。
        """
        if event.type() not in (
            QEvent.MouseButtonPress,
            QEvent.MouseButtonRelease,
            QEvent.MouseButtonDblClick,
        ):
            return super().eventFilter(obj, event)
        if not isinstance(event, QMouseEvent) or event.button() != Qt.LeftButton:
            return super().eventFilter(obj, event)
        if not self.isVisible():
            return super().eventFilter(obj, event)
        if not self._editing_id and not getattr(self, "_block_row_edit", False):
            return super().eventFilter(obj, event)

        hit = QApplication.widgetAt(event.globalPos())
        if hit is None:
            return super().eventFilter(obj, event)
        if self._is_in_edit_safe_zone(hit):
            return super().eventFilter(obj, event)

        on_this_page = hit is self or self.isAncestorOf(hit)
        if not on_this_page:
            return super().eventFilter(obj, event)

        if self._editing_id and event.type() == QEvent.MouseButtonPress:
            self._cancel_edit()
        return True

    def _is_in_edit_safe_zone(self, obj: QWidget) -> bool:
        """输入参数 + 计算结果视为编辑工作区；行上的「复制」也不能被取消编辑吞掉。"""
        if obj is None:
            return False
        top = getattr(self, "_top_panel", None)
        w = obj
        while w is not None:
            if top is not None and w is top:
                return True
            if w.objectName() == "RecordCopyBtn":
                return True
            if w is self:
                return False
            w = w.parentWidget()
        return False

    def _clear_block_row_edit(self):
        app = QApplication.instance()
        if app is not None and app.mouseButtons() & Qt.LeftButton:
            QTimer.singleShot(50, self._clear_block_row_edit)
            return
        self._block_row_edit = False

    def _copy_record(self):
        """编辑态点「复制」：把当前编辑数据插到本条下方，命名为「新记录」，并切去编辑它。"""
        if not self._editing_id:
            return
        records = _load_records()
        src_idx = next(
            (i for i, r in enumerate(records) if r.get("id") == self._editing_id),
            None,
        )
        if src_idx is None:
            return

        if self._last_result:
            new_rec = dict(self._last_result)
        else:
            new_rec = dict(records[src_idx])
        new_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
        new_rec["id"] = new_id
        new_rec["platform"] = "新记录"
        records.insert(src_idx + 1, new_rec)
        _save_records(records)

        self._editing_id = new_id
        self._auto_calc_suspended = True
        try:
            self.inp_platform.setText("新记录")
        finally:
            self._auto_calc_suspended = False
        self.btn_save.setText("保存修改")
        self._set_usage_btns_enabled(False)
        self._refresh_records()
        self._calc()
        if self._last_result:
            self._show_hint("✓ 已复制为「新记录」，可继续改")

    def _cancel_edit(self):
        """点记录区空白 / 切页：退出编辑模式，输入区恢复初始状态，不保存任何修改。"""
        if not self._editing_id:
            return
        self._block_row_edit = True
        QTimer.singleShot(0, self._clear_block_row_edit)
        self._editing_id  = None
        self._last_result = None
        try:
            self._calc_timer.stop()
        except Exception:
            pass
        self._auto_calc_suspended = True
        try:
            self.inp_platform.clear()
            self.inp_amount.clear()
            # 取消编辑：汇率恢复为 user.txt 里记住的值，禁止写死 7.25 覆盖用户习惯
            self._restore_remembered_rate()
            self._set_currency("CNY")
            self._set_fee_type("monthly")
            self.inp_monthly_pts.clear()
            self.inp_consume.clear()
            # 用途类型不改：取消编辑后仍停在当前生图/生视频列表
            self._video_secs = 10
            self.lbl_secs_display.setText("10 秒")
            self.cmb_video_secs.blockSignals(True)
            self.cmb_video_secs.setCurrentIndex(6)
            self.cmb_video_secs.blockSignals(False)
        finally:
            self._auto_calc_suspended = False
        self.btn_save.setText("保存到记录")
        self.btn_save.setEnabled(False)
        self._set_usage_btns_enabled(True)
        self._clear_result_metrics()
        self._show_hint()  # 恢复默认引导
        self._mark_editing_row(None)

    def _delete_record(self, record):
        if QMessageBox.question(
            self, "确认删除",
            f"确定要删除「{record.get('platform', '此记录')}」吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes:
            records = [r for r in _load_records() if r.get("id") != record.get("id")]
            _save_records(records)
            self._refresh_records()

    def _reorder_records(self, record_id, target_index, layout):
        """拖拽排序后更新记录文件并刷新显示。"""
        records = _load_records()
        img_recs = [r for r in records if r.get("usage_type", "image") == "image"]
        vid_recs = [r for r in records if r.get("usage_type", "image") != "image"]
        is_image = layout is self.layout_image
        recs_list = img_recs if is_image else vid_recs
        moved_idx = None
        moved_record = None
        for i, r in enumerate(recs_list):
            if r.get("id") == record_id:
                moved_idx = i
                moved_record = r
                break
        if moved_record is None:
            return
        if target_index == moved_idx:
            return
        recs_list.pop(moved_idx)
        adjusted = target_index - 1 if target_index > moved_idx else target_index
        recs_list.insert(adjusted, moved_record)
        new_records = img_recs + vid_recs
        _save_records(new_records)
        self._refresh_records()

    def _set_cost_sort(self, section_key):
        """点击排序按钮：无 → 升序 → 降序 → 无，循环切换。"""
        sort_attr = "_cost_sort_img" if section_key == "img" else "_cost_sort_vid"
        cur = getattr(self, sort_attr, 0)
        new_val = (cur + 1) % 3  # 0→1→2→0
        setattr(self, sort_attr, new_val)
        btn = self._cost_sort_btns.get(section_key)
        if btn is not None:
            self._style_cost_sort_btn(btn)
        self._refresh_records()

    def _refresh_records(self):
        for layout in (self.layout_image, self.layout_video):
            while layout.count() > 1:
                item = layout.takeAt(0)
                if item.widget(): item.widget().deleteLater()

        records   = _load_records()
        img_recs  = [r for r in records if r.get("usage_type", "image") == "image"]
        vid_recs  = [r for r in records if r.get("usage_type", "image") != "image"]

        if self._cost_sort_img != 0:
            img_recs.sort(key=lambda r: r.get("cost_per", 0), reverse=self._cost_sort_img == 2)
        if self._cost_sort_vid != 0:
            vid_recs.sort(key=lambda r: r.get("cost_per", 0), reverse=self._cost_sort_vid == 2)

        self._record_rows = {}

        def _fill(layout, recs, empty_text):
            if not recs:
                lbl = QLabel(empty_text)
                lbl.setObjectName("RecordEmpty")
                lbl.setAlignment(Qt.AlignCenter)
                layout.insertWidget(0, lbl)
                return
            for i, record in enumerate(recs):
                rid = record.get("id")
                row = RecordRow(record, self._edit_record, self._delete_record, self._copy_record,
                                 is_even=(i % 2 == 1), is_editing=(rid == self._editing_id))
                layout.insertWidget(i, row)
                self._record_rows[rid] = row

        _fill(self.layout_image, img_recs, "暂无生图记录")
        _fill(self.layout_video, vid_recs, "暂无生视频记录")
