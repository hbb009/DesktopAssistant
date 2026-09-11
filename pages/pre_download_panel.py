# pages/pre_download_panel.py
# 「预下载记录」：弹窗浏览/删除/清空队列（系统总览「预处理文件」或按平台打开）。
# 「预处理文件」：系统总览开关旁按钮，整表查看/编辑六平台 + 无法处理（样式对齐速存「记录文件」）。

from __future__ import annotations

import os
import re
import time
from typing import Callable, Dict, List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QSizePolicy, QDialog, QDialogButtonBox, QTextEdit, QMessageBox,
)

from styles.style_all import (
    make_card, install_card_title, restyle_card_title, restyle_card_frame,
    theme, tk,
    CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP,
    apply_mini_button,
    message_box_info, message_box_warn,
)

# 与图集/视频页「预下载」按钮同款橙（BTN_DOWNLOAD_QSS），紧凑版给转出键用
_EXPORT_BTN_QSS = """
QPushButton {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #f5ac4d, stop:1 #ea9530);
    border: 1px solid #d97f1e;
    color: #241300;
    font-weight: 700;
    font-size: 11px;
    border-radius: 5px;
    padding: 0 6px;
}
QPushButton:hover {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #f8bb66, stop:1 #f0a542);
    border-color: #e08b28;
    color: #241300;
}
QPushButton:pressed {
    background: #d98a28;
    border-color: #c67a1e;
    color: #241300;
}
QPushButton:disabled {
    background: rgba(100,116,139,0.25);
    color: #94a3b8;
    border: 1px solid rgba(100,116,139,0.45);
    font-weight: 600;
}
"""
from utils.pre_download import PLAT_LABEL, normalize_line
try:
    from utils.pre_download import FAILED_PLAT, SECTION_KEYS
except ImportError:
    # 主进程若仍是改分类前加载的 utils.pre_download，按字面补齐，避免弹窗直接起不来
    FAILED_PLAT = "failed"
    SECTION_KEYS = (
        "douyin", "bilibili", "youtube", "ehentai", "pixiv", "hitomi", FAILED_PLAT,
    )
    if FAILED_PLAT not in PLAT_LABEL:
        PLAT_LABEL = dict(PLAT_LABEL)
        PLAT_LABEL[FAILED_PLAT] = "无法处理"
from utils.logger import get_logger

log = get_logger(__name__)


def format_pre_process_text(get_store: Callable) -> str:
    """六平台 + 无法处理 → 可读文本（# 平台名 分段，供预处理文件弹窗编辑）。"""
    store = get_store() if callable(get_store) else None
    lines: List[str] = []
    for plat in SECTION_KEYS:
        lab = PLAT_LABEL.get(plat, plat)
        lines.append(f"# {lab}")
        if store is not None:
            for u in store.list_platform(plat):
                if u:
                    lines.append(u)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_pre_process_text(text: str) -> Dict[str, List[str]]:
    """解析预处理文件文本 → {platform: [url, ...]}。

    分段标题：``# 抖音`` / ``【B站】`` / 裸平台名；未识别段忽略其行。
    """
    label_to_plat = {v.lower(): k for k, v in PLAT_LABEL.items()}
    for k in SECTION_KEYS:
        label_to_plat[k.lower()] = k
    label_to_plat["无法处理"] = FAILED_PLAT

    queues: Dict[str, List[str]] = {k: [] for k in SECTION_KEYS}
    cur: Optional[str] = None
    for raw in (text or "").splitlines():
        s = (raw or "").strip()
        if not s:
            continue
        # 分段标题
        head = s
        if head.startswith("#"):
            head = head.lstrip("#").strip()
        elif head.startswith("【") and head.endswith("】"):
            head = head[1:-1].strip()
        low = head.lower()
        if low in label_to_plat:
            cur = label_to_plat[low]
            continue
        if cur is None:
            continue
        line = normalize_line(s)
        if line and line not in queues[cur]:
            queues[cur].append(line)
    return queues


class PreDownloadPanel(QWidget):
    """展示某平台预下载队列；支持删除选中 / 清空。"""

    changed = pyqtSignal()  # 用户改队列后通知调度刷新

    def __init__(
        self,
        platform: str,
        get_store: Callable,
        parent=None,
        *,
        compact: bool = False,
    ):
        super().__init__(parent)
        self.platform = platform
        self._get_store = get_store
        self._theme_titles = []
        self._theme_frames = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        card = make_card(f"CardPreDl_{platform}", borderless=bool(compact))
        self._theme_frames.append(card)
        box = QVBoxLayout(card)
        box.setContentsMargins(
            CARD_LEFT_GAP if not compact else 10,
            CARD_TOP_GAP if not compact else 8,
            CARD_RIGHT_GAP if not compact else 10,
            CARD_BOTTOM_GAP if not compact else 8,
        )
        box.setSpacing(6)

        title = f"预下载记录 · {PLAT_LABEL.get(platform, platform)}"
        self._theme_titles.append(install_card_title(card, box, title))

        head = QHBoxLayout()
        head.setSpacing(6)
        self.lbl_count = QLabel("0 条")
        self.lbl_count.setStyleSheet(
            f"color:{tk('text_mut')};font-size:12px;background:transparent;border:none;"
        )
        head.addWidget(self.lbl_count, 1)
        self.btn_del = apply_mini_button(QPushButton("删除选中"))
        self.btn_clear = apply_mini_button(QPushButton("清空"))
        self.btn_del.clicked.connect(self._on_delete)
        self.btn_clear.clicked.connect(self._on_clear)
        head.addWidget(self.btn_del)
        head.addWidget(self.btn_clear)
        box.addLayout(head)

        self.list = QListWidget()
        self.list.setObjectName("PreDownloadList")
        self.list.setMinimumHeight(160 if not compact else 120)
        self.list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        box.addWidget(self.list, 1)

        self.lbl_hint = QLabel(
            "复制白名单内链接将自动加入本列表；"
            "自动处理请在「系统总览 → 预下载」打开开关"
            "（开后空闲按序处理，开开关与每条结束都间隔 3 秒）。"
        )
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet(
            f"color:{tk('text_faint')};font-size:11px;background:transparent;border:none;"
        )
        box.addWidget(self.lbl_hint)

        root.addWidget(card)
        try:
            theme.changed.connect(self.refresh_theme)
        except Exception:
            pass
        self.reload()

    def refresh_theme(self, *_):
        for fr in self._theme_frames:
            try:
                restyle_card_frame(fr)
            except Exception:
                pass
        for t in self._theme_titles:
            try:
                restyle_card_title(t)
            except Exception:
                pass
        try:
            self.lbl_count.setStyleSheet(
                f"color:{tk('text_mut')};font-size:12px;background:transparent;border:none;"
            )
            self.lbl_hint.setStyleSheet(
                f"color:{tk('text_faint')};font-size:11px;background:transparent;border:none;"
            )
        except Exception:
            pass

    def reload(self):
        store = self._get_store()
        if store is None:
            return
        lines = store.list_platform(self.platform)
        self.list.clear()
        for line in lines:
            self.list.addItem(QListWidgetItem(line))
        self.lbl_count.setText(f"{len(lines)} 条")

    def _on_delete(self):
        store = self._get_store()
        if store is None:
            return
        row = self.list.currentRow()
        if row < 0:
            return
        store.remove_at(self.platform, row)
        self.reload()
        self.changed.emit()

    def _on_clear(self):
        store = self._get_store()
        if store is None:
            return
        n = store.clear_platform(self.platform)
        if n:
            self.reload()
            self.changed.emit()


def open_pre_download_dialog(
    platform: str,
    get_store: Callable,
    parent=None,
    *,
    on_changed: Optional[Callable] = None,
) -> None:
    """打开单平台「记录文件」弹窗（预下载队列）。"""
    dlg = QDialog(parent)
    dlg.setWindowTitle("记录文件")
    try:
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
    except Exception:
        pass
    dlg.resize(560, 420)
    dlg.setMinimumSize(400, 280)
    try:
        if parent is not None and parent.styleSheet():
            dlg.setStyleSheet(parent.styleSheet())
    except Exception:
        pass

    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(12, 12, 12, 12)
    lay.setSpacing(10)

    panel = PreDownloadPanel(platform, get_store, dlg, compact=False)
    if on_changed is not None:
        panel.changed.connect(on_changed)
    lay.addWidget(panel, 1)

    bb = QDialogButtonBox(QDialogButtonBox.Close)
    bb.rejected.connect(dlg.reject)
    bb.accepted.connect(dlg.accept)
    try:
        bb.button(QDialogButtonBox.Close).clicked.connect(dlg.accept)
    except Exception:
        pass
    lay.addWidget(bb)

    dlg.exec_()


# 转出记录卡用下载子页标题：e-hentai.org / hitomi.la / 抖音 …
_EXPORT_CARD_NAME = {
    "douyin": "抖音",
    "bilibili": "B站",
    "youtube": "YouTube",
    "ehentai": "e-hentai.org",
    "pixiv": "pixiv",
    "hitomi": "hitomi.la",
}


def _new_export_card_path(label: str) -> str:
    """在 data/ 根生成不冲突路径：card_e-hentai.org_01.txt / card_抖音_02.txt …"""
    try:
        from utils.app_paths import cards_dir
        rec_dir = cards_dir()
    except Exception:
        rec_dir = os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
        )
    try:
        os.makedirs(rec_dir, exist_ok=True)
    except Exception:
        pass
    # 文件名去掉路径非法字符；下载名本身可含 . -
    safe = re.sub(r'[\\/:*?"<>|]+', "_", (label or "未命名").strip()) or "未命名"
    for i in range(1, 100):
        path = os.path.join(rec_dir, f"card_{safe}_{i:02d}.txt")
        if not os.path.isfile(path):
            return path
    return os.path.join(rec_dir, f"card_{safe}_{int(time.time())}.txt")


def _refresh_fast_record_cards(parent) -> None:
    """转出后刷新速存页记录卡网格。"""
    w = parent
    seen = set()
    while w is not None and id(w) not in seen:
        seen.add(id(w))
        pf = getattr(w, "page_fast", None)
        if pf is not None and hasattr(pf, "_refresh_record_cards"):
            try:
                pf._refresh_record_cards()
            except Exception:
                log.exception("刷新速存记录卡失败")
            return
        try:
            w = w.parent()
        except Exception:
            break


def open_pre_process_file_dialog(
    get_store: Callable,
    parent=None,
    *,
    on_changed: Optional[Callable] = None,
) -> None:
    """打开「预处理文件」弹窗：编辑队列；可「转出」某平台到 F6 记录卡。"""
    dlg = QDialog(parent)
    dlg.setWindowTitle("预处理文件")
    try:
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
    except Exception:
        pass
    dlg.resize(680, 460)
    dlg.setMinimumSize(520, 320)
    try:
        if parent is not None and parent.styleSheet():
            dlg.setStyleSheet(parent.styleSheet())
    except Exception:
        pass

    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(12, 12, 12, 12)
    lay.setSpacing(10)

    tip = QLabel(
        "按「# 平台名」分段编辑，每行一条。"
        "解析不了或无法下载的记录会自动进入「无法处理」，不再自动重试。"
        "「转出 xx」：把该平台记录写成速存「记录卡」并清空本侧该段，"
        "可到速存图文点记录卡做批处理。"
    )
    tip.setWordWrap(True)
    tip.setStyleSheet(
        f"QLabel{{color:{tk('text_mut')};font-size:12px;"
        f"background:transparent;border:none;}}"
    )
    lay.addWidget(tip)

    preview = QTextEdit()
    preview.setObjectName("PreProcessFileEdit")
    preview.setAcceptRichText(False)
    preview.setStyleSheet(
        f"QTextEdit{{background:{tk('input_bg')};color:{tk('text')};"
        f"border:1px solid {tk('border')};border-radius:6px;font-size:13px;}}"
    )
    try:
        preview.setPlainText(format_pre_process_text(get_store))
    except Exception:
        log.exception("加载预处理文件文本失败")
        preview.setPlainText("（无法读取预处理文件）")
    lay.addWidget(preview, 1)

    def _make_btn(text, bg=None, fg=None, bd=None, hover_bg=None, *, compact=False):
        b = QPushButton(text)
        b.setFixedHeight(26 if compact else 34)
        b.setCursor(Qt.PointingHandCursor)
        fs = "11px" if compact else "13px"
        br = "5px" if compact else "6px"
        pad = "padding:0 6px;" if compact else ""
        if compact:
            # 可点 = 图集「预下载」同款橙；不可点走 :disabled 灰
            from PyQt5.QtGui import QColor, QPalette
            b.setStyleSheet(_EXPORT_BTN_QSS)
            pal = b.palette()
            ink = QColor("#241300")
            mut = QColor("#94a3b8")
            pal.setColor(QPalette.ButtonText, ink)
            pal.setColor(QPalette.WindowText, ink)
            pal.setColor(QPalette.Text, ink)
            pal.setColor(QPalette.Disabled, QPalette.ButtonText, mut)
            pal.setColor(QPalette.Disabled, QPalette.WindowText, mut)
            pal.setColor(QPalette.Disabled, QPalette.Text, mut)
            b.setPalette(pal)
            return b
        if bg:
            qss = (
                f"QPushButton{{background:{bg};color:{fg or '#ffffff'};"
                f"border:1px solid {bd or bg};border-radius:{br};font-size:{fs};{pad}}}"
            )
            if hover_bg:
                qss += f"QPushButton:hover{{background:{hover_bg};}}"
        else:
            qss = (
                f"QPushButton{{background:{tk('input_bg')};color:{tk('text')};"
                f"border:1px solid {tk('border')};border-radius:{br};font-size:{fs};{pad}}}"
            )
            if hover_bg:
                qss += f"QPushButton:hover{{background:{hover_bg};}}"
        qss += (
            "QPushButton:disabled{"
            "background:rgba(100,116,139,0.25);color:#94a3b8;"
            "border:1px solid rgba(100,116,139,0.45);}"
        )
        b.setStyleSheet(qss)
        return b

    def _apply_queues_to_store(queues: Dict[str, List[str]]) -> bool:
        store = get_store() if callable(get_store) else None
        if store is None:
            message_box_warn(dlg, "保存失败", "预下载存储未初始化。")
            return False
        store.replace_queues(queues)
        if on_changed is not None:
            try:
                on_changed()
            except Exception:
                log.exception("预处理文件 on_changed 失败")
        return True

    def _sync_preview_from_queues(queues: Dict[str, List[str]]):
        """用内存队列重写文本区（不读盘，避免与未保存编辑不同步）。"""
        lines: List[str] = []
        for plat in SECTION_KEYS:
            lab = PLAT_LABEL.get(plat, plat)
            lines.append(f"# {lab}")
            for u in queues.get(plat) or []:
                if u:
                    lines.append(u)
            lines.append("")
        preview.blockSignals(True)
        preview.setPlainText("\n".join(lines).rstrip() + "\n")
        preview.blockSignals(False)

    export_btns: Dict[str, QPushButton] = {}

    def _refresh_export_btn_states(*_args):
        """无本类记录 → 冻结对应「转出」键；编辑区变更 / 转出后调用。"""
        try:
            queues = parse_pre_process_text(preview.toPlainText())
        except Exception:
            queues = {k: [] for k in SECTION_KEYS}
        for plat, btn in export_btns.items():
            n = len(queues.get(plat) or [])
            lab = PLAT_LABEL.get(plat, plat)
            has = n > 0
            btn.setEnabled(has)
            if has:
                btn.setCursor(Qt.PointingHandCursor)
                btn.setToolTip(
                    f"把「{lab}」{n} 条写入速存记录卡，并清空预处理中该段"
                )
            else:
                btn.setCursor(Qt.ForbiddenCursor)
                btn.setToolTip(f"「{lab}」暂无记录 · 按钮已冻结")

    def _do_save() -> bool:
        try:
            queues = parse_pre_process_text(preview.toPlainText())
            if not _apply_queues_to_store(queues):
                return False
            store = get_store()
            log.info(
                "预处理文件已保存 total=%s",
                store.total_count() if store is not None else -1,
            )
            _refresh_export_btn_states()
            return True
        except Exception as e:
            log.exception("预处理文件保存失败")
            message_box_warn(dlg, "保存失败", str(e))
            return False

    def _do_save_and_close():
        if _do_save():
            dlg.accept()

    def _export_to_record_card(plat: str):
        """把某平台记录转出为 cards/ 记录卡，并清空预处理侧该段。"""
        lab = _EXPORT_CARD_NAME.get(plat, PLAT_LABEL.get(plat, plat))
        try:
            # 以当前编辑区为准（含未点保存的修改）
            queues = parse_pre_process_text(preview.toPlainText())
            lines = list(queues.get(plat) or [])
            if not lines:
                _refresh_export_btn_states()
                return
            path = _new_export_card_path(lab)
            body = "\n".join(lines) + "\n"
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(body)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            os.replace(tmp, path)
            queues[plat] = []
            if not _apply_queues_to_store(queues):
                log.error("转出后写回预处理队列失败 plat=%s path=%s", plat, path)
            _sync_preview_from_queues(queues)
            _refresh_export_btn_states()
            _refresh_fast_record_cards(parent if parent is not None else dlg)
            fname = os.path.basename(path)
            log.info(
                "预下载转出记录卡 plat=%s n=%s file=%s",
                plat, len(lines), fname,
            )
            message_box_info(
                dlg,
                "已转出",
                f"已将「{lab}」{len(lines)} 条写入记录卡：\n{fname}\n\n"
                f"可到「速存图文」找到该卡，点开后走批处理。",
            )
        except Exception as e:
            log.exception("转出记录卡失败 plat=%s", plat)
            message_box_warn(dlg, "转出失败", str(e))

    # ── 六平台「转出」：单行紧凑（无法处理不转出）──────────────
    # 短文案省宽度：e-hentai→EH，hitomi.la→hitomi
    _EXPORT_SHORT = {
        "douyin": "抖音",
        "bilibili": "B站",
        "youtube": "YT",
        "ehentai": "EH",
        "pixiv": "Pixiv",
        "hitomi": "hitomi",
    }
    export_row = QHBoxLayout()
    export_row.setSpacing(4)
    for plat in SECTION_KEYS:
        if plat == FAILED_PLAT:
            continue
        short = _EXPORT_SHORT.get(plat, PLAT_LABEL.get(plat, plat))
        b = _make_btn(f"转出 {short}", compact=True)
        b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        b.clicked.connect(lambda checked=False, p=plat: _export_to_record_card(p))
        export_btns[plat] = b
        export_row.addWidget(b, 1)
    lay.addLayout(export_row)
    preview.textChanged.connect(_refresh_export_btn_states)
    _refresh_export_btn_states()

    # ── 底栏：保存并关闭 + 关闭 ───────────────────────────────
    btn_row = QHBoxLayout()
    btn_row.setSpacing(8)
    btn_row.addStretch(1)
    try:
        acc = tk("accent")
    except Exception:
        acc = "#2563eb"
    btn_ok = _make_btn("保存并关闭", bg=acc, fg="#ffffff", bd=acc, hover_bg=acc)
    btn_ok.clicked.connect(_do_save_and_close)
    btn_row.addWidget(btn_ok)
    btn_close = _make_btn("关闭")
    btn_close.clicked.connect(dlg.reject)
    btn_row.addWidget(btn_close)
    lay.addLayout(btn_row)

    dlg.exec_()
