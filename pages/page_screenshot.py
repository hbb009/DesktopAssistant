from styles.style_all import (
    TEXT_STYLE,
    install_card_title,
    make_card,
    apply_folder_path_edit,
    restyle_folder_path_edit,
    apply_medium_button,
    apply_simple_record,

    CARD_LEFT_GAP,
    CARD_TOP_GAP,
    CARD_RIGHT_GAP,
    CARD_BOTTOM_GAP,
    theme,
    tk,
    attach_ok_auto_close,
    set_message_box_selectable,
)
from utils.logger import get_logger

log = get_logger(__name__)

# 全局 QWidget 兜底背景会给没显式声明 background:transparent 的 QLabel/QRadioButton/
# QCheckBox 刷上不透明色块（见 card_ui_standard.md 3.3），TEXT_STYLE 本身不含这条，
# 这里补一份"叠加透明背景"的版本，本页所有用 TEXT_STYLE 的控件统一改用这个。
TEXT_STYLE_T = TEXT_STYLE + " background: transparent;"

import os
import sys
import math
import subprocess
from datetime import datetime

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QCheckBox, QFileDialog,
    QButtonGroup, QRadioButton, QMenu, QApplication, QFrame, QSizePolicy,
    QLineEdit as _QLineEdit, QToolButton, QDialog, QTextEdit,
    QAbstractItemView,
)
from PyQt5.QtCore import (
    Qt, QRect, QPoint, QSize, QTimer, pyqtSignal, QObject, QThread, QRectF, QPointF,
    QAbstractNativeEventFilter, QCoreApplication, QEvent,
)
from PyQt5.QtGui import (
    QPixmap, QPainter, QPen, QBrush, QColor, QIcon, QFont, QPolygon, QPainterPath,
    QConicalGradient,
)
import ctypes
from utils.file_utils import ensure_dir
from utils.cursor_toast import show_cursor_toast

# ── 全局热键（Windows RegisterHotKey，与速存图文同路）────────────────
# 不再用 keyboard 库：其 Windows 消息循环存在 LPMSG() 空指针问题，
# 热键「注册成功」但收不到按键，表现为截图快捷键完全无响应。
try:
    import ctypes.wintypes as _wintypes
except Exception:  # pragma: no cover
    _wintypes = None

_WM_HOTKEY = 0x0312
_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_NOREPEAT = 0x4000
# 避开速存页占用的 0x1002–0x1006
_SHOT_HOTKEY_ID = 0x1010
_MOD_MAP = {"ctrl": _MOD_CONTROL, "alt": _MOD_ALT, "shift": _MOD_SHIFT}


class _ShotHotkeyFilter(QAbstractNativeEventFilter):
    """把 WM_HOTKEY 转到主线程回调（与 page_fast_save 一致）。"""

    def __init__(self, callback, hotkey_id: int):
        super().__init__()
        self._cb = callback
        self._hid = int(hotkey_id)

    def nativeEventFilter(self, eventType, message):
        try:
            if eventType not in ("windows_generic_MSG", "windows_dispatcher_MSG"):
                return False, 0
            if _wintypes is None:
                return False, 0
            msg = _wintypes.MSG.from_address(int(message))
            if msg.message == _WM_HOTKEY and int(msg.wParam) == self._hid:
                cb = self._cb
                if callable(cb):
                    try:
                        cb()
                    except Exception:
                        log.exception("截图热键回调失败")
                return True, 0
        except Exception:
            log.exception("截图热键消息过滤失败")
        return False, 0


# ============================================================
# 通用工具
# ============================================================
def open_in_file_manager(path, select=True):
    """在系统文件管理器中打开（select=True 时选中该文件）。跨平台兜底。"""
    try:
        norm = os.path.normpath(path)
        if sys.platform.startswith("win"):
            if select and os.path.isfile(norm):
                subprocess.Popen(["explorer", "/select,", norm])
            else:
                os.startfile(norm if os.path.isdir(norm) else os.path.dirname(norm))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", norm] if select else ["open", norm])
        else:
            subprocess.Popen(["xdg-open", norm if os.path.isdir(norm) else os.path.dirname(norm)])
    except Exception:
        try:
            os.startfile(os.path.dirname(path))
        except Exception:
            log.exception("打开截图目录失败 path=%s", path)


def open_file(path):
    """用系统默认程序打开文件。"""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        log.exception("打开截图文件失败 path=%s", path)


class _Signal(QObject):
    trigger = pyqtSignal()


# ============================================================
# 截图记录列表（对齐速存图文 FastRunLogList）
# · 新记录：始终选中最新 + 强制滚屏
# · 点空白：取消选中
# ============================================================
class ShotRunLogList(QListWidget):
    def addItem(self, *args, **kwargs):
        super().addItem(*args, **kwargs)
        self._focus_latest(force_select=True)

    def _focus_latest(self, force_select: bool = True):
        try:
            n = self.count()
            if n <= 0:
                return
            last = self.item(n - 1)
            if last is None:
                return
            if force_select and self.currentItem() is not last:
                self.setCurrentItem(last)
            self.scrollToItem(last, QAbstractItemView.PositionAtBottom)
            self.scrollToBottom()
            QTimer.singleShot(0, self._scroll_latest_again)
        except Exception:
            pass

    def _scroll_latest_again(self):
        try:
            n = self.count()
            if n <= 0:
                return
            last = self.item(n - 1)
            if last is None:
                return
            self.scrollToItem(last, QAbstractItemView.PositionAtBottom)
            self.scrollToBottom()
        except Exception:
            pass

    def mousePressEvent(self, event):
        it = self.itemAt(event.pos())
        if it is None:
            self.clearSelection()
            self.setCurrentRow(-1)
            event.accept()
            return
        super().mousePressEvent(event)


# ============================================================
# 预览标签：绑定当前选中记录（图 / 操作文案 / 空）
# ============================================================
class PreviewLabel(QLabel):
    _PLACEHOLDER = "（选中左侧记录可预览）"

    def __init__(self):
        super().__init__()
        self._src = None
        self._mode = "empty"  # empty | image | text
        self.setAlignment(Qt.AlignCenter)
        # 与速存预览一致：主窗最小时预览区约 2/5 宽，140 为可用下限
        self.setMinimumSize(140, 140)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._apply_style()
        self.setText(self._PLACEHOLDER)

    def minimumSizeHint(self):
        return QSize(140, 140)

    def sizeHint(self):
        return QSize(220, 220)

    def refresh_theme(self, *_):
        self._apply_style()

    def _apply_style(self):
        self.setStyleSheet(f"background:transparent; color:{tk('text_dim')};")

    def show_empty(self, hint: str = None):
        self._src = None
        self._mode = "empty"
        self._apply_style()
        self.clear()
        self.setTextFormat(Qt.PlainText)
        self.setAlignment(Qt.AlignCenter)
        self.setText(hint if hint is not None else self._PLACEHOLDER)

    def set_image(self, pixmap):
        self._src = pixmap if (pixmap is not None and not pixmap.isNull()) else None
        if self._src is None:
            self.show_empty()
            return
        self._mode = "image"
        self._apply_style()
        self.clear()
        self.setTextFormat(Qt.PlainText)
        self.setAlignment(Qt.AlignCenter)
        self._rescale()

    def set_text_content(self, text: str):
        """操作类记录：主区展示文案。"""
        self._src = None
        self._mode = "text"
        self._apply_style()
        self.clear()
        self.setTextFormat(Qt.PlainText)
        self.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.setText(text or self._PLACEHOLDER)

    def _rescale(self):
        if self._mode != "image" or self._src is None:
            return
        self.setPixmap(self._src.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, e):
        self._rescale()
        super().resizeEvent(e)


# ============================================================
# 顶层窗口枚举（Windows · 识别窗口用）
# ============================================================
# EnumWindows 按 Z 序（前→后）。遮罩盖住全屏后 WindowFromPoint 会命中自己，
# 因此改用「缓存顶层窗矩形 + 光标命中」；只认可见、非最小化的顶层程序窗。
_SKIP_WIN_CLASSES = frozenset({
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "DV2ControlHost", "MsgrIMEWindowClass", "SysShadow",
    "Windows.UI.Core.CoreWindow",  # UWP 宿主杂窗，多数无用
})


def _enum_toplevel_window_rects(exclude_hwnd=0):
    """返回 [(QRect 屏幕坐标·与 GetWindowRect 同单位, 标题), ...]，Z 序前→后。"""
    if not sys.platform.startswith("win"):
        return []
    try:
        import win32gui
        import win32con
    except ImportError:
        return []

    out = []

    def _cb(hwnd, _):
        try:
            if exclude_hwnd and int(hwnd) == int(exclude_hwnd):
                return True
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if win32gui.IsIconic(hwnd):
                return True
            cls = win32gui.GetClassName(hwnd) or ""
            if cls in _SKIP_WIN_CLASSES:
                return True
            ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            # 工具窗且非 AppWindow：开始菜单碎片、悬浮工具条等
            if (ex & win32con.WS_EX_TOOLWINDOW) and not (ex & win32con.WS_EX_APPWINDOW):
                return True
            title = (win32gui.GetWindowText(hwnd) or "").strip()
            l, t, r, b = win32gui.GetWindowRect(hwnd)
            if (r - l) < 16 or (b - t) < 16:
                return True
            # UWP 空壳 ApplicationFrameWindow / 无标题幽灵窗：避免悬停框满半屏
            if not title and cls in ("Button", "ApplicationFrameWindow"):
                return True
            out.append((QRect(l, t, r - l, b - t), title))
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        return []
    return out


# ============================================================
# 全屏框选遮罩（高分屏坐标已修正；直接从冻结画面裁剪，不含红框）
# 可选：识别顶层窗口 — 悬停高亮，单击截整窗；拖拽仍自由框选
# ============================================================
class Overlay(QWidget):
    _DRAG_THRESH = 6  # 超过此像素视为自由框选，而非「点选窗口」

    def __init__(self, on_capture, on_cancel, detect_window=False):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.start = self.end = None
        self._dragging = False
        self.on_capture = on_capture
        self.on_cancel = on_cancel
        self.detect_window = bool(detect_window) and sys.platform.startswith("win")
        self._hover_rect = None   # 遮罩本地逻辑坐标
        self._hover_title = ""
        self._win_cache = []      # [(overlay-local QRect, title), ...]

        self._build_background()

        self.setGeometry(self._screen_geo)
        self.show()
        # 多显示器下 Windows 可能把无边框窗钳制回主屏，show 后再校正一次
        self.setGeometry(self._screen_geo)
        self.raise_(); self.activateWindow(); self.setFocus()

        if self.detect_window:
            # 等 winId 就绪后再枚举，排除本遮罩
            QTimer.singleShot(0, self._build_window_cache)

    def _build_background(self):
        """抓取整个虚拟桌面（覆盖所有显示器），统一到参考 dpr 坐标系。

        self.bg        —— 物理像素的整桌合成图
        self.dpr       —— 参考缩放（以主屏抓图为准）
        self._screen_geo —— 虚拟桌面逻辑范围（原点可能为负）
        """
        screens = QApplication.screens()
        if not screens:
            screens = [QApplication.primaryScreen()]

        virtual = QRect(screens[0].geometry())
        for s in screens[1:]:
            virtual = virtual.united(s.geometry())

        # 参考 dpr：以主屏抓图尺寸比为准（与原逻辑一致，最稳）
        ref_dpr = 1.0
        try:
            _g = screens[0].geometry()
            _pm = screens[0].grabWindow(0)
            _pm.setDevicePixelRatio(1.0)
            if _g.width():
                ref_dpr = _pm.width() / _g.width()
        except Exception:
            ref_dpr = 1.0
        if not (ref_dpr > 0):
            ref_dpr = 1.0

        W = max(1, int(round(virtual.width() * ref_dpr)))
        H = max(1, int(round(virtual.height() * ref_dpr)))
        bg = QPixmap(W, H)
        bg.fill(Qt.black)
        painter = QPainter(bg)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        try:
            for s in screens:
                geo = s.geometry()
                try:
                    s_dpr = float(s.devicePixelRatio() or ref_dpr)
                except Exception:
                    s_dpr = ref_dpr
                if not (s_dpr > 0):
                    s_dpr = ref_dpr
                pm = s.grabWindow(0)
                pm.setDevicePixelRatio(1.0)
                tw = max(1, int(round(geo.width() * ref_dpr)))
                th = max(1, int(round(geo.height() * ref_dpr)))
                if s_dpr != ref_dpr:
                    pm = pm.scaled(tw, th, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                tx = int(round((geo.x() - virtual.x()) * ref_dpr))
                ty = int(round((geo.y() - virtual.y()) * ref_dpr))
                painter.drawPixmap(tx, ty, pm)
        finally:
            painter.end()
        self.bg = bg
        self.bg.setDevicePixelRatio(1.0)
        self.dpr = ref_dpr
        self._screen_geo = QRect(virtual)

    def _build_window_cache(self):
        try:
            own = int(self.winId())
        except Exception:
            own = 0
        raw = _enum_toplevel_window_rects(exclude_hwnd=own)
        cache = []
        for scr_rect, title in raw:
            local = self._screen_rect_to_local(scr_rect)
            if local is None:
                continue
            # 与整桌遮罩相交才有意义
            inter = local.intersected(self.rect())
            if inter.width() < 10 or inter.height() < 10:
                continue
            cache.append((inter, title))
        self._win_cache = cache
        # 刷新一次悬停（光标可能已在窗上）
        self._update_hover_at(self.mapFromGlobal(self.cursor().pos()))

    def _screen_rect_to_local(self, scr_rect: QRect):
        """GetWindowRect → 遮罩本地逻辑坐标（与 mouse 事件、_phys 一致）。

        注意 Qt 的 QRect.right()/bottom() 是含端点的，换算宽高时必须用 width/height，
        避免差 1 像素。
        """
        d = self.dpr if self.dpr else 1.0
        geo = self._screen_geo
        # grab 是物理像素，geo/鼠标是 Qt 逻辑；GetWindowRect 在 DPI 感知进程下多为物理像素
        # 与现有 _phys 对称：logical = physical / dpr，再减主屏逻辑原点
        try:
            x = int(round(scr_rect.x() / d)) - geo.x()
            y = int(round(scr_rect.y() / d)) - geo.y()
            w = max(1, int(round(scr_rect.width() / d)))
            h = max(1, int(round(scr_rect.height() / d)))
        except Exception:
            return None
        return QRect(x, y, w, h)

    def _phys(self, r):
        d = self.dpr
        return QRect(int(r.x() * d), int(r.y() * d),
                     int(r.width() * d), int(r.height() * d))

    def _update_hover_at(self, pos: QPoint):
        if not self.detect_window or self._dragging or self.start:
            return
        hit_rect, hit_title = None, ""
        for rect, title in self._win_cache:
            if rect.contains(pos):
                hit_rect, hit_title = rect, title
                break  # Z 序前→后，第一个即最前
        if hit_rect != self._hover_rect or hit_title != self._hover_title:
            self._hover_rect = hit_rect
            self._hover_title = hit_title
            self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.start = e.pos()
            self.end = self.start
            self._dragging = False
            self.update()

    def mouseMoveEvent(self, e):
        if self.start is not None:
            self.end = e.pos()
            dist = (e.pos() - self.start).manhattanLength()
            if dist >= self._DRAG_THRESH:
                self._dragging = True
                self._hover_rect = None
                self._hover_title = ""
            self.update()
        elif self.detect_window:
            self._update_hover_at(e.pos())

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.RightButton:
            self.on_cancel(); self.close(); return
        if e.button() != Qt.LeftButton or self.start is None:
            return

        # 自由框选
        if self._dragging and self.end is not None:
            rect = QRect(self.start, self.end).normalized()
            self.close()
            if rect.width() < 10 or rect.height() < 10:
                self.on_cancel(); return
            crop = self.bg.copy(self._phys(rect))
            crop.setDevicePixelRatio(1.0)
            self.on_capture(crop)
            return

        # 点选：识别到的顶层窗
        if self.detect_window and self._hover_rect is not None:
            rect = QRect(self._hover_rect)
            self.close()
            if rect.width() < 10 or rect.height() < 10:
                self.on_cancel(); return
            crop = self.bg.copy(self._phys(rect))
            crop.setDevicePixelRatio(1.0)
            self.on_capture(crop)
            return

        # 未拖拽且无窗口命中：与旧行为一致，过小则取消
        rect = QRect(self.start, self.end or self.start).normalized()
        self.close()
        if rect.width() < 10 or rect.height() < 10:
            self.on_cancel(); return
        crop = self.bg.copy(self._phys(rect))
        crop.setDevicePixelRatio(1.0)
        self.on_capture(crop)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.on_cancel(); self.close()

    def _draw_sel(self, p: QPainter, sel: QRect, label: str = ""):
        p.drawPixmap(sel, self.bg, self._phys(sel))
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#3aa0ff"), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(sel)
        w = int(sel.width() * self.dpr); h = int(sel.height() * self.dpr)
        txt = f"{w} × {h}"
        if label:
            # 标题过长截断
            t = label.replace("\n", " ").strip()
            if len(t) > 28:
                t = t[:27] + "…"
            txt = f"{t}  {txt}"
        f = QFont(); f.setPixelSize(13); p.setFont(f)
        tw = p.fontMetrics().horizontalAdvance(txt) + 12
        ty = sel.top() - 24 if sel.top() > 24 else sel.top() + 6
        # 标签勿画出遮罩外
        tx = max(0, min(sel.left(), self.width() - tw))
        p.fillRect(tx, ty, tw, 20, QColor(0, 0, 0, 180))
        p.setPen(QColor("#ffffff"))
        p.drawText(tx + 6, ty + 15, txt)

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.bg)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        if self.start and self.end and self._dragging:
            self._draw_sel(p, QRect(self.start, self.end).normalized())
        elif self._hover_rect is not None and not self._dragging:
            self._draw_sel(p, self._hover_rect, self._hover_title)


# ============================================================
# QQ 风截图编辑：矢量图标 / 胶囊工具栏 / 画布 / OCR
# 参考：项目根 temp1.png（QQ 截图工具栏）
# ============================================================

# 工具栏深色玻璃（临摹 QQ，不跟随主程序亮暗主题）
_ED_BG = "#1a1a1e"
_ED_BAR = QColor(36, 36, 40, 235)
_ED_ICON = QColor(235, 235, 240)
_ED_ICON_DIM = QColor(160, 160, 168)
_ED_ACCENT = QColor(58, 160, 255)
_ED_DANGER = QColor(255, 90, 90)
_ED_OK = QColor(52, 199, 89)
_ED_BORDER = QColor(58, 160, 255)


def _arc_pt(cx, cy, r, deg):
    """圆弧上的点；deg 为屏幕角（0=右，90=下，180=左，270=上）。"""
    a = math.radians(deg)
    return QPointF(cx + r * math.cos(a), cy + r * math.sin(a))


def _paint_edit_icon(p: QPainter, kind: str, s: float, color: QColor):
    """在 s×s 方格内画 QQ 风格线性图标。"""
    p.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(color, max(1.4, s * 0.07), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    m = s * 0.22
    r = QRectF(m, m, s - 2 * m, s - 2 * m)

    if kind == "rect":
        p.drawRoundedRect(r, s * 0.06, s * 0.06)
    elif kind == "ellipse":
        p.drawEllipse(r)
    elif kind == "arrow":
        p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.top()))
        # 箭头头部
        tip = QPointF(r.right(), r.top())
        p.drawLine(tip, QPointF(tip.x() - s * 0.22, tip.y()))
        p.drawLine(tip, QPointF(tip.x(), tip.y() + s * 0.22))
    elif kind == "pen":
        # 铅笔：斜向笔身 + 笔尖 + 笔尾橡皮
        body = QPainterPath()
        # 笔杆四边形（左下 → 右上）
        body.moveTo(s * 0.22, s * 0.72)
        body.lineTo(s * 0.34, s * 0.84)
        body.lineTo(s * 0.78, s * 0.40)
        body.lineTo(s * 0.66, s * 0.28)
        body.closeSubpath()
        p.setPen(QPen(color, max(1.2, s * 0.06), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(body)
        # 笔尖三角
        tip = QPainterPath()
        tip.moveTo(s * 0.22, s * 0.72)
        tip.lineTo(s * 0.34, s * 0.84)
        tip.lineTo(s * 0.16, s * 0.88)
        tip.closeSubpath()
        p.setBrush(QBrush(color))
        p.setPen(Qt.NoPen)
        p.drawPath(tip)
        # 笔尾斜切
        p.setPen(QPen(color, max(1.2, s * 0.06), Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(s * 0.66, s * 0.28), QPointF(s * 0.78, s * 0.40))
        p.drawLine(QPointF(s * 0.70, s * 0.24), QPointF(s * 0.82, s * 0.36))
    elif kind == "text":
        f = QFont("Segoe UI", max(9, int(s * 0.48)))
        f.setBold(True)
        p.setFont(f)
        p.setPen(color)
        p.drawText(QRectF(0, 0, s, s), Qt.AlignCenter, "A")
    elif kind == "mosaic":
        # 2×2 棋盘
        p.setPen(Qt.NoPen)
        c1, c2 = color, QColor(color)
        c2.setAlpha(90)
        cells = [
            (r.left(), r.top(), c1), (r.center().x(), r.top(), c2),
            (r.left(), r.center().y(), c2), (r.center().x(), r.center().y(), c1),
        ]
        cw, ch = r.width() / 2 - 1, r.height() / 2 - 1
        for x, y, c in cells:
            p.setBrush(QBrush(c))
            p.drawRoundedRect(QRectF(x, y, cw, ch), 1.5, 1.5)
    elif kind == "ocr":
        # 文字「OCR」
        f = QFont("Segoe UI", max(7, int(s * 0.34)))
        f.setBold(True)
        f.setLetterSpacing(QFont.AbsoluteSpacing, -0.5)
        p.setFont(f)
        p.setPen(color)
        p.drawText(QRectF(0, 0, s, s), Qt.AlignCenter, "OCR")
    elif kind == "undo":
        # 经典撤销：左侧半圆弧（右开口）+ 上端向左箭头
        pw = max(1.7, s * 0.085)
        pen = QPen(color, pw, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        cx, cy, r = s / 2, s / 2, s * 0.31
        path = QPainterPath()
        for i in range(25):
            # 屏幕角 270(上)→90(下)，经 180(左)，即右开口的「C」
            deg = 270.0 - 180.0 * i / 24.0
            pt = _arc_pt(cx, cy, r, deg)
            if i == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
        p.drawPath(path)
        # 顶端箭头，向左（沿弧行进方向）
        tip = _arc_pt(cx, cy, r, 270.0)
        L = s * 0.12
        p.drawLine(tip, QPointF(tip.x() + L * math.cos(math.radians(38)),
                                tip.y() + L * math.sin(math.radians(38))))
        p.drawLine(tip, QPointF(tip.x() + L * math.cos(math.radians(-38)),
                                tip.y() + L * math.sin(math.radians(-38))))
    elif kind == "redo":
        # 经典重做：右侧半圆弧（左开口）+ 上端向右箭头（撤销的镜像）
        pw = max(1.7, s * 0.085)
        pen = QPen(color, pw, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        cx, cy, r = s / 2, s / 2, s * 0.31
        path = QPainterPath()
        for i in range(25):
            # 屏幕角 270(上)→450(=90 下)，经 0(右)，即左开口的「C」
            deg = 270.0 + 180.0 * i / 24.0
            pt = _arc_pt(cx, cy, r, deg)
            if i == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
        p.drawPath(path)
        # 顶端箭头，向右（沿弧行进方向）
        tip = _arc_pt(cx, cy, r, 270.0)
        L = s * 0.12
        p.drawLine(tip, QPointF(tip.x() + L * math.cos(math.radians(180 + 38)),
                                tip.y() + L * math.sin(math.radians(180 + 38))))
        p.drawLine(tip, QPointF(tip.x() + L * math.cos(math.radians(180 - 38)),
                                tip.y() + L * math.sin(math.radians(180 - 38))))
    elif kind == "save":
        # 下载箭头
        p.drawLine(QPointF(r.center().x(), r.top() + s * 0.02),
                   QPointF(r.center().x(), r.bottom() - s * 0.22))
        p.drawLine(QPointF(r.center().x(), r.bottom() - s * 0.22),
                   QPointF(r.center().x() - s * 0.16, r.bottom() - s * 0.38))
        p.drawLine(QPointF(r.center().x(), r.bottom() - s * 0.22),
                   QPointF(r.center().x() + s * 0.16, r.bottom() - s * 0.38))
        p.drawLine(QPointF(r.left() + s * 0.02, r.bottom() - s * 0.08),
                   QPointF(r.right() - s * 0.02, r.bottom() - s * 0.08))
    elif kind == "saveas":
        # 另存为：经典软盘 + 向下箭头（存到指定目录）
        pw = max(1.5, s * 0.075)
        pen = QPen(color, pw, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        # 软盘主体
        p.drawRoundedRect(QRectF(s * 0.20, s * 0.30, s * 0.60, s * 0.54), s * 0.045, s * 0.045)
        # 金属快门槽
        p.drawRoundedRect(QRectF(s * 0.20, s * 0.30, s * 0.60, s * 0.15), s * 0.04, s * 0.04)
        # 纸标签
        p.drawRoundedRect(QRectF(s * 0.32, s * 0.56, s * 0.24, s * 0.28), s * 0.02, s * 0.02)
        p.drawLine(QPointF(s * 0.32, s * 0.66), QPointF(s * 0.56, s * 0.66))
        # 向下箭头（落入软盘）
        ax = s * 0.5
        p.drawLine(QPointF(ax, s * 0.10), QPointF(ax, s * 0.22))
        p.drawLine(QPointF(ax, s * 0.22), QPointF(ax - s * 0.11, s * 0.11))
        p.drawLine(QPointF(ax, s * 0.22), QPointF(ax + s * 0.11, s * 0.11))
    elif kind == "cancel":
        p.setPen(QPen(_ED_DANGER, max(1.6, s * 0.08), Qt.SolidLine, Qt.RoundCap))
        p.drawLine(r.topLeft() + QPointF(s * 0.04, s * 0.04),
                   r.bottomRight() - QPointF(s * 0.04, s * 0.04))
        p.drawLine(r.topRight() + QPointF(-s * 0.04, s * 0.04),
                   r.bottomLeft() + QPointF(s * 0.04, -s * 0.04))
    elif kind == "ok":
        p.setPen(QPen(_ED_OK, max(1.8, s * 0.09), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(QPointF(r.left() + s * 0.02, r.center().y()),
                   QPointF(r.center().x() - s * 0.04, r.bottom() - s * 0.06))
        p.drawLine(QPointF(r.center().x() - s * 0.04, r.bottom() - s * 0.06),
                   QPointF(r.right() - s * 0.02, r.top() + s * 0.08))
    elif kind == "color":
        # 外环 + 实心色块（色块颜色由调用方再画一层）
        p.drawEllipse(r)
    elif kind == "width":
        # 三条横线：细 / 中 / 粗示意
        pen.setWidthF(max(1.2, s * 0.06))
        p.setPen(pen)
        ys = (r.top() + s * 0.08, r.center().y(), r.bottom() - s * 0.08)
        spans = (0.55, 0.72, 0.90)
        for y, sp in zip(ys, spans):
            half = r.width() * sp * 0.5
            p.drawLine(QPointF(r.center().x() - half, y),
                       QPointF(r.center().x() + half, y))


_BTN_SS = (
    "QToolButton{background:transparent;border:none;border-radius:8px;padding:0;}"
    "QToolButton:hover{background:rgba(255,255,255,0.10);}"
    "QToolButton:checked{background:rgba(58,160,255,0.28);}"
    "QToolButton:pressed{background:rgba(255,255,255,0.14);}"
    "QToolButton:disabled{background:transparent;}"
    # 去掉 QToolButton 菜单自带的右下角小三角
    "QToolButton::menu-indicator{image:none;width:0;height:0;}"
)

_POP_MENU_SS = (
    "QMenu{background:rgba(40,40,44,245);border:1px solid rgba(255,255,255,0.12);"
    "border-radius:10px;padding:6px;}"
    "QMenu::item{padding:6px 14px;color:#e8e8ec;border-radius:6px;}"
    "QMenu::item:selected{background:rgba(58,160,255,0.30);}"
    "QMenu::separator{height:1px;background:rgba(255,255,255,0.10);margin:4px 6px;}"
)


class _IconToolBtn(QToolButton):
    """QQ 工具栏图标按钮：悬停浅底，选中蓝底。"""

    def __init__(self, kind: str, tip: str, parent=None, *, checkable=False):
        super().__init__(parent)
        self._kind = kind
        self.setCheckable(checkable)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(36, 36)
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet(_BTN_SS)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        color = _ED_ICON
        if not self.isEnabled():
            color = _ED_ICON_DIM
        elif self.isChecked():
            color = _ED_ACCENT
        elif self._kind == "cancel":
            color = _ED_DANGER
        elif self._kind == "ok":
            color = _ED_OK
        s = 22.0
        p.translate((self.width() - s) / 2, (self.height() - s) / 2)
        _paint_edit_icon(p, self._kind, s, color)
        p.end()


class _ColorPickBtn(QToolButton):
    """选颜色：中心当前色 + 彩色外环；点击弹出色板（无菜单三角）。"""

    colorChanged = pyqtSignal(str)
    _COLORS = ["#ff3b30", "#ff9500", "#ffcc00", "#34c759",
               "#0a84ff", "#af52de", "#ffffff", "#000000"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = "#ff3b30"
        self._menu = None
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(36, 36)
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet(_BTN_SS)
        # 不用 InstantPopup/setMenu，避免右下角小三角
        self.clicked.connect(self._popup)
        self._build_menu()

    def _build_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(_POP_MENU_SS)
        names = ["红", "橙", "黄", "绿", "蓝", "紫", "白", "黑"]
        for c, name in zip(self._COLORS, names):
            pm = QPixmap(16, 16)
            pm.fill(Qt.transparent)
            qp = QPainter(pm)
            qp.setRenderHint(QPainter.Antialiasing)
            qp.setBrush(QColor(c))
            qp.setPen(QPen(QColor(255, 255, 255, 90), 1))
            qp.drawEllipse(1, 1, 14, 14)
            qp.end()
            act = menu.addAction(QIcon(pm), name)
            act.setData(c)
        menu.triggered.connect(self._on_pick)
        self._menu = menu

    def _popup(self):
        if self._menu:
            self._menu.exec_(self.mapToGlobal(self.rect().bottomLeft()))

    def _on_pick(self, act):
        c = act.data()
        if not c:
            return
        self._color = str(c)
        self.colorChanged.emit(self._color)
        self.update()

    def set_color(self, c: str):
        self._color = c
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = 22.0
        x = (self.width() - s) / 2
        y = (self.height() - s) / 2
        cx, cy = x + s / 2, y + s / 2
        # 彩色圆锥渐变外环
        grad = QConicalGradient(cx, cy, 90)
        stops = [
            (0.00, "#ff3b30"), (0.14, "#ff9500"), (0.28, "#ffcc00"),
            (0.42, "#34c759"), (0.56, "#0a84ff"), (0.70, "#af52de"),
            (0.84, "#ff2d55"), (1.00, "#ff3b30"),
        ]
        for pos, col in stops:
            grad.setColorAt(pos, QColor(col))
        p.setPen(QPen(QBrush(grad), 2.4))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(x + 2.2, y + 2.2, s - 4.4, s - 4.4))
        # 实心当前色
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(self._color)))
        p.drawEllipse(QRectF(x + 6, y + 6, s - 12, s - 12))
        p.end()


class _WidthPickBtn(QToolButton):
    """选粗细：显示汉字 细/中/粗；点击弹出选择（无菜单三角）。"""

    widthChanged = pyqtSignal(int)
    _WIDTHS = [("细", 2), ("中", 3), ("粗", 6)]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._width = 3
        self._menu = None
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(36, 36)
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet(_BTN_SS)
        self.clicked.connect(self._popup)
        self._build_menu()

    def _label(self) -> str:
        for lab, px in self._WIDTHS:
            if px == self._width:
                return lab
        return "中"

    def _build_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(_POP_MENU_SS)
        self._acts = []
        for lab, px in self._WIDTHS:
            act = menu.addAction(lab)
            act.setData(px)
            act.setCheckable(True)
            if px == self._width:
                act.setChecked(True)
            self._acts.append(act)
        menu.triggered.connect(self._on_pick)
        self._menu = menu

    def _popup(self):
        if self._menu:
            self._menu.exec_(self.mapToGlobal(self.rect().bottomLeft()))

    def _on_pick(self, act):
        px = act.data()
        if px is None:
            return
        self._width = int(px)
        for a in self._acts:
            a.setChecked(a.data() == self._width)
        self.widthChanged.emit(self._width)
        self.update()

    def set_width(self, w: int):
        self._width = int(w)
        for a in getattr(self, "_acts", []):
            a.setChecked(a.data() == self._width)
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        f = QFont("Microsoft YaHei UI", 12)
        f.setBold(True)
        p.setFont(f)
        p.setPen(_ED_ICON)
        p.drawText(self.rect(), Qt.AlignCenter, self._label())
        p.end()


class _VSep(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(1, 22)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(0, 0, 1, self.height(), QColor(255, 255, 255, 40))


class _QQToolbar(QFrame):
    """深色胶囊工具栏（悬空，无第二行）。"""

    toolChanged = pyqtSignal(str)
    action = pyqtSignal(str)  # undo/redo/ocr/save/copy/cancel
    colorChanged = pyqtSignal(str)
    widthChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("QQShotToolbar")
        self.setFixedHeight(48)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "#QQShotToolbar{"
            "background:rgba(36,36,40,235);"
            "border:1px solid rgba(255,255,255,0.10);"
            "border-radius:12px;}"
        )

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(2)

        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        self._tool_btns = {}

        def add_tool(kind, tip):
            b = _IconToolBtn(kind, tip, self, checkable=True)
            b.clicked.connect(lambda _=False, k=kind: self._on_tool(k))
            self._tool_group.addButton(b)
            self._tool_btns[kind] = b
            lay.addWidget(b)
            return b

        def add_act(kind, tip, key):
            b = _IconToolBtn(kind, tip, self, checkable=False)
            b.clicked.connect(lambda _=False, k=key: self.action.emit(k))
            lay.addWidget(b)
            return b

        # 绘制工具
        add_tool("rect", "矩形")
        add_tool("ellipse", "椭圆")
        add_tool("arrow", "箭头")
        add_tool("pen", "画笔")
        add_tool("text", "文字")
        lay.addWidget(_VSep(self))
        add_tool("mosaic", "马赛克")
        lay.addWidget(_VSep(self))
        # 颜色 / 粗细（原第二行精简并入）
        self.btn_color = _ColorPickBtn(self)
        self.btn_color.colorChanged.connect(self.colorChanged.emit)
        lay.addWidget(self.btn_color)
        self.btn_width = _WidthPickBtn(self)
        self.btn_width.widthChanged.connect(self.widthChanged.emit)
        lay.addWidget(self.btn_width)
        lay.addWidget(_VSep(self))
        add_act("ocr", "识字", "ocr")
        lay.addWidget(_VSep(self))
        add_act("undo", "撤销", "undo")
        add_act("redo", "重做", "redo")
        lay.addWidget(_VSep(self))
        add_act("save", "另存为", "saveas")
        add_act("cancel", "取消", "cancel")
        add_act("ok", "保存并关闭", "save")

        self._tool_btns["rect"].setChecked(True)
        self.adjustSize()

    def _on_tool(self, kind: str):
        self.toolChanged.emit(kind)

    def set_tool(self, kind: str):
        b = self._tool_btns.get(kind)
        if b:
            b.setChecked(True)


class _Canvas(QWidget):
    """标注画布：矩形 / 椭圆 / 箭头 / 画笔 / 文字 / 马赛克。"""

    def __init__(self, pixmap):
        super().__init__()
        self.base = pixmap
        iw, ih = pixmap.width(), pixmap.height()
        avail = QApplication.primaryScreen().availableGeometry()
        max_w = int(avail.width() * 0.88)
        max_h = int(avail.height() * 0.72)
        self.scale = min(1.0, max_w / max(1, iw), max_h / max(1, ih))
        self.setFixedSize(max(1, int(iw * self.scale)), max(1, int(ih * self.scale)))
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)

        self.tool = "rect"
        self.color = QColor("#ff3b30")
        self.stroke_w = 3  # 勿用 self.width，会遮蔽 QWidget.width()
        self.shapes = []
        self._redo = []
        self.start_ip = None
        self.cur = None
        self.drawing = False
        self._mosaic_cache = None
        self._text_edit = None

    def _to_img(self, pos):
        return QPoint(int(pos.x() / self.scale), int(pos.y() / self.scale))

    def _mosaic_base(self):
        if self._mosaic_cache is None:
            block = 14
            img = self.base.toImage()
            w, h = img.width(), img.height()
            small = img.scaled(max(1, w // block), max(1, h // block),
                               Qt.IgnoreAspectRatio, Qt.FastTransformation)
            self._mosaic_cache = QPixmap.fromImage(
                small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.FastTransformation))
        return self._mosaic_cache

    def _push_shape(self, shape):
        self.shapes.append(shape)
        self._redo.clear()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self.tool == "ocr":
            return
        ip = self._to_img(e.pos())
        if self.tool == "text":
            self._begin_text(e.pos(), ip)
            return
        self.start_ip = ip
        self.drawing = True
        if self.tool in ("rect", "ellipse", "mosaic"):
            self.cur = {"type": self.tool, "rect": QRect(ip, ip),
                        "color": QColor(self.color), "width": self.stroke_w}
        elif self.tool == "arrow":
            self.cur = {"type": "arrow", "p1": ip, "p2": ip,
                        "color": QColor(self.color), "width": self.stroke_w}
        elif self.tool == "pen":
            self.cur = {"type": "pen", "points": [ip],
                        "color": QColor(self.color), "width": self.stroke_w}
        self.update()

    def mouseMoveEvent(self, e):
        if not self.drawing or self.cur is None:
            return
        ip = self._to_img(e.pos())
        t = self.cur["type"]
        if t in ("rect", "ellipse", "mosaic"):
            self.cur["rect"] = QRect(self.start_ip, ip).normalized()
        elif t == "arrow":
            self.cur["p2"] = ip
        elif t == "pen":
            self.cur["points"].append(ip)
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self.cur is None:
            return
        keep = True
        t = self.cur["type"]
        if t in ("rect", "ellipse", "mosaic"):
            r = self.cur["rect"]; keep = r.width() > 3 and r.height() > 3
        elif t == "arrow":
            keep = (self.cur["p1"] - self.cur["p2"]).manhattanLength() > 5
        elif t == "pen":
            keep = len(self.cur["points"]) > 1
        if keep:
            self._push_shape(self.cur)
        self.cur = None
        self.drawing = False
        self.update()

    def _begin_text(self, wpos, ipos):
        if self._text_edit is not None:
            self._commit_text()
        size = max(16, self.stroke_w * 6)
        le = _QLineEdit(self)
        le.setStyleSheet(
            f"background:rgba(0,0,0,140); color:{self.color.name()};"
            f"border:1px dashed {self.color.name()}; padding:2px;")
        f = QFont(); f.setPixelSize(max(12, int(size * self.scale))); le.setFont(f)
        le.move(wpos); le.setMinimumWidth(120)
        le.show(); le.setFocus()
        le._img_pos = ipos; le._font_size = size; le._committed = False
        le.returnPressed.connect(self._commit_text)
        le.editingFinished.connect(self._commit_text)
        self._text_edit = le

    def _commit_text(self):
        le = self._text_edit
        if le is None or getattr(le, "_committed", False):
            return
        le._committed = True
        text = le.text().strip()
        if text:
            self._push_shape({
                "type": "text",
                "pos": QPoint(le._img_pos.x(), le._img_pos.y() + le._font_size),
                "text": text, "color": QColor(self.color), "size": le._font_size})
        self._text_edit = None
        le.deleteLater()
        self.update()

    def undo(self):
        if self.shapes:
            self._redo.append(self.shapes.pop())
            self.update()

    def redo(self):
        if self._redo:
            self.shapes.append(self._redo.pop())
            self.update()

    def _draw_shape(self, p, s):
        t = s["type"]
        if t == "rect":
            p.setPen(QPen(s["color"], s["width"])); p.setBrush(Qt.NoBrush)
            p.drawRect(s["rect"])
        elif t == "ellipse":
            p.setPen(QPen(s["color"], s["width"])); p.setBrush(Qt.NoBrush)
            p.drawEllipse(s["rect"])
        elif t == "arrow":
            self._draw_arrow(p, s["p1"], s["p2"], s["color"], s["width"])
        elif t == "pen":
            p.setPen(QPen(s["color"], s["width"], Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            pts = s["points"]
            for i in range(1, len(pts)):
                p.drawLine(pts[i - 1], pts[i])
        elif t == "text":
            f = QFont(); f.setPixelSize(s["size"]); p.setFont(f)
            p.setPen(s["color"]); p.drawText(s["pos"], s["text"])
        elif t == "mosaic":
            p.drawPixmap(s["rect"], self._mosaic_base(), s["rect"])

    def _draw_arrow(self, p, p1, p2, color, width):
        p.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(p1, p2)
        ang = math.atan2(p2.y() - p1.y(), p2.x() - p1.x())
        head = max(12, width * 4)
        a1 = ang + math.radians(150); a2 = ang - math.radians(150)
        q1 = QPoint(int(p2.x() + head * math.cos(a1)), int(p2.y() + head * math.sin(a1)))
        q2 = QPoint(int(p2.x() + head * math.cos(a2)), int(p2.y() + head * math.sin(a2)))
        p.setBrush(QBrush(color))
        p.drawPolygon(QPolygon([p2, q1, q2]))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.save()
        p.scale(self.scale, self.scale)
        p.drawPixmap(0, 0, self.base)
        for s in self.shapes:
            self._draw_shape(p, s)
        if self.cur is not None:
            self._draw_shape(p, self.cur)
        p.restore()
        # 外圈选区蓝边（QQ 感）
        p.setPen(QPen(_ED_BORDER, 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))

    def render_result(self):
        self._commit_text()
        out = QPixmap(self.base)
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        for s in self.shapes:
            self._draw_shape(p, s)
        p.end()
        return out


# 关编辑窗时若 OCR 线程仍在跑，暂存于此，避免 QThread 被连带销毁崩溃
_OCR_THREAD_KEEPALIVE = []


class _OcrWorker(QThread):
    """仅接收磁盘路径；禁止把 QPixmap / QWidget 传入子线程。"""

    finished_ok = pyqtSignal(str, str)   # text, engine
    finished_err = pyqtSignal(str)

    def __init__(self, png_path: str, parent=None):
        super().__init__(parent)  # 建议 parent=None，由调用方托管生命周期
        self._path = png_path
        self._cancel = False

    def stop(self):
        self._cancel = True
        try:
            self.requestInterruption()
        except Exception:
            pass

    def run(self):
        path = self._path
        try:
            if self._cancel or self.isInterruptionRequested():
                return
            from utils.ocr_util import ocr_image_path
            text, eng = ocr_image_path(path)
            if self._cancel or self.isInterruptionRequested():
                return
            self.finished_ok.emit(text, eng)
        except Exception as e:
            if not self._cancel:
                self.finished_err.emit(f"{type(e).__name__}: {e}")
        finally:
            if path:
                try:
                    os.remove(path)
                except Exception:
                    pass


class _OcrStatusLamp(QWidget):
    """OCR 状态圆灯：busy=黄闪 / ok=绿 / bad=红。"""

    SIZE = 14
    _COLORS = {
        "busy": (QColor("#eab308"), QColor("#713f12")),
        "ok": (QColor("#22c55e"), QColor("#166534")),
        "bad": (QColor("#ef4444"), QColor("#7f1d1d")),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = "busy"
        self._on = True
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._timer = QTimer(self)
        self._timer.setInterval(450)
        self._timer.timeout.connect(self._blink)

    def set_state(self, state: str):
        if state not in self._COLORS:
            state = "bad"
        self._state = state
        if state == "busy":
            self._on = True
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._on = True
        self.update()

    def _blink(self):
        self._on = not self._on
        self.update()

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def showEvent(self, e):
        if self._state == "busy" and not self._timer.isActive():
            self._on = True
            self._timer.start()
        super().showEvent(e)

    def paintEvent(self, _):
        on_c, dim_c = self._COLORS.get(self._state, self._COLORS["bad"])
        c = on_c if self._on else dim_c
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        s = float(min(self.width(), self.height()))
        glow = QColor(on_c)
        glow.setAlpha(110 if self._on else 28)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QRectF(0.4, 0.4, s - 0.8, s - 0.8))
        m = 2.2
        p.setBrush(QBrush(c))
        p.drawEllipse(QRectF(m, m, s - 2.0 * m, s - 2.0 * m))
        if self._on:
            hi = QColor(255, 255, 255, 140)
            p.setBrush(QBrush(hi))
            p.drawEllipse(QRectF(s * 0.30, s * 0.26, s * 0.22, s * 0.16))
        p.end()


class _OcrStatusLed(QWidget):
    """OCR 引擎状态：圆灯 + 右侧 2～3 字（识别中 / 完成 / 不可用）。"""

    _CAPTION = {
        "busy": ("识别中", "#eab308"),
        "ok": ("完成", "#22c55e"),
        "bad": ("不可用", "#ef4444"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._state = "busy"
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._lamp = _OcrStatusLamp(self)
        self._txt = QLabel(self._CAPTION["busy"][0])
        self._txt.setWordWrap(False)
        self._txt.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        cap_font = QFont(self._txt.font())
        cap_font.setPixelSize(12)
        self._txt.setFont(cap_font)
        lay.addWidget(self._lamp, 0, Qt.AlignVCenter)
        lay.addWidget(self._txt, 0, Qt.AlignVCenter)
        self._apply_caption("busy")
        # 按最长文案占位，避免「完成」↔「不可用」时引擎行左右跳
        self._txt.setMinimumWidth(self._txt.fontMetrics().horizontalAdvance("不可用"))

    def set_state(self, state: str):
        if state not in self._CAPTION:
            state = "bad"
        self._state = state
        self._lamp.set_state(state)
        self._apply_caption(state)

    def _apply_caption(self, state: str):
        text, color = self._CAPTION.get(state, self._CAPTION["bad"])
        self._txt.setText(text)
        self._txt.setStyleSheet(
            f"QLabel{{color:{color};background:transparent;font-size:12px;}}"
        )
        self.setToolTip(text)


class _OcrResultDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("提取文字")
        # 有 parent（编辑窗）时：做 transient 对话框，系统保证叠在父窗之上，
        # 不要再叠一层全局 WindowStaysOnTopHint，否则会与编辑窗的动态置顶
        # setWindowFlags 抢 z-order，出现「闪一下又被压到后面」。
        # 编辑窗关闭时会 reparent 到 None 并补上置顶（见 _detach_ocr_for_close）。
        flags = Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint
        if parent is None:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setMinimumSize(420, 320)
        self.resize(480, 380)
        self.setStyleSheet(
            f"QDialog{{background:{_ED_BG};color:#e8e8ec;}}"
            f"QTextEdit{{background:#121214;color:#e8e8ec;border:1px solid #333;"
            f"border-radius:8px;padding:8px;font-size:13px;}}"
            f"QLabel{{color:#aaa;background:transparent;}}"
            f"QPushButton{{background:#2a2a30;color:#eee;border:1px solid #444;"
            f"border-radius:8px;padding:6px 14px;min-height:28px;}}"
            f"QPushButton:hover{{background:#3a3a42;}}"
            f"QPushButton#primary{{background:#1d4ed8;border-color:#3b82f6;}}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self.lbl = QLabel("引擎：…")
        self.lbl.setWordWrap(False)
        self.lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.led = _OcrStatusLed(self)
        head.addWidget(self.lbl, 1)
        head.addWidget(self.led, 0, Qt.AlignRight | Qt.AlignVCenter)
        lay.addLayout(head)
        self.edit = QTextEdit()
        self.edit.setReadOnly(False)
        self.edit.setPlaceholderText("识别结果将显示在这里…")
        lay.addWidget(self.edit, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_copy = QPushButton("复制全部")
        self.btn_copy.setObjectName("primary")
        self.btn_copy.clicked.connect(self._copy)
        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.accept)
        row.addWidget(self.btn_copy)
        row.addWidget(self.btn_close)
        lay.addLayout(row)
        self.btn_copy.setEnabled(False)

    def set_loading(self, hint: str = ""):
        engine = (hint or "").strip()
        if engine.lower().startswith("引擎：") or engine.lower().startswith("引擎:"):
            self.lbl.setText(engine)
        else:
            self.lbl.setText(f"引擎：{engine}" if engine else "引擎：…")
        self.led.set_state("busy")
        self.edit.setPlainText("")
        self.btn_copy.setEnabled(False)

    def set_result(self, text: str, engine: str):
        eng_map = {
            "paddleocr-vl": "本地 PaddleOCR-VL-1.6",
            "none": "无引擎",
        }
        self.lbl.setText(f"引擎：{eng_map.get(engine, engine)}")
        self.edit.setPlainText(text or "")
        failed = (engine == "none") or str(text or "").startswith("❌")
        self.led.set_state("bad" if failed else "ok")
        self.btn_copy.setEnabled(bool(text) and not str(text).startswith("❌"))

    def set_error(self, msg: str):
        self.lbl.setText("引擎：不可用")
        self.led.set_state("bad")
        self.edit.setPlainText(msg)
        self.btn_copy.setEnabled(False)

    def _copy(self):
        QApplication.clipboard().setText(self.edit.toPlainText())
        self.lbl.setText(self.lbl.text().split(" ·")[0] + " · 已复制")


# ============================================================
# 标注编辑（无边框：全屏遮罩 + 居中截图 + 悬空工具栏）
# ============================================================
class AnnotationEditor(QWidget):
    def __init__(self, pixmap, on_save, on_copy, on_cancel, on_save_as=None):
        super().__init__()
        self.on_save = on_save
        self.on_copy = on_copy
        self.on_cancel = on_cancel
        self.on_save_as = on_save_as
        self._done = False
        self._ocr_worker = None
        self._ocr_dlg = None
        # >0 时禁止 _sync_toplevel_flag 改 flags（另存为文件框等会短暂失焦，
        # 若此时 setWindowFlags 会重建 HWND，编辑窗会「闪没半秒」）
        self._freeze_toplevel = 0

        # 无标题栏、无系统边框；紧凑悬浮窗，只包住图与工具栏（见 _apply_compact_geometry）
        # 置顶是动态的：激活时置顶，失活（切到 QQ 等其它窗口）时撤置顶，避免压住别的窗口
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Window
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet("background:transparent;")
        self._dragging = False
        self._drag_pos = QPoint()
        self._drag_origin = QPoint()

        # 子控件绝对定位，不用传统窗口布局
        self.canvas = _Canvas(pixmap)
        self.canvas.setParent(self)

        self.toolbar = _QQToolbar(self)
        self.toolbar.setParent(self)
        self.toolbar.toolChanged.connect(self._set_tool)
        self.toolbar.action.connect(self._on_action)
        self.toolbar.colorChanged.connect(self._set_color)
        self.toolbar.widthChanged.connect(self._set_width)

        # 右键取消：画布/工具栏子控件会吞掉右键，用事件过滤器统一拦截
        self.canvas.installEventFilter(self)
        self.toolbar.installEventFilter(self)

        self._apply_compact_geometry()
        self._reposition()
        QTimer.singleShot(0, self._activate)

    def _activate(self):
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.OtherFocusReason)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.ActivationChange:
            # 激活/失活都等一下再同步置顶，让窗口状态先稳定
            QTimer.singleShot(0, self._sync_toplevel_flag)

    def _ocr_dialog_visible(self) -> bool:
        dlg = self._ocr_dlg
        if dlg is None:
            return False
        try:
            return bool(dlg.isVisible())
        except RuntimeError:
            self._ocr_dlg = None
            return False

    def _ocr_dialog_owns_focus(self) -> bool:
        """焦点是否在 OCR 结果窗（或其子控件）上。"""
        dlg = self._ocr_dlg
        if dlg is None:
            return False
        try:
            if not dlg.isVisible():
                return False
            aw = QApplication.activeWindow()
            if aw is None:
                return False
            if aw is dlg:
                return True
            # 少数情况下 activeWindow 是对话框内的临时子窗
            return bool(dlg.isAncestorOf(aw))
        except RuntimeError:
            self._ocr_dlg = None
            return False

    def _raise_ocr_dialog(self, activate: bool = False):
        """把「提取文字」抬到编辑窗前面；默认不抢焦点，避免来回闪。"""
        dlg = self._ocr_dlg
        if dlg is None:
            return
        try:
            if not dlg.isVisible():
                return
            dlg.raise_()
            if activate:
                dlg.activateWindow()
        except RuntimeError:
            self._ocr_dlg = None

    def _present_ocr_dialog(self):
        """显示 OCR 结果窗：先 show，再延后 raise，躲开本窗 ActivationChange 的 flags 同步。"""
        dlg = self._ocr_dlg
        if dlg is None:
            return
        try:
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
        except RuntimeError:
            self._ocr_dlg = None
            return
        # singleShot(0) 与 _sync_toplevel_flag 同一拍；再补一拍兜底 Windows z-order
        QTimer.singleShot(0, lambda: self._raise_ocr_dialog(activate=False))
        QTimer.singleShot(30, lambda: self._raise_ocr_dialog(activate=False))

    def _ensure_ocr_dialog(self) -> "_OcrResultDialog":
        """懒创建 OCR 结果窗：parent=本编辑窗，保证始终叠在编辑层之上。"""
        dlg = self._ocr_dlg
        if dlg is not None:
            try:
                # 已被 C++ 侧销毁时访问会 RuntimeError
                dlg.isVisible()
                return dlg
            except RuntimeError:
                self._ocr_dlg = None
        self._ocr_dlg = _OcrResultDialog(self)
        return self._ocr_dlg

    def begin_freeze_toplevel(self):
        """外部弹层（另存为文件框等）打开前调用，禁止改 windowFlags。"""
        self._freeze_toplevel = int(getattr(self, "_freeze_toplevel", 0)) + 1

    def end_freeze_toplevel(self):
        self._freeze_toplevel = max(0, int(getattr(self, "_freeze_toplevel", 0)) - 1)

    def _owned_popup_active(self) -> bool:
        """焦点是否在本编辑窗派生的弹层上（Qt 文件框 / 消息框等）。"""
        try:
            for candidate in (
                QApplication.activeModalWidget(),
                QApplication.activeWindow(),
            ):
                if candidate is None or candidate is self:
                    continue
                p = candidate
                while p is not None:
                    if p is self:
                        return True
                    p = p.parentWidget()
        except RuntimeError:
            pass
        return False

    def _sync_toplevel_flag(self):
        """激活时保持置顶；失活即用户切到其它窗口（如 QQ），撤掉置顶，
        让对方窗口真正盖住本窗、按钮可点；点回本窗再恢复置顶。

        注意：setWindowFlags 会重建原生 HWND，极易闪烁并打乱 z-order。
        以下情况一律冻结：显式 freeze、OCR 结果窗、本窗 parent 的其它弹层。
        """
        try:
            if self._done or not self.isVisible():
                return
            if int(getattr(self, "_freeze_toplevel", 0)) > 0:
                return
            if self._ocr_dialog_visible():
                if self.isActiveWindow() or self._ocr_dialog_owns_focus():
                    self._raise_ocr_dialog(activate=False)
                return
            # 另存为等：焦点在子对话框上时不要撤置顶/重建 HWND
            if self._owned_popup_active():
                return
            want = self.isActiveWindow()
            cur = bool(self.windowFlags() & Qt.WindowStaysOnTopHint)
            if want == cur:
                return
            if want:
                flags = self.windowFlags() | Qt.WindowStaysOnTopHint
            else:
                flags = self.windowFlags() & ~Qt.WindowStaysOnTopHint
            self.setWindowFlags(flags)
            self.show()
            if want:
                self.raise_()
        except Exception:
            pass

    def _apply_compact_geometry(self):
        """编辑窗只包住「图 + 工具栏」，不铺满主屏。

        原先全屏铺满 + 近透明背景 = 一块看不见的鼠标拦截层，编辑时整屏
        都点不到后面的窗口（像锁屏）。改成紧凑悬浮窗后，图与工具栏之外的
        桌面仍可正常点击、切窗口、回消息。
        """
        gap = 12
        pad = 12
        cw, ch = self.canvas.width(), self.canvas.height()
        self.toolbar.adjustSize()
        tw, th = self.toolbar.width(), self.toolbar.height()
        W = max(cw, tw) + 2 * pad
        H = ch + gap + th + 2 * pad
        avail = QApplication.primaryScreen().availableGeometry()
        if avail.width() > 0 and avail.height() > 0:
            W = min(W, avail.width())
            H = min(H, avail.height())
            x = avail.x() + (avail.width() - W) // 2
            y = avail.y() + (avail.height() - H) // 2
        else:
            x, y = 0, 0
        self.setGeometry(x, y, max(1, W), max(1, H))

    def _reposition(self):
        """截图居中，工具栏悬在其正下方。"""
        gap = 12
        cw, ch = self.canvas.width(), self.canvas.height()
        self.toolbar.adjustSize()
        tw, th = self.toolbar.width(), self.toolbar.height()
        total_h = ch + gap + th
        x = max(0, (self.width() - cw) // 2)
        y = max(8, (self.height() - total_h) // 2)
        self.canvas.move(x, y)
        self.canvas.raise_()
        tx = max(0, (self.width() - tw) // 2)
        self.toolbar.move(tx, y + ch + gap)
        self.toolbar.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._reposition()

    def paintEvent(self, _):
        # 不压暗：几乎全透明，窗口只包住图与工具栏，不遮挡桌面
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 1))

    def eventFilter(self, obj, ev):
        try:
            # 画布/工具栏上的右键 → 取消（左键由各自控件处理绘制）
            if ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.RightButton:
                self._cancel()
                return True
        except Exception:
            pass
        return super().eventFilter(obj, ev)

    def mousePressEvent(self, e):
        # 只接受显式取消（右键 / Escape / 工具栏 ✕），空白左键不再取消——
        # 全屏遮罩背景几乎透明，点图外区域本意不是取消，误触会毁掉整个编辑。
        if e.button() == Qt.RightButton:
            self._cancel()
            return
        if e.button() == Qt.LeftButton:
            # 空白边框按住可拖动悬浮编辑窗，方便挪开看/点后面的窗口
            self._drag_pos = e.globalPos()
            self._drag_origin = self.frameGeometry().topLeft()
            self._dragging = True
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._dragging and (e.buttons() & Qt.LeftButton):
            self.move(self._drag_origin + (e.globalPos() - self._drag_pos))
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._dragging = False
        super().mouseReleaseEvent(e)

    def _set_tool(self, k: str):
        self.canvas.tool = k
        if k == "text":
            self.canvas.setCursor(Qt.IBeamCursor)
        else:
            self.canvas.setCursor(Qt.CrossCursor)

    def _set_color(self, c: str):
        self.canvas.color = QColor(c)

    def _set_width(self, w: int):
        self.canvas.stroke_w = int(w)

    def _on_action(self, key: str):
        if key == "undo":
            self.canvas.undo()
        elif key == "redo":
            self.canvas.redo()
        elif key == "ocr":
            self._run_ocr()
        elif key == "save":
            self._save()
        elif key == "saveas":
            self._save_as()
        elif key == "copy":
            self._copy()
        elif key == "cancel":
            self._cancel()

    def _run_ocr(self):
        if self._ocr_worker is not None and self._ocr_worker.isRunning():
            return

        from utils.ocr_util import (
            is_local_ocr_ready, missing_local_ocr_message,
            pixmap_to_png_path, ocr_engine_hint,
        )
        from PyQt5.QtWidgets import QMessageBox

        # 未装本地 OCR：只提示去「关于」安装，不跑线程
        if not is_local_ocr_ready():
            box = QMessageBox(self)
            box.setWindowTitle("需要安装 OCR")
            box.setIcon(QMessageBox.Information)
            box.setText("尚未安装本地 OCR 引擎")
            box.setInformativeText(missing_local_ocr_message())
            set_message_box_selectable(box)
            btn_about = box.addButton("打开系统总览…", QMessageBox.AcceptRole)
            btn_ok = box.addButton("知道了", QMessageBox.RejectRole)
            attach_ok_auto_close(box, btn_ok)
            box.exec_()
            if box.clickedButton() is btn_about:
                try:
                    from PyQt5.QtWidgets import QApplication
                    for w in QApplication.topLevelWidgets():
                        if hasattr(w, "_goto_overview_page"):
                            w._goto_overview_page()
                            break
                except Exception:
                    log.debug("打开系统总览安装 OCR 失败", exc_info=True)
            return

        # 主线程：合成图 → 临时 PNG（子线程禁止碰 QPixmap）
        pix = self.canvas.render_result()
        try:
            path = pixmap_to_png_path(pix)
            hint = ocr_engine_hint()
        except Exception as e:
            dlg = self._ensure_ocr_dialog()
            dlg.set_error(f"准备图像失败：{e}")
            self._present_ocr_dialog()
            return

        dlg = self._ensure_ocr_dialog()
        dlg.set_loading(f"引擎：{hint}")
        self._present_ocr_dialog()

        worker = _OcrWorker(path, parent=None)
        self._ocr_worker = worker
        worker.finished_ok.connect(self._on_ocr_ok)
        worker.finished_err.connect(self._on_ocr_err)
        worker.finished.connect(self._on_ocr_thread_finished)
        worker.start()

    def _on_ocr_ok(self, text: str, eng: str):
        if self._ocr_dlg:
            try:
                self._ocr_dlg.set_result(text, eng)
                self._raise_ocr_dialog(activate=False)
            except RuntimeError:
                self._ocr_dlg = None

    def _on_ocr_err(self, msg: str):
        if self._ocr_dlg:
            try:
                self._ocr_dlg.set_error(msg)
                self._raise_ocr_dialog(activate=False)
            except RuntimeError:
                self._ocr_dlg = None

    def _on_ocr_thread_finished(self):
        w = self.sender()
        if w is self._ocr_worker:
            self._ocr_worker = None
        if w in _OCR_THREAD_KEEPALIVE:
            try:
                _OCR_THREAD_KEEPALIVE.remove(w)
            except ValueError:
                pass
        if w is not None:
            w.deleteLater()

    def _orphan_ocr_dialog(self, dlg):
        """编辑窗关闭时：把 OCR 结果窗拆成独立置顶窗，避免随 parent 销毁。"""
        if dlg is None:
            return
        try:
            was_visible = dlg.isVisible()
        except RuntimeError:
            return
        try:
            dlg.setParent(None)
            dlg.setWindowFlags(
                Qt.Dialog
                | Qt.WindowStaysOnTopHint
                | Qt.WindowTitleHint
                | Qt.WindowCloseButtonHint
            )
            if was_visible:
                dlg.show()
                dlg.raise_()
        except Exception:
            pass

    def _detach_ocr_for_close(self):
        """关编辑窗时安全剥离 OCR 线程，避免 Destroyed while still running。"""
        w = self._ocr_worker
        dlg = self._ocr_dlg
        # 先与编辑窗脱钩，结果窗可继续显示（线程结果仍可写进 dlg）
        self._orphan_ocr_dialog(dlg)
        self._ocr_dlg = None

        if w is None:
            return

        # 断开对编辑器的槽，避免已销毁对象被回调
        for sig, slot in (
            (w.finished_ok, self._on_ocr_ok),
            (w.finished_err, self._on_ocr_err),
            (w.finished, self._on_ocr_thread_finished),
        ):
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass

        if w.isRunning():
            # 结果仍回传到已独立的对话框
            if dlg is not None:
                try:
                    w.finished_ok.connect(dlg.set_result)
                    w.finished_err.connect(dlg.set_error)
                except Exception:
                    pass

            def _keep_cleanup():
                if w in _OCR_THREAD_KEEPALIVE:
                    try:
                        _OCR_THREAD_KEEPALIVE.remove(w)
                    except ValueError:
                        pass
                try:
                    w.deleteLater()
                except Exception:
                    pass

            try:
                w.finished.connect(_keep_cleanup)
            except Exception:
                pass
            if w not in _OCR_THREAD_KEEPALIVE:
                _OCR_THREAD_KEEPALIVE.append(w)
            # 不在关窗路径上强行 terminate（OCR/网络中途杀线程更易崩）
            self._ocr_worker = None
        else:
            try:
                w.deleteLater()
            except Exception:
                pass
            self._ocr_worker = None

    def _save(self):
        self._done = True
        self.on_save(self.canvas.render_result())
        self.close()

    def _save_as(self):
        if callable(self.on_save_as):
            ok = self.on_save_as(self.canvas.render_result())
            if ok is False:
                # 用户在对话框点了「取消」：留在编辑状态，不销毁标注
                return
        else:
            self.on_save(self.canvas.render_result())
        self._done = True
        self.close()

    def _copy(self):
        self._done = True
        self.on_copy(self.canvas.render_result())
        self.close()

    def _cancel(self):
        self._done = True
        self.on_cancel()
        self.close()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._cancel()
        elif e.key() == Qt.Key_Z and (e.modifiers() & Qt.ControlModifier):
            self.canvas.undo()
        elif e.key() == Qt.Key_Y and (e.modifiers() & Qt.ControlModifier):
            self.canvas.redo()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ControlModifier):
            # 对勾 ≈ 保存并关闭；Enter 与绿勾一致
            if self.canvas._text_edit is None:
                self._save()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter) and (e.modifiers() & Qt.ControlModifier):
            self._save()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        self._detach_ocr_for_close()
        if not self._done:
            self.on_cancel()
            self._done = True
        super().closeEvent(e)


# ============================================================
# 截图页
# ============================================================
class PageScreenshot(QWidget):
    # ===== 窗口高度 BUG 根治（与 Grok 诊断一致：heightForWidth 把虚高传给主窗口）=====
    # 本页含 wordWrap 自动换行的提示标签，会让整页 hasHeightForWidth()=True，Qt 据此按
    # 当前较窄宽度把页面高度算大，虚高一路传到主窗口，抬高窗口“首选高度”，真机上一拖
    # 窗口就长高、缩不回去。对照 6 个正常页面 hasHeightForWidth()=False、首选高度恒 836。
    # 修法：对外声明“高度不随宽度变化”，并把对外首选高度压到不超过侧边栏；标签内部
    # 仍照常换行、不截断文字，实际布局照常把可用高度分给本页，视觉无变化。
    def hasHeightForWidth(self):
        return False

    def sizeHint(self):
        s = super().sizeHint()
        return QSize(s.width(), min(s.height(), 700))

    def __init__(self):
        super().__init__()
        self._main_win = None
        self._win_hidden = False
        # 热键触发时由主窗口注入：切到「截图工具」页
        self._goto_shot_cb = None

        lay = QVBoxLayout(self)
        # 与系统总览一致：ContentRoot 已有左右内边距；不设会走 Qt 默认 ~11px 再叠一层
        lay.setContentsMargins(0, 0, 0, 0)

        # ================= 第 1 排（压紧高度）：A截图启用 / B截图键 / C截图设置 =================
        row1 = QHBoxLayout(); lay.addLayout(row1)

        # --- A 截图启用（功能区标准卡） ---
        gb_enable = make_card("CardShotEnable")
        vb_en = QVBoxLayout(gb_enable)
        vb_en.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        install_card_title(gb_enable, vb_en, "截图启用")
        self.checkbox_enable = QCheckBox("启用截图")
        self.checkbox_enable.setStyleSheet(TEXT_STYLE_T)
        self.checkbox_enable.stateChanged.connect(self._toggle)
        vb_en.addWidget(self.checkbox_enable)

        fmt_row = QHBoxLayout()
        lb_fmt = QLabel("格式：")
        lb_fmt.setProperty("typo", "muted")
        self.fmt_group = QButtonGroup(self)               # 格式单选
        self.rb_png = QRadioButton("PNG"); self.rb_png.setStyleSheet(TEXT_STYLE_T)
        self.rb_jpg = QRadioButton("JPG"); self.rb_jpg.setStyleSheet(TEXT_STYLE_T)
        self.fmt_group.addButton(self.rb_png); self.fmt_group.addButton(self.rb_jpg)
        self.rb_png.setChecked(True)
        fmt_row.addWidget(lb_fmt); fmt_row.addWidget(self.rb_png)
        fmt_row.addWidget(self.rb_jpg); fmt_row.addStretch()
        vb_en.addLayout(fmt_row)

        # 热键失效时的兜底：不依赖全局钩子，直接进截图流程
        self.btn_shot_now = apply_medium_button(QPushButton("立即截图"))
        self.btn_shot_now.clicked.connect(self._show_overlay)
        vb_en.addWidget(self.btn_shot_now)

        vb_en.addStretch()                       # 顶对齐，配合 4.7 同排等高
        row1.addWidget(gb_enable, 12)

        # --- B 截图键（功能区标准卡） ---
        gb_mod = make_card("CardShotHotkey")
        vb_mod = QVBoxLayout(gb_mod)
        vb_mod.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        install_card_title(gb_mod, vb_mod, "截图键")

        key_grid = QGridLayout()
        # 修饰键 | 竖线 | Q W E R / A S D F / Z X C V
        # 主键用独立列均分宽度，避免 setFixedWidth 把字母裁掉
        key_grid.setHorizontalSpacing(4)
        key_grid.setVerticalSpacing(2)
        key_grid.setContentsMargins(0, 0, 0, 0)

        self._key_sep = QFrame()
        self._key_sep.setFrameShape(QFrame.NoFrame)     # 原生 VLine 一旦 setStyleSheet 就画不出来，改用填色细矩形
        self._key_sep.setFixedWidth(1)
        self._key_sep.setStyleSheet(f"background: {tk('border')};")
        key_grid.addWidget(self._key_sep, 0, 1, 3, 1)   # 纵跨3行的竖分隔线，隔开"功能键"与"主键"两列

        self.mod_group = QButtonGroup(self)          # 独占单选：功能键列，每个一行
        for i, m in enumerate(["Ctrl", "Alt", "Shift"]):
            rb = QRadioButton(m)
            rb.setStyleSheet(TEXT_STYLE_T)
            rb.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            self.mod_group.addButton(rb)
            key_grid.addWidget(rb, i, 0)

        # 主键：指示器 + 字母在 Windows 上至少约 40px，固定 36 会裁掉右侧
        _KEY_RB_QSS = (
            TEXT_STYLE_T
            + " QRadioButton{spacing:3px; padding:0px 1px 0px 0px;}"
        )
        self.key_group = QButtonGroup(self)
        self._key_radios = []  # type: list
        for i, line in enumerate(
            [["Q", "W", "E", "R"], ["A", "S", "D", "F"], ["Z", "X", "C", "V"]]
        ):
            for j, k in enumerate(line):
                rb = QRadioButton(k)
                rb.setStyleSheet(_KEY_RB_QSS)
                rb.setMinimumWidth(40)
                rb.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
                self.key_group.addButton(rb)
                self._key_radios.append(rb)
                # 列 2..5：四个主键均分右侧空间
                key_grid.addWidget(rb, i, 2 + j)

        key_grid.setColumnStretch(0, 0)
        key_grid.setColumnStretch(1, 0)
        for c in range(2, 6):
            key_grid.setColumnMinimumWidth(c, 40)
            key_grid.setColumnStretch(c, 1)

        vb_mod.addLayout(key_grid)
        vb_mod.addStretch()                       # 顶对齐
        # 略加宽截图键卡：容纳修饰键 + 4 列主键，避免字母贴边被切
        row1.addWidget(gb_mod, 30)

        # --- C 截图设置（功能区标准卡） ---
        gb_out = make_card("CardShotOutput")
        vb_out = QVBoxLayout(gb_out)
        vb_out.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        install_card_title(gb_out, vb_out, "截图设置")

        # 保存路径：与速存图文「文件夹」同款（圆角框 + 📁）
        path_row = QHBoxLayout()
        self.path = QLineEdit(os.path.join(os.path.expanduser("~"), "Pictures", "ScreenshotImageSaver"))
        self._path_icon_action = apply_folder_path_edit(self.path)
        self.btn_select_dir = apply_medium_button(QPushButton("另选保存"))
        self.btn_select_dir.clicked.connect(self._choose_dir)
        path_row.addWidget(self.path, 1)
        path_row.addWidget(self.btn_select_dir)
        vb_out.addLayout(path_row)

        # 勾选项：最长文案单独一行，避免窄窗裁切
        self.chk_edit = QCheckBox("截图后编辑标注")
        self.chk_copy = QCheckBox("自动复制到剪贴板")  # 最长 → 第二行
        self.chk_hide = QCheckBox("隐藏当前窗口")
        self.chk_detect_win = QCheckBox("识别窗口")
        for c in (self.chk_edit, self.chk_copy, self.chk_hide, self.chk_detect_win):
            c.setStyleSheet(TEXT_STYLE_T)

        chk_row1 = QHBoxLayout()
        chk_row1.setSpacing(12)
        chk_row1.addWidget(self.chk_edit)
        chk_row1.addWidget(self.chk_hide)
        chk_row1.addStretch(1)
        vb_out.addLayout(chk_row1)

        chk_row2 = QHBoxLayout()
        chk_row2.setSpacing(12)
        chk_row2.addWidget(self.chk_copy)
        chk_row2.addWidget(self.chk_detect_win)
        chk_row2.addStretch(1)
        vb_out.addLayout(chk_row2)

        self.chk_edit.setChecked(False)                   # ★ 默认不勾
        self.chk_copy.setChecked(True)
        self.chk_detect_win.setChecked(False)
        vb_out.addStretch()                       # 顶对齐
        row1.addWidget(gb_out, 58)

        # ================= 第 2 排（拉伸吃满剩余高度）：操作记录 + 截图预览 =================
        row2 = QHBoxLayout(); lay.addLayout(row2, 1)   # 拉伸系数 1，第一行越压紧，这一排分到的高度越大

        gb_log = make_card("CardShotLog")
        vb_log = QVBoxLayout(gb_log)
        vb_log.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        install_card_title(gb_log, vb_log, "截图与操作记录")
        # 对齐速存图文运行记录：simple 样式 + 橙条选中 + 新记录选中并滚屏 + 空白取消选中
        self.list = ShotRunLogList()
        self.list.setObjectName("ShotList")
        self.list.setUniformItemSizes(True)
        self.list.setSpacing(1)
        apply_simple_record(self.list)
        try:
            self.list.setFocusPolicy(Qt.StrongFocus)
            self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        except Exception:
            pass
        theme.changed.connect(self.refresh_theme)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_menu)
        self.list.itemDoubleClicked.connect(self._on_dblclick)
        self.list.currentItemChanged.connect(self._on_current_changed)   # ★ 选中→右侧预览
        vb_log.addWidget(self.list)
        row2.addWidget(gb_log, 3)

        gb_prev = make_card("CardShotPreview")
        vb_prev = QVBoxLayout(gb_prev)
        vb_prev.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        install_card_title(gb_prev, vb_prev, "截图预览")
        self.preview = PreviewLabel()
        vb_prev.addWidget(self.preview, 1)
        self.lbl_preview_meta = QLabel()
        self.lbl_preview_meta.setAlignment(Qt.AlignHCenter)
        self.lbl_preview_meta.setWordWrap(True)
        # v9.9.6 修复：这个标签平时是"文件名 + 换行 + 分辨率"两行，但文件名长短不定，
        # 长文件名换行后行数会变，wordWrap 让它的高度跟着内容/宽度波动（hasHeightForWidth）。
        # 页面里这一块又没有 QScrollArea 兜底，波动会直接传给页面→主窗口的最小高度，
        # 窗口在拖动/跨屏 DPI 重新布局时就会出现"越拖越高、缩不回去"——跟"速存图文"
        # 页面之前那个问题是同一类根因。这里钉死高度（超出部分裁掉，完整信息放 tooltip），
        # 从根上掐断这条传染链路。
        self.lbl_preview_meta.setFixedHeight(40)
        self.lbl_preview_meta.setProperty("typo", "muted")
        self.lbl_preview_meta.setVisible(False)
        vb_prev.addWidget(self.lbl_preview_meta)
        row2.addWidget(gb_prev, 2)

        # 初始日志
        ensure_dir(self.path.text())
        self._log(f"📁 初始路径：{self.path.text()}")
        self._log("⏹️ 当前截图监听未启用（空闲）")

        # 信号与监听
        self._sig = _Signal()
        self._sig.trigger.connect(self._show_overlay)

        # 默认热键：Alt + A（单修饰键，避开 Ctrl+字母 的常见冲突）
        self._check_radio(self.mod_group, "Alt")
        self._check_radio(self.key_group, "A")
        self.hotkey = self._current_hotkey()
        self._hotkey_registered = False
        self._hotkey_filter = None  # type: _ShotHotkeyFilter | None
        self._hotkey_id = _SHOT_HOTKEY_ID
        self._op_item = None        # 当前截图操作对应的唯一记录行（状态随时间更新）
        self.mod_group.buttonClicked.connect(self._on_hotkey_changed)
        self.key_group.buttonClicked.connect(self._on_hotkey_changed)

        # 保存目录由「关于」弹窗统一管理，页面 UI 隐藏
        self.path.setVisible(False)
        self.btn_select_dir.setVisible(False)

    # ────────────────────────────────────────
    # 用户习惯（records/user.txt · screenshot 段）
    # ────────────────────────────────────────
    def export_settings(self) -> dict:
        """导出当前截图相关设置，供 user_prefs 落盘。"""
        mb = self.mod_group.checkedButton()
        kb = self.key_group.checkedButton()
        return {
            "enabled": bool(self.checkbox_enable.isChecked()),
            "save_path": (self.path.text() or "").strip(),
            "format": "jpg" if self.rb_jpg.isChecked() else "png",
            "hotkey_mod": mb.text() if mb else "Alt",
            "hotkey_key": kb.text() if kb else "A",
            "auto_edit": bool(self.chk_edit.isChecked()),
            "auto_copy": bool(self.chk_copy.isChecked()),
            "hide_window": bool(self.chk_hide.isChecked()),
            "detect_window": bool(self.chk_detect_win.isChecked()),
        }

    def apply_settings(self, d: dict):
        """从 user.txt 恢复设置。未知/空字段保持现状。"""
        if not isinstance(d, dict):
            return
        path = (d.get("save_path") or "").strip()
        if path:
            self.path.setText(path)
            try:
                ensure_dir(path)
            except Exception:
                pass

        img_fmt = str(d.get("format") or "png").lower()
        if img_fmt == "jpg":
            self.rb_jpg.setChecked(True)
        else:
            self.rb_png.setChecked(True)

        mod = str(d.get("hotkey_mod") or "Alt")
        key = str(d.get("hotkey_key") or "A")
        self._check_radio(self.mod_group, mod)
        self._check_radio(self.key_group, key)
        self.hotkey = self._current_hotkey()

        if "auto_edit" in d:
            self.chk_edit.setChecked(bool(d.get("auto_edit")))
        if "auto_copy" in d:
            self.chk_copy.setChecked(bool(d.get("auto_copy")))
        if "hide_window" in d:
            self.chk_hide.setChecked(bool(d.get("hide_window")))
        if "detect_window" in d:
            self.chk_detect_win.setChecked(bool(d.get("detect_window")))

        # 启用态最后设，触发 _toggle 注册/注销热键
        want_on = bool(d.get("enabled", False))
        if self.checkbox_enable.isChecked() != want_on:
            self.checkbox_enable.setChecked(want_on)
        elif want_on:
            self._bind_hotkey()

    # === 日志 ===

    def refresh_theme(self, *_):
        """重刷本页控件级样式（QSS 选择器覆盖不到的部分）。"""
        if hasattr(self, "_key_sep"):
            self._key_sep.setStyleSheet(f"background: {tk('border')};")
        if hasattr(self, "path"):
            restyle_folder_path_edit(self.path, getattr(self, "_path_icon_action", None))
        if hasattr(self, "preview"):
            self.preview.refresh_theme()

    def _log(self, text):
        # ShotRunLogList.addItem：选中最新 + 强制滚屏
        self.list.addItem(text)

    # === 目录/热键 ===
    def _choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择截图保存文件夹")
        if d:
            self.path.setText(d); ensure_dir(d)
            self._log(f"📁 保存路径切换为：{d}")

    def _toggle(self, st):
        if st == Qt.Checked:
            self._bind_hotkey()
            if self._hotkey_registered:
                self._log(f"🟢 截图监听已启用（等待：{self.hotkey.upper()}）")
            else:
                self._log("⚠️ 热键未就绪，仍可点「立即截图」框选")
        else:
            self._log("⏹️ 已停止截图监听（空闲）")
            self._unbind_hotkey()

    @staticmethod
    def _check_radio(group, text):
        for b in group.buttons():
            if b.text() == text:
                b.setChecked(True); return

    def _current_hotkey(self):
        mb = self.mod_group.checkedButton()
        kb = self.key_group.checkedButton()
        mod = mb.text().lower() if mb else "alt"
        key = kb.text().lower() if kb else "a"
        return f"{mod}+{key}"

    def _hotkey_mod_vk(self):
        """解析当前 UI 为 (RegisterHotKey 修饰位, 虚拟键码)。"""
        mb = self.mod_group.checkedButton()
        kb = self.key_group.checkedButton()
        mod_name = (mb.text() if mb else "Alt").strip().lower()
        key_name = (kb.text() if kb else "A").strip().upper()
        mod = _MOD_MAP.get(mod_name, _MOD_ALT) | _MOD_NOREPEAT
        if len(key_name) != 1 or not ("A" <= key_name <= "Z"):
            key_name = "A"
        vk = ord(key_name)
        return mod, vk

    def _bind_hotkey(self):
        """用 Windows RegisterHotKey 注册全局快捷键（与速存图文一致）。"""
        self._unbind_hotkey()
        if not sys.platform.startswith("win"):
            self._log("❌ 当前系统不支持全局截图热键，请用「立即截图」")
            return
        mod, vk = self._hotkey_mod_vk()
        try:
            ok = bool(ctypes.windll.user32.RegisterHotKey(
                None, int(self._hotkey_id), int(mod), int(vk)
            ))
        except Exception as e:
            self._log(f"❌ 热键注册异常：{e}")
            return
        if not ok:
            err = 0
            try:
                err = int(ctypes.get_last_error() or 0)
            except Exception:
                pass
            self._log(
                f"❌ 热键 {self.hotkey.upper()} 注册失败"
                f"{f'（错误码 {err}）' if err else ''}，可能被其它程序占用；"
                f"可换键或点「立即截图」"
            )
            return
        if self._hotkey_filter is None:
            self._hotkey_filter = _ShotHotkeyFilter(
                lambda: self._sig.trigger.emit(), self._hotkey_id
            )
            app = QCoreApplication.instance()
            if app is not None:
                app.installNativeEventFilter(self._hotkey_filter)
        self._hotkey_registered = True

    def _unbind_hotkey(self):
        if not self._hotkey_registered and self._hotkey_filter is None:
            # 仍尝试 Unregister，避免异常退出后残留
            pass
        try:
            ctypes.windll.user32.UnregisterHotKey(None, int(self._hotkey_id))
        except Exception:
            log.debug("注销截图热键失败", exc_info=True)
        self._hotkey_registered = False
        # filter 可保留；重复 install 无害，卸载需拿 QApp 且无 remove 对称 API 收益小

    def hotkey_inventory(self):
        key = (self.hotkey or self._current_hotkey() or "alt+a").upper()
        if not self.checkbox_enable.isChecked():
            st = "off"
        elif getattr(self, "_hotkey_registered", False):
            st = "ok"
        else:
            st = "busy"
        return [{"group": "截图工具", "name": "截图", "key": key, "state": st}]

    def rebind_hotkeys(self):
        key = (self.hotkey or self._current_hotkey() or "alt+a").upper()
        if not self.checkbox_enable.isChecked():
            self._unbind_hotkey()
            return [("截图", key, None)]
        self._bind_hotkey()
        return [("截图", key, bool(getattr(self, "_hotkey_registered", False)))]

    def shutdown(self):
        """主窗口关窗时调用：解绑热键、关闭框选遮罩与标注编辑窗。

        AnnotationEditor / Overlay 是独立顶层窗；不关的话主窗关掉后它们仍残留，
        且编辑中若 z-order 异常会表现为「主程序关不掉」。
        """
        try:
            self._unbind_hotkey()
        except Exception:
            log.exception("截图页关窗解绑热键失败")

        ov = getattr(self, "overlay", None)
        if ov is not None:
            try:
                ov.close()
            except Exception:
                try:
                    ov.hide()
                except Exception:
                    pass
            self.overlay = None

        ed = getattr(self, "editor", None)
        if ed is not None:
            try:
                ed.destroyed.disconnect(self._on_editor_destroyed)
            except Exception:
                pass
            try:
                # 标记已完成，避免 closeEvent 再走 on_cancel 刷状态
                ed._done = True
            except Exception:
                pass
            try:
                ed.close()
            except Exception:
                try:
                    ed.hide()
                except Exception:
                    pass
            self.editor = None
        # 无论编辑窗是否存在，关页时都解开主窗关闭冻结
        self._set_main_close_frozen(False)

    def _on_hotkey_changed(self, *_):
        self.hotkey = self._current_hotkey()
        if self.checkbox_enable.isChecked():
            self._bind_hotkey()
            if self._hotkey_registered:
                self._log(f"⌨️ 快捷键切换为：{self.hotkey.upper()}")

    # === 主窗口隐藏/恢复 ===
    def _hide_main(self):
        self._main_win = self.window()
        if self._main_win is not None:
            self._main_win.hide()
            self._win_hidden = True

    def _restore_main(self):
        if self._win_hidden and self._main_win is not None:
            self._main_win.show()
            self._main_win.raise_()
            self._main_win.activateWindow()
        self._win_hidden = False

    def set_goto_shot_callback(self, fn):
        """主窗口注入：热键截图时自动跳到本页。"""
        self._goto_shot_cb = fn

    def set_quick_capture(self, is_active_fn, sink_fn):
        """游戏助手在前时：热键/框选只出图，不进编辑、不切到截图页。"""
        self._quick_active_fn = is_active_fn
        self._quick_sink_fn = sink_fn

    def start_capture(self):
        self._show_overlay()

    def _is_quick_capture(self) -> bool:
        fn = getattr(self, "_quick_active_fn", None)
        if not callable(fn):
            return False
        try:
            return bool(fn())
        except Exception:
            return False

    # === 截图流程 ===
    def _show_overlay(self):
        # 避免热键连按 / 立即截图重复叠多层遮罩
        ov = getattr(self, "overlay", None)
        if ov is not None:
            try:
                if ov.isVisible():
                    return
            except RuntimeError:
                self.overlay = None
        self._quick_mode = self._is_quick_capture()
        self._start_op("🔥 截图已触发")
        if not self._quick_mode:
            # 先切到「截图工具」，再截屏（隐藏主窗前切换，恢复后仍停在本页）
            cb = self._goto_shot_cb
            if callable(cb):
                try:
                    cb()
                except Exception:
                    log.debug("截图前切换到截图页失败", exc_info=True)
        hide = bool(self.chk_hide.isChecked()) or bool(self._quick_mode)
        if hide:
            self._hide_main()
            QTimer.singleShot(180, self._start_overlay)   # 等窗口真正消失再抓屏
        else:
            self._start_overlay()

    def _start_overlay(self):
        self.overlay = Overlay(
            self._on_captured,
            self._on_overlay_cancel,
            detect_window=bool(self.chk_detect_win.isChecked()),
        )

    def _on_overlay_cancel(self):
        self._quick_mode = False
        self._restore_main()
        self._op_done_status("❌ 截图已取消")

    def _set_main_close_frozen(self, frozen: bool):
        """截图编辑期间冻结主程序关闭按钮；编辑结束再解开。"""
        win = self.window()
        if win is None:
            return
        fn = getattr(win, "set_close_button_frozen", None)
        if not callable(fn):
            return
        try:
            fn(bool(frozen))
        except Exception:
            log.exception("冻结/解冻主窗关闭按钮失败 frozen=%s", frozen)

    def _on_editor_destroyed(self, *_):
        """标注窗销毁后必解冻，避免主窗关闭按钮一直灰掉。"""
        self.editor = None
        self._set_main_close_frozen(False)

    def _on_captured(self, pixmap):
        self._restore_main()      # 拿到图立即恢复主窗口，编辑阶段不必再藏
        if getattr(self, "_quick_mode", False):
            self._quick_mode = False
            sink = getattr(self, "_quick_sink_fn", None)
            if callable(sink):
                try:
                    sink(pixmap)
                except Exception:
                    log.exception("快捷截图送到游戏助手失败")
            self._op_done_status("🎮 已送到游戏助手")
            return
        if self.chk_edit.isChecked():
            self._op_status("🖌 编辑标注中…")
            # 旧编辑窗若还在，先解绑，避免双重 destroyed 乱序
            old = getattr(self, "editor", None)
            if old is not None:
                try:
                    old.destroyed.disconnect(self._on_editor_destroyed)
                except Exception:
                    pass
            self.editor = AnnotationEditor(
                pixmap,
                on_save=lambda pix: self._finalize(pix, do_copy=True, do_save=True),
                on_copy=lambda pix: self._finalize(pix, do_copy=True, do_save=False),
                on_cancel=lambda: self._op_done_status("❌ 已取消（未保存）"),
                on_save_as=self._ask_save_as)
            # 编辑中禁止关主程序（关闭按钮灰化 + closeEvent 拦截）
            self._set_main_close_frozen(True)
            try:
                self.editor.destroyed.connect(self._on_editor_destroyed)
            except Exception:
                pass
            self.editor.show()
            self.editor.raise_(); self.editor.activateWindow()
        else:
            self._finalize(pixmap, do_copy=self.chk_copy.isChecked(), do_save=True)

    def _build_filepath(self):
        ext = "jpg" if self.rb_jpg.isChecked() else "png"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"screenshot_{stamp}"
        folder = self.path.text()
        candidate = os.path.join(folder, f"{base}.{ext}")
        i = 1
        while os.path.exists(candidate):
            candidate = os.path.join(folder, f"{base}_{i:03d}.{ext}")
            i += 1
        return candidate, ext

    def _finalize(self, pixmap, do_copy, do_save):
        if do_copy:
            QApplication.clipboard().setPixmap(pixmap)
            self._op_status("📋 已复制到剪贴板")
        if not do_save:
            self._op_done_status("📋 已复制到剪贴板")
            return
        ensure_dir(self.path.text())
        full, ext = self._build_filepath()
        img_fmt = "JPG" if ext == "jpg" else "PNG"
        quality = 92 if img_fmt == "JPG" else -1
        try:
            ok = pixmap.save(full, img_fmt, quality)
            if ok:
                self._op_done_image(full, "保存")
            else:
                self._op_done_status(f"❌ 保存失败：写入被拒绝（{full}）")
        except Exception as e:
            self._op_done_status(f"❌ 保存失败：{type(e).__name__}: {e}")

    def _ask_save_as(self, pixmap):
        """编辑工具栏「另存为」：让用户指定任意保存位置。

        不要用 setWindowFlags 临时去置顶：会重建 HWND，编辑窗会闪没半秒。
        做法：文件框 parent 挂编辑窗（transient，自然叠在上面）+ 冻结置顶同步。
        返回 True=已处理并关闭编辑，False=用户取消、留在编辑。
        """
        ed = getattr(self, "editor", None)
        parent = self
        if ed is not None:
            try:
                if ed.isVisible():
                    parent = ed
                    ed.begin_freeze_toplevel()
            except Exception:
                parent = self

        default_full, ext = self._build_filepath()
        default_dir = self.path.text()
        try:
            path, _ = QFileDialog.getSaveFileName(
                parent, "另存为",
                os.path.join(default_dir, os.path.basename(default_full)),
                f"图片 (*.{ext})",
            )
        finally:
            if ed is not None and parent is ed:
                try:
                    ed.end_freeze_toplevel()
                except Exception:
                    pass
        if not path:
            self._op_done_status("❌ 已取消另存为")
            return False
        if not os.path.splitext(path)[1]:
            path = f"{path}.{ext}"
        if self.chk_copy.isChecked():
            QApplication.clipboard().setPixmap(pixmap)
            self._op_status("📋 已复制到剪贴板")
        try:
            ok = pixmap.save(path)
            if ok:
                self._op_done_image(path, "另存为")
            else:
                self._op_done_status(f"❌ 另存失败：写入被拒绝（{path}）")
        except Exception as e:
            self._op_done_status(f"❌ 另存失败：{type(e).__name__}: {e}")
        return True

    # === 记录条目（纯文本，路径存 UserRole；预览交给右侧面板）===
    def _add_shot_item(self, path, kind="保存"):
        item = QListWidgetItem(f"📸 {kind}：{os.path.basename(path)}")
        item.setData(Qt.UserRole, path)
        # addItem 内部：选中最新 + 滚屏；预览由 currentItemChanged 刷新
        self.list.addItem(item)

    # === 操作记录：一次截图操作只对应列表里一条记录，状态随时间更新 ===
    def _start_op(self, status):
        """开启一次截图操作，建立该操作对应的唯一记录行。"""
        if self._op_item is not None:
            self._op_item = None
        item = QListWidgetItem(status)
        self._op_item = item
        self.list.addItem(item)

    def _op_status(self, status):
        """更新当前操作记录的状态文本（不新增行）。"""
        item = self._op_item
        if item is None:
            self._log(status)   # 无进行中操作时兜底为普通日志
            return
        item.setText(status)
        self._refresh_current_preview()

    def _op_done_image(self, path, kind="保存"):
        """操作成功产出图片：同一行升级为图片记录（可预览/右键）。kind 区分 保存/另存为。"""
        item = self._op_item
        self._op_item = None
        if item is None:
            self._add_shot_item(path, kind)
        else:
            item.setText(f"📸 {kind}：{os.path.basename(path)}")
            item.setData(Qt.UserRole, path)
            self._refresh_current_preview()
        show_cursor_toast("截图", f"已{kind}", accent="ok")

    def _op_done_status(self, status):
        """操作以状态收尾（取消/失败/仅复制）：同一行定格为状态文本。"""
        item = self._op_item
        self._op_item = None
        if item is None:
            self._log(status)
        else:
            item.setText(status)
            self._refresh_current_preview()
        note, accent = self._toast_from_shot_status(status)
        show_cursor_toast("截图", note, accent=accent)

    @staticmethod
    def _toast_from_shot_status(status):
        """列表保留完整状态；气泡一行：截图 · 状态。"""
        s = status or ""
        failed = "❌" in s
        if "取消" in s:
            return "已取消", "err" if failed else "info"
        if "另存失败" in s or ("另存" in s and "失败" in s):
            return "另存失败", "err"
        if "保存失败" in s:
            return "保存失败", "err"
        if "复制" in s:
            return "已复制", "ok"
        if failed:
            return "操作失败", "err"
        return "已完成", "ok"

    def _refresh_current_preview(self):
        """当前行的文字/图片数据变化后，刷新右侧预览。"""
        cur = self.list.currentItem()
        if cur is not None:
            try:
                self._on_current_changed(cur, cur)
            except Exception:
                pass

    def _on_current_changed(self, cur, _prev):
        """预览始终绑定当前选中记录（对齐速存图文）。"""
        if cur is None:
            self.preview.show_empty()
            self.lbl_preview_meta.clear()
            self.lbl_preview_meta.setVisible(False)
            return
        path = cur.data(Qt.UserRole)
        if isinstance(path, str) and path.strip() and os.path.isfile(path):
            pix = QPixmap(path)
            if not pix.isNull():
                self.preview.set_image(pix)
                meta_text = f"{os.path.basename(path)}\n{pix.width()}×{pix.height()}"
                self.lbl_preview_meta.setText(meta_text)
                self.lbl_preview_meta.setVisible(True)
                return
        # 操作日志等非截图条目：主区显示文案
        self.preview.set_text_content(cur.text() or "")
        self.lbl_preview_meta.clear()
        self.lbl_preview_meta.setVisible(False)

    def _on_dblclick(self, item):
        path = item.data(Qt.UserRole)
        if path and os.path.exists(path):
            open_file(path)

    def _on_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        path = item.data(Qt.UserRole)
        if not path:
            return
        menu = QMenu(self)
        act_open = menu.addAction("打开")
        act_copy = menu.addAction("复制到剪贴板")
        act_loc = menu.addAction("在文件夹中显示")
        menu.addSeparator()
        act_rm = menu.addAction("从列表移除")
        act_del = menu.addAction("删除文件")
        chosen = menu.exec_(self.list.mapToGlobal(pos))
        if chosen is None:
            return
        exists = os.path.exists(path)
        if chosen == act_open and exists:
            open_file(path)
        elif chosen == act_copy and exists:
            pm = QPixmap(path)
            if not pm.isNull():
                QApplication.clipboard().setPixmap(pm)
                self._log("📋 已复制到剪贴板")
        elif chosen == act_loc and exists:
            open_in_file_manager(path, select=True)
        elif chosen == act_rm:
            self.list.takeItem(self.list.row(item))
        elif chosen == act_del:
            try:
                if exists:
                    os.remove(path)
                self.list.takeItem(self.list.row(item))
                self._log(f"🗑️ 已删除文件：{path}")
            except Exception as e:
                self._log(f"❌ 删除失败：{type(e).__name__}: {e}")

    # === 对外：停止监听 ===
    def ensure_stopped(self):
        if self.checkbox_enable.isChecked():
            self.checkbox_enable.setChecked(False)
        self._unbind_hotkey()
        self._restore_main()
