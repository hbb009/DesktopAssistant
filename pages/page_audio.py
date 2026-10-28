# pages/page_audio.py
# 音频下载：版式对齐视频下载的抖音页，目前只有「汽水音乐」一页。
# 预下载角标、链接下简易进度线、音频媒体卡都走抖音页同一套控件。

from __future__ import annotations

import os

from PyQt5.QtCore import Qt, QTimer, QSize, QRectF, pyqtSignal
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QBrush, QPainterPath
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QTabBar, QLabel,
    QDialog, QFileDialog, QPushButton, QSizePolicy, QTextEdit,
)

from styles.style_all import (
    theme, fmt, tk, TAB_QSS, VIDEO_TAB_QSS, apply_transparent_surface,
    apply_mini_button, message_box_info, message_box_warn,
    CARD_LEFT_GAP, CARD_RIGHT_GAP, MEDIUM_BUTTON_H,
)
from pages.page_douyin import (
    PageDouyin,
    ParseWorker,
    is_qishui_url,
    extract_url,
    _mark_best_audio_only,
    make_filename,
    _ElideLabel,
)
from utils.cursor_toast import set_batch_progress_note, show_cursor_toast
from utils.logger import get_logger
from utils.qishui_records import (
    KIND_ORDER,
    active_names,
    add_records,
    classify_filename,
    format_entries,
    hint_text,
    load_entries,
    merge_external,
    name_aliases,
    parse_entries,
    read_external,
    save_entries,
    scan_dir,
)
from utils.gallery_records import records_path as _gallery_records_path
from utils.records_dialog_ui import RecordsTotalBar

log = get_logger(__name__)


def open_qishui_page(widget, text: str, auto_start: bool = True) -> bool:
    """从其它页把汽水链接交到「音频下载」。会切到该侧栏页。"""
    win = widget.window() if widget is not None else None
    if win is None:
        return False
    page = getattr(win, "page_audio", None)
    if page is None and hasattr(win, "_ensure_page"):
        try:
            page = win._ensure_page("audio")
        except Exception:
            log.exception("创建音频下载页失败")
            page = None
    if page is None or not hasattr(page, "handoff"):
        return False
    btn = getattr(win, "btn_audio", None)
    try:
        idx = win.stack.indexOf(page)
        if idx >= 0 and btn is not None and hasattr(win, "_switch"):
            win._switch(idx, btn)
    except Exception:
        log.exception("切换到音频下载失败")
    page.handoff(text, auto_start=auto_start)
    return True


def _apply_tab_page_bg(widget, object_name: str):
    widget.setObjectName(object_name)
    widget.setAttribute(Qt.WA_StyledBackground, True)
    widget.setAutoFillBackground(True)
    bg = tk("panel_2")
    widget.setStyleSheet(f"#{object_name}{{background:{bg};border:none;}}")


def _paint_qishui_icon(size: int = 22, selected: bool = True, dark: bool = True) -> QIcon:
    """汽水页签：青绿圆角底 + 单音符。未选中时降饱和。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = float(size)
    m = s * 0.06
    if selected:
        base = QColor("#0f766e") if dark else QColor("#14b8a6")
        rim = QColor("#5eead4")
        rim.setAlpha(220)
        fg = QColor("#ecfdf5")
    else:
        base = QColor("#3a3f4a") if dark else QColor("#c5ccd6")
        base.setAlpha(120 if dark else 140)
        rim = QColor("#6b7280")
        rim.setAlpha(90)
        fg = QColor("#9ca3af")
        fg.setAlpha(180)
    p.setPen(QPen(rim, max(1.0, s * 0.06)))
    p.setBrush(QBrush(base))
    p.drawRoundedRect(QRectF(m, m, s - 2 * m, s - 2 * m), s * 0.22, s * 0.22)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(fg))
    p.drawEllipse(QRectF(s * 0.28, s * 0.56, s * 0.22, s * 0.16))
    p.drawRoundedRect(QRectF(s * 0.46, s * 0.28, s * 0.07, s * 0.36), 1.0, 1.0)
    flag = QPainterPath()
    flag.moveTo(s * 0.53, s * 0.30)
    flag.cubicTo(s * 0.74, s * 0.32, s * 0.72, s * 0.50, s * 0.53, s * 0.46)
    flag.closeSubpath()
    p.drawPath(flag)
    p.end()
    return QIcon(pm)


_RECORD_SKIP_YELLOW = "#eab308"
_RECORD_SKIP_TIP = (
    "开：按视频、音频查已下记录，命中的那一项跳过\n"
    "关：不查记录，照常下载；磁盘上的同名文件仍会自动改名"
)
_RECORD_PLACEHOLDER = (
    "# 汽水音乐\n"
    "[视频]\n"
    "[桌面助手][作者]标题_视频（汽水）.mp4\n"
    "[音频]\n"
    "[桌面助手][作者]标题_音频（汽水）.mp3\n"
)


class PageQishui(PageDouyin):
    """汽水音乐：界面与抖音页相同，只接受汽水链接。

    已下记录按视频 / 音频分开，只跳过命中的那一项。
    """

    def __init__(self):
        super().__init__()
        try:
            self.url_edit.setPlaceholderText("粘贴汽水音乐链接→回车自动解析下载")
        except Exception:
            pass
        try:
            self.lbl_batch_hint.setText("粘贴链接后按回车开始解析")
        except Exception:
            pass
        lay = self.layout()
        if lay is not None:
            lay.addWidget(self._build_record_bar())
        self._refresh_record_hint()
        self._show_empty_card()

    def _fill_cards(self, items):
        _mark_best_audio_only(items)
        super()._fill_cards(items)

    def _on_parse_ok(self, item, media):
        _mark_best_audio_only(media)
        self._video_info = item
        self.btn_dl_sel.setEnabled(True)
        self._set_status("解析成功 ✓", "#22c55e")
        self._fill_cards(media)
        if self._auto_dl:
            self._auto_dl = False
            picked = [
                c.item for c in self._cards
                if c.is_checked() and getattr(c.item, "kind", "") == "audio"
            ]
            if picked:
                self._log("自动开始下载…", "ok")
                self._start_download(picked)
            else:
                self._log("没有可默认下载的音频", "warn")
                self._emit_nav_progress(False)
        else:
            self._emit_nav_progress(False)

    def _make_empty_card(self):
        card = super()._make_empty_card()
        for w in card.findChildren(QLabel):
            w.setText("粘贴汽水音乐链接 →\n回车自动解析")
            break
        return card

    def _effective_cookie_path(self) -> str:
        """本页 Cookie 优先；没配时用抖音 Cookie（同属 douyin 域）。"""
        ck = ""
        try:
            ck = (self.ck_path.text() or "").strip()
        except Exception:
            ck = ""
        if ck and os.path.isfile(ck):
            return ck
        win = self.window()
        pv = getattr(win, "page_douyin", None)
        sub = getattr(pv, "page_douyin", None) if pv is not None else None
        alt = ""
        edit = getattr(sub, "ck_path", None) if sub is not None else None
        if edit is not None:
            try:
                alt = (edit.text() or "").strip()
            except Exception:
                alt = ""
        if alt and os.path.isfile(alt):
            return alt
        return ""

    def _start_flow(self, use_clipboard=True):
        text = ""
        raw_clip = self._clipboard_raw() if use_clipboard else ""
        if use_clipboard and raw_clip and is_qishui_url(raw_clip):
            text = raw_clip.strip()
            self.url_edit.setText(text)
            self._log("已从剪贴板读取链接", "ok")
        if not text:
            text = (self.url_edit.text() or "").strip()
        probe = text or (raw_clip or "")
        url = ""
        if probe and is_qishui_url(probe):
            try:
                url = extract_url(probe)
            except Exception:
                url = probe if is_qishui_url(probe) else ""
        if not url or not is_qishui_url(url):
            if not probe:
                self._log("剪贴板里没有汽水音乐链接，输入框也是空的", "warn")
                self._set_status("没有链接", "#f97316")
            else:
                self._log(
                    "请粘贴汽水音乐链接（music.douyin.com/qishui 或 qishui.douyin.com）",
                    "warn",
                )
                self._set_status("不是汽水音乐链接", "#f97316")
            return
        self.url_edit.setText(url)
        if not self._effective_cookie_path():
            self._log("未配置 Cookie，公开曲目仍会尝试解析", "warn")
        elif not (self.ck_path.text() or "").strip():
            self._log("未单独配置汽水 Cookie，改用抖音 Cookie", "info")
        self._auto_dl = True
        self._parse(url)

    def _parse(self, url_text=None):
        if not isinstance(url_text, str) or not url_text.strip():
            url_text = self.url_edit.text()
        url_text = (url_text or "").strip()
        if not url_text:
            self._log("请先粘贴汽水音乐链接", "warn")
            return
        if self._parse_worker and self._parse_worker.isRunning():
            self._log("正在解析中，请稍候…", "warn")
            return
        if self._dl_worker and self._dl_worker.isRunning():
            self._log("正在下载中，请先等待或取消", "warn")
            return
        self.btn_dl_sel.setEnabled(False)
        self._clear_cards()
        self.progress.setValue(0)
        self._set_status("解析中…", "#f97316")
        self._log("开始解析…")
        self._emit_nav_progress(True, mode="busy")
        self._parse_worker = ParseWorker(url_text, self._effective_cookie_path())
        self._parse_worker.ok.connect(self._on_parse_ok)
        self._parse_worker.err.connect(self._on_parse_err)
        self._parse_worker.log.connect(self._log)
        self._parse_worker.start()

    def _canonical_name(self, item) -> str:
        vi = self._video_info or {}
        author = (
            vi.get("author", {}).get("nickname", "未知作者")
            if isinstance(vi.get("author"), dict) else "未知作者"
        )
        desc = vi.get("desc", "无标题")
        create_time = vi.get("create_time", 0)
        label = getattr(item, "label", "") or "media"
        ext = getattr(item, "ext", "") or ".mp4"
        if not str(ext).startswith("."):
            ext = "." + str(ext)
        return make_filename(author, desc, create_time, label, ext)

    def _record_kind(self, item) -> str:
        label = getattr(item, "label", "") or ""
        kind = getattr(item, "kind", "") or ""
        if kind == "audio" or "音频" in label:
            return "音频"
        return "视频"

    def _start_download(self, selected):
        selected = list(selected or [])
        if not selected:
            return super()._start_download(selected)
        for it in selected:
            try:
                it._record_name = ""
            except Exception:
                pass
        kept, skipped = self._partition_recorded(selected)
        if skipped and not kept:
            self._mark_already_downloaded(skipped)
            return
        if skipped:
            self._log(
                "记录中已有，跳过 %d 项：%s"
                % (len(skipped), "、".join(self._record_brief(it) for it in skipped)),
                "ok",
            )
        elif kept and not self._record_skip_enabled():
            known_names = getattr(self, "_record_known", set())
            known = [
                it for it in kept
                if name_aliases(self._canonical_name(it)) & known_names
            ]
            if known:
                self._log(
                    "记录中已有 %d 项，防重已关，继续：%s"
                    % (len(known), "、".join(self._record_brief(it) for it in known)),
                    "warn",
                )
        for it in kept:
            try:
                it._record_name = self._canonical_name(it)
            except Exception:
                pass
        super()._start_download(kept)

    def _record_brief(self, item) -> str:
        kind = self._record_kind(item)
        name = ""
        try:
            name = self._canonical_name(item)
        except Exception:
            name = getattr(item, "label", "") or ""
        if kind and name:
            return "%s（%s）" % (kind, name)
        return name or kind or "这一项"

    def _partition_recorded(self, items):
        try:
            known = active_names()
        except Exception:
            log.exception("读取汽水已下记录失败")
            known = set()
        self._record_known = known
        if not self._record_skip_enabled():
            return list(items), []
        kept, skipped = [], []
        for it in items:
            name = self._canonical_name(it)
            if name and name_aliases(name) & known:
                skipped.append(it)
            else:
                kept.append(it)
        return kept, skipped

    def _mark_already_downloaded(self, skipped):
        briefs = [self._record_brief(it) for it in (skipped or [])]
        self._log("━" * 36, "info")
        self._log("📋 已下载记录：%s" % "、".join(briefs), "info")
        self._log("这些项目已在下载记录中，跳过重复下载", "ok")
        self._log("━" * 36, "info")
        self._set_status("已跳过 · 下载记录中已有", "#22c55e")
        self._last_download_ok = True
        self._last_batch_note = "已下载过"
        self._auto_dl = False
        if not set_batch_progress_note("已下载过"):
            show_cursor_toast("下载", "已下载过", accent="ok")
        self._reset_btns()
        self._report_batch_item_done()

    def _commit_saved_records(self) -> list:
        worker = getattr(self, "_dl_worker", None)
        names = list(getattr(worker, "_saved_records", []) or []) if worker is not None else []
        if worker is not None:
            worker._saved_records = []
        recs = []
        for name in names:
            name = (name or "").strip()
            if not name:
                continue
            info = classify_filename(name)
            if not info:
                continue
            _plat, kind, qual, suspect = info
            recs.append({
                "kind": kind,
                "quality": qual,
                "name": name,
                "suspect": suspect,
            })
        if not recs:
            return []
        try:
            added = add_records(recs)
        except Exception:
            log.exception("写入汽水已下记录失败")
            return []
        if added:
            self._log("防重记录已更新 %d 条" % len(added), "ok")
            self._refresh_record_hint(added)
        return added

    def _on_dl_done(self, save_dir, success_count=0):
        self._commit_saved_records()
        super()._on_dl_done(save_dir, success_count)

    def _on_dl_cancel(self):
        self._commit_saved_records()
        super()._on_dl_cancel()

    def _record_skip_widgets(self):
        out = []
        for attr in ("sw_record_skip", "sw_record_skip_bar"):
            w = getattr(self, attr, None)
            if w is not None:
                out.append(w)
        return out

    def _record_skip_enabled(self) -> bool:
        for w in self._record_skip_widgets():
            try:
                return bool(w.isChecked())
            except Exception:
                continue
        return True

    def _set_record_skip(self, on: bool) -> None:
        on = bool(on)
        for w in self._record_skip_widgets():
            try:
                if bool(w.isChecked()) != on:
                    w.setChecked(on)
            except Exception:
                pass
        self._refresh_record_hint()

    def _on_record_skip_toggled(self, src=None):
        if src is not None:
            try:
                on = bool(src.isChecked())
            except Exception:
                on = self._record_skip_enabled()
        else:
            on = self._record_skip_enabled()
        self._set_record_skip(on)
        self._log(
            "已下记录防重：" + ("开（命中的项目跳过）" if on else "关（不查记录，继续下载）"),
            "info",
        )
        self._schedule_prefs_save()

    def _schedule_prefs_save(self):
        w = self
        while w is not None:
            fn = getattr(w, "_schedule_save_user_prefs", None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
                return
            w = w.parent()

    def _refresh_record_hint(self, added_names=None):
        lbl = getattr(self, "lbl_record_hint", None)
        if lbl is None:
            return
        try:
            text = hint_text(
                added_names=added_names,
                skip_on=self._record_skip_enabled(),
            )
        except Exception:
            log.exception("刷新汽水已下记录条数失败")
            return
        if hasattr(lbl, "setFullText"):
            lbl.setFullText(text)
        else:
            lbl.setText(text)
        try:
            lbl.setToolTip(text)
        except Exception:
            pass

    def _build_record_bar(self) -> QWidget:
        bar = QWidget(self)
        bar.setObjectName("QishuiRecordBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setStyleSheet("#QishuiRecordBar{background:transparent;border:none;}")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(CARD_LEFT_GAP, 2, CARD_RIGHT_GAP, 2)
        lay.setSpacing(8)

        hint = _ElideLabel("已有 0条")
        hint.setFixedHeight(MEDIUM_BUTTON_H)
        hint.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hint.setObjectName("QishuiRecordHint")
        hint.setStyleSheet(
            "QLabel#QishuiRecordHint{font-size:12px;color:#94a3b8;padding:0 4px;"
            "background:transparent;border:none;}"
        )
        self.lbl_record_hint = hint
        lay.addWidget(hint, 1)

        sw_bar = None
        try:
            from ui_main import SideToggleSwitch
            sw_bar = SideToggleSwitch(on_color=_RECORD_SKIP_YELLOW, parent=bar)
        except Exception:
            log.exception("创建汽水防重开关失败")
        if sw_bar is not None:
            sw_bar.setChecked(True)
            sw_bar.setToolTip(_RECORD_SKIP_TIP)
            sw_bar.clicked.connect(lambda: self._on_record_skip_toggled(sw_bar))
            self.sw_record_skip_bar = sw_bar
            lay.addWidget(sw_bar, 0, Qt.AlignVCenter)
        else:
            self.sw_record_skip_bar = None

        btn = apply_mini_button(QPushButton("已下记录"))
        btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        btn.setMinimumWidth(max(72, btn.sizeHint().width()))
        btn.setToolTip("按视频、音频查看和编辑汽水音乐已下载记录")
        btn.clicked.connect(self._open_records_dialog)
        self.btn_records = btn
        lay.addWidget(btn, 0, Qt.AlignVCenter)
        self._record_bar = bar
        return bar

    def _scan_save_dir(self, folder: str, *, log_result: bool = False) -> list:
        try:
            added = scan_dir(folder)
        except Exception:
            log.exception("扫描汽水保存目录失败")
            added = []
        suspects = list(getattr(scan_dir, "last_suspects", []) or [])
        self._refresh_record_hint(added if added else None)
        if log_result:
            if added or suspects:
                bits = []
                if added:
                    bits.append("新增 %d 条" % len(added))
                if suspects:
                    bits.append("待核 %d 条" % len(suspects))
                self._log("已下载：" + "，".join(bits), "ok")
            else:
                self._log("已下载：未发现新记录", "ok")
        return added

    def _open_records_dialog(self):
        _open_qishui_records_dialog(self)

    def export_settings(self) -> dict:
        data = super().export_settings()
        data["check_records"] = self._record_skip_enabled()
        return data

    def apply_settings(self, d: dict):
        super().apply_settings(d if isinstance(d, dict) else {})
        if isinstance(d, dict) and "check_records" in d:
            self._set_record_skip(bool(d.get("check_records")))
        path = ""
        try:
            path = (self.save_edit.text() or "").strip()
        except Exception:
            path = ""
        if path:
            self._scan_save_dir(path, log_result=False)

    def _report_batch_item_done(self):
        """批处理引擎在视频页上，本页不在它的父链里，要到主窗口去找。"""
        payload = (
            bool(getattr(self, "_last_download_ok", False)),
            (getattr(self, "_last_batch_note", None) or ""),
            "qishui",
        )
        w = self
        try:
            while w is not None:
                if hasattr(w, "notify_batch_item_done") and getattr(w, "_batch_mode", False):
                    w.notify_batch_item_done(*payload)
                    return
                w = w.parent()
        except Exception:
            pass
        win = self.window()
        pv = getattr(win, "page_douyin", None)
        if pv is not None and hasattr(pv, "notify_batch_item_done") and getattr(pv, "_batch_mode", False):
            try:
                pv.notify_batch_item_done(*payload)
            except Exception:
                log.exception("汽水批处理结果上报失败")


def _open_qishui_records_dialog(page):
    """已下记录弹窗。正文是 # 汽水音乐，下面按视频 / 音频分开。"""
    dlg = QDialog(page)
    dlg.setWindowTitle("已下记录")
    try:
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
    except Exception:
        pass
    dlg.resize(780, 440)
    dlg.setMinimumSize(620, 320)
    try:
        if page is not None and page.styleSheet():
            dlg.setStyleSheet(page.styleSheet())
    except Exception:
        pass

    v = QVBoxLayout(dlg)
    v.setContentsMargins(12, 12, 12, 12)
    v.setSpacing(10)

    preview = QTextEdit()
    preview.setAcceptRichText(False)
    preview.setPlaceholderText(_RECORD_PLACEHOLDER)
    preview.setStyleSheet(
        f"QTextEdit{{background:{tk('input_bg')};color:{tk('text')};"
        f"border:1px solid {tk('border')};border-radius:6px;font-size:13px;}}"
    )
    preview.setPlainText(format_entries(load_entries()))
    v.addWidget(preview, 1)

    _rec_file = _gallery_records_path("qishui")
    lbl_total = RecordsTotalBar(_rec_file, dlg)
    v.addWidget(lbl_total)

    def _editor_entries():
        return parse_entries(preview.toPlainText())

    def _set_total_text(extra: str = ""):
        rows = _editor_entries()
        n = sum(1 for e in rows if not e.get("suspect"))
        suspects = sum(1 for e in rows if e.get("suspect"))
        text = f"共 {n} 条"
        if suspects:
            text += f" · 待核 {suspects}"
        counts = {}
        for e in rows:
            if e.get("suspect"):
                continue
            kind = e.get("kind") or ""
            if kind in KIND_ORDER:
                counts[kind] = counts.get(kind, 0) + 1
        bits = [f"{kind} {counts[kind]}" for kind in KIND_ORDER if counts.get(kind)]
        if bits:
            text += " · " + " · ".join(bits)
        lbl_total.set_count_text(text + (extra or ""))

    preview.textChanged.connect(lambda: _set_total_text())
    _set_total_text()

    def _reload():
        preview.blockSignals(True)
        try:
            preview.setPlainText(format_entries(load_entries()))
        finally:
            preview.blockSignals(False)
        _set_total_text()

    def _make_btn(text, bg=None, fg=None, bd=None, hover_bg=None):
        b = QPushButton(text)
        b.setFixedHeight(34)
        b.setCursor(Qt.PointingHandCursor)
        if bg:
            qss = (
                f"QPushButton{{background:{bg};color:{fg or '#ffffff'};"
                f"border:1px solid {bd or bg};border-radius:6px;font-size:13px;}}"
            )
            if hover_bg:
                qss += f"QPushButton:hover{{background:{hover_bg};}}"
        else:
            qss = (
                f"QPushButton{{background:{tk('input_bg')};color:{tk('text')};"
                f"border:1px solid {tk('border')};border-radius:6px;font-size:13px;}}"
            )
            if hover_bg:
                qss += f"QPushButton:hover{{background:{hover_bg};}}"
        b.setStyleSheet(qss)
        return b

    def _write_editor():
        rows = _editor_entries()
        if not save_entries(rows):
            message_box_warn(dlg, "保存失败", "无法写入已下记录文件。")
            return False
        if hasattr(page, "_refresh_record_hint"):
            page._refresh_record_hint()
        return True

    def _do_add_dir():
        start = ""
        try:
            start = (page.save_edit.text() or "").strip()
        except Exception:
            start = ""
        if not start:
            start = os.path.expanduser("~/Downloads")
        folder = QFileDialog.getExistingDirectory(dlg, "选择要扫描的文件夹", start)
        if not folder:
            return
        if hasattr(page, "_scan_save_dir"):
            page._scan_save_dir(folder, log_result=True)
        _reload()

    def _do_add_file():
        def _tell(text, *, warn=False):
            fn = message_box_warn if warn else message_box_info
            QTimer.singleShot(0, lambda t=text, f=fn: f(dlg, "添加记录文件", t))

        try:
            start = os.path.join(os.path.expanduser("~"), "Desktop")
            if not os.path.isdir(start):
                start = os.path.expanduser("~")
            path, _ = QFileDialog.getOpenFileName(
                dlg, "选择记录文件", start,
                "文本文件 (*.txt);;所有文件 (*.*)",
            )
            path = (path or "").strip().strip('"')
            if not path:
                return
            incoming = read_external(path)
            if incoming is None:
                _tell(f"无法读取该文件：\n{path}", warn=True)
                return
            if not incoming:
                _tell("文件里没有可用记录（需要每行一条文件名，或按 # 汽水音乐 分段）。", warn=True)
                return
            current = _editor_entries()
            merged, added, _changed = merge_external(current, incoming)
            preview.blockSignals(True)
            try:
                preview.setPlainText(format_entries(merged))
            finally:
                preview.blockSignals(False)
            if not save_entries(merged):
                _tell("已读入编辑框，但写入记录文件失败。", warn=True)
                _set_total_text()
                return
            if hasattr(page, "_refresh_record_hint"):
                page._refresh_record_hint(added)
            try:
                if added:
                    page._log("已下载：从文件新增 %d 条记录" % len(added), "ok")
                else:
                    page._log("已下载：文件中无新记录", "ok")
            except Exception:
                pass
            skipped = max(0, len(incoming) - len(added))
            _set_total_text(f" · 本次新增 {len(added)} 条，跳过 {skipped} 条已有")
            _tell(f"已读 {len(incoming)} 条\n新增 {len(added)} 条\n跳过 {skipped} 条（已有）")
        except Exception as e:
            log.exception("添加汽水记录文件失败")
            _tell(f"导入出错：{e}", warn=True)

    def _scroll_to_sep():
        text = preview.toPlainText()
        idx = text.find("-----")
        if idx < 0:
            return
        cur = preview.textCursor()
        cur.setPosition(idx)
        preview.setTextCursor(cur)
        preview.ensureCursorVisible()

    def _do_organize():
        rows = _editor_entries()
        preview.blockSignals(True)
        try:
            preview.setPlainText(format_entries(rows))
        finally:
            preview.blockSignals(False)
        if not save_entries(_editor_entries()):
            message_box_warn(dlg, "保存失败", "无法写回记录文件。")
            _set_total_text()
            return
        if hasattr(page, "_refresh_record_hint"):
            page._refresh_record_hint()
        _set_total_text()
        if any(e.get("suspect") for e in rows):
            QTimer.singleShot(0, _scroll_to_sep)

    def _do_save_close():
        if _write_editor():
            dlg.accept()

    btn_row = QHBoxLayout()
    btn_row.setSpacing(8)
    btn_add = _make_btn(
        "添加已经下目录",
        bg="#1d4ed8", fg="#dbeafe", bd="#2563eb", hover_bg="#2563eb",
    )
    btn_add.setToolTip(
        "扫描文件夹里的视频和音频。带「_视频（汽水） / _音频（汽水）」的按这个标记归类"
    )
    btn_add.clicked.connect(_do_add_dir)
    btn_row.addWidget(btn_add)

    btn_add_file = _make_btn(
        "添加记录文件",
        bg="#1d4ed8", fg="#dbeafe", bd="#2563eb", hover_bg="#2563eb",
    )
    btn_add_file.setToolTip("从其它 .txt 合并记录。可按 # 汽水音乐 分段，也可以一行一个文件名")
    btn_add_file.clicked.connect(_do_add_file)
    btn_row.addWidget(btn_add_file)

    btn_org = _make_btn(
        "整理记录",
        bg="#a16207", fg="#fef9c3", bd="#ca8a04", hover_bg="#ca8a04",
    )
    btn_org.setToolTip("按视频 / 音频重新分组，并把 _2、(2) 另存名挪到分隔线下面（不删除）")
    btn_org.clicked.connect(_do_organize)
    btn_row.addWidget(btn_org)

    btn_row.addStretch(1)

    btn_save = _make_btn(
        "保存并关闭",
        bg="#14532d", fg="#bbf7d0", bd="#166534", hover_bg="#166534",
    )
    btn_save.clicked.connect(_do_save_close)
    btn_row.addWidget(btn_save)

    btn_close = _make_btn("关闭")
    btn_close.clicked.connect(dlg.reject)
    btn_row.addWidget(btn_close)
    v.addLayout(btn_row)

    dlg.exec_()


class PageAudio(QWidget):
    """音频下载：单页签「汽水音乐」。"""

    nav_progress = pyqtSignal(bool, int, str)

    def __init__(self):
        super().__init__()
        apply_transparent_surface(self, "PageAudio")
        self.setStyleSheet(
            fmt(TAB_QSS + VIDEO_TAB_QSS)
            + "\n#PageAudio{background:transparent;border:none;}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("VideoTabWidget")
        self.tabs.setIconSize(QSize(20, 20))
        try:
            from ui_main import install_tight_pre_dl_tab_bar
            install_tight_pre_dl_tab_bar(self.tabs)
        except Exception:
            log.exception("音频页签条安装失败")
        root.addWidget(self.tabs, 1)

        self.page_qishui = PageQishui()
        _apply_tab_page_bg(self.page_qishui, "TabQishui")
        self._pre_dl_tab_specs = (("qishui", "汽水音乐"),)
        self._pre_dl_counts = {"qishui": 0}
        self.tabs.addTab(self.page_qishui, "汽水音乐")
        self._pre_dl_badge_host = self._install_pre_dl_tab_badges()
        self._refresh_tab()
        self.tabs.currentChanged.connect(lambda _i: self._refresh_tab())
        self._install_record_skip_slot()
        try:
            self.page_qishui.nav_progress.connect(self._on_child_nav_progress)
        except Exception:
            log.exception("连接汽水进度线失败")
        theme.changed.connect(self.refresh_theme)

    def _install_pre_dl_tab_badges(self):
        from ui_main import TabPreDlBadgeHost
        return TabPreDlBadgeHost(
            self.tabs, getattr(self, "_pre_dl_tab_specs", ()), parent=self
        )

    def set_pre_dl_counts(self, counts: dict):
        counts = counts or {}
        try:
            n = max(0, int(counts.get("qishui", 0) or 0))
        except (TypeError, ValueError):
            n = 0
        self._pre_dl_counts["qishui"] = n
        host = getattr(self, "_pre_dl_badge_host", None)
        if host is not None:
            host.set_counts(self._pre_dl_counts)

    def set_batch_counts(self, counts: dict):
        counts = counts or {}
        try:
            n = max(0, int(counts.get("qishui", 0) or 0))
        except (TypeError, ValueError):
            n = 0
        host = getattr(self, "_pre_dl_badge_host", None)
        if host is not None and hasattr(host, "set_batch_counts"):
            host.set_batch_counts({"qishui": n})

    def _on_child_nav_progress(self, active: bool, pct: int, color: str):
        try:
            self.nav_progress.emit(bool(active), int(pct if pct is not None else 0), color or "")
        except Exception:
            log.exception("音频侧栏进度信号发送失败")

    def _refresh_tab(self):
        dark = bool(theme.is_dark)
        idx = self.tabs.currentIndex()
        self.tabs.setTabIcon(0, _paint_qishui_icon(22, selected=(idx == 0), dark=dark))
        self.tabs.setProperty("firstSelected", "true")
        st = self.tabs.style()
        if st is not None:
            st.unpolish(self.tabs)
            st.polish(self.tabs)
        self.tabs.update()
        host = getattr(self, "_pre_dl_badge_host", None)
        if host is not None:
            host.reposition()
        bar = self.tabs.tabBar()
        if bar is not None:
            place = getattr(bar, "_place_left_buttons", None)
            if callable(place):
                place()

    def _install_record_skip_slot(self):
        """汽水音乐页签左侧放正方形「防」，和页底防重开关是同一个状态。"""
        from ui_main import SidePreDlSlot

        page = getattr(self, "page_qishui", None)
        bar = self.tabs.tabBar() if self.tabs is not None else None
        if page is None or bar is None:
            return
        index = self.tabs.indexOf(page)
        if index < 0:
            return
        try:
            sw = SidePreDlSlot(parent=bar, glyph="防", on_color=_RECORD_SKIP_YELLOW)
            on = True
            if hasattr(page, "_record_skip_enabled"):
                on = bool(page._record_skip_enabled())
            sw.setChecked(on)
            sw.setToolTip(_RECORD_SKIP_TIP)
            sw.clicked.connect(lambda _=False, src=sw: page._on_record_skip_toggled(src))
            page.sw_record_skip = sw
            bar.setTabButton(index, QTabBar.LeftSide, sw)
            sw.bind_owner_tab(bar, index)
        except Exception:
            log.exception("安装汽水页签防重开关失败")
            page.sw_record_skip = None
            return
        refresh = getattr(bar, "_refresh_tab_layout", None)
        if callable(refresh):
            QTimer.singleShot(0, refresh)
        else:
            place = getattr(bar, "_place_left_buttons", None)
            if callable(place):
                QTimer.singleShot(0, place)

    def refresh_theme(self, *_):
        self.setStyleSheet(
            fmt(TAB_QSS + VIDEO_TAB_QSS)
            + "\n#PageAudio{background:transparent;border:none;}"
        )
        if hasattr(self.page_qishui, "refresh_theme"):
            self.page_qishui.refresh_theme()
        _apply_tab_page_bg(self.page_qishui, "TabQishui")
        self._refresh_tab()
        host = getattr(self, "_pre_dl_badge_host", None)
        if host is not None:
            host.refresh_theme()

    def handoff(self, link_text: str, auto_start: bool = True):
        self.tabs.setCurrentWidget(self.page_qishui)
        text = (link_text or "").strip()
        if text:
            self.page_qishui.url_edit.setText(text)
        if auto_start and text:
            QTimer.singleShot(0, lambda: self.page_qishui._start_flow(False))

    def export_settings(self) -> dict:
        page = getattr(self, "page_qishui", None)
        if page is not None and hasattr(page, "export_settings"):
            try:
                return {"qishui": page.export_settings()}
            except Exception:
                log.exception("导出汽水设置失败")
        return {"qishui": {"save_path": "", "cookie_path": "", "check_records": True}}

    def apply_settings(self, d: dict):
        if not isinstance(d, dict):
            return
        page = getattr(self, "page_qishui", None)
        sub = d.get("qishui") if isinstance(d.get("qishui"), dict) else d
        if page is not None and hasattr(page, "apply_settings"):
            try:
                page.apply_settings(sub or {})
            except Exception:
                log.exception("恢复汽水设置失败")

    def shutdown(self):
        page = getattr(self, "page_qishui", None)
        if page is not None and hasattr(page, "shutdown"):
            try:
                page.shutdown()
            except Exception:
                log.exception("关闭汽水页失败")
