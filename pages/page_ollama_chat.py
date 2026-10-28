# pages/page_ollama_chat.py
# 「本地对话」页面：连接本机 Ollama，选择模型进行多轮简单对话。
#   · 服务地址 / 上次模型写入 userdata/user.txt（ollama_chat 段）
#   · /api/chat 流式输出在工作线程中进行，界面不阻塞
#   · 对话记录只在内存中，「新对话」即清空

from __future__ import annotations

import html

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy,
    QTextBrowser, QTextEdit, QVBoxLayout, QWidget,
)

from styles.style_all import (
    theme, tk, install_card_title, make_card,
    CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP,
    apply_primary_button, apply_medium_button,
)
from utils.logger import get_logger
from utils.ollama_chat import DEFAULT_BASE, OllamaError, list_models, normalize_base, stream_chat
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
            self.ok.emit(list_models(self.base))
        except Exception as e:
            self.failed.emit(str(e))


class _ChatWorker(QThread):
    token = pyqtSignal(str)
    failed = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(self, base: str, model: str, messages: list, web: bool = False, parent=None):
        super().__init__(parent)
        self.base, self.model, self.messages = base, model, messages
        self.web = bool(web)
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
            for piece in stream_chat(
                self.base, self.model, self.messages,
                should_stop=lambda: self._stop, on_response=self._set_resp,
                web=self.web, on_status=self.status.emit,
            ):
                if self._stop:
                    break
                self.token.emit(piece)
        except Exception as e:
            if not self._stop:
                self.failed.emit(str(e))


class _InputEdit(QTextEdit):
    """Enter 发送，Shift+Enter 换行。"""
    send_requested = pyqtSignal()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.send_requested.emit()
            return
        super().keyPressEvent(e)


class PageOllamaChat(QWidget):
    def __init__(self):
        super().__init__()
        self._prefs_dirty_cb = None
        self._history = []          # [{"role":..., "content":...}]
        self._pending_model = ""    # user.txt 里记住的模型，列表回来后再选中
        self._models_worker = None
        self._chat_worker = None
        self._error_text = ""
        self._restore_link_status = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        card = make_card("CardOllamaChat")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setSpacing(8)
        title_lbl = install_card_title(card, box, "本地 Ollama 对话")
        self.lbl_status = QLabel("")
        self.lbl_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._sit_status_on_title(title_lbl)

        # 第一行：服务地址 + 模型 + 刷新 + 新对话
        bar = QHBoxLayout(); bar.setSpacing(8)
        bar.addWidget(QLabel("服务地址"))
        self.ed_base = QLineEdit(DEFAULT_BASE)
        self.ed_base.setPlaceholderText(DEFAULT_BASE)
        self.ed_base.setMinimumWidth(180)
        self.ed_base.editingFinished.connect(self._on_base_edited)
        bar.addWidget(self.ed_base, 2)
        bar.addWidget(QLabel("模型"))
        self.cb_model = QComboBox()
        self.cb_model.setMinimumWidth(160)
        self.cb_model.currentTextChanged.connect(self._on_model_changed)
        bar.addWidget(self.cb_model, 2)
        self.btn_refresh = apply_medium_button(QPushButton("刷新模型"))
        self.btn_refresh.clicked.connect(self.refresh_models)
        bar.addWidget(self.btn_refresh)
        self.btn_new = apply_medium_button(QPushButton("新对话"))
        self.btn_new.clicked.connect(self.new_conversation)
        bar.addWidget(self.btn_new)
        web_lbl = QLabel("联网")
        bar.addWidget(web_lbl, 0, Qt.AlignVCenter)
        from ui_main import SideToggleSwitch
        self.sw_web = SideToggleSwitch("#22C55E")
        self.sw_web.setToolTip("开启后，模型可以搜索并打开网页。关闭后不能访问网络。")
        self.sw_web.clicked.connect(lambda *_: self._notify_prefs())
        bar.addWidget(self.sw_web, 0, Qt.AlignVCenter)
        box.addLayout(bar)

        # 对话区
        self.view = QTextBrowser()
        self.view.setObjectName("OllamaChatView")
        self.view.setOpenExternalLinks(True)
        self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        box.addWidget(self.view, 1)

        # 输入区
        inp_row = QHBoxLayout(); inp_row.setSpacing(8)
        self.inp = _InputEdit()
        self.inp.setPlaceholderText("输入消息，Enter 发送，Shift+Enter 换行")
        self.inp.setFixedHeight(144)
        self.inp.send_requested.connect(self.send)
        inp_row.addWidget(self.inp, 1)
        btn_col = QVBoxLayout(); btn_col.setSpacing(6)
        self.btn_send = apply_primary_button(QPushButton("发送"))
        self.btn_send.clicked.connect(self.send)
        self.btn_clear = apply_medium_button(QPushButton("清屏"))
        self.btn_clear.clicked.connect(self.clear_screen)
        self.btn_stop = apply_medium_button(QPushButton("停止"))
        self.btn_stop.clicked.connect(self.stop_generation)
        self.btn_stop.setVisible(False)
        btn_col.addWidget(self.btn_send)
        btn_col.addWidget(self.btn_clear)
        btn_col.addWidget(self.btn_stop)
        btn_col.addStretch(1)
        inp_row.addLayout(btn_col)
        box.addLayout(inp_row)

        root.addWidget(card, 1)

        # 流式刷新节流：token 先进缓冲，定时重绘
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(60)
        self._render_timer.timeout.connect(lambda: self._render() if self._render_dirty else None)
        self._render_dirty = False

        try:
            theme.changed.connect(self._render)
        except Exception:
            pass
        self._loaded_once = False
        self._render()

    # ── user.txt ───────────────────────────────────────────────
    def set_prefs_dirty_callback(self, cb):
        self._prefs_dirty_cb = cb

    def _notify_prefs(self):
        fn = self._prefs_dirty_cb
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

    def export_settings(self) -> dict:
        model = self.cb_model.currentText().strip() or self._pending_model
        return {"server": self._base(), "model": model, "web": bool(self.sw_web.isChecked())}

    def apply_settings(self, d: dict):
        if not isinstance(d, dict):
            return
        srv = str(d.get("server") or "").strip()
        if srv:
            self.ed_base.setText(normalize_base(srv))
        self._pending_model = str(d.get("model") or "").strip()
        web = d.get("web")
        if isinstance(web, str):
            web = web.strip().lower() in ("1", "true", "yes", "on")
        self.sw_web.setChecked(bool(web))
        if self._pending_model and self.cb_model.findText(self._pending_model) >= 0:
            self.cb_model.setCurrentText(self._pending_model)

    # ── 生命周期 ───────────────────────────────────────────────
    def on_enter(self):
        if not self._loaded_once or self.cb_model.count() == 0:
            self._loaded_once = True
            self.refresh_models()
        self.inp.setFocus()

    def shutdown(self):
        try:
            if self._chat_worker is not None:
                self._chat_worker.stop()
            stop_qthread(self._chat_worker)
            stop_qthread(self._models_worker)
        except Exception:
            pass

    # ── 模型列表 ───────────────────────────────────────────────
    def _base(self) -> str:
        return normalize_base(self.ed_base.text())

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

    def _on_models_ok(self, names: list):
        want = self._pending_model or self.cb_model.currentText()
        self.cb_model.blockSignals(True)
        self.cb_model.clear()
        self.cb_model.addItems(names)
        if want and want in names:
            self.cb_model.setCurrentText(want)
        self.cb_model.blockSignals(False)
        if not names:
            self._set_status("Ollama 已连接，但本地还没有模型。请先在命令行执行 ollama pull 模型名 下载模型。", error=True)
            return
        if not (want and want in names):
            self._on_model_changed(self.cb_model.currentText())
        self._set_status(f"已连接，共 {len(names)} 个模型。")

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
        self.lbl_status.setStyleSheet(f"color:{color}; background:transparent;")
        self.lbl_status.setText(text)

    # ── 对话 ───────────────────────────────────────────────────
    def _busy(self) -> bool:
        return self._chat_worker is not None and self._chat_worker.isRunning()

    def send(self):
        if self._busy():
            return
        text = self.inp.toPlainText().strip()
        if not text:
            return
        model = self.cb_model.currentText().strip()
        if not model:
            self._set_status("还没有可用模型：请确认 Ollama 已启动并点击「刷新模型」。", error=True)
            return
        self.inp.clear()
        self._error_text = ""
        self._history.append({"role": "user", "content": text})
        msgs = [dict(m) for m in self._history]
        self._history.append({"role": "assistant", "content": ""})
        w = _ChatWorker(self._base(), model, msgs, web=self.sw_web.isChecked(), parent=self)
        w.token.connect(self._on_token)
        w.failed.connect(self._on_chat_failed)
        w.status.connect(self._on_worker_status)
        w.finished.connect(self._on_chat_finished)
        self._chat_worker = w
        self._set_busy_ui(True)
        self._render()
        w.start()

    def stop_generation(self):
        if self._chat_worker is not None:
            self._chat_worker.stop()

    def clear_screen(self):
        """清掉当前对话显示和上下文。正在生成时先停掉。"""
        self.stop_generation()
        self._history = []
        self._error_text = ""
        self._restore_link_status = False
        self._render()
        n = self.cb_model.count()
        if n:
            self._set_status(f"已清屏。已连接，共 {n} 个模型。")
        else:
            self._set_status("已清屏。")

    def new_conversation(self):
        self.clear_screen()
        self._set_status("已开始新对话。")

    def _on_token(self, piece: str):
        if self._history and self._history[-1]["role"] == "assistant":
            self._history[-1]["content"] += piece
            self._render_dirty = True

    def _on_chat_failed(self, msg: str):
        self._error_text = msg or "对话失败。"
        self._set_status(self._error_text, error=True)

    def _on_worker_status(self, text: str):
        self._restore_link_status = True
        self._set_status(text)

    def _on_chat_finished(self):
        # 空回复（出错/立即停止）不进历史，避免污染下一轮上下文
        if self._history and self._history[-1]["role"] == "assistant" and not self._history[-1]["content"]:
            self._history.pop()
            if self._history and self._history[-1]["role"] == "user" and self._error_text:
                self._history.pop()
        self._set_busy_ui(False)
        if self._restore_link_status and not self._error_text:
            n = self.cb_model.count()
            if n:
                self._set_status(f"已连接，共 {n} 个模型。")
        self._restore_link_status = False
        self._render()

    def _set_busy_ui(self, busy: bool):
        self.btn_send.setEnabled(not busy)
        self.btn_stop.setVisible(busy)
        self.cb_model.setEnabled(not busy)
        if busy:
            self._render_timer.start()
        else:
            self._render_timer.stop()

    # ── 绘制 ───────────────────────────────────────────────────
    def _bubble(self, role: str, content: str) -> str:
        if role == "user":
            who, bg, bd, align = "我", tk("bubble_user_bg"), tk("bubble_user_border"), "right"
        else:
            who, bg, bd, align = (self.cb_model.currentText() or "助手"), tk("bubble_ai_bg"), tk("bubble_ai_border"), "left"
        body = html.escape(content).replace("\n", "<br>") if content else "<i>思考中…</i>"
        return (
            f'<table width="100%" cellspacing="0" cellpadding="0" style="margin:6px 0;"><tr><td align="{align}">'
            f'<div style="color:{tk("text_mut")};font-size:11px;">{html.escape(who)}</div>'
            f'<table cellspacing="0" cellpadding="8" style="background:{bg};border:1px solid {bd};">'
            f'<tr><td style="color:{tk("text")};">{body}</td></tr></table>'
            f"</td></tr></table>"
        )

    def _render(self, *_):
        self._render_dirty = False
        sb = self.view.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        if not self._history:
            html_doc = (
                f'<p style="color:{tk("text_mut")};">选择本地模型后开始对话。'
                f"对话只保存在内存中，点击「新对话」即清空。</p>"
            )
        else:
            html_doc = "".join(self._bubble(m["role"], m["content"]) for m in self._history)
        self.view.setHtml(html_doc)
        if at_bottom or self._busy():
            sb.setValue(sb.maximum())
