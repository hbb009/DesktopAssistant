# pages/page_overview.py

from PyQt5.QtCore import Qt, QTimer, QSize, QEvent
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,
    QGroupBox, QScrollArea, QFrame, QSizePolicy,
)
import platform, shutil, sys, subprocess
from styles.style_all import (
    install_card_title,
    make_card,
    make_grid_row,
    restyle_card_frame,
    restyle_card_title,
    content_primary_color,
    content_secondary_color,
    CARD_TOP_GAP,
    CARD_LEFT_GAP,
    CARD_RIGHT_GAP,
    CARD_BOTTOM_GAP,
    theme,
)
from pages.settings_section import SettingsSection

# 统一行高（像素）：环境信息 / 资源监控 两栏共用，逐行对齐。
# 相对旧值 26 加高 2px（行距加大）；字号比全局正文 14px 小一码。
ROW_H = 26
OVERVIEW_ROW_H = ROW_H + 2
OVERVIEW_FONT_PX = 13


class _ShrinkLabel(QLabel):
    """单行文字：宽度可压到 0，超出用省略号。避免把总览两列最小宽度顶爆。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = text or ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setWordWrap(False)
        self._elide()

    def setText(self, text):
        self._full = text or ""
        self._elide()

    def _elide(self):
        fm = self.fontMetrics()
        super().setText(fm.elidedText(self._full, Qt.ElideRight, max(0, self.width())))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def showEvent(self, e):
        super().showEvent(e)
        self._elide()


class _OverviewScrollHost(QWidget):
    """总览滚动内容：横向最小宽度为 0，避免 QScrollArea 按子控件 sizeHint 撑出窗口。"""

    def minimumSizeHint(self):
        s = super().minimumSizeHint()
        return QSize(0, s.height())

    def sizeHint(self):
        s = super().sizeHint()
        return QSize(0, s.height())


class _EqualWidthRow(QWidget):
    """整页两列容器：始终按当前宽度 1:1 切，且不把最小宽度锁死。

    QHBoxLayout 的 stretch 只分配「超出 sizeHint 的剩余空间」，窄窗时两列
    会按 sizeHint 比例偏一边。旧实现用 setFixedWidth 强制对半，但 setFixedWidth
    会把 min=max 锁在当次宽度上——窗口从大缩到最小时列宽收不回去，右侧（含
    「重置」）被滚动区裁掉。这里改成 setGeometry，min/max 保持可收缩。
    """

    def __init__(self, spacing: int = 8, parent=None):
        super().__init__(parent)
        self._spacing = int(spacing)
        self._left = None
        self._right = None
        self._placing = False
        self.setMinimumWidth(0)
        pol = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        pol.setHeightForWidth(True)
        self.setSizePolicy(pol)

    def set_columns(self, left: QWidget, right: QWidget):
        self._left = left
        self._right = right
        for w in (left, right):
            w.setParent(self)
            w.setMinimumWidth(0)
            w.setMaximumWidth(16777215)
            w.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Maximum)
            w.show()
        self.updateGeometry()
        self._place()

    def minimumSizeHint(self):
        return QSize(0, self._needed_height(max(1, self.width())))

    def sizeHint(self):
        return QSize(0, self._needed_height(max(1, self.width())))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._needed_height(w)

    def _half(self, total_w):
        return max(1, (max(0, int(total_w)) - self._spacing) // 2)

    def _col_h(self, col, half):
        if col is None:
            return 0
        if col.hasHeightForWidth():
            return max(0, int(col.heightForWidth(half)))
        sh = col.sizeHint()
        mh = col.minimumSizeHint()
        return max(int(sh.height()), int(mh.height()), int(col.minimumHeight()), 1)

    def _needed_height(self, total_w):
        half = self._half(total_w)
        return max(self._col_h(self._left, half), self._col_h(self._right, half), 1)

    def event(self, e):
        if e.type() == QEvent.LayoutRequest and not self._placing:
            self.updateGeometry()
        return super().event(e)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place()

    def _place(self):
        if self._left is None or self._right is None or self._placing:
            return
        w = self.width()
        if w <= 0:
            return
        self._placing = True
        try:
            half = self._half(w)
            h = max(self.height(), 1)
            self._left.setGeometry(0, 0, half, h)
            self._right.setGeometry(half + self._spacing, 0, w - half - self._spacing, h)
        finally:
            self._placing = False

# 资源监控进度条：已达段颜色 / 未达轨道（浅色）
_BAR_CHUNK = {
    "BarGpu": "#30c86b",
    "BarVram": "#3aa0ff",
    "BarMem": "#f0a542",
    "BarCpu": "#9aa0ac",
    "BarTemp": "#f07f3c",
    "BarPower": "#9b59b6",
    "BarDisk": "#ff6b6b",
}


def _meter_track_color() -> str:
    """进度条未达部分（浅色轨道）。"""
    return "#4a5575" if theme.is_dark else "#e8edf5"


def _apply_meter_bar_style(bar: QProgressBar, chunk: str) -> None:
    """内联刷进度条：半粗 4px + 浅色轨道 + 无外框 + 彩色已达段。

    不用只依赖全局 QSS：行容器若曾用无选择器透明背景，会冲掉轨道底色。
    """
    track = _meter_track_color()
    bar.setStyleSheet(
        f"QProgressBar{{"
        f"background:{track};border:none;border-radius:2px;"
        f"min-height:4px;max-height:4px;height:4px;"
        f"padding:0;text-align:center;color:transparent;}}"
        f"QProgressBar::chunk{{background:{chunk};border-radius:2px;}}"
    )


_IS_WIN = sys.platform.startswith("win")


# ══════════════ Windows 集成显卡利用率（PDH 性能计数器） ══════════════
# 说明：nvidia-smi 只适用于 NVIDIA 独显。笔记本常见的 Intel/AMD 集显读不到数据，
# 会导致“资源监控”里 GPU 相关几项全空。这里用 Windows 自带的性能计数器
# “\GPU Engine(*)\Utilization Percentage” 读取任意厂商 GPU 的利用率作为兜底。
# 全程 try/except 包裹，任何异常都安全退回 None，绝不影响主界面刷新。
if _IS_WIN:
    import ctypes
    from ctypes import wintypes

    class _PDH_UNION(ctypes.Union):
        _fields_ = [
            ("longValue", ctypes.c_long),
            ("doubleValue", ctypes.c_double),
            ("largeValue", ctypes.c_longlong),
            ("AnsiStringValue", ctypes.c_char_p),
            ("WideStringValue", ctypes.c_wchar_p),
        ]

    class _PDH_FMT_COUNTERVALUE(ctypes.Structure):
        _fields_ = [("CStatus", wintypes.DWORD), ("value", _PDH_UNION)]

    class _PDH_FMT_COUNTERVALUE_ITEM_W(ctypes.Structure):
        _fields_ = [("szName", ctypes.c_wchar_p),
                    ("FmtValue", _PDH_FMT_COUNTERVALUE)]

    _PDH_FMT_DOUBLE = 0x00000200

    class _PdhGpu:
        """读取整机 GPU 利用率（取各引擎实例的最大值，近似任务管理器口径）。"""
        def __init__(self):
            self._ok = False
            self.hq = None
            try:
                self.pdh = ctypes.WinDLL("pdh")
                self.hq = wintypes.HANDLE()
                if self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.hq)) != 0:
                    return
                self.counter = wintypes.HANDLE()
                path = r"\GPU Engine(*)\Utilization Percentage"
                if self.pdh.PdhAddEnglishCounterW(
                        self.hq, path, 0, ctypes.byref(self.counter)) != 0:
                    return
                # 首次采样（计数器需要两次采样才能计算）
                self.pdh.PdhCollectQueryData(self.hq)
                self._ok = True
            except Exception:
                self._ok = False

        def read(self):
            if not self._ok:
                return None
            try:
                if self.pdh.PdhCollectQueryData(self.hq) != 0:
                    return None
                size = wintypes.DWORD(0)
                count = wintypes.DWORD(0)
                # 第一次调用取所需缓冲区大小
                self.pdh.PdhGetFormattedCounterArrayW(
                    self.counter, _PDH_FMT_DOUBLE,
                    ctypes.byref(size), ctypes.byref(count), None)
                if size.value == 0 or count.value == 0:
                    return None
                buf = ctypes.create_string_buffer(size.value)
                if self.pdh.PdhGetFormattedCounterArrayW(
                        self.counter, _PDH_FMT_DOUBLE,
                        ctypes.byref(size), ctypes.byref(count), buf) != 0:
                    return None
                item_sz = ctypes.sizeof(_PDH_FMT_COUNTERVALUE_ITEM_W)
                # 防越界：以缓冲区实际容量为准
                safe_n = min(count.value, size.value // item_sz)
                best = 0.0
                for i in range(safe_n):
                    item = _PDH_FMT_COUNTERVALUE_ITEM_W.from_buffer(buf, i * item_sz)
                    v = item.FmtValue.value.doubleValue
                    if v == v and v > best:   # 过滤 NaN
                        best = v
                return max(0, min(100, int(round(best))))
            except Exception:
                return None


def _detect_gpu_name():
    """返回主显卡名称（用于占位提示），失败返回空串。仅 Windows。"""
    if not _IS_WIN:
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        class DISPLAY_DEVICEW(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128),
                ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128),
                ("DeviceKey", wintypes.WCHAR * 128),
            ]
        i = 0
        names = []
        while True:
            dd = DISPLAY_DEVICEW()
            dd.cb = ctypes.sizeof(dd)
            if not ctypes.windll.user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
                break
            s = dd.DeviceString.strip()
            if s and s not in names:
                names.append(s)
            i += 1
            if i > 16:
                break
        return names[0] if names else ""
    except Exception:
        return ""


def _detect_cpu_name():
    """从注册表读取友好的 CPU 名称（比 platform.processor() 更可读）。仅 Windows。"""
    if not _IS_WIN:
        return ""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        val, _ = winreg.QueryValueEx(key, "ProcessorNameString")
        winreg.CloseKey(key)
        return (val or "").strip()
    except Exception:
        return ""

try:
    import psutil  # 可选
except Exception:
    psutil = None

class PageOverview(QWidget):
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 原教程区改为设置卡后内容变高，整体包一层滚动区
        scroll = QScrollArea(self)
        scroll.setObjectName("OverviewScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setAttribute(Qt.WA_StyledBackground, True)
        scroll.setStyleSheet(
            "QScrollArea#OverviewScroll{background:transparent;border:none;}"
            "QScrollArea#OverviewScroll > QWidget > QWidget{background:transparent;}"
        )
        try:
            scroll.viewport().setAutoFillBackground(False)
            scroll.viewport().setStyleSheet("background:transparent;")
        except Exception:
            pass
        outer.addWidget(scroll)

        host = _OverviewScrollHost()
        host.setObjectName("OverviewScrollHost")
        host.setMinimumWidth(0)
        host.setAttribute(Qt.WA_StyledBackground, True)
        host.setStyleSheet("#OverviewScrollHost{background:transparent;border:none;}")
        scroll.setWidget(host)

        # 主内容区 ContentRoot 已有约 18px 内边距；这里若再套 12px，
        # 左右会叠出约「两个汉字」宽的空白。总览贴齐主区，只保留卡片之间的间距。
        root = QVBoxLayout(host)
        root.setContentsMargins(0, 0, 0, 8)
        root.setSpacing(8)
        root.setAlignment(Qt.AlignTop)

        # 整页真正两列：左=环境信息+功能与说明，右=资源监控+设置卡。
        # 同一列共享宽度；_EqualWidthRow 在任意窗口宽度下都强制 1:1。
        # 原「配置」页内容接在这两列下面一行。
        left_wrap = QWidget()
        left_wrap.setObjectName("OverviewLeftCol")
        left_wrap.setMinimumWidth(0)
        left_col = QVBoxLayout(left_wrap)
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(8)
        left_col.setAlignment(Qt.AlignTop)

        right_wrap = QWidget()
        right_wrap.setObjectName("OverviewRightCol")
        right_wrap.setMinimumWidth(0)
        right_col = QVBoxLayout(right_wrap)
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(8)
        right_col.setAlignment(Qt.AlignTop)

        cols_row = _EqualWidthRow(spacing=8)
        cols_row.set_columns(left_wrap, right_wrap)
        root.addWidget(cols_row)

        def _apply_new_title(box: QGroupBox):
            box.setProperty("titleClass", "newTitle1")   # 让 QSS 命中“新标题1”
            box.style().unpolish(box); box.style().polish(box)  # 立即刷新样式

        # v9.9.2：这几张卡片改用内联样式画外观（见 style_common.make_card 的注释），
        # 不再吃全局 QSS 级联，所以主题切换时要自己收集控件引用、手动重刷。
        self._theme_frames = []          # 卡片外框（背景/边框）
        self._theme_titles = []          # 标题 QLabel
        self._theme_primary_labels = []  # 资源监控·第1列标签（常规字重）
        self._theme_primary_nums = []    # 资源监控·右侧数值
        self._theme_secondary_bold = []    # 环境信息·字段名
        self._theme_secondary_plain = []   # 环境信息·字段值

        # 左：环境信息
        # v9.9.2：改用 make_card() —— 无原生标题/无隐藏预留高度的 QFrame，
        # 避免 QGroupBox 原生标题机制在不同卡片上留白不一致的问题。
        card_env = make_card("CardEnv")
        card_env.setMinimumWidth(0)
        self._theme_frames.append(card_env)

        _env_box = QVBoxLayout(card_env)
        _env_box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        _env_box.setSpacing(0)
        self._theme_titles.append(install_card_title(card_env, _env_box, "环境信息"))

        # 环境信息正文：
        #  - 左列字段名去掉「：」
        #  - 右列从本区宽度 20% 处起左对齐（stretch 1:4）
        #  - 字号减一码；行高 = OVERVIEW_ROW_H（行距加大 2px）
        for label_text, value_text in self._env_fields():
            row_widget, row_lay = make_grid_row(OVERVIEW_ROW_H, spacing=0)
            row_widget.setMinimumWidth(0)
            row_widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            lab = _ShrinkLabel(label_text)
            lab.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            lab.setStyleSheet(
                f"background:transparent; color:{content_secondary_color()}; "
                f"font-weight:600; font-size:{OVERVIEW_FONT_PX}px;"
            )
            val = _ShrinkLabel(value_text)
            val.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            val.setStyleSheet(
                f"background:transparent; color:{content_secondary_color()}; "
                f"font-size:{OVERVIEW_FONT_PX}px;"
            )
            # 1:4 → 右列起点落在当前区 20% 处
            row_lay.addWidget(lab, 1)
            row_lay.addWidget(val, 4)
            _env_box.addWidget(row_widget)
            self._theme_secondary_bold.append(lab)
            self._theme_secondary_plain.append(val)

        # 不再在卡片内部加 addStretch：让卡片高度贴合内容本身。
        left_col.addWidget(card_env)

        # 右：资源监控 —— 卡片
        card_res = make_card("CardRes")
        card_res.setMinimumWidth(0)
        self._theme_frames.append(card_res)

        res_layout = QVBoxLayout(card_res)
        res_layout.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        res_layout.setSpacing(0)
        self._theme_titles.append(install_card_title(card_res, res_layout, "资源监控"))

        right_col.addWidget(card_res)

        # 资源监控：与环境信息同规格
        #  - 左列去掉「：」、常规字重（不加粗）；右列从 20% 处起（stretch 1:4）
        #  - 字号减一码；行高 OVERVIEW_ROW_H，与左侧逐行对齐
        def meter_row(label_text: str):
            row_widget, row = make_grid_row(OVERVIEW_ROW_H, spacing=0)
            row_widget.setMinimumWidth(0)
            row_widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

            lab = _ShrinkLabel(label_text)
            lab.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            lab.setStyleSheet(
                f"background:transparent; color:{content_primary_color()}; "
                f"font-weight:400; font-size:{OVERVIEW_FONT_PX}px;"
            )

            # 进度条：半粗 4px / 浅色未达轨道 / 无外框（内联样式，避免被祖先 QSS 冲掉）
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setProperty("variant", "thin")
            bar.setTextVisible(False)
            bar.setFixedHeight(4)
            bar.setMinimumWidth(0)

            # 右侧数字：宽度预留防抖动（如 “13W / 320W”）；窄列时允许再收
            num = QLabel("--")
            num.setStyleSheet(
                f"background:transparent; color:{content_primary_color()}; "
                f"font-weight:600; font-size:{OVERVIEW_FONT_PX}px;"
            )
            num.setMinimumWidth(0)
            num.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
            num.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

            # 右区（80%）：进度条 + 数字，起点对齐本区 20%
            # 必须 #id 限定透明，否则 border/background 会级联到进度条
            right = QWidget()
            right.setObjectName("MeterRight")
            right.setMinimumWidth(0)
            right.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            right.setStyleSheet("#MeterRight{background:transparent;}")
            right_l = QHBoxLayout(right)
            right_l.setContentsMargins(0, 0, 0, 0)
            right_l.setSpacing(8)
            right_l.addWidget(bar, 1)
            right_l.addWidget(num, 0)

            row.addWidget(lab, 1)
            row.addWidget(right, 4)

            res_layout.addWidget(row_widget)
            self._theme_primary_labels.append(lab)
            self._theme_primary_nums.append(num)
            return bar, num

        # 7 个指标条（标签已去「：」）；objectName 后立即刷内联轨道样式
        self._meter_bars = []  # (bar, chunk_color) 供主题切换重刷

        def _bind_bar(bar: QProgressBar, name: str):
            bar.setObjectName(name)
            chunk = _BAR_CHUNK[name]
            _apply_meter_bar_style(bar, chunk)
            self._meter_bars.append((bar, chunk))
            return bar

        self.bar_gpu,   self.txt_gpu   = meter_row("GPU使用率"); _bind_bar(self.bar_gpu, "BarGpu")
        self.bar_vram,  self.txt_vram  = meter_row("显存使用");  _bind_bar(self.bar_vram, "BarVram")
        self.bar_mem,   self.txt_mem   = meter_row("内存使用");  _bind_bar(self.bar_mem, "BarMem")
        self.bar_cpu,   self.txt_cpu   = meter_row("CPU使用");   _bind_bar(self.bar_cpu, "BarCpu")
        self.bar_temp,  self.txt_temp  = meter_row("GPU温度");   _bind_bar(self.bar_temp, "BarTemp")
        self.bar_power, self.txt_power = meter_row("GPU功耗");   _bind_bar(self.bar_power, "BarPower")
        self.bar_disk,  self.txt_disk  = meter_row("硬盘使用");  _bind_bar(self.bar_disk, "BarDisk")

        # 同理：资源监控也不再内部撑高，贴合 7 行内容本身的高度即可。

        # 集显利用率读取器（仅 Windows 且无 NVIDIA 时兜底使用）
        self._pdh_gpu = None
        if _IS_WIN:
            try:
                self._pdh_gpu = _PdhGpu()
            except Exception:
                self._pdh_gpu = None

        # 检测显卡名称（监控占位/日志用）
        self._gpu_name = _detect_gpu_name()

        # 设置区拆进整页两列：左=功能与说明（与环境信息同列），右=缩放/Cookie/预下载/数据管理
        self.settings_section = SettingsSection(self)
        self.settings_section.install_into(left_col, right_col)

        # 原「配置」页：热键 / OCR / 录屏 / 语音 / 白名单，接在两列下面
        from pages.page_about import PageAbout
        self.about_section = PageAbout(embedded=True)
        self.about_section.setMinimumWidth(0)
        self.about_section.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        root.addWidget(self.about_section)

        # 定时刷新（每 1s）
        self.timer = QTimer(self); self.timer.timeout.connect(self._tick)
        self.timer.start(1000)  # 每 1 秒刷新一次
        self._tick()

        # v9.9.2：这几张卡片改用内联样式画外观，不再随全局 QSS 自动换肤，
        # 所以要监听主题切换信号，手动重刷一遍配色（背景/边框/标题/正文）。
        theme.changed.connect(self._apply_theme)

    def shutdown(self):
        """主窗口关窗：停资源监控定时器。"""
        t = getattr(self, "timer", None)
        if t is not None:
            try:
                t.stop()
            except Exception:
                pass

    def _apply_theme(self, *_args):
        """主题切换（深色/浅色）时，重新刷新本页这几张"内联样式"卡片的配色。"""
        for frame in self._theme_frames:
            restyle_card_frame(frame)
        for title_lbl in self._theme_titles:
            restyle_card_title(title_lbl)
        primary = content_primary_color()
        secondary = content_secondary_color()
        for lbl in self._theme_primary_labels:
            lbl.setStyleSheet(
                f"background:transparent; color:{primary}; "
                f"font-weight:400; font-size:{OVERVIEW_FONT_PX}px;"
            )
        for lbl in self._theme_primary_nums:
            lbl.setStyleSheet(
                f"background:transparent; color:{primary}; "
                f"font-weight:600; font-size:{OVERVIEW_FONT_PX}px;"
            )
        for bar, chunk in getattr(self, "_meter_bars", []):
            _apply_meter_bar_style(bar, chunk)
        for lbl in self._theme_secondary_bold:
            lbl.setStyleSheet(
                f"background:transparent; color:{secondary}; "
                f"font-weight:600; font-size:{OVERVIEW_FONT_PX}px;"
            )
        for lbl in self._theme_secondary_plain:
            lbl.setStyleSheet(
                f"background:transparent; color:{secondary}; "
                f"font-size:{OVERVIEW_FONT_PX}px;"
            )
        # 设置卡（界面缩放 / Cookie设置 / 公共保存 / 数据管理）主题重刷
        if getattr(self, "settings_section", None) is not None:
            self.settings_section.restyle_theme()
        about = getattr(self, "about_section", None)
        if about is not None and hasattr(about, "restyle_theme"):
            about.restyle_theme()

    # ---------------- internal ----------------
    def _env_fields(self):
        """返回环境信息的 [(标签, 值), ...] 列表，供逐行固定高度布局使用
        （替代原来的单个富文本 QLabel，从根源上解决与"资源监控"逐行对不齐的问题）。"""
        import psutil
        from pathlib import Path

        node = platform.node() or "Unknown"
        cpu  = _detect_cpu_name() or platform.processor() or platform.uname().processor or "Unknown CPU"
        ram_gb = round(psutil.virtual_memory().total / (1024**3))
        arch = platform.machine() or "x64"
        sys_release = platform.win32_ver()[1] or platform.release()
        sys_version = platform.win32_ver()[2] or platform.version()

        try:
            home = Path.home()
            root_drive = home.drive + "\\" if home.drive else "/"
            du = shutil.disk_usage(root_drive)
            disk_total = round(du.total / (1024**3))
            disk_free  = round(du.free  / (1024**3))
            disk_used  = disk_total - disk_free
            disk_line  = f"{disk_free} GB 可用 / 共 {disk_total} GB（已用 {disk_used} GB）"
        except Exception:
            disk_line = "未知"

        py = platform.python_version()

        return [
            ("设备名",     node),
            ("处理器",     cpu),
            ("机带 RAM",   f"{ram_gb} GB"),
            ("系统类型",   f"64 位操作系统，基于 {arch} 的处理器"),
            ("系统版本",   f"Windows {sys_release} {sys_version}"),
            ("Python",    py),
            ("磁盘空间",   disk_line),
        ]

    # ---------------- 主窗口对接（设置卡） ----------------
    def set_main_window(self, mw):
        """主窗口注入：设置卡需要读写 _user_prefs / 缩放 / 导出导入。"""
        if getattr(self, "settings_section", None) is not None:
            self.settings_section.set_main_window(mw)
        about = getattr(self, "about_section", None)
        if about is not None and hasattr(about, "set_main_window"):
            about.set_main_window(mw)

    def on_enter(self, cookie_alert: bool = False):
        about = getattr(self, "about_section", None)
        if about is not None and hasattr(about, "on_enter"):
            about.on_enter(cookie_alert=cookie_alert)

    def export_whitelist(self):
        about = getattr(self, "about_section", None)
        if about is not None and hasattr(about, "export_whitelist"):
            return about.export_whitelist()
        return {}

    def has_prefs_loaded(self) -> bool:
        about = getattr(self, "about_section", None)
        if about is not None and hasattr(about, "has_prefs_loaded"):
            return bool(about.has_prefs_loaded())
        return False

    def reload_from_prefs(self):
        """偏好已加载/导入/重置后，刷新设置卡与下半配置区。"""
        about = getattr(self, "about_section", None)
        if about is not None and hasattr(about, "reload_from_prefs"):
            try:
                about.reload_from_prefs()
            except Exception:
                pass
        if getattr(self, "settings_section", None) is None:
            return
        prefs = getattr(self.settings_section._mw, "_user_prefs", None) or {}
        try:
            self.settings_section.reload_from_prefs(prefs)
        except Exception:
            pass

    def _query_nvidia(self):
        """
        返回：dict 或 None
        keys: util(%)、vram_used(MiB)、vram_total(MiB)、temp(°C)、pwr_draw(W)、pwr_limit(W)
        说明：在 Windows 下调用 nvidia-smi 时，显式隐藏控制台窗口，避免打包 exe 时闪窗。
        """
        try:
            # —— Windows 下隐藏子进程控制台窗口（关键）——————————————
            si = None
            cf = 0
            if sys.platform.startswith("win"):                     # 仅在 Windows 使用
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW      # 使用隐藏窗口
                si.wShowWindow = 0                                  # SW_HIDE
                cf = getattr(subprocess, "CREATE_NO_WINDOW", 0)     # 避免出现新控制台窗口

            out = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit",
                "--format=csv,noheader,nounits",
                ],
                stderr=subprocess.DEVNULL,          # 屏蔽错误输出
                universal_newlines=True,            # Python 3.10：文本模式
                timeout=1.2,                        # 略放宽一点，降低偶发超时
                startupinfo=si,                     # ★ 隐藏窗口（Windows）
                creationflags=cf                    # ★ 隐藏窗口（Windows）
            )

            line = out.strip().splitlines()[0]
            util, mu, mt, temp, pwr, lim = [s.strip() for s in line.split(",")]
            return {
                "util": int(float(util)),
                "vram_used": float(mu),
                "vram_total": max(1.0, float(mt)),
                "temp": int(float(temp)),
                "pwr_draw": float(pwr),
                "pwr_limit": max(1.0, float(lim)),
            }
        except Exception:
            return None

    def _tick(self):
        """每 1s 刷新资源数据：GPU(优先 nvidia-smi) + CPU/内存/磁盘(psutil)"""
        # ========= GPU（来自 nvidia-smi）=========
        info = self._query_nvidia()
        if info:
            # GPU 使用率（0~100）
            util = max(0, min(100, int(info["util"])))
            self.bar_gpu.setValue(util)
            self.txt_gpu.setText(f"{util}%")

            # 显存使用率（由已用/总量计算）
            vram_pct = int(info["vram_used"] / info["vram_total"] * 100)
            self.bar_vram.setValue(vram_pct)
            self.txt_vram.setText(f"{vram_pct}%")

            # 温度（刻度给到 110℃）
            self.bar_temp.setRange(0, 110)
            temp = int(info["temp"])
            self.bar_temp.setValue(temp)
            self.txt_temp.setText(f"{temp}℃")

            # 功耗（按功耗占比画条；右侧显示 “xW / yW”）
            p_pct = int(info["pwr_draw"] / info["pwr_limit"] * 100)
            p_pct = max(0, min(100, p_pct))
            self.bar_power.setValue(p_pct)
            self.txt_power.setText(f"{info['pwr_draw']:.0f}W / {info['pwr_limit']:.0f}W")
        else:
            # 无 NVIDIA 独显（或查询失败）：尝试用 Windows 性能计数器读集显利用率
            util = self._pdh_gpu.read() if self._pdh_gpu else None
            self.bar_temp.setRange(0, 100)  # 复位刻度（NVIDIA 分支曾改成 110）
            if util is not None:
                self.bar_gpu.setValue(util)
                self.txt_gpu.setText(f"{util}%")
            else:
                self.bar_gpu.setValue(0)
                self.txt_gpu.setText("N/A")
            # 显存 / 温度 / 功耗：集显一般无法读取，用 N/A 明确占位（而非空白）
            for bar, lab in (
                (self.bar_vram, self.txt_vram),
                (self.bar_temp, self.txt_temp),
                (self.bar_power, self.txt_power),
            ):
                bar.setValue(0)
                lab.setText("N/A")

        # ========= 系统资源（CPU / 内存 / 磁盘，来自 psutil）=========
        if psutil:
            try:
                # CPU：瞬时百分比（非阻塞）
                cpu = int(psutil.cpu_percent(interval=0))
                self.bar_cpu.setValue(cpu)
                self.txt_cpu.setText(f"{cpu}%")

                # 内存：百分比
                mem = int(psutil.virtual_memory().percent)
                self.bar_mem.setValue(mem)
                self.txt_mem.setText(f"{mem}%")

                # 磁盘：系统盘百分比（Windows 取用户主目录所在盘；其它平台取“/”）
                from pathlib import Path
                home = Path.home()
                root_drive = home.drive + "\\" if getattr(home, "drive", "") else "/"
                du = psutil.disk_usage(root_drive)
                disk_pct = int(du.percent)
                self.bar_disk.setValue(disk_pct)
                self.txt_disk.setText(f"{disk_pct}%")
            except Exception:
                # 即使 psutil 异常，也不中断 UI
                pass
        else:
            # 未安装 psutil：显示占位
            self.txt_cpu.setText("--")
            self.txt_mem.setText("--")
            self.txt_disk.setText("--")
