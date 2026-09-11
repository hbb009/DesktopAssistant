# pages/page_timezone_fx.py
# 「时区汇率」页面：
#   无整页滚动；左右高度 100%、宽 70% / 30%
#   左 70%：世界时钟——多城市中文模拟时钟，可增删；城市区右侧标准竖向滚动条
#            模拟时间：日历选日期 + 时分秒上下按钮；含一键还原当前时间
#   右 30%：汇率转换器——由「比例计算」页迁移而来

import json, os
from datetime import datetime, timedelta, timezone

from styles.style_all import (
    theme, tk, install_card_title, restyle_card_title, make_card,
    apply_transparent_surface,
    CARD_BOTTOM_GAP, CARD_TITLE_BODY_GAP,
)
from utils.flow_layout import FlowLayout

from PyQt5.QtCore import Qt, QTimer, QUrl, QRectF, QPointF, QDate, QSize, QEvent
from PyQt5.QtGui import QPainter, QColor, QPen, QBrush, QFont
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QDateEdit, QScrollArea, QFrame,
    QSizePolicy,
)

# 分隔线粗细（px）——与「比例计算 / 目录映射」一致
_SEP_PX = 3

# ── 时区数据库（可选）：优先 zoneinfo（含夏令时），失败则退回固定偏移 ──
try:
    from zoneinfo import ZoneInfo
    _HAS_ZONEINFO = True
except Exception:
    ZoneInfo = None
    _HAS_ZONEINFO = False


def make_tz(key: str, fixed_offset_hours: float):
    """返回一个 tzinfo。优先使用系统时区库（正确处理夏令时）；
    若不可用（如 Windows 未安装 tzdata），退回为固定 UTC 偏移。"""
    if _HAS_ZONEINFO:
        try:
            return ZoneInfo(key)
        except Exception:
            pass
    return timezone(timedelta(hours=fixed_offset_hours))


# 城市预设表：中文名 → (旗帜, IANA 时区, 固定偏移小时[退回用])
CITY_PRESETS = {
    "洛杉矶":   ("🇺🇸", "America/Los_Angeles", -8),
    "纽约":     ("🇺🇸", "America/New_York",    -5),
    "芝加哥":   ("🇺🇸", "America/Chicago",     -6),
    "圣保罗":   ("🇧🇷", "America/Sao_Paulo",   -3),
    "伦敦":     ("🇬🇧", "Europe/London",        0),
    "巴黎":     ("🇫🇷", "Europe/Paris",         1),
    "柏林":     ("🇩🇪", "Europe/Berlin",        1),
    "莫斯科":   ("🇷🇺", "Europe/Moscow",        3),
    "迪拜":     ("🇦🇪", "Asia/Dubai",           4),
    "加尔各答": ("🇮🇳", "Asia/Kolkata",         5.5),
    "曼谷":     ("🇹🇭", "Asia/Bangkok",         7),
    "上海":     ("🇨🇳", "Asia/Shanghai",        8),
    "香港":     ("🇭🇰", "Asia/Hong_Kong",       8),
    "台北":     ("🇹🇼", "Asia/Taipei",          8),
    "新加坡":   ("🇸🇬", "Asia/Singapore",       8),
    "首尔":     ("🇰🇷", "Asia/Seoul",           9),
    "东京":     ("🇯🇵", "Asia/Tokyo",           9),
    "悉尼":     ("🇦🇺", "Australia/Sydney",     10),
    "UTC":      ("🌐", "UTC",                    0),
}

# 默认展示的 8 个城市
DEFAULT_CITIES = ["洛杉矶", "纽约", "伦敦", "巴黎", "迪拜", "加尔各答", "上海", "东京"]

# 星期与时段中文
_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# ══════════════════════════ 时分秒上下调整控件 ══════════════════════════
class TimeSpinner(QWidget):
    """三列（时/分/秒）上下按钮调整时间，纯鼠标即可操作（无「时」「分」「秒」文字标签）。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._h = self._m = self._s = 0
        self._cb = None            # 用户点击调整后的回调
        self._val = {}

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self._btns, self._vals = [], []
        for key in ("h", "m", "s"):
            col = QVBoxLayout(); col.setSpacing(3); col.setAlignment(Qt.AlignHCenter)
            up = QPushButton("▲"); dn = QPushButton("▼")
            for b in (up, dn):
                b.setFixedSize(40, 22)
                b.setCursor(Qt.PointingHandCursor)
                self._btns.append(b)
            val = QLabel("00"); val.setAlignment(Qt.AlignCenter); val.setFixedSize(40, 26)
            self._vals.append(val)
            up.clicked.connect(lambda _=False, k=key, d=1: self._step(k, d))
            dn.clicked.connect(lambda _=False, k=key, d=-1: self._step(k, d))
            col.addWidget(up); col.addWidget(val); col.addWidget(dn)
            self._val[key] = val
            lay.addLayout(col)

        self.refresh_theme()

    def refresh_theme(self, *_):
        btn_qss = (
            f"QPushButton{{background:{tk('panel')}; color:{tk('text_strong')};"
            f"border:1px solid {tk('border')};"
            "border-radius:4px; font-size:14px; font-weight:700; padding:0px;}"
            f"QPushButton:hover{{background:{tk('accent')}; color:#ffffff;"
            f"border-color:{tk('accent')};}}"
        )
        for b in self._btns:
            b.setStyleSheet(btn_qss)
        for v in self._vals:
            v.setStyleSheet(f"background:transparent; font-size:19px; font-weight:700; color:{tk('text')};")

    def _step(self, k, d):
        if k == "h": self._h = (self._h + d) % 24
        elif k == "m": self._m = (self._m + d) % 60
        else: self._s = (self._s + d) % 60
        self._refresh()
        if self._cb:
            self._cb()

    def set_time(self, h, m, s):
        self._h, self._m, self._s = int(h), int(m), int(s)
        self._refresh()

    def get_time(self):
        return self._h, self._m, self._s

    def on_user_change(self, cb):
        self._cb = cb

    def _refresh(self):
        self._val["h"].setText(f"{self._h:02d}")
        self._val["m"].setText(f"{self._m:02d}")
        self._val["s"].setText(f"{self._s:02d}")


# ══════════════════════════ 模拟时钟表盘 ══════════════════════════
class AnalogClock(QWidget):
    """纯绘制的模拟表盘：时/分针深色，秒针红色。"""
    def __init__(self, parent=None, diameter=140):
        super().__init__(parent)
        self._h = self._m = self._s = 0
        self.setFixedSize(diameter, diameter)

    def set_hms(self, h: int, m: int, s: int):
        self._h, self._m, self._s = h, m, s
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w = self.width(); h = self.height()
        side = min(w, h)
        p.translate(w / 2, h / 2)
        p.scale(side / 200.0, side / 200.0)   # 以 200×200 为逻辑坐标系

        # 外圈 + 表盘底
        p.setPen(QPen(QColor("#111827"), 5))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(QRectF(-96, -96, 192, 192))

        # 刻度
        p.setPen(QPen(QColor("#111827"), 2))
        for i in range(60):
            p.save()
            p.rotate(i * 6)
            if i % 5 == 0:
                p.setPen(QPen(QColor("#111827"), 3))
                p.drawLine(QPointF(0, -92), QPointF(0, -82))
            else:
                p.setPen(QPen(QColor("#9ca3af"), 1))
                p.drawLine(QPointF(0, -92), QPointF(0, -87))
            p.restore()

        # 数字 1..12
        p.setPen(QColor("#111827"))
        f = QFont("Arial", 15); f.setBold(True)
        p.setFont(f)
        import math
        for n in range(1, 13):
            ang = math.radians(n * 30)
            x = math.sin(ang) * 68
            y = -math.cos(ang) * 68
            p.drawText(QRectF(x - 14, y - 12, 28, 24),
                       Qt.AlignCenter, str(n))

        # 指针角度
        hour_ang = (self._h % 12) * 30 + self._m * 0.5
        min_ang = self._m * 6 + self._s * 0.1
        sec_ang = self._s * 6

        # 时针
        p.save(); p.rotate(hour_ang)
        p.setPen(QPen(QColor("#111827"), 6, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(0, 8), QPointF(0, -48)); p.restore()
        # 分针
        p.save(); p.rotate(min_ang)
        p.setPen(QPen(QColor("#1f2937"), 4, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(0, 10), QPointF(0, -74)); p.restore()
        # 秒针
        p.save(); p.rotate(sec_ang)
        p.setPen(QPen(QColor("#e11d48"), 2, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(0, 18), QPointF(0, -84)); p.restore()

        # 中心轴
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor("#111827")))
        p.drawEllipse(QRectF(-5, -5, 10, 10))
        p.setBrush(QBrush(QColor("#e11d48")))
        p.drawEllipse(QRectF(-2.5, -2.5, 5, 5))
        p.end()


class _ClockGridHost(QWidget):
    """世界时钟流式网格宿主：按宽度算总高度，供 QScrollArea 正确出竖向滚动。"""

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        lay = self.layout()
        if lay is not None and lay.hasHeightForWidth():
            return lay.heightForWidth(w)
        return super().heightForWidth(w)

    def sizeHint(self):
        w = max(self.width(), 320)
        return QSize(w, self.heightForWidth(w))

    def minimumSizeHint(self):
        return QSize(0, 0)


# ══════════════════════════ 单个城市块 ══════════════════════════
class CityClock(QWidget):
    """一个城市：顶部旗帜+中文名胶囊 + 红叉关闭按钮、中间表盘、底部两行日期/时段。"""
    # 表盘直径；卡片宽约等于表盘 + 左右边距，底部两行文字不再把卡撑宽
    _DIAL = 110

    def __init__(self, city_name: str, flag: str, tz, on_remove=None):
        super().__init__()
        self.city_name = city_name
        self.tz = tz
        self._on_remove = on_remove

        # 固定最大宽度 ≈ 表盘，流式网格每行可多排几列
        card_w = self._DIAL + 24
        self.setFixedWidth(card_w)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignHCenter)

        # 顶部标题胶囊（旗帜+中文名） + 关闭按钮（红叉）
        head = QHBoxLayout(); head.setSpacing(4)
        self.header = QLabel(f"{flag}  {city_name}")
        self.header.setAlignment(Qt.AlignCenter)
        self.header.setWordWrap(True)

        btn_del = self.btn_del = QPushButton("✕")
        btn_del.setFixedSize(26, 26)
        btn_del.setCursor(Qt.PointingHandCursor)
        btn_del.clicked.connect(lambda: self._on_remove and self._on_remove(self))
        head.addStretch(1); head.addWidget(self.header); head.addWidget(btn_del); head.addStretch(1)
        lay.addLayout(head)

        self.clock = AnalogClock(diameter=self._DIAL)
        lay.addWidget(self.clock, 0, Qt.AlignHCenter)

        # 底部两行：① 日期+星期  ② 时段 · 时刻 —— 单行过长会撑宽卡片
        self.footer = QLabel("--")
        self.footer.setAlignment(Qt.AlignCenter)
        self.footer.setWordWrap(True)
        self.footer.setFixedWidth(self._DIAL)
        lay.addWidget(self.footer, 0, Qt.AlignHCenter)

        self.refresh_theme()

    def refresh_theme(self, *_):
        self.header.setStyleSheet(
            f"background:{tk('panel')}; color:{tk('text_strong')}; border:1px solid {tk('border')};"
            "border-radius:7px; padding:4px 8px; font-weight:600; font-size:13px;"
        )
        self.btn_del.setStyleSheet(
            f"QPushButton{{background:{tk('panel')}; color:{tk('err')};"
            f"border:1px solid {tk('border')};"
            "border-radius:5px; font-size:17px; font-weight:900; padding:0px;}"
            f"QPushButton:hover{{background:{tk('err')}; color:#ffffff;"
            f"border-color:{tk('err')};}}"
        )
        self.footer.setStyleSheet(
            f"background:transparent; color:{tk('text_mut')}; font-size:12px;"
            "line-height:1.25;"
        )
        self.clock.update()          # 表盘是 QPainter 绘制，重绘即可

    def apply_utc(self, now_utc: datetime):
        local = now_utc.astimezone(self.tz)
        self.clock.set_hms(local.hour, local.minute, local.second)
        wd = _WEEKDAYS[local.weekday()]
        # 两行：7月06日 周一 / 上午 · 07:14
        self.footer.setText(
            f"{local.month}月{local.day:02d}日 {wd}\n"
            f"{self._period(local.hour)} · {local.strftime('%H:%M')}"
        )

    @staticmethod
    def _period(hour: int) -> str:
        if 5 <= hour < 11:  return "上午"
        if 11 <= hour < 13: return "中午"
        if 13 <= hour < 18: return "下午"
        if 18 <= hour < 22: return "傍晚"
        return "夜晚"


# ══════════════════════════ 页面主体 ══════════════════════════
class PageTimezoneFx(QWidget):
    def __init__(self):
        super().__init__()
        apply_transparent_surface(self, "PageTimezoneFx")

        def _typo(w: QLabel, name: str):
            w.setProperty("typo", name)
            w.style().unpolish(w); w.style().polish(w)
        self._typo = _typo

        # 模拟时间偏移量：0 = 实时；非 0 = 相对真实时间平移
        self._offset = timedelta(0)
        self._cities = []          # list[CityClock]
        self._updating_edit = False
        self._theme_titles = []
        self._h_seps = []
        self._v_seps = []  # 工具行短竖线 + 列间通栏竖线

        # 无整页滚动条：左右高度 100%；约 65% / 35%
        # 列间：8px 空隙 + 3px 实线 + 8px 空隙（标准分隔，不可拖动）
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        left_wrap = QWidget()
        apply_transparent_surface(left_wrap, "TzLeftWrap")
        left_wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_col = QVBoxLayout(left_wrap)
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(0)

        right_wrap = QWidget()
        apply_transparent_surface(right_wrap, "TzRightWrap")
        right_wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_col = QVBoxLayout(right_wrap)
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(0)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(left_wrap, 65)
        row.addSpacing(8)
        row.addWidget(self._make_col_v_sep())
        row.addSpacing(8)
        row.addWidget(right_wrap, 35)
        root.addLayout(row, 1)

        self._build_world_clock(left_col)
        self._build_fx(right_col)

        # 初始城市：优先读取用户保存的默认城市（records/world_clock_cities.txt）
        init_names = self._load_saved_cities() or DEFAULT_CITIES
        for name in init_names:
            self._add_city(name, refresh=False)
        if not self._cities:
            for name in DEFAULT_CITIES:
                self._add_city(name, refresh=False)
        self._relayout_grid()
        self._refresh_base_combo()

        # 每秒刷新
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()

        theme.changed.connect(self.refresh_theme)
        self._restyle_seps()

    # 工具行内短竖线（基准城市 | 模拟时间）
    def _vsep(self):
        f = QFrame()
        f.setObjectName("TzVSep")
        f.setFrameShape(QFrame.NoFrame)
        f.setFixedWidth(_SEP_PX)
        f.setFixedHeight(28)
        f.setAttribute(Qt.WA_StyledBackground, True)
        self._v_seps.append(f)
        return f

    def _make_col_v_sep(self) -> QFrame:
        """列间竖向 3px 实线（不可拖动，与比例计算/目录映射同规格）。"""
        f = QFrame()
        f.setObjectName("TzVSep")
        f.setFrameShape(QFrame.NoFrame)
        f.setFixedWidth(_SEP_PX)
        f.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        f.setAttribute(Qt.WA_StyledBackground, True)
        self._v_seps.append(f)
        return f

    def _hsep(self) -> QFrame:
        """横向 3px 实线（工具区与时钟网格之间）。"""
        f = QFrame()
        f.setObjectName("TzHSep")
        f.setFrameShape(QFrame.NoFrame)
        f.setFixedHeight(_SEP_PX)
        f.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        f.setAttribute(Qt.WA_StyledBackground, True)
        self._h_seps.append(f)
        return f

    def _restyle_seps(self):
        """横/竖 3px 实线，随主题 border 色（与比例计算一致）。"""
        color = tk("border")
        h_qss = (
            "QFrame#TzHSep{"
            f"background:{color};border:none;"
            f"max-height:{_SEP_PX}px;min-height:{_SEP_PX}px;}}"
        )
        v_qss = (
            "QFrame#TzVSep{"
            f"background:{color};border:none;"
            f"max-width:{_SEP_PX}px;min-width:{_SEP_PX}px;}}"
        )
        for f in self._h_seps:
            f.setStyleSheet(h_qss)
        for f in self._v_seps:
            f.setStyleSheet(v_qss)

    # ────────────────── 世界时钟区（左，高度占满） ──────────────────
    def _build_world_clock(self, col):
        gb = make_card("CardTzWorldClock", borderless=True)
        gb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        box = QVBoxLayout(gb)
        box.setSpacing(8)
        box.setContentsMargins(0, 0, 0, CARD_BOTTOM_GAP)
        self._theme_titles.append(install_card_title(gb, box, "世界时钟"))
        col.addWidget(gb, 1)  # 高度 100%

        # —— 第一行：基准城市 | 模拟时间(日期+时分秒) | 还原当前时间 ——
        row1 = FlowLayout(h_spacing=6, v_spacing=6)

        lbl_base = QLabel("基准城市："); self._typo(lbl_base, "body")
        self.cb_base = QComboBox()
        self.cb_base.setFixedHeight(30)
        # 宽度跟内容走，文字与箭头约 2 个汉字空隙（见 _fit_base_combo_width）
        self.cb_base.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.cb_base.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.cb_base.currentIndexChanged.connect(lambda _=None: self._sync_base_edit())
        row1.addWidget(lbl_base); row1.addWidget(self.cb_base)

        row1.addWidget(self._vsep())

        lbl_sim = QLabel("模拟时间："); self._typo(lbl_sim, "body")
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)                 # 弹出小日历（参考图2）
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setFixedHeight(30); self.date_edit.setMinimumWidth(100)
        self.date_edit.dateChanged.connect(lambda _=None: self._on_datetime_edited())
        self.time_spin = TimeSpinner()                        # 时/分/秒上下按钮（参考图3）
        self.time_spin.on_user_change(self._on_datetime_edited)
        row1.addWidget(lbl_sim); row1.addWidget(self.date_edit); row1.addWidget(self.time_spin)

        btn_now = QPushButton("↺ 还原当前时间")
        btn_now.setProperty("role", "primary"); btn_now.setFixedHeight(30)
        btn_now.style().unpolish(btn_now); btn_now.style().polish(btn_now)
        btn_now.clicked.connect(self._restore_now)
        row1.addWidget(btn_now)
        box.addLayout(row1)

        # —— 工具区与城市网格：空隙 + 3px 实线 + 空隙 ——
        box.addSpacing(8)
        box.addWidget(self._hsep())
        box.addSpacing(8)

        # —— 第二行：添加城市 ——
        row2 = FlowLayout(h_spacing=6, v_spacing=6)
        lbl_add = QLabel("添加城市："); self._typo(lbl_add, "body")
        self.cb_add = QComboBox(); self.cb_add.setFixedHeight(30); self.cb_add.setMinimumWidth(110)
        btn_add = QPushButton("＋ 添加")
        btn_add.setProperty("role", "nav"); btn_add.setFixedHeight(30)
        btn_add.style().unpolish(btn_add); btn_add.style().polish(btn_add)
        btn_add.clicked.connect(self._on_add_clicked)
        row2.addWidget(lbl_add); row2.addWidget(self.cb_add); row2.addWidget(btn_add)

        btn_save = QPushButton("💾 保存为默认城市")
        btn_save.setProperty("role", "nav"); btn_save.setFixedHeight(30)
        btn_save.style().unpolish(btn_save); btn_save.style().polish(btn_save)
        btn_save.clicked.connect(self._save_default_cities)
        btn_reset = QPushButton("↺ 复位")
        btn_reset.setProperty("role", "nav"); btn_reset.setFixedHeight(30)
        btn_reset.style().unpolish(btn_reset); btn_reset.style().polish(btn_reset)
        btn_reset.clicked.connect(self._reset_default_cities)
        row2.addWidget(btn_save); row2.addWidget(btn_reset)
        box.addLayout(row2)

        # 保存 / 复位 的操作反馈
        self.city_status = QLabel(""); self._typo(self.city_status, "muted")
        box.addWidget(self.city_status)

        # —— 时钟网格：右侧标准竖向滚动条，上下浏览 8 城（及更多）——
        self.clock_scroll = QScrollArea()
        self.clock_scroll.setObjectName("TzClockScroll")  # 记录区滚动条标准
        self.clock_scroll.setWidgetResizable(True)
        self.clock_scroll.setFrameShape(QFrame.NoFrame)
        self.clock_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.clock_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.clock_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.clock_scroll.viewport().setAutoFillBackground(False)
        self.clock_scroll.setStyleSheet(
            "QScrollArea#TzClockScroll{background:transparent;border:none;}"
            "QScrollArea#TzClockScroll > QWidget > QWidget{background:transparent;}"
        )

        # 内容宿主：按视口宽度 heightForWidth，保证 8 城换行后滚动高度正确
        self.grid_host = _ClockGridHost()
        apply_transparent_surface(self.grid_host, "TzClockGridHost")
        self.grid = FlowLayout(self.grid_host, h_spacing=10, v_spacing=14)
        self.clock_scroll.setWidget(self.grid_host)
        box.addWidget(self.clock_scroll, 1)

        if not _HAS_ZONEINFO:
            hint = QLabel("⚠ 未检测到系统时区数据库，已使用固定时区偏移（可能不含夏令时）。"
                          "如需精确夏令时，可安装：pip install tzdata")
            hint.setWordWrap(True); self._typo(hint, "muted")
            box.addWidget(hint)

    # ────────────────── 汇率换算（右，高度占满） ──────────────────
    def _build_fx(self, col):
        gb_fx = make_card("CardTzFx", borderless=True)
        gb_fx.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        fx_box = QVBoxLayout(gb_fx)
        fx_box.setContentsMargins(0, 0, 0, CARD_BOTTOM_GAP)
        fx_box.setSpacing(CARD_TITLE_BODY_GAP)
        self._theme_titles.append(install_card_title(gb_fx, fx_box, "汇率换算"))
        col.addWidget(gb_fx, 1)  # 高度 100%

        self._fx_updating = False

        # 汇率表：code → (旗帜, 名称, 1外币=?CNY, 符号)
        self._fx_rates = {
            "CNY": ("🇨🇳", "人民币",     1.0,    "¥"),
            "USD": ("🇺🇸", "美元",       7.25,   "$"),
            "HKD": ("🇭🇰", "港币",       0.91,   "HK$"),
            "EUR": ("🇪🇺", "欧元",       7.85,   "€"),
            "GBP": ("🇬🇧", "英镑",       9.20,   "£"),
            "JPY": ("🇯🇵", "日元",       0.049,  "¥"),
            "AUD": ("🇦🇺", "澳大利亚元",  4.71,   "A$"),
            "CAD": ("🇨🇦", "加元",       5.28,   "C$"),
            "SGD": ("🇸🇬", "新加坡元",   5.39,   "S$"),
            "TWD": ("🇹🇼", "新台币",     0.22,   "NT$"),
            "KRW": ("🇰🇷", "韩元",       0.0053, "₩"),
        }
        self._fx_inputs = {}
        self._fx_rate_labels = {}

        for code, (flag, name, rate, sym) in self._fx_rates.items():
            row = QHBoxLayout(); row.setSpacing(4)
            # 标签左对齐，stretch 在中间，输入框右对齐
            cn_name = "".join(ch for ch in name if "一" <= ch <= "鿿")[:3]
            lbl_flag = QLabel(cn_name); self._typo(lbl_flag, "body")
            lbl_flag.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            lbl_flag.setToolTip(name)
            lbl_rate = QLabel(f"≈{rate:.3f}"); self._typo(lbl_rate, "muted")
            lbl_rate.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
            self._fx_rate_labels[code] = lbl_rate
            inp = QLineEdit()
            init_val = 1.0 / rate
            inp.setText(self._fmt_fx(init_val, code))
            inp.setFixedHeight(28); inp.setAlignment(Qt.AlignRight)
            inp.setMinimumWidth(60); inp.setMaximumWidth(120)
            inp.setStyleSheet(
                f"QLineEdit {{"
                f"  font-size: 16px;"
                f"  font-weight: 600;"
                f"  color: {tk('text')};"
                f"  border: none;"
                f"  border-top: none;"
                f"  border-left: none;"
                f"  border-right: none;"
                f"  border-bottom: 1px solid {tk('border')};"
                f"  border-radius: 0;"
                f"  background: transparent;"
                f"  padding: 0px 2px 1px 2px;"
                f"  margin: 0;"
                f"  qproperty-alignment: AlignRight;"
                f"}}"
                f"QLineEdit:focus {{"
                f"  border: none;"
                f"  border-bottom: 2px solid {tk('accent')};"
                f"  background: transparent;"
                f"}}"
            )
            inp.textEdited.connect(lambda txt, c=code: self._fx_on_edit(c, txt))
            self._fx_inputs[code] = inp
            row.addWidget(lbl_flag, 3)  # ~0.8
            row.addWidget(lbl_rate, 4)  # ~0.8
            row.addStretch(1)
            row.addWidget(inp, 6)
            fx_box.addLayout(row)

        self._fx_card = gb_fx
        self._fx_rates_shown = True
        gb_fx.installEventFilter(self)

        btn_refresh_row = QHBoxLayout()
        btn_refresh = QPushButton("刷新实时汇率")
        btn_refresh.setProperty("role", "nav")
        btn_refresh.style().unpolish(btn_refresh); btn_refresh.style().polish(btn_refresh)
        btn_refresh.setFixedHeight(32)
        btn_refresh.clicked.connect(self._fx_fetch)
        btn_refresh_row.addStretch(1); btn_refresh_row.addWidget(btn_refresh)
        fx_box.addLayout(btn_refresh_row)

        self.fx_status = QLabel("💡 汇率为内置参考值，点击「刷新实时汇率」更新；任意输入框均可直接编辑")
        self.fx_status.setWordWrap(True); self._typo(self.fx_status, "muted")
        fx_box.addWidget(self.fx_status)
        fx_box.addStretch(1)  # 卡片拉满高度时，内容靠上、下方留白

        self._nam = QNetworkAccessManager(self)
        self._nam.finished.connect(self._fx_on_response)

    # ═══════════════ 世界时钟逻辑 ═══════════════
    def _add_city(self, name: str, refresh=True):
        if name not in CITY_PRESETS:
            return
        if any(c.city_name == name for c in self._cities):
            return
        flag, key, off = CITY_PRESETS[name]
        cc = CityClock(name, flag, make_tz(key, off), on_remove=self._remove_city)
        self._cities.append(cc)
        if refresh:
            self._relayout_grid()
            self._refresh_base_combo()
            self._tick()

    def _remove_city(self, cc: "CityClock"):
        if cc not in self._cities:
            return
        if len(self._cities) <= 1:       # 至少保留 1 个城市
            return
        self._cities.remove(cc)
        cc.setParent(None); cc.deleteLater()
        self._relayout_grid()
        self._refresh_base_combo()
        self._tick()

    def _on_add_clicked(self):
        name = self.cb_add.currentData()
        if name:
            self._add_city(name)
            self._refresh_add_combo()

    # ——— 默认城市的保存 / 读取 / 复位（records/world_clock_cities.txt） ———
    def _cities_file(self):
        try:
            from utils.app_paths import records_file
            return records_file("world_clock_cities.txt")
        except Exception:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            rec_dir = os.path.join(root, "data")
            try:
                os.makedirs(rec_dir, exist_ok=True)
            except Exception:
                pass
            return os.path.join(rec_dir, "world_clock_cities.txt")

    def _load_saved_cities(self):
        """读取已保存的默认城市列表；无文件或无有效项则返回 None。"""
        try:
            path = self._cities_file()
            if not os.path.exists(path):
                return None
            names = []
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    n = line.strip()
                    if n and n in CITY_PRESETS and n not in names:
                        names.append(n)
            return names or None
        except Exception:
            return None

    def _save_default_cities(self):
        try:
            path = self._cities_file()
            with open(path, "w", encoding="utf-8") as f:
                for cc in self._cities:
                    f.write(cc.city_name + "\n")
            self.city_status.setText(
                f"✅ 已保存 {len(self._cities)} 个城市为默认（data/world_clock_cities.txt）")
        except Exception as e:
            self.city_status.setText(f"❌ 保存失败：{e}")

    def _reset_default_cities(self):
        # 恢复默认 8 城，并清除已保存的记录（回到原厂设置）
        try:
            path = self._cities_file()
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        self._set_cities(list(DEFAULT_CITIES))
        self.city_status.setText("↺ 已复位为默认 8 个城市")

    def _set_cities(self, names):
        for cc in self._cities:
            cc.setParent(None); cc.deleteLater()
        self._cities = []
        for n in names:
            self._add_city(n, refresh=False)
        self._relayout_grid()
        self._refresh_base_combo()
        self._tick()

    def reload_cities_from_disk(self):
        """导入/重置后：从 records/world_clock_cities.txt 重载城市列表。"""
        names = self._load_saved_cities() or list(DEFAULT_CITIES)
        self._set_cities(names)

    def _relayout_grid(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        for cc in self._cities:
            cc.setParent(self.grid_host)
            self.grid.addWidget(cc)
            cc.show()
        self._refresh_add_combo()

    def _refresh_add_combo(self):
        current = {c.city_name for c in self._cities}
        self.cb_add.blockSignals(True)
        self.cb_add.clear()
        for name in CITY_PRESETS:
            if name not in current:
                flag = CITY_PRESETS[name][0]
                self.cb_add.addItem(f"{flag}  {name}", name)
        self.cb_add.blockSignals(False)

    def _fit_base_combo_width(self):
        """按最长城市名收窄宽度：文末到下拉箭头约 2 个汉字空隙。"""
        cb = getattr(self, "cb_base", None)
        if cb is None:
            return
        fm = cb.fontMetrics()
        max_tw = 0
        for i in range(cb.count()):
            max_tw = max(max_tw, fm.horizontalAdvance(cb.itemText(i) or ""))
        if max_tw <= 0:
            max_tw = fm.horizontalAdvance("上海")
        # 左 padding≈10 + 箭头区≈28（全站 COMBO_DROP_W）+ 2 汉字空隙
        gap_2cjk = fm.horizontalAdvance("国国")
        w = max_tw + 10 + 28 + gap_2cjk + 4
        cb.setFixedWidth(max(72, min(int(w), 240)))

    def _refresh_base_combo(self):
        prev = self.cb_base.currentData() if self.cb_base.count() else None
        self.cb_base.blockSignals(True)
        self.cb_base.clear()
        for cc in self._cities:
            flag = CITY_PRESETS.get(cc.city_name, ("", "", 0))[0]
            self.cb_base.addItem(f"{flag}  {cc.city_name}", cc.city_name)
        if prev is not None:
            i = self.cb_base.findData(prev)
            if i >= 0:
                self.cb_base.setCurrentIndex(i)
        else:
            i = self.cb_base.findData("上海")      # 默认基准城市：上海
            self.cb_base.setCurrentIndex(i if i >= 0 else 0)
        self.cb_base.blockSignals(False)
        self._fit_base_combo_width()
        self._sync_base_edit()

    def _base_tz(self):
        name = self.cb_base.currentData()
        for cc in self._cities:
            if cc.city_name == name:
                return cc.tz
        return self._cities[0].tz if self._cities else timezone.utc

    def _display_utc(self) -> datetime:
        return datetime.now(timezone.utc) + self._offset

    def _calendar_open(self) -> bool:
        try:
            cw = self.date_edit.calendarWidget()
            return cw is not None and cw.isVisible()
        except Exception:
            return False

    def _sync_base_edit(self):
        """把当前（含偏移）时间按基准城市显示到日期/时分秒控件（不改变偏移）。"""
        if self.date_edit.hasFocus() or self._calendar_open():
            return
        local = self._display_utc().astimezone(self._base_tz())
        self._updating_edit = True
        self.date_edit.blockSignals(True)
        self.date_edit.setDate(QDate(local.year, local.month, local.day))
        self.date_edit.blockSignals(False)
        self.time_spin.set_time(local.hour, local.minute, local.second)
        self._updating_edit = False

    def _on_datetime_edited(self):
        """用户改了日期或时分秒 → 反推偏移量。"""
        if self._updating_edit:
            return
        d = self.date_edit.date()
        h, m, s = self.time_spin.get_time()
        naive = datetime(d.year(), d.month(), d.day(), h, m, s)
        try:
            aware = naive.replace(tzinfo=self._base_tz())
            target_utc = aware.astimezone(timezone.utc)
            self._offset = target_utc - datetime.now(timezone.utc)
        except Exception:
            self._offset = timedelta(0)
        self._tick()

    def _restore_now(self):
        self._offset = timedelta(0)
        self._sync_base_edit()
        self._tick()

    def _tick(self):
        now_utc = self._display_utc()
        for cc in self._cities:
            cc.apply_utc(now_utc)
        self._sync_base_edit()

    def refresh_theme(self, *_):
        """重刷本页控件级样式（QSS 选择器覆盖不到的部分）。"""
        for lbl in getattr(self, "_theme_titles", []):
            restyle_card_title(lbl)
        self._restyle_seps()
        self.time_spin.refresh_theme()
        for cc in self.findChildren(CityClock):
            cc.refresh_theme()

    def on_enter(self):
        self._sync_base_edit()
        self._tick()

    # ═══════════════ 汇率转换逻辑（与原比例计算页一致） ═══════════════
    def eventFilter(self, obj, event):
        if obj is self._fx_card and event.type() == QEvent.Resize:
            w = self._fx_card.width()
            if self._fx_rates_shown and w <= 215:
                self._fx_rates_shown = False
                for lbl in self._fx_rate_labels.values():
                    lbl.setVisible(False)
            elif not self._fx_rates_shown and w >= 235:
                self._fx_rates_shown = True
                for lbl in self._fx_rate_labels.values():
                    lbl.setVisible(True)
        return super().eventFilter(obj, event)

    def _fx_on_edit(self, src_code: str, txt: str):
        if self._fx_updating:
            return
        txt = txt.strip()
        if not txt:
            return
        try:
            val = float(txt)
        except ValueError:
            return
        _, _, rate_src, _ = self._fx_rates[src_code]
        cny_val = val * rate_src
        self._fx_updating = True
        try:
            for code, (_, _, rate, _) in self._fx_rates.items():
                if code == src_code:
                    continue
                converted = cny_val / rate
                inp = self._fx_inputs[code]
                inp.blockSignals(True)
                inp.setText(self._fmt_fx(converted, code))
                inp.blockSignals(False)
        finally:
            self._fx_updating = False

    def _fmt_fx(self, val: float, code: str) -> str:
        return f"{val:.2f}"

    def _fx_fetch(self):
        self.fx_status.setText("⏳ 正在获取实时汇率…")
        req = QNetworkRequest(QUrl("https://open.er-api.com/v6/latest/CNY"))
        self._nam.get(req)

    def _fx_on_response(self, reply):
        try:
            data = bytes(reply.readAll()).decode("utf-8")
            obj = json.loads(data)
            ext_rates = obj.get("rates", {})
            updated = 0
            for code in list(self._fx_rates.keys()):
                if code == "CNY":
                    continue
                if code in ext_rates:
                    r_foreign = ext_rates[code]
                    if r_foreign == 0:
                        continue
                    r_cny = 1.0 / r_foreign
                    flag, name, _, sym = self._fx_rates[code]
                    self._fx_rates[code] = (flag, name, r_cny, sym)
                    self._fx_rate_labels[code].setText(f"≈{r_cny:.3f}")
                    updated += 1
            try:
                cny_val = float(self._fx_inputs["CNY"].text())
            except ValueError:
                cny_val = 1.0
            self._fx_updating = True
            for code, (_, _, rate, _) in self._fx_rates.items():
                if code == "CNY":
                    continue
                inp = self._fx_inputs[code]
                inp.blockSignals(True)
                inp.setText(self._fmt_fx(cny_val / rate, code))
                inp.blockSignals(False)
            self._fx_updating = False
            self.fx_status.setText(f"🟢 已更新 {updated} 个汇率（来源：open.er-api.com）")
        except Exception as e:
            self.fx_status.setText(f"❌ 获取失败，使用内置汇率（{e}）")
        finally:
            reply.deleteLater()
