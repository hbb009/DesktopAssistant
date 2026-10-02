# pages/page_gamemeet.py
# 游戏会议 — 局域网共享文档 + 编辑权勾选 + 房间发现 + 历史数据
#
# 焦点与静默约定（本页全部操作全程遵守）：
#   1) 全程后台静默：不 activateWindow / raise_ 拉起主窗，不用 grabKeyboard；
#      创建/加入/同步/复制监听/报错均只更新页内状态行，不弹 QMessageBox 等会
#      把前台从游戏抢回桌面助手的模态框。应用在后台时也只刷本页标签。
#   2) 本页为侧栏当前页时：其它功能（预下载/批处理/热键切页等）不得自动把栈切走；
#      仅用户自己点侧栏/顶栏可离开。切走后主机系统复制监听立即停。
#   3) 开会中（主持或加入）切到其它侧栏页：TCP 连接与文档/成员/历史仍后台照常
#      收包并写入本页控件；回到本页时再对账刷新。未进会不监听系统复制。
#
# 临时/备份文件请放项目根 temp/（转交别人前可整夹删除）。
# 主机在会议中且本页为侧栏当前页、且昵称行右侧复制同步开关开启时：系统复制才写入公共区；关开关则临时不同步；切走本页或未进会与复制脱钩。
from __future__ import annotations

import getpass
import json
import os
import random
import time
import uuid

from PyQt5.QtCore import Qt, QTimer, QSize, pyqtSignal, QObject, QPointF
from PyQt5.QtGui import QGuiApplication, QColor, QPainter, QPen, QIcon, QPixmap
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QSizePolicy, QPlainTextEdit, QCheckBox,
    QScrollArea, QSpinBox,
    QStackedWidget,
)

from styles.style_all import (
    theme, tk, make_card, install_card_title,
    apply_medium_button, apply_mini_button,
    CARD_LEFT_GAP, CARD_RIGHT_GAP, CARD_TOP_GAP, CARD_BOTTOM_GAP,
)
from utils.gamemeet_net import (
    MeetHost, MeetClient, RoomBrowser, get_lan_ipv4, DEFAULT_PORT_HINT,
    JUBAO_XIANPEN_NAMES, history_preview, HISTORY_CAP,
    pick_unique_nickname, _nick_key, existing_nick_keys,
)
from utils.flow_layout import FlowLayout
from utils.logger import get_logger

log = get_logger(__name__)

# 再导出，便于外部/测试引用
__all_names_ref__ = JUBAO_XIANPEN_NAMES


def _default_nickname() -> str:
    for key in ("USERNAME", "USER", "LOGNAME"):
        v = (os.environ.get(key) or "").strip()
        if v:
            return v
    try:
        return getpass.getuser() or "用户1"
    except Exception:
        return "用户1"


def _history_colors():
    """最新亮 / 旧记录暗（对齐 gamebrain 互动日志）。"""
    try:
        dark = bool(theme.is_dark)
    except Exception:
        dark = True
    if dark:
        return "#F5F5F5", "#8A8F98"
    return "#111111", "#9AA0A6"


def _muted_color() -> str:
    try:
        return tk("text_muted")
    except Exception:
        return "#94a3b8"


def _text_color() -> str:
    try:
        return tk("text")
    except Exception:
        return "#e5e5e5"


def _input_bg() -> str:
    try:
        return tk("input_bg")
    except Exception:
        return "#1e1e1e"


def _border_color() -> str:
    try:
        return tk("border")
    except Exception:
        return "#333"


def _clock_icon(color: str, size: int = 14) -> QIcon:
    size = max(12, int(size))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    c = QColor(color)
    # PyQt5 drawEllipse(x,y,w,h) 只要 int；用 float 会 TypeError 并可能导致进程 Abort
    pen = QPen(c, max(1, int(round(size * 0.09))))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    m = max(1, int(round(size * 0.12)))
    p.drawEllipse(m, m, size - 2 * m, size - 2 * m)
    cx = cy = size / 2.0
    p.drawLine(QPointF(cx, cy), QPointF(cx, cy - size * 0.22))
    p.drawLine(QPointF(cx, cy), QPointF(cx + size * 0.16, cy + size * 0.04))
    p.end()
    return QIcon(pm)


def _trash_icon(color: str, size: int = 14) -> QIcon:
    size = max(12, int(size))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    c = QColor(color)
    s = float(size)
    pen = QPen(c, 1.4)
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


class SharedDocEdit(QPlainTextEdit):
    """Shared doc: clipboard paste only while in a meeting."""
    host_paste = pyqtSignal(str, str)  # prior_text, new_text

    def __init__(self, parent=None):
        super().__init__(parent)
        self._paste_hook_enabled = False
        self._clipboard_linked = False
        self._in_paste = False

    def set_paste_hook_enabled(self, on: bool):
        self._paste_hook_enabled = bool(on)

    def set_clipboard_linked(self, on: bool):
        self._clipboard_linked = bool(on)

    def insertFromMimeData(self, source):
        # Idle / not in meeting: ignore paste into public area
        if not self._clipboard_linked:
            return
        if self._paste_hook_enabled and source is not None and source.hasText():
            self._in_paste = True
            try:
                prior = self.toPlainText()
                super().insertFromMimeData(source)
                try:
                    self.host_paste.emit(prior, self.toPlainText())
                except Exception:
                    log.exception("paste callback failed")
            finally:
                self._in_paste = False
            return
        super().insertFromMimeData(source)

class _Bridge(QObject):
    """把网络线程回调弹回 Qt 主线程。"""
    roster = pyqtSignal(list)
    doc = pyqtSignal(int, str, str)       # rev, text, from_id
    status = pyqtSignal(str)
    error = pyqtSignal(str)
    welcome = pyqtSignal(str, int, str, str, bool)  # user_id, rev, text, name, nick_renamed
    nick_assign = pyqtSignal(str, bool)  # name, nick_renamed（重入 hello 后的最终名）
    history = pyqtSignal(list)           # history snapshot
    history_del = pyqtSignal(str)        # entry id removed by host
    rooms = pyqtSignal(list)             # discovered rooms


class PageGameMeet(QWidget):
    def hasHeightForWidth(self):
        return False

    def minimumSizeHint(self):
        return QSize(0, 0)

    def sizeHint(self):
        sh = super().sizeHint()
        if not sh.isValid() or sh.height() <= 0:
            return QSize(0, 0)
        if int(sh.height()) > 700:
            return QSize(sh.width(), 700)
        return sh

    def __init__(self):
        super().__init__()
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._prefs_cb = None
        self._loading = False
        self._theme_titles = []
        self._func_cards = []

        self._role = None          # None | "host" | "client"
        self._host: MeetHost | None = None
        self._client: MeetClient | None = None
        self._local_id = ""
        self._host_id = ""
        self._joined_ip = ""
        self._joined_port = 0
        self._last_host_ip = ""
        self._can_edit = False  # idle: public doc not linked to clipboard
        self._applying_doc = False
        self._doc_rev = 0
        self._user_rows = {}       # user_id -> (row_widget, checkbox, name_label)
        self._history_items = []   # [{id, text, preview, ts}, ...] 本地持久 + 会议同步
        self._history_rows = []    # UI row widgets
        self._rooms = []
        self._room_cards = []      # UI card widgets
        self._browser: RoomBrowser | None = None
        self._pre_paste_text = ""  # 主机粘贴前正文快照
        self._clip_suppress = False
        self._host_clip_watching = False
        self._nick_tip = ""
        self._nick_tip_until = 0.0

        self._bridge = _Bridge(self)
        _qc = Qt.QueuedConnection
        self._bridge.roster.connect(self._on_roster, _qc)
        self._bridge.doc.connect(self._on_doc, _qc)
        self._bridge.status.connect(self._on_status, _qc)
        self._bridge.error.connect(self._on_error, _qc)
        self._bridge.welcome.connect(self._on_welcome, _qc)
        self._bridge.nick_assign.connect(self._on_nick_assign, _qc)
        self._bridge.history.connect(self._on_history, _qc)
        self._bridge.history_del.connect(self._on_history_del, _qc)
        self._bridge.rooms.connect(self._on_rooms, _qc)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(200)
        self._debounce.timeout.connect(self._flush_doc)

        self._ip_timer = QTimer(self)
        self._ip_timer.setInterval(3000)
        self._ip_timer.timeout.connect(self._refresh_local_endpoint_labels)

        self._clip_debounce = QTimer(self)
        self._clip_debounce.setSingleShot(True)
        self._clip_debounce.setInterval(80)
        self._clip_debounce.timeout.connect(self._flush_host_clipboard)

        self._build_ui()
        self._bind_theme()
        self._restyle()
        self._update_conn_ui()
        self._apply_editability()
        self._start_room_browser()
        self._refresh_local_endpoint_labels()
        self._ip_timer.start()
        self._restore_history_from_disk()

    # ----- prefs -----
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
                log.exception("游戏会议偏好回写失败")

    def export_settings(self) -> dict:
        hist = []
        for it in self._history_items[:HISTORY_CAP]:
            if not isinstance(it, dict):
                continue
            txt = str(it.get("text") if it.get("text") is not None else "")
            hist.append({
                "id": str(it.get("id") or ""),
                "text": txt,
                "preview": str(it.get("preview") or history_preview(txt)),
                "ts": float(it.get("ts") or 0),
            })
        return {
            "nickname": self.edit_nick.text().strip(),
            "last_host": (self._last_host_ip or "").strip(),
            "last_port": int(self.spin_port.value()),
            "history": hist,
        }

    def apply_settings(self, data: dict):
        self._loading = True
        try:
            data = data or {}
            nick = str(data.get("nickname") or "").strip()
            if nick:
                self.edit_nick.setText(nick)
            host = str(data.get("last_host") or "").strip()
            if host:
                self._last_host_ip = host
            try:
                port = int(data.get("last_port") or DEFAULT_PORT_HINT)
                if 1 <= port <= 65535:
                    self.spin_port.setValue(port)
            except Exception:
                pass

            raw_hist = data.get("history")
            prefs_items = []
            if isinstance(raw_hist, list):
                seen = set()
                for it in raw_hist:
                    if not isinstance(it, dict):
                        continue
                    eid = str(it.get("id") or "").strip() or ("h-" + uuid.uuid4().hex[:10])
                    if eid in seen:
                        continue
                    seen.add(eid)
                    txt = str(it.get("text") if it.get("text") is not None else "")
                    prefs_items.append({
                        "id": eid,
                        "text": txt,
                        "preview": str(it.get("preview") or history_preview(txt)),
                        "ts": float(it.get("ts") or 0),
                    })
            file_items = self._load_history_file()
            by_id = {}
            for it in list(prefs_items) + list(file_items):
                if not isinstance(it, dict):
                    continue
                eid = str(it.get("id") or "").strip()
                if not eid:
                    continue
                prev = by_id.get(eid)
                if prev is None or float(it.get("ts") or 0) >= float(prev.get("ts") or 0):
                    by_id[eid] = {
                        "id": eid,
                        "text": str(it.get("text") if it.get("text") is not None else ""),
                        "preview": str(it.get("preview") or history_preview(str(it.get("text") or ""))),
                        "ts": float(it.get("ts") or 0),
                    }
            merged = list(by_id.values())
            merged.sort(key=lambda x: float(x.get("ts") or 0), reverse=True)
            self._history_items = merged[:HISTORY_CAP]
            self._rebuild_history_ui()
        finally:
            self._loading = False

    def on_enter(self):
        self._start_room_browser()
        self._refresh_local_endpoint_labels()
        self._sync_host_clipboard_watch()
        self._resync_session_ui()

    def showEvent(self, event):
        try:
            super().showEvent(event)
        finally:
            self._sync_host_clipboard_watch()
            self._resync_session_ui()

    def hideEvent(self, event):
        try:
            super().hideEvent(event)
        finally:
            # 仅停主机系统复制监听；开会连接与文档/成员/历史后台继续更新
            self._set_host_clipboard_watch(False)

    # ----- theme -----
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
        self._style_doc()
        self._style_nick_edit()
        self._style_status()
        self._style_room_scroll()
        self._style_history_scroll()
        self._rebuild_rooms_ui()
        self._rebuild_history_ui()

    def _style_doc(self):
        bg = _input_bg()
        fg = _text_color()
        bd = _border_color()
        self.edit_doc.setStyleSheet(
            f"QPlainTextEdit#GameMeetDoc{{"
            f"background:{bg};color:{fg};border:1px solid {bd};"
            f"border-radius:6px;padding:8px;font-size:13px;"
            f"}}"
        )

    def _style_nick_edit(self):
        """昵称输入：无外边框/底框，扁平。"""
        edit = getattr(self, "edit_nick", None)
        if edit is None:
            return
        fg = _text_color()
        edit.setStyleSheet(
            f"QLineEdit#GameMeetNickEdit{{"
            f"background:transparent;color:{fg};border:none;border-radius:0;"
            f"padding:2px 4px;font-size:13px;}}"
            f"QLineEdit#GameMeetNickEdit:focus{{"
            f"background:transparent;border:none;outline:none;}}"
        )

    def _style_status(self):
        self.lbl_status.setStyleSheet(f"color:{_muted_color()};font-size:11px;")

    def _style_room_scroll(self):
        self.rooms_scroll.setStyleSheet(
            "QScrollArea#GameMeetRoomsScroll{background:transparent;border:none;}"
            "QWidget#GameMeetRoomsInner{background:transparent;}"
        )

    def _style_history_scroll(self):
        self.history_scroll.setStyleSheet(
            "QScrollArea#GameMeetHistoryScroll{background:transparent;border:none;}"
            "QWidget#GameMeetHistoryInner{background:transparent;}"
        )

    # ----- UI -----
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # 顶行：左房间列表 ~31.8%（30.25% +5% → 31.7625%），右 = 成员；stretch 127:273
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        top.addWidget(self._build_room_card(), 127)
        top.addWidget(self._build_members_card(), 273)
        root.addLayout(top)

        split = QHBoxLayout()
        split.setContentsMargins(0, 0, 0, 0)
        split.setSpacing(8)

        doc_col = QWidget()
        doc_v = QVBoxLayout(doc_col)
        doc_v.setContentsMargins(0, 0, 0, 0)
        doc_v.setSpacing(4)

        self.edit_doc = SharedDocEdit()
        self.edit_doc.setObjectName("GameMeetDoc")
        self.edit_doc.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.edit_doc.setPlaceholderText(
            "会议共享文本…（主机复制即可写入此处并同步；有编辑权的成员也可修改）"
        )
        self.edit_doc.textChanged.connect(self._on_doc_edited)
        self.edit_doc.host_paste.connect(self._on_host_paste)
        doc_v.addWidget(self.edit_doc, 1)

        right = QWidget()
        right.setFixedWidth(220)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)
        rv.addWidget(self._build_history_card(), 1)

        split.addWidget(doc_col, 1)
        split.addWidget(right, 0)
        root.addLayout(split, 1)

    def _build_room_card(self) -> QFrame:
        card = make_card("CardGameMeetRooms")
        self._func_cards.append(card)
        box = QVBoxLayout(card)
        # 无标题行，略收边距省空间
        box.setContentsMargins(CARD_LEFT_GAP, 6, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setSpacing(6)

        self.rooms_scroll = QScrollArea()
        self.rooms_scroll.setObjectName("GameMeetRoomsScroll")
        self.rooms_scroll.setWidgetResizable(True)
        self.rooms_scroll.setFrameShape(QFrame.NoFrame)
        self.rooms_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.rooms_scroll.setFocusPolicy(Qt.NoFocus)
        self.rooms_scroll.setMinimumHeight(180)  # list area -10% vs 200
        self.rooms_inner = QWidget()
        self.rooms_inner.setObjectName("GameMeetRoomsInner")
        self.rooms_layout = QVBoxLayout(self.rooms_inner)
        self.rooms_layout.setContentsMargins(0, 0, 0, 0)
        self.rooms_layout.setSpacing(6)
        self.rooms_layout.addStretch(1)
        self.rooms_scroll.setWidget(self.rooms_inner)
        box.addWidget(self.rooms_scroll, 1)

        # 底栏：创建 + IP 值 + 端口号（无「本机IP」「端口」字样）
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        self.btn_create = apply_medium_button(QPushButton("创建"))
        self.btn_create.setText("创建")
        self.btn_create.clicked.connect(self._on_create)
        bottom.addWidget(self.btn_create, 0)

        self.lbl_local_ip = QLabel("—")
        self.lbl_local_ip.setTextInteractionFlags(Qt.TextSelectableByMouse)
        try:
            self.lbl_local_ip.setStyleSheet(f"color:{_muted_color()};font-size:12px;")
        except Exception:
            pass
        bottom.addWidget(self.lbl_local_ip, 1)

        self.spin_port = QSpinBox()
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(DEFAULT_PORT_HINT)
        self.spin_port.setMaximumWidth(80)
        self.spin_port.setFocusPolicy(Qt.ClickFocus)
        self.spin_port.valueChanged.connect(lambda *_: self._dirty())
        bottom.addWidget(self.spin_port, 0)
        box.addLayout(bottom)
        card.setMinimumHeight(234)  # panel -10% vs 260
        return card

    def _build_members_card(self) -> QFrame:
        """成员：无标题；昵称行（无框编辑 + 随机名 + 右对齐状态）+ 半尺寸方卡流式。"""
        card = make_card("CardGameMeetMembers")
        self._func_cards.append(card)
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, 6, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setSpacing(6)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.edit_nick = QLineEdit(_default_nickname())
        self.edit_nick.setObjectName("GameMeetNickEdit")
        self.edit_nick.setPlaceholderText("显示名称")
        self.edit_nick.setMaximumWidth(160)
        self.edit_nick.setFocusPolicy(Qt.ClickFocus)
        self.edit_nick.textChanged.connect(self._on_nick_changed)
        row1.addWidget(self.edit_nick, 0)

        self.btn_random_nick = apply_mini_button(QPushButton("随机名"))
        self.btn_random_nick.setFocusPolicy(Qt.NoFocus)
        self.btn_random_nick.clicked.connect(self._on_random_nick)
        row1.addWidget(self.btn_random_nick, 0)

        row1.addStretch(1)

        # 状态上移到昵称同一行，右对齐
        self.lbl_status = QLabel("未连接")
        self.lbl_status.setWordWrap(False)
        self.lbl_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        try:
            self.lbl_status.setStyleSheet(
                f"color:{_muted_color()};font-size:11px;background:transparent;border:none;"
            )
        except Exception:
            pass
        row1.addWidget(self.lbl_status, 1)

        box.addLayout(row1)
        self._style_nick_edit()

        self.roster_scroll = QScrollArea()
        self.roster_scroll.setWidgetResizable(True)
        self.roster_scroll.setFrameShape(QFrame.NoFrame)
        self.roster_scroll.setFocusPolicy(Qt.NoFocus)
        self.roster_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.roster_inner = QWidget()
        self.roster_inner.setObjectName("GameMeetRosterInner")
        self.roster_layout = FlowLayout(self.roster_inner, margin=0, h_spacing=6, v_spacing=6)
        self.roster_scroll.setWidget(self.roster_inner)
        box.addWidget(self.roster_scroll, 1)
        return card

    def _build_history_card(self) -> QFrame:
        card = make_card("CardGameMeetHistory")
        self._func_cards.append(card)
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setSpacing(6)
        self._theme_titles.append(install_card_title(card, box, "历史数据"))

        self.lbl_hist_hint = QLabel("点击条目复制全文；主机粘贴共享区会归档。")
        self.lbl_hist_hint.setWordWrap(True)
        try:
            self.lbl_hist_hint.setStyleSheet(f"color:{_muted_color()};font-size:11px;")
        except Exception:
            pass
        box.addWidget(self.lbl_hist_hint)

        self.history_scroll = QScrollArea()
        self.history_scroll.setObjectName("GameMeetHistoryScroll")
        self.history_scroll.setWidgetResizable(True)
        self.history_scroll.setFrameShape(QFrame.NoFrame)
        self.history_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.history_scroll.setFocusPolicy(Qt.NoFocus)
        self.history_inner = QWidget()
        self.history_inner.setObjectName("GameMeetHistoryInner")
        self.history_layout = QVBoxLayout(self.history_inner)
        self.history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_layout.setSpacing(4)
        self.history_layout.addStretch(1)
        self.history_scroll.setWidget(self.history_inner)
        box.addWidget(self.history_scroll, 1)
        return card

    # ----- room discovery -----
    def _start_room_browser(self):
        if self._browser is not None:
            return
        try:
            self._browser = RoomBrowser(
                on_rooms=lambda rooms: self._bridge.rooms.emit(list(rooms or []))
            )
            self._browser.start()
        except Exception:
            log.exception("启动房间发现失败")
            self._browser = None

    def _stop_room_browser(self):
        if self._browser:
            try:
                self._browser.stop()
            except Exception:
                log.exception("停止房间发现失败")
            self._browser = None

    def _refresh_local_endpoint_labels(self):
        try:
            ip = get_lan_ipv4()
        except Exception:
            ip = "—"
        port = int(self.spin_port.value()) if hasattr(self, "spin_port") else DEFAULT_PORT_HINT
        if self._role == "host" and self._host:
            try:
                ip = self._host.lan_ip or ip
                port = int(self._host.port or port)
            except Exception:
                pass
        if hasattr(self, "lbl_local_ip"):
            self.lbl_local_ip.setText(str(ip or "—"))

    def _on_rooms(self, rooms: list):
        try:
            self._rooms = list(rooms or [])
            self._rebuild_rooms_ui()
        except Exception:
            log.exception("房间列表回调失败")

    def _clear_room_cards(self):
        for w in list(self._room_cards):
            self.rooms_layout.removeWidget(w)
            w.deleteLater()
        self._room_cards.clear()

    def _is_in_room(self, room: dict) -> bool:
        if self._role is None:
            return False
        hid = str(room.get("host_id") or "")
        ip = str(room.get("ip") or "")
        port = int(room.get("port") or 0)
        if self._role == "host" and self._host:
            if hid and hid == self._host.host_id:
                return True
            if port and port == int(self._host.port) and ip and ip == (self._host.lan_ip or ""):
                return True
            return False
        if self._host_id and hid and hid == self._host_id:
            return True
        if self._joined_ip and self._joined_port:
            if ip == self._joined_ip and port == self._joined_port:
                return True
        return False

    def _rebuild_rooms_ui(self):
        try:
            self._clear_room_cards()
            bg = _input_bg()
            fg = _text_color()
            bd = _border_color()
            stretch_idx = self.rooms_layout.count() - 1
            for r in self._rooms:
                title = str(r.get("title") or "")
                nick = str(r.get("nick") or "")
                ip = str(r.get("ip") or "")
                port = int(r.get("port") or 0)
                hid = str(r.get("host_id") or "")
                if not title:
                    title = f"{nick}的会议" if nick else f"{ip}:{port}"
                in_room = self._is_in_room(r)

                card = QFrame()
                card.setObjectName("GameMeetRoomCard")
                card.setFocusPolicy(Qt.NoFocus)
                card.setStyleSheet(
                    f"QFrame#GameMeetRoomCard{{"
                    f"background:{bg};border:1px solid {bd};border-radius:8px;}}"
                )
                hl = QHBoxLayout(card)
                hl.setContentsMargins(6, 2, 4, 2)
                hl.setSpacing(4)

                # 仅一行标题；IP:port 放 tooltip（底栏仍有本机 IP）
                line = title if title else (f"{nick}的会议" if nick else f"{ip}:{port}")
                title_lbl = QLabel(line)
                title_lbl.setWordWrap(False)
                title_lbl.setStyleSheet(
                    f"color:{fg};font-size:12px;font-weight:{'600' if in_room else '500'};"
                    f"background:transparent;border:none;"
                )
                hl.addWidget(title_lbl, 1)

                btn_join = apply_mini_button(QPushButton("+"))
                btn_join.setFixedSize(22, 18)
                btn_join.setStyleSheet(
                    "QPushButton{font-size:11px;padding:0 4px;min-height:18px;max-height:18px;}"
                )
                btn_join.setFocusPolicy(Qt.NoFocus)
                can_join = (self._role is None) and (not in_room)
                btn_join.setEnabled(can_join)
                btn_join.clicked.connect(
                    lambda _=False, _r=dict(r): self._join_room(_r)
                )
                hl.addWidget(btn_join, 0)

                btn_leave = apply_mini_button(QPushButton("退出"))
                btn_leave.setFixedHeight(18)
                btn_leave.setStyleSheet(
                    "QPushButton{font-size:11px;padding:0 6px;min-height:18px;max-height:18px;}"
                )
                btn_leave.setFocusPolicy(Qt.NoFocus)
                btn_leave.setEnabled(in_room)
                btn_leave.clicked.connect(self._on_leave)
                hl.addWidget(btn_leave, 0)

                self.rooms_layout.insertWidget(max(0, stretch_idx), card)
                stretch_idx += 1
                self._room_cards.append(card)
        except Exception:
            log.exception("_rebuild_rooms_ui 失败")

    def _host_nick_from_room(self, room: dict) -> str:
        """主机昵称：优先 room['nick']；否则从 title「{nick}的会议」解析。"""
        nick = str((room or {}).get("nick") or "").strip()
        if nick:
            return nick
        title = str((room or {}).get("title") or "").strip()
        suffix = "的会议"
        if title.endswith(suffix) and len(title) > len(suffix):
            parsed = title[: -len(suffix)].strip()
            if parsed:
                return parsed
        return ""

    def _join_room(self, room: dict):
        if self._role is not None:
            self._notify("已在会议中，可先离开再加入", error=False)
            return
        ip = str(room.get("ip") or "").strip()
        port = int(room.get("port") or 0)
        if not ip or port <= 0:
            self._notify("房间地址无效", error=False)
            return
        # 加入前与主机昵称碰撞检测（主路径）；主机侧/花名册自愈仍作备份
        host_nick = self._host_nick_from_room(room)
        local = self.edit_nick.text().strip() or _default_nickname()
        if host_nick and _nick_key(local) == _nick_key(host_nick):
            final, renamed = pick_unique_nickname(local, [host_nick])
            if renamed or _nick_key(final) != _nick_key(local) or final != local:
                self._apply_assigned_nick(final, True)
        self._last_host_ip = ip
        self.spin_port.setValue(port)
        self._dirty()
        self._on_join()

    # ----- history -----
    def _history_file_path(self) -> str:
        try:
            from utils.app_paths import data_dir
            return os.path.join(data_dir(), "gamemeet_history.json")
        except Exception:
            return os.path.join("data", "gamemeet_history.json")

    def _normalize_history_items(self, items) -> list:
        cleaned = []
        seen = set()
        for it in items or []:
            if not isinstance(it, dict):
                continue
            eid = str(it.get("id") or "").strip() or ("h-" + uuid.uuid4().hex[:10])
            if eid in seen:
                continue
            seen.add(eid)
            txt = str(it.get("text") if it.get("text") is not None else "")
            cleaned.append({
                "id": eid,
                "text": txt,
                "preview": str(it.get("preview") or history_preview(txt)),
                "ts": float(it.get("ts") or 0),
            })
        cleaned.sort(key=lambda x: float(x.get("ts") or 0), reverse=True)
        return cleaned[:HISTORY_CAP]

    def _load_history_file(self) -> list:
        path = self._history_file_path()
        try:
            if not os.path.isfile(path):
                return []
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return []
            return self._normalize_history_items(data.get("items"))
        except Exception:
            log.exception("读取游戏会议历史文件失败: %s", path)
            return []

    def _save_history_file(self):
        path = self._history_file_path()
        try:
            parent = os.path.dirname(path) or "."
            os.makedirs(parent, exist_ok=True)
            items = []
            for it in list(self._history_items or [])[:HISTORY_CAP]:
                if not isinstance(it, dict):
                    continue
                txt = str(it.get("text") if it.get("text") is not None else "")
                items.append({
                    "id": str(it.get("id") or ""),
                    "text": txt,
                    "preview": str(it.get("preview") or history_preview(txt)),
                    "ts": float(it.get("ts") or 0),
                })
            payload = {"version": 1, "items": items}
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            log.exception("写入游戏会议历史文件失败: %s", path)

    def _restore_history_from_disk(self):
        """启动时从本地文件恢复历史卡片（prefs 尚未 apply 时也能显示）。"""
        items = self._load_history_file()
        if not items:
            return
        if self._history_items:
            self._merge_history(items, prefer_remote=False)
        else:
            self._history_items = items[:HISTORY_CAP]
        self._rebuild_history_ui()

    def _persist_history_dirty(self):
        self._save_history_file()
        self._dirty()

    def _merge_history(self, items: list, *, prefer_remote: bool = True):
        """合并会议历史到本地列表（按 id；新在前）。"""
        by_id = {}
        for it in list(self._history_items or []):
            if isinstance(it, dict) and it.get("id"):
                by_id[str(it["id"])] = dict(it)
        for it in items or []:
            if not isinstance(it, dict):
                continue
            eid = str(it.get("id") or "").strip()
            if not eid:
                continue
            txt = str(it.get("text") if it.get("text") is not None else "")
            entry = {
                "id": eid,
                "text": txt,
                "preview": str(it.get("preview") or history_preview(txt)),
                "ts": float(it.get("ts") or 0),
            }
            if eid in by_id and not prefer_remote:
                continue
            by_id[eid] = entry
        merged = list(by_id.values())
        merged.sort(key=lambda x: float(x.get("ts") or 0), reverse=True)
        self._history_items = merged[:HISTORY_CAP]

    def _on_history(self, items: list):
        # 加入者：主机为权威，整表替换，确保 history / history_add 都能刷新卡片
        # 主机本地：合并（保留本机持久条目）
        items = list(items or [])
        if self._role == "client":
            cleaned = []
            seen = set()
            for it in items:
                if not isinstance(it, dict):
                    continue
                eid = str(it.get("id") or "").strip()
                if not eid or eid in seen:
                    continue
                seen.add(eid)
                txt = str(it.get("text") if it.get("text") is not None else "")
                cleaned.append({
                    "id": eid,
                    "text": txt,
                    "preview": str(it.get("preview") or history_preview(txt)),
                    "ts": float(it.get("ts") or 0),
                })
            cleaned.sort(key=lambda x: float(x.get("ts") or 0), reverse=True)
            self._history_items = cleaned[:HISTORY_CAP]
        else:
            self._merge_history(items, prefer_remote=True)
        self._ensure_client_history_for_doc()
        self._rebuild_history_ui()
        self._persist_history_dirty()

    def _on_history_del(self, entry_id: str):
        eid = str(entry_id or "").strip()
        if not eid:
            return
        before = len(self._history_items)
        self._history_items = [
            x for x in self._history_items if str(x.get("id") or "") != eid
        ]
        if len(self._history_items) == before:
            return
        self._rebuild_history_ui()
        self._persist_history_dirty()

    def _clear_history_rows(self):
        for w in list(self._history_rows):
            self.history_layout.removeWidget(w)
            w.deleteLater()
        self._history_rows.clear()

    def _rebuild_history_ui(self):
        try:
            self._clear_history_rows()
            bright, dim = _history_colors()
            stretch_idx = self.history_layout.count() - 1
            items = list(self._history_items or [])
            try:
                hover_bg = tk("hover_veil")
            except Exception:
                try:
                    dark = bool(theme.is_dark)
                except Exception:
                    dark = True
                hover_bg = "rgba(148,163,184,0.14)" if dark else "rgba(15,23,42,0.06)"
            for i, it in enumerate(items):
                preview = str(it.get("preview") or "").strip() or "（空）"
                eid = str(it.get("id") or "")
                full = str(it.get("text") if it.get("text") is not None else "")
                is_newest = (i == 0)
                color = bright if is_newest else dim

                row = QFrame()
                row.setObjectName("GameMeetHistRow")
                row.setFocusPolicy(Qt.NoFocus)
                row.setCursor(Qt.PointingHandCursor)
                row.setStyleSheet(
                    "QFrame#GameMeetHistRow{"
                    "background:transparent;border:none;border-radius:6px;"
                    "}"
                    "QFrame#GameMeetHistRow:hover{"
                    f"background:{hover_bg};"
                    "}"
                )
                hl = QHBoxLayout(row)
                hl.setContentsMargins(6, 4, 4, 4)
                hl.setSpacing(4)

                lbl = QLabel(preview)
                lbl.setWordWrap(True)
                weight = "600" if is_newest else "400"
                lbl.setStyleSheet(
                    f"color:{color};font-size:12px;font-weight:{weight};"
                    f"background:transparent;border:none;"
                )
                lbl.setCursor(Qt.PointingHandCursor)

                def _bind_click(widget, text):
                    widget.mousePressEvent = lambda e, _t=text: (
                        self._copy_history_text(_t)
                        if e.button() == Qt.LeftButton else None
                    )

                _bind_click(lbl, full)
                # 点空白行也复制；删除钮用自己的 clicked，不走此绑定
                def _row_press(e, _t=full, _row=row):
                    if e.button() != Qt.LeftButton:
                        return
                    child = _row.childAt(e.pos())
                    # 点到删除钮或其子控件时不复制
                    w = child
                    while w is not None and w is not _row:
                        if isinstance(w, QPushButton):
                            return
                        w = w.parentWidget()
                    self._copy_history_text(_t)

                row.mousePressEvent = _row_press
                hl.addWidget(lbl, 1)

                del_btn = QPushButton()
                del_btn.setFlat(True)
                del_btn.setFocusPolicy(Qt.NoFocus)
                del_btn.setFixedSize(20, 20)
                del_btn.setIcon(_trash_icon("#ef4444", 13))
                del_btn.setIconSize(QSize(13, 13))
                del_btn.setCursor(Qt.ArrowCursor)
                del_btn.setStyleSheet(
                    "QPushButton{border:none;background:transparent;}"
                    "QPushButton:hover{background:rgba(239,68,68,0.15);border-radius:4px;}"
                )
                del_btn.clicked.connect(
                    lambda _=False, _id=eid: self._delete_history_entry(_id)
                )
                hl.addWidget(del_btn, 0)

                self.history_layout.insertWidget(max(0, stretch_idx), row)
                stretch_idx += 1
                self._history_rows.append(row)
        except Exception:
            log.exception("_rebuild_history_ui 失败")

    def _copy_history_text(self, text: str):
        self._copy_to_clipboard(text if text is not None else "")
        self._notify("已复制到剪贴板", error=False)

    def _delete_history_entry(self, entry_id: str):
        eid = str(entry_id or "").strip()
        if not eid:
            return
        before = len(self._history_items)
        self._history_items = [
            x for x in self._history_items if str(x.get("id") or "") != eid
        ]
        if len(self._history_items) == before:
            return
        self._rebuild_history_ui()
        self._persist_history_dirty()
        if self._role == "host" and self._host:
            try:
                self._host.delete_history(eid)
            except Exception:
                log.exception("广播删除历史失败")
        self._notify("已删除历史记录", error=False)

    def _copy_to_clipboard(self, text: str):
        """Write clipboard; briefly suppress host watch."""
        payload = text if text is not None else ""
        self._clip_suppress = True
        try:
            clip = QGuiApplication.clipboard()
            clip.setText(payload, mode=clip.Clipboard)
        except Exception:
            log.exception("clipboard write failed")
        finally:
            QTimer.singleShot(200, self._clear_clip_suppress)

    def _clear_clip_suppress(self):
        self._clip_suppress = False

    def _page_in_foreground(self) -> bool:
        """本页是否为侧栏当前页（不要求系统窗口抢焦点）。"""
        try:
            parent = self.parentWidget()
            if isinstance(parent, QStackedWidget):
                return parent.currentWidget() is self
        except Exception:
            pass
        try:
            return bool(self.isVisible())
        except Exception:
            return False

    def _resync_session_ui(self):
        """开会中切回本页：用会话缓存把文档/成员/历史再刷一遍（防后台漏刷）。"""
        if not self._role:
            return
        try:
            if self._host:
                text = self._host.current_text()
                rev = int(self._host.current_rev())
                hist = self._host.current_history()
                roster = self._host._roster_list() if hasattr(self._host, "_roster_list") else None
            elif self._client:
                text = self._client.current_text() if hasattr(self._client, "current_text") else None
                rev = int(self._client.current_rev())
                hist = self._client.current_history()
                roster = self._client.current_roster() if hasattr(self._client, "current_roster") else None
            else:
                return
            if text is not None and rev >= int(self._doc_rev or 0):
                self._set_doc_text(text, rev)
                self._pre_paste_text = text if text is not None else ""
            if hist is not None:
                # 与实时 history 信号同一路径：加入方整表替换，主机合并
                self._on_history(list(hist))
            if roster is not None:
                self._on_roster(list(roster))
            self._apply_editability()
            self._rebuild_rooms_ui()
        except Exception:
            log.exception("resync session ui failed")

    def _clipboard_owner_is_gamemeet(self) -> bool:
        """侧栏剪贴板归属是否为本页（由 MainWindow 控制）。"""
        try:
            w = self.window()
            owner = getattr(w, "clipboard_owner", None)
            if callable(owner):
                return str(owner() or "") == "gamemeet"
            if isinstance(owner, str):
                return owner == "gamemeet"
            get = getattr(w, "get_clipboard_owner", None)
            if callable(get):
                return str(get() or "") == "gamemeet"
            return str(getattr(w, "_clipboard_owner", "") or "") == "gamemeet"
        except Exception:
            return False

    def _copy_sync_enabled(self) -> bool:
        """主机是否把系统复制写入公共区（侧栏归属 == gamemeet）。"""
        return self._clipboard_owner_is_gamemeet()

    def _sync_host_clipboard_watch(self):
        """仅：主机 + 本页前台 + 侧栏归属 gamemeet → 监听系统复制。"""
        want = (
            self._role == "host"
            and bool(self._host)
            and self._page_in_foreground()
            and self._copy_sync_enabled()
        )
        self._set_host_clipboard_watch(want)

    def _set_host_clipboard_watch(self, on: bool):
        """Host clipboard watch connect/disconnect."""
        want = bool(on)
        if want == bool(self._host_clip_watching):
            return
        clip = QGuiApplication.clipboard()
        if want:
            try:
                clip.dataChanged.connect(self._on_host_clipboard_changed)
                self._host_clip_watching = True
            except Exception:
                log.exception("host clipboard watch connect failed")
                self._host_clip_watching = False
        else:
            try:
                clip.dataChanged.disconnect(self._on_host_clipboard_changed)
            except Exception:
                pass
            self._host_clip_watching = False
            try:
                self._clip_debounce.stop()
            except Exception:
                pass

    def _on_host_clipboard_changed(self):
        if self._role != "host" or not self._host:
            return
        if not self._page_in_foreground():
            return
        if not self._copy_sync_enabled():
            return
        if self._clip_suppress or self._applying_doc:
            return
        self._clip_debounce.start()

    def ingest_external_text(self, text: str):
        """主窗 F7 推送：与主机剪贴板刷新同路，但使用给定文本。

        返回 (ok, tip)。失败 tip 供页内状态行展示（不抢焦点）。
        """
        text = "" if text is None else str(text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # 加入者 / 未主持：不能写公共区
        if self._role == "client" or (self._role and self._role != "host"):
            tip = "加入者无法推送到公共区"
            self._notify(tip, error=False)
            return False, tip
        if self._role != "host" or not self._host:
            tip = "游戏会议推送需本机主持会议"
            self._notify(tip, error=False)
            return False, tip
        if not self._page_in_foreground():
            tip = "请先切到「游戏会议」页再按 F7"
            self._notify(tip, error=False)
            return False, tip
        if not self._copy_sync_enabled():
            tip = "侧栏复制归属未指向游戏会议"
            self._notify(tip, error=False)
            return False, tip
        # 注意：不要拦 _clip_suppress。F7 捕获期间 MainWindow 会 begin_clip_suppress，
        # 且 SelectionCapture 在 captured.emit 时仍保持 suppress（约 100ms 后才 end），
        # 正是为挡住 restore 触发的 dataChanged；本入口是主动灌入，必须放行。
        if self._applying_doc:
            tip = "会议文档忙碌，稍后再试"
            self._notify(tip, error=False)
            return False, tip
        if not text.strip():
            tip = "没有选中文本"
            self._notify(tip, error=False)
            return False, tip
        prior = self.edit_doc.toPlainText()
        if text == prior:
            tip = "选区与公共区相同"
            self._notify(tip, error=False)
            return True, tip
        try:
            self._set_doc_text(text, self._doc_rev)
            self._on_host_paste(prior, text)
            return True, "已推送到公共区"
        except Exception:
            log.exception("游戏会议填入选区失败")
            tip = "填入公共区失败"
            self._notify(tip, error=True)
            return False, tip

    def begin_clip_suppress(self):
        self._clip_suppress = True

    def end_clip_suppress(self):
        self._clip_suppress = False

    def _flush_host_clipboard(self):
        """Host copy -> public doc; only while this page is foreground."""
        if self._role != "host" or not self._host:
            return
        if not self._page_in_foreground():
            return
        if not self._copy_sync_enabled():
            return
        if self._clip_suppress or self._applying_doc:
            return
        try:
            clip = QGuiApplication.clipboard()
            text = clip.text(mode=clip.Clipboard)
        except Exception:
            log.exception("clipboard read failed")
            return
        if text is None:
            text = ""
        if not str(text).strip():
            return
        prior = self.edit_doc.toPlainText()
        if text == prior:
            return
        self._set_doc_text(text, self._doc_rev)
        self._on_host_paste(prior, text)

    def _on_host_paste(self, prior_text: str, new_text: str):
        """Host public doc new text (in-area paste or system copy)."""
        if self._role != "host" or not self._host:
            return
        if self._applying_doc:
            return
        new_text = new_text if new_text is not None else ""
        prior_text = prior_text if prior_text is not None else (self._pre_paste_text or "")
        try:
            self._debounce.stop()
        except Exception:
            pass
        try:
            self._host.replace_doc_from_paste(
                new_text,
                from_id=self._local_id,
                prior_text=prior_text,
            )
            self._doc_rev = self._host.current_rev()
            snap = []
            try:
                snap = self._host.current_history()
            except Exception:
                pass
            if snap is not None:
                self._merge_history(snap, prefer_remote=True)
                self._rebuild_history_ui()
                self._persist_history_dirty()
            self._notify("已同步到公共区", error=False)
        except Exception:
            log.exception("host copy/paste archive failed")
        finally:
            self._pre_paste_text = new_text

    def _capture_pre_paste(self):
        if not self._applying_doc:
            self._pre_paste_text = self.edit_doc.toPlainText()

    # ----- connection actions -----
    def _on_random_nick(self):
        names = list(JUBAO_XIANPEN_NAMES) or ["用户1"]
        cur = self.edit_nick.text().strip()
        pool = [n for n in names if n != cur] or names
        self.edit_nick.setText(random.choice(pool))
        self._notify(f"已随机为{self.edit_nick.text().strip()}", error=False)

    def _on_nick_changed(self, *_):
        self._dirty()
        # welcome / nick_assign 写回昵称时不要回推网络，避免竞态覆盖 tip
        if self._loading:
            return
        name = self.edit_nick.text().strip() or _default_nickname()
        if self._host:
            self._host.set_display_name(name)
        elif self._client:
            self._client.set_display_name(name)

    def _on_create(self):
        if self._role:
            self._notify("请先离开当前会议", error=False)
            return
        name = self.edit_nick.text().strip() or _default_nickname()
        self.edit_nick.setText(name)
        preferred = int(self.spin_port.value())
        host = MeetHost(
            display_name=name,
            on_roster=lambda users: self._bridge.roster.emit(list(users)),
            on_doc=lambda rev, text, fid: self._bridge.doc.emit(int(rev), str(text), str(fid)),
            on_status=lambda s: self._bridge.status.emit(str(s)),
            on_error=lambda e: self._bridge.error.emit(str(e)),
            on_history=lambda items: self._bridge.history.emit(list(items or [])),
        )
        self._host = host
        self._client = None
        self._role = "host"
        self._local_id = host.local_user_id()
        self._host_id = host.host_id
        self._joined_ip = ""
        self._joined_port = 0
        self._can_edit = True
        # 启动时把本地持久历史灌进主机会话，便于新成员拿到
        try:
            with host._lock:
                host._history = [dict(x) for x in self._history_items[:HISTORY_CAP]]
        except Exception:
            pass
        # 同步编辑器已有正文，避免首次粘贴时 _text 仍为空而漏归档
        try:
            host.seed_doc(self.edit_doc.toPlainText())
        except Exception:
            pass
        try:
            # 创建时确保当前正文有历史卡（旧历史非空但正文不同也会补一张）
            host.ensure_doc_history()
            snap = host.current_history()
            if snap:
                self._merge_history(snap, prefer_remote=True)
                self._rebuild_history_ui()
        except Exception:
            pass
        try:
            ip, port = host.start(preferred_port=preferred)
        except Exception as e:
            log.exception("创建会议失败")
            self._teardown()
            self._notify(f"创建失败：{e}", error=True)
            return
        self._doc_rev = host.current_rev()
        self.spin_port.setValue(port)
        self._last_host_ip = ip
        self._joined_ip = ip
        self._joined_port = port
        self.edit_doc.set_paste_hook_enabled(True)
        self._pre_paste_text = self.edit_doc.toPlainText()
        self._apply_editability()
        self._sync_host_clipboard_watch()
        self._update_conn_ui()
        self._rebuild_rooms_ui()
        self._refresh_local_endpoint_labels()
        self._dirty()

    def _on_join(self):
        if self._role:
            self._notify("请先离开当前会议", error=False)
            return
        host_ip = (self._last_host_ip or "").strip()
        port = int(self.spin_port.value())
        if not host_ip:
            self._notify("请从房间卡片「+」加入", error=False)
            return
        # hello 名必须与昵称框一致（空则写入默认 OS 名）
        name = self.edit_nick.text().strip() or _default_nickname()
        self._loading = True
        try:
            if self.edit_nick.text().strip() != name:
                self.edit_nick.setText(name)
        finally:
            self._loading = False

        def _welcome(uid, rev, text, assigned="", renamed=False):
            self._bridge.welcome.emit(
                str(uid), int(rev), str(text),
                str(assigned or ""), bool(renamed),
            )

        client = MeetClient(
            display_name=name,
            on_roster=lambda users: self._bridge.roster.emit(list(users)),
            on_doc=lambda rev, text, fid: self._bridge.doc.emit(int(rev), str(text), str(fid)),
            on_status=lambda s: self._bridge.status.emit(str(s)),
            on_error=lambda e: self._bridge.error.emit(str(e)),
            on_welcome=_welcome,
            on_history=lambda items: self._bridge.history.emit(list(items or [])),
            on_history_del=lambda eid: self._bridge.history_del.emit(str(eid or "")),
            on_nick_assign=lambda n, r: self._bridge.nick_assign.emit(str(n or ""), bool(r)),
        )
        # 先绑定角色，避免 connect 后早期信号走错 merge/主机路径
        self._client = client
        self._host = None
        self._role = "client"
        self._can_edit = False
        self._joined_ip = host_ip
        self._joined_port = port
        try:
            client.connect(host_ip, port)
        except Exception as e:
            log.exception("加入会议失败")
            self._teardown()
            self._notify(f"加入失败：{e}", error=True)
            return
        self.edit_doc.set_paste_hook_enabled(False)
        self._apply_editability()
        self._update_conn_ui()
        self._rebuild_rooms_ui()
        self._dirty()

    def _on_leave(self):
        self._teardown()
        self._clear_roster_ui()
        # 离开后保留本地历史（已持久）
        self._rebuild_history_ui()
        self._rebuild_rooms_ui()
        self.lbl_status.setText("未连接")
        self._style_status()
        self._update_conn_ui()
        self._refresh_local_endpoint_labels()

    def _teardown(self):
        self._debounce.stop()
        self._set_host_clipboard_watch(False)
        self.edit_doc.set_paste_hook_enabled(False)
        if self._host:
            try:
                self._host.stop()
            except Exception:
                log.exception("停止主机失败")
            self._host = None
        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                log.exception("断开客户端失败")
            self._client = None
        self._role = None
        self._local_id = ""
        self._host_id = ""
        self._joined_ip = ""
        self._joined_port = 0
        self._can_edit = False  # leave/idle: unlink clipboard
        self._apply_editability()

    def _update_conn_ui(self):
        busy = self._role is not None
        self.btn_create.setEnabled(not busy)
        self.spin_port.setEnabled(not busy)
        self._sync_host_clipboard_watch()
        self._sync_side_clipboard_slot_freeze()

    def _sync_side_clipboard_slot_freeze(self):
        """加入别人会议：侧栏「游戏会议」复制图标冻结；主机/未进会解冻。"""
        frozen = self._role == "client"
        w = self.window()
        fn = getattr(w, "set_gamemeet_clipboard_slot_frozen", None) if w is not None else None
        if callable(fn):
            try:
                fn(frozen)
            except Exception:
                log.exception("同步侧栏游戏会议复制图标冻结态失败")

    # ----- roster / edit -----
    def _on_roster(self, users: list):
        self._rebuild_roster(users or [])
        me = next((u for u in (users or []) if u.get("id") == self._local_id), None)
        if self._role == "host":
            self._can_edit = True
        elif self._role == "client":
            if me is not None:
                self._can_edit = bool(me.get("can_edit"))
            # 加入者自愈：主机旧代码未改名时，本地检测与其他成员冲突并改名 + 重发 hello
            my_name = ""
            if me is not None:
                my_name = str(me.get("name") or "").strip()
            if not my_name:
                my_name = self.edit_nick.text().strip()
            others = [
                str(u.get("name") or "")
                for u in (users or [])
                if (not self._local_id) or (u.get("id") != self._local_id)
            ]
            if my_name and _nick_key(my_name) in existing_nick_keys(others):
                final, renamed = pick_unique_nickname(my_name, others)
                if renamed or _nick_key(final) != _nick_key(my_name) or final != my_name:
                    self._apply_assigned_nick(final, True)
                    if self._client is not None:
                        try:
                            self._client.set_display_name(final)
                        except Exception:
                            pass
            elif me is not None:
                roster_name = str(me.get("name") or "").strip()
                local = self.edit_nick.text().strip()
                if roster_name and _nick_key(roster_name) != _nick_key(local):
                    self._apply_assigned_nick(roster_name, True)
        self._apply_editability()
        self._rebuild_rooms_ui()

    def _clear_roster_ui(self):
        for uid, (row, _cb, _lb) in list(self._user_rows.items()):
            self.roster_layout.removeWidget(row)
            row.deleteLater()
        self._user_rows.clear()

    def _rebuild_roster(self, users: list):
        try:
            self._clear_roster_ui()
            is_host_ui = self._role == "host"
            bg = _input_bg()
            fg = _text_color()
            bd = _border_color()
            muted = _muted_color()
            for u in users:
                uid = str(u.get("id") or "")
                name = str(u.get("name") or uid)
                can = bool(u.get("can_edit"))
                is_host_user = bool(u.get("is_host"))
                mine = uid == self._local_id

                card = QFrame()
                card.setObjectName("GameMeetMemberCard")
                card.setFocusPolicy(Qt.NoFocus)
                card.setFixedSize(68, 68)
                card.setStyleSheet(
                    f"QFrame#GameMeetMemberCard{{"
                    f"background:{bg};border:1px solid {bd};border-radius:6px;}}"
                )
                vl = QVBoxLayout(card)
                vl.setContentsMargins(4, 4, 4, 4)
                vl.setSpacing(2)

                top = QHBoxLayout()
                top.setContentsMargins(0, 0, 0, 0)
                top.setSpacing(0)
                cb = QCheckBox()
                cb.setChecked(can)
                cb.setEnabled(is_host_ui)
                cb.setFocusPolicy(Qt.NoFocus)
                cb.setFixedSize(18, 18)
                cb.setStyleSheet(
                    "QCheckBox{spacing:0;}"
                    "QCheckBox::indicator{width:15px;height:15px;}"
                )
                cb.stateChanged.connect(
                    lambda _s, _uid=uid: self._on_toggle_edit(_uid)
                )
                top.addWidget(cb, 0)
                top.addStretch(1)
                if is_host_user:
                    badge = QLabel("主")
                    badge.setStyleSheet(
                        f"color:{muted};font-size:10px;background:transparent;border:none;"
                    )
                    top.addWidget(badge, 0)
                vl.addLayout(top)

                label = QLabel(name)
                label.setWordWrap(True)
                label.setAlignment(Qt.AlignCenter)
                weight = "600" if mine else "500"
                label.setStyleSheet(
                    f"color:{fg};font-size:11px;font-weight:{weight};"
                    f"background:transparent;border:none;"
                )
                vl.addWidget(label, 1)

                self.roster_layout.addWidget(card)
                self._user_rows[uid] = (card, cb, label)
        except Exception:
            log.exception("_rebuild_roster 失败")

    def _on_toggle_edit(self, user_id: str):
        if self._role != "host" or not self._host:
            return
        row = self._user_rows.get(user_id)
        if not row:
            return
        _w, cb, _lb = row
        can = cb.isChecked()
        if user_id == self._local_id:
            if not cb.isChecked():
                cb.blockSignals(True)
                cb.setChecked(True)
                cb.blockSignals(False)
            can = True
        self._host.set_edit(user_id, can)

    def _apply_editability(self):
        in_meet = self._role in ("host", "client")
        self.edit_doc.setReadOnly(not self._can_edit)
        # Idle: no clipboard link. In-meet + editable: allow paste.
        try:
            self.edit_doc.set_clipboard_linked(in_meet and bool(self._can_edit))
        except Exception:
            pass
        # Host only: paste -> history hook
        self.edit_doc.set_paste_hook_enabled(self._role == "host" and bool(self._host))

    def _apply_assigned_nick(self, name: str, nick_renamed: bool):
        """写入最终昵称并提示；强制更新 edit_nick（即使 _loading）；期间不回推 hello。"""
        name = (name or "").strip()
        if not name:
            return
        cur = self.edit_nick.text().strip()
        changed = _nick_key(name) != _nick_key(cur) or name != cur
        # 强制写回昵称框，即使当前 _loading（欢迎/花名册自愈路径）
        prev_loading = bool(self._loading)
        self._loading = True
        try:
            if self.edit_nick.text() != name:
                self.edit_nick.setText(name)
        finally:
            self._loading = prev_loading
        if changed:
            self._dirty()
            if self._client is not None:
                try:
                    # 只改本地权威名，勿再发 hello（避免竞态）；自愈路径会显式 set_display_name
                    self._client.display_name = name
                except Exception:
                    pass
        if nick_renamed or changed:
            tip = f"昵称重复，已改为{name}"
            self._nick_tip = tip
            self._nick_tip_until = time.time() + 4.0
            self._notify(tip, error=False)
            # 延后多拍，盖过「已连接」等状态
            QTimer.singleShot(0, lambda n=tip: self._notify_nick_tip(n))
            QTimer.singleShot(120, lambda n=tip: self._notify_nick_tip(n))
            QTimer.singleShot(400, lambda n=tip: self._notify_nick_tip(n))

    def _ensure_client_history_for_doc(self) -> bool:
        """加入者自愈：正文非空但历史无同文卡片时，本地合成一张并刷新。"""
        if self._role != "client":
            return False
        try:
            text = self.edit_doc.toPlainText()
        except Exception:
            return False
        if text is None:
            text = ""
        if not str(text).strip():
            return False
        for it in self._history_items or []:
            if isinstance(it, dict) and str(it.get("text") or "") == text:
                return False
        entry = {
            "id": "h-" + uuid.uuid4().hex[:10],
            "text": text,
            "preview": history_preview(text),
            "ts": time.time(),
        }
        items = [entry] + list(self._history_items or [])
        self._history_items = items[:HISTORY_CAP]
        self._rebuild_history_ui()
        self._persist_history_dirty()
        return True

    def _on_nick_assign(self, name: str, nick_renamed: bool = True):
        self._apply_assigned_nick(name, bool(nick_renamed))

    def _on_welcome(self, user_id: str, rev: int, text: str, name: str = "", nick_renamed: bool = False):
        self._local_id = user_id
        self._doc_rev = rev
        if self._client:
            self._host_id = getattr(self._client, "host_id", "") or ""
        # 非空分配名一律应用；改名或与本地不同则提示
        assigned = (name or "").strip()
        if assigned:
            prev = self.edit_nick.text().strip()
            tip_needed = bool(nick_renamed) or (_nick_key(assigned) != _nick_key(prev)) or (assigned != prev)
            self._apply_assigned_nick(assigned, tip_needed)
        self._set_doc_text(text, rev)
        self._pre_paste_text = text if text is not None else ""
        self._ensure_client_history_for_doc()
        self._rebuild_rooms_ui()

    def _on_doc(self, rev: int, text: str, from_id: str):
        """文档同步：与本页是否在前台无关，切走其它页也照常写入。"""
        if rev < self._doc_rev:
            return
        if from_id and from_id == self._local_id:
            cur = self.edit_doc.toPlainText()
            if cur == text:
                self._doc_rev = rev
                self._ensure_client_history_for_doc()
                return
        self._set_doc_text(text, rev)
        self._pre_paste_text = text if text is not None else ""
        self._ensure_client_history_for_doc()

    def _set_doc_text(self, text: str, rev: int):
        self._doc_rev = rev
        self._applying_doc = True
        try:
            if self.edit_doc.toPlainText() != text:
                cursor = self.edit_doc.textCursor()
                pos = cursor.position()
                self.edit_doc.setPlainText(text)
                cursor = self.edit_doc.textCursor()
                cursor.setPosition(min(pos, len(text)))
                self.edit_doc.setTextCursor(cursor)
        finally:
            self._applying_doc = False

    def _on_doc_edited(self):
        if self._applying_doc:
            return
        if getattr(self.edit_doc, "_in_paste", False):
            # 粘贴路径由 host_paste 接管，避免防抖抢跑
            return
        if not self._can_edit:
            return
        if not self._role:
            return
        self._pre_paste_text = self.edit_doc.toPlainText()
        self._debounce.start()

    def _flush_doc(self):
        if not self._can_edit or not self._role:
            return
        text = self.edit_doc.toPlainText()
        try:
            if self._host:
                self._host.publish_doc(
                    text, from_id=self._local_id, consider_history=True
                )
                self._doc_rev = self._host.current_rev()
            elif self._client:
                self._client.publish_doc(text)
        except Exception:
            log.exception("同步文档失败")

    def _notify(self, msg: str, error: bool = False):
        """报错/提示只进页内状态行，不弹模态框、不抢焦点。"""
        text_msg = (msg or "").strip() or "—"
        if error:
            self.lbl_status.setText(f"错误 — {text_msg}")
            log.warning("游戏会议: %s", text_msg)
            try:
                self.lbl_status.setStyleSheet("color:#F87171;font-size:11px;")
            except Exception:
                pass
        else:
            self.lbl_status.setText(text_msg)
            self._style_status()

    def _notify_nick_tip(self, tip: str):
        tip = (tip or "").strip()
        if not tip:
            return
        self._nick_tip = tip
        self._nick_tip_until = time.time() + 4.0
        self._notify(tip, error=False)

    def _drop_client_session(self, reason: str = ""):
        """主机断开/结束后：退出会议态，恢复房间「+」可点，等待重新加入。"""
        if self._role != "client":
            return False
        try:
            self._teardown()
        except Exception:
            log.exception("drop client session teardown failed")
        try:
            self._clear_roster_ui()
        except Exception:
            pass
        try:
            self._rebuild_rooms_ui()
        except Exception:
            pass
        try:
            self._update_conn_ui()
        except Exception:
            pass
        try:
            self._refresh_local_endpoint_labels()
        except Exception:
            pass
        tip = (reason or "").strip()
        if "结束" in tip:
            tip = "主机已结束会议，可重新加入"
        elif "断开" in tip or "异常" in tip or not tip:
            tip = "连接已断开，可重新加入"
        self._notify(tip, error=False)
        return True

    def _on_status(self, msg: str):
        text = str(msg or "")
        # 加入后的「已连接」等状态不要盖住昵称重复提示
        if (
            getattr(self, "_nick_tip_until", 0)
            and time.time() < float(self._nick_tip_until)
            and (self._nick_tip or "").startswith("昵称重复")
            and not text.startswith("昵称重复")
            and not text.startswith("错误")
            and text not in ("连接已断开", "主机已结束会议")
        ):
            return
        # 主机掉线/结束：必须退出会议态，否则「+」一直灰、正文也不再同步
        if text in ("连接已断开", "主机已结束会议"):
            if self._drop_client_session(text):
                return
        self._notify(text, error=False)

    def _on_error(self, msg: str):
        err = str(msg or "").strip() or "连接异常"
        dropped = self._drop_client_session(err)
        # 已用友好提示覆盖时，仍把原始错误打到日志；状态行保留 drop 的文案
        if not dropped:
            self._notify(err, error=True)
        else:
            log.warning("游戏会议客户端断开: %s", err)

    def closeEvent(self, event):
        try:
            self._teardown()
        except Exception:
            pass
        try:
            self._stop_room_browser()
        except Exception:
            pass
        try:
            self._ip_timer.stop()
        except Exception:
            pass
        super().closeEvent(event)

