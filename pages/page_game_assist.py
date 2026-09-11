# pages/page_game_assist.py
# 游戏助手：知识库 + 截图 + 后台 OpenAI 兼容大模型 API 作答，多答案可切换。

from __future__ import annotations

import json
import os
import time

from PyQt5.QtCore import (
    Qt, QTimer, QSize, QThread, pyqtSignal, QObject, QEvent,
    QRectF, QPointF, QPropertyAnimation, QEasingCurve, QAbstractAnimation,
    QPoint, QRect, QMimeData,
)
from PyQt5.QtGui import (
    QPixmap, QPainter, QColor, QPen, QPainterPath, QIcon, QDrag,
)
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog,
    QLineEdit, QComboBox, QFrame, QSizePolicy, QApplication,
    QScrollArea,
    QPlainTextEdit, QGraphicsOpacityEffect, QCheckBox, QInputDialog,
    QMessageBox,
)

from styles.style_all import (
    theme, tk, TEXT_STYLE, make_card, install_card_title,
    apply_mini_button, apply_medium_button, make_glyph_icon,
    CARD_LEFT_GAP, CARD_RIGHT_GAP, CARD_TOP_GAP, CARD_BOTTOM_GAP,
    message_box_warn, message_box_info,
)
from utils.game_assist import (
    build_index, save_index, load_index, retrieve, kb_char_count,
    format_passages, chat, parse_answers,
    build_answer_prompt, list_api_models,
    normalize_api_base, default_api_base, KB_INLINE_LIMIT, game_assist_dir,
    ensure_managed_file_source, restore_missing_copies,
    relocate_missing_file_sources,
    remove_kb_copy, kb_target_for, overwrite_kb_copy, record_source_stamp,
    list_folder_files, is_supported_text_file, GID_UNCAT, UNCAT_TITLE,
    source_group,
    index_subset_for_groups,
)
from utils.flow_layout import FlowLayout
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
_KB_CARD_MIME = "application/x-ga-kb-card"

# 常用 OpenAI 兼容服务预设：(下拉显示名(中文), base_url, 推荐模型名；模型名为空则不自动填)
_API_PRESETS = (
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


class RecognizeWorker(QThread):
    """用本地 OCR 识别截图文字，不走大模型 API。结果可编辑后再点「开工」。"""

    status = pyqtSignal(str)
    step = pyqtSignal(str, str)  # (step_key, state)  →  _FLOW_STATE_*
    done = pyqtSignal(str)       # 识别出的文字（可能为空）
    fail = pyqtSignal(str)

    def __init__(self, image_path="", parent=None):
        super().__init__(parent)
        self._image_path = image_path or ""
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
            log.exception("游戏助手 OCR 识别失败")
            if not self._check():
                self.fail.emit(str(e) or "识别失败")

    def _run(self):
        self.step.emit("read", _FLOW_STATE_BUSY)
        self.status.emit("正在识别截图文字…")
        text = self._ocr_query()
        if self._check():
            return
        self.step.emit("read", _FLOW_STATE_OK)
        self.done.emit(text)

    def _ocr_query(self) -> str:
        try:
            from utils.ocr_util import is_local_ocr_ready, ocr_image_path
        except Exception:
            return ""
        if not is_local_ocr_ready():
            return ""
        path = self._image_path
        if not path or not os.path.isfile(path):
            return ""
        try:
            text, engine = ocr_image_path(path)
        except Exception:
            log.debug("游戏助手 OCR 失败", exc_info=True)
            return ""
        if not text or text.startswith("❌") or engine == "none":
            return ""
        return text.strip()[:2000]


class AskWorker(QThread):
    """用识别结果（可编辑后的文字）检索知识库并调大模型 API 作答。"""

    status = pyqtSignal(str)
    step = pyqtSignal(str, str)  # (step_key, state)  →  _FLOW_STATE_*
    done = pyqtSignal(list)
    fail = pyqtSignal(str)

    def __init__(self, base_url, api_key, model, index, query, parent=None):
        super().__init__(parent)
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._index = index or {}
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
            log.exception("游戏助手查询失败")
            if not self._check():
                self.fail.emit(str(e) or "查询失败")

    def _run(self):
        base_url = self._base_url
        api_key = self._api_key
        model = self._model
        index = self._index
        query = (self._query or "").strip()

        self.step.emit("retrieve", _FLOW_STATE_BUSY)
        self.status.emit("正在知识库检索…")
        chunks = retrieve(index, query, limit=8)
        if not chunks and kb_char_count(index) <= KB_INLINE_LIMIT:
            chunks = list((index.get("chunks") or [])[:8])
        passages = format_passages(chunks)
        self.step.emit("retrieve", _FLOW_STATE_OK)

        if self._check():
            return
        self.step.emit("answer", _FLOW_STATE_BUSY)
        self.status.emit("大模型 API 正在作答…")
        prompt = build_answer_prompt(query, passages, has_image=False)
        raw = chat(base_url, api_key, model, prompt, timeout=180)
        if self._check():
            return
        answers = parse_answers(raw)
        if not answers:
            self.fail.emit("模型没有给出可用答案")
            return
        self.step.emit("answer", _FLOW_STATE_OK)
        self.done.emit(answers)


def _kb_title(rec: dict) -> str:
    path = str((rec or {}).get("path") or "")
    name = os.path.basename(path.rstrip("\\/")) or path
    if (rec or {}).get("kind") == "folder":
        return name or "文件夹"
    stem = os.path.splitext(name)[0]
    return stem or name or "文件"


def _trash_icon(color: str, size: int = _KB_ICON_PX) -> QIcon:
    size = max(14, int(size))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    c = QColor(color)
    s = float(size)
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


def _rename_icon(color: str, size: int = _GROUP_RENAME_ICON_PX) -> QIcon:
    return make_glyph_icon("✎", px=int(size), color=color)


def _rgba(color: str, alpha: float) -> str:
    c = QColor(color)
    try:
        c.setAlphaF(max(0.0, min(1.0, float(alpha))))
    except (TypeError, ValueError):
        pass
    return f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha()})"


def _kb_inline_btn_qss(ink: str) -> str:
    """知识卡上带外框的小按钮（更新/删除共用），边框色随卡面色取 readable 色。"""
    base = _rgba(ink, 0.55)
    hot = _rgba(ink, 0.9)
    fill = _rgba(ink, 0.14)
    dim = _rgba(ink, 0.32)
    return (
        "QPushButton{{background:transparent;border:1px solid {base};"
        "border-radius:4px;color:{ink};font-size:11px;padding:0 3px;}}"
        "QPushButton:hover{{border-color:{hot};background:{fill};}}"
        "QPushButton:pressed{{background:{fill};border-color:{ink};}}"
        "QPushButton:disabled{{color:{dim};border-color:{dim};"
        "background:transparent;}}"
    ).format(base=base, ink=ink, hot=hot, fill=fill, dim=dim)


def _kb_group_icon_btn_qss(danger: bool = False) -> str:
    """分类标题行右侧的框选小按钮（改名/删除）：主题色描边外框。"""
    try:
        border = tk("border")
        hover = tk("accent")
        fill = tk("hover_veil")
    except Exception:
        border = "#25345c"
        hover = "#3a8ee0"
        fill = "rgba(255,255,255,0.04)"
    if danger:
        hover = "#ef4444"
    return (
        "QPushButton{{background:transparent;border:1px solid {border};"
        "border-radius:4px;padding:0;}}"
        "QPushButton:hover{{border-color:{hover};background:{fill};}}"
        "QPushButton:pressed{{background:{fill};border-color:{hover};}}"
    ).format(border=border, hover=hover, fill=fill)


class _KbCard(QFrame):
    """正方形知识卡：圆角色块 + 标题（可多行）+ 删除框。可拖拽改分类/排序。"""

    remove_requested = pyqtSignal(str)

    def __init__(self, rec: dict, parent=None):
        super().__init__(parent)
        self.rec = dict(rec or {})
        self.setObjectName("GameAssistKbCard")
        self.setFrameShape(QFrame.NoFrame)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedSize(_KB_CARD_W, _KB_CARD_H)
        self._press_pos = QPoint()
        self._drag_pressed = False
        self.setCursor(Qt.OpenHandCursor)
        color = str(self.rec.get("color") or _KB_PALETTE[0])
        self._bg = QColor(color)
        if not self._bg.isValid():
            self._bg = QColor(_KB_PALETTE[0])
        self._bd = QColor(self._bg.darker(140))
        y = 0.299 * self._bg.red() + 0.587 * self._bg.green() + 0.114 * self._bg.blue()
        ink = "#111827" if y >= 160 else "#ffffff"

        self._lab = QLabel()
        self._lab.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self._lab.setWordWrap(True)
        self._lab.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._lab.setStyleSheet(
            f"color:{ink}; font-size:11px; font-weight:600;"
            f"background:transparent; border:none; padding:2px 5px 0 5px;"
        )

        self.btn_del = QPushButton()
        self.btn_del.setCursor(Qt.PointingHandCursor)
        self.btn_del.setFocusPolicy(Qt.NoFocus)
        self.btn_del.setFlat(False)
        self.btn_del.setFixedSize(_KB_DEL_BTN_W, _KB_BTN_H)
        self.btn_del.setIconSize(QSize(13, 13))
        self.btn_del.setIcon(_trash_icon(ink, 13))
        self.btn_del.setToolTip("移除")
        self.btn_del.setStyleSheet(_kb_inline_btn_qss(ink))
        self.btn_del.clicked.connect(
            lambda: self.remove_requested.emit(str(self.rec.get("path") or ""))
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(3)
        lay.addWidget(self._lab, 1)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(self.btn_del, 0, Qt.AlignRight | Qt.AlignVCenter)
        lay.addLayout(row)
        self._apply_title()

    def _apply_title(self):
        title = _kb_title(self.rec)
        path = str(self.rec.get("path") or "")
        missing = bool(path) and not (
            os.path.isdir(path) if self.rec.get("kind") == "folder" else os.path.isfile(path)
        )
        if missing:
            title = title + "（丢失）"
        self._lab.setToolTip(path or title)
        self._lab.setText(title)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, _KB_CARD_RADIUS, _KB_CARD_RADIUS)
        p.setClipPath(path)
        p.fillRect(rect, self._bg)
        p.setClipping(False)
        p.setPen(QPen(self._bd, 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect, _KB_CARD_RADIUS, _KB_CARD_RADIUS)
        p.end()

    # ── 知识卡拖拽：按住卡片拖到某个分类分隔线即可改归属 ──
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press_pos = QPoint(e.pos())
            self._drag_pressed = True
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if (
            self._drag_pressed
            and e.buttons() & Qt.LeftButton
            and (e.pos() - self._press_pos).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            self._drag_pressed = False
            self._start_drag()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_pressed = False
        super().mouseReleaseEvent(e)

    def _start_drag(self):
        payload = {
            "path": str(self.rec.get("path") or ""),
            "gid": source_group(self.rec),
        }
        md = QMimeData()
        try:
            md.setData(_KB_CARD_MIME, json.dumps(payload).encode("utf-8"))
        except Exception:
            md.setData(_KB_CARD_MIME, str(payload.get("path") or "").encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(md)
        try:
            pm = self.grab()
            pm = pm.scaled(
                max(1, int(pm.width() * 0.6)),
                max(1, int(pm.height() * 0.6)),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            drag.setPixmap(pm)
            drag.setHotSpot(QPoint(pm.width() // 2, 18))
        except Exception:
            pass
        drag.exec_(Qt.MoveAction)


def _local_file_urls(md) -> list:
    """从拖放 mime 里取本地文件路径；非「从资源管理器拖文件」返回空。"""
    try:
        if md is None or not md.hasUrls():
            return []
        out = []
        for u in md.urls():
            try:
                if u.isLocalFile():
                    out.append(u.toLocalFile())
            except Exception:
                pass
        return out
    except Exception:
        return []


class _KbFileDropOverlay(QWidget):
    """整张「知识库」卡的拖放高亮浮层：圆角淡色填充 + 虚线描边。

    置顶、鼠标穿透，不拦截底下交互；文件拖入知识库区域时显示，
    松开/移出后隐藏。作为 card 的子控件自动跟随 card 尺寸。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("GameAssistKbDropOverlay")
        # 关键：全局 QWidget{background:…} 会把这层盖成不透明 → 内容“消失”
        self.setStyleSheet("#GameAssistKbDropOverlay{background:transparent;}")
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.hide()
        par = self.parentWidget()
        if par is not None:
            par.installEventFilter(self)
            self.setGeometry(par.rect().adjusted(1, 1, -1, -1))

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Resize:
            self._fit()
        return False

    def _fit(self):
        par = self.parentWidget()
        if par is not None:
            self.setGeometry(par.rect().adjusted(1, 1, -1, -1))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        try:
            accent = QColor(tk("accent"))
        except Exception:
            accent = QColor("#3a8ee0")
        pen = QPen(accent, 2.0)
        pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8.0, 8.0)
        p.end()

    def showEvent(self, event):
        self._fit()
        self.raise_()
        super().showEvent(event)


class _ExternalFileDropFilter(QObject):
    """给「知识库」区外围控件接 OS 文件拖放：文件拖入 → 回调 paths。

    只认本地文件的拖放；内部知识卡拖拽（私有 mime、无 url）原样忽略，
    交给原有放置逻辑，不干扰分类移动。
    """

    def __init__(self, on_files, parent=None, hover=None):
        super().__init__(parent)
        self._on_files = on_files
        self._hover = hover
        self._pending = []

    def eventFilter(self, obj, event):
        t = event.type()
        if t == QEvent.DragEnter:
            urls = _local_file_urls(event.mimeData())
            if urls:
                self._pending = urls
                event.acceptProposedAction()
                if callable(self._hover):
                    self._hover(True)
            else:
                self._pending = []
                event.ignore()
            return True
        if t == QEvent.DragMove:
            urls = _local_file_urls(event.mimeData())
            if urls:
                self._pending = urls
                event.acceptProposedAction()
            else:
                event.ignore()
            return True
        if t == QEvent.DragLeave:
            self._pending = []
            if callable(self._hover):
                self._hover(False)
            return False
        if t == QEvent.Drop:
            urls = _local_file_urls(event.mimeData())
            if not urls:
                urls = self._pending or []
            self._pending = []
            if urls:
                event.acceptProposedAction()
                try:
                    fn = self._on_files
                    if callable(fn):
                        fn(urls)
                except Exception:
                    log.exception("知识库拖放文件处理失败")
            else:
                event.ignore()
            if callable(self._hover):
                self._hover(False)
            return True
        return False


class _KbDropBox(QWidget):
    """分类区放置目标，带插入位置指示。

    · 跨分类拖入：卡片插入本分类开头（分隔线正下方），画横向插入线；
    · 同一分类内拖拽：卡片插到两张卡之间（或行首/行尾），画竖向插入线。
    插入位置以最后一次 dragMove 计算为准；鼠标停在别的区域时保留该区
    的位置指示，移开即清除。
    """

    drop_requested = pyqtSignal(str, str, int)  # (gid, source_path, index)
    files_dropped = pyqtSignal(list)             # 从资源管理器拖入的本地文件路径
    file_hover = pyqtSignal(bool)                # OS 文件是否悬停本块（交由整卡高亮）

    def __init__(self, gid, parent=None):
        super().__init__(parent)
        self.gid = gid
        self._over = False
        self._marker = None       # None | 'head' | 'between'
        self._insert_i = 0
        self._src_gid = None
        self._src_path = ""
        self._file_urls = []
        self._file_over = False   # OS 文件正悬停在块上（画接收高亮）
        self._kb_head = None      # 分隔线行（决定横向插入线的 y）
        self._kb_cards = []       # flow 里当前按显示顺序的卡片
        self.setAcceptDrops(True)
        self.setAttribute(Qt.WA_StyledBackground, True)

    def _set_over(self, on: bool):
        if on != self._over:
            self._over = on
            self.update()
        if not on:
            self._marker = None

    def _set_file_over(self, on: bool):
        if on != self._file_over:
            self._file_over = on
            self.file_hover.emit(bool(on))
            self.update()

    def _payload(self, md) -> dict:
        if md is None or not md.hasFormat(_KB_CARD_MIME):
            return None
        try:
            data = json.loads(bytes(md.data(_KB_CARD_MIME)).decode("utf-8", "replace"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        return data

    def _accept(self, e) -> bool:
        return self.gid is not None and self._payload(e.mimeData()) is not None

    # ── 卡片在自身坐标系里的矩形（按显示顺序） ────────────────
    def _flow_rects(self):
        out = []
        for c in list(self._kb_cards):
            if c is None:
                continue
            try:
                tl = c.mapTo(self, QPoint(0, 0))
                out.append((c, QRect(tl, c.size())))
            except Exception:
                pass
        return out

    def _insert_index(self, pos: QPoint) -> int:
        """指针 pos（本控件坐标）在哪个缝隙 → 返回卡序号 0..n。"""
        rects = self._flow_rects()
        if not rects:
            return 0
        tol = 8
        rows = []
        used = set()
        for i, (c, r) in enumerate(rects):
            if i in used:
                continue
            members = [(i, r)]
            used.add(i)
            for j, (c2, r2) in enumerate(rects):
                if j in used:
                    continue
                if abs(r2.center().y() - r.center().y()) <= tol:
                    members.append((j, r2))
                    used.add(j)
            members.sort(key=lambda kv: kv[1].x())
            rows.append(members)
        rows.sort(key=lambda m: m[0][1].y())

        best = None
        best_d = None
        for members in rows:
            rs = [r for _, r in members]
            top = min(r.top() for r in rs)
            bot = max(r.bottom() for r in rs)
            if top - tol <= pos.y() <= bot + tol:
                best = members
                break
            d = min(abs(pos.y() - r.center().y()) for r in rs)
            if best_d is None or d < best_d:
                best_d = d
                best = members
        prev = None
        for gidx, r in best:
            if pos.x() <= r.center().x():
                return int(gidx)
            prev = (gidx, r)
        if prev is not None:
            return int(prev[0]) + 1
        return len(rects)

    def dragEnterEvent(self, e):
        if self._accept(e):
            e.acceptProposedAction()
            self._refresh_state(e)
            return
        urls = _local_file_urls(e.mimeData())
        if urls:
            self._file_urls = urls
            self._set_file_over(True)
            e.acceptProposedAction()
        else:
            self._file_urls = []
            self._set_file_over(False)
            e.ignore()

    def dragMoveEvent(self, e):
        if self._accept(e):
            e.acceptProposedAction()
            self._refresh_state(e)
            return
        urls = _local_file_urls(e.mimeData())
        if urls:
            self._file_urls = urls
            self._set_file_over(True)
            e.acceptProposedAction()
        else:
            self._file_urls = []
            self._set_file_over(False)
            e.ignore()

    def dragLeaveEvent(self, e):
        self._file_urls = []
        self._set_file_over(False)
        self._set_over(False)
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        urls = _local_file_urls(e.mimeData())
        if not urls:
            urls = self._file_urls or []
        self._file_urls = []
        self._set_file_over(False)
        if urls:
            e.acceptProposedAction()
            self._set_over(False)
            self.files_dropped.emit(list(urls))
            return
        data = self._payload(e.mimeData())
        path = str((data or {}).get("path") or "")
        if self.gid is None or not path:
            self._set_over(False)
            e.ignore()
            return
        e.acceptProposedAction()
        gid = str(self.gid)
        src_gid = str((data or {}).get("gid") or "")
        idx = int(self._insert_i) if src_gid == gid else 0
        self._set_over(False)
        self.drop_requested.emit(gid, path, idx)

    def _refresh_state(self, e):
        data = self._payload(e.mimeData())
        path = str((data or {}).get("path") or "")
        gid = str((data or {}).get("gid") or "")
        same = bool(gid) and gid == self.gid
        self._src_path = path
        self._src_gid = gid
        if same:
            self._insert_i = self._insert_index(e.pos())
            self._marker = "between"
        else:
            self._insert_i = 0
            self._marker = "head"
        if not self._over:
            self._over = True
            self.update()
        else:
            self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        try:
            accent = tk("accent")
        except Exception:
            accent = "#3a8ee0"
        if not self._over or self._marker is None:
            p.end()
            return
        pen = QPen(QColor(accent), 2.0)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        w = self.width()
        if self._marker == "head":
            y = 6
            head = self._kb_head
            if head is not None:
                try:
                    y = int(head.geometry().bottom()) + 3
                except Exception:
                    pass
            p.drawLine(QPointF(2, y), QPointF(max(4, w - 2), y))
        else:
            rects = self._flow_rects()
            i = max(0, min(int(self._insert_i), len(rects)))
            if rects:
                if i < len(rects):
                    r = rects[i][1]
                    x = int(r.left()) - 3
                    y1 = int(r.top())
                    y2 = int(r.bottom())
                else:
                    r = rects[-1][1]
                    x = int(r.right()) + 3
                    y1 = int(r.top())
                    y2 = int(r.bottom())
                if x < 2:
                    x = 2
                if x > w - 2:
                    x = max(2, w - 3)
                p.drawLine(QPointF(x, y1), QPointF(x, y2))
        p.end()


class _ShotPreview(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pm = QPixmap()
        self.setMinimumHeight(120)
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setObjectName("GameAssistPreview")
        self.unsetCursor()
        self.setText("")

    def set_pixmap(self, pm: QPixmap):
        self._pm = QPixmap(pm) if pm is not None else QPixmap()
        self.update()

    def has_shot(self) -> bool:
        return not self._pm.isNull()

    def pixmap_copy(self) -> QPixmap:
        return QPixmap(self._pm)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._pm.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = self.rect().adjusted(1, 1, -1, -1)
        scaled = self._pm.scaled(r.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = r.x() + (r.width() - scaled.width()) // 2
        y = r.y() + (r.height() - scaled.height()) // 2
        p.drawPixmap(x, y, scaled)
        p.end()


_FLOW_STATE_IDLE = "idle"
_FLOW_STATE_BUSY = "busy"
_FLOW_STATE_OK = "ok"
_FLOW_STATE_FAIL = "fail"

# 交互卡片里的每一步（按新流程顺序：截图 → 本地OCR → 检索 → API 作答）
_INTERACTION_STEPS = (
    ("shot",       "截图 · 截取画面"),
    ("read",       "识读 · 本地 OCR"),
    ("retrieve",   "检索 · 知识库匹配"),
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


class PageGameAssist(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._prefs_cb = None
        self._loading = False
        self._state = "idle"
        self._sources = []
        self._kb_cards = []
        self._kb_sections = []
        self._groups = []
        self._gid_seq = 0
        self._index = {"sources": [], "files": 0, "chunks": []}
        self._answers = []
        self._answer_i = 0
        self._worker = None
        self._recognize_worker = None
        self._detect_worker = None
        self._capture_starter = None
        self._overlay = None
        self._win_hidden = False
        self._main_win = None
        self._theme_titles = []
        self._func_cards = []
        self._build_ui()
        self._bind_theme()
        self._load_index_silent()
        self._refresh_kb_status()
        self._show_answer()

    def minimumSizeHint(self):
        return QSize(0, 0)

    def hasHeightForWidth(self):
        """知识库卡片区的 FlowLayout 是 height-for-width 布局，会把整页（进而整个
        主窗口）的高度变成由内容高度决定：切到本页后第一次拖小窗口会被卡在
        内容自然高（~1022），直到布局重新落位。这里主动取消 height-for-width，
        让窗口高度只由最小高度约束决定（KB 区有滚动条，不需要靠它撑高）。"""
        return False

    def sizeHint(self):
        """页面内容偏高（KB 区在窄宽度下算出的自然高 ~480，整页 ~930），
        会把窗口尺寸提示顶到 1022，导致切到本页后第一次拖小窗口被卡在页高。
        这里把 sizeHint 高度封顶到窗口最小高度之内（与其他页同一做法，
        见 page_voice_input 的 sizeHint 固定值）。"""
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
                log.exception("游戏助手偏好回写失败")

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
        self._style_preview()
        self._style_result_edit()
        self._style_flow_status()
        self._style_status(self.lbl_api, self.lbl_api.text(), self._api_ok)
        self._style_status(self.lbl_kb, self.lbl_kb.text(), True)
        for row in getattr(self, "_step_rows", {}).values():
            try:
                row.refresh()
            except Exception:
                pass
        self.lbl_ans_meta.setStyleSheet(
            f"color:{tk('text_mut')}; font-size:12px; background:transparent; border:none;"
        )
        self._style_answer_edit()
        for sec in getattr(self, "_kb_sections", []):
            try:
                self._style_kb_section(sec)
            except Exception:
                pass
            try:
                if not sec.get("uncat"):
                    sec["rename_btn"].setIcon(_rename_icon(tk("text_mut")))
                    sec["del_btn"].setIcon(_trash_icon("#ef4444", _GROUP_DEL_ICON_PX))
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
        lv.addWidget(self._build_shot_card(), 25)
        lv.addWidget(self._build_recognition_card(), 22)
        lv.addWidget(self._build_answer_card(), 53)

        right = QWidget()
        right.setObjectName("GameAssistRight")
        right.setAttribute(Qt.WA_StyledBackground, True)
        right.setStyleSheet("#GameAssistRight{background:transparent;border:none;}")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)
        rv.setAlignment(Qt.AlignTop)
        api_card = self._build_api_card()
        api_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        rv.addWidget(api_card, 0)
        flow = self._build_interaction_card()
        flow.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        rv.addWidget(flow, 0)
        rv.addWidget(self._build_kb_card(), 1)

        split.addWidget(left, 1)
        split.addWidget(right, 1)
        root.addLayout(split, 1)

    def _build_kb_card(self) -> QFrame:
        card = make_card("CardGameAssistKb")
        self._func_cards.append(card)
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setSpacing(6)
        self._theme_titles.append(install_card_title(card, box, "知识库"))

        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_add_folder = apply_mini_button(QPushButton("添加文件夹"))
        self.btn_add_folder.setToolTip(
            "把文件夹里的文本文件逐个加入知识库，每个文件一张卡，放进「未分类」；\n"
            "文件会复制到程序的知识库目录统一管理，同名时确认后覆盖。\n"
            "「未分类」不参与回答；勾选分类或把卡拖进勾选分类后即自动参与"
        )
        self.btn_add_file = apply_mini_button(QPushButton("添加文件"))
        self.btn_add_file.setToolTip(
            "选择的文件会复制到程序的知识库目录统一管理，放进「未分类」；\n"
            "「未分类」不参与回答；勾选分类或把卡拖进勾选分类后即自动参与"
        )
        self.btn_new_group = apply_mini_button(QPushButton("新建分类"))
        self.btn_new_group.setToolTip("新建一个分隔线分类，可改名；勾选后才参与回答")
        self.btn_add_folder.clicked.connect(self._add_folder)
        self.btn_add_file.clicked.connect(self._add_files)
        self.btn_new_group.clicked.connect(self._new_group)
        row.addWidget(self.btn_add_folder)
        row.addWidget(self.btn_add_file)
        row.addStretch(1)
        row.addWidget(self.btn_new_group)
        box.addLayout(row)

        self.kb_host = QWidget()
        self.kb_host.setObjectName("GameAssistKbHost")
        self.kb_host.setAttribute(Qt.WA_StyledBackground, True)
        self.kb_host.setStyleSheet("#GameAssistKbHost{background:transparent;border:none;}")
        self.kb_host.setAcceptDrops(True)
        self._kb_drop_host_filter = _ExternalFileDropFilter(
            self._kb_drop_files, self.kb_host, hover=self._kb_set_file_hover
        )
        self.kb_host.installEventFilter(self._kb_drop_host_filter)
        self.kb_vbox = QVBoxLayout(self.kb_host)
        self.kb_vbox.setContentsMargins(0, 0, 0, 0)
        # 分类区之间不留独立间隙：竖向留白计入上方分类底部（拖放命中区更顺）
        self.kb_vbox.setSpacing(0)
        self.kb_scroll = QScrollArea()
        self.kb_scroll.setObjectName("GameAssistKbScroll")
        self.kb_scroll.setWidgetResizable(True)
        self.kb_scroll.setFrameShape(QFrame.NoFrame)
        self.kb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 垂直：保留滚轮/拖动滚动，但隐藏滚动条（知识库区希望干净整片）
        self.kb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.kb_scroll.setWidget(self.kb_host)
        self.kb_scroll.setStyleSheet(
            "QScrollArea#GameAssistKbScroll{background:transparent;border:none;}"
            "QScrollArea#GameAssistKbScroll > QWidget > QWidget{background:transparent;}"
        )
        try:
            self.kb_scroll.viewport().setAutoFillBackground(False)
            self.kb_scroll.viewport().setStyleSheet("background:transparent;")
        except Exception:
            pass
        # 滚动视口本身也兜底接收文件拖放（避开 QLineEdit 等会截走拖放的控件）
        vp = self.kb_scroll.viewport()
        vp.setAcceptDrops(True)
        self._kb_drop_viewport_filter = _ExternalFileDropFilter(
            self._kb_drop_files, vp, hover=self._kb_set_file_hover
        )
        vp.installEventFilter(self._kb_drop_viewport_filter)
        box.addWidget(self.kb_scroll, 1)

        self.lbl_kb = QLabel("还没有知识库")
        self.lbl_kb.setWordWrap(True)
        self.lbl_kb.setStyleSheet(TEXT_STYLE_T)
        box.addWidget(self.lbl_kb)
        # 整个「知识库」卡都可接 OS 文件拖入（含标题按钮行/状态行区域）
        card.setAcceptDrops(True)
        self._kb_drop_card_filter = _ExternalFileDropFilter(
            self._kb_drop_files, card, hover=self._kb_set_file_hover
        )
        card.installEventFilter(self._kb_drop_card_filter)
        # 整卡拖放高亮浮层（置顶、鼠标穿透）
        self._kb_hover_n = 0
        self._kb_drop_overlay = _KbFileDropOverlay(card)
        return card

    def _build_api_card(self) -> QFrame:
        card = make_card("CardGameAssistApi")
        self._func_cards.append(card)
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setSpacing(6)
        self._theme_titles.append(install_card_title(card, box, "大模型 API"))

        self._api_ok = False
        self.lbl_api = QLabel("未检测。填好 服务地址 / API 密钥 后点「检测」，会自动拉取该服务商支持的模型。")
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
            "服务地址：点右边下拉选中文服务名，地址会自动填好。\n"
            "DeepSeek 深度求索 / OpenAI / Kimi 月之暗面 / 智谱 GLM 清言\n"
            "通义千问（阿里百炼）/ 豆包（火山方舟）/ 腾讯混元\n"
            "Ollama（本机）/ LM Studio（本机）\n"
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
        self.edit_api_key.setPlaceholderText("sk-…")
        self.edit_api_key.setEchoMode(QLineEdit.Password)
        self.edit_api_key.setToolTip(
            "填模型提供方的 API Key，保存在本机 user.txt。\n"
            "部分自建/本地兼容服务可以留空。"
        )
        self.edit_api_key.editingFinished.connect(self._api_field_edited)
        self.edit_api_key.textEdited.connect(self._on_base_or_key_edited)
        row_key.addWidget(self.edit_api_key, 1)
        box.addLayout(row_key)

        row_model = QHBoxLayout()
        row_model.setSpacing(8)
        lbl_m = QLabel("模型")
        lbl_m.setObjectName("CalcFieldLabel")
        lbl_m.setStyleSheet("background:transparent;")
        row_model.addWidget(lbl_m)
        self.combo_model = QComboBox()
        self.combo_model.setObjectName("GameAssistModelCombo")
        self.combo_model.setEditable(True)
        self.combo_model.setInsertPolicy(QComboBox.NoInsert)
        self.combo_model.setMaxVisibleItems(12)
        self.combo_model.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLength)
        self.combo_model.setToolTip(
            "点「检测」会从服务商拉取它支持的模型列表，选一个即可。\n"
            "也可以直接手填模型名。"
        )
        try:
            self.combo_model.lineEdit().editingFinished.connect(self._api_field_edited)
        except Exception:
            pass
        row_model.addWidget(self.combo_model, 1)
        self.btn_detect = apply_medium_button(QPushButton("检测"))
        self.btn_detect.setToolTip("用上方的 地址 / 密钥 连接服务商，拉取可用的模型列表")
        self.btn_detect.clicked.connect(self._detect_api)
        row_model.addWidget(self.btn_detect)
        self.btn_clear_ctx = apply_mini_button(QPushButton("清空"))
        self.btn_clear_ctx.setToolTip(
            "清空当前这轮：截图预览、识别文字、回答与流程状态。\n"
            "服务地址 / API 密钥 / 模型 和知识库会保留。"
        )
        self.btn_clear_ctx.clicked.connect(self._clear_context)
        row_model.addWidget(self.btn_clear_ctx)
        self.btn_disconnect = apply_mini_button(QPushButton("断连"))
        self.btn_disconnect.setToolTip(
            "断开当前服务商连接：清掉拉来的模型列表，回到未连接状态。\n"
            "需要用时再点「检测」重新拉取。服务地址 / API 密钥会保留。"
        )
        self.btn_disconnect.clicked.connect(self._disconnect_api)
        row_model.addWidget(self.btn_disconnect)
        box.addLayout(row_model)

        box.addWidget(self.lbl_api)
        return card

    def _build_interaction_card(self) -> QFrame:
        """交互卡片：状态放第一排，下方按新流程列每一步。工作中状态黄色 + 慢节奏明暗闪动。"""
        card = make_card("CardGameAssistFlow")
        self._func_cards.append(card)
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setSpacing(4)
        self._theme_titles.append(install_card_title(card, box, "交互"))

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

        # 第二排：当前状态提示（工作中黄色 + 慢节奏明暗闪动）
        self.lbl_flow_status = QLabel("等待操作")
        self.lbl_flow_status.setObjectName("GameAssistFlowStatus")
        self.lbl_flow_status.setWordWrap(True)
        self.lbl_flow_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
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
        box.addWidget(self.lbl_flow_status)
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

    def _build_shot_card(self) -> QFrame:
        card = make_card("CardGameAssistShot")
        self._func_cards.append(card)
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setSpacing(6)
        self._theme_titles.append(install_card_title(card, box, "截图"))

        self.preview = _ShotPreview()
        self._style_preview()
        box.addWidget(self.preview, 1)
        return card

    def _build_recognition_card(self) -> QFrame:
        """识别结果：截图识别出的文字，可直接编辑，点「开工」再作答。"""
        card = make_card("CardGameAssistResult")
        self._func_cards.append(card)
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setSpacing(6)
        self._theme_titles.append(install_card_title(card, box, "识别结果"))

        self.edit_result = QPlainTextEdit()
        self.edit_result.setObjectName("GameAssistResultEdit")
        self.edit_result.setPlaceholderText("")
        self.edit_result.setTabChangesFocus(True)
        self.edit_result.setMaximumBlockCount(400)
        self._style_result_edit()
        box.addWidget(self.edit_result, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.lbl_result_hint = QLabel("修改后点「开工」以最新文字作答")
        self.lbl_result_hint.setStyleSheet(TEXT_STYLE_T)
        self.btn_stop_ocr = apply_mini_button(QPushButton("停止OCR"))
        self.btn_stop_ocr.setToolTip("停止正在进行的截图文字识别")
        self.btn_stop_ocr.setEnabled(False)
        self.btn_stop_ocr.clicked.connect(self._stop_ocr_recognize)
        self.btn_work = apply_mini_button(QPushButton("开工"))
        self.btn_work.clicked.connect(self._start_ask)
        row.addWidget(self.lbl_result_hint, 1)
        row.addWidget(self.btn_stop_ocr)
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
            f"border:none;padding:0;font-size:13px;}}"
        )

    def _build_answer_card(self) -> QFrame:
        card = make_card("CardGameAssistAns")
        self._func_cards.append(card)
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setSpacing(6)
        self._theme_titles.append(install_card_title(card, box, "回答"))

        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.btn_prev = apply_mini_button(QPushButton("上一条"))
        self.btn_next = apply_mini_button(QPushButton("下一条"))
        self.btn_prev.clicked.connect(lambda: self._step_answer(-1))
        self.btn_next.clicked.connect(lambda: self._step_answer(1))
        self.lbl_ans_idx = QLabel("0 / 0")
        self.lbl_ans_idx.setStyleSheet(TEXT_STYLE_T)
        self.lbl_ans_title = QLabel("")
        self.lbl_ans_title.setStyleSheet(TEXT_STYLE_T)
        self.lbl_ans_title.setWordWrap(True)
        nav.addWidget(self.btn_prev)
        nav.addWidget(self.lbl_ans_idx)
        nav.addWidget(self.btn_next)
        nav.addWidget(self.lbl_ans_title, 1)
        box.addLayout(nav)

        self.lbl_answer = QLabel("")
        self.lbl_answer.setObjectName("GameAssistAnswer")
        self.lbl_answer.setWordWrap(True)
        self.lbl_answer.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.lbl_answer.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_answer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.ans_scroll = QScrollArea()
        self.ans_scroll.setObjectName("GameAssistAnsScroll")
        self.ans_scroll.setWidgetResizable(True)
        self.ans_scroll.setFrameShape(QFrame.NoFrame)
        self.ans_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.ans_scroll.setWidget(self.lbl_answer)
        self.ans_scroll.setStyleSheet(
            "QScrollArea#GameAssistAnsScroll{background:transparent;border:none;}"
            "QScrollArea#GameAssistAnsScroll > QWidget > QWidget{background:transparent;}"
        )
        try:
            self.ans_scroll.viewport().setAutoFillBackground(False)
            self.ans_scroll.viewport().setStyleSheet("background:transparent;")
        except Exception:
            pass
        box.addWidget(self.ans_scroll, 1)

        self.lbl_ans_meta = QLabel("")
        self.lbl_ans_meta.setWordWrap(True)
        box.addWidget(self.lbl_ans_meta)
        return card

    def _style_preview(self):
        border = tk("border")
        mut = tk("text_mut")
        self.preview.setStyleSheet(
            f"QLabel#GameAssistPreview{{background:transparent; border:1px dashed {border};"
            f" color:{mut}; font-size:12px; border-radius:6px;}}"
        )

    def _style_answer_edit(self):
        lbl = getattr(self, "lbl_answer", None)
        if lbl is None:
            return
        empty = not (lbl.property("hasAnswer") or False)
        color = tk("text_mut") if empty else tk("text")
        lbl.setStyleSheet(
            f"QLabel#GameAssistAnswer{{background:transparent;border:none;"
            f"color:{color}; font-size:13px; padding:0;}}"
        )

    def _set_answer_body(self, text: str, *, placeholder: bool = False):
        lbl = getattr(self, "lbl_answer", None)
        if lbl is None:
            return
        if placeholder or not (text or "").strip():
            lbl.setProperty("hasAnswer", False)
            lbl.setText("截图之后，答案会出现在这里。多条答案用上一条 / 下一条切换。")
        else:
            lbl.setProperty("hasAnswer", True)
            lbl.setText(text)
        self._style_answer_edit()

    def _style_status(self, lbl: QLabel, text: str, ok: bool):
        color = "#16a34a" if ok else tk("text_mut")
        lbl.setText(text)
        lbl.setStyleSheet(
            f"color:{color}; font-size:12px; background:transparent; border:none;"
        )

    # ── 知识库 · 分类（分隔线） ──────────────────────────────
    def _next_kb_color(self) -> str:
        used = {str(s.get("color") or "").lower() for s in self._sources}
        for c in _KB_PALETTE:
            if c.lower() not in used:
                return c
        return _KB_PALETTE[len(self._sources) % len(_KB_PALETTE)]

    def _group_by_gid(self, gid: str):
        for g in self._groups:
            if g.get("gid") == gid:
                return g
        return None

    def _next_gid(self) -> str:
        self._gid_seq += 1
        return f"g{int(time.time())}_{self._gid_seq}"

    def _group_card_count(self, gid: str) -> int:
        n = 0
        for s in self._sources:
            if source_group(s) == gid:
                n += 1
        return n

    def _new_group(self):
        text, ok = QInputDialog.getText(
            self, "新建分类", "分类名称（勾选后参与大模型回答）：",
            QLineEdit.Normal, "",
        )
        if not ok:
            return
        name = (text or "").strip()[:24] or "新分类"
        self._groups.append({"gid": self._next_gid(), "title": name, "active": False})
        self._refresh_source_list()
        self._refresh_kb_status()
        self._dirty()

    def _delete_group(self, gid: str):
        meta = self._group_by_gid(gid)
        if meta is None:
            return
        n = self._group_card_count(gid)
        title = str(meta.get("title") or "该分类")
        if n > 0:
            r = QMessageBox.question(
                self, "删除分类",
                f"分类「{title}」下有 {n} 张知识卡。\n"
                f"删除后这些卡片会挪到「{UNCAT_TITLE}」存档（不参与回答）。\n确定删除吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if r != QMessageBox.Yes:
                return
        for s in self._sources:
            if source_group(s) == gid:
                s["group"] = GID_UNCAT
        self._groups = [g for g in self._groups if g.get("gid") != gid]
        self._refresh_source_list()
        self._rebuild_index()
        self._refresh_kb_status()
        self._dirty()

    def _on_group_active(self, gid: str, on: bool):
        meta = self._group_by_gid(gid)
        if meta is None:
            return
        if bool(meta.get("active")) == bool(on):
            return
        meta["active"] = bool(on)
        # 勾选范围变了 → 自动重建索引（只索引勾选分类），无需手动按钮
        self._rebuild_index()
        self._dirty()

    def _on_kb_drop(self, gid: str, path: str, index: int):
        """拖拽知识卡落位：index 为卡序号。
        同分类按 index 在两张卡之间重排；跨分类固定插到目标分类开头。"""
        path = (path or "").strip()
        if not path:
            return
        key = os.path.normcase(os.path.normpath(path))
        rec = None
        for s in self._sources:
            if os.path.normcase(os.path.normpath(s.get("path") or "")) == key:
                rec = s
                break
        if rec is None:
            return
        cur = source_group(rec)
        if cur != gid:
            rec["group"] = gid
            self._reorder_rec(rec, gid, 0)
        else:
            seq = [s for s in self._sources if source_group(s) == gid]
            try:
                old_i = seq.index(rec)
            except ValueError:
                return
            i = int(index)
            i = max(0, min(i, len(seq)))
            # index 是包含自身时的插入位：去掉自身后折算到“其它卡”之间
            eff = i if i <= old_i else i - 1
            if eff == old_i:
                return
            self._reorder_rec(rec, gid, eff)
        self._refresh_source_list()
        self._rebuild_index()
        self._refresh_kb_status()
        self._dirty()

    def _reorder_rec(self, rec: dict, gid: str, target: int):
        """把 rec 放回 self._sources，使它在分类 gid 的其它卡之间排到第 target 位。"""
        others = [s for s in self._sources if s is not rec]
        target = max(0, min(int(target), sum(1 for s in others if source_group(s) == gid)))
        new = []
        inserted = False
        placed = 0
        for s in others:
            if not inserted and source_group(s) == gid and placed >= target:
                new.append(rec)
                inserted = True
            new.append(s)
            if source_group(s) == gid:
                placed += 1
        if not inserted:
            new.append(rec)
        self._sources = new

    def _on_group_rename(self, gid: str, edit: QLineEdit):
        meta = self._group_by_gid(gid)
        if meta is None:
            return
        title = (edit.text() or "").strip()[:24]
        if not title:
            title = str(meta.get("title") or "新分类")
        meta["title"] = title
        try:
            edit.blockSignals(True)
            edit.setText(title)
            edit.blockSignals(False)
        except Exception:
            pass
        self._dirty()

    def _style_kb_section(self, sec: dict):
        head = sec.get("head")
        edit = sec.get("edit")
        count = sec.get("count")
        chk = sec.get("chk")
        try:
            border = tk("border")
            text = tk("text")
            mut = tk("text_mut")
            accent = tk("accent")
            panel_2 = tk("panel_2")
        except Exception:
            return
        if head is not None:
            head.setStyleSheet(
                "#GameAssistKbGroupHead{{background:transparent;border:none;"
                "border-bottom:1px solid {border};}}".format(border=border)
            )
        if edit is not None:
            ro = sec.get("uncat") or False
            fg = mut if ro else text
            qss = (
                "QLineEdit#GameAssistKbGroupTitle{{background:transparent;"
                "border:none;color:{fg};font-size:13px;font-weight:600;"
                "padding:0 2px;border-radius:3px;}}"
                "QLineEdit#GameAssistKbGroupTitle:hover{{background:transparent;"
                "border:none;border-bottom:1px dashed {border};}}"
                "QLineEdit#GameAssistKbGroupTitle:focus{{background:{panel_2};"
                "border:1px solid {accent};border-radius:4px;}}"
            ).format(fg=fg, border=border, panel_2=panel_2, accent=accent)
            edit.setStyleSheet(qss)
        if count is not None:
            count.setStyleSheet(
                "color:{mut};font-size:11px;background:transparent;"
                "border:none;".format(mut=mut)
            )
        if chk is not None:
            chk.setStyleSheet(
                "QCheckBox{{background:transparent;color:{text};"
                "spacing:0px;padding:0px;margin:0px;}}".format(text=text)
            )
        rn = sec.get("rename_btn")
        if rn is not None:
            rn.setStyleSheet(_kb_group_icon_btn_qss(danger=False))
        dl = sec.get("del_btn")
        if dl is not None:
            dl.setStyleSheet(_kb_group_icon_btn_qss(danger=True))

    def _make_kb_section(self, gid: str, meta: dict, recs: list, uncat: bool) -> dict:
        sec = {"gid": gid, "uncat": uncat, "cards": [], "widget": None}
        host = _KbDropBox(gid)
        host.setObjectName("GameAssistKbGroup")
        host.setStyleSheet("#GameAssistKbGroup{background:transparent;border:none;}")
        host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        host.drop_requested.connect(self._on_kb_drop)
        host.files_dropped.connect(self._kb_drop_files)
        host.file_hover.connect(self._kb_set_file_hover)
        outer = QVBoxLayout(host)
        outer.setContentsMargins(0, 0, 0, _KB_BAND_GAP)
        outer.setSpacing(6)

        head = QWidget()
        head.setObjectName("GameAssistKbGroupHead")
        head.setAttribute(Qt.WA_StyledBackground, True)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 2, 0, 3)
        hl.setSpacing(4)
        sec["head"] = head

        chk = None
        if not uncat:
            chk = QCheckBox("")
            chk.setToolTip("勾选后，该分类的内容参与大模型回答")
            chk.setChecked(bool(meta and meta.get("active")))
            chk.toggled.connect(
                lambda on, g=gid: self._on_group_active(g, bool(on))
            )
            hl.addWidget(chk)
        sec["chk"] = chk

        title = UNCAT_TITLE if uncat else str((meta or {}).get("title") or "新分类")
        edit = QLineEdit(title)
        edit.setObjectName("GameAssistKbGroupTitle")
        if uncat:
            edit.setReadOnly(True)
            edit.setToolTip(
                f"「{UNCAT_TITLE}」：新添加/老内容都归这里，不参与大模型回答"
            )
        else:
            edit.setToolTip("双击或点右侧 ✎ 改名")
            edit.setCursor(Qt.IBeamCursor)
        edit.setFocusPolicy(Qt.ClickFocus)
        edit.setAcceptDrops(False)
        edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        edit.editingFinished.connect(
            lambda e=edit, g=gid: self._on_group_rename(g, e)
        )
        hl.addWidget(edit)
        sec["edit"] = edit

        count = QLabel(f"{len(recs)} 张")
        if uncat:
            count.setText(f"{len(recs)} 张 · 不参与回答")
            count.setToolTip("「未分类」只存档，不参与大模型回答")
        else:
            count.setToolTip("该分类下的知识卡数量")
        sec["count"] = count
        hl.addWidget(count)

        btn_rename = None
        btn_del = None
        if not uncat:
            btn_rename = QPushButton()
            btn_rename.setObjectName("GameAssistGroupRename")
            btn_rename.setCursor(Qt.PointingHandCursor)
            btn_rename.setFocusPolicy(Qt.NoFocus)
            btn_rename.setFlat(True)
            btn_rename.setFixedSize(_KB_ICON_BTN, _KB_ICON_BTN)
            btn_rename.setIconSize(QSize(_GROUP_RENAME_ICON_PX, _GROUP_RENAME_ICON_PX))
            btn_rename.setToolTip("改名")
            btn_rename.clicked.connect(
                lambda: (edit.setFocus(), edit.selectAll())
            )
            btn_rename.setStyleSheet(_kb_group_icon_btn_qss(danger=False))
            hl.addWidget(btn_rename)

            btn_del = QPushButton()
            btn_del.setObjectName("GameAssistGroupDel")
            btn_del.setCursor(Qt.PointingHandCursor)
            btn_del.setFocusPolicy(Qt.NoFocus)
            btn_del.setFlat(True)
            btn_del.setFixedSize(_KB_ICON_BTN, _KB_ICON_BTN)
            btn_del.setIconSize(QSize(_GROUP_DEL_ICON_PX, _GROUP_DEL_ICON_PX))
            btn_del.setToolTip("删除分类（卡片移入未分类）")
            btn_del.clicked.connect(lambda g=gid: self._delete_group(g))
            btn_del.setStyleSheet(_kb_group_icon_btn_qss(danger=True))
            hl.addWidget(btn_del)
        sec["rename_btn"] = btn_rename
        sec["del_btn"] = btn_del
        try:
            if not uncat:
                btn_rename.setIcon(_rename_icon(tk("text_mut")))
                btn_del.setIcon(_trash_icon("#ef4444", _GROUP_DEL_ICON_PX))
        except Exception:
            pass

        outer.addWidget(head)

        if recs:
            flow_host = QWidget()
            flow_host.setObjectName("GameAssistKbFlow")
            flow_host.setAttribute(Qt.WA_StyledBackground, True)
            flow_host.setStyleSheet("#GameAssistKbFlow{background:transparent;border:none;}")
            flow_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            flow = FlowLayout(flow_host, margin=0, h_spacing=6, v_spacing=6)
            for rec in recs:
                if not rec.get("color"):
                    rec["color"] = self._next_kb_color()
                card = _KbCard(rec)
                card.remove_requested.connect(self._remove_source_path)
                flow.addWidget(card)
                sec["cards"].append(card)
            outer.addWidget(flow_host)
        else:
            # 空分类也要保留“一张卡高”的放置区，方便直接拖进
            if uncat:
                hint = "暂无内容 · 新添加的文件夹/文件会落在这里（不参与回答）"
            else:
                hint = "还没有卡片 · 把卡片拖到这里即放入本分类"
            flow_host = QWidget()
            flow_host.setObjectName("GameAssistKbFlow")
            flow_host.setAttribute(Qt.WA_StyledBackground, True)
            flow_host.setStyleSheet("#GameAssistKbFlow{background:transparent;border:none;}")
            flow_host.setMinimumHeight(_KB_CARD_H)
            flow_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            vb = QVBoxLayout(flow_host)
            vb.setContentsMargins(0, 0, 0, 0)
            vb.addStretch(1)
            empty = QLabel(hint)
            empty.setAlignment(Qt.AlignCenter)
            empty.setWordWrap(True)
            empty.setStyleSheet(
                f"color:{tk('text_dim')};font-size:11px;"
                "background:transparent;border:none;"
            )
            vb.addWidget(empty)
            vb.addStretch(1)
            outer.addWidget(flow_host)

        # 供放置目标计算插入线：分隔线行 y + 当前卡显示顺序
        try:
            host._kb_head = head
            host._kb_cards = list(sec["cards"])
        except Exception:
            pass
        self._style_kb_section(sec)
        sec["widget"] = host
        return sec

    def _refresh_source_list(self):
        for sec in list(self._kb_sections):
            w = sec.get("widget")
            if w is None:
                continue
            try:
                self.kb_vbox.removeWidget(w)
            except Exception:
                pass
            w.setParent(None)
            w.deleteLater()
        # 清掉上一次收尾的 stretch，避免越堆越多
        for i in reversed(range(self.kb_vbox.count())):
            item = self.kb_vbox.itemAt(i)
            if item is not None and item.spacerItem():
                self.kb_vbox.takeAt(i)
        self._kb_sections = []
        self._kb_cards = []

        by_group = {}
        known = {g.get("gid") for g in self._groups}
        for rec in self._sources:
            g = source_group(rec)
            if g != GID_UNCAT and g not in known:
                g = GID_UNCAT
                rec["group"] = GID_UNCAT
            by_group.setdefault(g, []).append(rec)

        order = [g.get("gid") for g in self._groups]
        for g in list(by_group):
            if g not in order and g != GID_UNCAT:
                order.append(g)
        if GID_UNCAT not in order:
            order.append(GID_UNCAT)

        for gid in order:
            meta = self._group_by_gid(gid)
            recs = by_group.get(gid) or []
            uncat = gid == GID_UNCAT
            sec = self._make_kb_section(gid, meta, recs, uncat)
            self._kb_sections.append(sec)
            for card in sec["cards"]:
                self._kb_cards.append(card)
            self.kb_vbox.addWidget(sec["widget"], 0, Qt.AlignTop)
        # 底部 stretch：把分类区整体顶到最上、纵向连续，不掺入空隙
        self.kb_vbox.addStretch(1)

    def _source_keys(self) -> set:
        keys = set()
        for s in self._sources:
            p = s.get("path") or ""
            if p:
                keys.add(os.path.normcase(os.path.normpath(p)))
            o = s.get("origin") or ""
            if o:
                keys.add(os.path.normcase(os.path.normpath(o)))
        return keys

    def _kb_conflict(self, fp: str, have: set) -> bool:
        """该文件按原名落到知识库目录时，是否与现有来源同名。"""
        fp = os.path.normpath(fp)
        if os.path.normcase(fp) in have:
            return False
        tkey = os.path.normcase(kb_target_for(fp))
        for s in self._sources:
            for k in (s.get("path") or "", s.get("origin") or ""):
                if os.path.normcase(os.path.normpath(k)) == tkey:
                    return True
        return False

    def _add_source_file(self, fp: str, have: set, overwrite_conflict: bool = False) -> bool:
        """把单个文件加入知识库：复制托管 + 同名覆盖 + 记录源文件。

        供「添加文件」和「添加文件夹」共用。同名时按调用方已确认的
        overwrite_conflict 处理，不再逐个弹窗。返回是否真正新增/更新。
        """
        fp = os.path.normpath(fp)
        if os.path.normcase(fp) in have:
            return False
        target = kb_target_for(fp)
        tkey = os.path.normcase(target)
        hit = None
        for s in self._sources:
            for k in (s.get("path") or "", s.get("origin") or ""):
                if os.path.normcase(os.path.normpath(k)) == tkey:
                    hit = s
                    break
            if hit is not None:
                break
        if hit is not None:
            if not overwrite_conflict:
                return False
            rec = dict(hit)
            rec["kind"] = "file"
            rec["path"] = target
            rec["origin"] = fp
            rec["group"] = GID_UNCAT
            if not overwrite_kb_copy(fp, target):
                return False
            record_source_stamp(rec, fp)
            rec["updated_at"] = time.time()
            self._sources[self._sources.index(hit)] = rec
            have.add(tkey)
            have.add(os.path.normcase(os.path.normpath(fp)))
            return True
        rec = ensure_managed_file_source({
            "kind": "file",
            "path": fp,
            "color": self._next_kb_color(),
        })
        rec["group"] = GID_UNCAT
        key = os.path.normcase(os.path.normpath(rec.get("path") or ""))
        if not key or key in have:
            return False
        self._sources.append(rec)
        have.add(key)
        o = rec.get("origin") or ""
        if o:
            have.add(os.path.normcase(os.path.normpath(o)))
        return True

    def _confirm_kb_overwrite(self, n: int) -> bool:
        """一次性确认整批同名覆盖，避免每个文件都弹一次。"""
        if n <= 0:
            return False
        r = QMessageBox.question(
            self, "同名文件",
            f"知识库已存在 {n} 个同名文件，是否全部用所选文件覆盖？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return r == QMessageBox.Yes

    def _add_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择攻略文件夹")
        if not path:
            return
        path = os.path.normpath(path)
        files = list_folder_files(path)
        if not files:
            message_box_info(self, "添加文件夹", "该文件夹里没有可加入的文本文件。")
            return
        if len(files) > 100:
            r = QMessageBox.question(
                self, "添加文件夹",
                f"该文件夹里共有 {len(files)} 个文本文件，将逐个复制进知识库。\n是否继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if r != QMessageBox.Yes:
                return
        cands = [os.path.normpath(fp) for fp in files]
        have = self._source_keys()
        cands = [fp for fp in cands if os.path.normcase(fp) not in have]
        conflicts = [fp for fp in cands if self._kb_conflict(fp, have)]
        overwrite = self._confirm_kb_overwrite(len(conflicts))
        changed = False
        for fp in cands:
            if self._add_source_file(fp, have, overwrite_conflict=overwrite):
                changed = True
        if not changed:
            message_box_info(self, "添加文件夹", "文件夹里的文件都已存在，无需重复添加。")
            return
        self._refresh_source_list()
        self._rebuild_index()
        self._dirty()

    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择攻略文件", "", _KB_FILTER)
        if not files:
            return
        self._add_files_paths(files)

    def _kb_set_file_hover(self, on: bool):
        """OS 文件悬停知识库区：整卡虚线高亮（用计数避免移动时闪烁）。"""
        try:
            ov = self._kb_drop_overlay
        except Exception:
            ov = None
        if on:
            self._kb_hover_n = getattr(self, "_kb_hover_n", 0) + 1
            if ov is not None:
                ov.show()
            return
        self._kb_hover_n = max(0, getattr(self, "_kb_hover_n", 1) - 1)

        def _later_hide():
            if getattr(self, "_kb_hover_n", 0) <= 0:
                try:
                    self._kb_drop_overlay.hide()
                except Exception:
                    pass

        if self._kb_hover_n <= 0:
            QTimer.singleShot(180, _later_hide)

    def _kb_drop_files(self, paths):
        """从资源管理器拖进知识库区的文件：直接放进「未分类」（与「添加文件」同路径）。

        只接收单个文本类文件；拖入的是文件夹时提示用「添加文件夹」。
        """
        if not paths:
            return
        dirs = []
        files = []
        for p in paths:
            try:
                if os.path.isdir(p):
                    dirs.append(p)
                elif os.path.isfile(p):
                    files.append(p)
            except Exception:
                pass
        if dirs and not files:
            return
        if not files:
            return
        self._add_files_paths(files, drop=True)

    def _add_files_paths(self, paths, drop: bool = False):
        """把一组本地文件加入知识库（统一放「未分类」），供弹窗添加与拖放共用。

        drop=True 时对非文本扩展名静默跳过，避免窗口拖文件过来时弹窗打断。
        """
        cands = []
        for fp in paths:
            fp = os.path.normpath(fp)
            if drop and not is_supported_text_file(fp):
                continue
            cands.append(fp)
        if not cands:
            if not drop:
                message_box_info(
                    self,
                    "添加文件",
                    "没有可加入的文本文件。\n\n"
                    "支持：txt / md / markdown / html / htm / json / csv / log /\n"
                    "rst / ini / cfg / yaml / yml",
                )
            return
        have = self._source_keys()
        cands = [fp for fp in cands if os.path.normcase(fp) not in have]
        if not cands:
            if not drop:
                message_box_info(self, "添加文件", "所选文件都已存在，无需重复添加。")
            return
        conflicts = [fp for fp in cands if self._kb_conflict(fp, have)]
        overwrite = self._confirm_kb_overwrite(len(conflicts))
        added = 0
        for fp in cands:
            if self._add_source_file(fp, have, overwrite_conflict=overwrite):
                added += 1
        if not added:
            return
        self._refresh_source_list()
        self._rebuild_index()
        self._dirty()

    def _remove_source_path(self, path: str):
        """移除来源。文件来源若在知识库托管目录内，删除程序里的副本；源文件不动。"""
        key = os.path.normcase(os.path.normpath(path or ""))
        if not key:
            return
        for s in self._sources:
            if os.path.normcase(os.path.normpath(s.get("path") or "")) != key:
                continue
            if (s.get("kind") or "file") == "file":
                remove_kb_copy(s.get("path") or "")
            break
        self._sources = [
            s for s in self._sources
            if os.path.normcase(os.path.normpath(s.get("path") or "")) != key
        ]
        self._refresh_source_list()
        self._rebuild_index()
        self._dirty()

    def _active_sources(self) -> list:
        """只返回勾选分类里的来源（未分类/未勾选一律不参与索引）。"""
        gids = {g.get("gid") for g in self._groups if g.get("active")}
        return [s for s in self._sources if source_group(s) in gids]

    def _rebuild_index(self, force: bool = False):
        del force
        # 归位旧路径残留来源 + 恢复托管副本丢失的文件，再建索引
        healed = restored = 0
        try:
            healed = relocate_missing_file_sources(self._sources)
        except Exception:
            log.debug("归位旧路径来源失败", exc_info=True)
        try:
            restored = restore_missing_copies(self._sources)
        except Exception:
            log.debug("恢复缺失知识库副本失败", exc_info=True)
        if healed or restored:
            self._refresh_source_list()
            self._dirty()
        srcs = self._active_sources()
        try:
            self._index = build_index(srcs)
            save_index(self._index)
        except Exception:
            log.exception("重建知识库索引失败")
            self._index = {"sources": [dict(s) for s in srcs], "files": 0, "chunks": []}
        self._refresh_kb_status()

    def _load_index_silent(self):
        try:
            idx = load_index()
        except Exception:
            idx = {"sources": [], "files": 0, "chunks": []}
        if not self._sources and idx.get("sources"):
            self._sources = list(idx.get("sources") or [])
            self._refresh_source_list()
        self._index = idx
        chunks = idx.get("chunks") or []
        if self._sources and (not chunks or not any("src" in c for c in chunks)):
            self._rebuild_index()

    def _refresh_kb_status(self):
        """底部只显示两件事：勾了几个分类、已挂载多少段（只算勾选分类）。"""
        if not self._sources:
            self._style_status(
                self.lbl_kb, "知识库是空的。先添加文件夹或文件。", False
            )
            return
        n_chunk = len(self._effective_index().get("chunks") or [])
        active = sum(1 for g in self._groups if g.get("active"))
        total = len(self._groups)
        summary = f"勾选 {active}/{total} 分类 · 已挂载 {n_chunk} 段"
        self._style_status(self.lbl_kb, summary, n_chunk > 0)

    # ── 大模型 API 配置 / 检测 ─────────────────────────────
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
            self._style_status(
                self.lbl_api, "配置已改动，请重新点「检测」。", False
            )
        except Exception:
            pass

    def _api_config(self) -> tuple:
        base = normalize_api_base(self._api_url_for(self.combo_api_base.currentText()))
        key = (self.edit_api_key.text() or "").strip()
        model = self.combo_model.currentText().strip()
        return base, key, model

    def _detect_api(self):
        if self._state == "busy":
            message_box_info(self, "游戏助手", "上一张还在查，稍后再检测。")
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
        if not names:
            self._api_ok = True
            self._style_status(
                self.lbl_api, "已连接，但服务商没有返回任何模型。", False
            )
            self._dirty()
            return
        cb = self.combo_model
        try:
            cb.blockSignals(True)
            for n in names:
                cb.addItem(n)
            want = prev if prev in names else names[0]
            i = cb.findText(want)
            if i >= 0:
                cb.setCurrentIndex(i)
            cb.blockSignals(False)
        except Exception:
            pass
        self._api_ok = True
        self._style_status(self.lbl_api, f"已连接 · 拉取到 {len(names)} 个模型", True)
        self._dirty()

    def _on_detect_fail(self, msg: str):
        try:
            self.btn_detect.setEnabled(True)
        except Exception:
            pass
        self._api_ok = False
        self._style_status(self.lbl_api, f"未连接：{msg}", False)
        message_box_warn(self, "游戏助手", str(msg or "连接失败"))

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
        stop_qthread(w, name="game_assist_detect")
        try:
            self.btn_detect.setEnabled(True)
        except Exception:
            pass

    def _clear_context(self):
        """清空当前这轮：截图预览、识别文字、回答与流程状态；API 配置与知识库保留。"""
        if self._state == "busy":
            message_box_info(self, "游戏助手", "正在处理上一张，稍后再清空。")
            return
        self.edit_result.setPlainText("")
        self._answers = []
        self._answer_i = 0
        try:
            self.preview.set_pixmap(QPixmap())
            self.preview.setText("")
        except Exception:
            pass
        self.lbl_ans_meta.setText("")
        self._reset_flow()
        self._show_answer()
        self._flow_hint("已清空本轮内容，可截新图后开工。")

    def _disconnect_api(self):
        """断开连接：清掉模型列表并回到未连接状态；地址 / 密钥保留。"""
        if self._state == "busy":
            message_box_info(self, "游戏助手", "正在处理上一张，稍后再断开。")
            return
        self._api_ok = False
        self._clear_model_list()
        self._style_status(self.lbl_api, "已断开。点「检测」重新连接服务商并拉取模型。", False)
        self._dirty()

    def on_enter(self):
        return

    def set_capture_starter(self, fn):
        """主窗口注入：走截图工具的框选遮罩（热键同一套）。"""
        self._capture_starter = fn

    def take_quick_shot(self, pixmap: QPixmap):
        """截图工具快捷截图回调：只预览，不进编辑。"""
        self._on_captured(pixmap)

    # ── 截图 ────────────────────────────────────────────────
    def _start_capture(self):
        if self._state == "busy":
            message_box_info(self, "游戏助手", "上一张还在查，稍等再截。")
            return
        self._flow_step("shot", _FLOW_STATE_BUSY)
        fn = self._capture_starter
        if callable(fn):
            try:
                fn()
                return
            except Exception:
                log.exception("调用截图工具失败")
        ov = self._overlay
        if ov is not None:
            try:
                if ov.isVisible():
                    return
            except RuntimeError:
                self._overlay = None
        self._hide_main()
        QTimer.singleShot(180, self._open_overlay)

    def _open_overlay(self):
        from pages.page_screenshot import Overlay
        self._overlay = Overlay(self._on_captured, self._on_capture_cancel, detect_window=False)

    def _hide_main(self):
        self._main_win = self.window()
        if self._main_win is not None:
            self._main_win.hide()
            self._win_hidden = True

    def _restore_main(self):
        if self._win_hidden and self._main_win is not None:
            self._main_win.show()
            self._main_win.raise_()
            self._main_win.activateWindow()
        self._win_hidden = False

    def _on_capture_cancel(self):
        self._flow_step("shot", _FLOW_STATE_IDLE)
        self._restore_main()

    def _on_captured(self, pixmap: QPixmap):
        self._restore_main()
        if pixmap is None or pixmap.isNull():
            return
        self.preview.set_pixmap(pixmap)
        self.preview.setText("")
        self.preview.update()
        self._recognize_from_shot(pixmap)

    def _recognize_from_shot(self, pixmap: QPixmap):
        """截图后本地 OCR 识别文字 → 放进可编辑的「识别结果」；点「开工」才调大模型作答。"""
        shot_path = os.path.join(game_assist_dir(), "last_shot.png")
        try:
            pixmap.save(shot_path, "PNG")
        except Exception:
            log.debug("保存截图失败", exc_info=True)
            shot_path = ""

        self._stop_recognize_worker()
        self._stop_detect_worker()
        self._stop_worker()
        self._state = "busy"
        self._reset_flow()
        self._flow_step("shot", _FLOW_STATE_OK)
        self.edit_result.setPlainText("")
        self._answers = []
        self._show_answer()
        self.lbl_ans_meta.setText("")

        w = RecognizeWorker(shot_path, parent=self)
        w.status.connect(self._on_ask_status)
        w.step.connect(self._on_ask_step)
        w.done.connect(self._on_recognize_done)
        w.fail.connect(self._on_recognize_fail)
        self._recognize_worker = w
        w.start()
        try:
            self.btn_stop_ocr.setEnabled(True)
        except Exception:
            pass

    def _stop_ocr_recognize(self):
        """手动停止正在进行的截图识别。"""
        self._stop_recognize_worker()
        self._state = "idle"
        self._flow_step("read", _FLOW_STATE_IDLE)
        self._flow_hint("已停止识别，可手动填写后点「开工」。")
        try:
            self.btn_stop_ocr.setEnabled(False)
        except Exception:
            pass

    def _on_recognize_done(self, text: str):
        self._state = "idle"
        try:
            self.btn_stop_ocr.setEnabled(False)
        except Exception:
            pass
        self.edit_result.setPlainText(text or "")
        if text:
            self._flow_hint("已识别。可直接修改后点「开工」作答。")
            return
        try:
            from utils.ocr_util import is_local_ocr_ready
            ready = bool(is_local_ocr_ready())
        except Exception:
            ready = False
        if ready:
            self._flow_hint("未识别到文字，可手动填写后点「开工」。")
        else:
            self._flow_hint("本地 OCR 未就绪，可直接在上方填写文字后点「开工」。")

    def _on_recognize_fail(self, msg: str):
        self._state = "idle"
        try:
            self.btn_stop_ocr.setEnabled(False)
        except Exception:
            pass
        key = getattr(self, "_flow_active", None)
        if key not in self._step_rows:
            key = "read"
        self._flow_step(key, _FLOW_STATE_FAIL)
        self._flow_hint(str(msg or "识别失败"))
        message_box_warn(self, "游戏助手", str(msg or "识别失败"))

    def _stop_recognize_worker(self):
        w = self._recognize_worker
        self._recognize_worker = None
        if w is None:
            return
        try:
            w.status.disconnect(self._on_ask_status)
            w.step.disconnect(self._on_ask_step)
            w.done.disconnect(self._on_recognize_done)
            w.fail.disconnect(self._on_recognize_fail)
        except Exception:
            pass
        stop_qthread(w, name="game_assist_recognize")

    def _effective_index(self) -> dict:
        """把整份索引裁剪到「勾选参与回答的分类」；未分类不参与。"""
        gids = [g.get("gid") for g in self._groups if g.get("active")]
        return index_subset_for_groups(self._index, gids)

    def _start_ask(self):
        """开工：以当前「识别结果」里的文字（用户可改）检索知识库并调大模型 API 作答。
        识别只走本地 OCR；API 调用在「大模型」卡里配置，点「开工」直接发起。"""
        if self._state == "busy":
            message_box_info(self, "游戏助手", "还在处理上一张，稍等。")
            return
        query = self.edit_result.toPlainText().strip()
        if not query:
            message_box_warn(self, "游戏助手", "识别结果里没有文字。先截图，或直接在上方填写。")
            return
        if not self._sources:
            message_box_warn(
                self, "游戏助手",
                "知识库是空的。先添加攻略文件夹或文件"
                "（新内容进「未分类」不参与；勾选分类后再把内容拖入即可参与）。",
            )
            return
        # 索引只含勾选分类的内容，勾选/拖拽时已自动重建；此处兜底刷新
        if not (self._index.get("chunks") or []):
            self._rebuild_index()
        eff = self._effective_index()
        if not (eff.get("chunks") or []):
            message_box_warn(
                self, "游戏助手",
                "知识库里没有可参与回答的内容："
                "只有勾选分类会参与回答，「未分类」不参与。\n"
                "请先勾选内容所属的分类（或把卡拖进已勾选分类）后再开工。",
            )
            return
        self._run_ask(query)

    def _run_ask(self, query: str):
        base, key, model = self._api_config()
        self.combo_api_base.setEditText(base)
        if not model:
            self._state = "idle"
            message_box_warn(
                self, "游戏助手",
                "还没有模型。请先点「检测」从服务商拉取它支持的模型列表并选一个，"
                "也可以直接手填模型名。",
            )
            return
        eff = self._effective_index()
        if not (eff.get("chunks") or []):
            self._state = "idle"
            self._flow_hint("没有可参与回答的内容")
            message_box_warn(
                self, "游戏助手",
                "没有可参与回答的内容。请勾选内容所属的分类（「未分类」不参与）。",
            )
            return
        self._stop_worker()
        self._state = "busy"
        self._set_answer_body("", placeholder=True)
        self.lbl_ans_meta.setText("")
        w = AskWorker(base, key, model, eff, query, parent=self)
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
        self._flow_hint("完成，多条答案用上一条 / 下一条切换。")
        self._answers = list(answers or [])
        self._answer_i = 0
        self._show_answer()

    def _on_ask_fail(self, msg: str):
        self._state = "idle"
        key = getattr(self, "_flow_active", None)
        if key not in self._step_rows:
            key = "answer"
        self._flow_step(key, _FLOW_STATE_FAIL)
        self._flow_hint(str(msg or "查询失败"))
        self.lbl_ans_meta.setText("")
        message_box_warn(self, "游戏助手", str(msg or "查询失败"))

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
        stop_qthread(w, name="game_assist")

    # ── 答案切换 ────────────────────────────────────────────
    def _step_answer(self, delta: int):
        n = len(self._answers)
        if n <= 0:
            return
        self._answer_i = (self._answer_i + int(delta)) % n
        self._show_answer()

    def _show_answer(self):
        n = len(self._answers)
        has = n > 0
        self.btn_prev.setEnabled(n > 1)
        self.btn_next.setEnabled(n > 1)
        if not has:
            self.lbl_ans_idx.setText("0 / 0")
            self.lbl_ans_title.setText("")
            self._set_answer_body("", placeholder=True)
            if self._state != "busy":
                self.lbl_ans_meta.setText("")
            return
        i = max(0, min(self._answer_i, n - 1))
        rec = self._answers[i]
        self.lbl_ans_idx.setText(f"{i + 1} / {n}")
        self.lbl_ans_title.setText(rec.get("title") or "")
        self._set_answer_body(rec.get("answer") or "")
        src = (rec.get("source") or "").strip()
        self.lbl_ans_meta.setText(f"来源：{src}" if src else "")

    # ── 偏好 / 生命周期 ─────────────────────────────────────
    def export_settings(self) -> dict:
        base, key, model = self._api_config()
        return {
            "sources": list(self._sources),
            "groups": [dict(g) for g in self._groups],
            "base_url": base,
            "api_key": key,
            "model": model,
        }

    def apply_settings(self, data: dict):
        self._loading = True
        migrated = False
        try:
            data = data or {}
            base = normalize_api_base(
                str(data.get("base_url") or "") or default_api_base()
            )
            pi = self._api_preset_index(base)
            if pi >= 0:
                self.combo_api_base.setCurrentIndex(pi)
            else:
                self.combo_api_base.setCurrentIndex(-1)
            self.combo_api_base.setEditText(base)
            self.edit_api_key.setText(str(data.get("api_key") or ""))
            self._clear_model_list()
            self._set_model_text(str(data.get("model") or ""))

            # 分类（分隔线组）：未分类为内置，不存
            groups = []
            seen_gid = set()
            raw_groups = data.get("groups")
            if isinstance(raw_groups, list):
                for item in raw_groups:
                    if not isinstance(item, dict):
                        continue
                    gid = str(item.get("gid") or "").strip()
                    if not gid or gid == GID_UNCAT or gid in seen_gid:
                        continue
                    title = (str(item.get("title") or "").strip())[:24] or "新分类"
                    groups.append({
                        "gid": gid,
                        "title": title,
                        "active": bool(item.get("active")),
                    })
                    seen_gid.add(gid)
            self._groups = groups

            srcs = data.get("sources")
            if isinstance(srcs, list):
                clean = []
                for s in srcs:
                    if not isinstance(s, dict):
                        continue
                    path = str(s.get("path") or "").strip()
                    if not path:
                        continue
                    kind = "folder" if (s.get("kind") or "") == "folder" else "file"
                    color = str(s.get("color") or "").strip()
                    rec = {
                        "kind": kind,
                        "path": os.path.normpath(path),
                        "color": color,
                    }
                    grp = str(s.get("group") or "").strip()
                    rec["group"] = grp if (grp and grp in seen_gid) else GID_UNCAT
                    origin = str(s.get("origin") or "").strip()
                    if origin:
                        rec["origin"] = os.path.normpath(origin)
                    for k in ("origin_mtime", "origin_size", "updated_at"):
                        if s.get(k) is not None:
                            rec[k] = s[k]
                    # 老版本文件来源：复制进知识库目录并记录源文件
                    if kind == "file" and not rec.get("origin"):
                        new = ensure_managed_file_source(rec)
                        if new is not rec:
                            rec = new
                            migrated = True
                    clean.append(rec)
                self._sources = clean
                self._refresh_source_list()
                # 目录迁移残留的旧路径来源：归位到当前托管目录（缺文件自动从源恢复）
                try:
                    healed = relocate_missing_file_sources(self._sources)
                except Exception:
                    healed = 0
                    log.debug("归位旧路径来源失败", exc_info=True)
                if healed:
                    self._refresh_source_list()
                    migrated = True
                src_now = [
                    (x.get("kind"), os.path.normcase(x.get("path") or ""))
                    for x in self._sources
                ]
                src_idx = [
                    (x.get("kind"), os.path.normcase(x.get("path") or ""))
                    for x in (self._index.get("sources") or [])
                ]
                chunks = self._index.get("chunks") or []
                need = (
                    src_now != src_idx
                    or not chunks
                    or any("src" not in c for c in chunks)
                )
                if need:
                    self._rebuild_index()
                else:
                    self._refresh_kb_status()
        finally:
            self._loading = False
        if migrated:
            self._dirty()

    def shutdown(self):
        self._stop_detect_worker()
        self._stop_recognize_worker()
        self._stop_worker()
        ov = self._overlay
        self._overlay = None
        if ov is not None:
            try:
                ov.close()
            except Exception:
                pass
        self._restore_main()
        self._state = "idle"
