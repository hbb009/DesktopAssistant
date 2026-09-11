# pages/page_video.py
# 视频下载：QTabWidget 分页（抖音 / B站 / YouTube）
# v9.10：原「抖音下载」升级为多平台「视频下载」
# v9.13：新增「B站」分页（yt-dlp 引擎，布局对齐抖音）
# v9.13.1：新增统一跨平台批量下载

import os
import re

from PyQt5.QtCore import Qt, QTimer, QRectF, QSize, QPointF, pyqtSignal
from PyQt5.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QPen, QBrush, QPainterPath, QLinearGradient,
)
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QTabWidget, QLabel, QFileDialog,
    QDialog, QDialogButtonBox, QTextEdit, QApplication, QSizePolicy, QFrame,
)

from styles.style_all import (
    theme,
    fmt,
    tk,
    TAB_QSS,
    VIDEO_TAB_QSS,
    apply_transparent_surface,
    attach_ok_auto_close,
    message_box_info,
)
from pages.page_douyin import PageDouyin
from pages.page_bilibili import PageBilibili
from pages.page_youtube import PageYoutube
from utils.logger import get_logger
from utils.cursor_toast import (
    show_cursor_toast,
    show_batch_progress_toast,
    stop_batch_progress_toast,
    set_batch_progress_note,
    batch_platform_label,
    sanitize_batch_record_name,
)

log = get_logger(__name__)


def _show_batch_done_dialog(parent, summary: str, detail: str, ok: int, err: int):
    """批量下载完成弹窗：顶部摘要固定，中间链接列表可滚动缩略，高度不超过屏幕。"""
    dlg = QDialog(parent)
    dlg.setWindowTitle("批量下载完成")
    dlg.setModal(True)
    dlg.setMinimumWidth(420)

    # 屏幕可用高度的约 55%，中间列表再取其中一部分，保证整窗不超出显示器
    screen_h = 800
    try:
        scr = QApplication.primaryScreen()
        if scr is not None:
            screen_h = max(480, scr.availableGeometry().height())
    except Exception:
        pass
    max_dlg_h = int(screen_h * 0.55)
    mid_max_h = max(120, min(280, int(screen_h * 0.28)))

    root = QVBoxLayout(dlg)
    root.setContentsMargins(16, 14, 16, 12)
    root.setSpacing(10)

    # 标题 + 摘要（固定，不滚动）
    title = QLabel("📊 批量下载报告")
    title.setStyleSheet("font-size:15px;font-weight:700;")
    root.addWidget(title)

    icon = "✅" if err == 0 else ("⚠️" if ok > 0 else "❌")
    head = QLabel(f"{icon}  {(summary or '').strip()}")
    head.setWordWrap(True)
    head.setStyleSheet("font-size:13px;")
    root.addWidget(head)

    # 中间明细：限高 + 滚动（长列表缩略在可视区内浏览）
    mid = QTextEdit()
    mid.setReadOnly(True)
    mid.setFrameShape(QFrame.StyledPanel)
    mid.setPlainText(detail or "（无链接明细）")
    mid.setMinimumHeight(100)
    mid.setMaximumHeight(mid_max_h)
    mid.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    mid.setStyleSheet(
        "QTextEdit{font-size:12px;font-family:Consolas,'Microsoft YaHei UI',sans-serif;}"
    )
    # 默认滚到顶部，长文只露可视区高度
    try:
        from PyQt5.QtGui import QTextCursor
        cur = mid.textCursor()
        cur.movePosition(QTextCursor.Start)
        mid.setTextCursor(cur)
    except Exception:
        pass
    root.addWidget(mid, 1)

    tip = QLabel("中间为链接明细（可滚动查看全文）")
    tip.setStyleSheet("color:#94a3b8;font-size:11px;")
    root.addWidget(tip)

    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dlg.accept)
    attach_ok_auto_close(dlg, buttons.button(QDialogButtonBox.Ok), 10)
    root.addWidget(buttons)

    dlg.setMaximumHeight(max_dlg_h)
    dlg.adjustSize()
    # 宽度随内容略放宽，仍受屏宽约束
    try:
        scr = QApplication.primaryScreen()
        if scr is not None:
            max_w = min(640, int(scr.availableGeometry().width() * 0.7))
            dlg.setMaximumWidth(max_w)
            if dlg.width() < 420:
                dlg.resize(420, min(dlg.height(), max_dlg_h))
    except Exception:
        pass
    dlg.exec_()


def _apply_tab_page_bg(widget, object_name: str, extra_qss: str = ""):
    """子页底色对齐 Tab pane（panel_2）。"""
    widget.setObjectName(object_name)
    widget.setAttribute(Qt.WA_StyledBackground, True)
    widget.setAutoFillBackground(True)
    bg = tk("panel_2")
    widget.setStyleSheet(
        f"#{object_name}{{background:{bg};border:none;}}\n{extra_qss or ''}"
    )


def _paint_douyin_icon(size: int = 22, selected: bool = True, dark: bool = True) -> QIcon:
    """抖音风格徽章：深色圆角底 + 双色音符（青/玫红叠影），未选中时整体降饱和。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)

    s = float(size)
    m = s * 0.06
    body = QRectF(m, m, s - 2 * m, s - 2 * m)

    if selected:
        # 近黑底 + 微青边，偏品牌质感
        base = QColor("#121212") if dark else QColor("#1a1a1a")
        rim = QColor("#25F4EE")
        rim.setAlpha(200)
        note_a = QColor("#25F4EE")
        note_b = QColor("#FE2C55")
    else:
        # 未选中：灰化、半透明
        base = QColor("#3a3f4a") if dark else QColor("#c5ccd6")
        base.setAlpha(120 if dark else 140)
        rim = QColor("#6b7280")
        rim.setAlpha(90)
        note_a = QColor("#9ca3af")
        note_a.setAlpha(150)
        note_b = QColor("#9ca3af")
        note_b.setAlpha(100)

    p.setPen(QPen(rim, max(1.0, s * 0.06)))
    p.setBrush(QBrush(base))
    p.drawRoundedRect(body, s * 0.22, s * 0.22)

    # 双音符：玫红轻偏，青色主影（抖音经典叠色）
    def _note(ox: float, oy: float, col: QColor, scale: float = 1.0):
        p.save()
        p.translate(ox, oy)
        p.scale(scale, scale)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(col))
        # 符头
        p.drawEllipse(QRectF(s * 0.22, s * 0.48, s * 0.28, s * 0.22))
        # 符杆
        p.drawRoundedRect(QRectF(s * 0.44, s * 0.22, s * 0.07, s * 0.40), 1.5, 1.5)
        # 符旗
        flag = QPainterPath()
        flag.moveTo(s * 0.51, s * 0.22)
        flag.cubicTo(s * 0.72, s * 0.18, s * 0.78, s * 0.38, s * 0.58, s * 0.42)
        flag.lineTo(s * 0.51, s * 0.42)
        flag.closeSubpath()
        p.drawPath(flag)
        p.restore()

    _note(s * 0.04, s * 0.02, note_b, 0.92)
    _note(0.0, 0.0, note_a, 1.0)

    # 选中时顶部细高光
    if selected:
        hi = QLinearGradient(0, m, 0, m + s * 0.35)
        hi.setColorAt(0, QColor(255, 255, 255, 40 if dark else 55))
        hi.setColorAt(1, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(hi))
        p.drawRoundedRect(QRectF(m + 1, m + 1, s - 2 * m - 2, s * 0.38), s * 0.18, s * 0.18)

    p.end()
    return QIcon(pm)


def _paint_bilibili_icon(size: int = 22, selected: bool = True, dark: bool = True) -> QIcon:
    """B站风格徽章：粉蓝圆角底 + 电视壳（天线 + 屏），未选中时降饱和。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)

    s = float(size)
    m = s * 0.08
    body = QRectF(m, m, s - 2 * m, s - 2 * m)

    if selected:
        # B站经典粉 #FB7299
        grad = QLinearGradient(body.topLeft(), body.bottomLeft())
        grad.setColorAt(0, QColor("#FF8FB3"))
        grad.setColorAt(1, QColor("#F25D8E"))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(body, s * 0.22, s * 0.22)
        ink = QColor("#FFFFFF")
        screen = QColor(255, 255, 255, 45)
    else:
        base = QColor("#6b7280") if dark else QColor("#b0b8c4")
        base.setAlpha(130 if dark else 150)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(base))
        p.drawRoundedRect(body, s * 0.22, s * 0.22)
        ink = QColor("#e5e7eb") if dark else QColor("#f8fafc")
        ink.setAlpha(170)
        screen = QColor(255, 255, 255, 28 if dark else 40)

    # 电视天线（两根斜线）
    pen = QPen(ink, max(1.4, s * 0.07))
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    cx = body.center().x()
    top = body.top() + s * 0.14
    p.drawLine(QPointF(cx - s * 0.12, top + s * 0.10), QPointF(cx - s * 0.02, top))
    p.drawLine(QPointF(cx + s * 0.12, top + s * 0.10), QPointF(cx + s * 0.02, top))

    # 电视外框
    tv = QRectF(body.left() + s * 0.14, body.top() + s * 0.28,
                body.width() - s * 0.28, body.height() - s * 0.40)
    p.setPen(QPen(ink, max(1.2, s * 0.06)))
    p.setBrush(QBrush(screen))
    p.drawRoundedRect(tv, s * 0.08, s * 0.08)

    # 屏内笑眼两点（小圆）
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(ink))
    eye_y = tv.center().y() - s * 0.02
    er = s * 0.045
    p.drawEllipse(QPointF(tv.center().x() - s * 0.08, eye_y), er, er)
    p.drawEllipse(QPointF(tv.center().x() + s * 0.08, eye_y), er, er)
    # 小嘴弧
    mouth = QPainterPath()
    mx, my = tv.center().x(), tv.center().y() + s * 0.06
    mouth.moveTo(mx - s * 0.07, my)
    mouth.quadTo(mx, my + s * 0.06, mx + s * 0.07, my)
    p.setPen(QPen(ink, max(1.1, s * 0.055)))
    p.setBrush(Qt.NoBrush)
    p.drawPath(mouth)

    p.end()
    return QIcon(pm)


def _paint_youtube_icon(size: int = 22, selected: bool = True, dark: bool = True) -> QIcon:
    """YouTube 风格徽章：圆角播放胶囊 + 三角，未选中时降饱和。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)

    s = float(size)
    m = s * 0.08
    body = QRectF(m, m + s * 0.08, s - 2 * m, s - 2 * m - s * 0.16)

    if selected:
        # 轻微纵向渐变，更「厚」
        grad = QLinearGradient(body.topLeft(), body.bottomLeft())
        grad.setColorAt(0, QColor("#FF2A2A"))
        grad.setColorAt(1, QColor("#CC0000"))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(body, body.height() * 0.28, body.height() * 0.28)
        # 内边高光
        p.setPen(QPen(QColor(255, 255, 255, 35), 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(body.adjusted(0.5, 0.5, -0.5, -0.5), body.height() * 0.28, body.height() * 0.28)
        tri = QColor("#FFFFFF")
    else:
        base = QColor("#6b7280") if dark else QColor("#b0b8c4")
        base.setAlpha(130 if dark else 150)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(base))
        p.drawRoundedRect(body, body.height() * 0.28, body.height() * 0.28)
        tri = QColor("#e5e7eb") if dark else QColor("#f8fafc")
        tri.setAlpha(160)

    # 播放三角（略居中偏右一点更像品牌标）
    cx, cy = body.center().x() + s * 0.02, body.center().y()
    tw, th = s * 0.18, s * 0.22
    path = QPainterPath()
    path.moveTo(cx - tw * 0.45, cy - th * 0.55)
    path.lineTo(cx - tw * 0.45, cy + th * 0.55)
    path.lineTo(cx + tw * 0.65, cy)
    path.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(tri))
    p.drawPath(path)

    p.end()
    return QIcon(pm)


class PageVideo(QWidget):
    """视频下载：抖音 / B站 / YouTube 三个页面卡。"""

    # 侧栏「视频下载」底边进度线（汇总三个子页）
    nav_progress = pyqtSignal(bool, int, str)

    def __init__(self):
        super().__init__()
        apply_transparent_surface(self, "PageVideo")
        self.setStyleSheet(
            fmt(TAB_QSS + VIDEO_TAB_QSS)
            + "\n#PageVideo{background:transparent;border:none;}"
        )
        # 各子页最近一次进度；任一活跃则侧栏显示，都闲时隐藏
        self._nav_sources = {
            "douyin": (False, 0, ""),
            "bilibili": (False, 0, ""),
            "youtube": (False, 0, ""),
        }

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
            try:
                self.tabs.tabBar().setElideMode(Qt.ElideNone)
                self.tabs.tabBar().setExpanding(False)
            except Exception:
                pass
        root.addWidget(self.tabs, 1)

        # ── 自动下载（记录列表 · 跨平台混链共用；速存/图集批处理统一走此引擎）──
        self._batch_urls = []
        self._batch_index = 0
        self._batch_mode = False
        self._batch_file_path = ""
        self._batch_ok = 0
        self._batch_err = 0
        self._batch_skip = 0          # 已下载过（记录命中）
        self._batch_active = False
        self._batch_stop_requested = False  # F6：当前条完成后中止
        self._batch_current_platform = ""
        self._batch_current_url = ""   # 当前正在下载的 URL（精准删行用）
        self._batch_ok_urls = []       # 新下成功的链接
        self._batch_err_urls = []      # 失败 / 无法识别
        self._batch_skip_urls = []     # 已下载过
        self._batch_mixed = False      # 文件内 ≥2 类平台
        self._batch_n_platforms = 0
        self._batch_platform_counts = {}  # key → 条数
        self._batch_item_gen = 0       # 本条世代号，防迟到信号误结算
        self._batch_timer = QTimer(self)
        self._batch_timer.setSingleShot(True)
        self._batch_timer.timeout.connect(self._batch_check_next)

        self.page_douyin = PageDouyin()
        self.page_bilibili = PageBilibili()
        self.page_youtube = PageYoutube()
        _apply_tab_page_bg(self.page_douyin, "TabDouyin")
        _apply_tab_page_bg(self.page_bilibili, "TabBilibili")
        _apply_tab_page_bg(self.page_youtube, "TabYoutube")

        # 顺序：抖音 → B站 → YouTube
        self._pre_dl_tab_specs = (
            ("douyin", "douyin"),
            ("bilibili", "bilibili.com"),
            ("youtube", "YouTube"),
        )
        self._pre_dl_counts = {k: 0 for k, _ in self._pre_dl_tab_specs}
        self.tabs.addTab(self.page_douyin, "douyin")
        self.tabs.addTab(self.page_bilibili, "bilibili.com")
        self.tabs.addTab(self.page_youtube, "YouTube")
        self._pre_dl_badge_host = self._install_pre_dl_tab_badges()
        self._refresh_platform_tabs()
        self.tabs.currentChanged.connect(lambda _i: self._refresh_platform_tabs())

        # 子页解析/下载进度 → 统一侧栏线（不在哪个 Tab 都能看到）
        try:
            self.page_douyin.nav_progress.connect(
                lambda a, p, c: self._on_child_nav_progress("douyin", a, p, c)
            )
            self.page_bilibili.nav_progress.connect(
                lambda a, p, c: self._on_child_nav_progress("bilibili", a, p, c)
            )
            self.page_youtube.nav_progress.connect(
                lambda a, p, c: self._on_child_nav_progress("youtube", a, p, c)
            )
        except Exception:
            log.exception("连接视频子页侧栏进度线失败")

        theme.changed.connect(self.refresh_theme)

    def notify_batch_item_done(self, ok: bool, note: str = "", source: str = ""):
        """子页显式上报本条结果：校验通过才删记录行，再续跑下一条。

        与 nav_progress 空闲信号双通道；_batch_active 做单飞，避免重复结算。
        ok=True / 已下载过：从记录文件删除当前 URL 对应行。
        """
        if not getattr(self, "_batch_mode", False) or not getattr(self, "_batch_active", False):
            return
        src = (source or getattr(self, "_batch_current_platform", "") or "").strip()
        cur = (getattr(self, "_batch_current_platform", "") or "").strip()
        if src and cur and src != cur:
            # 非当前平台的迟到信号，忽略
            return
        note = (note or "").strip() or ("成功" if ok else "失败")
        cur_url = (self._batch_current_url or "").strip()
        # 先落锁，防止 nav_progress 与显式回调各结算一次
        self._batch_active = False
        is_skip = ok and note in ("已下载过", "已存在", "跳过")
        if is_skip:
            self._batch_skip += 1
            if cur_url:
                self._batch_skip_urls.append(cur_url)
            removed = self._batch_remove_current_line()
            log.info(
                "自动下载本条跳过(已下过) platform=%s removed=%s url=%s",
                cur or src, removed, (cur_url or "")[:80],
            )
        elif ok:
            self._batch_ok += 1
            if cur_url:
                self._batch_ok_urls.append(cur_url)
            removed = self._batch_remove_current_line()
            log.info(
                "自动下载本条成功 platform=%s note=%s removed=%s url=%s",
                cur or src, note, removed, (cur_url or "")[:80],
            )
        else:
            self._batch_err += 1
            if cur_url:
                self._batch_err_urls.append(cur_url)
            log.info(
                "自动下载本条失败 platform=%s note=%s url=%s",
                cur or src, note, (cur_url or "")[:80],
            )
        self._batch_progress_toast(note=note)
        self._notify_batch_badges()
        if getattr(self, "_batch_stop_requested", False):
            self._batch_finish()
        else:
            # 混链时稍长间隔，给切页/worker 收尾；跳过则更快
            delay = 1200 if is_skip else 2800
            self._batch_timer.start(delay)

    def _on_child_nav_progress(self, source: str, active: bool, pct: int, color: str):
        """汇总抖音/B站/YouTube（图集信号仅用于批量完成续跑，不更新视频侧栏进度线）。"""
        # 图集信号：只走批量完成逻辑，不碰视频侧栏进度线（图集有自己的侧栏线）
        if source in ("ehentai", "pixiv", "hitomi"):
            if not active and self._batch_mode and self._batch_active and source == self._batch_current_platform:
                eh, pixiv, hitomi, _pg = self._get_gallery_pages()
                p = {
                    "ehentai": eh,
                    "pixiv": pixiv,
                    "hitomi": hitomi,
                }.get(source)
                ok = bool(p is not None and getattr(p, "_last_download_ok", False))
                note = ""
                if p is not None:
                    note = (getattr(p, "_last_batch_note", None) or "").strip()
                    try:
                        p._last_batch_note = ""
                    except Exception:
                        pass
                self.notify_batch_item_done(ok, note=note, source=source)
            return

        # 视频源：更新视频侧栏进度线 + 批量完成
        self._nav_sources[source] = (bool(active), int(pct if pct is not None else 0), color or "")
        if active:
            try:
                self.nav_progress.emit(True, int(pct if pct is not None else 0), color or "")
            except Exception:
                log.exception("视频侧栏进度信号发送失败 source=%s", source)
            return

        # 本源结束：侧栏优先显示其它仍在忙的平台；批处理续跑不受此影响
        # （混链批时若另一平台残留 active 标志，旧逻辑会 return 导致整批卡住）
        other_busy = False
        for key, (oa, op, oc) in self._nav_sources.items():
            if key == source:
                continue
            if oa:
                other_busy = True
                try:
                    self.nav_progress.emit(True, op, oc)
                except Exception:
                    log.exception("视频侧栏进度信号发送失败 source=%s", key)
                break
        if not other_busy:
            try:
                self.nav_progress.emit(False, 0, "")
            except Exception:
                log.exception("视频侧栏进度清零信号发送失败")

        # 自动下载：当前子页空闲 → 结算本条（与 notify_batch_item_done 单飞）
        if self._batch_mode and self._batch_active and source == self._batch_current_platform:
            p = {
                "douyin": getattr(self, "page_douyin", None),
                "bilibili": getattr(self, "page_bilibili", None),
                "youtube": getattr(self, "page_youtube", None),
            }.get(source)
            ok = bool(p is not None and getattr(p, "_last_download_ok", False))
            note = ""
            if p is not None:
                note = (getattr(p, "_last_batch_note", None) or "").strip()
                try:
                    p._last_batch_note = ""
                except Exception:
                    pass
            self.notify_batch_item_done(ok, note=note, source=source)

    def _install_pre_dl_tab_badges(self):
        """页签右侧内边距叠数字角标（不占按钮位，宽度不随条数变）。"""
        from ui_main import TabPreDlBadgeHost

        return TabPreDlBadgeHost(
            self.tabs, getattr(self, "_pre_dl_tab_specs", ()), parent=self
        )

    def set_pre_dl_counts(self, counts: dict):
        """子页页签右侧显示预下载条数；0 条隐藏角标，页签宽度不变。"""
        counts = counts or {}
        for key, _base in getattr(self, "_pre_dl_tab_specs", ()):
            try:
                n = max(0, int(counts.get(key, 0) or 0))
            except (TypeError, ValueError):
                n = 0
            self._pre_dl_counts[key] = n
        host = getattr(self, "_pre_dl_badge_host", None)
        if host is not None:
            host.set_counts(self._pre_dl_counts)

    def set_batch_counts(self, counts: dict):
        """子页页签显示批处理剩余「批N」；0 条隐藏。"""
        counts = counts or {}
        data = {}
        for key, _base in getattr(self, "_pre_dl_tab_specs", ()):
            try:
                n = max(0, int(counts.get(key, 0) or 0))
            except (TypeError, ValueError):
                n = 0
            data[key] = n
        host = getattr(self, "_pre_dl_badge_host", None)
        if host is not None and hasattr(host, "set_batch_counts"):
            host.set_batch_counts(data)

    def batch_remaining_by_platform(self) -> dict:
        """批处理尚未完成的链接（含当前条）按平台计数。"""
        if not getattr(self, "_batch_mode", False):
            return {}
        urls = list(getattr(self, "_batch_urls", None) or [])
        if not urls:
            return {}
        try:
            idx = max(0, int(getattr(self, "_batch_index", 0) or 0))
        except (TypeError, ValueError):
            idx = 0
        if getattr(self, "_batch_active", False) and idx > 0:
            start = idx - 1
        else:
            start = idx
        start = max(0, min(start, len(urls)))
        counts = {}
        for u in urls[start:]:
            plat = self._batch_platform_for(u)
            if not plat:
                continue
            counts[plat] = counts.get(plat, 0) + 1
        return counts

    def _notify_batch_badges(self):
        """通知主窗口刷新侧栏 / 页签「批N」。"""
        targets = []
        try:
            win = self.window()
        except Exception:
            win = None
        if win is not None:
            targets.append(win)
        w = self
        while w is not None:
            if w not in targets:
                targets.append(w)
            try:
                w = w.parent()
            except Exception:
                break
        for t in targets:
            if hasattr(t, "refresh_batch_badges"):
                try:
                    t.refresh_batch_badges()
                except Exception:
                    log.exception("刷新批处理角标失败")
                return

    def _apply_pre_dl_tab_texts(self):
        host = getattr(self, "_pre_dl_badge_host", None)
        if host is not None:
            host.set_counts(getattr(self, "_pre_dl_counts", {}))

    def _refresh_platform_tabs(self):
        """按选中态刷新：图标明暗、文字色、pane 左上圆角（firstSelected）。"""
        dark = bool(theme.is_dark)
        idx = self.tabs.currentIndex()
        self.tabs.setTabIcon(0, _paint_douyin_icon(22, selected=(idx == 0), dark=dark))
        self.tabs.setTabIcon(1, _paint_bilibili_icon(22, selected=(idx == 1), dark=dark))
        self.tabs.setTabIcon(2, _paint_youtube_icon(22, selected=(idx == 2), dark=dark))

        # firstSelected → VIDEO_TAB_QSS 控制 pane 左上是否圆角
        # 选中第一张：左上直角（页卡左边承接）；否则顶边线左端圆角
        first = "true" if idx == 0 else "false"
        self.tabs.setProperty("firstSelected", first)
        st = self.tabs.style()
        if st is not None:
            st.unpolish(self.tabs)
            st.polish(self.tabs)
        self.tabs.update()

        # 文字色再补一层，亮/暗都拉大对比
        bar = self.tabs.tabBar()
        if bar is not None:
            from PyQt5.QtGui import QColor as _QC
            sel, idle = _QC(tk("text_strong")), _QC(tk("text_faint"))
            idle.setAlpha(150 if dark else 160)
            for i in range(self.tabs.count()):
                bar.setTabTextColor(i, sel if i == idx else idle)
            try:
                bar.setDrawBase(False)
            except Exception:
                pass
        host = getattr(self, "_pre_dl_badge_host", None)
        if host is not None:
            host.reposition()

    # ── 跨平台交接：粘错平台链接时自动切 Tab 并续跑 ──

    # ── 自动下载（记录列表 · 跨平台混链共用）──

    def _batch_choose_file(self):
        """文件对话框入口 → start_batch_from_file。"""
        try:
            from utils.app_paths import records_dir
            base = records_dir()
            rec_dir = os.path.join(base, "batch")
            try:
                os.makedirs(rec_dir, exist_ok=True)
            except Exception:
                rec_dir = base
        except Exception:
            rec_dir = os.path.normpath(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
            )
            try:
                os.makedirs(rec_dir, exist_ok=True)
            except Exception:
                pass
        path, _ = QFileDialog.getOpenFileName(
            self, "选择链接文件", rec_dir, "文本文件 (*.txt);;所有文件 (*)"
        )
        if path:
            self.start_batch_from_file(path)

    def start_batch_from_file(self, filepath: str) -> bool:
        """统一入口：从记录/链接文件启动自动下载（混链按行识别平台）。

        速存记录卡、图集页「批处理」、本页选文件 均应走此方法。
        """
        path = (filepath or "").strip()
        if not path or not os.path.isfile(path):
            self._log_to_current("记录文件不存在", "warn")
            return False
        if getattr(self, "_batch_mode", False):
            self._log_to_current("已有自动下载在进行，请先 F6 中止或等待结束", "warn")
            return False

        urls = []
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        urls.append(line)
        except Exception as e:
            self._log_to_current(f"读取文件失败：{e}", "err")
            return False
        if not urls:
            self._log_to_current("文件中未找到有效链接", "warn")
            return False

        # 预分析平台构成（混链提示）
        counts = {}
        unknown = 0
        for u in urls:
            p = self._batch_platform_for(u)
            if not p:
                unknown += 1
                continue
            counts[p] = counts.get(p, 0) + 1
        n_plat = len(counts)
        mixed = n_plat >= 2

        try:
            self._batch_timer.stop()
        except Exception:
            pass
        self._batch_urls = urls
        self._batch_index = 0
        self._batch_mode = True
        self._batch_file_path = path
        self._batch_ok = 0
        self._batch_err = 0
        self._batch_skip = 0
        self._batch_active = False
        self._batch_stop_requested = False
        self._batch_current_platform = ""
        self._batch_current_url = ""
        self._batch_ok_urls = []
        self._batch_err_urls = []
        self._batch_skip_urls = []
        self._batch_mixed = mixed
        self._batch_n_platforms = n_plat
        self._batch_platform_counts = dict(counts)
        self._batch_item_gen = 0
        # 用户手动开的批处理优先：预下载不再开下一条；当前条下完再让路
        waiting = bool(self._set_clipboard_auto_frozen(True))

        # 摘要
        if mixed:
            parts = []
            for k, n in sorted(counts.items(), key=lambda kv: -kv[1]):
                parts.append(f"{batch_platform_label(k) or k}×{n}")
            mix_s = "、".join(parts)
            if unknown:
                mix_s += f"、未识别×{unknown}"
            self._log_to_current(
                f"自动下载：共 {len(urls)} 条 · 混链 {n_plat} 类（{mix_s}）",
                "ok",
            )
        else:
            only = next(iter(counts), "")
            lab = batch_platform_label(only) if only else "未知"
            extra = f" · 未识别 {unknown} 条" if unknown else ""
            self._log_to_current(
                f"自动下载：共 {len(urls)} 条 · {lab}{extra}",
                "ok",
            )
        self._notify_batch_badges()
        if waiting:
            self._log_to_current("预下载当前条下完后再开始批处理", "warn")
            self._batch_progress_toast(note="等预下载当前条")
        else:
            self._batch_next()
        return True

    def _set_clipboard_auto_frozen(self, frozen: bool) -> bool:
        """通知主窗口冻结/解冻；返回预下载是否还在下当前条（批处理需等待）。"""
        w = self
        while w is not None:
            if hasattr(w, "set_clipboard_auto_frozen"):
                try:
                    return bool(w.set_clipboard_auto_frozen(bool(frozen)))
                except Exception:
                    log.exception("通知主窗口冻结自动下载开关失败 frozen=%s", frozen)
                    return False
            try:
                w = w.parent()
            except Exception:
                break
        return False

    def request_batch_stop(self) -> bool:
        """F6：预约中止。当前记录处理完后再停；若已在条间空闲则立刻收口。"""
        if not getattr(self, "_batch_mode", False):
            return False
        if getattr(self, "_batch_stop_requested", False):
            # 不另弹 CursorToast，状态挂在常驻气泡第一行
            set_batch_progress_note("已预约中止")
            return True
        self._batch_stop_requested = True
        if not getattr(self, "_batch_active", False):
            try:
                self._batch_timer.stop()
            except Exception:
                pass
            # 条间空闲：立刻收口（完成提示由 _batch_finish 统一弹出）
            QTimer.singleShot(0, self._batch_finish)
        else:
            set_batch_progress_note("当前条后中止")
            self._log_to_current("已预约中止：当前条完成后停止批处理", "warn")
        return True

    def _batch_check_next(self):
        self._batch_next()

    @staticmethod
    def _batch_platform_for(url: str) -> str:
        """识别 URL 所属平台。"""
        low = (url or "").lower()
        if "douyin.com" in low or "tiktok.com" in low or "v.douyin.com" in low:
            return "douyin"
        if "youtube.com" in low or "youtu.be" in low:
            return "youtube"
        if ("bilibili.com" in low or "b23.tv" in low or "bili2233.cn" in low
                or bool(re.search(r"\bBV[0-9A-Za-z]{10}\b|\bav\d+\b", url or "", re.I))):
            return "bilibili"
        if "e-hentai.org" in low or "exhentai.org" in low:
            return "ehentai"
        if "pixiv.net" in low:
            return "pixiv"
        if "hitomi.la" in low:
            return "hitomi"
        return ""

    def _get_gallery_pages(self):
        """延迟获取图集子页引用（通过主窗口）。返回 (eh, pixiv, hitomi, page_gallery)。"""
        w = self
        while w:
            pg = getattr(w, "page_gallery", None)
            if pg is not None:
                return (
                    getattr(pg, "page_eh", None),
                    getattr(pg, "page_pixiv", None),
                    getattr(pg, "page_hitomi", None),
                    pg,
                )
            w = w.parent()
        return None, None, None, None

    def _batch_next(self):
        if getattr(self, "_batch_stop_requested", False):
            self._batch_finish()
            return
        if not self._batch_mode or not self._batch_urls or self._batch_index >= len(self._batch_urls):
            self._batch_finish()
            return
        url = self._batch_urls[self._batch_index].strip()
        self._batch_index += 1
        platform = self._batch_platform_for(url)
        self._batch_current_platform = platform

        if not platform:
            self._batch_err += 1
            self._batch_err_urls.append(url)
            self._batch_current_url = url
            self._log_to_current(
                f"未识别链接，保留记录 ({self._batch_index}/{len(self._batch_urls)})",
                "warn",
            )
            self._batch_active = False
            self._batch_progress_toast(note="无法识别")
            self._notify_batch_badges()
            if getattr(self, "_batch_stop_requested", False):
                self._batch_finish()
            else:
                self._batch_timer.start(600)
            return

        self._batch_item_gen = int(getattr(self, "_batch_item_gen", 0) or 0) + 1
        self._batch_active = True
        self._batch_current_url = url
        self._notify_batch_badges()
        lab = batch_platform_label(platform) or platform
        self._log_to_current(
            f"[{lab}] {url[:60]}… ({self._batch_index}/{len(self._batch_urls)})",
            "ok",
        )
        # 每条开始时刷新气泡（含混链/累计）
        self._batch_progress_toast()
        # 开跑前清目标页成功标志，避免混链时读到上一次的成功结果
        self._batch_reset_ok_flag(platform)
        # 视频三平台：清其它源残留 active，避免侧栏逻辑干扰（批处理续跑已不依赖此）
        if platform in ("douyin", "bilibili", "youtube"):
            for k in list(self._nav_sources.keys()):
                if k != platform:
                    self._nav_sources[k] = (False, 0, "")
        if platform == "douyin":
            self.handoff_to_douyin(url, auto_start=True)
        elif platform == "bilibili":
            self.handoff_to_bilibili(url, auto_start=True)
        elif platform == "youtube":
            self.handoff_to_youtube(url, auto_start=True)
        elif platform in ("ehentai", "pixiv", "hitomi"):
            eh, pixiv, hitomi, pg = self._get_gallery_pages()
            sub = {
                "ehentai": eh,
                "pixiv": pixiv,
                "hitomi": hitomi,
            }.get(platform)
            if sub is None or pg is None:
                self._batch_err += 1
                self._batch_err_urls.append(url)
                self._batch_active = False
                self._notify_batch_badges()
                self._batch_timer.start(500)
                return
            # 首次调度图集链接时，把图集子页的完成信号接到自动下载引擎
            if not getattr(self, "_gallery_batch_wired", False):
                self._gallery_batch_wired = True
                try:
                    if eh is not None:
                        eh.nav_progress.connect(
                            lambda a, p, c: self._on_child_nav_progress("ehentai", a, p, c)
                        )
                    if pixiv is not None:
                        pixiv.nav_progress.connect(
                            lambda a, p, c: self._on_child_nav_progress("pixiv", a, p, c)
                        )
                    if hitomi is not None:
                        hitomi.nav_progress.connect(
                            lambda a, p, c: self._on_child_nav_progress("hitomi", a, p, c)
                        )
                except Exception:
                    pass
            # 切换到图集页 + 对应子页
            mw = None
            w = self
            while w:
                if hasattr(w, "btn_gallery"):
                    mw = w
                w = w.parent()
            if mw and hasattr(mw, "stack") and hasattr(mw, "btn_gallery"):
                try:
                    idx = mw.stack.indexOf(pg)
                    if idx >= 0:
                        mw._switch(idx, mw.btn_gallery)
                except Exception:
                    pass
            pg.tabs.setCurrentWidget(sub)
            sub.url_edit.setText(url)
            # 用默认参数钉住 sub，避免后续混链时闭包指到别的子页
            QTimer.singleShot(100, lambda s=sub: s._start_flow(False))

    def _batch_reset_ok_flag(self, platform: str) -> None:
        """每条开跑前把对应子页的 _last_download_ok / _last_batch_note 置空。"""
        page = None
        if platform == "douyin":
            page = getattr(self, "page_douyin", None)
        elif platform == "bilibili":
            page = getattr(self, "page_bilibili", None)
        elif platform == "youtube":
            page = getattr(self, "page_youtube", None)
        elif platform in ("ehentai", "pixiv", "hitomi"):
            eh, pixiv, hitomi, _pg = self._get_gallery_pages()
            page = {
                "ehentai": eh,
                "pixiv": pixiv,
                "hitomi": hitomi,
            }.get(platform)
        if page is not None:
            try:
                page._last_download_ok = False
            except Exception:
                pass
            try:
                page._last_batch_note = ""
            except Exception:
                pass

    @staticmethod
    def _batch_line_matches_url(line: str, url: str) -> bool:
        """判断记录文件中的一行是否对应当前批处理 URL。

        支持：整行相等、行内包含链接、尾斜杠差异、分享文案里夹链接。
        """
        a = (line or "").strip()
        b = (url or "").strip()
        if not a or not b:
            return False
        if a == b:
            return True
        # 尾斜杠宽容
        a0, b0 = a.rstrip("/"), b.rstrip("/")
        if a0 and b0 and a0 == b0:
            return True
        # 分享文案：「…… https://… ……」整行写入时，按包含匹配
        if b in a or b0 in a:
            return True
        if a in b or a0 in b0:
            return True
        # 从行里再抽一次 http(s)
        try:
            m = re.search(r"(https?://[^\s<>\"']+)", a, re.I)
            if m:
                u = m.group(1).rstrip(").,];'\"")
                if u == b or u.rstrip("/") == b0:
                    return True
        except Exception:
            pass
        return False

    def _batch_remove_current_line(self) -> bool:
        """校验成功后：从源记录文件删除当前 URL 对应的行。返回是否已删除。"""
        url = (self._batch_current_url or "").strip()
        path = (self._batch_file_path or "").strip()
        if not url or not path or not os.path.isfile(path):
            msg = f"  ⚠ 删行跳过：url 或文件路径无效 path={path!r}"
            self._log_to_current(msg, "warn")
            log.warning("批处理删行跳过 url=%r path=%r", url[:80], path)
            return False
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                lines = f.readlines()
        except Exception as e:
            self._log_to_current(f"  ⚠ 删行读取失败：{e}", "warn")
            log.exception("批处理删行读取失败 path=%s", path)
            return False
        new_lines = []
        found = False
        for line in lines:
            if not found and self._batch_line_matches_url(line, url):
                found = True
                continue
            new_lines.append(line)
        if not found:
            self._log_to_current(f"  ⚠ 删行未匹配：{url[:60]}…", "warn")
            log.warning("批处理删行未匹配 url=%s path=%s", url[:120], path)
            return False
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            log.info("批处理已删记录行 url=%s remain=%s", url[:120], len(new_lines))
            return True
        except Exception as e:
            self._log_to_current(f"  ⚠ 删行写入失败：{e}", "warn")
            log.exception("批处理删行写入失败 path=%s", path)
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            return False

    def _batch_record_display_name(self) -> str:
        """气泡用记录卡名：去扩展名；下划线改空格；剥掉「预下载」前缀。"""
        base = os.path.basename(self._batch_file_path)
        name, _ext = os.path.splitext(base)
        return sanitize_batch_record_name(name or base) or "批处理"

    def _batch_progress_toast(self, note: str = ""):
        """气泡两行：上行 记录名 · X/Y；下行 成跳败 · 按F6中止。"""
        try:
            total = len(self._batch_urls or [])
            if total > 0:
                show_batch_progress_toast(
                    "",
                    self._batch_index,
                    total,
                    note=note or "",
                    ok=int(getattr(self, "_batch_ok", 0) or 0),
                    err=int(getattr(self, "_batch_err", 0) or 0),
                    skip=int(getattr(self, "_batch_skip", 0) or 0),
                    record_name=self._batch_record_display_name(),
                )
        except Exception:
            pass

    def _batch_finish(self):
        try:
            self._batch_timer.stop()
        except Exception:
            pass
        try:
            stop_batch_progress_toast()
        except Exception:
            pass
        # 批处理收口：强制清掉各子页简易进度线 + 侧栏线，避免某条异常路径漏 hide
        try:
            self.nav_progress.emit(False, 0, "")
        except Exception:
            pass
        for page in (
            getattr(self, "page_douyin", None),
            getattr(self, "page_bilibili", None),
            getattr(self, "page_youtube", None),
        ):
            if page is not None and hasattr(page, "_emit_nav_progress"):
                try:
                    page._emit_nav_progress(False)
                except Exception:
                    pass
        eh, pixiv, hitomi, _pg = self._get_gallery_pages()
        for page in (eh, pixiv, hitomi):
            if page is not None and hasattr(page, "_emit_nav_progress"):
                try:
                    page._emit_nav_progress(False)
                except Exception:
                    pass
        cnt = self._batch_index
        ok = self._batch_ok
        err = self._batch_err
        skip = int(getattr(self, "_batch_skip", 0) or 0)
        path = self._batch_file_path
        ok_urls = list(self._batch_ok_urls)
        err_urls = list(self._batch_err_urls)
        skip_urls = list(getattr(self, "_batch_skip_urls", None) or [])
        mixed = bool(getattr(self, "_batch_mixed", False))
        plat_counts = dict(getattr(self, "_batch_platform_counts", None) or {})
        stopped = bool(getattr(self, "_batch_stop_requested", False))
        self._batch_urls = []
        self._batch_index = 0
        self._batch_mode = False
        self._batch_file_path = ""
        self._batch_ok = 0
        self._batch_err = 0
        self._batch_skip = 0
        self._batch_active = False
        self._batch_stop_requested = False
        self._batch_current_platform = ""
        self._batch_current_url = ""
        self._batch_ok_urls = []
        self._batch_err_urls = []
        self._batch_skip_urls = []
        self._batch_mixed = False
        self._batch_n_platforms = 0
        self._batch_platform_counts = {}
        self._notify_batch_badges()
        # 批处理结束：解冻侧栏自动下载开关（状态保持批处理前的开/关）
        self._set_clipboard_auto_frozen(False)
        if cnt > 0:
            show_cursor_toast(
                "自动下载",
                "已中止" if stopped else "完成",
                current=cnt, total=cnt,
                ok=ok, skip=skip, err=err,
                accent="info" if stopped else "ok",
            )
            # 结果只在回到「速存图文」后弹报告
            file_deleted = False
            if err == 0 and path and os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8-sig") as f:
                        remaining = [
                            l for l in f
                            if l.strip() and not l.strip().startswith("#")
                        ]
                    if not remaining:
                        os.remove(path)
                        file_deleted = True
                except Exception:
                    pass
            fname = os.path.basename(path) if path else "—"
            if file_deleted:
                file_note = f"记录文件「{fname}」已全部完成，已自动删除"
            elif err > 0:
                file_note = f"记录文件「{fname}」已保留（含 {err} 条失败/未识别）"
            else:
                file_note = f"记录文件「{fname}」"
            mix_note = ""
            if mixed and plat_counts:
                bits = [
                    f"{batch_platform_label(k) or k}×{n}"
                    for k, n in sorted(plat_counts.items(), key=lambda kv: -kv[1])
                ]
                mix_note = " · 混链 " + "、".join(bits)
            skip_part = f" · 已下过 {skip}" if skip else ""
            summary_text = (
                f"共 {cnt} 条 · 成功 {ok}{skip_part} · 失败 {err}{mix_note}\n{file_note}"
            )
            detail_lines = []
            if ok_urls:
                detail_lines.append(f"✅ 新下载（{len(ok_urls)}）：")
                for u in ok_urls:
                    detail_lines.append(f"  · {u}")
            if skip_urls:
                if detail_lines:
                    detail_lines.append("")
                detail_lines.append(f"📋 已下载过（{len(skip_urls)}）：")
                for u in skip_urls:
                    detail_lines.append(f"  · {u}")
            if err_urls:
                if detail_lines:
                    detail_lines.append("")
                detail_lines.append(f"❌ 失败/未识别（{len(err_urls)}）：")
                for u in err_urls:
                    detail_lines.append(f"  · {u}")
            detail_text = "\n".join(detail_lines) if detail_lines else "（无链接明细）"
            # 切回速存图文页面并弹窗
            mw = None
            w = self
            while w:
                if hasattr(w, "page_fast") and hasattr(w, "btn_fast") and hasattr(w, "stack"):
                    mw = w
                    break
                w = w.parent()
            if mw:
                try:
                    from PyQt5.QtCore import QTimer as _QtTimer

                    def _show_report(
                        _mw=mw,
                        _summary=summary_text,
                        _detail=detail_text,
                        _ok=ok,
                        _err=err,
                    ):
                        try:
                            _mw._switch(_mw.stack.indexOf(_mw.page_fast), _mw.btn_fast)
                        except Exception:
                            pass
                        # 回到速存图文：刷新记录卡片（处理完的文件可能已被删除），
                        # 并在运行记录区补一条简报，关掉弹窗后仍有历史可查
                        try:
                            pf = getattr(_mw, "page_fast", None)
                            if pf is not None:
                                try:
                                    pf._refresh_record_cards()
                                except Exception:
                                    pass
                                try:
                                    lines = [
                                        l for l in str(_summary or "").splitlines() if l.strip()
                                    ]
                                    if lines:
                                        pf.list_widget.addItem(
                                            f"📊 {'；'.join(lines)}"
                                        )
                                        pf.list_widget.scrollToBottom()
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        try:
                            _show_batch_done_dialog(_mw, _summary, _detail, _ok, _err)
                        except Exception:
                            # 兜底：极简 MessageBox（同样 10 秒倒计时关闭）
                            message_box_info(
                                _mw, "批量下载完成", _summary, auto_close_sec=10,
                            )

                    _QtTimer.singleShot(500, _show_report)
                except Exception:
                    pass

    def _log_to_current(self, msg: str, level: str = "ok"):
        """日志写到当前可见子页的运行日志区。"""
        cur = self.tabs.currentWidget()
        if cur is not None and hasattr(cur, "_log"):
            try:
                cur._log(msg, level)
            except Exception:
                pass

    def _status_to_platform(self, platform: str, text: str, color: str = None):
        """状态总结写到指定平台子页提示行；无平台时退回当前视频子页。"""
        page = None
        plat = (platform or "").strip().lower()
        if plat == "douyin":
            page = getattr(self, "page_douyin", None)
        elif plat == "bilibili":
            page = getattr(self, "page_bilibili", None)
        elif plat == "youtube":
            page = getattr(self, "page_youtube", None)
        elif plat in ("ehentai", "pixiv", "hitomi"):
            eh, pixiv, hitomi, _pg = self._get_gallery_pages()
            page = {
                "ehentai": eh,
                "pixiv": pixiv,
                "hitomi": hitomi,
            }.get(plat)
        if page is None:
            page = self.tabs.currentWidget() if hasattr(self, "tabs") else None
        if page is None:
            return
        try:
            if hasattr(page, "_set_status"):
                page._set_status(text or "", color)
                return
        except Exception:
            pass
        for attr in ("lbl_batch_hint", "lbl_hint"):
            lbl = getattr(page, attr, None)
            if lbl is None:
                continue
            try:
                lbl.setText(text or "")
                col = color or "#94a3b8"
                weight = "700" if color else "600"
                lbl.setStyleSheet(
                    f"color:{col}; font-weight:{weight}; font-size:13px;"
                )
            except Exception:
                pass
            break

    def shutdown(self):
        """主窗口关窗：停抖音/B站/YouTube 子页 worker + 自动下载。"""
        try:
            self._batch_timer.stop()
            self._batch_mode = False
        except Exception:
            pass
        try:
            self._notify_batch_badges()
        except Exception:
            pass
        try:
            self._set_clipboard_auto_frozen(False)
        except Exception:
            pass
        for p in (
            getattr(self, "page_douyin", None),
            getattr(self, "page_bilibili", None),
            getattr(self, "page_youtube", None),
        ):
            if p is not None and hasattr(p, "shutdown"):
                try:
                    p.shutdown()
                except Exception:
                    pass

    def handoff_to_youtube(self, link_text: str, auto_start: bool = True):
        """把链接交给 YouTube 页。"""
        self.tabs.setCurrentWidget(self.page_youtube)
        text = (link_text or "").strip()
        if text:
            self.page_youtube.url_edit.setText(text)
        if auto_start and text:
            QTimer.singleShot(0, lambda: self.page_youtube._start_flow(False))

    def handoff_to_douyin(self, link_text: str, auto_start: bool = True):
        """把链接交给抖音页。"""
        self.tabs.setCurrentWidget(self.page_douyin)
        text = (link_text or "").strip()
        if text:
            self.page_douyin.url_edit.setText(text)
        if auto_start and text:
            QTimer.singleShot(0, lambda: self.page_douyin._start_flow(False))

    def handoff_to_bilibili(self, link_text: str, auto_start: bool = True):
        """把链接交给 B站 页。"""
        self.tabs.setCurrentWidget(self.page_bilibili)
        text = (link_text or "").strip()
        if text:
            self.page_bilibili.url_edit.setText(text)
        if auto_start and text:
            QTimer.singleShot(0, lambda: self.page_bilibili._start_flow(False))

    def refresh_theme(self, *_):
        self.setStyleSheet(
            fmt(TAB_QSS + VIDEO_TAB_QSS)
            + "\n#PageVideo{background:transparent;border:none;}"
        )
        # 先让子页刷新控件级样式，再盖回 pane 底色（子页 setStyleSheet 会冲掉底色）
        if hasattr(self.page_douyin, "refresh_theme"):
            self.page_douyin.refresh_theme()
        if hasattr(self.page_bilibili, "refresh_theme"):
            self.page_bilibili.refresh_theme()
        if hasattr(self.page_youtube, "refresh_theme"):
            self.page_youtube.refresh_theme()
        _apply_tab_page_bg(self.page_douyin, "TabDouyin")
        _apply_tab_page_bg(self.page_bilibili, "TabBilibili")
        _apply_tab_page_bg(self.page_youtube, "TabYoutube")
        self._refresh_platform_tabs()
        host = getattr(self, "_pre_dl_badge_host", None)
        if host is not None:
            host.refresh_theme()

    # ── 用户习惯（records/user.txt · video 段）─────────────────────────────
    def export_settings(self) -> dict:
        """导出各子页设置，供 user_prefs 落盘。"""
        result = {}
        for key, page in (
            ("douyin", getattr(self, "page_douyin", None)),
            ("bilibili", getattr(self, "page_bilibili", None)),
            ("youtube", getattr(self, "page_youtube", None)),
        ):
            if page is not None and hasattr(page, "export_settings"):
                try:
                    result[key] = page.export_settings()
                except Exception:
                    result[key] = {"save_path": "", "cookie_path": ""}
        return result

    def apply_settings(self, d: dict):
        """从 user.txt 恢复各子页设置。未知/空字段保持现状。"""
        if not isinstance(d, dict):
            return
        for key, page in (
            ("douyin", getattr(self, "page_douyin", None)),
            ("bilibili", getattr(self, "page_bilibili", None)),
            ("youtube", getattr(self, "page_youtube", None)),
        ):
            if page is not None and hasattr(page, "apply_settings"):
                try:
                    page.apply_settings(d.get(key) or {})
                except Exception:
                    pass
