# pages/page_timezone_fx.py
# 「时区汇率」页面：
#   无整页滚动；同一张卡片内左右 70% / 30%
#   左 70%：世界时钟——多城市中文模拟时钟，可增删；城市列表写入 user.txt
#            模拟时间：日历选日期 + 时/分/秒上下按钮；含一键还原时间
#   右 30%：汇率转换器——由「比例计算」页迁移而来

import json, os
from datetime import datetime, timedelta, timezone

from styles.style_all import (
    theme, tk, install_card_title, make_card, _card_colors,
    CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP,
    COMBO_RADIUS, _combo_chevron_url,
)
from utils.flow_layout import FlowLayout

from PyQt5.QtCore import Qt, QEvent, QTimer, QUrl, QRectF, QPointF, QPoint, QDate, QTime, QSize
from PyQt5.QtGui import QPainter, QColor, QPen, QBrush, QFont, QFontMetrics, QPalette
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QDateEdit, QCalendarWidget, QGroupBox,
    QScrollArea, QFrame, QSizePolicy, QApplication,
)

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


# ══════════════════════════ 时/分/秒 上下调整控件 ══════════════════════════
class TimeSpinner(QWidget):
    """三列（时/分/秒）上下按钮调整时间，纯鼠标即可操作（参考截图样式）。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._h = self._m = self._s = 0
        self._cb = None            # 用户点击调整后的回调
        self._val = {}

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignHCenter)

        self._btns, self._vals, self._caps = [], [], []
        for key, cap, _mx in (("h", "时", 24), ("m", "分", 60), ("s", "秒", 60)):
            col = QVBoxLayout(); col.setSpacing(3); col.setAlignment(Qt.AlignHCenter)
            up = QPushButton("▲"); dn = QPushButton("▼")
            for b in (up, dn):
                b.setFixedSize(46, 24)
                b.setCursor(Qt.PointingHandCursor)
                self._btns.append(b)
            val = QLabel("00"); val.setAlignment(Qt.AlignCenter); val.setFixedSize(46, 28)
            cap_lbl = QLabel(cap); cap_lbl.setAlignment(Qt.AlignCenter)
            self._vals.append(val); self._caps.append(cap_lbl)
            up.clicked.connect(lambda _=False, k=key, d=1: self._step(k, d))
            dn.clicked.connect(lambda _=False, k=key, d=-1: self._step(k, d))
            col.addWidget(up); col.addWidget(val); col.addWidget(dn); col.addWidget(cap_lbl)
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
        for c in self._caps:
            c.setStyleSheet(f"background:transparent; font-size:11px; color:{tk('text_mut')};")

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
    """纯绘制的模拟表盘：白色圆面（与页面底色区分），方块四角铺卡片底。"""
    def __init__(self, parent=None, diameter=140):
        super().__init__(parent)
        self._h = self._m = self._s = 0
        self._diameter = int(diameter)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background:transparent;border:none;")
        self.setFixedSize(self._diameter, self._diameter)

    def set_diameter(self, diameter: int):
        self._diameter = max(80, int(diameter))
        self.setFixedSize(self._diameter, self._diameter)
        self.update()

    def set_hms(self, h: int, m: int, s: int):
        self._h, self._m, self._s = h, m, s
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w = self.width(); h = self.height()
        try:
            card = QColor(_card_colors()["bg"])
        except Exception:
            card = QColor("#0f1430")
        face = QColor("#ffffff")
        ink = QColor("#111827")
        mut = QColor("#9ca3af")
        sec = QColor("#e11d48")

        # 四角铺卡片底，圆面保持白底（和页面/卡片区分开）
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(card))
        p.drawRect(0, 0, w, h)

        side = min(w, h)
        p.translate(w / 2, h / 2)
        p.scale(side / 200.0, side / 200.0)   # 以 200×200 为逻辑坐标系

        p.setPen(QPen(ink, 5))
        p.setBrush(QBrush(face))
        p.drawEllipse(QRectF(-96, -96, 192, 192))

        # 刻度
        for i in range(60):
            p.save()
            p.rotate(i * 6)
            if i % 5 == 0:
                p.setPen(QPen(ink, 3))
                p.drawLine(QPointF(0, -92), QPointF(0, -82))
            else:
                p.setPen(QPen(mut, 1))
                p.drawLine(QPointF(0, -92), QPointF(0, -87))
            p.restore()

        # 数字 1..12
        p.setPen(ink)
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
        p.setPen(QPen(ink, 6, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(0, 8), QPointF(0, -48)); p.restore()
        # 分针
        p.save(); p.rotate(min_ang)
        p.setPen(QPen(ink, 4, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(0, 10), QPointF(0, -74)); p.restore()
        # 秒针
        p.save(); p.rotate(sec_ang)
        p.setPen(QPen(sec, 2, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(0, 18), QPointF(0, -84)); p.restore()

        # 中心轴
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(ink))
        p.drawEllipse(QRectF(-5, -5, 10, 10))
        p.setBrush(QBrush(sec))
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
_GRID_CLOCK_D = 140
# 最小窗 960 时世界时钟内宽 ≈509；一行 3 个 + 间距 8×2 → 每格最多 ≈164
_GRID_CLOCK_W = 164
# 本页城市下拉高度；5 个按钮再矮 4px
_TZ_CTRL_H = 32
_TZ_BTN_H = _TZ_CTRL_H - 4
# 上方预览钟比网格钟加大 2 号（约两档 16px）
_HERO_CLOCK_D = _GRID_CLOCK_D + 32


class CityClock(QWidget):
    """网格钟：表盘；左下中文名+删除；右下两行（日期周 / 时段时刻）。"""
    def __init__(
        self, city_name: str, flag: str, tz,
        on_remove=None, on_select=None, featured=False, diameter=None,
    ):
        super().__init__()
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background:transparent;border:none;")
        self.city_name = city_name
        self.tz = tz
        self._on_remove = on_remove
        self._on_select = on_select
        self._featured = bool(featured)
        self._selected = False
        self._flag = flag

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(12)
        lay.setAlignment(Qt.AlignHCenter)

        self.header = None
        self.footer = None
        self.footer_date = None
        self.footer_time = None
        self.btn_del = None

        d = int(diameter if diameter is not None else (
            _HERO_CLOCK_D if self._featured else _GRID_CLOCK_D
        ))
        self.clock = AnalogClock(diameter=d)

        if not self._featured:
            self.setCursor(Qt.ArrowCursor)
            self.setFixedWidth(_GRID_CLOCK_W)
            name_row = QHBoxLayout()
            name_row.setContentsMargins(0, 0, 0, 0)
            name_row.setSpacing(4)
            self.header = QLabel(city_name)
            self.header.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            btn_del = self.btn_del = QPushButton("✕")
            btn_del.setFixedSize(22, 22)
            btn_del.setCursor(Qt.PointingHandCursor)
            btn_del.setToolTip(f"删除 {city_name}")
            btn_del.clicked.connect(lambda: self._on_remove and self._on_remove(self))
            name_row.addStretch(1)
            name_row.addWidget(self.header, 0, Qt.AlignVCenter)
            name_row.addWidget(btn_del, 0, Qt.AlignVCenter)
            name_row.addStretch(1)
            lay.addLayout(name_row)

        lay.addWidget(self.clock, 0, Qt.AlignHCenter)

        if not self._featured:
            self.footer_date = QLabel("--")
            self.footer_date.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
            self.footer_date.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            self.footer_time = None
            lay.addWidget(self.footer_date)
            self.footer = self.footer_date

        self.refresh_theme()

    def mousePressEvent(self, e):
        super().mousePressEvent(e)

    def set_selected(self, on: bool):
        on = bool(on)
        if self._selected == on:
            return
        self._selected = on
        self.refresh_theme()

    def set_identity(self, city_name: str, flag: str, tz):
        self.city_name = city_name
        self._flag = flag
        self.tz = tz
        if self.header is not None:
            self.header.setText(city_name)
        if self.btn_del is not None:
            self.btn_del.setToolTip(f"删除 {city_name}")
        self.refresh_theme()

    def refresh_theme(self, *_):
        if self.header is not None:
            if self._selected and not self._featured:
                bd, bg = tk("accent"), tk("sel_bg")
            else:
                bd, bg = tk("border"), tk("panel")
            self.header.setStyleSheet(
                f"background:{bg}; color:{tk('text_strong')}; border:1px solid {bd};"
                "border-radius:7px; padding:2px 6px; font-weight:600; font-size:13px;"
            )
        if self.btn_del is not None:
            self.btn_del.setStyleSheet(
                f"QPushButton{{background:{tk('panel')}; color:{tk('err')};"
                f"border:1px solid {tk('border')};"
                "border-radius:5px; font-size:14px; font-weight:900; padding:0px;}"
                f"QPushButton:hover{{background:{tk('err')}; color:#ffffff;"
                f"border-color:{tk('err')};}}"
            )
        foot_qss = (
            f"background:transparent; color:{tk('text_mut')}; font-size:15px;"
        )
        for lab in (self.footer_date, self.footer_time, self.footer):
            if lab is not None:
                lab.setStyleSheet(foot_qss)
        self.clock.update()          # 表盘是 QPainter 绘制，重绘即可

    def apply_utc(self, now_utc: datetime):
        local = now_utc.astimezone(self.tz)
        self.clock.set_hms(local.hour, local.minute, local.second)
        if self.footer_date is None and self.footer is None:
            return
        wd = _WEEKDAYS[local.weekday()].replace("周", "")
        date_s = f"{local.month}.{local.day} · {wd}"
        time_s = f"{self._period(local.hour)} · {local.strftime('%H:%M')}"
        line = f"{date_s} · {time_s}"
        if self.footer_date is not None:
            self.footer_date.setText(line)
        elif self.footer is not None:
            self.footer.setText(line)

    @staticmethod
    def _period(hour: int) -> str:
        if 5 <= hour < 11:  return "上午"
        if 11 <= hour < 13: return "中午"
        if 13 <= hour < 18: return "下午"
        if 18 <= hour < 22: return "傍晚"
        return "夜晚"


# ══════════════════════════ 页面主体 ══════════════════════════
class PageTimezoneFx(QWidget):
    # ===== 窗口高度 BUG 防护（与截图/速存/游戏辅助页同一套路）=====
    # 本页工具行 + 时钟网格用了 FlowLayout，默认 hasHeightForWidth()=True。
    # Qt 会按当前窗口宽度把「推荐高度」一路抬到主窗口，拖一下就变高且缩不回去。
    # 切断整页 HFW；内部滚动/换行照常，只是不再绑架窗口高度。
    def hasHeightForWidth(self):
        return False

    def minimumSizeHint(self):
        return QSize(0, 0)

    def sizeHint(self):
        sh = super().sizeHint()
        if not sh.isValid() or sh.height() <= 0:
            return QSize(0, 0)
        cap = 700
        if int(sh.height()) > cap:
            return QSize(sh.width(), cap)
        return sh

    def __init__(self):
        super().__init__()

        def _typo(w: QLabel, name: str):
            w.setProperty("typo", name)
            w.style().unpolish(w); w.style().polish(w)
        self._typo = _typo

        # 模拟时间偏移量：0 = 实时；非 0 = 相对真实时间平移
        self._offset = timedelta(0)
        self._cities = []          # list[CityClock]
        self._updating_edit = False

        # 无整页滚动条：一张卡片内左右 70% / 30%
        # 世界时钟区内用标准竖向滚动条上下浏览
        # 与系统总览一致：ContentRoot 已有左右内边距，页面不再叠第二层
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        card = make_card("CardTzPage")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        body = QHBoxLayout(card)
        body.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        body.setSpacing(8)

        left = make_card("CardTzWorldClock", borderless=True)
        left.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 0, 0, 0)
        left_box.setSpacing(10)
        install_card_title(left, left_box, "世界时钟")

        right = make_card("CardTzFx", borderless=True)
        right.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        fx_box = QVBoxLayout(right)
        fx_box.setContentsMargins(0, 0, 0, 0)
        install_card_title(right, fx_box, "汇率转换器（CNY 基准）")

        body.addWidget(left, 7)
        body.addWidget(self._vsep())
        body.addWidget(right, 3)
        root.addWidget(card, 1)

        self._build_world_clock(left_box)
        self._build_fx(fx_box)
        self._apply_tz_ctrl_qss()

        self._prefs_dirty_cb = None
        # 初始城市：user.txt 稍后 apply_settings；此处先读旧 records 文件作一次性迁移
        init_names = self._load_saved_cities() or DEFAULT_CITIES
        for name in init_names:
            self._add_city(name, refresh=False)
        if not self._cities:
            for name in DEFAULT_CITIES:
                self._add_city(name, refresh=False)
        self._relayout_grid()
        self._refresh_base_combo()
        self._sync_hero()

        # 每秒刷新
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()

    # 竖向分隔线（卡片内左右分区）
    def _vsep(self):
        f = QFrame(); f.setFrameShape(QFrame.VLine)
        f.setObjectName("TzVSep")
        f.setStyleSheet(f"QFrame{{color:{tk('border')}; background:{tk('border')}; max-width:1px;}}")
        f.setFixedWidth(1)
        f.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        return f

    def _fit_page_btn(self, btn: QPushButton):
        """高度对齐城市下拉：钉死 32px，去掉全局 4px 垂直 padding。"""
        if btn is None:
            return
        btn.setProperty("tzCtrl", "1")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(_TZ_BTN_H)
        btn.setMinimumHeight(_TZ_BTN_H)
        btn.setMaximumHeight(_TZ_BTN_H)
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def _apply_tz_ctrl_qss(self):
        """只约束本页 5 个按钮的盒模型，配色仍走全局 QPushButton。"""
        h = _TZ_BTN_H
        self.setStyleSheet(
            f'QPushButton[tzCtrl="1"]{{'
            f'min-height:{h}px;max-height:{h}px;padding:0px 14px;}}'
        )

    # ────────────────── 世界时钟区（左 70%，高度占满） ──────────────────
    def _build_world_clock(self, box):
        # —— 第一行：左 50% 基准城市+时钟 ｜ 右 50% 日期+时分秒+还原 ——
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(16)

        left_wrap = QWidget()
        left_wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        left_col = QVBoxLayout(left_wrap)
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(8)
        base_row = QHBoxLayout(); base_row.setSpacing(8)
        lbl_base = QLabel("基准城市："); self._typo(lbl_base, "body")
        self.cb_base = QComboBox(); self.cb_base.setObjectName("TzCityCombo")
        self.cb_base.setFixedHeight(_TZ_CTRL_H); self.cb_base.setMinimumWidth(0)
        self.cb_base.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.cb_base.currentIndexChanged.connect(lambda _=None: self._on_base_changed())
        base_row.addWidget(lbl_base); base_row.addWidget(self.cb_base, 1)
        left_col.addLayout(base_row)
        lbl_kind = QLabel("时钟"); self._typo(lbl_kind, "body")
        left_col.addWidget(lbl_kind)
        flag0, key0, off0 = CITY_PRESETS["上海"]
        self.hero = CityClock(
            "上海", flag0, make_tz(key0, off0),
            featured=True, diameter=_HERO_CLOCK_D,
        )
        self._hero_name = "上海"
        left_col.addWidget(self.hero, 0, Qt.AlignHCenter)
        left_col.addStretch(1)
        row1.addWidget(left_wrap, 1)

        right_wrap = QWidget()
        right_wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        right_col = QVBoxLayout(right_wrap)
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(8)
        right_col.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.lbl_date = QLabel("")
        self.lbl_date.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.lbl_date.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.date_edit = QDateEdit(self)
        self.date_edit.hide()
        self.date_edit.dateChanged.connect(lambda _=None: self._on_datetime_edited())
        self.btn_date = QPushButton("修改日期")
        self.btn_date.setProperty("role", "nav")
        self._fit_page_btn(self.btn_date)
        self.btn_date.clicked.connect(self._open_date_popup)
        self.cal = QCalendarWidget(self)
        self.cal.setWindowFlags(Qt.Popup)
        self.cal.clicked.connect(self._on_cal_picked)
        self._setup_date_calendar()
        self._style_date_label()
        right_col.addWidget(self.lbl_date, 0, Qt.AlignHCenter)
        self.time_spin = TimeSpinner()
        theme.changed.connect(self.refresh_theme)
        self.time_spin.on_user_change(self._on_datetime_edited)
        right_col.addWidget(self.time_spin, 0, Qt.AlignHCenter)
        btn_wrap = QWidget()
        btn_wrap.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        btn_row = QHBoxLayout(btn_wrap)
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(8)
        btn_row.setAlignment(Qt.AlignHCenter)
        self.btn_now = QPushButton("还原时间")
        self.btn_now.setProperty("role", "primary")
        self._fit_page_btn(self.btn_now)
        self.btn_now.clicked.connect(self._restore_now)
        btn_row.addWidget(self.btn_date, 0)
        btn_row.addWidget(self.btn_now, 0)
        right_col.addWidget(btn_wrap, 0, Qt.AlignHCenter)
        right_col.addStretch(1)
        row1.addWidget(right_wrap, 1)
        box.addLayout(row1)

        # —— 白色分隔线（城市区域之上） ——
        wline = self._wline = QFrame(); wline.setFrameShape(QFrame.HLine)
        wline.setFixedHeight(2)
        wline.setObjectName("TzHLine")
        wline.setStyleSheet(
            f"QFrame{{background:{tk('text')}; color:{tk('text')}; border:none; max-height:2px;}}")
        box.addWidget(wline)

        # —— 第二行：添加城市 ——
        row2 = FlowLayout(h_spacing=8, v_spacing=8)
        lbl_add = QLabel("添加城市："); self._typo(lbl_add, "body")
        self.cb_add = QComboBox(); self.cb_add.setObjectName("TzCityCombo")
        self.cb_add.setFixedHeight(_TZ_CTRL_H); self.cb_add.setMinimumWidth(0)
        self.cb_add.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.btn_add = QPushButton("＋ 添加")
        self.btn_add.setProperty("role", "nav")
        self._fit_page_btn(self.btn_add)
        self.btn_add.clicked.connect(self._on_add_clicked)
        row2.addWidget(lbl_add); row2.addWidget(self.cb_add); row2.addWidget(self.btn_add)

        self.btn_reset = QPushButton("↺ 复位")
        self.btn_reset.setProperty("role", "nav")
        self._fit_page_btn(self.btn_reset)
        self.btn_reset.setToolTip("恢复为默认的 8 个城市")
        self.btn_reset.clicked.connect(self._reset_default_cities)
        row2.addWidget(self.btn_reset)
        box.addLayout(row2)

        # —— 时钟网格：右侧标准竖向滚动条，上下浏览 8 城（及更多）——
        self.clock_scroll = QScrollArea()
        self.clock_scroll.setObjectName("TzClockScroll")  # 记录区滚动条标准
        self.clock_scroll.setWidgetResizable(True)
        self.clock_scroll.setFrameShape(QFrame.NoFrame)
        self.clock_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.clock_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.clock_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.clock_scroll.viewport().setAutoFillBackground(False)
        self.clock_scroll.setStyleSheet(
            "QScrollArea#TzClockScroll{background:transparent;border:none;}"
            "QScrollArea#TzClockScroll > QWidget > QWidget{background:transparent;}"
            "QScrollBar:vertical{width:0px;}"
            "QScrollBar:horizontal{height:0px;}"
        )

        # 内容宿主：按视口宽度 heightForWidth，保证 8 城换行后滚动高度正确
        self.grid_host = _ClockGridHost()
        self.grid_host.setObjectName("TzClockGridHost")
        self.grid_host.setAttribute(Qt.WA_StyledBackground, True)
        self.grid_host.setStyleSheet("#TzClockGridHost{background:transparent;border:none;}")
        self.grid = FlowLayout(self.grid_host, h_spacing=8, v_spacing=12)
        self.clock_scroll.setWidget(self.grid_host)
        box.addWidget(self.clock_scroll, 1)

        if not _HAS_ZONEINFO:
            hint = QLabel("⚠ 未检测到系统时区数据库，已使用固定时区偏移（可能不含夏令时）。"
                          "如需精确夏令时，可安装：pip install tzdata")
            hint.setWordWrap(True); self._typo(hint, "muted")
            box.addWidget(hint)

    # ────────────────── 汇率转换器（右 30%，高度占满） ──────────────────
    def _build_fx(self, fx_box):
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
        self._fx_edit_backup = {}
        self._fx_base_code = "CNY"  # 基准币种：基准绿 / 换算蓝

        # 用网格统一三列宽度；每行独立 HBox 会被内容撑开导致「对不齐」
        fx_grid = QGridLayout()
        fx_grid.setHorizontalSpacing(6)
        fx_grid.setVerticalSpacing(6)
        fx_grid.setColumnStretch(0, 3)   # ~0.6
        fx_grid.setColumnStretch(1, 4)   # ~0.8
        fx_grid.setColumnStretch(2, 6)   # ~1.2

        for r, (code, (flag, name, rate, sym)) in enumerate(self._fx_rates.items()):
            cn_name = "".join(ch for ch in name if "一" <= ch <= "鿿")[:3]
            lbl_flag = QLabel(cn_name); self._typo(lbl_flag, "body")
            lbl_flag.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            lbl_flag.setToolTip(name)
            lbl_flag.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            lbl_rate = QLabel(f"≈{rate:.3f}"); self._typo(lbl_rate, "muted")
            lbl_rate.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
            # 等宽数字，右对齐后小数点才能齐
            _rf = QFont("Consolas")
            if not _rf.exactMatch():
                _rf = QFont("Cascadia Mono")
            if not _rf.exactMatch():
                _rf = QFont("Courier New")
            _rf.setStyleHint(QFont.Monospace)
            _rf.setPointSize(max(12, lbl_rate.font().pointSize()))
            lbl_rate.setFont(_rf)
            lbl_rate.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            self._fx_rate_labels[code] = lbl_rate
            inp = QLineEdit()
            init_val = 1.0 / rate
            inp.setText(f"{init_val:.2f}")
            inp.setAlignment(Qt.AlignRight)
            inp.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            # 无外框；底线用独立 1px 线，间距可控（避免 QLineEdit 垂直居中撑出空隙）
            inp.setAutoFillBackground(False)
            fm = inp.fontMetrics()
            inp.setFixedHeight(max(18, fm.height() + 2))
            self._fx_style_amount(inp, code)
            inp.setToolTip(f"可直接输入 {name} 金额，其他货币自动换算")
            inp.textEdited.connect(lambda txt, c=code: self._fx_on_edit(c, txt))
            inp.installEventFilter(self)
            self._fx_inputs[code] = inp

            under = QFrame()
            under.setObjectName("FxAmtUnderline")
            under.setFixedHeight(1)
            under.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            under.setStyleSheet(
                f"QFrame#FxAmtUnderline{{background:{tk('border')}; border:none; max-height:1px;}}"
            )

            cell = QWidget()
            cell_l = QVBoxLayout(cell)
            cell_l.setContentsMargins(0, 0, 0, 0)
            cell_l.setSpacing(1)  # 数字与底线间距 1px
            cell_l.addWidget(inp)
            cell_l.addWidget(under)

            fx_grid.addWidget(lbl_flag, r, 0)
            fx_grid.addWidget(lbl_rate, r, 1)
            fx_grid.addWidget(cell, r, 2)

        fx_box.addLayout(fx_grid)

        btn_refresh_row = QHBoxLayout()
        self.btn_fx_refresh = QPushButton("刷新实时汇率")
        self.btn_fx_refresh.setProperty("role", "nav")
        self._fit_page_btn(self.btn_fx_refresh)
        self.btn_fx_refresh.clicked.connect(self._fx_fetch)
        btn_refresh_row.addStretch(1); btn_refresh_row.addWidget(self.btn_fx_refresh)
        fx_box.addLayout(btn_refresh_row)

        self.fx_status = QLabel("💡 汇率为内置参考值，点击「刷新实时汇率」更新；任意输入框均可直接编辑")
        self.fx_status.setWordWrap(True); self._typo(self.fx_status, "muted")
        fx_box.addWidget(self.fx_status)
        fx_box.addStretch(1)  # 卡片拉满高度时，内容靠上、下方留白

        self._nam = QNetworkAccessManager(self)
        self._nam.finished.connect(self._fx_on_response)

        self._fx_refresh_amount_colors()
        self._fx_install_blank_cancel()

    # ═══════════════ 世界时钟逻辑 ═══════════════
    def _add_city(self, name: str, refresh=True):
        if name not in CITY_PRESETS:
            return
        if any(c.city_name == name for c in self._cities):
            return
        flag, key, off = CITY_PRESETS[name]
        cc = CityClock(
            name, flag, make_tz(key, off),
            on_remove=self._remove_city, on_select=self._select_city,
        )
        self._cities.append(cc)
        if refresh:
            self._relayout_grid()
            self._refresh_base_combo()
            self._tick()
            self._notify_prefs()

    def _remove_city(self, cc: "CityClock"):
        if cc not in self._cities:
            return
        if len(self._cities) <= 1:       # 至少保留 1 个城市
            return
        self._cities.remove(cc)
        cc.setParent(None); cc.deleteLater()
        self._relayout_grid()
        self._refresh_base_combo()
        self._sync_hero()
        self._tick()
        self._notify_prefs()

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
            rec_dir = os.path.join(root, "records")
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

    def _reset_default_cities(self):
        # 恢复默认 8 城；旧 records 文件一并清掉，之后只走 user.txt
        try:
            path = self._cities_file()
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        self._set_cities(list(DEFAULT_CITIES))
        self._notify_prefs()

    def _set_cities(self, names):
        for cc in self._cities:
            cc.setParent(None); cc.deleteLater()
        self._cities = []
        for n in names:
            self._add_city(n, refresh=False)
        self._relayout_grid()
        self._refresh_base_combo()
        self._sync_hero()
        self._tick()

    def _relayout_grid(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        hero = getattr(self, "_hero_name", None)
        for cc in self._cities:
            cc.setParent(self.grid_host)
            # 左上角大钟已是该城，下面不再重复摆一个小时钟
            if cc.city_name == hero:
                cc.hide()
                continue
            self.grid.addWidget(cc)
            cc.show()
        self._refresh_add_combo()

    @staticmethod
    def _city_combo_label(name: str) -> str:
        """下拉只显示中文名（UTC 作「世界时」），不要旗帜/英文简称。"""
        if name == "UTC":
            return "世界时"
        return name

    def _style_city_combo(self, cb: QComboBox):
        """本页城市下拉：收紧箭头区，避免全站 28px 右边距把字裁掉、又空出两字宽。"""
        if cb is None:
            return
        try:
            panel = tk("panel")
            border = tk("border")
            text = tk("text")
            accent = tk("accent")
            faint = tk("text_faint")
            panel_3 = tk("panel_3")
            sel_bg = tk("sel_bg")
            sel_text = tk("sel_text")
        except Exception:
            panel, border, text = "#141b33", "#25345c", "#d7def7"
            accent, faint, panel_3 = "#3a8ee0", "#5a6a8a", "#10162c"
            sel_bg, sel_text = "#1f3a8a", "#93c5fd"
        try:
            chev = _combo_chevron_url()
        except Exception:
            chev = ""
        r = int(COMBO_RADIUS)
        arrow = 16
        cb.setStyleSheet(
            f"QComboBox#TzCityCombo{{"
            f"background-color:{panel};border:1px solid {border};"
            f"border-radius:{r}px;padding:0px {arrow}px 0px 8px;"
            f"min-height:28px;color:{text};font-size:13px;}}"
            f"QComboBox#TzCityCombo:hover,QComboBox#TzCityCombo:on{{"
            f"border-color:{accent};}}"
            f"QComboBox#TzCityCombo:disabled{{color:{faint};"
            f"background-color:{panel_3};}}"
            f"QComboBox#TzCityCombo::drop-down{{"
            f"subcontrol-origin:padding;subcontrol-position:center right;"
            f"width:{arrow}px;border:none;background:transparent;}}"
            f"QComboBox#TzCityCombo::down-arrow{{"
            f"image:url({chev});width:12px;height:8px;}}"
            f"QComboBox#TzCityCombo QAbstractItemView{{"
            f"background:{panel};color:{text};border:1px solid {border};"
            f"selection-background-color:{sel_bg};selection-color:{sel_text};}}"
        )

    def _fit_city_combo(self, cb: QComboBox):
        """按 13px 实测文字宽 + 左 8 + 箭头 16 + 边框，不再用全站 28px 右边距。"""
        if cb is None:
            return
        self._style_city_combo(cb)
        f = QFont(cb.font())
        f.setPixelSize(13)
        fm = QFontMetrics(f)
        tw = fm.horizontalAdvance("上海")
        for i in range(cb.count()):
            tw = max(tw, fm.horizontalAdvance(cb.itemText(i)))
        cb.setFixedWidth(tw + 8 + 16 + 8 + 20)

    def _refresh_add_combo(self):
        current = {c.city_name for c in self._cities}
        self.cb_add.blockSignals(True)
        self.cb_add.clear()
        for name in CITY_PRESETS:
            if name not in current:
                self.cb_add.addItem(self._city_combo_label(name), name)
        self.cb_add.blockSignals(False)
        self._fit_city_combo(self.cb_add)

    def _refresh_base_combo(self):
        prev = self.cb_base.currentData() if self.cb_base.count() else None
        self.cb_base.blockSignals(True)
        self.cb_base.clear()
        for cc in self._cities:
            self.cb_base.addItem(self._city_combo_label(cc.city_name), cc.city_name)
        if prev is not None:
            i = self.cb_base.findData(prev)
            if i >= 0:
                self.cb_base.setCurrentIndex(i)
        else:
            i = self.cb_base.findData("上海")      # 默认基准城市：上海
            self.cb_base.setCurrentIndex(i if i >= 0 else 0)
        self.cb_base.blockSignals(False)
        self._fit_city_combo(self.cb_base)
        self._sync_base_edit()

    def _on_base_changed(self):
        self._sync_base_edit()
        name = self.cb_base.currentData()
        cc = next((c for c in self._cities if c.city_name == name), None)
        if cc is not None:
            self._select_city(cc)

    def _select_city(self, cc: "CityClock", persist: bool = True):
        """点选小时钟 / 改基准城市 → 左上角大钟换成该城，网格里不再重复。"""
        if cc is None or getattr(cc, "_featured", False):
            return
        self._hero_name = cc.city_name
        flag = CITY_PRESETS.get(cc.city_name, ("", "", 0))[0]
        self.hero.set_identity(cc.city_name, flag, cc.tz)
        self.hero.apply_utc(self._display_utc())
        for c in self._cities:
            c.set_selected(c.city_name == cc.city_name)
        if self.cb_base.currentData() != cc.city_name:
            i = self.cb_base.findData(cc.city_name)
            if i >= 0:
                self.cb_base.blockSignals(True)
                self.cb_base.setCurrentIndex(i)
                self.cb_base.blockSignals(False)
                self._sync_base_edit()
        self._relayout_grid()
        if persist:
            self._notify_prefs()

    def set_prefs_dirty_callback(self, cb):
        self._prefs_dirty_cb = cb

    def _notify_prefs(self):
        fn = getattr(self, "_prefs_dirty_cb", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

    def export_settings(self) -> dict:
        cities = [c.city_name for c in self._cities if c.city_name in CITY_PRESETS]
        hero = getattr(self, "_hero_name", "") or ""
        if hero not in cities and cities:
            hero = cities[0]
        return {
            "cities": cities,
            "hero_city": hero,
        }

    def apply_settings(self, d: dict):
        if not isinstance(d, dict):
            return
        raw = d.get("cities")
        names = []
        if isinstance(raw, list):
            for n in raw:
                n = str(n or "").strip()
                if n in CITY_PRESETS and n not in names:
                    names.append(n)
        if names:
            self._set_cities(names)
        hero = str(d.get("hero_city") or "").strip()
        if hero in CITY_PRESETS:
            self._hero_name = hero
            self._sync_hero()

    def reload_cities_from_disk(self):
        """城市已进 user.txt，不再读 records/world_clock_cities.txt。"""
        return

    def _sync_hero(self):
        if not self._cities or not hasattr(self, "hero"):
            return
        name = getattr(self, "_hero_name", None)
        cc = next((c for c in self._cities if c.city_name == name), self._cities[0])
        self._select_city(cc, persist=False)

    def _setup_date_calendar(self):
        """网格月历弹层：无周序号、周一开头、短星期名。"""
        cal = getattr(self, "cal", None)
        if cal is None:
            return
        cal.setGridVisible(True)
        cal.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        cal.setHorizontalHeaderFormat(QCalendarWidget.ShortDayNames)
        cal.setFirstDayOfWeek(Qt.Monday)
        self._style_date_calendar()

    def _style_date_calendar(self):
        w = getattr(self, "cal", None)
        if w is None:
            return
        w.setStyleSheet(
            f"QCalendarWidget{{background:{tk('panel')};color:{tk('text')};}}"
            "QCalendarWidget QWidget#qt_calendar_navigationbar{"
            f"background:{tk('accent')};color:#ffffff;}}"
            "QCalendarWidget QToolButton{color:#ffffff;background:transparent;"
            "font-weight:600;}"
            "QCalendarWidget QToolButton:hover{"
            f"background:{tk('accent')};}}"
            "QCalendarWidget QAbstractItemView:enabled{"
            f"background:{tk('panel')};color:{tk('text')};"
            f"selection-background-color:{tk('accent')};selection-color:#ffffff;"
            "font-size:13px;}"
            "QCalendarWidget QAbstractItemView:disabled{color:rgba(127,127,127,0.55);}"
        )

    def _style_date_label(self):
        lbl = getattr(self, "lbl_date", None)
        if lbl is None:
            return
        try:
            color = tk("text")
        except Exception:
            color = "#d7def7"
        # body 约 13px，两次各加 4 → 21px，撑满时分秒上方一行
        lbl.setStyleSheet(
            f"background:transparent;color:{color};"
            "font-size:21px;font-weight:600;"
        )

    def _format_date_label(self, d: QDate) -> str:
        wd = _WEEKDAYS[(d.dayOfWeek() - 1) % 7]
        return f"{d.year()}年{d.month()}月{d.day()}日 {wd}"

    def _refresh_date_label(self):
        lbl = getattr(self, "lbl_date", None)
        de = getattr(self, "date_edit", None)
        if lbl is None or de is None:
            return
        lbl.setText(self._format_date_label(de.date()))

    def _open_date_popup(self):
        cal = getattr(self, "cal", None)
        btn = getattr(self, "btn_date", None)
        if cal is None or btn is None:
            return
        cal.setSelectedDate(self.date_edit.date())
        self._style_date_calendar()
        pos = btn.mapToGlobal(QPoint(0, btn.height()))
        cal.move(pos)
        cal.show()
        cal.raise_()

    def _on_cal_picked(self, qdate: QDate):
        cal = getattr(self, "cal", None)
        if cal is not None:
            cal.hide()
        if qdate is None or not qdate.isValid():
            return
        self.date_edit.setDate(qdate)
        self._refresh_date_label()

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
            cal = getattr(self, "cal", None)
            return cal is not None and cal.isVisible()
        except Exception:
            return False

    def _sync_base_edit(self):
        """把当前（含偏移）时间按基准城市显示到日期/时分秒控件（不改变偏移）。"""
        if self._calendar_open():
            return
        local = self._display_utc().astimezone(self._base_tz())
        self._updating_edit = True
        self.date_edit.blockSignals(True)
        self.date_edit.setDate(QDate(local.year, local.month, local.day))
        self.date_edit.blockSignals(False)
        self._refresh_date_label()
        self.time_spin.set_time(local.hour, local.minute, local.second)
        self._updating_edit = False

    def _on_datetime_edited(self):
        """用户改了日期或时分秒 → 反推偏移量。"""
        if self._updating_edit:
            return
        self._refresh_date_label()
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
        if hasattr(self, "hero"):
            self.hero.apply_utc(now_utc)
        self._sync_base_edit()

    def refresh_theme(self, *_):
        """重刷本页控件级样式（QSS 选择器覆盖不到的部分）。"""
        self.time_spin.refresh_theme()
        self._style_date_label()
        self._style_date_calendar()
        self._apply_tz_ctrl_qss()
        for b in (
            getattr(self, "btn_date", None),
            getattr(self, "btn_now", None),
            getattr(self, "btn_add", None),
            getattr(self, "btn_reset", None),
            getattr(self, "btn_fx_refresh", None),
        ):
            self._fit_page_btn(b)
        for cb in (getattr(self, "cb_add", None), getattr(self, "cb_base", None)):
            self._fit_city_combo(cb)
        for cc in self.findChildren(CityClock):
            cc.refresh_theme()
        for f in self.findChildren(QFrame):
            if f.objectName() == "TzVSep":
                f.setStyleSheet(
                    f"QFrame{{color:{tk('border')}; background:{tk('border')}; max-width:1px;}}")
            elif f.objectName() == "TzHLine":
                f.setStyleSheet(
                    f"QFrame{{background:{tk('text')}; color:{tk('text')}; "
                    "border:none; max-height:2px;}")

    def on_enter(self):
        self._sync_base_edit()
        self._tick()

    # ═══════════════ 汇率转换逻辑（与原比例计算页一致） ═══════════════

    def _fx_install_blank_cancel(self):
        """空白处点击也能取消：给页面上非金额框的控件装事件过滤。"""
        self.setFocusPolicy(Qt.ClickFocus)
        self.installEventFilter(self)
        for w in self.findChildren(QWidget):
            if w in self._fx_inputs.values():
                continue
            w.installEventFilter(self)

    def eventFilter(self, obj, event):
        # 1) 点金额框以外的任意处 → 强制取消当前编辑并清焦点
        if (
            event.type() == QEvent.MouseButtonPress
            and getattr(event, "button", lambda: None)() == Qt.LeftButton
            and obj not in getattr(self, "_fx_inputs", {}).values()
        ):
            fw = QApplication.focusWidget()
            if fw in getattr(self, "_fx_inputs", {}).values():
                code = next(c for c, w in self._fx_inputs.items() if w is fw)
                self._fx_cancel_edit(code)
                fw.clearFocus()
                self.setFocus(Qt.MouseFocusReason)

        # 2) 金额框焦点：点入清空；正常失焦时有数字则确认，否则取消
        if obj in getattr(self, "_fx_inputs", {}).values():
            code = next(c for c, w in self._fx_inputs.items() if w is obj)
            if event.type() == QEvent.FocusIn:
                self._fx_on_focus_in(code)
            elif event.type() == QEvent.FocusOut:
                self._fx_on_focus_out(code)
        return super().eventFilter(obj, event)

    def _fx_on_focus_in(self, code: str):
        inp = self._fx_inputs.get(code)
        if inp is None:
            return
        # 若已有备份说明仍在编辑中，不要再次清空
        if code not in self._fx_edit_backup:
            self._fx_edit_backup[code] = inp.text()
            inp.blockSignals(True)
            inp.clear()
            inp.blockSignals(False)

    def _fx_cancel_edit(self, code: str):
        """取消输入：恢复点入前的数值，并回算其它币种。"""
        inp = self._fx_inputs.get(code)
        if inp is None:
            return
        backup = self._fx_edit_backup.pop(code, None)
        if backup is None:
            return
        inp.blockSignals(True)
        inp.setText(backup)
        inp.blockSignals(False)
        self._fx_on_edit(code, backup, set_base=False)

    def _fx_on_focus_out(self, code: str):
        inp = self._fx_inputs.get(code)
        if inp is None:
            return
        # 已被空白点击取消过
        if code not in self._fx_edit_backup:
            return
        backup = self._fx_edit_backup.pop(code, None)
        txt = inp.text().strip()
        try:
            if not txt:
                raise ValueError("empty")
            val = float(txt)
        except ValueError:
            inp.blockSignals(True)
            inp.setText(backup if backup is not None else "")
            inp.blockSignals(False)
            if backup is not None:
                self._fx_on_edit(code, backup, set_base=False)
            return
        formatted = self._fmt_fx(val, code)
        if formatted != inp.text():
            inp.blockSignals(True)
            inp.setText(formatted)
            inp.blockSignals(False)
        self._fx_on_edit(code, formatted)

    def _fx_amount_color(self, code: str) -> str:
        # 基准绿 / 换算蓝（深色背景上可读）
        if code == getattr(self, "_fx_base_code", "CNY"):
            return "#3dd68c"
        return "#4ea1ff"

    def _fx_style_amount(self, inp, code: str):
        """金额框无「输入底」：跟卡片 panel 同色，盖掉全局 QLineEdit 的 input_bg。"""
        color = self._fx_amount_color(code)
        panel = tk("panel")
        inp.setObjectName(f"FxAmtEdit_{code}")
        inp.setProperty("fxAmount", True)
        inp.setAutoFillBackground(False)
        inp.setAttribute(Qt.WA_StyledBackground, True)
        pal = inp.palette()
        pal.setColor(QPalette.Base, QColor(panel))
        pal.setColor(QPalette.Window, QColor(panel))
        pal.setColor(QPalette.Text, QColor(color))
        pal.setColor(QPalette.HighlightedText, QColor(color))
        inp.setPalette(pal)
        # 跟主界面一致的黑体数字，不用 Consolas（在金额框里发虚/发硬）
        fnt = QFont("Microsoft YaHei UI")
        if not fnt.exactMatch():
            fnt = QFont("Microsoft YaHei")
        if not fnt.exactMatch():
            fnt = QFont("Segoe UI")
        fnt.setPixelSize(15)
        fnt.setWeight(QFont.DemiBold)
        try:
            fnt.setStyleStrategy(QFont.PreferOutline | QFont.PreferQuality)
        except Exception:
            pass
        inp.setFont(fnt)

        ss = (
            "QLineEdit[fxAmount=\"true\"], QLineEdit[fxAmount=\"true\"]:hover, "
            "QLineEdit[fxAmount=\"true\"]:focus {"
            "  font-size: 15px;"
            "  font-weight: 600;"
            "  font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI';"
            f"  color: {color};"
            "  border: none;"
            "  border-radius: 0;"
            "  outline: none;"
            f"  background: {panel};"
            f"  background-color: {panel};"
            "  padding: 0px 2px 0px 2px;"
            "  margin: 0;"
            "  min-height: 20px;"
            "  max-height: 22px;"
            "  qproperty-alignment: AlignRight;"
            f"  selection-background-color: {panel};"
            f"  selection-color: {color};"
            "}"
        )
        inp.setStyleSheet(ss)
        st = inp.style()
        if st is not None:
            st.unpolish(inp)
            st.polish(inp)
        fm = inp.fontMetrics()
        inp.setFixedHeight(max(20, fm.height() + 4))

    def _fx_refresh_amount_colors(self):
        for code, inp in getattr(self, "_fx_inputs", {}).items():
            self._fx_style_amount(inp, code)

    def _fx_on_edit(self, src_code: str, txt: str, set_base: bool = True):
        if self._fx_updating:
            return
        txt = txt.strip()
        if not txt:
            return
        try:
            val = float(txt)
        except ValueError:
            return
        # 谁在输入，谁就是基准（默认 CNY）；取消恢复时不改基准
        if set_base:
            self._fx_base_code = src_code
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
        self._fx_refresh_amount_colors()

    def _fmt_fx(self, val: float, code: str) -> str:
        # 金额统一保留 2 位小数
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
