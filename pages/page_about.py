# pages/page_about.py
# 组件安装 + 预下载域名白名单。独立「配置」页已取消，作为系统总览下半部分嵌入。

from __future__ import annotations

import os
import sys
from typing import Dict, List

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal, QRectF, QPointF, QSize
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
from utils.user_prefs import default_clipboard_whitelist, default_prefs
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


def _restyle_about_title(lbl: QLabel) -> None:
    """卡片标题走全站色，字号用配置页 +2。"""
    restyle_card_title(lbl)
    ss = lbl.styleSheet() or ""
    old = f"font-size:{sp(CARD_TITLE_FONT_SIZE)}px"
    new = f"font-size:{_FS['title']}px"
    if old in ss:
        lbl.setStyleSheet(ss.replace(old, new, 1))


# 白名单分组：key → (分区, 显示名)
_WHITELIST_GROUPS = (
    ("douyin", "视频下载", "抖音"),
    ("bilibili", "视频下载", "B站"),
    ("youtube", "视频下载", "YouTube"),
    ("gallery_eh", "图集下载", "e-hentai"),
    ("gallery_pixiv", "图集下载", "Pixiv"),
    ("gallery_hitomi", "图集下载", "hitomi.la"),
)


def _normalize_domain(raw: str) -> str:
    s = (raw or "").strip().lower()
    if not s:
        return ""
    # 允许用户粘贴完整 URL，只取 host
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    s = s.split("/")[0].split("?")[0].strip()
    if s.startswith("www."):
        s = s[4:]
    # 去掉首尾点
    s = s.strip(".")
    return s


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


class _WhitelistRow(QWidget):
    """单一下载子页的白名单行：标题 + 域名胶囊 + 添加。"""

    changed = pyqtSignal()

    def __init__(self, key: str, title: str, parent=None):
        super().__init__(parent)
        self.key = key
        self._domains: List[str] = []
        self._chips: Dict[str, _DomainChip] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self.lbl = QLabel(title)
        self.lbl.setStyleSheet(_lbl_qss(tk("text"), "row", weight=600))
        head.addWidget(self.lbl)
        head.addStretch(1)
        self.btn_reset = QPushButton("恢复默认")
        apply_mini_button(self.btn_reset)
        self.btn_reset.setCursor(Qt.PointingHandCursor)
        self.btn_reset.clicked.connect(self._reset_default)
        head.addWidget(self.btn_reset)
        root.addLayout(head)

        self.chip_host = QWidget()
        self.chip_host.setObjectName("AboutWhitelistChips")
        self.chip_lay = FlowLayout(self.chip_host, h_spacing=6, v_spacing=6)
        self.chip_lay.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.chip_host)

        add_row = QHBoxLayout()
        add_row.setContentsMargins(0, 0, 0, 0)
        add_row.setSpacing(6)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("添加域名，如 example.com")
        self.edit.setClearButtonEnabled(True)
        self.edit.returnPressed.connect(self._add_from_edit)
        add_row.addWidget(self.edit, 1)
        self.btn_add = QPushButton("添加")
        apply_mini_button(self.btn_add)
        self.btn_add.setCursor(Qt.PointingHandCursor)
        self.btn_add.clicked.connect(self._add_from_edit)
        add_row.addWidget(self.btn_add)
        root.addLayout(add_row)

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

    def _rebuild_chips(self):
        while self.chip_lay.count():
            item = self.chip_lay.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._chips.clear()
        for d in self._domains:
            chip = _DomainChip(d, self.chip_host)
            chip.removed.connect(self._remove_domain)
            self._chips[d] = chip
            self.chip_lay.addWidget(chip)
        if not self._domains:
            empty = QLabel("（空 · 该来源不会写入预下载记录）")
            empty.setStyleSheet(_lbl_qss(tk("text_faint"), "body"))
            self.chip_lay.addWidget(empty)

    def _add_from_edit(self):
        n = _normalize_domain(self.edit.text())
        self.edit.clear()
        if not n:
            return
        if n in self._domains:
            return
        self._domains.append(n)
        self._rebuild_chips()
        self.changed.emit()

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
        for chip in self._chips.values():
            try:
                chip._apply_style()
            except Exception:
                pass
        # 空提示在 rebuild 时写样式；主题切换后若有芯片则只刷胶囊
        if not self._domains:
            self._rebuild_chips()


# 三个组件卡共用提示
_COMP_HINT = "检测是否可用；未就绪时看安装教程"


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

        root.setContentsMargins(0, 0, 0, 8)
        root.setSpacing(10)
        root.setAlignment(Qt.AlignTop)
        self._content_root = root

        # 热键占用占 50%；右侧 50% 纵向叠放截图 OCR / 录屏 / 语音 / 克隆
        split = QHBoxLayout()
        split.setSpacing(8)
        split.setContentsMargins(0, 0, 0, 0)

        left = QWidget()
        left.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(0)
        left_lay.setAlignment(Qt.AlignTop)
        left_lay.addWidget(self._build_hotkey_card(), 1)

        right = QWidget()
        right.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(8)
        right_lay.addWidget(self._build_ocr_card(), 1)
        right_lay.addWidget(self._build_record_card(), 1)
        right_lay.addWidget(self._build_voice_card(), 1)
        right_lay.addWidget(self._build_clone_card(), 1)

        split.addWidget(left, 1)
        split.addWidget(right, 1)
        root.addLayout(split)

        # 自动下载白名单
        root.addWidget(self._build_auto_dl_card())

        # 独立页底部留白；嵌入总览时不要 stretch，否则在有限高度里会把卡片压扁裁字
        if not self._embedded:
            root.addStretch(1)

    # ── 对外 API ─────────────────────────────────────────────
    def set_main_window(self, mw):
        self._mw = mw

    def on_enter(self, cookie_alert: bool = False):
        # cookie_alert 参数保留兼容旧调用；提示条在系统总览「Cookie设置」
        del cookie_alert
        self.reload_from_prefs()
        QTimer.singleShot(0, self._refresh_all_component_status)
        QTimer.singleShot(0, self._refresh_hotkey_card)

    def restyle_theme(self):
        """主题切换后重刷本页内联样式。"""
        for lbl in self._theme_titles:
            try:
                _restyle_about_title(lbl)
            except Exception:
                pass
        self._style_auto_dl_chrome()
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

    def _build_comp_card(
        self,
        obj_name: str,
        title: str,
        intro: str,
        prefix: str,
        on_check,
        on_install,
    ) -> QFrame:
        """组件卡同一套：标题 / 介绍 / 状态 / 检测+安装教程 / 提示。"""
        card = make_card(obj_name)
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        self._add_title(card, box, title)

        lbl_intro = QLabel(intro)
        lbl_intro.setWordWrap(True)
        lbl_intro.setStyleSheet(self._comp_text_qss("intro"))
        box.addWidget(lbl_intro)
        self._comp_text_labels.append((lbl_intro, "intro"))

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(6)
        glyph = _StatusGlyph()
        lbl_status = QLabel("…")
        lbl_status.setWordWrap(True)
        lbl_status.setStyleSheet(self._comp_text_qss("status"))
        lbl_status._status_glyph = glyph
        lbl_status._comp_prefix = prefix
        status_row.addWidget(glyph, 0, Qt.AlignVCenter)
        status_row.addWidget(lbl_status, 1)
        box.addLayout(status_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_check = apply_medium_button(QPushButton("检测"))
        btn_install = apply_medium_button(QPushButton("安装教程"))
        btn_check.setMinimumWidth(56)
        btn_install.setMinimumWidth(80)
        btn_row.addWidget(btn_check)
        btn_row.addWidget(btn_install)
        btn_row.addStretch(1)
        box.addLayout(btn_row)

        lbl_hint = QLabel(_COMP_HINT)
        lbl_hint.setWordWrap(True)
        lbl_hint.setStyleSheet(self._comp_text_qss("hint"))
        box.addWidget(lbl_hint)
        self._comp_text_labels.append((lbl_hint, "hint"))

        btn_check.clicked.connect(on_check)
        btn_install.clicked.connect(on_install)
        box.addStretch(1)

        setattr(self, f"lbl_{prefix}_intro", lbl_intro)
        setattr(self, f"lbl_{prefix}_status", lbl_status)
        setattr(self, f"btn_{prefix}_check", btn_check)
        setattr(self, f"btn_{prefix}_install", btn_install)
        setattr(self, f"lbl_{prefix}_hint", lbl_hint)
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
            "CardAboutOcr", "截图 OCR", "截图后用 PaddleOCR-VL-1.6 识字",
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
            "CardAboutRecord", "录屏组件", "区域录屏需要 ffmpeg",
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
            "CardAboutVoice", "语音组件", "录音后转成文字",
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
            "CardAboutClone", "语音克隆", "本地 CosyVoice3 克隆音色",
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
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setAlignment(Qt.AlignTop)
        self._add_title(card, box, "预下载 · 域名白名单")

        tip = QLabel(
            "复制内容须包含 https:// 链接才会触发预下载；按链接主机名对照白名单入队"
            "（不立刻下载）。空名单 = 该来源永不入队。"
            "自动处理开关在「系统总览 → 预下载」。"
        )
        tip.setWordWrap(True)
        self.lbl_auto_dl_tip = tip
        box.addWidget(tip)

        cols = QHBoxLayout()
        cols.setSpacing(16)
        video_col = QVBoxLayout()
        video_col.setSpacing(10)
        gallery_col = QVBoxLayout()
        gallery_col.setSpacing(10)

        self.lbl_wl_sec_video = QLabel("视频下载")
        video_col.addWidget(self.lbl_wl_sec_video)
        self.lbl_wl_sec_gallery = QLabel("图集下载")
        gallery_col.addWidget(self.lbl_wl_sec_gallery)
        self._style_auto_dl_chrome()

        for key, section, title in _WHITELIST_GROUPS:
            row = _WhitelistRow(key, title, self)
            row.changed.connect(self._schedule_persist)
            self._whitelist_rows[key] = row
            if section == "视频下载":
                video_col.addWidget(row)
            else:
                gallery_col.addWidget(row)

        # 不要 addStretch：父级一给高，空隙会堆在「全部恢复默认白名单」按钮上方
        video_col.setAlignment(Qt.AlignTop)
        gallery_col.setAlignment(Qt.AlignTop)
        cols.addLayout(video_col, 1)
        cols.addLayout(gallery_col, 1)
        box.addLayout(cols, 0)

        foot = QHBoxLayout()
        foot.addStretch(1)
        btn_all_default = QPushButton("全部恢复默认白名单")
        apply_mini_button(btn_all_default)
        btn_all_default.setCursor(Qt.PointingHandCursor)
        btn_all_default.clicked.connect(self._reset_all_whitelist)
        foot.addWidget(btn_all_default)
        box.addLayout(foot)
        return card

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
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setSpacing(6)
        box.setAlignment(Qt.AlignTop)

        self._add_title(card, box, "热键占用")

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self.btn_hk_clear = apply_live_button(QPushButton("清除占用"))
        self.btn_hk_clear.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.btn_hk_clear.setEnabled(False)
        self.btn_hk_clear.clicked.connect(self._on_clear_hotkeys)
        head.addWidget(self.btn_hk_clear, 0, Qt.AlignVCenter)
        self.lbl_hk_summary = QLabel("")
        head.addWidget(self.lbl_hk_summary, 1, Qt.AlignVCenter)
        box.addLayout(head)

        self._hk_list_host = QWidget()
        self._hk_list_host.setObjectName("AboutHotkeyList")
        self._hk_list_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self._hk_list_lay = QVBoxLayout(self._hk_list_host)
        self._hk_list_lay.setContentsMargins(0, 0, 0, 0)
        self._hk_list_lay.setSpacing(0)
        self._hk_list_lay.setAlignment(Qt.AlignTop)
        box.addWidget(self._hk_list_host, 0, Qt.AlignTop)
        box.addStretch(1)
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

    def _hk_make_row(self, spec: dict, name_qss: str, key_qss: str) -> QWidget:
        state = str(spec.get("state") or "off")
        faint = _lbl_qss(tk("text_faint"), "body")
        line = QWidget()
        line.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        hl = QHBoxLayout(line)
        hl.setContentsMargins(0, 1, 0, 1)
        hl.setSpacing(8)
        key = QLabel(str(spec.get("key") or ""))
        key.setFixedWidth(60)
        key.setStyleSheet(faint if state == "off" else key_qss)
        name = QLabel(str(spec.get("name") or ""))
        name.setStyleSheet(faint if state == "off" else name_qss)
        hl.addWidget(key)
        hl.addWidget(name, 1)
        # 就绪不标，只标占用 / 未启用 / 扩展
        if state != "ok":
            st = QLabel(self._HK_STATE_TEXT.get(state, state))
            st.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            st.setStyleSheet(self._hk_state_qss(state))
            hl.addWidget(st)
        return line

    def _hk_make_col(self, groups, name_qss: str, key_qss: str, grp_qss: str) -> QWidget:
        col_w = QWidget()
        col_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        col = QVBoxLayout(col_w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        col.setAlignment(Qt.AlignTop)
        first = True
        for group, specs in groups:
            hdr = QLabel(group)
            qss = grp_qss if first else _lbl_qss(
                tk("accent"), "group", weight=700, extra="padding-top:8px;"
            )
            hdr.setStyleSheet(qss)
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
        grp_qss = _lbl_qss(tk("accent"), "group", weight=700, extra="padding-top:0px;")

        if not grouped:
            empty = QLabel("打开本页后显示热键清单")
            empty.setStyleSheet(_lbl_qss(tk("text_faint"), "body"))
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
        summary = " · ".join(parts)
        color = tk("err") if n_busy else tk("text_mut")
        if getattr(self, "lbl_hk_summary", None) is not None:
            self.lbl_hk_summary.setText(summary)
            self.lbl_hk_summary.setStyleSheet(_lbl_qss(color, "body"))
        self._sync_hk_clear_btn(n_busy)

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
        if self._loading:
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


