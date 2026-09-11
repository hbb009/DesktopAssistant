# pages/page_voice_clone.py
# 语音克隆：左 60%（上声音卡 / 下文案）+ 右 40% 生成记录

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from datetime import datetime

from PyQt5.QtCore import Qt, QTimer, QSize, QUrl, QRect, QRectF, QPoint, QPointF, QEvent, pyqtSignal
from PyQt5.QtGui import (
    QDesktopServices, QDragEnterEvent, QDropEvent, QColor, QPainter,
    QPen, QBrush, QPainterPath, QPixmap, QFont, QIcon,
)
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit,
    QSizePolicy, QFileDialog, QListWidget, QListWidgetItem, QFrame,
    QScrollArea, QAbstractItemView, QMessageBox, QGraphicsOpacityEffect,
)

from styles.style_all import (
    theme,
    tk,
    TEXT_STYLE,
    make_card,
    restyle_card_title,
    restyle_card_frame,
    apply_btn_download,
    BTN_DOWNLOAD_QSS,
    apply_medium_button,
    apply_mini_button,
    apply_simple_record,
    install_card_title,
    CARD_LEFT_GAP,
    CARD_RIGHT_GAP,
    CARD_TOP_GAP,
    CARD_BOTTOM_GAP,
    message_box_info,
)
from utils.cosyvoice_clone import (
    SynthWorker,
    PlayWavWorker,
    default_model_dir,
    weights_status,
    new_out_wav_path,
    wav_duration_sec,
    AUDIO_FILTER,
    is_audio_file,
    import_reference_audio,
    work_dir,
)
from utils.flow_layout import FlowLayout
from utils.logger import get_logger

log = get_logger(__name__)

_HIST_MAX = 80
_VOICE_CARD_W = 72   # 提示词内容卡 48×72 的 1.5 倍，仍 2:3
_VOICE_CARD_H = 108
_VOICE_CARD_ICON_BTN = 24
_VOICE_CARD_ICON_PX = 18
# 声音卡圆角：QSS border-radius 在本环境只圆了描边、背景仍是直角方块，
# 圆角由自定义 paintEvent 用 QPainterPath 兜底（见 _VoiceCard.paintEvent）
_VOICE_CARD_RADIUS = 6.0
_VOICE_DRAG_PX = 6
_RECORD_DRAG_PX = 6
_ROW_H = 40
_VOICE_PALETTE = (
    "#ef4444", "#f97316", "#eab308", "#84cc16",
    "#22c55e", "#14b8a6", "#06b6d4", "#0ea5e9",
    "#3b82f6", "#6366f1", "#8b5cf6", "#a855f7",
    "#d946ef", "#ec4899", "#f43f5e", "#fb7185",
)
_ICON_PX = 18
_ICON_BTN = 28
TEXT_STYLE_T = TEXT_STYLE + " background: transparent;"


def _norm_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path or ""))


def _audio_dup_key(path: str) -> str:
    """用体积 + 前 64KB 指纹判断是不是同一份声音。"""
    path = (path or "").strip()
    if not path or not os.path.isfile(path):
        return ""
    try:
        size = int(os.path.getsize(path))
        h = hashlib.md5()
        with open(path, "rb") as f:
            h.update(f.read(65536))
        return f"{size}:{h.hexdigest()}"
    except Exception:
        return ""


def _wav_sig(path: str) -> str:
    """文件签名：原声变了就作废缓存的逐字稿。"""
    try:
        st = os.stat(path)
        return f"{int(st.st_mtime)}:{int(st.st_size)}"
    except Exception:
        return ""


def _fit_card_text(text: str, fm, width: int, max_lines: int) -> str:
    """按宽度折行；超出行数时末行右侧截断。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    width = max(8, int(width))
    max_lines = max(1, int(max_lines))

    def _w(s: str) -> int:
        try:
            return int(fm.horizontalAdvance(s))
        except Exception:
            return int(fm.width(s))

    lines = []
    cur = ""
    for ch in raw:
        trial = cur + ch
        if not cur or _w(trial) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    if len(lines) <= max_lines:
        return "\n".join(lines)
    kept = lines[:max_lines]
    rest = "".join(lines[max_lines - 1:])
    kept[-1] = fm.elidedText(rest, Qt.ElideRight, width)
    return "\n".join(kept)


def _display_name(rec: dict) -> str:
    name = str((rec or {}).get("name") or "").strip()
    if name:
        return name
    stem = os.path.splitext(os.path.basename(str((rec or {}).get("path") or "")))[0]
    if not stem or stem.startswith("upload_"):
        return "声音"
    return stem


def _fmt_size(n: int) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        n = 0
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 / 1024:.2f} MB"


def _fmt_dur(sec: float) -> str:
    try:
        s = float(sec or 0)
    except (TypeError, ValueError):
        return ""
    if s <= 0:
        return ""
    whole = int(round(s))
    m, r = divmod(whole, 60)
    if m:
        return f"{m}:{r:02d}"
    return f"{s:.1f} 秒"


def _clock_of(ts: str) -> str:
    ts = str(ts or "")
    if len(ts) >= 16:
        return ts[11:16]
    return ts or "--:--"


def _audio_paths_from_urls(urls) -> list:
    out = []
    seen = set()
    for url in urls or []:
        try:
            p = url.toLocalFile() if hasattr(url, "toLocalFile") else str(url)
        except Exception:
            p = ""
        p = os.path.abspath(p) if p else ""
        if not is_audio_file(p):
            continue
        key = _norm_path(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _mime_looks_like_files(md) -> bool:
    """Explorer 拖入时，dragEnter 里 urls 可能还是空的，只声明了 uri-list。"""
    if md is None:
        return False
    try:
        if md.hasUrls() and md.urls():
            return True
    except Exception:
        pass
    try:
        fmts = [str(x) for x in (md.formats() or [])]
    except Exception:
        fmts = []
    for f in fmts:
        fl = f.lower()
        if "uri-list" in fl or "filename" in fl or "hdrop" in fl or "text/uri" in fl:
            return True
    return False


def _tool_icon(kind: str, color: str, size: int = _ICON_PX) -> QIcon:
    """播放 / 暂停 / 删除 线框图标。"""
    size = max(14, int(size))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    c = QColor(color)
    s = float(size)
    if kind == "play":
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(c))
        path = QPainterPath()
        path.moveTo(s * 0.32, s * 0.20)
        path.lineTo(s * 0.32, s * 0.80)
        path.lineTo(s * 0.82, s * 0.50)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "stop":
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(c))
        w, gap = s * 0.16, s * 0.14
        y, h = s * 0.22, s * 0.56
        p.drawRoundedRect(QRectF(s * 0.30, y, w, h), 1.4, 1.4)
        p.drawRoundedRect(QRectF(s * 0.30 + w + gap, y, w, h), 1.4, 1.4)
    else:
        pen = QPen(c, 1.55)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(s * 0.26, s * 0.32), QPointF(s * 0.74, s * 0.32))
        p.drawLine(QPointF(s * 0.38, s * 0.32), QPointF(s * 0.38, s * 0.22))
        p.drawLine(QPointF(s * 0.62, s * 0.32), QPointF(s * 0.62, s * 0.22))
        p.drawLine(QPointF(s * 0.34, s * 0.22), QPointF(s * 0.66, s * 0.22))
        p.drawLine(QPointF(s * 0.30, s * 0.32), QPointF(s * 0.34, s * 0.80))
        p.drawLine(QPointF(s * 0.70, s * 0.32), QPointF(s * 0.66, s * 0.80))
        p.drawLine(QPointF(s * 0.34, s * 0.80), QPointF(s * 0.66, s * 0.80))
    p.end()
    return QIcon(pm)


def _icon_wash() -> str:
    try:
        if theme.is_dark:
            return "rgba(255,255,255,0.12)"
    except Exception:
        pass
    return "rgba(15,23,42,0.08)"


def _norm_hex(val) -> str:
    s = str(val or "").strip()
    if not s:
        return ""
    if not s.startswith("#"):
        s = "#" + s
    c = QColor(s)
    if not c.isValid():
        return ""
    return c.name()


def _mix_hex(a: str, b: str, t: float) -> str:
    ca, cb = QColor(_norm_hex(a) or "#000000"), QColor(_norm_hex(b) or "#ffffff")
    t = max(0.0, min(1.0, float(t)))
    return QColor(
        int(round(ca.red() * (1 - t) + cb.red() * t)),
        int(round(ca.green() * (1 - t) + cb.green() * t)),
        int(round(ca.blue() * (1 - t) + cb.blue() * t)),
    ).name()


def _contrast_ink(color: str) -> str:
    c = QColor(_norm_hex(color) or "#000000")
    y = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return "#111827" if y >= 160 else "#ffffff"


def _pick_voice_color(used) -> str:
    used = {str(x or "").strip().lower() for x in (used or []) if x}
    pool = [c for c in _VOICE_PALETTE if c.lower() not in used]
    if not pool:
        pool = list(_VOICE_PALETTE)
    return random.choice(pool)


def _icon_btn(
    kind: str, color: str, tip: str, *,
    btn_size: int = None, icon_size: int = None, wash: bool = False, fill: str = "",
) -> QPushButton:
    btn = QPushButton()
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFocusPolicy(Qt.NoFocus)
    btn.setFlat(True)
    side = int(btn_size or _ICON_BTN)
    ico = int(icon_size or _ICON_PX)
    btn.setFixedSize(side, side)
    btn.setIconSize(QSize(ico, ico))
    btn.setIcon(_tool_icon(kind, color, ico))
    btn.setToolTip(tip)
    btn.setProperty("iconKind", kind)
    _style_icon_btn(btn, wash=wash, fill=fill)
    return btn


def _style_icon_btn(btn: QPushButton, *, wash: bool = False, fill: str = ""):
    fill = _norm_hex(fill)
    if fill:
        hover = _mix_hex(fill, "#ffffff", 0.18)
        press = _mix_hex(fill, "#000000", 0.16)
        btn.setStyleSheet(
            f"QPushButton{{background:{fill};border:none;padding:0;border-radius:6px;}}"
            f"QPushButton:hover{{background:{hover};}}"
            f"QPushButton:pressed{{background:{press};}}"
        )
        return
    if wash:
        bg = _icon_wash()
        btn.setStyleSheet(
            f"QPushButton{{background:{bg};border:none;padding:0;border-radius:6px;}}"
            "QPushButton:hover{background:rgba(148,163,184,0.28);}"
            "QPushButton:pressed{background:rgba(148,163,184,0.38);}"
        )
        return
    btn.setStyleSheet(
        "QPushButton{background:transparent;border:none;padding:0;}"
        "QPushButton:hover{background:rgba(148,163,184,0.18);border-radius:4px;}"
        "QPushButton:pressed{background:rgba(148,163,184,0.28);}"
    )


class _VoiceDropGuide(QWidget):
    """拖拽插入线：竖虚线叠在落点缝上，不占布局。"""

    W = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("VoiceDropGuide")
        self.setFixedWidth(self.W)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_StyledBackground, True)

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
        x = self.width() // 2
        p.drawLine(x, 4, x, max(5, self.height() - 4))
        p.end()


class _RecordDropGuide(QWidget):
    """记录拖拽插入线：横虚线叠在落点缝上，不占布局。"""

    H = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("RecordDropGuide")
        self.setFixedHeight(self.H)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_StyledBackground, True)

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
        p.drawLine(4, y, max(5, self.width() - 4), y)
        p.end()


class _RecordList(QListWidget):
    """生成记录列表：空白处点击取消选中。"""

    blank_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(False)
        self.setDragDropMode(QAbstractItemView.NoDragDrop)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.itemAt(e.pos()) is None:
            self.blank_clicked.emit()
        super().mousePressEvent(e)


class _RatioRow(QWidget):
    """左右按比例切宽（默认 60/40），高度随行拉伸。"""

    def __init__(self, left_frac: float = 0.6, spacing: int = 8, parent=None):
        super().__init__(parent)
        self._left_frac = float(left_frac)
        self._spacing = int(spacing)
        self._left = None
        self._right = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(self._spacing)

    def minimumSizeHint(self):
        return QSize(240, 180)

    def set_columns(self, left: QWidget, right: QWidget):
        self._left = left
        self._right = right
        for w in (left, right):
            w.setMinimumWidth(0)
            w.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self._lay.addWidget(left)
        self._lay.addWidget(right)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._left is None or self._right is None:
            return
        total = max(1, self.width() - self._spacing)
        lw = max(80, int(round(total * self._left_frac)))
        rw = max(80, total - lw)
        if self._left.width() != lw:
            self._left.setFixedWidth(lw)
        if self._right.width() != rw:
            self._right.setFixedWidth(rw)


class _RatioCol(QWidget):
    """上下按比例切高：上声音卡 60% / 下内容 40%。"""

    def __init__(self, top_frac: float = 0.6, spacing: int = 0, parent=None):
        super().__init__(parent)
        self._top_frac = float(top_frac)
        self._spacing = int(spacing)
        self._top = None
        self._bottom = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(self._spacing)

    def minimumSizeHint(self):
        return QSize(160, 200)

    def set_rows(self, top: QWidget, bottom: QWidget):
        self._top = top
        self._bottom = bottom
        for w in (top, bottom):
            w.setMinimumHeight(0)
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self._lay.addWidget(top)
        self._lay.addWidget(bottom)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._top is None or self._bottom is None:
            return
        total = max(1, self.height() - self._spacing)
        th = max(72, int(round(total * self._top_frac)))
        bh = max(72, total - th)
        if self._top.height() != th:
            self._top.setFixedHeight(th)
        if self._bottom.height() != bh:
            self._bottom.setFixedHeight(bh)


class _VoicePlayLine(QWidget):
    """贴在声音卡 / 记录行底边的绿色播放进度，从左到右。"""

    H = 3
    COLOR = QColor("#22c55e")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.H)
        self._pct = 0.0
        self.setVisible(False)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_progress(self, pct):
        if pct is None or pct < 0:
            self._pct = 0.0
            self.setVisible(False)
            self.update()
            return
        try:
            self._pct = max(0.0, min(100.0, float(pct)))
        except (TypeError, ValueError):
            self._pct = 0.0
        self.setVisible(True)
        self.raise_()
        self.update()

    def paintEvent(self, _e):
        if not self.isVisible() or self.width() <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w = int(round(self.width() * self._pct / 100.0))
        if w > 0:
            p.fillRect(0, 0, w, self.height(), self.COLOR)
        p.end()


class _VoiceCard(QFrame):
    """提示词内容卡同款：48×72、标题居中；底边播放/删除图标；可拖动排序。"""

    selected = pyqtSignal(str)
    play_requested = pyqtSignal(str)
    remove_requested = pyqtSignal(str)

    def __init__(self, rec: dict, parent=None, owner=None):
        super().__init__(parent)
        self.rec = dict(rec or {})
        self._owner = owner
        self._playing = False
        self._missing = False
        self._press = None
        self._dragging = False
        self.setObjectName("VoiceCloneCard")
        self.setFrameShape(QFrame.NoFrame)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._bg = QColor("#3b82f6")
        self._bd = QColor("#1b3b6f")
        self._bw = 1
        self.setCursor(Qt.OpenHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedSize(_VOICE_CARD_W, _VOICE_CARD_H)
        self._lab = QLabel()
        self._lab.setObjectName("VoiceCloneCardLab")
        self._lab.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self._lab.setWordWrap(True)
        self._lab.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.btn_play = _icon_btn(
            "play", "#93c5fd", "播放",
            btn_size=_VOICE_CARD_ICON_BTN, icon_size=_VOICE_CARD_ICON_PX,
        )
        self.btn_play.clicked.connect(lambda: self.play_requested.emit(self.path()))
        self.btn_del = _icon_btn(
            "trash", "#F87171", "删除",
            btn_size=_VOICE_CARD_ICON_BTN, icon_size=_VOICE_CARD_ICON_PX,
        )
        self.btn_del.clicked.connect(lambda: self.remove_requested.emit(self.path()))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 2)
        lay.setSpacing(0)
        lay.addWidget(self._lab, 1)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        row.addWidget(self.btn_play, 0, Qt.AlignLeft)
        row.addStretch(1)
        row.addWidget(self.btn_del, 0, Qt.AlignRight)
        lay.addLayout(row)
        self._play_line = _VoicePlayLine(self)
        self.apply_title()
        self._place_play_line()

    def _place_play_line(self):
        line = getattr(self, "_play_line", None)
        if line is None:
            return
        h = _VoicePlayLine.H
        line.setGeometry(1, max(0, self.height() - h), max(1, self.width() - 2), h)
        line.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_play_line()

    def paintEvent(self, _e):
        """QPainterPath 画圆角：背景色 + 描边都按圆角裁剪/绘制。

        QSS 的 border-radius 在本环境只圆了描边，背景填充仍是直角方块，
        所以声音卡的圆角必须在这里亲手画出来。
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, _VOICE_CARD_RADIUS, _VOICE_CARD_RADIUS)
        p.setClipPath(path)
        p.fillRect(rect, self._bg)
        p.setClipping(False)
        p.setPen(QPen(self._bd, float(max(1, self._bw))))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect, _VOICE_CARD_RADIUS, _VOICE_CARD_RADIUS)
        p.end()

    def path(self) -> str:
        return str(self.rec.get("path") or "")

    def color(self) -> str:
        return _norm_hex(self.rec.get("color"))

    def is_missing(self) -> bool:
        """声音文件是否存在：路径非空但文件已丢失。"""
        p = self.path()
        return bool(p) and not os.path.isfile(p)

    def _ink(self) -> str:
        if self._missing:
            return "#94a3b8"
        c = self.color()
        if c:
            return _contrast_ink(c)
        try:
            return "#93c5fd" if theme.is_dark else "#2563eb"
        except Exception:
            return "#93c5fd"

    def apply_title(self):
        self._missing = self.is_missing()
        title = _display_name(self.rec)
        if self._missing:
            title = (title or "声音") + "（丢失）"
        self._lab.setToolTip(title)
        fm = self._lab.fontMetrics()
        inner_w = max(8, _VOICE_CARD_W - 8)
        icon_row = _VOICE_CARD_ICON_BTN
        max_lines = max(1, (_VOICE_CARD_H - 8 - icon_row) // max(1, fm.lineSpacing()))
        self._lab.setText(_fit_card_text(title, fm, inner_w, max_lines))

    def set_playing(self, playing: bool):
        self._playing = bool(playing) and not self._missing
        ink = self._ink()
        self.btn_play.setEnabled(not self._missing)
        self.btn_play.setIcon(
            _tool_icon("stop" if self._playing else "play", ink, _VOICE_CARD_ICON_PX)
        )
        if self._missing:
            self.btn_play.setToolTip("声音文件已丢失，无法播放")
        else:
            self.btn_play.setToolTip("停止播放" if self._playing else "播放")
        try:
            self.btn_del.setIcon(_tool_icon("trash", ink, _VOICE_CARD_ICON_PX))
        except Exception:
            pass
        if not self._playing:
            self.set_play_progress(None)

    def set_play_progress(self, pct):
        line = getattr(self, "_play_line", None)
        if line is None:
            return
        line.set_progress(pct)
        self._place_play_line()

    def _hit_action_btn(self, pos) -> bool:
        w = self.childAt(pos)
        while w is not None and w is not self:
            if w is self.btn_play or w is self.btn_del:
                return True
            w = w.parentWidget()
        return False

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self._hit_action_btn(e.pos()):
            self._press = e.globalPos()
            self._dragging = False
            self.selected.emit(self.path())
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._press is None or not (e.buttons() & Qt.LeftButton):
            return
        owner = self._owner
        if owner is not None and getattr(owner, "_busy", lambda: False)():
            return
        if not self._dragging:
            if (e.globalPos() - self._press).manhattanLength() < _VOICE_DRAG_PX:
                return
            self._dragging = True
            self.setCursor(Qt.ClosedHandCursor)
            if owner is not None:
                owner._begin_voice_drag(self, e.globalPos())
        elif owner is not None:
            owner._follow_voice_drag(e.globalPos())
        e.accept()

    def mouseReleaseEvent(self, e):
        if self._dragging:
            owner = self._owner
            if owner is not None:
                owner._end_voice_drag(self)
            self._dragging = False
            self._press = None
            self.setCursor(Qt.OpenHandCursor)
            e.accept()
            return
        self._press = None
        super().mouseReleaseEvent(e)


class _FlowHost(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("VoiceCardHost")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("#VoiceCardHost{background:transparent;border:none;}")
        self.flow = FlowLayout(self, margin=0, h_spacing=6, v_spacing=6)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return max(1, int(self.flow.heightForWidth(w)))


class _VoiceBoard(QFrame):
    """60% 高度的拖入区：48×72 内容卡从左到右、不够换行。"""

    files_dropped = pyqtSignal(list)
    blank_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("VoiceCloneBoard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAcceptDrops(True)
        self.setCursor(Qt.ArrowCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(_VOICE_CARD_H + 16)
        self._hover = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(0)

        self.empty = QLabel("")
        self.empty.setObjectName("VoiceCloneBoardEmpty")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.empty, 1)

        self.host = _FlowHost()
        self.scroll = QScrollArea()
        self.scroll.setObjectName("VoiceCloneBoardScroll")
        self.scroll.setWidgetResizable(False)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setWidget(self.host)
        self.scroll.setStyleSheet(
            "QScrollArea#VoiceCloneBoardScroll{background:transparent;border:none;}"
            "QScrollArea#VoiceCloneBoardScroll > QWidget > QWidget{background:transparent;}"
        )
        try:
            self.scroll.viewport().setAutoFillBackground(False)
            self.scroll.viewport().setStyleSheet("background:transparent;")
        except Exception:
            pass
        self.scroll.setVisible(False)
        lay.addWidget(self.scroll, 1)
        for w in (self.empty, self.host, self.scroll, self.scroll.viewport()):
            self._bind_drop_proxy(w)
        self.refresh_theme()

    def _bind_drop_proxy(self, w):
        if w is None:
            return
        try:
            w.setAcceptDrops(True)
            w.installEventFilter(self)
        except Exception:
            pass

    def eventFilter(self, obj, ev):
        t = ev.type() if ev is not None else None
        if t == QEvent.DragEnter:
            self.dragEnterEvent(ev)
            return bool(ev.isAccepted())
        if t == QEvent.DragMove:
            self.dragMoveEvent(ev)
            return bool(ev.isAccepted())
        if t == QEvent.DragLeave:
            self.dragLeaveEvent(ev)
            return False
        if t == QEvent.Drop:
            self.dropEvent(ev)
            return bool(ev.isAccepted())
        if t == QEvent.MouseButtonPress:
            try:
                if ev.button() == Qt.LeftButton and not self._global_on_voice_card(ev.globalPos()):
                    self.blank_clicked.emit()
            except Exception:
                pass
            return False
        return super().eventFilter(obj, ev)

    def _global_on_voice_card(self, gpos) -> bool:
        try:
            w = QApplication.widgetAt(gpos)
        except Exception:
            w = None
        while w is not None:
            if isinstance(w, _VoiceCard):
                return True
            w = w.parentWidget()
        return False

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self._global_on_voice_card(e.globalPos()):
            self.blank_clicked.emit()
        super().mousePressEvent(e)

    def flow(self):
        return self.host.flow

    def set_has_cards(self, has: bool):
        self.empty.setVisible(not has)
        self.scroll.setVisible(bool(has))
        if has:
            QTimer.singleShot(0, self.fit_host)

    def fit_host(self):
        n = 0
        try:
            n = int(self.flow().count())
        except Exception:
            n = 0
        vw = 0
        try:
            vw = int(self.scroll.viewport().width())
        except Exception:
            vw = 0
        if vw < 8:
            vw = max(1, int(self.width()) - 16)
        cols = max(1, (vw + 6) // (_VOICE_CARD_W + 6))
        rows = max(1, (n + cols - 1) // cols) if n else 0
        h = 0
        try:
            h = int(self.host.heightForWidth(vw))
        except Exception:
            h = 0
        # 页面还没显示时 FlowLayout 会把隐藏项当空，heightForWidth≈1，这里按卡数兜底
        if n and h < _VOICE_CARD_H:
            h = rows * _VOICE_CARD_H + max(0, rows - 1) * 6
        self.host.setFixedSize(max(1, vw), max(h, 1))
        try:
            self.flow().invalidate()
            self.flow().activate()
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_host()

    def refresh_theme(self, *_):
        try:
            dim = tk("text_dim")
            bd = tk("border")
            acc = tk("accent") if self._hover else bd
        except Exception:
            dim, acc = "#94a3b8", "rgba(148,163,184,0.45)"
        self.setStyleSheet(
            f"QFrame#VoiceCloneBoard{{background:transparent;color:{dim};"
            f"border:1px dashed {acc};border-radius:8px;}}"
            f"QLabel#VoiceCloneBoardEmpty{{background:transparent;color:{dim};"
            f"border:none;font-size:14px;}}"
        )

    def dragEnterEvent(self, e: QDragEnterEvent):
        md = e.mimeData() if e is not None else None
        if _mime_looks_like_files(md) or _audio_paths_from_urls(
            md.urls() if md is not None and md.hasUrls() else []
        ):
            self._hover = True
            self.refresh_theme()
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        md = e.mimeData() if e is not None else None
        if _mime_looks_like_files(md) or _audio_paths_from_urls(
            md.urls() if md is not None and md.hasUrls() else []
        ):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self._hover = False
        self.refresh_theme()
        super().dragLeaveEvent(e)

    def dropEvent(self, e: QDropEvent):
        self._hover = False
        self.refresh_theme()
        md = e.mimeData() if e is not None else None
        paths = _audio_paths_from_urls(md.urls() if md is not None and md.hasUrls() else None)
        if paths:
            self.files_dropped.emit(paths)
            e.acceptProposedAction()
        else:
            e.ignore()


class _RecordRow(QWidget):
    """紧凑记录行：播放/停止图标 + 节选正文 + 删除图标；可拖动排序。"""

    play_requested = pyqtSignal()
    delete_requested = pyqtSignal()

    def __init__(self, title: str, subtitle: str = "", parent=None, owner=None, voice_color: str = ""):
        super().__init__(parent)
        self._owner = owner
        self._press = None
        self._dragging = False
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.OpenHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._title = title or ""
        self._playing = False
        self._voice_color = _norm_hex(voice_color)
        self.setToolTip(subtitle or self._title)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignVCenter)

        self.btn_play = _icon_btn(
            "play", self._play_color(), "播放",
            wash=not bool(self._voice_color),
            fill=self._voice_color,
        )
        self.btn_play.clicked.connect(self.play_requested.emit)
        lay.addWidget(self.btn_play, 0, Qt.AlignVCenter)

        self.lbl_title = QLabel(self._title)
        self.lbl_title.setStyleSheet(TEXT_STYLE_T)
        self.lbl_title.setWordWrap(False)
        self.lbl_title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.lbl_title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.lbl_title.setContentsMargins(0, 0, 0, 0)
        self.lbl_title.setIndent(0)
        self.lbl_title.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.lbl_title, 1)

        self.btn_del = _icon_btn("trash", "#F87171", "删除", wash=True)
        self.btn_del.clicked.connect(self.delete_requested.emit)
        lay.addWidget(self.btn_del, 0, Qt.AlignVCenter)
        self._play_line = _VoicePlayLine(self)
        self._place_play_line()

    def sizeHint(self):
        return QSize(160, _ROW_H)

    def minimumSizeHint(self):
        return QSize(80, _ROW_H)

    def _place_play_line(self):
        line = getattr(self, "_play_line", None)
        if line is None:
            return
        h = _VoicePlayLine.H
        line.setGeometry(0, max(0, self.height() - h), max(1, self.width()), h)
        line.raise_()

    def set_play_progress(self, pct):
        line = getattr(self, "_play_line", None)
        if line is None:
            return
        line.set_progress(pct)
        self._place_play_line()

    def _play_color(self) -> str:
        if self._voice_color:
            return _contrast_ink(self._voice_color)
        try:
            return "#93c5fd" if theme.is_dark else "#2563eb"
        except Exception:
            return "#93c5fd"

    def _hit_action_btn(self, pos) -> bool:
        w = self.childAt(pos)
        while w is not None and w is not self:
            if w is self.btn_play or w is self.btn_del:
                return True
            w = w.parentWidget()
        return False

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self._hit_action_btn(e.pos()):
            self._press = e.globalPos()
            self._dragging = False
            owner = self._owner
            if owner is not None:
                owner._select_record_row(self)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._press is None or not (e.buttons() & Qt.LeftButton):
            return
        owner = self._owner
        if not self._dragging:
            if (e.globalPos() - self._press).manhattanLength() < _RECORD_DRAG_PX:
                return
            self._dragging = True
            self.setCursor(Qt.ClosedHandCursor)
            if owner is not None:
                owner._begin_record_drag(self, e.globalPos())
        elif owner is not None:
            owner._follow_record_drag(e.globalPos())
        e.accept()

    def mouseReleaseEvent(self, e):
        if self._dragging:
            owner = self._owner
            if owner is not None:
                owner._end_record_drag(self)
            self._dragging = False
            self._press = None
            self.setCursor(Qt.OpenHandCursor)
            e.accept()
            return
        self._press = None
        if e.button() == Qt.LeftButton and not self._hit_action_btn(e.pos()):
            self.play_requested.emit()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_play_line()
        self._apply_elide()

    def showEvent(self, e):
        super().showEvent(e)
        self._place_play_line()
        self._apply_elide()

    def _apply_elide(self):
        fm = self.lbl_title.fontMetrics()
        w = max(8, self.lbl_title.width())
        self.lbl_title.setText(fm.elidedText(self._title, Qt.ElideRight, w))

    def set_playing(self, playing: bool):
        self._playing = bool(playing)
        kind = "stop" if self._playing else "play"
        self.btn_play.setIcon(_tool_icon(kind, self._play_color()))
        self.btn_play.setToolTip("停止播放" if self._playing else "播放")
        if not self._playing:
            self.set_play_progress(None)

    def refresh_theme(self):
        self.lbl_title.setStyleSheet(TEXT_STYLE_T)
        _style_icon_btn(
            self.btn_play,
            wash=not bool(self._voice_color),
            fill=self._voice_color,
        )
        _style_icon_btn(self.btn_del, wash=True)
        self.set_playing(self._playing)


class _CloneStatusLamp(QWidget):
    """生成状态圆灯：busy=黄闪 / ok=绿 / bad=红 / idle=灰。"""

    SIZE = 14
    _COLORS = {
        "idle": (QColor("#64748b"), QColor("#334155")),
        "busy": (QColor("#eab308"), QColor("#713f12")),
        "ok": (QColor("#22c55e"), QColor("#166534")),
        "bad": (QColor("#ef4444"), QColor("#7f1d1d")),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = "idle"
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


class _CloneStatusLed(QWidget):
    """与 OCR「提取文字」右侧同款：圆灯 + 2～3 字。"""

    _CAPTION = {
        "idle": ("就绪", "#94a3b8"),
        "busy": ("生成中", "#eab308"),
        "ok": ("完成", "#22c55e"),
        "bad": ("失败", "#ef4444"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedHeight(22)
        self._state = "idle"
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignVCenter)
        self._lamp = _CloneStatusLamp(self)
        self._txt = QLabel(self._CAPTION["idle"][0])
        self._txt.setWordWrap(False)
        self._txt.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        cap_font = QFont(self._txt.font())
        cap_font.setPixelSize(12)
        self._txt.setFont(cap_font)
        lay.addWidget(self._lamp, 0, Qt.AlignVCenter)
        lay.addWidget(self._txt, 0, Qt.AlignVCenter)
        self._apply_caption("idle")
        self._txt.setMinimumWidth(self._txt.fontMetrics().horizontalAdvance("生成中"))

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


class PageVoiceClone(QWidget):
    """左：声音卡 + 文案；右：生成记录。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._state = "idle"  # idle | synth
        self._prompt_wav = ""
        self._save_path = ""
        self._playing_path = ""
        self._worker = None
        self._player = None
        self._elapsed = 0
        self._loading = False
        self._voice_cards = []
        self._play_click_at = 0.0
        self._voice_drag = None
        self._voice_drag_ghost = None
        self._voice_drop_guide = None
        self._voice_drag_grab = None
        self._voice_drag_dest = 0
        self._record_drag_row = None
        self._record_drag_item = None
        self._record_drag_ghost = None
        self._record_drop_guide = None
        self._record_drag_grab = None
        self._record_drag_dest = 0
        self._record_drag_at = 0.0
        self._hist_blank_watch = set()
        self._play_t0 = 0.0
        self._play_dur = 0.0
        self._play_prog_timer = QTimer(self)
        self._play_prog_timer.setInterval(50)
        self._play_prog_timer.timeout.connect(self._tick_play_progress)
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._theme_titles = []
        self._func_cards = []
        self._prefs_cb = None

        self._build_ui()
        theme.changed.connect(self.refresh_theme)
        self._load_history()

    def minimumSizeHint(self):
        return QSize(0, 0)

    def sizeHint(self):
        return QSize(900, 640)

    def set_prefs_dirty_callback(self, fn):
        self._prefs_cb = fn

    def _dirty(self):
        if self._loading:
            return
        fn = self._prefs_cb
        if callable(fn):
            try:
                fn()
            except Exception:
                log.exception("语音克隆偏好回写失败")

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        split = _RatioRow(0.6, 0)

        left = _RatioCol(0.6, 0)
        left.setObjectName("VoiceCloneLeft")
        left.setAttribute(Qt.WA_StyledBackground, True)
        left.setStyleSheet("#VoiceCloneLeft{background:transparent;border:none;}")

        card_voice = make_card("CardVoiceCloneRef", borderless=True)
        self._func_cards.append(card_voice)
        lv = QVBoxLayout(card_voice)
        lv.setContentsMargins(CARD_LEFT_GAP, 8, 0, 0)
        lv.setSpacing(6)
        load_row = QHBoxLayout()
        load_row.setContentsMargins(0, 0, 0, 0)
        load_row.setSpacing(8)
        self.btn_load = apply_mini_button(QPushButton("加载声音文件"))
        self.btn_load.setCursor(Qt.PointingHandCursor)
        self.btn_load.clicked.connect(self._on_pick_wav)
        load_row.addWidget(self.btn_load, 0, Qt.AlignLeft | Qt.AlignVCenter)
        load_row.addStretch(1)
        self.lbl_drop_hint = QLabel("可拖拽声音文件到本区域")
        self.lbl_drop_hint.setProperty("typo", "muted")
        self.lbl_drop_hint.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_drop_hint.setStyleSheet(TEXT_STYLE_T)
        load_row.addWidget(self.lbl_drop_hint, 0, Qt.AlignRight | Qt.AlignVCenter)
        lv.addLayout(load_row)
        self.board = _VoiceBoard()
        self.board.files_dropped.connect(self._on_drop_paths)
        self.board.blank_clicked.connect(self._clear_voice_sel)
        lv.addWidget(self.board, 1)

        card_text = make_card("CardVoiceCloneText", borderless=True)
        self._func_cards.append(card_text)
        cv2 = QVBoxLayout(card_text)
        cv2.setContentsMargins(CARD_LEFT_GAP, 8, 0, 8)
        cv2.setSpacing(8)
        go = QHBoxLayout()
        go.setContentsMargins(0, 0, 0, 0)
        go.setSpacing(8)
        go.setAlignment(Qt.AlignVCenter)
        self.status_led = _CloneStatusLed()
        go.addWidget(self.status_led, 0, Qt.AlignVCenter)
        self.lbl_status = QLabel("")
        self.lbl_status.setProperty("typo", "muted")
        self.lbl_status.setWordWrap(False)
        self.lbl_status.setStyleSheet(TEXT_STYLE_T)
        self.lbl_status.setFixedHeight(22)
        self.lbl_status.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.lbl_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        go.addWidget(self.lbl_status, 1, Qt.AlignVCenter)
        self.btn_synth = QPushButton("生成")
        self.btn_synth.setObjectName("PasteVoiceBtn")
        self.btn_synth.setMinimumHeight(22)
        self.btn_synth.setMaximumHeight(22)
        self.btn_synth.setMinimumWidth(132)
        self.btn_synth.setCursor(Qt.PointingHandCursor)
        self.btn_synth.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        apply_btn_download(self.btn_synth)
        self.btn_synth.setStyleSheet(
            BTN_DOWNLOAD_QSS.replace("padding: 4px 14px;", "padding: 0px 14px;")
        )
        self.btn_synth.clicked.connect(self._on_synth_clicked)
        go.addWidget(self.btn_synth, 0, Qt.AlignVCenter)
        cv2.addLayout(go)
        self.edit_tts = QTextEdit()
        self.edit_tts.setObjectName("PasteContentEdit")
        self.edit_tts.setAcceptRichText(False)
        self.edit_tts.setPlaceholderText("写入要生成的内容")
        self.edit_tts.setMinimumHeight(40)
        self.edit_tts.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        cv2.addWidget(self.edit_tts, 1)
        left.set_rows(card_voice, card_text)

        card3 = make_card("CardVoiceCloneHist", borderless=True)
        self._func_cards.append(card3)
        cv3 = QVBoxLayout(card3)
        cv3.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        cv3.setSpacing(4)
        hist_title = install_card_title(card3, cv3, "生成记录")
        self._theme_titles.append(hist_title)
        self._hist_card = card3

        self.records = _RecordList()
        self.records.setObjectName("VoiceCloneRecords")
        self.records.setSelectionMode(QAbstractItemView.SingleSelection)
        self.records.setUniformItemSizes(True)
        self.records.setSpacing(2)
        self.records.setMouseTracking(True)
        apply_simple_record(self.records)
        self.records.itemClicked.connect(self._on_record_item_clicked)
        self.records.blank_clicked.connect(self._clear_record_sel)
        cv3.addWidget(self.records, 1)
        self._hist_blank_watch = {
            w for w in (card3, hist_title, hist_title.parentWidget()) if w is not None
        }
        for w in self._hist_blank_watch:
            w.installEventFilter(self)

        act = QHBoxLayout()
        act.setContentsMargins(0, 0, 0, 0)
        act.setSpacing(8)
        self.btn_refresh = QPushButton("刷新")
        self.btn_reveal = QPushButton("打开文件夹")
        for b in (self.btn_refresh, self.btn_reveal):
            apply_medium_button(b)
            b.setCursor(Qt.PointingHandCursor)
            act.addWidget(b)
        act.addStretch()
        self.btn_refresh.clicked.connect(self._refresh_lists)
        self.btn_reveal.clicked.connect(self._open_save_dir)
        cv3.addLayout(act)

        split.set_columns(left, card3)
        root.addWidget(split, 1)

    def eventFilter(self, obj, ev):
        if ev is not None and ev.type() == QEvent.MouseButtonPress:
            try:
                if ev.button() == Qt.LeftButton and obj in getattr(self, "_hist_blank_watch", ()):
                    self._clear_record_sel()
            except Exception:
                pass
        return super().eventFilter(obj, ev)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._relayout_voice_cards)

    def on_enter(self):
        self._prune_voice_cards(save=True)
        self._refresh_card_states()
        self._load_history()
        QTimer.singleShot(0, self._relayout_voice_cards)

    def _refresh_card_states(self):
        """启动/刷新/进入页面时重查每张声音卡：文件找回就恢复彩色，丢失就变灰。"""
        want = _norm_path(self._prompt_wav)
        for card in self._voice_cards:
            self._style_voice_card(card, selected=bool(want) and _norm_path(card.path()) == want)

    def _relayout_voice_cards(self):
        board = getattr(self, "board", None)
        if board is None:
            return
        board.set_has_cards(bool(self._voice_cards))
        board.fit_host()
        board.update()

    def refresh_theme(self, *_):
        for t in self._theme_titles:
            restyle_card_title(t)
        for c in self._func_cards:
            restyle_card_frame(c)
        if getattr(self, "lbl_drop_hint", None) is not None:
            self.lbl_drop_hint.setStyleSheet(TEXT_STYLE_T)
        if getattr(self, "lbl_status", None) is not None:
            self.lbl_status.setStyleSheet(TEXT_STYLE_T)
        led = getattr(self, "status_led", None)
        if led is not None:
            led.set_state(getattr(led, "_state", "idle") or "idle")
        board = getattr(self, "board", None)
        if board is not None:
            board.refresh_theme()
        for card in self._voice_cards:
            self._style_voice_card(card, selected=_norm_path(card.path()) == _norm_path(self._prompt_wav))
        try:
            apply_simple_record(self.records)
        except Exception:
            pass
        for i in range(self.records.count()):
            w = self.records.itemWidget(self.records.item(i))
            if isinstance(w, _RecordRow):
                w.refresh_theme()
                rec = self.records.item(i).data(Qt.UserRole) or {}
                path = str(rec.get("path") or "")
                w.set_playing(
                    bool(path) and _norm_path(path) == _norm_path(self._playing_path)
                )

    def _busy(self) -> bool:
        return self._state != "idle"

    def _set_busy_ui(self, busy: bool):
        self.board.setEnabled(not busy)
        self.board.setAcceptDrops(not busy)
        self.board.setCursor(Qt.ArrowCursor)
        self.btn_load.setEnabled(not busy)
        self.edit_tts.setReadOnly(busy)
        if busy:
            self._set_synth_led("busy", "准备中…")
        else:
            self.btn_synth.setText("生成")

    def _set_synth_led(self, state: str, detail: str = ""):
        led = getattr(self, "status_led", None)
        if led is not None:
            led.set_state(state)
        lbl = getattr(self, "lbl_status", None)
        if lbl is not None:
            lbl.setText(detail or "")
            lbl.setStyleSheet(TEXT_STYLE_T)
            lbl.setToolTip(detail or "")

    def _on_synth_status(self, msg: str):
        text = (msg or "").strip()
        log.info("语音克隆 %s", text)
        if self._state == "synth":
            self._set_synth_led("busy", text)

    def _warn(self, text: str):
        message_box_info(self, "语音克隆", text)

    def _style_voice_card(self, card: _VoiceCard, selected: bool = False):
        card.apply_title()  # 先刷新丢失标记，再决定配色
        missing = bool(getattr(card, "_missing", False))
        try:
            is_dark = bool(theme.is_dark)
        except Exception:
            is_dark = True
        if missing:
            # 声音文件丢失：灰色底 + 弱描边，禁用播放但仍可删除
            bg = "#475569" if is_dark else "#94a3b8"
            fg = "#e2e8f0" if is_dark else "#f8fafc"
            bd = "#64748b"
            bw = 1
        else:
            bg = card.color() or "#3b82f6"
            fg = _contrast_ink(bg)
            if selected:
                bd = "#f8fafc" if is_dark else "#0f172a"
                bw = 2
            else:
                bd = _mix_hex(bg, "#000000", 0.18)
                bw = 1
        card.setStyleSheet(
            f"QFrame#VoiceCloneCard QLabel{{"
            f"background:transparent;border:none;color:{fg};"
            f"font-size:12px;font-weight:600;}}"
        )
        # 背景 / 描边交给 paintEvent 用 QPainterPath 画圆角；
        # 这里只存颜色与线宽，并关掉 QPalette 平涂，避免盖出直角
        card._bg = QColor(bg)
        card._bd = QColor(bd)
        card._bw = max(1, int(bw))
        card.setAutoFillBackground(False)
        card.update()
        card.set_playing(_norm_path(card.path()) == _norm_path(self._playing_path))

    # ── 声音卡 ──────────────────────────────────────────────────────────
    def _export_voice_recs(self) -> list:
        rows = []
        for card in self._voice_cards:
            rec = dict(card.rec or {})
            path = str(rec.get("path") or "")
            if path:
                rows.append({
                    "path": path,
                    "name": _display_name(rec),
                    "src": str(rec.get("src") or ""),
                    "prompt_text": str(rec.get("prompt_text") or ""),
                    "prompt_sig": str(rec.get("prompt_sig") or ""),
                    "dup": str(rec.get("dup") or "") or _audio_dup_key(path),
                    "color": _norm_hex(rec.get("color")),
                })
        return rows

    def _find_card(self, path: str = "", src: str = ""):
        keys = set()
        fps = set()
        for p in (path, src):
            p = (p or "").strip()
            if not p:
                continue
            keys.add(_norm_path(p))
            fp = _audio_dup_key(p)
            if fp:
                fps.add(fp)
        for card in self._voice_cards:
            rec = card.rec or {}
            for p in (rec.get("path"), rec.get("src")):
                p = str(p or "").strip()
                if not p:
                    continue
                if _norm_path(p) in keys:
                    return card
            stored = str(rec.get("dup") or "").strip()
            if stored and stored in fps:
                return card
            for p in (rec.get("path"), rec.get("src")):
                fp = _audio_dup_key(str(p or ""))
                if fp and fp in fps:
                    return card
        return None

    def _alloc_voice_color(self) -> str:
        used = []
        for card in self._voice_cards:
            c = _norm_hex((card.rec or {}).get("color"))
            if c:
                used.append(c)
        return _pick_voice_color(used)

    def _sync_board(self):
        self.board.set_has_cards(bool(self._voice_cards))
        self.board.fit_host()

    def _select_voice(self, path: str, *, dirty: bool = True):
        path = path if path and os.path.isfile(path) else ""
        self._prompt_wav = path
        want = _norm_path(path)
        for card in self._voice_cards:
            self._style_voice_card(card, selected=bool(want) and _norm_path(card.path()) == want)
        if dirty:
            self._dirty()

    def _voice_drop_index(self, moving, gpos) -> int:
        others = [c for c in self._voice_cards if c is not moving]
        if not others:
            return 0
        for i, card in enumerate(others):
            r = QRect(card.mapToGlobal(QPoint(0, 0)), card.size())
            if gpos.y() < r.top() - 4:
                return i
            if r.top() - 6 <= gpos.y() <= r.bottom() + 6:
                if gpos.x() < r.center().x():
                    return i
        return len(others)

    def _apply_voice_order(self, cards: list):
        flow = self.board.flow()
        for card in list(self._voice_cards):
            flow.removeWidget(card)
        self._voice_cards = list(cards)
        for card in self._voice_cards:
            flow.addWidget(card)
        self._sync_board()

    def _begin_voice_drag(self, card, gpos):
        self._cleanup_voice_drag_visual()
        if self._busy() or card is None:
            return
        board = getattr(self, "board", None)
        if board is None:
            return
        self._voice_drag = card
        self._voice_drag_grab = card.mapFromGlobal(gpos)
        self._voice_drag_dest = self._voice_drop_index(card, gpos)
        try:
            fx = QGraphicsOpacityEffect(card)
            fx.setOpacity(0.45)
            card.setGraphicsEffect(fx)
        except Exception:
            pass
        try:
            card.grabMouse()
        except Exception:
            pass
        pix = None
        try:
            pix = card.grab()
        except Exception:
            pix = None
        ghost = QLabel(board)
        ghost.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        try:
            if pix is not None and not pix.isNull():
                ghost.setPixmap(pix)
            ghost.setFixedSize(card.size())
        except Exception:
            ghost.setFixedSize(card.size())
        gfx = QGraphicsOpacityEffect(ghost)
        gfx.setOpacity(0.55)
        ghost.setGraphicsEffect(gfx)
        ghost.show()
        self._voice_drag_ghost = ghost
        guide = _VoiceDropGuide(board)
        guide.hide()
        self._voice_drop_guide = guide
        self._follow_voice_drag(gpos)

    def _follow_voice_drag(self, gpos):
        board = getattr(self, "board", None)
        ghost = getattr(self, "_voice_drag_ghost", None)
        card = getattr(self, "_voice_drag", None)
        if board is None or ghost is None or card is None:
            return
        grab = getattr(self, "_voice_drag_grab", None)
        if grab is None:
            grab = QPoint(card.width() // 2, card.height() // 2)
        ghost.move(board.mapFromGlobal(gpos) - grab)
        ghost.show()
        ghost.raise_()
        dest = self._voice_drop_index(card, gpos)
        self._voice_drag_dest = dest
        self._place_voice_drop_guide(card, dest)
        guide = getattr(self, "_voice_drop_guide", None)
        if guide is not None:
            guide.raise_()
        ghost.raise_()

    def _place_voice_drop_guide(self, moving, dest: int):
        guide = getattr(self, "_voice_drop_guide", None)
        board = getattr(self, "board", None)
        if guide is None or board is None:
            return
        others = [c for c in self._voice_cards if c is not moving]
        if not others:
            guide.hide()
            return
        dest = max(0, min(int(dest), len(others)))
        if dest >= len(others):
            slot = others[-1]
            r = QRect(slot.mapTo(board, QPoint(0, 0)), slot.size())
            x = r.right()
        else:
            slot = others[dest]
            r = QRect(slot.mapTo(board, QPoint(0, 0)), slot.size())
            x = r.left()
        h = max(8, r.height())
        guide.setFixedHeight(h)
        guide.move(x - guide.width() // 2, r.y())
        guide.show()
        guide.raise_()

    def _cleanup_voice_drag_visual(self):
        card = getattr(self, "_voice_drag", None)
        if card is not None:
            try:
                card.releaseMouse()
            except Exception:
                pass
            try:
                card.setGraphicsEffect(None)
            except Exception:
                pass
            try:
                card.setCursor(Qt.OpenHandCursor)
            except Exception:
                pass
        ghost = getattr(self, "_voice_drag_ghost", None)
        guide = getattr(self, "_voice_drop_guide", None)
        self._voice_drag = None
        self._voice_drag_ghost = None
        self._voice_drop_guide = None
        self._voice_drag_grab = None
        for w in (ghost, guide):
            if w is None:
                continue
            try:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            except Exception:
                pass

    def _end_voice_drag(self, card):
        dest = int(getattr(self, "_voice_drag_dest", 0) or 0)
        moving = card if card is not None else getattr(self, "_voice_drag", None)
        self._cleanup_voice_drag_visual()
        if moving is None or moving not in self._voice_cards:
            return
        others = [c for c in self._voice_cards if c is not moving]
        dest = max(0, min(dest, len(others)))
        others.insert(dest, moving)
        if others != self._voice_cards:
            self._apply_voice_order(others)
        self._dirty()
        QTimer.singleShot(0, self._relayout_voice_cards)

    def _item_for_record_row(self, row):
        recs = getattr(self, "records", None)
        if recs is None or row is None:
            return None
        for i in range(recs.count()):
            it = recs.item(i)
            if recs.itemWidget(it) is row:
                return it
        return None

    def _select_record_row(self, row):
        it = self._item_for_record_row(row)
        if it is not None:
            self.records.setCurrentItem(it)

    def _clear_record_sel(self):
        if getattr(self, "_record_drag_row", None) is not None:
            return
        recs = getattr(self, "records", None)
        if recs is None:
            return
        recs.clearSelection()
        recs.setCurrentItem(None)

    def _record_other_items(self, moving_item):
        recs = getattr(self, "records", None)
        if recs is None:
            return []
        return [recs.item(i) for i in range(recs.count()) if recs.item(i) is not moving_item]

    def _record_drop_index(self, moving_item, gpos) -> int:
        others = self._record_other_items(moving_item)
        if not others:
            return 0
        recs = self.records
        y = recs.viewport().mapFromGlobal(gpos).y()
        for i, it in enumerate(others):
            r = recs.visualItemRect(it)
            if y < r.center().y():
                return i
        return len(others)

    def _begin_record_drag(self, row, gpos):
        self._cleanup_record_drag_visual()
        recs = getattr(self, "records", None)
        if recs is None or row is None:
            return
        item = self._item_for_record_row(row)
        if item is None:
            return
        vp = recs.viewport()
        self._record_drag_row = row
        self._record_drag_item = item
        self._record_drag_grab = row.mapFromGlobal(gpos)
        self._record_drag_dest = self._record_drop_index(item, gpos)
        try:
            fx = QGraphicsOpacityEffect(row)
            fx.setOpacity(0.45)
            row.setGraphicsEffect(fx)
        except Exception:
            pass
        try:
            row.grabMouse()
        except Exception:
            pass
        pix = None
        try:
            pix = row.grab()
        except Exception:
            pix = None
        ghost = QLabel(vp)
        ghost.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        try:
            if pix is not None and not pix.isNull():
                ghost.setPixmap(pix)
            ghost.setFixedSize(row.size())
        except Exception:
            ghost.setFixedSize(row.size())
        gfx = QGraphicsOpacityEffect(ghost)
        gfx.setOpacity(0.55)
        ghost.setGraphicsEffect(gfx)
        ghost.show()
        self._record_drag_ghost = ghost
        guide = _RecordDropGuide(vp)
        guide.hide()
        self._record_drop_guide = guide
        self._follow_record_drag(gpos)

    def _maybe_autoscroll_records(self, gpos):
        recs = getattr(self, "records", None)
        if recs is None:
            return
        vp = recs.viewport()
        y = vp.mapFromGlobal(gpos).y()
        bar = recs.verticalScrollBar()
        step = max(12, _ROW_H)
        if y < 28:
            bar.setValue(bar.value() - step)
        elif y > vp.height() - 28:
            bar.setValue(bar.value() + step)

    def _follow_record_drag(self, gpos):
        recs = getattr(self, "records", None)
        ghost = getattr(self, "_record_drag_ghost", None)
        row = getattr(self, "_record_drag_row", None)
        item = getattr(self, "_record_drag_item", None)
        if recs is None or ghost is None or row is None or item is None:
            return
        self._maybe_autoscroll_records(gpos)
        grab = getattr(self, "_record_drag_grab", None)
        if grab is None:
            grab = QPoint(row.width() // 2, row.height() // 2)
        ghost.move(recs.viewport().mapFromGlobal(gpos) - grab)
        ghost.show()
        ghost.raise_()
        dest = self._record_drop_index(item, gpos)
        self._record_drag_dest = dest
        self._place_record_drop_guide(item, dest)
        guide = getattr(self, "_record_drop_guide", None)
        if guide is not None:
            guide.raise_()
        ghost.raise_()

    def _place_record_drop_guide(self, moving_item, dest: int):
        guide = getattr(self, "_record_drop_guide", None)
        recs = getattr(self, "records", None)
        if guide is None or recs is None:
            return
        others = self._record_other_items(moving_item)
        if not others:
            guide.hide()
            return
        dest = max(0, min(int(dest), len(others)))
        if dest >= len(others):
            r = recs.visualItemRect(others[-1])
            y = r.bottom()
        else:
            r = recs.visualItemRect(others[dest])
            y = r.top()
        vp = recs.viewport()
        guide.setFixedWidth(max(8, vp.width() - 4))
        guide.move(2, y - guide.height() // 2)
        if y < -6 or y > vp.height() + 6:
            guide.hide()
        else:
            guide.show()
            guide.raise_()

    def _cleanup_record_drag_visual(self):
        row = getattr(self, "_record_drag_row", None)
        if row is not None:
            try:
                row.releaseMouse()
            except Exception:
                pass
            try:
                row.setGraphicsEffect(None)
            except Exception:
                pass
            try:
                row.setCursor(Qt.OpenHandCursor)
            except Exception:
                pass
        ghost = getattr(self, "_record_drag_ghost", None)
        guide = getattr(self, "_record_drop_guide", None)
        self._record_drag_row = None
        self._record_drag_item = None
        self._record_drag_ghost = None
        self._record_drop_guide = None
        self._record_drag_grab = None
        for w in (ghost, guide):
            if w is None:
                continue
            try:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            except Exception:
                pass

    def _end_record_drag(self, row):
        dest = int(getattr(self, "_record_drag_dest", 0) or 0)
        moving_row = row if row is not None else getattr(self, "_record_drag_row", None)
        item = getattr(self, "_record_drag_item", None) or self._item_for_record_row(moving_row)
        self._cleanup_record_drag_visual()
        self._record_drag_at = time.monotonic()
        recs = getattr(self, "records", None)
        if recs is None or item is None:
            return
        src = recs.row(item)
        if src < 0:
            return
        dest = max(0, min(dest, recs.count() - 1))
        if dest == src:
            recs.setCurrentItem(item)
            return
        w = recs.itemWidget(item)
        try:
            recs.removeItemWidget(item)
        except Exception:
            pass
        if w is not None:
            w.setParent(None)
        recs.takeItem(src)
        recs.insertItem(dest, item)
        if w is not None:
            recs.setItemWidget(item, w)
            if isinstance(w, _RecordRow):
                w._place_play_line()
        recs.setCurrentItem(item)
        self._save_history()

    def _add_voice(self, rec: dict, *, select: bool = True):
        rec = dict(rec or {})
        path = os.path.abspath(str(rec.get("path") or ""))
        if not path:
            return None
        rec["path"] = path
        rec["name"] = _display_name(rec)
        src = str(rec.get("src") or "")
        rec["dup"] = rec.get("dup") or _audio_dup_key(src) or _audio_dup_key(path)
        rec["color"] = _norm_hex(rec.get("color")) or self._alloc_voice_color()
        existed = self._find_card(path=path, src=src)
        if existed is not None:
            if not existed.color():
                existed.rec["color"] = rec["color"]
                self._style_voice_card(
                    existed,
                    selected=_norm_path(existed.path()) == _norm_path(self._prompt_wav),
                )
            if select:
                self._select_voice(existed.path())
            return existed
        card = _VoiceCard(rec, self.board.host, owner=self)
        card.selected.connect(self._on_card_selected)
        card.play_requested.connect(self._on_row_play)
        card.remove_requested.connect(self._remove_voice)
        self.board.flow().addWidget(card)
        self._voice_cards.append(card)
        self._style_voice_card(card, selected=False)
        self._sync_board()
        if select:
            self._select_voice(path)
        else:
            self._dirty()
        return card

    def _on_card_selected(self, path: str):
        if self._busy():
            return
        self._select_voice(path)

    def _clear_voice_sel(self):
        if self._busy():
            return
        if getattr(self, "_voice_drag", None) is not None:
            return
        if self._prompt_wav:
            self._select_voice("", dirty=True)

    def _remove_voice(self, path: str):
        if self._busy():
            return
        card = self._find_card(path=path)
        if card is None:
            return
        if _norm_path(self._playing_path) == _norm_path(path):
            self._stop_player()
        self.board.flow().removeWidget(card)
        self._voice_cards = [c for c in self._voice_cards if c is not card]
        card.setParent(None)
        card.deleteLater()
        if _norm_path(self._prompt_wav) == _norm_path(path):
            nxt = self._voice_cards[-1].path() if self._voice_cards else ""
            self._select_voice(nxt, dirty=False)
        self._sync_board()
        self._dirty()

    def _prune_voice_cards(self, *, save: bool):
        """只纠正当前选中：文件丢了就改选一张还在的，绝不自动拆卡。"""
        if not self._prompt_wav or os.path.isfile(self._prompt_wav):
            return
        nxt = ""
        for card in reversed(self._voice_cards):
            if os.path.isfile(card.path()):
                nxt = card.path()
                break
        self._select_voice(nxt, dirty=save)

    def _on_pick_wav(self):
        if self._busy():
            return
        start = os.path.dirname(self._prompt_wav) if self._prompt_wav else ""
        path, _ = QFileDialog.getOpenFileName(self, "选择原声音文件", start, AUDIO_FILTER)
        if path:
            self._on_upload_path(path)

    def _on_drop_paths(self, paths):
        last = ""
        for p in paths or []:
            dest = self._on_upload_path(p, select=False)
            if dest:
                last = dest
        if last:
            self._select_voice(last)

    def _set_drop_hint(self, text: str):
        lbl = getattr(self, "lbl_drop_hint", None)
        if lbl is None:
            return
        lbl.setText(text or "")
        lbl.setStyleSheet(TEXT_STYLE_T)

    def _on_upload_path(self, path: str, *, select: bool = True):
        if self._busy():
            return ""
        path = (path or "").strip()
        if not is_audio_file(path):
            self._warn("请拖入声音文件（wav / mp3 / flac / m4a / ogg）")
            return ""
        existed = self._find_card(src=path, path=path)
        if existed is not None:
            self._warn("这个声音已经在卡片里了")
            if select:
                self._select_voice(existed.path())
            return existed.path()
        try:
            dest, cropped = import_reference_audio(path)
        except Exception as e:
            self._warn(f"无法使用这个文件：{e}")
            return ""
        if cropped:
            self._set_drop_hint("已自动截取前 30 秒")
        name = os.path.splitext(os.path.basename(path))[0] or "声音"
        card = self._add_voice(
            {"path": dest, "name": name, "src": os.path.abspath(path)},
            select=select,
        )
        return card.path() if card is not None else dest

    # ── 生成 ────────────────────────────────────────────────────────────
    def _cached_prompt_text(self, wav: str) -> str:
        card = self._find_card(path=wav)
        if card is None:
            return ""
        rec = card.rec or {}
        text = str(rec.get("prompt_text") or "").strip()
        sig = str(rec.get("prompt_sig") or "")
        if text and sig and sig == _wav_sig(wav):
            return text
        return ""

    def _store_prompt_text(self, wav: str, text: str):
        text = (text or "").strip()
        if not wav or not text:
            return
        card = self._find_card(path=wav)
        if card is None:
            return
        rec = dict(card.rec or {})
        rec["prompt_text"] = text
        rec["prompt_sig"] = _wav_sig(wav)
        card.rec = rec
        self._dirty()

    def _on_synth_clicked(self):
        if self._state == "synth":
            self._cancel_synth()
            return
        if self._busy():
            return
        self._prune_voice_cards(save=True)
        wav = self._prompt_wav
        if not wav or not os.path.isfile(wav):
            self._warn("先把原声音文件拖进来")
            return
        dur = wav_duration_sec(wav)
        if dur and dur > 30.0:
            self._warn(
                f"参考音频太长（{dur:.1f} 秒）。\n\n"
                "CosyVoice 只支持 30 秒以内的原声，请先把原声裁剪到 30 秒以内"
                "再拖进来。"
            )
            return
        text = self.edit_tts.toPlainText().strip()
        if not text:
            self._warn("先写入要生成的内容")
            return
        model_dir = default_model_dir()
        st = weights_status(model_dir)
        if not st.get("has_weights"):
            self._warn("还没有模型权重。把 Fun-CosyVoice3-0.5B 放到 model/ 目录。")
            return
        if not st.get("has_source"):
            name = st.get("model_name") or "Fun-CosyVoice3-0.5B"
            self._warn(
                f"权重已经找到：{name}\n\n"
                "那是模型文件，还差官方推理源码 CosyVoice"
                "（代码很小，不是再下一份 9GB）。\n"
                "到「系统总览 → 语音克隆」看克隆命令。"
            )
            return
        prompt_text = self._cached_prompt_text(wav)
        if not prompt_text:
            try:
                import importlib.util
                has_w = importlib.util.find_spec("faster_whisper") is not None
            except Exception:
                has_w = False
            if not has_w:
                self._warn(
                    "零样本克隆要用 Whisper 识别原声在说什么。\n\n"
                    "未安装 faster-whisper。请先完全退出本程序，执行：\n"
                    "pip install faster-whisper numpy\n\n"
                    "装好后重新打开，再到「系统总览」检测语音组件。"
                )
                return
        card = self._find_card(path=wav)
        voice_name = _display_name(card.rec) if card is not None else ""
        out = new_out_wav_path(
            self._resolve_save_dir(),
            voice_name=voice_name,
            tts_text=text,
        )
        self._state = "synth"
        self._elapsed = 0
        self._elapsed_timer.start()
        self.btn_synth.setText("取消  00:00")
        self._set_busy_ui(True)
        self._worker = SynthWorker(
            model_dir=model_dir,
            prompt_wav=wav,
            tts_text=text,
            prompt_text=prompt_text,
            instruct="",
            speed=1.0,
            out_path=out,
            parent=self,
        )
        self._worker.status.connect(self._on_synth_status)
        self._worker.done.connect(self._on_synth_done)
        self._worker.fail.connect(self._on_synth_fail)
        self._worker.start()

    def _cancel_synth(self):
        w = self._worker
        if w is not None and hasattr(w, "stop"):
            try:
                w.stop()
            except Exception:
                pass

    def _tick_elapsed(self):
        self._elapsed += 1
        if self._state == "synth":
            m, s = divmod(self._elapsed, 60)
            self.btn_synth.setText(f"取消  {m:02d}:{s:02d}")

    def _on_synth_done(self, path: str, info: dict):
        self._elapsed_timer.stop()
        self._state = "idle"
        self._worker = None
        self._set_busy_ui(False)
        info = info or {}
        elapsed = info.get("elapsed")
        try:
            used = f"{float(elapsed):.1f} 秒"
        except (TypeError, ValueError):
            used = ""
        self._set_synth_led("ok", f"已完成{(' · ' + used) if used else ''}")
        asr = str(info.get("prompt_text") or "").strip()
        if asr:
            self._store_prompt_text(self._prompt_wav, asr)
            log.info(
                "语音克隆 mode=%s 原声文案=%s",
                info.get("mode") or "",
                asr[:80],
            )
        voice_path = self._prompt_wav or ""
        card = self._find_card(path=voice_path) if voice_path else None
        rec = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "text": self.edit_tts.toPlainText().strip(),
            "path": path,
            "ok": True,
            "voice_path": voice_path,
            "voice_color": card.color() if card is not None else "",
        }
        try:
            rec["dur"] = wav_duration_sec(path)
        except Exception:
            rec["dur"] = 0
        try:
            rec["size"] = os.path.getsize(path)
        except Exception:
            rec["size"] = 0
        self._add_record(rec, select=True)
        self._save_history()
        self._play_path(path)

    def _on_synth_fail(self, msg: str):
        self._elapsed_timer.stop()
        self._state = "idle"
        self._worker = None
        self._set_busy_ui(False)
        text = (msg or "").strip()
        if text == "已取消":
            self._set_synth_led("idle", "已取消")
            return
        self._set_synth_led("bad", text.splitlines()[0][:80] if text else "生成失败")
        message_box_info(
            self,
            "语音克隆",
            "生成失败：\n" + (text or "未知错误")[-600:],
            auto_close_sec=0,
        )

    # ── 记录 ────────────────────────────────────────────────────────────
    def _history_path(self) -> str:
        from utils.app_paths import voice_clone_history_path
        return voice_clone_history_path()

    def _record_subtitle(self, rec: dict) -> str:
        path = str(rec.get("path") or "")
        dur = rec.get("dur")
        try:
            dur_f = float(dur)
        except (TypeError, ValueError):
            dur_f = 0.0
        if dur_f <= 0 and path and os.path.isfile(path):
            dur_f = wav_duration_sec(path)
        size = rec.get("size")
        try:
            size_i = int(size)
        except (TypeError, ValueError):
            size_i = 0
        if size_i <= 0 and path and os.path.isfile(path):
            try:
                size_i = os.path.getsize(path)
            except Exception:
                size_i = 0
        parts = []
        d = _fmt_dur(dur_f)
        if d:
            parts.append(d)
        if size_i:
            parts.append(_fmt_size(size_i))
        clock = _clock_of(rec.get("ts") or "")
        if clock:
            parts.append(clock)
        return "  ·  ".join(parts) if parts else ""

    def _load_history(self):
        self.records.clear()
        path = self._history_path()
        if not os.path.isfile(path):
            return
        rows = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    fp = str(rec.get("path") or "")
                    if not fp or not os.path.isfile(fp):
                        continue
                    rows.append(rec)
        except Exception:
            log.exception("读取语音克隆记录失败")
            return
        pruned = rows[-_HIST_MAX:]
        for rec in pruned:
            self._add_record(rec, select=False)
        # 启动/进入页面时只滚到底部，不选中任何记录
        if self.records.count():
            self.records.scrollToBottom()
        self._save_history()

    def _save_history(self):
        rows = []
        for i in range(self.records.count()):
            rec = self.records.item(i).data(Qt.UserRole)
            if not isinstance(rec, dict):
                continue
            fp = str(rec.get("path") or "")
            if fp and os.path.isfile(fp):
                rows.append(rec)
        rows = rows[-_HIST_MAX:]
        path = self._history_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                for rec in rows:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            log.exception("保存语音克隆记录失败")

    def _record_voice_color(self, rec: dict) -> str:
        rec = rec or {}
        c = _norm_hex(rec.get("voice_color"))
        if c:
            return c
        path = str(rec.get("voice_path") or "")
        if path:
            card = self._find_card(path=path)
            if card is not None:
                return card.color()
        return ""

    def _add_record(self, rec: dict, *, select: bool):
        rec = dict(rec or {})
        fp = str(rec.get("path") or "")
        if not fp or not os.path.isfile(fp):
            return
        text = " ".join(str(rec.get("text") or "").split())
        title = text or os.path.basename(fp) or "（无文字）"
        item = QListWidgetItem()
        item.setData(Qt.UserRole, rec)
        item.setSizeHint(QSize(0, _ROW_H))
        self.records.addItem(item)
        row = _RecordRow(
            title,
            self._record_subtitle(rec),
            owner=self,
            voice_color=self._record_voice_color(rec),
        )
        row.play_requested.connect(lambda p=fp: self._on_row_play(p))
        row.delete_requested.connect(lambda p=fp: self._delete_record(p))
        self.records.setItemWidget(item, row)
        while self.records.count() > _HIST_MAX:
            self.records.takeItem(0)
        if select:
            self.records.setCurrentItem(item)
            self.records.scrollToItem(item)

    def _find_record_item(self, path: str):
        want = _norm_path(path)
        if not want:
            return None
        for i in range(self.records.count()):
            it = self.records.item(i)
            rec = it.data(Qt.UserRole) if it is not None else None
            if isinstance(rec, dict) and _norm_path(str(rec.get("path") or "")) == want:
                return it
        return None

    def _delete_record(self, path: str):
        path = (path or "").strip()
        if not path:
            return
        name = os.path.basename(path)
        if os.path.isfile(path):
            r = QMessageBox.question(
                self,
                "删除生成结果",
                f"确定删除这条生成结果吗？\n\n{name}\n\n此操作不可恢复。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if r != QMessageBox.Yes:
                return
            if _norm_path(getattr(self._player, "path", "") or "") == _norm_path(path):
                self._stop_player()
            try:
                os.remove(path)
            except Exception as e:
                self._warn(f"删除失败：{e}")
                return
        it = self._find_record_item(path)
        if it is not None:
            self.records.takeItem(self.records.row(it))
        self._save_history()

    def _refresh_lists(self):
        self._prune_voice_cards(save=True)
        self._refresh_card_states()
        self._load_history()

    def apply_save_path(self, path: str):
        self._save_path = (path or "").strip()

    def _resolve_save_dir(self) -> str:
        """生成结果放到公共保存根下的 VoiceClone/，不进 data/。"""
        p = (self._save_path or "").strip()
        if p:
            try:
                os.makedirs(p, exist_ok=True)
                return p
            except Exception:
                log.exception("创建语音克隆保存目录失败 path=%s", p)
        try:
            from utils.app_paths import user_downloads_dir
            from utils.user_prefs import load_user_prefs
            prefs, _ = load_user_prefs()
            base = (prefs.get("save_path_base") or "").strip()
            if not base:
                base = user_downloads_dir() or os.path.expanduser("~/Downloads")
            p = os.path.join(base, "VoiceClone")
            os.makedirs(p, exist_ok=True)
            self._save_path = p
            return p
        except Exception:
            log.exception("解析语音克隆公共目录失败")
        p = os.path.join(os.path.expanduser("~/Downloads"), "VoiceClone")
        try:
            os.makedirs(p, exist_ok=True)
        except Exception:
            pass
        return p

    def _open_save_dir(self):
        folder = self._resolve_save_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    # ── 播放 ────────────────────────────────────────────────────────────
    def _set_playing(self, path: str):
        self._playing_path = path or ""
        want = _norm_path(self._playing_path)
        for card in self._voice_cards:
            on = bool(want) and _norm_path(card.path()) == want
            card.set_playing(on)
            if not on:
                card.set_play_progress(None)
        recs = getattr(self, "records", None)
        if recs is not None:
            for i in range(recs.count()):
                w = recs.itemWidget(recs.item(i))
                if not isinstance(w, _RecordRow):
                    continue
                rec = recs.item(i).data(Qt.UserRole) or {}
                path = str(rec.get("path") or "")
                on = bool(want) and bool(path) and _norm_path(path) == want
                w.set_playing(on)
                if not on:
                    w.set_play_progress(None)

    def _iter_record_rows(self):
        recs = getattr(self, "records", None)
        if recs is None:
            return
        for i in range(recs.count()):
            it = recs.item(i)
            w = recs.itemWidget(it) if it is not None else None
            if not isinstance(w, _RecordRow):
                continue
            rec = it.data(Qt.UserRole) or {}
            yield w, str(rec.get("path") or "")

    def _stop_play_progress(self):
        try:
            self._play_prog_timer.stop()
        except Exception:
            pass
        self._play_t0 = 0.0
        self._play_dur = 0.0
        for card in self._voice_cards:
            card.set_play_progress(None)
        for row, _ in self._iter_record_rows():
            row.set_play_progress(None)

    def _tick_play_progress(self):
        want = _norm_path(self._playing_path)
        if not want or self._play_dur <= 0:
            self._stop_play_progress()
            return
        pct = (time.monotonic() - self._play_t0) / self._play_dur * 100.0
        pct = max(0.0, min(100.0, pct))
        for card in self._voice_cards:
            if _norm_path(card.path()) == want:
                card.set_play_progress(pct)
            else:
                card.set_play_progress(None)
        for row, path in self._iter_record_rows():
            if _norm_path(path) == want:
                row.set_play_progress(pct)
            else:
                row.set_play_progress(None)
        if pct >= 100.0:
            self._play_prog_timer.stop()

    def _start_play_progress(self, path: str):
        self._stop_play_progress()
        dur = wav_duration_sec(path)
        if dur <= 0:
            return
        self._play_t0 = time.monotonic()
        self._play_dur = dur
        want = _norm_path(path)
        for card in self._voice_cards:
            if _norm_path(card.path()) == want:
                card.set_play_progress(0)
        for row, rp in self._iter_record_rows():
            if _norm_path(rp) == want:
                row.set_play_progress(0)
        self._play_prog_timer.start()

    def _stop_player(self, *, wait: bool = False):
        p = self._player
        self._player = None
        self._stop_play_progress()
        self._set_playing("")
        if p is None:
            return
        try:
            if hasattr(p, "stop"):
                p.stop()
        except Exception:
            pass
        if not wait:
            return
        try:
            from utils.qthread_util import stop_qthread
            stop_qthread(p, name="voice-clone-play", timeout_ms=400)
        except Exception:
            pass

    def _on_play_finished(self, worker=None):
        if worker is not None and self._player is not None and worker is not self._player:
            return
        if self._player is worker:
            self._player = None
        self._stop_play_progress()
        self._set_playing("")

    def _on_play_fail(self, msg: str, path: str, worker=None):
        if worker is not None and self._player is not None and worker is not self._player:
            return
        self._on_play_finished(worker)
        if path and os.path.isfile(path):
            self._open_with_system(path)

    def _on_record_item_clicked(self, item):
        if getattr(self, "_record_drag_row", None) is not None:
            return
        if time.monotonic() - float(getattr(self, "_record_drag_at", 0) or 0) < 0.3:
            return
        rec = item.data(Qt.UserRole) if item is not None else None
        path = str((rec or {}).get("path") or "")
        if path:
            self._on_row_play(path)

    def _on_row_play(self, path: str):
        now = time.monotonic()
        if now - float(self._play_click_at or 0) < 0.18:
            return
        self._play_click_at = now
        if path and self._playing_path and _norm_path(self._playing_path) == _norm_path(path):
            self._stop_player()
            return
        self._play_path(path)

    def _play_path(self, path: str):
        if not path or not os.path.isfile(path):
            self._warn("没有可播放的文件")
            return
        it = self._find_record_item(path)
        if it is not None:
            self.records.setCurrentItem(it)
        self._stop_player()
        if os.path.splitext(path)[1].lower() == ".wav":
            try:
                import importlib.util
                if importlib.util.find_spec("sounddevice") is not None:
                    worker = PlayWavWorker(path, parent=self)
                    self._player = worker
                    worker.fail.connect(lambda m, p=path, w=worker: self._on_play_fail(m, p, w))
                    worker.finished.connect(lambda w=worker: self._on_play_finished(w))
                    self._set_playing(path)
                    self._start_play_progress(path)
                    worker.start()
                    return
            except Exception:
                pass
        self._open_with_system(path)

    def _open_with_system(self, path: str):
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception:
            try:
                os.startfile(path)  # type: ignore[attr-defined]
            except Exception as e:
                self._warn(f"无法播放：{e}")

    # ── 偏好 ────────────────────────────────────────────────────────────
    def export_settings(self) -> dict:
        return {
            "prompt_wav": self._prompt_wav or "",
            "prompt_wavs": self._export_voice_recs(),
            "tts_text": self.edit_tts.toPlainText(),
            "save_path": self._save_path or "",
        }

    def apply_settings(self, data: dict):
        self._loading = True
        filled_color = False
        try:
            data = data or {}
            save_path = str(data.get("save_path") or "").strip()
            if save_path:
                self.apply_save_path(save_path)
            tts = data.get("tts_text")
            if isinstance(tts, str) and tts:
                self.edit_tts.setPlainText(tts)

            for card in list(self._voice_cards):
                self.board.flow().removeWidget(card)
                card.setParent(None)
                card.deleteLater()
            self._voice_cards.clear()
            self._prompt_wav = ""

            rows = data.get("prompt_wavs")
            if not isinstance(rows, list):
                rows = []
            filled_color = False
            for rec in rows:
                if isinstance(rec, dict):
                    if not _norm_hex(rec.get("color")):
                        filled_color = True
                    self._add_voice(rec, select=False)
                elif isinstance(rec, str):
                    filled_color = True
                    self._add_voice({"path": rec}, select=False)
            wav = str(data.get("prompt_wav") or "").strip()
            if wav and os.path.isfile(wav) and self._find_card(path=wav) is None:
                filled_color = True
                self._add_voice({"path": wav}, select=False)
            self._select_voice("", dirty=False)
            self._sync_board()
            QTimer.singleShot(0, self._relayout_voice_cards)
        finally:
            self._loading = False
        if filled_color:
            self._dirty()

    def shutdown(self):
        try:
            self._elapsed_timer.stop()
        except Exception:
            pass
        w = self._worker
        if w is not None:
            try:
                if hasattr(w, "stop"):
                    w.stop()
            except Exception:
                pass
            try:
                from utils.qthread_util import stop_qthread
                stop_qthread(w, name="voice-clone")
            except Exception:
                pass
        self._stop_player()
        self._state = "idle"
