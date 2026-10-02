# pages/page_voice_clone.py
# 语音克隆（CosyVoice3）：三个分区竖向叠放 —— 声音卡 / 文案+生成 / 生成记录
# v9.18 起由「音频处理 · CosVoice3」页签单独承载（pages/page_cosvoice3.py），
# 不再内嵌在语音录入页里；子类补了自带「操作记录」区与页签背景。

from __future__ import annotations

import hashlib
import os
import random
import re
import time
from datetime import datetime

from PyQt5.QtCore import Qt, QTimer, QSize, QUrl, QRect, QRectF, QPoint, QPointF, QEvent, pyqtSignal
from PyQt5.QtGui import (
    QDesktopServices, QDragEnterEvent, QDropEvent, QColor, QPainter,
    QPen, QBrush, QPainterPath, QPixmap, QFont, QIcon, QCursor,
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
    apply_primary_button,
    fit_primary_button,
    apply_mini_button,
    apply_simple_record,
    CARD_LEFT_GAP,
    CARD_TOP_GAP,
    CARD_RIGHT_GAP,
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
    audio_duration_sec,
    load_wav_float32,
    AUDIO_FILTER,
    is_audio_file,
    prepare_reference_audio,
    work_dir,
)
from utils.flow_layout import FlowLayout
from utils.logger import get_logger

log = get_logger(__name__)

_HIST_MAX = 80
# 共享区里声音卡带子的固定高度（「音频处理」页签上方，左 70% 那一列）
# 右边 30% 是两页共用的记录面板，所以这个高度同时决定记录面板能有多高
_SHARED_CARD_H = 220
# VoiceClone 里这些是素材/临时文件，不算生成成品
_CLONE_OUT_SKIP = ("upload_", "crop_", "voice_", "in_", "ref_")


def _is_clone_output_name(name: str) -> bool:
    base = os.path.basename(name or "")
    if not base or base.startswith("."):
        return False
    ext = os.path.splitext(base)[1].lower()
    if ext not in (".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac"):
        return False
    low = base.lower()
    return not any(low.startswith(p) for p in _CLONE_OUT_SKIP)


def _wav_header_dur(path: str) -> float:
    try:
        import wave
        with wave.open(path, "rb") as w:
            n = int(w.getnframes() or 0)
            r = int(w.getframerate() or 0)
        if n > 0 and r > 0:
            return float(n) / float(r)
    except Exception:
        pass
    return 0.0
_VOICE_CARD_W = 60   # 原 72，减 12px
# 原 54（名字只放得下 1 行）；加一行汉字的高度，名字改成 2 行
_VOICE_CARD_H = 70
_VOICE_CARD_ICON_BTN = 24
_VOICE_CARD_ICON_PX = 18
# 名字区：2 行 × 每行 3 个汉字 = 6 个汉字，多出来的切掉
_VOICE_CARD_NAME_LINES = 2
_VOICE_CARD_NAME_PER_LINE = 3
# 卡片之间的缝隙，和卡内四边内边距一致（4px）
_CARD_GAP = 4
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
    """按宽度折行；超出行数时末行右侧截断。

    声音卡另走 _fit_voice_name：每行固定 3 个汉字、2 行共 6 个。
    """
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


def _fit_voice_name(text: str, fm, width: int) -> str:
    """声音卡名字：2 行、每行 3 个汉字（合计 6 个），多的右侧切掉。

    每行按 3 个汉字的宽度折，这样不管卡宽窄、字号大小，格数都是稳定的 3+3。
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    try:
        han = int(fm.horizontalAdvance("汉"))
    except Exception:
        han = int(fm.width("汉")) or 12
    han = max(6, han)
    per = max(1, int(_VOICE_CARD_NAME_PER_LINE))
    lines = int(_VOICE_CARD_NAME_LINES)
    keep = per * lines
    if len(raw) > keep:
        raw = raw[:keep - 1] + "…"      # 7 个字以上：第 7 个位置换成省略号
    return "\n".join(raw[i:i + per] for i in range(0, len(raw), per))


def _synth_level(state: str) -> str:
    """生成状态 → 记录面板的分级颜色。"""
    return {"ok": "ok", "bad": "err", "busy": "warn"}.get(state, "info")


def _voice_match_key(s: str) -> str:
    """把卡片名 / VoiceEdit 文件名收成可比较的键（标点都当 _）。"""
    from utils.voice_input import _safe_name
    base = os.path.splitext(os.path.basename(s or ""))[0].strip()
    base = re.sub(r"^(in|voice)_\d{8}_\d{6}_", "", base, flags=re.I)
    # 找回误拷曾写成 voice_时间戳_upload_时间戳，剥掉后不再当素材名
    base = re.sub(r"^upload_\d{8}_\d{6}(?:_\d+)?_?", "", base, flags=re.I)
    return _safe_name(base).lower()


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


def _url_to_local_path(url) -> str:
    if url is None:
        return ""
    p = ""
    try:
        p = url.toLocalFile() if hasattr(url, "toLocalFile") else ""
    except Exception:
        p = ""
    p = (p or "").strip()
    if p:
        try:
            return os.path.abspath(p)
        except Exception:
            return p
    try:
        s = url.toString() if hasattr(url, "toString") else str(url)
    except Exception:
        s = str(url or "")
    s = (s or "").strip()
    if s.lower().startswith("file:"):
        try:
            p = QUrl(s).toLocalFile()
        except Exception:
            p = ""
        p = (p or "").strip()
        if p:
            try:
                return os.path.abspath(p)
            except Exception:
                return p
    return ""


def _audio_paths_from_urls(urls) -> list:
    out = []
    seen = set()
    for url in urls or []:
        p = _url_to_local_path(url)
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
    """播放 / 暂停 / 磁盘 / 删除 线框图标。"""
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
    elif kind == "disk":
        pen = QPen(c, max(1.15, s * 0.09))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(s * 0.20, s * 0.18, s * 0.60, s * 0.64), s * 0.07, s * 0.07)
        p.setBrush(QBrush(c))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(s * 0.32, s * 0.18, s * 0.36, s * 0.16), 1.1, 1.1)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(s * 0.30, s * 0.46, s * 0.40, s * 0.26), 1.0, 1.0)
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
    """拖拽插入线：竖虚线叠在两张卡中间的缝上，不占布局。"""

    W = 8
    PEN = 4  # 原 2px，再粗 2px

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
        pen = QPen(color, self.PEN, Qt.CustomDashLine)
        pen.setDashPattern([3, 3])
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        x = self.width() / 2.0
        p.drawLine(QPointF(x, 4), QPointF(x, max(5, self.height() - 4)))
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
    """绿色播放进度，从左到右。未播放时隐藏。"""

    H = 1
    COLOR = QColor("#22c55e")

    def __init__(self, parent=None, *, reserve: bool = False):
        super().__init__(parent)
        self.setFixedHeight(self.H)
        self._pct = 0.0
        self._reserve = bool(reserve)
        self.setVisible(True if self._reserve else False)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_progress(self, pct):
        if pct is None or pct < 0:
            self._pct = 0.0
            if not self._reserve:
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
    """提示词内容卡同款：标题居中；底边磁盘标记/删除图标；可拖动排序。"""

    selected = pyqtSignal(str)
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

        self.btn_disk = _icon_btn(
            "disk", "#93c5fd", "已保存在 VoiceEdit",
            btn_size=_VOICE_CARD_ICON_BTN, icon_size=_VOICE_CARD_ICON_PX,
        )
        self.btn_disk.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.btn_disk.setCursor(Qt.ArrowCursor)
        self.btn_disk.setFocusPolicy(Qt.NoFocus)
        self.btn_del = _icon_btn(
            "trash", "#F87171", "删除",
            btn_size=_VOICE_CARD_ICON_BTN, icon_size=_VOICE_CARD_ICON_PX,
        )
        self.btn_del.clicked.connect(lambda: self.remove_requested.emit(self.path()))

        lay = QVBoxLayout(self)
        # 四边统一 4px（原来下边是 2px，卡片看起来上重下轻）
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(0)
        lay.addWidget(self._lab, 1)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        row.addWidget(self.btn_disk, 0, Qt.AlignLeft)
        row.addStretch(1)
        row.addWidget(self.btn_del, 0, Qt.AlignRight)
        lay.addLayout(row)
        self.apply_title()

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
        fm = self._lab.fontMetrics()
        inner_w = max(8, _VOICE_CARD_W - 8)
        self._lab.setText(_fit_voice_name(title, fm, inner_w))
        self.refresh_disk_icon()

    def refresh_disk_icon(self):
        """文件在 VoiceEdit 里才显示磁盘标记，否则藏掉。"""
        btn = getattr(self, "btn_disk", None)
        if btn is None:
            return
        show = False
        owner = self._owner
        if not self.is_missing() and owner is not None and hasattr(owner, "_in_voice_edit"):
            try:
                show = bool(owner._in_voice_edit(self.path()))
            except Exception:
                show = False
        btn.setVisible(show)
        if show:
            btn.setIcon(_tool_icon("disk", self._ink(), _VOICE_CARD_ICON_PX))

    def set_playing(self, playing: bool):
        self._playing = bool(playing) and not self._missing
        ink = self._ink()
        self.refresh_disk_icon()
        try:
            self.btn_del.setIcon(_tool_icon("trash", ink, _VOICE_CARD_ICON_PX))
        except Exception:
            pass
        if not self._playing:
            self.set_play_progress(None)

    def set_play_progress(self, pct):
        del pct  # 声音卡进度改在波形下方

    def _hit_action_btn(self, pos) -> bool:
        w = self.childAt(pos)
        while w is not None and w is not self:
            if w is getattr(self, "btn_del", None):
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
        # 卡与卡之间的缝隙 = 卡内四边内边距 = 4px
        self.flow = FlowLayout(self, margin=0, h_spacing=_CARD_GAP, v_spacing=_CARD_GAP)

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
        self.setMinimumHeight(_VOICE_CARD_H)
        self._hover = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
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
            vw = max(1, int(self.width()))
        cols = max(1, (vw + _CARD_GAP) // (_VOICE_CARD_W + _CARD_GAP))
        rows = max(1, (n + cols - 1) // cols) if n else 0
        h = 0
        try:
            h = int(self.host.heightForWidth(vw))
        except Exception:
            h = 0
        # 页面还没显示时 FlowLayout 会把隐藏项当空，heightForWidth≈1，这里按卡数兜底
        if n and h < _VOICE_CARD_H:
            h = rows * _VOICE_CARD_H + max(0, rows - 1) * _CARD_GAP
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
        except Exception:
            dim = "#94a3b8"
        # 虚线框改 paintEvent 叠画，stylesheet 永远 border:none，避免拖入时挤布局
        self.setStyleSheet(
            f"QFrame#VoiceCloneBoard{{background:transparent;color:{dim};"
            f"border:none;}}"
            f"QLabel#VoiceCloneBoardEmpty{{background:transparent;color:{dim};"
            f"border:none;font-size:14px;}}"
        )
        self.update()

    def _set_drop_hover(self, on: bool):
        on = bool(on)
        if self._hover == on:
            return
        self._hover = on
        self.update()

    def _drop_cursor_inside(self) -> bool:
        """子控件之间切换也会冒 DragLeave，用光标判断是否还在板内。"""
        try:
            return self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        except Exception:
            return False

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self._hover:
            return
        try:
            acc = QColor(tk("accent"))
        except Exception:
            acc = QColor("#3b82f6")
        if not acc.isValid():
            acc = QColor("#3b82f6")
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(acc, 1, Qt.DashLine)
        pen.setDashPattern([4, 3])
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.drawRoundedRect(r, 8, 8)
        p.end()

    def dragEnterEvent(self, e: QDragEnterEvent):
        md = e.mimeData() if e is not None else None
        if _mime_looks_like_files(md) or _audio_paths_from_urls(
            md.urls() if md is not None and md.hasUrls() else []
        ):
            self._set_drop_hover(True)
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        md = e.mimeData() if e is not None else None
        if _mime_looks_like_files(md) or _audio_paths_from_urls(
            md.urls() if md is not None and md.hasUrls() else []
        ):
            self._set_drop_hover(True)
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        if not self._drop_cursor_inside():
            self._set_drop_hover(False)
        super().dragLeaveEvent(e)

    def dropEvent(self, e: QDropEvent):
        self._set_drop_hover(False)
        md = e.mimeData() if e is not None else None
        paths = _audio_paths_from_urls(md.urls() if md is not None and md.hasUrls() else None)
        if paths:
            self.files_dropped.emit(paths)
            e.acceptProposedAction()
        elif _mime_looks_like_files(md):
            self.files_dropped.emit([])
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

        lay = QHBoxLayout(self)
        # 结果区要求上下左右贴边，内边距由列表统一给 0
        lay.setContentsMargins(0, 0, 0, 0)
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


def _fmt_mmss(sec: float) -> str:
    """秒 → mm:ss。超过 99 分就自然写成 100:05，不截断。"""
    try:
        sec = max(0.0, float(sec))
    except (TypeError, ValueError):
        sec = 0.0
    m, s = divmod(int(round(sec)), 60)
    return f"{m:02d}:{s:02d}"


class _PlayTimeLabel(QLabel):
    """波形右侧的播放时间：00:03/00:45，左当前、右总长。"""

    # 11 字符（00:00/00:00）按 12px 等宽约 79px，留到 13 字符（100:00/100:00）
    # 也不被裁。这里写死宽度而不是量 fontMetrics：无字体的环境量出来是垃圾值。
    W = 96

    def __init__(self, parent=None):
        super().__init__("00:00/00:00", parent)
        self.setObjectName("CosPlayTime")
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        f = QFont(self.font())
        f.setPixelSize(12)
        f.setStyleHint(QFont.Monospace)
        f.setFixedPitch(True)          # 等宽，秒数跳动时不会左右抖
        self.setFont(f)
        self.setFixedWidth(self.W)

    def set_times(self, cur: float, total: float) -> None:
        self.setText(f"{_fmt_mmss(cur)}/{_fmt_mmss(total)}")


class _PlayWaveform(QWidget):
    """就绪左侧：播放有声音时跳动的波形条。"""

    BARS = 18
    W = 84
    H = 22

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.W, self.H)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._active = False
        self._prog = None  # None=不画底边进度；0–100 画 1px 绿线
        self._env = []
        self._levels = [0.08] * self.BARS
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)

    def set_progress(self, pct):
        """波形底边 1px 进度；None 表示隐藏，不改控件尺寸。"""
        if pct is None or pct < 0:
            self._prog = None
            self.update()
            return
        try:
            self._prog = max(0.0, min(100.0, float(pct)))
        except (TypeError, ValueError):
            self._prog = 0.0
        self.update()

    def start(self, path: str = ""):
        self._env = self._load_env(path)
        self._active = True
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def stop(self):
        self._active = False
        self._prog = None
        self._timer.stop()
        self._env = []
        self._levels = [0.08] * self.BARS
        self.update()

    @staticmethod
    def _load_env(path: str):
        path = (path or "").strip()
        if not path or not os.path.isfile(path):
            return []
        try:
            audio, _sr = load_wav_float32(path)
        except Exception:
            return []
        if audio is None:
            return []
        try:
            samples = audio.reshape(-1)
            n = int(getattr(samples, "size", len(samples)))
        except Exception:
            return []
        if n < 16:
            return []
        bins = 64
        step = max(1, n // bins)
        env = []
        for i in range(bins):
            chunk = samples[i * step:(i + 1) * step]
            try:
                v = float(abs(chunk).mean())
            except Exception:
                v = 0.0
            env.append(v)
        mx = max(env) or 1.0
        return [min(1.0, (v / mx) ** 0.55) for v in env]

    def _tick(self):
        import math
        n = self.BARS
        t = time.monotonic()
        env = self._env
        levels = []
        for i in range(n):
            if env:
                pos = (t * 14.0 + i * 1.2) % len(env)
                i0 = int(pos) % len(env)
                i1 = (i0 + 1) % len(env)
                frac = pos - int(pos)
                v = env[i0] * (1.0 - frac) + env[i1] * frac
                v = 0.10 + 0.90 * v
            else:
                v = 0.12 + 0.88 * abs(math.sin(t * 7.2 + i * 0.52)) * (
                    0.40 + 0.60 * abs(math.sin(t * 3.4 + i * 0.29))
                )
            prev = self._levels[i] if i < len(self._levels) else 0.1
            v = prev * 0.32 + v * 0.68
            levels.append(max(0.06, min(1.0, v)))
        self._levels = levels
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        n = self.BARS
        gap = 1.6
        bar_w = max(1.4, (w - gap * (n - 1)) / n)
        try:
            col = QColor(tk("accent"))
        except Exception:
            col = QColor("#3b82f6")
        if not col.isValid():
            col = QColor("#3b82f6")
        col.setAlpha(210 if self._active else 80)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(col))
        mid = h / 2.0
        for i, lv in enumerate(self._levels):
            bh = max(2.0, (h - 2.0) * float(lv))
            x = i * (bar_w + gap)
            p.drawRoundedRect(QRectF(x, mid - bh / 2.0, bar_w, bh), 1.0, 1.0)
        if self._prog is not None:
            pw = int(round(self.width() * self._prog / 100.0))
            if pw > 0:
                p.fillRect(0, max(0, self.height() - 1), pw, 1, QColor("#22c55e"))
        p.end()

    def hideEvent(self, e):
        if self._timer.isActive():
            self._timer.stop()
        super().hideEvent(e)

    def showEvent(self, e):
        if self._active and not self._timer.isActive():
            self._timer.start()
        super().showEvent(e)


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

    def set_captions(self, captions: dict):
        """换掉圆灯右侧的文案。

        「音频处理」页的共享状态灯会按当前页签切换：AUK 显示推理文案，
        CosVoice3 显示生成文案。
        """
        if isinstance(captions, dict) and captions:
            self._CAPTION = {
                k: v for k, v in captions.items() if k in self._lamp._COLORS
            }
        self._apply_caption(self._state)

    def _apply_caption(self, state: str):
        text, color = self._CAPTION.get(state, self._CAPTION["bad"])
        self._txt.setText(text)
        self._txt.setStyleSheet(
            f"QLabel{{color:{color};background:transparent;font-size:12px;}}"
        )


class PageVoiceClone(QWidget):
    # ===== 窗口高度 BUG 防护（与截图/速存/时区汇率页同一套路）=====
    # FlowLayout / wordWrap 会让整页 hasHeightForWidth()=True，Qt 拖窗口时抬高推荐高度。
    # 切断整页 HFW；内部滚动/换行照常。
    def hasHeightForWidth(self):
        return False


    """三个分区竖向叠放。"""

    def __init__(self, parent=None, shared_host=None, result_host=None,
                 card_host=None, log_host=None, shared_log=None):
        super().__init__(parent)
        # 「音频处理」页把各区块的落脚点传进来；None = 本页自己摆
        #   shared_host —— 播放行（[▶] 波形 状态灯 提示 处理 打开文件夹）
        #   card_host   —— 声音卡带（与记录并排，容器给 70% 宽）
        #   result_host —— 结果卡（容器右 40%，两页共用）
        #   log_host    —— 记录面板（容器声音卡右边 30%，与本页共用）
        self._shared_host = shared_host
        self._card_host = card_host
        self._result_host = result_host
        self._log_host = log_host
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._state = "idle"  # idle | synth
        self._prefs_cb = None
        self._op_log_cb = None
        self._prompt_wav = ""
        self._rec_play_path = ""
        self._clear_rec_sel_cb = None
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
        self._play_total = 0.0   # 时间标签右边那个「总长」
        self._play_prog_timer = QTimer(self)
        self._play_prog_timer.setInterval(50)
        self._play_prog_timer.timeout.connect(self._tick_play_progress)
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._theme_titles = []
        self._func_cards = []
        self._prefs_cb = None
        self._led_changed_cb = None
        self._voice_changed_cb = None
        self._shared_log = shared_log   # 「音频处理」页的共用记录面板（构造时传入）
        # 声音卡是否还在本页：进共享区后为 None，高度计算不再参与
        self._col_voice = None
        # 生成状态灯的最新状态。「音频处理」页按当前页签决定显示哪一套文案
        self._led_state = "idle"
        # 子类（PageCosVoice3）在底部挂「操作记录」区时，把那一行设进来，
        # 文案/记录两栏的高度计算要把它的高度先扣掉，否则会把整页顶出可视区。
        self._extra_log_row = None

        self._build_ui()
        theme.changed.connect(self.refresh_theme)
        # 此时还在堆栈里、宽度为 0，立刻灌 QListWidget 会让记录行变成 0 宽看不见
        self._refresh_synth_enabled()

    def minimumSizeHint(self):
        return QSize(0, 0)

    def sizeHint(self):
        return QSize(900, 640)

    def set_prefs_dirty_callback(self, fn):
        self._prefs_cb = fn

    def set_op_log_callback(self, fn):
        """日志去处。PageCosVoice3 注入自己的「操作记录」列表。"""
        self._op_log_cb = fn

    def set_clear_rec_sel_callback(self, fn):
        """播放时若声音卡优先，用来清掉外部转写记录的选中。

        v9.18 起本页已独立成「音频处理 · CosVoice3」页签，不再接语音录入页，
        这里保留仅为兼容旧调用方。
        """
        self._clear_rec_sel_cb = fn

    def _op_log(self, text: str, replace_prefix: str = "", level: str = "info"):
        """操作记录。有共用面板时写进去，否则交给注入的回调。"""
        text = (text or "").strip()
        if not text:
            return
        panel = getattr(self, "_shared_log", None)
        if panel is not None:
            try:
                panel.append(text, level=level, replace_prefix=replace_prefix)
                return
            except Exception:
                log.exception("写共用记录面板失败")
        fn = getattr(self, "_op_log_cb", None)
        if callable(fn):
            try:
                fn(text, replace_prefix=replace_prefix)
            except TypeError:
                try:
                    fn(text)
                except Exception:
                    log.exception("语音克隆操作记录回写失败")
            except Exception:
                log.exception("语音克隆操作记录回写失败")

    def set_shared_log(self, panel):
        """注入「音频处理」页的共用记录面板（AUK 也往这里写）。"""
        self._shared_log = panel

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
        # 根布局间距对齐 AUK 页（8）
        root.setSpacing(8)

        # 声音卡 + 播放行归「音频处理」页共享，落在两个页签上方。
        # card_host 单独给（容器里它与记录面板并排），没有就退回 shared_host。
        shared = self._shared_host
        shared_lay = None
        if shared is not None:
            shared_lay = shared.layout()
            if shared_lay is None:
                shared_lay = QVBoxLayout(shared)
                shared_lay.setContentsMargins(0, 0, 0, 0)
                shared_lay.setSpacing(6)
        card_host = self._card_host if self._card_host is not None else shared
        card_lay = None
        if card_host is not None:
            card_lay = card_host.layout()
            if card_lay is None:
                card_lay = QVBoxLayout(card_host)
                card_lay.setContentsMargins(0, 0, 0, 0)
                card_lay.setSpacing(0)

        # 结果卡：result_host 为 None（单独 new 本页调试）时留在本页右列
        result_host = self._result_host

        # 声音卡
        card_voice = make_card("CardVoiceCloneRef", borderless=True)
        self._func_cards.append(card_voice)
        lv = QVBoxLayout(card_voice)
        lv.setContentsMargins(4, 0, 4, 4)
        lv.setSpacing(0)
        self.board = _VoiceBoard()
        self.board.files_dropped.connect(self._on_drop_paths)
        self.board.blank_clicked.connect(self._clear_voice_sel)
        try:
            self.board._bind_drop_proxy(card_voice)
        except Exception:
            pass
        lv.addWidget(self.board, 1)
        card_voice.setFixedHeight(_SHARED_CARD_H)
        self.lbl_drop_hint = QLabel("")
        self.lbl_drop_hint.hide()

        # 播放行：播放按钮 + 波形 + 状态灯 …… 右端＝提示 +「生成」按钮
        play_row = QWidget()
        play_row.setObjectName("CosSharedPlayRow")
        play_row.setAttribute(Qt.WA_StyledBackground, True)
        play_row.setStyleSheet("#CosSharedPlayRow{background:transparent;border:none;}")
        go = QHBoxLayout(play_row)
        go.setContentsMargins(2, 0, 2, 0)
        go.setSpacing(8)
        go.setAlignment(Qt.AlignVCenter)
        # 「声音卡改名」在「播放」左边
        self.btn_rename = apply_mini_button(QPushButton("声音卡改名"))
        self.btn_rename.setCursor(Qt.PointingHandCursor)
        self.btn_rename.setFixedHeight(22)
        self.btn_rename.setToolTip("改选中声音卡显示的汉字名（卡片上显示 6 个字）")
        self.btn_rename.clicked.connect(self._on_rename_voice)
        go.addWidget(self.btn_rename, 0, Qt.AlignVCenter)
        self.btn_wave_play = _icon_btn(
            "play", "#94a3b8", "先选一张声音卡",
            btn_size=22, icon_size=16, wash=True,
        )
        self.btn_wave_play.clicked.connect(self._on_wave_play)
        go.addWidget(self.btn_wave_play, 0, Qt.AlignVCenter)
        self.wave_meter = _PlayWaveform()
        go.addWidget(self.wave_meter, 0, Qt.AlignVCenter)
        # 波形右边跟一个「当前/总长」，播放时逐秒走
        self.lbl_play_time = _PlayTimeLabel()
        go.addWidget(self.lbl_play_time, 0, Qt.AlignVCenter)
        go.addStretch(1)
        # 右端槽：状态灯 · 提示 ·「处理」· 打开文件夹，全部靠右
        right_box = QWidget()
        right_box.setObjectName("CosSharedPlayRight")
        right_box.setAttribute(Qt.WA_StyledBackground, True)
        right_box.setStyleSheet("#CosSharedPlayRight{background:transparent;border:none;}")
        rg = QHBoxLayout(right_box)
        rg.setContentsMargins(0, 0, 0, 0)
        rg.setSpacing(10)
        rg.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # 槽内顺序：省显存（AUK 页专用）· 提示 · 处理 · 打开文件夹
        # 省显存由容器插到最前面，这里只放提示。
        self.lbl_load_hint = QLabel("可拖文件到上面")
        self.lbl_load_hint.setCursor(Qt.ArrowCursor)
        self.lbl_load_hint.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        rg.addWidget(self.lbl_load_hint, 0, Qt.AlignRight | Qt.AlignVCenter)
        go.addWidget(right_box, 0, Qt.AlignRight | Qt.AlignVCenter)
        # 状态灯排在 right_box 之后：容器会往槽里塞省显存/处理/打开文件夹，
        # 放进槽里就排不到最右了，放外层才能真正贴着行尾右对齐。
        self.status_led = _CloneStatusLed()
        go.addWidget(self.status_led, 0, Qt.AlignRight | Qt.AlignVCenter)
        self._play_row = play_row
        self._play_row_right_box = right_box
        self._play_row_right_lay = rg

        if shared_lay is not None:
            # 播放行整行保持在共享头底部
            shared_lay.addWidget(play_row, 0)
        if card_lay is not None:
            card_lay.addWidget(card_voice, 0)
            self._col_voice = None          # 声音卡不在本页，高度不参与计算
        else:
            root.addWidget(play_row, 0)
            root.addWidget(card_voice, 0)
            self._col_voice = card_voice

        card_text = make_card("CardVoiceCloneText", borderless=True)
        self._func_cards.append(card_text)
        cv2 = QVBoxLayout(card_text)
        # 内边距对齐 AUK 页的「结果」大面板（CARD_*_GAP + spacing 6）
        cv2.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        cv2.setSpacing(6)

        # 「生成」按钮不放在页内：交给「音频处理」容器摆到页签条右侧
        self.btn_synth = QPushButton("生成")
        apply_primary_button(self.btn_synth)
        fit_primary_button(self.btn_synth)
        self.btn_synth.clicked.connect(self._on_synth_clicked)
        self._corner_btn = self.btn_synth
        self._style_load_hint()
        self.edit_tts = QTextEdit()
        self.edit_tts.setObjectName("PasteContentEdit")
        self.edit_tts.setAcceptRichText(False)
        self.edit_tts.setPlaceholderText("文案")
        self.edit_tts.setMinimumHeight(72)
        self.edit_tts.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._style_tts_edit()
        self.edit_tts.textChanged.connect(self._refresh_synth_enabled)
        cv2.addWidget(self.edit_tts, 1)

        # 左列（编辑区；子类再往左下追加「操作记录」）
        col_left = QWidget()
        col_left.setObjectName("CosColLeft")
        col_left.setAttribute(Qt.WA_StyledBackground, True)
        col_left.setStyleSheet("#CosColLeft{background:transparent;border:none;}")
        col_left_lay = QVBoxLayout(col_left)
        col_left_lay.setContentsMargins(0, 0, 0, 0)
        col_left_lay.setSpacing(8)      # 对齐 AUK 页左列
        col_left_lay.addWidget(card_text, 1)

        card3 = make_card("CardVoiceCloneHist", borderless=True)
        self._func_cards.append(card3)
        cv3 = QVBoxLayout(card3)
        # 记录列表上下左右都不留空
        cv3.setContentsMargins(0, 0, 0, 0)
        cv3.setSpacing(0)
        self._hist_card = card3

        self.records = _RecordList()
        self.records.setObjectName("VoiceCloneRecords")
        self.records.setSelectionMode(QAbstractItemView.SingleSelection)
        self.records.setUniformItemSizes(True)
        self.records.setSpacing(0)
        self.records.setMouseTracking(True)
        apply_simple_record(self.records)
        self._style_result_list()
        self.records.itemClicked.connect(self._on_record_item_clicked)
        self.records.blank_clicked.connect(self._clear_record_sel)
        cv3.addWidget(self.records, 1)
        self._hist_blank_watch = {card3}
        for w in self._hist_blank_watch:
            w.installEventFilter(self)

        # 「打开文件夹」不占结果区，交给容器摆到共享播放行「处理」右边
        self.btn_reveal = apply_mini_button(QPushButton("打开文件夹"))
        self.btn_reveal.setCursor(Qt.PointingHandCursor)
        self.btn_reveal.setFixedHeight(22)
        self.btn_reveal.clicked.connect(self._open_save_dir)

        # 结果卡：外挂到容器右 40% 时不进本页布局，本页变单列
        result_lay = None
        if result_host is not None:
            result_lay = result_host.layout()
            if result_lay is None:
                result_lay = QVBoxLayout(result_host)
                result_lay.setContentsMargins(0, 0, 0, 0)
                result_lay.setSpacing(0)
        if result_lay is not None:
            result_lay.addWidget(card3, 1)
            self._col_right = None
            root.addWidget(col_left, 1)
        else:
            cols = QHBoxLayout()
            cols.setContentsMargins(0, 0, 0, 0)
            cols.setSpacing(8)          # 对齐 AUK 页根
            col_right = QWidget()
            col_right.setObjectName("CosColRight")
            col_right.setAttribute(Qt.WA_StyledBackground, True)
            col_right.setStyleSheet("#CosColRight{background:transparent;border:none;}")
            col_right_lay = QVBoxLayout(col_right)
            col_right_lay.setContentsMargins(0, 0, 0, 0)
            col_right_lay.setSpacing(0)
            col_right_lay.addWidget(card3, 1)
            cols.addWidget(col_left, 1)
            cols.addWidget(col_right, 1)
            self._col_right = col_right
            self._cols_layout = cols
            root.addLayout(cols, 1)
        self._col_left = col_left
        self._col_left_lay = col_left_lay

        self._col_text = card_text
        self._col_hist = card3
        self._col_layout_busy = False
        if self._col_voice is not None:
            root.addWidget(card_voice, 0)
        self._refresh_wave_play_btn()
        QTimer.singleShot(0, self._apply_column_heights)

    def _style_result_list(self):
        """结果记录区：上下左右不留空，隐藏竖向滚动条（滚轮/触控板照常滚）。

        全局 recordStyle="simple" 会画一条顶部虚线并留 padding；结果卡本身已经是
        无框的（borderless），那条虚线多余，四边 padding 也让记录看着内缩。
        这里按 #VoiceCloneRecords 覆盖掉——只改外观，滚动能力不动。
        """
        lst = getattr(self, "records", None)
        if lst is None:
            return
        try:
            lst.setFrameShape(QFrame.NoFrame)
        except Exception:
            pass
        try:
            lst.setContentsMargins(0, 0, 0, 0)
        except Exception:
            pass
        try:
            # AlwaysOff 只是不画滚动条，滚轮/触控板/翻页仍由 QAbstractScrollArea 处理
            lst.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            lst.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        except Exception:
            pass
        # 记录行自带 4px 内边距，一并去掉，上下左右正好贴边
        for i in range(lst.count()):
            w = lst.itemWidget(lst.item(i))
            if isinstance(w, _RecordRow):
                try:
                    lay = w.layout()
                    if lay is not None:
                        lay.setContentsMargins(0, 0, 0, 0)
                except Exception:
                    pass

    def process_button(self):
        """本页的「生成」按钮（蓝底白字）。容器把它摆到共享播放行右端。"""
        return getattr(self, "_corner_btn", None)

    def _on_rename_voice(self):
        """改选中声音卡的汉字名。名字只影响卡片显示，不动磁盘上的音频。"""
        path = self._prompt_wav or ""
        if not path:
            self._op_log("先选一张声音卡")
            return
        card = self._find_card(path=path)
        if card is None:
            self._op_log("这张声音卡找不到了")
            return
        cur = str((card.rec or {}).get("name") or "").strip()
        try:
            from PyQt5.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(
                self, "声音卡改名", "显示的名字（最多 6 个字）：", text=cur
            )
        except Exception:
            log.exception("声音卡改名对话框失败")
            return
        if not ok:
            return
        name = " ".join((name or "").split())[:12]
        if not name:
            return
        self.rename_selected_voice(name)

    def rename_selected_voice(self, name: str) -> bool:
        """把选中声音卡的 rec["name"] 改成 name 并落盘。"""
        path = self._prompt_wav or ""
        if not path:
            return False
        card = self._find_card(path=path)
        if card is None:
            return False
        rec = dict(card.rec or {})
        rec["name"] = " ".join((name or "").split())
        card.rec = rec
        card.apply_title()
        self._style_voice_card(card, selected=True)
        self._dirty()
        self._refresh_synth_enabled()
        self._notify_voice_changed()
        return True

    def play_row_right_layout(self):
        """共享播放行右端槽：提示后面就是「生成」按钮。"""
        return getattr(self, "_play_row_right_lay", None)

    def set_process_visible(self, visible: bool):
        btn = self.process_button()
        if btn is not None:
            btn.setVisible(bool(visible))

    def set_voice_changed_callback(self, fn):
        """选中声音卡变化时上报：容器据此刷新 AUK 页「生成」按钮的可用性。"""
        self._voice_changed_cb = fn

    def _notify_voice_changed(self):
        cb = getattr(self, "_voice_changed_cb", None)
        if callable(cb):
            try:
                cb(self._prompt_wav or "")
            except Exception:
                log.exception("声音卡选中变化回调失败")

    def eventFilter(self, obj, ev):
        if ev is not None and ev.type() == QEvent.MouseButtonPress:
            try:
                if ev.button() == Qt.LeftButton and obj in getattr(self, "_hist_blank_watch", ()):
                    self._clear_record_sel()
            except Exception:
                pass
        return super().eventFilter(obj, ev)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_column_heights()

    def _page_body_height(self) -> int:
        """两列各自能吃满的整列高度（两列都拉满页高）。

        根布局里除最后那个拉伸项（cols）外都是固定高控件，逐个扣掉再算间距。
        不能统一减 spacing*(n-1)：只有一项时减不掉，否则整列短一截、被布局
        垂直居中，卡片就会比 AUK 页低 3px。
        """
        lay = self.layout()
        if lay is None:
            return 0
        m = lay.contentsMargins()
        avail = self.height() - m.top() - m.bottom()
        spacing = max(0, int(lay.spacing()))
        for i in range(max(0, lay.count() - 1)):
            it = lay.itemAt(i)
            w = it.widget() if it is not None else None
            if w is None:
                continue
            avail -= int(w.sizeHint().height()) + spacing
        return avail

    def _apply_column_heights(self):
        """两列布局：右列「结果」整列吃满；左列由子类（要放「操作记录」）自己分。

        结果卡外挂到容器（右 40%）时本页是单列，右列不参与。
        """
        if getattr(self, "_col_layout_busy", False):
            return
        text = getattr(self, "_col_text", None)
        total = self._page_body_height()
        if total < 120:
            return
        self._col_layout_busy = True
        try:
            if getattr(self, "_col_right", None) is not None:
                hist = getattr(self, "_col_hist", None)
                if hist is not None and (
                    hist.minimumHeight() != total or hist.maximumHeight() != total
                ):
                    hist.setFixedHeight(total)
            # 左列没有额外区块时（或记录区已提到共享面板），文案区吃满整列
            if (getattr(self, "_extra_log_row", None) is None
                    or getattr(self, "_log_external", False)) and text is not None:
                if text.minimumHeight() != total or text.maximumHeight() != total:
                    text.setFixedHeight(total)
        finally:
            self._col_layout_busy = False

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_column_heights()
        self._refresh_card_states()
        if self.records.count() == 0:
            self._load_history()
        QTimer.singleShot(0, self._relayout_voice_cards)
        QTimer.singleShot(0, self._relayout_history_rows)

    def on_enter(self):
        self._prune_voice_cards(save=True)
        self._refresh_card_states()
        self._load_history()
        self._refresh_synth_enabled()
        QTimer.singleShot(0, self._relayout_voice_cards)
        QTimer.singleShot(0, self._relayout_history_rows)

    def _refresh_card_states(self):
        """启动/刷新/进入页面时重查每张声音卡：文件找回就恢复彩色，丢失就变灰。"""
        changed = False
        for card in self._voice_cards:
            if card.is_missing():
                newp = self._recover_voice_path(card)
                if newp:
                    rec = dict(card.rec or {})
                    rec["path"] = os.path.abspath(newp)
                    rec["dup"] = _audio_dup_key(newp) or rec.get("dup") or ""
                    card.rec = rec
                    changed = True
                    log.info("已找回声音卡 %s", _display_name(rec))
        if self._prompt_wav and not os.path.isfile(self._prompt_wav):
            self._prompt_wav = ""
        want = _norm_path(self._prompt_wav)
        for card in self._voice_cards:
            self._style_voice_card(card, selected=bool(want) and _norm_path(card.path()) == want)
        self._refresh_wave_play_btn()
        if changed:
            self._dirty()

    def _relayout_voice_cards(self):
        board = getattr(self, "board", None)
        if board is None:
            return
        board.set_has_cards(bool(self._voice_cards))
        board.fit_host()
        board.update()

    def _style_tts_edit(self):
        """文案编辑区：不要外框，直接融进面板；只留文字与选区颜色。"""
        edit = getattr(self, "edit_tts", None)
        if edit is None:
            return
        try:
            dark = bool(theme.is_dark)
        except Exception:
            dark = True
        if dark:
            fg, caret = "#e8eaf0", "#93c5fd"
        else:
            fg, caret = "#1f2937", "#2563eb"
        edit.setStyleSheet(
            f"QTextEdit#PasteContentEdit{{background:transparent;border:none;"
            f"padding:2px;color:{fg};font-size:13px;caret-color:{caret};"
            "selection-background-color:#1f3a8a;}}"
        )
        try:
            edit.document().setDocumentMargin(0)
        except Exception:
            pass
        try:
            edit.setViewportMargins(0, 0, 0, 0)
        except Exception:
            pass

    def _style_load_hint(self, *_):
        """提示「可拖文件到上面」：跟随主题的次要文字色。"""
        lbl = getattr(self, "lbl_load_hint", None)
        if lbl is None:
            return
        try:
            color = tk("text_mut")
        except Exception:
            color = "#9fb0d7"
        try:
            lbl.setStyleSheet(
                f"color:{color};background:transparent;border:none;font-size:12px;"
            )
        except Exception:
            pass

    def refresh_theme(self, *_):
        for t in self._theme_titles:
            restyle_card_title(t)
        for c in self._func_cards:
            restyle_card_frame(c)
        self._style_tts_edit()
        self._style_load_hint()
        if getattr(self, "lbl_drop_hint", None) is not None:
            self.lbl_drop_hint.setStyleSheet(TEXT_STYLE_T)
        led = getattr(self, "status_led", None)
        if led is not None:
            # 只重画，不改文案：文案归「音频处理」页按页签切
            led._lamp.set_state(getattr(led, "_state", "idle") or "idle")
        board = getattr(self, "board", None)
        if board is not None:
            board.refresh_theme()
        for card in self._voice_cards:
            self._style_voice_card(card, selected=_norm_path(card.path()) == _norm_path(self._prompt_wav))
        try:
            apply_simple_record(self.records)
            self._style_result_list()
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
        self._refresh_wave_play_btn()

    def _busy(self) -> bool:
        return self._state != "idle"

    def _set_busy_ui(self, busy: bool):
        self.board.setEnabled(not busy)
        self.board.setAcceptDrops(not busy)
        self.board.setCursor(Qt.ArrowCursor)
        self.edit_tts.setReadOnly(busy)
        if busy:
            self._set_synth_led("busy", "准备中")
        else:
            self.btn_synth.setText("生成")
            self._fit_synth_btn_width()
        self._refresh_synth_enabled()

    def _fit_synth_btn_width(self):
        fit_primary_button(getattr(self, "btn_synth", None))

    def _set_synth_led(self, state: str, detail: str = ""):
        led = getattr(self, "status_led", None)
        self._led_state = state
        if led is not None:
            led.set_state(state)
        detail = (detail or "").strip()
        if detail:
            self._op_log(detail, level=_synth_level(state))
        cb = getattr(self, "_led_changed_cb", None)
        if callable(cb):
            try:
                cb(state)
            except Exception:
                log.exception("语音克隆状态灯回调失败")

    # ── 供「音频处理」页使用 ───────────────────────────────────────────
    def set_led_changed_callback(self, fn):
        """状态变化上报：容器按当前页签决定共享状态灯显示哪套文案。"""
        self._led_changed_cb = fn

    def led_state(self) -> str:
        return getattr(self, "_led_state", "idle") or "idle"

    def selected_voice_path(self) -> str:
        """当前选中的声音卡路径。共享给 AUK 页签当输入音频。"""
        return self._prompt_wav or ""

    def selected_voice_name(self) -> str:
        """当前选中的声音卡显示名（可能被改过）。"""
        path = self._prompt_wav or ""
        card = self._find_card(path=path) if path else None
        if card is not None:
            rec = card.rec or {}
            name = str(rec.get("name") or "").strip()
            if name:
                return name
        return os.path.splitext(os.path.basename(path))[0] if path else "未选声音"

    def selected_voice_source_stem(self) -> str:
        """选中声音卡的**原文件名**（不含扩展名）。

        卡片可以用「声音卡改名」改显示名，但 rec["src"] 仍记着拖进来时那个原文件，
        AUK 生成的文件名用它，产出才追得到来源。
        """
        path = self._prompt_wav or ""
        card = self._find_card(path=path) if path else None
        if card is None:
            return ""
        rec = card.rec or {}
        for key in ("src", "path"):
            raw = str(rec.get(key) or "").strip()
            if raw:
                stem = os.path.splitext(os.path.basename(raw))[0].strip()
                if stem:
                    return stem
        return ""

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
            # 声音文件丢失：灰色底 + 弱描边，不显示磁盘标记但仍可删除
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

    def _find_missing_card_by_name(self, name: str):
        """灰色（文件丢失）声音卡：按展示名 / 原文件名匹配，便于拖回同名素材恢复。"""
        want = os.path.splitext(os.path.basename(name or ""))[0].strip().lower()
        if not want or want == "声音":
            return None
        for card in self._voice_cards:
            if not card.is_missing():
                continue
            rec = card.rec or {}
            title = _display_name(rec).strip().lower()
            if title == want:
                return card
            for p in (rec.get("src"), rec.get("path"), rec.get("name")):
                stem = os.path.splitext(os.path.basename(str(p or "")))[0].strip().lower()
                if stem and stem == want:
                    return card
        return None

    def _restore_voice_card(self, card, dest: str, src: str):
        rec = dict(card.rec or {})
        rec["path"] = os.path.abspath(dest)
        rec["src"] = os.path.abspath(src) if src else rec.get("src") or ""
        rec["dup"] = _audio_dup_key(dest) or _audio_dup_key(src) or str(rec.get("dup") or "")
        rec["name"] = rec.get("name") or _display_name(rec)
        rec["prompt_text"] = ""
        rec["prompt_sig"] = ""
        card.rec = rec
        card.apply_title()
        selected = _norm_path(card.path()) == _norm_path(self._prompt_wav)
        self._style_voice_card(card, selected=selected)
        self._dirty()
        return card.path()

    def _find_voice_edit_material(self, name: str) -> str:
        """在 VoiceEdit 里按规范化文件名找回 in_/voice_ 素材。"""
        key = _voice_match_key(name)
        if not key or key == "audio":
            return ""
        try:
            from utils.voice_input import work_dir as ve_dir
            folder = ve_dir()
            names = os.listdir(folder)
        except Exception:
            return ""
        hits = []
        for fn in names:
            low = fn.lower()
            if not (low.startswith("voice_") or low.startswith("in_")):
                continue
            # 忽略找回时误生成的 voice_*_upload_* 派生文件
            if re.search(r"_upload_\d{8}_", fn, re.I):
                continue
            fk = _voice_match_key(fn)
            if not fk or fk == "audio":
                continue
            if fk == key:
                hits.append(os.path.join(folder, fn))
        wavs = [p for p in hits if p.lower().endswith(".wav") and os.path.isfile(p)]
        if wavs:
            return wavs[0]
        for p in hits:
            if os.path.isfile(p):
                return p
        return ""

    def _persist_ref_wav(self, wav_path: str) -> str:
        """把处理好的参考 wav 落到 VoiceEdit，避免只活在 Downloads\\VoiceClone。"""
        ve = self._copy_material_to_voice_edit(wav_path)
        return ve if ve else wav_path

    def _recover_voice_path(self, card) -> str:
        """只认已经存在的文件，绝不拷贝、转码、另存。"""
        rec = card.rec or {}
        src = str(rec.get("src") or "").strip()
        name = _display_name(rec)
        cands = [
            self._find_voice_edit_material(name),
            self._find_voice_edit_material(src),
            src,
        ]
        seen = set()
        for cand in cands:
            cand = (cand or "").strip()
            if not cand or cand in seen:
                continue
            seen.add(cand)
            if os.path.isfile(cand) and is_audio_file(cand):
                return os.path.abspath(cand)
        return ""

    def _in_voice_edit(self, path: str) -> bool:
        path = (path or "").strip()
        if not path:
            return False
        try:
            from utils.voice_input import work_dir as ve_dir
            root = os.path.normcase(os.path.abspath(ve_dir()))
            ap = os.path.normcase(os.path.abspath(path))
            return ap == root or ap.startswith(root + os.sep)
        except Exception:
            return False

    def _copy_material_to_voice_edit(self, path: str) -> str:
        """声音素材复制进 VoiceEdit；失败返回空串。"""
        path = (path or "").strip()
        if not path or not os.path.isfile(path):
            return ""
        try:
            from utils.voice_input import copy_input_audio, work_dir as ve_dir
            dest = copy_input_audio(path, prefix="voice") or ""
            if not dest or not os.path.isfile(dest):
                return ""
            root = os.path.normcase(os.path.abspath(ve_dir()))
            ap = os.path.normcase(os.path.abspath(dest))
            if ap == root or ap.startswith(root + os.sep):
                return dest
        except Exception:
            log.exception("复制声音素材到 VoiceEdit 失败")
        return ""

    def _ve_log(self, action: str, *paths, extra: str = ""):
        from utils.voice_input import ve_op_msg
        self._op_log(ve_op_msg(action, *paths, extra=extra))

    def _prepare_copied_material(self, ve: str):
        """对新拷进 VoiceEdit 的文件转 wav / 裁 30 秒；结果必须仍落在 VoiceEdit。"""
        dest, cropped = prepare_reference_audio(ve)
        if dest and dest != ve and os.path.isfile(dest):
            self._ve_log("转码", ve, dest)
            try:
                os.remove(ve)
                self._ve_log("删除", ve)
            except Exception:
                pass
        dest = dest or ve
        if cropped:
            self._ve_log("裁剪", dest, extra="前 30 秒")
        if dest and not self._in_voice_edit(dest):
            persisted = self._persist_ref_wav(dest)
            if persisted:
                self._ve_log("复制", persisted)
                dest = persisted
        if not dest or not os.path.isfile(dest):
            return "", False
        return dest, bool(cropped)

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
        self._refresh_synth_enabled()
        self._refresh_wave_play_btn()
        # 换卡就换总长，播放中则不打断（正在播的那份说了算）
        if not self._playing_path:
            self._set_play_total_for(path)
        self._notify_voice_changed()

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
        gap = 6  # 与声音卡 FlowLayout 水平间距一致
        dest = max(0, min(int(dest), len(others)))
        if dest >= len(others):
            slot = others[-1]
            r = QRect(slot.mapTo(board, QPoint(0, 0)), slot.size())
            x = r.right() + gap // 2
            y = r.y()
            h = r.height()
        elif dest <= 0:
            slot = others[0]
            r = QRect(slot.mapTo(board, QPoint(0, 0)), slot.size())
            x = r.left() - gap // 2
            y = r.y()
            h = r.height()
        else:
            prev = others[dest - 1]
            slot = others[dest]
            pr = QRect(prev.mapTo(board, QPoint(0, 0)), prev.size())
            r = QRect(slot.mapTo(board, QPoint(0, 0)), slot.size())
            same_row = abs(pr.center().y() - r.center().y()) <= max(pr.height(), r.height()) // 2
            if same_row:
                x = (pr.right() + r.left()) // 2
                y = min(pr.y(), r.y())
                h = max(pr.height(), r.height())
            else:
                x = r.left() - gap // 2
                y = r.y()
                h = r.height()
        h = max(8, h)
        guide.setFixedHeight(h)
        guide.move(x - guide.width() // 2, y)
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
        card.remove_requested.connect(self._remove_voice)
        try:
            self.board._bind_drop_proxy(card)
        except Exception:
            pass
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
        path, _ = QFileDialog.getOpenFileName(self, "打开文件", start, AUDIO_FILTER)
        if path:
            self._on_upload_path(path)

    def _on_drop_paths(self, paths):
        paths = list(paths or [])
        if not paths:
            self._op_log("加载失败 · 请拖入声音文件")
            return
        last = ""
        for p in paths:
            dest = self._on_upload_path(p, select=False, silent=True)
            if dest:
                last = dest
        if last:
            self._select_voice(last)

    def _on_upload_path(self, path: str, *, select: bool = True, silent: bool = False):
        if self._busy():
            self._op_log("加载失败 · 正在生成，请稍后再试")
            return ""
        path = (path or "").strip()
        name = os.path.splitext(os.path.basename(path))[0] or "声音"
        if not is_audio_file(path):
            self._op_log(f"加载失败 · {os.path.basename(path) or name}（不是声音文件）")
            if not silent:
                self._warn("请拖入声音文件")
            return ""

        path = os.path.abspath(path)
        existed = self._find_card(src=path, path=path)
        if existed is None:
            existed = self._find_missing_card_by_name(name)
        # 卡还在、文件已在 VoiceEdit：不再拷一份
        if (
            existed is not None
            and not existed.is_missing()
            and self._in_voice_edit(existed.path())
        ):
            self._op_log(f"声音卡已存在 · {name}")
            if select:
                self._select_voice(existed.path())
            return existed.path()

        ve = ""
        made_copy = False
        recovered = False
        # 丢失卡：先按同名找回 VoiceEdit 里已有的 in_/voice_ 副本
        if existed is not None and existed.is_missing():
            ve = self._find_voice_edit_material(name) or self._find_voice_edit_material(path) or ""
            recovered = bool(ve)
        if not ve:
            if self._in_voice_edit(path) and os.path.isfile(path):
                ve = path
            else:
                ve = self._copy_material_to_voice_edit(path)
                made_copy = bool(ve) and os.path.normcase(os.path.abspath(ve)) != os.path.normcase(path)
        if recovered and ve:
            self._ve_log("找回", ve)
        elif made_copy and ve:
            self._ve_log("复制", ve)
        if not ve or not os.path.isfile(ve):
            self._op_log(f"加载失败 · {name}（未能写入 VoiceEdit）")
            if not silent:
                self._warn(f"未能把「{name}」复制到 VoiceEdit")
            return ""

        dest = ve
        cropped = False
        if made_copy:
            try:
                dest, cropped = self._prepare_copied_material(ve)
            except Exception as e:
                self._op_log(f"加载失败 · {name}（{e}）")
                if not silent:
                    self._warn(f"无法使用这个文件：{e}")
                return ""
        if not dest or not os.path.isfile(dest):
            self._op_log(f"加载失败 · {name}（无法导入）")
            return ""

        crop_note = "（已截取前 30 秒）" if cropped else ""
        if cropped:
            self._set_drop_hint("已截取前 30 秒")

        if existed is not None:
            was_missing = existed.is_missing()
            restored = self._restore_voice_card(existed, dest, path)
            if was_missing:
                self._op_log(f"已恢复声音卡 · {name}{crop_note}")
            else:
                self._op_log(f"已加载声音 · {name}{crop_note}")
            if select:
                self._select_voice(restored)
            return restored

        card = self._add_voice(
            {"path": dest, "name": name, "src": os.path.abspath(path)},
            select=select,
        )
        self._op_log(f"已加载声音 · {name}{crop_note}")
        return card.path() if card is not None else dest

    def _set_drop_hint(self, text: str):
        lbl = getattr(self, "lbl_drop_hint", None)
        if lbl is None:
            return
        lbl.setText(text or "")
        lbl.setStyleSheet(TEXT_STYLE_T)

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

    def _refresh_synth_enabled(self):
        """处理：仅当已选声音卡且文案非空时可用；生成中保持可点以便取消。

        条件不满足就是冻结（禁用）状态，鼠标停上去说明差什么。
        """
        btn = getattr(self, "btn_synth", None)
        if btn is None:
            return
        if self._state == "synth":
            btn.setEnabled(True)
            try:
                btn.setToolTip("点一下取消生成")
            except Exception:
                pass
            return
        has_voice = bool(self._prompt_wav and os.path.isfile(self._prompt_wav))
        edit = getattr(self, "edit_tts", None)
        has_text = bool(edit is not None and edit.toPlainText().strip())
        btn.setEnabled(has_voice and has_text)
        blockers = []
        if not has_voice:
            blockers.append("先在上方选一张声音卡")
        if not has_text:
            blockers.append("先写文案")
        try:
            btn.setToolTip("；".join(blockers))
        except Exception:
            pass

    def _on_synth_clicked(self):
        if self._state == "synth":
            self._cancel_synth()
            return
        if self._busy():
            return
        self._prune_voice_cards(save=True)
        wav = self._prompt_wav
        if not wav or not os.path.isfile(wav):
            self._warn("先选声音卡")
            return
        dur = wav_duration_sec(wav)
        if dur and dur > 30.0:
            self._warn(f"原声需 ≤30 秒（当前 {dur:.0f} 秒）")
            return
        text = self.edit_tts.toPlainText().strip()
        if not text:
            self._warn("先写文案")
            return
        model_dir = default_model_dir()
        st = weights_status(model_dir)
        if not st.get("has_weights"):
            self._warn("缺少模型权重（model/Fun-CosyVoice3-0.5B）")
            return
        if not st.get("has_source"):
            self._warn("缺少 CosyVoice 源码（系统总览 → 语音克隆）")
            return
        prompt_text = self._cached_prompt_text(wav)
        if not prompt_text:
            try:
                import importlib.util
                has_w = importlib.util.find_spec("faster_whisper") is not None
            except Exception:
                has_w = False
            if not has_w:
                self._warn("缺少 faster-whisper（系统总览 → 语音组件）")
                return
        card = self._find_card(path=wav)
        # 声音卡段：改名优先（_display_name 先看 rec["name"]），与 AUK 同一口径
        voice_name = _display_name(card.rec) if card is not None else ""
        if not voice_name:
            voice_name = os.path.splitext(os.path.basename(wav))[0]
        out = new_out_wav_path(
            self._resolve_save_dir(),
            voice_name=voice_name,
            tts_text=text,
        )
        self._state = "synth"
        self._elapsed = 0
        self._elapsed_timer.start()
        self.btn_synth.setText("取消")
        self._fit_synth_btn_width()
        # 精简：面板只留一条进度，原前缀与文案一致，后面的状态就原地替换它
        self._op_log(f"开始生成 · {voice_name}", replace_prefix="开始生成")
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
        # 不再每秒往记录面板刷秒数（原来 replace_prefix="生成中 " 每秒改一行）

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
        self._set_synth_led("ok", f"生成完成{(' · ' + used) if used else ''}")
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
            "text": os.path.splitext(os.path.basename(path))[0],
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
        self._play_path(path)

    def _on_synth_fail(self, msg: str):
        self._elapsed_timer.stop()
        self._state = "idle"
        self._worker = None
        self._set_busy_ui(False)
        text = (msg or "").strip()
        if text == "已取消":
            log.info("语音克隆 已取消")
            self._set_synth_led("idle", "已取消生成")
            return
        from utils.cosyvoice_clone import brief_clone_error
        brief = brief_clone_error(text)
        log.error("语音克隆失败 %s", text.replace("\n", " · ")[:2000])
        self._set_synth_led("bad", f"生成失败 · {brief}")

    # ── 记录 ────────────────────────────────────────────────────────────
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

    def _hist_row_width(self) -> int:
        recs = getattr(self, "records", None)
        if recs is None:
            return 160
        try:
            vw = int(recs.viewport().width() or 0)
        except Exception:
            vw = 0
        return max(160, vw)

    def _relayout_history_rows(self):
        recs = getattr(self, "records", None)
        if recs is None:
            return
        w = self._hist_row_width()
        for i in range(recs.count()):
            it = recs.item(i)
            if it is None:
                continue
            it.setSizeHint(QSize(w, _ROW_H))
            row = recs.itemWidget(it)
            if row is not None:
                try:
                    row.updateGeometry()
                except Exception:
                    pass

    def _load_history(self):
        """结果 = VoiceClone 目录里的成品 wav，不另写 jsonl。

        AUK 页签跑出来的成品也落在这个目录，靠 utils.auk_client 的任务索引
        把「任务名 + 素材名」还原成行首文字；索引里没有就退回文件名。
        """
        self.records.clear()
        folder = self._resolve_save_dir()
        try:
            names = os.listdir(folder)
        except Exception:
            log.exception("扫描 VoiceClone 失败 dir=%s", folder)
            return
        try:
            from utils.auk_client import task_titles
            titles = task_titles()
        except Exception:
            titles = {}
        rows = []
        for name in names:
            if not _is_clone_output_name(name):
                continue
            fp = os.path.normpath(os.path.join(folder, name))
            if not os.path.isfile(fp):
                continue
            try:
                st = os.stat(fp)
            except Exception:
                continue
            stem = os.path.splitext(name)[0]
            rec = {
                "path": fp,
                "text": titles.get(name.lower()) or stem,
                "ts": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "size": int(st.st_size),
                "dur": _wav_header_dur(fp),
                "voice_color": self._guess_output_voice_color(stem),
            }
            rows.append((st.st_mtime, rec))
        rows.sort(key=lambda x: x[0])
        for _, rec in rows[-_HIST_MAX:]:
            self._add_record(rec, select=False)
        if self.records.count():
            self.records.scrollToBottom()
        QTimer.singleShot(0, self._relayout_history_rows)

    def _guess_output_voice_color(self, stem: str) -> str:
        head = (stem or "").split("_")[0].strip()
        if not head:
            return ""
        for card in self._voice_cards:
            name = _display_name(card.rec or {})
            if not name:
                continue
            if name.startswith(head) or head.startswith(name[: min(len(name), len(head))]):
                c = card.color()
                if c:
                    return c
        return ""

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
        fp = os.path.normpath(str(rec.get("path") or "").strip())
        if not fp:
            return
        rec["path"] = fp
        existed = self._find_record_item(fp)
        if existed is not None:
            if select:
                self.records.setCurrentItem(existed)
                self.records.scrollToItem(existed)
            return
        missing = not os.path.isfile(fp)
        text = " ".join(str(rec.get("text") or "").split())
        title = text or os.path.basename(fp) or "（无文字）"
        if missing:
            title = f"{title}（丢失）"
        item = QListWidgetItem()
        item.setData(Qt.UserRole, rec)
        item.setSizeHint(QSize(self._hist_row_width(), _ROW_H))
        self.records.addItem(item)
        row = _RecordRow(
            title,
            self._record_subtitle(rec),
            owner=self,
            voice_color=self._record_voice_color(rec),
        )
        if missing:
            try:
                row.btn_play.setEnabled(False)
            except Exception:
                pass
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
                from utils.app_paths import is_other_user_profile_path
                if is_other_user_profile_path(p):
                    p = ""
                else:
                    os.makedirs(p, exist_ok=True)
                    return p
            except Exception:
                log.exception("创建语音克隆保存目录失败 path=%s", p)
        try:
            from utils.app_paths import public_save_base
            from utils.user_prefs import load_user_prefs
            prefs, _ = load_user_prefs()
            base = public_save_base(prefs)
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
    def start_wave(self, path: str = ""):
        w = getattr(self, "wave_meter", None)
        if w is not None:
            w.start(path or "")

    def stop_wave(self):
        if getattr(self, "_playing_path", ""):
            return
        w = getattr(self, "wave_meter", None)
        if w is not None:
            w.stop()

    def set_rec_play_path(self, path: str):
        """外部（语音录入页）选中的转写记录路径。

        v9.18 起两页不再联动，这里只保留接口；不调用时播放按钮走声音卡。
        """
        path = (path or "").strip()
        self._rec_play_path = path if path and os.path.isfile(path) else ""
        self._refresh_wave_play_btn()

    def _wave_target_path(self) -> str:
        """有声音卡就播声音卡，否则播选中的转写记录。"""
        voice = self._prompt_wav if (
            self._prompt_wav and os.path.isfile(self._prompt_wav)
        ) else ""
        rec = self._rec_play_path if (
            self._rec_play_path and os.path.isfile(self._rec_play_path)
        ) else ""
        return voice or rec or ""

    def _refresh_wave_play_btn(self):
        btn = getattr(self, "btn_wave_play", None)
        if btn is None:
            return
        playing = bool(self._playing_path)
        target = self._wave_target_path()
        armed = bool(target) or playing
        try:
            fill = tk("accent")
        except Exception:
            fill = "#3b82f6"
        if armed:
            ink = "#ffffff"
            _style_icon_btn(btn, fill=fill)
            btn.setEnabled(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip("停止" if playing else "播放")
        else:
            ink = "#94a3b8"
            btn.setEnabled(False)
            btn.setCursor(Qt.ArrowCursor)
            btn.setToolTip("先选一张声音卡")
            bg = "rgba(148,163,184,0.14)"
            btn.setStyleSheet(
                f"QPushButton{{background:{bg};border:none;padding:0;border-radius:6px;}}"
                "QPushButton:disabled{background:rgba(148,163,184,0.14);}"
            )
        btn.setIcon(_tool_icon("stop" if playing else "play", ink, 16))

    def _on_wave_play(self):
        if self._playing_path:
            self._stop_player()
            return
        voice = self._prompt_wav if (
            self._prompt_wav and os.path.isfile(self._prompt_wav)
        ) else ""
        rec = self._rec_play_path if (
            self._rec_play_path and os.path.isfile(self._rec_play_path)
        ) else ""
        if voice:
            if rec:
                fn = getattr(self, "_clear_rec_sel_cb", None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        pass
            self._on_row_play(voice)
            return
        if rec:
            self._on_row_play(rec)

    def _set_playing(self, path: str):
        self._playing_path = path or ""
        want = _norm_path(self._playing_path)
        w = getattr(self, "wave_meter", None)
        if w is not None:
            if self._playing_path:
                w.start(self._playing_path)
            else:
                w.stop()
        self._refresh_wave_play_btn()
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

    # ── 共用结果列表（AUK 页签也往这里放）─────────────────────────────
    def add_result(self, path: str, title: str = "") -> None:
        """外部（AUK 页签）把成品塞进这一个结果列表。

        列表顺序是「旧 → 新」，新结果追加到末尾并滚到底，和本页生成一致。
        title 为空时退回文件名。
        """
        fp = os.path.normpath((path or "").strip())
        if not fp:
            return
        stem = os.path.splitext(os.path.basename(fp))[0]
        rec = {
            "path": fp,
            "text": " ".join((title or "").split()) or stem,
        }
        try:
            st = os.stat(fp)
            rec["ts"] = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            rec["size"] = int(st.st_size)
            rec["dur"] = _wav_header_dur(fp)
        except Exception:
            pass
        self._add_record(rec, select=False)
        try:
            self.records.scrollToBottom()
        except Exception:
            pass
        QTimer.singleShot(0, self._relayout_history_rows)

    def highlight_result(self, path: str) -> None:
        """只点亮/熄灭某一行的高亮，不碰波形与播放器。

        AUG 结果行用 winsound 自己播，波形只跟声音卡走（见 v9.18 的约定）。
        """
        want = _norm_path(path)
        if not want:
            return
        for row, p in self._iter_record_rows():
            try:
                row.set_playing(bool(p) and _norm_path(p) == want)
                if not (bool(p) and _norm_path(p) == want):
                    row.set_play_progress(None)
            except Exception:
                pass

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

    def _set_wave_prog(self, pct):
        w = getattr(self, "wave_meter", None)
        if w is not None and hasattr(w, "set_progress"):
            w.set_progress(pct)

    def _set_play_time(self, cur: float = 0.0, total: float = None) -> None:
        """刷新「当前/总长」。total=None 表示沿用上一次的总长。"""
        if total is not None:
            self._play_total = max(0.0, float(total or 0.0))
        lbl = getattr(self, "lbl_play_time", None)
        if lbl is None:
            return
        try:
            lbl.set_times(cur, self._play_total)
        except Exception:
            log.exception("刷新播放时间失败")

    def _set_play_total_for(self, path: str) -> None:
        """按文件刷总长。只读文件头，选卡时调也不卡界面。"""
        dur = audio_duration_sec(path or "")
        self._set_play_time(0.0, dur)

    def _playing_is_hist_record(self) -> bool:
        want = _norm_path(self._playing_path)
        if not want:
            return False
        for _, path in self._iter_record_rows():
            if _norm_path(path) == want:
                return True
        return False

    def _stop_play_progress(self):
        try:
            self._play_prog_timer.stop()
        except Exception:
            pass
        self._play_t0 = 0.0
        self._play_dur = 0.0
        self._set_wave_prog(None)
        # 停了就回到 00:00，右边总长留着，用户能直接看到这段有多长
        self._set_play_time(0.0)
        for row, _ in self._iter_record_rows():
            row.set_play_progress(None)

    def _tick_play_progress(self):
        want = _norm_path(self._playing_path)
        if not want or self._play_dur <= 0:
            self._stop_play_progress()
            return
        elapsed = time.monotonic() - self._play_t0
        pct = elapsed / self._play_dur * 100.0
        pct = max(0.0, min(100.0, pct))
        self._set_play_time(min(elapsed, self._play_dur), None)
        if self._playing_is_hist_record():
            self._set_wave_prog(None)
            for row, path in self._iter_record_rows():
                row.set_play_progress(pct if _norm_path(path) == want else None)
        else:
            self._set_wave_prog(pct)
            for row, _ in self._iter_record_rows():
                row.set_play_progress(None)
        if pct >= 100.0:
            self._play_prog_timer.stop()

    def _start_play_progress(self, path: str):
        self._stop_play_progress()
        dur = wav_duration_sec(path)
        if dur <= 0:
            # 读不出时长也别让标签留着上一首的数字
            self._set_play_time(0.0, 0.0)
            return
        self._play_t0 = time.monotonic()
        self._play_dur = dur
        self._set_play_time(0.0, dur)
        want = _norm_path(path)
        if any(_norm_path(rp) == want for _, rp in self._iter_record_rows()):
            self._set_wave_prog(None)
            for row, rp in self._iter_record_rows():
                if _norm_path(rp) == want:
                    row.set_play_progress(0)
        else:
            self._set_wave_prog(0)
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
        # 不再自动弹系统播放器：那会抢走焦点、并且是单实例的（换一条就顶掉上一条）。
        # 只在操作记录里说一句，播放按钮恢复成可点。
        self._op_log(f"播放失败：{msg}")
        if path and os.path.isfile(path):
            self._set_playing("")

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
        # 不管什么格式都先试程序内播放（PlayWavWorker 现已支持 mp3/flac/ogg…）。
        # 以前只给 .wav 走这条路，其余全退到系统播放器——那个是单实例的，
        # 换一张卡再点就会把上一条顶掉，听起来像「点别的卡就中止」。
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
            log.exception("启动内置播放器失败")
        # 只有真的放不了才交给系统（并说明原因）
        self._op_log("这个格式程序内放不了，已用系统播放器打开")
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
        self._refresh_card_states()
        if filled_color:
            self._dirty()
        self._refresh_synth_enabled()

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
