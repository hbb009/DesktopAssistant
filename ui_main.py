# ui_main.py — 主窗口：侧栏导航、主题、用户偏好、页面堆栈
import ctypes
import os
import re
import sys
import time

from PyQt5.QtCore import (
    Qt, QUrl, QSize, QTimer, QRect, QRectF, QPoint, QPointF, QPropertyAnimation, QEasingCurve,
    pyqtProperty, QEvent, QObject,
)
from PyQt5.QtGui import (
    QIcon, QDesktopServices, QFont, QFontMetrics, QPixmap, QColor, QPainter,
    QPen, QBrush, QLinearGradient, QRadialGradient, QPolygonF, QRegion,
)
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QStackedWidget, QSizePolicy, QLabel, QFrame, QLineEdit, QAbstractButton,
    QScrollArea, QTabBar, QStyle, QStyleOptionTab, QGraphicsOpacityEffect,
)

from pages.page_points_calc import PagePointsCalc
from pages.page_fast_save import PageFastSave
from pages.page_paste import PagePaste
from pages.page_image_proc import PageImageProc
from pages.page_voice_input import PageVoiceInput
from pages.page_voice_clone import PageVoiceClone
from pages.page_game_assist import PageGameAssist
from pages.page_screenshot import PageScreenshot
from pages.page_overview import PageOverview
from pages.page_video import PageVideo
from pages.page_gallery import PageGallery
from pages.page_ratio_calc import PageRatioCalc
from pages.page_dir_link import PageDirLink
from pages.page_timezone_fx import PageTimezoneFx
from pages.page_region_record import PageRegionRecord
from pages.page_clock import PageClock
from pages.page_prompt_editor import PagePromptEditor

from styles.style_all import (
    theme, tk, sp, build_func_card_qss, restyle_all_func_cards,
    apply_theme_palette, build_combo_qss,
    polish_combo_widgets,
    format_ui_scale, UI_SCALE_OPTIONS,
    message_box_info, message_box_warn, message_box_critical,
)
from styles.side_nav_icons import (
    paint_side_nav_icon, side_nav_icon_size,
    paint_header_clock_icon, paint_header_rename_icon,
)
from utils.user_prefs import load_user_prefs, save_user_prefs, default_prefs
from utils.logger import get_logger

log = get_logger(__name__)

# ── 页面集合：当前主程序包含的全部页面 ───────────────────────────────────
_PAGES_MAIN = {
    "overview", "fast", "video", "gallery",
    "paste", "img_proc", "voice", "voice_clone", "game_assist", "prompt", "points", "shot", "region_rec",
    "ratio", "dir_link", "tz_fx",
    "about",
}

# 主窗最小尺寸（对齐 records/user.txt 当前 window_w/h=960×776；可放大，不锁最大）
_WIN_MIN_W = 960
_WIN_MIN_H = 776
# 侧栏宽：原 200 → 196 → 190，主内容区相应加宽
_SIDE_W = 190
# 侧栏滚轮：每次物理拨动移动的像素（菜单按钮高 36，一格 = 一个菜单项）
# Windows 常把一格拆成 3 条 Wheel 事件（「滚动三行」），要靠短时合并
_NAV_WHEEL_STEP = 36
_NAV_WHEEL_COALESCE_S = 0.09

# ── 侧栏导航分区与默认顺序（支持拖拽排序，顺序保存到 records/user.txt）────
# 分区：pinned=置顶常用（不显示标签）/ assistant=助手 / tool=工具小偏门
# 默认顺序与 v9.16 出厂一致：置顶含截图/录屏；助手为粘贴/积分/语音/提示词；工具为比例/时区/目录
_SIDE_SECTION_MAP = {
    "fast": "pinned", "video": "pinned", "gallery": "pinned",
    "shot": "pinned", "region_rec": "pinned",
    "paste": "assistant", "img_proc": "assistant", "points": "assistant",
    "voice": "assistant", "voice_clone": "assistant", "game_assist": "assistant",
    "prompt": "assistant",
    "ratio": "tool", "tz_fx": "tool", "dir_link": "tool",
}

# 导航 key → 按钮属性名（key 与按钮属性名并非一一对应）
_NAV_BTN_ATTR = {
    "fast": "btn_fast", "video": "btn_douyin",
    "gallery": "btn_gallery", "paste": "btn_paste", "img_proc": "btn_img_proc",
    "voice": "btn_voice", "voice_clone": "btn_voice_clone",
    "game_assist": "btn_game_assist", "prompt": "btn_prompt",
    "points": "btn_points", "shot": "btn_shot", "region_rec": "btn_region_rec",
    "ratio": "btn_ratio", "dir_link": "btn_dir_link", "tz_fx": "btn_tz_fx",
}

# 分区默认标题（可被用户重命名，存于 ui.section_names）
_DEFAULT_SECTION_TITLES = {
    "assistant": "助手 · 主手脚",
    "tool":      "工具 · 小偏门",
}

# 分区结构：分区key → (标题, 该分区按钮)。分项名是「固定占位槽」，
# 与按钮一起参与拖拽占位计算，只是分项名本身不可被拖拽。
_SIDE_SECTIONS = [
    ("pinned",    None,                  ["fast", "video", "gallery", "shot", "region_rec"]),
    ("assistant", "助手 · 主手脚",          ["paste", "img_proc", "points", "voice", "voice_clone", "game_assist", "prompt"]),
    ("tool",      "工具 · 小偏门",          ["ratio", "tz_fx", "dir_link"]),
]

# 分区名集合（用于校验 @hdr:xxx 槽位）
_SECTION_NAMES = {s for s, _t, _ks in _SIDE_SECTIONS}

# 侧栏锁定项：不可拖拽排序
# · overview / about 都在底栏，不进可排序列表
_SIDE_LOCKED_KEYS = frozenset({"overview", "about"})

# 电子钟空闲跳转：默认 60 秒；语音编辑 / 提示词 / 语音克隆 / 图片处理 / 截图工具 5 分钟；
# 游戏助手最久（10 分钟），避免截图 → 本地OCR → 编辑 → 大模型 API 作答途中被切走
_CLOCK_IDLE_MS = 60 * 1000
_CLOCK_IDLE_PROMPT_MS = 5 * 60 * 1000
_CLOCK_IDLE_GAME_ASSIST_MS = 10 * 60 * 1000


def _hdr_key(sec: str) -> str:
    """分项名槽位的 key（与按钮 key 区分，以 @hdr: 开头）。"""
    return f"@hdr:{sec}"


def _is_hdr(item) -> bool:
    return isinstance(item, str) and item.startswith("@hdr:")


def resource_path(*paths):
    try:
        from utils.app_paths import resource_path as _rp
        return _rp(*paths)
    except Exception:
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, *paths)

ASSETS_DIR = resource_path("assets")


class SideProgressLine(QWidget):
    """贴在导航按钮底边上的进度线（无文字）。

    - 解析中：整条灰色，1px
    - 下载中：灰底轨道（2px）+ 彩色进度覆盖（与页内进度条 chunk 同色）
    - 解析结束后灰底保留，下载时彩色进度从左到右逐覆盖灰底
    - 无任务：隐藏
    位置由主窗口叠在按钮底边，不占布局空隙。
    """

    H_BUSY = 1       # 解析中灰线
    H_PROGRESS = 2   # 下载进度

    TRACK_COLOR = QColor("#64748b")  # 灰底轨道色

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.H_PROGRESS)
        self._pct = 0          # 0–100；<0 表示整条灰线（解析中）
        self._color = QColor("#94a3b8")
        self._was_busy = False
        self.setVisible(False)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def line_height(self) -> int:
        """当前应占高度：解析 1px / 下载 2px。"""
        return self.H_BUSY if self._pct < 0 else self.H_PROGRESS

    def set_progress(self, active: bool, pct: int = 0, color: str = ""):
        """active=False 时一律隐藏（任务结束 / 失败 / 跳过 / 取消）。

        旧逻辑在「解析 busy → idle」时故意保留灰底轨道，等下载接上；
        但解析失败、已下载过、取消等路径也会走 idle，导致侧栏线一直显示。
        下载开始会再次 active=True 重新画出进度，无需预留空轨道。
        """
        if not active:
            self._pct = 0
            self._was_busy = False
            self._color = QColor("#94a3b8")
            self.setFixedHeight(self.H_PROGRESS)
            self.setVisible(False)
            self.update()
            return
        try:
            self._pct = int(pct)
        except (TypeError, ValueError):
            self._pct = 0
        if color:
            try:
                self._color = QColor(color)
            except Exception:
                pass
        elif self._pct < 0:
            self._color = QColor("#94a3b8")
        if self._pct < 0:
            self._was_busy = True
            self.setFixedHeight(self.H_BUSY)
        else:
            self._was_busy = False
            self.setFixedHeight(self.H_PROGRESS)
        self.setVisible(True)
        self.raise_()
        self.update()

    def paintEvent(self, event):
        if not self.isVisible() or self.width() <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w = self.width()
        h = self.height()
        if self._pct < 0:
            p.fillRect(0, 0, w, h, self._color)
        else:
            p.fillRect(0, 0, w, h, self.TRACK_COLOR)
            if self._pct > 0:
                pct = max(0, min(100, self._pct))
                pw = max(2, int(w * pct / 100.0))
                pw = min(pw, w)
                p.fillRect(0, 0, pw, h, self._color)
        p.end()


class SideDropGuide(QWidget):
    """侧栏拖拽插入线：叠在两项缝上的虚线，不占布局。"""

    H = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SideDropGuide")
        self.setFixedHeight(self.H)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        try:
            theme.changed.connect(self.update)
        except Exception:
            pass

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        try:
            is_dark = bool(theme.is_dark)
        except Exception:
            is_dark = True
        color = QColor("#ffffff") if is_dark else QColor("#334155")
        pen = QPen(color, 2, Qt.CustomDashLine)
        pen.setDashPattern([3, 3])
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        y = self.height() // 2
        p.drawLine(8, y, max(9, self.width() - 8), y)
        p.end()


class _SideNavDragHandler(QObject):
    """侧栏导航按钮拖拽排序。

    超过阈值后：原位按钮变半透明仍占位，跟手的是半透明副本；
    插入虚线叠在落点缝上（不挤布局）。松手后再换序落盘。
    """

    _DRAG_THRESHOLD = 6  # 像素

    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw
        self._drag = None  # {key, btn, press, active, last}

    def install(self, btn):
        btn.installEventFilter(self)

    @staticmethod
    def _set_flag(btn, dragging):
        try:
            btn.setProperty("dragging", dragging)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        except Exception:
            pass

    def eventFilter(self, obj, event):
        try:
            t = event.type()
            if t == QEvent.MouseButtonPress:
                if event.button() == Qt.LeftButton:
                    key = str(obj.property("navKey") or "")
                    # 锁定项（系统总览等）只响应点击，不进入拖拽
                    if key in _SIDE_LOCKED_KEYS:
                        self._drag = None
                        return False
                    self._drag = {
                        "key": key,
                        "btn": obj,
                        "press": event.globalPos(),
                        "active": False,
                        "last_idx": None,
                    }
                return False
            if t == QEvent.MouseMove:
                d = self._drag
                if d is None or d["btn"] is not obj:
                    return False
                if d.get("key") in _SIDE_LOCKED_KEYS:
                    return False
                if not d["active"]:
                    if (event.globalPos() - d["press"]).manhattanLength() < self._DRAG_THRESHOLD:
                        return False
                    d["active"] = True
                    self._set_flag(obj, True)
                    try:
                        obj.setCursor(Qt.ClosedHandCursor)
                    except Exception:
                        pass
                    self.mw._begin_side_nav_drag(d["key"], obj, event.globalPos())
                gpos = event.globalPos()
                self.mw._follow_side_nav_drag(gpos)
                idx = self.mw._drop_index(d["key"], gpos)
                d["last_idx"] = idx
                self.mw._place_side_drop_guide(d["key"], idx)
                return True
            if t == QEvent.MouseButtonRelease:
                d = self._drag
                if d is None or d["btn"] is not obj:
                    return False
                active = d["active"]
                key = d.get("key")
                idx = d.get("last_idx")
                self._drag = None
                if active:
                    # 拖拽结束：吞掉 release，避免误触按钮点击切页
                    self._set_flag(obj, False)
                    try:
                        obj.setDown(False)
                        obj.setCursor(Qt.PointingHandCursor)
                    except Exception:
                        pass
                    self.mw._end_side_nav_drag()
                    if key and idx is not None:
                        self.mw._reorder_side(key, idx)
                    self.mw._persist_side_order()
                    return True
                return False
            if t == QEvent.MouseButtonDblClick:
                return False
            if t == QEvent.Wheel:
                # 菜单按钮会吞掉滚轮；转到侧栏慢速滚动
                try:
                    return bool(self.mw._apply_nav_wheel(event))
                except Exception:
                    return False
        except Exception:
            pass
        return False


class SideToggleSwitch(QAbstractButton):
    """侧栏高品质滑动开关：圆角轨道 + 白滑块 + 短动画。

    开态颜色与对应菜单图标一致（速存绿 / 截图青蓝）。
    API 兼容原 SideLed：setChecked / isChecked / clicked / toggled。
    支持 setFrozen：批处理等进行中灰化锁定，保留开关真实状态、禁止点击。
    """

    W, H = 36, 20
    _FROZEN_TIP = "批处理进行中 · 剪贴板自动下载已暂停"

    def __init__(self, on_color="#22C55E", parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._on_c0 = QColor(on_color)
        # 略深的渐变尾色
        self._on_c1 = QColor(on_color).darker(112)
        self._knob = 0.0  # 0=关 1=开
        self._frozen = False
        self._anim = QPropertyAnimation(self, b"knobPos", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def getKnobPos(self):
        return self._knob

    def setKnobPos(self, v):
        self._knob = max(0.0, min(1.0, float(v)))
        self.update()

    knobPos = pyqtProperty(float, fget=getKnobPos, fset=setKnobPos)

    def isFrozen(self) -> bool:
        return bool(getattr(self, "_frozen", False))

    def setFrozen(self, frozen: bool):
        """冻结：保留 isChecked 真实状态，灰化显示并拦截点击。"""
        frozen = bool(frozen)
        if bool(getattr(self, "_frozen", False)) == frozen:
            return
        self._frozen = frozen
        if frozen:
            self.setCursor(Qt.ForbiddenCursor)
            self.setToolTip(self._FROZEN_TIP)
        else:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip("")
        self.update()

    def setChecked(self, checked: bool):
        """外部同步状态时直接到位（不播动画，避免恢复偏好时抖动）。"""
        checked = bool(checked)
        self.blockSignals(True)
        super().setChecked(checked)
        self.blockSignals(False)
        self._anim.stop()
        self._knob = 1.0 if checked else 0.0
        self.update()

    def mousePressEvent(self, e):
        if getattr(self, "_frozen", False):
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        # 冻结中：吞掉点击，不切换
        if getattr(self, "_frozen", False):
            e.accept()
            return
        # 用户点击：切换 + 滑动动画 + 发 clicked（主窗口连的是 clicked）
        if (
            e.button() == Qt.LeftButton
            and self.isEnabled()
            and self.rect().contains(e.pos())
        ):
            new = not self.isChecked()
            self.blockSignals(True)
            super().setChecked(new)
            self.blockSignals(False)
            self._anim.stop()
            self._anim.setStartValue(self._knob)
            self._anim.setEndValue(1.0 if new else 0.0)
            self._anim.start()
            self.clicked.emit()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        t = self._knob
        frozen = bool(getattr(self, "_frozen", False))
        # 冻结：整体压暗，开态改用灰蓝而非品牌蓝，一眼能看出「暂停中」
        if frozen:
            p.setOpacity(0.58)

        # 轨道
        track = QRectF(1.0, 2.0, w - 2.0, h - 4.0)
        radius = track.height() / 2.0
        if t < 0.02:
            # 关：中性灰轨
            if theme.is_dark:
                base = QColor(255, 255, 255, 28)
                rim = QColor(255, 255, 255, 48)
            else:
                base = QColor(148, 163, 184, 90)
                rim = QColor(148, 163, 184, 140)
            if frozen:
                base = QColor(100, 116, 139, 55)
                rim = QColor(100, 116, 139, 90)
            p.setPen(QPen(rim, 1.0))
            p.setBrush(QBrush(base))
            p.drawRoundedRect(track, radius, radius)
        else:
            # 开：品牌色渐变轨（可与关态插值）；冻结时改为 slate 灰蓝
            g = QLinearGradient(track.topLeft(), track.topRight())
            if frozen:
                c0 = QColor("#64748B")
                c1 = QColor("#475569")
            else:
                c0 = QColor(self._on_c0)
                c1 = QColor(self._on_c1)
            if t < 1.0:
                # 未完全打开时略降饱和
                c0.setAlpha(int(80 + 175 * t))
                c1.setAlpha(int(80 + 175 * t))
            g.setColorAt(0, c0)
            g.setColorAt(1, c1)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(g))
            p.drawRoundedRect(track, radius, radius)
            # 内高光
            hi = QLinearGradient(track.topLeft(), QPointF(track.left(), track.center().y()))
            hi.setColorAt(0, QColor(255, 255, 255, int((30 if frozen else 50) * t)))
            hi.setColorAt(1, QColor(255, 255, 255, 0))
            p.setBrush(QBrush(hi))
            p.drawRoundedRect(track.adjusted(0.5, 0.5, -0.5, -track.height() * 0.35), radius, radius)

        # 滑块
        pad = 3.0
        kn = h - 2.0 * pad
        x0 = pad
        x1 = w - pad - kn
        kx = x0 + (x1 - x0) * t
        knob = QRectF(kx, pad, kn, kn)

        # 滑块阴影
        sh = QRadialGradient(knob.center() + QPointF(0, 1), kn * 0.7)
        sh.setColorAt(0, QColor(0, 0, 0, 50))
        sh.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(sh))
        p.drawEllipse(knob.adjusted(-1, 0, 1, 2))

        # 滑块本体（冻结时略发灰）
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
        # 滑块顶光
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(255, 255, 255, 100 if frozen else 160)))
        p.drawEllipse(QRectF(kx + kn * 0.22, pad + kn * 0.15, kn * 0.45, kn * 0.28))

        p.end()

    def sizeHint(self):
        return QSize(self.W, self.H)


class SideRecordDot(QWidget):
    """侧栏「区域录屏」状态圆标：空心小圆；录制中圆心红点，结束红点消失。"""

    SIZE = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self._recording = False
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_recording(self, on: bool):
        on = bool(on)
        if self._recording == on:
            return
        self._recording = on
        self.update()

    def is_recording(self) -> bool:
        return self._recording

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        s = float(min(self.width(), self.height()))
        m = 0.5
        ring = QRectF(m, m, s - 2 * m, s - 2 * m)
        # 外圈：录制中用更醒目的红，空闲时中性描边
        if self._recording:
            rim = QColor("#F87171") if theme.is_dark else QColor("#EF4444")
            p.setPen(QPen(rim, 1.4))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(ring)
            # 圆心红点
            inset = s * 0.28
            core = ring.adjusted(inset, inset, -inset, -inset)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor("#EF4444")))
            p.drawEllipse(core)
        else:
            if theme.is_dark:
                rim = QColor(255, 255, 255, 70)
            else:
                rim = QColor(100, 116, 139, 120)
            p.setPen(QPen(rim, 1.2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(ring)
        p.end()

    def sizeHint(self):
        return QSize(self.SIZE, self.SIZE)


def side_count_badge_font() -> QFont:
    """预N / 批N 共用字号，避免量宽和实绘各走一套字体。"""
    f = QFont()
    f.setPixelSize(11)
    f.setWeight(QFont.Bold)
    return f


def side_count_label(prefix: str, n: int) -> str:
    """预NN / 批NN：汉字和数字紧贴，中间没有空格。"""
    prefix = "" if prefix is None else str(prefix)
    try:
        n = max(0, int(n or 0))
    except (TypeError, ValueError):
        n = 0
    if n < 100:
        return f"{prefix}{n:02d}"
    return f"{prefix}{n}"


def side_count_info_width(digit_slots: int = 2) -> int:
    """按「预88 / 批88」整词量宽，汉字与数字之间不加空档。"""
    fm = QFontMetrics(side_count_badge_font())
    slots = max(2, int(digit_slots or 2))
    sample = "8" * slots
    return max(
        int(fm.horizontalAdvance("预" + sample)),
        int(fm.horizontalAdvance("批" + sample)),
    )


def side_count_badge_width(n: int, prefix: str = "预") -> int:
    """页签数字槽：无前缀时按数字本身；有前缀时走汉字+数字列。"""
    try:
        n = max(0, int(n))
    except (TypeError, ValueError):
        n = 0
    prefix = "" if prefix is None else str(prefix)
    if n <= 0:
        return 0
    if prefix:
        return side_count_info_width(max(2, len(str(n))))
    fm = QFontMetrics(side_count_badge_font())
    return max(12, int(fm.horizontalAdvance(str(n))))


class SidePreDlBadge(QLabel):
    """侧栏条数信息：无外框。左汉字、右数字；0 条隐藏。

    prefix=「预」预下载 / 「批」批处理。与同伴共用数字列宽，上下叠放对齐。
    """

    H = 15
    GAP = 0

    def __init__(
        self,
        parent=None,
        prefix="预",
        slot_w: int = 0,
        accent_dark=None,
        accent_light=None,
        object_name="SidePreDlBadge",
    ):
        super().__init__(parent)
        self.setObjectName(object_name or "SidePreDlBadge")
        self.setAlignment(Qt.AlignCenter)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFont(side_count_badge_font())
        self._prefix = "" if prefix is None else str(prefix)
        self._accent_dark = (accent_dark or "").strip() or "#60a5fa"
        self._accent_light = (accent_light or "").strip() or "#3b82f6"
        try:
            self._slot_w = max(0, int(slot_w or 0))
        except (TypeError, ValueError):
            self._slot_w = 0
        self._count = 0
        self._digit_slots = 2
        self.hide()
        self.refresh_theme()

    def set_count(self, n: int, digit_slots: int = 0):
        try:
            n = max(0, int(n))
        except (TypeError, ValueError):
            n = 0
        self._count = n
        if n <= 0:
            self._digit_slots = 2
            self.setText("")
            self.setFixedSize(0, 0)
            self.hide()
            return
        try:
            slots = max(2, int(digit_slots or 0), len(str(n)))
        except (TypeError, ValueError):
            slots = max(2, len(str(n)))
        self._digit_slots = slots
        self.setText("")
        w = self._slot_w if self._slot_w > 0 else side_count_info_width(slots)
        self.setFixedSize(w, self.H)
        self.refresh_theme()
        self.show()
        self.raise_()

    def set_digit_slots(self, slots: int):
        if self._count <= 0:
            return
        try:
            slots = max(2, int(slots or 2), len(str(self._count)))
        except (TypeError, ValueError):
            slots = max(2, len(str(self._count)))
        if slots == self._digit_slots and self.width() > 0:
            return
        self._digit_slots = slots
        w = self._slot_w if self._slot_w > 0 else side_count_info_width(slots)
        self.setFixedSize(w, self.H)
        self.update()

    def count(self) -> int:
        return int(self._count or 0)

    def _accent(self) -> str:
        try:
            is_dark = bool(theme.is_dark)
        except Exception:
            is_dark = True
        if is_dark:
            return self._accent_dark or "#60a5fa"
        return self._accent_light or "#3b82f6"

    def refresh_theme(self, *_):
        name = self.objectName() or "SidePreDlBadge"
        accent = self._accent()
        self.setStyleSheet(
            f"QLabel#{name}{{"
            f"color:{accent};font-size:11px;font-weight:700;"
            f"background:transparent;border:none;padding:0;}}"
        )
        self.update()

    def paintEvent(self, event):
        del event
        if self._count <= 0 or not self._prefix:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        f = side_count_badge_font()
        p.setFont(f)
        p.setPen(QColor(self._accent()))
        p.drawText(
            self.rect(),
            Qt.AlignVCenter | Qt.AlignLeft,
            side_count_label(self._prefix, self._count),
        )
        p.end()


class TightPreDlTabBar(QTabBar):
    """六个下载子页统一：左「防」或原图标 + 固定标题 + 预/批NN。

    预下载与批处理互斥，只画一个；N=0 不画「预00」，槽位宽度仍占着。
    """

    PAD_L = 12
    PAD_R = 14
    ICON_GAP = 8
    TEXT_BADGE_GAP = 6
    SLOT_W = 24
    BADGE_H = 18
    BADGE_MIN_W = 18
    BATCH_GAP = 4
    BATCH_MIN_W = 28
    LEFT_BTN_W = 16  # 「防」开关
    TITLE_LIFT = 1  # 标题相对「防」/「批预」单独上移

    def __init__(self, parent=None):
        super().__init__(parent)
        self._counts = []
        self._batch_counts = []
        try:
            self.setElideMode(Qt.ElideNone)
            self.setExpanding(False)
            self.setDrawBase(False)
        except Exception:
            pass

    def set_pre_dl_counts(self, counts):
        seq = []
        for n in list(counts or ()):
            try:
                seq.append(max(0, int(n or 0)))
            except (TypeError, ValueError):
                seq.append(0)
        if seq != self._counts:
            self._counts = seq
            self.update()

    def set_batch_counts(self, counts):
        seq = []
        for n in list(counts or ()):
            try:
                seq.append(max(0, int(n or 0)))
            except (TypeError, ValueError):
                seq.append(0)
        if seq != self._batch_counts:
            self._batch_counts = seq
            self.update()

    def _refresh_tab_layout(self):
        """QTabBar 不会因自定义 sizeHint 依赖项变化自动重排，必须捅一下。"""
        for i in range(self.count()):
            try:
                self.setTabText(i, self.tabText(i))
            except Exception:
                pass
        try:
            self.updateGeometry()
        except Exception:
            pass
        par = self.parentWidget()
        if par is not None:
            try:
                par.updateGeometry()
            except Exception:
                pass
        self.update()
        self._place_left_buttons()

    def _count_at(self, index: int) -> int:
        if 0 <= index < len(self._counts):
            return int(self._counts[index] or 0)
        return 0

    def _batch_at(self, index: int) -> int:
        if 0 <= index < len(self._batch_counts):
            return int(self._batch_counts[index] or 0)
        return 0

    def _title_font(self, selected: bool = True) -> QFont:
        # 选中/未选中同一字重（600）。按态加粗会让未选中末字被裁。
        del selected
        f = QFont(self.font())
        f.setWeight(QFont.DemiBold)
        return f

    def _text_w(self, index: int) -> int:
        text = self.tabText(index) or ""
        fm = QFontMetrics(self._title_font())
        adv = int(fm.horizontalAdvance(text))
        box = int(fm.boundingRect(text).width())
        return max(adv, box)

    def _left_btn(self, index: int):
        try:
            return self.tabButton(index, self.LeftSide)
        except Exception:
            return None

    def _place_left_buttons(self):
        """页签左侧控件对齐到原图标位（PAD_L + 垂直居中）。"""
        for i in range(self.count()):
            btn = self._left_btn(i)
            if btn is None:
                continue
            r = self.tabRect(i)
            if not r.isValid() or r.isEmpty():
                continue
            try:
                x = r.x() + self.PAD_L
                y = r.y() + max(0, (r.height() - btn.height()) // 2)
                btn.move(x, y)
                btn.raise_()
            except Exception:
                pass

    def tabLayoutChange(self):
        super().tabLayoutChange()
        self._place_left_buttons()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_left_buttons()

    def _badge_font(self) -> QFont:
        return side_count_badge_font()

    def _info_slot_w(self) -> int:
        return side_count_info_width(2)

    def _title_slot_w(self, index: int) -> int:
        return self._text_w(index)

    def _left_span(self, index: int) -> int:
        """有「防」开关用 16px；否则用原图标宽。"""
        if self._left_btn(index) is not None:
            return self.LEFT_BTN_W + self.ICON_GAP
        try:
            if not self.tabIcon(index).isNull():
                return int(self.iconSize().width() or 20) + self.ICON_GAP
        except Exception:
            pass
        return 0

    def _count_num_text(self, n: int) -> str:
        try:
            n = max(0, int(n or 0))
        except (TypeError, ValueError):
            n = 0
        if n < 100:
            return f"{n:02d}"
        return str(n)

    def _active_count(self, index: int):
        """批优先，否则预。返回 (prefix, n)。"""
        n_batch = self._batch_at(index)
        if n_batch > 0:
            return "批", n_batch
        return "预", self._count_at(index)

    def tabSizeHint(self, index):
        base = super().tabSizeHint(index)
        h = base.height() if base.isValid() else 28
        w = (
            self.PAD_L + self._left_span(index) + self._title_slot_w(index)
            + self.TEXT_BADGE_GAP + self._info_slot_w() + self.PAD_R
        )
        return QSize(max(8, w), h)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        opt = QStyleOptionTab()
        for i in range(self.count()):
            self.initStyleOption(opt, i)
            r = self.tabRect(i)
            if event is not None and not r.intersects(event.rect()):
                continue
            opt.text = ""
            opt.icon = QIcon()
            self.style().drawControl(QStyle.CE_TabBarTabShape, opt, p, self)

            p.save()
            p.setClipRect(r)

            x = float(r.x() + self.PAD_L)
            cy = r.y() + r.height() / 2.0
            left_btn = self._left_btn(i)
            if left_btn is not None:
                x = float(r.x() + self.PAD_L + self.LEFT_BTN_W + self.ICON_GAP)
            else:
                icon = self.tabIcon(i)
                if not icon.isNull():
                    iw = int(self.iconSize().width() or 20)
                    ih = int(self.iconSize().height() or 20)
                    ir = QRect(int(x), int(round(cy - ih / 2.0)), iw, ih)
                    icon.paint(p, ir, Qt.AlignCenter)
                    x += iw + self.ICON_GAP

            selected = i == self.currentIndex()
            f = self._title_font(selected)
            p.setFont(f)
            color = self.tabTextColor(i)
            if not color.isValid():
                color = QColor(self.palette().color(self.foregroundRole()))
            p.setPen(color)
            text = self.tabText(i) or ""
            fm = QFontMetrics(f)
            th = int(fm.height())
            title_w = self._title_slot_w(i)
            info_w = self._info_slot_w()
            prefix, n_show = self._active_count(i)
            tr = QRect(int(x), int(round(cy - th / 2.0)) - int(self.TITLE_LIFT), title_w, th)
            p.save()
            p.setClipRect(tr)
            p.drawText(tr, Qt.AlignVCenter | Qt.AlignLeft | Qt.TextSingleLine, text)
            p.restore()
            self._paint_count_text(
                p,
                QRect(
                    int(x) + title_w + self.TEXT_BADGE_GAP,
                    int(round(cy - self.BADGE_H / 2.0)),
                    info_w,
                    int(self.BADGE_H),
                ),
                prefix,
                n_show,
            )
            p.restore()
        p.end()

    def _paint_count_text(self, p, rect, prefix, n):
        """页签条数：无外框。N=0 不画（槽位仍占宽）；有数才画 预NN / 批NN。"""
        try:
            n = max(0, int(n or 0))
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            return
        try:
            dark = bool(theme.is_dark)
        except Exception:
            dark = True
        accent = QColor("#60a5fa" if dark else "#3b82f6")
        p.save()
        p.setRenderHint(QPainter.TextAntialiasing, True)
        f = self._badge_font()
        p.setFont(f)
        p.setPen(QPen(accent))
        prefix = "" if prefix is None else str(prefix)
        text = side_count_label(prefix, n) if prefix else self._count_num_text(n)
        p.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, text)
        p.restore()


def install_tight_pre_dl_tab_bar(tabs):
    """在 addTab 之前换上收紧页签条。"""
    bar = TightPreDlTabBar()
    if tabs is not None:
        tabs.setTabBar(bar)
    return bar


class SideNavScrollArea(QScrollArea):
    """侧栏菜单滚动区：滚轮不走 Qt 默认「一次三行」。"""

    def wheelEvent(self, e):
        w = self.window()
        fn = getattr(w, "_apply_nav_wheel", None)
        if callable(fn) and fn(e):
            e.accept()
            return
        e.accept()


class SideNavScrollBtn(QAbstractButton):
    """侧栏导航 20px 高的上/下翻页条，替代该区域垂直滚动条。"""

    H = 20

    def __init__(self, direction: str, parent=None):
        super().__init__(parent)
        self._dir = "up" if direction == "up" else "down"
        self.setObjectName("SideNavScrollBtn")
        self.setFixedHeight(self.H)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAutoRepeat(True)
        self.setAutoRepeatDelay(280)
        self.setAutoRepeatInterval(70)
        self.setToolTip("向上滚动菜单" if self._dir == "up" else "向下滚动菜单")
        try:
            theme.changed.connect(self.update)
        except Exception:
            pass

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, event):
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        if w <= 2 or h <= 2:
            p.end()
            return
        try:
            is_dark = bool(theme.is_dark)
        except Exception:
            is_dark = True
        enabled = self.isEnabled()
        hover = enabled and self.underMouse()
        pressed = enabled and self.isDown()
        if pressed:
            bg = QColor(99, 102, 241, 70 if is_dark else 50)
        elif hover:
            bg = QColor(255, 255, 255, 28) if is_dark else QColor(100, 116, 139, 36)
        else:
            bg = QColor(0, 0, 0, 0)
        if bg.alpha() > 0:
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(bg))
            p.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), 4.0, 4.0)
        if not enabled:
            color = QColor("#3d4a66") if is_dark else QColor("#cbd5e1")
        elif hover:
            color = QColor("#e6edff") if is_dark else QColor("#0f172a")
        else:
            color = QColor("#9fb0d7") if is_dark else QColor("#64748b")
        cx = w / 2.0
        cy = h / 2.0
        if self._dir == "up":
            pts = QPolygonF([
                QPointF(cx - 6.5, cy + 2.8),
                QPointF(cx, cy - 3.2),
                QPointF(cx + 6.5, cy + 2.8),
            ])
        else:
            pts = QPolygonF([
                QPointF(cx - 6.5, cy - 2.8),
                QPointF(cx, cy + 3.2),
                QPointF(cx + 6.5, cy - 2.8),
            ])
        pen = QPen(color, 2.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(pts)
        p.end()


class SidePreDlSlot(QAbstractButton):
    """正方形单字开关。

    开：描边方框 + 单字；关：灰色空框。点击切换，API 对齐 SideToggleSwitch。
    配置按钮右侧用「预」；图集页签防重用「防」。
    """

    W = 16
    H = 16
    RADIUS = 2.0
    _FROZEN_TIP = "批处理进行中 · 预下载已暂停"

    def __init__(self, parent=None, glyph="预", on_color=""):
        super().__init__(parent)
        self._glyph = (glyph or "预").strip()[:1] or "预"
        self._on_color = (on_color or "").strip()
        self.setCheckable(True)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self._frozen = False
        try:
            theme.changed.connect(self.update)
        except Exception:
            pass

    def isFrozen(self) -> bool:
        return bool(getattr(self, "_frozen", False))

    def setFrozen(self, frozen: bool):
        frozen = bool(frozen)
        if bool(getattr(self, "_frozen", False)) == frozen:
            return
        self._frozen = frozen
        if frozen:
            self.setCursor(Qt.ForbiddenCursor)
            self.setToolTip(self._FROZEN_TIP)
        else:
            self.setCursor(Qt.PointingHandCursor)
        self.update()

    def setChecked(self, checked: bool):
        self.blockSignals(True)
        super().setChecked(bool(checked))
        self.blockSignals(False)
        self.update()

    def mousePressEvent(self, e):
        if getattr(self, "_frozen", False):
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if getattr(self, "_frozen", False):
            e.accept()
            return
        if (
            e.button() == Qt.LeftButton
            and self.isEnabled()
            and self.rect().contains(e.pos())
        ):
            new = not self.isChecked()
            self.blockSignals(True)
            super().setChecked(new)
            self.blockSignals(False)
            self.update()
            self.clicked.emit()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if getattr(self, "_frozen", False):
            p.setOpacity(0.58)
        w, h = float(self.width()), float(self.height())
        rect = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        radius = float(self.RADIUS)
        try:
            dark = bool(theme.is_dark)
        except Exception:
            dark = True
        on = bool(self.isChecked())
        if on:
            custom = QColor(self._on_color) if self._on_color else QColor()
            if custom.isValid():
                accent = QColor(custom).lighter(118) if dark else QColor(custom)
                bg = QColor(15, 23, 42, 184) if dark else QColor(255, 255, 255, 235)
            elif dark:
                bg = QColor(15, 23, 42, 184)
                accent = QColor("#60a5fa")
            else:
                bg = QColor(255, 255, 255, 235)
                accent = QColor("#3b82f6")
            p.setPen(QPen(accent, 1.0))
            p.setBrush(QBrush(bg))
            p.drawRoundedRect(rect, radius, radius)
            p.setPen(QPen(accent))
            f = QFont(self.font())
            f.setPixelSize(11)
            f.setWeight(QFont.Bold)
            p.setFont(f)
            p.drawText(rect, Qt.AlignCenter, self._glyph)
        else:
            if dark:
                rim = QColor(148, 163, 184, 150)
                fill = QColor(15, 23, 42, 90)
            else:
                rim = QColor(148, 163, 184, 180)
                fill = QColor(148, 163, 184, 55)
            p.setPen(QPen(rim, 1.0))
            p.setBrush(QBrush(fill))
            p.drawRoundedRect(rect, radius, radius)
        p.end()

    def sizeHint(self):
        return QSize(self.W, self.H)


class TabPreDlBadgeHost(QObject):
    """把各子页预下载 / 批处理条数交给 TightPreDlTabBar 自己画。"""

    def __init__(self, tabs, specs, parent=None):
        super().__init__(parent or tabs)
        self._tabs = tabs
        self._specs = list(specs or ())
        self._counts = {k: 0 for k, _ in self._specs}
        self._batch_counts = {k: 0 for k, _ in self._specs}
        self._bar = tabs.tabBar() if tabs is not None else None

    def set_counts(self, counts):
        counts = counts or {}
        for key, _base in self._specs:
            try:
                n = max(0, int(counts.get(key, 0) or 0))
            except (TypeError, ValueError):
                n = 0
            self._counts[key] = n
        tabs = self._tabs
        for i, (key, base) in enumerate(self._specs):
            if tabs is not None and i < tabs.count() and tabs.tabText(i) != base:
                tabs.setTabText(i, base)
        bar = self._bar
        if bar is not None and hasattr(bar, "set_pre_dl_counts"):
            bar.set_pre_dl_counts([self._counts.get(k, 0) for k, _ in self._specs])

    def set_batch_counts(self, counts):
        counts = counts or {}
        for key, _base in self._specs:
            try:
                n = max(0, int(counts.get(key, 0) or 0))
            except (TypeError, ValueError):
                n = 0
            self._batch_counts[key] = n
        bar = self._bar
        if bar is not None and hasattr(bar, "set_batch_counts"):
            bar.set_batch_counts([self._batch_counts.get(k, 0) for k, _ in self._specs])

    def refresh_theme(self):
        if self._bar is not None:
            self._bar.update()

    def reposition(self):
        if self._bar is not None:
            self._bar.update()


class _CurrentOnlyStack(QStackedWidget):
    """v9.9.6 关键修复：普通 QStackedWidget 的 sizeHint()/minimumSizeHint()
    默认是"所有已加入的子页面里最大的那个"，不是只看当前正显示的这一页——这是
    Qt 的一个经典陷阱。也就是说，只要某一个页面（不管现在是不是正显示它）曾经
    被计算出过一个偏大的最小尺寸，整个主窗口就会被这个"历史最大值"钉住，之后
    切回任何别的页面都缩不回去，表现就是"窗口莫名其妙缩不小、好像有什么参数
    把最小高度锁住了"——而且这个锁定和你当前在哪个页面完全无关，很难对上号。
    这里重写这两个方法，只以 currentWidget() 的尺寸为准，其它没在显示的页面
    再怎么样都不会连累主窗口。这是从架构上兜底：就算以后某个页面又冒出类似
    "内容驱动的最小尺寸不稳定"的问题，影响范围也只会局限在那个页面自己被
    显示的时候，不会污染整个程序的窗口尺寸。"""

    def sizeHint(self):
        w = self.currentWidget()
        return w.sizeHint() if w is not None else super().sizeHint()

    def minimumSizeHint(self):
        w = self.currentWidget()
        return w.minimumSizeHint() if w is not None else super().minimumSizeHint()


class _ClockIdleWatcher(QObject):
    """捕获键鼠活动，重置电子钟空闲计时（默认 60 秒，提示词等 5 分钟）。"""

    _WATCH = frozenset((
        QEvent.MouseMove,
        QEvent.MouseButtonPress,
        QEvent.MouseButtonDblClick,
        QEvent.KeyPress,
        QEvent.Wheel,
    ))

    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw

    def eventFilter(self, obj, event):
        try:
            if event.type() in self._WATCH:
                self.mw._note_user_activity()
        except Exception:
            pass
        return False


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("AppRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)

        # 默认深色；浅色仍可通过侧栏 ☀/🌙 切换
        self.is_light_theme = False
        # 界面缩放由启动时 QT_SCALE_FACTOR 整窗放大（见 mainv916）；此处只记偏好值
        self._ui_scale = self._read_active_qt_scale()
        # 内部 sp()/写死像素保持设计稿 1×，避免与 QT_SCALE_FACTOR 叠乘
        try:
            theme.set_scale(1.0, quiet=True)
        except Exception:
            pass
        self._apply_qss('app.qss')
        theme.set_theme("dark")

        self.setWindowIcon(QIcon(os.path.join(ASSETS_DIR, 'star.ico')))
        self.setWindowTitle("桌面助手 v9.16")
        # 主窗最小 960×776（与 user.txt 当前一致）；不设最大，可自由放大
        self.setMinimumSize(_WIN_MIN_W, _WIN_MIN_H)
        self.resize(_WIN_MIN_W, _WIN_MIN_H)
        self._restoring_geometry = False
        QTimer.singleShot(0, lambda: self._set_titlebar_theme(True))
        self._apply_app_font()

        # 页面堆栈
        self.stack = _CurrentOnlyStack()
        self.stack.setAttribute(Qt.WA_StyledBackground, True)
        self.stack.setObjectName("StackArea")

        # ======= 根布局：侧栏 + 主内容（扁平浮动卡片，无顶部通栏） =======
        outer = QHBoxLayout(self)
        # 最小 960×776 时上下边距略收，给侧栏导航与内容区留余量
        outer.setContentsMargins(8, 14, 14, 14)
        outer.setSpacing(8)

        # ── 左侧侧栏（宽 190）────────────────────────────────
        self.side_wrap = QWidget()
        self.side_wrap.setObjectName("SideBar")
        self.side_wrap.setFixedWidth(sp(_SIDE_W))
        self.side_wrap.setAttribute(Qt.WA_StyledBackground, True)

        side = QVBoxLayout(self.side_wrap)
        # 右 4px，让菜单更贴近主内容区；底边 0，让「系统总览 / 明·暗」底边
        # 与右侧内容卡底边齐平（原 10 会把底栏垫高约半个字）
        side.setContentsMargins(8, 12, 4, 0)
        side.setSpacing(3)

        # 品牌区：左侧 star.ico（高度约等于标题+副标题两行）
        brand = QWidget()
        brand.setObjectName("SideBrand")
        brand.setAttribute(Qt.WA_StyledBackground, True)
        brand_l = QHBoxLayout(brand)
        brand_l.setContentsMargins(0, 0, 0, 12)
        brand_l.setSpacing(10)
        self.logo = QLabel()
        self.logo.setObjectName("SideLogo")
        self.logo.setAlignment(Qt.AlignCenter)
        self._star_pix_src = QPixmap(os.path.join(ASSETS_DIR, "star.ico"))
        self._apply_logo_size()
        brand_l.addWidget(self.logo, 0, Qt.AlignVCenter)
        brand_txt = QVBoxLayout()
        brand_txt.setContentsMargins(0, 0, 0, 0)
        brand_txt.setSpacing(0)
        self.lbl_brand_title = QLabel("桌面助手 v9.16")
        self.lbl_brand_title.setObjectName("SideBrandTitle")
        self.lbl_brand_sub = QLabel("给 AI 人的口袋瑞士军刀")
        self.lbl_brand_sub.setObjectName("SideBrandSub")
        brand_txt.addWidget(self.lbl_brand_title)
        brand_txt.addWidget(self.lbl_brand_sub)
        brand_l.addLayout(brand_txt, 1)
        side.addWidget(brand)

        # 导航按钮：彩色矢量图标（圆角色底 + 白符号）+ 文案；固定 icon 尺寸，不挤字
        def _nav(key: str, label: str) -> QPushButton:
            b = QPushButton(f" {label}")
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setProperty("kind", "side")
            b.setProperty("navKey", key)
            b.setProperty("active", False)
            b.setCursor(Qt.PointingHandCursor)
            b.setFlat(True)
            # 不要焦点：点击后 Qt 会把按钮 raise 到浮动 LED 之上，导致勾选框被盖住
            b.setFocusPolicy(Qt.NoFocus)
            b.setStyleSheet("")  # 走 QSS，不吃全局按钮描边
            self._apply_side_nav_metrics(b, key)
            return b

        self.btn_fast         = _nav("fast", "速存图文") if self._page_enabled("fast") else None
        self.btn_paste        = _nav("paste", "粘贴助手") if self._page_enabled("paste") else None
        self.btn_img_proc     = _nav("img_proc", "图片处理") if self._page_enabled("img_proc") else None
        self.btn_voice        = _nav("voice", "语音编辑") if self._page_enabled("voice") else None
        self.btn_voice_clone  = _nav("voice_clone", "语音克隆") if self._page_enabled("voice_clone") else None
        self.btn_game_assist  = _nav("game_assist", "游戏助手") if self._page_enabled("game_assist") else None
        self.btn_prompt       = _nav("prompt", "提示词") if self._page_enabled("prompt") else None
        self.btn_points       = _nav("points", "积分计算") if self._page_enabled("points") else None
        self.btn_shot         = _nav("shot", "截图工具") if self._page_enabled("shot") else None
        self.btn_douyin       = _nav("video", "视频下载") if self._page_enabled("video") else None
        self.btn_gallery      = _nav("gallery", "图集下载") if self._page_enabled("gallery") else None
        self.btn_region_rec   = _nav("region_rec", "区域录屏") if self._page_enabled("region_rec") else None
        # 工具 · 小偏门（原「实用工具」合页拆成三个单页）
        self.btn_ratio        = _nav("ratio", "比例计算") if self._page_enabled("ratio") else None
        self.btn_dir_link     = _nav("dir_link", "目录映射") if self._page_enabled("dir_link") else None
        self.btn_tz_fx        = _nav("tz_fx", "时区汇率") if self._page_enabled("tz_fx") else None

        # 侧栏顺序：置顶常用 → 助手 → 工具·小偏门
        self.side_btns = [
            b for b in (
                getattr(self, "btn_fast", None),
                getattr(self, "btn_douyin", None), getattr(self, "btn_gallery", None),
                getattr(self, "btn_paste", None), getattr(self, "btn_img_proc", None),
                getattr(self, "btn_voice", None), getattr(self, "btn_voice_clone", None),
                getattr(self, "btn_game_assist", None),
                getattr(self, "btn_prompt", None),
                getattr(self, "btn_points", None), getattr(self, "btn_shot", None),
                getattr(self, "btn_region_rec", None),
                getattr(self, "btn_ratio", None), getattr(self, "btn_dir_link", None),
                getattr(self, "btn_tz_fx", None),
            )
            if b is not None
        ]

        # 侧栏开关（浮动叠在菜单右侧）：品质滑动开关，色系对齐菜单图标
        # 速存绿 #22C55E · 截图/预下载青蓝 #06B6D4（与 side_nav_icons 一致）
        # 视频/图集「自动下载」开关已移除 → 改为页签条数 + 空闲自动开跑
        self.led_fast = SideToggleSwitch(on_color="#22C55E", parent=self.side_wrap) if self._page_enabled("fast") else None
        self.led_shot = SideToggleSwitch(on_color="#06B6D4", parent=self.side_wrap) if self._page_enabled("shot") else None
        self.led_pre_dl = SidePreDlSlot(parent=self.side_wrap)
        self.led_pre_dl.setToolTip(
            "预下载\n"
            "关：当前图集/视频下完后停止，不再开下一条\n"
            "开：空闲时按队列自动处理（打开后先等 3 秒）\n"
            "与「系统总览 → 预下载」开关同步"
        )
        self.led_video_auto = None
        self.led_gallery_auto = None
        # 视频/图集右侧「预N」角标（0 条隐藏）
        self.badge_pre_video = (
            SidePreDlBadge(parent=self.side_wrap, prefix="预")
            if self._page_enabled("video") else None
        )
        self.badge_pre_gallery = (
            SidePreDlBadge(parent=self.side_wrap, prefix="预")
            if self._page_enabled("gallery") else None
        )
        # 批处理「批N」：样式同「预N」，蓝色；有剩余才显示
        self.badge_batch_video = (
            SidePreDlBadge(
                parent=self.side_wrap, prefix="批",
                object_name="SideBatchBadge",
            )
            if self._page_enabled("video") else None
        )
        self.badge_batch_gallery = (
            SidePreDlBadge(
                parent=self.side_wrap, prefix="批",
                object_name="SideBatchBadge",
            )
            if self._page_enabled("gallery") else None
        )
        for _bb in (self.badge_batch_video, self.badge_batch_gallery):
            if _bb is not None:
                _bb.setToolTip("批处理剩余条数")
        # 「区域录屏」状态圆标：录制中圆心红点，结束后消失（空心圆保留）
        self.led_region_rec = SideRecordDot(parent=self.side_wrap) if self._page_enabled("region_rec") else None

        # 右侧给开关留空（速存 / 截图）
        for _b, _k in (
            (getattr(self, "btn_fast", None), "fast"),
            (getattr(self, "btn_shot", None), "shot"),
        ):
            if _b is not None and self._page_enabled(_k):
                _b.setProperty("hasLed", True)
                _b.style().unpolish(_b)
                _b.style().polish(_b)
        # 「预」开关叠在底栏「系统总览」右侧
        # 视频/图集：右侧给「预N」角标留空
        for _b in (getattr(self, "btn_douyin", None), getattr(self, "btn_gallery", None)):
            if _b is not None:
                _b.setProperty("hasPreBadge", True)
                _b.style().unpolish(_b)
                _b.style().polish(_b)
        # 区域录屏圆标较小，只略增右侧内边距
        if self.btn_region_rec is not None:
            self.btn_region_rec.setProperty("hasRecDot", True)
            self.btn_region_rec.style().unpolish(self.btn_region_rec)
            self.btn_region_rec.style().polish(self.btn_region_rec)

        # ── 导航列表：统一滚动容器（支持拖拽排序）────────────────────────
        # 「视频下载」底边 2px 线：叠在按钮下边线上（不占 layout 空隙）
        self.video_side_prog = SideProgressLine(self.side_wrap) if self._page_enabled("video") else None
        # 「图集下载」底边 2px 线：叠在按钮下边线上（不占 layout 空隙）
        self.gallery_side_prog = SideProgressLine(self.side_wrap) if self._page_enabled("gallery") else None

        self.nav_scroll = SideNavScrollArea()
        self.nav_scroll.setObjectName("SideNavScroll")
        self.nav_scroll.setFrameShape(QFrame.NoFrame)
        self.nav_scroll.setWidgetResizable(True)
        # 上下 20px 按钮替代本区垂直滚动条
        self.nav_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav_scroll.setStyleSheet(
            "QScrollArea#SideNavScroll{background:transparent;border:none;}"
            "QScrollArea#SideNavScroll > QWidget > QWidget{background:transparent;}"
        )
        self.nav_container = QWidget()
        self.nav_container.setObjectName("SideNavContainer")
        self.nav_layout = QVBoxLayout(self.nav_container)
        self.nav_layout.setContentsMargins(0, 0, 0, 0)
        self.nav_layout.setSpacing(3)
        self.nav_scroll.setWidget(self.nav_container)
        self.nav_scroll.verticalScrollBar().valueChanged.connect(self._reposition_leds)
        self.nav_scroll.verticalScrollBar().valueChanged.connect(self._sync_nav_scroll_btns)
        self.nav_scroll.verticalScrollBar().rangeChanged.connect(
            lambda *_: self._sync_nav_scroll_btns()
        )
        self.nav_scroll.installEventFilter(self)
        try:
            self.nav_scroll.viewport().installEventFilter(self)
        except Exception:
            pass
        try:
            self.nav_scroll.verticalScrollBar().setSingleStep(_NAV_WHEEL_STEP)
            self.nav_scroll.verticalScrollBar().installEventFilter(self)
        except Exception:
            pass
        self.nav_container.installEventFilter(self)
        self._nav_wheel_last_t = 0.0
        # 菜单上的开关/角标/进度线挂到视口里，跟着按钮一起被裁切
        self._reparent_nav_overlays()

        self.btn_nav_up = SideNavScrollBtn("up", self.side_wrap)
        self.btn_nav_down = SideNavScrollBtn("down", self.side_wrap)
        self.btn_nav_up.clicked.connect(lambda: self._nudge_nav_scroll(-1))
        self.btn_nav_down.clicked.connect(lambda: self._nudge_nav_scroll(1))
        self.btn_nav_up.installEventFilter(self)
        self.btn_nav_down.installEventFilter(self)

        side.addWidget(self.btn_nav_up)
        side.addWidget(self.nav_scroll, 1)
        side.addWidget(self.btn_nav_down)

        self._build_side_headers()
        try:
            theme.changed.connect(self._restyle_side_headers)
        except Exception:
            pass
        self._side_order = self._default_side_order()
        self._normalize_locked_side_order()
        self.side_btns = [b for b in (self._btn_for_key(k) for k in self._side_order) if b is not None]
        self._side_drag = _SideNavDragHandler(self)
        for b in self.side_btns:
            if b is None:
                continue
            # 锁定项仍安装 filter（用于拦截拖拽），但不会进入拖拽态
            self._side_drag.install(b)
        self._rebuild_nav()
        self._style_sidebar_extras()
        self._sync_nav_scroll_btns()

        # 底栏：系统总览 + 「预」开关 + 主题切换 —— 固定在侧栏底部，不参与拖拽排序
        foot = QHBoxLayout()
        foot.setContentsMargins(0, 4, 0, 0)
        foot.setSpacing(6)
        self.btn_overview = QPushButton(" 系统总览")
        self.btn_overview.setObjectName("SideFootBtn")
        self.btn_overview.setCursor(Qt.PointingHandCursor)
        self.btn_overview.setFlat(True)
        self.btn_overview.setStyleSheet("")
        self.btn_overview.setFocusPolicy(Qt.NoFocus)
        self.btn_overview.setProperty("hasPreSlot", True)
        try:
            self.btn_overview.setIcon(paint_side_nav_icon("overview", 18))
            self.btn_overview.setIconSize(QSize(18, 18))
        except Exception:
            self.btn_overview.setText("系统总览")
        self.btn_overview.style().unpolish(self.btn_overview)
        self.btn_overview.style().polish(self.btn_overview)
        self.btn_overview.clicked.connect(self._goto_overview_page)
        foot.addWidget(self.btn_overview, 1)
        # 旧「配置」按钮位已改成系统总览；保留别名以免其它代码还引用 btn_settings
        self.btn_settings = self.btn_overview

        # 与默认主题一致：深色显示 ☀（点一下切浅色），浅色显示 🌙
        self.btn_theme = QPushButton("☀" if not self.is_light_theme else "🌙")
        self.btn_theme.setObjectName("SideThemeBtn")
        self.btn_theme.setCursor(Qt.PointingHandCursor)
        self.btn_theme.setFlat(False)   # flat 在 Windows 上易吃掉样式/裁切图标
        self.btn_theme.clicked.connect(self._toggle_theme)
        foot.addWidget(self.btn_theme, 0, Qt.AlignRight | Qt.AlignVCenter)
        side.addLayout(foot)
        self._apply_chrome_metrics()
        self._reposition_leds()

        # 开关 / 进度线 / 录屏圆标父级已是 side_wrap
        for _led in (
            getattr(self, "led_fast", None),
            getattr(self, "led_shot", None),
            getattr(self, "led_pre_dl", None),
            getattr(self, "led_region_rec", None),
        ):
            if _led is not None:
                _led.raise_()
        for _prog in (
            getattr(self, "video_side_prog", None),
            getattr(self, "gallery_side_prog", None),
        ):
            if _prog is not None:
                _prog.raise_()

        # ── 右侧主内容卡片 ─────────────────────────────────────
        content_wrap = QWidget()
        content_wrap.setObjectName("ContentRoot")
        content_wrap.setAttribute(Qt.WA_StyledBackground, True)

        content_layout = QVBoxLayout(content_wrap)
        # 配合最小窗：内边距略收，stack 区约 700×600
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(8)

        # 顶栏：页面标题 + 搜索提示 + 外链
        self.theme_bar = QWidget()
        self.theme_bar.setObjectName("ThemeBar")
        self.theme_bar.setAttribute(Qt.WA_StyledBackground, True)
        theme_h = QHBoxLayout(self.theme_bar)
        theme_h.setContentsMargins(0, 0, 0, 4)
        theme_h.setSpacing(6)

        self.top_title = QLabel("系统总览")
        self.top_title.setObjectName("PageTitle")
        theme_h.addWidget(self.top_title, 0, Qt.AlignVCenter)
        theme_h.addStretch(1)

        # 顶栏统一控件高度（搜索与右侧 1:1 按钮对齐；随缩放在 _apply_chrome_metrics 更新）
        _hdr_h = sp(34)

        # 顶栏搜索：胶囊外形，高度与右侧按钮一致
        self.search_wrap = QWidget()
        self.search_wrap.setObjectName("HeaderSearch")
        self.search_wrap.setFixedHeight(_hdr_h)
        self.search_wrap.setMinimumWidth(160)
        self.search_wrap.setMaximumWidth(240)
        self.search_wrap.setAttribute(Qt.WA_StyledBackground, True)
        search_l = QHBoxLayout(self.search_wrap)
        search_l.setContentsMargins(14, 0, 12, 0)
        search_l.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("HeaderSearchInput")
        self.search_edit.setPlaceholderText("搜索更多有趣内容")
        self.search_edit.setFrame(False)
        self.search_edit.setClearButtonEnabled(False)
        self.search_icon = QLabel("⌕")
        self.search_icon.setObjectName("HeaderSearchIcon")
        self.search_icon.setAlignment(Qt.AlignCenter)
        self.search_icon.setFixedWidth(20)
        search_l.addWidget(self.search_edit, 1)
        search_l.addWidget(self.search_icon, 0)
        theme_h.addWidget(self.search_wrap, 0, Qt.AlignVCenter)

        # 兼容旧引用名
        self.search_hint = self.search_wrap

        self._header_link_btns = []

        def add_link_text(text: str, url: str, wide: bool = False):
            # 默认 1:1 方形；宽文案（如 Github）保持同高、略加宽
            btn = QPushButton(text)
            btn.setObjectName("LinkIcon")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFlat(False)
            if wide:
                btn.setFixedHeight(_hdr_h)
                btn.setMinimumWidth(max(_hdr_h, 72))
                btn.setMaximumHeight(_hdr_h)
                btn.setStyleSheet("QPushButton{padding: 0px 8px;}")
            else:
                btn.setFixedSize(_hdr_h, _hdr_h)
                btn.setMinimumSize(_hdr_h, _hdr_h)
                btn.setMaximumSize(_hdr_h, _hdr_h)
            btn.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
            theme_h.addWidget(btn, 0, Qt.AlignVCenter)
            self._header_link_btns.append(btn)

        add_link_text("B站", "https://www.bilibili.com/")
        # 原「YT」改为 Github，承接系统总览「软件信息」里的仓库链接
        add_link_text("Github", "https://github.com/hbb009", wide=True)
        # 最右：时间图标 → 电子钟页（替换原 B 站空间猫标）
        self.btn_clock = QPushButton()
        self.btn_clock.setObjectName("LinkIcon")
        self.btn_clock.setCursor(Qt.PointingHandCursor)
        self.btn_clock.setFlat(False)
        self.btn_clock.setFixedSize(_hdr_h, _hdr_h)
        self.btn_clock.setToolTip("电子钟")
        self.btn_clock.setFocusPolicy(Qt.NoFocus)
        self.btn_clock.clicked.connect(self._goto_clock_page)
        theme_h.addWidget(self.btn_clock, 0, Qt.AlignVCenter)
        self._header_link_btns.append(self.btn_clock)
        self._refresh_clock_header_icon()
        # 顶栏建完后再刷一次壳层尺寸（含搜索/外链）
        self._apply_chrome_metrics()

        content_layout.addWidget(self.theme_bar, 0)
        content_layout.addWidget(self.stack, 1)

        # 兼容旧代码引用（隐藏顶栏）
        self.topbar = QWidget()
        self.topbar.setVisible(False)
        self.topbar.setMaximumHeight(0)

        outer.addWidget(self.side_wrap, 0)
        outer.addWidget(content_wrap, 1)

        # ======= 页面注册 =======
        try:
            if self._page_enabled("fast"):
                self.page_fast = PageFastSave()
            if self._page_enabled("paste"):
                self.page_paste = PagePaste()
            if self._page_enabled("img_proc"):
                self.page_img_proc = PageImageProc()
            if self._page_enabled("voice"):
                self.page_voice = PageVoiceInput()
            if self._page_enabled("voice_clone"):
                self.page_voice_clone = PageVoiceClone()
            if self._page_enabled("game_assist"):
                self.page_game_assist = PageGameAssist()
            if self._page_enabled("prompt"):
                self.page_prompt = PagePromptEditor()
            if self._page_enabled("points"):
                self.page_points = PagePointsCalc()
            if self._page_enabled("shot"):
                self.page_shot = PageScreenshot()
            if self._page_enabled("overview"):
                self.page_overview = PageOverview()
            if self._page_enabled("video"):
                self.page_douyin = PageVideo()
            if self._page_enabled("gallery"):
                self.page_gallery = PageGallery()
            # 工具 · 小偏门：比例计算 / 目录映射 / 时区汇率（原「实用工具」合页拆成单页）
            if self._page_enabled("ratio"):
                self.page_ratio = PageRatioCalc()
            if self._page_enabled("dir_link"):
                self.page_dir_link = PageDirLink()
            if self._page_enabled("tz_fx"):
                self.page_tz_fx = PageTimezoneFx()
            if self._page_enabled("region_rec"):
                self.page_region_rec = PageRegionRecord()
            # 原「配置」页已嵌入系统总览下半，不再单独建页
            self.page_about = getattr(
                getattr(self, "page_overview", None), "about_section", None
            )
            self.page_clock = PageClock()
        except Exception:
            log.exception("页面构造失败")
            raise

        # 语音录入 → 送到粘贴助手
        try:
            if hasattr(self, "page_voice"):
                self.page_voice.set_goto_paste_callback(self._voice_goto_paste)
        except Exception:
            log.exception("绑定语音录入→粘贴助手回调失败")

        # 截图热键 → 自动跳到「截图工具」页；游戏助手在前则快捷截图
        try:
            if hasattr(self, "page_shot"):
                self.page_shot.set_goto_shot_callback(self._goto_shot_page)
                if hasattr(self, "page_game_assist"):
                    self.page_shot.set_quick_capture(
                        self._is_on_game_assist_page,
                        self.page_game_assist.take_quick_shot,
                    )
                    self.page_game_assist.set_capture_starter(
                        self.page_shot.start_capture
                    )
        except Exception:
            log.exception("绑定截图热键→截图工具页失败")

        # 区域录屏：主窗口引用 + 热键切页 + 偏好落盘 + 侧栏录制红点
        try:
            if hasattr(self, "page_region_rec"):
                self.page_region_rec.set_main_window(self)
                self.page_region_rec.set_goto_page_callback(self._goto_region_rec_page)
                self.page_region_rec.set_prefs_dirty_callback(self._schedule_save_user_prefs)
                self.page_region_rec.recording_changed.connect(self._on_region_rec_recording)
        except Exception:
            log.exception("绑定区域录屏回调失败")

        # 系统总览：设置卡 + 下半配置区（原关于页）
        try:
            if getattr(self, "page_overview", None) is not None:
                self.page_overview.set_main_window(self)
                sec = getattr(self.page_overview, "settings_section", None)
                if sec is not None and hasattr(sec, "cookies_configured"):
                    sec.cookies_configured.connect(self._on_overview_cookies_configured)
        except Exception:
            log.exception("绑定系统总览设置卡主窗口失败")

        # 图片处理：偏好变更防抖写回
        try:
            if hasattr(self, "page_img_proc"):
                self.page_img_proc.set_prefs_dirty_callback(self._schedule_save_user_prefs)
        except Exception:
            log.exception("绑定图片处理偏好回调失败")

        # 提示词：写入 user.txt
        try:
            if hasattr(self, "page_prompt"):
                self.page_prompt.set_prefs_dirty_callback(self._schedule_save_user_prefs)
        except Exception:
            log.exception("绑定提示词偏好回调失败")

        try:
            if hasattr(self, "page_voice"):
                self.page_voice.set_prefs_dirty_callback(self._schedule_save_user_prefs)
        except Exception:
            log.exception("绑定语音编辑偏好回调失败")

        try:
            if hasattr(self, "page_voice_clone"):
                self.page_voice_clone.set_prefs_dirty_callback(self._schedule_save_user_prefs)
        except Exception:
            log.exception("绑定语音克隆偏好回调失败")

        try:
            if hasattr(self, "page_game_assist"):
                self.page_game_assist.set_prefs_dirty_callback(self._schedule_save_user_prefs)
        except Exception:
            log.exception("绑定游戏助手偏好回调失败")

        # 电子钟：数字颜色写入 user.txt
        try:
            if hasattr(self, "page_clock"):
                self.page_clock.set_prefs_dirty_callback(self._schedule_save_user_prefs)
        except Exception:
            log.exception("绑定电子钟偏好回调失败")

        for p in (
            getattr(self, "page_overview", None),
            getattr(self, "page_fast", None), getattr(self, "page_paste", None),
            getattr(self, "page_img_proc", None),
            getattr(self, "page_voice", None), getattr(self, "page_voice_clone", None),
            getattr(self, "page_game_assist", None),
            getattr(self, "page_points", None),
            getattr(self, "page_shot", None), getattr(self, "page_douyin", None),
            getattr(self, "page_gallery", None), getattr(self, "page_ratio", None),
            getattr(self, "page_dir_link", None), getattr(self, "page_tz_fx", None),
            getattr(self, "page_region_rec", None),
            getattr(self, "page_clock", None),
            getattr(self, "page_prompt", None),
        ):
            if p is None:
                continue
            p.setAttribute(Qt.WA_StyledBackground, True)
            p.setObjectName("PageRoot")
            self.stack.addWidget(p)

        # 默认停靠页：系统总览（底栏按钮）
        self.stack.setCurrentWidget(self.page_overview)
        self._highlight(self.btn_overview)
        self.top_title.setText("系统总览")
        try:
            self.page_overview.on_enter()
        except Exception:
            log.exception("系统总览 on_enter 失败")

        # ======= 连接切换 =======
        if self._page_enabled("fast"):
            self.btn_fast.clicked.connect(
                lambda: self._switch(self.stack.indexOf(self.page_fast), self.btn_fast))
        if self._page_enabled("paste"):
            self.btn_paste.clicked.connect(
                lambda: self._switch(self.stack.indexOf(self.page_paste), self.btn_paste))
        if self._page_enabled("img_proc"):
            self.btn_img_proc.clicked.connect(
                lambda: self._switch(self.stack.indexOf(self.page_img_proc), self.btn_img_proc))
        if self._page_enabled("voice"):
            self.btn_voice.clicked.connect(
                lambda: self._switch(self.stack.indexOf(self.page_voice), self.btn_voice))
        if self._page_enabled("voice_clone"):
            self.btn_voice_clone.clicked.connect(
                lambda: (
                    self._switch(self.stack.indexOf(self.page_voice_clone), self.btn_voice_clone),
                    getattr(self.page_voice_clone, "on_enter", lambda: None)(),
                )
            )
        if self._page_enabled("game_assist"):
            self.btn_game_assist.clicked.connect(
                lambda: (
                    self._switch(self.stack.indexOf(self.page_game_assist), self.btn_game_assist),
                    getattr(self.page_game_assist, "on_enter", lambda: None)(),
                )
            )
        if self._page_enabled("prompt"):
            self.btn_prompt.clicked.connect(self._goto_prompt_page)
        if self._page_enabled("points"):
            self.btn_points.clicked.connect(
                lambda: self._switch(self.stack.indexOf(self.page_points), self.btn_points))
        if self._page_enabled("shot"):
            self.btn_shot.clicked.connect(
                lambda: self._switch(self.stack.indexOf(self.page_shot), self.btn_shot))
        if self._page_enabled("video"):
            self.btn_douyin.clicked.connect(
                lambda: self._switch(self.stack.indexOf(self.page_douyin), self.btn_douyin))
        if self._page_enabled("gallery"):
            self.btn_gallery.clicked.connect(
                lambda: self._switch(self.stack.indexOf(self.page_gallery), self.btn_gallery))
        # 视频下载进行中：侧栏按钮下 2px 进度线（抖音/YouTube，离页也能看进度）
        try:
            if self._page_enabled("video"):
                self.page_douyin.nav_progress.connect(self._on_video_nav_progress)
        except Exception:
            log.exception("连接视频侧栏进度线失败")
        # 图集下载进行中：侧栏按钮下 2px 进度线（离页也能看进度）
        try:
            if self._page_enabled("gallery"):
                self.page_gallery.nav_progress.connect(self._on_gallery_nav_progress)
        except Exception:
            log.exception("连接图集侧栏进度线失败")
        if self._page_enabled("ratio"):
            self.btn_ratio.clicked.connect(
                lambda: self._switch(self.stack.indexOf(self.page_ratio), self.btn_ratio))
        if self._page_enabled("dir_link"):
            self.btn_dir_link.clicked.connect(
                lambda: self._switch(self.stack.indexOf(self.page_dir_link), self.btn_dir_link))
        if self._page_enabled("tz_fx"):
            self.btn_tz_fx.clicked.connect(
                lambda: (
                    self._switch(self.stack.indexOf(self.page_tz_fx), self.btn_tz_fx),
                    self.page_tz_fx.on_enter(),
                )
            )
        if self._page_enabled("region_rec"):
            self.btn_region_rec.clicked.connect(
                lambda: (
                    self._switch(self.stack.indexOf(self.page_region_rec), self.btn_region_rec),
                    self.page_region_rec.on_enter()
                )
            )

        # LED 开关（单一总开关）
        if self._page_enabled("fast"):
            self.led_fast.clicked.connect(self._toggle_fast_led)
        if self._page_enabled("shot"):
            self.led_shot.clicked.connect(self._toggle_shot_led)
        if getattr(self, "led_pre_dl", None) is not None:
            self.led_pre_dl.clicked.connect(self._on_side_pre_dl_clicked)

        # 预下载记录：剪贴板白名单入队 + 空闲自动开跑（与 F6 记录/批处理并存）
        self._setup_pre_download()

        # 全局剪贴板监听 → 白名单命中则写入对应平台「预下载记录」
        self._clipboard_auto_pending_text = ""
        self._clipboard_auto_timer = QTimer(self)
        self._clipboard_auto_timer.setSingleShot(True)
        self._clipboard_auto_timer.setInterval(200)
        self._clipboard_auto_timer.timeout.connect(self._process_global_clipboard_auto)
        try:
            QApplication.clipboard().dataChanged.connect(self._on_global_clipboard_changed)
        except Exception:
            log.exception("接入全局剪贴板监听失败")
        # 任意子功能变化时同步 LED 状态
        if self._page_enabled("fast"):
            self.page_fast.chk_imgonly.toggled.connect(self._sync_fast_led)
            self.page_fast.chk_manual.toggled.connect(self._sync_fast_led)
            self.page_fast.chk_mkdir.toggled.connect(self._sync_fast_led)
            # 速存启用总开关与 LED 双向联动
            self.page_fast.chk_enable_all.toggled.connect(self._sync_fast_led)
            # 速存操作（存图/存文本/建文件夹）成功 → 自动切到「速存图文」页查看记录
            self.page_fast.request_show.connect(
                lambda: self._switch(self.stack.indexOf(self.page_fast), self.btn_fast))
            # F6 记录模式 → 关闭/恢复（预下载与 F6 批处理并存）
            self.page_fast.record_mode_toggled_sig.connect(self._on_record_mode_toggled)
        if self._page_enabled("shot"):
            self.page_shot.checkbox_enable.toggled.connect(
                lambda checked: self.led_shot.setChecked(checked))

        # 用户习惯：启动加载 records/user.txt；变更防抖写回；关窗再存一次
        self._prefs_timer = QTimer(self)
        self._prefs_timer.setSingleShot(True)
        self._prefs_timer.timeout.connect(self._save_user_prefs)
        self._wire_user_prefs_autosave()
        self._load_user_prefs()
        # 启动后自动检测 Cookie 文件是否缺失，缺失则在系统总览「Cookie设置」提示
        QTimer.singleShot(800, self._check_cookie_files_startup)
        if self._page_enabled("fast"):
            self._sync_fast_led()
        if self._page_enabled("shot"):
            try:
                self.led_shot.setChecked(self.page_shot.checkbox_enable.isChecked())
            except Exception:
                log.exception("同步截图 LED 状态失败")

        log.info("主窗口初始化完成 pages=%s", self.stack.count())

        # 电子钟：无任务空闲后自动进入（默认 60 秒，提示词 / 语音克隆 / 图片处理 5 分钟）；
        # 用户操作或任务进行则重新计时
        self._clock_idle_timer = QTimer(self)
        self._clock_idle_timer.setSingleShot(True)
        self._clock_idle_timer.setInterval(_CLOCK_IDLE_MS)
        self._clock_idle_timer.timeout.connect(self._on_clock_idle_timeout)
        self._clock_idle_guard = QTimer(self)
        self._clock_idle_guard.setInterval(5000)
        self._clock_idle_guard.timeout.connect(self._clock_idle_guard_tick)
        self._clock_idle_guard.start()
        self._clock_idle_watcher = _ClockIdleWatcher(self)
        try:
            QApplication.instance().installEventFilter(self._clock_idle_watcher)
        except Exception:
            log.exception("安装电子钟空闲监听失败")
        self._arm_clock_idle()

    def _end_points_record_edit(self):
        """离开积分计算页前：若正在编辑某条记录，先结束编辑。"""
        page = getattr(self, "page_points", None)
        if page is None or not getattr(page, "_editing_id", None):
            return
        try:
            page._cancel_edit()
        except Exception:
            log.exception("切页结束积分记录编辑失败")

    def _switch(self, index, btn):
        if self.stack.currentIndex() != index:
            self._end_points_record_edit()
        self.stack.setCurrentIndex(index)
        self._highlight(btn)
        self.top_title.setText(self._title_for(self.stack.currentWidget()))
        # 切页后 currentWidget() 变了，_CurrentOnlyStack 的 sizeHint 也该跟着变——
        # 主动 invalidate 一下，逼 Qt 立刻重新问一遍尺寸，不等下次窗口事件才生效。
        self.stack.updateGeometry()
        # 选中态 polish / 点击焦点会把菜单按钮抬到 LED 上面 → 勾选框“消失”
        self._raise_side_leds()
        QTimer.singleShot(0, self._raise_side_leds)
        try:
            self._arm_clock_idle()
        except Exception:
            pass

    def _page_enabled(self, key: str) -> bool:
        """该页面 key 是否属于当前程序页面集合。"""
        return key in _PAGES_MAIN

    def _voice_goto_paste(self, text: str = ""):
        """语音录入页「送到粘贴助手」：切页并把文字填入内容框。"""
        try:
            self._switch(self.stack.indexOf(self.page_paste), self.btn_paste)
            text = (text or "").strip()
            if text and hasattr(self.page_paste, "inp_content"):
                cur = self.page_paste.inp_content.toPlainText().strip()
                if cur:
                    self.page_paste.inp_content.setPlainText(cur + "\n" + text)
                else:
                    self.page_paste.inp_content.setPlainText(text)
                if hasattr(self.page_paste, "_show_hint"):
                    self.page_paste._show_hint("已填入语音内容")
        except Exception:
            log.exception("语音录入送到粘贴助手失败")

    def _goto_shot_page(self):
        """截图热键触发时切到「截图工具」（便于看记录/预览）。"""
        try:
            self._switch(self.stack.indexOf(self.page_shot), self.btn_shot)
        except Exception:
            log.exception("热键切换到截图工具页失败")

    def _goto_region_rec_page(self):
        """区域录屏热键触发时切到本页。"""
        try:
            self._switch(self.stack.indexOf(self.page_region_rec), self.btn_region_rec)
        except Exception:
            log.exception("热键切换到区域录屏页失败")

    def _is_on_clock_page(self) -> bool:
        page = getattr(self, "page_clock", None)
        return page is not None and self.stack.currentWidget() is page

    def _is_on_prompt_page(self) -> bool:
        page = getattr(self, "page_prompt", None)
        return page is not None and self.stack.currentWidget() is page

    def _is_on_voice_clone_page(self) -> bool:
        page = getattr(self, "page_voice_clone", None)
        return page is not None and self.stack.currentWidget() is page

    def _is_on_voice_page(self) -> bool:
        page = getattr(self, "page_voice", None)
        return page is not None and self.stack.currentWidget() is page

    def _is_on_game_assist_page(self) -> bool:
        page = getattr(self, "page_game_assist", None)
        return page is not None and self.stack.currentWidget() is page

    def _is_on_img_proc_page(self) -> bool:
        page = getattr(self, "page_img_proc", None)
        return page is not None and self.stack.currentWidget() is page

    def _is_on_shot_page(self) -> bool:
        page = getattr(self, "page_shot", None)
        return page is not None and self.stack.currentWidget() is page

    def _clock_idle_interval_ms(self) -> int:
        if self._is_on_game_assist_page():
            return _CLOCK_IDLE_GAME_ASSIST_MS
        if (
            self._is_on_prompt_page()
            or self._is_on_voice_page()
            or self._is_on_voice_clone_page()
            or self._is_on_img_proc_page()
            or self._is_on_shot_page()
        ):
            return _CLOCK_IDLE_PROMPT_MS
        return _CLOCK_IDLE_MS

    def _clock_icon_color(self) -> str:
        on = False
        try:
            on = str(self.btn_clock.property("clockOn") or "") in ("true", "1")
        except Exception:
            pass
        if on:
            return "#FBBF24" if not self.is_light_theme else "#4F46E5"
        return "#64748b" if self.is_light_theme else "#9fb0d7"

    def _refresh_clock_header_icon(self):
        btn = getattr(self, "btn_clock", None)
        if btn is None:
            return
        try:
            btn.setIcon(paint_header_clock_icon(18, self._clock_icon_color()))
            btn.setIconSize(QSize(18, 18))
            btn.setText("")
        except Exception:
            log.exception("刷新顶栏时钟图标失败")

    def _set_clock_btn_active(self, active: bool):
        btn = getattr(self, "btn_clock", None)
        if btn is None:
            return
        try:
            btn.setProperty("clockOn", "true" if active else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            self._refresh_clock_header_icon()
        except Exception:
            pass

    def _goto_prompt_page(self):
        """侧栏「提示词」：切页并让双栏重新对齐。"""
        page = getattr(self, "page_prompt", None)
        btn = getattr(self, "btn_prompt", None)
        if page is None or btn is None:
            log.warning("提示词未初始化")
            return
        try:
            self._switch(self.stack.indexOf(page), btn)
            if hasattr(page, "on_enter"):
                page.on_enter()
        except Exception:
            log.exception("切换到提示词失败")

    def _goto_clock_page(self):
        """顶栏时间图标 / 空闲超时：进入电子钟页。"""
        page = getattr(self, "page_clock", None)
        if page is None:
            log.warning("电子钟页未初始化")
            return
        try:
            self._end_points_record_edit()
            self._highlight(None)
            self.stack.setCurrentWidget(page)
            self.top_title.setText("电子钟")
            self.stack.updateGeometry()
            self._set_clock_btn_active(True)
            if hasattr(self, "_clock_idle_timer"):
                self._clock_idle_timer.stop()
            if hasattr(page, "on_enter"):
                page.on_enter()
            self._raise_side_leds()
        except Exception:
            log.exception("切换到电子钟页失败")

    def _is_clock_blocked_by_task(self) -> bool:
        """有下载 / 录屏 / 语音任务，积分记录正在编辑，或弹窗未关时，不自动进电子钟。

        弹窗未关时停表：避免用户在看错误/提示弹窗期间，主窗口被切到电子钟屏保。
        弹窗关闭后，_clock_idle_guard_tick 会在数秒内重新从满额开始计时。
        """
        try:
            if QApplication.activeModalWidget() is not None:
                return True
        except Exception:
            pass
        try:
            if QApplication.activePopupWidget() is not None:
                return True
        except Exception:
            pass
        try:
            if self._is_any_download_busy():
                return True
        except Exception:
            pass
        rec = getattr(self, "page_region_rec", None)
        if rec is not None:
            recorder = getattr(rec, "_recorder", None)
            if recorder is not None and getattr(recorder, "is_recording", False):
                return True
        voice = getattr(self, "page_voice", None)
        if voice is not None and getattr(voice, "_state", "") in ("recording", "transcribing"):
            return True
        clone = getattr(self, "page_voice_clone", None)
        if clone is not None and getattr(clone, "_state", "") == "synth":
            return True
        game = getattr(self, "page_game_assist", None)
        if game is not None and getattr(game, "_state", "") == "busy":
            return True
        fast = getattr(self, "page_fast", None)
        if fast is not None and getattr(fast, "_recording", False):
            return True
        points = getattr(self, "page_points", None)
        if points is not None and getattr(points, "_editing_id", None):
            return True
        return False

    def _arm_clock_idle(self):
        """无任务且不在电子钟页时，按当前页重新计空闲（默认 60 秒 / 提示词等长任务页 5 分钟）。"""
        timer = getattr(self, "_clock_idle_timer", None)
        if timer is None:
            return
        if self._is_on_clock_page() or self._is_clock_blocked_by_task():
            timer.stop()
            return
        timer.setInterval(self._clock_idle_interval_ms())
        timer.start()

    def _on_clock_idle_timeout(self):
        if self._is_on_clock_page():
            return
        if self._is_clock_blocked_by_task():
            return
        self._goto_clock_page()

    def _clock_idle_guard_tick(self):
        """任务结束后补上计时；任务进行中停表。"""
        if self._is_on_clock_page():
            return
        if self._is_clock_blocked_by_task():
            try:
                self._clock_idle_timer.stop()
            except Exception:
                pass
            return
        timer = getattr(self, "_clock_idle_timer", None)
        if timer is not None and not timer.isActive():
            timer.setInterval(self._clock_idle_interval_ms())
            timer.start()

    def _note_user_activity(self):
        if self._is_on_clock_page():
            return
        self._arm_clock_idle()

    def _title_for(self, w: QWidget) -> str:
        if w is getattr(self, "page_overview",     None): return "系统总览"
        if w is getattr(self, "page_fast",         None): return "速存图文"
        if w is getattr(self, "page_paste",        None): return "粘贴助手"
        if w is getattr(self, "page_img_proc",     None): return "图片处理"
        if w is getattr(self, "page_voice",        None): return "语音编辑"
        if w is getattr(self, "page_voice_clone",  None): return "语音克隆"
        if w is getattr(self, "page_game_assist",  None): return "游戏助手"
        if w is getattr(self, "page_points",       None): return "积分计算"
        if w is getattr(self, "page_shot",         None): return "截图工具"
        if w is getattr(self, "page_douyin",       None): return "视频下载"
        if w is getattr(self, "page_gallery",      None): return "图集下载"
        if w is getattr(self, "page_ratio",       None): return "比例计算"
        if w is getattr(self, "page_dir_link",    None): return "目录映射"
        if w is getattr(self, "page_tz_fx",       None): return "时区汇率"
        if w is getattr(self, "page_region_rec",   None): return "区域录屏"
        if w is getattr(self, "page_about",        None): return "系统总览"
        if w is getattr(self, "page_clock",        None): return "电子钟"
        if w is getattr(self, "page_prompt",       None): return "提示词"
        return "桌面助手"

    def _sync_fast_led(self, _=None):
        """任意速存子功能变化时，LED 亮起条件：至少一个功能开启"""
        if not hasattr(self, "page_fast") or not hasattr(self, "led_fast"):
            return
        self.led_fast.setChecked(self.page_fast.is_any_active())

    def _on_record_mode_toggled(self, action: str):
        """F6 记录模式：与预下载并存，不再联动已移除的侧栏自动下载开关。"""
        del action

    def _toggle_fast_led(self):
        """点击 LED：全部一键开 / 全部一键关"""
        if not hasattr(self, "page_fast") or not hasattr(self, "led_fast"):
            return
        currently_any = self.page_fast.is_any_active()
        self.page_fast.set_all_features(not currently_any)
        self.led_fast.setChecked(not currently_any)
        # 同步页面内总开关显示
        self.page_fast._sync_enable_all_display()

    def _toggle_fast_txt_led(self):
        if hasattr(self, "page_fast"):
            self.page_fast.chk_manual.setChecked(not self.page_fast.chk_manual.isChecked())

    def _toggle_shot_led(self):
        if hasattr(self, "page_shot"):
            self.page_shot.checkbox_enable.setChecked(not self.page_shot.checkbox_enable.isChecked())

    # ── 预下载：全局剪贴板 → 白名单入队 ─────────────────────
    def _on_global_clipboard_changed(self):
        """剪贴板变化：短延迟合并后按白名单写入预下载记录。

        必须先看到 https:// 才触发；本地文件/纯文本/http 都不进预下载。
        """
        text = ""
        try:
            cb = QApplication.clipboard()
            if cb is None:
                return
            text = (cb.text() or "").strip()
        except Exception:
            return
        if "https://" not in text.lower():
            return
        self._clipboard_auto_pending_text = text
        self._clipboard_auto_timer.start()

    def _clipboard_whitelist(self) -> dict:
        """读取当前预下载域名白名单（关于页可编辑；缺省用默认表）。"""
        try:
            from utils.user_prefs import default_clipboard_whitelist
            ca = (getattr(self, "_user_prefs", None) or {}).get("clipboard_auto") or {}
            wl = ca.get("whitelist") if isinstance(ca, dict) else None
            base = default_clipboard_whitelist()
            if not isinstance(wl, dict):
                return base
            out = dict(base)
            for k, v in wl.items():
                if isinstance(v, list):
                    out[k] = [str(x).strip().lower() for x in v if str(x).strip()]
            return out
        except Exception:
            from utils.user_prefs import default_clipboard_whitelist
            return default_clipboard_whitelist()

    _HTTPS_URL_RE = re.compile(r"https://[^\s<>\"'`]+", re.I)

    @staticmethod
    def _extract_https_urls(text: str):
        """只抽出真正的 https 链接，避免正文里提到域名被当成命中。"""
        out = []
        for raw in MainWindow._HTTPS_URL_RE.findall(text or ""):
            u = (raw or "").rstrip(").,];，。、\"'")
            if u:
                out.append(u)
        return out

    @staticmethod
    def _host_hits_domains(host: str, domains) -> bool:
        host = (host or "").lower().strip(".")
        if not host:
            return False
        for d in domains or []:
            dd = (d or "").strip().lower().lstrip(".")
            if not dd:
                continue
            if host == dd or host.endswith("." + dd):
                return True
        return False

    @staticmethod
    def _text_hits_domains(text_low: str, domains) -> bool:
        """兼容旧调用：整段子串匹配。预下载请用链接主机名。"""
        for d in domains or []:
            dd = (d or "").strip().lower()
            if dd and dd in text_low:
                return True
        return False

    def _detect_pre_download_platform(self, text: str) -> str:
        """按白名单识别平台 key；只认 https 链接的主机名，未命中返回空串。

        正文里写到 youtube.com / AV1 等不算。B 站 BV/av 兜底已去掉：
        本流程本身要求剪贴板含 https://，有链接时应用主机名判断即可。
        """
        text = (text or "").strip()
        if not text or "https://" not in text.lower():
            return ""
        urls = self._extract_https_urls(text)
        if not urls:
            return ""
        from urllib.parse import urlparse

        wl = self._clipboard_whitelist()
        order = (
            ("douyin", "douyin"),
            ("bilibili", "bilibili"),
            ("youtube", "youtube"),
            ("gallery_eh", "ehentai"),
            ("gallery_pixiv", "pixiv"),
            ("gallery_hitomi", "hitomi"),
        )
        for url in urls:
            try:
                host = (urlparse(url).hostname or "").lower()
            except Exception:
                host = ""
            if not host:
                continue
            for wl_key, plat in order:
                if self._host_hits_domains(host, wl.get(wl_key)):
                    return plat
        return ""

    def _process_global_clipboard_auto(self):
        """复制命中白名单 → 写入对应页「预下载记录」，不立即开下。

        无论当前是否正在跑预下载/其它下载，都应能入队并气泡提示。
        """
        text = (getattr(self, "_clipboard_auto_pending_text", "") or "").strip()
        self._clipboard_auto_pending_text = ""
        if not text or "https://" not in text.lower():
            return
        plat = self._detect_pre_download_platform(text)
        if not plat:
            log.info("预下载：剪贴板未命中白名单 text=%s", text[:80])
            return
        store = getattr(self, "_pre_dl_store", None)
        if store is None:
            return
        # 正在处理的那条已从队列 pop 出：允许再次复制同一链接重新入队
        ok = store.enqueue(plat, text)
        n = len(store.list_platform(plat))
        if ok:
            log.info(
                "预下载入队 platform=%s n=%s running=%s text=%s",
                plat, n, bool(getattr(self, "_pre_dl_running", False)), text[:80],
            )
            self._refresh_pre_dl_panels(plat)
            self._show_pre_dl_enqueue_toast(plat, n=n, duplicated=False)
            # 忙时只入队，调度函数内部会自行 return
            self._pre_dl_try_schedule()
        else:
            log.info("预下载跳过(队列已有相同内容) platform=%s n=%s", plat, n)
            self._refresh_pre_dl_panels(plat)
            self._show_pre_dl_enqueue_toast(plat, n=n, duplicated=True)

    def _show_pre_dl_enqueue_toast(self, plat: str, n: int = None, duplicated: bool = False):
        """鼠标旁小气泡：平台key·预N（N=当前预下载总数=视频总数+图集总数）。

        例如 douyin 入队后共 5 条 → 「douyin·预5」。
        """
        del n
        try:
            from utils.cursor_toast import show_cursor_toast
            key = (plat or "").strip() or "?"
            total = 0
            store = getattr(self, "_pre_dl_store", None)
            if store is not None:
                try:
                    total = int(store.total_count() or 0)
                except Exception:
                    total = 0
            show_cursor_toast(
                f"{key}·预{total}",
                accent="info" if duplicated else "ok",
                stay=1800,
            )
        except Exception:
            log.exception("预下载入队气泡失败 platform=%s", plat)

    def _setup_pre_download(self):
        """初始化预下载存储、子页页签条数、空闲调度定时器。"""
        from utils.pre_download import PreDownloadStore

        self._pre_dl_store = PreDownloadStore()
        self._pre_dl_auto_process = False  # 默认关；启动后从 user.txt 恢复
        self._pre_dl_running = False
        self._pre_dl_current_plat = ""
        self._pre_dl_current_url = ""
        self._pre_dl_was_busy = False
        self._pre_dl_stop_after_current = False  # 关掉开关：本条/本集下完再停
        self._pre_dl_user_cancelled = False  # 当前条由页面「取消」按钮中止
        self._pre_dl_cancel_blocked = False  # 仅剩一条：无法排到队尾
        self._pre_dl_paused_for_batch = False  # 批处理优先：不再开下一条
        self._batch_waiting_pre_dl = False  # 等预下载当前条下完再开批处理
        # 空闲防抖：解析结束与下载开始之间常有短暂 idle，避免误判「本条结束」
        self._pre_dl_idle_timer = QTimer(self)
        self._pre_dl_idle_timer.setSingleShot(True)
        self._pre_dl_idle_timer.setInterval(2800)
        self._pre_dl_idle_timer.timeout.connect(self._pre_dl_on_idle_settled)
        # 打开开关后先等冷却，再开第一条
        self._pre_dl_arm_timer = QTimer(self)
        self._pre_dl_arm_timer.setSingleShot(True)
        self._pre_dl_arm_timer.timeout.connect(self._pre_dl_try_schedule)

        if self._page_enabled("video"):
            pv = getattr(self, "page_douyin", None)
            if pv is not None:
                try:
                    pv.nav_progress.connect(self._on_pre_dl_nav_progress)
                except Exception:
                    log.exception("接入视频预下载进度失败")
        if self._page_enabled("gallery"):
            gal = getattr(self, "page_gallery", None)
            if gal is not None:
                for attr, plat in (
                    ("page_eh", "ehentai"),
                    ("page_pixiv", "pixiv"),
                    ("page_hitomi", "hitomi"),
                ):
                    sub = getattr(gal, attr, None)
                    if sub is None:
                        continue
                    try:
                        if hasattr(sub, "nav_progress"):
                            sub.nav_progress.connect(
                                lambda a, p, c, _plat=plat: self._on_pre_dl_nav_progress_plat(
                                    _plat, a, p, c
                                )
                            )
                    except Exception:
                        log.exception("接入图集预下载进度失败 plat=%s", plat)

        self._wire_pre_dl_cancel_buttons()
        self._refresh_pre_dl_panels()
        self._pre_dl_timer = QTimer(self)
        self._pre_dl_timer.setInterval(2000)
        self._pre_dl_timer.timeout.connect(self._pre_dl_try_schedule)
        self._pre_dl_timer.start()
        QTimer.singleShot(800, self._pre_dl_try_schedule)

    def _open_pre_dl_dialog(self, plat: str):
        from pages.pre_download_panel import open_pre_download_dialog
        open_pre_download_dialog(
            plat,
            lambda: getattr(self, "_pre_dl_store", None),
            self,
            on_changed=lambda: (
                self._refresh_pre_dl_panels(plat),
                self._pre_dl_try_schedule(),
            ),
        )
        self._refresh_pre_dl_panels(plat)

    def _pre_dl_count(self, plat: str) -> int:
        store = getattr(self, "_pre_dl_store", None)
        if store is None:
            return 0
        try:
            return len(store.list_platform(plat))
        except Exception:
            return 0

    def _update_pre_dl_tab_labels(self):
        """把各平台队列条数写到子页页签名后；0 条不显示数字。"""
        if self._page_enabled("video"):
            pv = getattr(self, "page_douyin", None)
            if pv is not None and hasattr(pv, "set_pre_dl_counts"):
                try:
                    pv.set_pre_dl_counts({
                        "douyin": self._pre_dl_count("douyin"),
                        "bilibili": self._pre_dl_count("bilibili"),
                        "youtube": self._pre_dl_count("youtube"),
                    })
                except Exception:
                    log.exception("更新视频子页预下载条数失败")
        if self._page_enabled("gallery"):
            gal = getattr(self, "page_gallery", None)
            if gal is not None and hasattr(gal, "set_pre_dl_counts"):
                try:
                    gal.set_pre_dl_counts({
                        "ehentai": self._pre_dl_count("ehentai"),
                        "pixiv": self._pre_dl_count("pixiv"),
                        "hitomi": self._pre_dl_count("hitomi"),
                    })
                except Exception:
                    log.exception("更新图集子页预下载条数失败")

    def _refresh_pre_dl_panels(self, plat: str = None):
        """刷新子页页签条数 + 侧栏「预N」角标。"""
        del plat
        self._update_pre_dl_tab_labels()
        self._update_side_pre_dl_badges()

    def _pre_dl_group_count(self, group: str) -> int:
        """侧栏分组条数：video=抖音+B站+YT；gallery=EH+Pixiv+hitomi。"""
        store = getattr(self, "_pre_dl_store", None)
        if store is None:
            return 0
        if group == "video":
            keys = ("douyin", "bilibili", "youtube")
        elif group == "gallery":
            keys = ("ehentai", "pixiv", "hitomi")
        else:
            return 0
        n = 0
        for k in keys:
            try:
                n += len(store.list_platform(k))
            except Exception:
                pass
        return n

    def _update_side_pre_dl_badges(self):
        """更新侧栏「预N」角标；0 隐藏并复位位置。"""
        for badge, group in (
            (getattr(self, "badge_pre_video", None), "video"),
            (getattr(self, "badge_pre_gallery", None), "gallery"),
        ):
            if badge is None:
                continue
            try:
                badge.set_count(self._pre_dl_group_count(group))
            except Exception:
                log.exception("更新侧栏预下载角标失败 group=%s", group)
        try:
            self._sync_side_count_pairs()
            self._sync_side_badge_padding()
            self._reposition_leds()
        except Exception:
            pass

    def refresh_batch_badges(self):
        """按自动下载引擎剩余条数刷新侧栏「批N」+ 各子页页签。"""
        pv = getattr(self, "page_douyin", None)
        counts = {}
        if pv is not None and hasattr(pv, "batch_remaining_by_platform"):
            try:
                counts = pv.batch_remaining_by_platform() or {}
            except Exception:
                log.exception("读取批处理剩余条数失败")
                counts = {}
        video_keys = ("douyin", "bilibili", "youtube")
        gallery_keys = ("ehentai", "pixiv", "hitomi")
        video_n = 0
        gallery_n = 0
        for k in video_keys:
            try:
                video_n += max(0, int(counts.get(k, 0) or 0))
            except (TypeError, ValueError):
                pass
        for k in gallery_keys:
            try:
                gallery_n += max(0, int(counts.get(k, 0) or 0))
            except (TypeError, ValueError):
                pass
        for badge, n, label in (
            (getattr(self, "badge_batch_video", None), video_n, "视频"),
            (getattr(self, "badge_batch_gallery", None), gallery_n, "图集"),
        ):
            if badge is None:
                continue
            try:
                badge.set_count(n)
                if n > 0:
                    badge.setToolTip(f"批处理剩余 {n} 条（{label}）")
            except Exception:
                log.exception("更新侧栏批处理角标失败 group=%s", label)
        if pv is not None and hasattr(pv, "set_batch_counts"):
            try:
                pv.set_batch_counts({k: counts.get(k, 0) for k in video_keys})
            except Exception:
                log.exception("更新视频页签批处理条数失败")
        if self._page_enabled("gallery"):
            gal = getattr(self, "page_gallery", None)
            if gal is not None and hasattr(gal, "set_batch_counts"):
                try:
                    gal.set_batch_counts({k: counts.get(k, 0) for k in gallery_keys})
                except Exception:
                    log.exception("更新图集页签批处理条数失败")
        try:
            self._sync_side_count_pairs()
            self._sync_side_badge_padding()
            self._reposition_leds()
        except Exception:
            pass

    def _page_workers_busy(self, page) -> bool:
        if page is None:
            return False
        for attr in ("_parse_worker", "_dl_worker", "_worker"):
            w = getattr(page, attr, None)
            try:
                if w is not None and hasattr(w, "isRunning") and w.isRunning():
                    return True
            except Exception:
                pass
        if getattr(page, "_nav_busy", False):
            return True
        return False

    def _is_any_download_busy(self, *, ignore_pre_dl_flag: bool = False) -> bool:
        """是否有下载任务在跑。ignore_pre_dl_flag：调度自检时不把本标志当忙。"""
        if not ignore_pre_dl_flag and getattr(self, "_pre_dl_running", False):
            return True
        pv = getattr(self, "page_douyin", None)
        if pv is not None and getattr(pv, "_batch_mode", False):
            return True
        if self._page_enabled("video") and pv is not None:
            for attr in ("page_douyin", "page_bilibili", "page_youtube"):
                if self._page_workers_busy(getattr(pv, attr, None)):
                    return True
            try:
                for _k, (oa, _op, _oc) in (getattr(pv, "_nav_sources", None) or {}).items():
                    if oa:
                        return True
            except Exception:
                pass
        if self._page_enabled("gallery"):
            gal = getattr(self, "page_gallery", None)
            if gal is not None:
                for attr in ("page_eh", "page_pixiv", "page_hitomi"):
                    sub = getattr(gal, attr, None)
                    if self._page_workers_busy(sub):
                        return True
                    if sub is not None and (
                        getattr(sub, "_batch_mode", False)
                        or getattr(sub, "_fill_active_once", False)
                    ):
                        return True
        return False

    def _on_pre_dl_nav_progress(self, active: bool, pct: int, color: str):
        """视频页聚合进度：预下载单条结束时收口。"""
        del pct, color
        self._pre_dl_on_activity(bool(active))

    def _on_pre_dl_nav_progress_plat(self, plat: str, active: bool, pct: int, color: str):
        del pct, color
        if plat and getattr(self, "_pre_dl_current_plat", "") == plat:
            self._pre_dl_on_activity(bool(active))

    def _pre_dl_on_activity(self, active: bool):
        if not getattr(self, "_pre_dl_running", False):
            self._pre_dl_was_busy = False
            try:
                self._pre_dl_idle_timer.stop()
            except Exception:
                pass
            return
        if active:
            self._pre_dl_was_busy = True
            try:
                self._pre_dl_idle_timer.stop()
            except Exception:
                pass
            return
        # 变空闲：防抖后再确认（避免解析→下载间隙误收口）
        if getattr(self, "_pre_dl_was_busy", False):
            try:
                self._pre_dl_idle_timer.start()
            except Exception:
                self._pre_dl_on_idle_settled()

    def _pre_dl_on_idle_settled(self):
        """连续空闲一段时间后，才认定本条预下载结束。"""
        if not getattr(self, "_pre_dl_running", False):
            return
        if self._is_any_download_busy(ignore_pre_dl_flag=True):
            # 仍在忙（可能刚进入下载阶段），继续等
            self._pre_dl_was_busy = True
            try:
                self._pre_dl_idle_timer.start()
            except Exception:
                pass
            return
        # 以子页 _last_download_ok 为准，成功才删记录
        ok = self._pre_dl_query_last_ok()
        self._pre_dl_finish_current(ok=ok)

    def _pre_dl_page_for(self, plat: str):
        """预下载平台 → 对应子页实例。"""
        plat = (plat or "").strip()
        if plat in ("douyin", "bilibili", "youtube"):
            pv = getattr(self, "page_douyin", None)
            if pv is None:
                return None
            return {
                "douyin": getattr(pv, "page_douyin", None),
                "bilibili": getattr(pv, "page_bilibili", None),
                "youtube": getattr(pv, "page_youtube", None),
            }.get(plat)
        if plat in ("ehentai", "pixiv", "hitomi"):
            gal = getattr(self, "page_gallery", None)
            if gal is None:
                return None
            return {
                "ehentai": getattr(gal, "page_eh", None),
                "pixiv": getattr(gal, "page_pixiv", None),
                "hitomi": getattr(gal, "page_hitomi", None),
            }.get(plat)
        return None

    def _pre_dl_query_last_ok(self) -> bool:
        """下载收口后读子页成功标志（与批处理同源 _last_download_ok）。"""
        plat = getattr(self, "_pre_dl_current_plat", "") or ""
        page = self._pre_dl_page_for(plat)
        if page is None:
            return False
        return bool(getattr(page, "_last_download_ok", False))

    def _wire_pre_dl_cancel_buttons(self):
        """六个下载页的「取消」：预下载进行中则把当前条排到总队尾。"""
        from utils.pre_download import PLATFORMS

        for plat in PLATFORMS:
            page = self._pre_dl_page_for(plat)
            btn = getattr(page, "btn_cancel", None) if page is not None else None
            if btn is None:
                continue
            try:
                btn.clicked.connect(lambda *_, p=plat: self._on_pre_dl_page_cancel(p))
            except Exception:
                log.exception("接入预下载取消按钮失败 plat=%s", plat)

    def _on_pre_dl_page_cancel(self, plat: str):
        """预下载进行中点「取消」：立刻改 pre_download.json；仅剩一条则无法操作并关开关。"""
        if not getattr(self, "_pre_dl_running", False):
            return
        if (getattr(self, "_pre_dl_current_plat", "") or "") != (plat or "").strip():
            return
        store = getattr(self, "_pre_dl_store", None)
        url = getattr(self, "_pre_dl_current_url", "") or ""
        if store is None or not url:
            return
        if not store.can_move_to_end():
            self._pre_dl_user_cancelled = False
            self._pre_dl_cancel_blocked = True
            log.info("预下载取消无法操作（仅剩一条）platform=%s url=%s", plat, url[:80])
            self._show_pre_dl_switch_toast(
                "无法取消", hint="仅剩一条", err=1, accent="err", stay=1800,
            )
            self.set_pre_dl_auto_process_enabled(False, persist=True, notify=False)
            return
        moved = bool(store.move_to_end(plat, url))
        self._pre_dl_user_cancelled = True
        self._pre_dl_cancel_blocked = False
        self._refresh_pre_dl_panels(plat)
        log.info(
            "预下载取消立刻落盘队尾 platform=%s moved=%s url=%s",
            plat, moved, url[:80],
        )
        if moved:
            self._show_pre_dl_switch_toast(
                "已取消", hint="已排到队尾", skip=1, accent="info", stay=1800,
            )

    def _pre_dl_finish_current(self, ok: bool = True):
        """本条结束：成功删记录；用户取消排到总队尾；解析/下载失败移入无法处理。"""
        if not getattr(self, "_pre_dl_running", False):
            return
        try:
            self._pre_dl_idle_timer.stop()
        except Exception:
            pass
        plat = getattr(self, "_pre_dl_current_plat", "") or ""
        url = getattr(self, "_pre_dl_current_url", "") or ""
        store = getattr(self, "_pre_dl_store", None)
        removed = False
        moved_failed = False
        user_cancelled = bool(getattr(self, "_pre_dl_user_cancelled", False))
        cancel_blocked = bool(getattr(self, "_pre_dl_cancel_blocked", False))
        self._pre_dl_user_cancelled = False
        self._pre_dl_cancel_blocked = False
        if store is not None:
            if user_cancelled:
                # 点取消时已经写入 JSON 队尾，这里不再挪、也不删
                log.info(
                    "预下载取消收口（记录已在队尾）platform=%s url=%s",
                    plat, url[:80],
                )
            elif cancel_blocked:
                log.info(
                    "预下载取消收口（仅剩一条未改队列）platform=%s url=%s",
                    plat, url[:80],
                )
            elif ok and url:
                removed = bool(store.remove_match(plat, url))
                log.info(
                    "预下载成功删记录 platform=%s removed=%s url=%s",
                    plat, removed, url[:80],
                )
            elif not ok and url:
                moved_failed = bool(store.move_to_failed(plat, url))
                log.info(
                    "预下载失败已移入无法处理 platform=%s moved=%s url=%s",
                    plat, moved_failed, url[:80],
                )
            store.mark_finished()
        log.info(
            "预下载本条结束 platform=%s ok=%s cancelled=%s blocked=%s "
            "removed=%s failed=%s",
            plat, ok, user_cancelled, cancel_blocked, removed, moved_failed,
        )
        self._pre_dl_running = False
        self._pre_dl_current_plat = ""
        self._pre_dl_current_url = ""
        self._pre_dl_was_busy = False
        self._refresh_pre_dl_panels(plat or None)
        stop_after = bool(getattr(self, "_pre_dl_stop_after_current", False))
        self._pre_dl_stop_after_current = False
        if moved_failed and not user_cancelled and not cancel_blocked:
            self._show_pre_dl_switch_toast(
                "无法处理", hint="解析或无法下载", err=1, accent="err", stay=1800,
            )
        if getattr(self, "_batch_waiting_pre_dl", False):
            self._batch_waiting_pre_dl = False
            log.info("预下载当前条已结束，开始批处理")
            QTimer.singleShot(400, self._kick_pending_batch)
            return
        if stop_after or not self.is_pre_dl_auto_process_enabled():
            if stop_after and not cancel_blocked:
                self._show_pre_dl_switch_toast(
                    "已停", hint="当前条已结束", accent="ok", stay=1800,
                )
            return
        # 冷却结束后再由定时器拉下一条
        QTimer.singleShot(1000, self._pre_dl_try_schedule)

    def is_pre_dl_auto_process_enabled(self) -> bool:
        """是否允许空闲时自动处理预下载队列。状态写入 user.txt。"""
        return bool(getattr(self, "_pre_dl_auto_process", False))

    def _on_side_pre_dl_clicked(self):
        """侧栏「系统总览」右侧开关 → 与设置区预下载开关同一套状态。"""
        led = getattr(self, "led_pre_dl", None)
        on = bool(led.isChecked()) if led is not None else False
        self.set_pre_dl_auto_process_enabled(on)

    def _pre_dl_current_is_gallery(self) -> bool:
        plat = (getattr(self, "_pre_dl_current_plat", "") or "").strip()
        return plat in ("ehentai", "pixiv", "hitomi")

    def _show_pre_dl_switch_toast(
        self, status: str, *, hint: str = "", ok: int = 0, skip: int = 0,
        err: int = 0, accent: str = "info", stay: int = 1800,
    ):
        try:
            from utils.cursor_toast import show_cursor_toast
            show_cursor_toast(
                "预下载", status, ok=ok, skip=skip, err=err,
                hint=hint, accent=accent, stay=stay,
            )
        except Exception:
            log.exception("预下载开关气泡失败 status=%s", status)

    def set_pre_dl_auto_process_enabled(self, on: bool, *, persist: bool = True, notify: bool = None):
        """打开/关闭预下载自动处理；开后先等 COOLDOWN_SEC 再调度第一条。

        关闭时不中断当前条：图集/视频下完本条后再停。
        persist=True（默认）时写入 user.txt；启动恢复用 persist=False。
        notify=None 时跟 persist：用户点开关才出气泡，启动恢复/Cookie 检测不打扰。
        """
        on = bool(on)
        if notify is None:
            notify = bool(persist)
        prev = bool(getattr(self, "_pre_dl_auto_process", False))
        self._pre_dl_auto_process = on
        if prev != on:
            log.info("预下载自动处理 %s → %s", "开" if prev else "关", "开" if on else "关")
        # 同步系统总览设置区 + 侧栏「预」开关
        try:
            sec = getattr(
                getattr(self, "page_overview", None), "settings_section", None
            )
            if sec is not None and hasattr(sec, "sync_pre_dl_auto_checkbox"):
                sec.sync_pre_dl_auto_checkbox(on)
        except Exception:
            log.exception("同步系统总览预下载开关失败")
        try:
            led = getattr(self, "led_pre_dl", None)
            if led is not None and bool(led.isChecked()) != bool(on):
                led.setChecked(bool(on))
        except Exception:
            log.exception("同步侧栏预下载开关失败")
        if persist:
            try:
                prefs = getattr(self, "_user_prefs", None)
                if isinstance(prefs, dict):
                    ca = prefs.get("clipboard_auto")
                    if not isinstance(ca, dict):
                        ca = {}
                        prefs["clipboard_auto"] = ca
                    ca["auto_process"] = on
                self._schedule_save_user_prefs()
            except Exception:
                log.exception("保存预下载开关到 user.txt 失败")
        arm = getattr(self, "_pre_dl_arm_timer", None)
        if not on:
            if arm is not None:
                arm.stop()
            running = bool(getattr(self, "_pre_dl_running", False))
            if running:
                self._pre_dl_stop_after_current = True
            else:
                self._pre_dl_stop_after_current = False
            if notify and prev:
                if running and self._pre_dl_current_is_gallery():
                    self._show_pre_dl_switch_toast(
                        "下完本集即停", accent="info", stay=2200,
                    )
                elif running:
                    self._show_pre_dl_switch_toast(
                        "下完本条即停", accent="info", stay=2200,
                    )
                else:
                    self._show_pre_dl_switch_toast(
                        "已关", accent="info", stay=1600,
                    )
            return
        # 开：取消「下完再停」，先记冷却，3 秒后再拉第一条（关→开也重新等）
        self._pre_dl_stop_after_current = False
        if notify and not prev:
            self._show_pre_dl_switch_toast(
                "已开", accent="ok", stay=1600,
            )
        store = getattr(self, "_pre_dl_store", None)
        if store is not None:
            try:
                store.mark_finished()
            except Exception:
                log.exception("打开预下载时写入冷却失败")
        try:
            from utils.pre_download import COOLDOWN_SEC
            ms = max(0, int(float(COOLDOWN_SEC) * 1000))
        except Exception:
            ms = 3000
        if arm is not None:
            arm.start(ms)
        else:
            QTimer.singleShot(ms, self._pre_dl_try_schedule)

    def _pre_dl_try_schedule(self):
        """空闲 + 开关开 + 冷却结束 → 看队头并自动处理（不提前出队）。"""
        if getattr(self, "_pre_dl_running", False):
            return
        # 批处理是用户手动开的，优先级最高：暂停期间不抢下载页
        if getattr(self, "_pre_dl_paused_for_batch", False):
            return
        # 保险开关：关着 / 已点「下完再停」只保留队列，不自动开跑
        if not self.is_pre_dl_auto_process_enabled():
            return
        if getattr(self, "_pre_dl_stop_after_current", False):
            return
        store = getattr(self, "_pre_dl_store", None)
        if store is None or not store.can_activate():
            return
        if self._is_any_download_busy():
            return
        # 只 peek：开跑期间记录仍在队列里，记数不减
        item = store.peek_next()
        if not item:
            return
        plat, url = item
        self._pre_dl_start(plat, url)

    def _pre_dl_start(self, plat: str, url: str):
        """按平台启动一条下载（不依赖侧栏开关）。"""
        url = (url or "").strip()
        plat = (plat or "").strip()
        if not url or not plat:
            return
        log.info("预下载开跑 platform=%s url=%s", plat, url[:100])
        self._pre_dl_running = True
        self._pre_dl_current_plat = plat
        self._pre_dl_current_url = url
        self._pre_dl_was_busy = False
        self._pre_dl_user_cancelled = False
        self._pre_dl_cancel_blocked = False
        self._pre_dl_start_retry = False
        try:
            self._pre_dl_idle_timer.stop()
        except Exception:
            pass
        try:
            if plat in ("douyin", "bilibili", "youtube"):
                pv = getattr(self, "page_douyin", None)
                if pv is None:
                    raise RuntimeError("视频页未加载")
                self._switch(self.stack.indexOf(pv), self.btn_douyin)
                if plat == "douyin":
                    pv.handoff_to_douyin(url, auto_start=True)
                elif plat == "bilibili":
                    pv.handoff_to_bilibili(url, auto_start=True)
                else:
                    pv.handoff_to_youtube(url, auto_start=True)
            elif plat in ("ehentai", "pixiv", "hitomi"):
                gal = getattr(self, "page_gallery", None)
                if gal is None:
                    raise RuntimeError("图集页未加载")
                sub = {
                    "ehentai": getattr(gal, "page_eh", None),
                    "pixiv": getattr(gal, "page_pixiv", None),
                    "hitomi": getattr(gal, "page_hitomi", None),
                }.get(plat)
                if sub is None:
                    raise RuntimeError(f"图集子页未加载 {plat}")
                self._switch(self.stack.indexOf(gal), self.btn_gallery)
                try:
                    gal.tabs.setCurrentWidget(sub)
                except Exception:
                    pass
                if hasattr(sub, "url_edit"):
                    sub.url_edit.setText(url)
                elif hasattr(gal, "url_edit") and plat == "ehentai":
                    gal.url_edit.setText(url)
                QTimer.singleShot(120, lambda s=sub: s._start_flow(False))
            else:
                raise RuntimeError(f"未知平台 {plat}")
            # 若子页未立刻报 busy，稍后仍由定时器/超时收口
            QTimer.singleShot(1500, self._pre_dl_check_started)
        except Exception:
            log.exception("预下载启动失败 platform=%s", plat)
            # 启动失败：移入无法处理，避免反复开跑同一条
            try:
                store = getattr(self, "_pre_dl_store", None)
                if store is not None:
                    store.move_to_failed(plat, url)
                    store.mark_finished()
            except Exception:
                pass
            self._pre_dl_running = False
            self._pre_dl_current_plat = ""
            self._pre_dl_current_url = ""
            self._refresh_pre_dl_panels(plat)
            self._show_pre_dl_switch_toast(
                "无法处理", hint="无法启动", err=1, accent="err", stay=1800,
            )

    def _pre_dl_check_started(self):
        """启动后仍完全空闲 → 再给一段时间；仍无活动才按失败收口。"""
        if not getattr(self, "_pre_dl_running", False):
            return
        if getattr(self, "_pre_dl_was_busy", False):
            return
        if self._is_any_download_busy(ignore_pre_dl_flag=True):
            self._pre_dl_was_busy = True
            return
        # 图集/视频可能启动较慢：再等一轮
        if not getattr(self, "_pre_dl_start_retry", False):
            self._pre_dl_start_retry = True
            QTimer.singleShot(2500, self._pre_dl_check_started)
            return
        self._pre_dl_start_retry = False
        log.warning("预下载启动后无活动 platform=%s", getattr(self, "_pre_dl_current_plat", ""))
        # 无活动视为失败：不删记录，只收口 + 冷却
        self._pre_dl_finish_current(ok=False)

    def _highlight(self, active_btn):
        self._set_clock_btn_active(False)
        for b in self.side_btns:
            b.setProperty("kind", "side")
            b.setProperty("active", b is active_btn)
            b.style().unpolish(b)
            b.style().polish(b)
        # 底栏「系统总览」选中态
        btn_about = getattr(self, "btn_overview", None) or getattr(self, "btn_settings", None)
        if btn_about is not None:
            try:
                btn_about.setProperty("active", active_btn is btn_about)
                btn_about.style().unpolish(btn_about)
                btn_about.style().polish(btn_about)
            except Exception:
                pass
        # polish 会打乱叠放顺序，立刻把 LED 抬回最上
        self._raise_side_leds()

    def _raise_side_leds(self):
        """保证侧栏 LED / 预下载角标画在对应菜单按钮之上。"""
        for led in (
            getattr(self, "led_fast", None), getattr(self, "led_shot", None),
            getattr(self, "led_pre_dl", None),
            getattr(self, "led_region_rec", None),
        ):
            if led is None:
                continue
            try:
                if isinstance(led, SidePreDlBadge):
                    if led.count() > 0:
                        led.show()
                        led.raise_()
                else:
                    led.show()
                    led.raise_()
            except Exception:
                pass
        # 预/批互斥，只抬正在显示的那一个
        for batch, pre in (
            (getattr(self, "badge_batch_video", None), getattr(self, "badge_pre_video", None)),
            (getattr(self, "badge_batch_gallery", None), getattr(self, "badge_pre_gallery", None)),
        ):
            winner = None
            if batch is not None and batch.count() > 0:
                winner = batch
            elif pre is not None and pre.count() > 0:
                winner = pre
            if winner is not None and winner.isVisible():
                try:
                    winner.raise_()
                except Exception:
                    pass
        # 视频/图集进度线贴按钮底边，也需抬到按钮之上
        for prog in (
            getattr(self, "video_side_prog", None),
            getattr(self, "gallery_side_prog", None),
        ):
            if prog is not None and prog.isVisible():
                try:
                    prog.raise_()
                except Exception:
                    pass
        self._clip_nav_overlays()

    def _nav_overlay_host(self):
        """菜单浮动层的父级：导航视口（才能被滚动裁切）。"""
        nav = getattr(self, "nav_scroll", None)
        if nav is not None:
            try:
                return nav.viewport()
            except Exception:
                pass
        return getattr(self, "side_wrap", None)

    def _iter_nav_overlays(self):
        for w in (
            getattr(self, "led_fast", None),
            getattr(self, "led_shot", None),
            getattr(self, "led_region_rec", None),
            getattr(self, "badge_pre_video", None),
            getattr(self, "badge_batch_video", None),
            getattr(self, "badge_pre_gallery", None),
            getattr(self, "badge_batch_gallery", None),
            getattr(self, "video_side_prog", None),
            getattr(self, "gallery_side_prog", None),
        ):
            if w is not None:
                yield w

    def _reparent_nav_overlays(self):
        host = self._nav_overlay_host()
        if host is None:
            return
        for w in self._iter_nav_overlays():
            try:
                if w.parentWidget() is not host:
                    w.setParent(host)
                w.installEventFilter(self)
            except Exception:
                pass

    def _clip_overlay_to_nav_btn(self, overlay, btn):
        """菜单浮动开关：滚出视口则隐藏；半露出则裁切。

        空 QRegion 在 Qt 里常被当成「不裁」，按钮消失后开关还会露在外面。
        """
        if overlay is None or btn is None:
            return
        nav = getattr(self, "nav_scroll", None)
        host = overlay.parentWidget()
        if nav is None or host is None:
            try:
                overlay.clearMask()
            except Exception:
                pass
            return
        try:
            vp = nav.viewport()
            btn_in_host = QRect(btn.mapTo(host, QPoint(0, 0)), btn.size())
            if host is vp:
                vp_in_host = vp.rect()
            else:
                vp_in_host = QRect(vp.mapTo(host, QPoint(0, 0)), vp.size())
            vis_btn = btn_in_host.intersected(vp_in_host)
            geo = overlay.geometry()
            vis = geo.intersected(vis_btn)
            if vis.width() < 2 or vis.height() < 2:
                overlay.hide()
                overlay.clearMask()
                return
            if vis == geo:
                overlay.clearMask()
            else:
                overlay.setMask(QRegion(vis.translated(-geo.x(), -geo.y())))
        except Exception:
            try:
                overlay.clearMask()
            except Exception:
                pass

    def _clip_nav_overlays(self):
        """导航区内所有浮动层跟对应按钮一起裁切（底栏「预」除外）。"""
        for overlay, btn in (
            (getattr(self, "led_fast", None), getattr(self, "btn_fast", None)),
            (getattr(self, "led_shot", None), getattr(self, "btn_shot", None)),
            (getattr(self, "led_region_rec", None), getattr(self, "btn_region_rec", None)),
            (getattr(self, "badge_pre_video", None), getattr(self, "btn_douyin", None)),
            (getattr(self, "badge_batch_video", None), getattr(self, "btn_douyin", None)),
            (getattr(self, "badge_pre_gallery", None), getattr(self, "btn_gallery", None)),
            (getattr(self, "badge_batch_gallery", None), getattr(self, "btn_gallery", None)),
            (getattr(self, "video_side_prog", None), getattr(self, "btn_douyin", None)),
            (getattr(self, "gallery_side_prog", None), getattr(self, "btn_gallery", None)),
        ):
            if overlay is None or btn is None:
                continue
            self._clip_overlay_to_nav_btn(overlay, btn)

    def _reposition_leds(self):
        margin_right = 6
        led_h = 20
        led_w = 36

        # 「预」正方形开关：贴底栏「系统总览」按钮右侧垂直居中
        _foot_btn = getattr(self, "btn_overview", None) or getattr(self, "btn_settings", None)
        if _foot_btn is not None and getattr(self, "led_pre_dl", None) is not None:
            led_w = self.led_pre_dl.width() or SidePreDlSlot.W
            led_h = self.led_pre_dl.height() or SidePreDlSlot.H
            pos = _foot_btn.mapTo(self.side_wrap, _foot_btn.rect().topRight())
            y = pos.y() + (_foot_btn.height() - led_h) // 2
            x = pos.x() - led_w - margin_right
            self.led_pre_dl.move(x, y)

        host = self._nav_overlay_host() or self.side_wrap
        if getattr(self, "btn_fast", None) is not None and getattr(self, "led_fast", None) is not None:
            led_w = self.led_fast.width() or SideToggleSwitch.W
            led_h = self.led_fast.height() or SideToggleSwitch.H
            pos = self.btn_fast.mapTo(host, self.btn_fast.rect().topRight())
            y   = pos.y() + (self.btn_fast.height() - led_h) // 2
            x   = pos.x() - led_w - margin_right
            self.led_fast.move(x, y)

        if getattr(self, "btn_shot", None) is not None and getattr(self, "led_shot", None) is not None:
            pos = self.btn_shot.mapTo(host, self.btn_shot.rect().topRight())
            x = pos.x() - led_w - margin_right
            y = pos.y() + (self.btn_shot.height() - led_h) // 2
            self.led_shot.move(x, y)

        # 预下载/批处理条数：互斥，只显示一个
        self._reposition_count_badge_pair(
            getattr(self, "badge_batch_video", None),
            getattr(self, "badge_pre_video", None),
            getattr(self, "btn_douyin", None),
            margin_right,
        )
        self._reposition_count_badge_pair(
            getattr(self, "badge_batch_gallery", None),
            getattr(self, "badge_pre_gallery", None),
            getattr(self, "btn_gallery", None),
            margin_right,
        )

        # 区域录屏圆标 12×12，贴按钮右侧垂直居中
        rec_led = getattr(self, "led_region_rec", None)
        if rec_led is not None and getattr(self, "btn_region_rec", None) is not None:
            dw = rec_led.width() or SideRecordDot.SIZE
            dh = rec_led.height() or SideRecordDot.SIZE
            pos = self.btn_region_rec.mapTo(
                host, self.btn_region_rec.rect().topRight()
            )
            x = pos.x() - dw - margin_right
            y = pos.y() + (self.btn_region_rec.height() - dh) // 2
            rec_led.move(x, y)

        self._reposition_video_prog()
        self._reposition_gallery_prog()
        self._raise_side_leds()
        self._clip_nav_overlays()

    def _sync_side_count_pairs(self):
        """预/批共用数字列宽：两位起，随较大那个 N 加位，汉字列不动。"""
        for pre, batch in (
            (
                getattr(self, "badge_pre_video", None),
                getattr(self, "badge_batch_video", None),
            ),
            (
                getattr(self, "badge_pre_gallery", None),
                getattr(self, "badge_batch_gallery", None),
            ),
        ):
            ns = []
            for b in (pre, batch):
                if b is not None and b.count() > 0:
                    ns.append(b.count())
            slots = max([2] + [len(str(n)) for n in ns])
            for b in (pre, batch):
                if b is not None and b.count() > 0:
                    try:
                        b.set_digit_slots(slots)
                    except Exception:
                        pass

    def _reposition_count_badge_pair(self, batch, pre, btn, margin_right: int = 6):
        """预/批互斥：有批只显示批，否则显示预。"""
        if btn is None:
            return
        use_batch = batch is not None and batch.count() > 0
        shown = None
        hidden = []
        if use_batch:
            shown = batch
            if pre is not None:
                hidden.append(pre)
        else:
            if batch is not None:
                hidden.append(batch)
            if pre is not None and pre.count() > 0:
                shown = pre
            elif pre is not None:
                hidden.append(pre)
        for b in hidden:
            try:
                b.hide()
            except Exception:
                pass
        if shown is None:
            return
        bw = shown.width() or 28
        bh = shown.height() or SidePreDlBadge.H
        host = shown.parentWidget() or self._nav_overlay_host() or self.side_wrap
        pos = btn.mapTo(host, btn.rect().topRight())
        x = pos.x() - bw - margin_right
        y = pos.y() + (btn.height() - bh) // 2
        shown.move(x, y)
        shown.show()
        shown.raise_()
        self._clip_overlay_to_nav_btn(shown, btn)

    def _sync_side_badge_padding(self):
        """叠放后宽度与单条相同，清掉旧的并排加宽标记。"""
        for btn in (
            getattr(self, "btn_douyin", None),
            getattr(self, "btn_gallery", None),
        ):
            if btn is None:
                continue
            if not btn.property("hasDualBadge"):
                continue
            btn.setProperty("hasDualBadge", False)
            st = btn.style()
            if st is not None:
                st.unpolish(btn)
                st.polish(btn)

    def _on_region_rec_recording(self, recording: bool):
        """区域录屏开始/结束 → 侧栏圆标红点显隐。"""
        led = getattr(self, "led_region_rec", None)
        if led is not None:
            led.set_recording(bool(recording))

    def _reposition_side_prog(self, line, btn):
        """把进度线贴在指定侧栏按钮底边上（与按钮同宽，左右各缩 8px 对齐内边距）。

        按钮嵌在可滚动导航容器里，浮动层挂在视口上，用 mapTo 换到视口坐标。
        """
        wrap = (line.parentWidget() if line is not None else None) or self._nav_overlay_host()
        if line is None or btn is None or wrap is None:
            return
        tl = btn.mapTo(wrap, btn.rect().topLeft())
        r = QRect(tl, btn.size())
        inset = 8  # 与 #SideBar QPushButton[kind=side] padding 一致
        x = r.x() + inset
        w = max(8, r.width() - inset * 2)
        lh = line.line_height() if hasattr(line, "line_height") else 2
        y = r.y() + r.height() - lh  # 贴底边（解析 1px / 下载 2px）
        line.setGeometry(x, y, w, lh)
        if line.isVisible():
            line.raise_()
        self._clip_overlay_to_nav_btn(line, btn)

    def _reposition_video_prog(self):
        """把进度线贴在「视频下载」按钮底边上。"""
        self._reposition_side_prog(
            getattr(self, "video_side_prog", None),
            getattr(self, "btn_douyin", None),
        )

    def _reposition_gallery_prog(self):
        """把进度线贴在「图集下载」按钮底边上。"""
        self._reposition_side_prog(
            getattr(self, "gallery_side_prog", None),
            getattr(self, "btn_gallery", None),
        )

    def eventFilter(self, obj, event):
        nav = getattr(self, "nav_scroll", None)
        if obj is nav and event.type() == QEvent.Resize:
            self._sync_nav_scroll_btns()
        if event.type() == QEvent.Wheel and self._is_nav_wheel_source(obj):
            return self._apply_nav_wheel(event)
        rename_btns = getattr(self, "_side_header_rename_btns", None) or {}
        if obj in rename_btns.values():
            t = event.type()
            if t == QEvent.Enter:
                self._paint_section_rename_icon(obj, hover=True)
            elif t == QEvent.Leave:
                self._paint_section_rename_icon(obj, hover=False)
        return super().eventFilter(obj, event)

    def _is_nav_wheel_source(self, obj) -> bool:
        """滚轮落在侧栏菜单区域（含箭头、视口、分区头、浮动开关）时由我们接管。"""
        if obj is None:
            return False
        nav = getattr(self, "nav_scroll", None)
        if obj in (
            nav,
            getattr(self, "nav_container", None),
            getattr(self, "btn_nav_up", None),
            getattr(self, "btn_nav_down", None),
        ):
            return True
        if nav is not None:
            try:
                if obj is nav.viewport() or obj is nav.verticalScrollBar():
                    return True
            except Exception:
                pass
        for widgets in (getattr(self, "_side_headers", None) or {}).values():
            for hw in widgets or []:
                if obj is hw:
                    return True
                try:
                    if hw.isAncestorOf(obj):
                        return True
                except Exception:
                    pass
        try:
            for w in self._iter_nav_overlays():
                if obj is w:
                    return True
        except Exception:
            pass
        return False

    def _apply_nav_wheel(self, event) -> bool:
        """侧栏滚轮：一次物理拨动只挪 18px。

        Windows 默认「滚动三行」会连发 3 条 Wheel（或 angleDelta=360），
        按幅度换算会刚好等于一条菜单高。这里忽略幅度，短时合并成一步。
        """
        nav = getattr(self, "nav_scroll", None)
        if nav is None:
            return False
        try:
            bar = nav.verticalScrollBar()
        except Exception:
            return False
        pd_y = 0
        ad_y = 0
        inverted = False
        try:
            pd = event.pixelDelta()
            if pd is not None:
                pd_y = int(pd.y())
        except Exception:
            pd_y = 0
        try:
            ad_y = int(event.angleDelta().y())
        except Exception:
            ad_y = 0
        try:
            inverted = bool(event.inverted())
        except Exception:
            inverted = False
        if inverted:
            pd_y = -pd_y
            ad_y = -ad_y
        if not pd_y and not ad_y:
            return True

        now = time.perf_counter()
        last = float(getattr(self, "_nav_wheel_last_t", 0.0) or 0.0)

        # 触控板/高精度滚轮像素滚动：没有「一格三发」，按像素缩并封顶
        if pd_y and abs(ad_y) < 120:
            dy = int(round(pd_y / 2.0)) or (1 if pd_y > 0 else -1)
            dy = max(-16, min(16, dy))
            bar.setValue(bar.value() - dy)
            self._nav_wheel_last_t = now
            return True

        # 鼠标滚轮：90ms 内的重复事件视为同一格（三行设定）
        if (now - last) < _NAV_WHEEL_COALESCE_S:
            return True
        self._nav_wheel_last_t = now
        sign = 1 if (ad_y or pd_y) > 0 else -1
        bar.setValue(bar.value() - sign * _NAV_WHEEL_STEP)
        return True

    def _nudge_nav_scroll(self, direction: int):
        """direction: -1 上 / +1 下。点击箭头仍按约一条菜单高。"""
        nav = getattr(self, "nav_scroll", None)
        if nav is None:
            return
        bar = nav.verticalScrollBar()
        step = max(24, int(bar.singleStep() or 0), 36)
        bar.setValue(bar.value() + int(direction) * step)

    def _sync_nav_scroll_btns(self, *_args):
        """无溢出或到顶/到底时禁用对应箭头。"""
        nav = getattr(self, "nav_scroll", None)
        up = getattr(self, "btn_nav_up", None)
        down = getattr(self, "btn_nav_down", None)
        if nav is None or up is None or down is None:
            return
        bar = nav.verticalScrollBar()
        vmin, vmax = bar.minimum(), bar.maximum()
        val = bar.value()
        overflow = vmax > vmin
        up.setEnabled(overflow and val > vmin)
        down.setEnabled(overflow and val < vmax)
        up.setCursor(Qt.PointingHandCursor if up.isEnabled() else Qt.ArrowCursor)
        down.setCursor(Qt.PointingHandCursor if down.isEnabled() else Qt.ArrowCursor)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._reposition_leds)
        QTimer.singleShot(0, self._sync_nav_scroll_btns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_leds()
        self._sync_nav_scroll_btns()
        # 窗口大小变化写入 user.txt（防抖）；恢复几何时跳过
        if not getattr(self, "_restoring_geometry", False):
            try:
                self._schedule_save_user_prefs()
            except Exception:
                log.exception("窗口尺寸变化后保存偏好失败")

    def changeEvent(self, event):
        super().changeEvent(event)
        try:
            if event.type() == QEvent.WindowStateChange:
                if not getattr(self, "_restoring_geometry", False):
                    self._schedule_save_user_prefs()
        except Exception:
            log.exception("窗口状态变化后保存偏好失败")

    def _on_video_nav_progress(self, active: bool, pct: int, color: str):
        """视频页（抖音/YouTube）解析/下载 → 侧栏「视频下载」底边细线。"""
        bar = getattr(self, "video_side_prog", None)
        if bar is None:
            return
        bar.set_progress(bool(active), int(pct if pct is not None else 0), color or "")
        self._reposition_video_prog()

    def _on_gallery_nav_progress(self, active: bool, pct: int, color: str):
        """图集页解析/下载 → 侧栏「图集下载」底边细线。"""
        bar = getattr(self, "gallery_side_prog", None)
        if bar is None:
            return
        bar.set_progress(bool(active), int(pct if pct is not None else 0), color or "")
        self._reposition_gallery_prog()

    def _active_download_tasks(self) -> list:
        """收集当前仍在跑的下载任务名称（不含解析-only）。

        覆盖：视频下载（抖音 / B站 / YouTube）、图集下载（e-hentai / pixiv）。
        """
        labels = []

        def _dl_running(page) -> bool:
            if page is None:
                return False
            w = getattr(page, "_dl_worker", None)
            try:
                return bool(w is not None and w.isRunning())
            except Exception:
                return False

        video = getattr(self, "page_douyin", None)  # PageVideo 容器
        if video is not None:
            if _dl_running(getattr(video, "page_douyin", None)):
                labels.append("视频下载 · 抖音")
            if _dl_running(getattr(video, "page_bilibili", None)):
                labels.append("视频下载 · B站")
            if _dl_running(getattr(video, "page_youtube", None)):
                labels.append("视频下载 · YouTube")

        gal = getattr(self, "page_gallery", None)
        if gal is not None:
            if _dl_running(getattr(gal, "page_eh", None)):
                labels.append("图集下载 · e-hentai")
            if _dl_running(getattr(gal, "page_pixiv", None)):
                labels.append("图集下载 · pixiv")

        return labels

    def _confirm_close_while_downloading(self, tasks: list) -> bool:
        """下载进行中关窗：一次确认。确定关闭=关，返回程序=不关。"""
        from PyQt5.QtWidgets import QMessageBox

        names = "\n".join(f"  · {t}" for t in (tasks or []))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("下载进行中")
        box.setText(
            f"当前仍有下载任务未完成：\n{names}\n\n"
            "关闭程序将中断下载。"
        )
        btn_close = box.addButton("确定关闭", QMessageBox.AcceptRole)
        btn_back = box.addButton("返回程序", QMessageBox.RejectRole)
        box.setDefaultButton(btn_back)
        box.exec_()
        return box.clickedButton() is btn_close

    def _set_pre_dl_ui_frozen(self, frozen: bool):
        """批处理期间锁住预下载开关（侧栏「预」+ 系统总览），不改开关本身的开/关。"""
        frozen = bool(frozen)
        led = getattr(self, "led_pre_dl", None)
        if led is not None and hasattr(led, "setFrozen"):
            try:
                led.setFrozen(frozen)
            except Exception:
                log.exception("冻结侧栏预下载开关失败 frozen=%s", frozen)
        try:
            sec = getattr(getattr(self, "page_overview", None), "settings_section", None)
            sw = getattr(sec, "sw_pre_dl_auto", None) if sec is not None else None
            if sw is not None and hasattr(sw, "setFrozen"):
                sw.setFrozen(frozen)
        except Exception:
            log.exception("冻结系统总览预下载开关失败 frozen=%s", frozen)

    def _kick_pending_batch(self):
        """预下载当前条结束后，启动已排队的批处理。"""
        pv = getattr(self, "page_douyin", None)
        if pv is None:
            return
        if not getattr(pv, "_batch_mode", False):
            return
        if getattr(pv, "_batch_active", False):
            return
        if getattr(pv, "_batch_stop_requested", False):
            return
        try:
            pv._batch_next()
        except Exception:
            log.exception("预下载让路后启动批处理失败")

    def _pre_dl_yield_to_batch(self) -> bool:
        """批处理优先：不再开下一条预下载；若正在下，等当前条完成再让路。

        返回 True = 当前条还在下，批处理需要等。
        """
        self._pre_dl_paused_for_batch = True
        try:
            self._pre_dl_arm_timer.stop()
        except Exception:
            pass
        self._set_pre_dl_ui_frozen(True)
        if not getattr(self, "_pre_dl_running", False):
            self._batch_waiting_pre_dl = False
            return False
        plat = getattr(self, "_pre_dl_current_plat", "") or ""
        url = getattr(self, "_pre_dl_current_url", "") or ""
        self._batch_waiting_pre_dl = True
        log.info("批处理排队：等预下载当前条下完 platform=%s url=%s", plat, url[:80])
        return True

    def _pre_dl_resume_after_batch(self):
        """批处理结束：解开预下载开关；开关仍开则稍后继续队列。"""
        self._batch_waiting_pre_dl = False
        was = bool(getattr(self, "_pre_dl_paused_for_batch", False))
        self._pre_dl_paused_for_batch = False
        self._set_pre_dl_ui_frozen(False)
        if not was:
            return
        if getattr(self, "_pre_dl_running", False):
            return
        if not self.is_pre_dl_auto_process_enabled():
            return
        log.info("批处理结束，预下载准备恢复")
        QTimer.singleShot(800, self._pre_dl_try_schedule)

    def set_clipboard_auto_frozen(self, frozen: bool) -> bool:
        """批处理进行中：冻结速存「开始记录F6」、记录卡，并让预下载让路。

        F6 热键仍可中止批处理。预下载开关保持原状态，只是暂时不能点。
        返回 True = 预下载当前条还在下，批处理应等它完成。
        """
        frozen = bool(frozen)
        pf = getattr(self, "page_fast", None)
        if pf is not None and hasattr(pf, "set_record_btn_frozen"):
            try:
                pf.set_record_btn_frozen(frozen)
            except Exception:
                log.exception("设置速存记录 UI 冻结失败 frozen=%s", frozen)
        waiting = False
        if frozen:
            try:
                waiting = bool(self._pre_dl_yield_to_batch())
            except Exception:
                log.exception("批处理开始时暂停预下载失败")
        else:
            try:
                self._pre_dl_resume_after_batch()
            except Exception:
                log.exception("批处理结束时恢复预下载失败")
        return waiting

    def set_close_button_frozen(self, frozen: bool):
        """冻结/解冻标题栏关闭按钮（截图编辑等会话期间禁止关主程序）。

        Windows：灰化系统菜单 SC_CLOSE，标题栏 × 同步不可点。
        同时记标志，closeEvent / Alt+F4 一律 ignore，双保险。
        """
        frozen = bool(frozen)
        self._close_button_frozen = frozen
        if not sys.platform.startswith("win"):
            return
        try:
            hwnd = int(self.winId())
            if not hwnd:
                return
            user32 = ctypes.windll.user32
            # False=取当前菜单副本；改 Enable 后需 DrawMenuBar 刷新标题栏
            hmenu = user32.GetSystemMenu(hwnd, False)
            if not hmenu:
                return
            SC_CLOSE = 0xF060
            MF_BYCOMMAND = 0x00000000
            MF_ENABLED = 0x00000000
            MF_GRAYED = 0x00000001
            MF_DISABLED = 0x00000002
            if frozen:
                user32.EnableMenuItem(
                    hmenu, SC_CLOSE, MF_BYCOMMAND | MF_GRAYED | MF_DISABLED
                )
            else:
                user32.EnableMenuItem(hmenu, SC_CLOSE, MF_BYCOMMAND | MF_ENABLED)
            user32.DrawMenuBar(hwnd)
        except Exception:
            log.exception("设置主窗关闭按钮冻结失败 frozen=%s", frozen)

    def closeEvent(self, event):
        """关闭主窗口前，先停掉所有后台线程，避免 QThread destroyed 崩溃。

        子页挂在 QStackedWidget 里，Qt 不会单独触发它们的 closeEvent，
        必须在这里统一调用各页的 shutdown() / stop_background_checks()。

        若有下载任务进行中：弹窗确认一次，取消则忽略关窗。
        """
        # 截图编辑等：关闭按钮已冻结，Alt+F4 / 任务栏关闭也一律拦住
        if getattr(self, "_close_button_frozen", False):
            event.ignore()
            return

        # 下载中 → 确认一次；取消则不关
        try:
            tasks = self._active_download_tasks()
        except Exception:
            log.exception("检测下载任务失败")
            tasks = []
        if tasks:
            try:
                if not self._confirm_close_while_downloading(tasks):
                    event.ignore()
                    return
            except Exception:
                log.exception("关窗下载确认弹窗失败，按取消处理")
                event.ignore()
                return

        # 关窗前落盘用户习惯（速存/截图路径与开关等）
        try:
            self._save_user_prefs()
        except Exception:
            log.exception("关窗保存用户偏好失败")

        # 顺序：下载类 → 其它；关窗前统一停各页后台任务
        pages = (
            getattr(self, "page_fast", None),
            getattr(self, "page_voice", None),    # 语音录入
            getattr(self, "page_voice_clone", None),  # 语音克隆
            getattr(self, "page_game_assist", None),  # 游戏助手
            getattr(self, "page_douyin", None),   # PageVideo：内含抖音/B站/YouTube
            getattr(self, "page_gallery", None),
            getattr(self, "page_ratio", None),      # 工具·小偏门
            getattr(self, "page_dir_link", None),   # 内含磁盘扫描线程
            getattr(self, "page_tz_fx", None),
            getattr(self, "page_overview", None),
            getattr(self, "page_shot", None),
            getattr(self, "page_region_rec", None),
            getattr(self, "page_img_proc", None),
        )
        for p in pages:
            if p is None:
                continue
            for meth_name in ("shutdown", "stop_background_checks"):
                fn = getattr(p, meth_name, None)
                if not callable(fn):
                    continue
                try:
                    fn()
                except Exception:
                    log.exception("关窗停止页面任务失败 page=%s meth=%s",
                                  type(p).__name__, meth_name)
                break  # 同一页有 shutdown 就不再调 stop_background_checks

        # 停掉 keyboard 全局监听线程：Qt 卸载期间热键回调访问已释放内存会
        # 直接 access violation（page_screenshot 曾因此弃用 keyboard 库）。
        try:
            import keyboard
            try:
                keyboard.unhook_all()
            except Exception:
                pass
        except Exception:
            pass

        super().closeEvent(event)

    # ── 用户习惯 records/user.txt ─────────────────────────────
    def _collect_user_prefs(self) -> dict:
        import copy
        prefs = copy.deepcopy(getattr(self, "_user_prefs", None) or default_prefs())
        # 确保所有默认结构字段都存在（版本升级时可能新增 key）
        def_prefs = default_prefs()
        for _k, _v in def_prefs.items():
            if _k not in prefs:
                prefs[_k] = _v
            elif isinstance(_v, dict) and isinstance(prefs.get(_k), dict):
                for _sk, _sv in _v.items():
                    if _sk not in prefs[_k]:
                        prefs[_k][_sk] = _sv
        try:
            if self._page_enabled("fast"):
                prefs["fast_save"].update(self.page_fast.export_settings())
        except Exception:
            log.exception("导出速存设置失败")
        try:
            if self._page_enabled("shot"):
                prefs["screenshot"].update(self.page_shot.export_settings())
        except Exception:
            log.exception("导出截图设置失败")
        try:
            if self._page_enabled("region_rec"):
                prefs["region_record"].update(self.page_region_rec.export_settings())
        except Exception:
            log.exception("导出区域录屏设置失败")
        try:
            if self._page_enabled("gallery"):
                prefs["gallery"].update(self.page_gallery.export_settings())
        except Exception:
            log.exception("导出图集设置失败")
        try:
            if self._page_enabled("gallery"):
                pixiv = getattr(self.page_gallery, "page_pixiv", None)
                if pixiv and hasattr(pixiv, "export_settings"):
                    exported = pixiv.export_settings()
                    # 防止 UI 里空 cookie_path 覆盖掉已保存的值
                    if not exported.get("cookie_path", "").strip():
                        cur = (self._user_prefs or {}).get("gallery_pixiv", {})
                        saved = (cur.get("cookie_path") or "").strip() if isinstance(cur, dict) else ""
                        if saved:
                            exported["cookie_path"] = saved
                    prefs["gallery_pixiv"].update(exported)
        except Exception:
            log.exception("导出 Pixiv 图集设置失败")
        try:
            if self._page_enabled("points"):
                prefs["points_calc"].update(self.page_points.export_settings())
        except Exception:
            log.exception("导出积分计算设置失败")
        try:
            if self._page_enabled("img_proc"):
                if not isinstance(prefs.get("image_proc"), dict):
                    prefs["image_proc"] = {}
                prefs["image_proc"].update(self.page_img_proc.export_settings())
        except Exception:
            log.exception("导出图片处理设置失败")
        try:
            page = getattr(self, "page_prompt", None)
            if page is not None and hasattr(page, "export_settings"):
                prefs["prompt_editor"] = page.export_settings()
        except Exception:
            log.exception("导出提示词失败")
        try:
            page = getattr(self, "page_clock", None)
            if page is not None and hasattr(page, "export_settings"):
                prefs["clock"] = page.export_settings()
        except Exception:
            log.exception("导出电子钟设置失败")
        try:
            if self._page_enabled("voice"):
                prefs["voice_input"].update(self.page_voice.export_settings())
        except Exception:
            log.exception("导出语音录入设置失败")
        try:
            if self._page_enabled("voice_clone"):
                if not isinstance(prefs.get("voice_clone"), dict):
                    prefs["voice_clone"] = {}
                prefs["voice_clone"].update(self.page_voice_clone.export_settings())
        except Exception:
            log.exception("导出语音克隆设置失败")
        try:
            if self._page_enabled("game_assist"):
                if not isinstance(prefs.get("game_assist"), dict):
                    prefs["game_assist"] = {}
                prefs["game_assist"].update(self.page_game_assist.export_settings())
        except Exception:
            log.exception("导出游戏助手设置失败")
        try:
            if not isinstance(prefs.get("clipboard_auto"), dict):
                prefs["clipboard_auto"] = {}
            # 侧栏视频/图集自动下载开关已移除；保留键以免旧版读档报错
            prefs["clipboard_auto"]["video"] = False
            prefs["clipboard_auto"]["gallery"] = False
            prefs["clipboard_auto"]["auto_process"] = self.is_pre_dl_auto_process_enabled()
            # 白名单：关于页已 reload 过才以页面为准；否则保留内存/默认
            try:
                from utils.user_prefs import default_clipboard_whitelist
                existing = (prefs.get("clipboard_auto") or {}).get("whitelist")
                page = getattr(self, "page_about", None)
                if page is not None and getattr(page, "has_prefs_loaded", lambda: False)():
                    prefs["clipboard_auto"]["whitelist"] = page.export_whitelist()
                elif not existing:
                    prefs["clipboard_auto"]["whitelist"] = default_clipboard_whitelist()
            except Exception:
                log.exception("导出自动下载白名单失败")
        except Exception:
            log.exception("导出自动下载开关设置失败")
        try:
            if self._page_enabled("video"):
                prefs["video"] = self.page_douyin.export_settings()
        except Exception:
            log.exception("导出视频下载设置失败")
        prefs["ui"]["theme"] = "light" if self.is_light_theme else "dark"
        prefs["ui"]["scale"] = float(
            getattr(self, "_ui_scale", None) or self._read_active_qt_scale()
        )
        # 侧栏顺序：始终与当前导航一致
        try:
            prefs["ui"]["side_order"] = list(getattr(self, "_side_order", None) or [])
        except Exception:
            pass
        # 窗口尺寸：只记当前宽高，不记录最大化状态
        try:
            prefs["ui"]["window_w"] = max(_WIN_MIN_W, int(self.width()))
            prefs["ui"]["window_h"] = max(_WIN_MIN_H, int(self.height()))
        except Exception:
            prefs["ui"]["window_w"] = _WIN_MIN_W
            prefs["ui"]["window_h"] = _WIN_MIN_H
        return prefs

    def _save_user_prefs(self):
        try:
            save_user_prefs(self._collect_user_prefs())
        except Exception:
            log.exception("保存 user.txt 失败")

    def _schedule_save_user_prefs(self, *_args):
        """设置变更后 500ms 防抖写盘，避免拖动路径时狂写文件。"""
        try:
            self._prefs_timer.start(500)
        except Exception:
            self._save_user_prefs()

    def _wire_user_prefs_autosave(self):
        """把会影响习惯的控件变更接到防抖保存。"""
        if self._page_enabled("fast"):
            pf = self.page_fast
            for sig in (
                pf.chk_enable_all.toggled,
                pf.chk_imgonly.toggled,
                pf.chk_manual.toggled,
                pf.chk_mkdir.toggled,
                pf.save_path.textChanged,
                pf.mkdir_name.textChanged,
                pf.mkdir_name_b.textChanged,
                pf._txt_hotkey_group.buttonClicked,
                pf._mkdir_hotkey_group.buttonClicked,
            ):
                try:
                    sig.connect(self._schedule_save_user_prefs)
                except Exception:
                    pass
            # 序列文件检查：忽略扩展名 / 补全
            for attr in ("chk_ignore_ext", "chk_check_fill", "chk_new_record"):
                w = getattr(pf, attr, None)
                if w is not None:
                    try:
                        w.toggled.connect(self._schedule_save_user_prefs)
                    except Exception:
                        pass

        if self._page_enabled("shot"):
            sh = self.page_shot
            for sig in (
                sh.checkbox_enable.toggled,
                sh.path.textChanged,
                sh.chk_edit.toggled,
                sh.chk_copy.toggled,
                sh.chk_hide.toggled,
                sh.mod_group.buttonClicked,
                sh.key_group.buttonClicked,
                sh.fmt_group.buttonClicked,
            ):
                try:
                    sig.connect(self._schedule_save_user_prefs)
                except Exception:
                    pass

        if self._page_enabled("region_rec"):
            try:
                rr = self.page_region_rec
                for sig in (rr.chk_hide.toggled, rr.chk_cursor.toggled):
                    sig.connect(self._schedule_save_user_prefs)
            except Exception:
                pass

        # 图集下载：间隔 / 路径 / Cookie（控件名随版本变动，逐个判空接线，避免一处失效拖垮整组）
        if self._page_enabled("gallery"):
            gal = self.page_gallery
            try:
                for attr, sig_names in (
                    ("cmb_delay", ("currentTextChanged", "activated")),
                    ("save_edit", ("textChanged",)),
                    ("ck_path", ("textChanged",)),
                ):
                    w = getattr(gal, attr, None)
                    if w is None:
                        continue
                    for sig_name in sig_names:
                        sig = getattr(w, sig_name, None)
                        if sig is not None:
                            try:
                                sig.connect(self._schedule_save_user_prefs)
                            except Exception:
                                pass
                for sub_name in ("page_eh", "page_hitomi"):
                    sub = getattr(gal, sub_name, None)
                    if sub is None:
                        continue
                    for attr in ("sw_record_skip", "sw_record_skip_bar"):
                        sw = getattr(sub, attr, None)
                        if sw is None:
                            continue
                        try:
                            sw.clicked.connect(self._schedule_save_user_prefs)
                        except Exception:
                            pass
            except Exception:
                log.exception("接线图集偏好自动保存失败")

            # 图集下载 · Pixiv 分页：保存路径 / Cookie（共享间隔框在容器页已接，不重复）
            try:
                pixiv = getattr(self.page_gallery, "page_pixiv", None)
                if pixiv is not None:
                    for attr in ("save_edit", "ck_path"):
                        w = getattr(pixiv, attr, None)
                        if w is not None:
                            try:
                                w.textChanged.connect(self._schedule_save_user_prefs)
                            except Exception:
                                pass
            except Exception:
                log.exception("接线 Pixiv 图集偏好自动保存失败")

        # 视频下载：抖音 / B站 / YouTube 保存路径与 Cookie
        if self._page_enabled("video"):
            try:
                pv = self.page_douyin  # PageVideo 容器
                for sub in ("page_douyin", "page_bilibili", "page_youtube"):
                    sp = getattr(pv, sub, None)
                    if sp is None:
                        continue
                    for attr in ("save_edit", "ck_path"):
                        w = getattr(sp, attr, None)
                        if w is not None:
                            try:
                                w.textChanged.connect(self._schedule_save_user_prefs)
                            except Exception:
                                pass
            except Exception:
                log.exception("接线视频下载偏好自动保存失败")

        # 语音录入：录音方式 A/B
        if self._page_enabled("voice"):
            try:
                self.page_voice.radio_hold.toggled.connect(self._schedule_save_user_prefs)
                self.page_voice.radio_click.toggled.connect(self._schedule_save_user_prefs)
            except Exception:
                log.exception("接线语音录入偏好自动保存失败")

        try:
            self.btn_theme.clicked.connect(self._schedule_save_user_prefs)
        except Exception:
            pass

    def _load_user_prefs(self):
        """启动加载 records/user.txt。

        判断不能只看「文件在不在」：
          · 无文件 / 空文件 / 坏 JSON / 非本程序结构 → 按当前界面状态新建
          · 合法本程序配置 → 恢复到界面，并补写可能新增的字段
        """
        prefs, created_new = load_user_prefs()
        self._user_prefs = prefs
        log.info("加载用户偏好 created_new=%s", created_new)

        # 主题 / 缩放（先于业务页，避免控件刚刷完又切主题）
        # 新建档时用界面当前主题；合法文件才按 prefs 切主题
        ui_prefs = prefs.get("ui") or {}
        # 侧栏拖拽排序：应用保存的顺序（若从未拖过则保持默认）
        try:
            saved_order = ui_prefs.get("side_order")
            if isinstance(saved_order, list) and saved_order:
                self._apply_side_order(saved_order)
        except Exception:
            log.exception("恢复侧栏顺序失败")
        # 分类重命名：把保存过的名称刷到分区标签上
        try:
            self._apply_section_names(prefs)
        except Exception:
            log.exception("恢复分类名称失败")
        if not created_new:
            want_light = ui_prefs.get("theme", "dark") != "dark"
            if want_light != bool(self.is_light_theme):
                try:
                    self._toggle_theme()
                except Exception:
                    log.exception("应用主题偏好失败")
            try:
                if self._page_enabled("fast"):
                    self.page_fast.apply_settings(prefs.get("fast_save") or {})
            except Exception:
                log.exception("恢复速存设置失败")
            try:
                if self._page_enabled("shot"):
                    self.page_shot.apply_settings(prefs.get("screenshot") or {})
            except Exception:
                log.exception("恢复截图设置失败")
            try:
                if self._page_enabled("gallery"):
                    self.page_gallery.apply_settings(prefs.get("gallery") or {})
            except Exception:
                log.exception("恢复图集设置失败")
            try:
                if self._page_enabled("gallery"):
                    pixiv = getattr(self.page_gallery, "page_pixiv", None)
                    if pixiv and hasattr(pixiv, "apply_settings"):
                        pixiv.apply_settings(prefs.get("gallery_pixiv") or {})
            except Exception:
                log.exception("恢复 Pixiv 图集设置失败")
            try:
                if self._page_enabled("voice"):
                    self.page_voice.apply_settings(prefs.get("voice_input") or {})
            except Exception:
                log.exception("恢复语音录入设置失败")
            try:
                if self._page_enabled("voice_clone"):
                    self.page_voice_clone.apply_settings(prefs.get("voice_clone") or {})
            except Exception:
                log.exception("恢复语音克隆设置失败")
            try:
                if self._page_enabled("game_assist"):
                    self.page_game_assist.apply_settings(prefs.get("game_assist") or {})
            except Exception:
                log.exception("恢复游戏助手设置失败")
            # 侧栏自动下载开关已移除，忽略 clipboard_auto.video/gallery
            try:
                if self._page_enabled("video"):
                    self.page_douyin.apply_settings(prefs.get("video") or {})
            except Exception:
                log.exception("恢复视频下载设置失败")

        # 关于页控件（路径/Cookie/白名单）：新建档也要刷一次，避免空白名单被 _save 写回
        try:
            if hasattr(self, "page_about") and hasattr(self.page_about, "reload_from_prefs"):
                self.page_about.reload_from_prefs()
        except Exception:
            log.exception("恢复关于页设置失败")
        # 系统总览设置卡（界面缩放 / Cookie设置 / 公共保存）
        try:
            if hasattr(self, "page_overview") and hasattr(self.page_overview, "reload_from_prefs"):
                self.page_overview.reload_from_prefs()
        except Exception:
            log.exception("恢复系统总览设置失败")

        # 区域录屏路径：始终恢复（含新建档时由公共根目录派生）
        if self._page_enabled("region_rec"):
            try:
                rr = dict(prefs.get("region_record") or {})
                if not (rr.get("save_path") or "").strip():
                    base = (prefs.get("save_path_base") or "").strip()
                    if not base:
                        shot = ((prefs.get("screenshot") or {}).get("save_path") or "").replace("\\", "/")
                        if shot.endswith("/ScreenshotImageSaver"):
                            base = shot[: -len("/ScreenshotImageSaver")]
                    if not base:
                        try:
                            from utils.app_paths import user_downloads_dir
                            base = user_downloads_dir()
                        except Exception:
                            base = os.path.expanduser("~/Downloads")
                    rr["save_path"] = os.path.join(base, "RegionRecord").replace("\\", "/")
                    prefs["region_record"] = rr
                self.page_region_rec.apply_settings(rr)
            except Exception:
                log.exception("恢复区域录屏设置失败")

        # 界面缩放：只同步偏好显示值（真正缩放已在 mainv916 启动前设 QT_SCALE_FACTOR）
        try:
            want_scale = float(ui_prefs.get("scale", 1.0) or 1.0)
        except (TypeError, ValueError):
            want_scale = 1.0
        self._ui_scale = float(
            min(UI_SCALE_OPTIONS, key=lambda x: abs(x - want_scale))
        )

        # 窗口大小 / 最大化
        try:
            self._apply_window_geometry(ui_prefs)
        except Exception:
            log.exception("恢复窗口尺寸失败")

        # 积分汇率：无论是否新建档都要 apply（load 已 deep_merge 默认值）
        # 否则页面构造时的默认 7.25 会在首次 _save_user_prefs 时盖掉用户值
        try:
            self.page_points.apply_settings(prefs.get("points_calc") or {})
        except Exception:
            log.exception("恢复积分计算设置失败")

        # 图片处理：空路径时由公共根派生 …/ImageProc
        if self._page_enabled("img_proc"):
            try:
                ip = dict(prefs.get("image_proc") or {})
                if not (ip.get("save_path") or "").strip():
                    base = (prefs.get("save_path_base") or "").strip()
                    if not base:
                        shot = ((prefs.get("screenshot") or {}).get("save_path") or "").replace("\\", "/")
                        if shot.endswith("/ScreenshotImageSaver"):
                            base = shot[: -len("/ScreenshotImageSaver")]
                    if not base:
                        try:
                            from utils.app_paths import user_downloads_dir
                            base = user_downloads_dir()
                        except Exception:
                            base = os.path.expanduser("~/Downloads")
                    ip["save_path"] = os.path.join(base, "ImageProc").replace("\\", "/")
                    prefs["image_proc"] = ip
                self.page_img_proc.apply_settings(ip)
            except Exception:
                log.exception("恢复图片处理设置失败")

        # 提示词：始终恢复（空则尝试迁旧 editor.json）
        try:
            page = getattr(self, "page_prompt", None)
            if page is not None and hasattr(page, "apply_settings"):
                page.apply_settings(
                    prefs.get("prompt_editor") or {},
                    prefs.get("female_char") or {},
                )
        except Exception:
            log.exception("恢复提示词失败")
        try:
            page = getattr(self, "page_clock", None)
            if page is not None and hasattr(page, "apply_settings"):
                page.apply_settings(prefs.get("clock") or {})
        except Exception:
            log.exception("恢复电子钟设置失败")

        # 预下载开关：从 user.txt 恢复；本机无 Cookie 则强制关
        try:
            self._apply_pre_dl_auto_from_prefs(prefs, persist=False)
        except Exception:
            log.exception("恢复预下载开关失败")

        # 首次建档 / 无效重建 / 已有文件补全新字段：都写一份当前快照
        self._save_user_prefs()

    def _iter_configured_cookie_paths(self, prefs=None):
        """视频/图集各平台 Cookie 路径：(显示名, 路径)。"""
        prefs = prefs if isinstance(prefs, dict) else (getattr(self, "_user_prefs", None) or {})
        items = []
        v = prefs.get("video") or {}
        for key, label in (("douyin", "抖音"), ("bilibili", "B站"), ("youtube", "YouTube")):
            sub = v.get(key) if isinstance(v.get(key), dict) else {}
            items.append((label, (sub or {}).get("cookie_path", "")))
        for sec, label in (("gallery", "图集(EH)"), ("gallery_pixiv", "图集(Pixiv)")):
            s = prefs.get(sec) if isinstance(prefs.get(sec), dict) else {}
            items.append((label, (s or {}).get("cookie_path", "")))
        return items

    def _has_loaded_cookie(self, prefs=None) -> bool:
        """本机是否已加载至少一个有效 Cookie 文件。"""
        for _label, path in self._iter_configured_cookie_paths(prefs):
            p = (path or "").strip()
            if p and os.path.isfile(p):
                return True
        return False

    def _apply_pre_dl_auto_from_prefs(self, prefs=None, *, persist: bool = False):
        """按 user.txt 恢复预下载开关；本机未加载 Cookie 则强制关闭。"""
        prefs = prefs if isinstance(prefs, dict) else (getattr(self, "_user_prefs", None) or {})
        want = bool((prefs.get("clipboard_auto") or {}).get("auto_process"))
        if not self._has_loaded_cookie(prefs):
            if want:
                log.info("本机未加载 Cookie，预下载自动处理改为关闭")
            want = False
            ca = prefs.get("clipboard_auto")
            if isinstance(ca, dict):
                ca["auto_process"] = False
        self.set_pre_dl_auto_process_enabled(want, persist=persist)

    def _check_cookie_files_startup(self):
        """启动时检查 Cookie：缺失提示；本机一个都没加载则警告并关闭预下载。"""
        # 仅「视频 / 图集」页存在时才有意义
        if not self._page_enabled("video") and not self._page_enabled("gallery"):
            return
        prefs = getattr(self, "_user_prefs", None) or {}
        missing = []
        loaded = 0
        for label, path in self._iter_configured_cookie_paths(prefs):
            p = (path or "").strip()
            if p and os.path.isfile(p):
                loaded += 1
            elif p:
                missing.append(f"{label}（{p}）")

        no_cookie = loaded == 0
        if no_cookie:
            try:
                self.set_pre_dl_auto_process_enabled(False, persist=True, notify=False)
            except Exception:
                log.exception("无 Cookie 时关闭预下载失败")
            log.info("启动检测：本机未加载 Cookie，预下载已关闭")
            try:
                self._goto_overview_page()
                sec = getattr(
                    getattr(self, "page_overview", None), "settings_section", None
                )
                if sec is not None and hasattr(sec, "set_cookie_alert"):
                    sec.set_cookie_alert(
                        True,
                        "⚠ 未检测到已加载的 Cookie，预下载已关闭。请先加载 Cookie。",
                    )
            except Exception:
                log.exception("显示 Cookie 未加载提示失败")
            try:
                from styles.style_all import message_box_warn
                message_box_warn(
                    self,
                    "Cookie 未加载",
                    "本机尚未加载 Cookie 文件。\n\n"
                    "预下载自动处理已关闭。\n"
                    "请到「系统总览 → Cookie设置」加载后再打开预下载开关。",
                    auto_close_sec=5,
                )
            except Exception:
                log.exception("弹出 Cookie 未加载警告失败")
            return

        if missing:
            log.info("启动检测：%d 个 Cookie 文件缺失，在系统总览提示配置", len(missing))
            try:
                self._goto_overview_page()
                sec = getattr(
                    getattr(self, "page_overview", None), "settings_section", None
                )
                if sec is not None and hasattr(sec, "set_cookie_alert"):
                    sec.set_cookie_alert(True)
            except Exception:
                log.exception("显示 Cookie 缺失提示失败")

    def _apply_window_geometry(self, ui_prefs: dict):
        """从 user.txt 恢复主窗口宽高（不得小于最小尺寸）。"""
        ui_prefs = ui_prefs or {}
        try:
            w = int(ui_prefs.get("window_w") or 0)
            h = int(ui_prefs.get("window_h") or 0)
        except (TypeError, ValueError):
            w, h = 0, 0

        self._restoring_geometry = True
        try:
            # 取消可能残留的最大尺寸锁定，保证可拖拽放大
            self.setMaximumSize(16777215, 16777215)
            self.setMinimumSize(_WIN_MIN_W, _WIN_MIN_H)
            if w >= _WIN_MIN_W and h >= _WIN_MIN_H:
                self.resize(w, h)
            else:
                self.resize(_WIN_MIN_W, _WIN_MIN_H)
            QTimer.singleShot(0, self._end_restoring_geometry)
        except Exception:
            self._restoring_geometry = False
            raise

    def _end_restoring_geometry(self):
        self._restoring_geometry = False

    def _apply_dark_titlebar(self):
        try:
            hwnd = int(self.winId())
            value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass

    def _apply_qss(self, filename: str):
        """加载应用级样式表，并追加功能区标准卡 + 全站下拉 QSS。"""
        qss_path = os.path.join(ASSETS_DIR, filename)
        if os.path.exists(qss_path):
            with open(qss_path, 'r', encoding='utf-8') as f:
                base = f.read()
            # 功能区标准卡：以系统总览三块卡为基准，圆角/配色由 style_all 统一管理
            is_dark = not self.is_light_theme
            base = (
                base
                + "\n" + build_func_card_qss(is_dark=is_dark)
                # 全站下拉（圆角 + chevron + 弹出列表）
                + "\n" + build_combo_qss(is_dark=is_dark)
            )
            # 不再在 QSS 里逐像素放大：整站缩放交给 QT_SCALE_FACTOR（启动前设置）
            app = QApplication.instance()
            app.setStyleSheet(base)
            # Windows 系统深色模式时，控件会吃系统暗色 Base/Text → 亮色主题下
            # 出现「下拉暗底 + 深字看不清」。统一 palette + 强制 polish 全站下拉。
            apply_theme_palette(app, is_dark=is_dark)
            try:
                polish_combo_widgets(self)
            except Exception:
                pass

    def _apply_app_font(self):
        """全局默认字体（设计稿字号；物理大小由 QT_SCALE_FACTOR 放大）。"""
        f = QFont("微软雅黑")
        f.setPointSize(13)
        self.setFont(f)
        app = QApplication.instance()
        if app is not None:
            app.setFont(f)

    def _apply_logo_size(self):
        n = 40
        self.logo.setFixedSize(n, n)
        src = getattr(self, "_star_pix_src", None)
        if src is not None and not src.isNull():
            self.logo.setPixmap(
                src.scaled(n, n, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        else:
            self.logo.setText("★")

    def _apply_side_nav_metrics(self, btn: QPushButton, key: str = None):
        """侧栏导航按钮高度与图标（设计稿像素，由 Qt 全局缩放）。"""
        if key is None:
            key = str(btn.property("navKey") or "")
        icon_sz = side_nav_icon_size()
        if key:
            btn.setIcon(paint_side_nav_icon(key, icon_sz.width()))
        btn.setIconSize(icon_sz)
        btn.setFixedHeight(36)
        st = btn.style()
        if st is not None:
            st.unpolish(btn)
            st.polish(btn)

    def _apply_chrome_metrics(self):
        """主窗壳层固定尺寸：侧栏宽、底栏按钮、顶栏高度等（设计稿 1×）。"""
        if hasattr(self, "side_wrap") and self.side_wrap is not None:
            self.side_wrap.setFixedWidth(_SIDE_W)
        if hasattr(self, "logo"):
            self._apply_logo_size()
        for b in getattr(self, "side_btns", []) or []:
            key = str(b.property("navKey") or "")
            self._apply_side_nav_metrics(b, key)
        if hasattr(self, "btn_settings") and self.btn_settings is not None:
            self.btn_settings.setFixedHeight(38)
        for b in (
            getattr(self, "btn_nav_up", None),
            getattr(self, "btn_nav_down", None),
        ):
            if b is not None:
                b.setFixedHeight(SideNavScrollBtn.H)
        n = 36
        theme_btn = getattr(self, "btn_theme", None)
        if theme_btn is not None:
            theme_btn.setFixedSize(n, n)
            theme_btn.setMinimumSize(n, n)
            theme_btn.setMaximumSize(n, n)
        hdr = 34
        if hasattr(self, "search_wrap") and self.search_wrap is not None:
            self.search_wrap.setFixedHeight(hdr)
            self.search_wrap.setMinimumWidth(160)
        if hasattr(self, "search_icon") and self.search_icon is not None:
            self.search_icon.setFixedWidth(20)
        for btn in getattr(self, "_header_link_btns", []) or []:
            # 宽文案按钮（如 Github）只锁高度，保留可读宽度
            txt = (btn.text() or "").strip()
            if len(txt) > 2:
                btn.setFixedHeight(hdr)
                btn.setMinimumHeight(hdr)
                btn.setMaximumHeight(hdr)
                btn.setMinimumWidth(max(hdr, 64))
                btn.setMaximumWidth(16777215)
            else:
                btn.setFixedSize(hdr, hdr)
                btn.setMinimumSize(hdr, hdr)
                btn.setMaximumSize(hdr, hdr)
        try:
            self._refresh_clock_header_icon()
        except Exception:
            pass
        try:
            cw = self.findChild(QWidget, "ContentRoot")
            if cw is not None and cw.layout() is not None:
                cw.layout().setContentsMargins(14, 10, 14, 10)
                cw.layout().setSpacing(8)
        except Exception:
            pass
        self.setMinimumSize(_WIN_MIN_W, _WIN_MIN_H)
        self._style_sidebar_extras()

    @staticmethod
    def _read_active_qt_scale() -> float:
        """当前进程实际生效的 QT_SCALE_FACTOR（启动时写入）。"""
        try:
            s = float(os.environ.get("QT_SCALE_FACTOR", "1") or "1")
        except (TypeError, ValueError):
            s = 1.0
        return float(min(UI_SCALE_OPTIONS, key=lambda x: abs(x - s)))

    def _normalize_ui_scale(self, scale: float) -> float:
        try:
            s = float(scale)
        except (TypeError, ValueError):
            s = 1.0
        return float(min(UI_SCALE_OPTIONS, key=lambda x: abs(x - s)))

    def _release_global_hotkeys(self):
        """重启前先放掉本进程的 RegisterHotKey，避免新进程启动时撞上自己。"""
        pf = getattr(self, "page_fast", None)
        if pf is not None:
            for name in (
                "_unregister_txt_hotkey",
                "_unregister_mkdir_hotkey",
                "_unregister_recording_hotkey",
            ):
                fn = getattr(pf, name, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        log.exception("重启前注销速存热键失败 meth=%s", name)
        shot = getattr(self, "page_shot", None)
        if shot is not None:
            fn = getattr(shot, "_unbind_hotkey", None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    log.exception("重启前注销截图热键失败")
        rr = getattr(self, "page_region_rec", None)
        if rr is not None:
            fn = getattr(rr, "_unbind_hotkeys", None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    log.exception("重启前解绑录屏热键失败")

    def find_sibling_assistants(self):
        """其它正在跑的本程序进程（残留实例会占住 F4/F8 等热键）。"""
        hits = []
        try:
            import psutil
        except Exception:
            return hits
        me = os.getpid()
        my_exe = ""
        try:
            if getattr(sys, "frozen", False):
                my_exe = os.path.normcase(os.path.abspath(sys.executable))
        except Exception:
            my_exe = ""
        try:
            procs = psutil.process_iter(["pid", "name", "cmdline", "exe"])
        except Exception:
            return hits
        for p in procs:
            info = {}
            try:
                info = p.info or {}
            except Exception:
                continue
            if int(info.get("pid") or 0) == me:
                continue
            try:
                name = info.get("name") or ""
                cmd = info.get("cmdline") or []
                exe = info.get("exe") or ""
            except (psutil.Error, Exception):
                continue
            blob = " ".join(
                str(x) for x in (name, exe, *list(cmd))
            ).lower()
            if "mainv916" in blob:
                hits.append(p)
                continue
            if my_exe and exe:
                try:
                    if os.path.normcase(os.path.abspath(exe)) == my_exe:
                        hits.append(p)
                except Exception:
                    pass
        return hits

    def collect_hotkey_inventory(self):
        """各页当前全局热键 + 就绪/占用/未启用。"""
        rows = []
        for attr in ("page_fast", "page_shot", "page_region_rec"):
            page = getattr(self, attr, None)
            fn = getattr(page, "hotkey_inventory", None) if page is not None else None
            if not callable(fn):
                continue
            try:
                rows.extend(list(fn() or ()))
            except Exception:
                log.exception("收集热键清单失败 page=%s", attr)
        return rows

    def force_release_hotkeys(self):
        """结束本程序残留进程，并按当前开关重注册全部热键。"""
        import time

        killed = []
        for p in list(self.find_sibling_assistants() or ()):
            pid = 0
            try:
                pid = int(p.pid)
            except Exception:
                pid = 0
            try:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except Exception:
                    pass
                if p.is_running():
                    p.kill()
                    try:
                        p.wait(timeout=2)
                    except Exception:
                        pass
                killed.append(pid)
                log.info("已结束残留桌面助手进程 pid=%s", pid)
            except Exception:
                log.exception("结束残留进程失败 pid=%s", pid)
        try:
            time.sleep(0.2)
        except Exception:
            pass
        try:
            QApplication.processEvents()
        except Exception:
            pass

        rebound = []
        for attr in ("page_fast", "page_shot", "page_region_rec"):
            page = getattr(self, attr, None)
            fn = getattr(page, "rebind_hotkeys", None) if page is not None else None
            if not callable(fn):
                continue
            try:
                rebound.extend(list(fn() or ()))
            except Exception:
                log.exception("重注册热键失败 page=%s", attr)
        return {"killed": killed, "rebound": rebound}

    def _relaunch_app(self):
        """保存后重启自身，使新的 QT_SCALE_FACTOR 在创建 QApplication 前生效。"""
        import subprocess

        try:
            self._save_user_prefs()
        except Exception:
            log.exception("重启前保存偏好失败")

        try:
            self._release_global_hotkeys()
        except Exception:
            log.exception("重启前释放全局热键失败")

        if getattr(sys, "frozen", False):
            cmd = [sys.executable] + list(sys.argv[1:])
        else:
            script = os.path.abspath(sys.argv[0])
            cmd = [sys.executable, script] + list(sys.argv[1:])

        log.info("为界面缩放重启: %s", cmd)
        try:
            # 关键修复：PyInstaller onefile 运行时会用 _MEIPASS2 指向自己解压出的
            # 临时目录，并把它写进当前进程的环境变量。若子进程原样继承这个变量，
            # 子进程会误以为临时目录已就绪而跳过解压，直接复用它——但父进程退出时
            # 会清理/删除这个目录，两者之间存在竞态，导致子进程读到"文件不存在"
            # 且每次报错的文件都可能不同（这正是你看到的 pyi_rth_pkgres / Lorem
            # ipsum.txt 报错的根因）。这里必须去掉 _MEIPASS2，让子进程重新完整解
            # 压到自己独立的新临时目录，避免与旧进程的清理动作抢同一份文件。
            env = os.environ.copy()
            env.pop("_MEIPASS2", None)

            kwargs = {
                "cwd": os.getcwd(),
                "close_fds": True,
                "env": env,
            }
            if sys.platform == "win32":
                # 脱离当前控制台，避免父进程退出带走子进程
                CREATE_NEW_PROCESS_GROUP = 0x00000200
                DETACHED_PROCESS = 0x00000008
                kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
            subprocess.Popen(cmd, **kwargs)
        except Exception:
            log.exception("重启进程失败")
            try:
                message_box_warn(
                    self,
                    "界面缩放",
                    "无法自动重启。请手动关闭并重新打开本程序，新缩放才会完整生效。",
                )
            except Exception:
                pass
            return

        app = QApplication.instance()
        if app is not None:
            app.quit()
        else:
            sys.exit(0)

    def _apply_ui_scale(
        self,
        scale: float,
        *,
        resize_window: bool = True,
        from_prefs: bool = False,
        save: bool = True,
        restart: bool = True,
    ):
        """写入界面缩放偏好；与当前进程不一致时重启以套用 QT_SCALE_FACTOR。

        Qt 的全局缩放必须在 QApplication 创建前设置，运行中改 QSS 盖不全写死尺寸，
        故采用「存盘 + 重启」的粗暴方式，让整站（含写死像素）一起变大/变小。
        """
        del resize_window  # 兼容旧调用；物理尺寸由 Qt 全局缩放处理
        new = self._normalize_ui_scale(scale)
        active = self._read_active_qt_scale()

        self._ui_scale = new
        # 内部 layout 辅助保持 1×，勿与 QT_SCALE_FACTOR 叠乘
        try:
            theme.set_scale(1.0, quiet=True)
        except Exception:
            pass

        if save or not from_prefs:
            try:
                # 立即落盘，保证重启后 mainv916 能读到新倍数
                self._save_user_prefs()
            except Exception:
                log.exception("保存界面缩放失败")

        if from_prefs:
            # 启动恢复：缩放已由环境变量生效，只需对齐偏好值
            log.info(
                "界面缩放偏好 %s（进程 QT_SCALE_FACTOR=%s）",
                format_ui_scale(new),
                format_ui_scale(active),
            )
            return

        if abs(new - active) < 1e-6:
            log.info("界面缩放未变 %s", format_ui_scale(new))
            return

        log.info(
            "界面缩放 %s → %s，准备重启",
            format_ui_scale(active),
            format_ui_scale(new),
        )
        if restart:
            self._relaunch_app()

    # ── 侧栏导航排序：拖拽重排 / 分区头部 / 顺序持久化 ──────────────

    def _btn_for_key(self, key: str):
        """导航 key → 按钮控件（分项名槽位返回 None；key 与属性名并非一一对应）。"""
        if _is_hdr(key):
            return None
        attr = _NAV_BTN_ATTR.get(key, "")
        return getattr(self, attr, None) if attr else None

    def _section_enabled(self, sec: str) -> bool:
        """该分区在当前变体下是否有可用的按钮。"""
        for _s, _t, keys in _SIDE_SECTIONS:
            if _s == sec:
                return any(self._page_enabled(k) for k in keys)
        return False

    def _default_side_order(self) -> list:
        """默认顺序（含分项名槽位，按当前运行变体过滤）。"""
        out = []
        for sec, _t, keys in _SIDE_SECTIONS:
            enabled = [k for k in keys if self._page_enabled(k)]
            if not enabled:
                continue
            if sec != "pinned":
                out.append(_hdr_key(sec))
            out.extend(enabled)
        return out

    def _build_side_headers(self):
        """一次性创建各分区头部（标签 + 分割线；助手 / 工具带改名按钮）。"""
        self._side_header_labels = {}
        self._side_header_seps = {}
        self._side_header_rename_btns = {}
        self._side_headers = {}

        def _label(text, sec):
            w = QLabel(text)
            w.setObjectName("SideSectionLabel")
            w.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self._side_header_labels[sec] = w
            return w

        def _sep(sec):
            f = QFrame()
            f.setObjectName("SideSep")
            f.setFrameShape(QFrame.HLine)
            self._side_header_seps[sec] = f
            return f

        def _rename_hdr(text, sec):
            """助手 / 工具：标签 + 右侧改名铅笔按钮。"""
            hdr = QWidget()
            h = QHBoxLayout(hdr)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(2)
            lbl = _label(text, sec)
            lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            h.addWidget(lbl, 1, Qt.AlignVCenter)
            btn = QPushButton()
            btn.setObjectName("SideSectionRename")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFlat(True)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setAutoDefault(False)
            btn.setDefault(False)
            btn.setFixedSize(18, 18)
            btn.setToolTip("重命名分类")
            btn.clicked.connect(lambda: self._rename_section(sec))
            h.addWidget(btn, 0, Qt.AlignVCenter)
            self._side_header_rename_btns[sec] = btn
            return hdr

        self._side_headers = {
            "assistant": [_rename_hdr(_DEFAULT_SECTION_TITLES["assistant"], "assistant"), _sep("assistant")],
            "tool":      [_rename_hdr(_DEFAULT_SECTION_TITLES["tool"], "tool"), _sep("tool")],
        }
        for widgets in self._side_headers.values():
            for hw in widgets:
                hw.installEventFilter(self)
                for c in hw.findChildren(QWidget):
                    c.installEventFilter(self)
        self._restyle_side_headers()

    def _restyle_side_headers(self, *_args):
        """随主题重刷分区标签 / 分隔线 / 改名按钮配色（inline 样式的主题钩子）。"""
        try:
            label_qss = (
                f"color: {tk('text_faint')}; font-size: 11px; font-weight: 600; "
                f"padding: 6px 4px 6px 8px; margin: 0; background: transparent;"
            )
            sep_qss = (
                f"QFrame#SideSep{{background:{tk('border')}; max-height:1px; "
                f"min-height:1px; border:none;}}"
            )
            rename_qss = (
                f"QPushButton#SideSectionRename{{background:transparent;border:none;"
                f"padding:0;margin:0;min-width:18px;min-height:18px;border-radius:0;}}"
                f"QPushButton#SideSectionRename:hover,"
                f"QPushButton#SideSectionRename:pressed{{background:transparent;border:none;}}"
            )
            icon_color = tk("text_faint")
            hover_color = tk("text")
            for sec, lbl in (self._side_header_labels or {}).items():
                lbl.setStyleSheet(label_qss)
            for sec, f in (self._side_header_seps or {}).items():
                f.setStyleSheet(sep_qss)
            for w in (self._side_header_rename_btns or {}).values():
                w.setStyleSheet(rename_qss)
                w.setProperty("renameColor", icon_color)
                w.setProperty("renameHoverColor", hover_color)
                self._paint_section_rename_icon(w, hover=False)
        except Exception:
            log.exception("重刷侧栏分区样式失败")

    def _paint_section_rename_icon(self, btn, hover: bool = False):
        """分区改名按钮：线框铅笔，颜色随主题 / 悬停变化。"""
        if btn is None:
            return
        try:
            color = btn.property("renameHoverColor" if hover else "renameColor")
            if not color:
                color = tk("text" if hover else "text_faint")
            btn.setIcon(paint_header_rename_icon(14, str(color)))
            btn.setIconSize(QSize(14, 14))
            btn.setText("")
        except Exception:
            log.exception("刷新分区改名图标失败")

    def _rename_section(self, sec: str):
        """改名：QInputDialog 输入新标题 → 更新标签 → 写入 ui.section_names。"""
        try:
            from PyQt5.QtWidgets import QInputDialog
        except Exception:
            return
        prefs = getattr(self, "_user_prefs", None)
        names = {}
        if isinstance(prefs, dict):
            names = dict((prefs.get("ui") or {}).get("section_names") or {})
        current = names.get(sec) or _DEFAULT_SECTION_TITLES.get(sec, sec)
        text, ok = QInputDialog.getText(self, "重命名分类", "新名称：", text=current)
        if not ok:
            return
        new = (text or "").strip() or _DEFAULT_SECTION_TITLES.get(sec, sec)
        try:
            if not isinstance(prefs, dict):
                prefs = {}
                self._user_prefs = prefs
            ui = prefs.setdefault("ui", {})
            sn = dict(ui.get("section_names") or {})
            sn[sec] = new
            ui["section_names"] = sn
        except Exception:
            log.exception("保存分类名称失败")
        # 更新对应标签文字
        lbl = (self._side_header_labels or {}).get(sec)
        if lbl is not None:
            lbl.setText(new)
        try:
            self._save_user_prefs()
        except Exception:
            log.exception("保存分类名称失败")

    def _apply_section_names(self, prefs: dict):
        """把分类名称刷到侧栏标签；缺省则恢复出厂标题。"""
        try:
            names = (prefs or {}).get("ui", {}).get("section_names") or {}
            if not isinstance(names, dict):
                names = {}
            factory = {s: t for s, t, _ks in _SIDE_SECTIONS if t}
            for sec, lbl in (self._side_header_labels or {}).items():
                name = names.get(sec)
                if name:
                    lbl.setText(str(name))
                elif sec in factory:
                    lbl.setText(factory[sec])
        except Exception:
            log.exception("恢复分类名称失败")

    def _rebuild_nav(self):
        """按 _side_order 重建导航列表。

        _side_order 里分项名是固定槽位（@hdr:xxx），与按钮平级渲染；
        助手/工具槽位前各插一个 stretch，实现「助手区垂直居中、工具区贴底」。
        """
        layout = getattr(self, "nav_layout", None)
        if layout is None:
            return
        while layout.count():
            it = layout.takeAt(0)
            w = it.widget()
            if w is not None:
                layout.removeWidget(w)
        for item in self._side_order:
            if _is_hdr(item):
                sec = item[len("@hdr:"):]
                if sec in ("assistant", "tool"):
                    layout.addStretch(1)
                for hw in self._side_headers.get(sec, []):
                    layout.addWidget(hw)
            else:
                btn = self._btn_for_key(item)
                if btn is not None:
                    layout.addWidget(btn)
        QTimer.singleShot(0, self._reposition_leds)
        QTimer.singleShot(0, self._sync_nav_scroll_btns)
        self._raise_side_leds()

    def _apply_side_order(self, keys):
        """应用持久化的顺序：校验/去重按钮与分项名槽位，缺失的补齐。

        兼容旧格式（纯按钮列表，无槽位）：自动按按钮分区插回分项名槽位。
        保留已保存的相对顺序（含跨分区的摆放），槽位去重后按各自分区归位。
        """
        try:
            keys = keys or []
            if not any(_is_hdr(k) for k in keys):
                keys = self._inject_hdrs(keys)
            # 1) 校验 + 去重，保留原始相对顺序
            valid = []
            seen_btn = set()
            seen_hdr = set()
            for k in keys:
                if _is_hdr(k):
                    sec = k[len("@hdr:"):]
                    if sec in _SECTION_NAMES and self._section_enabled(sec) and sec not in seen_hdr:
                        valid.append(k)
                        seen_hdr.add(sec)
                elif k in _NAV_BTN_ATTR and self._page_enabled(k) and k not in seen_btn:
                    valid.append(k)
                    seen_btn.add(k)
            # 2) 每个启用分区都必须有唯一槽位，缺失的插到该分区第一个按钮之前
            for sec, _t, bkeys in _SIDE_SECTIONS:
                if sec == "pinned" or sec in seen_hdr or not self._section_enabled(sec):
                    continue
                pos = None
                for i, v in enumerate(valid):
                    if v in bkeys:
                        pos = i
                        break
                if pos is None:
                    pos = len(valid)
                valid.insert(pos, _hdr_key(sec))
                seen_hdr.add(sec)
            # 3) 补齐缺失的按钮：插到默认序中「前一项」之后（避免新页掉到整表末尾）
            default_order = self._default_side_order()
            for i, k in enumerate(default_order):
                if _is_hdr(k) or k in seen_btn:
                    continue
                # 找默认序里 k 之前、且已在 valid 中的最近一项
                insert_at = len(valid)
                for j in range(i - 1, -1, -1):
                    prev = default_order[j]
                    if prev in valid:
                        insert_at = valid.index(prev) + 1
                        break
                valid.insert(insert_at, k)
                seen_btn.add(k)
            # 4) 剔除误入的底栏项（系统总览 / 关于）
            valid = [k for k in valid if k not in ("about", "overview")]
            self._side_order = valid
            self._normalize_locked_side_order()
            self.side_btns = [
                b for b in (self._btn_for_key(k) for k in self._side_order) if b is not None
            ]
            self._rebuild_nav()
        except Exception:
            log.exception("应用侧栏顺序失败")

    @staticmethod
    def _inject_hdrs(button_order):
        """旧格式（纯按钮列表）→ 按按钮分区在首个按钮前插回分项名槽位。"""
        out = []
        placed = set()
        for k in button_order or []:
            sec = _SIDE_SECTION_MAP.get(k)
            if sec and sec != "pinned" and sec not in placed:
                out.append(_hdr_key(sec))
                placed.add(sec)
            out.append(k)
        return out

    def _normalize_locked_side_order(self):
        """锁定项归位：系统总览 / 关于都在底栏，不进可排序列表。"""
        order = list(getattr(self, "_side_order", None) or [])
        order = [k for k in order if k not in ("about", "overview")]
        self._side_order = order

    def _begin_side_nav_drag(self, key: str, btn, gpos):
        """原位按钮半透明仍占位；跟手半透明副本 + 插入线（不挤布局）。"""
        self._end_side_nav_drag()
        wrap = getattr(self, "side_wrap", None)
        if wrap is None or btn is None:
            return
        self._side_drag_key = key
        self._side_drag_btn = btn
        self._side_drag_grab = btn.mapFromGlobal(gpos)
        pix = None
        try:
            pix = btn.grab()
        except Exception:
            pix = None
        fx = QGraphicsOpacityEffect(btn)
        fx.setOpacity(0.5)
        btn.setGraphicsEffect(fx)
        try:
            btn.grabMouse()
        except Exception:
            pass
        ghost = QLabel(wrap)
        ghost.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        ghost.setAttribute(Qt.WA_StyledBackground, False)
        try:
            if pix is not None and not pix.isNull():
                ghost.setPixmap(pix)
            ghost.setFixedSize(btn.size())
        except Exception:
            ghost.setFixedSize(btn.size())
        gfx = QGraphicsOpacityEffect(ghost)
        gfx.setOpacity(0.55)
        ghost.setGraphicsEffect(gfx)
        ghost.show()
        self._side_drag_ghost = ghost
        guide = SideDropGuide(wrap)
        guide.hide()
        self._side_drop_guide = guide
        self._follow_side_nav_drag(gpos)
        self._place_side_drop_guide(key, self._drop_index(key, gpos))

    def _follow_side_nav_drag(self, gpos):
        wrap = getattr(self, "side_wrap", None)
        ghost = getattr(self, "_side_drag_ghost", None)
        btn = getattr(self, "_side_drag_btn", None)
        if wrap is None or ghost is None or btn is None:
            return
        grab = getattr(self, "_side_drag_grab", None)
        if grab is None:
            grab = QPoint(btn.width() // 2, btn.height() // 2)
        ghost.move(wrap.mapFromGlobal(gpos) - grab)
        ghost.show()
        ghost.raise_()
        guide = getattr(self, "_side_drop_guide", None)
        if guide is not None:
            guide.raise_()
        self._maybe_autoscroll_nav(gpos)

    def _slot_rect_in_wrap(self, key: str):
        """侧栏一项（按钮或分区头）在 side_wrap 坐标下的矩形。"""
        wrap = getattr(self, "side_wrap", None)
        if wrap is None or not key:
            return None
        w = None
        if _is_hdr(key):
            sec = key[len("@hdr:"):]
            hdrs = (getattr(self, "_side_headers", None) or {}).get(sec) or []
            w = hdrs[0] if hdrs else None
        else:
            w = self._btn_for_key(key)
        if w is None:
            return None
        try:
            return QRect(w.mapTo(wrap, QPoint(0, 0)), w.size())
        except Exception:
            return None

    def _side_visible_buttons(self, order) -> list:
        """order 中当前可见的导航按钮 key（分类名槽位不参与停车计数）。"""
        out = []
        for item in order or []:
            if _is_hdr(item):
                continue
            b = self._btn_for_key(item)
            if b is None or not b.isVisible():
                continue
            out.append(item)
        return out

    def _place_side_drop_guide(self, key: str, idx: int):
        """把插入线叠在落点缝上，不改布局。

        idx 与 _drop_index / _reorder_side 共用同一坐标系：
        others（去掉 key 的 _side_order）里第 idx 个元素之前，
        0=最前、len=最后。插入线永远贴按钮的顶边 / 底边，
        绝不压到分类名上：
        · 下方是按钮 → 画在下行按钮上沿（含分区顶部）
        · 下方是分类名 → 画在上方按钮下沿（上一分组底部）
        """
        guide = getattr(self, "_side_drop_guide", None)
        wrap = getattr(self, "side_wrap", None)
        if guide is None or wrap is None:
            return
        order = getattr(self, "_side_order", None) or []
        others = [k for k in order if k != key]
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            idx = 0
        n = len(others)
        if n <= 0:
            guide.hide()
            return
        idx = max(0, min(n, idx))
        r = None
        y = None
        if idx <= 0:
            r = self._slot_rect_in_wrap(others[0])
            y = r.top() if r is not None else None
        elif idx >= n:
            r = self._slot_rect_in_wrap(others[-1])
            y = r.bottom() if r is not None else None
        elif _is_hdr(others[idx]):
            # 下方是分类名：把按钮放在它上面那个按钮之后（分组底部）
            r = self._slot_rect_in_wrap(others[idx - 1])
            y = r.bottom() if r is not None else None
        else:
            # 下方是按钮：贴它的顶边（上方若是分类名即分区顶部）
            r = self._slot_rect_in_wrap(others[idx])
            y = r.top() if r is not None else None
        if r is None or y is None:
            guide.hide()
            return
        h = guide.height() or SideDropGuide.H
        guide.setFixedWidth(max(8, r.width()))
        guide.move(r.x(), y - h // 2)
        guide.show()
        guide.raise_()
        ghost = getattr(self, "_side_drag_ghost", None)
        if ghost is not None:
            ghost.raise_()

    def _maybe_autoscroll_nav(self, gpos):
        nav = getattr(self, "nav_scroll", None)
        if nav is None:
            return
        bar = nav.verticalScrollBar()
        if bar.maximum() <= bar.minimum():
            return
        vp = nav.viewport()
        top = vp.mapToGlobal(QPoint(0, 0)).y()
        bot = top + vp.height()
        y = gpos.y()
        band = 22
        if y < top + band:
            bar.setValue(bar.value() - 12)
        elif y > bot - band:
            bar.setValue(bar.value() + 12)

    def _end_side_nav_drag(self, rebuild: bool = True):
        """松手：原位恢复不透明，去掉跟手副本和插入线。"""
        del rebuild
        btn = getattr(self, "_side_drag_btn", None)
        if btn is not None:
            try:
                btn.releaseMouse()
            except Exception:
                pass
            try:
                btn.setGraphicsEffect(None)
            except Exception:
                pass
            try:
                btn.setCursor(Qt.PointingHandCursor)
                btn.setDown(False)
            except Exception:
                pass
        ghost = getattr(self, "_side_drag_ghost", None)
        guide = getattr(self, "_side_drop_guide", None)
        self._side_drag_key = None
        self._side_drag_btn = None
        self._side_drag_grab = None
        self._side_drag_ghost = None
        self._side_drop_guide = None
        for w in (ghost, guide):
            if w is None:
                continue
            try:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            except Exception:
                pass
        self._reposition_leds()

    def _drop_index(self, key: str, global_pos) -> int:
        """把 key 插到 others（去掉 key 的 _side_order）的哪个下标。

        只统计其它可见按钮的“中点”，分类名槽位 @hdr 不参与计数。
        下标 t 的含义：others[t] 之前（0=最前、len=最后）。
        同一缝隙若横跨分类名（上一分组底部 / 下一分区顶部两选一），
        按光标到上下两个按钮边的距离就近选择，插入线只贴按钮边，
        不会停在分类名上。
        """
        if key in _SIDE_LOCKED_KEYS:
            # 锁定项本身不可拖；若误算则保持原位
            try:
                return list(self._side_order).index(key)
            except ValueError:
                return 0
        order = getattr(self, "_side_order", None) or []
        others = [k for k in order if k != key]
        btns = self._side_visible_buttons(others)
        n = len(btns)
        if n <= 0:
            return 0
        y = global_pos.y()
        cnt = 0
        for item in btns:
            b = self._btn_for_key(item)
            try:
                if b.mapToGlobal(b.rect().center()).y() < y:
                    cnt += 1
            except Exception:
                continue
        if cnt <= 0:
            # 最前：放到第一个按钮之前（含它前面可能的槽位）
            return others.index(btns[0])
        if cnt >= n:
            # 最后：放到最后一个按钮之后
            return others.index(btns[-1]) + 1
        up = btns[cnt - 1]
        low = btns[cnt]
        pos_after_up = others.index(up) + 1   # 上一分组底部
        pos_before_low = others.index(low)    # 下一分区顶部
        if pos_after_up == pos_before_low:
            # 两个按钮挨着（中间没有分类名）：同一个缝
            return pos_before_low
        # up 与 low 之间有分类名 / 分隔线 / 弹性空档：按光标距哪边近吸附
        u = self._btn_for_key(up)
        l = self._btn_for_key(low)
        try:
            ub = u.mapToGlobal(QPoint(0, u.height())).y()
            lt = l.mapToGlobal(QPoint(0, 0)).y()
            if y < (ub + lt) / 2.0:
                return pos_after_up
        except Exception:
            pass
        return pos_before_low

    def _reorder_side(self, key: str, index: int):
        """把 key 挪到 others（去掉 key 后的 _side_order）的第 index 位。

        index 与 _drop_index / _place_side_drop_guide 共用同一坐标系：
        插到 others[index] 之前（0=最前、len=最后）。保证插入线的
        缝隙与松手后的最终落点一致，不会被分类名槽位顶乱。锁定项拒绝移动。
        """
        try:
            if key in _SIDE_LOCKED_KEYS:
                return
            order = self._side_order
            if key not in order:
                return
            order.remove(key)
            try:
                index = int(index)
            except (TypeError, ValueError):
                index = 0
            index = max(0, min(len(order), index))
            order.insert(index, key)
            self._normalize_locked_side_order()
            self.side_btns = [
                b for b in (self._btn_for_key(k) for k in self._side_order) if b is not None
            ]
            self._rebuild_nav()
        except Exception:
            log.exception("拖拽重排侧栏失败")

    def _persist_side_order(self):
        """拖拽结束：把侧栏顺序写入 user.txt（ui.side_order），立即落盘。"""
        try:
            self._normalize_locked_side_order()
            prefs = getattr(self, "_user_prefs", None)
            if prefs is None:
                return
            prefs.setdefault("ui", {})["side_order"] = list(self._side_order)
            self._save_user_prefs()
        except Exception:
            log.exception("保存侧栏顺序失败")

    def _style_sidebar_extras(self):
        """侧边栏分组标签与分割线（随主题内联）。"""
        lbl_qss = (
            f"color: {tk('text_faint')}; font-size: 11px; font-weight: 600; "
            f"padding: 8px 8px 2px 8px; margin: 0; background: transparent;"
        )
        sep_qss = (
            f"QFrame#SideSep{{background:{tk('border')}; max-height:1px; "
            f"min-height:1px; border:none;}}"
        )
        if getattr(self, "lbl_assistant", None) is not None:
            self.lbl_assistant.setStyleSheet(lbl_qss)
        if getattr(self, "lbl_tool", None) is not None:
            self.lbl_tool.setStyleSheet(lbl_qss)
        for sep in (
            getattr(self, "sep_assistant", None),
            getattr(self, "sep_tool", None),
        ):
            if sep is not None:
                sep.setStyleSheet(sep_qss)
        # 预下载 / 批处理角标主题色
        for badge in (
            getattr(self, "badge_pre_video", None),
            getattr(self, "badge_pre_gallery", None),
            getattr(self, "badge_batch_video", None),
            getattr(self, "badge_batch_gallery", None),
        ):
            if badge is not None and hasattr(badge, "refresh_theme"):
                try:
                    badge.refresh_theme()
                except Exception:
                    pass

    def _show_settings_about(self, cookie_alert=False):
        """兼容旧调用：打开系统总览（原配置内容已并入该页）。"""
        self._goto_overview_page()

    def _goto_overview_page(self):
        """打开底栏「系统总览」。"""
        page = getattr(self, "page_overview", None)
        btn = getattr(self, "btn_overview", None)
        if page is None:
            log.warning("系统总览未初始化")
            return
        try:
            self._switch(self.stack.indexOf(page), btn)
        except Exception:
            log.exception("打开系统总览失败")
            return
        try:
            if hasattr(page, "on_enter"):
                page.on_enter()
        except Exception:
            log.exception("系统总览 on_enter 失败")

    def _on_overview_cookies_configured(self):
        """系统总览配好 Cookie 后，收起「Cookie设置」卡内的缺失告警条。"""
        try:
            sec = getattr(
                getattr(self, "page_overview", None), "settings_section", None
            )
            if sec is not None and hasattr(sec, "set_cookie_alert"):
                sec.set_cookie_alert(False)
        except Exception:
            pass

    def _close_app_log_handlers(self):
        """关闭 app.log 文件句柄，便于删除/清空 records。"""
        import logging
        import utils.logger as logger_mod

        root = logging.getLogger("deskassist")
        for h in list(root.handlers):
            try:
                h.flush()
            except Exception:
                pass
            try:
                h.close()
            except Exception:
                pass
            try:
                root.removeHandler(h)
            except Exception:
                pass
        try:
            logger_mod._CONFIGURED = False
        except Exception:
            pass

    # ── 数据管理：导出 / 导入 / 重置（覆盖全部本地业务数据）────────
    def _local_data_roots(self):
        """[(zip 前缀, 绝对路径)]：records + 图集下载历史 + 速存 cards。"""
        from utils.app_paths import local_data_roots
        return local_data_roots()

    def _wipe_dir_contents(self, dir_path: str, label: str = ""):
        """删除目录内全部文件/子目录。返回 (cleared_names, errors)。"""
        import shutil

        cleared, errors = [], []
        if not os.path.isdir(dir_path):
            return cleared, errors
        prefix = f"{label}/" if label else ""
        for name in list(os.listdir(dir_path)):
            path = os.path.join(dir_path, name)
            tag = f"{prefix}{name}"
            try:
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                cleared.append(tag)
            except Exception as e:
                # Windows 上偶发文件锁：尽量截断为空
                try:
                    if os.path.isfile(path):
                        with open(path, "w", encoding="utf-8") as f:
                            f.truncate(0)
                        cleared.append(f"{tag}(已清空)")
                    else:
                        errors.append(f"{tag}: {e}")
                except Exception as e2:
                    errors.append(f"{tag}: {e2}")
        return cleared, errors

    def _wipe_records_dir(self):
        """兼容旧调用：清空 data/。"""
        from utils.app_paths import data_dir
        return self._wipe_dir_contents(data_dir(), "data")

    def _wipe_all_local_data(self):
        """清空导出范围内全部本地数据目录。"""
        cleared, errors = [], []
        for label, path in self._local_data_roots():
            c, e = self._wipe_dir_contents(path, label)
            cleared.extend(c)
            errors.extend(e)
        return cleared, errors

    def _copy_tree_merge(self, src_dir: str, dst_dir: str) -> int:
        """把 src_dir 下内容复制到 dst_dir（覆盖同名）。返回复制条目数。"""
        import shutil

        if not os.path.isdir(src_dir):
            return 0
        os.makedirs(dst_dir, exist_ok=True)
        n = 0
        for name in os.listdir(src_dir):
            s = os.path.join(src_dir, name)
            d = os.path.join(dst_dir, name)
            if os.path.isdir(s):
                if os.path.exists(d):
                    shutil.rmtree(d, ignore_errors=True)
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)
            n += 1
        return n

    def _apply_prefs_to_all_pages(self, prefs: dict, *, allow_scale_restart: bool = False) -> bool:
        """把 prefs 刷到全部业务页与壳层控件。

        allow_scale_restart=True 且缩放与当前进程不一致时会重启应用并返回 True
        （调用方应立即 return，后续逻辑不会执行）。
        """
        prefs = prefs if isinstance(prefs, dict) else default_prefs()
        self._user_prefs = prefs
        ui_prefs = prefs.get("ui") or {}

        # 主题
        want_light = ui_prefs.get("theme", "dark") != "dark"
        if want_light != bool(getattr(self, "is_light_theme", False)):
            try:
                self._toggle_theme()
            except Exception:
                log.exception("应用主题失败")

        # 侧栏顺序 / 分区名 / 窗口
        try:
            order = ui_prefs.get("side_order") or self._default_side_order()
            self._apply_side_order(order)
        except Exception:
            log.exception("应用侧栏顺序失败")
        try:
            self._apply_section_names(prefs)
        except Exception:
            log.exception("应用分区名称失败")
        try:
            self._apply_window_geometry(ui_prefs)
        except Exception:
            log.exception("应用窗口尺寸失败")

        # 缩放
        try:
            want_scale = float(ui_prefs.get("scale", 1.0) or 1.0)
            want_scale = self._normalize_ui_scale(want_scale)
            self._ui_scale = want_scale
            if allow_scale_restart and abs(want_scale - self._read_active_qt_scale()) > 1e-6:
                self._apply_ui_scale(
                    want_scale, from_prefs=False, save=True, restart=True
                )
                return True
        except Exception:
            log.exception("应用界面缩放失败")

        # 区域录屏路径：空则由公共根 / 截图路径派生（与启动加载一致）
        try:
            rr = dict(prefs.get("region_record") or {})
            if not (rr.get("save_path") or "").strip():
                base = (prefs.get("save_path_base") or "").strip()
                if not base:
                    shot = ((prefs.get("screenshot") or {}).get("save_path") or "").replace("\\", "/")
                    if shot.endswith("/ScreenshotImageSaver"):
                        base = shot[: -len("/ScreenshotImageSaver")]
                if not base:
                    try:
                        from utils.app_paths import user_downloads_dir
                        base = user_downloads_dir()
                    except Exception:
                        base = os.path.expanduser("~/Downloads")
                rr["save_path"] = os.path.join(base, "RegionRecord").replace("\\", "/")
                prefs["region_record"] = rr
                self._user_prefs = prefs
        except Exception:
            log.exception("派生区域录屏路径失败")

        # 图片处理路径：空则由公共根派生 …/ImageProc
        try:
            ip = dict(prefs.get("image_proc") or {})
            if not (ip.get("save_path") or "").strip():
                base = (prefs.get("save_path_base") or "").strip()
                if not base:
                    shot = ((prefs.get("screenshot") or {}).get("save_path") or "").replace("\\", "/")
                    if shot.endswith("/ScreenshotImageSaver"):
                        base = shot[: -len("/ScreenshotImageSaver")]
                if not base:
                    try:
                        from utils.app_paths import user_downloads_dir
                        base = user_downloads_dir()
                    except Exception:
                        base = os.path.expanduser("~/Downloads")
                ip["save_path"] = os.path.join(base, "ImageProc").replace("\\", "/")
                prefs["image_proc"] = ip
                self._user_prefs = prefs
        except Exception:
            log.exception("派生图片处理路径失败")

        try:
            vc = dict(prefs.get("voice_clone") or {})
            if not (vc.get("save_path") or "").strip():
                base = (prefs.get("save_path_base") or "").strip()
                if not base:
                    shot = ((prefs.get("screenshot") or {}).get("save_path") or "").replace("\\", "/")
                    if shot.endswith("/ScreenshotImageSaver"):
                        base = shot[: -len("/ScreenshotImageSaver")]
                if not base:
                    try:
                        from utils.app_paths import user_downloads_dir
                        base = user_downloads_dir()
                    except Exception:
                        base = os.path.expanduser("~/Downloads")
                vc["save_path"] = os.path.join(base, "VoiceClone").replace("\\", "/")
                prefs["voice_clone"] = vc
                self._user_prefs = prefs
        except Exception:
            log.exception("派生语音克隆路径失败")

        # 各业务页设置
        page_keys = (
            (getattr(self, "page_fast", None), "fast_save"),
            (getattr(self, "page_shot", None), "screenshot"),
            (getattr(self, "page_region_rec", None), "region_record"),
            (getattr(self, "page_img_proc", None), "image_proc"),
            (getattr(self, "page_gallery", None), "gallery"),
            (getattr(self, "page_points", None), "points_calc"),
            (getattr(self, "page_voice", None), "voice_input"),
            (getattr(self, "page_voice_clone", None), "voice_clone"),
            (getattr(self, "page_game_assist", None), "game_assist"),
            (getattr(self, "page_douyin", None), "video"),  # PageVideo 容器
            (getattr(self, "page_prompt", None), "prompt_editor"),
            (getattr(self, "page_clock", None), "clock"),
        )
        for page, key in page_keys:
            if page is None or not hasattr(page, "apply_settings"):
                continue
            try:
                page.apply_settings(prefs.get(key) or {})
            except Exception:
                log.exception("恢复 %s 设置失败", key)

        # 图集 Pixiv 独立段（与 gallery 嵌套段并存）
        try:
            gal = getattr(self, "page_gallery", None)
            pixiv = getattr(gal, "page_pixiv", None) if gal is not None else None
            if pixiv is not None and hasattr(pixiv, "apply_settings"):
                pixiv.apply_settings(prefs.get("gallery_pixiv") or {})
        except Exception:
            log.exception("恢复 Pixiv 图集设置失败")

        # 自动下载侧栏开关 + 白名单（关于页）
        try:
            ca = prefs.get("clipboard_auto") or {}
            if hasattr(self, "led_video_auto"):
                self.led_video_auto.setChecked(bool(ca.get("video", False)))
            if hasattr(self, "led_gallery_auto"):
                self.led_gallery_auto.setChecked(bool(ca.get("gallery", False)))
        except Exception:
            log.exception("恢复自动下载开关失败")

        # 文件型业务数据：粘贴仓库 / 积分历史 / 时区城市 / 速存 cards
        try:
            if hasattr(self, "page_paste") and hasattr(self.page_paste, "_reload"):
                self.page_paste._reload()
        except Exception:
            log.exception("刷新粘贴助手失败")
        try:
            if hasattr(self, "page_points") and hasattr(self.page_points, "_refresh_records"):
                self.page_points._refresh_records()
        except Exception:
            log.exception("刷新积分记录失败")
        try:
            tz = getattr(self, "page_tz_fx", None)
            if tz is not None and hasattr(tz, "reload_cities_from_disk"):
                tz.reload_cities_from_disk()
        except Exception:
            log.exception("刷新时区城市失败")
        try:
            pf = getattr(self, "page_fast", None)
            if pf is not None and hasattr(pf, "_refresh_record_cards"):
                pf._refresh_record_cards()
        except Exception:
            log.exception("刷新速存记录卡片失败")

        # 侧栏 LED / 设置卡
        try:
            self._sync_fast_led()
        except Exception:
            pass
        try:
            if hasattr(self, "led_shot") and hasattr(self, "page_shot"):
                self.led_shot.setChecked(self.page_shot.checkbox_enable.isChecked())
        except Exception:
            pass
        try:
            if hasattr(self, "page_about") and hasattr(self.page_about, "reload_from_prefs"):
                self.page_about.reload_from_prefs()
        except Exception:
            log.exception("刷新关于页失败")
        try:
            if hasattr(self, "page_overview") and hasattr(self.page_overview, "reload_from_prefs"):
                self.page_overview.reload_from_prefs()
        except Exception:
            log.exception("刷新系统总览设置失败")
        try:
            self._apply_pre_dl_auto_from_prefs(prefs, persist=False)
        except Exception:
            log.exception("应用预下载开关失败")
        return False

    def _apply_factory_defaults_after_records_reset(self):
        """磁盘清空后：界面与内存配置恢复出厂默认，并写回干净 user.txt。"""
        prefs = default_prefs()
        self._user_prefs = prefs
        self._ui_scale = 1.0
        try:
            self._apply_prefs_to_all_pages(prefs, allow_scale_restart=False)
        except Exception:
            log.exception("重置后应用默认设置失败")
        try:
            self._save_user_prefs()
        except Exception:
            log.exception("重置后保存默认 user.txt 失败")
        # 缩放：默认 1×；与当前进程不一致则重启
        try:
            if abs(self._read_active_qt_scale() - 1.0) > 1e-6:
                self._apply_ui_scale(1.0, from_prefs=False, save=True, restart=True)
        except Exception:
            log.exception("重置后应用默认缩放失败")

    def _on_export_records(self, parent=None):
        """数据管理「导出」：打包 data/（含卡片与图集去重记录）。"""
        from datetime import datetime
        from PyQt5.QtWidgets import QFileDialog, QMessageBox
        import zipfile

        parent = parent or self
        try:
            self._save_user_prefs()
        except Exception:
            log.exception("导出前保存偏好失败")

        roots = self._local_data_roots()
        has_any = False
        for _label, d in roots:
            if os.path.isdir(d) and os.listdir(d):
                has_any = True
                break
        if not has_any:
            message_box_info(parent, "导出", "没有可导出的本地数据。")
            return

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"桌面助手_数据备份_{stamp}.zip"
        path, _ = QFileDialog.getSaveFileName(
            parent,
            "导出本地数据",
            default_name,
            "ZIP 文件 (*.zip)",
        )
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"

        try:
            n_files = 0
            bundles = []
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                for label, dir_path in roots:
                    if not os.path.isdir(dir_path):
                        continue
                    count_here = 0
                    for root, _dirs, files in os.walk(dir_path):
                        for name in files:
                            if name.endswith(".tmp"):
                                continue
                            full = os.path.join(root, name)
                            rel = os.path.relpath(full, dir_path).replace("\\", "/")
                            arc = f"{label}/{rel}"
                            try:
                                zf.write(full, arcname=arc)
                                n_files += 1
                                count_here += 1
                            except Exception:
                                log.exception("导出跳过文件 path=%s", full)
                    if count_here:
                        bundles.append(f"{label}({count_here})")
            log.info("导出本地数据 → %s files=%s bundles=%s", path, n_files, bundles)
            message_box_info(
                parent,
                "导出完成",
                f"已导出 {n_files} 个文件到：\n{path}\n\n"
                f"包含：{', '.join(bundles) if bundles else '（空）'}\n"
                "· data：偏好 / 粘贴仓库 / 积分历史 / 时区城市 / 日志\n"
                "· data（card_*.txt）：速存记录模式卡片\n"
                "· 图集去重记录（gallery_eh.txt / gallery_hitomi.txt）",
            )
        except Exception as e:
            log.exception("导出本地数据失败")
            message_box_critical(parent, "导出失败", f"无法写入 ZIP：\n{e}")

    def _on_import_records(self, parent=None):
        """数据管理「导入」：从 ZIP 恢复本地数据并刷新全部界面。"""
        from PyQt5.QtWidgets import QFileDialog, QMessageBox
        import shutil
        import tempfile
        import zipfile
        from utils.app_paths import data_dir, flatten_data_subdirs

        parent = parent or self
        path, _ = QFileDialog.getOpenFileName(
            parent,
            "导入本地数据",
            "",
            "ZIP 文件 (*.zip);;所有文件 (*.*)",
        )
        if not path:
            return
        if not os.path.isfile(path):
            message_box_warn(parent, "导入", "所选文件不存在。")
            return

        reply = QMessageBox.warning(
            parent,
            "导入本地数据",
            "导入将覆盖 data/ 下的本地数据：\n"
            "· 偏好 / 粘贴 / 积分 / 时区城市 / 日志\n"
            "· 图集下载去重记录\n"
            "· 速存记录卡片\n\n"
            "旧版 records / gallery / cards 备份仍可导入。\n"
            "建议先点「导出」备份当前数据。\n\n是否继续导入？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            self._close_app_log_handlers()
        except Exception:
            log.exception("导入前关闭日志句柄失败")

        try:
            with tempfile.TemporaryDirectory(prefix="deskassist_import_") as td:
                with zipfile.ZipFile(path, "r") as zf:
                    for info in zf.infolist():
                        name = info.filename.replace("\\", "/")
                        if name.startswith("/") or name.startswith("../") or "/../" in f"/{name}/":
                            continue
                        if name.endswith("/"):
                            continue
                        target = os.path.normpath(os.path.join(td, name))
                        if not target.startswith(os.path.normpath(td) + os.sep):
                            continue
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        with zf.open(info, "r") as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)

                # 兼容 ZIP 根下多包一层 deskassist_data / 桌面助手_xxx
                scan_root = td
                entries = [e for e in os.listdir(td) if e not in (".", "..")]
                known = {"data", "records", "gallery", "cards"}
                if len(entries) == 1:
                    only = os.path.join(td, entries[0])
                    if os.path.isdir(only):
                        inner = set(os.listdir(only))
                        if known & {x.lower() for x in inner} or entries[0].lower() in known:
                            scan_root = only

                # 新：data/；中：records + gallery + cards；旧：扁平 user.txt 等
                top = set(os.listdir(scan_root)) if os.path.isdir(scan_root) else set()
                top_l = {x.lower(): x for x in top}
                markers = {
                    "user.txt", "paste_helper.txt", "points_calc.txt",
                    "app.log", "world_clock_cities.txt",
                    "pre_download.json", "gallery_eh.txt", "gallery_hitomi.txt",
                }
                has_data = "data" in top_l and os.path.isdir(
                    os.path.join(scan_root, top_l["data"])
                )
                has_legacy_roots = bool({"records", "gallery", "cards"} & set(top_l.keys()))
                flat_records = bool(markers & top) and not has_data and not has_legacy_roots

                if not has_data and not has_legacy_roots and not flat_records:
                    if "records" in top_l:
                        only = os.path.join(scan_root, top_l["records"])
                        if os.path.isdir(only) and os.listdir(only):
                            has_legacy_roots = True
                        else:
                            raise RuntimeError("ZIP 内没有可用文件")
                    else:
                        raise RuntimeError(
                            "ZIP 内没有可识别的数据（需要 data/、records/ 或 user.txt）"
                        )

                dest = data_dir()
                cleared, wipe_errs, copied = [], [], 0
                c, e = self._wipe_dir_contents(dest, "data")
                cleared.extend(c)
                wipe_errs.extend(e)

                if has_data:
                    copied += self._copy_tree_merge(
                        os.path.join(scan_root, top_l["data"]), dest
                    )
                elif has_legacy_roots:
                    rec_key = top_l.get("records")
                    if rec_key:
                        src = os.path.join(scan_root, rec_key)
                        if os.path.isdir(src):
                            copied += self._copy_tree_merge(src, dest)
                    gal_key = top_l.get("gallery")
                    if gal_key:
                        src = os.path.join(scan_root, gal_key)
                        if os.path.isdir(src):
                            copied += self._copy_tree_merge(src, dest)
                    cards_key = top_l.get("cards")
                    if cards_key:
                        src = os.path.join(scan_root, cards_key)
                        if os.path.isdir(src):
                            # 先进 data/cards 临时落地，再统一平铺改名（card_*）
                            copied += self._copy_tree_merge(
                                src, os.path.join(dest, "cards")
                            )
                else:
                    copied += self._copy_tree_merge(scan_root, dest)

                # 旧备份里的 data/cards、data/prompts、data/voice_clone 等子目录
                # 可能被原样合回：平铺上提，避免“有文件但程序不再读目录”
                try:
                    flatten_data_subdirs()
                except Exception:
                    log.exception("导入后平铺 data 子目录失败")

            log.info(
                "导入本地数据 ← %s copied=%s cleared=%s wipe_errs=%s "
                "has_data=%s has_legacy=%s flat=%s",
                path, copied, cleared, wipe_errs,
                has_data, has_legacy_roots, flat_records,
            )
        except Exception as e:
            log.exception("导入本地数据失败")
            try:
                from utils.logger import get_logger as _gl
                _gl(__name__)
            except Exception:
                pass
            message_box_critical(parent, "导入失败", f"无法导入：\n{e}")
            return

        try:
            from utils.logger import get_logger as _gl
            _gl(__name__).info("导入后日志已重新打开")
        except Exception:
            pass

        try:
            self._apply_records_from_disk()
        except Exception:
            log.exception("导入后应用配置失败")

        message_box_info(
            parent,
            "导入完成",
            "本地数据已从备份恢复，界面设置已刷新。",
        )

    def _apply_records_from_disk(self):
        """从磁盘重载偏好与各页数据（导入后用）。"""
        prefs, _created = load_user_prefs()
        # 缩放不一致会重启，由 allow_scale_restart 处理
        self._apply_prefs_to_all_pages(prefs, allow_scale_restart=True)

    def _on_reset_records(self, parent=None):
        """数据管理「重置」：清空全部本地数据目录并恢复出厂设置。"""
        from PyQt5.QtWidgets import QMessageBox

        parent = parent or self
        reply = QMessageBox.warning(
            parent,
            "重置本地数据",
            "将清除 data/ 下全部本地业务数据，包括：\n"
            "· 用户偏好、粘贴仓库、积分历史、时区城市、日志\n"
            "· 图集下载去重记录\n"
            "· 速存记录模式卡片\n"
            "· 侧栏顺序 / 分区名 / 主题 / 缩放 / 各页路径与 Cookie 配置\n\n"
            "此操作不可撤销。是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            self._close_app_log_handlers()
        except Exception:
            log.exception("关闭日志句柄失败")

        cleared, errors = [], []
        try:
            cleared, errors = self._wipe_all_local_data()
        except Exception:
            log.exception("清空本地数据失败")
            message_box_critical(parent, "重置失败", "清空本地数据时出错，请查看日志。")
            return

        try:
            from utils.logger import get_logger as _gl
            _gl(__name__).info(
                "用户重置本地数据：cleared=%s errors=%s", cleared, errors
            )
        except Exception:
            pass

        try:
            self._apply_factory_defaults_after_records_reset()
        except Exception:
            log.exception("重置后恢复默认设置失败")

        n = len(cleared)
        msg = f"已清除 {n} 项本地数据，设置已恢复默认。"
        if errors:
            msg += "\n\n以下项未能完全删除：\n" + "\n".join(errors[:8])
            if len(errors) > 8:
                msg += f"\n…共 {len(errors)} 项"
        message_box_info(parent, "重置完成", msg)

    def _toggle_theme(self):
        self.is_light_theme = not self.is_light_theme
        name = "light" if self.is_light_theme else "dark"

        # 浅色界面显示月亮（切到深色）；深色界面显示太阳（切到浅色）
        self.btn_theme.setText("🌙" if self.is_light_theme else "☀")
        self._set_titlebar_theme(not self.is_light_theme)

        # 1) 应用级 QSS
        self._apply_qss('app_light.qss' if self.is_light_theme else 'app.qss')

        # 2) 广播给所有控件级样式的订阅者
        theme.set_theme(name)

        # 3) 主窗口自身的内联样式
        self._style_sidebar_extras()

        # 4) 侧边栏按钮用了 property 选择器，需要重新 polish
        for b in self.side_btns:
            b.style().unpolish(b); b.style().polish(b)
        if hasattr(self, "btn_settings"):
            self.btn_settings.style().unpolish(self.btn_settings)
            self.btn_settings.style().polish(self.btn_settings)
        if hasattr(self, "btn_theme"):
            self.btn_theme.style().unpolish(self.btn_theme)
            self.btn_theme.style().polish(self.btn_theme)

        # 5) 全站功能区标准卡：按新主题重刷内联外观（与系统总览三块一致）
        restyle_all_func_cards(self)
        # 6) 下拉再 polish 一次（主题切换后系统暗色绘制残留）
        try:
            polish_combo_widgets(self)
        except Exception:
            pass
        # 7) 关于页内联控件（Cookie 胶囊、白名单芯片等）
        try:
            if hasattr(self, "page_about") and hasattr(self.page_about, "restyle_theme"):
                self.page_about.restyle_theme()
        except Exception:
            log.exception("主题切换后重刷关于页失败")
        try:
            if hasattr(self, "page_prompt") and hasattr(self.page_prompt, "restyle_theme"):
                self.page_prompt.restyle_theme()
        except Exception:
            log.exception("主题切换后重刷提示词失败")
        try:
            self._refresh_clock_header_icon()
        except Exception:
            pass

    def _set_titlebar_theme(self, is_dark):
        # 动态控制 Windows 原生标题栏颜色
        try:
            hwnd = int(self.winId())
            value = ctypes.c_int(1 if is_dark else 0)
            for attr in (20, 19):   # 20=新版 Win10/11，19=20H1 之前的旧属性号
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass
