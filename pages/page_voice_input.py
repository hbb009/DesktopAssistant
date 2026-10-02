# pages/page_voice_input.py
# 语音录入 —— 本地语音转文字（faster-whisper）
# 流程：录音结束 → VoiceEdit 同名 wav → 模型转写 → 同名 txt
#       上传音频 → 直接转写原文件（不复制）→ VoiceEdit/{原名}_01.txt
#       → 本页文案编辑区 + 「转写结果」记录卡
# 记录卡「跳转文本页」把文字送进独立的「文本编辑」页继续整理
#
# v9.18：原右半列的语音克隆（CosyVoice3）已拆到「音频处理」页的独立页签，
#        本页只留「录音 + 转写结果」，与那边不再联动。

import os

from PyQt5.QtCore import Qt, QTimer, QSize, QRectF, QUrl, QEvent, pyqtSignal
from PyQt5.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QBrush, QPainterPath, QDesktopServices,
)
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QSizePolicy, QApplication, QRadioButton, QButtonGroup,
    QFileDialog, QScrollArea, QFrame, QListWidget, QListWidgetItem,
    QAbstractItemView,
)

from styles.style_all import (
    theme,
    tk,
    make_card,
    restyle_card_title,
    restyle_card_frame,
    apply_btn_download,
    apply_simple_record,
    BTN_DOWNLOAD_QSS,
    CARD_LEFT_GAP,
    CARD_RIGHT_GAP,
    CARD_TOP_GAP,
    CARD_BOTTOM_GAP,
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
    work_dir as voice_edit_work_dir,
    save_recording_wav,
    save_transcript_beside,
    save_transcript_versioned,
    audio_duration_sec,
    list_voice_edit_records,
    delete_voice_edit_record,
    ve_op_msg,
)


def _record_kind(audio_path: str = "", txt_path: str = "") -> str:
    """rec=本页录音，file=上传文件转写。"""
    for p in (audio_path, txt_path):
        name = os.path.basename(p or "").lower()
        if name.startswith("rec_") or name.startswith("out_"):
            return "rec"
    return "file"


def _kind_prefix(kind: str) -> str:
    return "文传" if kind == "file" else "录音"


def _file_record_title(audio_path: str = "", txt_path: str = "") -> str:
    """文传记录标题：原文件名，去掉扩展名和 _01 版本后缀。"""
    audio = (audio_path or "").strip()
    if audio:
        stem = os.path.splitext(os.path.basename(audio))[0].strip()
        return stem or os.path.basename(audio)
    name = os.path.basename((txt_path or "").strip())
    stem, _ext = os.path.splitext(name)
    if len(stem) > 3 and stem[-3] == "_" and stem[-2:].isdigit():
        stem = stem[:-3]
    stem = (stem or "").strip()
    return stem or name or "文传"


def _record_title(kind: str, seq: int, audio_path: str = "", txt_path: str = "") -> str:
    if kind == "file":
        return _file_record_title(audio_path, txt_path)
    try:
        n = int(seq or 0)
    except (TypeError, ValueError):
        n = 0
    return f"录音{n:02d}" if n > 0 else "录音"


def _fmt_rec_dur(sec: float) -> str:
    """录音长度：不足 1 小时 MM:SS，否则 HH:MM:SS。"""
    try:
        raw = float(sec or 0)
    except (TypeError, ValueError):
        raw = 0.0
    s = max(0, int(round(raw)))
    if s == 0 and raw > 0:
        s = 1
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _rec_media_icon(kind: str, color: str, size: int = 16) -> QIcon:
    """播放三角 / 暂停双竖。"""
    size = max(14, int(size))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    c = QColor(color)
    s = float(size)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(c))
    if kind == "pause":
        w, gap = s * 0.16, s * 0.14
        y, h = s * 0.22, s * 0.56
        p.drawRoundedRect(QRectF(s * 0.30, y, w, h), 1.4, 1.4)
        p.drawRoundedRect(QRectF(s * 0.30 + w + gap, y, w, h), 1.4, 1.4)
    else:
        path = QPainterPath()
        path.moveTo(s * 0.32, s * 0.20)
        path.lineTo(s * 0.32, s * 0.80)
        path.lineTo(s * 0.82, s * 0.50)
        path.closeSubpath()
        p.drawPath(path)
    p.end()
    return QIcon(pm)


class _RecordCard(QFrame):
    """单条转写记录卡：录音/文传序号 + 时长 + 一行正文 + 复制 / 跳转 / 删除。"""

    edit_requested = pyqtSignal(str)
    copy_requested = pyqtSignal(str)
    remove_requested = pyqtSignal(object)
    select_requested = pyqtSignal(object)

    def __init__(self, seq: int, duration_sec: float, text: str, audio_path: str, owner, parent=None, txt_path: str = "", kind: str = "rec"):
        super().__init__(parent)
        self._owner = owner
        self.seq = seq
        self.kind = "file" if kind == "file" else "rec"
        self.duration_sec = float(duration_sec or 0)
        self.text = text
        self.audio_path = audio_path or ""
        self.txt_path = (txt_path or "").strip()
        self.title = _record_title(self.kind, seq, self.audio_path, self.txt_path)
        self._playing = False
        self.setObjectName("VoiceRecordCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 4, 10, 4)
        v.setSpacing(4)

        # 头行：录音01 / 文件名 · 时长 …… 复制 / 跳转文本页 / 删除（录音不再放播放）
        head = QHBoxLayout()
        head.setSpacing(8)
        self.lbl_seq = QLabel(self.title)
        self.lbl_seq.setObjectName("VRecSeq")
        self.lbl_seq.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.lbl_dur = QLabel(_fmt_rec_dur(self.duration_sec))
        self.lbl_dur.setObjectName("VRecTime")
        self.lbl_dur.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        head.addWidget(self.lbl_dur, 0)
        self.btn_play = None
        if self.kind == "file":
            self.lbl_seq.setMinimumWidth(0)
            self.lbl_seq.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            head.insertWidget(0, self.lbl_seq, 1)
            self.lbl_dur.hide()
        else:
            head.insertWidget(0, self.lbl_seq, 0)
            head.addStretch(1)

        self.btn_copy = QPushButton("复制")
        self.btn_copy.setObjectName("VRecCopy")
        self.btn_copy.setCursor(Qt.PointingHandCursor)
        self.btn_copy.setFocusPolicy(Qt.NoFocus)
        self.btn_copy.clicked.connect(lambda: self.copy_requested.emit(self.text))
        head.addWidget(self.btn_copy, 0)

        self.btn_edit = QPushButton("跳转文本页")
        self.btn_edit.setObjectName("VRecEdit")
        self.btn_edit.setCursor(Qt.PointingHandCursor)
        self.btn_edit.setFocusPolicy(Qt.NoFocus)
        self.btn_edit.clicked.connect(lambda: self.edit_requested.emit(self.text))
        head.addWidget(self.btn_edit, 0)

        self.btn_del = QPushButton("✕")
        self.btn_del.setObjectName("VRecDel")
        self.btn_del.setCursor(Qt.PointingHandCursor)
        self.btn_del.setFocusPolicy(Qt.NoFocus)
        self.btn_del.clicked.connect(lambda: self.remove_requested.emit(self))
        head.addWidget(self.btn_del, 0)
        v.addLayout(head)

        # 正文：只留一行，超出右端切掉。横向 Ignored，避免长文把卡片撑出视口。
        self.lbl_text = QLabel("")
        self.lbl_text.setObjectName("VRecText")
        self.lbl_text.setWordWrap(False)
        self.lbl_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_text.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.lbl_text.setMinimumWidth(0)
        self.lbl_text.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._fit_text_height()
        v.addWidget(self.lbl_text, 0)
        self._elide_text()
        for w in (self.lbl_seq, self.lbl_dur, self.lbl_text):
            w.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.lbl_text.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self._hit_action_btn(e.pos()):
            self.select_requested.emit(self)
            e.accept()
            return
        super().mousePressEvent(e)

    def _hit_action_btn(self, pos) -> bool:
        w = self.childAt(pos)
        while w is not None and w is not self:
            if w in (self.btn_copy, self.btn_edit, self.btn_del):
                return True
            w = w.parentWidget()
        return False

    def restyle(self):
        owner = self._owner
        if owner is not None and hasattr(owner, "_restyle_record_card"):
            owner._restyle_record_card(self)

    def showEvent(self, event):
        super().showEvent(event)
        self._elide_seq()
        self._elide_text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide_seq()
        self._elide_text()

    def _elide_seq(self):
        lab = getattr(self, "lbl_seq", None)
        if lab is None:
            return
        raw = (self.title or "").strip()
        if self.kind != "file":
            lab.setText(raw)
            return
        fm = lab.fontMetrics()
        w = max(0, lab.width())
        if w < 16:
            return
        lab.setText(fm.elidedText(raw, Qt.ElideRight, w) if raw else "")

    def _fit_text_height(self):
        lab = getattr(self, "lbl_text", None)
        if lab is None:
            return
        lab.setFixedHeight(max(14, lab.fontMetrics().height()))

    def _elide_text(self):
        lab = getattr(self, "lbl_text", None)
        if lab is None:
            return
        self._fit_text_height()
        raw = " ".join((self.text or "").replace("\r", " ").split())
        fm = lab.fontMetrics()
        w = lab.width()
        if w < 20:
            w = max(20, self.width() - 24)
        lab.setText(fm.elidedText(raw, Qt.ElideRight, w) if raw else "")

    def set_playing(self, playing: bool):
        self._playing = bool(playing)
        self.refresh_play_icon()

    def refresh_play_icon(self):
        btn = getattr(self, "btn_play", None)
        if btn is None:
            return
        if getattr(self, "kind", "rec") == "file":
            btn.hide()
            dur = getattr(self, "lbl_dur", None)
            if dur is not None:
                dur.hide()
            return
        has_audio = bool(self.audio_path and os.path.isfile(self.audio_path))
        btn.setEnabled(has_audio)
        color = tk("accent") if has_audio else tk("text_mut")
        kind = "pause" if self._playing else "play"
        btn.setIcon(_rec_media_icon(kind, color, 16))


class _RecordsHost(QWidget):
    """转写结果滚动内容：点卡片之间的空白处取消选中。"""

    blank_clicked = pyqtSignal()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            w = self.childAt(e.pos())
            while w is not None and w is not self:
                if isinstance(w, _RecordCard):
                    super().mousePressEvent(e)
                    return
                w = w.parentWidget()
            self.blank_clicked.emit()
            e.accept()
            return
        super().mousePressEvent(e)


class _EmptyRecordsHint(QLabel):
    """转写结果为空时的占位提示。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("VoiceRecordsEmpty")
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.setStyleSheet(
            f"QLabel#VoiceRecordsEmpty{{color:{tk('text_dim')};"
            f"font-size:12px;background:transparent;border:none;padding:6px 2px;}}"
        )


class _PageBodyScroll(QScrollArea):
    """整页纵向滚动区：隐藏滚动条，滚轮直接翻页（内容超出一屏时用）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollArea > QWidget > QWidget{background:transparent;}"
        )
        try:
            self.viewport().setAutoFillBackground(False)
            self.viewport().setStyleSheet("background:transparent;")
        except Exception:
            pass

    def wheelEvent(self, e):
        bar = self.verticalScrollBar()
        if bar is not None and bar.maximum() > 0:
            delta = e.angleDelta().y() or e.angleDelta().x()
            if delta:
                bar.setValue(bar.value() - delta)
                e.accept()
                return
        super().wheelEvent(e)


class PageVoiceInput(QWidget):
    """说话 → 本地转写 → 记录卡（编辑/复制）→ 送到文本编辑页整理。"""

    # 录音模式
    MODE_HOLD = "hold"    # A：按住录音，松手结束
    MODE_CLICK = "click"  # B：点击开始，再点结束
    # 「录音」与「转写结果」两块的标题行统一高度，保证文字对齐
    _SECTION_HDR_H = 30
    # 两块卡片顶部留白：出厂 CARD_TOP_GAP 基础上减 10px
    _ROW_TOP_PAD = max(2, CARD_TOP_GAP - 10)

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
        self._rec_seq = 0
        self._file_seq = 0
        self._record_cards = []       # 现有记录卡
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)

        self._theme_titles = []
        self._func_cards = []
        self._prefs_dirty = None
        self._goto_editor_cb = None   # 主窗口注入：文字送到「文本编辑」页
        self._goto_paste_cb = None    # 兼容旧回调
        self._pending_audio_path = "" # 当前这条录音 wav / 上传的原文件路径
        self._pending_duration = 0.0  # 当前这条录音长度（秒）
        self._pending_from_upload = False  # 上传音频：不复制原文件，txt 用版本号
        self._player = None           # 转写记录卡播放线程
        self._playing_card = None
        self._selected_rec = None     # 点选的「录音」卡，供右栏播放按钮

        self._build_ui()
        theme.changed.connect(self.refresh_theme)
        self._refresh_record_btn()
        self._sync_mode_ui()
        self._reload_records_from_disk(force=True)
        self._update_empty_hint()

    def minimumSizeHint(self):
        return QSize(0, 0)

    def sizeHint(self):
        return QSize(900, 620)

    # ── 构建 ──────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 整页：左 50% 语音录入；右 50% 曾是语音克隆，v9.18 拆去「音频处理」页后留白
        self.outer_scroll = _PageBodyScroll(self)
        root.addWidget(self.outer_scroll, 1)

        body = QWidget()
        body.setObjectName("VoicePageScrollHost")
        body.setAttribute(Qt.WA_StyledBackground, True)
        body.setStyleSheet("#VoicePageScrollHost{background:transparent;border:none;}")
        cols = QHBoxLayout(body)
        cols.setContentsMargins(0, 0, 0, 0)
        cols.setSpacing(4)

        # 左列：录音（顶）+ 转写结果（向下占满剩余）
        col_left = QWidget()
        col_left.setObjectName("VoiceColLeft")
        col_left_lay = QVBoxLayout(col_left)
        col_left_lay.setContentsMargins(0, 0, 0, 0)
        col_left_lay.setSpacing(8)

        # ── 左列·录音控制（模型 / 语言 / 录音方式 / 录音按钮 / 状态）──
        card_left = make_card("CardVoiceLeft")
        self._func_cards.append(card_left)
        cl = QVBoxLayout(card_left)
        cl.setContentsMargins(CARD_LEFT_GAP, self._ROW_TOP_PAD, CARD_RIGHT_GAP, 6)
        cl.setSpacing(4)
        self._install_section_head(cl, "录音")

        # 模型 + 语言：同一行，左 6 右 4
        row_ml = QHBoxLayout()
        row_ml.setContentsMargins(0, 0, 0, 0)
        row_ml.setSpacing(8)

        cell_m = QWidget()
        cell_m.setMinimumWidth(0)
        cell_m.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        hm = QHBoxLayout(cell_m)
        hm.setContentsMargins(0, 0, 0, 0)
        hm.setSpacing(6)
        lbl_m = QLabel("模型")
        lbl_m.setObjectName("CalcFieldLabel")
        lbl_m.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        hm.addWidget(lbl_m)
        self.combo_model = QComboBox()
        self.combo_model.setMinimumWidth(0)
        self.combo_model.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._populate_models()
        hm.addWidget(self.combo_model, 1)

        cell_l = QWidget()
        cell_l.setMinimumWidth(0)
        cell_l.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        hl = QHBoxLayout(cell_l)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)
        lbl_l = QLabel("语言")
        lbl_l.setObjectName("CalcFieldLabel")
        lbl_l.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        hl.addWidget(lbl_l)
        self.combo_lang = QComboBox()
        for key, label in LANGUAGE_CHOICES:
            self.combo_lang.addItem(label, key)
        idx_zh = self.combo_lang.findData("zh")
        self.combo_lang.setCurrentIndex(idx_zh if idx_zh >= 0 else 0)
        self.combo_lang.setMinimumWidth(0)
        self.combo_lang.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        hl.addWidget(self.combo_lang, 1)

        row_ml.addWidget(cell_m, 6)
        row_ml.addWidget(cell_l, 4)
        cl.addLayout(row_ml)

        # 录音方式 + A / B：同一行
        row_mode = QHBoxLayout()
        row_mode.setContentsMargins(0, 0, 0, 0)
        row_mode.setSpacing(8)
        lbl_mode = QLabel("录音方式")
        lbl_mode.setObjectName("CalcFieldLabel")
        lbl_mode.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        row_mode.addWidget(lbl_mode, 0)

        self._mode_group = QButtonGroup(self)
        self.radio_hold = QRadioButton("A 按住录音，松手结束")
        self.radio_hold.setStyleSheet("background: transparent;")
        self.radio_hold.setMinimumWidth(0)
        self.radio_hold.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.radio_click = QRadioButton("B 点击开始，再点结束")
        self.radio_click.setStyleSheet("background: transparent;")
        self.radio_click.setMinimumWidth(0)
        self.radio_click.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.radio_click.setChecked(True)  # 默认沿用原交互
        self._mode_group.addButton(self.radio_hold)
        self._mode_group.addButton(self.radio_click)
        self.radio_hold.toggled.connect(self._on_mode_changed)
        self.radio_click.toggled.connect(self._on_mode_changed)
        row_mode.addWidget(self.radio_hold, 1)
        row_mode.addWidget(self.radio_click, 1)
        cl.addLayout(row_mode)

        cl.addSpacing(2)

        # 状态
        self.lbl_status = QLabel("● 空闲")
        self.lbl_status.setObjectName("StatusLbl")
        self.lbl_status.setStyleSheet(f"color:{tk('text_mut')}; font-size:13px;")
        cl.addWidget(self.lbl_status)

        # 开始录音 / 上传音频：同一行、同高
        row_act = QHBoxLayout()
        row_act.setContentsMargins(0, 0, 0, 0)
        row_act.setSpacing(8)
        self.btn_record = QPushButton("开始录音")
        self.btn_record.setMinimumWidth(0)
        self.btn_record.setCursor(Qt.PointingHandCursor)
        # A：pressed / released；B：clicked（互斥，见下方槽）
        self.btn_record.pressed.connect(self._on_record_pressed)
        self.btn_record.released.connect(self._on_record_released)
        self.btn_record.clicked.connect(self._on_record_clicked)
        self._apply_compact_download(self.btn_record)
        row_act.addWidget(self.btn_record, 1)

        self.btn_file = QPushButton("上传音频")
        self.btn_file.setMinimumWidth(0)
        self.btn_file.setCursor(Qt.PointingHandCursor)
        self.btn_file.clicked.connect(self._on_pick_audio_file)
        self._apply_compact_download(self.btn_file)
        row_act.addWidget(self.btn_file, 1)
        cl.addLayout(row_act)

        # 依赖提示
        self.lbl_deps = QLabel("")
        self.lbl_deps.setWordWrap(True)
        self.lbl_deps.setObjectName("ResultHint")
        self.lbl_deps.setStyleSheet(f"color:{tk('err')}; font-size:12px;")
        self.lbl_deps.hide()
        cl.addWidget(self.lbl_deps)
        cl.addStretch(1)

        col_left_lay.addWidget(card_left, 0)

        # ══ 左列·转写结果：记录卡 + 下方操作记录 ══════════════════════
        card_right = make_card("CardVoiceRight")
        self._func_cards.append(card_right)
        cr = QVBoxLayout(card_right)
        cr.setContentsMargins(CARD_LEFT_GAP, self._ROW_TOP_PAD, CARD_RIGHT_GAP, 6)
        cr.setSpacing(4)
        self._install_section_head(cr, "转写结果")

        # 记录卡滚动区
        self.records_scroll = QScrollArea()
        self.records_scroll.setObjectName("VoiceRecordsScroll")
        self.records_scroll.setWidgetResizable(True)
        self.records_scroll.setFrameShape(QFrame.NoFrame)
        self.records_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.records_scroll.setAttribute(Qt.WA_StyledBackground, True)
        self.records_scroll.setStyleSheet(
            "#VoiceRecordsScroll{background:transparent;border:none;}"
            "#VoiceRecordsScroll > QWidget > QWidget{background:transparent;}"
        )
        try:
            self.records_scroll.viewport().setAutoFillBackground(False)
            self.records_scroll.viewport().setStyleSheet("background:transparent;")
        except Exception:
            pass
        self.records_host = _RecordsHost()
        self.records_host.setObjectName("VoiceRecordsHost")
        self.records_host.setAttribute(Qt.WA_StyledBackground, True)
        self.records_host.setMinimumWidth(0)
        self.records_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.records_host.setStyleSheet("#VoiceRecordsHost{background:transparent;border:none;}")
        self.records_host.blank_clicked.connect(self._clear_rec_sel)
        self.records_layout = QVBoxLayout(self.records_host)
        self.records_layout.setContentsMargins(0, 0, 0, 0)
        self.records_layout.setSpacing(6)
        self.records_layout.setAlignment(Qt.AlignTop)
        self.records_layout.addStretch(1)
        self.records_scroll.setWidget(self.records_host)
        try:
            self.records_scroll.viewport().installEventFilter(self)
            self.records_scroll.installEventFilter(self)
            self.records_host.installEventFilter(self)
        except Exception:
            pass
        cr.addWidget(self.records_scroll, 1)

        # 分隔线 + 操作记录
        sep = QFrame()
        sep.setObjectName("VoiceRecordSep")
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"#VoiceRecordSep{{background:{tk('border')};border:none;}}")
        self._record_sep = sep
        cr.addWidget(sep)

        log_head = QLabel("操作记录")
        log_head.setProperty("role", "card-title")
        restyle_card_title(log_head)
        self._theme_titles.append(log_head)
        cr.addWidget(log_head)

        self.log = QListWidget()
        self.log.setObjectName("VoiceOpLog")
        self.log.setWordWrap(False)
        self.log.setTextElideMode(Qt.ElideRight)
        self.log.setSpacing(0)
        self.log.setSelectionMode(QAbstractItemView.NoSelection)
        self.log.setFocusPolicy(Qt.NoFocus)
        self.log.setUniformItemSizes(True)
        self._op_log_row_h = 22
        self._op_log_rows = 10
        self._style_op_log()
        cr.addWidget(self.log, 0)

        col_left_lay.addWidget(card_right, 1)
        cols.addWidget(col_left, 1)
        # 右半边空着：保持左栏 50% 宽度，不让卡片被拉满全宽
        cols.addStretch(1)

        self.outer_scroll.setWidget(body)

    def _install_section_head(self, layout, title: str) -> QLabel:
        """在卡片 layout 顶端放一行等高的分区标题。

        左右两栏标题共用同一行高（_SECTION_HDR_H），文字垂直居中，
        保证「录音」与「转写结果」标题文字顶端对齐。
        """
        head = QWidget()
        head.setObjectName("CardTitleHead")
        head.setAttribute(Qt.WA_StyledBackground, True)
        head.setStyleSheet("#CardTitleHead{background:transparent;border:none;}")
        head.setFixedHeight(self._SECTION_HDR_H)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)

        lbl = QLabel(title)
        lbl.setProperty("role", "card-title")
        restyle_card_title(lbl)
        lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        hl.addWidget(lbl, 0, Qt.AlignVCenter)
        hl.addStretch(1)

        layout.insertWidget(0, head)
        self._theme_titles.append(lbl)
        return lbl

    @staticmethod
    def _apply_compact_download(btn):
        """记录卡按钮压到 22px，padding 上下为 0，和行内其它控件齐平。"""
        apply_btn_download(btn)
        btn.setMinimumHeight(22)
        btn.setMaximumHeight(22)
        btn.setStyleSheet(
            BTN_DOWNLOAD_QSS.replace("padding: 4px 14px;", "padding: 0px 14px;")
        )

    # ── 录音模式 ──────────────────────────────────────────────────────
    def _record_mode(self) -> str:
        if self.radio_hold.isChecked():
            return self.MODE_HOLD
        return self.MODE_CLICK

    def _on_mode_changed(self, *_):
        if self._state != "idle":
            return
        self._sync_mode_ui()

    def export_settings(self) -> dict:
        """写入 user.txt 的语音录入段。"""
        return {
            "record_mode": self._record_mode(),
            "model": str(self.combo_model.currentData() or ""),
            "language": "" if self.combo_lang.currentData() is None else str(self.combo_lang.currentData()),
        }

    def apply_settings(self, data: dict):
        """从 user.txt 恢复录音方式。"""
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
        if "model" in data:
            key = str(data.get("model") or "")
            idx = self.combo_model.findData(key) if key else -1
            if idx >= 0:
                self.combo_model.blockSignals(True)
                self.combo_model.setCurrentIndex(idx)
                self.combo_model.blockSignals(False)
        if "language" in data:
            lang = data.get("language")
            lang = None if lang in (None, "") else str(lang)
            idx = self.combo_lang.findData(lang)
            if idx >= 0:
                self.combo_lang.blockSignals(True)
                self.combo_lang.setCurrentIndex(idx)
                self.combo_lang.blockSignals(False)

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
        if self._state == "idle":
            self._reload_records_from_disk()

    def eventFilter(self, obj, ev):
        if ev is not None and ev.type() == QEvent.MouseButtonPress:
            try:
                if ev.button() == Qt.LeftButton and self._click_is_records_blank(obj, ev):
                    self._clear_rec_sel()
            except Exception:
                pass
        return super().eventFilter(obj, ev)

    def _click_is_records_blank(self, obj, ev) -> bool:
        """点在转写结果滚动区空白（不是某张记录卡）上。"""
        scroll = getattr(self, "records_scroll", None)
        host = getattr(self, "records_host", None)
        if scroll is None:
            return False
        vp = scroll.viewport()
        if obj not in (vp, scroll, host):
            return False
        w = None
        try:
            gpos = ev.globalPos()
            w = QApplication.widgetAt(gpos)
        except Exception:
            w = None
        while w is not None:
            if isinstance(w, _RecordCard):
                return False
            if w in (vp, scroll, host):
                return True
            w = w.parentWidget()
        return obj in (vp, scroll, host)

    def refresh_deps(self):
        """重新检测 sounddevice / faster-whisper，更新红字提示与录音按钮。"""
        ok, missing = voice_deps_ok()
        lbl = getattr(self, "lbl_deps", None)
        if lbl is not None:
            if ok:
                lbl.clear()
                lbl.hide()
            else:
                uniq = list(dict.fromkeys(missing or []))
                pkgs = " ".join(uniq) if uniq else "sounddevice numpy faster-whisper"
                lbl.setText(f"缺少：{pkgs}（系统总览 → 语音组件）")
                lbl.setStyleSheet(f"color:{tk('err')}; font-size:12px;")
                lbl.show()
        if self._state == "idle":
            self._refresh_record_btn()

    # ── 主题 ──────────────────────────────────────────────────────────
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
        for card in self._record_cards:
            self._restyle_record_card(card)
        sep = getattr(self, "_record_sep", None)
        if sep is not None:
            sep.setStyleSheet(f"#VoiceRecordSep{{background:{tk('border')};border:none;}}")
        self._style_op_log()
        self._refresh_record_btn()

    def _style_op_log(self):
        """操作记录：单行、左边不留选中条空位；高度刚好 10 行，超出自动滚屏。"""
        lg = getattr(self, "log", None)
        if lg is None:
            return
        apply_simple_record(lg)
        lg.setWordWrap(False)
        lg.setSpacing(0)
        lg.setFrameShape(QFrame.NoFrame)
        lg.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        lg.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        try:
            lg.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        except Exception:
            pass
        try:
            lg.setStyleSheet(
                "QListWidget#VoiceOpLog{"
                "background:transparent;border:none;padding:0;}"
                "QListWidget#VoiceOpLog::item{"
                "background:transparent;border:none;margin:0;"
                "padding:0px 4px 0px 0px;}"
                "QListWidget#VoiceOpLog::item:hover,"
                "QListWidget#VoiceOpLog::item:selected,"
                "QListWidget#VoiceOpLog::item:selected:active,"
                "QListWidget#VoiceOpLog::item:selected:!active{"
                "background:transparent;border:none;}"
            )
        except Exception:
            pass
        fm = lg.fontMetrics()
        row_h = max(int(fm.lineSpacing()) + 2, 18)
        self._op_log_row_h = row_h
        n = int(getattr(self, "_op_log_rows", 10) or 10)
        lg.setFixedHeight(row_h * n)
        for i in range(lg.count()):
            it = lg.item(i)
            if it is not None:
                it.setSizeHint(QSize(10, row_h))

    def _restyle_record_card(self, card):
        """按主题刷写单张记录卡样式。"""
        if card is None:
            return
        name = "VoiceRecordCard"
        accent = tk("accent")
        hover = tk("accent_hover")
        border = tk("border")
        text = tk("text")
        kind = getattr(card, "kind", "rec")
        seq_col = tk("ok") if kind == "file" else tk("accent")
        time_col = tk("text_mut")
        rowbg = tk("row_bg")
        selected = card is getattr(self, "_selected_rec", None)
        # 选中只换颜色、不改线宽，避免 1px↔2px 把卡片撑一下抖动
        bd = accent if selected else border
        card.setStyleSheet(
            f"#VoiceRecordCard{{background:{rowbg};border:2px solid {bd};"
            f"border-radius:8px;}}"
            f"QLabel#VRecSeq{{background:transparent;color:{seq_col};"
            f"font-size:12px;font-weight:700;border:none;}}"
            f"QLabel#VRecTime{{background:transparent;color:{time_col};"
            f"font-size:11px;border:none;}}"
            f"QLabel#VRecText{{background:transparent;color:{text};"
            f"font-size:13px;border:none;padding:0;margin:0;}}"
            f"QPushButton#VRecEdit{{background:{accent};color:#ffffff;"
            f"border:none;border-radius:5px;padding:2px 10px;font-size:12px;}}"
            f"QPushButton#VRecEdit:hover{{background:{hover};}}"
            f"QPushButton#VRecCopy{{background:transparent;color:{time_col};"
            f"border:1px solid {border};border-radius:5px;"
            f"padding:1px 8px;font-size:12px;}}"
            f"QPushButton#VRecCopy:hover{{color:{text};border-color:{time_col};}}"
            f"QPushButton#VRecDel{{background:transparent;color:{time_col};"
            f"border:none;border-radius:5px;padding:1px 6px;font-size:12px;}}"
            f"QPushButton#VRecDel:hover{{color:{tk('err')};}}"
            f"QPushButton#VRecPlay{{background:transparent;border:none;padding:0;"
            f"border-radius:5px;}}"
            f"QPushButton#VRecPlay:hover{{background:rgba(148,163,184,0.18);}}"
            f"QPushButton#VRecPlay:disabled{{background:transparent;}}"
        )
        if hasattr(card, "refresh_play_icon"):
            card.refresh_play_icon()
        if hasattr(card, "_elide_seq"):
            card._elide_seq()
        if hasattr(card, "_elide_text"):
            card._elide_text()

    # ── 模型 ──────────────────────────────────────────────────────────
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

    # ── 日志 ──────────────────────────────────────────────────────────
    @staticmethod
    def _card_label(card) -> str:
        if card is None:
            return ""
        title = str(getattr(card, "title", "") or "").strip()
        if title:
            return title
        try:
            n = int(getattr(card, "seq", 0) or 0)
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            return ""
        return f"{_kind_prefix(getattr(card, 'kind', 'rec'))}{n:02d}"

    def _log(self, text: str, replace_prefix: str = ""):
        text = (text or "").strip()
        if not text:
            return
        prefix = (replace_prefix or "").strip()
        if prefix:
            for i in range(self.log.count() - 1, -1, -1):
                it = self.log.item(i)
                if it is not None and it.text().startswith(prefix):
                    it.setText(text)
                    self._scroll_op_log()
                    return
        item = QListWidgetItem(text)
        row_h = int(getattr(self, "_op_log_row_h", 0) or 0)
        if row_h > 0:
            item.setSizeHint(QSize(10, row_h))
        self.log.addItem(item)
        while self.log.count() > 80:
            self.log.takeItem(0)
        self._scroll_op_log()

    def _scroll_op_log(self):
        """新记录贴底；等布局刷新后再滚一次，避免刚写入时条还没长出来。"""
        lg = getattr(self, "log", None)
        if lg is None:
            return

        def _to_end():
            lg.scrollToBottom()
            bar = lg.verticalScrollBar()
            if bar is not None:
                bar.setValue(bar.maximum())

        _to_end()
        QTimer.singleShot(0, _to_end)

    # ── 录音状态机 ────────────────────────────────────────────────────
    def _refresh_record_btn(self):
        ok, _ = voice_deps_ok()
        mode = self._record_mode()
        mode_enabled = self._state == "idle"
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
            self._log("缺少依赖")
            return
        self._state = "recording"
        self._elapsed = 0
        self._elapsed_timer.start()
        self._refresh_record_btn()

        self._recorder = RecordWorker(SAMPLE_RATE, parent=self)
        self._recorder.finished_audio.connect(self._on_audio_ready)
        self._recorder.fail.connect(self._on_fail)
        self._recorder.start()
        self._log("开始录音")

    def _stop_recording(self):
        if self._recorder is not None:
            self._recorder.stop()
        self._elapsed_timer.stop()
        self._state = "transcribing"
        self._refresh_record_btn()

    def _tick_elapsed(self):
        self._elapsed += 1
        if self._state == "recording":
            self._refresh_record_btn()

    def _on_fail(self, msg: str):
        self._pending_audio_path = ""
        self._pending_duration = 0.0
        self._pending_from_upload = False
        self._reset_idle()
        self._log(msg.replace("\n", " · "))

    def _on_audio_ready(self, audio, samplerate: int):
        """录音结束：先把 wav 落到 VoiceEdit，再调用模型转写。"""
        wav_path = save_recording_wav(audio, samplerate)
        if not wav_path:
            self._on_fail(ve_op_msg("写入失败", extra="录音"))
            return
        self._log(ve_op_msg("写入", wav_path))
        self._pending_audio_path = wav_path
        self._pending_from_upload = False
        try:
            n = int(getattr(audio, "size", None) or len(audio))
            sr = float(samplerate or SAMPLE_RATE) or float(SAMPLE_RATE)
            self._pending_duration = (float(n) / sr) if n > 0 else 0.0
        except Exception:
            self._pending_duration = float(self._elapsed or 0)
        if self._pending_duration <= 0:
            self._pending_duration = audio_duration_sec(wav_path)
        self._log("已保存，识别中")
        self.lbl_status.setText("● 已保存，识别中")
        self.lbl_status.setStyleSheet(f"color:{tk('warn')}; font-size:13px;")

        model_size = self.combo_model.currentData() or pick_default_model()
        language = self.combo_lang.currentData()
        self._transcriber = TranscribeWorker(
            audio,
            samplerate,
            model_size=model_size,
            language=language,
            parent=self,
            force_cpu=True,
            use_subprocess=True,
        )
        self._transcriber.status.connect(self.lbl_status.setText)
        self._transcriber.done.connect(self._on_done)
        self._transcriber.fail.connect(self._on_fail)
        self._transcriber.start()

    # ── 上传音频 ──────────────────────────────────────────────────────
    def _on_pick_audio_file(self):
        if self._state != "idle":
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择音频",
            voice_edit_work_dir(),
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
            self._log("缺少 faster-whisper")
            return
        if not os.path.isfile(path):
            self._log("找不到所选音频文件")
            return
        self._pending_audio_path = path
        self._pending_from_upload = True
        self._pending_duration = audio_duration_sec(path)
        self._state = "transcribing"
        self._refresh_record_btn()
        model_size = self.combo_model.currentData() or pick_default_model()
        language = self.combo_lang.currentData()
        self._log("已选音频，识别中")
        self.lbl_status.setText("● 识别中")
        self.lbl_status.setStyleSheet(f"color:{tk('warn')}; font-size:13px;")
        self._transcriber = TranscribeFileWorker(
            path,
            model_size=model_size,
            language=language,
            parent=self,
            force_cpu=True,
        )
        self._transcriber.status.connect(self.lbl_status.setText)
        self._transcriber.done.connect(self._on_done)
        self._transcriber.fail.connect(self._on_fail)
        self._transcriber.start()

    def _on_done(self, text: str):
        text = (text or "").strip()
        audio_path = self._pending_audio_path or ""
        from_upload = bool(self._pending_from_upload)
        self._pending_audio_path = ""
        self._pending_duration = 0.0
        self._pending_from_upload = False
        txt_path = ""
        if text and audio_path:
            if from_upload:
                txt_path = save_transcript_versioned(text, audio_path)
            else:
                txt_path = save_transcript_beside(text, audio_path)
            if txt_path:
                self._log(ve_op_msg("写入", txt_path))
            else:
                self._log(ve_op_msg("写入失败", extra="转写文本"))
        self._reset_idle()
        self._reload_records_from_disk(scroll_bottom=True, force=True)
        want = "file" if from_upload else "rec"
        tag = ""
        for card in reversed(self._record_cards):
            if getattr(card, "kind", "rec") == want:
                tag = self._card_label(card)
                break
        if text:
            self._log(f"识别完成 · {tag}" if tag else "识别完成")
        else:
            self._log("识别完成，无文字")
        self.lbl_status.setText("● 识别完成" if text else "● 无文字")
        self.lbl_status.setStyleSheet(
            f"color:{tk('ok' if text else 'warn')}; font-size:13px;"
        )

    # ── 记录卡 ────────────────────────────────────────────────────────
    def _update_empty_hint(self):
        """空记录区不再放教程占位。"""
        hint = getattr(self, "_empty_hint", None)
        if hint is not None:
            hint.setVisible(False)

    def _records_disk_sig(self, rows=None):
        if rows is None:
            rows = list_voice_edit_records()
        return tuple(
            (
                str(r.get("audio_path") or ""),
                str(r.get("txt_path") or ""),
                round(float(r.get("mtime") or 0), 2),
            )
            for r in rows
        )

    def _on_rec_card_selected(self, card):
        """点选转写记录（录音 / 文传均可）；取消选中请点空白处。"""
        if card is None or card not in self._record_cards:
            return
        if self._selected_rec is card:
            return
        self._selected_rec = card
        self._sync_rec_play_sel()

    def _clear_rec_sel(self):
        if self._selected_rec is None:
            self._sync_rec_play_sel()
            return
        self._selected_rec = None
        self._sync_rec_play_sel()

    def _sync_rec_play_sel(self):
        """重刷记录卡的选中描边。

        v9.18 起不再把选中路径同步给 CosVoice3 页签（原内嵌克隆面板已拆走）。
        """
        for c in self._record_cards:
            try:
                c.restyle()
            except Exception:
                pass

    def _reload_records_from_disk(self, *, scroll_bottom=False, force=False):
        """转写结果以 VoiceEdit 目录为准：开机、切回本页、识别完都从这里重建。"""
        if self._playing_card is not None and not force:
            return
        try:
            rows = list_voice_edit_records()
        except Exception:
            rows = []
        sig = self._records_disk_sig(rows)
        if not force and sig == getattr(self, "_records_sig", None):
            return
        self._records_sig = sig
        self._stop_record_player()
        self._selected_rec = None
        for card in list(self._record_cards):
            self.records_layout.removeWidget(card)
            card.hide()
            card.setParent(None)
            card.deleteLater()
        self._record_cards.clear()
        self._rec_seq = 0
        self._file_seq = 0
        for rec in rows:
            self._add_record(
                rec.get("text") or "",
                audio_path=rec.get("audio_path") or "",
                duration_sec=rec.get("duration") or 0,
                txt_path=rec.get("txt_path") or "",
            )
        self._update_empty_hint()
        self._sync_rec_play_sel()
        if scroll_bottom:
            QTimer.singleShot(0, self._scroll_records_to_bottom)

    def _add_record(self, text: str, audio_path: str = "", duration_sec: float = 0.0, txt_path: str = ""):
        """追加一张记录卡（录音 / 文传各自从 01 编号）。"""
        kind = _record_kind(audio_path, txt_path)
        if kind == "file":
            self._file_seq += 1
            seq = self._file_seq
        else:
            self._rec_seq += 1
            seq = self._rec_seq
        card = _RecordCard(
            seq, duration_sec, text, audio_path, self, txt_path=txt_path, kind=kind,
        )
        card.edit_requested.connect(self._on_record_edit)
        card.copy_requested.connect(self._on_record_copy)
        card.remove_requested.connect(self._on_record_remove)
        card.select_requested.connect(self._on_rec_card_selected)
        card.restyle()
        self._record_cards.append(card)
        n = self.records_layout.count()
        self.records_layout.insertWidget(max(0, n - 1), card)
        self._update_empty_hint()

    def _scroll_records_to_bottom(self):
        bar = self.records_scroll.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())

    def _on_record_edit(self, text: str):
        """记录卡「跳转文本页」：把内容送进独立的「文本编辑」页。"""
        card = self.sender()
        tag = self._card_label(card)
        fn = self._goto_editor_cb
        if callable(fn):
            try:
                fn(text)
                self._log(f"已跳转文本页 · {tag}" if tag else "已跳转文本页")
                return
            except Exception:
                pass
        QApplication.clipboard().setText(text)
        self._log(f"已复制 {tag}" if tag else "已复制")

    def _on_record_copy(self, text: str):
        QApplication.clipboard().setText(text)
        card = self.sender()
        tag = self._card_label(card)
        self._log(f"已复制 {tag}" if tag else "已复制")

    def _on_record_remove(self, card):
        target = card if isinstance(card, _RecordCard) else None
        if target is None or target not in self._record_cards:
            return
        if target is self._playing_card:
            self._stop_record_player()
        tag = self._card_label(target)
        if self._selected_rec is target:
            self._selected_rec = None
        removed = delete_voice_edit_record(
            getattr(target, "audio_path", "") or "",
            getattr(target, "txt_path", "") or "",
        )
        if removed:
            self._log(ve_op_msg("删除", *removed))
        self._reload_records_from_disk(force=True)
        self._log(f"已删除 {tag}" if tag else "已删除记录")

    def _on_record_play(self, card):
        """播放 / 暂停当前这条录音。"""
        if card is None:
            return
        path = (getattr(card, "audio_path", "") or "").strip()
        if not path or not os.path.isfile(path):
            self._log("没有可播放的声音文件")
            return
        if card is self._playing_card:
            self._stop_record_player()
            return
        self._play_record_path(card, path)

    def _mark_playing_card(self, card):
        self._playing_card = card
        for c in self._record_cards:
            c.set_playing(c is card)

    def _stop_record_player(self, *, wait: bool = False):
        p = self._player
        self._player = None
        self._mark_playing_card(None)
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
            stop_qthread(p, name="voice-record-play", timeout_ms=400)
        except Exception:
            pass

    def _on_record_play_finished(self, worker=None):
        if worker is not None and self._player is not None and worker is not self._player:
            return
        if self._player is worker:
            self._player = None
        self._mark_playing_card(None)

    def _on_record_play_fail(self, msg: str, path: str, worker=None):
        if worker is not None and self._player is not None and worker is not self._player:
            return
        self._on_record_play_finished(worker)
        if path and os.path.isfile(path):
            try:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
                return
            except Exception:
                pass
        self._log((msg or "播放失败").replace("\n", " · "))

    def _play_record_path(self, card, path: str):
        self._stop_record_player()
        if os.path.splitext(path)[1].lower() == ".wav":
            try:
                from utils.cosyvoice_clone import PlayWavWorker
                worker = PlayWavWorker(path, parent=self)
                self._player = worker
                worker.fail.connect(
                    lambda m, p=path, w=worker: self._on_record_play_fail(m, p, w)
                )
                worker.finished.connect(
                    lambda w=worker: self._on_record_play_finished(w)
                )
                self._mark_playing_card(card)
                worker.start()
                return
            except Exception:
                pass
        self._mark_playing_card(None)
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception as e:
            self._log(f"无法播放：{e}")

    def _reset_idle(self):
        self._elapsed_timer.stop()
        self._state = "idle"
        self._recorder = None
        self._transcriber = None
        self._refresh_record_btn()
        self._sync_mode_ui()

    # ── 主窗口回调注入 ───────────────────────────────────────────────
    def set_prefs_dirty_callback(self, fn):
        """主窗口注入：有变动时安排 user.txt 落盘。"""
        self._prefs_dirty = fn

    def set_goto_editor_callback(self, fn):
        """主窗口注入：把一段文字送进「文本编辑」页（record_mode 等）。"""
        self._goto_editor_cb = fn

    def set_goto_paste_callback(self, fn):
        """兼容主窗口注入（本页已无「送到粘贴助手」入口）。"""
        self._goto_paste_cb = fn

    def shutdown(self):
        try:
            self._elapsed_timer.stop()
        except Exception:
            pass
        try:
            self._stop_record_player(wait=True)
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
