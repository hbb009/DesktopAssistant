# styles/style_all.py
# ---------------------------------------------------------------------------
# 全站样式合并文件（原 style_theme.py / style_common.py / style_disk_treemap.py /
# style_douyin.py 等合并而来）。
#
# 【为什么合并】桌面小程序体量不大，文件之间没有命名冲突，拆分带来的
# "改一处只影响一个文件"收益小于"多开多个文件找东西"的成本，遂合一。
#
# 【迁移方式】所有变量名/函数名完全不变，只是**模块路径**变了：
#     原来：from styles.style_theme import theme, tk, fmt
#     现在统一：from styles.style_all import theme, tk, fmt, make_card, ...
#
# 【结构】本文件从上到下分 5 段：
#   1. 主题引擎（原 style_theme.py）—— 全局色板、theme/tk/fmt
#   2. 全局卡片工具箱（原 style_common.py）
#   3. 磁盘分析组件专属（原 style_disk_treemap.py）
#   4. 抖音下载页专属（原 style_douyin.py）
#   5. 共用 Tab 样式（视频等 VIDEO_TAB_QSS）
# AI 聊天/反推/无滚轮下拉等已拆到 styles_api/（APIv913 专用）。
# ---------------------------------------------------------------------------

import re

from PyQt5.QtCore import QObject, pyqtSignal, Qt
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PyQt5.QtWidgets import (
    QLabel, QGroupBox, QFrame, QBoxLayout, QWidget, QHBoxLayout, QVBoxLayout,
    QSizePolicy, QLineEdit, QPushButton, QScrollArea, QComboBox,
)
from utils.logger import get_logger

_log = get_logger(__name__)


# 弹窗 / 说明文字：网址、英文、路径等可鼠标拖选复制
_SELECTABLE_LABEL_FLAGS = (
    Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
)
# 可选中 + 可点链接（用于 GitHub 等）
_SELECTABLE_LINK_FLAGS = Qt.TextBrowserInteraction


def set_label_selectable(label: QLabel, *, links: bool = False) -> QLabel:
    """让 QLabel 文字可鼠标选中；links=True 时还支持点开 <a href>。"""
    if label is None:
        return label
    try:
        label.setTextInteractionFlags(
            _SELECTABLE_LINK_FLAGS if links else _SELECTABLE_LABEL_FLAGS
        )
    except Exception:
        pass
    return label


def set_message_box_selectable(box) -> None:
    """QMessageBox 正文可选中（含 chrome://、路径、英文说明）。"""
    if box is None:
        return
    try:
        box.setTextInteractionFlags(_SELECTABLE_LABEL_FLAGS)
    except Exception:
        pass


MSG_AUTO_CLOSE_SEC = 5


def attach_ok_auto_close(dialog, ok_btn, sec=None) -> None:
    """关闭按钮显示倒计时，到 0 自动点它。定时器挂在弹窗上，提前点不会空回调。"""
    if dialog is None or ok_btn is None:
        return
    if sec is None:
        sec = MSG_AUTO_CLOSE_SEC
    try:
        left = int(sec)
    except (TypeError, ValueError):
        left = 0
    if left <= 0:
        return
    from PyQt5.QtCore import QTimer

    orig = (ok_btn.text() or "").strip() or "确定"
    state = {"left": left}

    def _paint():
        ok_btn.setText(f"{orig}（{state['left']}）")

    def _tick():
        state["left"] -= 1
        if state["left"] <= 0:
            timer.stop()
            try:
                ok_btn.click()
            except Exception:
                try:
                    dialog.accept()
                except Exception:
                    pass
            return
        _paint()

    timer = QTimer(dialog)
    timer.setInterval(1000)
    timer.timeout.connect(_tick)
    _paint()
    timer.start()


def message_box_info(parent, title: str, text: str, **kwargs):
    """可拖选正文的 QMessageBox.information。默认不自动关闭；
    需要自动关时传 auto_close_sec=N 秒。"""
    from PyQt5.QtWidgets import QMessageBox
    box = QMessageBox(parent)
    box.setIcon(kwargs.get("icon", QMessageBox.Information))
    box.setWindowTitle(title or "")
    box.setText(text or "")
    if kwargs.get("informative"):
        box.setInformativeText(kwargs.get("informative") or "")
    set_message_box_selectable(box)
    box.setStandardButtons(kwargs.get("buttons", QMessageBox.Ok))
    sec = kwargs.get("auto_close_sec", 0)
    attach_ok_auto_close(box, box.button(QMessageBox.Ok), sec)
    return box.exec_()


def message_box_warn(parent, title: str, text: str, **kwargs):
    """可拖选正文的 QMessageBox.warning。默认不自动关闭；
    需要自动关时传 auto_close_sec=N 秒。"""
    from PyQt5.QtWidgets import QMessageBox
    box = QMessageBox(parent)
    box.setIcon(kwargs.get("icon", QMessageBox.Warning))
    box.setWindowTitle(title or "")
    box.setText(text or "")
    if kwargs.get("informative"):
        box.setInformativeText(kwargs.get("informative") or "")
    set_message_box_selectable(box)
    box.setStandardButtons(kwargs.get("buttons", QMessageBox.Ok))
    sec = kwargs.get("auto_close_sec", 0)
    attach_ok_auto_close(box, box.button(QMessageBox.Ok), sec)
    return box.exec_()


def message_box_critical(parent, title: str, text: str, **kwargs):
    """可拖选正文的 QMessageBox.critical。默认不自动关闭；
    需要自动关时传 auto_close_sec=N 秒。"""
    from PyQt5.QtWidgets import QMessageBox
    box = QMessageBox(parent)
    box.setIcon(kwargs.get("icon", QMessageBox.Critical))
    box.setWindowTitle(title or "")
    box.setText(text or "")
    if kwargs.get("informative"):
        box.setInformativeText(kwargs.get("informative") or "")
    set_message_box_selectable(box)
    box.setStandardButtons(kwargs.get("buttons", QMessageBox.Ok))
    sec = kwargs.get("auto_close_sec", 0)
    attach_ok_auto_close(box, box.button(QMessageBox.Ok), sec)
    return box.exec_()


# ═══════════════════════════════════════════════════════════════════════════
# 1. 主题引擎（原 styles/style_theme.py）
# ═══════════════════════════════════════════════════════════════════════════
# 全局色板中枢。
#
# 用法：
#   from styles.style_all import theme, tk, fmt
#
#   # 1) 样式模块里定义模板（占位符用 {token}）
#   PANEL_QSS = "QWidget{{ background:{panel}; border:1px solid {border}; }}"
#
#   # 2) 应用时格式化
#   self.setStyleSheet(fmt(PANEL_QSS))
#
#   # 3) 订阅主题变更
#   theme.changed.connect(self.refresh_theme)
#
# 注意：模板里字面量的花括号要写成 {{ }}（Python format 规则）。

# ── 深色（原 app.qss 色系）─────────────────────────────────────
DARK = {
    # 底层
    "bg":           "#0b1124",   # 窗口底
    "panel":        "#141b33",   # 卡片/面板
    "panel_2":      "#0e1530",   # 更深的次级面板（对话气泡、下拉）
    "panel_3":      "#10162c",   # tab 栏
    "panel_deep":   "#080f1c",   # 比 panel_2 更深一层（思考/来源气泡正文）
    "topbar":       "#0f1430",
    "canvas":       "#111110",   # WebEngine / 画布纯黑底

    # 输入控件
    "input_bg":     "#0d1b35",
    "input_bg_ro":  "#0a1428",   # readOnly
    "input_deep":   "#0a1220",   # SD/ComfyUI 的深输入框

    # 描边
    "border":       "#25345c",
    "border_soft":  "#1e2a45",
    "border_2":     "#2a3965",
    "border_3":     "#2d4070",

    # 文字
    "text":         "#d7def7",
    "text_strong":  "#cfe0ff",
    "text_mut":     "#9fb0d7",
    "text_dim":     "#6f7fa8",
    "text_faint":   "#5a6a8a",

    # 交互
    "accent":       "#3a8ee0",
    "accent_hover": "#2f7cc8",
    "accent_dis":   "#2a3454",
    "hover_veil":   "rgba(255,255,255,0.04)",
    "sel_bg":       "#1f3a8a",
    "sel_bg_hover": "#1e4a80",
    "sel_text":     "#93c5fd",
    "row_bg":       "#1a2138",

    # 对话气泡（用户浅蓝 / AI 浅绿，暗色下调暗）
    "bubble_user_bg":     "#1a2f4a",
    "bubble_user_border": "#3b6ea5",
    "bubble_ai_bg":       "#163528",
    "bubble_ai_border":   "#2f7a55",

}

# ── 浅色（temp1.png 扁平平面：柔和灰蓝底 + 白卡片 + 淡紫强调）──
LIGHT = {
    "bg":           "#eef2f7",   # 窗外柔和底
    "panel":        "#ffffff",   # 卡片 / 侧栏
    "panel_2":      "#f8fafc",
    "panel_3":      "#f1f5f9",
    "panel_deep":   "#f1f5f9",
    "topbar":       "#ffffff",
    "canvas":       "#eef2f7",

    "input_bg":     "#f8fafc",
    "input_bg_ro":  "#f1f5f9",
    "input_deep":   "#f8fafc",

    "border":       "#e2e8f0",
    "border_soft":  "#eef2f7",
    "border_2":     "#e2e8f0",
    "border_3":     "#cbd5e1",

    "text":         "#334155",
    "text_strong":  "#0f172a",
    "text_mut":     "#64748b",
    "text_dim":     "#94a3b8",
    "text_faint":   "#94a3b8",

    "accent":       "#6366f1",   # 导航选中 / 强调（靛紫）
    "accent_hover": "#4f46e5",
    "accent_dis":   "#e2e8f0",
    "hover_veil":   "rgba(99,102,241,0.06)",
    "sel_bg":       "#eef2ff",
    "sel_bg_hover": "#e0e7ff",
    "sel_text":     "#4f46e5",
    "row_bg":       "#f8fafc",

    # 对话气泡（用户浅蓝 / AI 浅绿）
    "bubble_user_bg":     "#e0f2fe",
    "bubble_user_border": "#93c5fd",
    "bubble_ai_bg":       "#dcfce7",
    "bubble_ai_border":   "#86efac",

}

# ── 语义强调色：两套主题通用，不参与切换 ───────────────────────
# （成功/失败/警告/品牌色 —— 深浅底上都够对比度，无需分叉）
ACCENTS = {
    "ok":      "#22c55e",
    "err":     "#ef4444",
    "warn":    "#f59e0b",
    "brand":   "#f97316",   # 抖音页橙色主题
    "info":    "#3b82f6",
    "purple":  "#a78bfa",
    "cyan":    "#7dd3fc",
}

_PALETTES = {"dark": DARK, "light": LIGHT}


# 界面缩放可选倍数（侧栏按钮菜单）
UI_SCALE_OPTIONS = (0.75, 1.0, 1.25, 1.5)


class _ThemeManager(QObject):
    """全局单例。切换主题 / 缩放时发出信号，各页面自行重刷样式。"""

    changed = pyqtSignal(str)        # 参数：'dark' | 'light'
    scale_changed = pyqtSignal(float)  # 参数：缩放倍数

    def __init__(self):
        super().__init__()
        # 默认深色（与 default_prefs / MainWindow 出厂一致）
        self._name = "dark"
        self._scale = 1.0

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_dark(self) -> bool:
        return self._name == "dark"

    @property
    def scale(self) -> float:
        """全站界面缩放倍数（1.0 = 设计稿原尺寸）。"""
        return self._scale

    @property
    def tokens(self) -> dict:
        d = dict(_PALETTES[self._name])
        d.update(ACCENTS)
        return d

    def set_theme(self, name: str):
        if name not in _PALETTES:
            raise ValueError(f"未知主题: {name}")
        if name == self._name:
            return
        self._name = name
        self.changed.emit(name)

    def set_scale(self, scale: float, *, quiet: bool = False) -> float:
        """设置全站 UI 缩放。返回归一化后的倍数。

        quiet=True 时只改数值不发信号（用于启动早期尚未建完窗体时）。
        """
        try:
            s = float(scale)
        except (TypeError, ValueError):
            s = 1.0
        # 夹在合理范围，并尽量对齐预设档
        s = max(0.5, min(3.0, s))
        best = min(UI_SCALE_OPTIONS, key=lambda x: abs(x - s))
        if abs(best - s) < 0.05:
            s = float(best)
        if abs(s - self._scale) < 1e-6:
            return self._scale
        self._scale = s
        if not quiet:
            self.scale_changed.emit(s)
            # 通知各页 refresh_theme，顺带重刷内联字号/边距
            self.changed.emit(self._name)
        return self._scale

    def toggle(self) -> str:
        self.set_theme("light" if self._name == "dark" else "dark")
        return self._name


theme = _ThemeManager()


def tk(key: str) -> str:
    """取单个色值：tk('panel') -> '#141b33'"""
    return theme.tokens[key]


def fmt(template: str) -> str:
    """把 {token} 占位符替换成当前主题色值。"""
    return template.format(**theme.tokens)


def sp(px) -> int:
    """设计像素 → int。

    整站缩放改由启动时 QT_SCALE_FACTOR 完成（见 mainv916），此处固定按 1×
    返回，避免与全局缩放叠乘。保留 sp() 仅为兼容旧调用。
    """
    try:
        v = float(px)
    except (TypeError, ValueError):
        return 1
    return max(1, int(round(v)))


def format_ui_scale(scale: float = None) -> str:
    """0.75 → '0.75×'，1.0 → '1×'。"""
    s = theme.scale if scale is None else float(scale)
    if abs(s - round(s)) < 1e-6:
        return f"{int(round(s))}×"
    # 去掉多余 0：1.50 → 1.5
    t = f"{s:.2f}".rstrip("0").rstrip(".")
    return f"{t}×"


_PX_RE = re.compile(r"(?<![\w.-])(-?\d+(?:\.\d+)?)px\b")


def scale_stylesheet(qss: str, scale: float = None) -> str:
    """把 QSS 里所有 Npx 按缩放倍数重写（圆角、字号、padding、边距等）。"""
    if not qss:
        return qss or ""
    s = theme.scale if scale is None else float(scale)
    if abs(s - 1.0) < 1e-6:
        return qss

    def _repl(m):
        try:
            n = float(m.group(1))
        except (TypeError, ValueError):
            return m.group(0)
        if n == 0:
            return "0px"
        out = int(round(n * s))
        # 原本 ≥1 的描边/空隙缩小时至少留 1px，避免线消失
        if abs(n) >= 1 and out == 0:
            out = 1 if n > 0 else -1
        return f"{out}px"

    return _PX_RE.sub(_repl, qss)


def apply_theme_palette(app=None, is_dark: bool = None) -> None:
    """按当前主题重写 QApplication 调色板。

    Windows 若开了系统深色模式，Qt 控件（尤其 QComboBox / QLineEdit）会优先用
    系统暗色 Base/Text，QSS 又写不全时就会出现「亮色主题 + 暗色下拉/黑底浅字」。
    这里把 Window/Base/Text/Button/Highlight 一次性对齐主题色。
    """
    from PyQt5.QtGui import QPalette, QColor
    from PyQt5.QtWidgets import QApplication

    if app is None:
        app = QApplication.instance()
    if app is None:
        return
    if is_dark is None:
        is_dark = theme.is_dark
    c = DARK if is_dark else LIGHT

    window = QColor(c["bg"])
    base = QColor(c["panel"])           # 输入框 / 下拉底
    alt = QColor(c["panel_2"])
    text = QColor(c["text"])
    text_strong = QColor(c["text_strong"])
    text_mut = QColor(c["text_mut"])
    button = QColor(c["panel_3"])
    highlight = QColor(c["sel_bg"])
    highlighted = QColor(c["sel_text"])
    border = QColor(c["border"])

    pal = app.palette()
    roles = (
        (QPalette.Window, window),
        (QPalette.WindowText, text),
        (QPalette.Base, base),
        (QPalette.AlternateBase, alt),
        (QPalette.Text, text),
        (QPalette.BrightText, text_strong),
        (QPalette.Button, button),
        (QPalette.ButtonText, text),
        (QPalette.Highlight, highlight),
        (QPalette.HighlightedText, highlighted),
        (QPalette.Link, QColor(c["accent"])),
        (QPalette.LinkVisited, QColor(c.get("accent_hover", c["accent"]))),
        (QPalette.Light, QColor(c["panel"])),
        (QPalette.Midlight, QColor(c["panel_3"])),
        (QPalette.Mid, border),
        (QPalette.Dark, QColor(c["border_3"])),
        (QPalette.Shadow, QColor(c["border_3"])),
    )
    for role, color in roles:
        for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
            if group == QPalette.Disabled and role in (
                QPalette.Text, QPalette.WindowText, QPalette.ButtonText,
            ):
                pal.setColor(group, role, text_mut)
            else:
                pal.setColor(group, role, color)
    try:
        pal.setColor(QPalette.PlaceholderText, text_mut)
    except Exception:
        pass
    app.setPalette(pal)


# ── 全站 QComboBox 统一样式 ─────────────────────────────────
# 高 28、圆角 8、现代 chevron、弹出列表圆角 + 主题选中色。
# 挂到 QApplication 样式表末尾，覆盖 app*.qss 里旧的简单 QComboBox
# 规则，以及 QGroupBox 内更高优先级选择器。
COMBO_H = 28
COMBO_RADIUS = 8
COMBO_CHEVRON_W = 12
COMBO_CHEVRON_H = 8
COMBO_DROP_W = 26


def _combo_chevron_url(is_dark: bool = None) -> str:
    """主题对应下拉箭头 SVG 的 file url（正斜线，供 QSS url()）。"""
    import os
    import sys
    if is_dark is None:
        is_dark = theme.is_dark
    name = "combo_chevron_dark.svg" if is_dark else "combo_chevron_light.svg"
    try:
        from utils.app_paths import resource_root
        base = resource_root()
    except Exception:
        base = getattr(sys, "_MEIPASS", None)
        if not base:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "assets", name).replace("\\", "/")
    return path


# 选择器带祖先，压过 QGroupBox[titleVariant=accent] QComboBox 等旧规则
# 注意：
#  · 必须用 background-color（Windows 下 background 对 Combo 经常不生效）
#  · 不要写死 max-height，允许页面 setMinimumHeight(34) 等
#  · 可编辑下拉内嵌 QLineEdit、弹出 QListView 一并着色
_COMBO_QSS = """
/* ════ 全站下拉 · style_all.build_combo_qss ════ */
QComboBox,
QGroupBox QComboBox,
QFrame QComboBox,
QGroupBox[titleVariant="accent"] QComboBox,
QGroupBox[variant="card"] QComboBox,
QFrame[funcCard="1"] QComboBox,
QFormLayout QComboBox,
QAbstractItemView QComboBox {{
    background-color: {panel};
    background: {panel};
    border: 1px solid {border};
    border-radius: {radius}px;
    padding: 0px {pad_right}px 0px 10px;
    min-height: {h}px;
    color: {text};
    font-size: 13px;
    outline: none;
    combobox-popup: 0;
}}
QComboBox:hover,
QGroupBox QComboBox:hover,
QFrame QComboBox:hover,
QGroupBox[titleVariant="accent"] QComboBox:hover,
QFrame[funcCard="1"] QComboBox:hover {{
    border-color: {accent};
}}
QComboBox:focus,
QGroupBox QComboBox:focus,
QFrame QComboBox:focus,
QGroupBox[titleVariant="accent"] QComboBox:focus,
QFrame[funcCard="1"] QComboBox:focus,
QComboBox:on {{
    border-color: {accent};
}}
QComboBox:disabled,
QGroupBox QComboBox:disabled,
QFrame QComboBox:disabled,
QGroupBox[titleVariant="accent"] QComboBox:disabled,
QFrame[funcCard="1"] QComboBox:disabled {{
    color: {text_faint};
    background-color: {panel_3};
    background: {panel_3};
    border-color: {border_soft};
}}
QComboBox::drop-down,
QGroupBox QComboBox::drop-down,
QFrame QComboBox::drop-down,
QGroupBox[titleVariant="accent"] QComboBox::drop-down,
QFrame[funcCard="1"] QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: {drop_w}px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow,
QGroupBox QComboBox::down-arrow,
QFrame QComboBox::down-arrow,
QGroupBox[titleVariant="accent"] QComboBox::down-arrow,
QFrame[funcCard="1"] QComboBox::down-arrow {{
    image: url({chevron});
    width: {chev_w}px;
    height: {chev_h}px;
}}
/* 可编辑下拉：内嵌行编辑器（模型选择「默认模型」等） */
QComboBox QLineEdit,
QComboBox QLineEdit:focus,
QGroupBox QComboBox QLineEdit,
QFrame QComboBox QLineEdit {{
    background-color: transparent;
    background: transparent;
    border: none;
    color: {text};
    selection-background-color: {sel_bg};
    selection-color: {sel_text};
    padding: 0px;
    margin: 0px;
}}
/* 弹出列表（含可编辑/不可编辑） */
QComboBox QAbstractItemView,
QComboBox QListView,
QGroupBox QComboBox QAbstractItemView,
QFrame QComboBox QAbstractItemView,
QGroupBox[titleVariant="accent"] QComboBox QAbstractItemView,
QFrame[funcCard="1"] QComboBox QAbstractItemView {{
    background-color: {panel};
    background: {panel};
    border: 1px solid {border};
    border-radius: {radius}px;
    outline: none;
    padding: 4px;
    selection-background-color: {sel_bg};
    selection-color: {sel_text};
    color: {text};
}}
QComboBox QAbstractItemView::item,
QComboBox QListView::item {{
    min-height: 26px;
    padding: 4px 8px;
    color: {text};
    background: transparent;
}}
QComboBox QAbstractItemView::item:selected,
QComboBox QListView::item:selected {{
    background: {sel_bg};
    color: {sel_text};
}}
QComboBox QAbstractItemView::item:hover,
QComboBox QListView::item:hover {{
    background: {sel_bg};
    color: {sel_text};
}}
"""


def build_combo_qss(is_dark: bool = None) -> str:
    """生成全站 QComboBox 样式（供应用级 stylesheet 追加）。"""
    if is_dark is None:
        is_dark = theme.is_dark
    pal = DARK if is_dark else LIGHT
    chevron = _combo_chevron_url(is_dark=is_dark)
    return _COMBO_QSS.format(
        panel=pal["panel"],
        panel_3=pal["panel_3"],
        border=pal["border"],
        border_soft=pal["border_soft"],
        text=pal["text"],
        text_faint=pal["text_faint"],
        accent=pal["accent"],
        sel_bg=pal["sel_bg"],
        sel_text=pal["sel_text"],
        radius=COMBO_RADIUS,
        h=COMBO_H,
        pad_right=COMBO_DROP_W + 2,
        drop_w=COMBO_DROP_W,
        chev_w=COMBO_CHEVRON_W,
        chev_h=COMBO_CHEVRON_H,
        chevron=chevron,
    )


def polish_combo_widgets(root) -> None:
    """主题切换后强制所有 QComboBox 重新 polish，避免残留系统暗色绘制。"""
    if root is None:
        return
    widgets = []
    if isinstance(root, QComboBox):
        widgets = [root]
    elif isinstance(root, QWidget):
        widgets = root.findChildren(QComboBox)
    for cb in widgets:
        try:
            st = cb.style()
            if st is not None:
                st.unpolish(cb)
                st.polish(cb)
            cb.update()
            # 弹出视图也刷一遍
            try:
                view = cb.view()
                if view is not None:
                    vst = view.style()
                    if vst is not None:
                        vst.unpolish(view)
                        vst.polish(view)
                    view.update()
            except Exception:
                pass
        except Exception:
            pass


# 抖音「粘贴并解析」同款主操作按钮（橙渐变 + 深色字）。
# 深/浅主题同色；高度吃全局 padding 4px 14px，不设 min-height。
# 必须写在控件级 stylesheet：父级若 setStyleSheet("background:transparent")，
# 会打断应用级 QSS，字色被 *{color} 盖成浅色叠在橙底上等于「没字」。
BTN_DOWNLOAD_QSS = """
QPushButton#BtnDownload {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #f5ac4d, stop:1 #ea9530);
    border: 1px solid #d97f1e;
    color: #241300;
    font-weight: 700;
    padding: 4px 14px;
}
QPushButton#BtnDownload:hover {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #f8bb66, stop:1 #f0a542);
    border-color: #e08b28;
    color: #241300;
}
QPushButton#BtnDownload:pressed {
    background: #d98a28;
    border-color: #c67a1e;
    color: #241300;
}
QPushButton#BtnDownload:disabled {
    background: rgba(240,165,66,0.35);
    border-color: rgba(240,165,66,0.30);
    color: rgba(36,19,0,0.55);
}
"""


def apply_btn_download(btn) -> None:
    """把按钮做成抖音「粘贴并解析」同款：objectName + 控件级样式 + 字色 palette。"""
    from PyQt5.QtGui import QColor, QPalette
    btn.setObjectName("BtnDownload")
    btn.setStyleSheet(BTN_DOWNLOAD_QSS)
    pal = btn.palette()
    fg = QColor("#241300")
    pal.setColor(QPalette.ButtonText, fg)
    pal.setColor(QPalette.WindowText, fg)
    pal.setColor(QPalette.Text, fg)
    btn.setPalette(pal)


# ═══════════════════════════════════════════════════════════════════════════
# 路径输入框（速存图文「文件夹」同款：圆角外框 + 左侧文件夹图标 + 路径文字）
# ───────────────────────────────────────────────────────────────────────────
# 用法：
#   from styles.style_all import apply_folder_path_edit, restyle_folder_path_edit
#   edit = QLineEdit()
#   act = apply_folder_path_edit(edit)           # 打标 + 加 📁 图标
#   # 主题切换时：
#   restyle_folder_path_edit(edit, act)
#
# 样式：QLineEdit[pathStyle="folder"]（app.qss / app_light.qss）
# 高度：padding 上下 3px（比普通输入框矮 6px）
# ═══════════════════════════════════════════════════════════════════════════

FOLDER_PATH_GLYPH = "📁"
# 路径框左侧图标边长。16 时 emoji 下沿容易被 QLineEdit action 槽裁切，缩一号到 14。
FOLDER_PATH_ICON_PX = 14


def make_glyph_icon(glyph: str = FOLDER_PATH_GLYPH, px: int = 16, color: str = None) -> QIcon:
    """用文字符号画小图标（📁 / ⌨ 等），颜色默认跟当前主题次要文字色。"""
    if not color:
        try:
            color = content_secondary_color()
        except Exception:
            color = tk("text_mut")
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    font = QFont()
    # 字号略小于画布，给 emoji 上下留余量，避免底部被切
    font.setPixelSize(max(10, int(px * 0.78)))
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(pm.rect(), Qt.AlignCenter, glyph)
    painter.end()
    return QIcon(pm)


def apply_folder_path_edit(
    edit: QLineEdit, glyph: str = FOLDER_PATH_GLYPH, px: int = FOLDER_PATH_ICON_PX
):
    """把 QLineEdit 做成「文件夹路径」全局样式：圆角框 + 左侧图标。

    基准：速存图文 → 速存图片 → 文件夹路径框。
    返回 leading QAction（主题切换时 restyle_folder_path_edit 更新图标色）。
    """
    edit.setProperty("pathStyle", "folder")
    try:
        edit.setStyleSheet("")  # 让 QSS pathStyle 生效
    except Exception:
        pass
    for act in list(edit.actions()):
        try:
            if act.property("folderPathIcon"):
                edit.removeAction(act)
        except Exception:
            pass
    icon = make_glyph_icon(glyph, px=px)
    action = edit.addAction(icon, QLineEdit.LeadingPosition)
    action.setProperty("folderPathIcon", True)
    action.setProperty("folderPathGlyph", glyph)
    action.setProperty("folderPathIconPx", int(px))
    st = edit.style()
    if st is not None:
        st.unpolish(edit)
        st.polish(edit)
    edit.update()
    return action


def restyle_folder_path_edit(
    edit: QLineEdit, action=None, glyph: str = None, px: int = None
):
    """主题切换时刷新路径框左侧图标颜色。"""
    if action is None:
        for act in edit.actions():
            if act.property("folderPathIcon"):
                action = act
                break
    if action is None:
        return
    g = glyph or action.property("folderPathGlyph") or FOLDER_PATH_GLYPH
    if px is None:
        stored = action.property("folderPathIconPx")
        try:
            px = int(stored) if stored is not None else FOLDER_PATH_ICON_PX
        except (TypeError, ValueError):
            px = FOLDER_PATH_ICON_PX
    action.setIcon(make_glyph_icon(str(g), px=px))


# ═══════════════════════════════════════════════════════════════════════════
# 小按钮（速存图文「安装」同款：矮、字略小、圆角略紧）
# ───────────────────────────────────────────────────────────────────────────
# 用法：
#   from styles.style_all import apply_mini_button
#   btn = QPushButton("安装")
#   apply_mini_button(btn)
#
# 样式：QPushButton[kind="mini"]（app.qss / app_light.qss）
# 尺寸：高 22px · 左右 padding 8px · 字 12px · 圆角 6px
# ═══════════════════════════════════════════════════════════════════════════

def apply_mini_button(btn: QPushButton) -> QPushButton:
    """把 QPushButton 打成全局「小按钮」样式（速存图文 →「安装」同款）。

    只改外观标记与指针；文案/信号由调用方自己设。
    返回同一按钮，便于链式写法：apply_mini_button(QPushButton("安装"))
    """
    btn.setProperty("kind", "mini")
    try:
        btn.setStyleSheet("")  # 清掉内联样式，让 QSS kind=mini 生效
    except Exception:
        pass
    try:
        btn.setCursor(Qt.PointingHandCursor)
    except Exception:
        pass
    try:
        btn.setFlat(False)
    except Exception:
        pass
    st = btn.style()
    if st is not None:
        st.unpolish(btn)
        st.polish(btn)
    btn.update()
    return btn


# ═══════════════════════════════════════════════════════════════════════════
# 中按钮（与「文件夹路径」框同高 29px：另选目录等同排操作）
# ───────────────────────────────────────────────────────────────────────────
# 用法：
#   from styles.style_all import apply_medium_button
#   btn = QPushButton("另选目录")
#   apply_medium_button(btn)
#
# 样式：QPushButton[kind="medium"]（app.qss / app_light.qss）
# 尺寸：实测高 29px（QSS 写 27 + 上下边框 1）· 左右 pad 12 · 字 13 · 圆角 7
# 小按钮 mini≈22 · 中按钮 medium=29 · 对齐路径框 pathStyle=folder
# ═══════════════════════════════════════════════════════════════════════════

# 中按钮目标高度（控件实际像素，含边框）：与 pathStyle=folder 路径框一致
MEDIUM_BUTTON_H = 29


def apply_medium_button(btn: QPushButton) -> QPushButton:
    """把 QPushButton 打成全局「中按钮」样式（高 29px，对齐文件夹路径框）。

    只改外观标记与指针；文案/信号由调用方自己设。
    返回同一按钮，便于链式写法：apply_medium_button(QPushButton("另选目录"))
    """
    btn.setProperty("kind", "medium")
    try:
        btn.setStyleSheet("")  # 清掉内联样式，让 QSS kind=medium 生效
    except Exception:
        pass
    try:
        btn.setCursor(Qt.PointingHandCursor)
    except Exception:
        pass
    try:
        btn.setFlat(False)
    except Exception:
        pass
    st = btn.style()
    if st is not None:
        st.unpolish(btn)
        st.polish(btn)
    btn.update()
    return btn


# ═══════════════════════════════════════════════════════════════════════════
# 活按钮（宽度随布局「活」：吃掉本行剩余宽度，高度固定）
# ───────────────────────────────────────────────────────────────────────────
# 用法：
#   from styles.style_all import apply_live_button
#   btn = apply_live_button(QPushButton("立即计算"))
#   row.addWidget(btn, 1)   # stretch≥1，才能吃到剩余宽度
#
# 样式：QPushButton[kind="live"]（app.qss / app_light.qss）
# 尺寸：高约 28px · 左右 pad 12 · 字 13 · 无固定宽（min 约 72）
# 小 mini≈22 · 中 medium=29 · 活 live=宽自适应
# ═══════════════════════════════════════════════════════════════════════════

LIVE_BUTTON_H = 28


def apply_simple_record(widget):
    """全局「简易记录」样式：列表区仅顶部虚线 + 标准记录区滚动条。

    基准：速存图文 →「运行记录」列表区。
    圆角外框由外层 make_card（功能区标准卡）提供，本样式不画左右/底边。
    适用：QListWidget / QTextEdit 等日志列表；**不**改字号。

    用法::

        from styles.style_all import apply_simple_record
        apply_simple_record(self.list_widget)
        apply_simple_record(self.log_box)   # 抖音「运行日志」
    """
    try:
        widget.setProperty("recordStyle", "simple")
    except Exception:
        pass
    try:
        widget.setAttribute(Qt.WA_StyledBackground, True)
    except Exception:
        pass
    try:
        widget.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    except Exception:
        pass
    try:
        # QTextEdit 默认 StyledPanel 会画原生框，与「仅顶虚线」叠出左右底线
        from PyQt5.QtWidgets import QTextEdit, QFrame as _QF
        if isinstance(widget, QTextEdit):
            widget.setFrameShape(_QF.NoFrame)
            widget.setFrameShadow(_QF.Plain)
            widget.setLineWidth(0)
            widget.setMidLineWidth(0)
    except Exception:
        pass
    try:
        # 清内联样式，让 app.qss 的 recordStyle=simple 生效；不写 font-size
        widget.setStyleSheet("")
    except Exception:
        pass
    try:
        st = widget.style()
        if st is not None:
            st.unpolish(widget)
            st.polish(widget)
        widget.update()
    except Exception:
        pass
    return widget


def apply_transparent_surface(widget, object_name: str = None):
    """中间层表面透明，避免全局 ``QWidget { background: #eef2f7/#0b1124 }``
    在主内容白卡/深面板里露出「脏底」。

    必须用 ``#objectName`` 选择器；禁止无选择器 ``background``（会级联到
    子控件，冲掉路径框/进度条等）。

    用法::

        apply_transparent_surface(self, "PageDirLink")
        apply_transparent_surface(left_wrap, "TzLeftWrap")
        apply_transparent_surface(scroll, "MyScroll")  # QScrollArea 也可
    """
    if object_name:
        try:
            widget.setObjectName(object_name)
        except Exception:
            pass
    name = ""
    try:
        name = widget.objectName() or ""
    except Exception:
        name = ""
    if not name:
        name = f"TransSurf_{id(widget) & 0xFFFFFF:x}"
        try:
            widget.setObjectName(name)
        except Exception:
            pass
    try:
        widget.setAttribute(Qt.WA_StyledBackground, True)
    except Exception:
        pass
    try:
        if isinstance(widget, QScrollArea):
            widget.setStyleSheet(
                f"QScrollArea#{name}{{background:transparent;border:none;}}"
                f"QScrollArea#{name} > QWidget > QWidget{{background:transparent;}}"
            )
            try:
                vp = widget.viewport()
                vp.setAutoFillBackground(False)
                vp.setStyleSheet("background:transparent;")
            except Exception:
                pass
        else:
            widget.setStyleSheet(
                f"#{name}{{background:transparent;border:none;}}"
            )
    except Exception:
        pass
    return widget


def apply_live_button(btn: QPushButton) -> QPushButton:
    """把 QPushButton 打成全局「活按钮」：高度固定，宽度由布局 stretch 决定。

    调用方请用 row.addWidget(btn, stretch≥1) 放入行尾/需要撑满的位置。
    返回同一按钮，便于链式写法：apply_live_button(QPushButton("立即计算"))
    """
    btn.setProperty("kind", "live")
    try:
        btn.setStyleSheet("")  # 清内联，走 QSS kind=live
    except Exception:
        pass
    try:
        # 横向可伸可缩，纵向固定 —— 宽度跟「当前位置」走
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    except Exception:
        pass
    try:
        btn.setMinimumWidth(72)
        # 不要 setMaximumWidth(16777215)：部分 Qt 会告警
        # “The largest allowed size is (16777215,16777215)”
        btn.setFixedHeight(LIVE_BUTTON_H)
    except Exception:
        pass
    try:
        btn.setCursor(Qt.PointingHandCursor)
    except Exception:
        pass
    try:
        btn.setFlat(False)
    except Exception:
        pass
    st = btn.style()
    if st is not None:
        st.unpolish(btn)
        st.polish(btn)
    btn.update()
    return btn


# ═══════════════════════════════════════════════════════════════════════════
# 2. 全局卡片工具箱（原 styles/style_common.py）
# ═══════════════════════════════════════════════════════════════════════════
# 【不要删除本段】虽然 TEXT_STYLE 等常量是空字符串，但它们被多个页面 import：
#   pages/page_fast_save.py / page_dir_link.py / page_screenshot.py 等
#
# 空字符串是有意义的：setStyleSheet("") 会清掉控件级样式，
# 让 assets/app.qss（或 app_light.qss）的全局规则接管 —— 这正是主题化想要的。
# 想彻底移除，得先把那 60 多处 setStyleSheet(TEXT_STYLE) 一起删掉。

TEXT_STYLE = ""
BUTTON_STYLE = ""
BUTTON_PRIMARY_STYLE = ""
LINEEDIT_STYLE = ""


# ── 卡片内嵌标题 + 网格对齐（v9.9 界面美容） ──
# 背景：QGroupBox 的原生标题默认画在“外边框线 / 外边距”上，
# 视觉上会把卡片的外层圆角矩形“咬开一个缺口”。
# v9.9   把标题挪到卡片内部顶端（QLabel），圆角矩形保持完整。
# v9.9.1 修复标题颜色被 QSS 级联覆盖导致的颜色/字号不统一。
# v9.9.2 修复更深层的问题：
#   1) 即便标题文字关掉了（setTitle("")），QGroupBox 原生仍会按“是否曾经
#      设置过标题”等隐藏状态，在不同卡片上保留【不完全相同】的预留高度——
#      这是标题上方留白始终对不齐（相差几像素）的根本原因，且这个差值
#      来自 QGroupBox 自身样式引擎的 subControlRect 计算，不可控、无法
#      单纯靠改 QSS 的 margin/padding 消除。
#      解决办法：不再用 QGroupBox 装标题，改用无原生装饰的 QFrame 作卡片
#      容器，边框/圆角/背景全部由我们自己显式画，标题、间距、行高全部由
#      布局代码精确摆放，不存在任何“框架自己偷偷留白”的隐藏变量。这也是
#      macOS/iOS 原生应用常用的做法：系统控件默认装饰不可控时，直接用无
#      装饰容器 + 手工布局取代，保证像素级可预测。
#   2) QLabel 在 Qt 里其实是 QFrame 的子类——给卡片写 `QFrame{...}` 这种
#      裸类型选择器会"连坐"到卡片内部所有 QLabel 身上，这才是"每一行都
#      莫名其妙出现一个边框方块"的真正根因。卡片外观样式必须用 ID 选择器
#      精确限定在卡片自身，不能向下传染给子控件。
#   3) 环境信息/资源监控两栏要逐行对齐，靠 CSS line-height 和"控件默认
#      高度"去凑是凑不齐的（字体行高与控件高度是两套不同的度量）。这里
#      统一改成"固定高度网格行"（make_grid_row）：不管行内放的是文字还是
#      进度条，都装进同样高度的容器里，逐行累加的位置就是完全确定的。

# ═══════════════════════════════════════════════════════════════════════════
# 功能区圆角矩形「标准卡」
# ───────────────────────────────────────────────────────────────────────────
# 基准：系统总览页「环境信息 / 资源监控 / 操作与安装教程」三块区域。
# 统一管理入口（改这里即可全站生效）：
#   · CARD_RADIUS / CARD_BORDER_PX / _CARD_THEME
#   · make_card() / restyle_card_frame() / apply_func_card()
#   · build_func_card_qss()  → 由 ui_main._apply_qss 追加注入应用级样式
# 圆角：14 → 12 → 10（两次各减 2px）。
# ═══════════════════════════════════════════════════════════════════════════

# 配色（亮/暗）；标题色用于 install_card_title
_CARD_THEME = {
    "dark": dict(
        bg="#0f1430", border="#25345c",
        title="#6087BE", primary="#AFC6FF", secondary="#6087BE",
    ),
    "light": dict(
        bg="#ffffff", border="#e2e8f0",
        title="#64748b", primary="#334155", secondary="#64748b",
    ),
}

CARD_RADIUS = 10             # 功能区标准圆角（px）
CARD_BORDER_PX = 1           # 描边宽度
CARD_TITLE_FONT_SIZE = 12    # 标题字号（px）
CARD_TITLE_FONT_WEIGHT = 600 # 标题字重

# 内边距几何（与总览卡一致）
CARD_TOP_GAP = 12
CARD_LEFT_GAP = 12
CARD_RIGHT_GAP = 12
CARD_BOTTOM_GAP = 12
CARD_TITLE_BODY_GAP = 6   # 标题与正文间距


def _card_colors() -> dict:
    """取当前主题下功能区标准卡配色。"""
    return _CARD_THEME["dark" if theme.is_dark else "light"]


def content_primary_color() -> str:
    """主要文字颜色（如"资源监控"数值/标签），随主题变化。"""
    return _card_colors()["primary"]


def content_secondary_color() -> str:
    """次要文字颜色（如"环境信息"正文，与标题同色），随主题变化。"""
    return _card_colors()["secondary"]


_CARD_FRAME_QSS_TEMPLATE = """
QFrame#{name} {{
    background: {bg};
    border: {bw}px solid {border};
    border-radius: {radius}px;
}}
"""

# 无外框卡：融入已有容器（如视频下载 Tab pane），避免双重描边
_CARD_FRAME_BORDERLESS_QSS_TEMPLATE = """
QFrame#{name} {{
    background: transparent;
    border: none;
    border-radius: 0px;
}}
"""


def _is_borderless_card(widget) -> bool:
    """读取 make_card(borderless=True) 打在控件上的标记。"""
    try:
        return str(widget.property("borderless") or "") in ("1", "true", "True")
    except Exception:
        return False


def make_card(object_name: str, borderless: bool = False) -> QFrame:
    """创建功能区标准卡（QFrame）。

    外观由 restyle_card_frame 按 _CARD_THEME + CARD_RADIUS 内联绘制；
    与系统总览三块卡同规格。object_name 必填且全页唯一（作 QSS id）。

    borderless=True：无描边/无圆角/透明底，用于嵌在已有外框内的分区
    （如抖音/YouTube 页「解析与下载」卡融入 Tab 内容区）。
    标记会写到 property，主题切换 / install_card_title 重刷时仍保持无外框。
    """
    frame = QFrame()
    frame.setObjectName(object_name)
    frame.setAttribute(Qt.WA_StyledBackground, True)
    frame.setProperty("borderless", "1" if borderless else "0")
    apply_func_card(frame)
    restyle_card_frame(frame)
    return frame


def restyle_card_frame(frame: QFrame) -> None:
    """按当前主题重刷 make_card 帧外观；主题切换时调用。"""
    name = frame.objectName() or "FuncCardAnon"
    if not frame.objectName():
        frame.setObjectName(name)
    if _is_borderless_card(frame):
        frame.setStyleSheet(
            _CARD_FRAME_BORDERLESS_QSS_TEMPLATE.format(name=name)
        )
        return
    c = _card_colors()
    frame.setStyleSheet(
        _CARD_FRAME_QSS_TEMPLATE.format(
            name=name,
            bg=c["bg"],
            border=c["border"],
            bw=sp(CARD_BORDER_PX),
            radius=sp(CARD_RADIUS),
        )
    )


def apply_func_card(widget) -> None:
    """把任意 QGroupBox / QFrame 标记为功能区标准卡（供 QSS 统一命中）。"""
    try:
        widget.setProperty("funcCard", "1")
        widget.setAttribute(Qt.WA_StyledBackground, True)
        # 触发 property 选择器重新匹配
        st = widget.style()
        if st is not None:
            st.unpolish(widget)
            st.polish(widget)
        widget.update()
    except Exception:
        _log.exception("标记功能区标准卡失败")


def restyle_func_area(widget) -> None:
    """按功能区标准卡规格，内联刷写 QFrame / QGroupBox 外观。

    与 make_card / 系统总览三块卡同底色、同描边、同圆角（CARD_RADIUS）。
    优先走内联样式，避免被其它 QSS 规则冲掉；主题切换时由主窗口批量调用。
    """
    apply_func_card(widget)
    name = widget.objectName()
    if not name:
        name = f"FuncArea_{id(widget) & 0xFFFFFF:x}"
        widget.setObjectName(name)
    c = _card_colors()
    r, bw = sp(CARD_RADIUS), sp(CARD_BORDER_PX)
    bg, border = c["bg"], c["border"]
    pad_top = sp(4)

    if isinstance(widget, QGroupBox):
        # 原生标题已由 install_card_title 清空；::title 收成 0，避免边框缺口
        widget.setStyleSheet(
            f"QGroupBox#{name}{{"
            f"background:{bg};border:{bw}px solid {border};"
            f"border-radius:{r}px;margin-top:0px;padding-top:{pad_top}px;}}"
            f"QGroupBox#{name}::title{{"
            f"subcontrol-origin:margin;left:0;top:0;padding:0;"
            f"height:0px;width:0px;color:transparent;border:none;}}"
        )
    elif isinstance(widget, QFrame):
        restyle_card_frame(widget)
    else:
        widget.setStyleSheet(
            f"#{name}{{background:{bg};border:{bw}px solid {border};"
            f"border-radius:{r}px;}}"
        )


def restyle_all_func_cards(root: QWidget) -> None:
    """遍历 root 子树，重刷所有 funcCard=1 的功能区（主题切换时调用）。"""
    if root is None:
        return
    try:
        if str(root.property("funcCard")) == "1":
            restyle_func_area(root)
    except Exception:
        _log.exception("重刷根功能区卡片失败")
    for w in root.findChildren(QWidget):
        try:
            if str(w.property("funcCard")) == "1":
                restyle_func_area(w)
        except Exception:
            _log.exception(
                "重刷功能区卡片失败 name=%s",
                getattr(w, "objectName", lambda: "")() or type(w).__name__,
            )


def build_func_card_qss(is_dark: bool = None) -> str:
    """生成功能区标准卡的应用级 QSS 片段（亮/暗各一套）。

    挂到 ui_main._apply_qss 末尾，覆盖 app*.qss 里旧的分散圆角规则，
    保证全站功能区与系统总览三块卡同圆角、同描边、同底色。
    """
    if is_dark is None:
        is_dark = theme.is_dark
    c = _CARD_THEME["dark" if is_dark else "light"]
    # 设计稿像素；整段 QSS 还会经 scale_stylesheet 再乘一次倍数，这里保持未缩放
    r = CARD_RADIUS
    bw = CARD_BORDER_PX
    return f"""
/* ════ 功能区标准卡 · style_all.build_func_card_qss · radius={r}px border={bw}px ════ */
QFrame[funcCard="1"] {{
    background: {c["bg"]};
    border: {bw}px solid {c["border"]};
    border-radius: {r}px;
}}
/* 无圆角外框（Tab 内用分隔线分区） */
QFrame#PanelCard {{
    background: transparent;
    border: none;
    border-radius: 0;
}}
QGroupBox[titleVariant="accent"] {{
    background: {c["bg"]};
    border: {bw}px solid {c["border"]};
    border-radius: {r}px;
}}
QGroupBox[titleVariant="accent"]::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 4px 6px;
    background: {c["bg"]};
    color: {c["title"]};
    font-size: {CARD_TITLE_FONT_SIZE}px;
    font-weight: {CARD_TITLE_FONT_WEIGHT};
}}
"""


def _title_qss(color: str) -> str:
    return (
        "QLabel{{background:transparent; color:{color}; "
        "font-size:{size}px; font-weight:{weight};}}"
    ).format(
        color=color,
        size=sp(CARD_TITLE_FONT_SIZE),
        weight=CARD_TITLE_FONT_WEIGHT,
    )


def install_card_title(box, layout: QBoxLayout, title: str, gap: int = None) -> QLabel:
    """把卡片标题放到 layout 内部顶端，并标记为功能区标准卡。

    标题与正文间距统一由全局 CARD_TITLE_BODY_GAP 控制（默认 6px），
    实现方式：标题包在 head 容器里，底部 margin = gap。
    这样即使 layout.setSpacing(n) > 0，也不会和 insertSpacing 再叠一层空隙。

    同时兼容两种容器：
      - QGroupBox（旧写法，会调用 setTitle("") 关闭原生标题）
      - QFrame / 其它普通容器（新写法，没有原生标题可关，直接跳过）

    参数：
        box   —— 目标容器（QGroupBox 或 QFrame）
        layout—— 该容器自己的顶层布局（QVBoxLayout / QHBoxLayout 均可）
        title —— 标题文字
        gap   —— 标题与正文间距；None 则用 CARD_TITLE_BODY_GAP（推荐不传，走全局）
    返回：
        新建的标题 QLabel（role="card-title"，主题切换时 restyle_card_title）
    """
    if gap is None:
        gap = CARD_TITLE_BODY_GAP
    gap = max(0, int(gap))

    # 凡走 install_card_title 的区域一律视为功能区标准卡（内联刷外观）
    if hasattr(box, "setTitle"):
        box.setTitle("")                     # QGroupBox：关闭原生标题，避免其画在边框线上打断圆角矩形
    restyle_func_area(box)

    lbl = QLabel(title)
    lbl.setProperty("role", "card-title")
    restyle_card_title(lbl)
    lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    # 标题头：底部空隙由全局 CARD_TITLE_BODY_GAP 控制。
    # layout.setSpacing(S) 会在 head 与下一控件之间再加 S，故底部 margin 取
    # max(0, gap - S)，使「标题文字 → 正文」总距约等于 gap，全站统一可调。
    lay_sp = max(0, int(layout.spacing()))
    bottom = max(0, gap - lay_sp)

    head = QWidget()
    head.setObjectName("CardTitleHead")
    head.setAttribute(Qt.WA_StyledBackground, True)
    head.setStyleSheet("background: transparent; border: none;")
    head.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    head_l = QVBoxLayout(head)
    head_l.setContentsMargins(0, 0, 0, bottom)
    head_l.setSpacing(0)
    head_l.addWidget(lbl)

    layout.insertWidget(0, head)
    return lbl


def restyle_card_title(label: QLabel) -> None:
    """按当前主题重新刷标题颜色；主题切换时调用一次即可。"""
    label.setStyleSheet(_title_qss(_card_colors()["title"]))


def make_grid_row(row_height: int, spacing: int = 8) -> tuple:
    """创建一个【固定高度】的横向行容器，用于让不同卡片的多行内容
    严格按同一网格对齐（类似排版里的“基线网格”）。
    不管行内放的是文字 QLabel 还是 QProgressBar，只要都装进这个固定
    高度的容器里，逐行累加下来的纵向位置就是完全确定、可预测的，
    不会因为字体行高、控件默认高度等“隐藏变量”产生累积误差。

    返回 (row_widget, row_layout)，调用方把内容加到 row_layout 里，
    再把 row_widget 加到卡片的外层 QVBoxLayout 上。
    """
    row_widget = QWidget()
    row_widget.setFixedHeight(row_height)
    # 显式透明背景：全局有一条 QWidget { background: ... } 规则，
    # 会给所有"裸" QWidget 刷上不透明底色；这里必须显式覆盖，否则每一行
    # 会变成一个视觉上的"色块"，而不是融入卡片背景。
    # 必须带 #id：无选择器的 background 会级联到子控件（如进度条），冲掉轨道底色。
    row_widget.setObjectName("GridRow")
    row_widget.setStyleSheet("#GridRow{background:transparent;}")
    row_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    row_layout = QHBoxLayout(row_widget)
    row_layout.setContentsMargins(0, 0, 0, 0)
    row_layout.setSpacing(spacing)
    return row_widget, row_layout


# ═══════════════════════════════════════════════════════════════════════════
# 3. 磁盘分析组件专属（原 styles/style_disk_treemap.py）
# ═══════════════════════════════════════════════════════════════════════════
# 磁盘分析组件（pages/disk_treemap_widget.py）专属样式。
#
# 【使用方式】所有常量都是模板，必须包 fmt()：
#     from styles.style_all import fmt, TAB_BAR_QSS
#     self.tab_bar.setStyleSheet(fmt(TAB_BAR_QSS))
#
# 并在 __init__ 末尾加：  theme.changed.connect(self.refresh_theme)
#
# 【模板语法】字面花括号写成 {{ }}。
#
# 依赖运行时数据的颜色（DriveTabSub 的使用率红/橙/灰警告色）仍留在
# Python 代码中动态生成，本段只管与数据无关的静态样式。

# ── DriveTab（盘符 Tab 卡片）──────────────────────────────────────────

# 激活状态（accent 高亮 + 下划线）
DRIVE_TAB_ACTIVE_QSS = "#DriveTab {{ background: {hover_veil}; border-radius: 0; }}"
DRIVE_TAB_ACTIVE_MAIN_QSS = "color:{accent}; background:transparent;"
DRIVE_TAB_ACTIVE_UNDERLINE_QSS = "background:{accent}; border-radius: 0;"

# 非激活状态（透明 + hover 淡亮）
DRIVE_TAB_INACTIVE_QSS = (
    "#DriveTab {{ background: transparent; border-radius: 0; }}"
    "#DriveTab:hover {{ background: {hover_veil}; }}"
)
DRIVE_TAB_INACTIVE_MAIN_QSS = "color:{text_mut}; background:transparent;"
DRIVE_TAB_INACTIVE_UNDERLINE_QSS = "background: transparent; border-radius: 0;"

# 磁盘图标 emoji label
DRIVE_ICON_QSS = "font-size:17px; background:transparent;"

# 主标签（盘符名）正常态背景（字体/颜色在 _apply_style 里动态设置）
DRIVE_MAIN_BASE_QSS = "background:transparent;"

# ── Tab 栏容器 ─────────────────────────────────────────────────────────
TAB_BAR_QSS = "#DiskTabBar {{ background: {panel_3}; border-bottom: 1px solid {border}; }}"

# 扫描控制条
SCAN_BAR_QSS = "background:{panel}; border-bottom:1px solid {border};"

# 扫描按钮
SCAN_BTN_QSS = (
    "QPushButton {{ background:{accent}; color:#ffffff; border:none; border-radius:6px; "
    "padding:0 16px; font-size:12px; font-weight:600; }}"
    "QPushButton:hover {{ background:{accent_hover}; }}"
    "QPushButton:disabled {{ background:{accent_dis}; color:{text_dim}; }}"
)

# 扫描进度条
SCAN_PROGRESS_QSS = (
    "QProgressBar {{ background:{row_bg}; border:none; border-radius:4px; }}"
    "QProgressBar::chunk {{ background:{accent}; border-radius:4px; }}"
)

# 扫描状态文字
SCAN_STATUS_QSS = "color:{text_dim}; font-size:11px; background:transparent;"

# ── 右侧大文件排行榜面板 ───────────────────────────────────────────────
FILES_PANEL_QSS = "background:{panel_2}; border-left:1px solid {border};"

# 面板标题
FILES_TITLE_QSS = "color:{text_mut}; font-size:12px; font-weight:600; background:transparent;"

# 文件列表（透明底，无边框）
FILES_LIST_QSS = (
    "QListWidget{{background:transparent; border:none;}}"
    "QListWidget::item{{border:none; padding:0;}}"
)

# WebEngine 回退提示（无 PyQtWebEngine 时）
FALLBACK_LABEL_QSS = "color:{err}; font-size:13px; background:transparent;"

# WebEngine 视图
# 嵌入主内容卡内的 WebView 走透明，由主区/卡片底色透出（勿用 canvas 窗外灰底）
WEB_VIEW_QSS = "background:transparent; border:none;"

# ── _FileRankRow（文件排行条目）────────────────────────────────────────
FILE_RANK_ROW_QSS = "_FileRankRow {{ background:{row_bg}; border-radius:6px; }}"

FILE_RANK_NUM_QSS  = "color:{text_faint}; font-size:11px; background:transparent;"
FILE_RANK_NAME_QSS = "color:{text}; font-size:12px; background:transparent;"
FILE_RANK_SIZE_QSS = "color:{accent}; font-size:11px; font-weight:600; background:transparent;"


# ═══════════════════════════════════════════════════════════════════════════
# 4. 抖音下载页专属（原 styles/style_douyin.py）
# ═══════════════════════════════════════════════════════════════════════════
# 抖音下载页（pages/page_douyin.py）专属样式。
# 该页主色调为橙色（brand），与全局蓝色体系有意区分 —— 橙色是语义/品牌色，
# 两套主题通用，不参与切换；底色、边框、文字则跟随主题。
#
# 【使用方式】必须包 fmt()：
#     from styles.style_all import fmt, PAGE_QSS
#     self.setStyleSheet(fmt(PAGE_QSS))

# 页面根组件样式（setStyleSheet 到 PageDouyin 自身）
PAGE_QSS = """
    QWidget {{ color: {text}; }}
    QLineEdit {{
        background: {input_bg}; color: {text};
        border: 1px solid {border_2}; border-radius: 4px;
        padding: 4px 8px; font-family: Consolas;
    }}
    QLineEdit:focus {{ border-color: {brand}; }}
    QLineEdit[readOnly="true"] {{
        background: {input_bg_ro}; color: {text_mut};
        border-color: {border_soft};
    }}
    QPushButton#BtnParse, QPushButton#BtnDownload {{
        background: {brand}; color: #ffffff;
        border: none; border-radius: 4px;
        padding: 6px 18px; font-weight: bold; font-size: 13px;
    }}
    QPushButton#BtnParse:hover, QPushButton#BtnDownload:hover {{
        background: #ea580c;
    }}
    QPushButton#BtnParse:disabled, QPushButton#BtnDownload:disabled {{
        background: {accent_dis}; color: {text_dim};
    }}
    QPushButton#BtnSmall {{
        background: {panel}; color: {text_mut};
        border: 1px solid {border_2}; border-radius: 4px;
        padding: 4px 10px; font-size: 12px;
    }}
    QPushButton#BtnSmall:hover {{ background: {panel_3}; color: {text_strong}; }}
    QPushButton#BtnCancel {{
        background: transparent; color: {err};
        border: 1px solid {err}; border-radius: 4px;
        padding: 4px 10px; font-size: 12px;
    }}
    QTextEdit {{
        background: {input_bg}; color: {text};
        border: none; font-family: Consolas; font-size: 11px;
    }}
    QProgressBar {{
        background: {panel}; border: 1px solid {border_3};
        border-radius: 4px; height: 22px;
        color: {text}; font-size: 12px; font-weight: bold;
        text-align: center;
    }}
    QProgressBar::chunk {{ background: {brand}; border-radius: 3px; }}
    QScrollArea {{ border: none; background: transparent; }}
    QLabel#SecTitle {{ color: {text_mut}; font-size: 12px; }}
    QLabel#StatusLbl {{ font-size: 12px; }}
"""

# GroupBox 卡片样式（左侧「下载设置」、右侧「Cookie + 链接」共用）
GB_STYLE = """
    QGroupBox {{
        color: {text_dim};
        font-size: 11px;
        border: 1px solid {border_3};
        border-radius: 6px;
        margin-top: 8px;
        padding: 6px 8px 6px 8px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 10px;
        top: 0px;
        padding: 0 4px;
        background: {panel_2};
    }}
"""

# 媒体选择区分隔线
DIVIDER_QSS = "QFrame{{background:{border_3}; max-height:1px; min-height:1px;}}"


# ═══════════════════════════════════════════════════════════════════════════
# 5. 共用 Tab 子页底色（视频下载 Tab 完整样式见 VIDEO_TAB_QSS）
# ═══════════════════════════════════════════════════════════════════════════
#
# 【使用方式】模板常量须包 fmt()：
#       from styles.style_all import fmt, TAB_QSS
#       self.tabs.setStyleSheet(fmt(TAB_QSS))
#
# 【模板语法】字面花括号必须写成 {{ }}，否则 str.format 会报错。

TAB_QSS = """
/* ── 子页本体：必须与 pane 同色，禁止透明（透明会透出窗外灰底） ── */
QWidget#TabDouyin,
QWidget#TabYoutube,
QWidget#TabBilibili {{
    background: {panel_2};
    border: none;
}}
"""

# 视频下载页 Tab：经典「文件夹页卡」衔接（来自家中 style_all 调整版）
# 与 TAB_QSS 拼接使用：fmt(TAB_QSS + VIDEO_TAB_QSS)
#
# 视觉约定：
#   · pane 顶边 = Tab 下方分隔线；左右顶端带圆角（右端恒圆）
#   · 选中第一张时左上角不圆（由页卡左边承接），属性 firstSelected 由 page_video 切换
#   · 未选中页卡略上抬，露出其下方的分隔线
#   · 选中页卡下压 1px，底边色=面板色，压住分隔线 → 与正文连通
VIDEO_TAB_QSS = """
/* ── 视频下载 · 内容容器 ─────────────────────────────────────── */
QTabWidget#VideoTabWidget::pane {{
    border: 1px solid {border_3};
    border-top: 1px solid {border_3};
    /* 默认：顶边左右都圆（第一张未选中时，线从左圆角起） */
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    border-bottom-left-radius: 10px;
    border-bottom-right-radius: 10px;
    background: {panel_2};
    /* 与 TabBar 重叠 1px，便于选中卡压住顶边线 */
    top: -1px;
    margin-top: 0px;
    padding: 10px;
}}
/* 选中「抖音」等第一张：左上角直角，页卡左边与 pane 左边连成一体 */
QTabWidget#VideoTabWidget[firstSelected="true"]::pane {{
    border-top-left-radius: 0px;
}}
QTabWidget#VideoTabWidget[firstSelected="false"]::pane {{
    border-top-left-radius: 10px;
}}
QTabWidget#VideoTabWidget > QTabBar {{
    background: transparent;
    qproperty-drawBase: 0;
}}
/* 未选中：淡化 + 上抬，下方露出 pane 顶边分隔线 */
QTabWidget#VideoTabWidget > QTabBar::tab {{
    background: transparent;
    color: {text_faint};
    /* 左右只留页签边距；图标/标题/数字由 TightPreDlTabBar 按各标题字宽自绘 */
    padding: 7px 12px 7px 12px;
    margin-right: 6px;
    margin-top: 3px;
    margin-bottom: 0px;
    min-width: 0px;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    border: 1px solid transparent;
    border-bottom: none;
    font-size: 14px;
    /* 各态同一字重：选中加粗会把未选中页签量窄，末字被裁 */
    font-weight: 600;
}}
/* 选中：实底 + 强调顶线，下压盖住分隔线，与正文同色连通 */
QTabWidget#VideoTabWidget > QTabBar::tab:selected {{
    background: {panel_2};
    color: {text_strong};
    border: 1px solid {border_3};
    border-top: 2px solid {accent};
    border-bottom-color: {panel_2};
    margin-top: 0px;
    margin-bottom: -1px;
    font-weight: 600;
    padding-top: 6px;
    padding-bottom: 8px;
}}
/* 第一张且选中：左边与 pane 外框对齐（左上圆角在页卡上） */
QTabWidget#VideoTabWidget[firstSelected="true"] > QTabBar::tab:selected {{
    border-top-left-radius: 10px;
    margin-left: 0px;
}}
QTabWidget#VideoTabWidget > QTabBar::tab:hover:!selected {{
    background: {hover_veil};
    color: {text_dim};
    border: 1px solid {border_soft};
    border-bottom: none;
}}
/* 抖音 / YouTube 子页本体：与 pane 同色 */
QWidget#TabDouyin,
QWidget#TabYoutube {{
    background: {panel_2};
    border: none;
}}
"""

