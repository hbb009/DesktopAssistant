# pages/page_gamebrain.py
# 游戏大脑：文本区内容 → 后台 OpenRouter/Jev（或其它兼容 API）作答；结果写入「交互记录」日志。
# 本页是 API/JEV 导向；「开工」按钮只走大模型 API，不检查、不检索知识库。
#
# 焦点约定（本页全部操作全程遵守）：
#   本页全部操作全程不得抢占当前窗口焦点——不 activateWindow / raise_ 拉起主窗，
#   不用 grabKeyboard。全局 F7 改由主窗「静默复制」（按侧栏复制归属推送前台选中文字）使用。

from __future__ import annotations

import time


from PyQt5.QtCore import (
    Qt, QTimer, QSize, QThread, pyqtSignal, QObject,
    QPropertyAnimation, QEasingCurve, QAbstractAnimation,
)
from PyQt5.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QFrame, QSizePolicy, QApplication,
    QRadioButton, QButtonGroup,
    QPlainTextEdit, QGraphicsOpacityEffect,
)

from styles.style_all import (
    theme, tk, TEXT_STYLE, make_card, install_card_title,
    apply_mini_button, apply_medium_button,
    CARD_LEFT_GAP, CARD_RIGHT_GAP, CARD_TOP_GAP, CARD_BOTTOM_GAP,
)
from utils.gamebrain import (
    chat, parse_answers, list_api_models,
    normalize_api_base, default_api_base, default_api_key, default_jev_model,
    jev_decisions, is_jev_model, is_openrouter_base,
)
from utils.logger import get_logger
from utils.qthread_util import stop_qthread

log = get_logger(__name__)

TEXT_STYLE_T = TEXT_STYLE + " background: transparent;"
_KB_CARD_W = 72
_KB_CARD_H = 72            # 与宽同尺寸（正方形知识卡）
_KB_CARD_RADIUS = 8.0
_KB_ICON_BTN = 24
_KB_ICON_PX = 18
_KB_BTN_H = 18             # 卡片内小按钮外框高度
_KB_DEL_BTN_W = 22         # 删除按钮宽
_KB_BAND_GAP = 8           # 分类间竖向留白（归属上方分类，作为拖放命中区）
_KB_PALETTE = (
    "#ef4444", "#f97316", "#eab308", "#84cc16",
    "#22c55e", "#14b8a6", "#06b6d4", "#0ea5e9",
    "#3b82f6", "#6366f1", "#8b5cf6", "#a855f7",
    "#d946ef", "#ec4899", "#f43f5e", "#fb7185",
)
_KB_FILTER = (
    "攻略文本 (*.txt *.md *.markdown *.html *.htm *.json *.csv *.log *.rst "
    "*.ini *.cfg *.yaml *.yml);;所有文件 (*.*)"
)
_GROUP_RENAME_ICON_PX = 14
_GROUP_DEL_ICON_PX = 14
# 知识卡拖拽到分类用的私有 mime 类型（不放 text/plain，避免误入输入框）
_KB_CARD_MIME = "application/x-gb-kb-card"

# 常用服务预设：(下拉显示名, base_url, 推荐模型)。游戏大脑默认 OpenRouter + Jev。
_API_PRESETS = (
    ("OpenRouter（Jev）",    "https://openrouter.ai/api/v1",                           "typesafe/jev-1.13"),
    ("DeepSeek 深度求索",    "https://api.deepseek.com/v1",                             "deepseek-chat"),
    ("OpenAI",              "https://api.openai.com/v1",                               "gpt-4o-mini"),
    ("Kimi 月之暗面",        "https://api.moonshot.cn/v1",                             "moonshot-v1-8k"),
    ("智谱 GLM 清言",        "https://open.bigmodel.cn/api/paas/v4",                    "glm-4-flash"),
    ("通义千问（阿里百炼）",  "https://dashscope.aliyuncs.com/compatible-mode/v1",      "qwen-plus"),
    ("豆包（火山方舟）",      "https://ark.cn-beijing.volces.com/api/v3",                ""),
    ("腾讯混元",             "https://api.hunyuan.cloud.tencent.com/v1",               ""),
    ("Ollama（本机）",       "http://127.0.0.1:11434/v1",                              ""),
    ("LM Studio（本机）",    "http://127.0.0.1:1234/v1",                               ""),
)
# 上面预设带出的推荐模型代号（判断“模型框里是不是上一家服务留下的”用）
_API_PRESET_MODELS = {m for _n, _u, m in _API_PRESETS if m}


class DetectWorker(QThread):
    """点「检测」：拉服务商 /models 列表回来填模型下拉；主线程不阻塞。"""

    done = pyqtSignal(list)      # 模型 id 列表（可空）
    fail = pyqtSignal(str)

    def __init__(self, base_url: str, api_key: str, parent=None):
        super().__init__(parent)
        self._base_url = base_url
        self._api_key = api_key
        self._cancel = False

    def stop(self):
        self._cancel = True
        try:
            self.requestInterruption()
        except Exception:
            pass

    def run(self):
        try:
            names = list_api_models(self._base_url, self._api_key, timeout=30)
        except Exception as e:
            if not self._cancel:
                msg = str(e) or "连接失败"
                if "404" in msg:
                    msg = (
                        "该服务不提供模型列表接口（/models 404），"
                        "无法自动拉取。请直接把模型名填进「模型」框再开工。"
                    )
                self.fail.emit(msg)
            return
        if not self._cancel:
            self.done.emit(names)


class AskWorker(QThread):
    """把文本区内容直接送给大模型 API 作答（不检索知识库）。"""

    status = pyqtSignal(str)
    step = pyqtSignal(str, str)  # (step_key, state)  →  _FLOW_STATE_*
    done = pyqtSignal(list)
    fail = pyqtSignal(str)

    def __init__(self, base_url, api_key, model, query, parent=None):
        super().__init__(parent)
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._query = query or ""
        self._cancel = False

    def stop(self):
        self._cancel = True
        try:
            self.requestInterruption()
        except Exception:
            pass

    def _check(self):
        return self._cancel or self.isInterruptionRequested()

    def run(self):
        try:
            self._run()
        except Exception as e:
            log.exception("游戏大脑查询失败")
            if not self._check():
                self.fail.emit(str(e) or "查询失败")

    def _run(self):
        base_url = self._base_url
        api_key = self._api_key
        model = self._model
        query = (self._query or "").strip()

        if self._check():
            return
        self.step.emit("answer", _FLOW_STATE_BUSY)
        self.status.emit("大模型 API 正在作答…")
        # Jev：走 OpenRouter Decisions API；其它模型走 chat/completions
        if is_jev_model(model):
            raw = jev_decisions(base_url, api_key, model, query, timeout=180)
        else:
            raw = chat(base_url, api_key, model, query, timeout=180)
        if self._check():
            return
        answers = parse_answers(raw)
        if not answers:
            self.fail.emit("模型没有给出可用答案")
            return
        self.step.emit("answer", _FLOW_STATE_OK)
        self.done.emit(answers)



TEXT_STYLE_T = TEXT_STYLE + " background: transparent;"

# 交互记录条数上限（按「一条 append」计，多行仍算一条）
_LOG_RECORD_CAP = 200

_FLOW_STATE_IDLE = "idle"
_FLOW_STATE_BUSY = "busy"
_FLOW_STATE_OK = "ok"
_FLOW_STATE_FAIL = "fail"

# 交互卡片里的每一步（文本 → API 作答；开工不经知识库）
_INTERACTION_STEPS = (
    ("answer",     "作答 · 大模型 API"),
)

_FLOW_STATE_TEXT = {
    _FLOW_STATE_IDLE: "等待",
    _FLOW_STATE_BUSY: "进行中",
    _FLOW_STATE_OK:   "完成",
    _FLOW_STATE_FAIL: "失败",
}

_FLOW_STATE_COLOR = {
    _FLOW_STATE_IDLE: None,  # 主题弱化色
    _FLOW_STATE_BUSY: "#f59e0b",  # 工作中：黄色
    _FLOW_STATE_OK:   "#22c55e",
    _FLOW_STATE_FAIL: "#ef4444",
}


class _FlowStepChip(QWidget):
    """交互流程的精简表示：状态色圆点 + 短步骤名，一排胶囊。

    悬停提示完整步骤名与当前状态文字；颜色随状态（灰=等待，
    黄=进行中，绿=完成，红=失败）。用于把原 5 行步骤压成一行。
    """

    H = 20

    def __init__(self, label: str, desc: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background:transparent;border:none;")
        self.setFixedHeight(self.H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        self.dot = QLabel("●")
        self.dot.setFixedWidth(10)
        self.name = QLabel(label)
        lay.addWidget(self.dot)
        lay.addWidget(self.name)
        self._state = _FLOW_STATE_IDLE
        self._tt_desc = desc or label
        self.setToolTip(f"{desc or label}：{_FLOW_STATE_TEXT[_FLOW_STATE_IDLE]}")
        self.refresh()

    def set_state(self, state: str, text: str = ""):
        if state not in _FLOW_STATE_TEXT:
            state = _FLOW_STATE_IDLE
        self._state = state
        tip = (text or "").strip() or _FLOW_STATE_TEXT[state]
        self.setToolTip(f"{self._tt_desc}：{tip}")
        self.refresh()

    def refresh(self):
        try:
            dark = bool(theme.is_dark)
        except Exception:
            dark = True
        color = _FLOW_STATE_COLOR.get(self._state)
        if not color:
            color = "#6f7fa8" if dark else "#94a3b8"
        self.dot.setStyleSheet(
            f"color:{color};font-size:8px;background:transparent;border:none;"
        )
        self.name.setStyleSheet(
            f"color:{color};font-size:11px;font-weight:600;"
            f"background:transparent;border:none;"
        )



class PageGameBrain(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._prefs_cb = None
        self._loading = False
        self._state = "idle"
        self._answers = []
        self._worker = None
        self._detect_worker = None
        self._theme_titles = []
        self._func_cards = []
        self._clip_watch_connected = False
        self._clipboard_capture_enabled = False  # 侧栏剪贴板归属；默认关
        self._clip_ignore = 0
        self._flow_active = None
        self._log_records = []  # 每条为带时间戳的完整记录文本（可多行）
        self._build_ui()
        self._bind_theme()
        self._bind_clipboard_watch()

    def minimumSizeHint(self):
        return QSize(0, 0)

    def hasHeightForWidth(self):
        """取消 height-for-width，窗口高度只由最小高度约束决定。"""
        return False

    def sizeHint(self):
        """把 sizeHint 高度封顶，避免切到本页后第一次拖小窗口被卡在页高。"""
        sh = super().sizeHint()
        if not sh.isValid() or sh.height() <= 0:
            return QSize(0, 0)
        cap = 660
        if int(sh.height()) > cap:
            return QSize(sh.width(), cap)
        return sh

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
                log.exception("游戏大脑偏好回写失败")

    def _bind_theme(self):
        try:
            theme.changed.connect(self._restyle)
        except Exception:
            pass

    def _restyle(self, *_):
        for lbl in self._theme_titles:
            try:
                from styles.style_all import restyle_card_title
                restyle_card_title(lbl)
            except Exception:
                pass
        self._style_result_edit()
        self._style_flow_status()
        self._style_interact_log()
        self._style_status(self.lbl_api, self.lbl_api.text(), self._api_ok)
        for row in getattr(self, "_step_rows", {}).values():
            try:
                row.refresh()
            except Exception:
                pass

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        split = QHBoxLayout()
        split.setContentsMargins(0, 0, 0, 0)
        split.setSpacing(8)

        left = QWidget()
        left.setObjectName("GameAssistLeft")
        left.setAttribute(Qt.WA_StyledBackground, True)
        left.setStyleSheet("#GameAssistLeft{background:transparent;border:none;}")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)
        lv.addWidget(self._build_recognition_card(), 1)

        right = QWidget()
        right.setObjectName("GameAssistRight")
        right.setAttribute(Qt.WA_StyledBackground, True)
        right.setStyleSheet("#GameAssistRight{background:transparent;border:none;}")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)
        # 右栏：大模型 API（自然高）+ 交互记录（吃掉剩余高度）
        api_card = self._build_api_card()
        api_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        rv.addWidget(api_card, 0)
        flow = self._build_interaction_card()
        flow.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        rv.addWidget(flow, 1)

        # 左 60% / 右 40%
        split.addWidget(left, 3)
        split.addWidget(right, 2)
        root.addLayout(split, 1)

    def _build_api_card(self) -> QFrame:
        card = make_card("CardGameAssistApi")
        self._func_cards.append(card)
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setSpacing(6)
        self._theme_titles.append(install_card_title(card, box, "大模型 API"))

        self._api_ok = False
        self.lbl_api = QLabel("默认 OpenRouter + Jev。点「检测」确认连通；开工时按结构化 json_schema 要决策。")
        self.lbl_api.setWordWrap(True)

        row_base = QHBoxLayout()
        row_base.setSpacing(8)
        lbl_b = QLabel("服务地址")
        lbl_b.setObjectName("CalcFieldLabel")
        lbl_b.setStyleSheet("background:transparent;")
        row_base.addWidget(lbl_b)
        self.combo_api_base = QComboBox()
        self.combo_api_base.setObjectName("GameAssistApiBase")
        self.combo_api_base.setEditable(True)
        self.combo_api_base.setInsertPolicy(QComboBox.NoInsert)
        self.combo_api_base.setMaxVisibleItems(12)
        self.combo_api_base.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLength)
        self.combo_api_base.setMinimumContentsLength(16)
        for _name, url, _hint in _API_PRESETS:
            self.combo_api_base.addItem(_name, url)
        idx = self._api_preset_index(default_api_base())
        if idx >= 0:
            self.combo_api_base.setCurrentIndex(idx)
        self.combo_api_base.setEditText(default_api_base())
        self.combo_api_base.setToolTip(
            "默认：OpenRouter（https://openrouter.ai/api/v1），配合 Jev 模型 typesafe/jev-1.13。\n"
            "点右边下拉也可换成其它 OpenAI 兼容服务。\n"
            "也可直接粘贴自定义地址（自动补 https:// 与 /v1）。"
        )
        try:
            self.combo_api_base.lineEdit().editingFinished.connect(self._api_field_edited)
        except Exception:
            pass
        self.combo_api_base.editTextChanged.connect(self._on_base_or_key_edited)
        self.combo_api_base.activated.connect(self._on_api_preset)
        row_base.addWidget(self.combo_api_base, 1)
        box.addLayout(row_base)

        row_key = QHBoxLayout()
        row_key.setSpacing(8)
        lbl_k = QLabel("API 密钥")
        lbl_k.setObjectName("CalcFieldLabel")
        lbl_k.setStyleSheet("background:transparent;")
        row_key.addWidget(lbl_k)
        self.edit_api_key = QLineEdit()
        self.edit_api_key.setPlaceholderText("sk-or-v1-…")
        self.edit_api_key.setEchoMode(QLineEdit.Password)
        self.edit_api_key.setText(default_api_key())
        self.edit_api_key.setToolTip(
            "OpenRouter API Key（sk-or-v1- 开头），保存在本机 user.txt。\n"
            "请求头会带 Authorization: Bearer …"
        )
        self.edit_api_key.editingFinished.connect(self._api_field_edited)
        self.edit_api_key.textEdited.connect(self._on_base_or_key_edited)
        row_key.addWidget(self.edit_api_key, 1)
        box.addLayout(row_key)

        # 模型：JEV 固定 / 其它模型下拉，二选一
        row_mode = QHBoxLayout()
        row_mode.setSpacing(12)
        lbl_m = QLabel("模型")
        lbl_m.setObjectName("CalcFieldLabel")
        lbl_m.setStyleSheet("background:transparent;")
        row_mode.addWidget(lbl_m)
        self._model_mode_group = QButtonGroup(self)
        self._model_mode_group.setExclusive(True)
        self.radio_model_jev = QRadioButton("JEV 大模型")
        self.radio_model_other = QRadioButton("其它模型")
        self.radio_model_jev.setStyleSheet("background:transparent;")
        self.radio_model_other.setStyleSheet("background:transparent;")
        self.radio_model_jev.setToolTip(
            "固定使用 typesafe/jev-1.13（OpenRouter Decisions API 上的 Jev 决策模型）。"
        )
        self.radio_model_other.setToolTip(
            "启用下方下拉：点「检测」拉取列表，或手填其它模型名。"
        )
        self._model_mode_group.addButton(self.radio_model_jev, 0)
        self._model_mode_group.addButton(self.radio_model_other, 1)
        self.radio_model_jev.setChecked(True)
        row_mode.addWidget(self.radio_model_jev)
        row_mode.addWidget(self.radio_model_other)
        row_mode.addStretch(1)
        box.addLayout(row_mode)

        row_model = QHBoxLayout()
        row_model.setSpacing(8)
        self.combo_model = QComboBox()
        self.combo_model.setObjectName("GameAssistModelCombo")
        self.combo_model.setEditable(True)
        self.combo_model.setInsertPolicy(QComboBox.NoInsert)
        self.combo_model.setMaxVisibleItems(12)
        self.combo_model.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLength)
        self.combo_model.setMinimumContentsLength(16)
        self.combo_model.setToolTip(
            "「其它模型」模式下可用。点「检测」拉取服务商列表，也可手填。"
        )
        self._set_model_text(default_jev_model())
        try:
            self.combo_model.lineEdit().editingFinished.connect(self._api_field_edited)
        except Exception:
            pass
        row_model.addWidget(self.combo_model, 1)
        self.btn_detect = apply_medium_button(QPushButton("检测"))
        self.btn_detect.setToolTip("用上方的 地址 / 密钥 连接 OpenRouter（或其它服务商），拉取模型列表")
        self.btn_detect.clicked.connect(self._detect_api)
        row_model.addWidget(self.btn_detect)
        box.addLayout(row_model)
        self.radio_model_jev.toggled.connect(self._on_model_mode_toggled)
        self._sync_model_combo_enabled()

        box.addWidget(self.lbl_api)
        return card

    def _build_interaction_card(self) -> QFrame:
        """交互记录：顶部运行状态（约前 3 行视觉），其下可滚动 API 结果历史。"""
        card = make_card("CardGameAssistFlow")
        self._func_cards.append(card)
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setSpacing(4)
        self._theme_titles.append(install_card_title(card, box, "交互记录"))

        # 第一排：四步胶囊一排（状态色圆点 + 短名；悬停看完整步骤与状态）
        self.steps_bar = QWidget()
        self.steps_bar.setObjectName("GameAssistFlowBar")
        self.steps_bar.setAttribute(Qt.WA_StyledBackground, True)
        self.steps_bar.setStyleSheet("#GameAssistFlowBar{background:transparent;border:none;}")
        bar = QHBoxLayout(self.steps_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(10)
        self._step_rows = {}
        for key, name in _INTERACTION_STEPS:
            short = name.split("·", 1)[0].strip() or name
            chip = _FlowStepChip(short, name)
            self._step_rows[key] = chip
            bar.addWidget(chip)
        bar.addStretch(1)
        box.addWidget(self.steps_bar)

        # 第二排：当前状态提示（工作中黄色 + 慢节奏明暗闪动）——固定在卡顶，不进日志
        self.lbl_flow_status = QLabel("等待操作")
        self.lbl_flow_status.setObjectName("GameAssistFlowStatus")
        self.lbl_flow_status.setWordWrap(True)
        self.lbl_flow_status.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.lbl_flow_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.lbl_flow_status.setMinimumHeight(36)
        self.lbl_flow_status.setMaximumHeight(54)
        self._flow_status_effect = QGraphicsOpacityEffect(self.lbl_flow_status)
        self.lbl_flow_status.setGraphicsEffect(self._flow_status_effect)
        self._flow_status_anim = QPropertyAnimation(self._flow_status_effect, b"opacity", self)
        self._flow_status_anim.setDuration(1600)
        self._flow_status_anim.setKeyValues([
            (0.0, 0.45),
            (0.5, 1.0),
            (1.0, 0.45),
        ])
        self._flow_status_anim.setEasingCurve(QEasingCurve.InOutSine)
        self._flow_status_anim.setLoopCount(-1)
        self._style_flow_status()
        box.addWidget(self.lbl_flow_status, 0)

        # 其下：API 收到内容的可滚动历史（最新在底部，自动滚到底）
        self.edit_interact_log = QPlainTextEdit()
        self.edit_interact_log.setObjectName("GameAssistInteractLog")
        self.edit_interact_log.setReadOnly(True)
        self.edit_interact_log.setTabChangesFocus(True)
        self.edit_interact_log.setPlaceholderText("API 返回的内容会按条记录在这里")
        # 条数上限由 _LOG_RECORD_CAP 管；这里只作文档块安全网
        self.edit_interact_log.setMaximumBlockCount(8000)
        self.edit_interact_log.setFocusPolicy(Qt.NoFocus)
        self.edit_interact_log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._style_interact_log()
        box.addWidget(self.edit_interact_log, 1)
        return card

    def _style_flow_status(self):
        lbl = getattr(self, "lbl_flow_status", None)
        if lbl is None:
            return
        busy = self._state == "busy"
        color = "#f59e0b" if busy else tk("text_mut")
        lbl.setStyleSheet(
            f"color:{color}; font-size:13px; font-weight:600;"
            f"background:transparent; border:none;"
        )

    def _reset_flow(self):
        for row in self._step_rows.values():
            row.set_state(_FLOW_STATE_IDLE)
        self._flow_active = None
        self._flow_hint("")

    def _flow_step(self, key: str, state: str, text: str = ""):
        row = self._step_rows.get(key)
        if row is None:
            return
        if state == _FLOW_STATE_BUSY:
            self._flow_active = key
        row.set_state(state, text)

    def _flow_hint(self, msg: str):
        lbl = getattr(self, "lbl_flow_status", None)
        if lbl is None:
            return
        lbl.setText((msg or "").strip() or "等待操作")
        self._style_flow_status()
        anim = getattr(self, "_flow_status_anim", None)
        if anim is None:
            return
        if self._state == "busy":
            if anim.state() != QAbstractAnimation.Running:
                anim.start()
        else:
            anim.stop()
            try:
                self._flow_status_effect.setOpacity(1.0)
            except Exception:
                pass

    def _build_recognition_card(self) -> QFrame:
        """文本：系统剪贴板里新复制的文字会自动填入，可再改，点「开工」作答。"""
        card = make_card("CardGameAssistResult")
        self._func_cards.append(card)
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setSpacing(6)
        self._theme_titles.append(install_card_title(card, box, "文本"))

        self.edit_result = QPlainTextEdit()
        self.edit_result.setObjectName("GameAssistResultEdit")
        self.edit_result.setPlaceholderText("在此填写要发送的内容（剪贴板新文字仍会自动填入）")
        self.edit_result.setTabChangesFocus(True)
        self.edit_result.setMaximumBlockCount(400)
        self._style_result_edit()
        box.addWidget(self.edit_result, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addStretch(1)
        self.btn_work = apply_mini_button(QPushButton("开工"))
        self.btn_work.setToolTip("开工（发送文本区内容到已配置的 API）")
        self.btn_work.clicked.connect(self._start_ask)
        row.addWidget(self.btn_work)
        box.addLayout(row)
        return card

    def _style_result_edit(self):
        edit = getattr(self, "edit_result", None)
        if edit is None:
            return
        edit.setStyleSheet(
            f"QPlainTextEdit#GameAssistResultEdit{{"
            f"background:transparent;color:{tk('text')};"
            f"border:none;padding:0;font-size:15px;}}"
        )


    def _style_interact_log(self):
        """底色透明；正文颜色由 _render_interact_log 的 QTextCharFormat 控制。"""
        edit = getattr(self, "edit_interact_log", None)
        if edit is None:
            return
        # 不在 QSS 里写 color：否则会盖住 QTextCharFormat 的亮/暗着色
        edit.setStyleSheet(
            "QPlainTextEdit#GameAssistInteractLog{"
            "background:transparent;border:none;padding:0;font-size:13px;}"
        )
        self._render_interact_log(scroll=False)

    def _interact_log_colors(self):
        """最新亮 / 历史暗。跟主题走（与 tk(text)/弱化色对比一致）。"""
        try:
            dark = bool(theme.is_dark)
        except Exception:
            dark = True
        if dark:
            # 暗色：最新近白、旧记录中灰（比 text_mut 再暗一点，对比更明显）
            return "#F5F5F5", "#8A8F98"
        # 浅色：最新近黑、旧记录浅灰
        return "#111111", "#9AA0A6"

    def _render_interact_log(self, *, scroll: bool = True):
        """按 _log_records 重绘：旧条暗色，最新一条亮色（可略加粗）。"""
        edit = getattr(self, "edit_interact_log", None)
        if edit is None:
            return
        records = list(getattr(self, "_log_records", None) or [])
        bright, dim = self._interact_log_colors()

        fmt_dim = QTextCharFormat()
        fmt_dim.setForeground(QColor(dim))
        fmt_dim.setFontWeight(QFont.Normal)

        fmt_bright = QTextCharFormat()
        fmt_bright.setForeground(QColor(bright))
        fmt_bright.setFontWeight(QFont.DemiBold)

        # 整页重写，避免只改最后一块时边界难对齐
        edit.clear()
        if not records:
            if scroll:
                try:
                    sb = edit.verticalScrollBar()
                    sb.setValue(sb.maximum())
                except Exception:
                    pass
            return

        cursor = QTextCursor(edit.document())
        cursor.beginEditBlock()
        try:
            last = len(records) - 1
            for i, rec in enumerate(records):
                if i > 0:
                    # 条与条之间空一行（用暗色，避免亮色空行闪）
                    cursor.insertText("\n\n", fmt_dim)
                fmt = fmt_bright if i == last else fmt_dim
                cursor.insertText(rec, fmt)
        finally:
            cursor.endEditBlock()

        if scroll:
            try:
                sb = edit.verticalScrollBar()
                sb.setValue(sb.maximum())
            except Exception:
                pass

    def _style_status(self, lbl: QLabel, text: str, ok: bool):
        color = "#16a34a" if ok else tk("text_mut")
        lbl.setText(text)
        lbl.setStyleSheet(
            f"color:{color}; font-size:12px; background:transparent; border:none;"
        )

    def _append_interact_record(self, body: str):
        """往交互记录追加一条：入列表后整表重绘（最新亮、旧条暗）。"""
        if getattr(self, "edit_interact_log", None) is None:
            return
        text = (body or "").strip()
        if not text:
            return
        # 二次防守：展示前再压一遍相邻重复行
        cleaned_lines = []
        prev = None
        for ln in text.splitlines():
            s = ln.rstrip()
            key = "".join(s.split()).lower()
            if key and key == prev:
                continue
            cleaned_lines.append(s)
            prev = key if key else prev
        text = "\n".join(cleaned_lines).strip()
        if not text:
            return
        ts = time.strftime("%H:%M:%S")
        block = f"[{ts}] {text}"
        recs = getattr(self, "_log_records", None)
        if recs is None:
            self._log_records = []
            recs = self._log_records
        recs.append(block)
        # 超限丢掉最旧的
        if len(recs) > _LOG_RECORD_CAP:
            del recs[:-_LOG_RECORD_CAP]
        self._render_interact_log(scroll=True)

    def _api_preset_index(self, base: str) -> int:
        """找与某地址匹配的预设序号；找不到返回 -1。"""
        try:
            b = normalize_api_base(base or "")
        except Exception:
            return -1
        for i, (_n, url, _h) in enumerate(_API_PRESETS):
            try:
                if normalize_api_base(url) == b:
                    return i
            except Exception:
                continue
        return -1

    def _api_url_for(self, raw: str) -> str:
        """当前文本是中文预设名（下拉选中后尚未落成地址）时，换成对应地址。"""
        raw = (raw or "").strip()
        for _n, url, _h in _API_PRESETS:
            if raw == _n:
                return url
        return raw

    def _clear_model_list(self):
        cb = self.combo_model
        try:
            cb.blockSignals(True)
            cb.clear()
            cb.blockSignals(False)
        except Exception:
            pass

    def _set_model_text(self, text: str):
        text = (text or "").strip()
        try:
            self.combo_model.blockSignals(True)
            self.combo_model.setEditText(text)
            self.combo_model.blockSignals(False)
        except Exception:
            pass

    def _on_api_preset(self, index: int):
        """从下拉选中文服务名：自动填地址；模型若为空、或还是上一家服务的推荐模型，
        换成当前服务的推荐模型（用户手填的自定义模型保留）。"""
        try:
            _name, url, hint = _API_PRESETS[int(index)]
        except Exception:
            return
        self.combo_api_base.setEditText(normalize_api_base(url))
        prev = self.combo_model.currentText().strip()
        if not prev or prev in _API_PRESET_MODELS:
            keep = hint
        else:
            keep = prev
        self._clear_model_list()
        if keep:
            self._set_model_text(keep)
        self._dirty()

    def _api_field_edited(self):
        self._dirty()

    def _on_base_or_key_edited(self, *_):
        """改了地址 / 密钥后，清掉上一家服务拉来的模型列表并提示重新检测。"""
        if self._loading:
            return
        self._clear_model_list()
        self._invalidate_api_ok()

    def _invalidate_api_ok(self, *_):
        if self._loading or not self._api_ok:
            return
        self._api_ok = False
        try:
            msg = "配置已改动，请重新点「检测」。"
            self._style_status(self.lbl_api, msg, False)
            self._flow_hint(msg)
        except Exception:
            pass

    def _model_mode(self) -> str:
        """当前模型选择：「jev」固定 typesafe/jev-1.13；「other」用下拉/手填。"""
        try:
            if self.radio_model_other.isChecked():
                return "other"
        except Exception:
            pass
        return "jev"

    def _set_model_mode(self, mode: str):
        """应用 model_mode 偏好；默认 jev。"""
        mode = (mode or "").strip().lower()
        try:
            if mode == "other":
                self.radio_model_other.setChecked(True)
            else:
                self.radio_model_jev.setChecked(True)
        except Exception:
            pass
        self._sync_model_combo_enabled()

    def _sync_model_combo_enabled(self):
        """其它模型模式才启用下拉；JEV 模式灰显。检测按钮始终可用。"""
        other = self._model_mode() == "other"
        try:
            self.combo_model.setEnabled(other)
        except Exception:
            pass
        try:
            le = self.combo_model.lineEdit()
            if le is not None:
                le.setEnabled(other)
        except Exception:
            pass

    def _on_model_mode_toggled(self, _checked: bool = False):
        self._sync_model_combo_enabled()
        if self._loading:
            return
        self._dirty()

    def _api_config(self) -> tuple:
        base = normalize_api_base(self._api_url_for(self.combo_api_base.currentText()))
        key = (self.edit_api_key.text() or "").strip()
        if self._model_mode() == "jev":
            model = default_jev_model()
        else:
            model = self.combo_model.currentText().strip()
        return base, key, model

    def _detect_api(self):
        if self._state == "busy":
            self._notify("info", "上一轮还在查，稍后再检测。")
            return
        w = self._detect_worker
        if w is not None:
            try:
                if w.isRunning():
                    return
            except RuntimeError:
                self._detect_worker = None
        base, key, _model = self._api_config()
        self.combo_api_base.setEditText(base)
        self.btn_detect.setEnabled(False)
        self._style_status(self.lbl_api, "正在连接并拉取模型列表…", False)
        self._flow_hint("正在连接并拉取模型列表…")
        worker = DetectWorker(base, key, parent=self)
        worker.done.connect(self._on_detect_done)
        worker.fail.connect(self._on_detect_fail)
        self._detect_worker = worker
        worker.start()

    def _on_detect_done(self, names):
        try:
            self.btn_detect.setEnabled(True)
        except Exception:
            pass
        names = [str(n) for n in (names or []) if str(n or "").strip()]
        prev = self.combo_model.currentText().strip()
        self._clear_model_list()
        jev = default_jev_model()
        base, _key, _m = self._api_config()
        # OpenRouter 的 /models 常不列出 Jev，手动补进 typesafe/jev-1.13
        try:
            from utils.gamebrain import is_openrouter_base
            if is_openrouter_base(base) and jev not in names:
                names = [jev] + names
        except Exception:
            if jev not in names:
                names = [jev] + list(names)
        if not names:
            self._api_ok = True
            if not prev:
                self._set_model_text(jev)
            else:
                self._set_model_text(prev)
            msg = "已连接，但服务商没有返回模型列表。已保留当前模型名。"
            self._style_status(self.lbl_api, msg, False)
            self._flow_hint(msg)
            self._dirty()
            return
        cb = self.combo_model
        try:
            cb.blockSignals(True)
            for n in names:
                cb.addItem(n)
            if prev in names:
                want = prev
            elif jev in names:
                want = jev
            else:
                want = names[0]
            i = cb.findText(want)
            if i >= 0:
                cb.setCurrentIndex(i)
            else:
                self._set_model_text(want)
            cb.blockSignals(False)
        except Exception:
            pass
        self._api_ok = True
        extra = "（已补入 typesafe/jev-1.13）" if jev in names else ""
        msg = f"已连接 · 拉取到 {len(names)} 个模型{extra}"
        self._style_status(self.lbl_api, msg, True)
        self._flow_hint(msg)
        self._dirty()

    def _on_detect_fail(self, msg: str):
        try:
            self.btn_detect.setEnabled(True)
        except Exception:
            pass
        self._api_ok = False
        detail = str(msg or "连接失败")
        self._style_status(self.lbl_api, f"未连接：{detail}", False)
        self._notify("warn", f"未连接：{detail}")

    def _stop_detect_worker(self):
        w = self._detect_worker
        self._detect_worker = None
        if w is None:
            return
        try:
            w.done.disconnect(self._on_detect_done)
            w.fail.disconnect(self._on_detect_fail)
        except Exception:
            pass
        stop_qthread(w, name="gamebrain_detect")
        try:
            self.btn_detect.setEnabled(True)
        except Exception:
            pass

    def on_enter(self):
        if getattr(self, "_clipboard_capture_enabled", False):
            self._on_clipboard_settled()

    def set_clipboard_capture_enabled(self, enabled: bool, *, sync_now: bool = True):
        """侧栏剪贴板归属：仅归属本页时消费系统剪贴板变化。"""
        enabled = bool(enabled)
        prev = bool(getattr(self, "_clipboard_capture_enabled", False))
        self._clipboard_capture_enabled = enabled
        if enabled and (not prev) and sync_now:
            QTimer.singleShot(0, self._on_clipboard_settled)
        if not enabled:
            timer = getattr(self, "_clip_timer", None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    pass

    def _bind_clipboard_watch(self):
        if self._clip_watch_connected:
            return
        try:
            cb = QApplication.clipboard()
            if cb is None:
                return
            cb.dataChanged.connect(self._on_clipboard_changed)
            self._clip_watch_connected = True
        except Exception:
            log.exception("游戏大脑接入剪贴板监听失败")
            return
        self._clip_timer = QTimer(self)
        self._clip_timer.setSingleShot(True)
        self._clip_timer.setInterval(80)
        self._clip_timer.timeout.connect(self._on_clipboard_settled)
        # 不在绑定时 sync；等归属开启

    def _on_clipboard_changed(self):
        if not getattr(self, "_clipboard_capture_enabled", False):
            return
        if int(getattr(self, "_clip_ignore", 0) or 0) > 0:
            return
        timer = getattr(self, "_clip_timer", None)
        if timer is not None:
            timer.start()

    def _on_clipboard_settled(self):
        if not getattr(self, "_clipboard_capture_enabled", False):
            return
        if int(getattr(self, "_clip_ignore", 0) or 0) > 0:
            return
        text = ""
        try:
            cb = QApplication.clipboard()
            if cb is not None:
                text = cb.text() or ""
        except Exception:
            return
        text = text.replace("\x00", "")
        if not text.strip():
            return
        edit = getattr(self, "edit_result", None)
        if edit is None:
            return
        if text == edit.toPlainText():
            return
        edit.setPlainText(text)

    def _unbind_clipboard_watch(self):
        if not self._clip_watch_connected:
            return
        try:
            cb = QApplication.clipboard()
            if cb is not None:
                cb.dataChanged.disconnect(self._on_clipboard_changed)
        except Exception:
            pass
        self._clip_watch_connected = False
        timer = getattr(self, "_clip_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass


    def _notify(self, kind: str, msg: str):
        """报错/信息只进「交互记录」状态行（lbl_flow_status），不弹 QMessageBox / 不抢焦点。

        kind 仅作语义标记（info/warn）；展示统一走 _flow_hint。
        """
        text = str(msg or "").strip()
        if text:
            self._flow_hint(text)

    def _start_ask(self):
        """开工：以当前「文本」区内容调配置好的 API，响应追加进「交互记录」日志。

        文案来源：edit_result（用户可见的文本输入区），不是剪贴板直送。
        按钮入口；全程不 activateWindow / raise_ / grabKeyboard。
        本页开工只走 API/JEV。
        """
        if self._state == "busy":
            self._notify("info", "上一轮还在查，稍等。")
            return
        query = (self.edit_result.toPlainText() or "").strip()
        if not query:
            self._notify("warn", "文本区是空的。请在上方填写要发送的内容。")
            return
        self._run_ask(query)

    def _run_ask(self, query: str):
        base, key, model = self._api_config()
        self.combo_api_base.setEditText(base)
        if not model:
            self._state = "idle"
            self._notify(
                "warn",
                "还没有模型。默认请填 typesafe/jev-1.13，或点「检测」从 OpenRouter 拉取列表。",
            )
            return
        if not key and is_openrouter_base(base):
            self._state = "idle"
            self._notify(
                "warn",
                "还没有 API Key。请在上方「API Key」里填写 OpenRouter 密钥，"
                "或设置环境变量 OPENROUTER_API_KEY 后重启程序。",
            )
            return
        self._stop_worker()
        self._state = "busy"
        w = AskWorker(base, key, model, query, parent=self)
        w.status.connect(self._on_ask_status)
        w.step.connect(self._on_ask_step)
        w.done.connect(self._on_ask_done)
        w.fail.connect(self._on_ask_fail)
        self._worker = w
        w.start()

    def _on_ask_status(self, msg: str):
        self._flow_hint(msg or "")

    def _on_ask_step(self, key: str, state: str):
        self._flow_step(key, state)

    def _on_ask_done(self, answers):
        self._state = "idle"
        self._flow_step("answer", _FLOW_STATE_OK)
        self._flow_hint("完成")
        self._answers = list(answers or [])
        for rec in self._answers:
            body = (rec.get("answer") or "").strip()
            if not body:
                body = (rec.get("title") or "").strip()
            self._append_interact_record(body)

    def _on_ask_fail(self, msg: str):
        self._state = "idle"
        key = getattr(self, "_flow_active", None)
        if key not in self._step_rows:
            key = "answer"
        self._flow_step(key, _FLOW_STATE_FAIL)
        self._notify("warn", str(msg or "查询失败"))

    def _stop_worker(self):
        w = self._worker
        self._worker = None
        if w is None:
            return
        try:
            w.status.disconnect(self._on_ask_status)
            w.step.disconnect(self._on_ask_step)
            w.done.disconnect(self._on_ask_done)
            w.fail.disconnect(self._on_ask_fail)
        except Exception:
            pass
        stop_qthread(w, name="gamebrain")

    # ── 偏好 / 生命周期 ─────────────────────────────────────
    def export_settings(self) -> dict:
        base, key, model = self._api_config()
        mode = self._model_mode()
        # jev 模式强制落盘 typesafe/jev-1.13；其它模式记下拉/手填，并另存 other_model 便于切回
        other_model = self.combo_model.currentText().strip()
        return {
            "base_url": base,
            "api_key": key,
            "model": model,
            "model_mode": mode,
            "other_model": other_model,
        }

    def apply_settings(self, data: dict):
        self._loading = True
        try:
            data = data or {}
            raw_base = str(data.get("base_url") or "").strip()
            # 本页刚从游戏助手复制时默认还是 DeepSeek；空或旧默认都改到 OpenRouter
            if (not raw_base) or ("deepseek.com" in raw_base.lower()):
                base = default_api_base()
            else:
                base = normalize_api_base(raw_base)
            pi = self._api_preset_index(base)
            if pi >= 0:
                self.combo_api_base.setCurrentIndex(pi)
            else:
                self.combo_api_base.setCurrentIndex(-1)
            self.combo_api_base.setEditText(base)
            key = str(data.get("api_key") or "").strip() or default_api_key()
            self.edit_api_key.setText(key)
            self._clear_model_list()
            model = str(data.get("model") or "").strip() or default_jev_model()
            other_model = str(data.get("other_model") or "").strip()
            raw_mode = str(data.get("model_mode") or "").strip().lower()
            # legacy typesafe/jev → JEV 模式 + 钉到 typesafe/jev-1.13
            if model.lower() == "typesafe/jev":
                model = default_jev_model()
                if raw_mode not in ("jev", "other"):
                    raw_mode = "jev"
            if raw_mode in ("jev", "other"):
                mode = raw_mode
            else:
                # 旧偏好无 model_mode：已是/空则默认 JEV，否则视为其它模型
                mode = "jev" if (not model or is_jev_model(model)) else "other"
            if mode == "other":
                self._set_model_text(other_model or model or default_jev_model())
            else:
                # JEV 模式：下拉保留其它模型候选（便于切回），API 仍固定 typesafe/jev-1.13
                keep = other_model or (
                    model if (model and not is_jev_model(model)) else ""
                )
                self._set_model_text(keep or default_jev_model())
            self._set_model_mode(mode)
            # 知识库 sources/groups 不再由本页加载或保存
        finally:
            self._loading = False

    def ingest_external_text(self, text: str) -> bool:
        """主窗 F7 静默复制：把选区文本写入结果/文本区（与剪贴板落地同路）。"""
        text = (text or "").replace(chr(0), "")
        if not str(text).strip():
            self._notify("warn", "没有选中文本")
            return False
        edit = getattr(self, "edit_result", None)
        if edit is None:
            return False
        try:
            if text == edit.toPlainText():
                self._flow_hint("选区与文本区相同")
                return True
            edit.setPlainText(text)
            self._flow_hint("已填入选区")
            return True
        except Exception:
            log.exception("游戏大脑填入选区失败")
            self._notify("warn", "填入选区失败")
            return False

    def begin_clip_ignore(self):
        self._clip_ignore = int(getattr(self, "_clip_ignore", 0) or 0) + 1

    def end_clip_ignore(self):
        self._clip_ignore = max(0, int(getattr(self, "_clip_ignore", 0) or 0) - 1)

    def hotkey_inventory(self):
        # F7 已改由主窗「静默复制」登记
        return []

    def rebind_hotkeys(self):
        return []

    def shutdown(self):
        self._unbind_clipboard_watch()
        self._stop_detect_worker()
        self._stop_worker()
        self._state = "idle"
