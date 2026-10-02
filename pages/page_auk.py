# pages/page_auk.py
# 「音频处理」页：AUK + CosVoice3 两个页签。
#   · PageAuk         —— AUK 页签（原独立页内容，推理在无 Qt 的子进程里跑）
#   · PageCosVoice3   —— CosVoice3 页签（语音克隆，见 pages/page_cosvoice3.py）
# 声音卡带子 + 播放行由容器 PageAudioProcess 放在两个页签上方，两页共用一份卡
# （统一存 voice_clone 段 + VoiceClone 目录；旧 data/auk 卡库已删）。
# 不改语音录入页的听写，也不走图片处理的流程。
# 加本页时不要升级或修改旧功能正在用的公共包（transformers / torch 等）。
# 2026-09 为了本页把 transformers 升到 4.57，语音克隆的声音变成乱声。

from __future__ import annotations

import os
from datetime import datetime

from PyQt5.QtCore import Qt, QThread, QTimer, QSize, QRectF, QPointF, QEvent, pyqtSignal
from PyQt5.QtGui import (
    QColor, QPainter, QPen, QBrush, QPainterPath, QPixmap, QIcon,
    QTextCursor,
)
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QButtonGroup,
    QCheckBox, QSizePolicy, QFrame, QScrollArea, QLayout, QTabWidget,
)

from styles.style_all import (
    make_card, install_card_title, restyle_card_title, restyle_card_frame,
    apply_primary_button, fit_primary_button, theme, tk, sp, CARD_RADIUS,
    TAB_QSS, VIDEO_TAB_QSS, fmt, apply_transparent_surface,
)
from utils.auk_client import AukClient, model_paths
from utils.logger import get_logger
from pages.page_cosvoice3 import PageCosVoice3, _apply_tab_page_bg
from pages.shared_log_panel import SharedLogPanel

log = get_logger(__name__)

_TASKS = (
    ("enhance", "降噪"),
    ("replace", "改词"),
    ("tts", "配音"),
    ("emotion", "改情绪"),
    ("whisper", "转耳语"),
)
_TASK_LABEL = dict(_TASKS)
# 产物文件名中间段用的处理方式简写（卡片上仍显示上面的完整叫法）
_TASK_SHORT = {
    "enhance": "降噪",
    "replace": "换词",
    "tts": "配音",
    "emotion": "情绪",
    "whisper": "耳语",
}
_TASK_KEYS = {k for k, _n in _TASKS}
_EMOTIONS = ("开心", "悲伤", "生气", "温柔", "平静")
# 结果行图标基准尺寸
_ICON_PX = 18


def _norm(path: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(path or ""))
    except Exception:
        return os.path.normcase(path or "")


def _voice_clone_dir() -> str:
    from utils.app_paths import voice_clone_dir
    return voice_clone_dir()


def _tool_icon(kind: str, color: str, size: int = _ICON_PX) -> QIcon:
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


def _spoken(text: str) -> str:
    """配音文案收成一行。换行留在提示里时，模型容易把几个字含糊过去。"""
    return " ".join((text or "").split())


def _tts_seconds(text: str) -> float:
    """按汉字留够时间，并在末尾多留一口气，避免最后几个字被挤糊。"""
    units = 0.0
    for ch in text or "":
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF:
            units += 1.0
        elif ch.isalnum():
            units += 0.5
        elif ch in "，。！？、；：,.!?;":
            units += 0.35
    return max(1.8, min(20.0, units * 0.27 + 0.65))


def _instruction(task: str, text: str, old: str, new: str, emotion: str) -> str:
    if task == "enhance":
        return "去除这段语音里的噪声和混响，还原清晰自然的说话声，保持说话人和说话内容不变。"
    if task == "replace":
        return f"把「{old.strip()}」替换成「{new.strip()}」，保持原来的音色。"
    if task == "tts":
        # 模型认的是这句英文模板，中文也放在引号里，不要改成另一套说法。
        return f'Say the following with the same voice: "{_spoken(text)}"'
    if task == "emotion":
        return f"把情绪改成{emotion}，保持说话内容和音色不变。"
    if task == "whisper":
        return "把这段话转成耳语，保持说话人和内容不变。"
    return ""


class _RunThread(QThread):
    finished_ok = pyqtSignal(dict)

    def __init__(self, client: AukClient, req: dict, parent=None):
        super().__init__(parent)
        self._client = client
        self._req = req

    def run(self):
        try:
            self.finished_ok.emit(self._client.run(self._req))
        except Exception as exc:
            self.finished_ok.emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


class _OpGlass(QWidget):
    """盖住未选中的处理卡，点击后改选这一张。"""

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        if parent is not None:
            parent.installEventFilter(self)
            self.setGeometry(parent.rect())
            self.raise_()

    def _fit(self):
        p = self.parentWidget()
        if p is None:
            return
        self.setGeometry(p.rect())
        if self.isVisible():
            self.raise_()

    def eventFilter(self, obj, event):
        if obj is self.parent() and event.type() in (QEvent.Resize, QEvent.Show):
            self._fit()
        return False

    def showEvent(self, e):
        super().showEvent(e)
        self._fit()

    def paintEvent(self, _e):
        w, h = self.width(), self.height()
        if w <= 2 or h <= 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        try:
            dark = bool(theme.is_dark)
        except Exception:
            dark = True
        fill = QColor(8, 14, 28, 168) if dark else QColor(226, 232, 240, 176)
        try:
            r = float(sp(CARD_RADIUS))
        except Exception:
            r = float(CARD_RADIUS)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), r, r)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(fill))
        p.drawPath(path)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
            e.accept()
            return
        super().mousePressEvent(e)


class _CardRing(QWidget):
    """选中处理卡的亮色描边，不占布局，也不吃鼠标。"""

    PEN_W = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.hide()
        if parent is not None:
            parent.installEventFilter(self)
            self.setGeometry(parent.rect())

    def eventFilter(self, obj, event):
        if obj is self.parent() and event.type() in (QEvent.Resize, QEvent.Show):
            self.setGeometry(obj.rect())
            if self.isVisible():
                self.raise_()
        return False

    def paintEvent(self, _e):
        w, h = self.width(), self.height()
        if w <= self.PEN_W + 1 or h <= self.PEN_W + 1:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        try:
            color = QColor(tk("accent"))
            if theme.is_dark:
                hi = QColor(tk("sel_text"))

                def _m(a, b):
                    return int(round(a + (b - a) * 0.45))

                color = QColor(
                    _m(color.red(), hi.red()),
                    _m(color.green(), hi.green()),
                    _m(color.blue(), hi.blue()),
                )
        except Exception:
            color = QColor("#6db5ff")
        try:
            r = max(2, int(float(sp(CARD_RADIUS)) - 1))
        except Exception:
            r = int(float(CARD_RADIUS) - 1)
        half = self.PEN_W / 2.0
        rect = QRectF(half, half, w - self.PEN_W, h - self.PEN_W)
        path = QPainterPath()
        path.addRoundedRect(rect, r, r)
        p.setPen(QPen(color, self.PEN_W))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)


class _QuietScroll(QScrollArea):
    """处理卡按自己的高度排，滚轮可滑，不画出滚动条。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(False)
        self.setFrameShape(QFrame.NoFrame)
        self.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollBar:vertical{width:0px;}"
            "QScrollBar:horizontal{height:0px;}"
        )

    def setWidget(self, w):
        old = self.widget()
        if old is not None:
            old.removeEventFilter(self)
        super().setWidget(w)
        if w is not None:
            w.setMinimumWidth(0)
            w.installEventFilter(self)
            self._sync()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync()

    def eventFilter(self, obj, event):
        if obj is self.widget() and event.type() in (
            QEvent.LayoutRequest, QEvent.Resize, QEvent.Show,
        ):
            QTimer.singleShot(0, self._sync)
        return super().eventFilter(obj, event)

    def _sync(self):
        inner = self.widget()
        if inner is None:
            return
        vw = max(1, self.viewport().width())
        hint = inner.sizeHint()
        minh = inner.minimumSizeHint()
        h = max(1, int(hint.height()), int(minh.height()))
        if inner.width() != vw or inner.height() != h:
            inner.setFixedSize(vw, h)



class PageAuk(QWidget):
    def __init__(self, log_host=None, shared_log=None):
        super().__init__()
        # 「音频处理」页的共享记录面板；None = 状态日志留在本页底栏
        self._log_host = log_host
        # 两页合用的那条时间线。构造时就给好，本页就不用再建自己的日志框
        self._shared_log = shared_log
        self.setObjectName("PageAuk")
        self._client = AukClient()
        self._thread = None
        self._active = ""
        self._status_lines = []
        self._status_booted = False
        self._run_started = None
        self._run_label = ""
        self._run_timer = QTimer(self)
        self._run_timer.setInterval(1000)
        self._run_timer.timeout.connect(self._tick_run)
        self._voice_src = None      # 共享声音卡来源（CosVoice3），由容器注入
        self._result_src = None     # 共用结果列表（CosVoice3），由容器注入
        self._led_state = "idle"
        self._led_changed_cb = None
        self._op_cards = {}
        self._titles = []
        self._hint_labels = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)          # 对齐 CosVoice3 页根

        # 「生成」按钮不放在页内：交给「音频处理」容器摆到共享播放行右端
        self.btn_run = QPushButton("生成")
        apply_primary_button(self.btn_run)
        fit_primary_button(self.btn_run)
        self.btn_run.clicked.connect(self._start)
        self._corner_btn = self.btn_run

        # 结果列表已上提为容器右 40% 的共用区，本页只留处理卡 + 状态栏
        root.addWidget(self._build_ops(), 1)
        root.addWidget(self._build_foot(), 0)

        self._refresh_status()
        theme.changed.connect(self._restyle)

    def hasHeightForWidth(self):
        return False

    # ── 共享声音卡 ────────────────────────────────────────────────────
    def set_voice_source(self, page):
        """注入共享声音卡来源（CosVoice3 页签）。"""
        self._voice_src = page

    def set_result_source(self, page):
        """注入共用结果列表（CosVoice3 页签持有）。"""
        self._result_src = page

    # ── 共享状态灯 ────────────────────────────────────────────────────
    def set_led_changed_callback(self, fn):
        """状态变化上报：容器按当前页签把共享状态灯切到 AUK 这套文案。"""
        self._led_changed_cb = fn

    def led_state(self) -> str:
        return self._led_state or "idle"

    def set_shared_log(self, panel):
        """注入「音频处理」页的共用记录面板（与 CosVoice3 同一条时间线）。"""
        self._shared_log = panel

    def _set_led(self, state: str):
        state = state if state in ("idle", "busy", "ok", "bad") else "idle"
        if state == self._led_state:
            return
        self._led_state = state
        cb = getattr(self, "_led_changed_cb", None)
        if callable(cb):
            try:
                cb(state)
            except Exception:
                log.exception("AUK 状态灯回调失败")

    def selected_voice_path(self) -> str:
        src = self._voice_src
        if src is not None and hasattr(src, "selected_voice_path"):
            try:
                return src.selected_voice_path() or ""
            except Exception:
                log.exception("读取共享声音卡失败")
        return ""

    def _audio_label(self) -> str:
        src = self._voice_src
        if src is not None and hasattr(src, "selected_voice_name"):
            try:
                name = str(src.selected_voice_name() or "").strip()
                if name:
                    return name
            except Exception:
                log.exception("读取共享声音卡名失败")
        return "未选声音"

    def _source_stem(self) -> str:
        """选中声音卡的原文件名（不含扩展名）。卡改过名也能追回来源。"""
        src = self._voice_src
        if src is not None and hasattr(src, "selected_voice_source_stem"):
            try:
                return str(src.selected_voice_source_stem() or "").strip()
            except Exception:
                log.exception("读取声音卡原文件名失败")
        return ""

    def _audio_name_for_file(self) -> str:
        """产物文件名的「声音卡」段：**改名优先**，没改名才用原文件名。

        原来反过来取原文件名，卡片改过名也只体现在结果行标题里，文件名追不到
        用户的命名；CosVoice3 那边是改名优先，两页产出因此对不上。
        """
        return self._audio_label() or self._source_stem()

    def _caption_for_file(self, spoken: str = "") -> str:
        """产物文件名的中间段。

        非配音用处理方式简写（降噪/换词/情绪/耳语）；配音按约定带文案，
        形如「配音早上好今天」，这样和 CosVoice3 的纯字幕段能一眼分开。
        """
        short = _TASK_SHORT.get(self._task(), self._task() or "")
        if self._task() != "tts" or not spoken:
            return short
        from utils.cosyvoice_clone import CAPTION_HANZI, _first_hanzi

        body = _first_hanzi(spoken, CAPTION_HANZI, "")
        return f"{short}{body}" if body else short

    def _build_ops(self) -> QWidget:
        scroll = _QuietScroll()
        scroll.setObjectName("AukOpScroll")
        inner = QWidget()
        inner.setObjectName("AukOpInner")
        inner.setAttribute(Qt.WA_StyledBackground, True)
        inner.setStyleSheet("#AukOpInner{background:transparent;}")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignTop)
        try:
            lay.setSizeConstraint(QLayout.SetMinimumSize)
        except Exception:
            pass

        # 「省显存」原来在处理区顶部，挪到共享播放行「可拖文件…」左边
        self.chk_offload = QCheckBox("省显存")
        self.chk_offload.setToolTip("更慢。显存不够时保持勾选。")
        self.chk_offload.setChecked(True)

        def _pack(key: str, title: str):
            card = make_card("CardAuk" + key[:1].upper() + key[1:])
            card.setMinimumWidth(0)
            card.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
            box = QVBoxLayout(card)
            box.setContentsMargins(8, 4, 8, 4)
            box.setSpacing(4)
            try:
                box.setSizeConstraint(QLayout.SetMinimumSize)
            except Exception:
                pass
            self._titles.append(install_card_title(card, box, title, gap=0))
            lay.addWidget(card, 0, Qt.AlignTop)
            return card, box

        card, box = _pack("enhance", "降噪")
        self.lbl_enhance = self._hint("去掉噪声和混响，说话人和内容不变。")
        self._sit_beside_title(self._titles[-1], self.lbl_enhance)
        self._install_op("enhance", card)

        card, box = _pack("replace", "改词")
        self.edit_old = self._two_line_edit("原来的词")
        self.edit_new = self._two_line_edit("改成")
        box.addWidget(self.edit_old)
        box.addWidget(self.edit_new)
        self.edit_old.textChanged.connect(self._refresh_run_enabled)
        self.edit_new.textChanged.connect(self._refresh_run_enabled)
        self._install_op("replace", card)

        card, box = _pack("tts", "配音")
        self.edit_text = QTextEdit()
        self.edit_text.setObjectName("AukTtsEdit")
        self.edit_text.setAcceptRichText(False)
        self.edit_text.setPlaceholderText("要说的话")
        self.edit_text.setLineWrapMode(QTextEdit.WidgetWidth)
        self.edit_text.setTabChangesFocus(True)
        self.edit_text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.edit_text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.edit_text.document().setDocumentMargin(2)
        self.edit_text.setFixedHeight(self._box_height(self.edit_text, 4))
        box.addWidget(self.edit_text)
        self.edit_text.textChanged.connect(self._refresh_run_enabled)
        self._install_op("tts", card)

        card, box = _pack("emotion", "改情绪")
        emo_row = QHBoxLayout()
        emo_row.setContentsMargins(0, 0, 0, 0)
        emo_row.setSpacing(6)
        self._emo_group = QButtonGroup(self)
        self._emo_group.setExclusive(True)
        self._emo_btns = []
        for i, name in enumerate(_EMOTIONS):
            btn = QPushButton(name)
            btn.setObjectName("AukEmoBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setMinimumWidth(0)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self._emo_group.addButton(btn, i)
            self._emo_btns.append(btn)
            emo_row.addWidget(btn)
        self._emo_btns[0].setChecked(True)
        emo_host = QWidget()
        emo_host.setAttribute(Qt.WA_StyledBackground, True)
        emo_host.setStyleSheet("background:transparent;border:none;")
        emo_host.setLayout(emo_row)
        self._sit_beside_title(self._titles[-1], emo_host)
        self._install_op("emotion", card)

        card, box = _pack("whisper", "转耳语")
        self.lbl_whisper = self._hint("转成耳语，说话人和内容不变。")
        self._sit_beside_title(self._titles[-1], self.lbl_whisper)
        self._install_op("whisper", card)

        scroll.setWidget(inner)
        try:
            scroll.viewport().setAutoFillBackground(False)
            scroll.viewport().setStyleSheet("background:transparent;")
        except Exception:
            pass
        self._op_scroll = scroll
        self._op_inner = inner
        self._op_blank_watch = {scroll, inner, scroll.viewport()}
        for w in self._op_blank_watch:
            w.installEventFilter(self)
        self._style_emo_btns()
        return scroll

    def _hint(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setWordWrap(False)
        lab.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._hint_labels.append(lab)
        return lab

    def _sit_beside_title(self, title_label: QLabel, widget: QWidget) -> None:
        """说明或输入跟在标题右边，能一行写完的不再另起一行。"""
        head = title_label.parentWidget()
        lay = head.layout() if head is not None else None
        if lay is None:
            return
        lay.removeWidget(title_label)
        row_w = QWidget()
        row_w.setAttribute(Qt.WA_StyledBackground, True)
        row_w.setStyleSheet("background:transparent;border:none;")
        row = QHBoxLayout(row_w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        title_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row.addWidget(title_label, 0, Qt.AlignVCenter)
        row.addWidget(widget, 1, Qt.AlignVCenter)
        lay.addWidget(row_w)

    def process_button(self):
        """本页的「生成」按钮（蓝底白字）。容器把它摆到共享播放行右端。"""
        return getattr(self, "_corner_btn", None)

    def set_process_visible(self, visible: bool):
        btn = self.process_button()
        if btn is not None:
            btn.setVisible(bool(visible))

    def _two_line_edit(self, placeholder: str) -> QTextEdit:
        edit = QTextEdit()
        edit.setAcceptRichText(False)
        edit.setPlaceholderText(placeholder)
        edit.setLineWrapMode(QTextEdit.WidgetWidth)
        edit.setTabChangesFocus(True)
        edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        edit.document().setDocumentMargin(2)
        edit.setFixedHeight(self._box_height(edit, 2))
        return edit

    def _build_foot(self) -> QWidget:
        """状态日志的落脚点。

        接了共用记录面板时，本页不再建自己的日志框（两页写进同一条时间线）。
        """
        box_owner = getattr(self, "_log_host", None)
        if box_owner is not None:
            self._make_status_log()
            _host_lay = box_owner.layout()
            if _host_lay is None:
                _host_lay = QVBoxLayout(box_owner)
                _host_lay.setContentsMargins(0, 0, 0, 0)
                _host_lay.setSpacing(0)
            _host_lay.addWidget(self.log_status, 1)
            return QWidget()   # 空占位：调用方照旧 addWidget，进容器时是 no-op
        if getattr(self, "_shared_log", None) is not None:
            return QWidget()   # 共用面板接管，本页不留底栏
        foot = QWidget()
        foot.setObjectName("AukFoot")
        foot.setAttribute(Qt.WA_StyledBackground, True)
        foot.setStyleSheet("#AukFoot{background:transparent;}")
        foot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        box = QVBoxLayout(foot)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.setAlignment(Qt.AlignBottom)
        self._make_status_log()
        box.addWidget(self.log_status, 0, Qt.AlignBottom)
        return foot

    def _make_status_log(self) -> None:
        self.log_status = QTextEdit()
        self.log_status.setObjectName("AukStatusLog")
        self.log_status.setReadOnly(True)
        self.log_status.setAcceptRichText(False)
        self.log_status.setLineWrapMode(QTextEdit.WidgetWidth)
        self.log_status.setPlaceholderText("这里显示状态")
        self.log_status.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.log_status.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.log_status.setFrameShape(QFrame.NoFrame)
        # 共享记录面板里要吃满高度；本页底栏时按行数收缩
        if getattr(self, "_log_host", None) is not None:
            self.log_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        else:
            self.log_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.log_status.document().setDocumentMargin(0)
        self._style_status_log()

    def log_widget(self):
        """供容器放进共享记录面板并按页签切换。"""
        return getattr(self, "log_status", None)

    def _style_status_log(self):
        box = getattr(self, "log_status", None)
        if box is None:
            return
        try:
            fg = tk("text_mut")
        except Exception:
            fg = "#9fb0d7"
        box.setStyleSheet(
            "QTextEdit#AukStatusLog{"
            "background:transparent;border:none;padding:0;"
            f"font-size:13px;color:{fg};}}"
            "QTextEdit#AukStatusLog QScrollBar:vertical{width:0px;margin:0;}"
            "QTextEdit#AukStatusLog QScrollBar:horizontal{height:0px;margin:0;}"
        )

    def _install_op(self, key: str, card: QFrame) -> None:
        glass = _OpGlass(card)
        glass.clicked.connect(lambda _=False, k=key: self._set_active_op(k))
        card._op_glass = glass
        ring = _CardRing(card)
        card._op_ring = ring
        self._op_cards[key] = card
        glass.hide()
        ring.hide()

    def eventFilter(self, obj, ev):
        if (
            ev is not None
            and ev.type() == QEvent.MouseButtonPress
            and obj in getattr(self, "_op_blank_watch", ())
        ):
            try:
                if ev.button() == Qt.LeftButton and not self._press_on_op_card(ev.globalPos()):
                    self._clear_op()
            except Exception:
                pass
        return super().eventFilter(obj, ev)

    def _press_on_op_card(self, gpos) -> bool:
        try:
            w = QApplication.widgetAt(gpos)
        except Exception:
            w = None
        cards = set(self._op_cards.values())
        while w is not None:
            if w in cards:
                return True
            w = w.parentWidget()
        return False

    def _set_active_op(self, key: str) -> None:
        if key not in _TASK_KEYS or key == self._active:
            return
        self._active = key
        self._apply_op_chrome()
        self._refresh_run_enabled()

    def _clear_op(self) -> None:
        if not self._active:
            return
        self._active = ""
        self._apply_op_chrome()
        self._refresh_run_enabled()

    # ── 「生成」按钮可用性 ────────────────────────────────────────────
    def _run_blockers(self) -> list:
        """列出当前不满足的条件。空列表 = 可点。"""
        out = []
        try:
            if not model_paths()["ready"]:
                out.append("AuK 模型没齐")
        except Exception:
            out.append("读不到 AuK 模型")
        if self._thread is not None or (
            getattr(self, "_run_timer", None) is not None and self._run_timer.isActive()
        ):
            out.append("正在运行")
        task = self._task()
        if not task:
            out.append("先选一种处理方式")
        audio = self.selected_voice_path().strip()
        if not audio or not os.path.isfile(audio):
            out.append("先在上方选一张声音卡")
        if task == "tts" and not self.edit_text.toPlainText().strip():
            out.append("先写要说的话")
        if task == "replace":
            if not self.edit_old.toPlainText().strip():
                out.append("填原来的词")
            if not self.edit_new.toPlainText().strip():
                out.append("填改成后的词")
        return out

    def _refresh_run_enabled(self):
        """不满足条件时「生成」按钮冻结（禁用）。"""
        btn = getattr(self, "btn_run", None)
        if btn is None:
            return
        blockers = self._run_blockers()
        btn.setEnabled(not blockers)
        try:
            btn.setToolTip("" if not blockers else "；".join(blockers))
        except Exception:
            pass

    def _apply_op_chrome(self):
        active = self._task()
        for key, card in self._op_cards.items():
            on = bool(active) and key == active
            ring = getattr(card, "_op_ring", None)
            glass = getattr(card, "_op_glass", None)
            if on:
                if glass is not None:
                    glass.hide()
                if ring is not None:
                    ring.setGeometry(card.rect())
                    ring.show()
                    ring.raise_()
            else:
                if ring is not None:
                    ring.hide()
                if glass is not None:
                    glass.show()
                    glass._fit()
            want = "1" if on else "0"
            try:
                if str(card.property("cardActive") or "0") != want:
                    card.setProperty("cardActive", want)
                    restyle_card_frame(card)
            except Exception:
                pass

    def _outline_qss(self) -> str:
        try:
            bd, fg = tk("border"), tk("text")
            mut, on_bd = tk("text_mut"), tk("accent")
            on_bg, on_fg = tk("sel_bg"), tk("sel_text")
        except Exception:
            bd, fg, mut = "#25345c", "#d7def7", "#9fb0d7"
            on_bd, on_bg, on_fg = "#3a8ee0", "#1f3a8a", "#93c5fd"
        return (
            "QPushButton#AukEmoBtn{"
            f"background:transparent;color:{fg};border:1px solid {bd};"
            "border-radius:6px;padding:2px 4px;font-size:12px;font-weight:600;"
            "min-height:24px;max-height:26px;}"
            "QPushButton#AukEmoBtn:hover{"
            f"border-color:{on_bd};color:{on_fg};}}"
            "QPushButton#AukEmoBtn:checked{"
            f"background:{on_bg};border-color:{on_bd};color:{on_fg};}}"
            "QPushButton#AukEmoBtn:disabled{"
            f"color:{mut};border-color:{bd};}}"
        )

    def _style_emo_btns(self):
        qss = self._outline_qss()
        for b in getattr(self, "_emo_btns", []) or []:
            b.setStyleSheet(qss)

    def _style_hints(self):
        try:
            mut = tk("text_mut")
        except Exception:
            mut = "#94a3b8"
        qss = f"color:{mut};background:transparent;border:none;"
        for lab in self._hint_labels:
            lab.setStyleSheet(qss)

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_text_boxes()

    def _box_height(self, edit, lines: int) -> int:
        try:
            edit.ensurePolished()
        except Exception:
            pass
        fm = edit.fontMetrics()
        line = max(fm.lineSpacing(), fm.height(), 1)
        try:
            margin = int(edit.document().documentMargin()) * 2
        except Exception:
            margin = 4
        return int(line) * int(lines) + margin + edit.frameWidth() * 2 + 14

    def _fit_text_boxes(self):
        for edit, lines in (
            (getattr(self, "edit_text", None), 4),
            (getattr(self, "edit_old", None), 2),
            (getattr(self, "edit_new", None), 2),
        ):
            if edit is None:
                continue
            h = self._box_height(edit, lines)
            if edit.height() != h:
                edit.setFixedHeight(h)
        self._fit_status_height()

    def _fit_status_height(self):
        """状态文字贴在左列底部，高度跟文字走，不再在空框里悬着。

        进了共享记录面板后不再限高：面板高度就是给它用的，跟 CosVoice3 的
        记录列表一样铺满，切换页签时两份记录看起来一致。
        """
        box = getattr(self, "log_status", None)
        if box is None:
            return
        try:
            box.document().setDocumentMargin(0)
        except Exception:
            pass
        if getattr(self, "_log_host", None) is not None:
            try:
                box.setMinimumHeight(0)
                box.setMaximumHeight(16777215)
                box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            except Exception:
                pass
            return
        line = max(box.fontMetrics().lineSpacing(), box.fontMetrics().height(), 1)
        used = 0
        block = box.document().begin()
        while block.isValid():
            lay = block.layout()
            used += max(1, lay.lineCount() if lay is not None else 1)
            block = block.next()
        used = max(1, min(4, used))
        h = line * used
        box.setMinimumHeight(h)
        box.setMaximumHeight(h)
        if box.height() != h:
            box.setFixedHeight(h)

    def _restyle(self, *_):
        self._style_status_log()
        self._style_hints()
        self._style_emo_btns()
        for card in list(self._op_cards.values()):
            try:
                restyle_card_frame(card)
            except Exception:
                pass
        for t in self._titles:
            try:
                restyle_card_title(t)
            except Exception:
                pass
        self._apply_op_chrome()

    def _refresh_status(self):
        paths = model_paths()
        if paths["ready"]:
            if not self._status_booted:
                self._status_booted = True
                # 精简：原来是一整段使用说明，属于噪音，只留状态本身
                self._append("AuK 就绪")
        else:
            missing = []
            if not os.path.isfile(paths["ckpt"]):
                missing.append("AuK 权重")
            if not os.path.isdir(paths["qwen"]):
                missing.append("Qwen2.5-Omni-3B")
            if not os.path.isdir(paths["src"]):
                missing.append("AuK 运行库 model/AuK-src")
            if not self._status_booted:
                self._status_booted = True
                self._append("还不能开始。还缺：" + "、".join(missing), level="warn")
        self._refresh_run_enabled()
        self._restyle()

    def _task(self) -> str:
        return self._active if self._active in _TASK_KEYS else ""

    def _emotion(self) -> str:
        for b in getattr(self, "_emo_btns", []) or []:
            if b.isChecked():
                return b.text()
        return _EMOTIONS[0]

    def _paint_status(self):
        box = getattr(self, "log_status", None)
        if box is None:
            return
        text = "\n".join(self._status_lines)
        if box.toPlainText() == text:
            return
        box.setPlainText(text)
        cur = box.textCursor()
        cur.movePosition(QTextCursor.End)
        box.setTextCursor(cur)
        box.ensureCursorVisible()
        self._fit_status_height()

    def _append(self, text: str, replace_prefix: str = "", level: str = "info"):
        """写记录。

        replace_prefix 默认空 = 追加新行。只有「正在运行」那类会原地刷新的行
        才传前缀——之前这里无条件传 "正在运行"，导致第一条 AUK 消息被打上标记，
        之后每条都去覆盖它，所有提示塌成一行。
        """
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
        self._status_lines.append(text)
        if len(self._status_lines) > 40:
            del self._status_lines[:-40]
        self._paint_status()
        log.info("auk %s", text)

    def _run_line(self, sec: int) -> str:
        label = self._run_label or _TASK_LABEL.get(self._task(), "处理")
        return f"正在运行 · {label} · {self._audio_label()}"

    def _tick_run(self):
        # 精简：面板里只留一条静态的「正在运行 · 任务 · 素材」，
        # 原来每秒把「已用 N 秒」刷一遍，对用户没意义
        if self._run_started is None:
            return
        # 同前缀原地替换，所以计时刷新不会堆行
        self._append(self._run_line(0), replace_prefix="正在运行")

    def _begin_run(self, label: str):
        self._run_label = label
        self._run_started = datetime.now()
        self.btn_run.setText("生成中…")
        fit_primary_button(self.btn_run)
        self._set_led("busy")
        self._tick_run()
        self._run_timer.start()
        self._refresh_run_enabled()

    def _end_run(self):
        if self._run_timer.isActive():
            self._tick_run()
        self._run_timer.stop()
        self._run_started = None
        self.btn_run.setText("生成")
        fit_primary_button(self.btn_run)
        self._refresh_run_enabled()

    def _start(self):
        task = self._task()
        audio = self.selected_voice_path().strip()
        if not task:
            self._append("先选一种处理方式")
            return
        text = self.edit_text.toPlainText().strip()
        old = self.edit_old.toPlainText().strip()
        new = self.edit_new.toPlainText().strip()
        if not audio or not os.path.isfile(audio):
            self._append("先在上方选一张声音卡")
            return
        if task == "tts" and not text:
            self._append("先写要说的话")
            return
        if task == "replace" and (not old or not new):
            self._append("改词需要原来的词和改成的词")
            return
        paths = model_paths()
        from utils.cosyvoice_clone import new_out_wav_path
        spoken = _spoken(text) if task == "tts" else ""
        caption = self._caption_for_file(spoken)
        out = new_out_wav_path(_voice_clone_dir(), self._audio_name_for_file(), caption)
        seconds = None
        gen_text = ""
        if task == "tts":
            seconds = _tts_seconds(spoken)
            gen_text = spoken
            text = spoken
        req = {
            "config": paths["config"],
            "ckpt": paths["ckpt"],
            "qwen": paths["qwen"],
            "src": paths["src"],
            "instruction": _instruction(task, text, old, new, self._emotion()),
            "audio": audio,
            "out": out,
            "gen_seconds": seconds,
            "gen_text": gen_text,
            "cpu_offload": self.chk_offload.isChecked(),
        }
        self._begin_run(_TASK_LABEL.get(task, "处理"))
        self._thread = _RunThread(self._client, req, self)
        self._thread.finished_ok.connect(self._on_done)
        self._thread.start()

    def _on_done(self, data: dict):
        label = self._run_label or _TASK_LABEL.get(self._task(), "处理")
        self._thread = None
        self._end_run()
        self._refresh_status()
        if data.get("ok") and data.get("out"):
            path = data["out"]
            self._add_result(path, self._result_title(label))
            self._set_led("ok")
            self._append(
                f"已完成 · {label}。结果已放在右边，点一下就播放。",
                replace_prefix="正在运行",
                level="ok",
            )
            return
        err = (data.get("error") or "失败").strip()
        self._set_led("bad")
        self._append(
            f"失败 · {label}。" + err.splitlines()[-1][:240],
            replace_prefix="正在运行",
            level="err",
        )

    def _result_title(self, label: str) -> str:
        if self._task() == "tts":
            body = _spoken(self.edit_text.toPlainText())
        elif self._task() == "replace":
            body = (
                f"{self.edit_old.toPlainText().strip()} → "
                f"{self.edit_new.toPlainText().strip()}"
            )
        elif self._task() == "emotion":
            body = self._emotion()
        else:
            # 降噪 / 转耳语：带上原文件名，卡片改过名也追得到来源
            body = self._audio_label()
            stem = self._source_stem()
            if stem and stem != body:
                body = f"{body} · 原名 {stem}"
        body = " ".join((body or "").split())
        return f"{label}  {body}".strip()

    def _add_result(self, path: str, title: str) -> None:
        """成品推进共用结果列表，并记一条任务索引让重启后行首文字不丢。"""
        try:
            from utils.auk_client import record_task_title
            record_task_title(path, title)
        except Exception:
            log.exception("记录 AUK 任务标题失败")
        self._push_shared_result(path, title)

    def _push_shared_result(self, path: str, title: str) -> None:
        src = self._result_src
        if src is not None and hasattr(src, "add_result"):
            try:
                src.add_result(path, title)
                return
            except Exception:
                log.exception("推进共用结果列表失败")
        self._append("结果已生成，但共用列表没接上")

    def shutdown(self):
        try:
            self._run_timer.stop()
        except Exception:
            pass
        try:
            self._client.stop()
        except Exception:
            log.exception("关闭 AUK 子进程失败")


# ══════════════════════════════════════════════════════════════════════
# 「音频处理」容器页：AUK · CosVoice3
# 版式对齐视频下载 / 音频下载：QTabWidget + 页签图标 + 统一子页底色。
# ══════════════════════════════════════════════════════════════════════

_TAB_ICON_PX = 22

# 共享状态灯的两套文案：跟着当前页签切
_COS_LED_CAPTION = {
    "idle": ("就绪", "#94a3b8"),
    "busy": ("生成中", "#eab308"),
    "ok": ("完成", "#22c55e"),
    "bad": ("失败", "#ef4444"),
}
# 共享状态灯的两套文案：跟着当前页签切。
# idle 两边都写「就绪」——之前 AUK 写「空闲」，两个子页面同一个灯却两种叫法。
_AUK_LED_CAPTION = {
    "idle": ("就绪", "#94a3b8"),
    "busy": ("推理中", "#eab308"),
    "ok": ("完成", "#22c55e"),
    "bad": ("失败", "#ef4444"),
}


def _tab_icon_base(p, s: float, selected: bool, dark: bool, base_hex: str):
    """页签图标共用的圆角底 + 描边，返回前景色。"""
    if selected:
        base = QColor(base_hex) if dark else QColor(base_hex)
        rim = QColor("#ffffff")
        rim.setAlpha(90)
        fg = QColor("#ffffff")
    else:
        base = QColor("#3a3f4a") if dark else QColor("#c5ccd6")
        base.setAlpha(120 if dark else 140)
        rim = QColor("#6b7280")
        rim.setAlpha(90)
        fg = QColor("#9ca3af")
        fg.setAlpha(180)
    m = s * 0.06
    p.setPen(QPen(rim, max(1.0, s * 0.06)))
    p.setBrush(QBrush(base))
    p.drawRoundedRect(QRectF(m, m, s - 2 * m, s - 2 * m), s * 0.22, s * 0.22)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(fg))
    return fg


def _paint_auk_icon(size: int = _TAB_ICON_PX, selected: bool = True, dark: bool = True) -> QIcon:
    """AUK 页签：橙色圆角底 + 四根声波柱（与侧栏 AUK 图标同一色系）。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = float(size)
    _tab_icon_base(p, s, selected, dark, "#F97316")
    # 四根居中的竖条，高低不同，像一段波形
    bars = ((0.30, 0.26), (0.42, 0.44), (0.54, 0.34), (0.66, 0.20))
    w = s * 0.075
    for bx, bh in bars:
        x = s * bx
        y = s * (0.5 - bh / 2.0)
        p.drawRoundedRect(QRectF(x, y, w, s * bh), w / 2.0, w / 2.0)
    p.end()
    return QIcon(pm)


def _paint_cos_icon(size: int = _TAB_ICON_PX, selected: bool = True, dark: bool = True) -> QIcon:
    """CosVoice3 页签：紫色圆角底 + 话筒（胶囊 + 托架 + 立杆 + 底座）。

    Qt 的弧度从 3 点方向逆时针量，y 向下为正，所以下半圈是 180°→360°。
    """
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = float(size)
    ink = _tab_icon_base(p, s, selected, dark, "#8B5CF6")
    # 话筒身
    p.drawRoundedRect(QRectF(s * 0.40, s * 0.20, s * 0.14, s * 0.30), s * 0.07, s * 0.07)
    # 托架：下半圈弧，包住胶囊底部
    pen = QPen(ink, max(1.0, s * 0.07))
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(s * 0.31, s * 0.31, s * 0.32, s * 0.32), 180 * 16, 180 * 16)
    # 立杆 + 底座
    p.drawLine(QPointF(s * 0.47, s * 0.63), QPointF(s * 0.47, s * 0.73))
    p.drawLine(QPointF(s * 0.37, s * 0.75), QPointF(s * 0.57, s * 0.75))
    p.end()
    return QIcon(pm)


class PageAudioProcess(QWidget):
    """音频处理：AUK · CosVoice3 两页签。"""

    def __init__(self):
        super().__init__()
        apply_transparent_surface(self, "PageAudioProcess")
        self.setStyleSheet(self._page_qss())

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("VideoTabWidget")
        self.tabs.setIconSize(QSize(_TAB_ICON_PX, _TAB_ICON_PX))
        try:
            from ui_main import install_tight_pre_dl_tab_bar
            install_tight_pre_dl_tab_bar(self.tabs)
            # 本页有预下载，角标要能画出来；但空着的时候不必常占两位数字的宽度，
            # 传 "auto"：有排队数了才留位，平时标题后面是干净的
            setter = getattr(self.tabs.tabBar(), "set_info_slot", None)
            if callable(setter):
                setter("auto")
        except Exception:
            log.exception("音频处理页签条安装失败")

        # 共享头：上面一行左 70% 声音卡 / 右 30% 共用记录面板，
        # 下面整行是播放行（[▶] 波形 状态灯 提示 处理 打开文件夹）。
        # 声音卡与记录面板的控件都由 PageCosVoice3 创建后挂进来
        # （卡与播放行的全部逻辑都在它那边）。
        self.shared_host = QWidget()
        self.shared_host.setObjectName("AudioProcessShared")
        self.shared_host.setAttribute(Qt.WA_StyledBackground, True)
        self.shared_host.setStyleSheet("#AudioProcessShared{background:transparent;border:none;}")
        self.shared_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        _shl = QVBoxLayout(self.shared_host)
        _shl.setContentsMargins(0, 0, 0, 0)
        _shl.setSpacing(6)

        self.shared_row = QWidget()
        self.shared_row.setObjectName("AudioProcessSharedRow")
        self.shared_row.setAttribute(Qt.WA_StyledBackground, True)
        self.shared_row.setStyleSheet("#AudioProcessSharedRow{background:transparent;border:none;}")
        # 声音卡 / 记录两块都不要深色底（全局 QWidget 规则会给裸 QWidget 刷底色），
        # 靠中间一条 1px 分隔线区分
        self.card_host = QWidget()
        self.card_host.setObjectName("AudioProcessCards")
        self.card_host.setAttribute(Qt.WA_StyledBackground, True)
        self.card_host.setStyleSheet("#AudioProcessCards{background:transparent;border:none;}")
        self.card_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.log_host = QWidget()
        self.log_host.setObjectName("AudioProcessLog")
        self.log_host.setAttribute(Qt.WA_StyledBackground, True)
        self.log_host.setStyleSheet("#AudioProcessLog{background:transparent;border:none;}")
        self.log_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.shared_sep = QFrame()
        self.shared_sep.setObjectName("AudioProcessSharedSep")
        self.shared_sep.setFixedWidth(1)
        self.shared_sep.setStyleSheet(f"#AudioProcessSharedSep{{background:{tk('border')};border:none;}}")
        self.shared_sep.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        _rlay = QHBoxLayout(self.shared_row)
        _rlay.setContentsMargins(0, 0, 0, 0)
        _rlay.setSpacing(8)              # 对齐 AUK 页根
        _rlay.addWidget(self.card_host, 7)      # 70% 声音卡
        _rlay.addWidget(self.shared_sep, 0)
        _rlay.addWidget(self.log_host, 3)       # 30% 共用记录面板
        _shl.addWidget(self.shared_row, 1)

        # 结果卡上提成容器右 40% 的共用区（两页共用一份列表）
        self.result_host = QWidget()
        self.result_host.setObjectName("AudioProcessResult")
        self.result_host.setAttribute(Qt.WA_StyledBackground, True)
        self.result_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # 结果卡原来在页签里，底色由 #TabAuk / #TabCosVoice3 给；上提成共用区后
        # 自己补上同样的 panel_2，否则卡片看起来是悬空的。
        _apply_tab_page_bg(self.result_host, "AudioProcessResult")
        _rl = QVBoxLayout(self.result_host)
        _rl.setContentsMargins(0, 0, 0, 0)
        _rl.setSpacing(0)

        # 共用记录面板：AUK 与 CosVoice3 写进同一条时间线，不按页签分
        self.log_panel = SharedLogPanel(self.log_host)
        _llay = self.log_host.layout()
        if _llay is None:
            _llay = QVBoxLayout(self.log_host)
            _llay.setContentsMargins(0, 0, 0, 0)
            _llay.setSpacing(0)
        _llay.addWidget(self.log_panel, 1)

        # 两页都把记录写进同一个面板：构造时就传进去，页面自己就不会再建一份
        self.page_auk = PageAuk(shared_log=self.log_panel)
        self.page_cosvoice3 = PageCosVoice3(
            shared_host=self.shared_host,
            result_host=self.result_host,
            card_host=self.card_host,
            log_host=None,
            shared_log=self.log_panel,
        )
        self.log_panel.refresh_theme()
        # AUK 不再有自己的一套卡/结果，改读共享区
        self.page_auk.set_voice_source(self.page_cosvoice3)
        self.page_auk.set_result_source(self.page_cosvoice3)
        _apply_tab_page_bg(self.page_auk, "TabAuk")
        self.page_cosvoice3.apply_tab_bg("TabCosVoice3")

        self.tabs.addTab(self.page_auk, "AUK")
        self.tabs.addTab(self.page_cosvoice3, "CosVoice3")

        # 共享头在上，下面一行：左 60% 页签内容 / 右 40% 共用结果
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)              # 对齐 AUK 页根
        body.addWidget(self.tabs, 6)   # 60%
        body.addWidget(self.result_host, 4)  # 40%
        root.addWidget(self.shared_host, 0)
        root.addLayout(body, 1)
        self._install_corner_buttons()
        # 按钮宽度按「生成中…」量。构造时全局 QSS 还没生效，字体会偏小，
        # 量出来的宽度偏窄；等样式表落位后再量一次。
        QTimer.singleShot(0, self._fit_process_buttons)
        QTimer.singleShot(300, self._fit_process_buttons)

        # 共享状态灯：谁在跑就显示谁的文案
        for page in (self.page_auk, self.page_cosvoice3):
            cb = getattr(page, "set_led_changed_callback", None)
            if callable(cb):
                try:
                    cb(lambda *_a, _p=page: self._sync_led(_p))
                except Exception:
                    log.exception("接线共享状态灯失败")
        # 共享声音卡选中变化 → 刷新 AUK 页「生成」按钮的可用性
        cb = getattr(self.page_cosvoice3, "set_voice_changed_callback", None)
        if callable(cb):
            try:
                cb(lambda *_a: self.page_auk._refresh_run_enabled())
            except Exception:
                log.exception("接线声音卡选中变化失败")
        self.page_auk._refresh_run_enabled()
        self.tabs.currentChanged.connect(lambda _i: self._refresh_tab())
        self._refresh_tab()
        self._sync_led(None)
        theme.changed.connect(self.refresh_theme)

    def _fit_process_buttons(self):
        """按「生成中…」这个最长的文案重算两页按钮的固定宽度。

        构造那一刻全局 QSS 还没生效，fontMetrics() 偏小，算出来的宽度会窄一截；
        这里在样式表落位后再量一次，两页也才会一致。
        """
        for page in (self.page_auk, self.page_cosvoice3):
            getter = getattr(page, "process_button", None)
            btn = getter() if callable(getter) else None
            if btn is not None:
                try:
                    fit_primary_button(btn)
                except Exception:
                    log.exception("重算「生成」按钮宽度失败")

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_process_buttons()

    def _install_corner_buttons(self):
        """两个子页的「生成」按钮摆到共享播放行右端（右对齐，紧跟右对齐的提示）。

        按当前页签只显示一个；两个都留着，按钮自己的可用/文案逻辑照旧生效。
        """
        slot = self.page_cosvoice3.play_row_right_layout() if hasattr(
            self.page_cosvoice3, "play_row_right_layout"
        ) else None
        if slot is None:
            log.exception("播放行右端槽缺失，「生成」按钮无处安放")
            return
        # 「省显存」插到提示左边（槽内最前），只在 AUK 页签显示
        off = getattr(self.page_auk, "chk_offload", None)
        if off is not None:
            try:
                off.setParent(slot.parentWidget())
                slot.insertWidget(0, off, 0, Qt.AlignRight | Qt.AlignVCenter)
            except Exception:
                log.exception("「省显存」进共享播放行失败")
        # 共用记录面板：两页的记录都挂进去，按页签只显示当前页那份
        self._log_widgets = []
        for page in (self.page_auk, self.page_cosvoice3):
            logw = page.log_widget() if hasattr(page, "log_widget") else None
            if logw is not None:
                self._log_widgets.append((page, logw))

        self._corner_btns = []
        for page in (self.page_auk, self.page_cosvoice3):
            btn = page.process_button() if hasattr(page, "process_button") else None
            if btn is None:
                continue
            btn.setParent(slot.parentWidget())
            slot.addWidget(btn, 0, Qt.AlignRight | Qt.AlignVCenter)
            self._corner_btns.append((page, btn))
        # 「打开文件夹」紧跟「处理」右侧（结果区不留标题行，挪到共享播放行）
        rev = getattr(self.page_cosvoice3, "btn_reveal", None)
        if rev is not None:
            try:
                rev.setParent(slot.parentWidget())
                slot.addWidget(rev, 0, Qt.AlignRight | Qt.AlignVCenter)
            except Exception:
                log.exception("「打开文件夹」进共享播放行失败")

    def _sync_corner(self):
        """按当前页签切换：生成按钮、记录面板显示哪份；「省显存」在非 AUK 页冻结。"""
        cur = self.tabs.currentWidget()
        for page, btn in getattr(self, "_corner_btns", ()):
            btn.setVisible(page is cur)
        off = getattr(self.page_auk, "chk_offload", None)
        if off is not None:
            # 常驻显示，只在 AUK 页签可用；CosVoice3 页签下冻结
            off.setEnabled(cur is self.page_auk)
            off.setVisible(True)
        for page, logw in getattr(self, "_log_widgets", ()):
            if logw is not None:
                logw.setVisible(page is cur)

    def _sync_led(self, _src=None):
        """共享状态灯跟着当前页签走：AUK 页签显示推理状态，否则显示生成状态。"""
        led = getattr(self.page_cosvoice3, "status_led", None)
        if led is None:
            return
        auk = self.tabs.currentIndex() == 0
        if auk:
            state = self.page_auk.led_state()
            led.set_captions(_AUK_LED_CAPTION)
        else:
            state = self.page_cosvoice3.led_state()
            led.set_captions(_COS_LED_CAPTION)
        try:
            led.set_state(state)
        except Exception:
            log.exception("刷新共享状态灯失败")

    @staticmethod
    def _page_qss() -> str:
        return (
            fmt(TAB_QSS + VIDEO_TAB_QSS)
            + "\n#PageAudioProcess{background:transparent;border:none;}"
        )

    def _refresh_tab(self):
        """按当前页签重画两个图标（选中高亮 / 未选中降饱和）。"""
        dark = bool(theme.is_dark)
        idx = self.tabs.currentIndex()
        try:
            self.tabs.setTabIcon(0, _paint_auk_icon(_TAB_ICON_PX, selected=(idx == 0), dark=dark))
            self.tabs.setTabIcon(1, _paint_cos_icon(_TAB_ICON_PX, selected=(idx == 1), dark=dark))
            self.tabs.setProperty("firstSelected", "true")
            st = self.tabs.style()
            if st is not None:
                st.unpolish(self.tabs)
                st.polish(self.tabs)
            self.tabs.update()
        except Exception:
            log.exception("刷新音频处理页签图标失败")
        self._sync_corner()
        self._sync_led()

    def refresh_theme(self, *_):
        self.setStyleSheet(self._page_qss())
        for page, name in (
            (self.page_auk, "TabAuk"),
            (self.page_cosvoice3, "TabCosVoice3"),
        ):
            if hasattr(page, "refresh_theme"):
                try:
                    page.refresh_theme()
                except Exception:
                    log.exception("刷新 %s 子页主题失败", name)
        _apply_tab_page_bg(self.page_auk, "TabAuk")
        self.page_cosvoice3.apply_tab_bg("TabCosVoice3")
        _apply_tab_page_bg(self.result_host, "AudioProcessResult")
        lp = getattr(self, "log_panel", None)
        if lp is not None:
            lp.refresh_theme()
        self._fit_process_buttons()
        self._refresh_tab()

    def on_enter(self):
        """侧栏切进本页：两个子页各自刷新。"""
        for page in (self.page_auk, self.page_cosvoice3):
            if hasattr(page, "on_enter"):
                try:
                    page.on_enter()
                except Exception:
                    log.exception("进入音频处理子页失败")
        self._sync_led()

    def goto_tab(self, key: str):
        """key: 'auk' | 'cosvoice3'。"""
        w = self.page_cosvoice3 if key == "cosvoice3" else self.page_auk
        self.tabs.setCurrentWidget(w)

    def export_settings(self) -> dict:
        page = getattr(self, "page_cosvoice3", None)
        if page is not None and hasattr(page, "export_settings"):
            try:
                return page.export_settings()
            except Exception:
                log.exception("导出 CosVoice3 设置失败")
        return {}

    def apply_settings(self, data: dict):
        page = getattr(self, "page_cosvoice3", None)
        if page is not None and hasattr(page, "apply_settings"):
            try:
                page.apply_settings(data or {})
            except Exception:
                log.exception("恢复 CosVoice3 设置失败")

    def shutdown(self):
        for page in (self.page_auk, self.page_cosvoice3):
            if page is not None and hasattr(page, "shutdown"):
                try:
                    page.shutdown()
                except Exception:
                    log.exception("关闭音频处理子页失败")
