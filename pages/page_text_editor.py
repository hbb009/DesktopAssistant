# pages/page_text_editor.py
# 文本编辑 —— 独立整理排版区（语音编辑分离出来的「编辑整理」部分）
# 功能：主编辑区 + 一键整理/加序号/加符号前缀 + 前缀符号历史卡片（拖拽排序）

import os
import re
from datetime import datetime

from PyQt5.QtCore import Qt, QEvent, QTimer, QSize, QPoint, QRectF, pyqtSignal
from PyQt5.QtGui import (
    QTextCursor, QFont, QPixmap, QPainter, QColor, QPen,
)
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit,
    QSizePolicy, QApplication, QLineEdit, QScrollArea, QFrame,
    QListWidget, QRadioButton, QButtonGroup, QCheckBox,
)

from styles.style_all import (
    theme,
    tk,
    restyle_card_title,
    restyle_card_frame,
    apply_btn_download,
    apply_medium_button,
    MEDIUM_BUTTON_H,
    CARD_LEFT_GAP,
)
from pages.seq_dup_panel import SeqDupPanel
from utils.logger import get_logger

log = get_logger(__name__)
_KEEP_VER_FALLBACK = "v9.18"


# ── 前缀符号历史卡片：参照「提示词」分类卡 —— 彩色圆角卡 + 影子拖拽排序 ──
_PREFIX_MAX = 24          # 历史前缀上限
_PREFIX_CARD_H = 30       # 卡片高（压缩以适配小窗口）
_PREFIX_CARD_MIN_W = 46   # 卡片最小宽
_PREFIX_DRAG_PX = 6       # 拖拽启动阈值
_PREFIX_GUIDE_W = 12      # 拖拽插入虚线宽
_PREFIX_SPACING = 6
_PREFIX_PALETTE = (
    "#3b82f6", "#8b5cf6", "#ec4899", "#ef4444", "#f97316",
    "#eab308", "#22c55e", "#14b8a6", "#06b6d4", "#f43f5e",
    "#6366f1", "#a855f7",
)


def _mix_hex(a: str, b: str, t: float) -> str:
    ca, cb = QColor(a), QColor(b)
    return QColor(
        int(ca.red() + (cb.red() - ca.red()) * t),
        int(ca.green() + (cb.green() - ca.green()) * t),
        int(ca.blue() + (cb.blue() - ca.blue()) * t),
    ).name()


def _pfx_contrast_fg(bg: str) -> str:
    try:
        c = QColor(bg)
        lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
        return "#111111" if lum > 150 else "#ffffff"
    except Exception:
        return "#ffffff"


class _PrefixCard(QWidget):
    """前缀卡片：彩色圆角（仿提示词分类卡）。单击回填 / ✕删除 / 按住拖动排序。

    拖拽方式与提示词页一致：不弹 QDrag，原地保留 + 半透明影子跟手 +
    卡片间虚线插入线，松手落位后重排。
    """

    clicked = pyqtSignal(str)
    remove_requested = pyqtSignal(str)

    def __init__(self, symbol: str, accent: str, owner, parent=None):
        super().__init__(parent)
        self.symbol = symbol
        self._owner = owner
        self._press = None
        self._dragging = False
        self.setObjectName("VoicePrefixCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(False)
        self.setCursor(Qt.OpenHandCursor)
        self.setFixedHeight(_PREFIX_CARD_H)
        self.setMinimumWidth(_PREFIX_CARD_MIN_W)

        self._accent = accent
        self._selected = False
        self._apply_card_style()

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 20, 0)
        lay.setSpacing(0)
        lab = QLabel(symbol)
        lab.setAlignment(Qt.AlignCenter)
        lab.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(lab, 1)
        self._lab = lab

        self.btn_x = QPushButton("✕", self)
        self.btn_x.setObjectName("VoicePrefixCardDel")
        self.btn_x.setCursor(Qt.PointingHandCursor)
        self.btn_x.setFocusPolicy(Qt.NoFocus)
        self.btn_x.setFixedSize(18, 18)
        self.btn_x.clicked.connect(lambda: self.remove_requested.emit(self.symbol))
        self._place_x()

    def _place_x(self):
        b = self.btn_x
        b.setGeometry(self.width() - b.width() - 1, 1, b.width(), b.height())
        b.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_x()

    def apply_theme(self):
        self._apply_card_style()

    def set_selected(self, selected: bool):
        selected = bool(selected)
        if selected == self._selected:
            return
        self._selected = selected
        self._apply_card_style()

    def _apply_card_style(self):
        accent = self._accent
        fg = _pfx_contrast_fg(accent)
        # 选中/未选中都用 2px，避免白框出现时卡片内缩一圈
        bd = "#ffffff" if self._selected else _mix_hex(accent, "#ffffff", 0.10)
        self.setStyleSheet(
            "#VoicePrefixCard{background-color:%s;border:2px solid %s;"
            "border-radius:8px;}"
            "#VoicePrefixCard QLabel{background:transparent;border:none;color:%s;"
            "font-size:12px;font-weight:600;}"
            "QPushButton#VoicePrefixCardDel{background:transparent;border:none;"
            "color:rgba(255,255,255,200);font-size:12px;font-weight:700;padding:0;}"
            "QPushButton#VoicePrefixCardDel:hover{color:#111111;}"
            % (accent, bd, fg)
        )

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.globalPos()
            self._dragging = False
        e.accept()

    def mouseMoveEvent(self, e):
        if self._press is None or not (e.buttons() & Qt.LeftButton):
            return
        if not self._dragging:
            if (e.globalPos() - self._press).manhattanLength() < _PREFIX_DRAG_PX:
                return
            self._dragging = True
            self.setCursor(Qt.ClosedHandCursor)
            self._owner._begin_prefix_drag(self)
        self._owner._move_prefix_drag(e.globalPos())

    def mouseReleaseEvent(self, e):
        was_drag = self._dragging
        if was_drag:
            self._owner._end_prefix_drag()
        self._dragging = False
        self._press = None
        self.setCursor(Qt.OpenHandCursor)
        if e.button() == Qt.LeftButton and not was_drag:
            if self.rect().contains(e.pos()):
                self.clicked.emit(self.symbol)
        super().mouseReleaseEvent(e)


class _PrefixEmptyCard(QWidget):
    """无历史时的占位「前缀卡」：虚线圆角框 + 中央「空」。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("VoicePrefixEmptyCard")
        self.setFixedSize(64, _PREFIX_CARD_H)
        self.setCursor(Qt.ArrowCursor)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        try:
            dark = bool(theme.is_dark)
        except Exception:
            dark = True
        rim = QColor("#4b5a85" if dark else "#c2cce0")
        dim = QColor("#6f7fa8" if dark else "#94a3b8")
        pen = QPen(rim, 1, Qt.CustomDashLine)
        pen.setDashPattern([3, 3])
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8.0, 8.0)
        p.setPen(dim)
        f = QFont()
        f.setPixelSize(12)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, "空")
        p.end()


class _PrefixDragPreview(QWidget):
    """拖拽半透明影子：跟鼠标走，原卡不动。"""

    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self.setObjectName("VoicePrefixDragPreview")
        self._pm = QPixmap(pixmap)
        self.setFixedSize(max(1, self._pm.width()), max(1, self._pm.height()))
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.hide()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setOpacity(0.55)
        p.drawPixmap(0, 0, self._pm)
        p.end()


class _PrefixDropGuide(QWidget):
    """拖拽插入虚线：竖在两张卡之间或最前/最后。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("VoicePrefixDropGuide")
        self.setFixedSize(_PREFIX_GUIDE_W, _PREFIX_CARD_H)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.hide()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        try:
            dark = bool(theme.is_dark)
        except Exception:
            dark = True
        color = QColor("#ffffff" if dark else "#334155")
        pen = QPen(color, 2, Qt.CustomDashLine)
        pen.setDashPattern([3, 3])
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        x = self.width() // 2
        p.drawLine(x, 3, x, self.height() - 3)
        p.end()


_CHAR_CELL_H = 22
_CHAR_GAP = 3
_CHAR_PREVIEW_MAX_W = 280


class _CharPreview(QWidget):
    """输入框右侧：每个字符一块小格子，空格显示成可见空位。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SymbolCharPreview")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(28)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        f = QFont(self.font())
        f.setPixelSize(12)
        self.setFont(f)
        self._text = ""
        self.hide()

    def set_text(self, text: str):
        text = text if isinstance(text, str) else str(text or "")
        if text == self._text:
            self._sync_width()
            return
        self._text = text
        self.setVisible(bool(text))
        self._sync_width()
        self.update()

    def _cells(self):
        fm = self.fontMetrics()
        out = []
        for ch in self._text:
            space = ch in (" ", "\t", "\u00a0", "\u3000")
            if ch == "\t":
                shown = "⇥"
            elif space:
                shown = "␣"
            else:
                shown = ch
            w = max(18, int(fm.horizontalAdvance(shown)) + 10)
            if ch == "\t":
                w = max(w, 28)
            elif ch == "\u3000":
                w = max(w, 24)
            out.append((shown, space, w))
        return out

    def _content_w(self) -> int:
        cells = self._cells()
        if not cells:
            return 0
        return int(sum(c[2] for c in cells) + _CHAR_GAP * (len(cells) - 1))

    def _sync_width(self):
        w = self._content_w()
        if w <= 0:
            self.setFixedWidth(0)
        else:
            cap = _CHAR_PREVIEW_MAX_W
            parent = self.parentWidget()
            if parent is not None and parent.width() > 0:
                cap = min(cap, max(18, parent.width()))
            self.setFixedWidth(min(cap, w))
        self.updateGeometry()

    def sizeHint(self):
        return QSize(min(_CHAR_PREVIEW_MAX_W, self._content_w()) if self._text else 0, 28)

    def minimumSizeHint(self):
        return QSize(0, 28)

    def paintEvent(self, _e):
        if not self._text:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        try:
            fg = QColor(tk("text"))
            dim = QColor(tk("text_dim"))
            bd = QColor(tk("border"))
            bg = QColor(tk("input_bg"))
        except Exception:
            fg = QColor("#e2e8f0")
            dim = QColor("#94a3b8")
            bd = QColor("#475569")
            bg = QColor("#1e293b")
        y = (self.height() - _CHAR_CELL_H) / 2.0
        x = 0.0
        limit = float(self.width())
        for shown, space, w in self._cells():
            if x >= limit:
                break
            r = QRectF(x + 0.5, y + 0.5, min(w - 1.0, limit - x - 0.5), _CHAR_CELL_H - 1.0)
            if space:
                p.setBrush(Qt.NoBrush)
                pen = QPen(dim, 1, Qt.DashLine)
                p.setPen(pen)
                p.drawRoundedRect(r, 4, 4)
                p.setPen(dim)
            else:
                p.setBrush(bg)
                p.setPen(QPen(bd, 1))
                p.drawRoundedRect(r, 4, 4)
                p.setPen(fg)
            p.drawText(r, Qt.AlignCenter, shown)
            x += w + _CHAR_GAP
        p.end()


class _PrefixHScroll(QScrollArea):
    """横向滚动区：滚轮转横滑，像提示词分区卡那行。"""

    def wheelEvent(self, e):
        bar = self.horizontalScrollBar()
        delta = e.angleDelta().x() or e.angleDelta().y()
        if bar is not None and bar.maximum() > 0 and delta:
            bar.setValue(bar.value() - delta)
            e.accept()
            return
        super().wheelEvent(e)


_SEP_PX = 3
_UNDO_MAX = 5           # 操作记录「撤销 / 重做」最多 5 步
_UNDO_IDLE_MS = 700     # 连续输入停这么久算一步


class PageTextEditor(QWidget):
    """文本编辑：左编辑区；右栏三区「操作记录 / 文本操作 / 文件处理」。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._theme_titles = []
        self._theme_frames = []
        self._func_cards = []
        self._h_seps = []
        self._symbol_history = []      # 用过的前缀符号（顺序即卡片顺序）
        self._selected_symbol = None   # 当前选中的前缀卡（白框）；点空白取消
        self._prefs_dirty = None       # 主窗口注入：有变动就安排落盘 user.txt
        self._click_filter_on = False
        self._undo_stack = []
        self._redo_stack = []
        self._last_snapshot = ""
        self._suspend_history = False
        self._undo_timer = QTimer(self)
        self._undo_timer.setSingleShot(True)
        self._undo_timer.setInterval(_UNDO_IDLE_MS)
        self._undo_timer.timeout.connect(self._commit_typed_undo)

        self._build_ui()
        theme.changed.connect(self.refresh_theme)
        self._install_blank_click_filter()

    def minimumSizeHint(self):
        return QSize(0, 0)

    def sizeHint(self):
        return QSize(900, 600)

    def _section_title(self, text: str) -> QLabel:
        """栏内标题：只刷字色，不 restyle 容器（避免内层再套一张功能卡）。"""
        lbl = QLabel(text)
        lbl.setProperty("role", "card-title")
        restyle_card_title(lbl)
        self._theme_titles.append(lbl)
        return lbl

    def _make_hsep(self) -> QFrame:
        f = QFrame()
        f.setObjectName("TextEditorHSep")
        f.setFrameShape(QFrame.NoFrame)
        f.setFixedHeight(_SEP_PX)
        f.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        f.setAttribute(Qt.WA_StyledBackground, True)
        self._h_seps.append(f)
        return f

    # ── 构建 ──────────────────────────────────────────────────────────
    def _build_ui(self):
        # ContentRoot 已有左右 14px。再写 CARD_LEFT_GAP 会叠成 26px。
        # 与积分计算 / 系统总览相同：页面 0 边距，不再套第二层。
        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        page.addLayout(body, 1)

        # ══ 左 70%：编辑区 + 快速保持 / 复制 / 清空 ══════════════════
        left = QWidget()
        left.setObjectName("TextEditorLeft")
        left.setAttribute(Qt.WA_StyledBackground, True)
        left.setStyleSheet("#TextEditorLeft{background:transparent;border:none;}")
        left.setMinimumWidth(0)
        left.setMinimumHeight(0)
        left.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        cve = QVBoxLayout(left)
        cve.setContentsMargins(0, 0, 0, 0)
        cve.setSpacing(4)

        row_title = QHBoxLayout()
        row_title.setContentsMargins(0, 0, 0, 0)
        row_title.setSpacing(8)
        row_title.addWidget(self._section_title("编辑区"), 0, Qt.AlignVCenter)
        row_title.addStretch(1)
        self.chk_word_wrap = QCheckBox("自动换行")
        self.chk_word_wrap.setChecked(True)
        self.chk_word_wrap.setStyleSheet("background:transparent;")
        self.chk_word_wrap.setMinimumWidth(0)
        self.chk_word_wrap.setCursor(Qt.PointingHandCursor)
        self.chk_word_wrap.toggled.connect(self._on_word_wrap_toggled)
        row_title.addWidget(self.chk_word_wrap, 0, Qt.AlignVCenter)
        cve.addLayout(row_title)

        self.editor = QTextEdit()
        self.editor.setObjectName("PasteContentEdit")
        self.editor.setAcceptRichText(False)
        self.editor.setLineWrapMode(QTextEdit.WidgetWidth)
        self.editor.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.editor.setMinimumHeight(0)
        self.editor.setMinimumWidth(0)
        self.editor.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        try:
            self.editor.document().setDocumentMargin(0)
        except Exception:
            pass
        self.editor.setViewportMargins(0, 0, 0, 0)
        self.editor.setUndoRedoEnabled(False)
        self.editor.textChanged.connect(self._on_editor_text_changed)
        cve.addWidget(self.editor, 1)

        row_edit_btn = QHBoxLayout()
        row_edit_btn.setSpacing(8)
        row_edit_btn.addStretch(1)
        self.btn_quick_keep = apply_medium_button(QPushButton("快速保持"))
        self.btn_quick_keep.setMinimumHeight(28)
        self.btn_quick_keep.setMinimumWidth(0)
        self.btn_quick_keep.clicked.connect(self._quick_keep)
        self.btn_copy = QPushButton("复制")
        apply_btn_download(self.btn_copy)
        self.btn_copy.setMinimumHeight(28)
        self.btn_copy.setMinimumWidth(0)
        self.btn_copy.setCursor(Qt.PointingHandCursor)
        self.btn_copy.clicked.connect(self._copy_text)
        self.btn_clear_editor = apply_medium_button(QPushButton("清空编辑区"))
        self.btn_clear_editor.setMinimumHeight(28)
        self.btn_clear_editor.setMinimumWidth(0)
        self.btn_clear_editor.clicked.connect(self._on_clear_editor)
        row_edit_btn.addWidget(self.btn_quick_keep, 0)
        row_edit_btn.addWidget(self.btn_copy, 0)
        row_edit_btn.addWidget(self.btn_clear_editor, 0)
        cve.addLayout(row_edit_btn)


        # ══ 右 30%：操作记录 | 编辑 | 文件处理（三区，无互斥蒙层）══
        right = QWidget()
        right.setObjectName("TextEditorRight")
        right.setAttribute(Qt.WA_StyledBackground, True)
        right.setStyleSheet("#TextEditorRight{background:transparent;border:none;}")
        right.setMinimumWidth(0)
        right.setMinimumHeight(0)
        right.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(4)

        # —— 1) 操作记录 ——
        card_log = QWidget()
        card_log.setObjectName("CardTextEditorLog")
        card_log.setAttribute(Qt.WA_StyledBackground, True)
        card_log.setStyleSheet("#CardTextEditorLog{background:transparent;border:none;}")
        card_log.setMinimumHeight(0)
        card_log.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        cv3 = QVBoxLayout(card_log)
        cv3.setContentsMargins(0, 0, 0, 0)
        cv3.setSpacing(2)
        row_log_title = QHBoxLayout()
        row_log_title.setContentsMargins(0, 0, 0, 0)
        row_log_title.setSpacing(6)
        row_log_title.addWidget(self._section_title("操作记录"), 0, Qt.AlignVCenter)
        row_log_title.addStretch(1)
        self.btn_undo = apply_medium_button(QPushButton("撤销"))
        self.btn_undo.setMinimumHeight(MEDIUM_BUTTON_H)
        self.btn_undo.setMinimumWidth(0)
        self.btn_undo.setCursor(Qt.PointingHandCursor)
        self.btn_undo.clicked.connect(self._undo_editor)
        self.btn_redo = apply_medium_button(QPushButton("重做"))
        self.btn_redo.setMinimumHeight(MEDIUM_BUTTON_H)
        self.btn_redo.setMinimumWidth(0)
        self.btn_redo.setCursor(Qt.PointingHandCursor)
        self.btn_redo.clicked.connect(self._redo_editor)
        row_log_title.addWidget(self.btn_undo, 0)
        row_log_title.addWidget(self.btn_redo, 0)
        cv3.addLayout(row_log_title)
        self.log = QListWidget()
        self.log.setObjectName("SimpleRecordList")
        self.log.setMinimumWidth(0)
        self.log.setMinimumHeight(0)
        self.log.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.log.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.log.setWordWrap(True)
        self.log.setTextElideMode(Qt.ElideRight)
        cv3.addWidget(self.log, 1)
        rv.addWidget(card_log, 28)
        self._refresh_undo_btns()

        # —— 2) 编辑（整理 + 加序号 + 加符号，压成约 4 行）——
        edit_panel = QWidget()
        edit_panel.setObjectName("TextEditorEditZone")
        edit_panel.setAttribute(Qt.WA_StyledBackground, True)
        edit_panel.setStyleSheet("#TextEditorEditZone{background:transparent;border:none;}")
        edit_panel.setMinimumHeight(0)
        edit_panel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        cve_r = QVBoxLayout(edit_panel)
        cve_r.setContentsMargins(0, 0, 0, 0)
        cve_r.setSpacing(4)
        cve_r.setAlignment(Qt.AlignTop)
        cve_r.addWidget(self._section_title("文本操作"), 0, Qt.AlignLeft)
        cve_r.addWidget(self._make_hsep())

        row_tools = QHBoxLayout()
        row_tools.setSpacing(4)
        self.btn_one_para = apply_medium_button(QPushButton("整理成1段"))
        self.btn_one_para.setMinimumHeight(MEDIUM_BUTTON_H)
        self.btn_one_para.setMinimumWidth(0)
        self.btn_one_para.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_one_para.setCursor(Qt.PointingHandCursor)
        self.btn_one_para.clicked.connect(self._collapse_to_one_paragraph)
        self.btn_del_blank = apply_medium_button(QPushButton("删除空行"))
        self.btn_del_blank.setMinimumHeight(MEDIUM_BUTTON_H)
        self.btn_del_blank.setMinimumWidth(0)
        self.btn_del_blank.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_del_blank.setCursor(Qt.PointingHandCursor)
        self.btn_del_blank.clicked.connect(self._remove_blank_lines)
        row_tools.addWidget(self.btn_one_para, 1)
        row_tools.addWidget(self.btn_del_blank, 1)
        cve_r.addLayout(row_tools)
        cve_r.addWidget(self._make_hsep())

        row_num = QHBoxLayout()
        row_num.setSpacing(4)
        self.btn_add_num = apply_medium_button(QPushButton("加序号"))
        self.btn_add_num.setMinimumHeight(MEDIUM_BUTTON_H)
        self.btn_add_num.setMinimumWidth(0)
        self.btn_add_num.setCursor(Qt.PointingHandCursor)
        self.btn_add_num.clicked.connect(self._add_line_numbers)
        row_num.addWidget(self.btn_add_num, 0)
        self.inp_num_start = QLineEdit("1")
        self.inp_num_start.setObjectName("CalcInput")
        self.inp_num_start.setToolTip("起始序号（正整数；无效时按 1）")
        self.inp_num_start.setFixedHeight(MEDIUM_BUTTON_H)
        self.inp_num_start.setFixedWidth(44)
        self.inp_num_start.setAlignment(Qt.AlignCenter)
        self.inp_num_start.setMaxLength(6)
        row_num.addWidget(self.inp_num_start, 0)
        self._num_style_group = QButtonGroup(self)
        self.radio_num_abc = QRadioButton("ABC")
        self.radio_num_123 = QRadioButton("123")
        self.radio_num_123.setChecked(True)
        for r in (self.radio_num_abc, self.radio_num_123):
            r.setStyleSheet("background:transparent;")
            r.setMinimumWidth(0)
            r.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            r.setCursor(Qt.PointingHandCursor)
            self._num_style_group.addButton(r)
            row_num.addWidget(r, 0)
        row_num.addStretch(1)
        cve_r.addLayout(row_num)
        cve_r.addWidget(self._make_hsep())

        row_add_sym = QHBoxLayout()
        row_add_sym.setSpacing(4)
        self.btn_add_symbol = apply_medium_button(QPushButton("加符号"))
        self.btn_add_symbol.setMinimumHeight(MEDIUM_BUTTON_H)
        self.btn_add_symbol.setMinimumWidth(0)
        self.btn_add_symbol.setCursor(Qt.PointingHandCursor)
        self.btn_add_symbol.clicked.connect(self._add_symbol_prefix)
        row_add_sym.addWidget(self.btn_add_symbol, 0)
        self.inp_symbol = QLineEdit()
        self.inp_symbol.setObjectName("CalcInput")
        self.inp_symbol.setPlaceholderText("符号编辑")
        self.inp_symbol.setFixedHeight(MEDIUM_BUTTON_H)
        self.inp_symbol.setMinimumWidth(0)
        self.inp_symbol.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.inp_symbol.setClearButtonEnabled(True)
        self.inp_symbol.setCursor(Qt.IBeamCursor)
        row_add_sym.addWidget(self.inp_symbol, 1)
        self.btn_symbol_save = apply_medium_button(QPushButton("+"))
        self.btn_symbol_save.setFixedWidth(36)
        self.btn_symbol_save.setMinimumHeight(28)
        self.btn_symbol_save.setCursor(Qt.PointingHandCursor)
        self.btn_symbol_save.setToolTip("把当前前缀内容直接存成一张「前缀卡」")
        self.btn_symbol_save.clicked.connect(self._save_current_symbol_as_card)
        row_add_sym.addWidget(self.btn_symbol_save, 0)
        self.symbol_preview = _CharPreview()
        self.symbol_preview.setMinimumWidth(0)
        row_add_sym.addWidget(self.symbol_preview, 0, Qt.AlignVCenter)
        cve_r.addLayout(row_add_sym)
        self.inp_symbol.textChanged.connect(self.symbol_preview.set_text)

        self.symbol_row = QWidget()
        self.symbol_row.setObjectName("VoicePrefixRow")
        self.symbol_row.setAttribute(Qt.WA_StyledBackground, True)
        self.symbol_row.setStyleSheet("#VoicePrefixRow{background:transparent;border:none;}")
        self.symbol_row.setMinimumWidth(0)
        sr = QHBoxLayout(self.symbol_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.setSpacing(4)
        self.prefix_scroll = _PrefixHScroll()
        self.prefix_scroll.setObjectName("VoicePrefixScroll")
        self.prefix_scroll.setWidgetResizable(True)
        self.prefix_scroll.setFrameShape(QFrame.NoFrame)
        self.prefix_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.prefix_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.prefix_scroll.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.prefix_scroll.setFixedHeight(_PREFIX_CARD_H + 4)
        self.prefix_scroll.setMinimumWidth(0)
        self.prefix_scroll.setAttribute(Qt.WA_StyledBackground, True)
        self.prefix_scroll.setStyleSheet(
            "#VoicePrefixScroll{background:transparent;border:none;}"
            "#VoicePrefixScroll > QWidget > QWidget{background:transparent;}"
        )
        try:
            self.prefix_scroll.viewport().setAutoFillBackground(False)
            self.prefix_scroll.viewport().setStyleSheet("background:transparent;")
        except Exception:
            pass
        self.prefix_host = QWidget()
        self.prefix_host.setObjectName("VoicePrefixHost")
        self.prefix_host.setAttribute(Qt.WA_StyledBackground, True)
        self.prefix_host.setStyleSheet("#VoicePrefixHost{background:transparent;border:none;}")
        self.prefix_flow = QHBoxLayout(self.prefix_host)
        self.prefix_flow.setContentsMargins(0, 0, 0, 0)
        self.prefix_flow.setSpacing(_PREFIX_SPACING)
        self.prefix_flow.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.prefix_scroll.setWidget(self.prefix_host)
        sr.addWidget(self.prefix_scroll, 1)
        sr.setAlignment(Qt.AlignTop)
        self._pfx_guide = _PrefixDropGuide(self.prefix_host)
        self._pfx_preview = None
        self._pfx_drag_sym = None
        self._pfx_grab = None
        self._pfx_drag_card = None
        self.symbol_row.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Maximum)
        cve_r.addWidget(self.symbol_row, 0, Qt.AlignTop)
        self._refresh_symbol_chips()
        self._restyle_sep()
        rv.addWidget(edit_panel, 32)

        # —— 3) 文件处理（序列 + 查重）——
        self.seq_dup = SeqDupPanel()
        try:
            self.seq_dup.prefs_changed.connect(self._mark_prefs_dirty)
        except Exception:
            pass
        self._theme_frames.append(self.seq_dup)
        rv.addWidget(self.seq_dup, 40)

        body.addWidget(left, 70)
        body.addWidget(right, 30)


    # ── 对外接口 ──────────────────────────────────────────────────────
    def load_text(self, text: str, append: bool = False):
        """供外部（语音录入等）把文字送入编辑器。append=False 覆盖当前内容。"""
        text = (text or "").strip()
        if not text:
            return
        if append:
            cur = self.editor.toPlainText().strip()
            if cur:
                text = cur + "\n\n" + text
        self._mutate_editor(text, "已接收待编辑文本")

    def focus_editor(self):
        self.editor.setFocus()
        self.editor.activateWindow()

    def editor_text(self) -> str:
        return self.editor.toPlainText()

    def set_prefs_dirty_callback(self, fn):
        """主窗口注入：有变动时安排 user.txt 落盘。"""
        self._prefs_dirty = fn

    def _refresh_undo_btns(self):
        cur = self.editor.toPlainText() if hasattr(self, "editor") else ""
        can_undo = bool(self._undo_stack) or cur != self._last_snapshot
        can_redo = bool(self._redo_stack)
        if hasattr(self, "btn_undo"):
            self.btn_undo.setEnabled(can_undo)
        if hasattr(self, "btn_redo"):
            self.btn_redo.setEnabled(can_redo)

    def _apply_editor_text(self, text: str):
        self._suspend_history = True
        try:
            self.editor.setPlainText(text)
            c = self.editor.textCursor()
            c.movePosition(QTextCursor.End)
            self.editor.setTextCursor(c)
        finally:
            self._suspend_history = False

    def _trim_undo(self):
        extra = len(self._undo_stack) - _UNDO_MAX
        if extra > 0:
            del self._undo_stack[:extra]

    def _commit_typed_undo(self):
        """输入停顿后：把停顿前的快照收成一步。"""
        if self._suspend_history or not hasattr(self, "editor"):
            return
        cur = self.editor.toPlainText()
        if cur == self._last_snapshot:
            return
        self._undo_stack.append(self._last_snapshot)
        self._trim_undo()
        self._last_snapshot = cur
        self._redo_stack.clear()
        self._refresh_undo_btns()

    def _on_editor_text_changed(self):
        if self._suspend_history:
            return
        self._mark_prefs_dirty()
        self._undo_timer.start()
        self._refresh_undo_btns()

    def _mutate_editor(self, new_text: str, log_msg: str = ""):
        """工具按钮改正文：先落下正在打的字，再记一步后写入。"""
        self._undo_timer.stop()
        self._commit_typed_undo()
        cur = self.editor.toPlainText()
        if new_text == cur:
            if log_msg:
                self._log(log_msg)
            return
        self._undo_stack.append(cur)
        self._trim_undo()
        self._redo_stack.clear()
        self._apply_editor_text(new_text)
        self._last_snapshot = new_text
        self._refresh_undo_btns()
        self._mark_prefs_dirty()
        if log_msg:
            self._log(log_msg)

    def _undo_editor(self):
        self._undo_timer.stop()
        cur = self.editor.toPlainText()
        if cur != self._last_snapshot:
            self._redo_stack.append(cur)
            self._apply_editor_text(self._last_snapshot)
            self._refresh_undo_btns()
            self._mark_prefs_dirty()
            self._log("已撤销")
            return
        if not self._undo_stack:
            return
        self._redo_stack.append(cur)
        prev = self._undo_stack.pop()
        self._last_snapshot = prev
        self._apply_editor_text(prev)
        self._refresh_undo_btns()
        self._mark_prefs_dirty()
        self._log("已撤销")

    def _redo_editor(self):
        if not self._redo_stack:
            return
        self._undo_timer.stop()
        self._commit_typed_undo()
        cur = self.editor.toPlainText()
        nxt = self._redo_stack.pop()
        self._undo_stack.append(cur)
        self._trim_undo()
        self._last_snapshot = nxt
        self._apply_editor_text(nxt)
        self._refresh_undo_btns()
        self._mark_prefs_dirty()
        self._log("已重做")

    # ── 主题 ──────────────────────────────────────────────────────────
    def _restyle_sep(self):
        # 占位高度仍用 _SEP_PX，只把可见线画成 1px，不改布局间距
        color = tk("border")
        qss = (
            "QFrame#TextEditorHSep{"
            f"background:transparent;border:none;border-top:1px solid {color};"
            f"max-height:{_SEP_PX}px;min-height:{_SEP_PX}px;}}"
        )
        for sep in getattr(self, "_h_seps", []) or []:
            sep.setStyleSheet(qss)

    def refresh_theme(self, *_):
        for t in self._theme_titles:
            restyle_card_title(t)
        panel = getattr(self, "seq_dup", None)
        if panel is not None and hasattr(panel, "refresh_theme"):
            try:
                panel.refresh_theme()
            except Exception:
                pass
        for c in self._func_cards:
            restyle_card_frame(c)
        self._restyle_sep()
        prev = getattr(self, "symbol_preview", None)
        if prev is not None:
            prev.update()
        for w in self._prefix_cards():
            w.apply_theme()

    # ── 日志 ──────────────────────────────────────────────────────────
    def _log(self, text: str):
        self.log.addItem(text)
        self.log.scrollToBottom()

    # ── 编辑工具 ──────────────────────────────────────────────────────
    def _on_clear_editor(self):
        if not (self.editor.toPlainText() or "").strip():
            self._log("编辑区为空")
            return
        self._mutate_editor("", "已清空编辑区")

    def _copy_text(self):
        text = self.editor.toPlainText().strip()
        if not text:
            self._log("编辑区为空，未复制")
            return
        QApplication.clipboard().setText(text)
        self._log("已复制到剪贴板")

    def _keep_version_tag(self) -> str:
        try:
            title = self.window().windowTitle() or ""
        except Exception:
            title = ""
        found = re.search(r"v\d+(?:\.\d+)+", title)
        return found.group(0) if found else _KEEP_VER_FALLBACK

    def _public_save_root(self) -> str:
        prefs = None
        try:
            prefs = getattr(self.window(), "_user_prefs", None)
        except Exception:
            prefs = None
        from utils.app_paths import public_save_base
        return public_save_base(prefs if isinstance(prefs, dict) else None)

    def _quick_keep(self):
        """把编辑区原文存到公共目录根：桌面助手v9.18_20261022_14.35_01.txt。"""
        raw = self.editor.toPlainText()
        if not (raw or "").strip():
            self._log("编辑区为空，未保存")
            return
        try:
            root = self._public_save_root()
        except Exception:
            log.exception("读取公共目录失败")
            self._log("公共目录不可用，未保存")
            return
        if not root or not os.path.isdir(root):
            self._log("公共目录不可用，未保存")
            return
        now = datetime.now()
        prefix = (
            f"桌面助手{self._keep_version_tag()}_"
            f"{now.strftime('%Y%m%d')}_{now.strftime('%H.%M')}_"
        )
        for n in range(1, 100):
            name = f"{prefix}{n:02d}.txt"
            path = os.path.join(root, name)
            try:
                with open(path, "x", encoding="utf-8") as fp:
                    fp.write(raw)
            except FileExistsError:
                continue
            except OSError as e:
                log.exception("快速保持写入失败 path=%s", path)
                self._log(f"保存失败：{e}")
                return
            self._log(f"已快速保持：{name}")
            return
        self._log("这一分钟已有 99 个文件，未保存")

    def _collapse_to_one_paragraph(self):
        """删除编辑区全部换行符，整理成连续一段（针对 GPT 爱断行）。"""
        raw = self.editor.toPlainText()
        if not (raw or "").strip():
            self._log("编辑区为空，无需整理")
            return
        one = (
            raw.replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\u2028", "\n")
            .replace("\u2029", "\n")
            .replace("\n", "")
        )
        if one == raw:
            self._log("编辑区已无换行，无需整理")
            return
        self._mutate_editor(one, "已整理成1段（已去除全部换行）")

    def _remove_blank_lines(self):
        """删除编辑区里的空行（仅空白的行也去掉），保留有内容的行与顺序。"""
        raw = self.editor.toPlainText()
        if not (raw or "").strip():
            self._log("编辑区为空，无需删除空行")
            return
        norm = (
            raw.replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\u2028", "\n")
            .replace("\u2029", "\n")
        )
        lines = norm.split("\n")
        if not any(not ln.strip() for ln in lines):
            self._log("编辑区没有空行，无需删除")
            return
        kept = [ln for ln in lines if ln.strip()]
        self._mutate_editor("\n".join(kept), "已删除空行")

    @staticmethod
    def _index_to_letters(i: int) -> str:
        """0 → A，25 → Z，26 → AA。"""
        n = int(i) + 1
        chars = []
        while n > 0:
            n, rem = divmod(n - 1, 26)
            chars.append(chr(ord("A") + rem))
        return "".join(reversed(chars))

    def _num_start_value(self) -> int:
        """起始序号：正整数；空/非法回退为 1。"""
        w = getattr(self, "inp_num_start", None)
        raw = ""
        try:
            raw = (w.text() if w is not None else "") or ""
        except Exception:
            raw = ""
        raw = raw.strip()
        if not raw:
            return 1
        try:
            n = int(raw)
        except Exception:
            return 1
        if n < 1:
            return 1
        return n

    def _add_line_numbers(self):
        """按行加序号：123 为 N. N+1…；ABC 为对应字母（超 26 行走 AA）。起始见旁输入框。"""
        raw = self.editor.toPlainText()
        if not (raw or "").strip():
            self._log("编辑区为空，无需加序号")
            return
        lines = [ln.strip() for ln in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip()]
        if not lines:
            self._log("编辑区无有效行，无需加序号")
            return
        start = self._num_start_value()
        use_abc = bool(self.radio_num_abc.isChecked())
        strip_re = re.compile(r"^\s*(?:\d+|[A-Za-z]+)\s*[\.、．\)]\s*")
        cleaned = [strip_re.sub("", ln).strip() or ln for ln in lines]
        if use_abc:
            numbered = [
                f"{self._index_to_letters(start - 1 + i)}. {text}"
                for i, text in enumerate(cleaned)
            ]
            shown = (
                f"{self._index_to_letters(start - 1)}"
                f"–{self._index_to_letters(start - 1 + len(numbered) - 1)}"
            )
        else:
            numbered = [f"{start + i}. {text}" for i, text in enumerate(cleaned)]
            shown = f"{start}–{start + len(numbered) - 1}"
        kind = "ABC" if use_abc else "123"
        self._mutate_editor(
            "\n".join(numbered),
            f"已加序号（{kind}）：{shown}（共 {len(numbered)} 行）",
        )

    def _add_symbol_prefix(self):
        """按行加自定义符号前缀（如「· 」「- 」「→ 」）。"""
        raw = self.editor.toPlainText()
        if not (raw or "").strip():
            self._log("编辑区为空，无需加符号")
            return
        symbol = self.inp_symbol.text() or ""
        if not symbol:
            self.inp_symbol.setFocus()
            self._log("请先在输入框填写符号前缀（可含空格），如「· 」")
            return
        lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        sym_re = re.compile(r"^(\s*)(" + re.escape(symbol) + r")(.*)$")
        out = []
        n_apply = 0
        for line in lines:
            m = sym_re.match(line)
            if m:
                out.append(m.group(1) + symbol + m.group(3))
                n_apply += 1
                continue
            if not (line or "").strip():
                out.append(symbol)
            else:
                out.append(symbol + line)
            n_apply += 1
        shown = symbol if len(symbol) <= 8 else symbol[:8] + "…"
        self._mutate_editor(
            "\n".join(out),
            f"已加符号前缀「{shown}」（共 {n_apply} 行）",
        )
        self._remember_symbol(symbol)

    # ── 前缀符号历史卡片 ─────────────────────────────────────────────
    def _apply_word_wrap(self, checked: bool):
        """开：按窗口宽度折行；关：一行到底，出现横向滚动条。"""
        if checked:
            self.editor.setLineWrapMode(QTextEdit.WidgetWidth)
            self.editor.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        else:
            self.editor.setLineWrapMode(QTextEdit.NoWrap)
            self.editor.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

    def _on_word_wrap_toggled(self, checked: bool):
        self._apply_word_wrap(checked)
        self._mark_prefs_dirty()


    def export_check_dup_settings(self) -> dict:
        panel = getattr(self, "seq_dup", None)
        if panel is None:
            return {}
        try:
            return dict(panel.export_settings() or {})
        except Exception:
            return {}

    def apply_check_dup_settings(self, d: dict) -> None:
        panel = getattr(self, "seq_dup", None)
        if panel is None:
            return
        try:
            panel.apply_settings(d or {})
        except Exception:
            pass


    def on_sequence_fill_done(self, **kwargs):
        panel = getattr(self, "seq_dup", None)
        if panel is not None and hasattr(panel, "on_sequence_fill_done"):
            return panel.on_sequence_fill_done(**kwargs)

    def stop_background_checks(self):
        panel = getattr(self, "seq_dup", None)
        if panel is not None and hasattr(panel, "stop_background_checks"):
            try:
                panel.stop_background_checks()
            except Exception:
                pass

    def shutdown(self):
        self.stop_background_checks()

    def export_settings(self) -> dict:
        """写入 user.txt 的文本编辑段。"""
        wrap = True
        chk = getattr(self, "chk_word_wrap", None)
        if chk is not None:
            wrap = bool(chk.isChecked())
        return {
            "symbol_history": [str(s) for s in self._symbol_history],
            "word_wrap": wrap,
            "editor_text": self.editor.toPlainText() if hasattr(self, "editor") else "",
        }

    def apply_settings(self, data: dict):
        """从 user.txt 恢复前缀历史、换行开关、编辑区正文。"""
        data = data or {}
        if "word_wrap" in data:
            wrap = bool(data.get("word_wrap"))
            chk = getattr(self, "chk_word_wrap", None)
            if chk is not None and chk.isChecked() != wrap:
                chk.blockSignals(True)
                chk.setChecked(wrap)
                chk.blockSignals(False)
                self._apply_word_wrap(wrap)
        if "editor_text" in data and hasattr(self, "editor"):
            text = data.get("editor_text")
            text = text if isinstance(text, str) else str(text or "")
            if self.editor.toPlainText() != text:
                self._suspend_history = True
                try:
                    self.editor.setPlainText(text)
                finally:
                    self._suspend_history = False
            self._last_snapshot = text
            self._undo_stack = []
            self._redo_stack = []
            self._refresh_undo_btns()
        raw = data.get("symbol_history")
        if isinstance(raw, list):
            hist = []
            for s in raw:
                s = str(s)
                if s and s not in hist:
                    hist.append(s)
                    if len(hist) >= _PREFIX_MAX:
                        break
            self._symbol_history = hist
        if hasattr(self, "prefix_host"):
            self._refresh_symbol_chips()

    def _mark_prefs_dirty(self):
        fn = self._prefs_dirty
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

    def _remember_symbol(self, symbol: str):
        """把「这次实际用过的」前缀收进历史（去重、保持添加顺序）。"""
        symbol = symbol or ""
        if not symbol:
            return
        if symbol in self._symbol_history:
            return
        self._symbol_history.append(symbol)
        if len(self._symbol_history) > _PREFIX_MAX:
            del self._symbol_history[: len(self._symbol_history) - _PREFIX_MAX]
        self._refresh_symbol_chips()
        self._mark_prefs_dirty()

    def _save_current_symbol_as_card(self):
        """「+」：把前缀编辑区当前内容直接存成一张前缀卡。"""
        symbol = self.inp_symbol.text() or ""
        if not symbol:
            self._log("前缀编辑区是空的，无法存成前缀卡")
            self.inp_symbol.setFocus()
            return
        existed = symbol in self._symbol_history
        self._remember_symbol(symbol)
        if existed:
            self._log(f"「{symbol}」已在前缀卡中")
        else:
            self._log(f"已把当前前缀「{symbol}」存成前缀卡")

    def _use_symbol(self, symbol: str):
        """单击卡片：选中（白框）并把前缀内容放进编辑框。"""
        self.inp_symbol.setText(symbol)
        self.inp_symbol.setFocus()
        self._select_symbol(symbol)

    def _prefix_cards(self):
        host = getattr(self, "prefix_host", None)
        if host is None:
            return []
        return [w for w in host.children() if isinstance(w, _PrefixCard)]

    def _select_symbol(self, symbol: str):
        self._selected_symbol = symbol or None
        self._sync_card_selection()

    def _clear_symbol_selection(self):
        if not self._selected_symbol:
            return
        self._selected_symbol = None
        self._sync_card_selection()

    def _sync_card_selection(self):
        sel = self._selected_symbol
        for w in self._prefix_cards():
            w.set_selected(w.symbol == sel)

    def _click_keeps_symbol_sel(self, w) -> bool:
        cur = w
        while cur is not None:
            if isinstance(cur, _PrefixCard):
                return True
            if cur is self:
                break
            cur = cur.parentWidget()
        return False

    def _install_blank_click_filter(self):
        app = QApplication.instance()
        if app is None:
            return
        app.installEventFilter(self)
        self._click_filter_on = True
        try:
            self.destroyed.connect(self._teardown_click_filter)
        except Exception:
            pass

    def _teardown_click_filter(self, *_):
        if not getattr(self, "_click_filter_on", False):
            return
        app = QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
            except Exception:
                pass
        self._click_filter_on = False

    def eventFilter(self, obj, ev):
        try:
            if (
                ev.type() == QEvent.MouseButtonPress
                and ev.button() == Qt.LeftButton
                and self._selected_symbol
                and self.isVisible()
                and not getattr(self, "_pfx_drag_sym", None)
            ):
                w = None
                try:
                    w = QApplication.widgetAt(ev.globalPos())
                except Exception:
                    w = obj if isinstance(obj, QWidget) else None
                if w is not None and not self._click_keeps_symbol_sel(w):
                    self._clear_symbol_selection()
        except Exception:
            pass
        return super().eventFilter(obj, ev)

    def _remove_symbol(self, symbol: str):
        if symbol in self._symbol_history:
            if self._selected_symbol == symbol:
                self._selected_symbol = None
            self._symbol_history.remove(symbol)
            self._refresh_symbol_chips()
            self._mark_prefs_dirty()
            self._log(f"已删除前缀卡片「{symbol}」")

    def _refresh_symbol_chips(self):
        """重建彩色前缀卡；无历史时显示一条虚线「空」占位卡。"""
        if not hasattr(self, "prefix_host"):
            return
        self._pfx_clear_drag_visual()
        flow = self.prefix_flow
        while flow.count():
            it = flow.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        if not self._symbol_history:
            empty = _PrefixEmptyCard(self.prefix_host)
            flow.addWidget(empty)
            self._selected_symbol = None
            return
        from PyQt5.QtGui import QFontMetrics
        fm = QFontMetrics(self.prefix_host.font())
        for i, symbol in enumerate(self._symbol_history):
            text = str(symbol)
            accent = _PREFIX_PALETTE[i % len(_PREFIX_PALETTE)]
            card = _PrefixCard(text, accent, self, self.prefix_host)
            w = max(_PREFIX_CARD_MIN_W, fm.horizontalAdvance(text) + 36)
            card.setFixedWidth(w)
            card.clicked.connect(lambda s=text: self._use_symbol(s))
            card.remove_requested.connect(lambda s=text: self._remove_symbol(s))
            flow.addWidget(card)
        flow.addStretch(1)
        if self._selected_symbol and self._selected_symbol not in self._symbol_history:
            self._selected_symbol = None
        self._sync_card_selection()

    # ── 卡片拖拽（影子跟手 + 卡间虚线）────────────────────────────
    def _pfx_card_rects(self):
        out = []
        for w in self.prefix_host.children():
            if isinstance(w, _PrefixCard):
                out.append(w)
        return out

    def _pfx_clear_drag_visual(self):
        g = getattr(self, "_pfx_guide", None)
        if g is not None:
            g.hide()
        pv = getattr(self, "_pfx_preview", None)
        if pv is not None:
            pv.hide()
            pv.setParent(None)
            pv.deleteLater()
        self._pfx_preview = None
        self._pfx_drag_sym = None
        self._pfx_grab = None
        self._pfx_drag_card = None

    def _begin_prefix_drag(self, card):
        self._pfx_clear_drag_visual()
        sym = str(card.symbol or "")
        if sym not in self._symbol_history:
            return
        self._pfx_drag_sym = sym
        self._pfx_drag_card = card
        try:
            card.grabMouse()
        except Exception:
            pass
        pm = card.grab()
        pv = _PrefixDragPreview(pm, self)
        self._pfx_preview = pv
        try:
            pos = card.mapTo(self, QPoint(0, 0))
        except Exception:
            pos = QPoint(0, 0)
        pv.move(pos)
        pv.show()
        pv.raise_()
        grab = QPoint(pm.width() // 2, pm.height() // 2)
        self._pfx_grab = grab
        press = getattr(card, "_press", None)
        if press is not None:
            self._pfx_move_visual(press)

    def _move_prefix_drag(self, gpos):
        sym = self._pfx_drag_sym
        if not sym:
            return
        pv = self._pfx_preview
        if pv is not None:
            grab = self._pfx_grab or QPoint(pv.width() // 2, pv.height() // 2)
            pv.move(self.mapFromGlobal(gpos) - grab)
        self._pfx_move_visual(self.mapFromGlobal(gpos))

    def _pfx_drop_index(self, local: QPoint) -> int:
        cards = self._pfx_card_rects()
        if not cards:
            return 0
        return sum(1 for c in cards if c.x() + c.width() // 2 < local.x())

    def _pfx_guide_line_x(self, insert: int) -> int:
        cards = self._pfx_card_rects()
        n = len(cards)
        if not cards:
            return 0
        if insert <= 0:
            c = cards[0]
            return c.x() - _PREFIX_SPACING // 2
        if insert >= n:
            c = cards[-1]
            return c.x() + c.width() + _PREFIX_SPACING // 2
        left = cards[insert - 1]
        right = cards[insert]
        return (left.x() + left.width() + right.x()) // 2

    def _pfx_move_visual(self, gpos):
        host = self.prefix_host
        local = host.mapFromGlobal(gpos)
        insert = self._pfx_drop_index(local)
        g = self._pfx_guide
        gx = self._pfx_guide_line_x(insert)
        g.move(max(0, gx - _PREFIX_GUIDE_W // 2), 0)
        g.show()
        g.raise_()

    def _end_prefix_drag(self):
        sym = self._pfx_drag_sym
        card = self._pfx_drag_card
        if card is not None:
            try:
                card.releaseMouse()
            except Exception:
                pass
            card.setCursor(Qt.OpenHandCursor)
        if sym:
            cards = self._pfx_card_rects()
            if cards:
                gx = self._pfx_guide.x() + _PREFIX_GUIDE_W // 2
                insert = 0
                for c in cards:
                    if gx > c.x() + c.width() // 2:
                        insert += 1
                self._commit_prefix_order(sym, insert)
        self._pfx_clear_drag_visual()

    def _commit_prefix_order(self, symbol: str, insert: int):
        if symbol not in self._symbol_history:
            return
        old = self._symbol_history.index(symbol)
        if old == insert or old == insert - 1:
            self._refresh_symbol_chips()
            return
        self._symbol_history.pop(old)
        if old < insert:
            insert -= 1
        insert = max(0, min(insert, len(self._symbol_history)))
        self._symbol_history.insert(insert, symbol)
        self._refresh_symbol_chips()
        self._mark_prefs_dirty()
