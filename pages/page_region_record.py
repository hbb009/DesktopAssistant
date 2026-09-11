# pages/page_region_record.py
# ---------------------------------------------------------------------------
# 区域录屏：
#   · Alt+2 一律先进入选区/调整：无默认区域 → 定位+调整；有默认区域 → 直接调整
#   · 调整中再 Alt+2（或点「开始录制」）才正式开录
#   · 录制中外框（比录区大 1px，不进画面）
#   · 操作记录：左侧视频缩略图 + 单击播放（无独立预览区）
# ---------------------------------------------------------------------------

from __future__ import annotations

import os
import sys
import subprocess
from datetime import datetime

from PyQt5.QtCore import (
    Qt, QRect, QRectF, QPoint, QTimer, QSize, pyqtSignal, QObject, QThread,
)
from PyQt5.QtGui import (
    QPainter, QColor, QPen, QFont, QPixmap, QBrush, QPolygon, QPainterPath,
)
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QListWidget, QListWidgetItem, QApplication,
    QMessageBox, QAbstractItemView,
)

from styles.style_all import (
    TEXT_STYLE,
    install_card_title,
    make_card,
    restyle_card_frame,
    apply_medium_button,
    apply_simple_record,
    CARD_LEFT_GAP,
    CARD_TOP_GAP,
    CARD_RIGHT_GAP,
    CARD_BOTTOM_GAP,
    tk,
    message_box_info, message_box_warn,
)
from utils.region_recorder import (
    RegionRecorder,
    REGION_RECORD_SUBDIR,
    MAX_SECONDS,
    find_ffmpeg,
    ffmpeg_missing_message,
    ensure_even,
    probe_video_info,
    format_duration,
)
from utils.logger import get_logger
from utils.file_utils import ensure_dir

log = get_logger(__name__)

TEXT_STYLE_T = TEXT_STYLE + " background: transparent;"

HOTKEY_START = "alt+2"
HOTKEY_STOP = "alt+3"

# 调整手柄命中半宽（逻辑像素）
_HANDLE = 7
_MIN_SIDE = 32
# 中心移动图标半径（逻辑像素）
_MOVE_GRIP = 18


def _open_in_file_manager(path, select=True):
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
            log.exception("打开目录失败 path=%s", path)


def _open_file(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        log.exception("打开文件失败 path=%s", path)


def _virtual_geometry() -> QRect:
    rect = QRect()
    for s in QApplication.screens() or []:
        rect = rect.united(s.geometry())
    if rect.isNull() or rect.width() < 1:
        scr = QApplication.primaryScreen()
        if scr:
            return QRect(scr.geometry())
        return QRect(0, 0, 1920, 1080)
    return rect


def logical_rect_to_gdigrab(rect: QRect):
    rect = rect.normalized()
    center = rect.center()
    screen = QApplication.screenAt(center) or QApplication.primaryScreen()
    dpr = float(screen.devicePixelRatio()) if screen else 1.0
    if dpr <= 0:
        dpr = 1.0
    virt = _virtual_geometry()
    x = int(round((rect.x() - virt.x()) * dpr))
    y = int(round((rect.y() - virt.y()) * dpr))
    w = ensure_even(int(round(rect.width() * dpr)))
    h = ensure_even(int(round(rect.height() * dpr)))
    return x, y, w, h


def rect_to_dict(r: QRect) -> dict:
    r = r.normalized()
    return {"x": int(r.x()), "y": int(r.y()), "w": int(r.width()), "h": int(r.height())}


def dict_to_rect(d) -> QRect | None:
    if not isinstance(d, dict):
        return None
    try:
        w, h = int(d.get("w") or 0), int(d.get("h") or 0)
        if w < _MIN_SIDE or h < _MIN_SIDE:
            return None
        return QRect(int(d.get("x") or 0), int(d.get("y") or 0), w, h)
    except (TypeError, ValueError):
        return None


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 / 1024:.2f} MB"


# ============================================================
# 初次框选
# ============================================================
class RegionSelectOverlay(QWidget):
    _DRAG_THRESH = 6

    def __init__(self, on_done, on_cancel):
        super().__init__()
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.start = self.end = None
        self._dragging = False
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setGeometry(_virtual_geometry())
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.start = e.pos()
            self.end = self.start
            self._dragging = False
            self.update()

    def mouseMoveEvent(self, e):
        if self.start is not None:
            self.end = e.pos()
            if (e.pos() - self.start).manhattanLength() >= self._DRAG_THRESH:
                self._dragging = True
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.RightButton:
            self._finish_cancel()
            return
        if e.button() != Qt.LeftButton or self.start is None:
            return
        end = self.end or self.start
        local = QRect(self.start, end).normalized()
        if not self._dragging or local.width() < 10 or local.height() < 10:
            self._finish_cancel()
            return
        global_rect = QRect(
            self.mapToGlobal(local.topLeft()),
            self.mapToGlobal(local.bottomRight()),
        ).normalized()
        self.hide()
        self.close()
        try:
            self.on_done(global_rect)
        except Exception:
            log.exception("选区回调失败")

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._finish_cancel()

    def _finish_cancel(self):
        self.hide()
        self.close()
        try:
            self.on_cancel()
        except Exception:
            log.exception("选区取消回调失败")

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        if self.start and self.end and self._dragging:
            sel = QRect(self.start, self.end).normalized()
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.fillRect(sel, Qt.transparent)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.setPen(QPen(QColor("#EF4444"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(sel)
            txt = f"{sel.width()} × {sel.height()}"
            f = QFont()
            f.setPixelSize(13)
            p.setFont(f)
            tw = p.fontMetrics().horizontalAdvance(txt) + 12
            ty = sel.top() - 24 if sel.top() > 24 else sel.top() + 6
            tx = max(0, min(sel.left(), self.width() - tw))
            p.fillRect(tx, ty, tw, 20, QColor(0, 0, 0, 180))
            p.setPen(QColor("#ffffff"))
            p.drawText(tx + 6, ty + 15, txt)


# ============================================================
# 选区调整（移动 / 八向缩放）+ 底部操作条
# ============================================================
class RegionAdjustOverlay(QWidget):
    """定位完成后可拖动/缩放；Alt+2 确认开录，取消放弃。

    操作条是本窗体的子控件（不是独立顶层窗），保证「取消 / 开始录制」始终可点。
    遮罩每帧用 CompositionMode_Source 绝对绘制，避免 Windows 半透明窗重绘时越拖越暗。
    """

    confirmed = pyqtSignal(QRect)
    cancelled = pyqtSignal()

    # 选区外遮罩 alpha（绝对色，禁止叠加以免越拖越黑）
    _DIM_A = 88

    def __init__(self, global_rect: QRect):
        super().__init__()
        self._rect = global_rect.normalized()
        self._mode = None  # None | 'move' | 'n'|'s'|'e'|'w'|'ne'|...
        self._press = None
        self._orig = None
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setGeometry(_virtual_geometry())

        # 操作条：子控件（点得着），随选区贴边移动
        self._hint = QWidget(self)
        self._hint.setObjectName("RegionAdjustHint")
        self._hint.setAttribute(Qt.WA_StyledBackground, True)
        self._hint.setStyleSheet(
            "#RegionAdjustHint{"
            "  background: rgba(15, 23, 42, 236);"
            "  border: 1px solid rgba(255,255,255,0.18);"
            "  border-radius: 10px;"
            "}"
        )
        hl = QHBoxLayout(self._hint)
        hl.setContentsMargins(12, 8, 12, 8)
        hl.setSpacing(8)
        tip = QLabel("拖中心⊕移动 · 拖边角/边线缩放 · Alt+2 开始录制 · Esc 取消")
        tip.setStyleSheet("color:#F8FAFC; font-size:12px; background:transparent;")
        hl.addWidget(tip)
        self._btn_ok = QPushButton("开始录制  Alt+2")
        self._btn_ok.setCursor(Qt.PointingHandCursor)
        self._btn_ok.setDefault(False)
        self._btn_ok.setAutoDefault(False)
        self._btn_ok.setFocusPolicy(Qt.NoFocus)
        self._btn_ok.setStyleSheet(
            "QPushButton{background:#EF4444;color:#fff;border:none;border-radius:6px;"
            "padding:6px 12px;font-weight:600;font-size:12px;}"
            "QPushButton:hover{background:#DC2626;}"
            "QPushButton:pressed{background:#B91C1C;}"
        )
        self._btn_ok.clicked.connect(self._confirm)
        hl.addWidget(self._btn_ok)
        self._btn_cancel = QPushButton("取消")
        self._btn_cancel.setCursor(Qt.PointingHandCursor)
        self._btn_cancel.setDefault(False)
        self._btn_cancel.setAutoDefault(False)
        self._btn_cancel.setFocusPolicy(Qt.NoFocus)
        self._btn_cancel.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.14);color:#F8FAFC;"
            "border:1px solid rgba(255,255,255,0.28);border-radius:6px;"
            "padding:6px 14px;font-size:12px;font-weight:600;}"
            "QPushButton:hover{background:rgba(255,255,255,0.26);}"
            "QPushButton:pressed{background:rgba(255,255,255,0.34);}"
        )
        self._btn_cancel.clicked.connect(self._cancel)
        hl.addWidget(self._btn_cancel)
        self._hint.adjustSize()
        self._hint.setMinimumHeight(40)
        self._place_hint()
        self._hint.show()
        self._hint.raise_()

        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.OtherFocusReason)
        # 再抬一次操作条，避免被首次 show 盖住
        self._hint.raise_()

    def current_rect(self) -> QRect:
        return QRect(self._rect)

    def _local_rect(self) -> QRect:
        g = self._rect
        return QRect(
            self.mapFromGlobal(g.topLeft()),
            self.mapFromGlobal(g.bottomRight()),
        ).normalized()

    def _move_grip_rect(self) -> QRect:
        """选区中心的移动手柄矩形（本地坐标）。"""
        c = self._local_rect().center()
        g = _MOVE_GRIP
        return QRect(c.x() - g, c.y() - g, g * 2, g * 2)

    def _hit(self, pos: QPoint) -> str | None:
        # 点在操作条上：不启动拖拽（按钮自己处理点击）
        if self._hint.isVisible() and self._hint.geometry().contains(pos):
            return None
        r = self._local_rect()
        hs = _HANDLE
        x, y = pos.x(), pos.y()
        # 中心移动手柄优先
        if self._move_grip_rect().contains(pos):
            return "move"
        near_l = abs(x - r.left()) <= hs
        near_r = abs(x - r.right()) <= hs
        near_t = abs(y - r.top()) <= hs
        near_b = abs(y - r.bottom()) <= hs
        in_x = r.left() - hs <= x <= r.right() + hs
        in_y = r.top() - hs <= y <= r.bottom() + hs
        if near_t and near_l:
            return "nw"
        if near_t and near_r:
            return "ne"
        if near_b and near_l:
            return "sw"
        if near_b and near_r:
            return "se"
        if near_t and in_x:
            return "n"
        if near_b and in_x:
            return "s"
        if near_l and in_y:
            return "w"
        if near_r and in_y:
            return "e"
        # 选区内部（非边线）也可拖动移动
        if r.adjusted(hs, hs, -hs, -hs).contains(pos):
            return "move"
        return None

    def _cursor_for(self, mode: str | None):
        m = {
            "n": Qt.SizeVerCursor, "s": Qt.SizeVerCursor,
            "e": Qt.SizeHorCursor, "w": Qt.SizeHorCursor,
            "nw": Qt.SizeFDiagCursor, "se": Qt.SizeFDiagCursor,
            "ne": Qt.SizeBDiagCursor, "sw": Qt.SizeBDiagCursor,
            "move": Qt.SizeAllCursor,
        }
        return m.get(mode, Qt.ArrowCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self._cancel()
            return
        if e.button() != Qt.LeftButton:
            return
        mode = self._hit(e.pos())
        if not mode:
            return
        self._mode = mode
        self._press = e.globalPos()
        self._orig = QRect(self._rect)

    def mouseMoveEvent(self, e):
        if self._mode is None:
            self.setCursor(self._cursor_for(self._hit(e.pos())))
            return
        delta = e.globalPos() - self._press
        r = QRect(self._orig)
        m = self._mode
        if m == "move":
            r.translate(delta)
        else:
            if "n" in m:
                r.setTop(self._orig.top() + delta.y())
            if "s" in m:
                r.setBottom(self._orig.bottom() + delta.y())
            if "w" in m:
                r.setLeft(self._orig.left() + delta.x())
            if "e" in m:
                r.setRight(self._orig.right() + delta.x())
            r = r.normalized()
            if r.width() < _MIN_SIDE:
                if "w" in m:
                    r.setLeft(r.right() - _MIN_SIDE)
                else:
                    r.setRight(r.left() + _MIN_SIDE)
            if r.height() < _MIN_SIDE:
                if "n" in m:
                    r.setTop(r.bottom() - _MIN_SIDE)
                else:
                    r.setBottom(r.top() + _MIN_SIDE)
        # 限制在虚拟桌面内
        virt = _virtual_geometry()
        if r.width() > virt.width():
            r.setWidth(virt.width())
        if r.height() > virt.height():
            r.setHeight(virt.height())
        if r.left() < virt.left():
            r.moveLeft(virt.left())
        if r.top() < virt.top():
            r.moveTop(virt.top())
        if r.right() > virt.right():
            r.moveRight(virt.right())
        if r.bottom() > virt.bottom():
            r.moveBottom(virt.bottom())
        self._rect = r
        self._place_hint()
        # 整窗刷新，避免脏区叠加导致遮罩越来越深
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._mode = None
            self._press = None
            self._orig = None
            # 松手后保证操作条在最前、可点
            try:
                self._hint.raise_()
            except Exception:
                pass

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._cancel()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._confirm()

    def _place_hint(self):
        """把操作条贴在选区下方（空间不够则上方），坐标为本窗本地。"""
        self._hint.adjustSize()
        hw = max(self._hint.sizeHint().width(), 420)
        hh = max(self._hint.sizeHint().height(), 40)
        self._hint.setFixedSize(hw, hh)

        lr = self._local_rect()
        # 优先选区下方居中
        x = lr.center().x() - hw // 2
        y = lr.bottom() + 12
        if y + hh > self.height() - 8:
            y = lr.top() - hh - 12
        x = max(8, min(x, self.width() - hw - 8))
        y = max(8, min(y, self.height() - hh - 8))
        self._hint.move(x, y)
        self._hint.raise_()

    def _confirm(self):
        r = QRect(self._rect)
        self._close_all()
        self.confirmed.emit(r)

    def _cancel(self):
        self._close_all()
        self.cancelled.emit()

    def _close_all(self):
        self.hide()
        self.close()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        lr = self._local_rect()

        # 关键：Windows 半透明分层窗若用 SourceOver 叠暗色，每拖一次会更黑。
        # 整帧用 Source 绝对写入固定 alpha，选区挖「几乎透明但仍可点」的洞。
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), QColor(0, 0, 0, self._DIM_A))
        # alpha=1：可接收鼠标（alpha=0 会点穿桌面，中心拖不动）
        if lr.isValid() and lr.width() > 0 and lr.height() > 0:
            p.fillRect(lr, QColor(0, 0, 0, 1))
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        if not lr.isValid():
            return

        p.setPen(QPen(QColor("#EF4444"), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(lr)

        # 八向缩放手柄
        p.setBrush(QBrush(QColor("#EF4444")))
        p.setPen(Qt.NoPen)
        hs = 6
        pts = [
            lr.topLeft(), lr.topRight(), lr.bottomLeft(), lr.bottomRight(),
            QPoint(lr.center().x(), lr.top()), QPoint(lr.center().x(), lr.bottom()),
            QPoint(lr.left(), lr.center().y()), QPoint(lr.right(), lr.center().y()),
        ]
        for pt in pts:
            p.drawRect(pt.x() - hs // 2, pt.y() - hs // 2, hs, hs)

        # 中心移动图标（圆底 + 十字四向箭头）
        self._paint_move_grip(p, lr.center())

        txt = f"{self._rect.width()} × {self._rect.height()}"
        f = QFont()
        f.setPixelSize(13)
        p.setFont(f)
        tw = p.fontMetrics().horizontalAdvance(txt) + 12
        ty = lr.top() - 24 if lr.top() > 24 else lr.top() + 8
        tx = max(0, min(lr.left(), self.width() - tw))
        p.fillRect(tx, ty, tw, 20, QColor(0, 0, 0, 180))
        p.setPen(QColor("#ffffff"))
        p.drawText(tx + 6, ty + 15, txt)

    def _paint_move_grip(self, p: QPainter, center: QPoint):
        """选区正中：半透明圆 + 四向箭头，提示可拖拽移动。"""
        g = _MOVE_GRIP
        cx, cy = center.x(), center.y()
        # 圆底
        p.setPen(QPen(QColor("#FFFFFF"), 1.5))
        p.setBrush(QBrush(QColor(15, 23, 42, 210)))
        p.drawEllipse(QPoint(cx, cy), g, g)
        # 四向箭头（十字）
        p.setPen(QPen(QColor("#FFFFFF"), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        arm = g - 6
        head = 5
        # 上下左右线
        p.drawLine(cx, cy - arm, cx, cy + arm)
        p.drawLine(cx - arm, cy, cx + arm, cy)
        # 箭头尖
        p.setBrush(QBrush(QColor("#FFFFFF")))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygon([
            QPoint(cx, cy - arm - 1),
            QPoint(cx - head, cy - arm + head),
            QPoint(cx + head, cy - arm + head),
        ]))
        p.drawPolygon(QPolygon([
            QPoint(cx, cy + arm + 1),
            QPoint(cx - head, cy + arm - head),
            QPoint(cx + head, cy + arm - head),
        ]))
        p.drawPolygon(QPolygon([
            QPoint(cx - arm - 1, cy),
            QPoint(cx - arm + head, cy - head),
            QPoint(cx - arm + head, cy + head),
        ]))
        p.drawPolygon(QPolygon([
            QPoint(cx + arm + 1, cy),
            QPoint(cx + arm - head, cy - head),
            QPoint(cx + arm - head, cy + head),
        ]))

    def closeEvent(self, e):
        super().closeEvent(e)


# ============================================================
# 录制中：选区外框（整窗外扩，只在外侧 margin 画红边；中心透明不进画面）
# Windows 上「半透明 + stylesheet」细条几乎不可见，改用 paintEvent 实心绘制。
# ============================================================
class RecordingOutline(QWidget):
    """置顶镂空框：窗口比录区各大 M 像素，仅在外圈 M 宽画红色，录区内部完全透明。"""

    # 外扩厚度（逻辑像素）。≥2 才醒目；全部在录区外，gdigrab 录不到。
    MARGIN = 3

    def __init__(self, global_rect: QRect):
        super().__init__(None)
        self._m = int(self.MARGIN)
        r = global_rect.normalized()
        # 窗口覆盖录区 + 外侧 margin
        geo = r.adjusted(-self._m, -self._m, self._m, self._m)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        # 避免被当成子控件裁剪；强制顶层
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setGeometry(geo)
        self.show()
        self.raise_()
        # 再顶一次，防止被录制条盖住后仍需可见
        QTimer.singleShot(0, self.raise_)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        # 整窗先清空为全透明（WA_TranslucentBackground 下未画区域透明）
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), QColor(0, 0, 0, 0))
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        m = self._m
        w, h = self.width(), self.height()
        if w <= 2 * m or h <= 2 * m:
            return
        red = QColor("#EF4444")
        # 仅外圈四条实心带（全部在录区之外）
        p.fillRect(0, 0, w, m, red)                 # top
        p.fillRect(0, h - m, w, m, red)             # bottom
        p.fillRect(0, m, m, h - 2 * m, red)         # left
        p.fillRect(w - m, m, m, h - 2 * m, red)     # right
        # 内侧一像素亮边，更容易看见
        p.setPen(QPen(QColor(255, 255, 255, 200), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(m - 1, m - 1, w - 2 * m + 1, h - 2 * m + 1)

    def close_all(self):
        try:
            self.hide()
            self.close()
        except Exception:
            pass


# ============================================================
# 录制中浮层条
# ============================================================
class RecordingBar(QWidget):
    """录制中置顶控制条。主窗隐藏时这是主要可见 UI，需足够醒目。"""

    stop_clicked = pyqtSignal()
    cancel_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # 不用 Qt.Tool：Tool 窗在主窗 hide 后任务栏无入口，用户会以为程序没了
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Window
        )
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedHeight(48)
        self.setStyleSheet(
            "RecordingBar, QWidget#RecordingBarRoot{"
            "  background: rgba(15, 23, 42, 245);"
            "  border: 1px solid rgba(248,113,113,0.55);"
            "  border-radius: 10px;"
            "}"
        )
        self.setObjectName("RecordingBarRoot")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 14, 6)
        lay.setSpacing(10)
        self.lbl_dot = QLabel("● 录制中")
        self.lbl_dot.setStyleSheet(
            "color:#EF4444; font-size:13px; font-weight:700; background:transparent;"
        )
        lay.addWidget(self.lbl_dot)
        self.lbl_time = QLabel("00s / 60s")
        self.lbl_time.setStyleSheet(
            "color:#F8FAFC; font-size:13px; font-weight:600; background:transparent;"
        )
        lay.addWidget(self.lbl_time)
        self.btn_stop = QPushButton("停止并保存  Alt+3")
        self.btn_stop.setCursor(Qt.PointingHandCursor)
        self.btn_stop.setFocusPolicy(Qt.NoFocus)
        self.btn_stop.setStyleSheet(
            "QPushButton{background:#EF4444;color:#fff;border:none;border-radius:6px;"
            "padding:6px 12px;font-weight:600;font-size:12px;}"
            "QPushButton:hover{background:#DC2626;}"
        )
        self.btn_stop.clicked.connect(self.stop_clicked.emit)
        lay.addWidget(self.btn_stop)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.setFocusPolicy(Qt.NoFocus)
        self.btn_cancel.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.12);color:#E2E8F0;"
            "border:1px solid rgba(255,255,255,0.2);border-radius:6px;padding:6px 10px;font-size:12px;}"
            "QPushButton:hover{background:rgba(255,255,255,0.2);}"
        )
        self.btn_cancel.clicked.connect(self.cancel_clicked.emit)
        lay.addWidget(self.btn_cancel)
        self._blink = False
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(500)
        self._blink_timer.timeout.connect(self._toggle_dot)

    def show_near_rect(self, global_rect: QRect):
        self.adjustSize()
        w = max(self.sizeHint().width(), 360)
        self.setFixedWidth(w)
        x = global_rect.center().x() - w // 2
        y = global_rect.bottom() + 16
        virt = _virtual_geometry()
        if y + self.height() > virt.bottom() - 8:
            y = global_rect.top() - self.height() - 16
        x = max(virt.left() + 8, min(x, virt.right() - w - 8))
        y = max(virt.top() + 8, y)
        self.move(x, y)
        self.show()
        self.raise_()
        self._blink_timer.start()

    def set_elapsed(self, sec: float):
        s = max(0, min(int(sec), MAX_SECONDS))
        self.lbl_time.setText(f"{s:02d}s / {MAX_SECONDS:02d}s")

    def _toggle_dot(self):
        self._blink = not self._blink
        self.lbl_dot.setStyleSheet(
            f"color:{'#EF4444' if self._blink else '#7F1D1D'};"
            " font-size:13px; font-weight:700; background:transparent;"
        )

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(15, 23, 42, 230))
        p.setPen(QPen(QColor(239, 68, 68, 180), 1))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 10, 10)

    def closeEvent(self, e):
        self._blink_timer.stop()
        super().closeEvent(e)


class _HotkeyBridge(QObject):
    start_sig = pyqtSignal()
    stop_sig = pyqtSignal()


# 列表缩略图：高度跟行高走，宽度按视频真实比例（16:9 / 1:1 / 9:16…）自适应
# 不裁切画面，整帧等比装进「行高 × 最大宽度」盒子里
_THUMB_H = 52          # 贴合单条记录内容高度
_THUMB_MAX_W = 140     # 超宽画幅上限，避免挤掉文件名
_THUMB_MIN_W = 30      # 极竖屏仍保证可点
_ROW_H = 64


class _ThumbQueueWorker(QThread):
    """批量从视频抽首帧到 .thumbs/，逐个 ready。"""
    ready = pyqtSignal(str, str)  # video_path, thumb_path
    failed = pyqtSignal(str)

    def __init__(self, jobs: list, parent=None):
        super().__init__(parent)
        # jobs: [(video_path, thumb_path), ...]
        self._jobs = list(jobs or [])
        self._stop = False

    def cancel(self):
        self._stop = True

    def run(self):
        ff = find_ffmpeg()
        flags = 0x08000000 if sys.platform.startswith("win") else 0
        for video_path, thumb_path in self._jobs:
            if self._stop:
                break
            if not video_path or not thumb_path:
                continue
            if os.path.isfile(thumb_path) and os.path.getsize(thumb_path) > 32:
                self.ready.emit(video_path, thumb_path)
                continue
            if not ff:
                self.failed.emit(video_path)
                continue
            try:
                os.makedirs(os.path.dirname(thumb_path) or ".", exist_ok=True)
                # 不强制缩放比例：保留源帧宽高，显示时再按行高等比适配
                cmd = [
                    ff, "-y", "-ss", "0.15", "-i", video_path,
                    "-vframes", "1", "-q:v", "3", thumb_path,
                ]
                subprocess.run(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=flags, timeout=25,
                )
                if os.path.isfile(thumb_path) and os.path.getsize(thumb_path) > 32:
                    self.ready.emit(video_path, thumb_path)
                else:
                    self.failed.emit(video_path)
            except Exception:
                self.failed.emit(video_path)


class _MetaQueueWorker(QThread):
    """批量读取视频分辨率 / 时长。"""
    ready = pyqtSignal(str, int, int, float)  # path, w, h, duration

    def __init__(self, paths: list, parent=None):
        super().__init__(parent)
        self._paths = list(paths or [])
        self._stop = False

    def cancel(self):
        self._stop = True

    def run(self):
        for path in self._paths:
            if self._stop:
                break
            if not path:
                continue
            try:
                w, h, dur = probe_video_info(path)
            except Exception:
                w, h, dur = 0, 0, 0.0
            self.ready.emit(path, int(w or 0), int(h or 0), float(dur or 0.0))


def _fit_thumb_to_row(
    src: QPixmap,
    max_h: int = _THUMB_H,
    max_w: int = _THUMB_MAX_W,
    min_w: int = _THUMB_MIN_W,
) -> QPixmap:
    """按行高等比缩放，完整保留源画面比例（不裁切）。

    - 竖屏 9:16 → 高度吃满行高，宽度变窄
    - 方屏 1:1  → 近似正方形
    - 横屏 16:9 → 高度吃满，宽度变宽（不超过 max_w）
    """
    if src is None or src.isNull() or max_h < 1 or max_w < 1:
        return QPixmap()
    # 先装进 max_w×max_h 盒子，KeepAspectRatio = 不变形、不裁切
    scaled = src.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    if scaled.isNull():
        return QPixmap()
    # 极竖屏过窄时略放大到 min_w（仍保持比例，高度可能略小于 max_h）
    if scaled.width() < min_w and scaled.width() > 0:
        scaled = src.scaled(min_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    w, h = scaled.width(), scaled.height()
    if w < 1 or h < 1:
        return QPixmap()

    out = QPixmap(w, h)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    radius = 5.0
    body = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
    clip = QPainterPath()
    clip.addRoundedRect(body, radius, radius)
    p.setClipPath(clip)
    p.drawPixmap(0, 0, scaled)
    p.setClipping(False)
    p.setPen(QPen(QColor(0, 0, 0, 70), 1.0))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(body, radius, radius)

    # 播放角标：窄图缩到中心，宽图放右下
    if w >= 36 and h >= 28:
        bw, bh = 14, 12
        if w >= 56:
            bx, by = w - bw - 4, h - bh - 3
        else:
            bx, by = (w - bw) // 2, (h - bh) // 2
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(0, 0, 0, 120)))
        p.drawRoundedRect(QRectF(bx - 2, by - 1, bw + 4, bh + 2), 3.0, 3.0)
        play = QPainterPath()
        cx, cy = bx + bw * 0.5 - 1, by + bh * 0.5
        play.moveTo(cx - 3, cy - 4)
        play.lineTo(cx - 3, cy + 4)
        play.lineTo(cx + 4, cy)
        play.closeSubpath()
        p.setBrush(QBrush(QColor("#FFFFFF")))
        p.drawPath(play)
    p.end()
    return out


def _placeholder_thumb_pixmap(h: int = _THUMB_H) -> QPixmap:
    """无缩略图时的方形占位（高度=行高，宽高比 1:1）。"""
    h = max(24, int(h))
    w = h
    out = QPixmap(w, h)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)
    bg = QColor("#334155")
    try:
        from styles.style_all import theme as _th
        if not getattr(_th, "is_dark", True):
            bg = QColor("#CBD5E1")
    except Exception:
        pass
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), 5.0, 5.0)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(bg))
    p.drawPath(path)
    play = QPainterPath()
    cx, cy = w * 0.5 - 1, h * 0.5
    play.moveTo(cx - 5, cy - 7)
    play.lineTo(cx - 5, cy + 7)
    play.lineTo(cx + 8, cy)
    play.closeSubpath()
    p.setBrush(QBrush(QColor(255, 255, 255, 200)))
    p.drawPath(play)
    p.end()
    return out


class _RecordRow(QWidget):
    """单条录像：缩略图 + 文件名 + 最右侧删除。

    - 点缩略图/文字区 → 播放（不必先「选中」）
    - 点右侧「删除」→ 仅发删除请求，不播放
    """

    play_requested = pyqtSignal()
    delete_requested = pyqtSignal()

    def __init__(self, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 5, 8, 5)
        lay.setSpacing(10)

        self.thumb = QLabel()
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet("background:transparent; border:none;")
        self.set_placeholder()
        lay.addWidget(self.thumb, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self.lbl_title = QLabel(title or "")
        self.lbl_title.setStyleSheet(TEXT_STYLE_T)
        self.lbl_title.setWordWrap(False)
        col.addWidget(self.lbl_title)
        self.lbl_sub = QLabel(subtitle or "")
        self.lbl_sub.setProperty("typo", "muted")
        self.lbl_sub.setStyleSheet(TEXT_STYLE_T)
        self.lbl_sub.setWordWrap(False)
        col.addWidget(self.lbl_sub)
        col.addStretch(1)
        lay.addLayout(col, 1)

        self.btn_del = QPushButton("删除")
        self.btn_del.setCursor(Qt.PointingHandCursor)
        self.btn_del.setFocusPolicy(Qt.NoFocus)
        self.btn_del.setFixedHeight(28)
        self.btn_del.setStyleSheet(
            "QPushButton{"
            "  background: transparent; color: #F87171;"
            "  border: 1px solid rgba(248,113,113,0.45);"
            "  border-radius: 6px; padding: 2px 12px; font-size: 12px;"
            "}"
            "QPushButton:hover{"
            "  background: rgba(239,68,68,0.18); color: #FCA5A5;"
            "  border-color: rgba(248,113,113,0.75);"
            "}"
            "QPushButton:pressed{"
            "  background: rgba(239,68,68,0.30);"
            "}"
        )
        self.btn_del.clicked.connect(self._on_delete_clicked)
        lay.addWidget(self.btn_del, 0, Qt.AlignVCenter)

    def _on_delete_clicked(self):
        # 阻止再冒泡成「播放」
        self.delete_requested.emit()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            # 点在删除按钮或其子区域上 → 不播放
            w = self.childAt(e.pos())
            if w is not None and (w is self.btn_del or self.btn_del.isAncestorOf(w)):
                super().mouseReleaseEvent(e)
                return
            self.play_requested.emit()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def set_placeholder(self):
        pm = _placeholder_thumb_pixmap(_THUMB_H)
        self.thumb.setFixedSize(pm.size())
        self.thumb.setPixmap(pm)

    def set_thumb_pixmap(self, src: QPixmap):
        fitted = _fit_thumb_to_row(src, max_h=_THUMB_H, max_w=_THUMB_MAX_W)
        if fitted.isNull():
            self.set_placeholder()
            return
        self.thumb.setFixedSize(fitted.size())
        self.thumb.setPixmap(fitted)

    def set_thumb_file(self, path: str):
        if not path or not os.path.isfile(path):
            self.set_placeholder()
            return
        pm = QPixmap(path)
        if pm.isNull():
            self.set_placeholder()
            return
        self.set_thumb_pixmap(pm)

    def set_subtitle(self, text: str):
        self.lbl_sub.setText(text or "")

    def refresh_theme(self):
        self.lbl_title.setStyleSheet(TEXT_STYLE_T)
        self.lbl_sub.setStyleSheet(TEXT_STYLE_T)


# ============================================================
# 页面
# ============================================================
class PageRegionRecord(QWidget):
    # 侧栏录制状态圆标：True=录制中（圆心红点）/ False=空闲（红点消失）
    recording_changed = pyqtSignal(bool)

    def hasHeightForWidth(self):
        return False

    def sizeHint(self):
        s = super().sizeHint()
        return QSize(s.width(), min(s.height(), 700))

    def __init__(self):
        super().__init__()
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._main_win = None
        self._win_hidden = False
        self._goto_page_cb = None
        self._save_path = ""
        self._default_region: QRect | None = None
        self._overlay = None          # select overlay
        self._adjust = None           # RegionAdjustOverlay
        self._bar = None
        self._outline = None          # RecordingOutline
        self._recorder = RegionRecorder()
        self._hotkey_bridge = _HotkeyBridge()
        self._hotkey_bridge.start_sig.connect(self._on_hotkey_start)
        self._hotkey_bridge.stop_sig.connect(self._on_hotkey_stop)
        self._hk_start_h = None
        self._hk_stop_h = None
        self._tick = QTimer(self)
        self._tick.setInterval(200)
        self._tick.timeout.connect(self._on_tick)
        self._selecting = False
        self._adjusting = False
        self._prefs_dirty_cb = None
        self._thumb_worker = None
        self._thumb_cache = {}  # video -> thumb path
        self._meta_worker = None
        self._meta_cache = {}  # video -> (mtime, w, h, duration)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # ── 第一排 ──
        row1 = QHBoxLayout()
        root.addLayout(row1)

        card_rec = make_card("CardRegionRec")
        vb = QVBoxLayout(card_rec)
        vb.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        install_card_title(card_rec, vb, "区域录屏")

        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("开始录制")
        apply_medium_button(self.btn_start)
        self.btn_start.setCursor(Qt.PointingHandCursor)
        self.btn_start.clicked.connect(self._on_primary_click)
        btn_row.addWidget(self.btn_start)

        self.btn_clear_region = QPushButton("清除录制区域")
        apply_medium_button(self.btn_clear_region)
        self.btn_clear_region.setCursor(Qt.PointingHandCursor)
        self.btn_clear_region.clicked.connect(self._clear_default_region)
        btn_row.addWidget(self.btn_clear_region)
        btn_row.addStretch()
        vb.addLayout(btn_row)

        self.lbl_region = QLabel("默认区域：未设定")
        self.lbl_region.setStyleSheet(TEXT_STYLE_T)
        self.lbl_region.setWordWrap(True)
        vb.addWidget(self.lbl_region)

        self.lbl_status = QLabel("状态：空闲")
        self.lbl_status.setStyleSheet(TEXT_STYLE_T)
        self.lbl_status.setWordWrap(True)
        vb.addWidget(self.lbl_status)

        self.lbl_keys = QLabel(
            f"Alt+2：无默认区域 → 定位选区并调整；有默认区域 → 进入调整\n"
            f"调整中再次 Alt+2（或点「开始录制」）正式开录 · Alt+3 停止 · 最长 {MAX_SECONDS} 秒 · Esc/取消 放弃"
        )
        self.lbl_keys.setProperty("typo", "muted")
        self.lbl_keys.setWordWrap(True)
        self.lbl_keys.setStyleSheet(TEXT_STYLE_T)
        vb.addWidget(self.lbl_keys)

        self.lbl_ffmpeg = QLabel("")
        self.lbl_ffmpeg.setWordWrap(True)
        self.lbl_ffmpeg.setStyleSheet(TEXT_STYLE_T)
        vb.addWidget(self.lbl_ffmpeg)
        vb.addStretch()
        row1.addWidget(card_rec, 3)

        card_opt = make_card("CardRegionOpt")
        vo = QVBoxLayout(card_opt)
        vo.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        install_card_title(card_opt, vo, "选项")

        self.chk_cursor = QCheckBox("显示鼠标光标")
        self.chk_cursor.setChecked(True)
        self.chk_cursor.setStyleSheet(TEXT_STYLE_T)
        vo.addWidget(self.chk_cursor)

        # 默认不藏主界面：hide() 会连任务栏一起没了，用户会以为闪退
        self.chk_hide = QCheckBox("开录时最小化主窗口（可选，避免助手界面被录进画面）")
        self.chk_hide.setChecked(False)
        self.chk_hide.setStyleSheet(TEXT_STYLE_T)
        vo.addWidget(self.chk_hide)

        self.lbl_path = QLabel("保存至：（请在「关于」中设置公共保存目录）")
        self.lbl_path.setWordWrap(True)
        self.lbl_path.setStyleSheet(TEXT_STYLE_T)
        self.lbl_path.setProperty("typo", "muted")
        vo.addWidget(self.lbl_path)

        path_row = QHBoxLayout()
        self.btn_open_dir = QPushButton("打开文件夹")
        apply_medium_button(self.btn_open_dir)
        self.btn_open_dir.setCursor(Qt.PointingHandCursor)
        self.btn_open_dir.clicked.connect(self._open_save_dir)
        path_row.addWidget(self.btn_open_dir)
        path_row.addStretch()
        vo.addLayout(path_row)
        vo.addStretch()
        row1.addWidget(card_opt, 2)

        # ── 第二排：操作记录（本机目录扫描；行内删除，点内容播放）──
        card_list = make_card("CardRegionList")
        vl = QVBoxLayout(card_list)
        vl.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        vl.setSpacing(4)
        install_card_title(card_list, vl, "操作记录")

        self.lbl_list_hint = QLabel(
            "点缩略图/文件名播放 · 右侧删除需确认 · 列表仅扫描本机保存目录（换电脑互不影响）"
        )
        self.lbl_list_hint.setProperty("typo", "muted")
        self.lbl_list_hint.setWordWrap(True)
        self.lbl_list_hint.setStyleSheet(TEXT_STYLE_T)
        vl.addWidget(self.lbl_list_hint)

        self.list_files = QListWidget()
        self.list_files.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_files.setUniformItemSizes(True)
        self.list_files.setSpacing(4)
        self.list_files.setMouseTracking(True)
        apply_simple_record(self.list_files)
        vl.addWidget(self.list_files, 1)

        act = QHBoxLayout()
        self.btn_refresh = QPushButton("刷新")
        self.btn_reveal = QPushButton("打开文件夹")
        for b in (self.btn_refresh, self.btn_reveal):
            apply_medium_button(b)
            b.setCursor(Qt.PointingHandCursor)
            act.addWidget(b)
        act.addStretch()
        self.btn_refresh.clicked.connect(self.refresh_list)
        self.btn_reveal.clicked.connect(self._open_save_dir)
        vl.addLayout(act)
        root.addWidget(card_list, 1)

        self._cards = (card_rec, card_opt, card_list)
        self._update_ffmpeg_label()
        self._refresh_region_label()
        QTimer.singleShot(300, self._bind_hotkeys)

    # ── 主窗口 / 路径 ─────────────────────────────────────────
    def set_main_window(self, win):
        self._main_win = win

    def set_goto_page_callback(self, cb):
        self._goto_page_cb = cb

    def set_prefs_dirty_callback(self, cb):
        """主窗口注入：默认区域变更时触发落盘。"""
        self._prefs_dirty_cb = cb

    def apply_save_path(self, path: str):
        self._save_path = (path or "").strip()
        self._refresh_path_label()
        self.refresh_list()

    def _resolve_save_dir(self) -> str:
        if self._save_path:
            return self._save_path
        try:
            from utils.user_prefs import load_user_prefs
            prefs, _ = load_user_prefs()
            base = (prefs.get("save_path_base") or "").strip()
            if not base:
                base = os.path.expanduser("~/Downloads")
            return os.path.join(base, REGION_RECORD_SUBDIR)
        except Exception:
            log.debug("读取区域录屏保存目录偏好失败，已回退默认路径", exc_info=True)
            return os.path.join(os.path.expanduser("~/Downloads"), REGION_RECORD_SUBDIR)

    def _refresh_path_label(self):
        d = self._resolve_save_dir()
        self.lbl_path.setText(f"保存至：{d.replace(chr(92), '/')}")

    def _refresh_region_label(self):
        r = self._default_region
        if r is None:
            self.lbl_region.setText("录制区域：未设定（Alt+2 / 开始选区 → 定位选区）")
            self.btn_start.setText("开始选区")
            self.btn_clear_region.setEnabled(False)
        else:
            self.lbl_region.setText(
                f"录制区域：{r.width()}×{r.height()} @ ({r.x()}, {r.y()})"
            )
            # 有默认区域时先进入调整，不是立刻开录
            self.btn_start.setText("调整区域  Alt+2")
            self.btn_clear_region.setEnabled(True)

    def _update_ffmpeg_label(self):
        if find_ffmpeg():
            self.lbl_ffmpeg.setText("依赖：ffmpeg 已就绪")
            self.lbl_ffmpeg.setStyleSheet(TEXT_STYLE_T + f" color:{tk('ok')};")
        else:
            self.lbl_ffmpeg.setText(
                "依赖：未找到 ffmpeg（winget install Gyan.FFmpeg 后重启）"
            )
            self.lbl_ffmpeg.setStyleSheet(TEXT_STYLE_T + f" color:{tk('err')};")

    def _notify_prefs(self):
        cb = self._prefs_dirty_cb
        if callable(cb):
            try:
                cb()
            except Exception:
                log.exception("区域录屏偏好变更回调失败")

    # ── 热键 ─────────────────────────────────────────────────
    def _bind_hotkeys(self):
        self._unbind_hotkeys()
        try:
            import keyboard
            self._hk_start_h = keyboard.add_hotkey(
                HOTKEY_START, lambda: self._hotkey_bridge.start_sig.emit()
            )
            self._hk_stop_h = keyboard.add_hotkey(
                HOTKEY_STOP, lambda: self._hotkey_bridge.stop_sig.emit()
            )
            log.info("区域录屏热键已绑定 Alt+2 / Alt+3")
        except Exception:
            log.exception("绑定区域录屏热键失败")
            self.lbl_status.setText("状态：热键绑定失败（可点按钮操作）")

    def hotkey_inventory(self):
        ok = getattr(self, "_hk_start_h", None) is not None
        st = "ok" if ok else "busy"
        return [
            {"group": "区域录屏", "name": "选区 / 开始录制", "key": "Alt+2", "state": st},
            {"group": "区域录屏", "name": "停止并保存", "key": "Alt+3", "state": st},
        ]

    def rebind_hotkeys(self):
        self._bind_hotkeys()
        ok = getattr(self, "_hk_start_h", None) is not None
        return [
            ("选区 / 开始录制", "Alt+2", ok),
            ("停止并保存", "Alt+3", ok),
        ]

    def _unbind_hotkeys(self):
        try:
            import keyboard
            for h in (self._hk_start_h, self._hk_stop_h):
                if h is not None:
                    try:
                        keyboard.remove_hotkey(h)
                    except Exception:
                        log.debug("移除区域录屏热键失败", exc_info=True)
            self._hk_start_h = self._hk_stop_h = None
        except Exception:
            log.exception("解绑区域录屏热键失败")

    def _on_hotkey_start(self):
        if self._recorder.is_recording:
            return
        # 调整阶段：Alt+2 = 确认并录制
        if self._adjusting and self._adjust is not None:
            try:
                self._adjust._confirm()
            except Exception:
                log.exception("调整态确认失败")
            return
        if self._selecting:
            return
        cb = self._goto_page_cb
        if callable(cb):
            try:
                cb()
            except Exception:
                log.exception("热键跳转区域录屏页失败")
        self._on_primary_click()

    def _on_hotkey_stop(self):
        if self._recorder.is_recording:
            self._stop_record(discard=False)

    def _on_primary_click(self):
        """主按钮 / Alt+2：始终先进入选区或调整，确认后才开录。

        - 无默认区域：拖拽框选 → 自动进入调整态 → 再 Alt+2 开录
        - 有默认区域：直接进入调整态（可拖边角/移动）→ 再 Alt+2 开录
        不再「有默认区域就立刻开录」，避免跳过编辑一步。
        """
        if self._recorder.is_recording:
            self._set_status("正在录制中，请先 Alt+3 停止")
            return
        if self._adjusting or self._selecting:
            return
        if self._default_region is not None:
            self._enter_adjust_from_default()
        else:
            self.begin_select()

    # ── 默认区域 ─────────────────────────────────────────────
    def _set_default_region(self, rect: QRect, persist: bool = True):
        self._default_region = QRect(rect.normalized())
        self._refresh_region_label()
        if persist:
            self._notify_prefs()

    def _clear_default_region(self):
        if self._recorder.is_recording or self._selecting or self._adjusting:
            self._set_status("请先结束当前操作")
            return
        self._default_region = None
        self._refresh_region_label()
        self._notify_prefs()
        self._set_status("已清除录制区域")

    # ── 选区 / 调整 ───────────────────────────────────────────
    def begin_select(self):
        if self._recorder.is_recording:
            self._set_status("正在录制中，请先 Alt+3 停止")
            return
        if self._selecting or self._adjusting:
            return
        if not find_ffmpeg():
            message_box_info(self, "需要 ffmpeg", ffmpeg_missing_message())
            self._update_ffmpeg_label()
            return

        self._selecting = True
        self._set_status("请拖拽框选录制区域（Esc 取消）…")
        # 选区阶段不隐藏主窗：全屏遮罩已盖住桌面；hide 会导致任务栏图标消失，像闪退
        self._show_select_overlay()

    def _show_select_overlay(self):
        try:
            self._close_select_overlay()
            self._overlay = RegionSelectOverlay(
                self._on_select_done, self._on_select_cancel
            )
        except Exception:
            log.exception("打开选区遮罩失败")
            self._selecting = False
            self._set_status("打开选区失败")

    def _close_select_overlay(self):
        if self._overlay is not None:
            try:
                self._overlay.close()
            except Exception:
                pass
            self._overlay = None

    def _on_select_cancel(self):
        self._selecting = False
        self._close_select_overlay()
        self._set_status("已取消选区")

    def _on_select_done(self, global_rect: QRect):
        self._selecting = False
        self._close_select_overlay()
        # 进入调整态
        QTimer.singleShot(60, lambda: self._enter_adjust(global_rect))

    def _enter_adjust(self, global_rect: QRect):
        try:
            self._close_adjust()
            self._adjusting = True
            self._set_status("可调整选区：拖边角缩放 / 拖内部移动 · Alt+2 开录 · 取消放弃")
            self._adjust = RegionAdjustOverlay(global_rect)
            self._adjust.confirmed.connect(self._on_adjust_confirm)
            self._adjust.cancelled.connect(self._on_adjust_cancel)
        except Exception:
            log.exception("进入调整态失败")
            self._adjusting = False
            self._set_status("调整选区失败")

    def _close_adjust(self):
        if self._adjust is not None:
            try:
                self._adjust.close()
            except Exception:
                pass
            self._adjust = None
        self._adjusting = False

    def _on_adjust_cancel(self):
        self._close_adjust()
        self._set_status("已取消，未更新录制区域")

    def _on_adjust_confirm(self, rect: QRect):
        self._close_adjust()
        self._set_default_region(rect, persist=True)
        # 调整确认后才真正开录（第二次 Alt+2 / 点「开始录制」）
        QTimer.singleShot(80, lambda: self._start_record(rect))

    def _enter_adjust_from_default(self):
        """有默认区域时：进入调整态，不立刻开录；不隐藏主窗。"""
        r = self._default_region
        if r is None:
            self.begin_select()
            return
        if self._recorder.is_recording or self._selecting or self._adjusting:
            return
        if not find_ffmpeg():
            message_box_info(self, "需要 ffmpeg", ffmpeg_missing_message())
            self._update_ffmpeg_label()
            return
        self._enter_adjust(QRect(r))

    # ── 录制 ─────────────────────────────────────────────────
    def _start_record(self, global_rect: QRect):
        """开录入口：若需隐藏主窗，先藏再延迟启动，避免首帧录进助手界面。"""
        global_rect = global_rect.normalized()
        x, y, w, h = logical_rect_to_gdigrab(global_rect)
        if w < 32 or h < 32:
            self._set_status("选区太小，请重新设定默认区域")
            return

        folder = self._resolve_save_dir()
        try:
            ensure_dir(folder)
        except Exception as e:
            self._set_status(f"无法创建目录：{e}")
            return

        # 仅当用户勾选「开录时最小化」才收起主窗；默认保持界面可见
        if self.chk_hide.isChecked() and not self._win_hidden:
            self._hide_main()
            QTimer.singleShot(
                200,
                lambda r=QRect(global_rect): self._start_record_core(r),
            )
            return
        self._start_record_core(global_rect)

    def _start_record_core(self, global_rect: QRect):
        global_rect = global_rect.normalized()
        x, y, w, h = logical_rect_to_gdigrab(global_rect)
        if w < 32 or h < 32:
            self._restore_main()
            self._set_status("选区太小，请重新设定默认区域")
            return

        folder = self._resolve_save_dir()
        try:
            ensure_dir(folder)
        except Exception as e:
            self._restore_main()
            self._set_status(f"无法创建目录：{e}")
            return

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = os.path.join(folder, f"region_{stamp}.mp4")
        i = 1
        while os.path.exists(out):
            out = os.path.join(folder, f"region_{stamp}_{i:03d}.mp4")
            i += 1

        ok, err = self._recorder.start(
            out, x, y, w, h,
            draw_mouse=self.chk_cursor.isChecked(),
            max_seconds=MAX_SECONDS,
        )
        if not ok:
            self._restore_main()
            self._set_status(err.split("\n")[0] if err else "启动录制失败")
            if err and "ffmpeg" in err.lower():
                message_box_warn(self, "录制失败", err)
            return

        self._set_status(f"录制中… {w}×{h} → {os.path.basename(out)}")
        self.btn_start.setEnabled(False)
        self.btn_clear_region.setEnabled(False)
        try:
            self.recording_changed.emit(True)
        except Exception:
            log.exception("录制状态信号(True)发送失败")

        # 外侧选区框（不进画面）+ 控制条（主窗隐藏时这是唯一可见操作面）
        try:
            self._close_outline()
            self._outline = RecordingOutline(global_rect)
        except Exception:
            log.exception("显示录制外框失败")
            self._outline = None

        try:
            if self._bar is not None:
                self._bar.close()
            self._bar = RecordingBar()
            self._bar.stop_clicked.connect(lambda: self._stop_record(discard=False))
            self._bar.cancel_clicked.connect(lambda: self._stop_record(discard=True))
            self._bar.set_elapsed(0)
            self._bar.show_near_rect(global_rect)
        except Exception:
            log.exception("显示录制浮层失败")
            # 浮层失败时强制把主窗找回来，避免用户感觉程序消失
            self._restore_main()
            self._set_status("录制已开始，但控制条显示失败；可用 Alt+3 停止")

        if self._outline is not None:
            try:
                self._outline.show()
                self._outline.raise_()
            except Exception:
                pass
        if self._bar is not None:
            try:
                self._bar.raise_()
            except Exception:
                pass

        self._tick.start()

    def _close_outline(self):
        if self._outline is not None:
            try:
                self._outline.close_all()
            except Exception:
                pass
            self._outline = None

    def _on_tick(self):
        if not self._recorder.is_recording:
            self._tick.stop()
            self._finalize_after_auto_stop()
            return
        el = self._recorder.elapsed_seconds()
        if self._bar:
            self._bar.set_elapsed(el)
        self._set_status(f"录制中… {int(el)}s / {MAX_SECONDS}s")
        if el >= MAX_SECONDS - 0.05:
            self._stop_record(discard=False)

    def _finalize_after_auto_stop(self):
        result = self._recorder.stop(discard=False)
        self._after_stop(result, discarded=False)

    def _stop_record(self, discard: bool = False):
        self._tick.stop()
        result = self._recorder.stop(discard=discard)
        self._after_stop(result, discarded=discard)

    def _after_stop(self, result, discarded: bool):
        self._close_outline()
        if self._bar is not None:
            try:
                self._bar.close()
            except Exception:
                pass
            self._bar = None

        self._restore_main()
        self.btn_start.setEnabled(True)
        self._update_ffmpeg_label()
        self._refresh_region_label()
        try:
            self.recording_changed.emit(False)
        except Exception:
            log.exception("录制状态信号(False)发送失败")

        if discarded:
            self._set_status("已取消，未保存")
            return

        if result.ok and result.path:
            self._set_status(
                f"已保存（{result.elapsed:.1f}s）：{os.path.basename(result.path)}"
            )
            self.refresh_list()
            for i in range(self.list_files.count()):
                it = self.list_files.item(i)
                if it and it.data(Qt.UserRole) == result.path:
                    self.list_files.setCurrentItem(it)
                    break
        else:
            self._set_status(result.error or "保存失败")

    # ── 主窗显隐（开录可选：最小化，禁止 hide 以免像闪退）────
    def _hide_main(self):
        """开录时收起主窗：用最小化，保留任务栏图标，停止后可还原。"""
        win = self._main_win
        if win is None:
            p = self.window()
            if p and p is not self:
                win = p
        if win is None:
            return
        # 已最小化也记一笔，停录时仍会 restore
        if win.isVisible() or win.isMinimized():
            self._win_hidden = True
            try:
                win.showMinimized()
            except Exception:
                # 兜底才 hide（极少走到）
                try:
                    win.hide()
                except Exception:
                    pass

    def _restore_main(self):
        if not self._win_hidden:
            return
        self._win_hidden = False
        win = self._main_win
        if win is None:
            p = self.window()
            if p and p is not self:
                win = p
        if win is not None:
            try:
                win.showNormal()
            except Exception:
                win.show()
            win.raise_()
            win.activateWindow()

    def _set_status(self, text: str):
        self.lbl_status.setText(f"状态：{text}")

    # ── 列表（缩略图 + 单击播放）──────────────────────────────
    def _thumb_path_for(self, video_path: str) -> str:
        folder = self._resolve_save_dir()
        thumb_dir = os.path.join(folder, ".thumbs")
        base = os.path.splitext(os.path.basename(video_path))[0] + ".jpg"
        return os.path.join(thumb_dir, base)

    def _find_item_by_path(self, video_path: str):
        if not video_path:
            return None
        want = os.path.normpath(video_path)
        for i in range(self.list_files.count()):
            it = self.list_files.item(i)
            if not it:
                continue
            p = it.data(Qt.UserRole) or ""
            if p and os.path.normpath(p) == want:
                return it
        return None

    def _row_widget(self, item) -> _RecordRow | None:
        if item is None:
            return None
        w = self.list_files.itemWidget(item)
        return w if isinstance(w, _RecordRow) else None

    def _apply_thumb_to_item(self, item, thumb_path: str):
        row = self._row_widget(item)
        if row is not None:
            row.set_thumb_file(thumb_path)

    def _stop_thumb_worker(self):
        w = self._thumb_worker
        if w is None:
            return
        try:
            if hasattr(w, "cancel"):
                w.cancel()
        except Exception:
            pass
        try:
            w.ready.disconnect()
        except Exception:
            pass
        try:
            w.failed.disconnect()
        except Exception:
            pass
        if w.isRunning():
            try:
                w.wait(400)
            except Exception:
                pass
        self._thumb_worker = None

    def _stop_meta_worker(self):
        w = self._meta_worker
        if w is None:
            return
        try:
            if hasattr(w, "cancel"):
                w.cancel()
        except Exception:
            pass
        try:
            w.ready.disconnect()
        except Exception:
            pass
        if w.isRunning():
            try:
                w.wait(400)
            except Exception:
                pass
        self._meta_worker = None

    @staticmethod
    def _format_row_meta(w: int, h: int, duration: float, size: int) -> str:
        """副标题：分辨率 · 时长 · 体积（不显示录制时间）。"""
        parts = []
        if w > 0 and h > 0:
            parts.append(f"{w}×{h}")
        else:
            parts.append("分辨率 —")
        parts.append(format_duration(duration))
        if size > 0:
            parts.append(_fmt_size(size))
        return "  ·  ".join(parts)

    def _apply_meta_to_item(self, item, w: int, h: int, duration: float):
        row = self._row_widget(item)
        if row is None:
            return
        size = int(item.data(Qt.UserRole + 1) or 0)
        row.set_subtitle(self._format_row_meta(w, h, duration, size))
        # 缓存宽高供 tooltip
        item.setData(Qt.UserRole + 3, int(w or 0))
        item.setData(Qt.UserRole + 4, int(h or 0))
        item.setData(Qt.UserRole + 5, float(duration or 0.0))
        path = item.data(Qt.UserRole) or ""
        if path and w > 0 and h > 0:
            tip = f"{path}\n{w}×{h}  ·  {format_duration(duration)}\n点内容播放 · 右侧删除"

    def _start_thumb_queue(self, jobs: list):
        """jobs: [(video_path, thumb_path), ...] 异步抽帧并回填列表缩略图。"""
        self._stop_thumb_worker()
        if not jobs:
            return
        worker = _ThumbQueueWorker(jobs, self)
        self._thumb_worker = worker

        def _ok(vp, tp):
            self._thumb_cache[vp] = tp
            it = self._find_item_by_path(vp)
            if it is not None:
                self._apply_thumb_to_item(it, tp)

        def _fail(vp):
            # 保留占位图即可
            pass

        worker.ready.connect(_ok)
        worker.failed.connect(_fail)
        worker.start()

    def _start_meta_queue(self, paths: list):
        """异步读取分辨率/时长并回填副标题。"""
        self._stop_meta_worker()
        if not paths:
            return
        worker = _MetaQueueWorker(paths, self)
        self._meta_worker = worker

        def _ok(vp, w, h, dur):
            it = self._find_item_by_path(vp)
            if it is None:
                return
            mtime = float(it.data(Qt.UserRole + 2) or 0)
            self._meta_cache[vp] = (mtime, int(w or 0), int(h or 0), float(dur or 0.0))
            self._apply_meta_to_item(it, w, h, dur)

        worker.ready.connect(_ok)
        worker.start()

    def refresh_list(self):
        cur_path = self._selected_path()
        self._stop_thumb_worker()
        self._stop_meta_worker()
        self.list_files.clear()
        folder = self._resolve_save_dir()
        if not folder or not os.path.isdir(folder):
            return
        files = []
        try:
            for name in os.listdir(folder):
                if not name.lower().endswith((".mp4", ".mkv", ".webm", ".avi")):
                    continue
                fp = os.path.join(folder, name)
                if os.path.isfile(fp):
                    try:
                        mtime = os.path.getmtime(fp)
                        size = os.path.getsize(fp)
                    except Exception:
                        mtime, size = 0, 0
                    files.append((mtime, size, fp, name))
        except Exception:
            log.exception("扫描录像目录失败")
            return
        files.sort(key=lambda t: t[0], reverse=True)
        restore = None
        pending_thumbs = []
        pending_meta = []
        for mtime, size, fp, name in files[:80]:
            sz = _fmt_size(size)
            item = QListWidgetItem()
            item.setData(Qt.UserRole, fp)
            item.setData(Qt.UserRole + 1, size)
            item.setData(Qt.UserRole + 2, mtime)
            item.setSizeHint(QSize(0, _ROW_H))
            self.list_files.addItem(item)

            # 副标题：分辨率 · 时长 · 体积（不显示录制时间）
            cached_meta = self._meta_cache.get(fp)
            if cached_meta and abs(float(cached_meta[0]) - float(mtime)) < 0.5:
                _, w, h, dur = cached_meta
                sub = self._format_row_meta(w, h, dur, size)
                item.setData(Qt.UserRole + 3, w)
                item.setData(Qt.UserRole + 4, h)
                item.setData(Qt.UserRole + 5, dur)
            else:
                sub = f"{sz}  ·  读取分辨率/时长…"
                pending_meta.append(fp)

            row = _RecordRow(name, sub)
            row.play_requested.connect(lambda p=fp: self._play_path(p))
            row.delete_requested.connect(lambda p=fp: self._delete_path(p))
            self.list_files.setItemWidget(item, row)

            # 已有磁盘缩略图立刻按真实比例显示，否则异步抽帧
            thumb = self._thumb_path_for(fp)
            cached = self._thumb_cache.get(fp) or thumb
            if cached and os.path.isfile(cached) and os.path.getsize(cached) > 32:
                self._thumb_cache[fp] = cached
                row.set_thumb_file(cached)
            else:
                pending_thumbs.append((fp, thumb))
            if cur_path and os.path.normpath(fp) == os.path.normpath(cur_path):
                restore = item
        if restore:
            self.list_files.setCurrentItem(restore)
        if pending_thumbs:
            self._start_thumb_queue(pending_thumbs)
        if pending_meta:
            self._start_meta_queue(pending_meta)

    def _selected_path(self) -> str:
        it = self.list_files.currentItem()
        if not it:
            return ""
        return it.data(Qt.UserRole) or ""

    def _select_path(self, path: str):
        it = self._find_item_by_path(path)
        if it is not None:
            self.list_files.setCurrentItem(it)

    def _play_path(self, path: str):
        """播放本机录像文件（系统默认播放器）。"""
        path = (path or "").strip()
        if not path or not os.path.isfile(path):
            self._set_status("文件不存在或已删除")
            self.refresh_list()
            return
        self._select_path(path)
        _open_file(path)
        self._set_status(f"播放：{os.path.basename(path)}")

    def _delete_path(self, path: str):
        """行内删除：弹窗确认后删除本机文件 + 缩略图缓存。"""
        path = (path or "").strip()
        if not path:
            return
        if not os.path.isfile(path):
            self._set_status("文件已不存在，已刷新列表")
            self.refresh_list()
            return
        name = os.path.basename(path)
        r = QMessageBox.question(
            self,
            "删除录像",
            f"确定删除本机录像吗？\n\n{name}\n\n此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if r != QMessageBox.Yes:
            return
        try:
            os.remove(path)
            thumb = self._thumb_cache.pop(path, None)
            if not thumb:
                thumb = self._thumb_path_for(path)
            if thumb and os.path.isfile(thumb):
                try:
                    os.remove(thumb)
                except Exception:
                    pass
            self.refresh_list()
            self._set_status(f"已删除 {name}")
        except Exception as e:
            self._set_status(f"删除失败：{e}")
            message_box_warn(self, "删除失败", str(e))

    def _open_save_dir(self):
        d = self._resolve_save_dir()
        try:
            ensure_dir(d)
        except Exception:
            pass
        if os.path.isdir(d):
            _open_in_file_manager(d, select=False)

    # ── 偏好 ─────────────────────────────────────────────────
    def export_settings(self) -> dict:
        d = {
            "save_path": self._resolve_save_dir().replace("\\", "/"),
            "hide_window": bool(self.chk_hide.isChecked()),
            "show_cursor": bool(self.chk_cursor.isChecked()),
            "default_region": rect_to_dict(self._default_region) if self._default_region else None,
        }
        return d

    def apply_settings(self, d: dict):
        d = d or {}
        path = (d.get("save_path") or "").strip()
        if path:
            self.apply_save_path(path)
        if "hide_window" in d:
            self.chk_hide.setChecked(bool(d.get("hide_window")))
        if "show_cursor" in d:
            self.chk_cursor.setChecked(bool(d.get("show_cursor")))
        self._default_region = dict_to_rect(d.get("default_region"))
        self._refresh_region_label()

    def refresh_theme(self, *_):
        for c in getattr(self, "_cards", ()):
            try:
                restyle_card_frame(c)
            except Exception:
                pass
        self._update_ffmpeg_label()
        for w in (
            self.lbl_status, self.lbl_keys, self.lbl_path, self.lbl_region,
            self.chk_cursor, self.chk_hide,
            getattr(self, "lbl_list_hint", None),
        ):
            if w is not None:
                w.setStyleSheet(TEXT_STYLE_T)
        try:
            apply_simple_record(self.list_files)
        except Exception:
            pass
        # 主题切换：行内文案 + 占位图底色（真实缩略图比例不变，整表刷新最稳）
        try:
            self.refresh_list()
        except Exception:
            pass

    def on_enter(self):
        self._refresh_path_label()
        self._update_ffmpeg_label()
        self._refresh_region_label()
        self.refresh_list()

    def shutdown(self):
        self._tick.stop()
        self._unbind_hotkeys()
        if self._recorder.is_recording:
            try:
                self._recorder.stop(discard=False)
            except Exception:
                self._recorder.force_kill()
            try:
                self.recording_changed.emit(False)
            except Exception:
                pass
        self._close_outline()
        if self._bar is not None:
            try:
                self._bar.close()
            except Exception:
                pass
            self._bar = None
        self._close_select_overlay()
        self._close_adjust()
        self._stop_thumb_worker()
        self._stop_meta_worker()
        self._restore_main()
