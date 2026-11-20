# ==================== 标准库 ====================
import os
import re
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

# ==================== 第三方：PyQt5 ====================
from PyQt5.QtCore import (
    Qt, QStandardPaths, QTimer, pyqtSignal, QRectF,
    QAbstractNativeEventFilter, QCoreApplication, QSize,
    QObject, QEvent, QSignalBlocker, QUrl,
)
from PyQt5.QtGui import (
    QKeySequence, QIcon, QPixmap, QPainter, QColor, QPen, QBrush, QPainterPath, QFont, QDesktopServices,
)
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QListWidget,
    QCheckBox, QFileDialog, QShortcut,
    QComboBox, QTabWidget, QStackedWidget,
    QRadioButton, QButtonGroup, QScrollArea,
    QFrame, QSizePolicy, QListWidgetItem, QAbstractItemView,
)

# ==================== 本地模块 ====================
from styles.style_all import (
    install_card_title,
    make_card,
    apply_folder_path_edit,
    restyle_folder_path_edit,
    apply_medium_button,
    apply_btn_download,
    apply_simple_record,
    make_glyph_icon,
    restyle_card_frame,
    restyle_card_title,
    content_secondary_color,
    message_box_info, message_box_warn,
    CARD_TOP_GAP,
    CARD_LEFT_GAP,
    CARD_RIGHT_GAP,
    CARD_BOTTOM_GAP,
    theme,
    tk,
)
from utils.flow_layout import FlowLayout
from utils.logger import get_logger
from utils.cursor_toast import (
    show_cursor_toast, start_record_bubble, stop_record_bubble,
    update_record_bubble_count,
)
from utils.browser_image_save import BrowserImageSave
from utils.background_image_download import BackgroundImageDownload, image_http_url
from utils.background_download_log import BACKGROUND_STEPS, BackgroundDownloadLog

# ==================== Windows API ====================
import ctypes
import ctypes.wintypes as wintypes

try:
    LRESULT = wintypes.LRESULT
except AttributeError:
    LRESULT = ctypes.c_long if ctypes.sizeof(ctypes.c_void_p) == 4 else ctypes.c_longlong
try:
    INT = wintypes.INT
except AttributeError:
    INT = ctypes.c_int
try:
    WPARAM = wintypes.WPARAM
    LPARAM = wintypes.LPARAM
except AttributeError:
    WPARAM = ctypes.c_size_t
    LPARAM = ctypes.c_ssize_t
try:
    ULONG_PTR = wintypes.ULONG_PTR
except AttributeError:
    ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

if ctypes.sizeof(WPARAM) != ctypes.sizeof(ctypes.c_void_p):
    WPARAM = ctypes.c_size_t
if ctypes.sizeof(LPARAM) != ctypes.sizeof(ctypes.c_void_p):
    LPARAM = ctypes.c_ssize_t

WM_HOTKEY = 0x0312
VK_CODE   = {"F3": 0x72, "F4": 0x73, "F5": 0x74, "F6": 0x75, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F12": 0x7B}
# 缩放重启会先拉起新进程再退出旧进程；旧进程还占着热键时 RegisterHotKey 会失败。
# 失败后按这个间隔再试几次，等旧进程放掉。
_HK_RETRY_MS = (300, 700, 1500, 3000)
_ERROR_HOTKEY_ALREADY_REGISTERED = 1409
try:
    _USER32 = ctypes.WinDLL("user32", use_last_error=True)
except Exception:  # pragma: no cover
    _USER32 = ctypes.windll.user32


def _win_register_hotkey(hotkey_id: int, vk: int, mods: int = 0):
    """RegisterHotKey(NULL, …)。返回 (ok, winerr)。"""
    try:
        ok = bool(_USER32.RegisterHotKey(None, int(hotkey_id), int(mods), int(vk)))
    except Exception:
        return False, -1
    if ok:
        return True, 0
    try:
        return False, int(ctypes.get_last_error() or 0)
    except Exception:
        return False, 0


def _win_unregister_hotkey(hotkey_id: int) -> None:
    try:
        _USER32.UnregisterHotKey(None, int(hotkey_id))
    except Exception:
        pass

# 手动速存依赖（探测 win32 剪贴板能力 + pyperclip）
try:
    import win32clipboard  # noqa: F401  # 能力探测
    import win32gui
    import win32com.client
    import pyperclip
    _WIN_OK = True
except Exception:
    win32clipboard = None
    win32gui = None
    win32com = None
    pyperclip = None
    _WIN_OK = False

_SHARED_QUEUE = []

log = get_logger(__name__)

_SAVE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# ─────────────────────────────────────────────
# 全局热键过滤器（速存文本用）
# ─────────────────────────────────────────────
class _GlobalHotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, cb, hid):
        super().__init__()
        self.cb  = cb
        self.hid = hid

    def nativeEventFilter(self, eventType, message):
        if eventType != "windows_generic_MSG":
            return False, 0
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_HOTKEY and msg.wParam == self.hid:
            self.cb()
            return True, 0
        return False, 0


# ═══════════════════════════════════════════════════════════════
# 记录芯片：按「文字 + 左右各 1 汉字」紧算宽，避免全局 QPushButton
# padding(14px) / sizeHint 把卡撑得过宽；FlowLayout 也吃 sizeHint。
# ═══════════════════════════════════════════════════════════════
class _RecordCardChip(QPushButton):
    """速存文本 · 记录文件芯片（cards/*.txt）。"""

    _H = 28
    _FONT_PX = 13

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        f = QFont(self.font())
        f.setPixelSize(self._FONT_PX)
        f.setWeight(QFont.Normal)
        self.setFont(f)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self._recalc_width()

    def _side_pad(self) -> int:
        """左右各留 1 个汉字宽。"""
        try:
            return max(1, self.fontMetrics().horizontalAdvance("字"))
        except Exception:
            return self._FONT_PX

    def _recalc_width(self) -> int:
        fm = self.fontMetrics()
        side = self._side_pad()
        # boundingRect 对中文比 horizontalAdvance 更稳；+2 = 左右 1px 边框
        try:
            text_w = fm.boundingRect(self.text() or "").width()
        except Exception:
            text_w = fm.horizontalAdvance(self.text() or "")
        w = max(1, int(text_w) + side * 2 + 2)
        self.setFixedSize(w, self._H)
        return w

    def sizeHint(self):
        return QSize(self.width() or self._recalc_width(), self._H)

    def minimumSizeHint(self):
        return self.sizeHint()

    def apply_chrome(self, bg: str, fg: str, bd: str, hover: str) -> None:
        """背景/字色/边框；水平 padding=0，空隙靠 fixed 宽度 + 文字居中。"""
        # 不用 CSS 水平 padding：全局 QPushButton 的 14px 与 QSS sizeHint 会叠宽。
        # 宽 = 字宽 + 左右各 1 汉字，文字由按钮默认水平居中。
        self.setStyleSheet(
            f"QPushButton{{background:{bg};color:{fg};"
            f"border:1px solid {bd};border-radius:6px;"
            f"font-size:{self._FONT_PX}px;font-weight:400;"
            f"padding:0px;margin:0px;min-width:0px;}}"
            f"QPushButton:hover{{background:{hover};}}"
            f"QPushButton:disabled{{background:{bg};color:{fg};"
            f"border:1px solid {bd};}}"
        )
        self._recalc_width()


# ═══════════════════════════════════════════════════════════════
# 操作记录详情：单行显示，超宽时省略
# （卡片宽度只跟 row_top stretch 百分比走）
# ═══════════════════════════════════════════════════════════════
class _ElidedLabel(QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._full = text or ""
        self.setWordWrap(False)
        self.setMinimumWidth(0)
        # Ignored：布局算最小宽度时不看全文 sizeHint，避免长文案顶宽整卡
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

    def set_full_text(self, text: str):
        self._full = str(text or "")
        self._refresh_elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refresh_elide()

    def _refresh_elide(self):
        w = max(0, self.width())
        if w <= 1:
            self.setText(self._full)
            return
        self.setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, w))


# ═══════════════════════════════════════════════════════════════
# 运行记录列表：空白点击取消选中；新增条目始终滚到最底
# ═══════════════════════════════════════════════════════════════
class FastRunLogList(QListWidget):
    """速存图文「运行记录」专用列表。

    · 新增条目：始终选中最新一条，并强制滚屏到该条（即使此前已有选中）
    · 点空白：取消选中
    """

    def addItem(self, *args, **kwargs):
        super().addItem(*args, **kwargs)
        # 新记录事件：跳到新记录 + 滚屏（覆盖旧选中）
        self._focus_latest(force_select=True)

    def _focus_latest(self, force_select: bool = True):
        """定位到最新一条：可选设为当前选中，并保证视口滚到该条。"""
        try:
            n = self.count()
            if n <= 0:
                return
            last = self.item(n - 1)
            if last is None:
                return
            if force_select and self.currentItem() is not last:
                # 阻塞信号会漏预览刷新；允许 currentItemChanged 更新预览
                self.setCurrentItem(last)
            self.scrollToItem(last, QAbstractItemView.PositionAtBottom)
            self.scrollToBottom()
            # 布局完成后再滚一次，避免「已选中却停在半截」
            QTimer.singleShot(0, lambda: self._scroll_latest_again())
        except Exception:
            pass

    def _scroll_latest_again(self):
        try:
            n = self.count()
            if n <= 0:
                return
            last = self.item(n - 1)
            if last is None:
                return
            self.scrollToItem(last, QAbstractItemView.PositionAtBottom)
            self.scrollToBottom()
        except Exception:
            pass

    def mousePressEvent(self, event):
        # 点在条目空白区域：取消选中（预览区随 currentItemChanged(None) 清空）
        it = self.itemAt(event.pos())
        if it is None:
            self.clearSelection()
            self.setCurrentRow(-1)
            event.accept()
            return
        super().mousePressEvent(event)


def _reveal_in_explorer(path: str) -> bool:
    """打开资源管理器：文件则选中该文件，文件夹则打开该目录。"""
    path = os.path.normpath(path or "")
    if not path:
        return False
    try:
        if os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", path])
            return True
        if os.path.isdir(path):
            subprocess.Popen(["explorer", path])
            return True
        folder = os.path.dirname(path)
        if os.path.isdir(folder):
            subprocess.Popen(["explorer", folder])
            return True
    except Exception:
        log.warning("打开文件位置失败 %s", path)
    return False


# ═══════════════════════════════════════════════════════════════
# 预览标签（格式对齐「截图工具」PageScreenshot.PreviewLabel）
# · 预览内容始终绑定「当前选中的运行记录」
# · empty：无选中 / 解析中空记录；image：已保存图片；
#   symbol：文件夹/文本等操作（大图标 + 名称）；text：其它文案记录
# · 尺寸随控件 resize 重算；minimumSizeHint 钉死，避免长文字把窗口顶高
# · 有本地路径时点击预览 → 资源管理器定位该文件
# ═══════════════════════════════════════════════════════════════
class FastPreviewLabel(QLabel):
    _PLACEHOLDER = "（选中左侧记录可预览）"
    _TIP_REVEAL = "点击在文件夹中显示"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._src = None          # QPixmap or None
        self._mode = "empty"      # empty | image | symbol | text
        self._file_path = ""      # 可定位的本地路径（文件或文件夹）
        self.setAlignment(Qt.AlignCenter)
        # 主窗最小约 700 内容宽时预览区约 2/5，140 为可用下限，避免顶高窗口
        self.setMinimumSize(140, 140)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._apply_style()
        self.setText(self._PLACEHOLDER)
        self._set_clickable(False)

    def minimumSizeHint(self):
        # 阻断 wordWrap 文本的 heightForWidth 向上传染（同截图预览的尺寸兜底思路）
        return QSize(140, 140)

    def sizeHint(self):
        return QSize(220, 220)

    def refresh_theme(self, *_):
        self._apply_style()

    def _apply_style(self):
        self.setStyleSheet(f"background:transparent; color:{tk('text_dim')};")

    def _set_clickable(self, on: bool):
        self.setCursor(Qt.PointingHandCursor if on else Qt.ArrowCursor)
        self.setToolTip(self._TIP_REVEAL if on else "")

    def _bind_path(self, path: str = ""):
        path = (path or "").strip()
        self._file_path = path if path and os.path.exists(path) else ""
        self._set_clickable(bool(self._file_path))

    def show_empty(self, hint: str = None):
        """空记录 / 无选中：居中占位（解析中的空记录也走这里）。"""
        self._src = None
        self._mode = "empty"
        self._bind_path("")
        self._apply_style()
        self.clear()
        self.setTextFormat(Qt.PlainText)
        self.setAlignment(Qt.AlignCenter)
        self.setText(hint if hint is not None else self._PLACEHOLDER)

    def set_image(self, pixmap, path=""):
        self._src = pixmap if (pixmap is not None and not pixmap.isNull()) else None
        if self._src is None:
            self.show_empty()
            return
        self._mode = "image"
        self._bind_path(path)
        self._apply_style()
        self.clear()
        self.setTextFormat(Qt.PlainText)
        self.setAlignment(Qt.AlignCenter)
        self._rescale()

    def set_symbol_preview(self, glyph: str, title: str, path=""):
        """操作类记录预览：居中大图标 + 名称（文件夹 / 文本文件等）。"""
        self._src = None
        self._mode = "symbol"
        self._bind_path(path)
        self._apply_style()
        self.clear()
        self.setAlignment(Qt.AlignCenter)
        self.setTextFormat(Qt.RichText)
        g = (glyph or "•").strip() or "•"
        name = (title or "").strip() or "（未命名）"
        # 简易转义，避免标题里的 <>& 破坏 HTML
        for a, b in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;")):
            name = name.replace(a, b)
        color = tk("text")
        self.setText(
            f'<div style="text-align:center;">'
            f'<div style="font-size:52px;line-height:1;">{g}</div>'
            f'<div style="margin-top:0;font-size:14px;font-weight:600;'
            f'color:{color};">{name}</div>'
            f"</div>"
        )

    def set_text_content(self, text: str):
        """非图片运行记录：主区域展示文字（顶左对齐，格式贴近截图预览的主区占用）。"""
        self._src = None
        self._mode = "text"
        self._bind_path("")
        self._apply_style()
        self.clear()
        self.setTextFormat(Qt.PlainText)
        self.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.setText(text or self._PLACEHOLDER)

    def _rescale(self):
        if self._mode != "image" or self._src is None:
            return
        self.setPixmap(self._src.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._file_path:
            if not _reveal_in_explorer(self._file_path):
                show_cursor_toast(
                    "找不到文件",
                    os.path.basename(self._file_path) or self._file_path,
                    accent="warn",
                )
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def resizeEvent(self, e):
        self._rescale()
        super().resizeEvent(e)


# ═══════════════════════════════════════════════════════════════
# PageFastSave
# ═══════════════════════════════════════════════════════════════
def _safe_filename_part(text: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text or "")
    return cleaned.strip(" .")


_FAST_TXT_APP = "桌面助手"
_FAST_TXT_VER = "v9.19"


def _hanzi_count(text: str) -> int:
    return sum(1 for ch in text or "" if _is_hanzi(ch))


def _is_hanzi(ch: str) -> bool:
    code = ord(ch)
    return 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF


def _first_hanzi(text: str, n: int = 10) -> str:
    out = []
    for ch in text or "":
        if _is_hanzi(ch):
            out.append(ch)
            if len(out) >= n:
                break
    return "".join(out)


def _content_name_part(text: str, n: int = 10) -> str:
    """文件名第三段：内容里的前 n 个汉字。没有汉字时用前 n 个可见字符。"""
    hanzi = _first_hanzi(text, n)
    if hanzi:
        return hanzi
    out = []
    for ch in text or "":
        if ch.isspace() or ch in '<>:"/\\|?*':
            continue
        out.append(ch)
        if len(out) >= n:
            break
    return "".join(out)


def _cf_unicode_text() -> str:
    """直接读 CF_UNICODETEXT，避免 pyperclip / Qt 把中文读丢。"""
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except Exception:
        return ""
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    opened = False
    for _ in range(10):
        if user32.OpenClipboard(None):
            opened = True
            break
        time.sleep(0.02)
    if not opened:
        return ""
    try:
        if not user32.IsClipboardFormatAvailable(13):
            return ""
        handle = user32.GetClipboardData(13)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            text = ctypes.wstring_at(ptr) or ""
        finally:
            kernel32.GlobalUnlock(handle)
        return text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    except Exception:
        log.debug("读取 CF_UNICODETEXT 失败", exc_info=True)
        return ""
    finally:
        try:
            user32.CloseClipboard()
        except Exception:
            pass


def _read_fast_clip_text() -> str:
    """剪贴板纯文本。几种读法里优先保留汉字更多的那一份。"""
    candidates = []
    try:
        candidates.append(_cf_unicode_text())
    except Exception:
        pass
    try:
        cb = QApplication.clipboard()
        if cb is not None:
            candidates.append(cb.text() or "")
    except Exception:
        pass
    try:
        if pyperclip is not None:
            candidates.append(pyperclip.paste() or "")
    except Exception:
        log.debug("pyperclip 读取剪贴板失败", exc_info=True)
    best = ""
    best_hanzi = -1
    for raw in candidates:
        text = (raw or "").strip()
        if not text:
            continue
        n = _hanzi_count(text)
        if n > best_hanzi or (n == best_hanzi and len(text) > len(best)):
            best = text
            best_hanzi = n
    return best


def _fast_txt_stem(content: str) -> str:
    """未选中文件时的名字：桌面助手 v9.19 前10个汉字。"""
    parts = (
        _FAST_TXT_APP,
        _FAST_TXT_VER,
        _safe_filename_part(_content_name_part(content, 10)),
    )
    stem = " ".join(part for part in parts if part)
    if len(stem) > 80:
        stem = stem[:80].rstrip(" .")
    return stem or "未命名"


def _unique_txt_path(folder, stem, content, already_saved):
    """同名且内容相同返回 None；同名但内容不同则加 _2、_3。"""
    stem = stem or "未命名"
    path = os.path.join(folder, stem + ".txt")
    if already_saved(path, content):
        return None
    if not os.path.exists(path):
        return path
    for n in range(2, 100):
        nxt = os.path.join(folder, f"{stem}_{n}.txt")
        if already_saved(nxt, content):
            return None
        if not os.path.exists(nxt):
            return nxt
    return path


class PageFastSave(QWidget):
    background_enabled_changed = pyqtSignal(bool)
    browser_image_triggered = pyqtSignal(str, str)
    browser_image_link = pyqtSignal(str, str, str)
    browser_image_hover = pyqtSignal(bool)
    # 速存操作（存图/存文本/建文件夹）成功时发射，请求主窗口切到「速存图文」页
    request_show = pyqtSignal()
    # F6 记录模式切换 → 通知主窗口关闭/恢复自动下载
    record_mode_toggled_sig = pyqtSignal(str)

    @staticmethod
    def _typo(w, name):
        w.setProperty("typo", name)
        w.style().unpolish(w)
        w.style().polish(w)

    def _restyle_hseps(self):
        color = tk("border")
        qss = (
            "QFrame#FastSaveHSep{"
            f"background:{color};border:none;"
            f"max-height:3px;min-height:3px;}}"
        )
        for sep in getattr(self, "_h_seps", []) or []:
            sep.setStyleSheet(qss)

    @staticmethod
    def _set_qprop(w, name, value):
        """设置一个 Qt 动态属性并刷新样式——用于命中 app.qss / app_light.qss 里的属性选择器。"""
        w.setProperty(name, value)
        w.style().unpolish(w)
        w.style().polish(w)

    @staticmethod
    def _make_glyph_icon(glyph: str, px: int = 16, color: str = "#8fa3d9") -> QIcon:
        """兼容旧调用；实现已迁到 style_all.make_glyph_icon。"""
        return make_glyph_icon(glyph, px=px, color=color)

    # ===== 窗口高度 BUG 根治（与 Grok 诊断一致：heightForWidth 把虚高传给主窗口）=====
    # 本页含 wordWrap 自动换行标签，会让整页 hasHeightForWidth()=True。Qt 据此按当前
    # （较窄的）宽度把页面高度算大，这个虚高顺着布局链一路传到主窗口，抬高了窗口的
    # “首选高度”，于是真机上一拖标题栏，窗口就往首选高度长高、且缩不回去（切到没有此
    # 特性的页面才松开）。对照 6 个正常页面：它们 hasHeightForWidth()=False，窗口首选
    # 高度稳定在侧边栏决定的 836（<900），从不长高。
    # 修法：本页对外声明“高度不随宽度变化”，并把对外“首选高度”压到不超过侧边栏——
    #   · 标签内部仍照常换行，不截断文字（只是不把该属性外传给窗口）；
    #   · 实际布局仍会把可用高度分配给本页，Expanding 子控件照常填满，视觉无变化。
    def hasHeightForWidth(self):
        return False

    def sizeHint(self):
        s = super().sizeHint()
        return QSize(s.width(), min(s.height(), 700))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_top_cards()
        # 底部：运行记录 与 预览区 各占 50% 宽，写死为固定值，
        # 不受两侧内容 sizeHint / 长文字撑宽影响
        w = self.width()
        if w > 0:
            run = getattr(self, "_card_runlog", None)
            prev = getattr(self, "_card_preview", None)
            if run is not None and prev is not None:
                gap = max(1, int(getattr(self, "_bottom_gap", 6)))
                half = max(1, (w - gap) // 2)
                run.setFixedWidth(half)
                prev.setFixedWidth(max(1, w - gap - half))
        # 预览区变宽/变窄时重算文件名省略，分辨率行始终保留
        try:
            cur = self.list_widget.currentItem() if hasattr(self, "list_widget") else None
            if cur is not None and self._runlog_kind(cur) == "image":
                path = self._resolve_runlog_image_path(cur)
                if path and hasattr(self, "preview") and getattr(self.preview, "_src", None) is not None:
                    self._set_image_preview_meta(path, self.preview._src)
        except Exception:
            pass

    def _resize_top_cards(self):
        if not hasattr(self, "_card_img") or self.height() <= 0:
            return
        height = max(248, int(self.height() * 0.46))
        if self.chk_background_image.isChecked():
            layout = self._card_img.layout()
            # The top cards use fixed heights, so reserve enough room for all operation rows.
            height = max(height, layout.sizeHint().height())
            if self._card_img.isVisible():
                height = max(height, layout.heightForWidth(self._card_img.width()))
        for card in (self._card_img, self._card_txt, self._card_mkdir):
            card.setFixedHeight(height)

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setSpacing(6)
        # 与系统总览一致：ContentRoot 已有左右内边距，页面不再叠第二层
        lay.setContentsMargins(0, 0, 0, 0)

        # ══════════ 顶部三卡片：速存图片 / 速存文本 / 速建文件夹 ══════════
        row_top = QHBoxLayout()
        row_top.setSpacing(6)
        lay.addLayout(row_top)

        self._theme_frames = []
        self._theme_titles = []
        self._theme_secondary_bold = []
        self._theme_secondary_plain = []
        self._h_seps = []

        # ── 速存图片 ──
        gb_img  = make_card("CardFastAuto")
        self._theme_frames.append(gb_img)
        img_box = QVBoxLayout(gb_img)
        img_box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        img_box.setSpacing(0)
        title_img = install_card_title(gb_img, img_box, "速存图片")
        self._theme_titles.append(title_img)

        self.chk_background_image = QCheckBox("后台下载")
        self.chk_background_image.setStyleSheet("background: transparent;")
        img_box.addWidget(self.chk_background_image, 0, Qt.AlignLeft)

        self.btn_status = QPushButton("已关闭")
        self.btn_status.setObjectName("FastStatusLamp")
        self.btn_status.setFlat(True)
        self.btn_status.setDefault(False)
        self.btn_status.setAutoDefault(False)
        self.btn_status.setFocusPolicy(Qt.NoFocus)
        self.btn_status.setCursor(Qt.PointingHandCursor)
        self.btn_status.setToolTip("查看后台下载状态")
        # 状态文字和操作记录随开关变化，卡片宽度仍由顶部三卡的布局分配。
        self.btn_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._apply_status_btn_style("#f0a500", "已关闭")
        self.btn_status.clicked.connect(self._diagnose)
        img_box.addWidget(self.btn_status)


        self.lbl_img_hint = QLabel("在 Chrome 或 Edge 将鼠标停在图片上，按 Alt+1 下载。")
        self.lbl_img_hint.setWordWrap(True)
        self.lbl_img_hint.setStyleSheet("background: transparent;")
        self.lbl_img_hint.setAlignment(Qt.AlignLeft)
        self._typo(self.lbl_img_hint, "muted")
        img_box.addWidget(self.lbl_img_hint)

        self.background_operations = self._build_background_operations()
        self.background_operations.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.background_operations.hide()
        img_box.addWidget(self.background_operations)

        # 保存路径 —— 全局「文件夹路径」样式（圆角框 + 📁）
        row_path = QHBoxLayout()
        row_path.setSpacing(4)
        self.save_path = QLineEdit("")
        self._path_icon_action = apply_folder_path_edit(self.save_path)
        row_path.addWidget(self.save_path, 1)
        img_box.addLayout(row_path)
        img_box.addSpacing(6)

        # 全局「中按钮」：高 29px，与上方文件夹路径框齐高
        self.btn_choose_dir = apply_medium_button(QPushButton("另选目录"))
        img_box.addWidget(self.btn_choose_dir)
        img_box.addStretch(1)

        # ── 速存文本 ──
        gb_txt  = make_card("CardFastManual")
        self._theme_frames.append(gb_txt)
        txt_box = QVBoxLayout(gb_txt)
        txt_box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        txt_box.setSpacing(0)
        title_txt = install_card_title(gb_txt, txt_box, "速存文本")
        self._theme_titles.append(title_txt)

        self.chk_manual = QCheckBox("启用速存文本")
        self.chk_manual.setStyleSheet("background: transparent;")
        txt_box.addWidget(self.chk_manual, 0, Qt.AlignLeft)

        # ── 原速存文本 ──
        lbl_hint = QLabel("选中文件则创建同名文本；未选中时文件名为「桌面助手 v9.19 内容前10个汉字」")
        lbl_hint.setWordWrap(True)
        lbl_hint.setStyleSheet("background: transparent;")
        self._typo(lbl_hint, "muted")
        txt_box.addWidget(lbl_hint)
        txt_box.addSpacing(6)
        self._txt_hotkey_group = QButtonGroup(self)
        row_radios = QHBoxLayout()
        row_radios.setSpacing(4)
        self._txt_radio = {}
        for key in ["F3", "F4"]:
            rb = QRadioButton(key)
            rb.setStyleSheet("background: transparent;")
            self._txt_radio[key] = rb
            self._txt_hotkey_group.addButton(rb)
            row_radios.addWidget(rb)
        self._txt_radio["F4"].setChecked(True)
        row_radios.addStretch(1)
        txt_box.addLayout(row_radios)
        txt_box.addSpacing(6)

        # ── 记录模式 ──
        record_row = QHBoxLayout()
        record_row.setSpacing(8)
        self.btn_record = QPushButton("开始记录F6")
        self.btn_record.setCursor(Qt.PointingHandCursor)
        self.btn_record.clicked.connect(self._btn_record_clicked)
        # 批处理中冻结：开始记录按钮 + 记录卡（与侧栏自动下载开关一致）
        self._record_btn_frozen = False
        self._record_cards_frozen = False
        self._restyle_record_btn()
        record_row.addWidget(self.btn_record)
        self.chk_new_record = QCheckBox("随机记录名")
        self.chk_new_record.setStyleSheet("background: transparent;")
        record_row.addWidget(self.chk_new_record)
        record_row.addStretch(1)
        txt_box.addLayout(record_row)
        txt_box.addSpacing(2)

        self.lbl_record_hint = QLabel("点击「开始记录」后复制即存入文件")
        self.lbl_record_hint.setWordWrap(True)
        self.lbl_record_hint.setStyleSheet("background:transparent;padding:2px 0;")
        self.lbl_record_hint.setAlignment(Qt.AlignLeft)
        self._typo(self.lbl_record_hint, "muted")
        txt_box.addWidget(self.lbl_record_hint)
        txt_box.addSpacing(2)

        # 记录卡片网格（可滚动）
        self._record_scroll = QScrollArea()
        self._record_scroll.setFrameShape(QFrame.NoFrame)
        self._record_scroll.setWidgetResizable(True)
        self._record_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._record_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._record_scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
        )
        self._record_grid_widget = QWidget()
        self._record_grid_widget.setStyleSheet("background:transparent;")
        self._record_grid_layout = FlowLayout(self._record_grid_widget, margin=0, h_spacing=4, v_spacing=4)
        self._record_scroll.setWidget(self._record_grid_widget)
        txt_box.addWidget(self._record_scroll, 1)

        # ── 速建文件夹（回到顶部，与 A/B/C 同排） ──
        gb_mkdir = make_card("CardFastMkdir")
        gb_mkdir.setMinimumWidth(160)
        self._theme_frames.append(gb_mkdir)
        mkdir_box = QVBoxLayout(gb_mkdir)
        mkdir_box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        mkdir_box.setSpacing(0)
        title_mkdir = install_card_title(gb_mkdir, mkdir_box, "速建文件夹")
        self._theme_titles.append(title_mkdir)

        self.chk_mkdir = QCheckBox("启用速建文件夹")
        self.chk_mkdir.setStyleSheet("background: transparent;")
        mkdir_box.addWidget(self.chk_mkdir, 0, Qt.AlignLeft)

        row_mkdir_key = QHBoxLayout()
        row_mkdir_key.setSpacing(4)
        lbl_seq = QLabel("顺序新建")
        lbl_seq.setStyleSheet(f"background:transparent; color:{content_secondary_color()}; font-weight:600; padding:0;")
        self._theme_secondary_bold.append(lbl_seq)
        row_mkdir_key.addWidget(lbl_seq)
        self._mkdir_hotkey_group = QButtonGroup(self)
        self._mkdir_radio = {}
        for key in ["F8"]:  # 顺序新建仅 F8
            rb = QRadioButton(key)
            rb.setStyleSheet("background: transparent;")
            self._mkdir_radio[key] = rb
            self._mkdir_hotkey_group.addButton(rb)
            row_mkdir_key.addWidget(rb)
        self._mkdir_radio["F8"].setChecked(True)
        row_mkdir_key.addStretch(1)
        mkdir_box.addLayout(row_mkdir_key)
        mkdir_box.addSpacing(4)

        row_mkdir_f9 = QHBoxLayout()
        row_mkdir_f9.setSpacing(4)
        lbl_f9 = QLabel("F9 直接新建")
        lbl_f9.setStyleSheet(f"background:transparent; color:{content_secondary_color()}; padding:0;")
        self._theme_secondary_plain.append(lbl_f9)
        row_mkdir_f9.addWidget(lbl_f9)
        self.mkdir_name = QLineEdit("Grok")
        self.mkdir_name.setPlaceholderText("默认 Grok")
        self.mkdir_name.setFixedWidth(80)
        self.mkdir_name.setProperty("inputStyle", "underline")
        self.mkdir_name.style().unpolish(self.mkdir_name)
        self.mkdir_name.style().polish(self.mkdir_name)
        row_mkdir_f9.addWidget(self.mkdir_name)
        row_mkdir_f9.addStretch(1)
        mkdir_box.addLayout(row_mkdir_f9)
        mkdir_box.addSpacing(2)

        row_mkdir_f10 = QHBoxLayout()
        row_mkdir_f10.setSpacing(4)
        lbl_f10 = QLabel("F10 直接新建")
        lbl_f10.setStyleSheet(f"background:transparent; color:{content_secondary_color()}; padding:0;")
        self._theme_secondary_plain.append(lbl_f10)
        row_mkdir_f10.addWidget(lbl_f10)
        self.mkdir_name_b = QLineEdit("Qwen")
        self.mkdir_name_b.setPlaceholderText("默认 Qwen")
        self.mkdir_name_b.setFixedWidth(80)
        self.mkdir_name_b.setProperty("inputStyle", "underline")
        self.mkdir_name_b.style().unpolish(self.mkdir_name_b)
        self.mkdir_name_b.style().polish(self.mkdir_name_b)
        row_mkdir_f10.addWidget(self.mkdir_name_b)
        row_mkdir_f10.addStretch(1)
        mkdir_box.addLayout(row_mkdir_f10)
        mkdir_box.addSpacing(6)

        lbl_mkdir_hint = QLabel("选中文件夹按所选键顺序新建；未选中时 F9/F10 直接新建对应文件夹")
        lbl_mkdir_hint.setWordWrap(True)
        lbl_mkdir_hint.setStyleSheet("background: transparent;")
        self._typo(lbl_mkdir_hint, "muted")
        mkdir_box.addWidget(lbl_mkdir_hint)
        mkdir_box.addStretch(1)

        # 三卡宽度 stretch：图片 / 文本 / 速建 = 18 / 27 / 27（合计 72）
        # 序列文件检查+查重已迁到「文本编辑」页右栏
        # 三卡等高由 resizeEvent 控制：最小 248px，高度占比 46%
        self._card_img = gb_img
        self._card_txt = gb_txt
        self._card_mkdir = gb_mkdir
        for _card in (gb_img, gb_txt, gb_mkdir):
            _card.setMinimumWidth(0)
            _card.setMinimumHeight(248)
            _card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row_top.addWidget(gb_img, 18)
        row_top.addWidget(gb_txt, 27)
        row_top.addWidget(gb_mkdir, 27)

        # ══════════ 底部：运行记录（大，占主要空间） + 预览区（右侧，选中左侧记录可预览） ══════════
        row_bottom = QHBoxLayout()
        row_bottom.setSpacing(6)
        self._bottom_gap = 6
        lay.addLayout(row_bottom, 1)   # 底部区域吸收窗口剩余高度

        card_runlog = make_card("CardFastRunLog")
        self._theme_frames.append(card_runlog)
        self._card_runlog = card_runlog
        runlog_box = QVBoxLayout(card_runlog)
        runlog_box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        runlog_box.setSpacing(0)
        title_runlog = install_card_title(card_runlog, runlog_box, "运行记录")
        self._theme_titles.append(title_runlog)

        self.list_widget = FastRunLogList()
        self.list_widget.setObjectName("FastSaveList")
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setSpacing(1)
        # 全局「简易记录」+ FastSaveList 选中：高亮底 + 左侧 4px 橙色竖条
        apply_simple_record(self.list_widget)
        try:
            self.list_widget.setFocusPolicy(Qt.StrongFocus)
            # 允许点空白取消选中（见 FastRunLogList.mousePressEvent）
            self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        except Exception:
            pass
        runlog_box.addWidget(self.list_widget, 1)

        # 宽度：运行记录 与 预览区 各占 50%
        row_bottom.addWidget(card_runlog, 1)

        # ── 预览区：格式对齐「截图工具 → 截图预览」──────────────────────────
        #   主区 stretch 吃满高度（占位/图/文字）+ 底部固定 meta 行
        card_preview = make_card("CardFastPreview")
        self._theme_frames.append(card_preview)
        self._card_preview = card_preview
        preview_box = QVBoxLayout(card_preview)
        preview_box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        preview_box.setSpacing(0)
        title_preview = install_card_title(card_preview, preview_box, "预览区")
        self._theme_titles.append(title_preview)

        self.preview = FastPreviewLabel()
        preview_box.addWidget(self.preview, 1)

        self.lbl_preview_meta = QLabel()
        self.lbl_preview_meta.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        # 不换行：长文件名只占第 1 行（超出省略），第 2 行固定留给「宽×高 · 体积」，
        # 避免 wordWrap 把文件名拆成两行后把分辨率挤出 40px 可视区。
        self.lbl_preview_meta.setWordWrap(False)
        self.lbl_preview_meta.setFixedHeight(40)
        self.lbl_preview_meta.setProperty("typo", "muted")
        self.lbl_preview_meta.setVisible(False)
        self.lbl_preview_meta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        preview_box.addWidget(self.lbl_preview_meta)

        row_bottom.addWidget(card_preview, 1)

        self.list_widget.currentItemChanged.connect(self._on_runlog_item_selected)

        self._theme_glyph_actions = [
            (self._path_icon_action, "📁"),
        ]
        # ══════════ 信号与槽 ══════════
        self.browser_image_triggered.connect(self._on_hover_image_triggered)
        self.browser_image_link.connect(self._on_hover_image_link)
        self.browser_image_hover.connect(self._on_hover_image_status)
        self._hover_has_image = False
        self._background_captures = set()
        from utils.app_paths import log_dir
        self._background_log = BackgroundDownloadLog(os.path.join(log_dir(), "background_image_download.log"), log)
        self._refresh_background_operations()
        self._browser_image_save = BrowserImageSave(
            log, self.browser_image_link.emit, self.browser_image_hover.emit,
            on_trigger=self.browser_image_triggered.emit,
        )
        self._background_download = BackgroundImageDownload(self, _SAVE_UA)
        self._background_download.saved.connect(self._on_browser_image_saved)
        self._background_download.failed.connect(self._on_background_download_failed)
        self._background_download.step_changed.connect(self._record_background_step)
        self.btn_choose_dir.clicked.connect(self._choose_dir)
        self.chk_background_image.toggled.connect(self._on_background_image_toggled)
        self.chk_manual.toggled.connect(self._on_manual_toggled)
        self._txt_hotkey_group.buttonClicked.connect(self._on_txt_hotkey_changed)
        self._mkdir_hotkey_group.buttonClicked.connect(self._on_mkdir_hotkey_changed)
        self.save_path.textChanged.connect(self._on_path_edited)
        self._closing = False
        self._browser_status_timer = QTimer(self)
        self._browser_status_timer.setInterval(1000)
        self._browser_status_timer.timeout.connect(self._update_browser_image_status)

        # 速存文本热键
        self._hotkey_id              = 0x1002
        self._hotkey_filter          = None
        self._hotkey_registered_key  = None

        self._sc_manual = QShortcut(QKeySequence(self._current_txt_key()), self)
        self._sc_manual.activated.connect(self.manual_fast_save)
        self._sc_manual.setEnabled(False)

        # 速建文件夹热键（顺序新建固定 F8）
        self._mkdir_hotkey_id     = 0x1003
        self._mkdir_hotkey_filter = None
        self._mkdir_hotkey_registered = False
        # 直接新建 A（F9）
        self._mkdir_direct_id     = 0x1004
        self._mkdir_direct_filter = None
        # 直接新建 B（F10）
        self._mkdir_direct_b_id     = 0x1005
        self._mkdir_direct_b_filter = None
        self._mkdir_direct_registered = False
        self._mkdir_direct_b_registered = False
        self.chk_mkdir.toggled.connect(self._on_mkdir_toggled)

        # ── F6 记录模式热键（独立于按钮，全局生效）──
        self._recording_hotkey_id = 0x1006
        self._recording_hotkey_filter = None
        self._recording_hotkey_registered = False
        self._register_recording_hotkey()

        self.clipboard = QApplication.clipboard()

        # ── F6 记录模式 ──
        self._recording = False
        self._record_feature_states = None
        self._record_file = ""
        self._record_count = 0
        self._record_base_count = 0  # 已有记录行数（继续记录时从文件读入）
        self._record_clip_ignore = False
        # 防重复：本会话内已写入 + 启动时从全文件载入的规范化行集合
        self._record_seen = set()
        self.clipboard.dataChanged.connect(self._on_record_clipboard)

        self._init_save_dir()
        # 偏好恢复是否已写过「速存已按习惯启用」摘要（防重复 apply 刷屏）
        self._prefs_restored_once = False
        # quiet 路径下「同类状态/失败」只记一次的 key 集合
        self._runlog_once_keys = set()

        self._saved_images = set()  # 已记录的图片路径

        # 启动时加载今天的记录文件按钮
        self._init_record_file()

        theme.changed.connect(self._apply_theme)

        # 保存目录由「关于」弹窗统一管理，页面 UI 隐藏
        self.save_path.setVisible(False)
        self.btn_choose_dir.setVisible(False)

    # ────────────────────────────────────────
    # 用户习惯（records/user.txt · fast_save 段）
    # ────────────────────────────────────────
    def export_settings(self) -> dict:
        """导出当前速存相关设置，供 user_prefs 落盘。"""
        images, text, mkdir = self._record_feature_states or self._fast_feature_states()
        return {
            "images_enabled": images,
            "text_enabled": text,
            "mkdir_enabled": mkdir,
            "save_path": (self.save_path.text() or "").strip(),
            "txt_hotkey": self._current_txt_key(),
            "mkdir_hotkey": self._current_mkdir_key(),
            "mkdir_name_f9": (self.mkdir_name.text() or "").strip() or "Grok",
            "mkdir_name_f10": (self.mkdir_name_b.text() or "").strip() or "Qwen",
            "record_random_name": bool(self.chk_new_record.isChecked()),
        }

    def apply_settings(self, d: dict):
        """从 user.txt 恢复设置。未知/空字段保持现状。"""
        if not isinstance(d, dict):
            return
        path = (d.get("save_path") or "").strip()
        if path:
            self.save_path.blockSignals(True)
            try:
                self.save_path.setText(path)
            finally:
                self.save_path.blockSignals(False)
            self._require_writable_path(interactive=False)

        txt_key = str(d.get("txt_hotkey") or "").upper()
        if txt_key in getattr(self, "_txt_radio", {}):
            self._txt_radio[txt_key].setChecked(True)
            if hasattr(self, "_sc_manual"):
                self._sc_manual.setKey(QKeySequence(txt_key))

        mkdir_key = str(d.get("mkdir_hotkey") or "").upper()
        if mkdir_key == "F7":
            mkdir_key = "F8"  # 旧配置 F7 迁移为 F8
        if mkdir_key in getattr(self, "_mkdir_radio", {}):
            self._mkdir_radio[mkdir_key].setChecked(True)

        name_f9 = (d.get("mkdir_name_f9") or "").strip()
        if name_f9:
            self.mkdir_name.setText(name_f9)
        name_f10 = (d.get("mkdir_name_f10") or "").strip()
        if name_f10:
            self.mkdir_name_b.setText(name_f10)

        record_random = d.get("record_random_name")
        if record_random is not None and hasattr(self, "chk_new_record"):
            self.chk_new_record.setChecked(bool(record_random))

        states = (
            bool(d.get("images_enabled")),
            bool(d.get("text_enabled")),
            bool(d.get("mkdir_enabled")),
        )
        if self._recording:
            self._record_feature_states = states
        else:
            self._set_fast_feature_states(states)
        # 仅首次从偏好恢复时写一行摘要；之后再 apply 不再追加
        if not getattr(self, "_prefs_restored_once", False):
            self._prefs_restored_once = True
            if self.is_img_active():
                p = (self.save_path.text() or "").strip() or path
                tip = "🟢 后台下载已按习惯启用"
                if p:
                    tip += f" → {p}"
                try:
                    self.list_widget.addItem(tip)
                except Exception:
                    pass

    def _apply_theme(self, *_args):
        """主题切换时重刷卡片外观（内联样式方案，不吃全局 QSS 级联，需手动重刷）。
        注：记录列表虚线框 / 下划线输入框 / 迷你按钮已迁移到 app.qss、app_light.qss 的
        属性选择器（recordStyle / inputStyle / kind），会随应用级样式表切换自动生效，
        这里只需要处理内联画色的部分：卡片外观、标题、次要文字、以及程序绘制的符号图标。"""
        for frame in self._theme_frames:
            restyle_card_frame(frame)
        for lbl in self._theme_titles:
            restyle_card_title(lbl)
        secondary = content_secondary_color()
        for lbl in self._theme_secondary_bold:
            lbl.setStyleSheet(f"background:transparent; color:{secondary}; font-weight:600;")
        for lbl in self._theme_secondary_plain:
            lbl.setStyleSheet(f"background:transparent; color:{secondary};")
        if hasattr(self, "save_path"):
            restyle_folder_path_edit(self.save_path, getattr(self, "_path_icon_action", None))
        for action, glyph in getattr(self, "_theme_glyph_actions", []):
            if action is getattr(self, "_path_icon_action", None):
                continue  # 已由 restyle_folder_path_edit 处理
            action.setIcon(self._make_glyph_icon(glyph, color=secondary))
        if hasattr(self, "preview"):
            self.preview.refresh_theme()
        if hasattr(self, "btn_record"):
            if getattr(self, "_record_btn_frozen", False):
                self._restyle_record_btn("frozen")
            else:
                self._restyle_record_btn("recording" if self._recording else "idle")
        if hasattr(self, "_record_grid_layout"):
            self._refresh_record_cards()
        self._restyle_hseps()

    _IMG_LOG_RE = re.compile(
        r"已保存图片[：:]\s*(.+?\.(?:jpg|jpeg|png|gif|bmp|webp|avif|tiff?|ico))",
        re.IGNORECASE,
    )
    _FOLDER_LOG_RE = re.compile(
        r"(?:速建文件夹成功|直接新建成功)[：:]\s*(.+)$"
    )
    _TEXT_LOG_RE = re.compile(
        r"速存文本成功[：:]\s*(.+)$"
    )

    @staticmethod
    def _is_remote_url(s: str) -> bool:
        s = (s or "").strip().lower()
        return s.startswith(("http://", "https://", "blob:", "data:"))

    def _runlog_kind(self, item) -> str:
        """当前记录类型：parsing | image | folder | textfile | fail | generic。"""
        if item is None:
            return "generic"
        text = item.text() or ""
        if "解析中" in text:
            return "parsing"
        stored = item.data(Qt.UserRole)
        if isinstance(stored, str) and self._is_remote_url(stored):
            return "parsing"
        if "已保存图片" in text:
            return "image"
        if "速建文件夹成功" in text or "直接新建成功" in text:
            return "folder"
        if "速存文本成功" in text:
            return "textfile"
        if "解析失败" in text:
            return "fail"
        return "generic"

    def _clear_preview(self, hint: str = None):
        """预览区回到占位（无图、无 meta）。"""
        try:
            self.preview.show_empty(hint)
            self.lbl_preview_meta.clear()
            self.lbl_preview_meta.setToolTip("")
            self.lbl_preview_meta.setVisible(False)
        except Exception:
            pass

    @staticmethod
    def _fmt_file_size(n: int) -> str:
        try:
            n = int(n)
        except (TypeError, ValueError):
            return ""
        if n < 0:
            return ""
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.0f} KB"
        if n < 1024 * 1024 * 1024:
            return f"{n / (1024 * 1024):.2f} MB"
        return f"{n / (1024 * 1024 * 1024):.2f} GB"

    def _set_image_preview_meta(self, path: str, pix) -> None:
        """图片预览下方信息：文件名 + 分辨率（必显）+ 体积；完整内容进 tooltip。"""
        name = os.path.basename(path) if path else ""
        w = int(pix.width()) if pix is not None and not pix.isNull() else 0
        h = int(pix.height()) if pix is not None and not pix.isNull() else 0
        size_s = ""
        try:
            if path and os.path.isfile(path):
                size_s = self._fmt_file_size(os.path.getsize(path))
        except Exception:
            size_s = ""

        # 第 2 行：分辨率固定在前，避免被长文件名挤掉（这是用户反馈的「少了分辨率」根因）
        res = f"{w}×{h}" if (w > 0 and h > 0) else "分辨率 —"
        line2 = f"{res}  ·  {size_s}" if size_s else res

        # 第 1 行：按标签宽度中间省略，保证始终只有两行落在 40px 内
        disp_name = name or "（未命名）"
        try:
            fm = self.lbl_preview_meta.fontMetrics()
            avail = max(40, self.lbl_preview_meta.width() - 8)
            if avail > 40:
                disp_name = fm.elidedText(disp_name, Qt.ElideMiddle, avail)
        except Exception:
            pass

        meta_text = f"{disp_name}\n{line2}"
        self.lbl_preview_meta.setText(meta_text)
        tip_lines = []
        if path:
            tip_lines.append(path)
        tip_lines.append(line2)
        self.lbl_preview_meta.setToolTip("\n".join(tip_lines))
        self.lbl_preview_meta.setVisible(True)

    def _runlog_stored_path(self, item) -> str:
        """UserRole 里存的本地路径（文件或文件夹）；远程 URL 不算。"""
        if item is None:
            return ""
        stored = item.data(Qt.UserRole)
        if not isinstance(stored, str):
            return ""
        stored = stored.strip()
        if not stored or self._is_remote_url(stored):
            return ""
        return stored

    def _resolve_runlog_image_path(self, item) -> str:
        """从运行记录条目解析本地图片路径。
        优先 Qt.UserRole；否则用「已保存图片」文案文件名 + 保存目录回退。
        """
        if item is None or self._runlog_kind(item) != "image":
            return ""
        stored = self._runlog_stored_path(item)
        if stored and os.path.isfile(stored):
            return stored

        text = item.text() or ""
        m = self._IMG_LOG_RE.search(text)
        if not m:
            return ""
        filename = m.group(1).strip().replace("/", os.sep).replace("\\", os.sep)
        save_dir = (self.save_path.text() or "").strip()
        candidates = []
        if save_dir:
            candidates.append(os.path.join(save_dir, filename))
            base = os.path.basename(filename)
            if base and base != filename:
                candidates.append(os.path.join(save_dir, base))
        for c in candidates:
            if c and os.path.isfile(c):
                return c
        return ""

    def _resolve_runlog_folder(self, item) -> tuple:
        """返回 (folder_path_or_empty, display_name)。"""
        text = item.text() or "" if item is not None else ""
        stored = self._runlog_stored_path(item)
        if stored and os.path.isdir(stored):
            return stored, os.path.basename(stored.rstrip("\\/")) or stored
        m = self._FOLDER_LOG_RE.search(text)
        name = (m.group(1).strip() if m else "").strip()
        # 文案里可能是完整路径
        if name and (os.path.isdir(name) or "\\" in name or "/" in name):
            leaf = os.path.basename(name.rstrip("\\/")) or name
            return (name if os.path.isdir(name) else ""), leaf
        return "", name

    def _resolve_runlog_textfile(self, item) -> tuple:
        """返回 (file_path_or_empty, display_name)。"""
        text = item.text() or "" if item is not None else ""
        stored = self._runlog_stored_path(item)
        if stored and os.path.isfile(stored):
            return stored, os.path.basename(stored)
        m = self._TEXT_LOG_RE.search(text)
        name = (m.group(1).strip() if m else "").strip()
        return "", name

    def _on_runlog_item_selected(self, current, _previous):
        """预览区始终绑定当前选中记录：
        · 无选中 → 默认占位
        · 解析中（空记录）→ 空预览
        · 已保存图片 → 图片 + 文件名/尺寸
        · 新建文件夹 → 文件夹图标 + 文件夹名
        · 速存文本 → 文本图标 + 文件名
        · 其它 → 记录原文
        """
        if current is None:
            self._clear_preview()
            return

        kind = self._runlog_kind(current)
        text = current.text() or ""

        # 解析中 = 这条记录本身还是空的，预览就是空
        if kind == "parsing":
            self._clear_preview("（解析中…）")
            return

        if kind == "image":
            full_path = self._resolve_runlog_image_path(current)
            if full_path:
                pix = QPixmap(full_path)
                if not pix.isNull():
                    self.preview.set_image(pix, full_path)
                    self._set_image_preview_meta(full_path, pix)
                    return
            # 图片路径失效：仍显示记录文案，不沿用其它记录的图
            self.preview.set_text_content(text)
            self.lbl_preview_meta.clear()
            self.lbl_preview_meta.setToolTip("")
            self.lbl_preview_meta.setVisible(False)
            return

        if kind == "folder":
            path, name = self._resolve_runlog_folder(current)
            self.preview.set_symbol_preview("📁", name or "文件夹", path)
            if path:
                self.lbl_preview_meta.setText(path)
                self.lbl_preview_meta.setToolTip(path)
                self.lbl_preview_meta.setVisible(True)
            else:
                self.lbl_preview_meta.clear()
                self.lbl_preview_meta.setToolTip("")
                self.lbl_preview_meta.setVisible(False)
            return

        if kind == "textfile":
            path, name = self._resolve_runlog_textfile(current)
            self.preview.set_symbol_preview("📄", name or "文本", path)
            if path:
                self.lbl_preview_meta.setText(path)
                self.lbl_preview_meta.setToolTip(path)
                self.lbl_preview_meta.setVisible(True)
            else:
                self.lbl_preview_meta.clear()
                self.lbl_preview_meta.setToolTip("")
                self.lbl_preview_meta.setVisible(False)
            return

        if kind == "fail":
            self.preview.set_symbol_preview("⚠️", "解析失败")
            self.lbl_preview_meta.clear()
            self.lbl_preview_meta.setToolTip("")
            self.lbl_preview_meta.setVisible(False)
            return

        # 其它操作/状态记录：显示原文
        self.preview.set_text_content(text)
        self.lbl_preview_meta.clear()
        self.lbl_preview_meta.setToolTip("")
        self.lbl_preview_meta.setVisible(False)

    # ────────────────────────────────────────
    # 对外接口（兼容旧版 server.py）
    # ────────────────────────────────────────
    @staticmethod
    def shared_queue(): return _SHARED_QUEUE
    def allow_accept(self): return False
    def drain_queue(self): pass

    def is_img_active(self):
        return self.chk_background_image.isChecked()

    def _fast_feature_states(self):
        return self.is_img_active(), self.chk_manual.isChecked(), self.chk_mkdir.isChecked()

    def _set_fast_feature_states(self, states):
        images, text, mkdir = states
        with QSignalBlocker(self.chk_background_image), QSignalBlocker(self.chk_manual), QSignalBlocker(self.chk_mkdir):
            self.chk_background_image.setChecked(images)
            self.chk_manual.setChecked(text)
            self.chk_mkdir.setChecked(mkdir)
        self._on_background_image_toggled(images, quiet=True)
        self._on_manual_toggled(text, quiet=True)
        self._on_mkdir_toggled(mkdir, quiet=True)

    def _apply_status_btn_style(self, color: str, text: str, bold: bool = False):
        """用色点和文字显示后台下载状态。"""
        weight = "600" if bold else "400"
        self.btn_status.setText("●  " + text)
        self.btn_status.setStyleSheet(
            f"QPushButton#FastStatusLamp{{background:transparent; color:{color};"
            f"border:none; border-radius:0; padding:0; margin:0;"
            f"font-weight:{weight}; font-size:13px; text-align:left;}}"
            f"QPushButton#FastStatusLamp:hover,"
            f"QPushButton#FastStatusLamp:pressed,"
            f"QPushButton#FastStatusLamp:focus{{"
            f"background:transparent; border:none; color:{color};}}"
        )

    def _update_browser_image_status(self):
        if not self.is_img_active():
            self._apply_status_btn_style("#f0a500", "已关闭")
        elif self._background_download.busy:
            self._apply_status_btn_style("#f0a500", "下载中")
        elif self._browser_image_save.running:
            status = "已识别图片" if self._hover_has_image else "等待 Alt+1"
            self._apply_status_btn_style("#4caf50", status, bold=True)
        else:
            self._apply_status_btn_style("#e05252", "浏览器监听未就绪")

    def _on_background_image_toggled(self, checked: bool, quiet: bool = False):
        if checked and not self._require_writable_path(interactive=not quiet):
            with QSignalBlocker(self.chk_background_image):
                self.chk_background_image.setChecked(False)
            checked = False
            msg = "⚠️ 保存路径不可用，已取消后台下载"
            if quiet:
                self._runlog_once("path_unusable", msg)
            else:
                self._log_problem(msg)
                quiet = True

        self._browser_image_save.configure(checked)
        self.background_operations.setVisible(checked)
        if checked:
            self._browser_status_timer.start()
            if not quiet:
                path = self.save_path.text().strip()
                self.list_widget.addItem(f"🟢 后台下载已启用 → {path}")
        else:
            self._browser_status_timer.stop()
            self._hover_has_image = False
            self._interrupt_background_capture("后台下载已关闭。")
            if not quiet:
                self.list_widget.addItem("⏹️ 后台下载已关闭")
        self._update_browser_image_status()
        self._resize_top_cards()
        self.background_enabled_changed.emit(checked)

    @staticmethod
    def _unique_path(path: str) -> str:
        if not os.path.exists(path):
            return path
        base, ext = os.path.splitext(path)
        i = 1
        while os.path.exists(f"{base} ({i}){ext}"):
            i += 1
        return f"{base} ({i}){ext}"

    def _log_problem(self, msg: str):
        """诊断/连接/错误类信息写入运行记录列表，自动拆行逐条追加并滚屏。"""
        try:
            for line in str(msg or "").split("\n"):
                if line.strip():
                    self.list_widget.addItem(line.strip())
            self.list_widget.scrollToBottom()
        except Exception:
            log.exception("写入速存运行记录失败 msg=%s", str(msg or "")[:200])

    def _runlog_once(self, key: str, msg: str) -> bool:
        """运行记录按 key 去重：同一 key 本会话只写一条（偏好 quiet 恢复用）。

        返回 True 表示本次已写入；False 表示此前已写过、已跳过。
        """
        keys = getattr(self, "_runlog_once_keys", None)
        if keys is None:
            self._runlog_once_keys = set()
            keys = self._runlog_once_keys
        if key in keys:
            return False
        keys.add(key)
        try:
            self.list_widget.addItem(msg)
            self.list_widget.scrollToBottom()
        except Exception:
            pass
        return True

    def _diagnose(self):
        if self.is_img_active():
            self._update_browser_image_status()
            self._log_problem(f"📁 后台下载保存到：{self.save_path.text().strip()}")
        else:
            self._log_problem("后台下载已关闭。")

    # ────────────────────────────────────────
    # 速存文本
    # ────────────────────────────────────────
    def _current_txt_key(self) -> str:
        for key, rb in self._txt_radio.items():
            if rb.isChecked():
                return key
        return "F4"

    def _on_txt_hotkey_changed(self, _=None):
        key = self._current_txt_key()
        if hasattr(self, "_sc_manual"):
            self._sc_manual.setKey(QKeySequence(key))
        # 无论是否启用，都在「运行记录」留一条改键记录
        self.list_widget.addItem(f"⌨️ 文本快捷键已切换为：{key}")
        self.list_widget.scrollToBottom()
        # 仅在已启用时才真正重注册全局热键
        if self.chk_manual.isChecked():
            self._register_txt_hotkey(announce=False)

    def _on_manual_toggled(self, checked: bool, quiet: bool = False):
        """速存文本开关。quiet=True 时不写启用/关闭状态行。"""
        if checked:
            self._sc_manual.setEnabled(True)
            self._register_txt_hotkey(announce=False, quiet=quiet)
            if not quiet:
                self.list_widget.addItem(
                    f"🟡 速存文本：已启用（按 {self._current_txt_key()} 在资源管理器创建 .txt）"
                )
        else:
            self._sc_manual.setEnabled(False)
            self._unregister_txt_hotkey()
            if not quiet:
                self.list_widget.addItem("⏹️ 速存文本：已关闭")

    def _retry_hotkey_later(self, fn, attempt: int, tag: str) -> bool:
        """RegisterHotKey 失败后延迟再试。attempt 是刚刚失败的那一次（从 0 起）。"""
        if getattr(self, "_closing", False):
            return False
        if attempt >= len(_HK_RETRY_MS):
            return False
        delay = int(_HK_RETRY_MS[attempt])
        log.info("热键稍后重试 tag=%s attempt=%s delay_ms=%s", tag, attempt + 1, delay)
        QTimer.singleShot(delay, fn)
        return True

    def _register_txt_hotkey(
        self, announce: bool = True, quiet: bool = False, _attempt: int = 0
    ) -> bool:
        if getattr(self, "_closing", False):
            return False
        if _attempt and not self.chk_manual.isChecked():
            return False
        key = self._current_txt_key()
        if getattr(self, "_hotkey_registered_key", None) == key:
            return True
        self._unregister_txt_hotkey()
        vk = VK_CODE.get(key, VK_CODE["F4"])
        try:
            ok, err = _win_register_hotkey(self._hotkey_id, vk)
        except Exception as e:
            msg = f"❌ 注册热键 {key} 异常：{e}"
            if quiet:
                self._runlog_once(f"txt_hk_exc:{key}", msg)
            else:
                self.list_widget.addItem(msg)
            self._hotkey_registered_key = None
            return False
        if not ok:
            extra = f"（错误码 {err}）" if err else ""
            retrying = self._retry_hotkey_later(
                lambda a=_attempt + 1: self._register_txt_hotkey(
                    announce=False, quiet=True, _attempt=a
                ),
                _attempt,
                f"txt:{key}",
            )
            if _attempt == 0:
                msg = f"❌ 注册热键 {key} 失败，可能被其它程序占用{extra}"
                if retrying:
                    msg += "，稍后重试"
                if quiet:
                    self._runlog_once(f"txt_hk_fail:{key}", msg)
                else:
                    self.list_widget.addItem(msg)
            elif not retrying:
                log.warning("注册热键 %s 多次失败 winerr=%s", key, err)
            self._hotkey_registered_key = None
            return False
        if self._hotkey_filter is None:
            self._hotkey_filter = _GlobalHotkeyFilter(self.manual_fast_save, self._hotkey_id)
            QCoreApplication.instance().installNativeEventFilter(self._hotkey_filter)
        self._hotkey_registered_key = key
        if _attempt > 0:
            self.list_widget.addItem(f"🟡 热键 {key} 已就绪")
            self.list_widget.scrollToBottom()
        elif announce and not quiet:
            self.list_widget.addItem(f"🟡 速存文本全局热键就绪：{key}")
        return True

    def _unregister_txt_hotkey(self):
        try:
            _win_unregister_hotkey(self._hotkey_id)
        except Exception:
            # 未注册过 / 系统调用失败等情况都不应影响页面正常构造与运行
            log.debug("注销速存热键失败", exc_info=True)
        finally:
            self._hotkey_registered_key = None


    def _paste_box_text(self) -> str:
        """粘贴助手内容框。剪贴板纯文本没有汉字时，用这里正在显示的正文。"""
        win = self.window()
        page = getattr(win, "page_paste", None)
        edit = getattr(page, "inp_content", None)
        if edit is None:
            return ""
        try:
            return (edit.toPlainText() or "").strip()
        except Exception:
            return ""

    def manual_fast_save(self):
        if not self.chk_manual.isChecked():
            self.list_widget.addItem("⚠️ 请先启用速存文本")
            return
        if not _WIN_OK:
            self.list_widget.addItem("❌ 手动功能不可用：缺少 pywin32/pyperclip")
            return
        try:
            fg_hwnd = win32gui.GetForegroundWindow()
            if not fg_hwnd:
                self.list_widget.addItem("⚠️ 无法获取前台窗口，请先点击资源管理器再按快捷键")
                return

            clip_text = _read_fast_clip_text()

            doc, n_tabs = self._resolve_active_view(fg_hwnd, prefer_selected=True)
            selected_file = ""
            dest_dir = ""
            if doc is not None:
                try:
                    sel = doc.SelectedItems()
                    count = int(sel.Count)
                except Exception:
                    self.list_widget.addItem("❌ 无法读取选中项")
                    return
                if count > 1:
                    self.list_widget.addItem(
                        f"⚠️ 选中了 {count} 个项目，每次只能对 1 个文件操作，请重新选择"
                    )
                    return
                if count == 1:
                    item = sel.Item(0)
                    target_path = item.Path
                    if os.path.isdir(target_path):
                        self.list_widget.addItem(
                            f"⚠️ 选中的是文件夹，不支持对文件夹操作：{os.path.basename(target_path)}"
                        )
                        return
                    selected_file = target_path
                else:
                    dest_dir = self._view_path(doc)
            if not selected_file and not dest_dir:
                dest_dir = (self.save_path.text() or "").strip()

            if selected_file:
                self.list_widget.addItem(
                    f"📋 剪贴板{'有内容' if clip_text else '无内容（将写入空文件）'}"
                )
                txt_path = os.path.splitext(selected_file)[0] + ".txt"
                self._write_fast_txt(txt_path, clip_text)
                return

            if not _hanzi_count(clip_text):
                box = self._paste_box_text()
                if _hanzi_count(box):
                    clip_text = box
            if not clip_text:
                self.list_widget.addItem("⚠️ 剪贴板没有文本，无法创建文件")
                self.list_widget.scrollToBottom()
                return
            ok_dir, dest_dir, err = self._ensure_writable_dir(dest_dir, create=True)
            if not ok_dir:
                where = "当前文件夹" if n_tabs else "保存目录"
                self.list_widget.addItem(f"⚠️ {where}不可写：{dest_dir or '（空）'}（{err}）")
                self.list_widget.scrollToBottom()
                return
            stem = _fast_txt_stem(clip_text)
            txt_path = _unique_txt_path(dest_dir, stem, clip_text, self._txt_already_saved)
            if txt_path is None:
                base = stem + ".txt"
                self.list_widget.addItem(f"⏭️ 已忽略重复记录：{base} 内容与已保存相同")
                self.list_widget.scrollToBottom()
                return
            self.list_widget.addItem(f"📋 未选中文件，按程序名创建：{os.path.basename(txt_path)}")
            self._write_fast_txt(txt_path, clip_text)
        except Exception as e:
            log.exception("速存文本失败")
            self.list_widget.addItem(f"❌ 速存文本错误：{e}")

    def _write_fast_txt(self, txt_path: str, clip_text: str):
        if self._txt_already_saved(txt_path, clip_text):
            self.list_widget.addItem(
                f"⏭️ 已忽略重复记录：{os.path.basename(txt_path)} 内容与已保存相同"
            )
            self.list_widget.scrollToBottom()
            return
        try:
            with open(txt_path, "w", encoding="utf-8") as fp:
                fp.write(clip_text)
            self._push_record(
                f"✅ 速存文本成功：{os.path.basename(txt_path)}",
                select=True,
                filepath=txt_path,
            )
        except Exception as e:
            log.exception("速存文本写入失败 path=%s", txt_path)
            self.list_widget.addItem(f"❌ 写入失败：{txt_path}（{e}）")
        self.list_widget.scrollToBottom()

    def _txt_already_saved(self, txt_path: str, content: str) -> bool:
        """目标 .txt 已存在且内容与当前内容完全一致 → 视为重复记录。"""
        try:
            if not os.path.isfile(txt_path):
                return False
            with open(txt_path, "r", encoding="utf-8") as fp:
                return fp.read() == content
        except Exception:
            return False

    # ────────────────────────────────────────
    # 公共工具
    # ────────────────────────────────────────
    def _push_record(self, text: str, select: bool = False, filepath: str = None):
        """速存操作成功时统一调用：追加运行记录 → 滚到底部显示最新 →
        选中该条（联动右侧预览）→ 请求主窗口切到「速存图文」页。
        自动去重：同一图片路径不会重复记录「已保存图片」。
        filepath：本地完整路径（图片文件 / 文件夹 / 文本文件均可），
        写入条目 UserRole 供预览区稳定加载。"""
        path = (filepath or "").strip()
        m = self._IMG_LOG_RE.search(text)
        if m:
            key = os.path.normcase(os.path.abspath(path)) if path else m.group(1).strip().lower()
            if key in self._saved_images:
                return
            self._saved_images.add(key)
        try:
            item = QListWidgetItem(text)
            if path and (os.path.isfile(path) or os.path.isdir(path)):
                path = os.path.abspath(path)
                item.setData(Qt.UserRole, path)
            elif m:
                # 未显式传入时，尽量用当前保存目录拼出路径
                guess = os.path.join(
                    (self.save_path.text() or "").strip(), m.group(1).strip()
                )
                if os.path.isfile(guess):
                    guess = os.path.abspath(guess)
                    item.setData(Qt.UserRole, guess)
            self.list_widget.addItem(item)
            # addItem 已：选中最新 + 滚屏；select 参数保留语义兼容
            if select:
                self.list_widget._focus_latest(force_select=True)
        except Exception:
            pass
        self.request_show.emit()

    def _build_background_operations(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 5, 0, 0)
        layout.setSpacing(4)
        header = QHBoxLayout()
        header.addWidget(QLabel("操作记录"))
        header.addStretch()
        self._open_background_log = QPushButton("打开日志")
        self._open_background_log.setFlat(True)
        self._open_background_log.clicked.connect(self._view_background_log)
        header.addWidget(self._open_background_log)
        layout.addLayout(header)
        self._background_history = QComboBox()
        self._background_history.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self._background_history.setAccessibleName("后台下载操作记录")
        self._background_history.currentIndexChanged.connect(self._show_background_operation)
        layout.addWidget(self._background_history)

        steps = QVBoxLayout()
        steps.setSpacing(2)
        self._background_step_labels = {}
        for key, title in BACKGROUND_STEPS:
            row = QHBoxLayout()
            row.setSpacing(5)
            lamp = QLabel()
            lamp.setFixedSize(8, 8)
            label = QLabel(title)
            row.addWidget(lamp, 0, Qt.AlignVCenter)
            row.addWidget(label)
            row.addStretch()
            steps.addLayout(row)
            self._background_step_labels[key] = (lamp, label)
        layout.addLayout(steps)
        self._background_operation_detail = _ElidedLabel()
        self._typo(self._background_operation_detail, "muted")
        layout.addWidget(self._background_operation_detail)
        return panel

    def _view_background_log(self):
        path = self._background_log.path
        if not path.is_file() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self._log_problem(f"无法打开后台下载日志：{path}")

    def _refresh_background_operations(self, follow_latest=False):
        history = self._background_history
        current = None if follow_latest else history.currentData()
        states = {"ok": "成功", "failed": "失败", "pending": "进行中"}
        with QSignalBlocker(history):
            history.clear()
            for entry in reversed(self._background_log.records):
                started = entry.get("started_at", "")[5:19].replace("T", " ")
                history.addItem(f"{started}  ·  {states.get(entry.get('status'), '进行中')}", entry["id"])
            if not history.count():
                history.addItem("暂无操作")
            index = history.findData(current) if current is not None else -1
            history.setCurrentIndex(max(index, 0))
        self._open_background_log.setEnabled(self._background_log.path.is_file())
        self._open_background_log.setToolTip(str(self._background_log.path))
        self._show_background_operation()

    def _show_background_operation(self, _=None):
        history = self._background_history
        history.setToolTip(history.currentText())
        entry = self._background_log.find(history.currentData())
        states = {
            "ok": ("#4caf50", "通过"), "failed": ("#e05252", "失败"),
            "pending": ("#8a909c", "待执行"), "skipped": ("#8a909c", "未执行"),
        }
        summary = ""
        for key, (lamp, label) in self._background_step_labels.items():
            step = entry["steps"].get(key, {}) if entry else {}
            state = step.get("status", "pending")
            color, status = states.get(state, states["pending"])
            detail = step.get("detail", "")
            lamp.setStyleSheet(f"background: {color}; border-radius: 4px;")
            lamp.setAccessibleName(status)
            tooltip = f"{status} · {detail}" if detail else status
            lamp.setToolTip(tooltip)
            label.setToolTip(tooltip)
            if state == "failed":
                summary = detail
            elif key == "save" and state == "ok":
                summary = f"已保存 {os.path.basename(detail)}"
        self._background_operation_detail.set_full_text(summary)
        self._background_operation_detail.setToolTip(summary)

    def _record_background_step(self, operation_id, step, passed, detail):
        if not self._closing and self._background_log.step(operation_id, step, passed, detail):
            self._refresh_background_operations()

    def _interrupt_background_capture(self, reason):
        if self._background_captures:
            if self._background_log.interrupt(reason, self._background_captures):
                self._refresh_background_operations()
            self._background_captures.clear()

    def _on_browser_image_saved(self, path: str):
        if not self._closing:
            self._push_record(f"✅ 已保存图片：{os.path.basename(path)}", select=True, filepath=path)
            if self.chk_background_image.isChecked():
                self._update_browser_image_status()

    def _on_hover_image_triggered(self, operation_id: str, browser: str):
        if self._closing:
            return
        self._background_log.start(operation_id, browser)
        if self.chk_background_image.isChecked():
            self._background_captures.add(operation_id)
        else:
            self._background_log.step(operation_id, "source", False, "后台下载模式已关闭。")
        self._refresh_background_operations(follow_latest=True)

    def _on_hover_image_link(self, operation_id: str, address: str, reason: str):
        if self._closing or operation_id not in self._background_captures:
            return
        self._background_captures.discard(operation_id)
        url = image_http_url(address)
        if not url:
            reason = reason or "后台下载只支持 HTTP 或 HTTPS 图片链接。"
            self._record_background_step(operation_id, "source", False, reason)
            self._log_problem(reason)
            return
        origin = f"来源：{urlsplit(url).hostname}"
        self._record_background_step(operation_id, "source", True, f"{reason} {origin}" if reason else origin)
        folder = self.save_path.text().strip()
        if not folder:
            reason = "未设置速存图片目录。"
            self._record_background_step(operation_id, "process", False, reason)
            self._log_problem(reason)
            return
        self._background_download.submit(url, folder, operation_id)
        self._update_browser_image_status()

    def _on_hover_image_status(self, ready: bool):
        if not self._closing and self.chk_background_image.isChecked():
            self._hover_has_image = ready
            self._update_browser_image_status()

    def _on_background_download_failed(self, message: str):
        if not self._closing:
            self._log_problem(f"❌ 后台下载失败：{message}")
            if self.chk_background_image.isChecked():
                self._update_browser_image_status()

    def _init_save_dir(self):
        # 仅设默认路径；不写运行记录——随后 apply_settings 会恢复用户路径，
        # 写「初始路径」容易与真实归档目录不一致，且造成启动噪声。
        path = self._resolve_default_dir()
        self.save_path.setText(path)

    def _resolve_default_dir(self) -> str:
        """速存默认保存目录。

        只考虑标准位置，绝不在用户主目录根部或当前工作目录下建文件夹：
        早期版本把 ~/WebImageSaver 和 os.getcwd()/WebImageSaver 也当候选，
        从家目录命令行启动时会把 C:\\Users\\<你> 当数据落点，
        看起来像是程序往家目录里写东西。
        """
        candidates = []
        pic = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
        if pic:
            candidates.append(os.path.join(pic, "WebImageSaver"))
        try:
            from utils.app_paths import this_machine_downloads_dir
            dl = (this_machine_downloads_dir() or "").strip()
            if dl:
                candidates.append(os.path.join(dl, "WebImageSaver"))
        except Exception:
            pass
        for p in candidates:
            ok, norm, _ = self._ensure_writable_dir(p, create=True)
            if ok:
                return norm
        folder = QFileDialog.getExistingDirectory(self, "选择保存文件夹")
        if folder:
            ok, norm, _ = self._ensure_writable_dir(folder, create=True)
            if ok:
                return norm
        # 都不行：退到程序自己的 data/，不碰主目录和 CWD
        try:
            from utils.app_paths import app_root
            fallback = os.path.join(app_root(), "WebImageSaver")
        except Exception:
            fallback = os.path.join(tempfile.gettempdir(), "DesktopAssistant_WebImageSaver")
        os.makedirs(fallback, exist_ok=True)
        return os.path.abspath(fallback)

    def _ensure_writable_dir(self, path, create=True):
        try:
            if not path: return False, "", "空路径"
            if create:
                try:
                    from utils.app_paths import is_other_user_profile_path
                    if is_other_user_profile_path(path):
                        return False, path, "其他用户目录"
                except Exception:
                    pass
                os.makedirs(path, exist_ok=True)
            tf = os.path.join(path, "__w_test__.tmp")
            with open(tf, "w") as f: f.write("ok")
            os.remove(tf)
            return True, os.path.abspath(path), ""
        except Exception as e:
            return False, path, str(e)

    def _require_writable_path(self, interactive=True) -> bool:
        path = self.save_path.text().strip()
        ok, norm, err = self._ensure_writable_dir(path, create=True)
        if ok:
            self.save_path.setStyleSheet("")
            if norm != path: self.save_path.setText(norm)
            return True
        self.save_path.setStyleSheet(f"border: 1px solid {tk('err')};")
        if not interactive:
            return False
        if path: self.list_widget.addItem(f"⚠️ 路径不可用：{path}（{err}）")
        while True:
            folder = QFileDialog.getExistingDirectory(self, "选择保存文件夹")
            if not folder: return False
            ok2, norm2, err2 = self._ensure_writable_dir(folder, create=True)
            if ok2:
                self.save_path.setStyleSheet("")
                self.save_path.setText(norm2)
                return True
            self.list_widget.addItem(f"❌ 该目录不可写：{folder}（{err2}），请重新选择")

    def _on_path_edited(self, _=None):
        writable = self._require_writable_path(interactive=False)
        if self.is_img_active():
            if not writable:
                self._hover_has_image = False
                self._interrupt_background_capture("速存图片目录不可用。")
            self._browser_image_save.configure(writable)
            self._update_browser_image_status()

    def _choose_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "选择保存文件夹")
        if not folder: return
        self.save_path.setText(folder)
        if self._require_writable_path(interactive=True):
            path = self.save_path.text().strip()
            self.list_widget.addItem(f"📁 保存路径已改为：{path}")
            self.list_widget.scrollToBottom()

    # ────────────────────────────────────────
    # 生命周期
    # ────────────────────────────────────────
    def hideEvent(self, e):
        try: self._sc_manual.setEnabled(False)
        finally: super().hideEvent(e)

    def showEvent(self, e):
        if not hasattr(self, "_container"):
            self._bind_container_switch()
        super().showEvent(e)
        if self.chk_manual.isChecked():
            self._sc_manual.setEnabled(True)
            self._register_txt_hotkey(announce=False)

    # ────────────────────────────────────────
    # 速建文件夹
    # ────────────────────────────────────────
    @staticmethod
    def _next_folder_name(name: str) -> str:
        """
        推算下一个文件夹名：自动定位名称中的数字段并加 1，保留其余文字与位数。
          纯数字            511      → 512
          数字在结尾        H04      → H05 ；记录8 → 记录9
          数字在开头        5单元    → 6单元
          数字在中间        第2章    → 第3章
          含多段数字        2024第1季 → 2024第2季（默认对最后一段数字加 1）
          完全不含数字      服装     → 服装002
        位数处理：进位不足则补零保持原宽度（04→05）；超出原宽度则不截断（99→100，第9章→第10章）。
        """
        matches = list(re.finditer(r'\d+', name))
        if not matches:
            # 完全没有数字：追加 002 作为起始序号
            return name + "002"
        m = matches[-1]                       # 默认对最后一段数字加 1
        num_str = m.group(0)
        next_str = str(int(num_str) + 1).zfill(len(num_str))  # zfill 只补零、超宽不截断
        return name[:m.start()] + next_str + name[m.end():]

    @staticmethod
    def _next_available_folder_name(parent_dir: str, name: str) -> str:
        """
        目录感知的顺序新建：不是简单对选中名 +1，而是扫描同级目录里所有
        「同前缀 + 数字段 + 同后缀」的文件夹，取其中最大编号再 +1，从而自动跳过
        已存在的编号，一次建到位。
          选中 09，目录已有 10、11            → 12
          选中 H16B，目录已有 H15B/H16B/H17B → H18B
        位宽沿用选中名数字段的宽度（zfill 补零、超宽不截断）；若结果仍意外存在则
        继续 +1 直到空位。名称完全不含数字时，退回 name + 三位序号 起始。
        """
        matches = list(re.finditer(r'\d+', name))
        if not matches:
            # 无数字：从 name002 起向后找第一个空位
            n, width = 2, 3
            while True:
                cand = f"{name}{str(n).zfill(width)}"
                if not os.path.exists(os.path.join(parent_dir, cand)):
                    return cand
                n += 1

        m = matches[-1]                       # 定位最后一段数字
        prefix, suffix = name[:m.start()], name[m.end():]
        width  = len(m.group(0))
        max_num = int(m.group(0))             # 起点至少是选中名本身的编号

        # 扫描同级目录里「同前缀、同后缀」的文件夹，取最大编号
        pat = re.compile(r'^' + re.escape(prefix) + r'(\d+)' + re.escape(suffix) + r'$')
        try:
            for entry in os.listdir(parent_dir):
                if os.path.isdir(os.path.join(parent_dir, entry)):
                    mm = pat.match(entry)
                    if mm:
                        max_num = max(max_num, int(mm.group(1)))
        except OSError:
            pass

        # 从 max_num+1 起，找第一个不存在的编号
        n = max_num + 1
        while True:
            cand = prefix + str(n).zfill(width) + suffix
            if not os.path.exists(os.path.join(parent_dir, cand)):
                return cand
            n += 1

    def _current_mkdir_key(self) -> str:
        """当前顺序新建所选热键（仅 F8）。"""
        for key, rb in self._mkdir_radio.items():
            if rb.isChecked():
                return key
        return "F8"

    def _register_mkdir_seq(self, announce: bool = True, _attempt: int = 0) -> bool:
        """注册（或按所选键重注册）顺序新建热键。

        已成功注册且键未变时直接返回 True，避免再次 apply 时
        Unregister→Register 竞态或被占用时反复失败刷屏。
        """
        if getattr(self, "_closing", False):
            return False
        if _attempt and not self.chk_mkdir.isChecked():
            return False
        seq_key = self._current_mkdir_key()
        if (
            getattr(self, "_mkdir_hotkey_registered", False)
            and getattr(self, "_mkdir_hotkey_key", None) == seq_key
        ):
            return True
        _win_unregister_hotkey(self._mkdir_hotkey_id)
        try:
            ok, err = _win_register_hotkey(self._mkdir_hotkey_id, VK_CODE[seq_key])
        except Exception:
            ok, err = False, -1
        if not ok:
            self._mkdir_hotkey_registered = False
            self._mkdir_hotkey_key = None
            retrying = self._retry_hotkey_later(
                lambda a=_attempt + 1: self._register_mkdir_seq(
                    announce=False, _attempt=a
                ),
                _attempt,
                f"mkdir:{seq_key}",
            )
            if _attempt == 0:
                log.warning(
                    "注册顺序新建热键失败 key=%s winerr=%s retrying=%s",
                    seq_key, err, retrying,
                )
            return False
        if self._mkdir_hotkey_filter is None:
            self._mkdir_hotkey_filter = _GlobalHotkeyFilter(
                self.mkdir_fast_create, self._mkdir_hotkey_id
            )
            QCoreApplication.instance().installNativeEventFilter(self._mkdir_hotkey_filter)
        self._mkdir_hotkey_registered = True
        self._mkdir_hotkey_key = seq_key
        if _attempt > 0:
            self.list_widget.addItem(f"🟡 热键 {seq_key} 已就绪")
            self.list_widget.scrollToBottom()
        elif announce:
            self.list_widget.addItem(f"⌨️ 顺序新建热键切换为：{seq_key}")
        return True

    def _on_mkdir_hotkey_changed(self, _=None):
        """切换顺序新建可选键：始终记录一条；仅已启用时才真正重注册热键。"""
        seq_key = self._current_mkdir_key()
        self.list_widget.addItem(f"⌨️ 顺序新建热键已切换为：{seq_key}")
        self.list_widget.scrollToBottom()
        if self.chk_mkdir.isChecked():
            self._register_mkdir_seq(announce=False)

    def _on_mkdir_toggled(self, checked: bool, quiet: bool = False):
        """速建文件夹开关。quiet=True 时不写启用/关闭状态行；热键失败最多记一次。"""
        if checked:
            seq_key = self._current_mkdir_key()
            ok_seq = self._register_mkdir_seq(announce=False)
            if not ok_seq:
                msg = f"❌ 注册 {seq_key} 热键失败，可能被其他程序占用，稍后重试"
                if quiet:
                    self._runlog_once(f"mkdir_seq_fail:{seq_key}", msg)
                else:
                    self.list_widget.addItem(msg)
                # F8 失败不挡住 F9/F10；顺序键会自己延迟再注册

            # F9 / F10：先 Unregister 再 Register，保证关闭后再开 / 再次 apply 均幂等
            for _hid in (self._mkdir_direct_id, self._mkdir_direct_b_id):
                _win_unregister_hotkey(_hid)

            # 注册 F9「直接新建 A」
            try:
                ok9, _err9 = _win_register_hotkey(self._mkdir_direct_id, VK_CODE["F9"])
            except Exception:
                ok9 = False
            self._mkdir_direct_registered = bool(ok9)
            if ok9 and self._mkdir_direct_filter is None:
                self._mkdir_direct_filter = _GlobalHotkeyFilter(
                    self.mkdir_direct_create_a, self._mkdir_direct_id
                )
                QCoreApplication.instance().installNativeEventFilter(self._mkdir_direct_filter)

            # 注册 F10「直接新建 B」
            try:
                ok10, _err10 = _win_register_hotkey(self._mkdir_direct_b_id, VK_CODE["F10"])
            except Exception:
                ok10 = False
            self._mkdir_direct_b_registered = bool(ok10)
            if ok10 and self._mkdir_direct_b_filter is None:
                self._mkdir_direct_b_filter = _GlobalHotkeyFilter(
                    self.mkdir_direct_create_b, self._mkdir_direct_b_id
                )
                QCoreApplication.instance().installNativeEventFilter(self._mkdir_direct_b_filter)

            if quiet:
                if not (ok9 and ok10):
                    failed = " ".join(k for k, ok in (("F9", ok9), ("F10", ok10)) if not ok)
                    self._runlog_once(
                        f"mkdir_direct_fail:{failed}",
                        f"⚠️ 速建文件夹：{failed} 热键注册失败（可能被占用）",
                    )
            elif ok9 and ok10:
                self.list_widget.addItem(
                    f"🟢 速建文件夹：已启用（{seq_key} 顺序新建 / F9 · F10 直接新建 就绪）"
                )
            else:
                failed = " ".join(k for k, ok in (("F9", ok9), ("F10", ok10)) if not ok)
                self.list_widget.addItem(
                    f"🟢 速建文件夹：{seq_key} 已启用；⚠️ {failed} 注册失败（可能被占用）"
                )
        else:
            self._unregister_mkdir_hotkey()
            if not quiet:
                self.list_widget.addItem("⏹️ 速建文件夹：已关闭")

    def _register_recording_hotkey(self, _attempt: int = 0):
        """注册 F6 全局热键：按下切换记录模式。"""
        if getattr(self, "_closing", False):
            return False
        _win_unregister_hotkey(self._recording_hotkey_id)
        try:
            ok, err = _win_register_hotkey(self._recording_hotkey_id, VK_CODE["F6"])
        except Exception:
            ok, err = False, -1
        if not ok:
            retrying = self._retry_hotkey_later(
                lambda a=_attempt + 1: self._register_recording_hotkey(_attempt=a),
                _attempt,
                "rec:F6",
            )
            if _attempt == 0:
                log.warning("注册 F6 热键失败 winerr=%s retrying=%s", err, retrying)
            self._recording_hotkey_filter = None
            self._recording_hotkey_registered = False
            return False
        if self._recording_hotkey_filter is None:
            self._recording_hotkey_filter = _GlobalHotkeyFilter(
                self._toggle_record_mode, self._recording_hotkey_id
            )
            QCoreApplication.instance().installNativeEventFilter(self._recording_hotkey_filter)
        self._recording_hotkey_registered = True
        if _attempt > 0:
            log.info("F6 热键重试成功 attempt=%s", _attempt)
        return True

    def _unregister_recording_hotkey(self):
        _win_unregister_hotkey(self._recording_hotkey_id)
        self._recording_hotkey_filter = None
        self._recording_hotkey_registered = False

    def _block_mkdir(self, yes: bool):
        try: self.chk_mkdir.blockSignals(yes)
        except Exception: pass

    def _unregister_mkdir_hotkey(self):
        _win_unregister_hotkey(self._mkdir_hotkey_id)
        _win_unregister_hotkey(self._mkdir_direct_id)
        _win_unregister_hotkey(self._mkdir_direct_b_id)
        self._mkdir_hotkey_registered = False
        self._mkdir_hotkey_key = None
        self._mkdir_direct_registered = False
        self._mkdir_direct_b_registered = False

    def hotkey_inventory(self):
        """配置页清单：速存相关全局热键及当前占用状态。"""
        def row(name, key, state):
            return {"group": "速存图文", "name": name, "key": key, "state": state}

        rows = [row("速存图片", "Alt+1", "plugin")]
        txt_key = self._current_txt_key()
        if not self.chk_manual.isChecked():
            txt_st = "off"
        elif getattr(self, "_hotkey_registered_key", None) == txt_key:
            txt_st = "ok"
        else:
            txt_st = "busy"
        rows.append(row("速存文本", txt_key, txt_st))
        rec_st = "ok" if getattr(self, "_recording_hotkey_registered", False) else "busy"
        rows.append(row("记录 / 批处理中止", "F6", rec_st))
        mk = self._current_mkdir_key()
        if not self.chk_mkdir.isChecked():
            rows.append(row("顺序新建文件夹", mk, "off"))
            rows.append(row("直接新建 A", "F9", "off"))
            rows.append(row("直接新建 B", "F10", "off"))
        else:
            rows.append(row(
                "顺序新建文件夹", mk,
                "ok" if getattr(self, "_mkdir_hotkey_registered", False) else "busy",
            ))
            rows.append(row(
                "直接新建 A", "F9",
                "ok" if getattr(self, "_mkdir_direct_registered", False) else "busy",
            ))
            rows.append(row(
                "直接新建 B", "F10",
                "ok" if getattr(self, "_mkdir_direct_b_registered", False) else "busy",
            ))
        return rows

    def rebind_hotkeys(self):
        """注销后按当前开关重注册。返回 [(名称, 按键, 成功), ...]。"""
        out = []
        ok_f6 = bool(self._register_recording_hotkey())
        out.append(("记录 / 批处理中止", "F6", ok_f6))
        if self.chk_manual.isChecked():
            self._hotkey_registered_key = None
            key = self._current_txt_key()
            ok = bool(self._register_txt_hotkey(announce=False, quiet=True))
            out.append(("速存文本", key, ok))
        if self.chk_mkdir.isChecked():
            self._mkdir_hotkey_registered = False
            self._mkdir_hotkey_key = None
            self._on_mkdir_toggled(True, quiet=True)
            mk = self._current_mkdir_key()
            out.append((
                "顺序新建文件夹", mk,
                bool(getattr(self, "_mkdir_hotkey_registered", False)),
            ))
            out.append((
                "直接新建 A", "F9",
                bool(getattr(self, "_mkdir_direct_registered", False)),
            ))
            out.append((
                "直接新建 B", "F10",
                bool(getattr(self, "_mkdir_direct_b_registered", False)),
            ))
        return out

    # ────────────────────────────────────────
    # 资源管理器（含 Win10/11 多标签）活动视图解析
    # ────────────────────────────────────────

    @staticmethod
    def _view_path(doc) -> str:
        """取某个文件夹视图当前所在目录，失败返回空串。

        注意：不能用 callable() 来区分「属性」和「方法」——
        pywin32 的 COM 对象实现了 __call__（默认属性），callable() 恒为真，
        误判会导致把属性当方法调用而取不到路径。这里改为直接按顺序试。
        """
        # 路径 1：pywin32（ShellFolderView）——属性式访问
        try:
            p = doc.Folder.Self.Path
            if p:
                return p
        except Exception:
            pass
        # 路径 2：comtypes dynamic 包装——方法式访问
        try:
            p = doc.Folder().Self().Path
            if p:
                return p
        except Exception:
            pass
        return ""

    @staticmethod
    def _children_by_zorder(hwnd):
        """按 z-order（从最顶层开始）列出 hwnd 的直接子窗口。"""
        import win32con
        res = []
        try:
            h = win32gui.GetWindow(hwnd, win32con.GW_CHILD)   # z-order 最顶的子窗口
            guard = 0
            while h and guard < 256:
                res.append(h)
                h = win32gui.GetWindow(h, win32con.GW_HWNDNEXT)
                guard += 1
        except Exception:
            pass
        return res

    def _active_tab_hwnd(self, fg_hwnd):
        """取「当前活动标签」的控件窗口句柄（Win11 标签式资源管理器）。

        依据：每个标签都有自己的 ShellTabWindowClass 控件窗口，
        而【活动标签的控件窗口始终位于 z-order 最顶端】。
        注意：不能用「窗口是否可见」来判断 —— 对 Win11 资源管理器无效，
        非活动标签的窗口同样报告为可见（这正是之前几版失败的原因）。

        无标签（Win10 原生）时返回 0，调用方直接用主窗口即可。
        """
        for h in self._children_by_zorder(fg_hwnd):
            try:
                if win32gui.GetClassName(h) == "ShellTabWindowClass":
                    return h          # 第一个即 z-order 最顶 = 活动标签
            except Exception:
                continue
        return 0

    def _tab_hwnd_of_window(self, w):
        """取某个 Shell 窗口对象（IWebBrowser2）所属【标签】的控件窗口句柄。

        关键：w.HWND 返回的是主窗口句柄（多标签下三个候选全一样，无法区分）；
        而 IShellBrowser::GetWindow() 返回的才是该标签自己的 ShellTabWindowClass 句柄。
        pywin32 原生支持 IServiceProvider::QueryService，可直接取到 IShellBrowser。
        """
        import pythoncom
        try:
            from win32com.shell import shell as _shell
            iid_sb = _shell.IID_IShellBrowser
        except Exception:
            iid_sb = pythoncom.MakeIID("{000214E2-0000-0000-C000-000000000046}")
        try:
            sp = w._oleobj_.QueryInterface(pythoncom.IID_IServiceProvider)
            sb = sp.QueryService(iid_sb, iid_sb)
            return int(sb.GetWindow())
        except Exception as e:
            self._sb_err = f"{e}"
            return 0

    def _resolve_active_view(self, fg_hwnd, prefer_selected: bool = False):
        """解析前台资源管理器「当前活动标签」的文件夹视图。

        返回 (doc, n_tabs)：
          doc 非 None            → 拿到活动视图，可用 .SelectedItems() / .Folder.Self.Path
          doc 为 None 且 n_tabs==0 → 前台窗口不是资源管理器
          doc 为 None 且 n_tabs>0  → 是资源管理器，但多标签下无法确定活动标签
        """
        self._sb_err = ""
        shell = win32com.client.Dispatch("Shell.Application")
        candidates = []
        for w in shell.Windows():
            try:
                if int(w.HWND) == fg_hwnd:
                    candidates.append(w)
            except Exception:
                continue

        if not candidates:
            return None, 0
        if len(candidates) == 1:
            try:
                return candidates[0].Document, 1
            except Exception:
                return None, 1

        # ── 1. 主路径：z-order 最顶的 ShellTabWindowClass = 活动标签，
        #        再用 IShellBrowser::GetWindow() 找出属于它的那个 Shell 窗口对象 ──
        active_tab = self._active_tab_hwnd(fg_hwnd)
        self._last_tab_diag = f"活动标签 hwnd={active_tab}，候选 {len(candidates)} 个"
        if active_tab:
            seen = []
            for w in candidates:
                tab = self._tab_hwnd_of_window(w)
                seen.append(tab)
                if tab and tab == active_tab:
                    try:
                        return w.Document, len(candidates)
                    except Exception:
                        pass
            self._last_tab_diag += f"，各标签 hwnd={seen}"
            if self._sb_err:
                self._last_tab_diag += f"，IShellBrowser 错误({self._sb_err})"

        # ── 2. 回退：窗口标题与各标签目录比对 ──
        try:
            title = (win32gui.GetWindowText(fg_hwnd) or "").strip()
        except Exception:
            title = ""
        if title:
            def _leaf(s: str) -> str:
                return re.split(r'[\\/]', s.rstrip("\\/"))[-1].lower()
            t_full, t_leaf = title.rstrip("\\/").lower(), _leaf(title)
            for w in candidates:
                try:
                    doc_w = w.Document
                except Exception:
                    continue
                p = self._view_path(doc_w)
                if p and (p.rstrip("\\/").lower() == t_full or _leaf(p) == t_leaf):
                    return doc_w, len(candidates)

        # ── 3. 回退：选用「确实有选中项」的标签（仅需要选中项的功能可用）──
        if prefer_selected:
            for w in candidates:
                try:
                    doc_w = w.Document
                    if doc_w.SelectedItems().Count > 0:
                        return doc_w, len(candidates)
                except Exception:
                    continue

        return None, len(candidates)

    def mkdir_fast_create(self):
        """顺序新建热键触发：在当前资源管理器选中文件夹旁边新建下一个编号文件夹。"""
        if not self.chk_mkdir.isChecked():
            self.list_widget.addItem("⚠️ 请先启用速建文件夹")
            return
        if not _WIN_OK:
            self.list_widget.addItem("❌ 功能不可用：缺少 pywin32")
            return
        try:
            fg_hwnd = win32gui.GetForegroundWindow()
            if not fg_hwnd:
                self.list_widget.addItem("⚠️ 无法获取前台窗口，请先点击资源管理器")
                return

            doc, n_tabs = self._resolve_active_view(fg_hwnd, prefer_selected=True)

            if doc is None and n_tabs == 0:
                self.list_widget.addItem("⚠️ 前台窗口不是资源管理器")
                return
            if doc is None:
                self.list_widget.addItem(
                    f"⚠️ 未选中任何文件夹，请先选中一个文件夹再按 {self._current_mkdir_key()}"
                    f"（检测到 {n_tabs} 个标签）"
                )
                return

            try:
                sel = doc.SelectedItems()
                count = sel.Count
            except Exception:
                self.list_widget.addItem("❌ 无法读取选中项")
                return

            if count == 0:
                self.list_widget.addItem(
                    f"⚠️ 未选中任何文件夹，请先选中一个文件夹再按 {self._current_mkdir_key()}"
                )
                return
            if count > 1:
                self.list_widget.addItem(f"⚠️ 选中了 {count} 个项目，每次只能对 1 个文件夹操作")
                return

            item = sel.Item(0)
            target_path = item.Path

            if not os.path.isdir(target_path):
                self.list_widget.addItem(f"⚠️ 选中的是文件，请选中一个文件夹再按 {self._current_mkdir_key()}")
                return

            parent_dir  = os.path.dirname(target_path)
            folder_name = os.path.basename(target_path)
            # 目录感知：扫描同级同模式文件夹取最大编号 +1，跳过已存在的编号
            new_name    = self._next_available_folder_name(parent_dir, folder_name)
            new_path    = os.path.join(parent_dir, new_name)

            os.makedirs(new_path)
            self._push_record(
                f"✅ 速建文件夹成功：{new_name}",
                select=True,
                filepath=new_path,
            )

        except Exception as e:
            log.exception("速建文件夹失败")
            self.list_widget.addItem(f"❌ 速建文件夹错误：{e}")

    def mkdir_direct_create_a(self):
        """F9 触发：新建名称 A（默认 Grok）的文件夹。"""
        self._mkdir_direct_create(self.mkdir_name, "Grok", "F9")

    def mkdir_direct_create_b(self):
        """F10 触发：新建名称 B（默认 Qwen）的文件夹。"""
        self._mkdir_direct_create(self.mkdir_name_b, "Qwen", "F10")

    def _mkdir_direct_create(self, name_edit, default_name: str, key_label: str):
        """在当前资源管理器活动标签内，仅当未选中任何项时，
        新建一个以 name_edit 内容命名的空文件夹。"""
        if not self.chk_mkdir.isChecked():
            self.list_widget.addItem("⚠️ 请先启用速建文件夹")
            return
        if not _WIN_OK:
            self.list_widget.addItem("❌ 功能不可用：缺少 pywin32")
            return
        name = name_edit.text().strip() or default_name
        # 清洗非法字符；若清洗后只剩下划线/空白（如输入了 "///"），回退默认名
        name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
        if not name.strip("_ "):
            name = default_name
        try:
            fg_hwnd = win32gui.GetForegroundWindow()
            if not fg_hwnd:
                self.list_widget.addItem("⚠️ 无法获取前台窗口，请先点击资源管理器")
                return

            doc, n_tabs = self._resolve_active_view(fg_hwnd, prefer_selected=False)

            if doc is None and n_tabs == 0:
                self.list_widget.addItem("⚠️ 前台窗口不是资源管理器")
                return
            if doc is None:
                # 认不出当前活动标签：宁可不建，也不要建到错误的目录里
                diag = getattr(self, "_last_tab_diag", "无诊断")
                self.list_widget.addItem("⚠️ 无法确定当前标签，已取消新建")
                self.list_widget.addItem(f"🔍 诊断：{diag}；Shell.Windows 候选 {n_tabs} 个")
                self.list_widget.scrollToBottom()
                self._log_problem(f"🔍 {key_label} 定位失败：{diag}；候选 {n_tabs} 个")
                return

            # 仅在“未选中任何项”时才直接新建
            try:
                count = doc.SelectedItems().Count
            except Exception:
                count = 0
            if count > 0:
                self.list_widget.addItem(
                    f"ℹ️ 已选中 {count} 项，{key_label} 直接新建仅在未选中任何项时生效"
                )
                return

            # 取当前活动标签所在目录
            cur_dir = self._view_path(doc)
            if not cur_dir or not os.path.isdir(cur_dir):
                self.list_widget.addItem(
                    f"⚠️ 无法确定当前文件夹｜取到的路径={cur_dir!r}｜doc={type(doc).__name__}"
                )
                self.list_widget.scrollToBottom()
                return

            new_path = self._unique_path(os.path.join(cur_dir, name))
            os.makedirs(new_path)
            self._push_record(
                f"✅ 直接新建成功：{os.path.basename(new_path)}",
                select=True,
                filepath=new_path,
            )

        except Exception as e:
            log.exception("直接新建文件夹失败")
            self.list_widget.addItem(f"❌ 直接新建错误：{e}")

    # ────────────────────────────────────────
    # F6 记录模式
    # ────────────────────────────────────────
    # ────────────────────────────────────────
    # F6 记录模式
    # ────────────────────────────────────────
    def _refresh_icon(self) -> QIcon:
        """20×20 圆形双箭头刷新图标。"""
        s = 20.0
        pm = QPixmap(int(s), int(s))
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        col = QColor(tk("text_mut"))
        pen = QPen(col, 2.0)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        cx, cy = s / 2, s / 2
        r = s * 0.32
        # 两个半弧拼成环形箭头
        p.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2), 30 * 16, 150 * 16)
        p.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2), 210 * 16, 150 * 16)
        # 上箭头
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(col))
        path1 = QPainterPath()
        x1, y1 = cx + r * 0.45, cy - r
        path1.moveTo(x1, y1)
        path1.lineTo(x1 - s * 0.1, y1 - s * 0.12)
        path1.lineTo(x1 + s * 0.1, y1 - s * 0.12)
        path1.closeSubpath()
        p.drawPath(path1)
        # 下箭头
        path2 = QPainterPath()
        x2, y2 = cx - r * 0.45, cy + r
        path2.moveTo(x2, y2)
        path2.lineTo(x2 + s * 0.1, y2 + s * 0.12)
        path2.lineTo(x2 - s * 0.1, y2 + s * 0.12)
        path2.closeSubpath()
        p.drawPath(path2)
        p.end()
        return QIcon(pm)

    def _restyle_record_btn(self, state="idle"):
        """state: idle / recording / frozen（批处理中灰化锁定）"""
        # 冻结期间其它路径的 restyle 不得盖掉冻结外观
        if getattr(self, "_record_btn_frozen", False):
            state = "frozen"
        if state == "recording":
            bg, fg, bd = "#3B82F6", "#ffffff", "#2563EB"
            hover = bd
        elif state == "frozen":
            # 与侧栏自动下载开关冻结态一致：slate 灰蓝、无 hover 反馈
            bg, fg, bd = "#64748B", "#E2E8F0", "#475569"
            hover = bg
        else:
            bg = tk("input_bg")
            fg = tk("text")
            bd = tk("border")
            hover = bd
        self.btn_record.setStyleSheet(
            f"QPushButton{{background:{bg};color:{fg};"
            f"border:1px solid {bd};border-radius:6px;"
            f"padding:6px 10px;font-size:12px;font-weight:bold;}}"
            f"QPushButton:hover{{background:{hover};}}"
            f"QPushButton:disabled{{background:{bg};color:{fg};border:1px solid {bd};}}"
        )

    _RECORD_UI_FROZEN_TIP = "批处理进行中 · 暂不可用（中止请按 F6）"

    def set_record_btn_frozen(self, frozen: bool):
        """批处理进行中：冻结「开始记录F6」与记录卡（禁止点进记录/再开批处理；F6 热键仍可中止）。"""
        frozen = bool(frozen)
        btn_was = bool(getattr(self, "_record_btn_frozen", False))
        cards_was = bool(getattr(self, "_record_cards_frozen", False))
        if btn_was == frozen and cards_was == frozen:
            return
        self._record_btn_frozen = frozen
        self._record_cards_frozen = frozen

        btn = getattr(self, "btn_record", None)
        if btn is not None:
            if frozen:
                btn.setEnabled(False)
                btn.setCursor(Qt.ForbiddenCursor)
                btn.setToolTip("批处理进行中 · 记录模式暂不可用（中止请按 F6）")
                self._restyle_record_btn("frozen")
            else:
                btn.setEnabled(True)
                btn.setCursor(Qt.PointingHandCursor)
                btn.setToolTip("")
                self._restyle_record_btn(
                    "recording" if getattr(self, "_recording", False) else "idle"
                )

        # 记录卡：就地改样式/可点，或解冻时整表刷新恢复正常外观
        if hasattr(self, "_record_grid_layout"):
            if frozen:
                self._apply_record_cards_frozen_style()
            else:
                try:
                    self._refresh_record_cards()
                except Exception:
                    self._apply_record_cards_frozen_style()

    def _apply_record_cards_frozen_style(self):
        """按当前 _record_cards_frozen 刷新网格内全部记录卡外观与可点状态。"""
        layout = getattr(self, "_record_grid_layout", None)
        if layout is None:
            return
        frozen = bool(getattr(self, "_record_cards_frozen", False))
        tip = self._RECORD_UI_FROZEN_TIP if frozen else ""
        if frozen:
            bg, fg, bd = "#64748B", "#E2E8F0", "#475569"
            hover = bg
            cursor = Qt.ForbiddenCursor
        else:
            bg = tk("input_bg")
            fg = tk("text")
            bd = tk("border")
            hover = tk("sel_bg")
            cursor = Qt.PointingHandCursor
        for i in range(layout.count()):
            item = layout.itemAt(i)
            w = item.widget() if item is not None else None
            if w is None:
                continue
            try:
                if isinstance(w, _RecordCardChip):
                    w.apply_chrome(bg, fg, bd, hover)
                w.setEnabled(not frozen)
                w.setCursor(cursor)
                w.setToolTip(tip)
            except Exception:
                pass

    def _btn_record_clicked(self):
        if getattr(self, "_record_btn_frozen", False):
            return
        self._toggle_record_mode()

    # ── 随机记录名池（修仙/玄幻风，2~3 汉字）────
    _RECORD_NAMES = [
        "废灵根", "聚灵丹", "聚宝盆", "炼妖壶", "斩仙剑", "定海珠", "打神鞭",
        "封神榜", "长生诀", "九转丹", "破阵子", "乾坤袋", "缚龙索", "飞天梭",
        "迷魂幡", "落魄钟", "诛仙阵", "万剑诀", "天罡气", "地煞火", "化神丹",
        "筑基液", "金丹砂", "元婴果", "渡劫期", "炼虚丹", "合体果", "大乘丹",
        "飞升台", "百草园", "千机阁", "万象塔", "招魂幡", "避水珠", "风火轮",
        "阴阳镜", "捆仙绳", "番天印", "落宝钱", "五色旗", "混元伞", "紫金铃",
        "金刚琢", "太乙甲", "北斗盘", "南明离", "玄黄鼎", "造化炉", "开天斧",
        "残阳", "玄铁", "青莲", "龙鳞", "凤羽", "玉骨", "星砂", "月魄",
        "雷音", "冰心", "金翅", "紫气", "冥河", "凌云", "沧海", "幽兰",
        "苍穹", "碧落", "归墟", "九天", "太古", "玄黄", "鸿蒙", "万劫",
        "轮回", "无量", "混元", "两仪", "四象", "八卦", "星河", "流光",
    ]

    def _toggle_record_mode(self):
        # 批处理进行中：F6 = 预约「当前条处理完再停」，不进入记录模式
        if self._try_stop_batch_via_f6():
            return
        # 按钮冻结时不启停记录（热键中止批处理已在上方处理）
        if getattr(self, "_record_btn_frozen", False):
            return
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _try_stop_batch_via_f6(self) -> bool:
        """若有批处理在跑，转调 request_batch_stop；成功受理返回 True。"""
        try:
            w = self
            mw = None
            while w is not None:
                if getattr(w, "page_douyin", None) is not None or getattr(w, "page_gallery", None) is not None:
                    mw = w
                    break
                try:
                    w = w.parent()
                except Exception:
                    break
            if mw is None:
                return False
            # 自动下载引擎（PageVideo，速存/图集批处理统一走此）
            pv = getattr(mw, "page_douyin", None)
            if pv is not None and getattr(pv, "_batch_mode", False):
                if hasattr(pv, "request_batch_stop"):
                    return bool(pv.request_batch_stop())
                return False
            # 图集：本地批处理 或 序列补全
            pg = getattr(mw, "page_gallery", None)
            if pg is not None:
                for attr in ("page_eh", "page_pixiv", "page_hitomi"):
                    sub = getattr(pg, attr, None)
                    if sub is None:
                        continue
                    filling = bool(
                        getattr(sub, "_fill_active_once", False)
                        or getattr(sub, "_fill_only_names", None)
                    )
                    if getattr(sub, "_batch_mode", False) or filling:
                        if hasattr(sub, "request_batch_stop"):
                            return bool(sub.request_batch_stop())
                        return False
        except Exception:
            pass
        return False

    def _resolve_record_path(self) -> str:
        """返回记录文件路径：每次调用都新建一个文件。
        勾选「新建随机记录名」→ 随机中文名；不勾选 → 当天顺序编号。
        平铺在 records/ 根，文件带 card_ 前缀（如 card_记录文件_MMDD_NN.txt）。"""
        try:
            from utils.app_paths import cards_dir
            rec_dir = cards_dir()
        except Exception:
            rec_dir = os.path.normpath(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "userdata")
            )
            os.makedirs(rec_dir, exist_ok=True)
        md = time.strftime("%m%d")

        if not self.chk_new_record.isChecked():
            pat = re.compile(rf'^card_记录文件_{md}(?:_(\d+))?\.txt$')
            max_idx = 0
            try:
                for name in os.listdir(rec_dir):
                    m = pat.match(name)
                    if m:
                        n = int(m.group(1)) if m.group(1) else 0
                        if n > max_idx:
                            max_idx = n
            except Exception:
                pass
            next_idx = max_idx + 1
            if next_idx > 99:
                next_idx = 1
            return os.path.join(rec_dir, f"card_记录文件_{md}_{next_idx:02d}.txt")

        import random
        tried = set()
        while len(tried) < len(self._RECORD_NAMES):
            name = random.choice(self._RECORD_NAMES)
            if name in tried:
                continue
            tried.add(name)
            path = os.path.join(rec_dir, f"card_{name}.txt")
            if not os.path.exists(path):
                return path
        ts = int(time.time())
        return os.path.join(rec_dir, f"card_无名_{ts}.txt")

    def _init_record_file(self):
        """启动时刷新记录卡片网格。"""
        self._refresh_record_cards()

    def _iter_record_card_paths(self, exclude: str = ""):
        """列出 data/ 下全部记录卡路径（card_*.txt），可排除当前文件。"""
        try:
            from utils.app_paths import cards_dir
            rec_dir = cards_dir()
        except Exception:
            rec_dir = os.path.normpath(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "userdata")
            )
        ex = os.path.normcase(os.path.abspath(exclude)) if exclude else ""
        out = []
        try:
            names = sorted(
                (f for f in os.listdir(rec_dir)
                 if self._is_record_card_file(f, rec_dir)),
                key=lambda f: os.path.getctime(os.path.join(rec_dir, f)),
            )
        except Exception:
            return out
        for fname in names:
            fpath = os.path.join(rec_dir, fname)
            if ex and os.path.normcase(os.path.abspath(fpath)) == ex:
                continue
            out.append(fpath)
        return out

    @staticmethod
    def _record_card_display_name(fpath: str) -> str:
        fname = os.path.basename(fpath or "")
        plain = fname[len("card_"):] if fname.startswith("card_") else fname
        return re.sub(r"\.txt$", "", plain, flags=re.I) or fname

    def _merge_record_file_into(self, target_path: str, source_path: str) -> int:
        """把源记录卡中的记录行移动到目标文件末尾；源记录为 0 则删除源文件。
        返回移动条数。"""
        target_path = (target_path or "").strip()
        source_path = (source_path or "").strip()
        if not target_path or not source_path:
            return 0
        if os.path.normcase(os.path.abspath(target_path)) == os.path.normcase(
            os.path.abspath(source_path)
        ):
            return 0
        if not os.path.isfile(source_path):
            return 0

        try:
            with open(source_path, "r", encoding="utf-8-sig") as f:
                raw_lines = f.readlines()
        except Exception:
            log.exception("并入：读取源文件失败 path=%s", source_path)
            raise

        move_lines = []
        keep_lines = []
        for raw in raw_lines:
            line = self._normalize_record_line(raw)
            if line and not line.startswith("#"):
                # 保留原文行尾风格：去掉换行后写回统一 \n
                move_lines.append(raw.rstrip("\r\n"))
            else:
                keep_lines.append(raw)

        if not move_lines:
            # 无可并入记录；若源已无记录行则按规则删掉空卡
            if not any(
                self._normalize_record_line(x) and not self._normalize_record_line(x).startswith("#")
                for x in raw_lines
            ):
                try:
                    os.remove(source_path)
                except Exception:
                    pass
            return 0

        # 追加到目标末尾
        try:
            need_nl = False
            if os.path.isfile(target_path) and os.path.getsize(target_path) > 0:
                with open(target_path, "rb") as bf:
                    bf.seek(-1, os.SEEK_END)
                    need_nl = bf.read(1) not in (b"\n", b"\r")
            with open(target_path, "a", encoding="utf-8") as f:
                if need_nl:
                    f.write("\n")
                for ln in move_lines:
                    f.write(ln + "\n")
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            log.exception("并入：写入目标失败 path=%s", target_path)
            raise

        # 源文件：无剩余记录行则删除，否则写回非记录残留
        still_records = [
            x for x in keep_lines
            if self._normalize_record_line(x) and not self._normalize_record_line(x).startswith("#")
        ]
        try:
            if still_records:
                tmp = source_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.writelines(keep_lines)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, source_path)
            else:
                os.remove(source_path)
        except Exception:
            log.exception("并入：更新/删除源文件失败 path=%s", source_path)
            raise

        return len(move_lines)

    def _record_card_clicked(self, filepath: str):
        """点击记录卡片弹出弹窗：预览内容 + 继续记录 / 批处理 / 删除。"""
        if getattr(self, "_record_cards_frozen", False):
            return
        if not filepath or not os.path.isfile(filepath):
            return
        fname = os.path.basename(filepath)
        name_no_ext = re.sub(r'\.txt$', '', fname)
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit, QMenu, QMessageBox
        dlg = QDialog(self)
        dlg.setWindowTitle("记录文件")
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dlg.resize(640, 360)
        dlg.setMinimumSize(480, 280)
        dlg.setStyleSheet(self.styleSheet())
        v = QVBoxLayout(dlg)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)

        preview = QTextEdit()
        preview.setStyleSheet(
            f"QTextEdit{{background:{tk('input_bg')};color:{tk('text')};"
            f"border:1px solid {tk('border')};border-radius:6px;font-size:13px;}}"
        )
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            preview.setPlainText(content)
        except Exception:
            preview.setPlainText("（无法读取文件内容）")
        v.addWidget(preview, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

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

        def _do_save_and_close():
            _do_save(filepath, preview.toPlainText())
            dlg.accept()

        def _do_save(path, text):
            try:
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(text)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
            except Exception as e:
                log.exception("运行记录保存失败 path=%s", path)
                from PyQt5.QtWidgets import QMessageBox
                message_box_warn(dlg, "保存失败", str(e))

        # 删除按钮：弹确认框
        btn_delete = _make_btn("删除", bg="#7f1d1d", fg="#fca5a5", bd="#991b1b", hover_bg="#991b1b")

        def _delete_with_confirm():
            from PyQt5.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                dlg, "确认删除", f"确定要删除 {name_no_ext} 吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self._delete_record_file(filepath, dlg)

        btn_delete.clicked.connect(_delete_with_confirm)
        btn_row.addWidget(btn_delete)

        btn_merge = _make_btn("并入")
        btn_merge.setToolTip("把其它记录文件中的记录移动到本文件末尾")

        def _do_merge_menu():
            # 先把编辑区写回，避免未保存内容被覆盖
            _do_save(filepath, preview.toPlainText())
            candidates = []
            for fp in self._iter_record_card_paths(exclude=filepath):
                # 正在记录写入的源文件不参与并入
                if getattr(self, "_recording", False) and getattr(self, "_record_file", None):
                    try:
                        if os.path.normcase(os.path.abspath(fp)) == os.path.normcase(
                            os.path.abspath(self._record_file)
                        ):
                            continue
                    except Exception:
                        pass
                n = len(self._load_record_seen(fp))
                if n <= 0:
                    continue
                candidates.append((fp, n))
            if not candidates:
                message_box_info(dlg, "并入", "没有可并入的其它记录文件。")
                return
            menu = QMenu(dlg)
            menu.setStyleSheet(
                f"QMenu{{background:{tk('panel')};color:{tk('text')};"
                f"border:1px solid {tk('border')};}}"
                f"QMenu::item:selected{{background:{tk('sel_bg')};}}"
            )
            for fp, n in candidates:
                label = f"{self._record_card_display_name(fp)}（{n}）"
                act = menu.addAction(label)
                act.setData(fp)
            pos = btn_merge.mapToGlobal(btn_merge.rect().bottomLeft())
            chosen = menu.exec_(pos)
            if chosen is None:
                return
            src = chosen.data()
            if not src:
                return
            src_name = self._record_card_display_name(src)
            n = len(self._load_record_seen(src))
            reply = QMessageBox.question(
                dlg, "确认并入",
                f"把「{src_name}」的 {n} 条记录移动到本文件末尾？\n"
                f"并入后该文件若已无记录将被删除。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            try:
                moved = self._merge_record_file_into(filepath, src)
            except Exception as e:
                message_box_warn(dlg, "并入失败", str(e))
                return
            # 刷新预览与卡片
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    preview.setPlainText(f.read())
            except Exception:
                pass
            self._refresh_record_cards()
            # 若正在往本文件记录，重建防重复集合
            if getattr(self, "_recording", False) and getattr(self, "_record_file", None):
                try:
                    if os.path.normcase(os.path.abspath(self._record_file)) == os.path.normcase(
                        os.path.abspath(filepath)
                    ):
                        self._record_seen = self._load_record_seen(filepath)
                        self._record_base_count = len(self._record_seen)
                except Exception:
                    pass
            self.list_widget.addItem(
                f"已并入 {moved} 条 ← {src_name}"
            )
            self.list_widget.scrollToBottom()

        btn_merge.clicked.connect(_do_merge_menu)
        btn_row.addWidget(btn_merge)

        btn_row.addStretch(1)

        # 右下三键配色顺序：黄 → 蓝 → 绿
        if not self._recording:
            btn_append = _make_btn(
                f"继续记录_{name_no_ext}",
                bg="#a16207", fg="#fef9c3", bd="#ca8a04", hover_bg="#ca8a04",
            )
            btn_append.clicked.connect(lambda: (self._start_recording_to(filepath), dlg.accept()))
            btn_row.addWidget(btn_append)

        btn_batch = _make_btn(
            f"批处理_{name_no_ext}",
            bg="#1d4ed8", fg="#dbeafe", bd="#2563eb", hover_bg="#2563eb",
        )
        btn_batch.clicked.connect(lambda: self._batch_from_record(filepath, dlg))
        btn_row.addWidget(btn_batch)

        btn_save = _make_btn(
            "保存并关闭",
            bg="#14532d", fg="#bbf7d0", bd="#166534", hover_bg="#166534",
        )
        btn_save.clicked.connect(_do_save_and_close)
        btn_row.addWidget(btn_save)

        v.addLayout(btn_row)

        # 恢复上次关闭时的窗口尺寸
        _saved_w, _saved_h = 0, 0
        try:
            from utils.user_prefs import load_user_prefs
            prefs, _ = load_user_prefs()
            fs = (prefs.get("fast_save") or {})
            dw = fs.get("record_dialog_w")
            dh = fs.get("record_dialog_h")
            if isinstance(dw, int) and isinstance(dh, int) and dw > 0 and dh > 0:
                _saved_w, _saved_h = dw, dh
        except Exception:
            pass

        # 事件过滤器：对话框隐藏时记下尺寸
        class _SizeSaver(QObject):
            def eventFilter(self, obj, event):
                if event.type() == QEvent.Hide:
                    try:
                        from utils.user_prefs import load_user_prefs, save_user_prefs
                        prefs, _ = load_user_prefs()
                        fs = prefs.get("fast_save")
                        if not isinstance(fs, dict):
                            fs = {}
                            prefs["fast_save"] = fs
                        fs["record_dialog_w"] = obj.width()
                        fs["record_dialog_h"] = obj.height()
                        save_user_prefs(prefs)
                        # 同步到主窗口内存
                        w2 = dlg.parent()
                        while w2:
                            if hasattr(w2, "_user_prefs") and isinstance(getattr(w2, "_user_prefs", None), dict):
                                w2._user_prefs = prefs
                                break
                            w2 = w2.parent()
                    except Exception:
                        pass
                return False

        _saver = _SizeSaver(dlg)
        dlg.installEventFilter(_saver)

        # 应用保存的尺寸（show 之后 resize 才能真正生效）
        if _saved_w > 0:
            QTimer.singleShot(10, lambda: dlg.resize(_saved_w, _saved_h))

        dlg.exec_()

        self._refresh_record_cards()

    def _batch_from_record(self, filepath: str, dlg=None):
        """从记录文件启动自动下载（混链统一走 PageVideo.start_batch_from_file）。"""
        if dlg is not None:
            try:
                dlg.accept()
            except Exception:
                pass
        pv = None
        mw = None
        w = self
        while w:
            if hasattr(w, "page_douyin"):
                pv = getattr(w, "page_douyin", None)
                mw = w
            w = w.parent()
        if pv is None or not hasattr(pv, "start_batch_from_file"):
            from PyQt5.QtWidgets import QMessageBox
            message_box_info(self, "提示", "未找到自动下载引擎，请确认程序已完整启动")
            return
        # 先切到视频页，便于日志可见；混链后续会再切到图集等
        if mw and hasattr(mw, "stack") and hasattr(mw, "btn_douyin"):
            try:
                idx = mw.stack.indexOf(pv)
                if idx >= 0:
                    if hasattr(mw, "_auto_switch"):
                        mw._auto_switch(idx, mw.btn_douyin)
                    else:
                        mw._switch(idx, mw.btn_douyin)
            except Exception:
                pass
        if not pv.start_batch_from_file(filepath):
            try:
                self.list_widget.addItem("⚠ 自动下载未能启动（文件空/不存在/已有任务在跑）")
                self.list_widget.scrollToBottom()
            except Exception:
                pass

    @staticmethod
    def _normalize_record_line(text: str) -> str:
        """记录行规范化：去首尾空白、内部空白压成单空格（与写入时一致）。"""
        return " ".join(((text or "").strip()).split())

    def _load_record_seen(self, filepath: str) -> set:
        """从记录文件载入全部非空行（规范化），供全文件防重复。"""
        seen = set()
        path = (filepath or "").strip()
        if not path or not os.path.isfile(path):
            return seen
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                for raw in f:
                    line = self._normalize_record_line(raw)
                    if line and not line.startswith("#"):
                        seen.add(line)
        except Exception:
            pass
        return seen

    def _start_recording_to(self, filepath: str):
        """使用已有文件进入记录模式（追加写入）。F6 新建与「继续记录」共用。"""
        if self._recording:
            self._stop_recording()
        rec_dir = os.path.dirname(filepath)
        os.makedirs(rec_dir, exist_ok=True)
        self._record_file = filepath
        self._record_count = 0
        self._record_base_count = 0
        self._recording = True
        self.btn_record.setText("停止记录F6")
        self._restyle_record_btn("recording")
        # 全文件防重复：启动时载入文件内全部已有行（新建空文件则为空集）
        self._record_seen = self._load_record_seen(self._record_file)
        self._record_base_count = len(self._record_seen)
        existing = f"已有{self._record_base_count}行，" if self._record_base_count else ""
        self.lbl_record_hint.setText(f"内容将逐行写入{existing}，已加 0 条")
        tip = f"🔴 记录中 → {os.path.basename(self._record_file)}"
        if self._record_seen:
            tip += f"（已载入 {len(self._record_seen)} 条防重复）"
        self.list_widget.addItem(tip)
        self.list_widget.scrollToBottom()
        start_record_bubble(self._record_base_count)
        self._record_feature_states = self._fast_feature_states()
        self._set_fast_feature_states((False, False, False))
        self.record_mode_toggled_sig.emit("started")
        self.request_show.emit()

    def _delete_record_file(self, filepath: str, dlg=None):
        """确认并删除记录文件。"""
        fname = os.path.basename(filepath)
        from PyQt5.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "确认删除", f"确定要删除 {fname} 吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            try:
                os.remove(filepath)
                self._refresh_record_cards()
                self.list_widget.addItem(f"已删除 {fname}")
                self.list_widget.scrollToBottom()
            except Exception as e:
                self.list_widget.addItem(f"删除失败：{e}")
                self.list_widget.scrollToBottom()
        if dlg:
            dlg.accept()

    @staticmethod
    def _is_record_card_file(fname: str, rec_dir: str) -> bool:
        """平铺后 records/ 根是否应显示为记录卡：只认 card_*.txt。

        records/ 根里还有 points_calc.txt 等非卡片 .txt，必须靠
        card_ 前缀过滤，不能像以前那样“目录里的任意 .txt 都算”。
        排除：隐藏文件、非普通文件、保存中的 .tmp 残留命名。
        """
        if not fname or not isinstance(fname, str):
            return False
        if fname.startswith("."):
            return False
        # 原子保存产生 path + ".tmp"（xxx.txt.tmp），不是合法记录
        if fname.lower().endswith(".tmp"):
            return False
        if not fname.lower().endswith(".txt"):
            return False
        if not fname.startswith("card_"):
            return False
        # 去掉扩展名后仍须有名字（拒绝 ".txt"）
        if not fname[:-4].strip():
            return False
        try:
            return os.path.isfile(os.path.join(rec_dir, fname))
        except Exception:
            return False

    def _refresh_record_cards(self):
        """扫描 records/ 根（card_*.txt），为每个记录文件生成方形卡片。

        兼容程序生成名（card_记录文件_MMDD_xx / card_随机中文名）。
        """
        # 清空旧卡片
        layout = self._record_grid_layout
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        try:
            from utils.app_paths import cards_dir
            rec_dir = cards_dir()
        except Exception:
            rec_dir = os.path.normpath(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "userdata")
            )
        try:
            files = sorted(
                (f for f in os.listdir(rec_dir)
                 if self._is_record_card_file(f, rec_dir)),
                key=lambda f: os.path.getctime(os.path.join(rec_dir, f)),
            )
        except Exception:
            return

        frozen = bool(getattr(self, "_record_cards_frozen", False))
        if frozen:
            card_color, text_color, border_color = "#64748B", "#E2E8F0", "#475569"
            hover_color = card_color
            tip = self._RECORD_UI_FROZEN_TIP
        else:
            card_color = tk("input_bg")
            border_color = tk("border")
            text_color = tk("text")
            hover_color = tk("sel_bg")
            tip = ""

        for idx, fname in enumerate(files, 1):
            plain = fname[len("card_"):] if fname.startswith("card_") else fname
            m = re.match(r'记录文件_(\d{4})(?:_(\d+))?\.txt$', plain)
            if m:
                short_date = m.group(1)
                sub = m.group(2) if m.group(2) else ""
                seq = f"{idx:02d}" if not sub else sub.zfill(2)
                name = f"{short_date}-{seq}"
            else:
                name = re.sub(r'\.txt$', '', plain)

            fpath = os.path.join(rec_dir, fname)
            # 与记录模式防重复一致：非空、非 # 注释行计为一条
            count = len(self._load_record_seen(fpath))
            display = f"{name} {count}"

            card = _RecordCardChip(display)
            card.apply_chrome(card_color, text_color, border_color, hover_color)
            if frozen:
                card.setEnabled(False)
                card.setCursor(Qt.ForbiddenCursor)
                card.setToolTip(tip)
            else:
                card.setToolTip("")
                card.clicked.connect(lambda checked, p=fpath: self._record_card_clicked(p))
            layout.addWidget(card)

    def _start_recording(self):
        self._start_recording_to(self._resolve_record_path())

    def _stop_recording(self):
        self._recording = False
        cnt = self._record_count
        self._record_seen = set()

        self.btn_record.setText("开始记录F6")
        self._restyle_record_btn("idle")
        self._refresh_record_cards()

        self.lbl_record_hint.setText(f"上次记录了 {cnt} 条")

        self.list_widget.addItem(f"⏹️ 记录模式已停止 → 新增 {cnt} 条，"
                                  f"文件：{os.path.basename(self._record_file)}")
        self.list_widget.scrollToBottom()
        stop_record_bubble()

        states, self._record_feature_states = self._record_feature_states, None
        if states is not None:
            self._set_fast_feature_states(states)
        self.record_mode_toggled_sig.emit("stopped")
        self.request_show.emit()

    def _on_record_clipboard(self):
        if not self._recording:
            return
        if self._record_clip_ignore:
            return
        if not self._record_file:
            return

        self._record_clip_ignore = True
        try:
            cb = QApplication.clipboard()
            if cb and cb.mimeData().hasUrls():
                return
            text = (cb.text() or "").strip() if cb else ""
            if not text:
                return
            # 多行分享文案压成单行，避免批处理按行拆碎后平台识别失败
            text = self._normalize_record_line(text)
            if not text:
                return

            # 全文件防重复：与文件内（及本会话已写入）任意一行相同则忽略
            if text in self._record_seen:
                self.list_widget.addItem("⏭️ 已忽略重复记录：文件中已有相同内容")
                self.list_widget.scrollToBottom()
                try:
                    cb.clear()
                except Exception:
                    pass
                return

            with open(self._record_file, "a", encoding="utf-8") as fp:
                fp.write(text + "\n")

            self._record_seen.add(text)
            self._record_count += 1
            self.lbl_record_hint.setText(f"内容将逐行写入，已加 {self._record_count} 条")
            update_record_bubble_count(self._record_base_count + self._record_count)
            cb.clear()
        except Exception as e:
            self.list_widget.addItem(f"❌ 记录失败：{e}")
            self.list_widget.scrollToBottom()
        finally:
            self._record_clip_ignore = False

    def closeEvent(self, e):
        self.stop_background_checks()
        self._unregister_txt_hotkey()
        self._unregister_mkdir_hotkey()
        super().closeEvent(e)
    def stop_background_checks(self):
        """主窗口关闭时清理下载、热键和后台任务。"""
        if self._closing:
            return
        self._closing = True
        self._browser_image_save.configure(False)
        self._background_log.interrupt("程序已关闭，后台下载中断。")
        self._background_download.stop()
        try:
            stop_record_bubble()
        except Exception:
            pass
        self._browser_status_timer.stop()
        try:
            self._unregister_txt_hotkey()
        except Exception:
            pass
        try:
            self._unregister_mkdir_hotkey()
        except Exception:
            pass
        try:
            self._unregister_recording_hotkey()
        except Exception:
            pass
        try:
            w = getattr(self, "_dup_worker", None)
            if w is not None:
                w.stop()
            from utils.qthread_util import stop_qthread
            stop_qthread(w, name="dup_clean_scan")
        except Exception:
            pass
        dlg = getattr(self, "_dup_dialog", None)
        if dlg is not None:
            try:
                dlg.shutdown()
                dlg.close()
            except Exception:
                pass
            self._dup_dialog = None
        dlg = getattr(self, "_dup_multi_dialog", None)
        if dlg is not None:
            try:
                dlg.shutdown()
                dlg.close()
            except Exception:
                pass
            self._dup_multi_dialog = None

    def shutdown(self):
        """与 stop_background_checks 同义，供主窗口统一收口。"""
        self.stop_background_checks()

    def _bind_container_switch(self):
        w = self.parent()
        while w:
            if isinstance(w, (QTabWidget, QStackedWidget)):
                self._container = w
                try: w.currentChanged.connect(self._on_container_current_changed)
                except Exception: pass
                break
            w = w.parent()

    def _on_container_current_changed(self, idx: int):
        """切到此页面时自动刷新记录卡片。"""
        try:
            w = self._container.widget(idx) if self._container else None
            if w is self:
                self._refresh_record_cards()
        except Exception:
            pass

    # ── 序列文件检查 ─────────────────────────────────────────────────────────────
