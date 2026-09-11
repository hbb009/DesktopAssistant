# ============================================================
# 全局鼠标旁轻提示气泡（无焦点 · 点击穿透 · 短暂显示后淡出）
# 三种气泡同一套壳：自绘不透明底 + 描边，不跟网页抢字色。
# 瞬时提示默认一行；批处理 / 记录中 才走两行。
# ============================================================
from PyQt5.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QRectF,
)
from PyQt5.QtWidgets import QApplication, QWidget, QLabel
from PyQt5.QtGui import QCursor, QColor, QPainter, QPen, QBrush

from styles.style_all import theme, ACCENTS

_TOAST_KEEPALIVE = []
_NAME_MAX = 16    # 上行名称过长时中间省略（与批处理记录名一致）

# 气泡相对光标的固定偏移：默认在鼠标右下方 24px（20~30px 区间内）
_TOAST_OFFSET = 24
# 瞬时提示单例：同一时间只有一条气泡，并发提示叠到第 3 行
_TOAST_BUBBLE = None  # type: CursorToast | None

# ── 三种气泡共用视觉（与 app.qss 正文同族，字号按主/次两档）────────
# CursorToast / RecordBubble / BatchBubble 只允许用这里的字号和圆角，
# 避免再出现 30px / 26px / 16px 和 12 / 9 / 10 圆角各写一套。
_FONT = "'Microsoft YaHei','微软雅黑','Segoe UI',Arial"
_RADIUS = 10
_PAD_X = 18
_PAD_Y = 8
_PX_MAIN = 16
_PX_SUB = 13
_W_MAIN = 700
_W_SUB = 600
_FOLLOW_MS = 40
_FALLBACK_ACCENTS = {
    "ok": "#22c55e",
    "err": "#ef4444",
    "warn": "#f59e0b",
    "info": "#3b82f6",
}


def _accent_hex(name: str) -> str:
    """成功/失败/警告/信息边框色：跟全站 ACCENTS，缺键回落到 info。"""
    key = (name or "info").strip() or "info"
    try:
        if key in ACCENTS:
            return ACCENTS[key]
        return ACCENTS.get("info", _FALLBACK_ACCENTS["info"])
    except Exception:
        return _FALLBACK_ACCENTS.get(key, _FALLBACK_ACCENTS["info"])


def _label_qss(color: str, px: int = _PX_MAIN, weight: int = _W_MAIN) -> str:
    return (
        f"background:transparent;color:{color};font-size:{px}px;"
        f"font-family:{_FONT};font-weight:{weight};padding:0px;"
    )


def _is_dark_theme() -> bool:
    try:
        return bool(theme.is_dark)
    except Exception:
        return True


def _toast_bg() -> QColor:
    """气泡底色：不透明，且避开纯白/纯黑，叠在浅色或深色网页上都看得出。"""
    c = QColor("#1b2438" if _is_dark_theme() else "#e8eef6")
    c.setAlpha(255)
    return c


def _toast_fg() -> str:
    """主行字色：深色底用浅灰蓝，浅色底用近黑。不用纯白，避免底没画实就糊掉。"""
    return "#e8eefc" if _is_dark_theme() else "#0f172a"


def _toast_fg_mut() -> str:
    return "#a8b4cc" if _is_dark_theme() else "#334155"


def _paint_toast_shell(widget, border_hex: str) -> None:
    """自绘圆角底+描边。WA_TranslucentBackground 下 stylesheet 背景经常不画。"""
    p = QPainter(widget)
    p.setRenderHint(QPainter.Antialiasing, True)
    r = QRectF(widget.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
    p.setBrush(QBrush(_toast_bg()))
    try:
        pen = QColor(border_hex)
    except Exception:
        pen = QColor(_accent_hex("info"))
    if not pen.isValid():
        pen = QColor(_accent_hex("info"))
    p.setPen(QPen(pen, 1.2))
    p.drawRoundedRect(r, float(_RADIUS), float(_RADIUS))
    p.end()


def _short_name(name: str, max_len: int = _NAME_MAX) -> str:
    """名称过长时中间省略，避免气泡撑满屏。"""
    s = (name or "").strip()
    if len(s) <= max_len:
        return s
    keep = max(4, (max_len - 1) // 2)
    return s[:keep] + "…" + s[-(max_len - keep - 1):]


def sanitize_batch_record_name(name: str) -> str:
    """批处理气泡用的记录卡名。

    旧版从预下载队列转出的卡叫「预下载_e-hentai.txt」，
    原样显示会变成「预下载 e-hentai · 3/5」，像在跑预下载。
    批处理只保留卡的内容名；剥干净后若空则用「批处理」。
    """
    s = " ".join((name or "").replace("_", " ").split())
    for pfx in ("预下载 ", "预下载"):
        if s.startswith(pfx):
            s = s[len(pfx):].strip(" -")
            break
    return s


def format_toast_main(name, status="", current=None, total=None) -> str:
    """上行：名称 · [X/Y ]状态。总数 ≤1 时不写进度，避免「1/1 完成」这种空话。"""
    head = _short_name(name)
    mid_parts = []
    if total is not None:
        try:
            y = max(0, int(total))
        except (TypeError, ValueError):
            y = 0
        if y > 1:
            try:
                x = max(0, int(0 if current is None else current))
            except (TypeError, ValueError):
                x = 0
            mid_parts.append(f"{x}/{y}")
    st = (status or "").strip()
    if st:
        mid_parts.append(st)
    mid = " ".join(mid_parts)
    if head and mid:
        return f"{head} · {mid}"
    return head or mid


def format_toast_sub(ok=0, skip=0, err=0, hint="") -> str:
    """下行：成n 跳m 败k[ · 提示]。"""
    try:
        ok_n = max(0, int(ok))
    except (TypeError, ValueError):
        ok_n = 0
    try:
        skip_n = max(0, int(skip))
    except (TypeError, ValueError):
        skip_n = 0
    try:
        err_n = max(0, int(err))
    except (TypeError, ValueError):
        err_n = 0
    left = f"成{ok_n} 跳{skip_n} 败{err_n}"
    h = (hint or "").strip()
    if h:
        return f"{left} · {h}"
    return left


def _need_sub_line(ok=0, skip=0, err=0, total=None) -> bool:
    """第二行只在「真有统计」时出现：批量（总数>1）或成/跳/败至少两项非零。"""
    try:
        tot = int(total) if total is not None else 0
    except (TypeError, ValueError):
        tot = 0
    hits = 0
    for n in (ok, skip, err):
        try:
            if int(n or 0) > 0:
                hits += 1
        except (TypeError, ValueError):
            pass
    return tot > 1 or hits >= 2


def _bubble_xy_for_cursor(gp, w, h):
    """气泡定位：默认在鼠标右下方 _TOAST_OFFSET 像素。

    右侧放不下 → 优先「鼠标正下方」（水平居中于光标），再退「左下方」；
    底部放不下 → 放到鼠标上方。最后兜底夹紧，保证任何情况都不出屏。
    返回 (x, y)。
    """
    off = _TOAST_OFFSET
    x = gp.x() + off
    y = gp.y() + off
    sc = QApplication.screenAt(gp)
    if sc is None:
        sc = QApplication.primaryScreen()
    if sc is None:
        return x, y
    g = sc.availableGeometry()
    left, top = g.left(), g.top()
    right, bottom = g.right(), g.bottom()
    if x + w > right:
        xc = gp.x() - w // 2
        if xc >= left and xc + w <= right:
            x = xc
        else:
            x = gp.x() - w - off
    if y + h > bottom:
        y = gp.y() - h - off
    x = max(left, min(x, right - w))
    y = max(top, min(y, bottom - h))
    return int(x), int(y)


def _layout_toast(host, main, sub, extra=None):
    """一行/两行/三行：有下行文字才撑对应行。extra 用于并发提示叠加。"""
    main.adjustSize()
    rows = [main]
    try:
        if (sub.text() or "").strip():
            rows.append(sub)
    except Exception:
        pass
    if extra is not None:
        try:
            if (extra.text() or "").strip():
                rows.append(extra)
        except Exception:
            pass
    for r in (sub, extra):
        if r is None:
            continue
        try:
            r.setVisible(r in rows)
        except Exception:
            pass
    if len(rows) == 1:
        host.resize(main.width() + _PAD_X, main.height() + _PAD_Y * 2)
        main.move((host.width() - main.width()) // 2, _PAD_Y)
        return
    for r in rows[1:]:
        r.adjustSize()
    gap = 2
    content_w = max(r.width() for r in rows)
    content_h = sum(r.height() for r in rows) + gap * (len(rows) - 1)
    host.resize(content_w + _PAD_X, content_h + _PAD_Y * 2)
    y = _PAD_Y
    for r in rows:
        r.move((host.width() - r.width()) // 2, y)
        y += r.height() + gap


def _toast_gc(t):
    global _TOAST_BUBBLE
    try:
        if t is _TOAST_BUBBLE:
            _TOAST_BUBBLE = None
    except Exception:
        pass
    try:
        if t in _TOAST_KEEPALIVE:
            _TOAST_KEEPALIVE.remove(t)
    except Exception:
        pass


class CursorToast(QWidget):
    """在鼠标附近弹出的小提示，不抢焦点、不拦截点击，自动淡出。"""

    def __init__(self, text, accent="info", stay=2800, cycle=False, subtitle=""):
        super().__init__(None)
        del cycle  # 旧参数：不再变色闪烁
        self.setObjectName("CursorToast")
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowOpacity(0.0)

        self._accent = accent or "info"
        self._main = QLabel(text or "", self)
        self._main.setAlignment(Qt.AlignHCenter)
        self._main.setWordWrap(False)
        self._sub = QLabel((subtitle or "").strip(), self)
        self._sub.setAlignment(Qt.AlignHCenter)
        self._sub.setWordWrap(False)
        self._extra = QLabel("", self)
        self._extra.setAlignment(Qt.AlignHCenter)
        self._extra.setWordWrap(False)
        self._apply_shell()
        self._relayout()
        try:
            theme.changed.connect(self._on_theme_changed)
        except Exception:
            pass

        # 固定偏移跟随鼠标（默认右下 24px，出屏自动换位），不再随机摆放
        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(_FOLLOW_MS)
        self._follow_timer.timeout.connect(self._follow_cursor)
        self._follow_timer.start()
        self._follow_cursor()

        self.show()
        self.raise_()
        self._force_topmost()
        self._fade_in(stay)

    def paintEvent(self, _event):
        _paint_toast_shell(self, _accent_hex(self._accent))

    def _apply_shell(self):
        self.setStyleSheet("#CursorToast{background:transparent;border:none;}")
        self._main.setStyleSheet(_label_qss(_toast_fg(), _PX_MAIN, _W_MAIN))
        self._sub.setStyleSheet(_label_qss(_toast_fg_mut(), _PX_SUB, _W_SUB))
        self._extra.setStyleSheet(_label_qss(_toast_fg(), _PX_SUB, _W_MAIN))

    def _relayout(self):
        _layout_toast(self, self._main, self._sub, self._extra)

    def _on_theme_changed(self, *_args):
        self._apply_shell()
        self._relayout()
        self.update()

    def _follow_cursor(self):
        """显示期间跟随鼠标移动（默认在鼠标右下方 24px，出屏自动换位）。"""
        try:
            gp = QCursor.pos()
        except Exception:
            return
        x, y = _bubble_xy_for_cursor(gp, self.width(), self.height())
        self.move(x, y)

    def append_extra(self, main: str, sub: str = "", stay: int = 2200):
        """并发提示叠到第 3 行；同一时间只有一个气泡。"""
        pieces = []
        if (main or "").strip():
            pieces.append((main or "").strip())
        if (sub or "").strip():
            pieces.append((sub or "").strip())
        if not pieces:
            return
        old = self._extra.text() or ""
        new = "\n".join([old, *pieces]) if old else "\n".join(pieces)
        self._extra.setText(new)
        self._relayout()
        self._follow_cursor()
        self._restart_stay(stay)

    def _restart_stay(self, stay: int):
        """重新计留时间：取消进行中的淡出，回到不透明再开始倒数。"""
        try:
            f = getattr(self, "_fadeout", None)
            if f is not None:
                f.stop()
                self._fadeout = None
        except Exception:
            pass
        try:
            self.setWindowOpacity(1.0)
        except Exception:
            pass
        self._stay_timer.start(max(1, int(stay)))

    def closeEvent(self, e):
        try:
            theme.changed.disconnect(self._on_theme_changed)
        except Exception:
            pass
        try:
            self._follow_timer.stop()
        except Exception:
            pass
        try:
            self._stay_timer.stop()
        except Exception:
            pass
        super().closeEvent(e)

    def _force_topmost(self):
        """Win32 SetWindowPos 强制置顶，避免被其它置顶窗（如截图遮罩）压在下面。"""
        try:
            import ctypes
            hwnd = int(self.winId())
            # HWND_TOPMOST=-1; SWP_NOSIZE|SWP_NOMOVE|SWP_NOACTIVATE
            ctypes.windll.user32.SetWindowPos(
                hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010
            )
        except Exception:
            pass

    def _fade_in(self, stay):
        self._stay_timer = QTimer(self)
        self._stay_timer.setSingleShot(True)
        self._stay_timer.timeout.connect(self._fade_out)
        self._fadein = QPropertyAnimation(self, b"windowOpacity", self)
        self._fadein.setDuration(140)
        self._fadein.setStartValue(0.0)
        self._fadein.setEndValue(1.0)
        self._fadein.setEasingCurve(QEasingCurve.OutCubic)
        self._fadein.finished.connect(
            lambda: self._stay_timer.start(max(1, int(stay)))
        )
        self._fadein.start()

    def _fade_out(self):
        if self.windowOpacity() <= 0.01:
            self.close()
            return
        # 淡出期间仍跟随；close 时停表
        self._fadeout = QPropertyAnimation(self, b"windowOpacity", self)
        self._fadeout.setDuration(220)
        self._fadeout.setStartValue(self.windowOpacity())
        self._fadeout.setEndValue(0.0)
        self._fadeout.setEasingCurve(QEasingCurve.InCubic)
        self._fadeout.finished.connect(self.close)
        self._fadeout.start()


def show_cursor_toast(
    name,
    status="",
    *,
    current=None,
    total=None,
    ok=0,
    skip=0,
    err=0,
    hint="",
    accent="info",
    stay=1400,
    cycle=False,
):
    """瞬时气泡。同一时间只有一条气泡：
    已有气泡在显示时，新提示叠到第 3 行（在现有两行内容下方）并重新计时。
    上行：name · [current/total ]status（总数≤1 不写进度）
    下行（可选）：成ok 跳skip 败err[ · hint]
    cycle 已废弃，保留参数以免旧调用报错。
    """
    del cycle
    try:
        main = format_toast_main(name, status, current=current, total=total)
        sub = ""
        if _need_sub_line(ok, skip, err, total):
            sub = format_toast_sub(ok=ok, skip=skip, err=err, hint=hint)
        global _TOAST_BUBBLE
        b = _TOAST_BUBBLE
        if b is not None and b.isVisible():
            b.append_extra(main, sub, stay=stay)
            return
        t = CursorToast(main, accent=accent, stay=stay, subtitle=sub)
        _TOAST_BUBBLE = t
        _TOAST_KEEPALIVE.append(t)
        t.destroyed.connect(lambda: _toast_gc(t))
    except Exception:
        pass


# ============================================================
# F6 记录模式常驻气泡：跟随鼠标 · 绿色「记录数为N」慢闪
# ============================================================
_RECORD_BUBBLE = None  # type: RecordBubble | None


class RecordBubble(QWidget):
    """记录模式下跟随鼠标的常驻气泡：不闪烁。"""

    def __init__(self):
        super().__init__(None)
        self.setObjectName("RecordBubble")
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        self._main = QLabel("", self)
        self._main.setAlignment(Qt.AlignHCenter)
        self._main.setWordWrap(False)
        self._sub = QLabel("", self)
        self._sub.setAlignment(Qt.AlignHCenter)
        self._sub.setWordWrap(False)
        self._apply_count(0)
        self._apply_shell()
        self._relayout()
        try:
            theme.changed.connect(self._on_theme_changed)
        except Exception:
            pass

        # 跟随鼠标；不闪烁，避免叠在浅色网页上时字色时隐时现
        self.setWindowOpacity(1.0)
        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(_FOLLOW_MS)
        self._follow_timer.timeout.connect(self._follow_cursor)
        self._follow_timer.start()

        self._follow_cursor()
        self.show()
        self.raise_()

    def paintEvent(self, _event):
        _paint_toast_shell(self, _accent_hex("ok"))

    def _apply_shell(self):
        self.setStyleSheet("#RecordBubble{background:transparent;border:none;}")
        self._main.setStyleSheet(_label_qss(_toast_fg(), _PX_MAIN, _W_MAIN))
        self._sub.setStyleSheet(_label_qss(_toast_fg_mut(), _PX_SUB, _W_SUB))

    def _relayout(self):
        _layout_toast(self, self._main, self._sub)

    def _on_theme_changed(self, *_args):
        self._apply_shell()
        self._relayout()
        self.update()

    def _apply_count(self, n: int):
        self._main.setText(format_toast_main("速存图文", "记录中"))
        self._sub.setText(format_toast_sub(ok=n, hint="F6结束"))

    def update_count(self, n: int):
        """更新下行成n：当前总条数。"""
        try:
            n = max(0, int(n))
        except (TypeError, ValueError):
            n = 0
        self._apply_count(n)
        self._relayout()

    def _follow_cursor(self):
        try:
            gp = QCursor.pos()
        except Exception:
            return
        x, y = _bubble_xy_for_cursor(gp, self.width(), self.height())
        self.move(x, y)
        self._force_topmost()

    def _force_topmost(self):
        """Win32 SetWindowPos 强制置顶，避免被其它置顶窗压在下面。"""
        try:
            import ctypes
            hwnd = int(self.winId())
            ctypes.windll.user32.SetWindowPos(
                hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010
            )
        except Exception:
            pass

    def closeEvent(self, e):
        try:
            theme.changed.disconnect(self._on_theme_changed)
        except Exception:
            pass
        try:
            self._follow_timer.stop()
        except Exception:
            pass
        super().closeEvent(e)


def _record_bubble_gc(*_args):
    global _RECORD_BUBBLE
    _RECORD_BUBBLE = None


def start_record_bubble(n: int = 0):
    """进入 F6 记录模式时调用：常驻气泡，不闪烁。"""
    global _RECORD_BUBBLE
    try:
        b = _RECORD_BUBBLE
        if b is not None:
            if b.isVisible():
                b.update_count(n)
                return
            try:
                b.close()
            except Exception:
                pass
            _RECORD_BUBBLE = None
        b = RecordBubble()
        b.update_count(n)
        _RECORD_BUBBLE = b
        b.destroyed.connect(_record_bubble_gc)
    except Exception:
        pass


def stop_record_bubble():
    """退出 F6 记录模式时调用：关闭跟随鼠标的记录中气泡。"""
    global _RECORD_BUBBLE
    try:
        if _RECORD_BUBBLE is not None:
            _RECORD_BUBBLE.close()
            _RECORD_BUBBLE = None
    except Exception:
        pass


def update_record_bubble_count(n: int):
    """更新气泡中的数字。"""
    global _RECORD_BUBBLE
    try:
        if _RECORD_BUBBLE is not None and _RECORD_BUBBLE.isVisible():
            _RECORD_BUBBLE.update_count(n)
    except Exception:
        pass


# 批处理气泡平台展示名：key（引擎内部）→ 气泡前缀 ZZ
_BATCH_PLATFORM_LABELS = {
    "douyin": "抖音",
    "bilibili": "B站",
    "youtube": "YouTube",
    "ehentai": "e-hentai.org",
    "pixiv": "pixiv",
    "hitomi": "hitomi.la",
    # 展示名自身也可直接传入
    "抖音": "抖音",
    "B站": "B站",
    "YouTube": "YouTube",
    "e-hentai.org": "e-hentai.org",
    "exhentai.org": "e-hentai.org",
    "pixiv.net": "pixiv",
    "hitomi.la": "hitomi.la",
    "ehentai": "e-hentai.org",
    "e-hentai.org": "e-hentai.org",
    "序列补全": "序列补全",
    "fill": "序列补全",
}


def batch_platform_label(platform) -> str:
    """把引擎 key / 域名 / 展示名统一成气泡用的 ZZ。"""
    key = (platform or "").strip()
    if not key:
        return ""
    if key in _BATCH_PLATFORM_LABELS:
        return _BATCH_PLATFORM_LABELS[key]
    low = key.lower()
    if low in _BATCH_PLATFORM_LABELS:
        return _BATCH_PLATFORM_LABELS[low]
    return key


# ============================================================
# 自动下载 / 批处理 / 序列补全 常驻气泡：跟随鼠标 · 两行
#   第一行：记录名 · X/Y[ 状态词]   （记录名必有：批处理=卡名，序列补全=文件夹名）
#   第二行：成n 跳m 败k · 按F6中止
# 一直显示到 stop_batch_progress_toast()；禁止再弹独立 CursorToast
# ============================================================
_BATCH_BUBBLE = None  # type: BatchBubble | None

def in_batch_download(widget=None) -> bool:
    """判断是否处于批量下载模式。

    1) 祖先链上有 _batch_mode（视频子页挂在 PageVideo 下）
    2) 批处理常驻气泡正在显示（图集页与 PageVideo 非父子关系，靠此识别自动下载）

    批量中单条完成不应弹独立「完成下载」；成功/重复写常驻气泡第一行。
    """
    try:
        w = widget
        while w is not None:
            if getattr(w, "_batch_mode", False):
                return True
            try:
                w = w.parent()
            except Exception:
                break
    except Exception:
        pass
    # 自动下载：图集等页面不在 PageVideo 父链上，用常驻气泡判定
    try:
        b = _BATCH_BUBBLE
        if b is not None and b.isVisible():
            return True
    except Exception:
        pass
    return False


class BatchBubble(QWidget):
    """自动下载进度常驻气泡：两行，跟随鼠标，不抢焦点、不拦截点击。"""

    def __init__(self, record_name: str):
        super().__init__(None)
        self.setObjectName("BatchBubble")
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        self._current = 0
        self._total = 0
        self._note = ""
        self._ok = 0
        self._err = 0
        self._skip = 0
        self._record_name = sanitize_batch_record_name(record_name) or "批处理"

        self._main = QLabel("", self)
        self._main.setAlignment(Qt.AlignHCenter)
        self._main.setWordWrap(False)
        self._sub = QLabel("成0 跳0 败0 · 按F6中止", self)
        self._sub.setAlignment(Qt.AlignHCenter)
        self._sub.setWordWrap(False)
        self._apply_shell()
        self._relayout()
        try:
            theme.changed.connect(self._on_theme_changed)
        except Exception:
            pass

        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(_FOLLOW_MS)
        self._follow_timer.timeout.connect(self._follow_cursor)
        self._follow_timer.start()

        self.setWindowOpacity(1.0)
        self._follow_cursor()
        self.show()
        self.raise_()
        self._force_topmost()

    def paintEvent(self, _event):
        _paint_toast_shell(self, _accent_hex("ok"))

    def update_progress(
        self,
        current,
        total,
        note: str = "",
        *,
        ok: int = 0,
        err: int = 0,
        skip: int = 0,
        record_name: str = "",
    ):
        """刷新两行。上行：记录名 · X/Y [note]；下行：成n 跳m 败k · 按F6中止。"""
        note = (note or "").strip()
        try:
            x = max(0, int(current))
        except (TypeError, ValueError):
            x = 0
        try:
            y = max(0, int(total))
        except (TypeError, ValueError):
            y = 0
        if y <= 0:
            return
        rn = sanitize_batch_record_name(record_name)
        if rn:
            self._record_name = rn
        self._current = x
        self._total = y
        self._note = note
        try:
            self._ok = max(0, int(ok))
        except (TypeError, ValueError):
            self._ok = 0
        try:
            self._err = max(0, int(err))
        except (TypeError, ValueError):
            self._err = 0
        try:
            self._skip = max(0, int(skip))
        except (TypeError, ValueError):
            self._skip = 0
        self._apply_text()

    def set_note(self, note: str):
        """只改上行状态词，进度数字与累计不变。"""
        self._note = (note or "").strip()
        if self._total <= 0:
            return
        self._apply_text()

    def set_stats(self, ok: int = 0, err: int = 0, skip: int = 0):
        """只刷新累计成/跳/败。"""
        try:
            self._ok = max(0, int(ok))
        except (TypeError, ValueError):
            self._ok = 0
        try:
            self._err = max(0, int(err))
        except (TypeError, ValueError):
            self._err = 0
        try:
            self._skip = max(0, int(skip))
        except (TypeError, ValueError):
            self._skip = 0
        if self._total <= 0:
            return
        self._apply_text()

    def _apply_text(self):
        self._main.setText(format_toast_main(
            self._record_name, self._note,
            current=self._current, total=self._total,
        ))
        self._sub.setText(format_toast_sub(
            ok=self._ok, skip=self._skip, err=self._err, hint="按F6中止",
        ))
        self._relayout()
        self._follow_cursor()

    def _apply_shell(self):
        self.setStyleSheet("#BatchBubble{background:transparent;border:none;}")
        self._main.setStyleSheet(_label_qss(_toast_fg(), _PX_MAIN, _W_MAIN))
        self._sub.setStyleSheet(_label_qss(_toast_fg_mut(), _PX_SUB, _W_SUB))

    def _on_theme_changed(self, *_args):
        self._apply_shell()
        self._relayout()
        self.update()

    def _relayout(self):
        _layout_toast(self, self._main, self._sub)

    def _follow_cursor(self):
        try:
            gp = QCursor.pos()
        except Exception:
            return
        x, y = _bubble_xy_for_cursor(gp, self.width(), self.height())
        self.move(x, y)
        self._force_topmost()

    def _force_topmost(self):
        try:
            import ctypes
            hwnd = int(self.winId())
            ctypes.windll.user32.SetWindowPos(
                hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010
            )
        except Exception:
            pass

    def closeEvent(self, e):
        try:
            theme.changed.disconnect(self._on_theme_changed)
        except Exception:
            pass
        try:
            self._follow_timer.stop()
        except Exception:
            pass
        super().closeEvent(e)


def _batch_bubble_gc(*_args):
    global _BATCH_BUBBLE
    _BATCH_BUBBLE = None


def show_batch_progress_toast(
    platform,
    current,
    total,
    accent="ok",
    stay=2200,
    already: bool = False,
    note: str = "",
    *,
    ok: int = 0,
    err: int = 0,
    skip: int = 0,
    mixed: bool = False,
    n_platforms: int = 0,
    record_name: str = "",
    hint: str = "",
):
    """常驻进度气泡两行：上行「记录名 · X/Y [note]」；下行「成n 跳m 败k · 按F6中止」。

    首次创建必须带 record_name（批处理卡名 / 序列补全文件夹名）。
    后续只改 note/进度时可不传 record_name（沿用已有）。
    platform / mixed / n_platforms / accent / stay / already / hint 仅为旧调用兼容，已忽略。
    """
    del platform, accent, stay, mixed, n_platforms, hint
    if already and not (note or "").strip():
        note = "已下载过"
    global _BATCH_BUBBLE
    try:
        y = int(total)
    except (TypeError, ValueError):
        y = 0
    if y <= 0:
        return
    rn = sanitize_batch_record_name(record_name)
    try:
        b = _BATCH_BUBBLE
        if b is not None:
            try:
                if b.isVisible():
                    b.update_progress(
                        current, total, note=note,
                        ok=ok, err=err, skip=skip, record_name=rn,
                    )
                    return
            except Exception:
                pass
            try:
                b.close()
            except Exception:
                pass
            _BATCH_BUBBLE = None
        # 首次必须有记录名
        if not rn:
            return
        b = BatchBubble(rn)
        b.update_progress(
            current, total, note=note,
            ok=ok, err=err, skip=skip, record_name=rn,
        )
        _BATCH_BUBBLE = b
        b.destroyed.connect(_batch_bubble_gc)
    except Exception:
        pass


def set_batch_progress_note(note: str) -> bool:
    """只改常驻气泡上行状态词（成功/已下载过/已预约中止…）。

    用于图集等不在 PageVideo 父链上、但自动下载气泡已显示的场景。
    成功改写返回 True；无常驻气泡返回 False。
    """
    global _BATCH_BUBBLE
    try:
        b = _BATCH_BUBBLE
        if b is not None and b.isVisible():
            b.set_note(note)
            return True
    except Exception:
        pass
    return False


def set_batch_progress_stats(ok: int = 0, err: int = 0, skip: int = 0) -> bool:
    """只改常驻气泡累计成/跳/败。"""
    global _BATCH_BUBBLE
    try:
        b = _BATCH_BUBBLE
        if b is not None and b.isVisible() and hasattr(b, "set_stats"):
            b.set_stats(ok=ok, err=err, skip=skip)
            return True
    except Exception:
        pass
    return False


def stop_batch_progress_toast():
    """批处理结束时关闭常驻进度气泡。"""
    global _BATCH_BUBBLE
    try:
        if _BATCH_BUBBLE is not None:
            _BATCH_BUBBLE.close()
            _BATCH_BUBBLE = None
    except Exception:
        pass


def batch_progress_prefix(widget) -> str:
    """批量模式下返回「ZZ批处理 X/Y 」前缀，否则返回空串。

    用于在解析/下载状态前显示总体进度，方便批量中看清第几条、共几条。
    向上查找带 _batch_mode/_batch_index/_batch_urls 的祖先（平台页 → PageVideo）。
    """
    try:
        w = widget
        while w is not None:
            if getattr(w, "_batch_mode", False):
                total = len(getattr(w, "_batch_urls", []) or [])
                idx = int(getattr(w, "_batch_index", 0) or 0)
                if total > 0:
                    plat = (
                        getattr(w, "_batch_current_platform", None)
                        or getattr(w, "_batch_platform_label", None)
                        or ""
                    )
                    zz = batch_platform_label(plat)
                    if zz:
                        return f"{zz}批处理 {idx}/{total} "
                    return f"批处理 {idx}/{total} "
                return ""
            try:
                w = w.parent()
            except Exception:
                break
    except Exception:
        pass
    return ""
