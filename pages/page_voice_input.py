# pages/page_voice_input.py
# 语音编辑 —— 本地语音转文字（faster-whisper）+ 主编辑排版区
# 侧栏「粘贴助手」下方独立页面

from datetime import datetime
import os
import re

from PyQt5.QtCore import Qt, QTimer, QSize, QPoint, QRectF, pyqtSignal
from PyQt5.QtGui import (
    QTextCursor, QFont, QPixmap, QPainter, QColor, QPen,
)
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit,
    QComboBox, QSizePolicy, QApplication, QListWidget, QListWidgetItem,
    QRadioButton, QButtonGroup, QInputDialog, QFileDialog, QLineEdit,
    QScrollArea, QFrame,
)

from styles.style_all import (
    theme,
    tk,
    make_card,
    install_card_title,
    restyle_card_title,
    restyle_card_frame,
    restyle_func_area,
    apply_btn_download,
    apply_medium_button,
    CARD_LEFT_GAP,
    CARD_TOP_GAP,
    CARD_RIGHT_GAP,
    CARD_TITLE_BODY_GAP,
)
from utils.voice_input import (
    RecordWorker,
    TranscribeWorker,
    TranscribeFileWorker,
    SAMPLE_RATE,
    MODEL_CHOICES,
    LANGUAGE_CHOICES,
    scan_local_models,
    pick_default_model,
    voice_deps_ok,
)


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
        fg = _pfx_contrast_fg(accent)
        self.setStyleSheet(
            "#VoicePrefixCard{background-color:%s;border:1px solid %s;"
            "border-radius:8px;}"
            "#VoicePrefixCard QLabel{background:transparent;border:none;color:%s;"
            "font-size:12px;font-weight:600;}"
            "QPushButton#VoicePrefixCardDel{background:transparent;border:none;"
            "color:rgba(255,255,255,200);font-size:12px;font-weight:700;padding:0;}"
            "QPushButton#VoicePrefixCardDel:hover{color:#111111;}"
            % (accent, _mix_hex(accent, "#ffffff", 0.10), fg)
        )

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
        # 换主题对彩色卡影响很小；只让 hover 提示更新
        pass

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
        if self._dragging:
            self._owner._end_prefix_drag()
        self._dragging = False
        self._press = None
        self.setCursor(Qt.OpenHandCursor)
        if e.button() == Qt.LeftButton and not self._dragging:
            if self.rect().contains(e.pos()):
                self.clicked.emit(self.symbol)
        super().mouseReleaseEvent(e)


def _mix_hex(a: str, b: str, t: float) -> str:
    ca, cb = QColor(a), QColor(b)
    return QColor(
        int(ca.red() + (cb.red() - ca.red()) * t),
        int(ca.green() + (cb.green() - ca.green()) * t),
        int(ca.blue() + (cb.blue() - ca.blue()) * t),
    ).name()


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


# ── 通用 ─────────────────────────────────────────────────────────────


class PageVoiceInput(QWidget):
    """说话 → 本地转写 → 多条记录 → 编辑整理（主编辑/排版）→ 复制。"""

    # 录音模式
    MODE_HOLD = "hold"    # A：按住录音，松手结束
    MODE_CLICK = "click"  # B：点击开始，再点结束

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._state = "idle"  # idle | recording | transcribing
        self._recorder = None
        self._transcriber = None
        self._elapsed = 0
        self._record_seq = 0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)

        self._theme_titles = []
        self._func_cards = []
        self._symbol_history = []      # 用过的前缀符号（顺序即卡片顺序）
        self._prefs_dirty = None       # 主窗口注入：有变动就安排落盘 user.txt

        self._build_ui()
        theme.changed.connect(self.refresh_theme)
        self._refresh_record_btn()
        self._sync_mode_ui()

    def minimumSizeHint(self):
        return QSize(0, 0)

    def sizeHint(self):
        return QSize(900, 640)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        # 分区之间尽量贴紧，避免「按住录音」与下方三大块之间空隙过大
        root.setSpacing(2)

        # ══ 控制卡 ════════════════════════════════════════════════════════
        card = make_card("CardVoiceControl", borderless=True)
        self._func_cards.append(card)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(
            CARD_LEFT_GAP, max(0, CARD_TOP_GAP - 4), CARD_RIGHT_GAP, 4
        )
        cv.setSpacing(8)
        self._theme_titles.append(install_card_title(card, cv, "语音编辑"))

        # 模型 + 语言
        row_set = QHBoxLayout()
        row_set.setSpacing(8)
        lbl_m = QLabel("模型")
        lbl_m.setObjectName("CalcFieldLabel")
        row_set.addWidget(lbl_m)
        self.combo_model = QComboBox()
        self.combo_model.setMinimumWidth(0)
        self.combo_model.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._populate_models()
        row_set.addWidget(self.combo_model, 2)

        self.btn_refresh = apply_medium_button(QPushButton("刷新模型"))
        self.btn_refresh.clicked.connect(self._on_refresh_models)
        row_set.addWidget(self.btn_refresh)

        lbl_l = QLabel("语言")
        lbl_l.setObjectName("CalcFieldLabel")
        row_set.addWidget(lbl_l)
        self.combo_lang = QComboBox()
        for key, label in LANGUAGE_CHOICES:
            self.combo_lang.addItem(label, key)
        idx_zh = self.combo_lang.findData("zh")
        self.combo_lang.setCurrentIndex(idx_zh if idx_zh >= 0 else 0)
        self.combo_lang.setMinimumWidth(100)
        row_set.addWidget(self.combo_lang, 1)
        cv.addLayout(row_set)

        # 录音模式 A / B
        row_mode = QHBoxLayout()
        row_mode.setSpacing(12)
        lbl_mode = QLabel("录音方式")
        lbl_mode.setObjectName("CalcFieldLabel")
        row_mode.addWidget(lbl_mode)

        self._mode_group = QButtonGroup(self)
        self.radio_hold = QRadioButton("A · 按住录音，松手结束")
        self.radio_hold.setStyleSheet("background: transparent;")
        self.radio_click = QRadioButton("B · 点击开始，再点结束")
        self.radio_click.setStyleSheet("background: transparent;")
        self.radio_click.setChecked(True)  # 默认沿用原交互
        self._mode_group.addButton(self.radio_hold)
        self._mode_group.addButton(self.radio_click)
        self.radio_hold.toggled.connect(self._on_mode_changed)
        self.radio_click.toggled.connect(self._on_mode_changed)
        row_mode.addWidget(self.radio_hold)
        row_mode.addWidget(self.radio_click)
        row_mode.addStretch(1)
        cv.addLayout(row_mode)

        # 状态 + 主按钮（开始录音 / 上传音频）
        row_act = QHBoxLayout()
        row_act.setSpacing(10)
        self.lbl_status = QLabel("● 空闲")
        self.lbl_status.setObjectName("StatusLbl")
        self.lbl_status.setStyleSheet(f"color:{tk('text_mut')}; font-size:13px;")
        row_act.addWidget(self.lbl_status, 1)

        self.btn_record = apply_medium_button(QPushButton("开始录音"))
        self.btn_record.setMinimumHeight(36)
        self.btn_record.setMinimumWidth(168)
        self.btn_record.setCursor(Qt.PointingHandCursor)
        # A：pressed / released；B：clicked（互斥，见下方槽）
        self.btn_record.pressed.connect(self._on_record_pressed)
        self.btn_record.released.connect(self._on_record_released)
        self.btn_record.clicked.connect(self._on_record_clicked)
        row_act.addWidget(self.btn_record, 0)

        # 上传音频 → 转文字（不依赖麦克风），放在「开始录音」右侧
        self.btn_file = apply_medium_button(QPushButton("上传音频"))
        self.btn_file.setMinimumHeight(36)
        self.btn_file.setMinimumWidth(168)
        self.btn_file.setCursor(Qt.PointingHandCursor)
        self.btn_file.clicked.connect(self._on_pick_audio_file)
        row_act.addWidget(self.btn_file, 0)

        cv.addLayout(row_act)

        # 依赖提示：启动时可能缺包；关于页安装后需可热刷新（见 refresh_deps / showEvent）
        self.lbl_deps = QLabel("")
        self.lbl_deps.setWordWrap(True)
        self.lbl_deps.setObjectName("ResultHint")
        self.lbl_deps.setStyleSheet(f"color:{tk('err')}; font-size:12px;")
        self.lbl_deps.hide()
        cv.addWidget(self.lbl_deps)
        self.refresh_deps()

        root.addWidget(card, 0)

        # ══ 转写结果（多条记录）════════════════════════════════════════════
        card2 = make_card("CardVoiceResult", borderless=True)
        self._func_cards.append(card2)
        cv2 = QVBoxLayout(card2)
        cv2.setContentsMargins(CARD_LEFT_GAP, 2, CARD_RIGHT_GAP, 4)
        cv2.setSpacing(4)
        self._theme_titles.append(self._install_result_title(card2, cv2))

        self.records = QListWidget()
        self.records.setObjectName("SimpleRecordList")
        self.records.setMinimumHeight(64)
        self.records.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.records.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.records.setWordWrap(True)
        self.records.setTextElideMode(Qt.ElideRight)
        self.records.itemDoubleClicked.connect(self._on_record_item_double_clicked)
        cv2.addWidget(self.records, 1)

        # 转写记录约 1 份；编辑整理约 2 份（高度加一倍，作主编辑/排版区）
        root.addWidget(card2, 1)

        # ══ 编辑整理区（主编辑与排版）════════════════════════════════════
        card_edit = make_card("CardVoiceEditor", borderless=True)
        self._func_cards.append(card_edit)
        cve = QVBoxLayout(card_edit)
        cve.setContentsMargins(CARD_LEFT_GAP, 2, CARD_RIGHT_GAP, 4)
        cve.setSpacing(4)
        self._theme_titles.append(
            install_card_title(card_edit, cve, "编辑整理 · 主编辑区", gap=2)
        )

        self.editor = QTextEdit()
        self.editor.setObjectName("PasteContentEdit")
        self.editor.setAcceptRichText(False)
        self.editor.setLineWrapMode(QTextEdit.WidgetWidth)
        # 原最小高度 90 提到 180 后，再把空间留给下方工具行会被挤没；
        # 编辑器做次要拉伸对象，固定最小值收到 120，保证前缀行可见
        self.editor.setMinimumHeight(120)
        self.editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        cve.addWidget(self.editor, 1)

        row_edit_btn = QHBoxLayout()
        row_edit_btn.setSpacing(8)
        # 最左：把 GPT 爱换行的文稿压成一段
        self.btn_one_para = apply_medium_button(QPushButton("整理成1段"))
        self.btn_one_para.setMinimumHeight(28)
        self.btn_one_para.setCursor(Qt.PointingHandCursor)
        self.btn_one_para.clicked.connect(self._collapse_to_one_paragraph)
        row_edit_btn.addWidget(self.btn_one_para, 0)
        # 「整理成1段」右侧：按行加序号
        self.btn_add_num = apply_medium_button(QPushButton("加序号"))
        self.btn_add_num.setMinimumHeight(28)
        self.btn_add_num.setCursor(Qt.PointingHandCursor)
        self.btn_add_num.clicked.connect(self._add_line_numbers)
        row_edit_btn.addWidget(self.btn_add_num, 0)
        # 主按钮行收尾：复制 / 清空编辑区
        self.btn_copy = QPushButton("复制")
        apply_btn_download(self.btn_copy)
        self.btn_copy.setMinimumHeight(28)
        self.btn_copy.setCursor(Qt.PointingHandCursor)
        self.btn_copy.clicked.connect(self._copy_text)
        self.btn_clear_editor = apply_medium_button(QPushButton("清空编辑区"))
        self.btn_clear_editor.setMinimumHeight(28)
        self.btn_clear_editor.clicked.connect(self.editor.clear)
        row_edit_btn.addWidget(self.btn_copy, 0)
        row_edit_btn.addWidget(self.btn_clear_editor, 0)
        cve.addLayout(row_edit_btn)

        # ── 前缀行：加符号 + 前缀编辑区 + 「当前前缀」提示 …… + ──
        # （与主按钮行分开一行，行末 + 把当前内容直接存成前缀卡）
        row_prefix = QHBoxLayout()
        row_prefix.setSpacing(8)
        self.btn_add_symbol = apply_medium_button(QPushButton("加符号"))
        self.btn_add_symbol.setMinimumHeight(28)
        self.btn_add_symbol.setCursor(Qt.PointingHandCursor)
        self.btn_add_symbol.clicked.connect(self._add_symbol_prefix)
        row_prefix.addWidget(self.btn_add_symbol, 0)
        # 前缀编辑区：内容原样保留（含空格），如「· 」「- 」「→ 」
        self.inp_symbol = QLineEdit()
        self.inp_symbol.setObjectName("VoiceSymbolInput")
        self.inp_symbol.setFixedHeight(self.btn_add_symbol.minimumHeight() or 28)
        self.inp_symbol.setMinimumWidth(150)
        self.inp_symbol.setMaximumWidth(190)
        self.inp_symbol.setClearButtonEnabled(True)
        self.inp_symbol.setCursor(Qt.IBeamCursor)
        row_prefix.addWidget(self.inp_symbol, 0)
        row_prefix.addStretch(1)
        # 行末「+」：把前缀编辑区当前内容直接做成一「前缀卡」
        self.btn_symbol_save = apply_medium_button(QPushButton("+"))
        self.btn_symbol_save.setFixedWidth(36)
        self.btn_symbol_save.setMinimumHeight(28)
        self.btn_symbol_save.setCursor(Qt.PointingHandCursor)
        self.btn_symbol_save.setToolTip("把当前前缀内容直接存成一张「前缀卡」")
        self.btn_symbol_save.clicked.connect(self._save_current_symbol_as_card)
        row_prefix.addWidget(self.btn_symbol_save, 0)
        cve.addLayout(row_prefix)

        # 前缀历史卡片行：用过的「前缀符号」收在这里，单击回填 / ✕删除 / 拖动排序
        self.symbol_row = QWidget()
        self.symbol_row.setObjectName("VoicePrefixRow")
        self.symbol_row.setAttribute(Qt.WA_StyledBackground, True)
        self.symbol_row.setStyleSheet("#VoicePrefixRow{background:transparent;border:none;}")
        sr = QHBoxLayout(self.symbol_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.setSpacing(6)
        # 横向滚动卡片行（仿提示词分区卡）：彩色卡 + 空态虚线「空」
        self.prefix_scroll = _PrefixHScroll()
        self.prefix_scroll.setObjectName("VoicePrefixScroll")
        self.prefix_scroll.setWidgetResizable(True)
        self.prefix_scroll.setFrameShape(QFrame.NoFrame)
        self.prefix_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.prefix_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.prefix_scroll.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.prefix_scroll.setFixedHeight(_PREFIX_CARD_H + 12)
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
        self.prefix_flow.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.prefix_scroll.setWidget(self.prefix_host)
        sr.addWidget(self.prefix_scroll, 1)
        # 拖拽视觉件
        self._pfx_guide = _PrefixDropGuide(self.prefix_host)
        self._pfx_preview = None
        self._pfx_drag_sym = None
        self._pfx_grab = None
        self._pfx_drag_card = None
        cve.addWidget(self.symbol_row)
        # 先画一次：无历史显示虚线「空」卡
        self._refresh_symbol_chips()

        root.addWidget(card_edit, 2)

        # ══ 简短操作记录 ══════════════════════════════════════════════════
        card3 = make_card("CardVoiceLog", borderless=True)
        self._func_cards.append(card3)
        cv3 = QVBoxLayout(card3)
        cv3.setContentsMargins(CARD_LEFT_GAP, 2, CARD_RIGHT_GAP, 4)
        cv3.setSpacing(2)
        self._theme_titles.append(install_card_title(card3, cv3, "操作记录", gap=2))
        self.log = QListWidget()
        self.log.setObjectName("SimpleRecordList")
        self.log.setMaximumHeight(80)
        self.log.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.log.setWordWrap(True)
        self.log.setTextElideMode(Qt.ElideRight)
        cv3.addWidget(self.log)
        root.addWidget(card3, 0)

        local = scan_local_models()
        if local:
            self._log(f"本地模型：{', '.join(local)}")
        else:
            self._log("未检测到本地模型目录，首次转写可能联网下载")
        self._log("录音方式：B · 点击开始/再点结束（可改选 A 按住松手）")

    def _install_result_title(self, card, layout) -> QLabel:
        """转写结果标题行：左侧标题 + 右侧「清除记录」。"""
        if hasattr(card, "setTitle"):
            card.setTitle("")
        restyle_func_area(card)

        # 标题与列表间距收紧（原 CARD_TITLE_BODY_GAP 偏松）
        gap = min(2, max(0, int(CARD_TITLE_BODY_GAP)))
        lay_sp = max(0, int(layout.spacing()))
        bottom = max(0, gap - lay_sp)

        head = QWidget()
        head.setObjectName("CardTitleHead")
        head.setAttribute(Qt.WA_StyledBackground, True)
        # 必须带 #CardTitleHead 限定，否则会套到子按钮，冲掉边框/底色
        head.setStyleSheet(
            "#CardTitleHead{background:transparent;border:none;}"
        )
        head.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, bottom)
        hl.setSpacing(8)

        lbl = QLabel("转写结果")
        lbl.setProperty("role", "card-title")
        restyle_card_title(lbl)
        lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        hl.addWidget(lbl, 0, Qt.AlignVCenter)
        hl.addStretch(1)

        # 与「清空编辑区」同款中按钮，保证可见边框
        self.btn_clear_records = apply_medium_button(QPushButton("清除记录"))
        self.btn_clear_records.setMinimumHeight(28)
        self.btn_clear_records.setMinimumWidth(72)
        self.btn_clear_records.setCursor(Qt.PointingHandCursor)
        self.btn_clear_records.setFlat(False)
        self.btn_clear_records.clicked.connect(self._clear_records)
        hl.addWidget(self.btn_clear_records, 0, Qt.AlignVCenter)

        layout.insertWidget(0, head)
        return lbl

    def _clear_records(self):
        self.records.clear()
        self._record_seq = 0
        self._log("已清除转写记录")

    # ── 录音模式 ──────────────────────────────────────────────────────────
    def _record_mode(self) -> str:
        if self.radio_hold.isChecked():
            return self.MODE_HOLD
        return self.MODE_CLICK

    def _on_mode_changed(self, *_):
        if self._state != "idle":
            return
        self._sync_mode_ui()
        mode = self._record_mode()
        if mode == self.MODE_HOLD:
            self._log("已切换：A · 按住录音，松手结束")
        else:
            self._log("已切换：B · 点击开始，再点结束")

    def export_settings(self) -> dict:
        """写入 user.txt 的语音录入段。"""
        return {
            "record_mode": self._record_mode(),
            "symbol_history": [str(s) for s in self._symbol_history],
        }

    def apply_settings(self, data: dict):
        """从 user.txt 恢复录音方式、前缀历史等。"""
        data = data or {}
        mode = str(data.get("record_mode") or self.MODE_CLICK).strip().lower()
        if mode not in (self.MODE_HOLD, self.MODE_CLICK):
            mode = self.MODE_CLICK
        self.radio_hold.blockSignals(True)
        self.radio_click.blockSignals(True)
        try:
            if mode == self.MODE_HOLD:
                self.radio_hold.setChecked(True)
            else:
                self.radio_click.setChecked(True)
        finally:
            self.radio_hold.blockSignals(False)
            self.radio_click.blockSignals(False)
        self._sync_mode_ui()

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

    def _sync_mode_ui(self):
        """根据模式刷新录音按钮文案。"""
        mode = self._record_mode()
        if self._state != "idle":
            return
        if mode == self.MODE_HOLD:
            self.btn_record.setText("按住录音")
        else:
            self.btn_record.setText("开始录音")

    def showEvent(self, event):
        super().showEvent(event)
        # 从「关于」装完依赖再切回来时，重新探测并更新红字提示
        self.refresh_deps()

    def refresh_deps(self):
        """重新检测 sounddevice / faster-whisper，更新红字提示与录音按钮。

        供 showEvent、关于页「安装」成功回调调用；不必重启程序。
        """
        ok, missing = voice_deps_ok()
        lbl = getattr(self, "lbl_deps", None)
        if lbl is not None:
            if ok:
                lbl.clear()
                lbl.hide()
            else:
                uniq = list(dict.fromkeys(missing or []))
                pkgs = " ".join(uniq) if uniq else "sounddevice numpy faster-whisper"
                lbl.setText(
                    "缺少依赖，请先安装：\n"
                    f"  pip install {pkgs}\n"
                    "可在「系统总览 → 语音组件」点安装；装好后回到本页会自动刷新。"
                    "若仍提示缺少，请重启本程序。"
                )
                lbl.setStyleSheet(f"color:{tk('err')}; font-size:12px;")
                lbl.show()
        if self._state == "idle":
            self._refresh_record_btn()

    # ── 主题 ──────────────────────────────────────────────────────────────
    def refresh_theme(self, *_):
        for t in self._theme_titles:
            restyle_card_title(t)
        for c in self._func_cards:
            restyle_card_frame(c)
        if self._state == "idle":
            self.lbl_status.setStyleSheet(f"color:{tk('text_mut')}; font-size:13px;")
        lbl = getattr(self, "lbl_deps", None)
        if lbl is not None and lbl.isVisible():
            lbl.setStyleSheet(f"color:{tk('err')}; font-size:12px;")
        self._refresh_record_btn()

    # ── 模型 ──────────────────────────────────────────────────────────────
    def _populate_models(self):
        local = set(scan_local_models())
        prefer = pick_default_model()
        keep = self.combo_model.currentData() if self.combo_model.count() else None
        self.combo_model.blockSignals(True)
        self.combo_model.clear()
        prefer_idx = 0
        for i, (key, label) in enumerate(MODEL_CHOICES):
            tag = "本地" if key in local else "需下载"
            self.combo_model.addItem(f"{label}  [{tag}]", key)
            if key == prefer:
                prefer_idx = i
        if keep is not None:
            ri = self.combo_model.findData(keep)
            self.combo_model.setCurrentIndex(ri if ri >= 0 else prefer_idx)
        else:
            self.combo_model.setCurrentIndex(prefer_idx)
        self.combo_model.blockSignals(False)

    def _on_refresh_models(self):
        self._populate_models()
        local = scan_local_models()
        if local:
            self._log(f"已刷新本地模型：{', '.join(local)}")
        else:
            self._log("刷新后仍未发现本地模型文件")

    # ── 日志 ──────────────────────────────────────────────────────────────
    def _log(self, text: str):
        self.log.addItem(text)
        self.log.scrollToBottom()

    # ── 录音状态机 ────────────────────────────────────────────────────────
    def _refresh_record_btn(self):
        ok, _ = voice_deps_ok()
        mode = self._record_mode()
        # 识别中不可切换模式
        mode_enabled = self._state == "idle"
        # 上传音频仅在空闲可用；录音/识别中禁用
        btn_file = getattr(self, "btn_file", None)
        if btn_file is not None:
            btn_file.setEnabled(self._state == "idle")
        self.radio_hold.setEnabled(mode_enabled)
        self.radio_click.setEnabled(mode_enabled)

        if self._state == "idle":
            self.btn_record.setEnabled(ok)
            self.btn_record.setProperty("recording", False)
            self.combo_model.setEnabled(True)
            self.combo_lang.setEnabled(True)
            self.lbl_status.setText("● 空闲")
            self.lbl_status.setStyleSheet(f"color:{tk('text_mut')}; font-size:13px;")
            self.btn_record.setText("按住录音" if mode == self.MODE_HOLD else "开始录音")
        elif self._state == "recording":
            m, s = divmod(self._elapsed, 60)
            if mode == self.MODE_HOLD:
                self.btn_record.setText(f"松手结束  {m:02d}:{s:02d}")
            else:
                self.btn_record.setText(f"结束识别  {m:02d}:{s:02d}")
            self.btn_record.setEnabled(True)
            self.btn_record.setProperty("recording", True)
            self.combo_model.setEnabled(False)
            self.combo_lang.setEnabled(False)
            self.lbl_status.setText(f"● 录音中 {m:02d}:{s:02d}")
            self.lbl_status.setStyleSheet(f"color:{tk('err')}; font-size:13px;")
        else:
            self.btn_record.setText("识别中…")
            self.btn_record.setEnabled(False)
            self.btn_record.setProperty("recording", False)
            self.combo_model.setEnabled(False)
            self.combo_lang.setEnabled(False)
            self.lbl_status.setText("● 识别中")
            self.lbl_status.setStyleSheet(f"color:{tk('warn')}; font-size:13px;")
        st = self.btn_record.style()
        if st is not None:
            st.unpolish(self.btn_record)
            st.polish(self.btn_record)

    def _on_record_pressed(self):
        """模式 A：按下开始。"""
        if self._record_mode() != self.MODE_HOLD:
            return
        if self._state == "idle":
            self._start_recording()

    def _on_record_released(self):
        """模式 A：松开结束。"""
        if self._record_mode() != self.MODE_HOLD:
            return
        if self._state == "recording":
            self._stop_recording()

    def _on_record_clicked(self):
        """模式 B：点击切换。模式 A 忽略 click（避免松手后误触）。"""
        if self._record_mode() != self.MODE_CLICK:
            return
        if self._state == "idle":
            self._start_recording()
        elif self._state == "recording":
            self._stop_recording()

    def _start_recording(self):
        ok, missing = voice_deps_ok()
        if not ok:
            self._log("缺少依赖：" + " ".join(dict.fromkeys(missing)))
            return
        self._state = "recording"
        self._elapsed = 0
        self._elapsed_timer.start()
        self._refresh_record_btn()
        mode_name = "按住" if self._record_mode() == self.MODE_HOLD else "点击"
        self._log(f"开始录音（{mode_name}）…")

        self._recorder = RecordWorker(SAMPLE_RATE, parent=self)
        self._recorder.finished_audio.connect(self._on_audio_ready)
        self._recorder.fail.connect(self._on_fail)
        self._recorder.start()

    def _stop_recording(self):
        if self._recorder is not None:
            self._recorder.stop()
        self._elapsed_timer.stop()
        self._state = "transcribing"
        self._refresh_record_btn()
        self._log("录音结束，正在独立子进程识别…")

    def _tick_elapsed(self):
        self._elapsed += 1
        if self._state == "recording":
            self._refresh_record_btn()

    def _on_fail(self, msg: str):
        self._reset_idle()
        self._log(msg.replace("\n", " · "))

    def _on_audio_ready(self, audio, samplerate: int):
        model_size = self.combo_model.currentData() or pick_default_model()
        language = self.combo_lang.currentData()
        self._log(
            f"启动子进程识别 · 模型 {model_size} · 语言 "
            f"{language or '自动'} · CPU"
        )
        self._transcriber = TranscribeWorker(
            audio,
            samplerate,
            model_size=model_size,
            language=language,
            parent=self,
            force_cpu=True,
            use_subprocess=True,
        )
        self._transcriber.status.connect(lambda m: (self.lbl_status.setText(m), self._log(m)))
        self._transcriber.done.connect(self._on_done)
        self._transcriber.fail.connect(self._on_fail)
        self._transcriber.start()

    # ── 上传音频 → 转文字 ────────────────────────────────────────────────
    def _on_pick_audio_file(self):
        if self._state != "idle":
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择声音文件（识别为文字）",
            "",
            "音频文件 (*.wav *.mp3 *.flac *.m4a *.aac *.ogg *.opus *.wma *.mp4 "
            "*.m4v *.webm *.mkv);;所有文件 (*.*)",
        )
        if path:
            self._start_file_transcribe(path)

    def _start_file_transcribe(self, path: str):
        path = (path or "").strip()
        if self._state != "idle":
            return
        _, missing = voice_deps_ok()
        if "faster-whisper" in missing:
            self._log("缺少 faster-whisper，无法转文字：pip install faster-whisper")
            return
        self._state = "transcribing"
        self._refresh_record_btn()
        model_size = self.combo_model.currentData() or pick_default_model()
        language = self.combo_lang.currentData()
        self._log(
            f"音频转文字：{os.path.basename(path)} · 模型 {model_size} · "
            f"语言 {language or '自动'} · CPU"
        )
        self.lbl_status.setText("● 识别中")
        self.lbl_status.setStyleSheet(f"color:{tk('warn')}; font-size:13px;")
        self._transcriber = TranscribeFileWorker(
            path,
            model_size=model_size,
            language=language,
            parent=self,
            force_cpu=True,
        )
        self._transcriber.status.connect(lambda m: (self.lbl_status.setText(m), self._log(m)))
        self._transcriber.done.connect(self._on_done)
        self._transcriber.fail.connect(self._on_fail)
        self._transcriber.start()

    def _on_done(self, text: str):
        self._reset_idle()
        text = (text or "").strip()
        if not text:
            self._log("没有识别到文字（可能是静音或太短）")
            return
        self._add_record(text)
        preview = text if len(text) <= 48 else text[:48] + "…"
        self._log(f"识别完成：{preview}")
        self.lbl_status.setText("● 识别完成")
        self.lbl_status.setStyleSheet(f"color:{tk('ok')}; font-size:13px;")

    def _add_record(self, text: str):
        """每次识别成功追加一条转写记录。"""
        self._record_seq += 1
        ts = datetime.now().strftime("%H:%M:%S")
        one_line = " ".join(text.split())
        preview = one_line if len(one_line) <= 72 else one_line[:72] + "…"
        label = f"#{self._record_seq}  {ts}  {preview}"
        item = QListWidgetItem(label)
        item.setData(Qt.UserRole, text)
        self.records.addItem(item)
        self.records.setCurrentItem(item)
        self.records.scrollToItem(item)
        # 新记录自动追加到编辑区，方便连续整理
        self._append_to_editor(text)

    def _append_to_editor(self, text: str):
        text = (text or "").strip()
        if not text:
            return
        cur = self.editor.toPlainText().strip()
        if cur:
            self.editor.setPlainText(cur + "\n\n" + text)
        else:
            self.editor.setPlainText(text)
        cur_c = self.editor.textCursor()
        cur_c.movePosition(QTextCursor.End)
        self.editor.setTextCursor(cur_c)

    def _on_record_item_double_clicked(self, item: QListWidgetItem):
        text = item.data(Qt.UserRole) if item else ""
        if not text:
            return
        self.editor.setPlainText(str(text))
        self._log("已用该记录覆盖编辑区")

    def _reset_idle(self):
        self._elapsed_timer.stop()
        self._state = "idle"
        self._recorder = None
        self._transcriber = None
        self._refresh_record_btn()
        self._sync_mode_ui()

    def _collapse_to_one_paragraph(self):
        """删除编辑区全部换行符，整理成连续一段（针对 GPT 爱断行）。"""
        raw = self.editor.toPlainText()
        if not (raw or "").strip():
            self._log("编辑区为空，无需整理")
            return
        # 统一各类换行后全部去掉（含 \r\n / \r / \n / Unicode 行分隔）
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
        self.editor.setPlainText(one)
        cur = self.editor.textCursor()
        cur.movePosition(QTextCursor.End)
        self.editor.setTextCursor(cur)
        self._log("已整理成1段（已去除全部换行）")
        self.lbl_status.setText("● 已整理成1段")
        self.lbl_status.setStyleSheet(f"color:{tk('ok')}; font-size:13px;")

    def _add_line_numbers(self):
        """按行给编辑区加序号。弹窗确认起始序号（默认 1）。

        例：起始 7 →
          7. 图集三站 + 防重复
          8. 截图 + OCR
          9. 区域录屏
        已有「1. / 1、/ 1) 」前缀的行会先剥掉再重新编号。
        """
        raw = self.editor.toPlainText()
        if not (raw or "").strip():
            self._log("编辑区为空，无需加序号")
            return

        # 非空行作为一条（空行丢弃，避免出现「7. 」空条目）
        lines = [ln.strip() for ln in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip()]
        if not lines:
            self._log("编辑区无有效行，无需加序号")
            return

        start, ok = QInputDialog.getInt(
            self,
            "加序号",
            f"将为 {len(lines)} 行内容加序号。\n请输入起始序号：",
            1,   # value
            0,   # min
            99999,
            1,   # step
        )
        if not ok:
            return

        # 去掉已有序号前缀，避免重复「7. 7. xxx」
        strip_re = re.compile(r"^\s*\d+\s*[\.、．\)]\s*")
        cleaned = [strip_re.sub("", ln).strip() or ln for ln in lines]

        numbered = [f"{start + i}. {text}" for i, text in enumerate(cleaned)]
        self.editor.setPlainText("\n".join(numbered))
        cur = self.editor.textCursor()
        cur.movePosition(QTextCursor.End)
        self.editor.setTextCursor(cur)
        end_n = start + len(numbered) - 1
        self._log(f"已加序号：{start}–{end_n}（共 {len(numbered)} 行）")
        self.lbl_status.setText(f"● 已加序号 {start}–{end_n}")
        self.lbl_status.setStyleSheet(f"color:{tk('ok')}; font-size:13px;")

    def _add_symbol_prefix(self):
        """按行加自定义符号前缀（如「· 」「- 」「→ 」）。

        符号取上方可填输入框原文（空格原样保留，如「· 」）。前缀挂在行首，
        行原本的前导空格不动（保留缩进/对齐，不与内容混淆）；
        空行与纯空白行同样补上前缀，做成「· ____」待填模板。
        已带相同前缀的行剥掉旧的再重挂，避免重复「· · xxx」。
        """
        raw = self.editor.toPlainText()
        if not (raw or "").strip():
            self._log("编辑区为空，无需加符号")
            return

        symbol = self.inp_symbol.text() or ""
        if not symbol:
            self.inp_symbol.setFocus()
            self._log("请先在输入框填写符号前缀（可含空格），如「· 」")
            self.lbl_status.setText("● 请填写符号前缀")
            self.lbl_status.setStyleSheet(f"color:{tk('warn')}; font-size:13px;")
            return

        lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        sym_re = re.compile(r"^(\s*)(" + re.escape(symbol) + r")(.*)$")
        out = []
        n_apply = 0
        for line in lines:
            m = sym_re.match(line)
            if m:
                # 已带同一符号：剥掉旧的再重挂（前导空格与符号后空格原样保留）
                out.append(m.group(1) + symbol + m.group(3))
                n_apply += 1
                continue
            if not (line or "").strip():
                # 空行 / 纯空白行也补前缀，方便做成「· ____」待填模板
                out.append(symbol)
            else:
                # 新挂：符号放行首，行原有前导空格整体保留在符号后
                out.append(symbol + line)
            n_apply += 1

        self.editor.setPlainText("\n".join(out))
        cur = self.editor.textCursor()
        cur.movePosition(QTextCursor.End)
        self.editor.setTextCursor(cur)
        shown = symbol if len(symbol) <= 8 else symbol[:8] + "…"
        self._log(f"已加符号前缀「{shown}」（共 {n_apply} 行）")
        self.lbl_status.setText(f"● 已加符号 ×{n_apply}")
        self.lbl_status.setStyleSheet(f"color:{tk('ok')}; font-size:13px;")
        self._remember_symbol(symbol)

    # ── 前缀符号历史卡片 ─────────────────────────────────────────────
    def set_prefs_dirty_callback(self, fn):
        """主窗口注入：有变动时安排 user.txt 落盘。"""
        self._prefs_dirty = fn

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
        hist = self._symbol_history
        if symbol in hist:
            return
        hist.append(symbol)
        if len(hist) > _PREFIX_MAX:
            del hist[: len(hist) - _PREFIX_MAX]
        self._refresh_symbol_chips()
        self._mark_prefs_dirty()

    def _save_current_symbol_as_card(self):
        """行末「+」：把前缀编辑区当前内容直接存成一张前缀卡。"""
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
        """单击卡片：把前缀内容放进「加符号」右侧的编辑框。"""
        self.inp_symbol.setText(symbol)
        self.inp_symbol.setFocus()

    def _remove_symbol(self, symbol: str):
        if symbol in self._symbol_history:
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
            return
        from PyQt5.QtGui import QFontMetrics
        fm = QFontMetrics(self.prefix_host.font())
        for i, symbol in enumerate(self._symbol_history):
            text = str(symbol)
            accent = _PREFIX_PALETTE[i % len(_PREFIX_PALETTE)]
            card = _PrefixCard(text, accent, self, self.prefix_host)
            w = max(_PREFIX_CARD_MIN_W, fm.horizontalAdvance(text) + 34)
            card.setFixedWidth(w)
            card.clicked.connect(lambda s=text: self._use_symbol(s))
            card.remove_requested.connect(lambda s=text: self._remove_symbol(s))
            flow.addWidget(card)
        flow.addStretch(1)

    # ── 卡片拖拽（仿提示词分区卡：影子跟手 + 卡间虚线）────────────
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

    def _begin_prefix_drag(self, card: "_PrefixCard"):
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
        """local 为 prefix_host 坐标：落点插到第几槽（0..n）。"""
        cards = self._pfx_card_rects()
        if not cards:
            return 0
        x = local.x()
        return sum(1 for c in cards if c.x() + c.width() // 2 < x)

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
            if not cards:
                pass
            else:
                # 用插入线当前位置换算落槽
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

    def _copy_text(self):
        """复制编辑区内容到剪贴板。"""
        text = self.editor.toPlainText().strip()
        if not text:
            self._log("编辑区为空，未复制")
            return
        QApplication.clipboard().setText(text)
        self._log("已复制到剪贴板")
        self.lbl_status.setText("● 已复制")
        self.lbl_status.setStyleSheet(f"color:{tk('ok')}; font-size:13px;")

    def set_goto_paste_callback(self, fn):
        """兼容主窗口注入（本页已无「送到粘贴助手」入口）。"""
        self._goto_paste_cb = fn

    def shutdown(self):
        try:
            self._elapsed_timer.stop()
        except Exception:
            pass
        rec = self._recorder
        if rec is not None:
            try:
                rec.stop()
            except Exception:
                pass
            try:
                if rec.isRunning():
                    rec.wait(1500)
            except Exception:
                pass
        tr = self._transcriber
        if tr is not None:
            try:
                from utils.qthread_util import stop_qthread
                stop_qthread(tr, name="voice-transcribe")
            except Exception:
                pass
        self._state = "idle"
