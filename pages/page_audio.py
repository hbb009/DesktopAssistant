# pages/page_audio.py
# 音频下载：版式对齐视频下载的抖音页，目前只有「汽水音乐」一页。
# 预下载角标、链接下简易进度线、音频媒体卡都走抖音页同一套控件。

from __future__ import annotations

import os

from PyQt5.QtCore import Qt, QTimer, QSize, QRectF, pyqtSignal
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QBrush, QPainterPath
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QTabWidget, QLabel

from styles.style_all import theme, fmt, tk, TAB_QSS, VIDEO_TAB_QSS, apply_transparent_surface
from pages.page_douyin import (
    PageDouyin,
    ParseWorker,
    is_qishui_url,
    extract_url,
    _mark_best_audio_only,
)
from utils.logger import get_logger

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


class PageQishui(PageDouyin):
    """汽水音乐：界面与抖音页相同，只接受汽水链接。"""

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
        return {"qishui": {"save_path": "", "cookie_path": ""}}

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
