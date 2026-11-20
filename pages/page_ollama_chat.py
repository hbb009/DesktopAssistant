# pages/page_ollama_chat.py
# 「本地对话」页面：配置、工作目录、Agent。
#   · 服务地址 / 上次模型写入 userdata/user.txt（ollama_chat 段）
#   · 普通「对话」页已去掉，聊天只走 Agent
#   · 旧的联网 / 服从开关不再显示，已保存的值仍写回 user.txt

from __future__ import annotations

import base64
import html
import os
import uuid

from PyQt5.QtCore import QEvent, QSize, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import (
    QColor, QCursor, QDesktopServices, QImage, QPainter, QPen,
    QTextCharFormat, QTextCursor, QTextTable,
)
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QComboBox, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSpinBox, QTableWidget,
    QTableWidgetItem, QTabWidget, QTextBrowser, QTextEdit, QToolTip,
    QVBoxLayout, QWidget,
)

from styles.style_all import (
    theme, tk, fmt, install_card_title, make_card, apply_transparent_surface,
    CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP,
    apply_primary_button, apply_medium_button, apply_std_button,
)
from utils.logger import get_logger
from utils.ollama_chat import (
    DEFAULT_BASE, OllamaError, list_model_details, normalize_base,
    stream_chat, stream_openai_chat,
    DEFAULT_NUM_CTX, MIN_NUM_CTX, MAX_NUM_CTX, clamp_num_ctx, estimate_tokens, fit_history,
)
from utils.ollama_dirs import clear_partial_reads
from utils.ollama_unload import reload_model
from utils.ollama_skills import compose_skills, scan_skills, skills_dir
from utils.qthread_util import stop_qthread

log = get_logger(__name__)


class _ModelsWorker(QThread):
    ok = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, base: str, parent=None):
        super().__init__(parent)
        self.base = base

    def run(self):
        try:
            self.ok.emit(list_model_details(self.base))
        except Exception as e:
            self.failed.emit(str(e))


class _ReloadWorker(QThread):
    """新对话：等上一轮回答停稳，再卸载并重新加载同一个模型。只走 Ollama HTTP，不杀进程。"""
    done = pyqtSignal(dict)

    def __init__(self, base: str, model: str, wait_for=None, parent=None):
        super().__init__(parent)
        self.base, self.model = base, model
        self.wait_for = list(wait_for or [])

    def run(self):
        for w in self.wait_for:
            try:
                w.wait(20000)   # 上一轮已被叫停，这里等它把连接关掉
            except Exception:
                pass
        try:
            res = reload_model(self.base, self.model)
        except Exception as e:
            res = {"ok": False, "text": f"重新加载模型没有完成：{e}"}
        self.done.emit(res)


class _ApiModelsWorker(QThread):
    ok = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, base: str, api_key: str, parent=None):
        super().__init__(parent)
        self.base = base
        self.api_key = api_key

    def run(self):
        try:
            from utils.gameassist import list_api_models
            self.ok.emit(list_api_models(self.base, self.api_key))
        except Exception as e:
            self.failed.emit(str(e))


class _ChatWorker(QThread):
    token = pyqtSignal(str)
    failed = pyqtSignal(str)
    status = pyqtSignal(str)
    notice = pyqtSignal(str)    # 要长期留在对话里的提示

    def __init__(self, base: str, model: str, messages: list, web: bool = False,
                 dirs: bool = False, article_dir: str = "",
                 source: str = "ollama", api_base: str = "", api_key: str = "",
                 extra_system: str = "", num_ctx: int = 0, opts: dict = None,
                 obedient: bool = True, session_id: str = "", parent=None):
        super().__init__(parent)
        self.num_ctx = int(num_ctx or 0)
        self.opts = {}   # 旧参数，已不再使用：分段、思考、续写都由程序自动决定
        self.base, self.model, self.messages = base, model, messages
        self.web = bool(web)
        self.dirs = bool(dirs)
        self.article_dir = article_dir or ""
        self.source = source if source in ("ollama", "api") else "ollama"
        self.api_base = api_base or ""
        self.api_key = api_key or ""
        self.extra_system = extra_system or ""
        self.obedient = bool(obedient)
        self.session_id = session_id or ""
        self._stop = False
        self._resp = None

    def stop(self):
        self._stop = True
        r = self._resp
        if r is not None:
            try:
                r.close()  # 打断阻塞中的读
            except Exception:
                pass

    def _set_resp(self, r):
        self._resp = r
        if self._stop:
            self.stop()

    def run(self):
        try:
            self.status.emit("正在连接模型…")
            common = dict(
                should_stop=lambda: self._stop,
                on_response=self._set_resp,
                extra_system=self.extra_system,
            )
            if self.source == "api":
                pieces = stream_openai_chat(
                    self.api_base, self.api_key, self.model, self.messages,
                    obedient=self.obedient, **common,
                )
            else:
                pieces = stream_chat(
                    self.base, self.model, self.messages,
                    web=self.web, dirs=self.dirs, article_dir=self.article_dir,
                    on_status=self.status.emit,
                    on_think=self._on_think,
                    num_ctx=self.num_ctx,
                    on_notice=self.notice.emit,
                    obedient=self.obedient,
                    session_id=self.session_id,
                    **self.opts,
                    **common,
                )
            for piece in pieces:
                if self._stop:
                    break
                self.token.emit(piece)
        except Exception as e:
            if not self._stop:
                log.exception("ollama 对话失败")
                self.failed.emit(str(e))

    def _on_think(self, n: int):
        if not self._stop:
            self.status.emit(f"正在思考…已 {n} 字")


class _ChatView(QTextBrowser):
    """对话区。复制图标由 loadResource 提供，避免 setHtml 把图片丢掉。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._copy_icon = None

    def set_copy_icon(self, image):
        self._copy_icon = image

    def loadResource(self, typ, name):
        icon = self._copy_icon
        if icon is not None and str(name.toString()).endswith("ollama-copy.png"):
            return icon
        return super().loadResource(typ, name)


class _InputEdit(QTextEdit):
    """Enter 发送，Shift+Enter 换行。"""
    send_requested = pyqtSignal()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.send_requested.emit()
            return
        super().keyPressEvent(e)


_API_PRESETS = (
    ("DeepSeek 深度求索", "https://api.deepseek.com/v1", "deepseek-chat"),
    ("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini"),
    ("Kimi 月之暗面", "https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    ("智谱 GLM 清言", "https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
    ("通义千问（阿里百炼）", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    ("豆包（火山方舟）", "https://ark.cn-beijing.volces.com/api/v3", ""),
    ("自定义", "", ""),
)
_API_PRESET_MODELS = {m for _n, _u, m in _API_PRESETS if m}

# 图片走 Ollama 的 images 字段。文本嵌进消息。别的格式不装成附件。
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_TEXT_EXT = {
    ".txt", ".md", ".markdown", ".json", ".csv", ".tsv", ".log",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".htm", ".css", ".xml",
    ".yaml", ".yml", ".ini", ".toml", ".rst", ".tex", ".sh", ".bat",
    ".ps1", ".sql", ".c", ".cpp", ".h", ".hpp", ".java", ".go", ".rs",
    ".vue", ".cs", ".php", ".rb", ".lua", ".swift", ".kt",
}
_ATTACH_TEXT_CAP = 30000
_ATTACH_IMAGE_MAX = 6
_ATTACH_IMAGE_BYTES = 8 * 1024 * 1024
_ATTACH_READ_BYTES = 2 * 1024 * 1024


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _mime_has_files(md) -> bool:
    if md is None:
        return False
    try:
        if md.hasUrls():
            return True
    except Exception:
        return False
    try:
        return bool(md.hasFormat("text/uri-list"))
    except Exception:
        return False


def _paths_from_mime(md) -> list:
    if md is None:
        return []
    found = []
    try:
        urls = list(md.urls() or [])
    except Exception:
        urls = []
    for url in urls:
        try:
            if not url.isLocalFile():
                continue
            path = os.path.normpath(url.toLocalFile())
        except Exception:
            continue
        if path and not any(_same_path(path, old) for old in found):
            found.append(path)
    return found


def _decode_text(data: bytes):
    if not data or b"\x00" in data[:4096]:
        return None
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def _load_attachment(path: str, text_left: int, image_left: int):
    """返回 (image|text|skip, 内容, 文件名, 跳过原因)。"""
    name = os.path.basename(path) or path
    if os.path.isdir(path):
        return "skip", "", name, "是文件夹"
    if not os.path.isfile(path):
        return "skip", "", name, "找不到文件"
    ext = os.path.splitext(name)[1].lower()
    try:
        size = os.path.getsize(path)
    except OSError:
        return "skip", "", name, "读不了"
    if size <= 0:
        return "skip", "", name, "是空文件"
    if ext in _IMAGE_EXT:
        if image_left <= 0:
            return "skip", "", name, f"图片一次最多 {_ATTACH_IMAGE_MAX} 张"
        if size > _ATTACH_IMAGE_BYTES:
            return "skip", "", name, "超过 8MB"
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            return "skip", "", name, "读不了"
        return "image", base64.b64encode(raw).decode("ascii"), name, ""
    sniff = ext not in _TEXT_EXT
    if sniff and size > 512 * 1024:
        return "skip", "", name, "不是图片或文本"
    try:
        with open(path, "rb") as fh:
            raw = fh.read(_ATTACH_READ_BYTES + 1)
    except OSError:
        return "skip", "", name, "读不了"
    cut = len(raw) > _ATTACH_READ_BYTES
    if cut:
        raw = raw[:_ATTACH_READ_BYTES]
    text = _decode_text(raw)
    if text is None:
        return "skip", "", name, "不是图片或文本"
    if text_left <= 0:
        return "skip", "", name, "文本附件已到上限"
    if len(text) > text_left or cut:
        text = text[:text_left].rstrip()
        text += "\n…（文件较长，这里只附了前面一段）"
    return "text", text, name, ""


def _pack_attachments(paths) -> tuple:
    """读出这一轮要发的附件。返回 images、文本、文件名、跳过说明、成功路径。"""
    images, blocks, names, notes, used = [], [], [], [], []
    text_left = _ATTACH_TEXT_CAP
    image_left = _ATTACH_IMAGE_MAX
    seen = []
    for raw in paths or []:
        path = os.path.normpath(str(raw or "").strip())
        if not path or any(_same_path(path, old) for old in seen):
            continue
        seen.append(path)
        kind, payload, name, reason = _load_attachment(path, text_left, image_left)
        if kind == "image":
            images.append(payload)
            blocks.append(f"【图片附件 {name}】")
            names.append(name)
            used.append(path)
            image_left -= 1
        elif kind == "text":
            blocks.append(f"【附件 {name}】\n{payload}")
            names.append(name)
            used.append(path)
            text_left = max(0, text_left - len(payload))
        else:
            notes.append(f"{name}：{reason}")
    return images, "\n\n".join(blocks).strip(), names, notes, used


class _AttachTabs(QTabWidget):
    """对话右下角。拖到记录页或文件页都会收下本地文件。"""

    files_dropped = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._armed = set()

    def _paths(self, e) -> list:
        md = e.mimeData() if e is not None else None
        if not _mime_has_files(md):
            return []
        return _paths_from_mime(md)

    def dragEnterEvent(self, e):
        if self._paths(e):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if self._paths(e):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        paths = self._paths(e)
        if not paths:
            e.ignore()
            return
        e.acceptProposedAction()
        self.files_dropped.emit(paths)

    def arm(self, widget):
        if widget is None:
            return
        key = id(widget)
        if key in self._armed:
            return
        self._armed.add(key)
        try:
            widget.setAcceptDrops(True)
        except Exception:
            pass
        widget.installEventFilter(self)
        viewport = getattr(widget, "viewport", None)
        if callable(viewport):
            try:
                self.arm(viewport())
            except Exception:
                pass

    def eventFilter(self, obj, e):
        et = e.type()
        if et in (QEvent.DragEnter, QEvent.DragMove):
            if self._paths(e):
                e.acceptProposedAction()
                return True
        elif et == QEvent.Drop:
            paths = self._paths(e)
            if paths:
                e.acceptProposedAction()
                self.files_dropped.emit(paths)
                return True
        return super().eventFilter(obj, e)

# 文件夹页卡，对齐视频下载 VIDEO_TAB_QSS。
_OLLAMA_TAB_QSS = """
QWidget#TabOllamaConfig,
QWidget#TabOllamaChat,
QWidget#TabOllamaWork {{
    background: {panel_2};
    border: none;
}}
QTabWidget#OllamaTabWidget::pane {{
    border: 1px solid {border_3};
    border-top: 1px solid {border_3};
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    border-bottom-left-radius: 10px;
    border-bottom-right-radius: 10px;
    background: {panel_2};
    top: -1px;
    margin-top: 0px;
    padding: 10px;
}}
QTabWidget#OllamaTabWidget[firstSelected="true"]::pane {{
    border-top-left-radius: 0px;
}}
QTabWidget#OllamaTabWidget[firstSelected="false"]::pane {{
    border-top-left-radius: 10px;
}}
QTabWidget#OllamaTabWidget > QTabBar {{
    background: transparent;
    qproperty-drawBase: 0;
}}
QTabWidget#OllamaTabWidget > QTabBar::tab {{
    background: transparent;
    color: {text_faint};
    padding: 7px 16px 7px 16px;
    margin-right: 6px;
    margin-top: 3px;
    margin-bottom: 0px;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    border: 1px solid transparent;
    border-bottom: none;
    font-size: 14px;
    font-weight: 600;
}}
QTabWidget#OllamaTabWidget > QTabBar::tab:selected {{
    background: {panel_2};
    color: {text_strong};
    border: 1px solid {border_3};
    border-top: 2px solid {accent};
    border-bottom-color: {panel_2};
    margin-top: 0px;
    margin-bottom: -1px;
    padding-top: 6px;
    padding-bottom: 8px;
}}
QTabWidget#OllamaTabWidget[firstSelected="true"] > QTabBar::tab:selected {{
    border-top-left-radius: 10px;
    margin-left: 0px;
}}
QTabWidget#OllamaTabWidget > QTabBar::tab:hover:!selected {{
    background: {hover_veil};
    color: {text_dim};
    border: 1px solid {border_soft};
    border-bottom: none;
}}
QTabWidget#OllamaSideTabs::pane {{
    border: 1px solid {border_soft};
    border-radius: 8px;
    background: transparent;
    top: -1px;
    padding: 4px;
}}
QTabWidget#OllamaSideTabs > QTabBar {{
    background: transparent;
    qproperty-drawBase: 0;
}}
QTabWidget#OllamaSideTabs > QTabBar::tab {{
    background: transparent;
    color: {text_faint};
    padding: 2px 10px 3px 10px;
    margin-right: 4px;
    margin-top: 1px;
    border: 1px solid transparent;
    border-bottom: none;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    font-size: 12px;
    font-weight: 600;
}}
QTabWidget#OllamaSideTabs > QTabBar::tab:selected {{
    background: {panel_2};
    color: {text_strong};
    border: 1px solid {border_soft};
    border-top: 2px solid {accent};
    border-bottom-color: {panel_2};
    margin-bottom: -1px;
}}
QTabWidget#OllamaSideTabs > QTabBar::tab:hover:!selected {{
    color: {text_dim};
    background: {hover_veil};
}}
QLabel#OllamaAttachHint {{
    color: {text_faint};
    background: transparent;
}}
QScrollArea#OllamaStatusScroll,
QScrollArea#OllamaStatusScroll > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
QListWidget#OllamaAttachList {{
    background: transparent;
    border: none;
    color: {text};
    outline: none;
}}
QListWidget#OllamaAttachList::item {{
    background: transparent;
    border: none;
}}
QWidget#OllamaAttachRow {{
    background: transparent;
    border: none;
}}
QLabel#OllamaAttachName {{
    color: {text};
    background: transparent;
}}
QPushButton#OllamaAttachDel {{
    background: transparent;
    color: {text_faint};
    border: none;
    padding: 0;
    font-size: 14px;
}}
QPushButton#OllamaAttachDel:hover {{
    color: {err};
}}
"""


class PageOllamaChat(QWidget):
    def __init__(self):
        super().__init__()
        self._prefs_dirty_cb = None
        self._history = []          # 旧对话页留下的内存记录，页签已去掉
        self._web_pref = False
        self._obey_pref = True
        self._agent_session_id = uuid.uuid4().hex
        self._pending_model = ""    # user.txt 里记住的模型，列表回来后再选中
        self._models_worker = None
        self._chat_worker = None
        self._reload_worker = None
        self._retired_workers = []
        self._status_lines = []
        self._attach_paths = []
        self._error_text = ""
        self._restore_link_status = False
        self._applying = False
        self._filling_models = False
        self._filling_skills = False
        self._syncing_preset = False
        self._api_worker = None
        self._skill_rows = []
        self._enabled_skill_names = []

        apply_transparent_surface(self, "PageOllamaChat")
        self.setStyleSheet(fmt(_OLLAMA_TAB_QSS))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("OllamaTabWidget")
        self.tabs.setDocumentMode(False)
        try:
            self.tabs.tabBar().setDrawBase(False)
            self.tabs.tabBar().setExpanding(False)
            self.tabs.tabBar().setElideMode(Qt.ElideNone)
        except Exception:
            pass
        self.page_config = QWidget()
        self.page_config.setObjectName("TabOllamaConfig")
        self.page_work = QWidget()
        self.page_work.setObjectName("TabOllamaWork")
        self.tabs.addTab(self.page_config, "配置")
        self.tabs.addTab(self.page_work, "工作目录")
        self.tabs.currentChanged.connect(self._sync_tab_chrome)
        root.addWidget(self.tabs, 1)
        self._build_config_page()
        self._build_work_page()
        from Agent.agent_chat_panel import AgentChatPanel
        self.agent_panel = AgentChatPanel(self)
        self.tabs.addTab(self.agent_panel, "Agent")
        self.tabs.setCurrentWidget(self.agent_panel)
        self._sync_tab_chrome()

        # 流式刷新节流：token 先进缓冲，定时只改最后一条气泡。
        # 整页 setHtml 会把滚动位置打回顶部，生成时不能走那条路。
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(60)
        self._render_timer.timeout.connect(self._flush_render)
        self._render_dirty = False
        self._body_table = None
        self._painted = -1

        try:
            theme.changed.connect(self._render)
            theme.changed.connect(self._polish_tabs)
        except Exception:
            pass
        self._loaded_once = False
        self._render()

    def _polish_tabs(self, *_):
        self.setStyleSheet(fmt(_OLLAMA_TAB_QSS))
        self._style_model_table()
        self._sync_tab_chrome()
        self._sync_side_tabs()

    def _sync_tab_chrome(self, *_):
        idx = self.tabs.currentIndex() if self.tabs is not None else 0
        first = "true" if idx == 0 else "false"
        self.tabs.setProperty("firstSelected", first)
        st = self.tabs.style()
        if st is not None:
            st.unpolish(self.tabs)
            st.polish(self.tabs)
        bar = self.tabs.tabBar()
        if bar is not None:
            from PyQt5.QtGui import QColor
            sel, idle = QColor(tk("text_strong")), QColor(tk("text_faint"))
            for i in range(self.tabs.count()):
                bar.setTabTextColor(i, sel if i == idx else idle)
            try:
                bar.setDrawBase(False)
            except Exception:
                pass
        if self.tabs.currentWidget() is self.page_work and not self._applying:
            self._reload_skills()
            self._refresh_work_files()

    def _sync_side_tabs(self, *_):
        tabs = getattr(self, "side_tabs", None)
        if tabs is None:
            return
        st = tabs.style()
        if st is not None:
            st.unpolish(tabs)
            st.polish(tabs)
        bar = tabs.tabBar()
        if bar is None:
            return
        from PyQt5.QtGui import QColor
        sel, idle = QColor(tk("text_strong")), QColor(tk("text_faint"))
        idx = tabs.currentIndex()
        for i in range(tabs.count()):
            bar.setTabTextColor(i, sel if i == idx else idle)

    def _build_config_page(self):
        outer = QVBoxLayout(self.page_config)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        host = QWidget()
        host.setStyleSheet("background:transparent;")
        scroll.setWidget(host)
        outer.addWidget(scroll)
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        src = QHBoxLayout()
        src.setSpacing(16)
        src.addWidget(QLabel("当前使用"))
        self.rb_ollama = QRadioButton("本地 Ollama")
        self.rb_api = QRadioButton("API")
        self._src_group = QButtonGroup(self)
        self._src_group.addButton(self.rb_ollama, 0)
        self._src_group.addButton(self.rb_api, 1)
        self.rb_ollama.setChecked(True)
        self.rb_ollama.toggled.connect(lambda *_: self._notify_prefs())
        src.addWidget(self.rb_ollama)
        src.addWidget(self.rb_api)
        src.addStretch(1)
        lay.addLayout(src)

        self.card_api = make_card("CardOllamaApi")
        al = QVBoxLayout(self.card_api)
        al.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        al.setSpacing(8)
        install_card_title(self.card_api, al, "API")
        tip = QLabel("选一家服务后填写地址和密钥，点「检测」拉取模型。对话走这条 API 时，不能把文章写入工作目录。")
        tip.setWordWrap(True)
        al.addWidget(tip)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("服务"))
        self.cb_api_preset = QComboBox()
        for name, _url, _model in _API_PRESETS:
            self.cb_api_preset.addItem(name)
        self.cb_api_preset.currentIndexChanged.connect(self._on_api_preset)
        row.addWidget(self.cb_api_preset, 1)
        al.addLayout(row)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("地址"))
        self.ed_api_base = QLineEdit()
        self.ed_api_base.setPlaceholderText("https://api.example.com/v1")
        self.ed_api_base.editingFinished.connect(self._notify_prefs)
        row.addWidget(self.ed_api_base, 1)
        al.addLayout(row)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("密钥"))
        self.ed_api_key = QLineEdit()
        self.ed_api_key.setEchoMode(QLineEdit.Password)
        self.ed_api_key.setPlaceholderText("sk-…")
        self.ed_api_key.editingFinished.connect(self._notify_prefs)
        row.addWidget(self.ed_api_key, 1)
        al.addLayout(row)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("模型"))
        self.cb_api_model = QComboBox()
        self.cb_api_model.setEditable(True)
        self.cb_api_model.setMinimumWidth(180)
        self.cb_api_model.currentTextChanged.connect(lambda *_: self._notify_prefs())
        row.addWidget(self.cb_api_model, 1)
        self.btn_api_detect = apply_medium_button(QPushButton("检测"))
        self.btn_api_detect.clicked.connect(self._detect_api)
        row.addWidget(self.btn_api_detect)
        al.addLayout(row)
        self.lbl_api = QLabel("")
        self.lbl_api.setWordWrap(True)
        al.addWidget(self.lbl_api)
        lay.addWidget(self.card_api)

        self.card_ollama = make_card("CardOllamaLocal")
        ol = QVBoxLayout(self.card_ollama)
        ol.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        ol.setSpacing(8)
        install_card_title(self.card_ollama, ol, "本地 Ollama")
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("服务地址"))
        self.ed_base = QLineEdit(DEFAULT_BASE)
        self.ed_base.setPlaceholderText(DEFAULT_BASE)
        self.ed_base.editingFinished.connect(self._on_base_edited)
        row.addWidget(self.ed_base, 1)
        self.btn_refresh = apply_medium_button(QPushButton("刷新"))
        self.btn_refresh.clicked.connect(self.refresh_models)
        row.addWidget(self.btn_refresh)
        ol.addLayout(row)
        self.lbl_ollama_count = QLabel("共 0 个模型")
        ol.addWidget(self.lbl_ollama_count)
        self.table_models = QTableWidget(0, 5)
        self.table_models.setHorizontalHeaderLabels(["名称", "参数", "量化", "体积", "上下文"])
        self.table_models.verticalHeader().setVisible(False)
        self.table_models.setShowGrid(False)
        self.table_models.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_models.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_models.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table_models.setFocusPolicy(Qt.NoFocus)
        self.table_models.verticalHeader().setDefaultSectionSize(36)
        self.table_models.setMinimumHeight(160)
        hdr = self.table_models.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 5):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table_models.itemSelectionChanged.connect(self._on_model_row)
        ol.addWidget(self.table_models, 1)
        self.cb_model = QComboBox()
        self.cb_model.hide()
        self.cb_model.currentTextChanged.connect(self._on_model_changed)
        lay.addWidget(self.card_ollama, 1)
        self.cb_api_preset.blockSignals(True)
        self.cb_api_preset.setCurrentIndex(len(_API_PRESETS) - 1)
        self.cb_api_preset.blockSignals(False)
        self._style_model_table()

    def _style_model_table(self):
        table = getattr(self, "table_models", None)
        if table is None:
            return
        table.setStyleSheet(
            f"QTableWidget{{background:transparent;border:none;color:{tk('text')};}}"
            f"QTableWidget::item{{padding:4px 6px;border:none;}}"
            f"QTableWidget::item:selected{{background:{tk('sel_bg')};color:{tk('sel_text')};}}"
            f"QHeaderView::section{{background:transparent;color:{tk('text_faint')};"
            f"border:none;border-bottom:1px solid {tk('border_soft')};padding:4px 6px;}}"
        )

    def _build_chat_page(self):
        from ui_main import SideToggleSwitch
        box = QVBoxLayout(self.page_chat)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addStretch(1)
        # 上下文长度由程序按任务自动决定（utils.ollama_auto），界面上不再放数字框。
        bar.addWidget(QLabel("联网"), 0, Qt.AlignVCenter)
        self.sw_web = SideToggleSwitch("#22C55E")
        self.sw_web.setToolTip("开启后，模型可以搜索并打开网页。关闭后不能访问网络。")
        self.sw_web.clicked.connect(lambda *_: self._notify_prefs())
        bar.addWidget(self.sw_web, 0, Qt.AlignVCenter)
        bar.addWidget(QLabel("听话"), 0, Qt.AlignVCenter)
        self.sw_obey = SideToggleSwitch("#6366F1")
        self.sw_obey.setChecked(True)
        self.sw_obey.setToolTip(
            "开启后，程序先判断你要干什么，只给对应的工具；"
            "命令类任务关掉思考、降低随机性。听错了就关掉，恢复原来的全工具模式。"
        )
        self.sw_obey.clicked.connect(lambda *_: self._notify_prefs())
        bar.addWidget(self.sw_obey, 0, Qt.AlignVCenter)
        self.btn_new = apply_medium_button(QPushButton("新对话"))
        self.btn_new.setObjectName("OllamaBtnNew")
        self.btn_new.clicked.connect(self.new_conversation)
        self.btn_clear = apply_medium_button(QPushButton("清屏"))
        self.btn_clear.setObjectName("OllamaBtnClear")
        self.btn_clear.clicked.connect(self.clear_screen)
        self.btn_send = apply_primary_button(QPushButton("发送"))
        self.btn_send.clicked.connect(self.send)
        self.btn_stop = apply_medium_button(QPushButton("停止"))
        self.btn_stop.setObjectName("OllamaBtnStop")
        self.btn_stop.clicked.connect(self.stop_generation)
        self.btn_stop.setEnabled(False)
        self._fit_ollama_bar_buttons()
        bar.addWidget(self.btn_new, 0, Qt.AlignVCenter)
        bar.addWidget(self.btn_clear, 0, Qt.AlignVCenter)
        bar.addWidget(self.btn_send, 0, Qt.AlignVCenter)
        bar.addWidget(self.btn_stop, 0, Qt.AlignVCenter)
        box.addLayout(bar)

        self.view = _ChatView()
        self.view.setObjectName("OllamaChatView")
        self.view.setOpenLinks(False)
        self.view.setOpenExternalLinks(False)
        self.view.anchorClicked.connect(self._on_bubble_anchor)
        self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        box.addWidget(self.view, 1)
        # 贴底标记：跟着用户的滚动走；用户往上翻就不再强行拉到底。
        self._stick = True
        self._scroll_quiet = 0
        vbar = self.view.verticalScrollBar()
        vbar.valueChanged.connect(self._on_scroll_value)
        vbar.rangeChanged.connect(self._on_scroll_range)

        inp_row = QWidget()
        inp_row.setFixedHeight(144)
        lay = QHBoxLayout(inp_row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        left = QWidget()
        left.setMinimumWidth(0)
        left.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        left_lay = QHBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(8)
        self.inp = _InputEdit()
        self.inp.setPlaceholderText("输入消息，Enter 发送，Shift+Enter 换行")
        self.inp.setFixedHeight(144)
        self.inp.setMinimumWidth(0)
        self.inp.send_requested.connect(self.send)
        left_lay.addWidget(self.inp, 1)
        lay.addWidget(left, 3)
        self.side_tabs = _AttachTabs()
        self.side_tabs.setObjectName("OllamaSideTabs")
        self.side_tabs.setDocumentMode(False)
        self.side_tabs.setMinimumWidth(0)
        self.side_tabs.setMinimumHeight(0)
        self.side_tabs.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        try:
            self.side_tabs.tabBar().setDrawBase(False)
            self.side_tabs.tabBar().setExpanding(False)
            self.side_tabs.tabBar().setElideMode(Qt.ElideNone)
        except Exception:
            pass
        page_log = QWidget()
        page_log.setObjectName("OllamaSideLog")
        log_lay = QVBoxLayout(page_log)
        log_lay.setContentsMargins(0, 0, 0, 0)
        log_lay.setSpacing(0)
        self.status_scroll = QScrollArea()
        self.status_scroll.setObjectName("OllamaStatusScroll")
        self.status_scroll.setWidgetResizable(True)
        self.status_scroll.setFrameShape(QFrame.NoFrame)
        self.status_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.lbl_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_status.setMinimumWidth(0)
        self.status_scroll.setWidget(self.lbl_status)
        log_lay.addWidget(self.status_scroll, 1)
        page_files = QWidget()
        page_files.setObjectName("OllamaSideFiles")
        file_lay = QVBoxLayout(page_files)
        file_lay.setContentsMargins(0, 0, 0, 0)
        file_lay.setSpacing(0)
        self.attach_hint = QLabel("把文件拖到这里\n发送时附给本地模型")
        self.attach_hint.setObjectName("OllamaAttachHint")
        self.attach_hint.setAlignment(Qt.AlignCenter)
        self.attach_hint.setWordWrap(True)
        self.attach_list = QListWidget()
        self.attach_list.setObjectName("OllamaAttachList")
        self.attach_list.setFrameShape(QFrame.NoFrame)
        self.attach_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.attach_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.attach_list.setMinimumHeight(0)
        self.attach_list.setVisible(False)
        file_lay.addWidget(self.attach_hint, 1)
        file_lay.addWidget(self.attach_list, 1)
        self.side_tabs.addTab(page_log, "记录")
        self.side_tabs.addTab(page_files, "文件")
        self.side_tabs.files_dropped.connect(self._add_attach_paths)
        self.side_tabs.currentChanged.connect(self._sync_side_tabs)
        for host in (page_log, self.status_scroll, self.lbl_status, page_files, self.attach_hint, self.attach_list):
            self.side_tabs.arm(host)
        self._sync_side_tabs()
        lay.addWidget(self.side_tabs, 1)
        box.addWidget(inp_row)

    def _fit_ollama_bar_buttons(self):
        """清屏、停止与发送同宽同高。新对话多一个字，在这个宽度上再加一个字宽。"""
        send = getattr(self, "btn_send", None)
        btn_new = getattr(self, "btn_new", None)
        btn_clear = getattr(self, "btn_clear", None)
        btn_stop = getattr(self, "btn_stop", None)
        if send is None or btn_new is None or btn_clear is None or btn_stop is None:
            return
        for btn in (btn_new, btn_clear, send, btn_stop):
            st = btn.style()
            if st is not None:
                st.unpolish(btn)
                st.polish(btn)
        width = send.sizeHint().width()
        if width < 8:
            return
        extra = send.fontMetrics().horizontalAdvance("字")
        if extra < 1:
            extra = 12
        btn_clear.setFixedWidth(width)
        btn_stop.setFixedWidth(width)
        btn_new.setFixedWidth(width + extra)

    def _build_work_page(self):
        row = QHBoxLayout(self.page_work)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        left = make_card("CardOllamaWork")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        ll.setSpacing(8)
        install_card_title(left, ll, "工作文件夹")
        head = QHBoxLayout()
        head.setSpacing(8)
        self.ed_article = QLineEdit()
        self.ed_article.setReadOnly(True)
        self.ed_article.setPlaceholderText("还没有选择工作文件夹")
        head.addWidget(self.ed_article, 1)
        self.btn_article_dir = apply_medium_button(QPushButton("选择"))
        self.btn_article_dir.clicked.connect(self._pick_article_dir)
        head.addWidget(self.btn_article_dir)
        btn_files = apply_medium_button(QPushButton("刷新"))
        btn_files.clicked.connect(self._refresh_work_files)
        head.addWidget(btn_files)
        ll.addLayout(head)
        self.file_list = QListWidget()
        self.file_list.itemDoubleClicked.connect(self._open_work_item)
        ll.addWidget(self.file_list, 1)
        row.addWidget(left, 7)

        right = make_card("CardOllamaSkills")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        rl.setSpacing(8)
        install_card_title(right, rl, "Skill")
        hint = QLabel("勾选后，对话会带上这些技能。把 .md 放进技能目录即可。")
        hint.setWordWrap(True)
        rl.addWidget(hint)
        self.skill_list = QListWidget()
        self.skill_list.itemChanged.connect(self._on_skill_check)
        rl.addWidget(self.skill_list, 1)
        btns = QHBoxLayout()
        btns.setSpacing(6)
        btn_open = apply_medium_button(QPushButton("打开目录"))
        btn_open.clicked.connect(self._open_skills_dir)
        btn_skill = apply_medium_button(QPushButton("刷新"))
        btn_skill.clicked.connect(self._reload_skills)
        btns.addWidget(btn_open)
        btns.addWidget(btn_skill)
        rl.addLayout(btns)
        row.addWidget(right, 3)

    # ── user.txt ───────────────────────────────────────────────
    def set_prefs_dirty_callback(self, cb):
        self._prefs_dirty_cb = cb

    def _notify_prefs(self):
        if (
            self._applying or self._filling_models or self._filling_skills
            or self._syncing_preset
        ):
            return
        fn = self._prefs_dirty_cb
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

    def _using_api(self) -> bool:
        return bool(self.rb_api.isChecked())

    def _article_dir(self) -> str:
        return self.ed_article.text().strip()

    def _save_enabled(self) -> bool:
        """选定工作文件夹后，本地模型才能把文章写进去。"""
        return (not self._using_api()) and bool(self._article_dir())

    def _model_name(self) -> str:
        if self._using_api():
            return self.cb_api_model.currentText().strip()
        return self.cb_model.currentText().strip() or self._pending_model

    def _link_status(self) -> str:
        if self._using_api():
            name = self._model_name()
            return f"当前使用 API：{name}。" if name else "当前使用 API。"
        n = self.cb_model.count()
        if n:
            return f"已连接，共 {n} 个模型。"
        return ""

    def _extra_system(self) -> str:
        chosen = []
        enabled = set(self._enabled_skill_names)
        for sk in self._skill_rows:
            if sk.get("name") in enabled:
                chosen.append(sk)
        return compose_skills(chosen)

    def _web_checked(self) -> bool:
        sw = getattr(self, "sw_web", None)
        if sw is not None:
            return bool(sw.isChecked())
        return bool(getattr(self, "_web_pref", False))

    def _obey_checked(self) -> bool:
        sw = getattr(self, "sw_obey", None)
        if sw is not None:
            return bool(sw.isChecked())
        return bool(getattr(self, "_obey_pref", True))

    def export_settings(self) -> dict:
        model = self.cb_model.currentText().strip() or self._pending_model
        skills = list(self._enabled_skill_names)
        return {
            "server": self._base(),
            "model": model,
            "web": self._web_checked(),
            "obedient": self._obey_checked(),
            "dirs": self._save_enabled(),
            "article_dir": self._article_dir(),
            "source": "api" if self._using_api() else "ollama",
            "api_base": self.ed_api_base.text().strip(),
            "api_key": self.ed_api_key.text(),
            "api_model": self.cb_api_model.currentText().strip(),
            "skills": skills,
        }

    def _num_ctx(self) -> int:
        """只用来裁剪过长的历史；真正发给 Ollama 的上下文长度由程序每轮自动决定。"""
        from utils.ollama_auto import AUTO_CEILING
        return AUTO_CEILING

    def apply_settings(self, d: dict):
        if not isinstance(d, dict):
            return
        self._applying = True
        try:
            srv = str(d.get("server") or "").strip()
            if srv:
                self.ed_base.setText(normalize_base(srv))
            self._pending_model = str(d.get("model") or "").strip()
            web = d.get("web")
            if isinstance(web, str):
                web = web.strip().lower() in ("1", "true", "yes", "on")
            self._web_pref = bool(web)
            sw_web = getattr(self, "sw_web", None)
            if sw_web is not None:
                sw_web.setChecked(bool(web))
            obedient = d.get("obedient")
            if obedient is None:
                obedient = True
            elif isinstance(obedient, str):
                obedient = obedient.strip().lower() in ("1", "true", "yes", "on")
            self._obey_pref = bool(obedient)
            sw_obey = getattr(self, "sw_obey", None)
            if sw_obey is not None:
                sw_obey.setChecked(bool(obedient))
            folder = str(d.get("article_dir") or "").strip()
            self.ed_article.setText(os.path.normpath(folder) if folder else "")
            source = str(d.get("source") or "ollama").strip().lower()
            self.rb_api.setChecked(source == "api")
            self.rb_ollama.setChecked(source != "api")
            api_base = str(d.get("api_base") or "").strip()
            if api_base:
                self.ed_api_base.setText(api_base)
            self.ed_api_key.setText(str(d.get("api_key") or ""))
            api_model = str(d.get("api_model") or "").strip()
            if api_model:
                self.cb_api_model.setCurrentText(api_model)
            self.cb_api_preset.blockSignals(True)
            self.cb_api_preset.setCurrentIndex(self._preset_index(self.ed_api_base.text()))
            self.cb_api_preset.blockSignals(False)
            # 旧设置里的 num_ctx / chunk_ratio / think_cap_ratio 等不再使用，读到也忽略。
            names = d.get("skills") or []
            self._enabled_skill_names = [str(n) for n in names if str(n).strip()] if isinstance(names, list) else []
            if self._pending_model and self.cb_model.findText(self._pending_model) >= 0:
                self.cb_model.setCurrentText(self._pending_model)
        finally:
            self._applying = False
        if self.tabs.currentWidget() is self.page_work:
            self._reload_skills()
            self._refresh_work_files()

    # ── 生命周期 ───────────────────────────────────────────────
    def on_enter(self):
        if not self._loaded_once or self.cb_model.count() == 0:
            self._loaded_once = True
            self.refresh_models()
        if self.tabs.currentWidget() is self.page_work:
            self._reload_skills()
            self._refresh_work_files()
        inp = getattr(self, "inp", None)
        if inp is not None and self.tabs.currentWidget() is getattr(self, "page_chat", None):
            inp.setFocus()

    def shutdown(self):
        if hasattr(self, 'agent_panel'):
            self.agent_panel.shutdown()
        try:
            self._abandon_worker()
            for w in list(getattr(self, "_retired_workers", [])):
                w.stop()
                stop_qthread(w)
            stop_qthread(self._models_worker)
            stop_qthread(getattr(self, "_api_worker", None))
            stop_qthread(getattr(self, "_reload_worker", None))
        except Exception:
            pass

    # ── 模型列表 ───────────────────────────────────────────────
    def _base(self) -> str:
        return normalize_base(self.ed_base.text())

    def _on_article_dir_edited(self):
        raw = self.ed_article.text().strip()
        if raw:
            self.ed_article.setText(os.path.normpath(raw))
        self._notify_prefs()

    def _pick_article_dir(self):
        from PyQt5.QtWidgets import QFileDialog
        start = self.ed_article.text().strip()
        if not os.path.isdir(start):
            start = os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, "选择文章保存目录", start)
        if not chosen:
            return
        self.ed_article.setText(os.path.normpath(chosen))
        self._refresh_work_files()
        self._notify_prefs()

    def _on_base_edited(self):
        nb = self._base()
        if self.ed_base.text() != nb:
            self.ed_base.setText(nb)
        self._notify_prefs()

    def _on_model_changed(self, name: str):
        if name:
            self._pending_model = name
            self._notify_prefs()

    def refresh_models(self):
        if self._models_worker is not None and self._models_worker.isRunning():
            return
        self.btn_refresh.setEnabled(False)
        self._set_status("正在读取模型列表…")
        w = _ModelsWorker(self._base(), self)
        w.ok.connect(self._on_models_ok)
        w.failed.connect(self._on_models_failed)
        w.finished.connect(lambda: self.btn_refresh.setEnabled(True))
        self._models_worker = w
        w.start()

    def _on_models_ok(self, rows: list):
        rows = [r for r in (rows or []) if isinstance(r, dict) and r.get("name")]
        names = [r["name"] for r in rows]
        want = self._pending_model or self.cb_model.currentText()
        self._filling_models = True
        self.cb_model.blockSignals(True)
        self.table_models.blockSignals(True)
        try:
            self.cb_model.clear()
            self.cb_model.addItems(names)
            self.table_models.setRowCount(len(rows))
            for r, row in enumerate(rows):
                for c, key in enumerate(("name", "params", "quant", "size", "ctx")):
                    item = QTableWidgetItem(str(row.get(key) or ""))
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    self.table_models.setItem(r, c, item)
            if want and want in names:
                self.cb_model.setCurrentText(want)
                self.table_models.selectRow(names.index(want))
            elif names:
                self.cb_model.setCurrentIndex(0)
                self.table_models.selectRow(0)
        finally:
            self.table_models.blockSignals(False)
            self.cb_model.blockSignals(False)
            self._filling_models = False
        self.lbl_ollama_count.setText(f"共 {len(names)} 个模型")
        if not names:
            self._set_status("Ollama 已连接，但本地还没有模型。请先在命令行执行 ollama pull 模型名 下载模型。", error=True)
            return
        if not (want and want in names):
            self._on_model_changed(self.cb_model.currentText())
        self._set_status(f"已连接，共 {len(names)} 个模型。")

    def _on_model_row(self):
        if getattr(self, "_filling_models", False):
            return
        selected = self.table_models.selectedItems()
        if not selected:
            return
        name = self.table_models.item(selected[0].row(), 0)
        if name is None:
            return
        text = name.text().strip()
        if text and text != self.cb_model.currentText():
            self.cb_model.setCurrentText(text)

    def _preset_index(self, base: str) -> int:
        b = (base or "").strip().rstrip("/").lower()
        if not b:
            return len(_API_PRESETS) - 1
        for i, (_name, url, _model) in enumerate(_API_PRESETS):
            if url and url.rstrip("/").lower() == b:
                return i
        return len(_API_PRESETS) - 1

    def _on_api_preset(self, index: int):
        if self._applying or self._syncing_preset:
            return
        try:
            _name, url, hint = _API_PRESETS[int(index)]
        except Exception:
            return
        if not url:
            self._notify_prefs()
            return
        self.ed_api_base.setText(url)
        prev = self.cb_api_model.currentText().strip()
        if not prev or prev in _API_PRESET_MODELS:
            self.cb_api_model.blockSignals(True)
            self.cb_api_model.clear()
            if hint:
                self.cb_api_model.addItem(hint)
                self.cb_api_model.setCurrentText(hint)
            self.cb_api_model.blockSignals(False)
        self.lbl_api.setText("")
        self._notify_prefs()

    def _detect_api(self):
        if self._api_worker is not None and self._api_worker.isRunning():
            return
        base = self.ed_api_base.text().strip()
        if not base:
            self._set_api_hint("先填写 API 地址。", error=True)
            return
        self.btn_api_detect.setEnabled(False)
        self._set_api_hint("正在检测…")
        w = _ApiModelsWorker(base, self.ed_api_key.text(), self)
        w.ok.connect(self._on_api_models_ok)
        w.failed.connect(self._on_api_models_failed)
        w.finished.connect(lambda: self.btn_api_detect.setEnabled(True))
        self._api_worker = w
        w.start()

    def _set_api_hint(self, text: str, error: bool = False):
        color = tk("err") if error else tk("text_mut")
        self.lbl_api.setStyleSheet(f"color:{color}; background:transparent;")
        self.lbl_api.setText(text)

    def _on_api_models_ok(self, names: list):
        names = [str(n).strip() for n in (names or []) if str(n).strip()]
        want = self.cb_api_model.currentText().strip()
        self.cb_api_model.blockSignals(True)
        try:
            self.cb_api_model.clear()
            if names:
                self.cb_api_model.addItems(names)
            if want and (not names or want in names):
                self.cb_api_model.setCurrentText(want)
            elif names:
                self.cb_api_model.setCurrentIndex(0)
        finally:
            self.cb_api_model.blockSignals(False)
        if names:
            self._set_api_hint(f"已连通，共 {len(names)} 个模型。")
        else:
            self._set_api_hint("已连通，但没有返回模型。可以自己填写模型名。")
        self._notify_prefs()

    def _on_api_models_failed(self, msg: str):
        self._set_api_hint(msg or "检测失败。", error=True)

    def _refresh_work_files(self):
        self.file_list.clear()
        folder = self._article_dir()
        if not folder:
            self._file_note("还没有选择工作文件夹。点「选择」指定一个目录。")
            return
        if not os.path.isdir(folder):
            self._file_note("这个文件夹不存在，或已经被移走。")
            return
        try:
            names = os.listdir(folder)
        except OSError as e:
            self._file_note(f"读不了这个文件夹：{e}")
            return
        rows = []
        for name in names:
            path = os.path.join(folder, name)
            try:
                is_dir = os.path.isdir(path)
            except OSError:
                is_dir = False
            rows.append((0 if is_dir else 1, name.lower(), name, path, is_dir))
        rows.sort()
        if not rows:
            self._file_note("这个文件夹是空的。")
            return
        cap = 400
        for _ord, _low, name, path, is_dir in rows[:cap]:
            item = QListWidgetItem(name + ("/" if is_dir else ""))
            item.setData(Qt.UserRole, path)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.file_list.addItem(item)
        if len(rows) > cap:
            self._file_note(f"还有 {len(rows) - cap} 项没有列出。")

    def _file_note(self, text: str):
        item = QListWidgetItem(text)
        item.setFlags(Qt.NoItemFlags)
        self.file_list.addItem(item)

    def _open_work_item(self, item):
        path = item.data(Qt.UserRole) if item is not None else ""
        if not path or not os.path.exists(path):
            return
        try:
            os.startfile(path)
        except OSError as e:
            self._set_status(f"打不开：{e}", error=True)

    def _open_skills_dir(self):
        path = skills_dir()
        try:
            os.startfile(path)
        except OSError as e:
            self._set_status(f"打不开技能目录：{e}", error=True)

    def _reload_skills(self):
        self._filling_skills = True
        self.skill_list.blockSignals(True)
        before = list(self._enabled_skill_names)
        try:
            self._skill_rows = scan_skills()
            alive = {sk.get("name") for sk in self._skill_rows}
            self._enabled_skill_names = [n for n in before if n in alive]
            enabled = set(self._enabled_skill_names)
            self.skill_list.clear()
            if not self._skill_rows:
                item = QListWidgetItem("还没有技能。打开目录，放入 .md 文件。")
                item.setFlags(Qt.NoItemFlags)
                self.skill_list.addItem(item)
            for sk in self._skill_rows:
                desc = (sk.get("desc") or "").strip()
                label = sk.get("name") or "未命名"
                if desc:
                    label = f"{label}  {desc}"
                item = QListWidgetItem(label)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setData(Qt.UserRole, sk.get("name") or "")
                item.setCheckState(
                    Qt.Checked if sk.get("name") in enabled else Qt.Unchecked
                )
                self.skill_list.addItem(item)
        finally:
            self.skill_list.blockSignals(False)
            self._filling_skills = False
        if self._enabled_skill_names != before and not self._applying:
            self._notify_prefs()

    def _on_skill_check(self, item):
        if self._filling_skills or self._applying or item is None:
            return
        name = item.data(Qt.UserRole)
        if not name:
            return
        enabled = [n for n in self._enabled_skill_names if n != name]
        if item.checkState() == Qt.Checked:
            enabled.append(name)
        order = [sk.get("name") for sk in self._skill_rows]
        self._enabled_skill_names = [n for n in order if n in set(enabled)]
        self._notify_prefs()

    def _on_models_failed(self, msg: str):
        self._set_status(msg or "读取模型列表失败。", error=True)

    def _sit_status_on_title(self, title_label: QLabel) -> None:
        """连接状态跟在卡片标题右边，贴行尾右对齐。"""
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
        row.setSpacing(12)
        title_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        row.addWidget(title_label, 0, Qt.AlignVCenter)
        row.addWidget(self.lbl_status, 1, Qt.AlignVCenter)
        lay.addWidget(row_w)

    def _set_status(self, text: str, error: bool = False):
        color = tk("err") if error else tk("text_mut")
        lbl = getattr(self, "lbl_status", None)
        if lbl is None:
            # 对话页已去掉。失败时写到配置页的模型计数上，避免刷新报错看不见。
            if error:
                count = getattr(self, "lbl_ollama_count", None)
                if count is not None:
                    count.setStyleSheet(f"color:{color}; background:transparent;")
                    count.setText(text)
            return
        lbl.setStyleSheet(f"color:{color}; background:transparent;")
        lbl.setText(text)
        QTimer.singleShot(0, self._scroll_status_bottom)

    def _scroll_status_bottom(self):
        scroll = getattr(self, "status_scroll", None)
        if scroll is None:
            return
        bar = scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _refresh_attach_list(self):
        lst = getattr(self, "attach_list", None)
        if lst is None:
            return
        lst.clear()
        for path in self._attach_paths:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, path)
            item.setSizeHint(QSize(0, 24))
            row = QWidget()
            row.setObjectName("OllamaAttachRow")
            row.setAttribute(Qt.WA_StyledBackground, True)
            lay = QHBoxLayout(row)
            lay.setContentsMargins(2, 0, 0, 0)
            lay.setSpacing(2)
            name = QLabel(os.path.basename(path))
            name.setObjectName("OllamaAttachName")
            name.setToolTip(path)
            name.setMinimumWidth(0)
            name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            btn = QPushButton("×")
            btn.setObjectName("OllamaAttachDel")
            btn.setFixedSize(18, 18)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setToolTip("移除")
            btn.clicked.connect(lambda _=False, p=path: self._remove_attach(p))
            lay.addWidget(name, 1)
            lay.addWidget(btn, 0)
            lst.addItem(item)
            lst.setItemWidget(item, row)
        n = len(self._attach_paths)
        self.attach_hint.setVisible(n == 0)
        lst.setVisible(n > 0)
        self.side_tabs.setTabText(1, "文件" if n == 0 else f"文件 {n}")

    def _add_attach_paths(self, paths):
        added = 0
        skipped = []
        for raw in paths or []:
            path = os.path.normpath(str(raw or "").strip())
            if not path:
                continue
            if os.path.isdir(path):
                skipped.append(f"{os.path.basename(path) or path}：是文件夹")
                continue
            if not os.path.isfile(path):
                skipped.append(f"{os.path.basename(path) or path}：找不到文件")
                continue
            if any(_same_path(path, old) for old in self._attach_paths):
                continue
            self._attach_paths.append(path)
            added += 1
        self._refresh_attach_list()
        if added:
            self.side_tabs.setCurrentIndex(1)
        if skipped:
            self._push_status("未加入：" + "；".join(skipped), error=True)
        elif added:
            self._push_status(f"已加入 {added} 个文件。")

    def _remove_attach(self, path: str):
        self._attach_paths = [p for p in self._attach_paths if not _same_path(p, path)]
        self._refresh_attach_list()

    def _model_message(self, m: dict):
        if m.get("role") not in ("user", "assistant", "system"):
            return None
        item = {"role": m["role"], "content": m.get("content") or ""}
        if m.get("images"):
            item["images"] = list(m["images"])
        if m.get("attach_text"):
            item["attach_text"] = m["attach_text"]
        return item

    def _push_status(self, text: str, error: bool = False):
        """对话状态往下接。思考、写正文这种计数只刷新最后一行。"""
        text = (text or "").strip()
        if not text:
            return
        key = text
        for prefix in ("正在思考", "正在写正文", "规划中", "感知中"):
            if text.startswith(prefix):
                key = prefix
                break
        lines = self._status_lines
        if lines and lines[-1][0] == key and key in ("正在思考", "正在写正文", "规划中", "感知中"):
            lines[-1] = (key, text)
        elif lines and lines[-1][1] == text:
            pass
        else:
            lines.append((key, text))
        self._status_lines = lines[-8:]
        self._set_status("\n".join(item[1] for item in self._status_lines), error=error)

    # ── 对话 ───────────────────────────────────────────────────
    def _busy(self) -> bool:
        return self._chat_worker is not None and self._chat_worker.isRunning()

    def send(self):
        if hasattr(self, 'agent_panel') and self.agent_panel.busy:
            self._push_status("新版 Agent 正在运行，请先暂停任务。")
            return
        if self._busy():
            return
        if self._reloading():
            self._push_status("模型正在重新加载，请稍候再发送。")
            return
        text = self.inp.toPlainText().strip()
        pending = list(self._attach_paths)
        if not text and not pending:
            return
        model = self._model_name()
        if not model:
            if self._using_api():
                self._set_status("还没有 API 模型。请到「配置」里填写并检测。", error=True)
            else:
                self._set_status("还没有可用模型：请到「配置」确认 Ollama 已启动并刷新。", error=True)
            return
        images, attach_text, names, skip_notes, used = [], "", [], [], []
        warns = []
        if pending and self._using_api():
            if not text:
                self._set_status("附件只发给本地模型。当前是 API。", error=True)
                return
            warns.append("当前是 API，附件未发送。")
        elif pending:
            images, attach_text, names, skip_notes, used = _pack_attachments(pending)
            if skip_notes:
                warns.append("未附上：" + "；".join(skip_notes))
            if not text and not names:
                self._set_status("；".join(skip_notes) or "没有能发送的附件。", error=True)
                return
            if not text:
                text = "请看附件。"
        num_ctx = self._num_ctx()
        send_item = {"role": "user", "content": text}
        if images:
            send_item["images"] = images
        if attach_text:
            send_item["attach_text"] = attach_text
        msgs = []
        for old in self._history:
            item = self._model_message(old)
            if item:
                msgs.append(item)
        msgs.append(send_item)
        notes = []
        if not self._using_api():
            reserve = estimate_tokens(self._extra_system()) + 300
            if self._save_enabled():
                reserve += 600
            if self.sw_web.isChecked():
                reserve += 300
            kept, dropped, last_tok, over = fit_history(msgs, num_ctx, reserve)
            if dropped and not getattr(self, "_trim_noticed", False):
                self._trim_noticed = True
                notes.append(
                    f"较早的 {dropped} 条对话已不再发给模型，只保留最近的内容。"
                )
            msgs = kept
        self.inp.clear()
        if used:
            self._attach_paths = [
                p for p in self._attach_paths if not any(_same_path(p, done) for done in used)
            ]
            self._refresh_attach_list()
        self._error_text = ""
        self._truncated = False
        self._status_lines = []
        self._push_status("正在发送…")
        if names:
            self._push_status(f"已附上 {len(names)} 个文件。")
            log.info("ollama 附件 %s", "、".join(names))
        for warn in warns:
            self._push_status(warn, error=True)
        shown = {"role": "user", "content": text}
        if names:
            shown["files"] = list(names)
        if images:
            shown["images"] = images
        if attach_text:
            shown["attach_text"] = attach_text
        self._history.append(shown)
        self._history.append({"role": "assistant", "content": "", "notices": notes})
        w = _ChatWorker(
            self._base(), model, msgs,
            web=self.sw_web.isChecked(), dirs=self._save_enabled(),
            article_dir=self._article_dir(),
            obedient=self.sw_obey.isChecked(),
            source="api" if self._using_api() else "ollama",
            api_base=self.ed_api_base.text().strip(),
            api_key=self.ed_api_key.text(),
            extra_system=self._extra_system(),
            num_ctx=0,   # 0 = 程序按任务自动决定
            session_id=getattr(self, "_agent_session_id", "") or "",
            parent=self,
        )
        w.token.connect(self._on_token)
        w.notice.connect(self._on_worker_notice)
        w.failed.connect(self._on_chat_failed)
        w.status.connect(self._on_worker_status)
        w.finished.connect(self._on_chat_finished)
        self._chat_worker = w
        self._set_busy_ui(True)
        self._stick = True          # 自己刚发的消息，一定拉到底
        self._render()
        self._scroll_to_bottom(force=True)
        w.start()

    def _from_current_worker(self) -> bool:
        w = self._chat_worker
        return w is not None and self.sender() is w

    def _abandon_worker(self):
        """停掉正在跑的那一轮，并立刻放开界面。晚到的字不再写入新对话。"""
        w = self._chat_worker
        self._chat_worker = None
        if w is not None:
            for sig, slot in (
                (w.token, self._on_token),
                (w.failed, self._on_chat_failed),
                (w.status, self._on_worker_status),
                (w.notice, self._on_worker_notice),
                (w.finished, self._on_chat_finished),
            ):
                try:
                    sig.disconnect(slot)
                except Exception:
                    pass
            w.stop()
            self._retired_workers.append(w)
            self._retired_workers = [x for x in self._retired_workers if x.isRunning()]
        self._render_dirty = False
        self._body_table = None
        self._painted = -1
        self._set_busy_ui(False)

    # ── 新对话：先卸载、再重新加载当前模型（释放内存/显存和 KV 缓存）──────
    def _reloading(self) -> bool:
        w = getattr(self, "_reload_worker", None)
        return w is not None and w.isRunning()

    def _start_model_reload(self, wait_for=None):
        if self._using_api():
            return False
        if self._reloading():
            self._push_status("模型正在重新加载，请稍候。")
            return False
        model = self.cb_model.currentText().strip() or self._pending_model
        if not model:
            self._push_status("还没有选模型，没有重新加载。")
            return False
        w = _ReloadWorker(self._base(), model, wait_for=wait_for, parent=self)
        w.done.connect(self._on_reload_done)
        self._reload_worker = w
        self.btn_send.setEnabled(False)   # 重新加载期间先不发送，避免和卸载撞在一起
        self._push_status(f"正在卸载并重新加载模型：{model}…")
        w.start()
        return True

    def _on_reload_done(self, res: dict):
        res = res or {}
        ok = bool(res.get("ok"))
        self._push_status(str(res.get("text") or "重新加载模型没有完成。"), error=not ok)
        self.btn_send.setEnabled(not self._busy())

    def stop_generation(self):
        running = self._chat_worker is not None
        self._abandon_worker()
        if running:
            self._push_status("已停止。")
        if (
            self._history
            and self._history[-1].get("role") == "assistant"
            and not self._history[-1].get("content")
        ):
            self._history.pop()
            self._render()

    def clear_screen(self):
        """清掉当前对话显示和上下文。正在生成的那一轮会马上停下。"""
        self._abandon_worker()
        self._history = []
        self._error_text = ""
        self._restore_link_status = False
        self._status_lines = []
        self._truncated = False
        self._trim_noticed = False
        clear_partial_reads()
        self._agent_session_id = uuid.uuid4().hex
        self._render()
        self._push_status("已清屏。上一段上下文已清空。")

    def new_conversation(self):
        # 停当前回答、清空对话、换新会话（不注入旧进度）。
        # 不再执行 keep_alive=0 卸载再重载 30GB+ 大模型，保持已加载的显存分配与 30720 上下文常驻，实现秒开新对话。
        self.clear_screen()
        self._status_lines = []
        self._push_status("已开始新对话。上下文与会话进度已重置（模型保持常驻，无需重新加载）。")

    def _on_token(self, piece: str):
        if not self._from_current_worker():
            return
        if self._history and self._history[-1]["role"] == "assistant":
            self._history[-1]["content"] += piece
            self._render_dirty = True

    def _on_chat_failed(self, msg: str):
        if not self._from_current_worker():
            return
        self._error_text = msg or "对话失败。"
        self._push_status(self._error_text, error=True)

    def _on_worker_status(self, text: str):
        if not self._from_current_worker():
            return
        self._restore_link_status = True
        self._push_status(text)

    def _on_worker_notice(self, text: str):
        if not self._from_current_worker():
            return
        self.add_notice(text)

    def add_notice(self, text: str):
        """长期留在对话里的提示：挂在当前助手气泡下面，不发给模型。"""
        text = (text or "").strip()
        if not text:
            return
        if "被截断" in text:
            self._truncated = True
        if self._history and self._history[-1].get("role") == "assistant":
            self._history[-1].setdefault("notices", []).append(text)
        else:
            self._history.append({"role": "notice", "content": text})
        self._render()

    def _on_chat_finished(self):
        if not self._from_current_worker():
            return
        # 空回复（出错/立即停止）不进历史，避免污染下一轮上下文；提示单独留下
        if self._history and self._history[-1]["role"] == "assistant" and not self._history[-1]["content"]:
            notes = self._history.pop().get("notices") or []
            if self._history and self._history[-1]["role"] == "user" and self._error_text:
                self._history.pop()
            for note in notes:
                self._history.append({"role": "notice", "content": note})
        self._set_busy_ui(False)
        self._restore_link_status = False
        last = self._status_lines[-1][1] if self._status_lines else ""
        if not self._error_text and getattr(self, "_truncated", False):
            if last != "未完成（被截断）":
                self._push_status("未完成（被截断）", error=True)
        elif not self._error_text and last.startswith("未完成"):
            self._push_status(last, error=True)
        elif not self._error_text:
            if last.startswith(("回答完成", "上下文已满", "模型仍在空转", "已停止", "已完成",
                                "没有新进展", "进度已记下", "上一轮回复被截断")):
                pass
            else:
                n = 0
                if self._history and self._history[-1].get("role") == "assistant":
                    n = len(self._history[-1].get("content") or "")
                if n:
                    self._push_status(f"回答完成，正文 {n} 字。")
        self._render()

    def _set_busy_ui(self, busy: bool):
        btn_send = getattr(self, "btn_send", None)
        btn_stop = getattr(self, "btn_stop", None)
        if btn_send is None or btn_stop is None:
            timer = getattr(self, "_render_timer", None)
            if timer is not None:
                timer.stop()
            return
        btn_send.setEnabled(not busy)
        btn_stop.setEnabled(busy)
        if not busy and self._reloading():
            self.btn_send.setEnabled(False)
        self.cb_model.setEnabled(not busy)
        if busy:
            self._render_timer.start()
        else:
            self._render_timer.stop()

    # ── 绘制 ───────────────────────────────────────────────────
    def _should_follow(self) -> bool:
        return bool(getattr(self, "_stick", True))

    def _set_scroll(self, value: int):
        bar = self.view.verticalScrollBar()
        self._scroll_quiet += 1
        try:
            bar.setValue(value)
        finally:
            self._scroll_quiet -= 1

    def _on_scroll_value(self, value: int):
        if self._scroll_quiet:
            return
        bar = self.view.verticalScrollBar()
        self._stick = bar.maximum() <= 0 or value >= bar.maximum() - 4

    def _on_scroll_range(self, _lo: int, hi: int):
        # 文档排版是分步完成的，最大值会陆续变大；贴底时每次都跟上。
        if getattr(self, "_stick", True):
            self._set_scroll(hi)

    def _snap_bottom(self):
        self._scroll_to_bottom()

    def _scroll_to_bottom(self, force: bool = False):
        """拉到底。排版可能还没完成，所以现在拉一次，事件循环空下来后再拉两次。"""
        if force:
            self._stick = True
        if not self._stick:
            return
        def go():
            if self._stick:
                self._settle_layout()
                self._set_scroll(self.view.verticalScrollBar().maximum())
        go()
        QTimer.singleShot(0, go)
        QTimer.singleShot(80, go)

    def _settle_layout(self):
        try:
            self.view.document().documentLayout().documentSize()   # 逼排版做完，滚动条范围随之更新
        except Exception:
            pass

    def _last_body_table(self):
        """最后一条气泡的内层表格，正文在它的单元格里。"""
        def walk(frame):
            found = None
            it = frame.begin()
            while not it.atEnd():
                child = it.currentFrame()
                if isinstance(child, QTextTable):
                    found = walk(child) or child
                it += 1
            return found
        return walk(self.view.document().rootFrame())

    def _capture_stream_body(self):
        self._body_table = None
        self._painted = -1
        if not self._history or self._history[-1].get("role") != "assistant":
            return
        table = self._last_body_table()
        if table is None:
            return
        self._body_table = table
        content = self._history[-1].get("content") or ""
        if content:
            self._painted = len(content)

    def _body_format(self, base: QTextCharFormat) -> QTextCharFormat:
        fmt = QTextCharFormat(base)
        fmt.setFontItalic(False)
        fmt.setForeground(QColor(tk("text")))
        return fmt

    def _patch_stream(self) -> bool:
        """只改最后一条助手气泡。失败时交给整页重绘。"""
        table = self._body_table
        if table is None or not self._busy():
            return False
        if not self._history or self._history[-1].get("role") != "assistant":
            return False
        content = self._history[-1].get("content") or ""
        if not content:
            return True
        try:
            if table.document() is not self.view.document():
                return False
            cell = table.cellAt(0, 0)
            if not cell.isValid():
                return False
            follow = self._should_follow()
            painted = self._painted
            if painted < 0 or painted > len(content):
                cur = cell.firstCursorPosition()
                cur.setPosition(cell.lastCursorPosition().position(), QTextCursor.KeepAnchor)
                cur.insertText(content, self._body_format(cur.charFormat()))
            elif painted < len(content):
                cur = cell.lastCursorPosition()
                cur.insertText(content[painted:], self._body_format(cur.charFormat()))
            else:
                return True
            self._painted = len(content)
        except Exception:
            log.exception("ollama 气泡追加失败")
            self._body_table = None
            self._painted = -1
            return False
        if follow:
            self._snap_bottom()
        return True

    def _flush_render(self):
        if not self._render_dirty:
            return
        if self._patch_stream():
            self._render_dirty = False
            return
        self._render()

    def _make_copy_icon(self):
        img = QImage(14, 14, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        pen = QPen(QColor(tk("text_faint")))
        pen.setWidth(1)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(5, 1, 7, 7)
        p.drawRect(1, 5, 7, 7)
        p.end()
        return img

    def _notice_html(self, text: str) -> str:
        return (
            f'<p style="margin:4px 0;color:{tk("warn")};font-size:12px;">'
            f"⚠ {html.escape(text)}</p>"
        )

    def _bubble(self, index: int, role: str, content: str, notices=None, files=None) -> str:
        if role == "notice":
            return self._notice_html(content)
        if role == "user":
            who, bg, bd, align = "我", tk("bubble_user_bg"), tk("bubble_user_border"), "right"
        else:
            who, bg, bd, align = (self._model_name() or "助手"), tk("bubble_ai_bg"), tk("bubble_ai_border"), "left"
        body = html.escape(content).replace("\n", "<br>") if content else "<i>思考中…</i>"
        if role == "user" and files:
            label = "、".join(html.escape(str(n)) for n in files if str(n).strip())
            if label:
                body = (
                    f'<div style="color:{tk("text_mut")};font-size:11px;margin-bottom:4px;">'
                    f"附件 {label}</div>"
                ) + body
        # 图标在名字行，不放进正文单元格。流式追加只改最后一条正文。
        copy = (
            f'&nbsp;<a href="app://copy/{index}" style="text-decoration:none;">'
            f'<img src="ollama-copy.png" width="12" height="12"/></a>'
        )
        return (
            f'<table width="100%" cellspacing="0" cellpadding="0" style="margin:6px 0;"><tr><td align="{align}">'
            f'<div style="color:{tk("text_mut")};font-size:11px;">{html.escape(who)}{copy}</div>'
            f'<table cellspacing="0" cellpadding="8" style="background:{bg};border:1px solid {bd};">'
            f'<tr><td style="color:{tk("text")};">{body}</td></tr></table>'
            + "".join(self._notice_html(n) for n in (notices or []))
            + f"</td></tr></table>"
        )

    def _on_bubble_anchor(self, url):
        if not isinstance(url, QUrl):
            url = QUrl(str(url))
        if url.scheme() == "app" and url.host() == "copy":
            try:
                idx = int((url.path() or "").strip("/"))
            except ValueError:
                return
            if idx < 0 or idx >= len(self._history):
                return
            content = self._history[idx].get("content") or ""
            if not content:
                self._push_status("这条还没有内容。")
                return
            QApplication.clipboard().setText(content)
            self._push_status("已复制。")
            QToolTip.showText(QCursor.pos(), "已复制", self.view)
            return
        if url.scheme().lower() in ("http", "https"):
            QDesktopServices.openUrl(url)

    def _render(self, *_):
        if getattr(self, "view", None) is None:
            return
        self._render_dirty = False
        self._body_table = None
        self._painted = -1
        follow = self._should_follow()
        keep = self.view.verticalScrollBar().value()
        if not self._history:
            html_doc = (
                f'<p style="color:{tk("text_mut")};">选择本地模型后开始对话。'
                f"对话只保存在内存中，点击「新对话」即清空。</p>"
            )
        else:
            html_doc = "".join(
                self._bubble(i, m["role"], m["content"], m.get("notices"), m.get("files"))
                for i, m in enumerate(self._history)
            )
        view = self.view
        view.set_copy_icon(self._make_copy_icon())
        # 关掉重绘再换文档，避免滚动条先回到顶部被画出来。
        # setHtml 会把滚动条打回 0：换文档期间不让它改贴底标记，换完按原状态恢复。
        view.setUpdatesEnabled(False)
        self._scroll_quiet += 1
        try:
            view.setHtml(html_doc)
            self._capture_stream_body()
            self._settle_layout()
            bar = view.verticalScrollBar()
            bar.setValue(bar.maximum() if follow else min(keep, bar.maximum()))
        finally:
            self._scroll_quiet -= 1
            view.setUpdatesEnabled(True)
        self._stick = follow
        if follow:
            self._scroll_to_bottom()
        else:
            QTimer.singleShot(0, lambda v=keep: (not self._stick) and self._set_scroll(
                min(v, self.view.verticalScrollBar().maximum())))
