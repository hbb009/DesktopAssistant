# pages/page_jimeng.py
# 即梦（Jimeng）视频下载 —— 嵌入「视频下载」页的平台子页
# 布局对齐抖音：链接回车解析 → 画质卡 → 选中下载 / 自动下载
# 解析：HTML 内 workDetail JSON；下载：HTTP GET + Referer

from __future__ import annotations

import html as html_mod
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QTextEdit, QFileDialog, QFrame, QProgressBar,
    QApplication, QScrollArea,
)

from pages.page_douyin import MediaCard, MediaItem, _ElideLabel, DouyinProgressLine

from styles.style_all import (
    theme,
    tk,
    install_card_title,
    restyle_card_title,
    restyle_card_frame,
    make_card,
    apply_folder_path_edit,
    restyle_folder_path_edit,
    apply_mini_button,
    apply_simple_record,
    MEDIUM_BUTTON_H,
    CARD_TOP_GAP,
    CARD_LEFT_GAP,
    CARD_RIGHT_GAP,
    CARD_BOTTOM_GAP,
)
from utils.logger import get_logger
from utils.cursor_toast import (
    show_cursor_toast, in_batch_download, batch_progress_prefix,
)
from utils.download_watchdog import (
    DownloadWatchdog, StalledDownloadError, find_socket,
)

_log = get_logger(__name__)

try:
    import requests as req_lib
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

UA_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
JIMENG_REFERER = "https://jimeng.jianying.com/"
_URL_RE = re.compile(
    r"https?://(?:[\w.-]*\.)?(?:jimeng\.jianying\.com|jimeng\.com)"
    r"/[^\s\"'<>]+",
    re.I,
)


# ── 模块级 URL 工具 ──────────────────────────────────────────────────────────

def extract_jimeng_url(text: str) -> str:
    """从文本中提取第一条即梦作品链接；找不到返回空串。"""
    if not text:
        return ""
    m = _URL_RE.search(text)
    if not m:
        return ""
    url = m.group(0).rstrip(").,;]}\"'")
    return url


def looks_like_jimeng(text: str) -> bool:
    t = (text or "").lower()
    return ("jimeng.jianying.com" in t) or ("jimeng.com" in t)


def _safe_filename_part(s: str, max_len: int = 40) -> str:
    s = (s or "").strip()
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", s)
    s = re.sub(r"\s+", " ", s).strip(" ._")
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip(" ._") + "…"
    return s or "jimeng"


def _brace_match_object(s: str, start: int) -> Optional[str]:
    """从 start（应为 '{') 起做花括号匹配，返回完整 JSON 对象字符串。"""
    if start < 0 or start >= len(s) or s[start] != "{":
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


def _parse_work_detail(html: str) -> dict:
    """从页面 HTML 提取 workDetail.value JSON。"""
    raw = html_mod.unescape(html or "")
    marker = 'workDetail":{"ok":true,"value":'
    idx = raw.find(marker)
    if idx < 0:
        # 宽松：允许空格
        m = re.search(r'workDetail"\s*:\s*\{\s*"ok"\s*:\s*true\s*,\s*"value"\s*:\s*', raw)
        if not m:
            raise ValueError("页面中未找到 workDetail 数据")
        after = m.end()
    else:
        after = idx + len(marker)

    # value 从 {"id 开始
    id_pos = raw.find('{"id', after)
    if id_pos < 0 or id_pos > after + 200:
        id_pos = raw.find("{", after)
    if id_pos < 0:
        raise ValueError("workDetail.value 起始位置未找到")
    blob = _brace_match_object(raw, id_pos)
    if not blob:
        raise ValueError("workDetail.value JSON 括号匹配失败")
    try:
        return json.loads(blob)
    except json.JSONDecodeError as e:
        raise ValueError(f"workDetail JSON 解析失败: {e}") from e


def _fmt_size(n: Any) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _fmt_duration(ms: Any) -> str:
    try:
        ms = int(ms or 0)
    except (TypeError, ValueError):
        return ""
    if ms <= 0:
        return ""
    sec = ms // 1000 if ms > 1000 else ms  # 有的字段已是秒
    if ms > 10000:  # 更像毫秒
        sec = ms // 1000
    m, s = divmod(max(0, sec), 60)
    return f"{m}:{s:02d}"


def build_media_specs(value: dict) -> Tuple[dict, List[MediaItem]]:
    """从 workDetail.value 构建 info + MediaItem 列表（画质卡）。"""
    common = value.get("commonAttr") or {}
    author = value.get("author") or {}
    video = value.get("video") or {}
    title = (common.get("title") or "").strip() or "即梦视频"
    author_name = (author.get("name") or "").strip() or "未知作者"
    cover = (video.get("coverUrl") or "").strip()
    duration = video.get("duration")

    specs: List[Tuple[str, str, dict]] = []  # label, url, meta

    origin = video.get("originVideo") or {}
    ou = (origin.get("videoUrl") or "").strip()
    if ou:
        specs.append((
            "原片",
            ou,
            {
                "width": origin.get("width"),
                "height": origin.get("height"),
                "size": origin.get("size"),
            },
        ))

    trans = video.get("transcodedVideo") or {}
    for key in ("1080p", "720p", "480p", "360p"):
        node = trans.get(key) or {}
        if not isinstance(node, dict):
            continue
        u = (node.get("videoUrl") or "").strip()
        if u:
            specs.append((
                key,
                u,
                {
                    "width": node.get("width"),
                    "height": node.get("height"),
                    "size": node.get("size"),
                },
            ))

    if not specs:
        raise ValueError("未找到可下载的视频地址")

    items: List[MediaItem] = []
    for i, (label, url, meta) in enumerate(specs):
        bits = [label]
        wh = ""
        try:
            w, h = int(meta.get("width") or 0), int(meta.get("height") or 0)
            if w and h:
                wh = f"{w}×{h}"
                bits.append(wh)
        except (TypeError, ValueError):
            pass
        sz = _fmt_size(meta.get("size"))
        if sz:
            bits.append(sz)
        card_label = " · ".join(bits)
        items.append(MediaItem(
            "video",
            card_label,
            url,
            ".mp4",
            cover,
            i,
            default_checked=(i == 0),  # 最高质量默认勾选
        ))

    info = {
        "title": title,
        "author": author_name,
        "cover": cover,
        "duration": duration,
        "duration_text": _fmt_duration(duration),
        "id": str(value.get("id") or common.get("id") or ""),
        "prefer_origin": bool(ou),
    }
    return info, items


# ── Workers ──────────────────────────────────────────────────────────────────

class ParseWorker(QThread):
    ok = pyqtSignal(object, list)   # info dict, list[MediaItem]
    err = pyqtSignal(str)
    log = pyqtSignal(str, str)

    def __init__(self, url_text: str):
        super().__init__()
        self.url_text = url_text or ""

    def _emit(self, msg: str, level: str = "info"):
        self.log.emit(msg, level)

    def run(self):
        try:
            url = extract_jimeng_url(self.url_text) or (self.url_text or "").strip()
            if not url or not looks_like_jimeng(url):
                raise ValueError("不是有效的即梦链接")
            self._emit(f"识别链接：{url}")
            headers = {
                "User-Agent": UA_BROWSER,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": JIMENG_REFERER,
            }
            self._emit("拉取作品页 HTML…")
            if HAS_REQUESTS:
                r = req_lib.get(url, headers=headers, timeout=30)
                r.raise_for_status()
                # 尽量按声明编码，否则 utf-8
                r.encoding = r.apparent_encoding or r.encoding or "utf-8"
                body = r.text
            else:
                import urllib.request
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                body = raw.decode("utf-8", errors="replace")

            self._emit(f"HTML 长度 {len(body)}，提取 workDetail…")
            value = _parse_work_detail(body)
            info, items = build_media_specs(value)
            self._emit(
                f"解析成功：{info.get('author')} · {info.get('title')} · "
                f"{len(items)} 档画质"
                + (f" · {info.get('duration_text')}" if info.get("duration_text") else ""),
                "ok",
            )
            self.ok.emit(info, items)
        except Exception as e:
            _log.exception("即梦解析失败")
            self.err.emit(str(e) or repr(e))


class DownloadWorker(QThread):
    progress = pyqtSignal(int)
    log = pyqtSignal(str, str)
    done = pyqtSignal(str, int)   # save_dir, success_count
    cancelled = pyqtSignal()

    def __init__(self, jobs: list, save_dir: str, cancel_flag: list):
        """jobs: [{label, url, filename}, ...]"""
        super().__init__()
        self._jobs = list(jobs or [])
        self._save_dir = save_dir
        self._cancel = cancel_flag

    def _emit(self, msg: str, level: str = "info"):
        self.log.emit(msg, level)

    def run(self):
        wd = self._wd = DownloadWatchdog()
        stall_msg = f"{int(wd.stall_seconds)} 秒无响应，已视为无法下载"
        os.makedirs(self._save_dir, exist_ok=True)
        headers = {
            "User-Agent": UA_BROWSER,
            "Referer": JIMENG_REFERER,
            "Accept": "*/*",
        }
        total = len(self._jobs)
        ok_n = 0
        if total <= 0:
            self.done.emit(self._save_dir, 0)
            return

        for i, job in enumerate(self._jobs):
            if self._cancel and self._cancel[0]:
                self.cancelled.emit()
                return
            label = job.get("label") or f"视频{i+1}"
            url = job.get("url") or ""
            filename = job.get("filename") or f"jimeng_{i+1}.mp4"
            path = os.path.join(self._save_dir, filename)
            # 重名自动改名
            if os.path.exists(path):
                base, ext = os.path.splitext(filename)
                n = 1
                while True:
                    cand = f"{base}_{n}{ext}"
                    path2 = os.path.join(self._save_dir, cand)
                    if not os.path.exists(path2):
                        path = path2
                        filename = cand
                        break
                    n += 1

            self._emit(f"下载 {label} → {filename}")
            key = f"jimeng-{i}"
            try:
                size = self._fetch(
                    url, path, key, headers, wd, stall_msg,
                    job_index=i, job_total=total,
                )
                if size and size > 0:
                    ok_n += 1
                    self._emit(f"✓ {label} · {_fmt_size(size)}", "ok")
                else:
                    self._emit(f"✗ {label} · 空文件", "err")
                    try:
                        if os.path.isfile(path) and os.path.getsize(path) == 0:
                            os.remove(path)
                    except Exception:
                        pass
            except InterruptedError:
                self.cancelled.emit()
                return
            except StalledDownloadError as e:
                self._emit(f"✗ {label} · {e}", "err")
            except Exception as e:
                self._emit(f"✗ {label} · {e}", "err")
            pct = int(round((i + 1) * 100.0 / total))
            self.progress.emit(pct)

        self.done.emit(self._save_dir, ok_n)

    def _emit_job_progress(self, job_index: int, job_total: int, downloaded: int, total_len: int):
        """单文件字节进度映射到总进度，驱动输入框下简化进度条。"""
        job_total = max(1, int(job_total or 1))
        base = int(job_index) * 100 // job_total
        span = max(1, 100 // job_total)
        if total_len and total_len > 0:
            frac = max(0.0, min(1.0, float(downloaded) / float(total_len)))
            pct = base + int(frac * span)
        else:
            pct = base
        self.progress.emit(max(0, min(99, pct)))

    def _fetch(self, url, path, key, headers, wd, stall_msg, job_index: int = 0, job_total: int = 1) -> int:
        if HAS_REQUESTS:
            r = req_lib.get(url, headers=headers, stream=True, timeout=60)
            r.raise_for_status()
            ctype = (r.headers.get("Content-Type") or "").lower()
            if ctype and "video" not in ctype and "octet" not in ctype and "mp4" not in ctype:
                # 仍尝试下载（部分 CDN 返回 application/octet-stream 或空）
                pass
            try:
                total_len = int(r.headers.get("Content-Length") or 0)
            except Exception:
                total_len = 0
            wd.attach_socket(key, find_socket(r))
            downloaded = 0
            last_emit = -1
            with open(path, "wb") as f:
                for chunk in r.iter_content(65536):
                    if self._cancel and self._cancel[0]:
                        raise InterruptedError()
                    if wd.is_dead(key):
                        raise StalledDownloadError(stall_msg)
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        # 降频：每约 2% 或未知长度每 512KB 刷一次
                        step = max(1, total_len // 50) if total_len else 512 * 1024
                        if downloaded - last_emit >= step or downloaded == total_len:
                            self._emit_job_progress(job_index, job_total, downloaded, total_len)
                            last_emit = downloaded
                    wd.touch(key)
            return downloaded
        import urllib.request
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            try:
                total_len = int(resp.headers.get("Content-Length") or 0)
            except Exception:
                total_len = 0
            wd.attach_socket(key, find_socket(resp))
            downloaded = 0
            last_emit = -1
            with open(path, "wb") as f:
                while True:
                    if self._cancel and self._cancel[0]:
                        raise InterruptedError()
                    if wd.is_dead(key):
                        raise StalledDownloadError(stall_msg)
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    step = max(1, total_len // 50) if total_len else 512 * 1024
                    if downloaded - last_emit >= step or (total_len and downloaded >= total_len):
                        self._emit_job_progress(job_index, job_total, downloaded, total_len)
                        last_emit = downloaded
                    wd.touch(key)
            return downloaded


# ── 主页面 ───────────────────────────────────────────────────────────────────

class PageJimeng(QWidget):
    nav_progress = pyqtSignal(bool, int, str)

    def __init__(self):
        super().__init__()
        self._info = None
        self._media_items: List[MediaItem] = []
        self._cards: List[MediaCard] = []
        self._parse_worker = None
        self._dl_worker = None
        self._cancel_flag = [False]
        self._auto_dl = False
        self._quality_options = []
        self._selected_quality_idx = 0
        self._quality_cards = []
        self._cookie_auto_tried = False   # 本会话是否已尝试自动加载 Cookie
        self._cookie_loaded_via = None    # 'auto' | 'manual' | None
        self._nav_busy = False
        self._last_download_ok = False
        self._last_batch_note = ""
        self._dl_total = 0
        self._theme_titles = []
        self._func_cards = []

        theme.changed.connect(self.refresh_theme)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # ══ 解析与下载（无圆角外框，融入 Tab 内容区）══════════════════════════
        gb_main = make_card("CardJimengParse", borderless=True)
        gv = QVBoxLayout(gb_main)
        gv.setSpacing(0)
        gv.setContentsMargins(0, max(0, CARD_TOP_GAP - 6), 0, CARD_BOTTOM_GAP)
        self._theme_titles = []
        self._func_cards = [gb_main]

        body_main = QWidget()
        body_main.setObjectName("JimengParseBody")
        body_main.setStyleSheet("#JimengParseBody{background:transparent;border:none;}"
                                "#Row2Left{background:transparent;}")
        gv_body = QVBoxLayout(body_main)
        gv_body.setContentsMargins(0, 0, 0, 0)
        gv_body.setSpacing(7)
        gv.addWidget(body_main, 1)

        # ── 第1块：Cookie 配置（由「关于」弹窗统一管理，页面整行隐藏）──
        self._conf_wrapper = QWidget()
        wrap_l = QVBoxLayout(self._conf_wrapper)
        wrap_l.setContentsMargins(0, 0, 0, 0)
        wrap_l.setSpacing(0)

        conf_row = QHBoxLayout()
        conf_row.setSpacing(8)

        ck_col = QVBoxLayout()
        ck_col.setSpacing(5)
        ck_head = QHBoxLayout()
        ck_head.setSpacing(8)
        lbl_ck = QLabel("Cookie 文件（可选，Netscape .txt）")
        lbl_ck.setObjectName("SecTitle")
        ck_head.addWidget(lbl_ck, 0)
        self.ck_status = _ElideLabel("")
        self.ck_status.setObjectName("StatusLbl")
        self.ck_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.ck_status.setStyleSheet(f"color:{tk('text_faint')}; font-size:12px;")
        ck_head.addWidget(self.ck_status, 1)
        ck_col.addLayout(ck_head)
        ck_row = QHBoxLayout()
        ck_row.setSpacing(6)
        self.btn_ck = QPushButton("选择文件")
        self.btn_ck.setObjectName("BtnSmall")
        self.btn_ck.setMinimumWidth(72)
        self.btn_ck.clicked.connect(self._pick_cookie)
        ck_row.addWidget(self.btn_ck)
        self.ck_path = QLineEdit()
        self.ck_path.setPlaceholderText("自动扫描下载目录…")
        self.ck_path.setReadOnly(True)
        self._ck_path_icon_action = apply_folder_path_edit(self.ck_path)
        self.ck_path.textChanged.connect(self._on_cookie_change)
        ck_row.addWidget(self.ck_path, 1)
        ck_col.addLayout(ck_row)
        conf_row.addLayout(ck_col, 60)

        conf_row.addStretch(40)

        wrap_l.addLayout(conf_row)
        gv_body.addWidget(self._conf_wrapper)
        self._conf_wrapper.setVisible(False)

        # ── Row1：链接输入 + 取消 ────────────────────────────────────────
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        url_wrap = QWidget()
        url_wrap.setAttribute(Qt.WA_StyledBackground, True)
        url_wrap.setStyleSheet("background: transparent;")
        url_wrap_lay = QVBoxLayout(url_wrap)
        url_wrap_lay.setContentsMargins(0, 0, 0, 0)
        url_wrap_lay.setSpacing(0)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("粘贴即梦链接 → 回车自动解析下载")
        self.url_edit.setFixedHeight(MEDIUM_BUTTON_H)
        self._url_edit_icon_action = apply_folder_path_edit(self.url_edit)
        self.url_edit.returnPressed.connect(lambda: self._start_flow(False))
        url_wrap_lay.addWidget(self.url_edit)
        self._dl_progress_line = DouyinProgressLine()
        url_wrap_lay.addWidget(self._dl_progress_line)
        row1.addWidget(url_wrap, 1)

        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("BtnCancel")
        self.btn_cancel.setFixedHeight(MEDIUM_BUTTON_H)
        self.btn_cancel.setFixedWidth(56)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(lambda: self._cancel_flag.__setitem__(0, True))
        row1.addWidget(self.btn_cancel, 0, Qt.AlignTop)
        gv_body.addLayout(row1)

        # ── Row2：状态提示（已无独立批处理按钮，统一由速存图文记录卡启动）──
        row2 = QHBoxLayout()
        row2.setSpacing(10)

        self.lbl_batch_hint = QLabel("粘贴即梦链接后按回车开始解析")
        self.lbl_batch_hint.setFixedHeight(MEDIUM_BUTTON_H)
        self.lbl_batch_hint.setAlignment(Qt.AlignVCenter)
        row2.addWidget(self.lbl_batch_hint, 1)

        gv_body.addLayout(row2)

        # 与 B站一致：进度条仅作隐藏数值源，驱动细进度线；禁止 setVisible(True)，否则无布局会弹独立窗口
        self.progress = QProgressBar(self)
        self.progress.setMaximum(100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.progress.valueChanged.connect(self._on_progress_value)

        # 保存路径（不可见，仍被各方法引用）
        self.save_edit = QLineEdit(os.path.expanduser("~/Downloads").replace("\\", "/"))
        self._save_edit_icon_action = apply_folder_path_edit(self.save_edit)
        self.save_edit.setVisible(False)

        root.addWidget(gb_main)

        # ══ 媒体内容（对齐抖音：横向内容卡 + 全选/选中下载）══════════════════
        media_head = QHBoxLayout()
        self._sec_label_widget(media_head, "媒体内容（解析后显示）")
        media_head.addStretch(1)
        self.btn_all    = QPushButton("全选")
        self.btn_none   = QPushButton("全不选")
        self.btn_dl_sel = QPushButton("选中下载")
        self.btn_open_dir = QPushButton("打开目录")
        for b in (self.btn_all, self.btn_none, self.btn_dl_sel, self.btn_open_dir):
            b.setObjectName("BtnSmall")
            b.setStyleSheet("QPushButton{padding:2px 10px;} QPushButton:disabled{color:#555555;background:transparent;border-color:#333333;}")
            media_head.addWidget(b)
        for b in (self.btn_all, self.btn_none, self.btn_dl_sel):
            b.setEnabled(False)
        self.btn_dl_sel.setMinimumWidth(84)
        self.btn_open_dir.setMinimumWidth(84)
        self.btn_open_dir.setEnabled(True)
        self.btn_all.clicked.connect(self._select_all)
        self.btn_none.clicked.connect(self._deselect_all)
        self.btn_dl_sel.clicked.connect(self._download_selected)
        self.btn_open_dir.clicked.connect(self._open_save_dir)
        root.addLayout(media_head)

        self.card_scroll = QScrollArea()
        self.card_scroll.setWidgetResizable(True)
        self.card_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.card_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.card_scroll.setFixedHeight(MediaCard.CARD_H + 16)
        self.card_scroll.setFrameShape(QFrame.NoFrame)
        self.card_scroll.setObjectName("JimengCardScroll")
        self.card_scroll.viewport().setAutoFillBackground(False)
        self.card_scroll.setStyleSheet(
            "QScrollArea#JimengCardScroll{background:transparent;border:none;}"
            "QScrollArea#JimengCardScroll > QWidget > QWidget{background:transparent;}"
        )
        card_inner = QWidget()
        card_inner.setAutoFillBackground(False)
        self._card_layout = QHBoxLayout(card_inner)
        self._card_layout.setContentsMargins(4, 4, 4, 4)
        self._card_layout.setSpacing(8)
        self.card_scroll.setWidget(card_inner)
        root.addWidget(self.card_scroll)

        self._empty_card = None
        self._show_empty_card()

        # ══ 运行日志（无圆角外框）═════════════════════════════════════════════
        card_log = make_card("CardJimengLog", borderless=True)
        log_lay = QVBoxLayout(card_log)
        log_lay.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        log_lay.setSpacing(0)
        title_log = install_card_title(card_log, log_lay, "运行日志")
        self._theme_titles.append(title_log)
        self._func_cards.append(card_log)

        head = title_log.parentWidget()
        head_l = head.layout()
        if head_l is not None:
            head_l.removeWidget(title_log)
            title_row = QHBoxLayout()
            title_row.setContentsMargins(0, 0, 0, 0)
            title_row.setSpacing(6)
            title_row.addWidget(title_log, 0, Qt.AlignVCenter)
            title_row.addStretch(1)
            self.btn_clear_log = apply_mini_button(QPushButton("清除记录"))
            self.btn_clear_log.clicked.connect(lambda: self.log_box.clear())
            title_row.addWidget(self.btn_clear_log, 0, Qt.AlignVCenter)
            head_l.addLayout(title_row)

        self.log_box = QTextEdit()
        self.log_box.setObjectName("JimengLogBox")
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(72)
        self.log_box.setFrameShape(QFrame.NoFrame)
        apply_simple_record(self.log_box)
        log_lay.addWidget(self.log_box, 1)
        root.addWidget(card_log, 1)

    # ── 样式 / 主题 ───────────────────────────────────────────────────────────

    def refresh_theme(self, *_):
        if hasattr(self, "dir_hint"):
            self.dir_hint.setStyleSheet(f"color:{tk('text_faint')}; font-size:12px;")
        if hasattr(self, "ck_path"):
            restyle_folder_path_edit(self.ck_path, getattr(self, "_ck_path_icon_action", None))
        if hasattr(self, "save_edit"):
            restyle_folder_path_edit(self.save_edit, getattr(self, "_save_edit_icon_action", None))
        if hasattr(self, "url_edit"):
            restyle_folder_path_edit(self.url_edit, getattr(self, "_url_edit_icon_action", None))
        for c in getattr(self, "_cards", []):
            if hasattr(c, "refresh_theme"):
                c.refresh_theme()
        for lbl in self._theme_titles:
            restyle_card_title(lbl)
        for frame in getattr(self, "_func_cards", []):
            restyle_card_frame(frame)
        # Cookie 按钮绿色态要跟主题重刷
        path = (self.ck_path.text() or "").strip() if hasattr(self, "ck_path") else ""
        self._set_cookie_btn_loaded(bool(path and os.path.isfile(path)))

    def _sec_label_widget(self, parent_layout, text):
        lbl = QLabel(text)
        lbl.setObjectName("SecTitle")
        parent_layout.addWidget(lbl)

    def _log(self, msg, level="info"):
        colors = {
            "ok": tk("ok"), "err": tk("err"), "warn": tk("warn"),
            "info": tk("text_mut"), "blue": tk("info"),
        }
        color = colors.get(level, tk("text_mut"))
        ts = time.strftime("%H:%M:%S")
        sb = self.log_box.verticalScrollBar()
        follow = sb.value() >= sb.maximum() - 8
        self.log_box.append(
            f'<span style="color:{tk("text_faint")}">[{ts}]</span> '
            f'<span style="color:{color}">{msg}</span>'
        )
        if follow:
            self._scroll_log_bottom()

    def _scroll_log_bottom(self):
        self.log_box.moveCursor(QTextCursor.End)
        self.log_box.ensureCursorVisible()
        sb = self.log_box.verticalScrollBar()
        sb.setValue(sb.maximum())
        QTimer.singleShot(0, lambda: sb.setValue(sb.maximum()))

    def _set_status(self, text, color=None):
        try:
            if hasattr(self, "lbl_batch_hint"):
                self.lbl_batch_hint.setText(text or "")
                col = color or tk("text_mut")
                weight = "700" if color else "600"
                self.lbl_batch_hint.setStyleSheet(
                    f"color:{col}; font-weight:{weight}; font-size:13px;"
                )
        except Exception:
            pass

    @staticmethod
    def _progress_gradient_color(pct: int) -> str:
        pct = max(0, min(100, pct))
        red, yellow, green = (239, 68, 68), (234, 179, 8), (34, 197, 94)
        if pct < 50:
            t = pct / 50.0
            a, b = red, yellow
        else:
            t = (pct - 50) / 50.0
            a, b = yellow, green
        rgb = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
        return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"

    def _on_progress_value(self, value):
        if self._nav_busy and int(value or 0) > 0:
            self._emit_nav_progress(True, int(value), mode="progress")

    def _emit_nav_progress(self, active: bool, pct: int = 0, mode: str = "progress"):
        self._nav_busy = bool(active)
        if not active:
            try:
                self.nav_progress.emit(False, 0, "")
            except Exception:
                pass
            try:
                self._dl_progress_line.hide_line()
            except Exception:
                pass
            return
        if mode == "busy":
            try:
                self.nav_progress.emit(True, -1, "#94a3b8")
            except Exception:
                pass
            try:
                self._dl_progress_line.set_busy(True)
            except Exception:
                pass
            return
        pct = max(0, min(100, int(pct or 0)))
        color = self._progress_gradient_color(pct)
        try:
            self.nav_progress.emit(True, pct, color)
        except Exception:
            pass
        try:
            self._dl_progress_line.set_progress(pct, color)
        except Exception:
            pass

    # ── Cookie / 目录 / 帮助 ─────────────────────────────────────────────────

    def showEvent(self, event):
        """本会话第一次显示本页时：尝试自动加载 Cookie（若已配置路径）。"""
        super().showEvent(event)
        if self._cookie_auto_tried:
            return
        self._cookie_auto_tried = True
        QTimer.singleShot(0, self._auto_load_cookie)

    def _set_cookie_btn_loaded(self, loaded: bool):
        """加载成功 → 绿色「已加载」；否则恢复「选择文件」。仍可点击重选。"""
        btn = getattr(self, "btn_ck", None)
        if btn is None:
            return
        if loaded:
            btn.setText("已加载")
            btn.setStyleSheet(
                f"QPushButton{{background:{tk('ok')}; color:#ffffff;"
                f"border:1px solid {tk('ok')}; border-radius:6px;"
                f"padding:4px 12px; font-weight:600;}}"
                f"QPushButton:hover{{background:{tk('ok')}; color:#ffffff;}}"
            )
        else:
            btn.setText("选择文件")
            btn.setStyleSheet("")
            st = btn.style()
            if st is not None:
                st.unpolish(btn)
                st.polish(btn)
            btn.update()

    def _auto_load_cookie(self):
        """若 ck_path 已有有效文件则刷新状态；否则保持空（即梦 Cookie 可选）。"""
        cur = (self.ck_path.text() or "").strip() if hasattr(self, "ck_path") else ""
        if cur and os.path.isfile(cur):
            self._on_cookie_change(cur)
            return
        self._set_cookie_btn_loaded(False)

    def _pick_cookie(self):
        start = (self.ck_path.text() or "").strip()
        if start and os.path.isfile(start):
            start = os.path.dirname(start)
        elif hasattr(self, "save_edit") and self.save_edit.text().strip():
            start = self.save_edit.text().strip()
        else:
            start = os.path.expanduser("~/Downloads")
        p, _ = QFileDialog.getOpenFileName(
            self, "选择 cookies.txt", start,
            "Cookie 文件 (*.txt);;所有文件 (*.*)",
        )
        if p:
            self._cookie_loaded_via = "manual"
            self.ck_path.setText(p.replace("\\", "/"))
            self._log(f"✓ 已手动加载 Cookie：{p}", "ok")

    def _on_cookie_change(self, path):
        path = (path or "").strip()
        if not path or not os.path.isfile(path):
            self.ck_status.setFullText("")
            self.ck_status.setStyleSheet(f"color:{tk('text_faint')}; font-size:12px;")
            self._set_cookie_btn_loaded(False)
            return
        try:
            raw = open(path, encoding="utf-8", errors="ignore").read()
        except Exception as e:
            self.ck_status.setFullText(f"❌ 读取失败: {e}")
            self.ck_status.setStyleSheet(f"color:{tk('err')}; font-size:12px;")
            self._set_cookie_btn_loaded(False)
            return
        low = raw.lower()
        lines_ck = [ln for ln in raw.splitlines() if ln.strip() and not ln.startswith("#")]
        count = len(lines_ck)
        has_jimeng = (
            "jimeng" in low
            or "jianying.com" in low
            or "bytedance" in low
            or "capcut" in low
        )
        via_auto = getattr(self, "_cookie_loaded_via", None) == "auto"
        if count <= 0:
            self.ck_status.setFullText("⚠ Cookie 文件为空")
            self.ck_status.setStyleSheet(f"color:{tk('warn')}; font-size:12px;")
            self._set_cookie_btn_loaded(False)
            return
        if has_jimeng:
            msg = (
                f"✅ 已自动加载（{count} 行）· 无需再点「选择文件」"
                if via_auto else f"✅ Cookie 已加载（{count} 行）"
            )
            self.ck_status.setStyleSheet(f"color:{tk('ok')}; font-size:12px;")
            self._set_cookie_btn_loaded(True)
        else:
            msg = f"⚠ 未见即梦相关域名（{count} 行，仍可使用）"
            self.ck_status.setStyleSheet(f"color:{tk('warn')}; font-size:12px;")
            self._set_cookie_btn_loaded(True)
        self.ck_status.setFullText(msg)

    def _show_help(self):
        """与 B站页同结构的帮助入口；即梦短提示，避免未接线时崩溃。"""
        text = (
            "【即梦 · 使用说明】\n"
            "1. 粘贴即梦作品链接 → 回车自动解析下载\n"
            "2. 勾选画质 → 选中下载\n"
            "3. Cookie（可选）由「关于」统一管理；本页配置行默认隐藏"
        )
        try:
            from styles.style_all import message_box_info
            message_box_info(self, "即梦 · 使用说明", text)
        except Exception:
            pass

    def _make_empty_card(self):
        card = QFrame()
        card.setObjectName("EmptyCard")
        card.setFixedSize(MediaCard.THUMB_W, MediaCard.CARD_H)
        card.setStyleSheet("QFrame#EmptyCard{background:transparent;border:1px solid #868686;border-radius:6px;}")
        v = QVBoxLayout(card)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        lbl = QLabel("粘贴即梦链接 →\n回车自动解析")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(
            f"color:{tk('text_faint')};font-size:12px;"
            f"background:transparent;border:none;"
        )
        v.addWidget(lbl)
        return card

    def _clear_card_layout(self):
        while self._card_layout.count():
            it = self._card_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _show_empty_card(self):
        for c in list(getattr(self, "_cards", []) or []):
            try:
                c.deleteLater()
            except Exception:
                pass
        self._cards = []
        self._media_items = []
        self._clear_card_layout()
        self._empty_card = self._make_empty_card()
        self._card_layout.addWidget(self._empty_card)
        self._card_layout.addStretch(1)

    # ── dirs ──────────────────────────────────────────────────────────────────

    def _pick_save_dir(self):
        cur = (self.save_edit.text() or "").strip() or os.path.expanduser("~/Downloads")
        path = QFileDialog.getExistingDirectory(self, "选择保存目录", cur)
        if path:
            self.save_edit.setText(path.replace("\\", "/"))

    def _open_save_dir(self):
        path = (self.save_edit.text() or "").strip() or os.path.expanduser("~/Downloads")
        try:
            if not os.path.isdir(path):
                os.makedirs(path, exist_ok=True)
        except Exception:
            pass
        try:
            if os.path.isdir(path):
                os.startfile(path)  # noqa: PTH
        except Exception as e:
            self._log(f"打开目录失败：{e}", "warn")

    # ── cards ─────────────────────────────────────────────────────────────────

    def _clear_cards(self):
        self.btn_all.setEnabled(False)
        self.btn_none.setEnabled(False)
        self.btn_dl_sel.setEnabled(False)
        self._show_empty_card()

    def _fill_cards(self, items: List[MediaItem]):
        for c in list(self._cards):
            try:
                c.deleteLater()
            except Exception:
                pass
        self._cards = []
        self._clear_card_layout()
        self._empty_card = None
        self._media_items = list(items or [])
        self._quality_options = []
        self._quality_cards = []
        for i, it in enumerate(self._media_items):
            card = MediaCard(it)
            self._card_layout.addWidget(card)
            self._cards.append(card)
            self._quality_cards.append(card)
            self._quality_options.append((it.label or f"画质{i+1}", it.url or "", False))
        self._card_layout.addStretch(1)
        enabled = bool(self._cards)
        for b in (self.btn_all, self.btn_none, self.btn_dl_sel):
            b.setEnabled(enabled)

    def _select_all(self):
        for c in self._cards:
            c.chk.setChecked(True)

    def _deselect_all(self):
        for c in self._cards:
            c.chk.setChecked(False)

    # ── clipboard / handoff ───────────────────────────────────────────────────

    def _clipboard_raw(self) -> str:
        try:
            return QApplication.clipboard().text() or ""
        except Exception:
            return ""

    def _clipboard_url(self) -> str:
        text = self._clipboard_raw()
        if not text:
            return ""
        url = extract_jimeng_url(text)
        return text if url else ""

    @staticmethod
    def _looks_like_douyin(text: str) -> bool:
        t = (text or "").lower()
        return ("douyin.com" in t) or ("tiktok.com" in t) or ("iesdouyin.com" in t)

    @staticmethod
    def _looks_like_bilibili(text: str) -> bool:
        t = (text or "").lower()
        if "bilibili.com" in t or "b23.tv" in t or "bili2233.cn" in t:
            return True
        return bool(re.search(r"\bBV[0-9A-Za-z]{10}\b|\bav\d+\b", text or "", re.I))

    @staticmethod
    def _looks_like_youtube(text: str) -> bool:
        t = (text or "").lower()
        return ("youtube.com" in t) or ("youtu.be" in t)

    @staticmethod
    def _looks_like_liblib(text: str) -> bool:
        t = (text or "").lower()
        return ("liblib.tv" in t) and ("/detail/" in t)

    def _find_page_video(self):
        w = self.parent()
        while w is not None:
            if w.__class__.__name__ == "PageVideo":
                return w
            w = w.parent()
        return None

    def _start_flow(self, use_clipboard=True):
        text = ""
        raw_clip = self._clipboard_raw() if use_clipboard else ""
        if use_clipboard:
            text = self._clipboard_url()
            if text:
                self.url_edit.setText(text)
                self._log("已从剪贴板读取链接", "ok")
        if not text:
            text = self.url_edit.text().strip()

        probe = text or raw_clip

        def _is_jimeng(s: str) -> bool:
            return bool(extract_jimeng_url(s) or looks_like_jimeng(s))

        if probe and self._looks_like_douyin(probe) and not _is_jimeng(probe):
            handoff = text or raw_clip
            self._log("检测到抖音/TikTok 链接，已自动切换到「抖音」分页…", "ok")
            pv = self._find_page_video()
            if pv is not None and hasattr(pv, "handoff_to_douyin"):
                pv.handoff_to_douyin(handoff, auto_start=True)
            return

        if probe and self._looks_like_bilibili(probe) and not _is_jimeng(probe):
            handoff = text or raw_clip
            self._log("检测到 B站 链接，已自动切换到「B站」分页…", "ok")
            pv = self._find_page_video()
            if pv is not None and hasattr(pv, "handoff_to_bilibili"):
                pv.handoff_to_bilibili(handoff, auto_start=True)
            return

        if probe and self._looks_like_youtube(probe) and not _is_jimeng(probe):
            handoff = text or raw_clip
            self._log("检测到 YouTube 链接，已自动切换到「YouTube」分页…", "ok")
            pv = self._find_page_video()
            if pv is not None and hasattr(pv, "handoff_to_youtube"):
                pv.handoff_to_youtube(handoff, auto_start=True)
            return

        if probe and self._looks_like_liblib(probe) and not _is_jimeng(probe):
            handoff = text or raw_clip
            self._log("检测到 LibTV 链接，已自动切换到「LibTV」分页…", "ok")
            pv = self._find_page_video()
            if pv is not None and hasattr(pv, "handoff_to_liblib"):
                pv.handoff_to_liblib(handoff, auto_start=True)
            return

        if not text:
            self._log("剪贴板里没有即梦链接，输入框也是空的", "warn")
            self._set_status("没有链接", "#f97316")
            return

        self._auto_dl = True
        self._parse(text)

    def _parse(self, url_text=None):
        if self._parse_worker and self._parse_worker.isRunning():
            self._log("正在解析中，请稍候…", "warn")
            return
        if self._dl_worker and self._dl_worker.isRunning():
            self._log("正在下载中，请先等待或取消", "warn")
            return
        if not isinstance(url_text, str) or not url_text.strip():
            url_text = self.url_edit.text()
        url_text = (url_text or "").strip()
        if not url_text:
            self._log("请先粘贴视频链接", "warn")
            return

        self.btn_dl_sel.setEnabled(False)
        self.progress.setValue(0)
        self._set_status(batch_progress_prefix(self) + "解析中…", "#f97316")
        self._log("开始解析…")
        self._clear_cards()
        self._emit_nav_progress(True, mode="busy")

        self._parse_worker = ParseWorker(url_text)
        self._parse_worker.ok.connect(self._on_parse_ok)
        self._parse_worker.err.connect(self._on_parse_err)
        self._parse_worker.log.connect(self._log)
        self._parse_worker.start()

    def _on_parse_ok(self, info: dict, items: list):
        self._info = info or {}
        self._fill_cards(items or [])
        title = (self._info.get("title") or "")[:40]
        self._set_status(f"解析成功 ✓ · {title}", "#22c55e")
        if self._auto_dl:
            self._auto_dl = False
            self._log("自动开始下载已选项…", "ok")
            self._download_selected()
        else:
            self._emit_nav_progress(False)

    def _on_parse_err(self, msg: str):
        self._last_download_ok = False
        self._last_batch_note = "失败"
        self._auto_dl = False
        self.btn_dl_sel.setEnabled(False)
        self._set_status("解析失败 ✗", "#ef4444")
        self._log("━" * 40, "err")
        for line in (msg or "").split("\n"):
            if line.strip():
                self._log(f"  {line.strip()}", "err")
        self._log("━" * 40, "err")
        self._emit_nav_progress(False)
        self._report_batch_item_done()

    def _make_filename(self, label: str) -> str:
        info = self._info or {}
        author = _safe_filename_part(info.get("author") or "", 24)
        title = _safe_filename_part(info.get("title") or "", 36)
        qual = _safe_filename_part(label.split("·")[0].strip() if label else "video", 12)
        return f"{author}_{title}_{qual}.mp4"

    def _download_selected(self):
        jobs = []
        for c in self._cards:
            if not c.is_checked():
                continue
            it = c.item
            jobs.append({
                "label": it.label or "视频",
                "url": it.url,
                "filename": self._make_filename(it.label or "视频"),
            })
        if not jobs:
            self._log("请先勾选至少一个画质", "warn")
            self._set_status("未勾选画质", "#f97316")
            if self._auto_dl:
                self._auto_dl = False
            # 批处理：无勾选视为失败
            if in_batch_download(self):
                self._last_download_ok = False
                self._last_batch_note = "失败"
                self._emit_nav_progress(False)
                self._report_batch_item_done()
            return
        self._start_download(jobs)

    def _start_download(self, jobs: list):
        if self._dl_worker and self._dl_worker.isRunning():
            self._log("正在下载中，请稍候…", "warn")
            return
        save_dir = (self.save_edit.text() or "").strip() or os.path.expanduser("~/Downloads")
        try:
            os.makedirs(save_dir, exist_ok=True)
        except Exception as e:
            self._log(f"无法创建保存目录：{e}", "err")
            self._last_download_ok = False
            self._last_batch_note = "失败"
            self._report_batch_item_done()
            return

        self._cancel_flag[0] = False
        self._dl_total = len(jobs)
        self._last_download_ok = False
        self._last_batch_note = ""
        self.btn_dl_sel.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setValue(0)
        self._set_status(batch_progress_prefix(self) + f"准备下载 {len(jobs)} 项…", "#f97316")
        self._emit_nav_progress(True, 0, mode="progress")

        self._dl_worker = DownloadWorker(jobs, save_dir, self._cancel_flag)
        self._dl_worker.progress.connect(self._on_dl_progress)
        self._dl_worker.log.connect(self._log)
        self._dl_worker.done.connect(self._on_dl_done)
        self._dl_worker.cancelled.connect(self._on_dl_cancel)
        self._dl_worker.start()

    def _on_dl_progress(self, pct: int):
        try:
            pct = int(pct)
        except (TypeError, ValueError):
            pct = 0
        pct = max(0, min(100, pct))
        self.progress.setValue(pct)
        total = int(getattr(self, "_dl_total", 0) or 0)
        text = f"下载中 {pct}%" + (f" · 共 {total} 项" if total else "")
        self._set_status(text, self._progress_gradient_color(pct))

    def _on_dl_done(self, save_dir, success_count=0):
        total = int(getattr(self, "_dl_total", 0) or 0) or int(success_count or 0)
        ok_n = int(success_count or 0)
        fail_n = max(0, total - ok_n)
        self._last_download_ok = ok_n > 0
        self._last_batch_note = "成功" if ok_n > 0 else "失败"
        self.progress.setValue(100)
        if ok_n <= 0:
            summary = f"完成：全部失败 0/{total} 项"
            color = "#ef4444"
            level = "err"
        elif fail_n:
            summary = f"完成：成功 {ok_n}/{total} 项 · 失败 {fail_n}"
            color = "#f97316"
            level = "warn"
        else:
            summary = f"完成：成功 {ok_n}/{total} 项 · 已保存"
            color = "#22c55e"
            level = "ok"
        self._set_status(summary, color)
        self._log(f"{summary} · 目录：{save_dir}", level)
        if ok_n > 0 and not in_batch_download(self):
            show_cursor_toast("下载", "完成", accent="ok")
        self._reset_btns()
        self._report_batch_item_done()

    def _on_dl_cancel(self):
        self._last_download_ok = False
        self._last_batch_note = "失败"
        self._set_status("已取消", "#ef4444")
        self._emit_nav_progress(False)
        self._reset_btns()
        self._report_batch_item_done()

    def _reset_btns(self):
        self.btn_dl_sel.setEnabled(bool(self._cards))
        self.btn_cancel.setEnabled(False)
        self._emit_nav_progress(False)

    def _report_batch_item_done(self):
        """自动下载：本条结束显式上报（校验成功才删记录行）。"""
        w = self
        try:
            while w is not None:
                if hasattr(w, "notify_batch_item_done") and getattr(w, "_batch_mode", False):
                    w.notify_batch_item_done(
                        bool(getattr(self, "_last_download_ok", False)),
                        note=(getattr(self, "_last_batch_note", None) or ""),
                        source="jimeng",
                    )
                    return
                w = w.parent()
        except Exception:
            pass

    def export_settings(self) -> dict:
        return {
            "save_path": (self.save_edit.text() or "").strip(),
            "cookie_path": (self.ck_path.text() or "").strip() if hasattr(self, "ck_path") else "",
        }

    def apply_settings(self, d: dict):
        if not isinstance(d, dict):
            return
        path = (d.get("save_path") or "").strip()
        if path:
            self.save_edit.blockSignals(True)
            try:
                self.save_edit.setText(path.replace("\\", "/"))
            finally:
                self.save_edit.blockSignals(False)
        ck = (d.get("cookie_path") or "").strip()
        if ck and os.path.isfile(ck) and hasattr(self, "ck_path"):
            self.ck_path.blockSignals(True)
            try:
                self.ck_path.setText(ck.replace("\\", "/"))
            finally:
                self.ck_path.blockSignals(False)

    def shutdown(self):
        from utils.qthread_util import stop_qthreads
        stop_qthreads(
            (getattr(self, "_parse_worker", None), getattr(self, "_dl_worker", None)),
            cancel_flag=getattr(self, "_cancel_flag", None),
        )
        self._emit_nav_progress(False)
