# pages/page_sub_usage.py
# 订阅用量：积分月池、月付、包年。
# 每条按自己的方向记录：正向 100% 是还剩，反向 100% 是已经用完。
# 页面可以把所有色条统一显示成正向或反向。到点回到「还没开始用」。

import calendar
from datetime import datetime, timedelta

from PyQt5.QtCore import Qt, QEvent, QTimer, QSize, QRectF, QPoint, QPointF, pyqtSignal
from PyQt5.QtGui import QPainter, QColor, QFont, QFontMetrics, QPen, QPixmap
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QLineEdit, QPushButton, QRadioButton, QButtonGroup, QCheckBox, QScrollArea,
    QSizePolicy, QMessageBox, QFrame,
)

from styles.style_all import (
    apply_medium_button, make_card, restyle_card_frame, restyle_card_title,
    theme, tk,
)
from utils.logger import get_logger

log = get_logger(__name__)

_DIR_LABEL = {"forward": "正向", "reverse": "反向"}


def record_dir(item):
    direction = (item or {}).get("record_dir") or "forward"
    if direction not in ("forward", "reverse"):
        return "forward"
    return direction


def editor_dir(item, fallback="forward"):
    """右边「正向 / 反向」按每条记录记。没单独写过的，用页面缺省。"""
    raw = (item or {}).get("editor_dir")
    if raw in ("forward", "reverse"):
        return raw
    if fallback in ("forward", "reverse"):
        return fallback
    return "forward"


def full_mark(direction):
    """还没开始用时，这一条记录方向上的数字。"""
    return 0 if direction == "reverse" else 100


def as_display(stored, record, display):
    stored = _clamp_pct(stored)
    if record == display:
        return stored
    return 100 - stored


def as_stored(shown, record, display):
    shown = _clamp_pct(shown)
    if record == display:
        return shown
    return 100 - shown


def window_on(item):
    """日重置。缺省开着，只有明确关掉的平台才没有。"""
    return (item or {}).get("window_on") is not False


def _editor_sense(display):
    """右边编辑区的读法：正向是已用，反向是剩余。"""
    return "reverse" if display == "forward" else "forward"


def _usage_word(display):
    return "已用" if display == "forward" else "剩余"


def _parse_dt(text):
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _fmt_dt(dt):
    return dt.strftime("%Y-%m-%dT%H:%M")


def _parse_hhmm(text, fallback=(8, 0)):
    parts = (text or "").strip().replace(".", ":").split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return fallback
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return fallback


def _clamp_pct(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 100
    return max(0, min(100, number))


def next_week_reset(now, weekday, hhmm):
    hour, minute = _parse_hhmm(hhmm)
    weekday = int(weekday) % 7
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    candidate = candidate + timedelta(days=(weekday - candidate.weekday()) % 7)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def roll_item(item, now):
    """到点回到还没开始用，并把下一次重置推到未来。返回是否改过。"""
    changed = False
    direction = record_dir(item)
    if item.get("record_dir") != direction:
        item["record_dir"] = direction
        changed = True
    full = full_mark(direction)
    if window_on(item):
        try:
            hours = int(item.get("window_hours") or 5)
        except (TypeError, ValueError):
            hours = 5
        hours = max(1, min(720, hours))
        if item.get("window_hours") != hours:
            item["window_hours"] = hours
            changed = True
        reset = _parse_dt(item.get("window_reset"))
        if reset is None:
            base = now.replace(second=0, microsecond=0)
            item["window_reset"] = _fmt_dt(base + timedelta(hours=hours))
            changed = True
        elif now >= reset:
            item["window_remain"] = full
            while now >= reset:
                reset += timedelta(hours=hours)
            item["window_reset"] = _fmt_dt(reset)
            changed = True
    try:
        weekday = int(item.get("week_weekday") or 0) % 7
    except (TypeError, ValueError):
        weekday = 0
    if item.get("week_weekday") != weekday:
        item["week_weekday"] = weekday
        changed = True
    hhmm = "%02d:%02d" % _parse_hhmm(item.get("week_time"))
    if item.get("week_time") != hhmm:
        item["week_time"] = hhmm
        changed = True
    week_reset = _parse_dt(item.get("week_reset"))
    if week_reset is None:
        item["week_reset"] = _fmt_dt(next_week_reset(now, weekday, hhmm))
        changed = True
    elif now >= week_reset:
        item["week_remain"] = full
        item["week_reset"] = _fmt_dt(next_week_reset(now, weekday, hhmm))
        changed = True
    if meter_of(item) == "points":
        preset = _parse_dt(item.get("points_reset"))
        if preset is not None and now >= preset:
            item["points_remain"] = full
            total = _points_total(item)
            if total > 0:
                item["points_left"] = total
            day = _renew_day(item)
            nxt = preset
            guard = 0
            while now >= nxt and guard < 36:
                nxt = _next_month(nxt, day)
                guard += 1
            item["points_reset"] = _fmt_dt(nxt)
            changed = True
    for key in ("window_remain", "week_remain"):
        clamped = _clamp_pct(item.get(key, full))
        if item.get(key) != clamped:
            item[key] = clamped
            changed = True
    return changed


def _read_pair(day_text, hour_text):
    def _num(text):
        try:
            return max(0, int((text or "0").strip() or "0"))
        except ValueError:
            return 0
    return min(_num(day_text), 3660), min(_num(hour_text), 9999)


def _read_span3(day_text, hour_text, minute_text):
    days, hours = _read_pair(day_text, hour_text)
    try:
        minutes = max(0, int((minute_text or "0").strip() or "0"))
    except ValueError:
        minutes = 0
    return days, hours, min(minutes, 9999)


def _split_span(reset, now):
    """把距离拆成整天和剩余小时，按最近的小时取整。不足 1 小时记成 0。"""
    if reset is None:
        return 0, 0
    seconds = int((reset - now).total_seconds())
    if seconds < 1800:
        return 0, 0
    total_hours = (seconds + 1800) // 3600
    if total_hours <= 0:
        return 0, 0
    return divmod(total_hours, 24)


def _span_reset(now, days, hours):
    base = now.replace(second=0, microsecond=0)
    return base + timedelta(days=days, hours=hours)


def _split_span_min(reset, now):
    """日重置、周重置的还剩：整天、小时、分钟，按最近的一分钟取整。"""
    if reset is None:
        return 0, 0, 0
    seconds = int((reset - now).total_seconds())
    if seconds < 30:
        return 0, 0, 0
    total_minutes = (seconds + 30) // 60
    days, rem = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rem, 60)
    return days, hours, minutes


def _span_reset_min(now, days, hours, minutes):
    """从现在起算日、时、分。存成整分钟后，读回来仍是这一组数。"""
    days, hours, minutes = int(days), int(hours), int(minutes)
    base = now.replace(second=0, microsecond=0)
    if now.second > 30 or (now.second == 30 and now.microsecond):
        base += timedelta(minutes=1)
    when = base + timedelta(days=days, hours=hours, minutes=minutes)
    total = days * 24 * 60 + hours * 60 + minutes
    target_days, rem = divmod(total, 24 * 60)
    target_hours, target_minutes = divmod(rem, 60)
    target = (target_days, target_hours, target_minutes)
    if _split_span_min(when, now) != target:
        for nudge in (1, -1):
            trial = when + timedelta(minutes=nudge)
            if _split_span_min(trial, now) == target:
                return trial
    return when


def _span_phrase(reset, now):
    seconds = int((reset - now).total_seconds())
    if seconds < 60:
        return "不到1分钟后重置"
    if seconds < 3600:
        return f"{seconds // 60}分钟后重置"
    days, hours = _split_span(reset, now)
    if days <= 0:
        return f"{max(1, seconds // 3600)}小时后重置"
    if hours <= 0:
        return f"{days}天后重置"
    return f"{days}天{hours}小时后重置"


def _next_month(dt, day):
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    last = calendar.monthrange(year, month)[1]
    day = min(max(1, int(day)), last)
    return dt.replace(year=year, month=month, day=day, second=0, microsecond=0)


def _on_month_day(dt, day):
    """把重置日改到这个月的某一天；这一天已经过去就推到下个月。"""
    last = calendar.monthrange(dt.year, dt.month)[1]
    use = min(max(1, int(day)), last)
    moved = dt.replace(day=use, second=0, microsecond=0)
    if moved <= datetime.now():
        moved = _next_month(moved, day)
    return moved


def window_caption(reset, now):
    if reset is None:
        return "未设重置时间"
    clock = reset.strftime("%H:%M")
    if reset.date() == now.date():
        when = f"今天{clock}"
    elif reset.date() == (now.date() + timedelta(days=1)):
        when = f"明天{clock}"
    else:
        when = f"{reset.month}月{reset.day}日{clock}"
    return f"{_span_phrase(reset, now)}・{when}"


def week_caption(reset, now=None):
    if reset is None:
        return "未设重置日"
    when = f"{reset.month}月{reset.day}日 {reset.strftime('%H:%M')}"
    if now is None:
        return f"{when}重置"
    return f"{_span_phrase(reset, now)}・{when}"


def meter_of(item):
    mode = (item or {}).get("meter") or "progress"
    if mode not in ("progress", "points"):
        return "progress"
    return mode


def _points_total(item):
    try:
        value = int((item or {}).get("monthly_points") or 0)
    except (TypeError, ValueError):
        value = 0
    return max(0, value)


def _points_left(item):
    """剩余积分。有 points_left 就用它，否则按还剩百分比从月积分推出来。"""
    raw = (item or {}).get("points_left", None)
    if raw is not None and raw != "":
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            pass
    total = _points_total(item)
    if total <= 0:
        return 0
    direction = record_dir(item)
    shown = as_display(
        (item or {}).get("points_remain", full_mark(direction)),
        direction,
        "forward",
    )
    return max(0, int(round(total * shown / 100.0)))


def _renew_day(item):
    try:
        day = int((item or {}).get("renew_day") or 1)
    except (TypeError, ValueError):
        day = 1
    return min(31, max(1, day))


def _points_due(item, now):
    """积分下一次重置的日期。有 points_reset 就用它，没有就用每月重置日。"""
    preset = _parse_dt((item or {}).get("points_reset"))
    if preset is not None:
        if preset.date() >= now.date():
            return preset
        day = preset.day
        nxt = preset
        guard = 0
        while nxt.date() < now.date() and guard < 36:
            nxt = _next_month(nxt, day)
            guard += 1
        return nxt
    day = _renew_day(item)
    last = calendar.monthrange(now.year, now.month)[1]
    use = min(max(1, day), last)
    candidate = now.replace(day=use, hour=0, minute=0, second=0, microsecond=0)
    if candidate.date() < now.date():
        candidate = _next_month(candidate, day)
    return candidate


def _points_pace_parts(item, now):
    """积分第三行只留剩余积分。建议按距重置的天数摊，当天按 1 天算，放到第四行。"""
    total = _points_total(item)
    left = _points_left(item)
    if total <= 0 and left <= 0:
        return "未设", ""
    due = _points_due(item, now)
    days = (due.date() - now.date()).days
    if days < 1:
        days = 1
    pace = int(left / days + 0.5)
    return f"{left}积分", f"建议 {pace}/天"


def _due_hex(days, span):
    """离重置越近越红。按这段周期的三分之二、三分之一分档，和色条同一套。"""
    if span <= 0:
        span = 1
    ratio = int(round(100.0 * max(0, days) / float(span)))
    return _state_color(ratio).name()


def _points_due_line(item, now):
    """第四行右侧：哪一天重置，还剩几个整天。当天是 0 天。"""
    due = _points_due(item, now)
    days = (due.date() - now.date()).days
    return f"{due.month}月{due.day}日重置・还剩{days}天"


def blank_item(name, kind):
    return {
        "id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
        "name": name,
        "kind": kind,
        "meter": "progress",
        "monthly_points": 0,
        "renew_day": 1,
        "expire": "",
        "record_dir": "forward",
        "points_remain": 100,
        "window_hours": 5,
        "window_reset": "",
        "window_remain": 100,
        "week_weekday": 0,
        "week_time": "08:00",
        "week_reset": "",
        "week_remain": 100,
    }


_LINE_H = 22
_BAR_T = 4
_EDIT_H = 29  # 与 kind=medium「添加」一致：内容 27px，加上下 1px 边框
_FROZEN_INK = "#4a5568"  # 日重置关掉后，这一段文字的深灰
_PROGRESS_EDIT_QSS = (
    "QLineEdit{background:#0e1530;color:#e8eaf0;border:1px solid #2a3965;"
    "border-radius:6px;padding:4px 2px;font-size:13px;"
    "selection-background-color:#1f3a8a;}"
    "QLineEdit:focus{border-color:#3b82f6;}"
)
_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _compose_month_day(month, day, hour, minute, existing, now):
    """月、日两个框合成重置日。同一天不改年份；改了就取下一次还没到的那天。"""
    if not (1 <= month <= 12 and 1 <= day <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    if (
        existing is not None
        and existing.month == month
        and existing.day == day
        and existing.hour == hour
        and existing.minute == minute
    ):
        return existing.replace(second=0, microsecond=0)
    for year in (now.year, now.year + 1, now.year + 2):
        last = calendar.monthrange(year, month)[1]
        if day > last:
            continue
        candidate = datetime(year, month, day, hour, minute)
        if candidate > now:
            return candidate
    return None


def _state_color(value):
    """100% 绿，中间黄，0% 红。"""
    value = _clamp_pct(value)
    if value >= 67:
        return QColor("#22C55E")
    if value >= 34:
        return QColor("#F59E0B")
    return QColor("#EF4444")


class _DragPreview(QWidget):
    """半透明拖拽影：跟鼠标走。原卡留在原位。"""

    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self._pm = QPixmap(pixmap)
        self.setFixedSize(max(1, self._pm.width()), max(1, self._pm.height()))
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setOpacity(0.55)
        painter.drawPixmap(0, 0, self._pm)
        painter.end()


class _DropGuide(QWidget):
    """拖拽插入点：横向虚线，落在两条记录之间或最上/最下。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(12)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _event):
        painter = QPainter(self)
        try:
            dark = bool(theme.is_dark)
        except Exception:
            dark = True
        color = QColor("#ffffff") if dark else QColor("#334155")
        pen = QPen(color, 2, Qt.CustomDashLine)
        pen.setDashPattern([3, 3])
        pen.setCapStyle(Qt.FlatCap)
        painter.setPen(pen)
        y = self.height() // 2
        painter.drawLine(6, y, max(6, self.width() - 6), y)
        painter.end()


class _RemainBar(QWidget):
    """4px 圆角色条，拉满所在行，垂直居中。只显示，不改数值。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 100
        self.setFixedHeight(_LINE_H)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        try:
            theme.changed.connect(self.update)
        except Exception:
            pass

    def value(self):
        return self._value

    def setValue(self, value):
        self._value = _clamp_pct(value)
        self.update()

    def _percent_font(self):
        font = QFont(self.font())
        font.setPixelSize(12)
        return font

    def _percent_text(self):
        return f"{self._value}%"

    def _percent_slot_width(self):
        """固定按 100% 占位。两位数字右边空出一位，色条长度不再跟着变。"""
        fm = QFontMetrics(self._percent_font())
        digit = max(fm.horizontalAdvance(str(n)) for n in range(10))
        return digit * 3 + fm.horizontalAdvance("%")

    def _track_width(self):
        return max(1.0, float(self.width()) - self._percent_slot_width() - 8.0)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            dark = bool(theme.is_dark)
        except Exception:
            dark = True
        track = QColor(255, 255, 255, 36) if dark else QColor(15, 23, 42, 28)
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        top = (self.height() - _BAR_T) / 2.0
        radius = _BAR_T / 2.0
        track_w = self._track_width()
        bar = QRectF(0, top, track_w, _BAR_T)
        painter.drawRoundedRect(bar, radius, radius)
        width = track_w * (self._value / 100.0)
        if width > 0.5:
            painter.setBrush(_state_color(self._value))
            # 宽度是浮点。必须走 QRectF，整型重载会让 SIP 写坏栈。
            painter.drawRoundedRect(QRectF(0, top, width, _BAR_T), radius, radius)
        text = self._percent_text()
        font = self._percent_font()
        fm = QFontMetrics(font)
        baseline = (self.height() + fm.ascent() - fm.descent()) / 2.0
        painter.setPen(_state_color(self._value))
        painter.setFont(font)
        painter.drawText(QPointF(track_w + 8.0, baseline), text)
        painter.end()


def _edit_button(text):
    """和「添加」同一套中按钮，高度对齐。"""
    btn = apply_medium_button(QPushButton(text))
    btn.setFocusPolicy(Qt.NoFocus)
    btn.setFixedHeight(_EDIT_H)
    return btn


def _edit_field(width, placeholder=""):
    edit = QLineEdit()
    edit.setObjectName("CalcInput")
    edit.setFixedHeight(_EDIT_H)
    edit.setFixedWidth(width)
    if placeholder:
        edit.setPlaceholderText(placeholder)
    return edit


class _RecordRow(QFrame):
    """单条订阅。外观对齐语音录入的转写记录卡。按住卡片可拖动排序。"""

    changed = pyqtSignal()
    remove_requested = pyqtSignal(object)
    picked = pyqtSignal(object)
    reorder_started = pyqtSignal(object, QPoint)
    reorder_moved = pyqtSignal(object, QPoint)
    reorder_finished = pyqtSignal(object)

    def __init__(self, item, parent=None, display_dir="forward"):
        super().__init__(parent)
        self.item = item
        self.display_dir = display_dir if display_dir in ("forward", "reverse") else "forward"
        self._selected = False
        self._press_gpos = None
        self._dragging = False
        self.setObjectName("SubUsageCard")
        self.setFrameShape(QFrame.NoFrame)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.OpenHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self._apply_selected_style()
        try:
            theme.changed.connect(self._apply_selected_style)
        except Exception:
            pass

        form = QVBoxLayout(self)
        form.setContentsMargins(10, 6, 10, 6)
        form.setSpacing(2)
        self.lbl_title = QLabel()
        self.lbl_title.setObjectName("SubTitle")
        self.lbl_title.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.btn_plus = self._dir_button("+", "SubPlus")
        self.btn_minus = self._dir_button("-", "SubMinus")
        self.btn_plus.clicked.connect(lambda: self._choose_dir("forward"))
        self.btn_minus.clicked.connect(lambda: self._choose_dir("reverse"))
        self.btn_del = QPushButton("✕")
        self.btn_del.setObjectName("SubDel")
        self.btn_del.setCursor(Qt.PointingHandCursor)
        self.btn_del.setFocusPolicy(Qt.NoFocus)
        self.btn_del.setFixedSize(22, 22)
        self.bar_window = _RemainBar()
        self.lbl_window = QLabel()
        self.lbl_window.setObjectName("SubHint")
        self.lbl_window.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.bar_week = _RemainBar()
        self.lbl_month_value = QLabel()
        self.lbl_month_value.setObjectName("SubMeter")
        self.lbl_month_value.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.lbl_week = QLabel()
        self.lbl_week.setObjectName("SubHint")
        self.lbl_week.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        self.lbl_suggest = QLabel()
        self.lbl_suggest.setObjectName("SubHint")
        self.lbl_suggest.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.lbl_suggest.hide()
        form.addWidget(self._title_line())
        _line_now, self.lbl_now = self._meter_line("剩余", self.bar_window)
        form.addWidget(_line_now)
        self._row_span, self.lbl_span = self._meter_line("本周", self.bar_week, self.lbl_month_value)
        self._row_span.layout().insertWidget(0, self.lbl_suggest, 0)
        self._row_foot = self._foot_line()
        form.addWidget(self._row_span)
        form.addWidget(self._row_foot)
        for lab in (
            self.lbl_title, self.lbl_window, self.lbl_week,
            self.lbl_month_value, self.lbl_suggest,
        ):
            lab.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.btn_del.clicked.connect(lambda: self.remove_requested.emit(self))
        self._arm_drag()
        self.sync_from_item()

    def _dir_button(self, text, name):
        btn = QPushButton(text)
        btn.setObjectName(name)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setFlat(True)
        btn.setFixedSize(22, 22)
        btn.setStyleSheet(self._dir_button_qss("#94a3b8"))
        return btn

    def _dir_button_qss(self, color):
        """无外框。选中是蓝色，没选中是灰色。"""
        return (
            "QPushButton{background:transparent;border:none;padding:0;"
            f"color:{color};font-size:16px;font-weight:700;}}"
            "QPushButton:hover{background:transparent;border:none;}"
            "QPushButton:pressed{background:transparent;border:none;}"
        )

    def _paint_dir_buttons(self):
        if getattr(self, "btn_plus", None) is None:
            return
        direction = record_dir(getattr(self, "item", None) or {})
        blue, gray = "#3a8ee0", "#94a3b8"
        self.btn_plus.setStyleSheet(
            self._dir_button_qss(blue if direction == "forward" else gray)
        )
        self.btn_minus.setStyleSheet(
            self._dir_button_qss(blue if direction == "reverse" else gray)
        )

    def _choose_dir(self, direction):
        """「+」正向、「-」反向，二选一。切换时对调已存百分比，画面上的剩余不变。"""
        self.picked.emit(self)
        if direction not in ("forward", "reverse") or record_dir(self.item) == direction:
            return
        old = record_dir(self.item)
        for key in ("window_remain", "week_remain", "points_remain"):
            self.item[key] = 100 - _clamp_pct(self.item.get(key, full_mark(old)))
        self.item["record_dir"] = direction
        self.sync_from_item()
        self.changed.emit()

    def _arm_drag(self):
        """方向和删除单独点。色条不接收鼠标，按住卡片其它地方拖动排序。"""
        self.installEventFilter(self)
        skip = {self.btn_del, self.btn_plus, self.btn_minus}
        for child in self.findChildren(QWidget):
            if child in skip:
                continue
            child.installEventFilter(self)

    def eventFilter(self, obj, event):
        et = event.type()
        if et == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._press_gpos = event.globalPos()
            self._dragging = False
            if obj is not self:
                self.picked.emit(self)
            return False
        if (
            et == QEvent.MouseMove
            and self._press_gpos is not None
            and (event.buttons() & Qt.LeftButton)
        ):
            if not self._dragging:
                dist = QApplication.startDragDistance()
                delta = (event.globalPos() - self._press_gpos).manhattanLength()
                if delta < dist:
                    return False
                self._dragging = True
                self.setCursor(Qt.ClosedHandCursor)
                self.reorder_started.emit(self, event.globalPos())
            self.reorder_moved.emit(self, event.globalPos())
            return True
        if et == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            was = self._dragging
            self._press_gpos = None
            self._dragging = False
            self.setCursor(Qt.OpenHandCursor)
            if was:
                self.reorder_finished.emit(self)
                return True
            return False
        return False

    def _host_line(self, row):
        row.setContentsMargins(0, 0, 0, 0)
        host = QWidget()
        host.setObjectName("SubLine")
        host.setAttribute(Qt.WA_StyledBackground, True)
        host.setFixedHeight(_LINE_H)
        host.setLayout(row)
        return host

    def _title_line(self):
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(self.lbl_title, 1)
        row.addWidget(self.btn_plus, 0)
        row.addWidget(self.btn_minus, 0)
        row.addWidget(self.btn_del, 0)
        return self._host_line(row)

    def _meter_line(self, title, bar, extra=None):
        row = QHBoxLayout()
        row.setSpacing(8)
        name = QLabel(title)
        name.setObjectName("SubMeter")
        name.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        name.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        row.addWidget(name, 0)
        row.addWidget(bar, 1)
        if extra is not None:
            row.addWidget(extra, 1)
        return self._host_line(row), name

    def _foot_line(self):
        """原第三行靠左，原第五行靠右，合成最后一行。"""
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.lbl_window, 1)
        row.addWidget(self.lbl_week, 0)
        return self._host_line(row)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.picked.emit(self)
            event.accept()
            return
        super().mousePressEvent(event)

    def set_selected(self, on):
        self._selected = bool(on)
        self._apply_selected_style()

    def _apply_selected_style(self, *_args):
        try:
            accent = tk("accent")
            border = tk("border")
            row_bg = tk("row_bg")
            text = tk("text")
            muted = tk("text_mut")
            err = tk("err")
        except Exception:
            accent, border, row_bg = "#60a5fa", "#2a3965", "#141b33"
            text, muted, err = "#e8eaf0", "#94a3b8", "#ef4444"
        bd = accent if self._selected else border
        self.setStyleSheet(
            f"#SubUsageCard{{background:{row_bg};border:2px solid {bd};"
            f"border-radius:8px;}}"
            f"#SubLine{{background:transparent;border:none;}}"
            f"QLabel#SubTitle{{background:transparent;color:{accent};"
            f"font-size:12px;font-weight:700;border:none;}}"
            f"QLabel#SubMeter{{background:transparent;color:{text};"
            f"font-size:12px;border:none;}}"
            f"QLabel#SubHint{{background:transparent;color:{muted};"
            f"font-size:12px;border:none;}}"
            f"QPushButton#SubDel{{background:transparent;color:{muted};"
            f"border:none;border-radius:5px;padding:0;font-size:12px;}}"
            f"QPushButton#SubDel:hover{{color:{err};}}"
        )
        self._paint_dir_buttons()
        self._paint_due_line(datetime.now())
        self._paint_points_emphasis(text, muted)

    def _paint_meter(self, bar, key):
        direction = record_dir(self.item)
        stored = _clamp_pct(self.item.get(key, full_mark(direction)))
        # 左边始终按「还剩多少」画。正向/反向只改右边编辑区。
        bar.setValue(as_display(stored, direction, "forward"))

    def sync_from_item(self):
        now = datetime.now()
        roll_item(self.item, now)
        self.lbl_title.setText((self.item.get("name") or "").strip() or "未命名")
        points = meter_of(self.item) == "points"
        self.lbl_now.setText("积分" if points else "剩余")
        self.lbl_span.setText("总分" if points else "本周")
        self.bar_week.setVisible(not points)
        self.lbl_month_value.setVisible(points)
        if points:
            self.bar_window.setVisible(True)
            total = _points_total(self.item)
            self.lbl_month_value.setText(str(total) if total else "未设")
            self._paint_meter(self.bar_window, "points_remain")
        else:
            self.bar_window.setVisible(True)
            if window_on(self.item):
                self._paint_meter(self.bar_window, "window_remain")
            else:
                # 日重置关掉时，当前这条只显示空条和 0，不改已存的数。
                self.bar_window.setValue(0)
            self._paint_meter(self.bar_week, "week_remain")
        self._order_tail_rows(points)
        self._place_due_label(points)
        self.refresh_captions(now)
        self._apply_selected_style()

    def _order_tail_rows(self, points):
        """积分时第三行在上、总分那行在下。进度仍是本周在上、提示在下。"""
        form = self.layout()
        top = self._row_foot if points else self._row_span
        bottom = self._row_span if points else self._row_foot
        if form.indexOf(bottom) == form.indexOf(top) + 1:
            return
        form.removeWidget(self._row_span)
        form.removeWidget(self._row_foot)
        form.addWidget(top)
        form.addWidget(bottom)

    def _place_due_label(self, points):
        """积分时重置说明跟到第四行右边。进度时它回到最后一行右边。"""
        foot = self._row_foot.layout()
        span = self._row_span.layout()
        home = span if points else foot
        if self.lbl_week.parentWidget() is home:
            return
        (foot if points else span).removeWidget(self.lbl_week)
        home.addWidget(self.lbl_week, 0)

    def refresh_captions(self, now):
        if meter_of(self.item) == "points":
            left, suggest = _points_pace_parts(self.item, now)
            self.lbl_window.setText(left)
            self.lbl_suggest.setText(suggest)
            self.lbl_suggest.setVisible(bool(suggest))
            self.lbl_week.setText(_points_due_line(self.item, now))
            self._paint_due_line(now)
            return
        self.lbl_suggest.hide()
        if not window_on(self.item):
            self.lbl_window.setText("无单日重置")
        else:
            self.lbl_window.setText(window_caption(_parse_dt(self.item.get("window_reset")), now))
        self.lbl_week.setText(week_caption(_parse_dt(self.item.get("week_reset")), now))
        self._paint_due_line(now)

    def _paint_due_line(self, now):
        """重置说明：离重置还早是绿，过了三分之一是黄，过了三分之二是红。"""
        label = getattr(self, "lbl_week", None)
        if label is None:
            return
        if meter_of(self.item) == "points":
            due = _points_due(self.item, now)
            color = _due_hex((due.date() - now.date()).days, 30)
        else:
            reset = _parse_dt(self.item.get("week_reset"))
            if reset is None:
                try:
                    color = tk("text_mut")
                except Exception:
                    color = "#94a3b8"
            else:
                color = _due_hex((reset.date() - now.date()).days, 7)
        label.setStyleSheet(
            "QLabel#SubHint{background:transparent;color:%s;font-size:12px;border:none;}"
            % color
        )

    def _paint_points_emphasis(self, text, muted):
        """积分第三行用正文色。总分和数字用次要色。进度恢复卡片原样。"""
        window = getattr(self, "lbl_window", None)
        span = getattr(self, "lbl_span", None)
        month = getattr(self, "lbl_month_value", None)
        if window is None or span is None or month is None:
            return
        if meter_of(getattr(self, "item", None) or {}) == "points":
            window.setStyleSheet(
                "QLabel#SubHint{background:transparent;color:%s;"
                "font-size:12px;border:none;}" % text
            )
            dim = (
                "QLabel#SubMeter{background:transparent;color:%s;"
                "font-size:12px;border:none;}" % muted
            )
            span.setStyleSheet(dim)
            month.setStyleSheet(dim)
            return
        window.setStyleSheet("")
        span.setStyleSheet("")
        month.setStyleSheet("")


class PageSubUsage(QWidget):
    def hasHeightForWidth(self):
        return False

    def minimumSizeHint(self):
        return QSize(0, 0)

    def sizeHint(self):
        hint = super().sizeHint()
        if not hint.isValid() or hint.height() <= 0:
            return QSize(0, 0)
        if hint.height() > 700:
            return QSize(hint.width(), 700)
        return hint

    def showEvent(self, event):
        super().showEvent(event)
        self._match_reset_label_width()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._cards = []
        self._current = None
        self._loading = False
        self._filling = False
        self._dirty_cb = None
        self._display = "forward"
        self._display_default = "forward"
        self._drag_card = None
        self._drag_preview = None
        self._drag_guide = None
        self._drag_grab = None
        self._drag_insert = None
        self._timer = QTimer(self)
        self._timer.setInterval(15000)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self._scroll.setAttribute(Qt.WA_StyledBackground, True)
        self._scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollArea > QWidget > QWidget{background:transparent;}"
        )
        try:
            self._scroll.viewport().setAutoFillBackground(False)
            self._scroll.viewport().setStyleSheet("background:transparent;")
        except Exception:
            pass
        self._host = QWidget()
        self._host.setObjectName("SubUsageHost")
        self._host.setAttribute(Qt.WA_StyledBackground, True)
        self._host.setStyleSheet("#SubUsageHost{background:transparent;border:none;}")
        self._list = QVBoxLayout(self._host)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(6)
        self._list.setAlignment(Qt.AlignTop)
        self._list.addStretch(1)
        self._scroll.setWidget(self._host)
        root.addWidget(self._scroll, 7)

        self._editor = make_card("SubUsageEditor")
        self._editor.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        side = QVBoxLayout(self._editor)
        side.setContentsMargins(12, 10, 12, 12)
        side.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        self._editor_title = QLabel("编辑")
        self._editor_title.setProperty("role", "card-title")
        restyle_card_title(self._editor_title)
        self.btn_display = _edit_button("正向")
        self.btn_display.clicked.connect(lambda _checked=False: self._toggle_display())
        self.btn_add = apply_medium_button(QPushButton("添加"))
        self.btn_add.setFixedHeight(_EDIT_H)
        self.btn_add.clicked.connect(self._add_blank)
        head.addWidget(self._editor_title, 0, Qt.AlignVCenter)
        head.addStretch(1)
        head.addWidget(self.btn_add, 0, Qt.AlignVCenter)
        head.addWidget(self.btn_display, 0, Qt.AlignVCenter)
        side.addLayout(head)

        self._empty = QLabel("先在左边点一条")
        self._empty.setObjectName("ResultHint")
        self._empty.setWordWrap(True)
        side.addWidget(self._empty)

        def _form_label(text):
            lab = QLabel(text)
            lab.setObjectName("SubFormLab")
            lab.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            lab.setContentsMargins(0, 0, 0, 0)
            self._form_labs.append(lab)
            return lab

        def _value_row(widgets):
            row = QHBoxLayout()
            row.setSpacing(6)
            row.setContentsMargins(0, 0, 0, 0)
            for widget, stretch in widgets:
                row.addWidget(widget, stretch, Qt.AlignVCenter)
            if not any(stretch for _widget, stretch in widgets):
                row.addStretch(1)
            return row

        def _sep():
            line = QFrame()
            line.setObjectName("SubUsageSep")
            line.setFixedHeight(1)
            line.setAttribute(Qt.WA_StyledBackground, True)
            self._seps.append(line)
            return line

        self._form_labs = []
        self._seps = []
        self.edit_name = QLineEdit()
        self.edit_name.setObjectName("CalcInput")
        self.edit_name.setFixedHeight(_EDIT_H)
        self.edit_name.setPlaceholderText("平台名称")
        self.edit_name.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.chk_window = QCheckBox("日重置")
        self.chk_window.setChecked(True)
        self.chk_window.setCursor(Qt.PointingHandCursor)
        self.chk_window.setMinimumWidth(0)
        self.chk_window.setStyleSheet("background:transparent;font-size:12px;")
        self.chk_window.toggled.connect(self._editor_window_on)
        self._meter_group = QButtonGroup(self)
        self._meter_radios = {}
        for key, text in (("progress", "进度"), ("points", "积分")):
            radio = QRadioButton(text)
            radio.setStyleSheet("background: transparent;")
            radio.setCursor(Qt.PointingHandCursor)
            self._meter_radios[key] = radio
            self._meter_group.addButton(radio)
        self._meter_radios["progress"].setChecked(True)
        self._meter_group.buttonClicked.connect(self._on_meter_radio)

        self.edit_window_pct = _edit_field(64, "100")
        self.edit_win_days = _edit_field(40, "0")
        self.edit_win_span = _edit_field(40, "0")
        self.edit_win_min = _edit_field(40, "0")
        self.edit_win_month = _edit_field(40, "10")
        self.edit_win_dom = _edit_field(40, "27")
        self.lbl_win_day = QLabel("周一")
        self.lbl_win_day.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.lbl_win_day.setFixedHeight(_EDIT_H)
        self.lbl_win_day.setFocusPolicy(Qt.NoFocus)
        self.edit_win_hour = _edit_field(40, "8")
        self.edit_win_clock_min = _edit_field(40, "0")
        self._win_colon = QLabel(":")
        self._win_colon.setAlignment(Qt.AlignCenter)
        self._win_colon.setFixedHeight(_EDIT_H)
        self.edit_week_pct = _edit_field(64, "100")
        self.lbl_week_day = QLabel("周一")
        self.lbl_week_day.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.lbl_week_day.setFixedHeight(_EDIT_H)
        self.lbl_week_day.setFocusPolicy(Qt.NoFocus)
        self.edit_week_hour = _edit_field(40, "8")
        self.edit_week_min = _edit_field(40, "0")
        self._week_colon = QLabel(":")
        self._week_colon.setAlignment(Qt.AlignCenter)
        self._week_colon.setFixedHeight(_EDIT_H)
        self.edit_week_days = _edit_field(40, "0")
        self.edit_week_span = _edit_field(40, "0")
        self.edit_week_span_min = _edit_field(40, "0")
        self.edit_week_month = _edit_field(40, "10")
        self.edit_week_dom = _edit_field(40, "27")
        self.edit_points = _edit_field(88, "0")
        self.edit_monthly = _edit_field(88, "30000")
        self.edit_renew = _edit_field(48, "1")
        self.edit_pts_days = _edit_field(44, "0")
        self.edit_pts_span = _edit_field(44, "0")
        self._pct_unit = QLabel("%")
        self._week_unit = QLabel("%")
        self._day_unit = QLabel("日")
        self._win_day_unit = QLabel("天")
        self._win_span_unit = QLabel("小时")
        self._win_min_unit = QLabel("分钟")
        self._win_month_unit = QLabel("月")
        self._win_dom_unit = QLabel("日")
        self._week_day_unit = QLabel("天")
        self._week_span_unit = QLabel("小时")
        self._week_span_min_unit = QLabel("分钟")
        self._week_month_unit = QLabel("月")
        self._week_dom_unit = QLabel("日")
        for lab in (
            self._week_day_unit, self._week_month_unit, self._week_colon,
            self._win_day_unit, self._win_month_unit, self._win_colon,
        ):
            lab.setFixedWidth(24)
            lab.setFixedHeight(_EDIT_H)
            lab.setAlignment(Qt.AlignCenter)
        for lab in (
            self._week_span_unit, self._week_span_min_unit, self._week_dom_unit,
            self._win_span_unit, self._win_min_unit, self._win_dom_unit,
        ):
            lab.setFixedWidth(32)
            lab.setFixedHeight(_EDIT_H)
            lab.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._pts_day_unit = QLabel("天")
        self._pts_span_unit = QLabel("小时后")

        self._form = QWidget()
        self._form.setObjectName("SubUsageForm")
        self._form.setAttribute(Qt.WA_StyledBackground, True)
        self._form.setStyleSheet("#SubUsageForm{background:transparent;border:none;}")
        form_lay = QVBoxLayout(self._form)
        form_lay.setContentsMargins(0, 0, 0, 0)
        form_lay.setSpacing(0)
        top = QWidget()
        top.setObjectName("SubUsageForm")
        grid = QGridLayout(top)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)

        def _place(row, label, widgets):
            grid.addWidget(_form_label(label), row, 0, Qt.AlignLeft | Qt.AlignVCenter)
            grid.addLayout(_value_row(widgets), row, 1)

        _place(0, "名称", [(self.edit_name, 1)])
        radio_row = QHBoxLayout()
        radio_row.setSpacing(4)
        radio_row.setContentsMargins(0, 0, 0, 0)
        radio_row.addWidget(self._meter_radios["progress"])
        radio_row.addWidget(self._meter_radios["points"])
        radio_row.addStretch(1)
        grid.addWidget(_form_label("方式"), 1, 0, Qt.AlignLeft | Qt.AlignVCenter)
        grid.addLayout(radio_row, 1, 1)

        self._progress_box = QWidget()
        self._progress_box.setObjectName("SubUsageForm")
        progress_grid = QGridLayout(self._progress_box)
        progress_grid.setContentsMargins(0, 8, 0, 0)
        progress_grid.setHorizontalSpacing(8)
        progress_grid.setVerticalSpacing(8)
        progress_grid.setColumnStretch(1, 1)

        def _place_in(target, row, label, widgets):
            lab = _form_label(label)
            target.addWidget(lab, row, 0, Qt.AlignLeft | Qt.AlignVCenter)
            target.addLayout(_value_row(widgets), row, 1)
            return lab

        def _span_grid(host=None):
            grid = QGridLayout(host) if host is not None else QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(6)
            grid.setVerticalSpacing(8)
            grid.setColumnMinimumWidth(1, 40)
            grid.setColumnMinimumWidth(2, 24)
            grid.setColumnMinimumWidth(3, 40)
            grid.setColumnMinimumWidth(4, 32)
            grid.setColumnMinimumWidth(5, 40)
            grid.setColumnMinimumWidth(6, 32)
            grid.setColumnStretch(7, 1)
            return grid

        self._lab_current = _form_label("已用")
        day_head = QHBoxLayout()
        day_head.setSpacing(6)
        day_head.setContentsMargins(0, 0, 0, 0)
        day_head.addWidget(self.chk_window, 0, Qt.AlignVCenter)
        day_head.addStretch(1)
        day_head.addWidget(self._lab_current, 0, Qt.AlignVCenter)
        day_head.addWidget(self.edit_window_pct, 0, Qt.AlignVCenter)
        day_head.addWidget(self._pct_unit, 0, Qt.AlignVCenter)
        progress_grid.addLayout(day_head, 0, 0, 1, 2)

        self._window_box = QWidget()
        self._window_box.setObjectName("SubUsageForm")
        self._window_box.setAttribute(Qt.WA_StyledBackground, True)
        self._window_box.setStyleSheet("#SubUsageForm{background:transparent;border:none;}")
        day_rows = _span_grid(self._window_box)
        self._lab_day_remain = _form_label("还剩")
        day_rows.addWidget(self._lab_day_remain, 0, 0, Qt.AlignLeft | Qt.AlignVCenter)
        day_rows.addWidget(self.edit_win_days, 0, 1)
        day_rows.addWidget(self._win_day_unit, 0, 2, Qt.AlignCenter)
        day_rows.addWidget(self.edit_win_span, 0, 3)
        day_rows.addWidget(self._win_span_unit, 0, 4, Qt.AlignLeft | Qt.AlignVCenter)
        day_rows.addWidget(self.edit_win_min, 0, 5)
        day_rows.addWidget(self._win_min_unit, 0, 6, Qt.AlignLeft | Qt.AlignVCenter)
        self._lab_day_date = _form_label("日期")
        day_rows.addWidget(self._lab_day_date, 1, 0, Qt.AlignLeft | Qt.AlignVCenter)
        day_rows.addWidget(self.edit_win_month, 1, 1)
        day_rows.addWidget(self._win_month_unit, 1, 2, Qt.AlignCenter)
        day_rows.addWidget(self.edit_win_dom, 1, 3)
        day_rows.addWidget(self._win_dom_unit, 1, 4, Qt.AlignLeft | Qt.AlignVCenter)
        day_rows.addWidget(self.lbl_win_day, 2, 0, Qt.AlignLeft | Qt.AlignVCenter)
        day_rows.addWidget(self.edit_win_hour, 2, 1)
        day_rows.addWidget(self._win_colon, 2, 2, Qt.AlignCenter)
        day_rows.addWidget(self.edit_win_clock_min, 2, 3)
        progress_grid.addWidget(self._window_box, 1, 0, 1, 2)
        progress_grid.addWidget(_sep(), 2, 0, 1, 2)
        self._lab_week = _form_label("已用")
        self._lab_week_reset = _form_label("周重置")
        week_head = QHBoxLayout()
        week_head.setSpacing(6)
        week_head.setContentsMargins(0, 0, 0, 0)
        week_head.addWidget(self._lab_week_reset, 0, Qt.AlignVCenter)
        week_head.addStretch(1)
        week_head.addWidget(self._lab_week, 0, Qt.AlignVCenter)
        week_head.addWidget(self.edit_week_pct, 0, Qt.AlignVCenter)
        week_head.addWidget(self._week_unit, 0, Qt.AlignVCenter)
        progress_grid.addLayout(week_head, 3, 0, 1, 2)

        week_rows = _span_grid()
        self._lab_week_remain = _form_label("还剩")
        self._lab_week_date = _form_label("日期")
        week_rows.addWidget(self._lab_week_remain, 0, 0, Qt.AlignLeft | Qt.AlignVCenter)
        week_rows.addWidget(self.edit_week_days, 0, 1)
        week_rows.addWidget(self._week_day_unit, 0, 2, Qt.AlignCenter)
        week_rows.addWidget(self.edit_week_span, 0, 3)
        week_rows.addWidget(self._week_span_unit, 0, 4, Qt.AlignLeft | Qt.AlignVCenter)
        week_rows.addWidget(self.edit_week_span_min, 0, 5)
        week_rows.addWidget(self._week_span_min_unit, 0, 6, Qt.AlignLeft | Qt.AlignVCenter)
        week_rows.addWidget(self._lab_week_date, 1, 0, Qt.AlignLeft | Qt.AlignVCenter)
        week_rows.addWidget(self.edit_week_month, 1, 1)
        week_rows.addWidget(self._week_month_unit, 1, 2, Qt.AlignCenter)
        week_rows.addWidget(self.edit_week_dom, 1, 3)
        week_rows.addWidget(self._week_dom_unit, 1, 4, Qt.AlignLeft | Qt.AlignVCenter)
        week_rows.addWidget(self.lbl_week_day, 2, 0, Qt.AlignLeft | Qt.AlignVCenter)
        week_rows.addWidget(self.edit_week_hour, 2, 1)
        week_rows.addWidget(self._week_colon, 2, 2, Qt.AlignCenter)
        week_rows.addWidget(self.edit_week_min, 2, 3)
        progress_grid.addLayout(week_rows, 4, 0, 1, 2)

        self._points_box = QWidget()
        self._points_box.setObjectName("SubUsageForm")
        points_grid = QGridLayout(self._points_box)
        points_grid.setContentsMargins(0, 8, 0, 0)
        points_grid.setHorizontalSpacing(8)
        points_grid.setVerticalSpacing(8)
        points_grid.setColumnStretch(1, 1)
        _place_in(points_grid, 0, "积分", [(self.edit_points, 0)])
        _place_in(points_grid, 1, "总分", [(self.edit_monthly, 0)])
        _place_in(points_grid, 2, "重置", [(self.edit_renew, 0), (self._day_unit, 0)])
        _place_in(points_grid, 3, "还剩", [
            (self.edit_pts_days, 0), (self._pts_day_unit, 0),
            (self.edit_pts_span, 0), (self._pts_span_unit, 0),
        ])

        form_lay.addWidget(top)
        form_lay.addWidget(self._progress_box)
        form_lay.addWidget(self._points_box)
        side.addWidget(self._form)
        side.addStretch(1)

        self._editor_widgets = (
            self.edit_name, self.chk_window,
            self._meter_radios["progress"], self._meter_radios["points"],
            self.edit_window_pct,
            self.edit_win_days, self.edit_win_span, self.edit_win_min, self.edit_week_pct,
            self.lbl_win_day, self.edit_win_hour, self.edit_win_clock_min,
            self.edit_win_month, self.edit_win_dom,
            self.lbl_week_day, self.edit_week_hour, self.edit_week_min,
            self.edit_week_days, self.edit_week_span, self.edit_week_span_min,
            self.edit_week_month, self.edit_week_dom,
            self.edit_points, self.edit_monthly, self.edit_renew,
            self.edit_pts_days, self.edit_pts_span,
        )
        self.edit_name.editingFinished.connect(self._editor_name)
        self.edit_window_pct.editingFinished.connect(self._editor_window_pct)
        self.edit_win_days.editingFinished.connect(self._editor_win_span)
        self.edit_win_span.editingFinished.connect(self._editor_win_span)
        self.edit_win_min.editingFinished.connect(self._editor_win_span)
        self.edit_win_hour.editingFinished.connect(self._editor_win_time)
        self.edit_win_clock_min.editingFinished.connect(self._editor_win_time)
        self.edit_win_month.editingFinished.connect(self._editor_win_date)
        self.edit_win_dom.editingFinished.connect(self._editor_win_date)
        self.edit_week_pct.editingFinished.connect(self._editor_week_pct)
        self.edit_week_hour.editingFinished.connect(self._editor_week_time)
        self.edit_week_min.editingFinished.connect(self._editor_week_time)
        self.edit_week_days.editingFinished.connect(self._editor_week_span)
        self.edit_week_span.editingFinished.connect(self._editor_week_span)
        self.edit_week_span_min.editingFinished.connect(self._editor_week_span)
        self.edit_week_month.editingFinished.connect(self._editor_week_date)
        self.edit_week_dom.editingFinished.connect(self._editor_week_date)
        self.edit_points.editingFinished.connect(self._editor_points)
        self.edit_monthly.editingFinished.connect(self._editor_monthly)
        self.edit_renew.editingFinished.connect(self._editor_renew)
        self.edit_pts_days.editingFinished.connect(self._editor_pts_span)
        self.edit_pts_span.editingFinished.connect(self._editor_pts_span)
        self._restyle_editor()
        try:
            theme.changed.connect(self._restyle_editor)
        except Exception:
            pass
        root.addWidget(self._editor, 3)
        self._fill_editor(None)
        self._watch_commit(self)

    def _restyle_editor(self, *_args):
        restyle_card_frame(self._editor)
        restyle_card_title(self._editor_title)
        try:
            border = tk("border")
            muted = tk("text_mut")
            ink = tk("text")
        except Exception:
            border, muted = "#2a3965", "#94a3b8"
            ink = "#e8eaf0"
        for line in self._seps:
            line.setStyleSheet(
                f"#SubUsageSep{{background:{border};border:none;max-height:1px;}}"
            )
        for lab in self._form_labs:
            lab.setStyleSheet(
                f"color:{muted};font-size:12px;background:transparent;border:none;"
            )
        for unit in (
            self._pct_unit, self._week_unit, self._day_unit,
            self._win_day_unit, self._win_span_unit, self._win_min_unit,
            self._win_month_unit, self._win_dom_unit,
            self._week_day_unit, self._week_span_unit, self._week_span_min_unit,
            self._week_month_unit, self._week_dom_unit,
            self._pts_day_unit, self._pts_span_unit,
        ):
            unit.setStyleSheet(
                f"color:{muted};font-size:12px;background:transparent;border:none;"
            )
        self.lbl_week_day.setStyleSheet(
            f"color:{ink};font-size:13px;background:transparent;border:none;"
        )
        self._week_colon.setStyleSheet(
            f"color:{ink};font-size:13px;background:transparent;border:none;"
        )
        self._paint_display_button()
        self._paint_window_frozen(
            self._current is not None and not window_on(self._current.item)
        )
        self._match_reset_label_width()

    def _paint_display_button(self):
        """正向绿色，反向蓝色。盖过全局中按钮的灰底。"""
        if self._display == "reverse":
            bg, hover = "#3a8ee0", "#2f7cc8"
        else:
            bg, hover = "#22C55E", "#16A34A"
        self.btn_display.setStyleSheet(
            "QPushButton{"
            f"background:{bg};color:#ffffff;border:1px solid {hover};"
            "border-radius:7px;padding:0 12px;min-height:27px;max-height:27px;"
            "min-width:0px;font-size:13px;font-weight:600;"
            "}"
            f"QPushButton:hover{{background:{hover};color:#ffffff;}}"
            f"QPushButton:pressed{{background:{hover};color:#ffffff;}}"
            "QPushButton:disabled{color:rgba(255,255,255,0.55);"
            f"background:{bg};border-color:{hover};}}"
        )

    def _sync_usage_labels(self):
        """正向时百分比是已用，反向时是剩余。本周同一读法。"""
        word = _usage_word(self._display)
        self._lab_current.setText(word)
        self._lab_week.setText(word)

    def _watch_commit(self, widget):
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            focus = QApplication.focusWidget()
            editor = getattr(self, "_editor", None)
            if (
                isinstance(focus, QLineEdit)
                and editor is not None
                and editor.isAncestorOf(focus)
                and obj is not focus
                and not focus.isAncestorOf(obj)
            ):
                focus.clearFocus()
        return super().eventFilter(obj, event)

    def set_prefs_dirty_callback(self, callback):
        self._dirty_cb = callback

    def _dirty(self):
        if self._loading:
            return
        cb = self._dirty_cb
        if callable(cb):
            try:
                cb()
            except Exception:
                log.exception("订阅用量保存通知失败")

    def export_settings(self):
        return {
            "display": self._display_default,
            "items": [dict(card.item) for card in self._cards],
        }

    def apply_settings(self, data):
        items = []
        display = "forward"
        if isinstance(data, dict):
            if isinstance(data.get("items"), list):
                items = [dict(row) for row in data.get("items") if isinstance(row, dict)]
            raw = data.get("display") or "forward"
            if raw in ("forward", "reverse"):
                display = raw
        self._display_default = display
        self._loading = True
        try:
            self._paint_editor_dir(display)
            self._rebuild(items)
        finally:
            self._loading = False

    def _paint_editor_dir(self, display):
        """只改右边按钮和「已用 / 剩余」。不写进记录。"""
        if display not in ("forward", "reverse"):
            display = "forward"
        self._display = display
        self.btn_display.setText(_DIR_LABEL.get(display, "正向"))
        self._paint_display_button()
        self._sync_usage_labels()

    def _set_display(self, display):
        self._paint_editor_dir(display)
        if self._current is not None:
            self._fill_editor(self._current)

    def _toggle_display(self):
        """当前这条的正向或反向。其它记录保持自己的。"""
        card = self._current
        if card is None:
            return
        current = editor_dir(card.item, self._display_default)
        new = "reverse" if current == "forward" else "forward"
        card.item["editor_dir"] = new
        self._set_display(new)
        self._dirty()

    def shutdown(self):
        self._clear_drag_visual()
        try:
            self._timer.stop()
        except Exception:
            pass

    def _rebuild(self, items):
        keep = self._current.item.get("id") if self._current is not None else None
        self._current = None
        while self._cards:
            card = self._cards.pop()
            self._list.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        now = datetime.now()
        for raw in items:
            item = dict(raw)
            roll_item(item, now)
            self._append_card(item)
        chosen = None
        for card in self._cards:
            if keep is not None and card.item.get("id") == keep:
                chosen = card
                break
        if chosen is None and self._cards:
            chosen = self._cards[0]
        self._select(chosen)

    def _append_card(self, item):
        card = _RecordRow(item, self._host, display_dir=self._display)
        self._watch_commit(card)
        card.picked.connect(self._select)
        card.changed.connect(lambda c=card: self._after_card_edit(c))
        card.remove_requested.connect(self._remove_card)
        card.reorder_started.connect(self._reorder_begin)
        card.reorder_moved.connect(self._reorder_move)
        card.reorder_finished.connect(self._reorder_finish)
        self._cards.append(card)
        self._list.insertWidget(self._list.count() - 1, card)
        return card

    def _drop_index(self, gpos):
        """插入点：指针在某张卡中线以上就插到它前面。原卡仍留在槽里。"""
        y = gpos.y()
        for i, card in enumerate(self._cards):
            center = card.mapToGlobal(card.rect().center())
            if y < center.y():
                return i
        return len(self._cards)

    def _place_drop_guide(self, insert):
        guide = self._drag_guide
        cards = self._cards
        if guide is None or not cards:
            return
        if guide.parent() is not self._host:
            guide.setParent(self._host)
        card0 = cards[0]
        guide.setFixedWidth(max(40, card0.width()))
        spacing = self._list.spacing()
        half = guide.height() // 2
        if insert <= 0:
            anchor = cards[0]
            line_y = anchor.y() - spacing / 2.0
            x = anchor.x()
        elif insert >= len(cards):
            anchor = cards[-1]
            line_y = anchor.y() + anchor.height() + spacing / 2.0
            x = anchor.x()
        else:
            above = cards[insert - 1]
            below = cards[insert]
            line_y = (above.y() + above.height() + below.y()) / 2.0
            x = below.x()
        guide.move(int(x), max(0, int(round(line_y - half))))
        guide.show()
        guide.raise_()

    def _clear_drag_visual(self):
        card = getattr(self, "_drag_card", None)
        if card is not None:
            try:
                card.releaseMouse()
            except Exception:
                pass
            card.setCursor(Qt.OpenHandCursor)
        preview = getattr(self, "_drag_preview", None)
        self._drag_preview = None
        if preview is not None:
            preview.hide()
            preview.setParent(None)
            preview.deleteLater()
        guide = getattr(self, "_drag_guide", None)
        self._drag_guide = None
        if guide is not None:
            guide.hide()
            guide.setParent(None)
            guide.deleteLater()
        self._drag_card = None
        self._drag_grab = None
        self._drag_insert = None

    def _reorder_begin(self, card, gpos):
        if card not in self._cards:
            return
        if self._drag_card is card and self._drag_preview is not None:
            return
        self._clear_drag_visual()
        self._drag_card = card
        self._drag_grab = card.mapFromGlobal(gpos)
        self._drag_insert = self._cards.index(card)
        shot = card.grab()
        guide = _DropGuide(self._host)
        self._drag_guide = guide
        self._place_drop_guide(self._drag_insert)
        preview = _DragPreview(shot, self)
        self._drag_preview = preview
        origin = self.mapFromGlobal(card.mapToGlobal(QPoint(0, 0)))
        preview.move(origin)
        preview.show()
        preview.raise_()
        try:
            card.grabMouse()
        except Exception:
            pass

    def _reorder_move(self, card, gpos):
        if self._drag_card is not card:
            self._reorder_begin(card, gpos)
        preview = self._drag_preview
        if preview is not None:
            grab = self._drag_grab or QPoint(card.width() // 2, card.height() // 2)
            preview.move(self.mapFromGlobal(gpos) - grab)
            preview.show()
            preview.raise_()
        vp = self._scroll.viewport()
        local_y = vp.mapFromGlobal(gpos).y()
        bar = self._scroll.verticalScrollBar()
        if local_y < 28:
            bar.setValue(bar.value() - 16)
        elif local_y > vp.height() - 28:
            bar.setValue(bar.value() + 16)
        dest = self._drop_index(gpos)
        if dest != self._drag_insert:
            self._drag_insert = dest
            self._place_drop_guide(dest)

    def _reorder_finish(self, card):
        dest = self._drag_insert
        src = self._cards.index(card) if card in self._cards else -1
        self._clear_drag_visual()
        if src < 0 or dest is None:
            return
        if dest > src:
            dest -= 1
        dest = max(0, min(dest, len(self._cards) - 1))
        if dest == src:
            return
        self._cards.pop(src)
        self._cards.insert(dest, card)
        self._list.removeWidget(card)
        self._list.insertWidget(dest, card)
        self._dirty()

    def _add_blank(self):
        card = self._append_card(blank_item("未命名", "points"))
        self._select(card)
        self._dirty()

    def _select(self, card):
        if card is not None and card not in self._cards:
            card = None
        self._current = card
        for item in self._cards:
            item.set_selected(item is card)
        self._fill_editor(card)

    def _after_card_edit(self, card):
        self._dirty()
        if card is self._current:
            self._fill_editor(card)

    def _fill_editor(self, card):
        self._filling = True
        try:
            enabled = card is not None
            self._form.setVisible(enabled)
            self._empty.setVisible(not enabled)
            for widget in self._editor_widgets:
                widget.setEnabled(enabled)
            if card is None:
                self.edit_name.clear()
                self.edit_window_pct.clear()
                self.edit_win_days.clear()
                self.edit_win_span.clear()
                self.edit_win_min.clear()
                self.edit_win_month.clear()
                self.edit_win_dom.clear()
                self.edit_win_hour.clear()
                self.edit_win_clock_min.clear()
                self.lbl_win_day.setText("周一")
                self.edit_week_pct.clear()
                self.edit_week_hour.clear()
                self.edit_week_min.clear()
                self.edit_week_days.clear()
                self.edit_week_span.clear()
                self.edit_week_span_min.clear()
                self.edit_week_month.clear()
                self.edit_week_dom.clear()
                self.edit_points.clear()
                self.edit_monthly.clear()
                self.edit_renew.clear()
                self.edit_pts_days.clear()
                self.edit_pts_span.clear()
                self.chk_window.blockSignals(True)
                self.chk_window.setChecked(True)
                self.chk_window.blockSignals(False)
                self.lbl_week_day.setText("周一")
                self._sync_mode_panels(None)
                self._sync_meter_radios(None)
                self._paint_editor_dir(self._display_default)
                return
            if not self.edit_name.hasFocus():
                self.edit_name.setText(card.item.get("name") or "")
            direction = record_dir(card.item)
            shown_dir = editor_dir(card.item, self._display_default)
            self._paint_editor_dir(shown_dir)
            sense = _editor_sense(shown_dir)
            window_shown = as_display(
                card.item.get("window_remain", full_mark(direction)),
                direction,
                sense,
            )
            week_shown = as_display(
                card.item.get("week_remain", full_mark(direction)),
                direction,
                sense,
            )
            if not self.edit_window_pct.hasFocus():
                self.edit_window_pct.setText(str(window_shown))
            now = datetime.now()
            reset = _parse_dt(card.item.get("window_reset"))
            self._fill_window_span(reset, now)
            self._fill_window_date(reset)
            self._fill_window_clock(reset)
            self.lbl_win_day.setText(_WEEKDAYS[reset.weekday()] if reset is not None else "周一")
            if not self.edit_week_pct.hasFocus():
                self.edit_week_pct.setText(str(week_shown))
            self._fill_week_clock(card.item)
            week_reset = _parse_dt(card.item.get("week_reset"))
            if week_reset is not None:
                weekday = week_reset.weekday()
            else:
                try:
                    weekday = int(card.item.get("week_weekday") or 0) % 7
                except (TypeError, ValueError):
                    weekday = 0
            self.lbl_week_day.setText(_WEEKDAYS[weekday])
            self._fill_week_span(week_reset, now)
            self._fill_week_date(week_reset)
            if not self.edit_points.hasFocus():
                self.edit_points.setText(str(_points_left(card.item)))
            if not self.edit_monthly.hasFocus():
                self.edit_monthly.setText(str(_points_total(card.item) or ""))
            if not self.edit_renew.hasFocus():
                self.edit_renew.setText(str(_renew_day(card.item)))
            points_reset = _parse_dt(card.item.get("points_reset"))
            self._fill_span(points_reset, self.edit_pts_days, self.edit_pts_span, now)
            self._sync_mode_panels(card)
            self._sync_meter_radios(card)
        finally:
            self._filling = False

    def _fill_week_date(self, reset):
        if self.edit_week_month.hasFocus() or self.edit_week_dom.hasFocus():
            return
        if reset is None:
            self.edit_week_month.clear()
            self.edit_week_dom.clear()
            return
        self.edit_week_month.setText(str(reset.month))
        self.edit_week_dom.setText(str(reset.day))

    def _editor_week_date(self):
        """周重置的月、日两个框。时刻沿用原来的，周几跟着这一天。没改不保存。"""
        card = self._editor_card()
        if card is None:
            return
        existing = _parse_dt(card.item.get("week_reset"))
        month_text = (self.edit_week_month.text() or "").strip()
        day_text = (self.edit_week_dom.text() or "").strip()
        if not month_text and not day_text and existing is None:
            return
        hour, minute = _parse_hhmm(card.item.get("week_time") or "08:00")
        try:
            month = int(month_text)
            day = int(day_text)
        except ValueError:
            self._fill_editor(card)
            return
        typed = _compose_month_day(month, day, hour, minute, existing, datetime.now())
        if typed is None:
            self._fill_editor(card)
            return
        try:
            stored_weekday = int(card.item.get("week_weekday"))
        except (TypeError, ValueError):
            stored_weekday = -1
        same = (
            existing is not None
            and _fmt_dt(existing) == _fmt_dt(typed)
            and stored_weekday % 7 == typed.weekday()
        )
        if same:
            return
        card.item["week_reset"] = _fmt_dt(typed)
        card.item["week_weekday"] = typed.weekday()
        card.sync_from_item()
        self._fill_editor(card)
        self._dirty()

    def _fill_span(self, reset, day_edit, hour_edit, now):
        days, hours = _split_span(reset, now)
        if not day_edit.hasFocus():
            day_edit.setText(str(days))
        if not hour_edit.hasFocus():
            hour_edit.setText(str(hours))

    def _commit_span(self, card, key, day_edit, hour_edit):
        days, hours = _read_pair(day_edit.text(), hour_edit.text())
        now = datetime.now()
        reset = _parse_dt(card.item.get(key))
        if (days, hours) == _split_span(reset, now):
            return None
        if days == 0 and hours == 0 and reset is None:
            return None
        when = _span_reset(now, days, hours)
        card.item[key] = _fmt_dt(when)
        return when

    def _fill_window_span(self, reset, now):
        days, hours, minutes = _split_span_min(reset, now)
        if not self.edit_win_days.hasFocus():
            self.edit_win_days.setText(str(days))
        if not self.edit_win_span.hasFocus():
            self.edit_win_span.setText(str(hours))
        if not self.edit_win_min.hasFocus():
            self.edit_win_min.setText(str(minutes))

    def _commit_window_span(self, card):
        """只改日重置的到点时刻，不动 window_hours。没改返回空。"""
        days, hours, minutes = _read_span3(
            self.edit_win_days.text(), self.edit_win_span.text(), self.edit_win_min.text(),
        )
        now = datetime.now()
        reset = _parse_dt(card.item.get("window_reset"))
        if (days, hours, minutes) == _split_span_min(reset, now):
            return None
        if (days, hours, minutes) == (0, 0, 0) and reset is None:
            return None
        when = _span_reset_min(now, days, hours, minutes)
        card.item["window_reset"] = _fmt_dt(when)
        return when

    def _editor_win_span(self):
        card = self._editor_card()
        if card is None:
            return
        if self._commit_window_span(card) is None:
            return
        card.sync_from_item()
        self._fill_editor(card)
        self._dirty()

    def _fill_window_date(self, reset):
        if self.edit_win_month.hasFocus() or self.edit_win_dom.hasFocus():
            return
        if reset is None:
            self.edit_win_month.clear()
            self.edit_win_dom.clear()
            return
        self.edit_win_month.setText(str(reset.month))
        self.edit_win_dom.setText(str(reset.day))

    def _fill_window_clock(self, reset):
        if reset is None:
            if not self.edit_win_hour.hasFocus():
                self.edit_win_hour.clear()
            if not self.edit_win_clock_min.hasFocus():
                self.edit_win_clock_min.clear()
            return
        if not self.edit_win_hour.hasFocus():
            self.edit_win_hour.setText(str(reset.hour))
        if not self.edit_win_clock_min.hasFocus():
            self.edit_win_clock_min.setText(str(reset.minute))

    def _editor_win_date(self):
        """日重置的月、日。时刻沿用原来的到点时间。没改不保存。"""
        card = self._editor_card()
        if card is None:
            return
        existing = _parse_dt(card.item.get("window_reset"))
        month_text = (self.edit_win_month.text() or "").strip()
        day_text = (self.edit_win_dom.text() or "").strip()
        if not month_text and not day_text and existing is None:
            return
        if existing is not None:
            hour, minute = existing.hour, existing.minute
        else:
            hour, minute = _parse_hhmm(
                "%s:%s" % (
                    (self.edit_win_hour.text() or "").strip(),
                    (self.edit_win_clock_min.text() or "").strip(),
                ),
            )
        try:
            month = int(month_text)
            day = int(day_text)
        except ValueError:
            self._fill_editor(card)
            return
        typed = _compose_month_day(month, day, hour, minute, existing, datetime.now())
        if typed is None:
            self._fill_editor(card)
            return
        if existing is not None and _fmt_dt(existing) == _fmt_dt(typed):
            return
        card.item["window_reset"] = _fmt_dt(typed)
        card.sync_from_item()
        self._fill_editor(card)
        self._dirty()

    def _editor_win_time(self):
        """时、分两个框。只改已选日期上的时刻。没改不保存。"""
        card = self._editor_card()
        if card is None:
            return
        parsed = _parse_hhmm(
            "%s:%s" % (
                (self.edit_win_hour.text() or "").strip(),
                (self.edit_win_clock_min.text() or "").strip(),
            ),
            fallback=(-1, -1),
        )
        if parsed == (-1, -1):
            self._fill_editor(card)
            return
        existing = _parse_dt(card.item.get("window_reset"))
        if existing is None:
            try:
                month = int((self.edit_win_month.text() or "").strip())
                day = int((self.edit_win_dom.text() or "").strip())
            except ValueError:
                self._fill_editor(card)
                return
            candidate = _compose_month_day(
                month, day, parsed[0], parsed[1], None, datetime.now(),
            )
            if candidate is None:
                self._fill_editor(card)
                return
        else:
            candidate = existing.replace(hour=parsed[0], minute=parsed[1], second=0, microsecond=0)
        if existing is not None and _fmt_dt(existing) == _fmt_dt(candidate):
            return
        card.item["window_reset"] = _fmt_dt(candidate)
        card.sync_from_item()
        self._fill_editor(card)
        self._dirty()

    def _fill_week_span(self, reset, now):
        days, hours, minutes = _split_span_min(reset, now)
        if not self.edit_week_days.hasFocus():
            self.edit_week_days.setText(str(days))
        if not self.edit_week_span.hasFocus():
            self.edit_week_span.setText(str(hours))
        if not self.edit_week_span_min.hasFocus():
            self.edit_week_span_min.setText(str(minutes))

    def _commit_week_span(self, card):
        """只改周重置的到点时刻。没改返回空，不推到下一个周几。"""
        days, hours, minutes = _read_span3(
            self.edit_week_days.text(),
            self.edit_week_span.text(),
            self.edit_week_span_min.text(),
        )
        now = datetime.now()
        reset = _parse_dt(card.item.get("week_reset"))
        if (days, hours, minutes) == _split_span_min(reset, now):
            return None
        if (days, hours, minutes) == (0, 0, 0) and reset is None:
            return None
        when = _span_reset_min(now, days, hours, minutes)
        card.item["week_reset"] = _fmt_dt(when)
        return when

    def _editor_week_span(self):
        card = self._editor_card()
        if card is None:
            return
        when = self._commit_week_span(card)
        if when is None:
            return
        card.item["week_weekday"] = when.weekday()
        card.item["week_time"] = when.strftime("%H:%M")
        card.sync_from_item()
        self._fill_editor(card)
        self._dirty()

    def _editor_pts_span(self):
        card = self._editor_card()
        if card is None:
            return
        when = self._commit_span(card, "points_reset", self.edit_pts_days, self.edit_pts_span)
        if when is None:
            return
        card.item["renew_day"] = when.day
        card.sync_from_item()
        self._fill_editor(card)
        self._dirty()

    def _editor_card(self):
        if self._filling or self._current is None or self._current not in self._cards:
            return None
        return self._current

    def _editor_name(self):
        card = self._editor_card()
        if card is None:
            return
        card.item["name"] = self.edit_name.text().strip()
        card.sync_from_item()
        self._dirty()

    def _editor_window_on(self, checked):
        card = self._editor_card()
        if card is None:
            return
        checked = bool(checked)
        if window_on(card.item) == checked:
            return
        card.item["window_on"] = checked
        card.sync_from_item()
        self._fill_editor(card)
        self._dirty()

    def _sync_mode_panels(self, card):
        points = card is not None and meter_of(card.item) == "points"
        self._progress_box.setVisible(card is not None and not points)
        self._points_box.setVisible(points)
        opened = card is None or window_on(card.item)
        self.chk_window.blockSignals(True)
        self.chk_window.setChecked(opened)
        self.chk_window.blockSignals(False)
        # 关掉只是不能改、也不滚动。行还在，高度不变，文字改成深灰。
        # 勾选留在外面，始终可点。已用和百分比与还剩一起冻住。
        self._window_box.setVisible(True)
        frozen = card is not None and not opened
        alive = card is not None and not frozen
        self.chk_window.setEnabled(card is not None)
        self._window_box.setEnabled(alive)
        self.edit_window_pct.setEnabled(alive)
        self._paint_window_frozen(frozen)

    def _paint_window_frozen(self, frozen):
        """日重置关掉时，这一段的字是深灰，输入框也不能改。勾选本身不灰。"""
        try:
            muted = tk("text_mut")
        except Exception:
            muted = "#94a3b8"
        color = _FROZEN_INK if frozen else muted
        label_sheet = (
            f"QLabel{{color:{color};font-size:12px;background:transparent;border:none;}}"
            f"QLabel:disabled{{color:{color};background:transparent;border:none;}}"
        )
        skip = {self.lbl_win_day, self._win_colon}
        labels = [
            lab for lab in self._window_box.findChildren(QLabel) if lab not in skip
        ]
        labels.extend((self._lab_current, self._pct_unit))
        for lab in labels:
            lab.setStyleSheet(label_sheet)
        try:
            ink = tk("text")
        except Exception:
            ink = "#e8eaf0"
        value_color = _FROZEN_INK if frozen else ink
        value_sheet = (
            f"QLabel{{color:{value_color};font-size:13px;background:transparent;border:none;}}"
            f"QLabel:disabled{{color:{value_color};background:transparent;border:none;}}"
        )
        self.lbl_win_day.setStyleSheet(value_sheet)
        self._win_colon.setStyleSheet(value_sheet)
        if frozen:
            sheet = (
                "QLineEdit,QLineEdit:disabled{"
                "background:transparent;color:%s;border:1px solid #2a3348;"
                "border-radius:6px;padding:4px 2px;font-size:13px;}"
                % _FROZEN_INK
            )
        else:
            sheet = _PROGRESS_EDIT_QSS
        daily = {
            self.edit_window_pct,
            self.edit_win_days, self.edit_win_span, self.edit_win_min,
            self.edit_win_month, self.edit_win_dom,
            self.edit_win_hour, self.edit_win_clock_min,
        }
        week = (
            self.edit_week_pct, self.edit_week_days, self.edit_week_span,
            self.edit_week_span_min, self.edit_week_month, self.edit_week_dom,
            self.edit_week_hour, self.edit_week_min,
        )
        for edit in daily:
            edit.setStyleSheet(sheet)
        for edit in week:
            edit.setStyleSheet(_PROGRESS_EDIT_QSS)
        self._paint_percent_inks()

    def _paint_percent_inks(self):
        """编辑区的百分比按还剩多少上色，和色条同一档。"""
        card = self._current
        if card is None:
            return
        direction = record_dir(card.item)
        frozen = not window_on(card.item)

        def remain(key):
            return as_display(
                card.item.get(key, full_mark(direction)),
                direction,
                "forward",
            )

        self._tint_percent_edit(self.edit_week_pct, remain("week_remain"), False)
        self._tint_percent_edit(self.edit_window_pct, remain("window_remain"), frozen)
        self._tint_percent_label(self._week_unit, remain("week_remain"))
        self._tint_percent_label(self._pct_unit, remain("window_remain"))

    def _tint_percent_edit(self, edit, value, frozen):
        color = _state_color(value).name()
        if frozen:
            edit.setStyleSheet(
                "QLineEdit,QLineEdit:disabled{"
                "background:transparent;color:%s;border:1px solid #2a3348;"
                "border-radius:6px;padding:4px 2px;font-size:13px;}"
                % color
            )
            return
        edit.setStyleSheet(
            "QLineEdit{background:#0e1530;color:%s;border:1px solid #2a3965;"
            "border-radius:6px;padding:4px 2px;font-size:13px;"
            "selection-background-color:#1f3a8a;}"
            "QLineEdit:focus{border-color:#3b82f6;}"
            % color
        )

    def _tint_percent_label(self, label, value):
        color = _state_color(value).name()
        label.setStyleSheet(
            "QLabel{color:%s;font-size:12px;background:transparent;border:none;}"
            "QLabel:disabled{color:%s;background:transparent;border:none;}"
            % (color, color)
        )

    def _match_reset_label_width(self):
        """日重置、周重置左边的字一样宽，下面的输入框才能上下对齐。"""
        labs = (
            self._lab_day_remain, self._lab_day_date, self.lbl_win_day,
            self._lab_week_remain, self._lab_week_date, self.lbl_week_day,
        )
        for lab in labs:
            lab.setMinimumWidth(0)
            lab.setMaximumWidth(16777215)
        width = 0
        for lab in labs:
            width = max(width, lab.sizeHint().width())
        if width <= 0:
            return
        for lab in labs:
            lab.setFixedWidth(width)

    def _sync_meter_radios(self, card):
        mode = meter_of(card.item) if card is not None else "progress"
        radio = self._meter_radios.get(mode)
        if radio is None:
            return
        self._meter_group.blockSignals(True)
        radio.setChecked(True)
        self._meter_group.blockSignals(False)

    def _on_meter_radio(self, *_args):
        checked = self._meter_group.checkedButton()
        mode = "points" if checked is self._meter_radios["points"] else "progress"
        self._set_meter(mode)

    def _set_meter(self, mode):
        card = self._editor_card()
        if card is None:
            return
        if mode not in ("progress", "points"):
            mode = "progress"
        if meter_of(card.item) == mode:
            return
        card.item["meter"] = mode
        if mode == "points" and "points_remain" not in card.item:
            card.item["points_remain"] = full_mark(record_dir(card.item))
        card.sync_from_item()
        self._fill_editor(card)
        self._dirty()

    def _sync_points_remain(self, card):
        """用剩余积分重算色条。方向翻转不要调用，否则会盖掉对调后的百分比。"""
        item = card.item
        total = _points_total(item)
        left = _points_left(item)
        if total > 0 and left > total:
            left = total
            item["points_left"] = left
        if total > 0:
            shown = int(round(min(left, total) * 100.0 / total))
        else:
            shown = 100 if left > 0 else 0
        item["points_remain"] = as_stored(shown, record_dir(item), "forward")

    def _editor_points(self):
        card = self._editor_card()
        if card is None:
            return
        try:
            left = int((self.edit_points.text() or "0").strip() or "0")
        except ValueError:
            left = 0
        left = max(0, left)
        total = _points_total(card.item)
        if total > 0:
            left = min(left, total)
        card.item["points_left"] = left
        self._sync_points_remain(card)
        card.sync_from_item()
        self._fill_editor(card)
        self._dirty()

    def _editor_monthly(self):
        card = self._editor_card()
        if card is None:
            return
        try:
            total = int((self.edit_monthly.text() or "0").strip() or "0")
        except ValueError:
            total = 0
        card.item["monthly_points"] = max(0, total)
        self._sync_points_remain(card)
        card.sync_from_item()
        self._fill_editor(card)
        self._dirty()

    def _editor_renew(self):
        card = self._editor_card()
        if card is None:
            return
        try:
            day = int((self.edit_renew.text() or "1").strip() or "1")
        except ValueError:
            day = 1
        card.item["renew_day"] = min(31, max(1, day))
        preset = _parse_dt(card.item.get("points_reset"))
        if preset is not None:
            moved = _on_month_day(preset, card.item["renew_day"])
            card.item["points_reset"] = _fmt_dt(moved)
        card.sync_from_item()
        self._fill_editor(card)
        self._dirty()

    def _editor_window_pct(self):
        card = self._editor_card()
        if card is None:
            return
        try:
            shown = int((self.edit_window_pct.text() or "0").strip() or "0")
        except ValueError:
            shown = 0
        card.item["window_remain"] = as_stored(
            shown, record_dir(card.item), _editor_sense(editor_dir(card.item, self._display_default)),
        )
        card.sync_from_item()
        self._fill_editor(card)
        self._dirty()

    def _editor_week_pct(self):
        card = self._editor_card()
        if card is None:
            return
        try:
            shown = int((self.edit_week_pct.text() or "0").strip() or "0")
        except ValueError:
            shown = 0
        card.item["week_remain"] = as_stored(
            shown, record_dir(card.item), _editor_sense(editor_dir(card.item, self._display_default)),
        )
        card.sync_from_item()
        self._fill_editor(card)
        self._dirty()

    def _fill_week_clock(self, item):
        hour, minute = _parse_hhmm((item or {}).get("week_time") or "08:00")
        if not self.edit_week_hour.hasFocus():
            self.edit_week_hour.setText(str(hour))
        if not self.edit_week_min.hasFocus():
            self.edit_week_min.setText(str(minute))

    def _editor_week_time(self):
        """时、分两个框。只改已选日期上的时刻，不把日期拉回最近的那个周几。没改不保存。"""
        card = self._editor_card()
        if card is None:
            return
        parsed = _parse_hhmm(
            f"{(self.edit_week_hour.text() or '').strip()}:{(self.edit_week_min.text() or '').strip()}",
            fallback=(-1, -1),
        )
        if parsed == (-1, -1):
            self._fill_editor(card)
            return
        new_time = "%02d:%02d" % parsed
        existing = _parse_dt(card.item.get("week_reset"))
        if existing is None:
            try:
                month = int((self.edit_week_month.text() or "").strip())
                day = int((self.edit_week_dom.text() or "").strip())
            except ValueError:
                self._fill_editor(card)
                return
            candidate = _compose_month_day(
                month, day, parsed[0], parsed[1], None, datetime.now(),
            )
            if candidate is None:
                self._fill_editor(card)
                return
        else:
            candidate = existing.replace(hour=parsed[0], minute=parsed[1], second=0, microsecond=0)
        same = (
            (card.item.get("week_time") or "") == new_time
            and existing is not None
            and _fmt_dt(existing) == _fmt_dt(candidate)
        )
        if same:
            return
        card.item["week_time"] = new_time
        card.item["week_reset"] = _fmt_dt(candidate)
        card.item["week_weekday"] = candidate.weekday()
        card.sync_from_item()
        self._fill_editor(card)
        self._dirty()

    def _remove_card(self, card):
        QTimer.singleShot(0, lambda c=card: self._confirm_remove(c))

    def _ask_remove(self, name):
        box = QMessageBox(self.window() or self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("删除记录")
        box.setText("确定删掉「%s」吗？" % name)
        ok = box.addButton("确定", QMessageBox.AcceptRole)
        cancel = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(cancel)
        box.exec_()
        return box.clickedButton() is ok

    def _confirm_remove(self, card):
        if card not in self._cards:
            return
        name = (card.item.get("name") or "").strip() or "这条记录"
        if not self._ask_remove(name):
            return
        index = self._cards.index(card) if card in self._cards else -1
        if index >= 0:
            self._cards.pop(index)
        self._list.removeWidget(card)
        card.setParent(None)
        card.deleteLater()
        neighbor = None
        if self._cards:
            neighbor = self._cards[min(index, len(self._cards) - 1)]
        if self._current is card:
            self._current = None
            self._select(neighbor)
        self._dirty()

    def _on_tick(self):
        now = datetime.now()
        dirty = False
        for card in list(self._cards):
            before = (
                card.item.get("record_dir"),
                card.item.get("window_reset"),
                card.item.get("window_remain"),
                card.item.get("week_reset"),
                card.item.get("week_remain"),
                card.item.get("points_reset"),
                card.item.get("points_remain"),
                card.item.get("points_left"),
            )
            roll_item(card.item, now)
            after = (
                card.item.get("record_dir"),
                card.item.get("window_reset"),
                card.item.get("window_remain"),
                card.item.get("week_reset"),
                card.item.get("week_remain"),
                card.item.get("points_reset"),
                card.item.get("points_remain"),
                card.item.get("points_left"),
            )
            if after != before:
                dirty = True
                card.sync_from_item()
            else:
                card.refresh_captions(now)
        if self._current is not None:
            self._fill_editor(self._current)
        if dirty:
            self._dirty()
