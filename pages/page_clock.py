# pages/page_clock.py
# 「电子钟」屏保页：七段数码时分（24h）+ 月历 + 天气卡。
# 天气先做界面示意，实况接口稍后接。

from __future__ import annotations

import calendar
import math
from datetime import date, datetime

from PyQt5.QtCore import Qt, QRectF, QPointF, QTimer
from PyQt5.QtGui import QPainter, QColor, QPen, QBrush, QPainterPath
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QGridLayout,
    QSizePolicy, QButtonGroup,
)

from styles.style_all import (
    theme, tk, make_card, restyle_card_frame, install_card_title,
    restyle_card_title, CARD_LEFT_GAP, CARD_RIGHT_GAP, CARD_TOP_GAP,
    CARD_BOTTOM_GAP, CARD_TITLE_BODY_GAP,
)
from pages.page_paste import CARD_COLORS
from pages.settings_section import GuidePageDot

_WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")

# 七段：a 顶 / b 右上 / c 右下 / d 底 / e 左下 / f 左上 / g 中
_SEG_MAP = {
    "0": "abcdef",
    "1": "bc",
    "2": "abged",
    "3": "abgcd",
    "4": "fgbc",
    "5": "afgcd",
    "6": "afgecd",
    "7": "abc",
    "8": "abcdefg",
    "9": "abfgcd",
}


# 与粘贴助手同一套七色；默认黄，接近原先暗色琥珀 LED
_DIGIT_COLOR_SET = {hex_c.lower() for hex_c, _name in CARD_COLORS}
DEFAULT_DIGIT_COLOR = CARD_COLORS[2][0]  # 黄 #eab308


def _normalize_digit_color(hex_color: str) -> str:
    c = (hex_color or "").strip().lower()
    if c in _DIGIT_COLOR_SET:
        for hex_c, _name in CARD_COLORS:
            if hex_c.lower() == c:
                return hex_c
    return DEFAULT_DIGIT_COLOR


def _led_colors(hex_color: str):
    """按所选色画七段：亮段实色，暗段同色极淡，暗主题带一点光晕。"""
    c = QColor(_normalize_digit_color(hex_color))
    if not c.isValid():
        c = QColor(DEFAULT_DIGIT_COLOR)
    r, g, b = c.red(), c.green(), c.blue()
    on = QColor(r, g, b)
    if theme.is_dark:
        return {
            "on": on,
            "off": QColor(r, g, b, 18),
            "glow": QColor(r, g, b, 70),
            "colon": on,
        }
    return {
        "on": on,
        "off": QColor(r, g, b, 28),
        "glow": QColor(r, g, b, 0),
        "colon": on,
    }


def _h_seg(x, y, w, t) -> QPainterPath:
    d = t * 0.42
    path = QPainterPath()
    path.moveTo(x + d, y)
    path.lineTo(x + w - d, y)
    path.lineTo(x + w, y + t * 0.5)
    path.lineTo(x + w - d, y + t)
    path.lineTo(x + d, y + t)
    path.lineTo(x, y + t * 0.5)
    path.closeSubpath()
    return path


def _v_seg(x, y, h, t) -> QPainterPath:
    d = t * 0.42
    path = QPainterPath()
    path.moveTo(x + t * 0.5, y)
    path.lineTo(x + t, y + d)
    path.lineTo(x + t, y + h - d)
    path.lineTo(x + t * 0.5, y + h)
    path.lineTo(x, y + h - d)
    path.lineTo(x, y + d)
    path.closeSubpath()
    return path


def _digit_paths(x, y, w, h) -> dict:
    t = max(6.0, min(w, h) * 0.16)
    gap = t * 0.18
    inner_w = w - t
    half = (h - t) * 0.5
    return {
        "a": _h_seg(x + t * 0.5 + gap, y, inner_w - gap * 2, t),
        "b": _v_seg(x + w - t, y + t * 0.5 + gap, half - gap * 2, t),
        "c": _v_seg(x + w - t, y + half + t * 0.5 + gap, half - gap * 2, t),
        "d": _h_seg(x + t * 0.5 + gap, y + h - t, inner_w - gap * 2, t),
        "e": _v_seg(x, y + half + t * 0.5 + gap, half - gap * 2, t),
        "f": _v_seg(x, y + t * 0.5 + gap, half - gap * 2, t),
        "g": _h_seg(x + t * 0.5 + gap, y + half - t * 0.5 + 15.0, inner_w - gap * 2, t),
    }


class SevenSegClock(QWidget):
    """只画时:分，冒号随秒闪。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hh = "00"
        self._mm = "00"
        self._colon_on = True
        self._color_hex = DEFAULT_DIGIT_COLOR
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_digit_color(self, hex_color: str):
        color = _normalize_digit_color(hex_color)
        if color == self._color_hex:
            return
        self._color_hex = color
        self.update()

    def digit_color(self) -> str:
        return self._color_hex

    def set_time(self, hh: str, mm: str, colon_on: bool):
        if hh == self._hh and mm == self._mm and colon_on == self._colon_on:
            return
        self._hh, self._mm, self._colon_on = hh, mm, colon_on
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        if w < 40 or h < 40:
            p.end()
            return

        # 1-2 / 3-4 各 26px；2 与 3 中间（冒号两侧合计）比原先少 20px
        gap_pair = 26.0
        gap_mid = 6.0
        side = 4.0
        colon_ratio = 0.32
        gaps_w = gap_pair * 2 + gap_mid * 2
        avail_w = max(1.0, w - side * 2)
        digit_w = (avail_w - gaps_w) / (4.0 + colon_ratio)
        digit_w = max(8.0, digit_w)
        digit_h = digit_w / 0.58
        max_h = max(20.0, h * 0.96)
        if digit_h > max_h:
            s = max_h / digit_h
            digit_w *= s
            digit_h *= s
        colon_w = digit_w * colon_ratio
        total = digit_w * 4 + colon_w + gaps_w
        x0 = (w - total) * 0.5
        y0 = (h - digit_h) * 0.5

        colors = _led_colors(self._color_hex)
        x = x0
        for i, ch in enumerate(list(self._hh)):
            if i:
                x += gap_pair
            self._paint_digit(p, ch, x, y0, digit_w, digit_h, colors)
            x += digit_w
        x += gap_mid
        self._paint_colon(p, x, y0, colon_w, digit_h, colors)
        x += colon_w + gap_mid
        for i, ch in enumerate(list(self._mm)):
            if i:
                x += gap_pair
            self._paint_digit(p, ch, x, y0, digit_w, digit_h, colors)
            x += digit_w
        p.end()

    def _paint_digit(self, p, ch, x, y, w, h, colors):
        segs = _digit_paths(x, y, w, h)
        on = set(_SEG_MAP.get(ch, ""))
        p.setPen(Qt.NoPen)
        if colors["glow"].alpha() > 0:
            p.setBrush(QBrush(colors["glow"]))
            for name, path in segs.items():
                if name in on:
                    p.drawPath(path)
        for name, path in segs.items():
            p.setBrush(QBrush(colors["on"] if name in on else colors["off"]))
            p.drawPath(path)

    def _paint_colon(self, p, x, y, w, h, colors):
        r = max(3.0, min(w, h) * 0.09)
        cx = x + w * 0.5
        p.setPen(Qt.NoPen)
        c = QColor(colors["colon"])
        if not self._colon_on:
            c.setAlpha(40)
        p.setBrush(QBrush(c))
        p.drawEllipse(QPointF(cx, y + h * 0.32), r, r)
        p.drawEllipse(QPointF(cx, y + h * 0.68), r, r)


class _DayCell(QLabel):
    """日期格：今天在格子里画缩小 30% 的青色正圆，数字仍居中。"""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._today_bg = QColor("#22D3EE")

    def paintEvent(self, event):
        if str(self.property("dayKind") or "") == "today":
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing, True)
            w, h = float(self.width()), float(self.height())
            # 原高亮铺满格子；改成正圆后再缩小 30%
            d = min(w, h) * 0.70
            if d > 1:
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(self._today_bg))
                p.drawEllipse(QRectF((w - d) * 0.5, (h - d) * 0.5, d, d))
            p.end()
        super().paintEvent(event)


class MonthCalendar(QWidget):
    """当月月历：周一为一周起始，今天用实心圆标出。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._year = 0
        self._month = 0
        self._cells = []
        self._head_lbls = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.lbl_month = QLabel()
        self.lbl_month.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        root.addWidget(self.lbl_month, 0)
        root.addSpacing(15)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(1)
        grid.setVerticalSpacing(1)
        for i, name in enumerate(_WEEKDAYS):
            lab = QLabel(name)
            lab.setAlignment(Qt.AlignCenter)
            lab.setFixedHeight(18)
            self._head_lbls.append(lab)
            grid.addWidget(lab, 0, i)
        for r in range(6):
            row = []
            for c in range(7):
                cell = _DayCell("")
                cell.setAlignment(Qt.AlignCenter)
                cell.setMinimumSize(18, 18)
                cell.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                grid.addWidget(cell, r + 1, c)
                row.append(cell)
            self._cells.append(row)
        root.addLayout(grid, 1)
        self.set_month(date.today())

    def set_month(self, d: date):
        if d.year == self._year and d.month == self._month:
            self._paint_today(d)
            return
        self._year, self._month = d.year, d.month
        self.lbl_month.setText(f"{d.year} 年 {d.month} 月")
        cal = calendar.Calendar(firstweekday=0)  # 周一
        weeks = cal.monthdayscalendar(d.year, d.month)
        today = date.today()
        for r in range(6):
            days = weeks[r] if r < len(weeks) else [0] * 7
            for c in range(7):
                n = days[c] if c < len(days) else 0
                cell = self._cells[r][c]
                if n <= 0:
                    cell.setText("")
                    cell.setProperty("dayKind", "empty")
                else:
                    cell.setText(str(n))
                    is_today = (d.year == today.year and d.month == today.month and n == today.day)
                    cell.setProperty("dayKind", "today" if is_today else ("weekend" if c >= 5 else "day"))
                cell.style().unpolish(cell)
                cell.style().polish(cell)
        self.refresh_theme()

    def _paint_today(self, d: date):
        today = date.today()
        changed = False
        for r in range(6):
            for c in range(7):
                cell = self._cells[r][c]
                txt = cell.text()
                if not txt:
                    continue
                n = int(txt)
                is_today = (d.year == today.year and d.month == today.month and n == today.day)
                kind = "today" if is_today else ("weekend" if c >= 5 else "day")
                if str(cell.property("dayKind") or "") != kind:
                    cell.setProperty("dayKind", kind)
                    changed = True
        if changed:
            self.refresh_theme()

    def refresh_theme(self):
        title = tk("text_strong")
        mut = tk("text_mut")
        dim = tk("text_dim")
        text = tk("text")
        accent = "#22D3EE" if theme.is_dark else "#06B6D4"
        on_accent = "#083344" if theme.is_dark else "#ffffff"
        weekend = "#22D3EE" if theme.is_dark else "#0891B2"
        self.lbl_month.setStyleSheet(
            f"color:{title}; font-size:20px; font-weight:800; background:transparent; border:none;"
        )
        for lab in self._head_lbls:
            lab.setStyleSheet(
                f"color:{mut}; font-size:15px; font-weight:700; background:transparent; border:none;"
            )
        for r in range(6):
            for c in range(7):
                cell = self._cells[r][c]
                kind = str(cell.property("dayKind") or "empty")
                if kind == "today":
                    cell._today_bg = QColor(accent)
                    ss = (
                        f"color:{on_accent}; background:transparent; border:none;"
                        "font-size:16px; font-weight:800;"
                    )
                elif kind == "weekend":
                    ss = (
                        f"color:{weekend}; background:transparent; border:none;"
                        "font-size:16px; font-weight:600;"
                    )
                elif kind == "day":
                    ss = (
                        f"color:{text}; background:transparent; border:none;"
                        "font-size:16px; font-weight:600;"
                    )
                else:
                    ss = f"color:{dim}; background:transparent; border:none; font-size:16px;"
                cell.setStyleSheet(ss)


class WeatherGlyph(QWidget):
    """手绘天气符号：晴 / 多云 / 阴 / 雨。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.kind = "cloud"
        self.setFixedSize(88, 88)

    def set_kind(self, kind: str):
        self.kind = kind or "cloud"
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        s = float(min(self.width(), self.height()))
        dark = theme.is_dark
        sun = QColor("#F5B942" if dark else "#F59E0B")
        cloud = QColor("#E2E8F0" if dark else "#94A3B8")
        rain = QColor("#7DD3FC" if dark else "#0EA5E9")
        kind = self.kind
        if kind in ("sun", "clear"):
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(sun))
            p.drawEllipse(QRectF(s * 0.28, s * 0.28, s * 0.44, s * 0.44))
            pen = QPen(sun, max(2.0, s * 0.05))
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            c = QPointF(s * 0.50, s * 0.50)
            for i in range(8):
                a = i * math.pi / 4
                p.drawLine(
                    QPointF(c.x() + math.cos(a) * s * 0.32, c.y() + math.sin(a) * s * 0.32),
                    QPointF(c.x() + math.cos(a) * s * 0.44, c.y() + math.sin(a) * s * 0.44),
                )
        else:
            if kind in ("partly", "cloud", "overcast"):
                if kind != "overcast":
                    p.setPen(Qt.NoPen)
                    p.setBrush(QBrush(sun))
                    p.drawEllipse(QRectF(s * 0.42, s * 0.14, s * 0.36, s * 0.36))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(cloud))
            p.drawEllipse(QRectF(s * 0.14, s * 0.38, s * 0.32, s * 0.28))
            p.drawEllipse(QRectF(s * 0.30, s * 0.30, s * 0.38, s * 0.32))
            p.drawEllipse(QRectF(s * 0.52, s * 0.38, s * 0.32, s * 0.28))
            p.drawRoundedRect(QRectF(s * 0.16, s * 0.48, s * 0.68, s * 0.22), 8, 8)
            if kind == "rain":
                pen = QPen(rain, max(2.0, s * 0.045))
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                for i, xx in enumerate((0.28, 0.46, 0.64)):
                    y0 = s * (0.76 if i != 1 else 0.80)
                    p.drawLine(QPointF(s * xx, y0), QPointF(s * xx - s * 0.04, y0 + s * 0.14))
        p.end()


class WeatherCard(QWidget):
    """天气卡：城市、温度、状况、湿度/风/体感、三日示意。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)
        self.lbl_city = QLabel("上海")
        self.lbl_badge = QLabel("多云")
        self.lbl_badge.setAlignment(Qt.AlignCenter)
        top.addWidget(self.lbl_city, 0, Qt.AlignVCenter)
        top.addWidget(self.lbl_badge, 0, Qt.AlignVCenter)
        top.addStretch(1)
        root.addLayout(top)

        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(14)
        self.glyph = WeatherGlyph()
        mid.addWidget(self.glyph, 0, Qt.AlignVCenter)
        self.lbl_temp = QLabel("26°")
        mid.addWidget(self.lbl_temp, 1, Qt.AlignVCenter)
        root.addLayout(mid)

        meta = QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(10)
        self.chip_hum = QLabel("湿度  62%")
        self.chip_wind = QLabel("东北风  2 级")
        self.chip_feel = QLabel("体感  28°")
        self._chips = [self.chip_hum, self.chip_wind, self.chip_feel]
        for chip in self._chips:
            chip.setAlignment(Qt.AlignCenter)
            chip.setMinimumHeight(36)
            meta.addWidget(chip, 1)
        root.addLayout(meta)

        self.forecast_row = QHBoxLayout()
        self.forecast_row.setContentsMargins(0, 0, 0, 0)
        self.forecast_row.setSpacing(10)
        self._forecast = []
        for _ in range(3):
            box = QFrame()
            box.setObjectName("ClockForecastCell")
            lay = QVBoxLayout(box)
            lay.setContentsMargins(10, 10, 10, 10)
            lay.setSpacing(4)
            d = QLabel()
            t = QLabel()
            d.setAlignment(Qt.AlignCenter)
            t.setAlignment(Qt.AlignCenter)
            lay.addWidget(d)
            lay.addWidget(t)
            self.forecast_row.addWidget(box, 1)
            self._forecast.append((box, d, t))
        root.addLayout(self.forecast_row)
        root.addStretch(1)

        self.set_preview()

    def set_preview(self):
        """界面预览数据，实况接入前占位。"""
        self.lbl_city.setText("上海")
        self.lbl_temp.setText("26°")
        self.lbl_badge.setText("多云")
        self.glyph.set_kind("cloud")
        self.chip_hum.setText("湿度  62%")
        self.chip_wind.setText("东北风  2 级")
        self.chip_feel.setText("体感  28°")
        rows = (("今天", "28° / 22°"), ("明天", "30° / 23°"), ("后天", "27° / 21°"))
        for (box, d, t), (name, rng) in zip(self._forecast, rows):
            d.setText(name)
            t.setText(rng)

    def refresh_theme(self):
        strong = tk("text_strong")
        mut = tk("text_mut")
        panel = tk("panel_2")
        border = tk("border")
        self.lbl_city.setStyleSheet(
            f"color:{strong}; font-size:28px; font-weight:800; background:transparent; border:none;"
        )
        badge_bg = "rgba(245,185,66,0.16)" if theme.is_dark else "#EEF2FF"
        badge_fg = "#F5B942" if theme.is_dark else "#4F46E5"
        self.lbl_badge.setStyleSheet(
            f"color:{badge_fg}; background:{badge_bg}; border:none;"
            "border-radius:8px; padding:0 12px; font-size:28px; font-weight:800;"
        )
        self.lbl_temp.setStyleSheet(
            f"color:{strong}; font-size:64px; font-weight:800; background:transparent; border:none;"
        )
        chip_ss = (
            f"color:{tk('text')}; background:{panel}; border:1px solid {border};"
            "border-radius:8px; padding:6px 8px; font-size:15px; font-weight:600;"
        )
        for chip in self._chips:
            chip.setStyleSheet(chip_ss)
        for box, d, t in self._forecast:
            box.setStyleSheet(
                f"QFrame#ClockForecastCell{{background:{panel}; border:1px solid {border};"
                "border-radius:10px;}"
            )
            d.setStyleSheet(f"color:{mut}; font-size:15px; font-weight:600; background:transparent; border:none;")
            t.setStyleSheet(f"color:{strong}; font-size:17px; font-weight:700; background:transparent; border:none;")
        self.glyph.update()


class _ClockColorBar(QWidget):
    """与系统总览「功能与说明」相同的翻页圆点：未选 8px / 选中 12px。"""

    def __init__(self, on_change=None, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")
        self._on_change = on_change
        self._color = DEFAULT_DIGIT_COLOR
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        lay = QHBoxLayout(self)
        # 与总览功能说明圆点行一致：上 4px、间距约 1px、两侧 stretch 居中
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(max(1, int(round(2 * 0.70))))
        lay.addStretch(1)
        for i, (hex_c, name) in enumerate(CARD_COLORS):
            btn = GuidePageDot(hex_c)
            btn.setObjectName("ClockColorDot")
            btn.setProperty("swatch", hex_c)
            btn.setToolTip(name)
            self._group.addButton(btn, i)
            lay.addWidget(btn, 0, Qt.AlignVCenter)
        lay.addStretch(1)
        self._group.buttonClicked.connect(self._on_clicked)
        self.set_color(DEFAULT_DIGIT_COLOR)

    def _on_clicked(self, btn):
        self._color = btn.property("swatch") or DEFAULT_DIGIT_COLOR
        for b in self._group.buttons():
            b.update()
        if callable(self._on_change):
            self._on_change(self._color)

    def color(self) -> str:
        return self._color or DEFAULT_DIGIT_COLOR

    def set_color(self, color: str):
        color = _normalize_digit_color(color)
        self._color = color
        for b in self._group.buttons():
            hex_c = (b.property("swatch") or "").lower()
            b.setChecked(hex_c == color.lower())
            b.update()

    def restyle_theme(self):
        for b in self._group.buttons():
            b.update()


class PageClock(QWidget):
    """电子钟主页面。"""

    def __init__(self):
        super().__init__()
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._last_date = None
        self._prefs_dirty_cb = None

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 8)
        root.setSpacing(10)

        # 上：大钟（四位数字在钟面区域高度居中）
        self.clock = SevenSegClock()
        self.clock.setMinimumHeight(200)
        root.addWidget(self.clock, 34)

        # 七色圆点：与总览「功能与说明」同款，独立一行，不贴数字
        self.color_bar = _ClockColorBar(on_change=self._on_digit_color)
        root.addWidget(self.color_bar, 0)

        # 下：月历 | 天气
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(10)

        self.cal_card = make_card("ClockCalCard")
        cal_lay = QVBoxLayout(self.cal_card)
        cal_lay.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        cal_lay.setSpacing(max(2, CARD_TITLE_BODY_GAP - 2))
        self._cal_title = install_card_title(self.cal_card, cal_lay, "日历")
        self.calendar = MonthCalendar()
        cal_lay.addWidget(self.calendar, 1)

        self.wx_card = make_card("ClockWxCard")
        wx_lay = QVBoxLayout(self.wx_card)
        wx_lay.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        wx_lay.setSpacing(CARD_TITLE_BODY_GAP)
        self._wx_title = install_card_title(self.wx_card, wx_lay, "天气")
        self.weather = WeatherCard()
        wx_lay.addWidget(self.weather, 1)

        bottom.addWidget(self.cal_card, 1)
        bottom.addWidget(self.wx_card, 1)
        root.addLayout(bottom, 21)

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        theme.changed.connect(self.refresh_theme)
        self.refresh_theme()
        self._tick()

    def on_enter(self):
        self._tick()

    def set_prefs_dirty_callback(self, cb):
        self._prefs_dirty_cb = cb

    def _notify_prefs_dirty(self):
        if callable(self._prefs_dirty_cb):
            try:
                self._prefs_dirty_cb()
            except Exception:
                pass

    def _on_digit_color(self, hex_color: str):
        self.clock.set_digit_color(hex_color)
        self._notify_prefs_dirty()

    def export_settings(self) -> dict:
        return {"digit_color": self.clock.digit_color()}

    def apply_settings(self, d: dict):
        if not isinstance(d, dict):
            return
        color = _normalize_digit_color(d.get("digit_color"))
        self.color_bar.set_color(color)
        self.clock.set_digit_color(color)

    def refresh_theme(self, *_):
        restyle_card_frame(self.cal_card)
        restyle_card_frame(self.wx_card)
        restyle_card_title(self._cal_title)
        restyle_card_title(self._wx_title)
        self.calendar.refresh_theme()
        self.weather.refresh_theme()
        self.color_bar.restyle_theme()
        self.clock.update()

    def _tick(self):
        now = datetime.now()
        self.clock.set_time(
            f"{now.hour:02d}",
            f"{now.minute:02d}",
            now.second % 2 == 0,
        )
        d = now.date()
        if d != self._last_date:
            self._last_date = d
            self.calendar.set_month(d)
