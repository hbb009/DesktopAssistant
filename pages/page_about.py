# pages/page_about.py
# 组件安装 + 预下载域名白名单。独立「配置」页已取消，作为系统总览下半部分嵌入。

from __future__ import annotations

import os
import sys
from typing import Dict, List

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal, QRectF, QPointF, QSize, QEvent
from PyQt5.QtGui import (
    QPainter, QPen, QBrush, QColor, QPainterPath, QIcon, QPixmap, QFont,
)
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QScrollArea, QFrame, QSizePolicy, QDialog, QToolTip,
)

from styles.style_all import (
    make_card, install_card_title, restyle_card_title, theme, tk, sp,
    CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP,
    CARD_TITLE_FONT_SIZE, apply_mini_button, apply_medium_button, apply_live_button,
    message_box_info, message_box_warn, set_label_selectable,
)
from utils.flow_layout import FlowLayout
from utils.user_prefs import (
    default_clipboard_whitelist, default_prefs, normalize_whitelist_pattern,
    other_whitelist_patterns,
)
from utils.logger import get_logger

log = get_logger(__name__)

# 配置页字号：相对全站各 +2。只改这张表即可整页一起动。
_FS = {
    "title": 14,   # 卡片标题 12→14
    "body":  14,   # 正文 / 状态 / 清单 / 摘要 12→14
    "row":   15,   # 白名单行名 13→15
    "hint":  13,   # 次要提示 11→13
    "group": 13,   # 分组名 11→13
    "chip":  14,   # 域名胶囊 12→14
    "chip_x": 15,  # 胶囊关闭 13→15
}


def _lbl_qss(color: str, role: str = "body", *, weight: int = None, extra: str = "") -> str:
    size = _FS[role]
    w = f"font-weight:{int(weight)};" if weight else ""
    return (
        f"QLabel{{color:{color};font-size:{size}px;{w}"
        f"background:transparent;border:none;{extra}}}"
    )



def _make_category_sep(object_name: str = "AboutCategorySep", thickness: int = 1) -> QFrame:
    """分类之间横线（颜色随主题 border；默认 1px，白名单可用 3px）。"""
    th = max(1, int(thickness or 1))
    sep = QFrame()
    sep.setObjectName(object_name)
    sep.setProperty("sepThickness", th)
    sep.setFixedHeight(th)
    sep.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    try:
        c = tk("border")
    except Exception:
        c = "#25345c"
    sep.setStyleSheet(
        f"QFrame#{object_name}{{background:{c};border:none;min-height:{th}px;max-height:{th}px;}}"
    )
    return sep


def _reset_default_icon(size: int = 14, color: str = "#9fb0d7") -> "QIcon":
    """恢复默认：圆形箭头。"""
    from PyQt5.QtGui import QIcon, QPixmap, QPainter, QPen, QColor
    from PyQt5.QtCore import Qt, QRectF
    size = max(12, int(size))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = float(size)
    c = QColor(color)
    pen = QPen(c, max(1.4, s * 0.10))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    # 3/4 圆弧
    rect = QRectF(s * 0.18, s * 0.18, s * 0.64, s * 0.64)
    p.drawArc(rect, int(45 * 16), int(270 * 16))
    # 箭头三角
    from PyQt5.QtGui import QPolygonF, QBrush
    from PyQt5.QtCore import QPointF
    tip = QPointF(s * 0.72, s * 0.22)
    tri = QPolygonF([
        tip,
        QPointF(s * 0.52, s * 0.18),
        QPointF(s * 0.62, s * 0.38),
    ])
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(c))
    p.drawPolygon(tri)
    p.end()
    return QIcon(pm)


def _restyle_about_title(lbl: QLabel) -> None:
    """卡片标题走全站色，字号用配置页 +2。"""
    restyle_card_title(lbl)
    ss = lbl.styleSheet() or ""
    old = f"font-size:{sp(CARD_TITLE_FONT_SIZE)}px"
    new = f"font-size:{_FS['title']}px"
    if old in ss:
        lbl.setStyleSheet(ss.replace(old, new, 1))


# 白名单按下载子页：视频 4 + 音频 1 + 图集 3。汽水音乐单独一条。
_WHITELIST_GROUPS = (
    ("douyin", "视频下载", "抖音"),
    ("bilibili", "视频下载", "B站"),
    ("youtube", "视频下载", "YouTube"),
    ("other", "视频下载", "其它视频"),
    ("qishui", "音频下载", "汽水音乐"),
    ("gallery_eh", "图集下载", "e-hentai"),
    ("gallery_pixiv", "图集下载", "Pixiv"),
    ("gallery_hitomi", "图集下载", "hitomi.la"),
)


def _normalize_domain(raw: str) -> str:
    """允许 host 或 host/path（如 music.douyin.com/qishui）。"""
    return normalize_whitelist_pattern(raw)


class _DomainChip(QFrame):
    """可删除的域名胶囊。"""

    removed = pyqtSignal(str)

    def __init__(self, domain: str, parent=None):
        super().__init__(parent)
        self.domain = domain
        self.setObjectName("AboutDomainChip")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 2, 4, 2)
        lay.setSpacing(4)
        self.lbl = QLabel(domain)
        self.lbl.setObjectName("AboutDomainChipText")
        self.btn = QPushButton("×")
        self.btn.setObjectName("AboutDomainChipClose")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setFixedSize(20, 20)
        self.btn.setFlat(True)
        self.btn.clicked.connect(lambda: self.removed.emit(self.domain))
        lay.addWidget(self.lbl)
        lay.addWidget(self.btn)
        self._apply_style()

    def _apply_style(self):
        try:
            bg = tk("chip_bg") if False else (
                "rgba(59,130,246,0.18)" if theme.is_dark else "rgba(59,130,246,0.12)"
            )
            bd = tk("accent")
            fg = tk("text")
            mut = tk("text_mut")
        except Exception:
            bg, bd, fg, mut = "rgba(59,130,246,0.15)", "#3b82f6", "#e2e8f0", "#94a3b8"
        self.setStyleSheet(
            f"QFrame#AboutDomainChip{{"
            f"background:{bg};border:1px solid {bd};border-radius:10px;}}"
            f"QLabel#AboutDomainChipText{{"
            f"color:{fg};font-size:{_FS['chip']}px;background:transparent;border:none;}}"
            f"QPushButton#AboutDomainChipClose{{"
            f"color:{mut};font-size:{_FS['chip_x']}px;font-weight:700;"
            f"background:transparent;border:none;padding:0;}}"
            f"QPushButton#AboutDomainChipClose:hover{{color:{fg};}}"
        )



class _WhitelistFlowHost(QWidget):
    """FlowLayout 宿主：把 heightForWidth 暴露给外层，避免名单换行后高度被裁。"""

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        lay = self.layout()
        if lay is not None and hasattr(lay, "heightForWidth"):
            try:
                return int(lay.heightForWidth(max(1, int(width))))
            except Exception:
                pass
        return super().heightForWidth(width)

    def sizeHint(self):
        lay = self.layout()
        w = max(1, int(self.width() or 0))
        if lay is not None and w > 1 and hasattr(lay, "heightForWidth"):
            try:
                return QSize(w, int(lay.heightForWidth(w)))
            except Exception:
                pass
        if lay is not None:
            try:
                return lay.sizeHint()
            except Exception:
                pass
        return super().sizeHint()

    def minimumSizeHint(self):
        return self.sizeHint()


class _WhitelistRow(QFrame):
    """单个来源白名单：矩形外框；标题+刷新；域名胶囊。可点击选中。"""

    changed = pyqtSignal()
    selected = pyqtSignal(str)  # key

    def __init__(self, key: str, title: str, parent=None):
        super().__init__(parent)
        self.key = key
        self._title = title
        self._domains: List[str] = []
        self._chips: Dict[str, _DomainChip] = {}
        self._selected = False

        self.setObjectName("WhitelistCatFrame")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        # Preferred：按内容增高，避免 Maximum + 低估 sizeHint 把名单卡裁掉
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(0)
        root.setAlignment(Qt.AlignTop)

        # 流式宿主：必须拉满行宽，否则 AlignLeft 只给 sizeHint 宽，
        # 父布局又不按 heightForWidth 增高 → 只露出一行，域名像「消失」
        self.chip_host = _WhitelistFlowHost()
        self.chip_host.setObjectName("AboutWhitelistChips")
        self.chip_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.chip_lay = FlowLayout(self.chip_host, h_spacing=6, v_spacing=4)
        self.chip_lay.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.chip_host)

        self.lbl = QLabel(title)
        self.lbl.setStyleSheet(_lbl_qss(tk("text"), "row", weight=600))
        self.lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        # 刷新：正方形（边长跟名单卡行高对齐）
        self.btn_reset = QPushButton()
        self.btn_reset.setObjectName("WhitelistResetBtn")
        apply_mini_button(self.btn_reset)
        side = 24
        icon_sz = 14
        try:
            ink = tk("text_mut")
        except Exception:
            ink = "#9fb0d7"
        self.btn_reset.setIcon(_reset_default_icon(icon_sz, ink))
        self.btn_reset.setIconSize(QSize(icon_sz, icon_sz))
        self.btn_reset.setToolTip("恢复默认")
        self.btn_reset.setCursor(Qt.PointingHandCursor)
        self.btn_reset.setFocusPolicy(Qt.NoFocus)
        self.btn_reset.clicked.connect(self._on_reset_clicked)
        # 强制正方形，覆盖 mini / 全局 QSS 的宽高限制
        self.btn_reset.setFixedSize(side, side)
        self.btn_reset.setStyleSheet(
            f"QPushButton#WhitelistResetBtn{{"
            f"min-width:{side}px;max-width:{side}px;"
            f"min-height:{side}px;max-height:{side}px;"
            f"padding:0;margin:0;}}"
        )

        self.chip_lay.addWidget(self.lbl)
        self.chip_lay.addWidget(self.btn_reset)

        self.installEventFilter(self)
        self.lbl.installEventFilter(self)
        self.chip_host.installEventFilter(self)
        self.btn_reset.installEventFilter(self)

        self._apply_frame_style()

    def eventFilter(self, obj, event):
        try:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self.selected.emit(self.key)
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.selected.emit(self.key)
        super().mousePressEvent(event)

    def _on_reset_clicked(self):
        self.selected.emit(self.key)
        self._reset_default()

    def set_selected(self, on: bool):
        on = bool(on)
        if self._selected == on:
            self._apply_frame_style()
            return
        self._selected = on
        self._apply_frame_style()

    def is_selected(self) -> bool:
        return bool(self._selected)

    def _apply_frame_style(self):
        try:
            border = tk("accent") if self._selected else tk("border")
        except Exception:
            border = "#3b82f6" if self._selected else "#25345c"
        bg = "rgba(59,130,246,0.10)" if self._selected else "transparent"
        # 选中/未选中同为 2px 边框，避免切换时布局抖动
        w = 2
        self.setStyleSheet(
            f"QFrame#WhitelistCatFrame{{"
            f"background:{bg};border:{w}px solid {border};border-radius:8px;}}"
            f"QFrame#WhitelistCatFrame QLabel{{background:transparent;}}"
            f"QFrame#WhitelistCatFrame QWidget#AboutWhitelistChips{{"
            f"background:transparent;border:none;}}"
        )

    def set_domains(self, domains: List[str]):
        cleaned: List[str] = []
        seen = set()
        for d in domains or []:
            n = _normalize_domain(str(d))
            if n and n not in seen:
                seen.add(n)
                cleaned.append(n)
        self._domains = cleaned
        self._rebuild_chips()

    def domains(self) -> List[str]:
        return list(self._domains)

    def add_domain(self, raw: str) -> bool:
        """添加一个域名；成功返回 True。"""
        n = _normalize_domain(raw)
        if not n or n in self._domains:
            return False
        self._domains.append(n)
        self._rebuild_chips()
        self.changed.emit()
        return True

    def _rebuild_chips(self):
        # 清空后按序重加：分类名、刷新、名单卡（同流左对齐少占行）
        while self.chip_lay.count():
            item = self.chip_lay.takeAt(0)
            w = item.widget() if item else None
            if w is None:
                continue
            if w is self.lbl or w is self.btn_reset:
                continue
            w.setParent(None)
            w.deleteLater()
        while self.chip_lay.count():
            self.chip_lay.takeAt(0)
        self.chip_lay.addWidget(self.lbl)
        self.chip_lay.addWidget(self.btn_reset)
        self._chips.clear()
        for d in self._domains:
            chip = _DomainChip(d, self.chip_host)
            chip.removed.connect(self._remove_domain)
            try:
                chip.installEventFilter(self)
            except Exception:
                pass
            self._chips[d] = chip
            self.chip_lay.addWidget(chip)
        if not self._domains:
            empty = QLabel("（空）")
            empty.setStyleSheet(_lbl_qss(tk("text_faint"), "hint"))
            try:
                empty.installEventFilter(self)
            except Exception:
                pass
            self.chip_lay.addWidget(empty)
        try:
            self.chip_host.updateGeometry()
            self.updateGeometry()
        except Exception:
            pass

    def _remove_domain(self, domain: str):
        d = _normalize_domain(domain)
        if d in self._domains:
            self._domains = [x for x in self._domains if x != d]
            self._rebuild_chips()
            self.changed.emit()

    def _reset_default(self):
        defaults = default_clipboard_whitelist()
        self.set_domains(list(defaults.get(self.key) or []))
        self.changed.emit()

    def restyle(self):
        try:
            self.lbl.setStyleSheet(_lbl_qss(tk("text"), "row", weight=600))
        except Exception:
            pass
        self._apply_frame_style()
        try:
            ink = tk("text_mut")
        except Exception:
            ink = "#9fb0d7"
        try:
            side = 24
            icon_sz = 14
            self.btn_reset.setIcon(_reset_default_icon(icon_sz, ink))
            self.btn_reset.setIconSize(QSize(icon_sz, icon_sz))
            self.btn_reset.setFixedSize(side, side)
            self.btn_reset.setStyleSheet(
                f"QPushButton#WhitelistResetBtn{{"
                f"min-width:{side}px;max-width:{side}px;"
                f"min-height:{side}px;max-height:{side}px;"
                f"padding:0;margin:0;}}"
            )
        except Exception:
            pass
        for chip in self._chips.values():
            try:
                chip._apply_style()
            except Exception:
                pass
        if not self._domains:
            self._rebuild_chips()


def _guide_python() -> str:
    try:
        from utils.app_paths import pip_python
        return pip_python() or "python"
    except Exception:
        return sys.executable or "python"


def _guide_pip(args: str) -> str:
    py = _guide_python()
    return f'"{py}" -m pip {args}'


def _copy_glyph_pixmap(size: int, color: str, *, done: bool = False) -> QPixmap:
    size = max(14, int(size))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = float(size)
    pen = QPen(QColor(color), max(1.3, s * 0.09))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    if done:
        path = QPainterPath()
        path.moveTo(s * 0.22, s * 0.52)
        path.lineTo(s * 0.42, s * 0.70)
        path.lineTo(s * 0.78, s * 0.30)
        p.drawPath(path)
    else:
        p.drawRoundedRect(QRectF(s * 0.20, s * 0.28, s * 0.60, s * 0.58), s * 0.08, s * 0.08)
        p.drawRoundedRect(QRectF(s * 0.33, s * 0.12, s * 0.34, s * 0.22), s * 0.06, s * 0.06)
        p.drawLine(QPointF(s * 0.34, s * 0.50), QPointF(s * 0.66, s * 0.50))
        p.drawLine(QPointF(s * 0.34, s * 0.64), QPointF(s * 0.58, s * 0.64))
    p.end()
    return pm


class _StatusGlyph(QWidget):
    """组件可用/未就绪的状态标：自绘圆底 + 勾/叹号。

    不用 ✅ / ⚠ 彩色 emoji：Windows Fluent 3D 字体会给绿勾加一层红褐描边。
    """

    SIZE = 15

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ok = None  # True=可用 / False=未就绪 / None=隐藏
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.hide()
        try:
            theme.changed.connect(self.update)
        except Exception:
            pass

    def set_ok(self, ok):
        if ok is True:
            self._ok = True
        elif ok is False:
            self._ok = False
        else:
            self._ok = None
        self.setVisible(self._ok is not None)
        self.update()

    def paintEvent(self, _e):
        if self._ok is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        s = float(min(self.width(), self.height()))
        m = 0.5
        disc = QRectF(m, m, s - 2.0 * m, s - 2.0 * m)
        try:
            fill = QColor(tk("ok") if self._ok else tk("warn"))
        except Exception:
            fill = QColor("#22c55e" if self._ok else "#f59e0b")
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(fill))
        p.drawEllipse(disc)

        pen = QPen(QColor("#ffffff"), 1.7)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if self._ok:
            path = QPainterPath()
            path.moveTo(s * 0.26, s * 0.52)
            path.lineTo(s * 0.42, s * 0.68)
            path.lineTo(s * 0.74, s * 0.34)
            p.drawPath(path)
        else:
            cx = s * 0.5
            p.drawLine(QPointF(cx, s * 0.28), QPointF(cx, s * 0.54))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor("#ffffff")))
            r = 1.15
            p.drawEllipse(QRectF(cx - r, s * 0.68 - r, r * 2.0, r * 2.0))
        p.end()


class _CmdCopyBtn(QPushButton):
    """命令行右侧复制图标。点一下复制该行，短暂换成对勾。"""

    SIZE = 22

    def __init__(self, command: str, parent=None):
        super().__init__(parent)
        self._cmd = command or ""
        self._done = False
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFlat(True)
        self.setToolTip("复制这条命令")
        self.clicked.connect(self._copy)
        self._restyle()

    def _icon_color(self) -> str:
        try:
            return tk("ok") if self._done else tk("text_mut")
        except Exception:
            return "#22c55e" if self._done else "#9fb0d7"

    def _restyle(self):
        self.setIcon(QIcon(_copy_glyph_pixmap(16, self._icon_color(), done=self._done)))
        self.setIconSize(QSize(16, 16))
        try:
            dark = bool(theme.is_dark)
        except Exception:
            dark = True
        hover = "rgba(255,255,255,0.10)" if dark else "rgba(15,23,42,0.08)"
        self.setStyleSheet(
            "QPushButton{background:transparent;border:none;padding:0;}"
            f"QPushButton:hover{{background:{hover};border-radius:4px;}}"
        )

    def _copy(self):
        if not self._cmd:
            return
        try:
            QApplication.clipboard().setText(self._cmd)
        except Exception:
            return
        self._done = True
        self.setToolTip("已复制")
        self._restyle()
        try:
            QToolTip.showText(self.mapToGlobal(self.rect().center()), "已复制", self)
        except Exception:
            pass
        t = QTimer(self)
        t.setSingleShot(True)
        t.timeout.connect(self._reset)
        t.start(1400)

    def _reset(self):
        self._done = False
        self.setToolTip("复制这条命令")
        self._restyle()


class _InstallGuideDialog(QDialog):
    """组件安装教程：纯文字 + 每条命令独立复制。无倒计时，需手动关闭。"""

    def __init__(self, parent, title: str, blocks: list):
        super().__init__(parent)
        self.setWindowTitle(title or "安装教程")
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.setMinimumWidth(540)
        self.resize(580, 460)
        self.setModal(True)
        self._build(blocks)

    def _build(self, blocks: list):
        try:
            bg = tk("panel")
            text = tk("text")
            mut = tk("text_mut")
            border = tk("border")
            cmd_bg = tk("input_bg")
            cmd_fg = tk("text")
        except Exception:
            bg, text, mut, border = "#141b33", "#d7def7", "#9fb0d7", "#25345c"
            cmd_bg, cmd_fg = "#0d1b35", "#d7def7"

        self.setStyleSheet(
            f"QDialog{{background:{bg};color:{text};}}"
            f"QScrollArea{{background:transparent;border:none;}}"
            f"QLabel{{background:transparent;}}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        host = QWidget()
        host.setObjectName("InstallGuideHost")
        host.setStyleSheet(f"#InstallGuideHost{{background:transparent;}}")
        body = QVBoxLayout(host)
        body.setContentsMargins(0, 0, 8, 0)
        body.setSpacing(10)

        para_qss = (
            f"QLabel{{color:{mut};font-size:{_FS['body']}px;"
            f"background:transparent;border:none;}}"
        )
        cmd_qss = (
            f"QLabel{{color:{cmd_fg};font-size:13px;"
            f"background:transparent;border:none;}}"
        )
        cmd_wrap_qss = (
            f"QFrame#GuideCmdRow{{background:{cmd_bg};"
            f"border:1px solid {border};border-radius:8px;}}"
        )

        for item in blocks or []:
            if isinstance(item, tuple) and len(item) >= 2 and item[0] == "cmd":
                cmd = str(item[1] or "").strip()
                if not cmd:
                    continue
                row = QFrame()
                row.setObjectName("GuideCmdRow")
                row.setStyleSheet(cmd_wrap_qss)
                hl = QHBoxLayout(row)
                hl.setContentsMargins(10, 8, 6, 8)
                hl.setSpacing(8)
                lab = QLabel(cmd)
                lab.setWordWrap(True)
                lab.setTextInteractionFlags(
                    Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
                )
                font = QFont("Consolas")
                font.setStyleHint(QFont.Monospace)
                font.setPixelSize(13)
                lab.setFont(font)
                lab.setStyleSheet(cmd_qss)
                lab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
                hl.addWidget(lab, 1)
                hl.addWidget(_CmdCopyBtn(cmd, row), 0, Qt.AlignTop)
                body.addWidget(row)
            else:
                text_s = str(item or "").strip()
                if not text_s:
                    continue
                lab = QLabel(text_s)
                lab.setWordWrap(True)
                lab.setStyleSheet(para_qss)
                set_label_selectable(lab)
                body.addWidget(lab)

        body.addStretch(1)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        foot = QHBoxLayout()
        foot.addStretch(1)
        btn_close = apply_medium_button(QPushButton("关闭"))
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self.accept)
        foot.addWidget(btn_close)
        root.addLayout(foot)


class PageAbout(QWidget):
    """组件安装 + 白名单。embedded=True 时作为系统总览下半部分（不再自带滚动）。"""

    # ===== 窗口高度 BUG 防护（与截图/速存/时区汇率页同一套路）=====
    # FlowLayout / wordWrap 会让整页 hasHeightForWidth()=True，Qt 拖窗口时抬高推荐高度。
    # 切断整页 HFW；内部滚动/换行照常。
    def hasHeightForWidth(self):
        return False

    def minimumSizeHint(self):
        # 嵌在系统总览滚动区里时，不能报 0，否则会被压扁裁字
        if getattr(self, "_embedded", False):
            sh = super().minimumSizeHint()
            if sh.isValid() and sh.height() > 0:
                return QSize(0, sh.height())
            sh2 = super().sizeHint()
            return QSize(0, max(0, int(sh2.height()) if sh2.isValid() else 0))
        return QSize(0, 0)

    def sizeHint(self):
        sh = super().sizeHint()
        if not sh.isValid() or sh.height() <= 0:
            return QSize(0, 0)
        # 独立页保留封顶，避免拖窗口把主窗撑飞；嵌入总览时不要封顶，交给外层滚动
        if getattr(self, "_embedded", False):
            return QSize(max(0, sh.width()), sh.height())
        cap = 700
        if int(sh.height()) > cap:
            return QSize(sh.width(), cap)
        return sh

    def __init__(self, embedded: bool = False):
        super().__init__()
        self._embedded = bool(embedded)
        self.setObjectName("AboutScroll" if self._embedded else "PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        if self._embedded:
            self.setStyleSheet(
                "#AboutScroll{background:transparent;border:none;}"
            )

        self._mw = None  # MainWindow
        self._theme_titles: List[QLabel] = []
        self._comp_text_labels: List[tuple] = []  # (QLabel, "intro"|"hint")
        self._whitelist_rows: Dict[str, _WhitelistRow] = {}
        self._category_seps: List[QFrame] = []  # 白名单/组件分类分隔线
        self._comp_busy = set()   # 正在检测：按钮冻结
        self._comp_ready = set()  # 状态为「可用」：检测/安装教程都冻结
        self._loading = False
        self._prefs_loaded = False

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._persist_now)

        if self._embedded:
            outer = QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)
            host = QWidget()
            host.setObjectName("AboutScrollHost")
            host.setAttribute(Qt.WA_StyledBackground, True)
            host.setStyleSheet("#AboutScrollHost{background:transparent;border:none;}")
            outer.addWidget(host)
            root = QVBoxLayout(host)
        else:
            # 独立页（兼容旧入口）：自带滚动区
            scroll = QScrollArea(self)
            scroll.setObjectName("AboutScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setAttribute(Qt.WA_StyledBackground, True)
            scroll.setStyleSheet(
                "QScrollArea#AboutScroll{background:transparent;border:none;}"
                "QScrollArea#AboutScroll > QWidget > QWidget{background:transparent;}"
            )
            try:
                scroll.viewport().setAutoFillBackground(False)
                scroll.viewport().setStyleSheet("background:transparent;")
            except Exception:
                pass
            wrap = QVBoxLayout(self)
            wrap.setContentsMargins(0, 0, 0, 0)
            wrap.addWidget(scroll)
            host = QWidget()
            host.setObjectName("AboutScrollHost")
            host.setAttribute(Qt.WA_StyledBackground, True)
            host.setStyleSheet("#AboutScrollHost{background:transparent;border:none;}")
            scroll.setWidget(host)
            root = QVBoxLayout(host)

        root.setContentsMargins(0, 0, 0, 6)
        root.setSpacing(6)
        root.setAlignment(Qt.AlignTop)
        self._content_root = root

        # 左右栏面板（嵌入总览时由 install_into 挂到整页两列）
        self.left_panel = QWidget()
        self.left_panel.setObjectName("AboutLeftPanel")
        self.left_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        left_lay = QVBoxLayout(self.left_panel)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(6)
        left_lay.setAlignment(Qt.AlignTop)
        left_lay.addWidget(self._build_auto_dl_card(), 0, Qt.AlignTop)

        self.right_panel = QWidget()
        self.right_panel.setObjectName("AboutRightPanel")
        self.right_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        right_lay = QVBoxLayout(self.right_panel)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(6)
        right_lay.setAlignment(Qt.AlignTop)
        right_lay.addWidget(self._build_components_card(), 0, Qt.AlignTop)
        right_lay.addWidget(self._build_hotkey_card(), 0, Qt.AlignTop)

        self._installed_split = False
        if self._embedded:
            # 暂挂在本控件，install_into 再挪到系统总览左右列
            shell = QHBoxLayout()
            shell.setContentsMargins(0, 0, 0, 0)
            shell.setSpacing(6)
            shell.addWidget(self.left_panel, 1)
            shell.addWidget(self.right_panel, 1)
            root.addLayout(shell)
        else:
            split = QHBoxLayout()
            split.setSpacing(6)
            split.setContentsMargins(0, 0, 0, 0)
            split.setAlignment(Qt.AlignTop)
            split.addWidget(self.left_panel, 1, Qt.AlignTop)
            split.addWidget(self.right_panel, 1, Qt.AlignTop)
            root.addLayout(split)
            root.addStretch(1)

    def install_into(self, left_layout, right_layout):
        """把左右栏嵌进系统总览整页两列（与设置区同列对齐）。"""
        if self._installed_split:
            return

        def _detach(layout, w):
            if layout is None:
                return False
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item is None:
                    continue
                if item.widget() is w:
                    layout.removeWidget(w)
                    return True
                if item.layout() is not None and _detach(item.layout(), w):
                    return True
            return False

        for panel in (self.left_panel, self.right_panel):
            parent = panel.parentWidget()
            if parent is not None:
                _detach(parent.layout(), panel)
            panel.setParent(None)

        left_layout.addWidget(self.left_panel, 0, Qt.AlignTop)
        right_layout.addWidget(self.right_panel, 0, Qt.AlignTop)
        self._installed_split = True
        self.hide()
        self.setFixedSize(0, 0)

    # ── 对外 API ─────────────────────────────────────────────
    def set_main_window(self, mw):
        self._mw = mw

    def on_enter(self, cookie_alert: bool = False):
        # cookie_alert 参数保留兼容旧调用；提示条在系统总览「Cookie设置」
        del cookie_alert
        self.reload_from_prefs()
        QTimer.singleShot(0, self._refresh_all_component_status)
        self._refresh_hotkey_card()
        QTimer.singleShot(0, self._refresh_hotkey_card)

    def restyle_theme(self):
        """主题切换后重刷本页内联样式。"""
        for lbl in self._theme_titles:
            try:
                _restyle_about_title(lbl)
            except Exception:
                pass
        self._style_auto_dl_chrome()
        self._restyle_category_seps()
        for lbl, kind in self._comp_text_labels:
            try:
                lbl.setStyleSheet(self._comp_text_qss(kind))
            except Exception:
                pass
        for row in self._whitelist_rows.values():
            try:
                row.restyle()
            except Exception:
                pass
        self._refresh_all_component_status()
        self._refresh_hotkey_card()

    def _refresh_all_component_status(self):
        self._refresh_ocr_status()
        self._refresh_record_status()
        self._refresh_voice_status()
        self._refresh_clone_status()

    def reload_from_prefs(self):
        """从主窗口内存偏好刷新控件（软件信息/界面缩放/保存与Cookie 在系统总览上半）。"""
        prefs = {}
        if self._mw is not None:
            prefs = getattr(self._mw, "_user_prefs", None) or {}
        if not isinstance(prefs, dict):
            prefs = {}
        self._loading = True
        try:
            self._load_whitelist(prefs)
            self._prefs_loaded = True
        finally:
            self._loading = False

    def has_prefs_loaded(self) -> bool:
        return bool(self._prefs_loaded)

    def export_whitelist(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for key, row in self._whitelist_rows.items():
            out[key] = row.domains()
        # 补齐默认 key
        for k, v in default_clipboard_whitelist().items():
            if k not in out:
                out[k] = list(v)
        return out

    # ── 组件卡共用骨架 ──────────────────────────────────────
    @staticmethod
    def _comp_text_qss(kind: str) -> str:
        if kind == "section":
            return _lbl_qss(tk("accent"), "group", weight=700)
        if kind == "intro":
            return _lbl_qss(tk("text_mut"), "body")
        if kind == "hint":
            return _lbl_qss(tk("text_faint"), "hint")
        return _lbl_qss(tk("text_mut"), "body")

    def _set_comp_status(self, lbl: QLabel, ok, text: str):
        """ok=True 绿勾 / False 警告标 / None 不画标（检测中）。"""
        if ok is True:
            color = tk("ok")
        elif ok is False:
            color = tk("warn")
        else:
            color = tk("text_mut")
        lbl.setText(text)
        lbl.setStyleSheet(_lbl_qss(color, "body"))
        glyph = getattr(lbl, "_status_glyph", None)
        if glyph is not None:
            glyph.set_ok(ok)
        prefix = getattr(lbl, "_comp_prefix", None)
        if prefix:
            if ok is True and (text or "").strip() == "可用":
                self._comp_ready.add(prefix)
            else:
                self._comp_ready.discard(prefix)
            self._apply_comp_btns(prefix)

    def _apply_comp_btns(self, prefix: str):
        frozen = prefix in self._comp_ready or prefix in self._comp_busy
        enabled = not frozen
        tip = "组件已可用" if prefix in self._comp_ready else ""
        for name in (f"btn_{prefix}_check", f"btn_{prefix}_install"):
            b = getattr(self, name, None)
            if b is not None:
                b.setEnabled(enabled)
                try:
                    b.setCursor(Qt.ArrowCursor if frozen else Qt.PointingHandCursor)
                    b.setToolTip(tip)
                except Exception:
                    pass

    def _show_install_guide(self, title: str, blocks: list):
        parent = self.window() if self.window() is not None else self
        dlg = _InstallGuideDialog(parent, title, blocks)
        dlg.exec_()

    def _add_title(self, card, box, text: str) -> QLabel:
        lbl = install_card_title(card, box, text)
        _restyle_about_title(lbl)
        self._theme_titles.append(lbl)
        return lbl

    def _build_comp_block(
        self,
        obj_name: str,
        title: str,
        intro: str,
        prefix: str,
        on_check,
        on_install,
    ) -> QFrame:
        """「组件」区内单段：小标题 / 介绍 / 状态 / 检测+安装教程 / 提示（无独立外卡）。"""
        block = QFrame()
        block.setObjectName(obj_name)
        block.setAttribute(Qt.WA_StyledBackground, True)
        block.setStyleSheet(
            f"QFrame#{obj_name}{{background:transparent;border:none;}}"
        )
        block.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        box = QVBoxLayout(block)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)
        box.setAlignment(Qt.AlignTop)

        # 两行：① 标题左 + 简介右；② 状态左 + 检测/安装教程右
        title_lbl = QLabel(title)
        title_lbl.setObjectName(f"{obj_name}Title")
        title_lbl.setStyleSheet(self._comp_text_qss("section"))
        self._comp_text_labels.append((title_lbl, "section"))

        lbl_intro = QLabel(intro)
        lbl_intro.setWordWrap(False)
        lbl_intro.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lbl_intro.setStyleSheet(self._comp_text_qss("intro"))
        self._comp_text_labels.append((lbl_intro, "intro"))

        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(8)
        row1.addWidget(title_lbl, 0, Qt.AlignVCenter)
        row1.addStretch(1)
        row1.addWidget(lbl_intro, 0, Qt.AlignVCenter)
        box.addLayout(row1)

        glyph = _StatusGlyph()
        lbl_status = QLabel("…")
        lbl_status.setWordWrap(False)
        lbl_status.setStyleSheet(self._comp_text_qss("status"))
        lbl_status._status_glyph = glyph
        lbl_status._comp_prefix = prefix

        btn_check = apply_medium_button(QPushButton("检测"))
        btn_install = apply_medium_button(QPushButton("安装教程"))
        btn_check.setMinimumWidth(56)
        btn_install.setMinimumWidth(80)

        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(6)
        row2.addWidget(glyph, 0, Qt.AlignVCenter)
        row2.addWidget(lbl_status, 0, Qt.AlignVCenter)
        row2.addStretch(1)
        row2.addWidget(btn_check, 0, Qt.AlignVCenter)
        row2.addWidget(btn_install, 0, Qt.AlignVCenter)
        box.addLayout(row2)

        btn_check.clicked.connect(on_check)
        btn_install.clicked.connect(on_install)

        setattr(self, f"lbl_{prefix}_intro", lbl_intro)
        setattr(self, f"lbl_{prefix}_status", lbl_status)
        setattr(self, f"btn_{prefix}_check", btn_check)
        setattr(self, f"btn_{prefix}_install", btn_install)
        return block

    def _build_comp_card(
        self,
        obj_name: str,
        title: str,
        intro: str,
        prefix: str,
        on_check,
        on_install,
    ) -> QFrame:
        """兼容旧调用：返回组件区内单段（不再各自套外卡）。"""
        return self._build_comp_block(
            obj_name, title, intro, prefix, on_check, on_install
        )

    def _build_components_card(self) -> QFrame:
        """四个组件并入同一「模型与组件」区域。"""
        card = make_card("CardAboutComponents")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setSpacing(4)  # 分类间有 1px 线，间距收一点
        box.setAlignment(Qt.AlignTop)
        self._add_title(card, box, "模型与组件")
        for i, builder in enumerate((
            self._build_ocr_card,
            self._build_record_card,
            self._build_voice_card,
            self._build_clone_card,
        )):
            if i:
                self._add_category_sep(box, "AboutCompSep")
            box.addWidget(builder())
        return card

    def _set_comp_btns(self, prefix: str, enabled: bool):
        """enabled=False 表示检测中；True 结束忙碌。可用态仍保持冻结。"""
        if enabled:
            self._comp_busy.discard(prefix)
        else:
            self._comp_busy.add(prefix)
        self._apply_comp_btns(prefix)

    # ── 卡片：截图 OCR ───────────────────────────────────────
    def _build_ocr_card(self) -> QFrame:
        return self._build_comp_card(
            "CardAboutOcr", "截图 OCR", "PaddleOCR-VL-1.6 识字",
            "ocr", self._on_ocr_check, self._on_ocr_install,
        )

    def _set_ocr_btns(self, enabled: bool):
        self._set_comp_btns("ocr", enabled)

    def _refresh_ocr_status(self):
        lbl = getattr(self, "lbl_ocr_status", None)
        if lbl is None:
            return
        try:
            from utils.ocr_util import local_ocr_status
            st = local_ocr_status()
            probe = st.get("probe_ok")
            if probe is True:
                self._set_comp_status(lbl, True, "可用")
            elif probe is False:
                self._set_comp_status(lbl, False, "不可用")
            elif st.get("installed"):
                self._set_comp_status(lbl, True, "文件就绪")
            else:
                self._set_comp_status(lbl, False, st.get("label") or "未就绪")
        except Exception as e:
            self._set_comp_status(lbl, False, f"状态检测失败：{e}")

    def _on_ocr_check(self):
        self._set_ocr_btns(False)
        self._set_comp_status(self.lbl_ocr_status, None, "正在检测…")

        class _ProbeWorker(QThread):
            done = pyqtSignal(object)

            def run(self):
                try:
                    from utils.ocr_util import probe_local_ocr
                    self.done.emit(probe_local_ocr(force=True))
                except Exception as ex:
                    self.done.emit({
                        "ok": False, "label": "检测异常",
                        "detail": str(ex), "probe_msg": str(ex),
                    })

        w = _ProbeWorker(self)

        def _done(st):
            self._refresh_ocr_status()
            self._set_ocr_btns(True)
            msg = (st.get("probe_msg") or st.get("detail") or "").strip()
            if st.get("ok") and st.get("probe_ok") is not False:
                message_box_info(self, "截图 OCR", "可用。")
            else:
                message_box_warn(
                    self, "截图 OCR",
                    (msg or "不可用。") + "\n\n请点「安装教程」。",
                )
            w.deleteLater()

        w.done.connect(_done)
        self._ocr_probe_worker = w
        w.start()

    def _on_ocr_install(self):
        from utils.ocr_util import find_vl_model_dir
        model = find_vl_model_dir()
        self._show_install_guide("截图 OCR · 安装教程", [
            "截图 OCR 使用本地 PaddleOCR-VL-1.6。请先完全退出本程序，再在命令行执行下列命令（运行中会锁住文件）。",
            f"1) 权重（约 1.9GB，已放入则跳过）：\n{model}",
            "2) 运行库。有 NVIDIA 显卡时先装 GPU 版 PaddlePaddle：",
            ("cmd", _guide_pip(
                "install paddlepaddle-gpu==3.2.1 "
                "-i https://www.paddlepaddle.org.cn/packages/stable/cu126/"
            )),
            "没有独显时改用 CPU 版：",
            ("cmd", _guide_pip("install paddlepaddle==3.2.1")),
            "再装 paddleocr：",
            ("cmd", _guide_pip('install -U "paddleocr[doc-parser]>=3.6.0"')),
            "装好后重新打开本程序，到本页点「检测」。",
        ])

    # ── 卡片：录屏组件（ffmpeg）──────────────────────────
    def _build_record_card(self) -> QFrame:
        return self._build_comp_card(
            "CardAboutRecord", "录屏组件", "需要 ffmpeg",
            "record", self._on_record_check, self._on_record_install,
        )

    def _refresh_record_status(self):
        lbl = getattr(self, "lbl_record_status", None)
        if lbl is None:
            return
        try:
            from utils.region_recorder import find_ffmpeg
            if find_ffmpeg():
                self._set_comp_status(lbl, True, "可用")
            else:
                self._set_comp_status(lbl, False, "未安装")
        except Exception as e:
            self._set_comp_status(lbl, False, f"状态检测失败：{e}")

    def _on_record_check(self):
        self._refresh_record_status()
        try:
            from utils.region_recorder import find_ffmpeg
            path = find_ffmpeg()
            if path:
                message_box_info(self, "录屏组件", "可用。")
            else:
                message_box_warn(self, "录屏组件", "不可用。\n\n请点「安装教程」。")
        except Exception as e:
            message_box_warn(self, "录屏组件", str(e))

    def _on_record_install(self):
        self._show_install_guide("录屏组件 · 安装教程", [
            "区域录屏需要 ffmpeg，并确保它已加入系统 PATH。任选一种方式安装：",
            ("cmd", "winget install Gyan.FFmpeg"),
            "或使用 scoop：",
            ("cmd", "scoop install ffmpeg"),
            "装好后重启本程序，再点「检测」。",
        ])

    # ── 卡片：语音组件 ────
    def _build_voice_card(self) -> QFrame:
        return self._build_comp_card(
            "CardAboutVoice", "语音组件", "录音转文字",
            "voice", self._on_voice_check, self._on_voice_install,
        )

    def _set_voice_btns(self, enabled: bool):
        self._set_comp_btns("voice", enabled)

    def _refresh_voice_status(self):
        lbl = getattr(self, "lbl_voice_status", None)
        if lbl is None:
            return
        try:
            from utils.voice_input import voice_deps_ok
            ok, _missing = voice_deps_ok()
            if ok:
                self._set_comp_status(lbl, True, "可用")
            else:
                self._set_comp_status(lbl, False, "未安装")
        except Exception as e:
            self._set_comp_status(lbl, False, f"状态检测失败：{e}")

    def _on_voice_check(self):
        self._set_voice_btns(False)
        self._set_comp_status(self.lbl_voice_status, None, "正在检测…")
        try:
            QApplication.processEvents()
        except Exception:
            pass
        try:
            from utils.voice_input import voice_deps_ok
            ok, _missing = voice_deps_ok()
            self._refresh_voice_status()
            if ok:
                message_box_info(self, "语音组件", "可用。")
            else:
                message_box_warn(self, "语音组件", "不可用。\n\n请点「安装教程」。")
        except Exception as e:
            self._refresh_voice_status()
            message_box_warn(self, "语音组件", str(e))
        finally:
            self._set_voice_btns(True)

    def _on_voice_install(self):
        self._show_install_guide("语音组件 · 安装教程", [
            "录音后转成文字，需要 sounddevice、numpy、faster-whisper。请先完全退出本程序，再执行：",
            ("cmd", _guide_pip("install sounddevice numpy faster-whisper")),
            "装好后重新打开本程序，到本页点「检测」。首次使用可能联网下载 Whisper 模型。",
        ])

    # ── 卡片：语音克隆（CosyVoice）──────────────────────────
    def _build_clone_card(self) -> QFrame:
        return self._build_comp_card(
            "CardAboutClone", "语音克隆", "CosyVoice3 克隆音色",
            "clone", self._on_clone_check, self._on_clone_install,
        )

    def _refresh_clone_status(self):
        lbl = getattr(self, "lbl_clone_status", None)
        if lbl is None:
            return
        try:
            from utils.cosyvoice_clone import weights_status, status_summary
            st = weights_status()
            ok, label, _detail = status_summary(st, None)
            if st.get("has_weights") and st.get("has_source") and st.get("torch_pkg"):
                self._set_comp_status(lbl, True, "文件就绪")
            elif st.get("has_weights"):
                self._set_comp_status(lbl, False, label)
            else:
                self._set_comp_status(lbl, False, "未放入模型")
        except Exception as e:
            self._set_comp_status(lbl, False, f"状态检测失败：{e}")

    def _on_clone_check(self):
        self._clone_probe_worker = None
        self._set_comp_btns("clone", False)
        self._set_comp_status(self.lbl_clone_status, None, "正在检测…")
        try:
            QApplication.processEvents()
        except Exception:
            pass
        try:
            from utils.cosyvoice_clone import (
                weights_status, status_summary, ProbeWorker, find_cosyvoice_home,
            )
            st = weights_status()
            ok, label, detail = status_summary(st, None)
            if not st.get("has_weights") or not st.get("has_source"):
                self._refresh_clone_status()
                message_box_warn(
                    self, "语音克隆",
                    f"{label}\n\n{detail}\n\n点「安装教程」查看步骤。",
                )
                return
            if not st.get("torch_pkg"):
                self._refresh_clone_status()
                message_box_warn(
                    self, "语音克隆",
                    "未安装 PyTorch。\n\n点「安装教程」查看步骤。",
                )
                return

            worker = ProbeWorker(st.get("model_dir") or "", parent=self)

            def _ok(data):
                self._set_comp_btns("clone", True)
                if data.get("ok"):
                    self._set_comp_status(self.lbl_clone_status, True, "可用")
                    gpu = data.get("gpu") or ("CUDA" if data.get("cuda") else "CPU")
                    home = find_cosyvoice_home() or "已导入"
                    message_box_info(
                        self, "语音克隆",
                        f"可用。\n\n模型：{st.get('model_name') or '-'}\n"
                        f"torch {data.get('torch') or ''} · {gpu}\n"
                        f"运行库：{home}",
                    )
                else:
                    self._refresh_clone_status()
                    message_box_warn(
                        self, "语音克隆",
                        "运行库探测失败。\n\n" + (data.get("error") or "未知错误"),
                    )
                worker.deleteLater()

            def _fail(msg):
                self._set_comp_btns("clone", True)
                self._refresh_clone_status()
                message_box_warn(self, "语音克隆", str(msg))
                worker.deleteLater()

            worker.done.connect(_ok)
            worker.fail.connect(_fail)
            self._clone_probe_worker = worker
            worker.start()
            return
        except Exception as e:
            self._refresh_clone_status()
            message_box_warn(self, "语音克隆", str(e))
        finally:
            if getattr(self, "_clone_probe_worker", None) is None:
                self._set_comp_btns("clone", True)

    def _on_clone_install(self):
        from utils.cosyvoice_clone import clone_cmd
        try:
            from utils.app_paths import model_dir
            dest = os.path.join(model_dir(), "CosyVoice")
            weights = os.path.join(model_dir(), "Fun-CosyVoice3-0.5B")
        except Exception:
            dest = r"model\CosyVoice"
            weights = r"model\Fun-CosyVoice3-0.5B"
        self._show_install_guide("语音克隆 · 安装教程", [
            "两套东西不要混：权重是模型文件（约 9GB），运行库是 GitHub 源码（通常几十 MB）。不要把 Fun-CosyVoice3-0.5B 改名为 CosyVoice。",
            f"1) 权重（已放好则跳过）：\n{weights}",
            f"2) 运行库目录：\n{dest}",
            "还缺运行库时执行：",
            ("cmd", clone_cmd()),
            "再装 PyTorch（按本机 CUDA 改 cu124）：",
            ("cmd", _guide_pip(
                "install torch torchaudio "
                "--index-url https://download.pytorch.org/whl/cu124"
            )),
            "其余依赖：",
            ("cmd", _guide_pip(f'install -r "{os.path.join(dest, "requirements.txt")}"')),
            "装好后回到本页点「检测」。也可设置 COSYVOICE_HOME 指向源码目录。",
        ])

    # ── 卡片：预下载域名白名单 ───────────────────────────────
    def _build_auto_dl_card(self) -> QFrame:
        card = make_card("CardAboutAutoDl")
        # 只占左列宽；高度随内容（Preferred，避免底部分类被裁）
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setAlignment(Qt.AlignTop)
        box.setSpacing(8)

        # 标题左｜输入 + 添加 右对齐（全卡共用一套）
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        title_lbl = QLabel("白名单")
        title_lbl.setObjectName("CardAboutAutoDlTitle")
        _restyle_about_title(title_lbl)
        self._theme_titles.append(title_lbl)
        title_row.addWidget(title_lbl, 0, Qt.AlignVCenter)
        title_row.addStretch(1)

        # 输入框与添加同高；给汉字留足上下内边距，避免底边被切
        add_h = 30
        self.wl_edit = QLineEdit()
        self.wl_edit.setObjectName("WhitelistDomainEdit")
        self.wl_edit.setPlaceholderText("先选分类，再填域名或链接")
        self.wl_edit.setClearButtonEnabled(True)
        self.wl_edit.setFixedHeight(add_h)
        self.wl_edit.setMinimumHeight(add_h)
        self.wl_edit.setMaximumHeight(add_h)
        self.wl_edit.setMinimumWidth(160)
        self.wl_edit.setMaximumWidth(320)
        self.wl_edit.setStyleSheet(
            f"QLineEdit#WhitelistDomainEdit{{"
            f"min-height:{add_h}px;max-height:{add_h}px;"
            f"padding:2px 8px;font-size:13px;"
            f"line-height:{add_h - 4}px;}}"
        )
        self.wl_edit.returnPressed.connect(self._wl_add_from_edit)
        self.wl_btn_add = QPushButton("添加")
        self.wl_btn_add.setObjectName("WhitelistAddBtn")
        apply_mini_button(self.wl_btn_add)
        self.wl_btn_add.setCursor(Qt.PointingHandCursor)
        self.wl_btn_add.setFixedSize(self.wl_btn_add.sizeHint().width() + 8, add_h)
        self.wl_btn_add.setFixedHeight(add_h)
        self.wl_btn_add.setMinimumHeight(add_h)
        self.wl_btn_add.setMaximumHeight(add_h)
        self.wl_btn_add.setStyleSheet(
            f"QPushButton#WhitelistAddBtn{{"
            f"min-height:{add_h}px;max-height:{add_h}px;"
            f"padding:0 12px;font-size:13px;}}"
        )
        self.wl_btn_add.clicked.connect(self._wl_add_from_edit)
        title_row.addWidget(self.wl_edit, 0, Qt.AlignVCenter)
        title_row.addWidget(self.wl_btn_add, 0, Qt.AlignVCenter)
        box.addLayout(title_row)

        tip = QLabel(
            "先点选分类，再添加域名或路径（如 music.douyin.com/qishui）。复制含 https 链接时入队预下载（不立刻下）。空名单=该来源不入队。"
        )
        tip.setWordWrap(True)
        self.lbl_auto_dl_tip = tip
        self._wl_tip_base = tip.text()
        self._wl_tip_warn = False
        box.addWidget(tip)

        # 单列：视频段 → 图集段；分类矩形框，无分隔线
        col = QVBoxLayout()
        col.setSpacing(8)
        col.setAlignment(Qt.AlignTop)

        self.lbl_wl_sec_video = None  # 分段标题已去掉
        self.lbl_wl_sec_gallery = None
        self._style_auto_dl_chrome()

        self._wl_selected_key = None
        for key, section, title in _WHITELIST_GROUPS:
            row = _WhitelistRow(key, title, self)
            row.changed.connect(self._schedule_persist)
            row.selected.connect(self._wl_select_key)
            self._whitelist_rows[key] = row
            col.addWidget(row)

        box.addLayout(col, 0)

        # 无默认选中：添加前需先点分类
        self._wl_selected_key = None
        return card

    def _wl_select_key(self, key: str):
        key = str(key or "")
        self._wl_selected_key = key if key in self._whitelist_rows else None
        for k, row in self._whitelist_rows.items():
            try:
                row.set_selected(k == self._wl_selected_key)
            except Exception:
                pass
        if self._wl_selected_key and self._wl_tip_warn:
            self._wl_tip_warn = False
            self._refresh_wl_tip()

    def _wl_add_from_edit(self):
        raw = ""
        try:
            raw = self.wl_edit.text()
        except Exception:
            raw = ""
        n = _normalize_domain(raw)
        if not n:
            return
        key = getattr(self, "_wl_selected_key", None)
        row = self._whitelist_rows.get(key) if key else None
        if row is None:
            self._wl_tip_warn = True
            self._refresh_wl_tip()
            return
        try:
            self.wl_edit.clear()
        except Exception:
            pass
        row.add_domain(n)

    def _refresh_wl_tip(self):
        tip = getattr(self, "lbl_auto_dl_tip", None)
        if tip is None:
            return
        base = getattr(self, "_wl_tip_base", tip.text())
        if getattr(self, "_wl_tip_warn", False):
            tip.setTextFormat(Qt.RichText)
            tip.setText(
                f'{base} <span style="color:#3b82f6;">没有选中分类</span>'
            )
        else:
            tip.setTextFormat(Qt.PlainText)
            tip.setText(base)
        self._style_auto_dl_chrome()


    def _restyle_category_seps(self):
        try:
            c = tk("border")
        except Exception:
            c = "#25345c"
        for sep in getattr(self, "_category_seps", []) or []:
            try:
                name = sep.objectName() or "AboutCategorySep"
                th = 1
                try:
                    th = int(sep.property("sepThickness") or 1)
                except Exception:
                    th = 1
                th = max(1, th)
                sep.setFixedHeight(th)
                sep.setStyleSheet(
                    f"QFrame#{name}{{background:{c};border:none;"
                    f"min-height:{th}px;max-height:{th}px;}}"
                )
            except Exception:
                pass

    def _add_category_sep(
        self,
        layout,
        object_name: str = "AboutCategorySep",
        *,
        pad_v: int = 0,
        thickness: int = 1,
    ) -> QFrame:
        """分类分隔线。pad_v>0 时线上下各加 pad_v px；thickness 控制线粗。"""
        # 白名单分隔线默认更粗
        th = int(thickness or 1)
        if object_name == "AboutWhitelistSep" and th <= 1:
            th = 3
        sep = _make_category_sep(object_name, thickness=th)
        self._category_seps.append(sep)
        pad = max(0, int(pad_v or 0))
        if pad:
            layout.addSpacing(pad)
        layout.addWidget(sep)
        if pad:
            layout.addSpacing(pad)
        return sep

    def _style_auto_dl_chrome(self):
        tip = getattr(self, "lbl_auto_dl_tip", None)
        if tip is not None:
            tip.setStyleSheet(_lbl_qss(tk("text_mut"), "body"))
        sec_qss = _lbl_qss(tk("accent"), "group", weight=700)
        for lbl in (
            getattr(self, "lbl_wl_sec_video", None),
            getattr(self, "lbl_wl_sec_gallery", None),
        ):
            if lbl is not None:
                lbl.setStyleSheet(sec_qss)

    def _load_whitelist(self, prefs: dict):
        ca = prefs.get("clipboard_auto") or {}
        if not isinstance(ca, dict):
            ca = {}
        wl = ca.get("whitelist") if isinstance(ca, dict) else None
        if not isinstance(wl, dict):
            wl = {}
        defaults = default_clipboard_whitelist()
        for key, row in self._whitelist_rows.items():
            if key == "other":
                domains = other_whitelist_patterns(wl)
            else:
                domains = wl.get(key)
                if not isinstance(domains, list):
                    domains = list(defaults.get(key) or [])
            row.set_domains([str(x) for x in domains])

    def _reset_all_whitelist(self):
        defaults = default_clipboard_whitelist()
        for key, row in self._whitelist_rows.items():
            row.set_domains(list(defaults.get(key) or []))
        self._schedule_persist()

    # ── 卡片：热键占用 ──────────────────────────────────────
    _HK_STATE_TEXT = {
        "ok": "就绪",
        "busy": "占用",
        "off": "未启用",
        "plugin": "扩展",
    }

    def _build_hotkey_card(self) -> QFrame:
        card = make_card("CardAboutHotkeys")
        # Minimum：列高不够时不把清单压扁裁字
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setSpacing(4)
        box.setAlignment(Qt.AlignTop)

        # 标题左｜状态 + 清除占用 右对齐（同行）
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title_lbl = QLabel("热键占用")
        title_lbl.setObjectName("CardAboutHotkeysTitle")
        _restyle_about_title(title_lbl)
        self._theme_titles.append(title_lbl)
        title_row.addWidget(title_lbl, 0, Qt.AlignVCenter)
        title_row.addStretch(1)
        self.lbl_hk_summary = QLabel("")
        self.lbl_hk_summary.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        title_row.addWidget(self.lbl_hk_summary, 0, Qt.AlignVCenter)
        self.btn_hk_clear = apply_live_button(QPushButton("清除占用"))
        self.btn_hk_clear.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.btn_hk_clear.setEnabled(False)
        self.btn_hk_clear.clicked.connect(self._on_clear_hotkeys)
        title_row.addWidget(self.btn_hk_clear, 0, Qt.AlignVCenter)
        box.addLayout(title_row)

        self._hk_list_host = QWidget()
        self._hk_list_host.setObjectName("AboutHotkeyList")
        self._hk_list_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._hk_list_lay = QVBoxLayout(self._hk_list_host)
        self._hk_list_lay.setContentsMargins(0, 0, 0, 0)
        self._hk_list_lay.setSpacing(0)
        self._hk_list_lay.setAlignment(Qt.AlignTop)
        box.addWidget(self._hk_list_host, 0, Qt.AlignTop)
        return card

    def _hk_state_qss(self, state: str) -> str:
        if state == "ok":
            color = tk("ok")
        elif state == "busy":
            color = tk("err")
        elif state == "plugin":
            color = tk("text_mut")
        else:
            color = tk("text_faint")
        return _lbl_qss(color, "body", weight=600)

    def _hk_clear_list(self):
        lay = getattr(self, "_hk_list_lay", None)
        if lay is None:
            return
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _hk_text_h(self, lbl: QLabel) -> int:
        """QSS 字号生效后的一行高度；未抛光时退回配置页正文字号。"""
        try:
            lbl.ensurePolished()
            h = int(lbl.fontMetrics().height())
        except Exception:
            h = 0
        return max(h, int(_FS.get("body") or 14) + 4)

    def _hk_make_row(self, spec: dict, name_qss: str, key_qss: str) -> QWidget:
        state = str(spec.get("state") or "off")
        faint = _lbl_qss(tk("text_faint"), "body")
        line = QWidget()
        line.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        hl = QHBoxLayout(line)
        hl.setContentsMargins(0, 2, 0, 2)
        hl.setSpacing(8)
        key = QLabel(str(spec.get("key") or ""))
        key.setFixedWidth(60)
        key.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        key.setStyleSheet(faint if state == "off" else key_qss)
        name = QLabel(str(spec.get("name") or ""))
        name.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        name.setStyleSheet(faint if state == "off" else name_qss)
        hl.addWidget(key)
        hl.addWidget(name, 1)
        row_h = max(self._hk_text_h(key), self._hk_text_h(name)) + 4
        # 就绪不标，只标占用 / 未启用 / 扩展
        if state != "ok":
            st = QLabel(self._HK_STATE_TEXT.get(state, state))
            st.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            st.setStyleSheet(self._hk_state_qss(state))
            hl.addWidget(st)
            row_h = max(row_h, self._hk_text_h(st) + 4)
        # 静默复制（F7）：本行最右侧开关
        if spec.get("toggle") == "silent_copy":
            # 懒导入：避免 page_about ← page_overview ← ui_main 循环依赖
            from ui_main import SideToggleSwitch

            sw = SideToggleSwitch(on_color="#22C55E", parent=line)
            sw.setChecked(bool(spec.get("checked")))
            sw.clicked.connect(
                lambda _=False, s=sw: self._on_silent_copy_toggled(s.isChecked())
            )
            hl.addWidget(sw, 0, Qt.AlignVCenter)
            row_h = max(row_h, int(SideToggleSwitch.H) + 4)
        line.setMinimumHeight(row_h)
        line.setFixedHeight(row_h)
        return line

    def _on_silent_copy_toggled(self, on: bool):
        mw = getattr(self, "_mw", None)
        if mw is not None and hasattr(mw, "set_silent_copy_enabled"):
            try:
                mw.set_silent_copy_enabled(bool(on))
            except Exception:
                log.exception("切换静默复制失败")
        # 清单重建会删掉当前开关：推迟到本次点击处理结束后
        QTimer.singleShot(0, self._refresh_hotkey_card)

    def _hk_make_col(self, groups, name_qss: str, key_qss: str, grp_qss: str) -> QWidget:
        col_w = QWidget()
        col_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        col = QVBoxLayout(col_w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        col.setAlignment(Qt.AlignTop)
        first = True
        for group, specs in groups:
            if not first:
                # 分组间距放在布局里。QSS padding-top 不撑高 QLabel，字会被上下裁掉。
                col.addSpacing(8)
            hdr = QLabel(group)
            hdr.setStyleSheet(grp_qss)
            hdr.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            hdr.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            hdr_h = self._hk_text_h(hdr) + 2
            hdr.setMinimumHeight(hdr_h)
            hdr.setFixedHeight(hdr_h)
            first = False
            col.addWidget(hdr)
            for spec in specs:
                col.addWidget(self._hk_make_row(spec, name_qss, key_qss))
        return col_w

    def _refresh_hotkey_card(self):
        if getattr(self, "_hk_list_lay", None) is None:
            return
        self._hk_clear_list()

        rows = []
        mw = getattr(self, "_mw", None)
        if mw is not None and hasattr(mw, "collect_hotkey_inventory"):
            try:
                rows = list(mw.collect_hotkey_inventory() or ())
            except Exception:
                log.exception("读取热键清单失败")

        n_ok = n_busy = n_off = 0
        grouped = []
        last_group = None
        bucket = []
        for spec in rows:
            group = str(spec.get("group") or "")
            state = str(spec.get("state") or "off")
            if state == "ok":
                n_ok += 1
            elif state == "busy":
                n_busy += 1
            elif state == "off":
                n_off += 1
            if group != last_group:
                if last_group is not None:
                    grouped.append((last_group, bucket))
                last_group = group
                bucket = []
            bucket.append(spec)
        if last_group is not None:
            grouped.append((last_group, bucket))

        name_qss = _lbl_qss(tk("text"), "body")
        key_qss = _lbl_qss(tk("text_mut"), "body")
        grp_qss = _lbl_qss(tk("accent"), "group", weight=700)

        if not grouped:
            empty = QLabel("热键页加载后显示清单")
            empty.setStyleSheet(_lbl_qss(tk("text_faint"), "body"))
            empty.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            empty.setMinimumHeight(self._hk_text_h(empty) + 4)
            self._hk_list_lay.addWidget(empty, 0, Qt.AlignTop)
        else:
            self._hk_list_lay.addWidget(
                self._hk_make_col(grouped, name_qss, key_qss, grp_qss), 0, Qt.AlignTop
            )

        parts = [f"{n_ok} 就绪"]
        if n_busy:
            parts.append(f"{n_busy} 占用")
        if n_off:
            parts.append(f"{n_off} 未启用")
        summary = " · ".join(parts) if rows else ""
        color = tk("err") if n_busy else tk("text_mut")
        if getattr(self, "lbl_hk_summary", None) is not None:
            self.lbl_hk_summary.setText(summary)
            self.lbl_hk_summary.setStyleSheet(_lbl_qss(color, "body"))
        self._sync_hk_clear_btn(n_busy)
        self._hk_relayout()

    def _hk_relayout(self):
        """清单高度变了：通知总览两列按新 sizeHint 重切，避免仍按空卡高度裁字。"""
        host = getattr(self, "_hk_list_host", None)
        w = host
        while w is not None:
            try:
                w.updateGeometry()
            except Exception:
                pass
            fn = getattr(w, "relayout_columns", None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
                return
            w = w.parentWidget()
        # 未嵌入总览时，至少让本卡自己撑开
        if host is not None:
            try:
                card = host.parentWidget()
                if card is not None:
                    card.adjustSize()
            except Exception:
                pass

    def _sync_hk_clear_btn(self, n_busy: int = 0):
        """有占用时可点（蓝色）；无占用则灰化冻结。"""
        btn = getattr(self, "btn_hk_clear", None)
        if btn is None:
            return
        busy = int(n_busy or 0) > 0
        btn.setEnabled(busy)
        try:
            btn.setCursor(Qt.PointingHandCursor if busy else Qt.ForbiddenCursor)
        except Exception:
            pass
        try:
            st = btn.style()
            if st is not None:
                st.unpolish(btn)
                st.polish(btn)
        except Exception:
            pass

    def _on_clear_hotkeys(self):
        mw = getattr(self, "_mw", None)
        btn = getattr(self, "btn_hk_clear", None)
        if mw is None or not hasattr(mw, "force_release_hotkeys"):
            return
        if btn is not None:
            btn.setEnabled(False)
        try:
            QApplication.processEvents()
        except Exception:
            pass
        try:
            mw.force_release_hotkeys()
        except Exception:
            log.exception("清除热键占用失败")
        self._refresh_hotkey_card()
        QTimer.singleShot(600, self._refresh_hotkey_card)
        QTimer.singleShot(1800, self._refresh_hotkey_card)

    # ── 持久化 ───────────────────────────────────────────────
    def _schedule_persist(self, *_args):
        if self._loading or not getattr(self, "_prefs_loaded", False):
            return
        self._save_timer.start()

    def _persist_now(self):
        if self._mw is None:
            return
        try:
            from utils.user_prefs import save_user_prefs
            import copy

            cur = getattr(self._mw, "_user_prefs", None) or default_prefs()
            new_prefs = copy.deepcopy(cur) if isinstance(cur, dict) else default_prefs()

            ca = new_prefs.get("clipboard_auto")
            if not isinstance(ca, dict):
                ca = {}
                new_prefs["clipboard_auto"] = ca
            # 旧侧栏键固定 False；预下载开关以主窗当前状态为准
            ca["video"] = False
            ca["gallery"] = False
            if self._mw is not None and hasattr(self._mw, "is_pre_dl_auto_process_enabled"):
                ca["auto_process"] = bool(self._mw.is_pre_dl_auto_process_enabled())
            ca["whitelist"] = self.export_whitelist()

            self._mw._user_prefs = new_prefs
            save_user_prefs(new_prefs)
            log.info("关于页设置已落盘")
        except Exception:
            log.exception("关于页保存设置失败")


