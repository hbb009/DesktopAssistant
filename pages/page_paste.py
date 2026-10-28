# -*- coding: utf-8 -*-
"""
page_paste.py  —  粘贴助手（提示词仓库）

集成于「桌面助手」主程序，作为侧边栏「粘贴助手」页面（PagePaste）。
记录文件：<项目根>/records/paste_helper.txt

功能：
  · 录入：主题 + 内容 + 七色选色 + 保存/更新
  · 剪贴板实时显示：复制后标题变绿色「已复制」，内容框同步文本
  · 白名单命中：右侧提示实时反馈（命中抖音 / B站 等）
  · Ctrl+Alt+1 把选中文本加入复制；Ctrl+Alt+2 从复制减去选中；Ctrl+Alt+3 互换
    用法直接写在三个按钮上。这三个操作发生在其它窗口，不靠悬停说明
  · 辅助粘贴滑动开关：关掉时三个按钮和 Ctrl+Alt+1/2/3 都不可用。
    打开后才注册热键，并强制关闭侧栏三个粘贴归属、临时冻结仓库卡片，
    标题固定为「系统剪贴板」，内容框只同步系统剪贴板
  · 仓库：彩色主题卡片（流式排列，可拖拽改序）
  · 单击卡片：复制内容 + 选中 + 进入修改（填回录入区）
  · 再点同一张：只复制，不重刷表单（避免抖动）
  · 有内容且没写主题时，保存按钮显示「替换内容」；点它变成「替换中……」，再点一张已有卡才替换该卡内容。写了主题则点保存成为新卡
  · 右键卡片：删除
"""

import os
import re
import sys
import json
import ctypes
from datetime import datetime

try:
    import ctypes.wintypes as _wintypes
except Exception:  # pragma: no cover
    _wintypes = None

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from styles.style_all import (
    theme, tk, install_card_title, apply_btn_download, make_card,
    restyle_card_title, sp, CARD_TITLE_FONT_SIZE, CARD_TITLE_FONT_WEIGHT,
    CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP,
)
from utils.logger import get_logger

from PyQt5.QtCore import (
    Qt, QTimer, QPoint, QRect, QRectF, QSize, QMimeData, QEvent, QPointF,
    QAbstractNativeEventFilter, QCoreApplication, QObject, pyqtSignal, pyqtProperty,
    QPropertyAnimation, QEasingCurve,
)
from PyQt5.QtGui import (
    QFont, QFontMetrics, QColor, QDrag, QPixmap, QPainter, QPen, QBrush, QPolygonF,
    QIcon, QCursor, QLinearGradient, QRadialGradient,
)
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QAbstractButton,
    QLineEdit, QTextEdit, QScrollArea, QFrame, QMessageBox,
    QSizePolicy, QApplication, QLayout, QButtonGroup, QAbstractScrollArea,
    QGraphicsOpacityEffect,
)

log = get_logger(__name__)

# 色块基准边长（旧圆点 22px）；录入菱形 = 一半×1.5，筛选圆 = 30%/70%
_SWATCH_BASE = 22

# ── 路径 ─────────────────────────────────────────────────────────────────────
def _paste_record_file() -> str:
    try:
        from utils.app_paths import records_file
        return records_file("paste_helper.txt")
    except Exception:
        root = os.path.dirname(_HERE) if os.path.basename(_HERE).lower() == "pages" else _HERE
        return os.path.join(root, "records", "paste_helper.txt")


# 兼容旧引用：模块加载时定一次路径；写盘前也会再取
RECORD_FILE = _paste_record_file()

# 七种常用色：红、橙、黄、绿、青、蓝、紫
CARD_COLORS = [
    ("#ef4444", "红"),
    ("#f97316", "橙"),
    ("#eab308", "黄"),
    ("#22c55e", "绿"),
    ("#06b6d4", "青"),
    ("#3b82f6", "蓝"),
    ("#a855f7", "紫"),
]
DEFAULT_COLOR = CARD_COLORS[0][0]

# 「已复制」标题里的操作说明
_PASTE_SAVE_HINT = "1、保存为新卡，填写主题后点保存；2、替换旧卡，不填主题，点「替换内容」后再点下面已有的卡片。"

# 卡片尺寸：一排稳放 4 个汉字，可排两行（固定，不随悬停变化）
# 边距须与 ThemeCard 内 layout margins 一致，否则会少算可用字宽
_CARD_MARGIN_X = 4
_CARD_MARGIN_Y = 2


def _card_font() -> QFont:
    """粘贴卡标题字体：14px / 600。"""
    f = QFont("微软雅黑", 10)
    f.setPixelSize(14)
    f.setWeight(QFont.DemiBold)
    return f


def _card_line_height() -> int:
    """标题行高：默认行距再收 2px（同提示词编辑器 _VTitle 的做法）。"""
    return max(10, int(QFontMetrics(_card_font()).lineSpacing()) - 2)


def _card_metrics():
    fm = QFontMetrics(_card_font())
    # 用真实四字串测宽（比单字×4 更准）。余量 5px、最小宽 95。
    text_w = max(fm.horizontalAdvance("汉字汉字"), fm.boundingRect("汉字汉字").width())
    text_h = _card_line_height() * 2
    w = text_w + _CARD_MARGIN_X * 2 + 5
    h = text_h + _CARD_MARGIN_Y * 2
    return max(95, w), max(50, h)

MIME_CARD_ID = "application/x-paste-helper-card-id"
DRAG_THRESHOLD = 8  # 像素，超过才算拖拽，避免误触

# ── 全局热键：Ctrl+Alt+1 加入 / Ctrl+Alt+2 减去 / Ctrl+Alt+3 互换 ──
# 与截图/速存同一路：Windows RegisterHotKey + nativeEventFilter。
# 不用 keyboard 库（Qt 卸载期会 access violation）。
# 热键 id 避开速存 0x1002–0x1006、截图 0x1010。
# 不能只用 Shift+数字：RegisterHotKey 会全局吞掉该组合，
# 美式/中文键盘上 Shift+1=!、Shift+2=@、Shift+3=#，整个系统都打不出来。
# 也不用 Ctrl+Shift（中文输入法切语言）或 Ctrl+1（浏览器切标签）。
_PASTE_HK_ADD = 0x1020
_PASTE_HK_SUB = 0x1021
_PASTE_HK_SWAP = 0x1022
_PASTE_HK_SPEC = (
    (_PASTE_HK_ADD, 0x31, 1),
    (_PASTE_HK_SUB, 0x32, 2),
    (_PASTE_HK_SWAP, 0x33, 3),
)
_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_NOREPEAT = 0x4000
_PASTE_HK_MODS = _MOD_CONTROL | _MOD_ALT | _MOD_NOREPEAT
_PASTE_HK_KEY_LABELS = {1: "Ctrl+Alt+1", 2: "Ctrl+Alt+2", 3: "Ctrl+Alt+3"}
# 用法写在按钮上。悬停说明已去掉：这三个操作不在本页里做。
_ASSIST_BTN_TEXT = {
    1: "加入  Ctrl+Alt+1\n接到后面，无空行则换行",
    2: "减去  Ctrl+Alt+2\n从剪贴板去掉",
    3: "互换  Ctrl+Alt+3\n和剪贴板对调",
}
_VK_CONTROL, _VK_C, _VK_V = 0x11, 0x43, 0x56
_VK_SHIFT, _VK_LSHIFT, _VK_RSHIFT = 0x10, 0xA0, 0xA1
_VK_MENU = 0x12
_KEYEVENTF_KEYUP = 0x0002
_WM_HOTKEY = 0x0312
_WM_COPY, _WM_PASTE = 0x0301, 0x0303
_HK_RETRY_MS = (300, 700, 1500, 3000)
_COPIED_TITLE_COLOR = "#22c55e"
_WARN_TITLE_COLOR = "#f59e0b"
_ERR_TITLE_COLOR = "#ef4444"

try:
    from utils.pre_download import PLAT_LABEL as _PLAT_HINT_NAME
except Exception:
    _PLAT_HINT_NAME = {
        "douyin": "抖音",
        "qishui": "汽水音乐",
        "bilibili": "B站",
        "youtube": "YouTube",
        "jimeng": "即梦",
        "liblib": "LibTV",
        "xvideos": "XVideos",
        "ehentai": "e-hentai",
        "pixiv": "Pixiv",
        "hitomi": "hitomi.la",
    }

try:
    _USER32 = ctypes.WinDLL("user32", use_last_error=True)
except Exception:  # pragma: no cover
    _USER32 = None
try:
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
except Exception:  # pragma: no cover
    _KERNEL32 = None

_CF_UNICODETEXT = 13
if _USER32 is not None:
    try:
        _USER32.GetClipboardData.restype = ctypes.c_void_p
        _USER32.GetClipboardData.argtypes = [ctypes.c_uint]
        _USER32.OpenClipboard.argtypes = [ctypes.c_void_p]
        _USER32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
    except Exception:
        pass
if _KERNEL32 is not None:
    try:
        _KERNEL32.GlobalLock.restype = ctypes.c_void_p
        _KERNEL32.GlobalLock.argtypes = [ctypes.c_void_p]
        _KERNEL32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    except Exception:
        pass


def _win_register_hotkey(hotkey_id: int, vk: int, mods: int = 0):
    if _USER32 is None:
        return False, -1
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
    if _USER32 is None:
        return
    try:
        _USER32.UnregisterHotKey(None, int(hotkey_id))
    except Exception:
        pass


def _clip_seq() -> int:
    if _USER32 is None:
        return 0
    try:
        return int(_USER32.GetClipboardSequenceNumber() or 0)
    except Exception:
        return 0


def _unstick_modifiers() -> None:
    """热键触发时 Shift 仍按着，不松开就发不出 Ctrl+C。"""
    if _USER32 is None:
        return
    for vk in (_VK_SHIFT, _VK_LSHIFT, _VK_RSHIFT, _VK_CONTROL, _VK_MENU):
        try:
            if _USER32.GetAsyncKeyState(vk) & 0x8000:
                _USER32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
        except Exception:
            pass


def _keybd(vk: int, up: bool = False) -> None:
    if _USER32 is None:
        return
    try:
        _USER32.keybd_event(int(vk), 0, _KEYEVENTF_KEYUP if up else 0, 0)
    except Exception:
        pass


def _send_ctrl(vk: int) -> None:
    _keybd(_VK_CONTROL, False)
    _keybd(vk, False)
    _keybd(vk, True)
    _keybd(_VK_CONTROL, True)


def _focused_hwnd():
    if _USER32 is None or _wintypes is None:
        return 0
    try:
        fg = _USER32.GetForegroundWindow()
    except Exception:
        return 0
    try:
        class _GUITHREADINFO(ctypes.Structure):
            _fields_ = (
                ("cbSize", _wintypes.DWORD),
                ("flags", _wintypes.DWORD),
                ("hwndActive", _wintypes.HWND),
                ("hwndFocus", _wintypes.HWND),
                ("hwndCapture", _wintypes.HWND),
                ("hwndMenuOwner", _wintypes.HWND),
                ("hwndMoveSize", _wintypes.HWND),
                ("hwndCaret", _wintypes.HWND),
                ("rcCaret", _wintypes.RECT),
            )
        gti = _GUITHREADINFO()
        gti.cbSize = ctypes.sizeof(_GUITHREADINFO)
        tid = _USER32.GetWindowThreadProcessId(fg, None)
        if _USER32.GetGUIThreadInfo(tid, ctypes.byref(gti)):
            return int(gti.hwndFocus or fg or 0)
        return int(fg or 0)
    except Exception:
        return int(fg or 0)


def _send_wm(msg: int) -> None:
    if _USER32 is None:
        return
    hwnd = _focused_hwnd()
    if not hwnd:
        return
    try:
        _USER32.SendMessageW(hwnd, int(msg), 0, 0)
    except Exception:
        pass


def _hwnd_is_self(hwnd) -> bool:
    if not hwnd or _USER32 is None or _wintypes is None:
        return False
    try:
        pid = _wintypes.DWORD(0)
        _USER32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
        return int(pid.value) == os.getpid()
    except Exception:
        return False


def _decode_clipboard_bytes(raw: bytes) -> str:
    """text/plain 可能是 UTF-8 / UTF-16 / GBK，按这个顺序试。"""
    if not raw:
        return ""
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16").rstrip("\x00")
    even = raw if len(raw) % 2 == 0 else (raw[:-1] if raw.endswith(b"\x00") else raw)
    if len(even) >= 4 and len(even) % 2 == 0:
        odd_zeros = sum(1 for i in range(1, min(len(even), 40), 2) if even[i] == 0)
        if odd_zeros >= 4:
            try:
                return even.decode("utf-16-le").rstrip("\x00")
            except UnicodeDecodeError:
                pass
    raw8 = raw.rstrip(b"\x00")
    if not raw8:
        return ""
    try:
        return raw8.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw8.decode("gb18030")
    except UnicodeDecodeError:
        return raw8.decode("utf-8", errors="replace")


def _clipboard_cf_unicode() -> str:
    """Windows CF_UNICODETEXT（UTF-16），避免把 UTF-8 当系统 ANSI 读成问号。"""
    if os.name != "nt" or _USER32 is None or _KERNEL32 is None:
        return ""
    if not _USER32.OpenClipboard(None):
        return ""
    try:
        if not _USER32.IsClipboardFormatAvailable(_CF_UNICODETEXT):
            return ""
        handle = _USER32.GetClipboardData(_CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = _KERNEL32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            text = ctypes.wstring_at(ptr) or ""
        finally:
            _KERNEL32.GlobalUnlock(handle)
        return text.replace("\r\n", "\n").replace("\r", "\n")
    except Exception:
        return ""
    finally:
        try:
            _USER32.CloseClipboard()
        except Exception:
            pass


def _clipboard_qt_bytes() -> str:
    cb = QApplication.clipboard()
    if cb is None:
        return ""
    md = cb.mimeData()
    if md is None:
        return cb.text() or ""
    for fmt in (
        'application/x-qt-windows-mime;value="Unicode"',
        "text/plain;charset=utf-8",
        "UTF8_STRING",
        "text/plain",
    ):
        try:
            if not md.hasFormat(fmt):
                continue
            raw = bytes(md.data(fmt))
        except Exception:
            continue
        if not raw:
            continue
        try:
            text = _decode_clipboard_bytes(raw)
        except Exception:
            continue
        if text:
            return text.replace("\r\n", "\n").replace("\r", "\n")
    return (md.text() or "").replace("\r\n", "\n").replace("\r", "\n")


def _read_clipboard_text() -> str:
    """读剪贴板纯文本：先 Unicode，再按 UTF-8 解 text/plain。"""
    text = ""
    try:
        text = _clipboard_cf_unicode()
    except Exception:
        text = ""
    if text:
        return text
    try:
        return _clipboard_qt_bytes()
    except Exception:
        return ""


def _clone_mime(md) -> QMimeData:
    out = QMimeData()
    if md is None:
        return out
    try:
        for fmt in md.formats():
            out.setData(fmt, md.data(fmt))
    except Exception:
        try:
            if md.hasText():
                out.setText(md.text() or "")
        except Exception:
            pass
    return out


def _normalize_sel_text(text: str) -> str:
    """QTextEdit.selectedText 用 U+2029 当换行。"""
    if not text:
        return ""
    return (
        text.replace("\u2029", "\n")
        .replace("\u2028", "\n")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def _clip_add(a: str, b: str) -> str:
    a = a or ""
    b = b or ""
    if not a:
        return b
    if not b:
        return a
    if a.endswith(b):
        return a
    if a.endswith(("\n", "\r", " ", "\t")) or b.startswith(("\n", "\r", " ", "\t")):
        return a + b
    return a + "\n" + b


def _clip_sub(a: str, b: str):
    """从 A 去掉 B。返回 (结果, 是否找到)。"""
    a = a or ""
    b = b or ""
    if not b:
        return a, False
    if b in a:
        return a.replace(b, ""), True
    bs = b.strip()
    if bs and bs in a:
        return a.replace(bs, ""), True
    return a, False


def _local_selected_text() -> str:
    w = QApplication.focusWidget()
    if w is None:
        return ""
    try:
        if isinstance(w, QTextEdit):
            return _normalize_sel_text(w.textCursor().selectedText() or "")
        if isinstance(w, QLineEdit):
            return _normalize_sel_text(w.selectedText() or "")
    except Exception:
        return ""
    return ""


def _replace_local_selection(text: str) -> bool:
    w = QApplication.focusWidget()
    if w is None:
        return False
    try:
        if isinstance(w, QTextEdit):
            cur = w.textCursor()
            if not cur.hasSelection():
                return False
            cur.insertText(text or "")
            return True
        if isinstance(w, QLineEdit):
            if not w.hasSelectedText():
                return False
            w.insert(text or "")
            return True
    except Exception:
        return False
    return False


class _PasteHotkeyBridge(QObject):
    """把 WM_HOTKEY 从 native filter 转到 Qt 主线程槽。"""
    fired = pyqtSignal(int)


class _PasteHotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, hid_to_op: dict, bridge: _PasteHotkeyBridge):
        super().__init__()
        self._map = {int(k): int(v) for k, v in (hid_to_op or {}).items()}
        self._bridge = bridge

    def nativeEventFilter(self, eventType, message):
        try:
            if eventType not in ("windows_generic_MSG", "windows_dispatcher_MSG"):
                return False, 0
            if _wintypes is None:
                return False, 0
            msg = _wintypes.MSG.from_address(int(message))
            if msg.message == _WM_HOTKEY:
                op = self._map.get(int(msg.wParam))
                if op:
                    self._bridge.fired.emit(op)
                    return True, 0
        except Exception:
            log.exception("粘贴助手热键消息过滤失败")
        return False, 0


def _tok(name, fallback):
    try:
        v = tk(name)
        return v if v else fallback
    except Exception:
        return fallback


def _transparent_bg(w: QWidget) -> QWidget:
    """中间层容器透明底：避免吃到全局 QWidget 画布色，与 GroupBox 功能区色不一致。"""
    w.setAttribute(Qt.WA_StyledBackground, True)
    w.setAutoFillBackground(False)
    w.setStyleSheet("background: transparent;")
    return w



_ICON_BTN = 28
_ICON_GLYPH = 16


def _clipboard_icon(size: int, color: str) -> QIcon:
    """线框剪贴板（复制）。"""
    size = max(12, int(size))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = float(size)
    pen = QPen(QColor(color), max(1.3, s * 0.09))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(s * 0.20, s * 0.28, s * 0.60, s * 0.58), s * 0.08, s * 0.08)
    p.drawRoundedRect(QRectF(s * 0.33, s * 0.12, s * 0.34, s * 0.22), s * 0.06, s * 0.06)
    p.drawLine(QPointF(s * 0.34, s * 0.50), QPointF(s * 0.66, s * 0.50))
    p.drawLine(QPointF(s * 0.34, s * 0.64), QPointF(s * 0.58, s * 0.64))
    p.end()
    return QIcon(pm)


def _trash_icon(size: int, color: str) -> QIcon:
    """线框垃圾桶（删除）。"""
    size = max(12, int(size))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = float(size)
    pen = QPen(QColor(color), max(1.4, s * 0.09))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawLine(QPointF(s * 0.26, s * 0.32), QPointF(s * 0.74, s * 0.32))
    p.drawLine(QPointF(s * 0.38, s * 0.32), QPointF(s * 0.38, s * 0.22))
    p.drawLine(QPointF(s * 0.62, s * 0.32), QPointF(s * 0.62, s * 0.22))
    p.drawLine(QPointF(s * 0.34, s * 0.22), QPointF(s * 0.66, s * 0.22))
    p.drawLine(QPointF(s * 0.30, s * 0.32), QPointF(s * 0.34, s * 0.80))
    p.drawLine(QPointF(s * 0.70, s * 0.32), QPointF(s * 0.66, s * 0.80))
    p.drawLine(QPointF(s * 0.34, s * 0.80), QPointF(s * 0.66, s * 0.80))
    p.end()
    return QIcon(pm)


def _icon_action_btn(object_name: str, icon: QIcon) -> QPushButton:
    b = QPushButton()
    b.setObjectName(object_name)
    b.setFixedSize(_ICON_BTN, _ICON_BTN)
    b.setIcon(icon)
    b.setIconSize(QSize(_ICON_GLYPH, _ICON_GLYPH))
    b.setCursor(Qt.PointingHandCursor)
    b.setFocusPolicy(Qt.NoFocus)
    b.setFlat(True)
    return b


# ── 存储 ─────────────────────────────────────────────────────────────────────
def _ensure_dir():
    global RECORD_FILE
    RECORD_FILE = _paste_record_file()
    os.makedirs(os.path.dirname(RECORD_FILE), exist_ok=True)


def _normalize_record(r: dict) -> dict:
    if "theme" not in r and "title" in r:
        r["theme"] = r.get("title") or ""
    if "color" not in r or not r.get("color"):
        r["color"] = DEFAULT_COLOR
    if "order" not in r:
        r["order"] = 0
    return r


def _load_records() -> list:
    if not os.path.exists(RECORD_FILE):
        return []
    records = []
    with open(RECORD_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(_normalize_record(json.loads(line)))
            except json.JSONDecodeError:
                continue
    # 旧数据无 order：按原文件顺序编号
    if records and all(int(r.get("order") or 0) == 0 for r in records):
        for i, r in enumerate(records):
            r["order"] = i
    return records


def _save_records(records: list):
    """原子写入 paste_helper.txt（写 .tmp 再 replace，避免半截损坏）。"""
    _ensure_dir()
    tmp = RECORD_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, RECORD_FILE)


def _sorted_by_order(records: list) -> list:
    return sorted(records, key=lambda r: int(r.get("order") or 0))


def _reindex(records: list) -> list:
    """按当前列表顺序重写 order：0..n-1"""
    for i, r in enumerate(records):
        r["order"] = i
    return records


# ── 流式布局 ─────────────────────────────────────────────────────────────────
class FlowLayout(QLayout):
    """
    卡片自动换行。

    注意：minimumSize / sizeHint 只按「单张卡片」算，绝不按
    heightForWidth(窄宽度) 把所有卡片竖着叠起来的高度上报——
    否则会一路顶到顶层窗口，把 minimumHeight 越撑越大，窗口再也缩不小
   （主程序里 wordWrap / FlowLayout 踩过的同类坑）。
    """

    def __init__(self, parent=None, margin=0, h_spacing=10, v_spacing=10):
        super().__init__(parent)
        self._h = h_spacing
        self._v = v_spacing
        self._items = []
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._layout(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, False)

    def sizeHint(self):
        # 给滚动区一个「单行高度」的合理提示，不把整列卡片高度上报
        return self.minimumSize()

    def minimumSize(self):
        # 只取子项中最大的那一个，不累加、不按窄宽叠高
        s = QSize(0, 0)
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
            s = s.expandedTo(it.sizeHint())
        if s.width() <= 0 or s.height() <= 0:
            cw, ch = _card_metrics()
            s = QSize(cw, ch)
        l, t, r, b = self.getContentsMargins()
        return s + QSize(l + r, t + b)

    def _layout(self, rect, test_only):
        l, t, r, b = self.getContentsMargins()
        effective = rect.adjusted(l, t, -r, -b)
        x, y = effective.x(), effective.y()
        line_h = 0
        # 宽度无效时按单行处理，避免测试布局时算出夸张高度
        if effective.width() <= 0:
            for it in self._items:
                hint = it.sizeHint()
                line_h = max(line_h, hint.height())
            return line_h + t + b

        for it in self._items:
            hint = it.sizeHint()
            next_x = x + hint.width() + self._h
            if next_x - self._h > effective.right() and line_h > 0:
                x = effective.x()
                y = y + line_h + self._v
                next_x = x + hint.width() + self._h
                line_h = 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + b


# ── 颜色工具 ─────────────────────────────────────────────────────────────────
def _lighten(hex_color: str, factor: float = 0.14) -> str:
    c = QColor(hex_color)
    return QColor(
        min(255, int(c.red() + (255 - c.red()) * factor)),
        min(255, int(c.green() + (255 - c.green()) * factor)),
        min(255, int(c.blue() + (255 - c.blue()) * factor)),
    ).name()


# ── 自绘色块：录入菱形 / 筛选圆点 ─────────────────────────────────────────────
class DiamondSwatch(QAbstractButton):
    """录入区色块：菱形；边长 = 旧圆点一半 × 1.5。

    选中态：菱形略放大 + 高对比描边（亮色用深环，避免白边在浅底上「变小」）。
    """

    # 旧基准 22 的一半 = 11，再 ×1.5 → 17
    SIZE = max(12, int(round((_SWATCH_BASE // 2) * 1.5)))  # 17
    # 槽位留足选中放大与描边，避免裁切
    SLOT = SIZE + 12  # 29

    def __init__(self, color_hex: str, parent=None):
        super().__init__(parent)
        self._color = color_hex
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(self.SLOT, self.SLOT)
        self.setProperty("swatch", color_hex)
        # 透明底：菱形外四角不套全局 QWidget 画布色
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent; border: none;")

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        checked = self.isChecked()
        hover = self.underMouse()
        # 选中略放大（约 +12%），悬停略抬一点；描边画在外侧不吃填充面积
        if checked:
            scale = 1.14
        elif hover:
            scale = 1.06
        else:
            scale = 1.0
        half = self.SIZE / 2.0 * scale
        poly = QPolygonF([
            QPointF(cx, cy - half),
            QPointF(cx + half, cy),
            QPointF(cx, cy + half),
            QPointF(cx - half, cy),
        ])

        # 亮色主题下选中用深色环；深色主题用亮环
        is_dark = True
        try:
            is_dark = bool(theme.is_dark)
        except Exception:
            is_dark = True

        p.setBrush(QBrush(QColor(self._color)))
        if checked:
            if is_dark:
                ring = QColor("#ffffff")
            else:
                # 浅底上白描边几乎看不见，还会像把色块「削小」
                ring = QColor("#0f172a")
            pen = QPen(ring, 2.2)
            pen.setJoinStyle(Qt.MiterJoin)
            p.setPen(pen)
            p.drawPolygon(poly)
            # 内圈高光：加深选中感，不缩小色面
            if not is_dark:
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(255, 255, 255, 180), 1.2))
                inset = half * 0.78
                poly_in = QPolygonF([
                    QPointF(cx, cy - inset),
                    QPointF(cx + inset, cy),
                    QPointF(cx, cy + inset),
                    QPointF(cx - inset, cy),
                ])
                p.drawPolygon(poly_in)
        elif hover:
            if is_dark:
                pen = QPen(QColor(255, 255, 255, 210), 1.4)
            else:
                pen = QPen(QColor(15, 23, 42, 140), 1.4)
            p.setPen(pen)
            p.drawPolygon(poly)
        else:
            if is_dark:
                pen = QPen(QColor(255, 255, 255, 70), 1.0)
            else:
                pen = QPen(QColor(15, 23, 42, 55), 1.0)
            p.setPen(pen)
            p.drawPolygon(poly)
        p.end()

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)


class CircleFilterSwatch(QAbstractButton):
    """
    仓库筛选色块：槽位固定，避免布局抖动。
    基准 22 × 1.5 = 33；未选中直径 ≈ 30%，选中 ≈ 70%。
    """

    SLOT = max(16, int(round(_SWATCH_BASE * 1.5)))  # 22 → 33
    R_OFF = 0.30
    R_ON = 0.70

    def __init__(self, color_hex: str, parent=None):
        super().__init__(parent)
        self._color = color_hex
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(self.SLOT, self.SLOT)
        self.setProperty("swatch", color_hex)
        # 透明底：圆外槽位不套全局 QWidget 画布色
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent; border: none;")

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        checked = self.isChecked()
        ratio = self.R_ON if checked else self.R_OFF
        d = self.SLOT * ratio
        x = (self.width() - d) / 2.0
        y = (self.height() - d) / 2.0
        p.setBrush(QBrush(QColor(self._color)))

        is_dark = True
        try:
            is_dark = bool(theme.is_dark)
        except Exception:
            pass

        if checked:
            # 亮色用深环，避免白边在浅底上「削小」选中圆
            ring = QColor("#ffffff") if is_dark else QColor("#0f172a")
            pen = QPen(ring, 2.0)
        elif self.underMouse():
            if is_dark:
                pen = QPen(QColor(255, 255, 255, 200), 1.4)
            else:
                pen = QPen(QColor(15, 23, 42, 140), 1.4)
        else:
            if is_dark:
                pen = QPen(QColor(255, 255, 255, 60), 1.0)
            else:
                pen = QPen(QColor(15, 23, 42, 50), 1.0)
        p.setPen(pen)
        p.drawEllipse(QRectF(x, y, d, d))
        p.end()

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)


# ── 录入区：七色选色条 ───────────────────────────────────────────────────────
class ColorPickerBar(QWidget):
    """七色菱形，单选。边长约为旧圆点一半 × 1.5。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        _transparent_bg(self)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._color = DEFAULT_COLOR

        wrap = QVBoxLayout(self)
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.setSpacing(0)

        lay = QHBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        for i, (hex_c, name) in enumerate(CARD_COLORS):
            btn = DiamondSwatch(hex_c)
            self._group.addButton(btn, i)
            lay.addWidget(btn, 0, Qt.AlignVCenter)

        lay.addStretch(1)
        wrap.addLayout(lay)
        self._group.buttonClicked.connect(self._on_clicked)
        first = self._group.button(0)
        if first:
            first.setChecked(True)

    def _on_clicked(self, btn):
        self._color = btn.property("swatch") or DEFAULT_COLOR
        # 菱形自绘，勾选态在 paintEvent 里读 isChecked()
        for b in self._group.buttons():
            b.update()

    def color(self) -> str:
        return self._color or DEFAULT_COLOR

    def set_color(self, color: str):
        color = (color or DEFAULT_COLOR).lower()
        matched = False
        for b in self._group.buttons():
            hex_c = (b.property("swatch") or "").lower()
            if hex_c == color:
                b.setChecked(True)
                self._color = b.property("swatch")
                matched = True
            else:
                b.setChecked(False)
        if not matched:
            b0 = self._group.button(0)
            if b0:
                b0.setChecked(True)
                self._color = DEFAULT_COLOR
        for b in self._group.buttons():
            b.update()


# ── 仓库区：七色快速过滤（支持多色同时选中）────────────────────────────────
class ColorFilterBar(QWidget):
    """
    [筛选/取消] + 七色圆点（可多选）。
    · 无文字标签；按钮文案：无色选中时「筛选」，有色选中时「取消」
    · 点「取消」：清空所有颜色筛选
    · 未选中小圆 30%，选中中圆 70%；槽位固定不抖布局
    """

    def __init__(self, on_change, parent=None):
        super().__init__(parent)
        _transparent_bg(self)
        self._on_change = on_change
        self._swatches = []  # CircleFilterSwatch 列表

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.btn_action = QPushButton("筛选")
        self.btn_action.setFixedHeight(26)
        self.btn_action.setMinimumWidth(52)
        self.btn_action.setCursor(Qt.PointingHandCursor)
        self.btn_action.setObjectName("ColorFilterAction")
        self.btn_action.clicked.connect(self._on_action_clicked)
        lay.addWidget(self.btn_action)

        for hex_c, name in CARD_COLORS:
            btn = CircleFilterSwatch(hex_c)
            btn.clicked.connect(self._on_swatch_clicked)
            self._swatches.append(btn)
            lay.addWidget(btn)

        lay.addStretch(1)
        self._sync_action_btn()

    def _action_qss(self, active: bool) -> str:
        """active=True 表示当前有颜色筛选，按钮显示「取消」。"""
        if active:
            return f"""
                QPushButton#ColorFilterAction {{
                    background: rgba(240,165,66,0.16);
                    border: 1px solid rgba(240,165,66,0.50);
                    border-radius: 6px;
                    color: {_tok('warn', '#f0a542')};
                    font-size: 12px;
                    font-weight: 700;
                    padding: 0 10px;
                }}
                QPushButton#ColorFilterAction:hover {{
                    background: rgba(240,165,66,0.28);
                }}
            """
        # 未筛选：半透明底，跟功能区同色系，不套全局画布色
        return f"""
            QPushButton#ColorFilterAction {{
                background: {_tok('hover_veil', 'rgba(255,255,255,0.05)')};
                border: 1px solid {_tok('border_soft', 'rgba(255,255,255,0.18)')};
                border-radius: 6px;
                color: {_tok('text_mut', '#9fb0d7')};
                font-size: 12px;
                font-weight: 600;
                padding: 0 10px;
            }}
            QPushButton#ColorFilterAction:hover {{
                background: {_tok('row_bg', 'rgba(255,255,255,0.09)')};
            }}
        """

    def selected_colors(self) -> list:
        """当前勾选的颜色 hex 列表（可多个）。"""
        return [
            b.property("swatch")
            for b in self._swatches
            if b.isChecked() and b.property("swatch")
        ]

    def _sync_action_btn(self):
        colors = self.selected_colors()
        active = len(colors) > 0
        self.btn_action.setText("取消" if active else "筛选")
        self.btn_action.setStyleSheet(self._action_qss(active))
        for b in self._swatches:
            b.update()

    def _emit(self):
        if callable(self._on_change):
            self._on_change(self.selected_colors())

    def _on_swatch_clicked(self):
        # 多选：每个色块独立勾选/取消，不互斥
        self._sync_action_btn()
        self._emit()

    def _on_action_clicked(self):
        if self.selected_colors():
            # 「取消」：清空所有颜色筛选
            self.clear()
            self._emit()
        # 无筛选时点「筛选」：无需操作（靠点色块开始筛）

    def clear(self):
        for b in self._swatches:
            b.setChecked(False)
            b.update()
        self._sync_action_btn()


# ── 主题卡片 ─────────────────────────────────────────────────────────────────
def _wrap_card_text(text: str, fm, width: int) -> list:
    """把标题按宽度逐字换行（最多两行，超出由卡片高度裁掉）。"""
    width = max(8, int(width))
    lines = []
    cur = ""
    for ch in (text or ""):
        trial = cur + ch
        if not cur or fm.horizontalAdvance(trial) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


class _CardTitle(QWidget):
    """粘贴卡标题：最多两行自动换行；行距比默认收 2px；配色走主题文字色
    （参考「图片处理 → 提高清晰度」的文字）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._fg = QColor("#d7def7")

    def set_title(self, text: str):
        self._text = (text or "").strip()
        self.update()
        self.updateGeometry()

    def set_color(self, color: str):
        try:
            c = QColor(color)
            if c.isValid():
                self._fg = c
        except Exception:
            pass
        self.update()

    def sizeHint(self):
        return QSize(4, _card_line_height() * 2)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        f = _card_font()
        p.setFont(f)
        p.setPen(self._fg)
        fm = QFontMetrics(f)
        step = _card_line_height()
        lines = _wrap_card_text(self._text, fm, self.width())[:2]
        if not lines:
            p.end()
            return
        total = step * len(lines)
        y0 = max(0, (self.height() - total) // 2)
        for i, ln in enumerate(lines):
            p.drawText(
                QRect(0, y0 + i * step, self.width(), step),
                Qt.AlignHCenter | Qt.AlignVCenter,
                ln,
            )
        p.end()


class ThemeCard(QFrame):
    """
    仓库卡片：只显示主题文字。
    · 单击 = 复制 + 进入修改
    · 拖拽 = 改顺序
    · 右键 = 删除
    热区：仅边框/亮度变化，尺寸始终不变。
    """

    def __init__(self, record, page, parent=None):
        super().__init__(parent)
        self._record = record
        self._page = page
        self._press_pos = None
        self._dragging = False
        self._selected = False

        self.setObjectName("ThemeCard")
        self.setAcceptDrops(True)
        self.setCursor(Qt.OpenHandCursor)
        # 固定尺寸：约 4 字宽 × 2 行高，杜绝悬停变大变小
        cw, ch = _card_metrics()
        self.setFixedSize(cw, ch)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        theme = (record.get("theme") or record.get("title") or "未命名").strip() or "未命名"

        lay = QVBoxLayout(self)
        lay.setContentsMargins(_CARD_MARGIN_X, _CARD_MARGIN_Y, _CARD_MARGIN_X, _CARD_MARGIN_Y)
        lay.setSpacing(0)

        self.lbl_theme = _CardTitle()
        self.lbl_theme.set_title(theme)
        # 标题盖住整张卡。不接收鼠标，点击才落在卡片上，避免被当成点了空白而取消「替换中」
        self.lbl_theme.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.lbl_theme, 1)

        self._apply_style(record.get("color") or DEFAULT_COLOR, selected=False)
        self._watch_frozen = False

    def set_watch_frozen(self, frozen: bool):
        """辅助粘贴查看中：卡片还在，但不能点、拖、删。"""
        frozen = bool(frozen)
        self._watch_frozen = frozen
        if frozen:
            effect = QGraphicsOpacityEffect(self)
            effect.setOpacity(0.4)
            self.setGraphicsEffect(effect)
            self.setCursor(Qt.ForbiddenCursor)
        else:
            self.setGraphicsEffect(None)
            self.setCursor(Qt.OpenHandCursor)

    def record_id(self):
        return self._record.get("id")

    def set_selected(self, selected: bool):
        # 状态未变则跳过：避免重复 setStyleSheet 引发卡片/流式布局闪动
        if self._selected == selected:
            return
        self._selected = selected
        self._apply_style(self._record.get("color") or DEFAULT_COLOR, selected=selected)

    def _apply_style(self, color: str, selected: bool = False):
        hover = _lighten(color, 0.10)
        # 边框始终同宽，只改颜色——选中时若宽度切换，流式排布会抖一下
        if selected:
            border = "#ffffff"
            hover_border = "#ffffff"
        else:
            border = "rgba(255,255,255,0.14)"
            hover_border = "rgba(255,255,255,0.55)"
        border_w = 3
        # 注意：hover 不改变 width/height/padding/margin，只改颜色与边框色
        self.setStyleSheet(f"""
            QFrame#ThemeCard {{
                background-color: {color};
                border: {border_w}px solid {border};
                border-radius: 6px;
            }}
            QFrame#ThemeCard:hover {{
                background-color: {hover};
                border: {border_w}px solid {hover_border};
            }}
        """)
        try:
            self.lbl_theme.set_color("#ffffff")
        except Exception:
            self.lbl_theme.set_color("#ffffff")

    def mousePressEvent(self, e):
        if getattr(self, "_watch_frozen", False):
            e.accept()
            return
        if e.button() == Qt.LeftButton:
            self._press_pos = e.pos()
            self._dragging = False
            self.setCursor(Qt.ClosedHandCursor)
            e.accept()
            return
        elif e.button() == Qt.RightButton:
            self._page._delete(self._record)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if getattr(self, "_watch_frozen", False):
            return
        if not (e.buttons() & Qt.LeftButton) or self._press_pos is None:
            return
        if (e.pos() - self._press_pos).manhattanLength() < DRAG_THRESHOLD:
            return
        self._dragging = True
        self._start_drag()

    def mouseReleaseEvent(self, e):
        if getattr(self, "_watch_frozen", False):
            e.accept()
            return
        self.setCursor(Qt.OpenHandCursor)
        if e.button() == Qt.LeftButton and not self._dragging:
            # 单击：复制 + 选中 + 进入录入区编辑
            self._page._click_card(self._record)
        self._press_pos = None
        self._dragging = False
        super().mouseReleaseEvent(e)

    def _start_drag(self):
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(MIME_CARD_ID, str(self.record_id()).encode("utf-8"))
        drag.setMimeData(mime)

        # 半透明拖影（尺寸与卡片一致，不放大）
        pix = QPixmap(self.size())
        pix.fill(Qt.transparent)
        self.render(pix)
        painter = QPainter(pix)
        painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        painter.fillRect(pix.rect(), QColor(0, 0, 0, 170))
        painter.end()
        drag.setPixmap(pix)
        drag.setHotSpot(self._press_pos if self._press_pos else QPoint(self.width() // 2, self.height() // 2))

        # 拖拽结束后可能仍会收到 release，保持 _dragging 避免误触发单击复制
        drag.exec_(Qt.MoveAction)
        self._dragging = True

    def dragEnterEvent(self, e):
        if getattr(self, "_watch_frozen", False):
            e.ignore()
            return
        if e.mimeData().hasFormat(MIME_CARD_ID):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if getattr(self, "_watch_frozen", False):
            e.ignore()
            return
        if e.mimeData().hasFormat(MIME_CARD_ID):
            e.acceptProposedAction()
            try:
                host = self._page.cards_host
                src_id = bytes(e.mimeData().data(MIME_CARD_ID)).decode("utf-8")
                if src_id == str(self.record_id()):
                    # 悬停在自身：不显示插入线（放下也是 no-op）
                    host.clear_drop_line()
                else:
                    host.show_drop_line_for_card(self, e.pos().x() > self.width() / 2)
            except Exception:
                pass
        else:
            e.ignore()

    def dropEvent(self, e):
        if getattr(self, "_watch_frozen", False):
            e.ignore()
            return
        if not e.mimeData().hasFormat(MIME_CARD_ID):
            e.ignore()
            return
        src_id = bytes(e.mimeData().data(MIME_CARD_ID)).decode("utf-8")
        tgt_id = str(self.record_id())
        if src_id and src_id != tgt_id:
            # 放到目标卡片的左/右半：决定插在前还是后
            insert_after = e.pos().x() > self.width() / 2
            self._page._reorder_cards(src_id, tgt_id, insert_after=insert_after)
        try:
            self._page.cards_host.clear_drop_line()
        except Exception:
            pass
        e.acceptProposedAction()


# ── 卡片容器（空白处也可接住拖放）────────────────────────────────────────────
class CardsDropHost(QWidget):
    def __init__(self, page, parent=None):
        super().__init__(parent)
        self._page = page
        self.setAcceptDrops(True)
        self.setObjectName("RecordsContainer")
        _transparent_bg(self)
        # 不把卡片总高度/总宽度当成窗口最小尺寸（交给外层 QScrollArea 滚动）
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        # 拖拽重排时显示的白色预插入线（QRect；None = 不显示）
        self._drop_line = None

    def minimumSizeHint(self):
        # 阻断 FlowLayout.heightForWidth 把「竖着叠满」的高度传给主窗口
        cw, ch = _card_metrics()
        return QSize(cw, ch)

    def sizeHint(self):
        lay = self.layout()
        if lay is not None and lay.hasHeightForWidth():
            w = max(self.width(), self.minimumSizeHint().width())
            return QSize(w, lay.heightForWidth(w))
        return self.minimumSizeHint()

    def _card_at(self, pos):
        w = self.childAt(pos)
        while w is not None and w is not self:
            if isinstance(w, ThemeCard):
                return w
            w = w.parentWidget()
        return None

    # ── 拖拽白色预插入线 ─────────────────────────────────────
    def _set_drop_line(self, rect):
        self._drop_line = rect
        self.update()

    def clear_drop_line(self):
        if self._drop_line is not None:
            self._drop_line = None
            self.update()

    def _flow_spacing(self) -> int:
        """卡片横向间距（跟随仓库 FlowLayout 的 h_spacing）。"""
        try:
            return int(getattr(self.layout(), "_h", None) or 4)
        except Exception:
            return 4

    def show_drop_line_for_card(self, card, after: bool):
        """在某张卡片前/后显示白色竖线，线放在与相邻卡缝隙的正中间。"""
        g = card.geometry()
        if not g.isValid() or g.isEmpty():
            self.clear_drop_line()
            return
        sp = self._flow_spacing()
        # after → 右边缘进半个间距；before → 左边缘进半个间距（落点相同，线居中在缝隙里）
        x = g.right() + sp // 2 if after else g.left() - sp // 2 - 1
        self._set_drop_line(QRect(x, g.top(), 2, g.height()))

    def _end_card_geom(self):
        """流程末尾卡片（最后一行右下角）的 geometry；无卡片返回 None。"""
        best = None
        for c in self.findChildren(ThemeCard):
            g = c.geometry()
            if not g.isValid() or g.isEmpty():
                continue
            if (
                best is None
                or g.bottom() > best.bottom()
                or (g.bottom() == best.bottom() and g.right() > best.right())
            ):
                best = g
        return best

    def show_drop_line(self, pos, src_id=None):
        """按鼠标位置计算插入线：在卡片左/右半 → 插其前/后；空白 → 插到末尾。
        没有有效插入位置（悬停源卡片自身 / 源卡片已在末尾）时隐藏。"""
        card = self._card_at(pos)
        if card is not None:
            if src_id is not None and src_id == str(card.record_id()):
                self.clear_drop_line()
                return
            self.show_drop_line_for_card(card, pos.x() > card.geometry().center().x())
            return
        end = self._end_card_geom()
        if end is None:
            self.clear_drop_line()
            return
        if src_id is not None:
            end_card = None
            for c in self.findChildren(ThemeCard):
                if c.geometry() == end:
                    end_card = c
                    break
            if end_card is not None and src_id == str(end_card.record_id()):
                # 源卡片本身已在末尾：插到末尾 = 无变化，不显示插入线
                self.clear_drop_line()
                return
        sp = self._flow_spacing()
        self._set_drop_line(QRect(end.right() + sp // 2, end.top(), 2, end.height()))

    def paintEvent(self, _e):
        super().paintEvent(_e)
        r = self._drop_line
        if r is None or r.width() <= 0 or r.height() <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.fillRect(r, QColor(255, 255, 255))
        p.end()

    def mousePressEvent(self, e):
        # 仓库空白处左键 = 取消选中。查看系统剪贴板时仓库是冻住的。
        if getattr(self._page, "_assist_watch", False):
            e.accept()
            return
        if e.button() == Qt.LeftButton and self._card_at(e.pos()) is None:
            self._page._cancel_edit()
        super().mousePressEvent(e)

    def dragEnterEvent(self, e):
        if getattr(self._page, "_assist_watch", False):
            e.ignore()
            return
        if e.mimeData().hasFormat(MIME_CARD_ID):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if getattr(self._page, "_assist_watch", False):
            e.ignore()
            return
        if e.mimeData().hasFormat(MIME_CARD_ID):
            e.acceptProposedAction()
            src_id = bytes(e.mimeData().data(MIME_CARD_ID)).decode("utf-8")
            self.show_drop_line(e.pos(), src_id=src_id)
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self.clear_drop_line()
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        if getattr(self._page, "_assist_watch", False):
            e.ignore()
            return
        if not e.mimeData().hasFormat(MIME_CARD_ID):
            e.ignore()
            return
        src_id = bytes(e.mimeData().data(MIME_CARD_ID)).decode("utf-8")
        card = self._card_at(e.pos())
        if card is not None:
            insert_after = e.pos().x() > card.geometry().center().x()
            self._page._reorder_cards(src_id, str(card.record_id()), insert_after=insert_after)
        else:
            self._page._reorder_to_end(src_id)
        self.clear_drop_line()
        e.acceptProposedAction()


class _AssistWatchButton(QAbstractButton):
    """辅助粘贴标题右侧：滑动开关。开=查看系统剪贴板。外观对齐侧栏开关。"""

    W, H = 36, 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setObjectName("AssistWatchBtn")
        on_color = "#22c55e"
        try:
            on_color = tk("ok") or on_color
        except Exception:
            pass
        self._on_c0 = QColor(on_color)
        self._on_c1 = QColor(on_color).darker(112)
        self._knob = 0.0
        self._anim = QPropertyAnimation(self, b"knobPos", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._slide)
        try:
            theme.changed.connect(self.update)
        except Exception:
            pass

    def getKnobPos(self):
        return self._knob

    def setKnobPos(self, v):
        self._knob = max(0.0, min(1.0, float(v)))
        self.update()

    knobPos = pyqtProperty(float, fget=getKnobPos, fset=setKnobPos)

    def _slide(self, on: bool):
        self._anim.stop()
        self._anim.setStartValue(self._knob)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        t = self._knob
        dark = True
        try:
            dark = bool(theme.is_dark)
        except Exception:
            pass
        track = QRectF(1.0, 2.0, w - 2.0, h - 4.0)
        radius = track.height() / 2.0
        if t < 0.02:
            if dark:
                base = QColor(255, 255, 255, 28)
                rim = QColor(255, 255, 255, 48)
            else:
                base = QColor(148, 163, 184, 90)
                rim = QColor(148, 163, 184, 140)
            p.setPen(QPen(rim, 1.0))
            p.setBrush(QBrush(base))
            p.drawRoundedRect(track, radius, radius)
        else:
            g = QLinearGradient(track.topLeft(), track.topRight())
            c0 = QColor(self._on_c0)
            c1 = QColor(self._on_c1)
            if t < 1.0:
                c0.setAlpha(int(80 + 175 * t))
                c1.setAlpha(int(80 + 175 * t))
            g.setColorAt(0, c0)
            g.setColorAt(1, c1)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(g))
            p.drawRoundedRect(track, radius, radius)
            hi = QLinearGradient(track.topLeft(), QPointF(track.left(), track.center().y()))
            hi.setColorAt(0, QColor(255, 255, 255, int(50 * t)))
            hi.setColorAt(1, QColor(255, 255, 255, 0))
            p.setBrush(QBrush(hi))
            p.drawRoundedRect(track.adjusted(0.5, 0.5, -0.5, -track.height() * 0.35), radius, radius)

        pad = 3.0
        kn = h - 2.0 * pad
        x0 = pad
        x1 = w - pad - kn
        kx = x0 + (x1 - x0) * t
        knob = QRectF(kx, pad, kn, kn)
        sh = QRadialGradient(knob.center() + QPointF(0, 1), kn * 0.7)
        sh.setColorAt(0, QColor(0, 0, 0, 50))
        sh.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(sh))
        p.drawEllipse(knob.adjusted(-1, 0, 1, 2))
        kg = QLinearGradient(knob.topLeft(), knob.bottomLeft())
        kg.setColorAt(0, QColor("#FFFFFF"))
        kg.setColorAt(1, QColor("#F1F5F9"))
        p.setBrush(QBrush(kg))
        p.setPen(QPen(QColor(0, 0, 0, 28), 1.0))
        p.drawEllipse(knob)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(255, 255, 255, 160)))
        p.drawEllipse(QRectF(kx + kn * 0.22, pad + kn * 0.15, kn * 0.45, kn * 0.28))
        p.end()


# ── 主页面 ───────────────────────────────────────────────────────────────────
class PagePaste(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        # 页面本身不向外强加最小尺寸，由外层窗口 setMinimumSize 说了算
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._selected_id = None   # 仓库卡片选中（高亮），不等于录入编辑
        self._editing_id = None    # 仅双击进入「主题/内容」编辑时才有值
        self._all_records = []
        self._card_widgets = {}  # id -> ThemeCard
        self._filter_colors = set()  # 空集合=不限颜色；可多色同时筛选

        self._copied_live = False
        self._last_clip_text = None
        self._applying_clip = False
        self._ignore_clip = 0
        self._clip_op_busy = False
        self._hotkeys_ok = False
        self._hotkey_filter = None
        self._hotkey_bridge = None
        self._hotkey_bound_ids = ()
        self._clip_watch_connected = False
        self._clipboard_capture_enabled = False  # 侧栏剪贴板归属；默认关
        self._assist_watch = False  # 辅助粘贴开关：只看系统剪贴板
        self._entry_title_tone = "idle"
        self._entry_title_text = "录入"
        self._theme_mode = "draft"
        self._entry_frame_color = ""
        self._capture_pending = False  # 内容框正在显示外部复制，尚未保存
        self._replace_armed = False    # 已点「替换内容」，等用户再点一张已有卡

        self._build_ui()
        self._install_blank_click_filter()
        self._reload()
        self._bind_clipboard_watch()
        # 辅助粘贴默认关：热键等开关打开再注册，避免空占 Ctrl+Alt+1/2/3
        try:
            theme.changed.connect(self.restyle_theme)
        except Exception:
            pass
        # 启动不主动灌剪贴板；归属开启后再 sync-now

    # ===== 窗口高度 BUG 防护（与截图/速存/时区汇率页同一套路）=====
    # FlowLayout / wordWrap 会让整页 hasHeightForWidth()=True，Qt 拖窗口时抬高推荐高度。
    # 切断整页 HFW；内部滚动/换行照常。
    def hasHeightForWidth(self):
        return False

    def minimumSizeHint(self):
        # 防止子控件（FlowLayout / 标签）把最小高度顶破窗口
        return QSize(0, 0)

    def sizeHint(self):
        return QSize(1080, 720)

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self):
        page = QVBoxLayout(self)
        # 与系统总览一致：ContentRoot 已有左右内边距，页面不再叠第二层
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(12)
        # 仓库高度减 25%：原 25/75 → 44/56（录入 44% · 仓库 56%）
        page.addWidget(self._build_entry_panel(), 44)
        page.addWidget(self._build_warehouse_panel(), 56)

    def _build_entry_panel(self) -> QWidget:
        """
        录入区布局：
          标题 A：剪贴板状态（录入 / 已复制 · 平台）
          左 75% B：主题（卡片名）+ 内容（剪贴板文本）
          右 25%：颜色 → 保存 → 辅助粘贴，整列顶对齐
        """
        # 录入区：不要外层卡壳/描边，只要标题 + 控件
        box = _transparent_bg(QWidget())
        box.setObjectName("CardPasteEntry")
        box.setProperty("borderless", "1")
        box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        form = QVBoxLayout(box)
        form.setContentsMargins(0, 0, 0, 0)  # 无卡内边距，外框感一并去掉
        form.setSpacing(8)
        self.lbl_entry_title = install_card_title(box, form, "录入")
        self.lbl_entry_title.setWordWrap(True)
        self.lbl_entry_title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        _title_head = self.lbl_entry_title.parentWidget()
        if _title_head is not None:
            _title_head.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)  # 中间 12px 分隔
        # 不要 AlignTop：否则左侧 Expanding 也不吃满高度，内容框会跟着 sizeHint 变高

        # ── 左 75%：主题 + 内容（标签并入 placeholder，不占侧栏宽度）────────
        left = _transparent_bg(QWidget())
        left.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(8)

        # 主题 / 内容：圆角浅色底（PasteThemeInput / PasteContentEdit，见 app.qss）
        # 注意：QTextEdit 默认 documentMargin=4，会比 QLineEdit 多缩进，占位「主题/内容」左不对齐
        self.inp_theme = QLineEdit()
        self.inp_theme.setObjectName("PasteThemeInput")
        self.inp_theme.setProperty("pasteEntry", "1")
        self.inp_theme.setPlaceholderText("主题")
        self.inp_theme.setFixedHeight(32)
        self.inp_theme.setTextMargins(0, 0, 0, 0)
        self.inp_theme.textChanged.connect(self._on_theme_edited)
        left_l.addWidget(self.inp_theme)

        self.inp_content = QTextEdit()
        self.inp_content.setObjectName("PasteContentEdit")
        self.inp_content.setProperty("pasteEntry", "1")
        self.inp_content.setPlaceholderText("内容")
        self.inp_content.setMinimumHeight(60)
        self.inp_content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # 不按文档内容调整自身尺寸，避免 setPlainText 后高度与空态不一致
        try:
            from PyQt5.QtWidgets import QAbstractScrollArea
            self.inp_content.setSizeAdjustPolicy(QAbstractScrollArea.AdjustIgnored)
        except Exception:
            pass
        # 去掉文档默认内边距，与主题行 QLineEdit 共用同一套 QSS padding，首字左对齐
        try:
            self.inp_content.document().setDocumentMargin(0)
        except Exception:
            pass
        self.inp_content.setViewportMargins(0, 0, 0, 0)
        # 竖向滚动条样式对齐截图工具「截图与操作记录」(recordStyle dashed 标准)
        # 内容不足时隐藏，避免空输入框右侧也常驻滚轨
        self.inp_content.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.inp_content.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inp_content.textChanged.connect(self._on_content_edited)
        left_l.addWidget(self.inp_content, 1)

        # ── 右 25%：颜色 / 保存 / 辅助粘贴，整列顶对齐 ────────
        right = _transparent_bg(QWidget())
        right.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(10)
        right_l.setAlignment(Qt.AlignTop)

        self.color_bar = ColorPickerBar()
        self.color_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        right_l.addWidget(self.color_bar, 0, Qt.AlignTop)

        self._row_btn_h = 28

        # 保存/更新 + 间距 + 复制图标 + 删除图标
        row_save = QHBoxLayout()
        row_save.setContentsMargins(0, 0, 0, 0)
        row_save.setSpacing(0)
        self.btn_save = QPushButton("保存")
        apply_btn_download(self.btn_save)
        self.btn_save.setFixedHeight(self._row_btn_h)
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_save.clicked.connect(self._on_save_clicked)
        row_save.addWidget(self.btn_save, 1)
        row_save.addSpacing(8)

        # 图标槽位固定宽，隐藏时仍占位，避免「更新」按钮左右跳
        self.edit_icons = _transparent_bg(QWidget())
        self.edit_icons.setFixedWidth(_ICON_BTN + 6 + _ICON_BTN)
        self.edit_icons.setFixedHeight(_ICON_BTN)
        icons_l = QHBoxLayout(self.edit_icons)
        icons_l.setContentsMargins(0, 0, 0, 0)
        icons_l.setSpacing(6)

        try:
            copy_color = tk("text_mut")
        except Exception:
            copy_color = "#9fb0d7"
        del_color = "#f87171"

        self.btn_copy = _icon_action_btn("RecordCopyBtn", _clipboard_icon(_ICON_GLYPH, copy_color))
        self.btn_copy.clicked.connect(self._duplicate_selected)
        icons_l.addWidget(self.btn_copy)

        self.btn_delete = _icon_action_btn("RecordDelBtn", _trash_icon(_ICON_GLYPH, del_color))
        self.btn_delete.clicked.connect(self._delete_selected)
        icons_l.addWidget(self.btn_delete)

        row_save.addWidget(self.edit_icons, 0, Qt.AlignVCenter)
        right_l.addLayout(row_save)
        self._set_selection_actions_frozen(True)

        right_l.addWidget(self._build_assist_paste_panel(), 0, Qt.AlignTop)
        right_l.addStretch(1)

        # 左 75% 纵向吃满 · 右 25% 同步拉高，颜色/保存顶对齐
        body.addWidget(left, 75)
        body.addWidget(right, 25)
        form.addLayout(body, 1)
        return box

    def _build_assist_paste_panel(self) -> QWidget:
        """保存下方：滑动开关 + 三个热键按钮。用法写在按钮上。"""
        box = _transparent_bg(QWidget())
        box.setObjectName("PasteAssistBox")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 2, 0, 0)
        lay.setSpacing(6)

        head = _transparent_bg(QWidget())
        head_l = QHBoxLayout(head)
        head_l.setContentsMargins(0, 0, 0, 0)
        head_l.setSpacing(6)
        self.lbl_assist_title = QLabel("辅助粘贴")
        self.lbl_assist_title.setObjectName("PasteAssistTitle")
        head_l.addWidget(self.lbl_assist_title, 1)
        self.btn_assist_watch = _AssistWatchButton()
        self.btn_assist_watch.toggled.connect(self._on_assist_watch_toggled)
        head_l.addWidget(self.btn_assist_watch, 0, Qt.AlignVCenter)
        lay.addWidget(head)

        self._assist_btns = []
        for op in (1, 2, 3):
            btn = QPushButton(_ASSIST_BTN_TEXT[op])
            btn.setObjectName("PasteAssistBtn")
            btn.setFixedHeight(46)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.clicked.connect(lambda _=False, n=op: self._on_clip_hotkey(n))
            self._assist_btns.append(btn)
            lay.addWidget(btn)

        self._restyle_assist_panel()
        self._set_assist_ops_enabled(False)
        return box

    def _on_assist_watch_toggled(self, on: bool):
        if on:
            self._enter_assist_watch()
        else:
            self._leave_assist_watch()

    def _enter_assist_watch(self):
        """打开开关：侧栏粘贴归属关掉，卡片冻住，录入区变成系统剪贴板查看。"""
        if getattr(self, "_assist_watch", False):
            return
        theme_w = getattr(self, "inp_theme", None)
        content_w = getattr(self, "inp_content", None)
        self._assist_watch_snap = {
            "content": content_w.toPlainText() if content_w is not None else "",
            "theme": theme_w.text() if theme_w is not None else "",
            "title": getattr(self, "_entry_title_text", "录入"),
            "tone": getattr(self, "_entry_title_tone", "idle"),
            "theme_mode": getattr(self, "_theme_mode", "draft"),
            "frame": getattr(self, "_entry_frame_color", ""),
            "capture": bool(getattr(self, "_capture_pending", False)),
            "replace": bool(getattr(self, "_replace_armed", False)),
            "editing": getattr(self, "_editing_id", None),
            "selected": getattr(self, "_selected_id", None),
            "copied": bool(getattr(self, "_copied_live", False)),
        }
        self._assist_watch = True
        self._replace_armed = False
        if content_w is not None:
            content_w.setReadOnly(True)
        if theme_w is not None:
            theme_w.setReadOnly(True)
        save = getattr(self, "btn_save", None)
        if save is not None:
            save.setEnabled(False)
        bar = getattr(self, "color_bar", None)
        if bar is not None:
            bar.setEnabled(False)
        self._set_selection_actions_frozen(True)
        self._set_cards_watch_frozen(True)
        self._apply_entry_title_style()
        self._set_assist_ops_enabled(True)
        self._bind_hotkeys()
        mw = self._main_window()
        if mw is not None and hasattr(mw, "set_assist_watch_lock"):
            try:
                mw.set_assist_watch_lock(True)
            except Exception:
                log.exception("开启辅助粘贴时关闭剪贴板归属失败")
        self._show_system_clipboard_now()

    def _leave_assist_watch(self):
        """关掉开关：录入区和卡片回到打开前，侧栏粘贴归属按打开前恢复。"""
        if not getattr(self, "_assist_watch", False):
            return
        snap = getattr(self, "_assist_watch_snap", None) or {}
        self._assist_watch = False
        self._set_assist_ops_enabled(False)
        self._unbind_hotkeys(keep_filter=True)
        theme_w = getattr(self, "inp_theme", None)
        content_w = getattr(self, "inp_content", None)
        if content_w is not None:
            content_w.setReadOnly(False)
        if theme_w is not None:
            theme_w.setReadOnly(False)
        save = getattr(self, "btn_save", None)
        if save is not None:
            save.setEnabled(True)
        bar = getattr(self, "color_bar", None)
        if bar is not None:
            bar.setEnabled(True)
        self._set_cards_watch_frozen(False)
        self._capture_pending = bool(snap.get("capture"))
        self._editing_id = snap.get("editing")
        self._selected_id = snap.get("selected")
        self._copied_live = bool(snap.get("copied"))
        self._entry_title_text = snap.get("title") or "录入"
        self._entry_title_tone = snap.get("tone") or "idle"
        mode = snap.get("theme_mode") or "draft"
        self._set_theme_mode(
            mode,
            color=snap.get("frame") or "",
            theme_text=snap.get("theme") if mode != "clip" else None,
        )
        if mode == "clip" and theme_w is not None:
            theme_w.blockSignals(True)
            try:
                theme_w.setText(snap.get("theme") or "")
            finally:
                theme_w.blockSignals(False)
        self._replace_armed = bool(snap.get("replace"))
        if content_w is not None:
            self._applying_clip = True
            try:
                content_w.setPlainText(snap.get("content") or "")
            finally:
                self._applying_clip = False
        self._apply_entry_title_style()
        self._mark_selected()
        self._sync_save_button()
        has_card = self._editing_id is not None or self._selected_id is not None
        self._set_selection_actions_frozen(not has_card)
        mw = self._main_window()
        if mw is not None and hasattr(mw, "set_assist_watch_lock"):
            try:
                mw.set_assist_watch_lock(False)
            except Exception:
                log.exception("关闭辅助粘贴时恢复剪贴板归属失败")

    def _set_cards_watch_frozen(self, frozen: bool):
        for card in (getattr(self, "_card_widgets", {}) or {}).values():
            try:
                card.set_watch_frozen(frozen)
            except Exception:
                pass

    def _show_system_clipboard_now(self):
        if not getattr(self, "_assist_watch", False):
            return
        try:
            text = _read_clipboard_text()
        except Exception:
            log.exception("读取系统剪贴板失败")
            return
        self._show_system_clipboard(text)

    def _show_system_clipboard(self, text: str):
        """查看模式下内容框只跟系统剪贴板，标题保持「系统剪贴板」。"""
        if not getattr(self, "_assist_watch", False):
            return
        text = text or ""
        self._last_clip_text = text
        edit = getattr(self, "inp_content", None)
        if edit is None:
            return
        self._applying_clip = True
        try:
            if edit.toPlainText() != text:
                edit.setPlainText(text)
        finally:
            self._applying_clip = False
        self._apply_entry_title_style()

    def _set_assist_ops_enabled(self, on: bool):
        """开关关掉时三个操作按钮冻结：变淡、禁止光标、点了无效。"""
        on = bool(on)
        for btn in getattr(self, "_assist_btns", []) or []:
            btn.setEnabled(on)
            btn.setCursor(Qt.ForbiddenCursor if not on else Qt.PointingHandCursor)
            if on:
                btn.setGraphicsEffect(None)
            else:
                effect = QGraphicsOpacityEffect(btn)
                effect.setOpacity(0.4)
                btn.setGraphicsEffect(effect)

    def _restyle_assist_panel(self):
        title = getattr(self, "lbl_assist_title", None)
        mut = "#94a3b8"
        try:
            mut = tk("text_mut") or mut
        except Exception:
            pass
        size = sp(12)
        if title is not None:
            title.setStyleSheet(
                f"QLabel{{background:transparent;color:{mut};"
                f"font-size:{size}px;font-weight:600;}}"
            )
        dark = True
        try:
            dark = bool(theme.is_dark)
        except Exception:
            pass
        if dark:
            bg, border, fg = "rgba(255,255,255,0.06)", "rgba(255,255,255,0.22)", "#cfe0ff"
            hover, pressed = "rgba(255,255,255,0.10)", "rgba(255,255,255,0.14)"
            dis_fg, dis_bg, dis_bd = "rgba(207,224,255,0.38)", "rgba(255,255,255,0.03)", "rgba(255,255,255,0.10)"
        else:
            bg, border, fg = "#f8fafc", "#e2e8f0", "#334155"
            hover, pressed = "rgba(0,0,0,0.06)", "rgba(0,0,0,0.10)"
            dis_fg, dis_bg, dis_bd = "#94a3b8", "#f8fafc", "#e2e8f0"
        qss = (
            f"QPushButton{{background:{bg};border:1px solid {border};color:{fg};"
            f"border-radius:7px;padding:4px 8px;font-size:12px;font-weight:600;"
            f"text-align:left;}}"
            f"QPushButton:hover{{background:{hover};}}"
            f"QPushButton:pressed{{background:{pressed};}}"
            f"QPushButton:disabled{{color:{dis_fg};background:{dis_bg};border-color:{dis_bd};}}"
        )
        for btn in getattr(self, "_assist_btns", []) or []:
            btn.setStyleSheet(qss)
        watch = getattr(self, "btn_assist_watch", None)
        if watch is not None:
            watch.update()

    def _build_warehouse_panel(self) -> QWidget:
        box = make_card("CardPasteWarehouse")

        outer = QVBoxLayout(box)
        outer.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        # spacing 与标题空隙由 install_card_title 补偿；正文行距用 spacing=8
        outer.setSpacing(8)
        install_card_title(box, outer, "仓库")

        # 工具条：左 50% 七色筛选 | 右 50% 搜索 + 取消 + 计数
        tool = QHBoxLayout()
        tool.setContentsMargins(0, 0, 0, 0)
        tool.setSpacing(12)

        left_tool = _transparent_bg(QWidget())
        left_l = QHBoxLayout(left_tool)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(0)
        self.color_filter = ColorFilterBar(on_change=self._on_color_filter)
        left_l.addWidget(self.color_filter, 1)

        right_tool = _transparent_bg(QWidget())
        right_l = QHBoxLayout(right_tool)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(8)

        self.inp_search = QLineEdit()
        self.inp_search.setObjectName("CalcInput")
        self.inp_search.setPlaceholderText("搜索")
        self.inp_search.setFixedHeight(30)
        self.inp_search.textChanged.connect(self._on_search_changed)
        right_l.addWidget(self.inp_search, 1)

        # 有搜索内容时才显示；高度与搜索框一致，不超出
        self.btn_clear_search = QPushButton("取消")
        self.btn_clear_search.setObjectName("SearchClearBtn")
        self.btn_clear_search.setFixedHeight(30)
        self.btn_clear_search.setMinimumWidth(72)
        self.btn_clear_search.setCursor(Qt.PointingHandCursor)
        self.btn_clear_search.clicked.connect(self._clear_search)
        self.btn_clear_search.setVisible(False)
        right_l.addWidget(self.btn_clear_search)

        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("RecordDate")
        right_l.addWidget(self.lbl_count, 0)

        tool.addWidget(left_tool, 50)
        tool.addWidget(right_tool, 50)
        outer.addLayout(tool)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("RecordsScroll")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # 滚动区自己吃掉内容高度，不把内容最小高度上报给窗口
        scroll.setMinimumHeight(0)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        try:
            scroll.setSizeAdjustPolicy(QAbstractScrollArea.AdjustIgnored)
        except Exception:
            pass
        # 视口默认用调色板 Base 色 / 全局 QWidget 画布色，会与 RecordsBox 功能区色不一致
        scroll.viewport().setAutoFillBackground(False)
        scroll.setStyleSheet(
            "QScrollArea#RecordsScroll{background:transparent;border:none;}"
            "QScrollArea#RecordsScroll > QWidget > QWidget{background:transparent;}"
        )

        self.cards_host = CardsDropHost(self)
        self.cards_layout = FlowLayout(self.cards_host, margin=4, h_spacing=4, v_spacing=4)
        scroll.setWidget(self.cards_host)
        self._warehouse_scroll = scroll
        outer.addWidget(scroll, 1)

        # 仓库卡片区可压缩，录入区保持内容高度
        box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        return box

    # ── 刷新 ─────────────────────────────────────────────────────────────────
    def _reload(self):
        self._all_records = _load_records()
        self._refresh_cards()

    def _on_color_filter(self, colors):
        """colors: 选中的颜色 hex 列表，空列表表示不筛选。"""
        self._filter_colors = {(c or "").lower() for c in (colors or []) if c}
        self._refresh_cards()

    def _on_search_changed(self, text):
        has = bool((text or "").strip())
        self.btn_clear_search.setVisible(has)
        self._refresh_cards()

    def _clear_search(self):
        self.inp_search.clear()
        # textChanged 会触发隐藏取消按钮并刷新

    def _refresh_cards(self):
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._card_widgets = {}

        kw = (self.inp_search.text() or "").strip().lower()
        recs = _sorted_by_order(self._all_records)

        # 颜色多选筛选：卡片颜色属于任一勾选色即显示
        if self._filter_colors:
            recs = [
                r for r in recs
                if (r.get("color") or "").lower() in self._filter_colors
            ]

        if kw:
            recs = [
                r for r in recs
                if kw in (r.get("theme") or r.get("title") or "").lower()
                or kw in (r.get("content") or "").lower()
            ]

        self.lbl_count.setText(f"共 {len(recs)} 条")

        if not recs:
            if not self._all_records:
                empty_msg = "仓库为空"
            else:
                empty_msg = "没有匹配"
            empty = QLabel(empty_msg)
            empty.setObjectName("RecordEmpty")
            empty.setAlignment(Qt.AlignCenter)
            empty.setMinimumHeight(120)
            self.cards_layout.addWidget(empty)
            return

        for record in recs:
            card = ThemeCard(record, page=self, parent=self.cards_host)
            if self._selected_id is not None and str(record.get("id")) == str(self._selected_id):
                card.set_selected(True)
            if getattr(self, "_assist_watch", False):
                card.set_watch_frozen(True)
            self.cards_layout.addWidget(card)
            self._card_widgets[str(record.get("id"))] = card
            # 新建卡片也挂上空白点击过滤器（卡片本身会被排除，不影响选中）
            card.installEventFilter(self)
            for child in card.findChildren(QWidget):
                child.installEventFilter(self)

    def _mark_selected(self):
        sid = str(self._selected_id) if self._selected_id is not None else None
        for rid, card in self._card_widgets.items():
            card.set_selected(sid is not None and rid == sid)

    def _active_card_id(self):
        """当前业务目标卡：优先编辑中的，否则仓库选中的。"""
        if self._editing_id is not None:
            return self._editing_id
        return self._selected_id

    # ── 空白点击 = 取消选中 ───────────────────────────────────────────────────
    def _install_blank_click_filter(self):
        """子控件点击冒泡检测：点到非交互空白处时取消选中。"""
        self.installEventFilter(self)
        for w in self.findChildren(QWidget):
            w.installEventFilter(self)

    def _press_hits_card(self) -> bool:
        """光标是否在某张内容卡上。卡内标题不吃鼠标时，事件可能冒到外层，不能据此取消替换。"""
        try:
            w = QApplication.widgetAt(QCursor.pos())
        except Exception:
            return False
        while w is not None:
            if isinstance(w, ThemeCard):
                return True
            w = w.parentWidget()
        return False

    def eventFilter(self, obj, event):
        if (
            not getattr(self, "_assist_watch", False)
            and event.type() == QEvent.MouseButtonPress
            and event.button() == Qt.LeftButton
            and (self._selected_id or self._editing_id or self._replace_armed)
            and not self._press_hits_card()
            and self._is_blank_deselect_target(obj)
        ):
            self._cancel_edit()
            return False
        return super().eventFilter(obj, event)

    def _is_blank_deselect_target(self, obj) -> bool:
        """判断这次点击是否落在“空白区”（非输入、非按钮、非卡片本体）。"""
        w = obj
        while w is not None:
            if isinstance(w, ThemeCard):
                return False
            if w in (
                self.btn_save, self.btn_copy, self.btn_delete,
                self.btn_clear_search,
                self.inp_theme, self.inp_content, self.inp_search,
            ):
                return False
            if isinstance(w, (QLineEdit, QTextEdit)):
                return False
            # 颜色菱形/圆点、以及其它功能按钮
            if isinstance(w, (QPushButton, QAbstractButton)):
                return False
            if w is self:
                break
            w = w.parentWidget()
        return True

    # ── 业务 ─────────────────────────────────────────────────────────────────
    def _show_hint(self, text: str):
        """标题一行：绿成功 / 黄提示 / 红问题。"""
        t = (text or "").strip()
        if not t:
            return
        if not t.endswith("。") and not t.endswith("."):
            t = t + "。"
        self._set_entry_status(t, self._hint_tone(t))

    @staticmethod
    def _hint_tone(text: str) -> str:
        t = text or ""
        if t.startswith("已"):
            return "ok"
        err_keys = (
            "不能为空", "没有选中", "不在复制", "是空的",
            "失败", "找不到", "内容为空",
        )
        if any(k in t for k in err_keys):
            return "err"
        return "warn"

    def _main_window(self):
        w = self.window()
        if w is not None and hasattr(w, "_detect_pre_download_platform"):
            return w
        app = QApplication.instance()
        if app is None:
            return None
        try:
            for tl in app.topLevelWidgets():
                if hasattr(tl, "_detect_pre_download_platform"):
                    return tl
        except Exception:
            return None
        return None

    def _whitelist_plat_name(self, text: str) -> str:
        """命中预下载白名单时返回平台显示名（抖音 / B站 …），未命中空串。"""
        raw = text or ""
        if "https://" not in raw.lower():
            return ""
        mw = self._main_window()
        detect = getattr(mw, "_detect_pre_download_platform", None) if mw is not None else None
        if not callable(detect):
            return ""
        try:
            plat = detect(raw) or ""
        except Exception:
            log.exception("粘贴助手白名单判定失败")
            return ""
        if not plat:
            return ""
        return _PLAT_HINT_NAME.get(plat, plat)

    def _status_with_plat(self, action: str, text: str) -> str:
        a = (action or "").rstrip("。.") + "。"
        plat = self._whitelist_plat_name(text)
        if plat:
            return f"{a}命中{plat}白名单。"
        return a

    def _set_entry_status(self, text: str, tone: str = "idle"):
        if getattr(self, "_assist_watch", False):
            self._apply_entry_title_style()
            return
        self._entry_title_text = text or "录入"
        self._entry_title_tone = tone or "idle"
        self._apply_entry_title_style()

    def _apply_entry_title_style(self):
        lbl = getattr(self, "lbl_entry_title", None)
        if lbl is None:
            return
        if getattr(self, "_assist_watch", False):
            lbl.setText("系统剪贴板")
            restyle_card_title(lbl)
            return
        tone = getattr(self, "_entry_title_tone", "idle")
        text = getattr(self, "_entry_title_text", None) or "录入"
        if tone == "idle":
            lbl.setText("录入")
            restyle_card_title(lbl)
            return
        if tone in ("copied", "ok"):
            color = _COPIED_TITLE_COLOR
        elif tone == "err":
            color = _ERR_TITLE_COLOR
        else:
            color = _WARN_TITLE_COLOR
        size = sp(CARD_TITLE_FONT_SIZE)
        weight = CARD_TITLE_FONT_WEIGHT
        lbl.setText(text)
        lbl.setStyleSheet(
            f"QLabel{{background:transparent; color:{color}; "
            f"font-size:{size}px; font-weight:{weight};}}"
        )

    def _set_entry_copied(self, copied: bool, plat: str = ""):
        """标题：已复制 / 已复制。命中某平台白名单。"""
        self._copied_live = bool(copied)
        if copied:
            head = f"已复制。命中{plat}白名单。" if plat else "已复制。"
            self._set_entry_status(head + _PASTE_SAVE_HINT, "ok")
        else:
            self._set_entry_status("录入", "idle")

    def _set_theme_mode(self, mode: str, color: str = "", theme_text=None):
        """clip=外部复制（主题可写、先留空）；card=点了卡片（外框用卡片色）；draft=可填主题。"""
        inp = getattr(self, "inp_theme", None)
        if inp is None:
            return
        self._theme_mode = mode
        if mode == "card":
            inp.setReadOnly(False)
            inp.setPlaceholderText("主题")
            inp.setCursor(Qt.IBeamCursor)
            if theme_text is not None:
                inp.setText(theme_text)
            self._apply_entry_frame(color or DEFAULT_COLOR)
            return
        if mode == "clip":
            inp.setReadOnly(False)
            inp.setPlaceholderText("主题")
            inp.setCursor(Qt.IBeamCursor)
            inp.blockSignals(True)
            inp.clear()
            inp.blockSignals(False)
            self._apply_entry_frame("")
            return
        inp.setReadOnly(False)
        inp.setPlaceholderText("主题")
        inp.setCursor(Qt.IBeamCursor)
        if theme_text is not None:
            inp.setText(theme_text)
        elif mode == "draft":
            inp.clear()
        self._apply_entry_frame("")

    def _apply_entry_frame(self, color: str = ""):
        """点卡片时把主题/内容外框改成卡片色；复制/空白则恢复默认。"""
        self._entry_frame_color = (color or "").strip()
        self._refresh_entry_frame()

    def _refresh_entry_frame(self):
        theme_w = getattr(self, "inp_theme", None)
        content_w = getattr(self, "inp_content", None)
        if theme_w is None:
            return
        color = getattr(self, "_entry_frame_color", "") or ""
        if not color:
            theme_w.setStyleSheet("")
            if content_w is not None:
                content_w.setStyleSheet("")
            return
        is_dark = True
        try:
            is_dark = bool(theme.is_dark)
        except Exception:
            pass
        bg = "#0e1530" if is_dark else "#ffffff"
        fg = "#e8eaf0" if is_dark else "#1f2937"
        # 与 app.qss 里 pasteEntry 一致：14px，四边内边距 4px。
        # 选中卡片只改边框颜色，粗细仍是默认 1px。
        common = (
            f"background:{bg};border:1px solid {color};border-radius:6px;"
            f"padding:4px;color:{fg};font-size:14px;"
        )
        theme_w.setStyleSheet(
            f"QLineEdit#PasteThemeInput{{{common}}}"
            f"QLineEdit#PasteThemeInput:focus{{{common}}}"
            f"QLineEdit#PasteThemeInput:read-only{{{common}}}"
        )
        if content_w is None:
            return
        content_w.setStyleSheet(
            f"QTextEdit#PasteContentEdit{{{common}}}"
            f"QTextEdit#PasteContentEdit:focus{{{common}}}"
            "QTextEdit#PasteContentEdit QScrollBar:vertical{"
            "background:transparent;width:6px;margin:2px 0;}"
            "QTextEdit#PasteContentEdit QScrollBar::handle:vertical{"
            "background:#f0a542;border-radius:3px;min-height:28px;}"
            "QTextEdit#PasteContentEdit QScrollBar::handle:vertical:hover{"
            "background:#f7bb64;}"
            "QTextEdit#PasteContentEdit QScrollBar::add-line:vertical,"
            "QTextEdit#PasteContentEdit QScrollBar::sub-line:vertical{"
            "height:0;background:none;border:none;}"
            "QTextEdit#PasteContentEdit QScrollBar::add-page:vertical,"
            "QTextEdit#PasteContentEdit QScrollBar::sub-page:vertical{"
            "background:none;}"
        )

    def restyle_theme(self, *_args):
        self._apply_entry_title_style()
        self._restyle_assist_panel()
        self._refresh_entry_frame()
        bar = getattr(self, "color_bar", None)
        if bar is not None:
            try:
                for b in bar._group.buttons():
                    b.update()
            except Exception:
                pass
        filt = getattr(self, "color_filter", None)
        if filt is not None:
            try:
                for b in getattr(filt, "_swatches", []) or []:
                    b.update()
            except Exception:
                pass

    def set_clipboard_capture_enabled(self, enabled: bool, *, sync_now: bool = True):
        """侧栏剪贴板归属：仅归属本页时消费系统剪贴板变化。"""
        enabled = bool(enabled)
        prev = bool(getattr(self, "_clipboard_capture_enabled", False))
        self._clipboard_capture_enabled = enabled
        if enabled and (not prev) and sync_now:
            # 刚获得归属：立刻同步一次当前剪贴板
            QTimer.singleShot(0, self._sync_clipboard_now)
        if not enabled:
            timer = getattr(self, "_clip_timer", None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    pass

    def _bind_clipboard_watch(self):
        if self._clip_watch_connected:
            return
        try:
            cb = QApplication.clipboard()
            if cb is None:
                return
            cb.dataChanged.connect(self._on_clipboard_changed)
            self._clip_watch_connected = True
        except Exception:
            log.exception("粘贴助手接入剪贴板监听失败")
            return
        self._clip_timer = QTimer(self)
        self._clip_timer.setSingleShot(True)
        self._clip_timer.setInterval(60)
        self._clip_timer.timeout.connect(self._on_clipboard_settled)

    def _on_clipboard_changed(self):
        watching = bool(getattr(self, "_assist_watch", False))
        if not watching and not getattr(self, "_clipboard_capture_enabled", False):
            return
        if self._ignore_clip > 0 or self._clip_op_busy:
            return
        timer = getattr(self, "_clip_timer", None)
        if timer is not None:
            timer.start()

    def _sync_clipboard_now(self):
        if getattr(self, "_assist_watch", False):
            self._show_system_clipboard_now()
            return
        if not getattr(self, "_clipboard_capture_enabled", False):
            return
        if self._ignore_clip > 0 or self._clip_op_busy:
            return
        self._on_clipboard_settled()

    def _on_clipboard_settled(self):
        if getattr(self, "_assist_watch", False):
            if self._ignore_clip > 0 or self._clip_op_busy:
                return
            self._show_system_clipboard_now()
            return
        if not getattr(self, "_clipboard_capture_enabled", False):
            return
        if self._ignore_clip > 0 or self._clip_op_busy:
            return
        text = ""
        try:
            text = _read_clipboard_text()
        except Exception:
            return
        self._apply_clipboard_text(text)

    def ingest_external_text(self, text: str) -> bool:
        """主窗 F7 推送：直接灌入录入区，不依赖临时剪贴板变化。"""
        text = text or ""
        if not str(text).strip():
            self._show_hint("没有选中文本")
            return False
        try:
            self._apply_clipboard_text(text)
            self._show_hint("已推送到粘贴助手")
            return True
        except Exception:
            log.exception("粘贴助手填入选区失败")
            self._show_hint("填入选区失败")
            return False

    def begin_clip_ignore(self):
        self._ignore_clip = int(getattr(self, "_ignore_clip", 0) or 0) + 1

    def end_clip_ignore(self):
        self._ignore_clip = max(0, int(getattr(self, "_ignore_clip", 0) or 0) - 1)

    def _apply_clipboard_text(self, text: str):
        if getattr(self, "_assist_watch", False):
            self._show_system_clipboard(text)
            return
        text = text or ""
        self._last_clip_text = text
        if not text.strip():
            # 复制的是图/文件等非文本：不冲掉录入框，只把标题收回
            if self._copied_live:
                self._set_entry_copied(False)
            return
        focused = False
        try:
            focused = bool(self.inp_content.hasFocus())
        except Exception:
            focused = False
        try:
            current = self.inp_content.toPlainText()
        except Exception:
            current = ""
        # 正在改内容框时，不拿新的外部复制覆盖
        if focused and current != text:
            return
        # 同一段文字又报了一次：留着用户已写的主题、已点的替换卡
        if current == text and (self._capture_pending or self._editing_id is not None):
            return
        if current != text:
            self._applying_clip = True
            try:
                self.inp_content.setPlainText(text)
            finally:
                self._applying_clip = False
        # 新的外部复制落地：退出卡片编辑，主题留空但可以写
        self._capture_pending = True
        self._replace_armed = False
        self._editing_id = None
        self._selected_id = None
        self._set_selection_actions_frozen(True)
        self._mark_selected()
        try:
            self.color_bar.set_color(DEFAULT_COLOR)
        except Exception:
            pass
        self._set_theme_mode("clip")
        self._sync_save_button()
        self._set_entry_copied(True, plat=self._whitelist_plat_name(text))

    def _on_theme_edited(self):
        if self._applying_clip:
            return
        if self._replace_armed and self.inp_theme.text().strip():
            self._replace_armed = False
        self._sync_save_button()

    def _sync_save_button(self):
        """有内容且没主题 → 替换内容；点过之后 → 替换中……；写了主题 → 保存；正在改卡 → 更新。"""
        btn = getattr(self, "btn_save", None)
        if btn is None:
            return
        if self._replace_armed:
            btn.setText("替换中……")
            return
        theme = ""
        content = ""
        try:
            theme = self.inp_theme.text().strip()
        except Exception:
            theme = ""
        try:
            content = self.inp_content.toPlainText().strip()
        except Exception:
            content = ""
        if self._editing_id is not None and theme:
            btn.setText("更新")
            return
        if content and not theme:
            btn.setText("替换内容")
            return
        if self._editing_id is not None:
            btn.setText("更新")
            return
        btn.setText("保存")

    def _on_content_edited(self):
        if self._applying_clip:
            return
        if self._replace_armed and not self.inp_content.toPlainText().strip():
            self._replace_armed = False
        self._sync_save_button()
        if not self._capture_pending and not self._copied_live:
            return
        if self._replace_armed:
            return
        cur = self.inp_content.toPlainText()
        if cur == (self._last_clip_text or ""):
            return
        if self._copied_live:
            self._copied_live = False
            self._set_entry_status("正在编辑。" + _PASTE_SAVE_HINT, "warn")

    def _set_clipboard_text(self, text: str):
        """写入剪贴板；短暂忽略 dataChanged，写完再自己刷录入区。"""
        self._ignore_clip += 1
        try:
            cb = QApplication.clipboard()
            if cb is not None:
                cb.setText(text or "")
        except Exception:
            log.exception("粘贴助手写剪贴板失败")
        finally:
            QTimer.singleShot(80, self._release_ignore_clip)
        self._apply_clipboard_text(text or "")

    def _release_ignore_clip(self):
        self._ignore_clip = max(0, int(self._ignore_clip) - 1)

    # ── 全局热键 Ctrl+Alt+1/2/3 ───────────────────────────────────────────
    def _bind_hotkeys(self, _attempt: int = 0):
        if not getattr(self, "_assist_watch", False):
            self._unbind_hotkeys(keep_filter=True)
            return False
        if not sys.platform.startswith("win"):
            self._hotkeys_ok = False
            return False
        self._unbind_hotkeys(keep_filter=True)
        mods = _PASTE_HK_MODS
        bound = []
        all_ok = True
        for hid, vk, _op in _PASTE_HK_SPEC:
            ok, err = _win_register_hotkey(hid, vk, mods)
            if ok:
                bound.append(hid)
                continue
            all_ok = False
            log.warning("粘贴助手热键注册失败 id=0x%X winerr=%s", hid, err)
        self._hotkey_bound_ids = tuple(bound)
        self._hotkeys_ok = bool(bound) and all_ok
        if bound and self._hotkey_filter is None:
            self._hotkey_bridge = _PasteHotkeyBridge(self)
            self._hotkey_bridge.fired.connect(self._on_clip_hotkey)
            hid_to_op = {hid: op for hid, _vk, op in _PASTE_HK_SPEC}
            self._hotkey_filter = _PasteHotkeyFilter(hid_to_op, self._hotkey_bridge)
            app = QCoreApplication.instance()
            if app is not None:
                app.installNativeEventFilter(self._hotkey_filter)
        if not all_ok and _attempt < len(_HK_RETRY_MS):
            delay = int(_HK_RETRY_MS[_attempt])
            QTimer.singleShot(delay, lambda a=_attempt + 1: self._bind_hotkeys(a))
        elif bound:
            log.info("粘贴助手热键就绪 Ctrl+Alt+1/2/3 n=%s", len(bound))
        return self._hotkeys_ok

    def _unbind_hotkeys(self, keep_filter: bool = False):
        for hid in (_PASTE_HK_ADD, _PASTE_HK_SUB, _PASTE_HK_SWAP):
            _win_unregister_hotkey(hid)
        self._hotkey_bound_ids = ()
        self._hotkeys_ok = False
        if keep_filter:
            return
        # 过滤器可留：Qt 没有廉价的对称卸载，重绑时复用

    def hotkey_inventory(self):
        if not getattr(self, "_assist_watch", False):
            st = "off"
        else:
            st = "ok" if self._hotkeys_ok else "busy"
        return [
            {"group": "粘贴助手", "name": "选中加入复制", "key": _PASTE_HK_KEY_LABELS[1], "state": st},
            {"group": "粘贴助手", "name": "从复制减去选中", "key": _PASTE_HK_KEY_LABELS[2], "state": st},
            {"group": "粘贴助手", "name": "复制与选中互换", "key": _PASTE_HK_KEY_LABELS[3], "state": st},
        ]

    def rebind_hotkeys(self):
        if not getattr(self, "_assist_watch", False):
            self._unbind_hotkeys(keep_filter=True)
            ok = False
        else:
            ok = bool(self._bind_hotkeys())
        return [
            ("选中加入复制", _PASTE_HK_KEY_LABELS[1], ok),
            ("从复制减去选中", _PASTE_HK_KEY_LABELS[2], ok),
            ("复制与选中互换", _PASTE_HK_KEY_LABELS[3], ok),
        ]

    def shutdown(self):
        self._clip_op_busy = False
        self._clip_op = None
        self._unbind_hotkeys()
        timer = getattr(self, "_clip_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        if self._clip_watch_connected:
            try:
                cb = QApplication.clipboard()
                if cb is not None:
                    cb.dataChanged.disconnect(self._on_clipboard_changed)
            except Exception:
                pass
            self._clip_watch_connected = False

    def _on_clip_hotkey(self, op: int):
        if not getattr(self, "_assist_watch", False):
            return
        if self._clip_op_busy:
            return
        op = int(op or 0)
        if op not in (1, 2, 3):
            return
        self._begin_clip_op(op)

    def _form_selected_text(self) -> str:
        """录入区里的选中文本（点按钮时焦点在按钮上，不能只看 focusWidget）。"""
        for w in (getattr(self, "inp_content", None), getattr(self, "inp_theme", None)):
            if w is None:
                continue
            try:
                if isinstance(w, QTextEdit):
                    t = _normalize_sel_text(w.textCursor().selectedText() or "")
                elif isinstance(w, QLineEdit):
                    t = _normalize_sel_text(w.selectedText() or "")
                else:
                    continue
                if t:
                    return t
            except Exception:
                continue
        return _local_selected_text()

    def _replace_form_selection(self, text: str) -> bool:
        for w in (getattr(self, "inp_content", None), getattr(self, "inp_theme", None)):
            if w is None:
                continue
            try:
                if isinstance(w, QTextEdit):
                    cur = w.textCursor()
                    if cur.hasSelection():
                        cur.insertText(text or "")
                        return True
                elif isinstance(w, QLineEdit) and w.hasSelectedText():
                    w.insert(text or "")
                    return True
            except Exception:
                continue
        return _replace_local_selection(text)

    def _begin_clip_op(self, op: int):
        """Ctrl+Alt+1/2/3：取当前选中文本 B，与剪贴板 A 做加入/减去/互换。"""
        self._clip_op_busy = True
        a = ""
        saved = QMimeData()
        try:
            a = _read_clipboard_text()
            cb = QApplication.clipboard()
            if cb is not None:
                saved = _clone_mime(cb.mimeData())
        except Exception:
            pass
        self._clip_op = {
            "op": op,
            "a": a,
            "saved": saved,
            "seq": _clip_seq(),
        }
        # 本进程前台且控件里有选区：不必模拟 Ctrl+C
        try:
            fg = _USER32.GetForegroundWindow() if _USER32 is not None else 0
        except Exception:
            fg = 0
        if _hwnd_is_self(fg):
            local = self._form_selected_text()
            if local:
                self._finish_clip_op(local)
                return
        QTimer.singleShot(20, self._clip_op_capture)

    def _clip_op_capture(self):
        if not self._clip_op_busy:
            return
        _unstick_modifiers()
        _send_wm(_WM_COPY)
        QTimer.singleShot(70, self._clip_op_after_wm_copy)

    def _clip_op_after_wm_copy(self):
        if not self._clip_op_busy:
            return
        ctx = getattr(self, "_clip_op", None) or {}
        if _clip_seq() != int(ctx.get("seq") or 0):
            self._clip_op_read_b()
            return
        _unstick_modifiers()
        _send_ctrl(_VK_C)
        QTimer.singleShot(90, lambda: self._clip_op_read_b(False))

    def _clip_op_read_b(self, extra=False):
        if not self._clip_op_busy:
            return
        ctx = getattr(self, "_clip_op", None) or {}
        if not ctx:
            return
        b = ""
        try:
            b = _normalize_sel_text(_read_clipboard_text())
        except Exception:
            b = ""
        seq_now = _clip_seq()
        seq0 = int(ctx.get("seq") or 0)
        # 序列号没变：这次 Ctrl+C / WM_COPY 没写进剪贴板
        if seq_now == seq0:
            if not extra:
                QTimer.singleShot(120, lambda: self._clip_op_read_b(True))
                return
            self._clip_op_abort("没有选中文本")
            return
        if not (b or "").strip():
            self._clip_op_abort("没有选中文本")
            return
        self._finish_clip_op(b)

    def _clip_op_restore_saved(self):
        ctx = getattr(self, "_clip_op", None) or {}
        saved = ctx.get("saved")
        try:
            cb = QApplication.clipboard()
            if cb is not None and isinstance(saved, QMimeData):
                cb.setMimeData(_clone_mime(saved))
        except Exception:
            a = ctx.get("a")
            if a:
                try:
                    QApplication.clipboard().setText(a)
                except Exception:
                    pass

    def _clip_op_abort(self, hint: str):
        self._ignore_clip += 1
        self._clip_op_restore_saved()
        self._clip_op_busy = False
        self._clip_op = None
        QTimer.singleShot(80, self._release_ignore_clip)
        if hint:
            self._show_hint(hint)
            QTimer.singleShot(1400, self._restore_hint_after_op)

    def _restore_hint_after_op(self):
        text = self._last_clip_text or ""
        if text.strip():
            self._set_entry_copied(True, plat=self._whitelist_plat_name(text))
        else:
            self._set_entry_copied(False)

    def _finish_clip_op(self, b: str):
        ctx = getattr(self, "_clip_op", None) or {}
        op = int(ctx.get("op") or 0)
        a = ctx.get("a") or ""
        b = _normalize_sel_text(b or "")
        if not b:
            self._clip_op_abort("没有选中文本")
            return
        if op == 1:
            new = _clip_add(a, b)
            self._clip_op_busy = False
            self._clip_op = None
            self._set_clipboard_text(new)
            self._show_hint(self._status_with_plat("已加入选中文本", new))
            QTimer.singleShot(1600, self._restore_hint_after_op)
            return
        if op == 2:
            if not a:
                self._clip_op_abort("复制是空的")
                return
            new, found = _clip_sub(a, b)
            if not found:
                self._clip_op_abort("选中内容不在复制里")
                return
            self._clip_op_busy = False
            self._clip_op = None
            self._set_clipboard_text(new)
            self._show_hint(self._status_with_plat("已从复制中减去选中文本", new))
            QTimer.singleShot(1600, self._restore_hint_after_op)
            return
        if op == 3:
            self._clip_op_do_swap(a, b)
            return
        self._clip_op_abort("")

    def _clip_op_do_swap(self, a: str, b: str):
        """选区换成 A，剪贴板换成 B。"""
        try:
            fg = _USER32.GetForegroundWindow() if _USER32 is not None else 0
        except Exception:
            fg = 0
        if _hwnd_is_self(fg) and self._replace_form_selection(a):
            self._clip_op_busy = False
            self._clip_op = None
            self._set_clipboard_text(b)
            self._show_hint(self._status_with_plat("已互换复制与选中文本", b))
            QTimer.singleShot(1600, self._restore_hint_after_op)
            return
        # 外进程：剪贴板先放 A，Ctrl+V 覆盖选区，再把剪贴板改成 B
        self._swap_b = b
        try:
            cb = QApplication.clipboard()
            if cb is not None:
                cb.setText(a or "")
        except Exception:
            log.exception("互换：写入 A 失败")
            self._clip_op_abort("互换失败")
            return
        QTimer.singleShot(40, self._clip_op_swap_ctrl_v)

    def _clip_op_swap_ctrl_v(self):
        if not self._clip_op_busy:
            return
        # 只走 Ctrl+V：WM_PASTE 在原生编辑框也会成功，再 Ctrl+V 会贴两次
        _unstick_modifiers()
        _send_ctrl(_VK_V)
        QTimer.singleShot(70, self._clip_op_swap_finish)

    def _clip_op_swap_finish(self):
        if not self._clip_op_busy:
            return
        b = getattr(self, "_swap_b", None)
        self._swap_b = None
        self._clip_op_busy = False
        self._clip_op = None
        self._set_clipboard_text(b or "")
        self._show_hint(self._status_with_plat("已互换复制与选中文本", b or ""))
        QTimer.singleShot(1600, self._restore_hint_after_op)

    def _action_icon_colors(self, frozen: bool):
        if frozen:
            try:
                c = tk("text_faint") or "#64748B"
            except Exception:
                c = "#64748B"
            return c, c
        try:
            copy_c = tk("text_mut") or "#9fb0d7"
        except Exception:
            copy_c = "#9fb0d7"
        return copy_c, "#f87171"

    def _set_selection_actions_frozen(self, frozen: bool):
        """未选卡片：复制/删除保持可见但灰化冻结；选中后解冻。"""
        frozen = bool(frozen)
        self._actions_frozen = frozen
        if hasattr(self, "edit_icons"):
            self.edit_icons.setVisible(True)
        specs = (
            (self.btn_copy, "先点一张卡片后再复制", "复制为新卡片"),
            (self.btn_delete, "先点一张卡片后再删除", "删除此卡片"),
        )
        for btn, tip_frozen, tip_on in specs:
            btn.setVisible(True)
            btn.setEnabled(not frozen)
            btn.setCursor(Qt.ForbiddenCursor if frozen else Qt.PointingHandCursor)
            btn.setToolTip(tip_frozen if frozen else tip_on)
            try:
                st = btn.style()
                if st is not None:
                    st.unpolish(btn)
                    st.polish(btn)
            except Exception:
                pass
        copy_c, del_c = self._action_icon_colors(frozen)
        self.btn_copy.setIcon(_clipboard_icon(_ICON_GLYPH, copy_c))
        self.btn_delete.setIcon(_trash_icon(_ICON_GLYPH, del_c))

    def _clear_form(self):
        self._selected_id = None
        self._editing_id = None
        self._replace_armed = False
        self._capture_pending = False
        self.inp_theme.clear()
        self.inp_content.clear()
        self.color_bar.set_color(DEFAULT_COLOR)
        self.btn_save.setText("保存")
        self._set_selection_actions_frozen(True)
        self._set_theme_mode("draft")
        self._set_entry_copied(False)
        self._mark_selected()

    def _bump_use_count(self, record_id):
        records = _load_records()
        for r in records:
            if r.get("id") == record_id:
                r["use_count"] = int(r.get("use_count") or 0) + 1
                break
        _save_records(records)
        self._all_records = records

    def _click_card(self, record):
        """单击卡片：复制内容 + 选中 + 进入主题/内容编辑。

        「替换中……」时：不打开这张卡，只把它的内容换成当前内容框里的文字。
        """
        if getattr(self, "_assist_watch", False):
            return
        if self._replace_armed:
            self._commit_replace(record)
            return
        content = record.get("content") or ""
        self._ignore_clip += 1
        try:
            QApplication.clipboard().setText(content)
        except Exception:
            pass
        QTimer.singleShot(80, self._release_ignore_clip)
        self._bump_use_count(record.get("id"))

        rid = record.get("id")
        # 已在编辑同一张：只复制+提示，不重刷表单（避免抖动）
        if self._editing_id is not None and str(self._editing_id) == str(rid):
            self._selected_id = rid
            self._set_selection_actions_frozen(False)
            self._mark_selected()
            self._set_entry_copied(True, plat=self._whitelist_plat_name(content))
            return

        self._enter_edit(record, copied=True)

    def _enter_edit(self, record, copied: bool = False):
        self._capture_pending = False
        self._replace_armed = False
        same = (
            self._editing_id is not None
            and str(self._editing_id) == str(record.get("id"))
        )
        self._selected_id = record.get("id")
        self._editing_id = record.get("id")
        theme = record.get("theme") or record.get("title") or ""
        # 同卡重复进入时不要反复 setText/setPlainText（会重置光标并触发布局）
        if not same:
            self._applying_clip = True
            try:
                self.inp_content.setPlainText(record.get("content") or "")
            finally:
                self._applying_clip = False
            self.color_bar.set_color(record.get("color") or DEFAULT_COLOR)
        self._last_clip_text = record.get("content") or ""
        self._set_theme_mode(
            "card",
            color=record.get("color") or DEFAULT_COLOR,
            theme_text=theme if not same else None,
        )
        self.btn_save.setText("更新")
        self._set_selection_actions_frozen(False)
        self._mark_selected()
        if copied:
            self._set_entry_copied(
                True, plat=self._whitelist_plat_name(record.get("content") or "")
            )
        else:
            self._set_entry_status("已选中卡片，改完后点更新。", "warn")
        # 点卡：只选中并填入数据，不进入主题/内容文本编辑（不抢光标、不闪烁插入符）
        # 用户若要改字，再自己点对应输入框即可
        self.inp_theme.clearFocus()
        self.inp_content.clearFocus()
        self.setFocus(Qt.OtherFocusReason)

    def _on_save_clicked(self):
        """保存 / 替换内容。替换要先点按钮，再点下面的卡。"""
        if getattr(self, "_assist_watch", False):
            return
        if self._replace_armed:
            self._replace_armed = False
            self._sync_save_button()
            self._set_entry_status("已取消替换。", "warn")
            return
        theme = ""
        content = ""
        try:
            theme = self.inp_theme.text().strip()
            content = self.inp_content.toPlainText().strip()
        except Exception:
            theme = ""
            content = ""
        if content and not theme:
            self._replace_armed = True
            self._editing_id = None
            self._selected_id = None
            self._set_selection_actions_frozen(True)
            self._mark_selected()
            self._sync_save_button()
            self._set_entry_status("点下面一张已有的内容卡，替换它的内容。", "warn")
            return
        self._save()

    def _commit_replace(self, record):
        """替换中：把当前内容写入这张已有卡，不打开它。"""
        content = ""
        try:
            content = self.inp_content.toPlainText().strip()
        except Exception:
            content = ""
        if not content:
            self._replace_armed = False
            self._sync_save_button()
            self._show_hint("内容不能为空")
            return
        records = _sorted_by_order(_load_records())
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        found = None
        for r in records:
            if str(r.get("id")) == str(record.get("id")):
                r["content"] = content
                r["date"] = now
                found = r
                break
        if found is None:
            self._replace_armed = False
            self._sync_save_button()
            self._show_hint("找不到要替换的卡片")
            return
        name = (found.get("theme") or found.get("title") or "此卡片").strip() or "此卡片"
        self._replace_armed = False
        self._after_record_write(records, f"已替换「{name}」")

    def _after_record_write(self, records, hint: str):
        _save_records(records)
        self._selected_id = None
        self._editing_id = None
        self._replace_armed = False
        self._capture_pending = False
        self.inp_theme.blockSignals(True)
        self.inp_theme.clear()
        self.inp_theme.blockSignals(False)
        self.inp_content.clear()
        self.color_bar.set_color(DEFAULT_COLOR)
        self.btn_save.setText("保存")
        self._set_selection_actions_frozen(True)
        self._set_theme_mode("draft")
        self._set_entry_copied(False)
        self._show_hint(hint)
        QTimer.singleShot(1400, lambda: self._set_entry_copied(False))
        self._all_records = records
        self._refresh_cards()

    def _save(self):
        theme = self.inp_theme.text().strip()
        content = self.inp_content.toPlainText().strip()
        color = self.color_bar.color()

        if not content:
            self._show_hint("内容不能为空")
            return

        if not theme:
            theme = content[:12] + ("…" if len(content) > 12 else "")

        records = _sorted_by_order(_load_records())
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        is_update = bool(self._editing_id)

        if is_update:
            found = False
            for r in records:
                if r.get("id") == self._editing_id:
                    r["theme"] = theme
                    r["title"] = theme
                    r["content"] = content
                    r["color"] = color
                    r["date"] = now
                    found = True
                    break
            if not found:
                is_update = False
                self._editing_id = None

        if not is_update:
            max_order = max((int(r.get("order") or 0) for r in records), default=-1)
            records.append({
                "id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
                "theme": theme,
                "title": theme,
                "content": content,
                "color": color,
                "date": now,
                "use_count": 0,
                "order": max_order + 1,
            })

        self._after_record_write(records, "已更新" if is_update else "已保存")

    def _cancel_edit(self):
        """空白处：取消选中。替换中或外部复制还在时，不清内容框。"""
        if getattr(self, "_assist_watch", False):
            return
        if self._replace_armed or (self._capture_pending and self._editing_id is None):
            self._replace_armed = False
            self._selected_id = None
            self._set_selection_actions_frozen(True)
            self._mark_selected()
            self._apply_entry_frame("")
            self._sync_save_button()
            text = ""
            try:
                text = self.inp_content.toPlainText()
            except Exception:
                text = ""
            if self._capture_pending:
                self._set_entry_copied(True, plat=self._whitelist_plat_name(text))
            else:
                self._set_entry_status("已取消替换。", "warn")
            return
        self._clear_form()

    @staticmethod
    def _next_copy_theme(base_theme: str, existing_themes: set) -> str:
        """生成「名字 + 序号」：豆绘1 → 豆绘2；Grok → Grok2（遇重名递增）。"""
        name = (base_theme or "未命名").strip() or "未命名"
        m = re.match(r"^(.*?)(\d+)$", name)
        if m:
            stem, start = m.group(1), int(m.group(2))
        else:
            stem, start = name, 1
        n = start + 1
        # 防止 stem 为空时只剩纯数字
        while True:
            candidate = f"{stem}{n}" if stem else str(n)
            if candidate not in existing_themes:
                return candidate
            n += 1

    def _duplicate_selected(self):
        """「复制」：在仓库中新增一张 名字+序号 的同内容卡片（基于选中/编辑中的卡）。"""
        rid = self._active_card_id()
        if not rid:
            self._show_hint("请先选卡片")
            return

        # 编辑态以表单为准；仅选中时以原卡为准
        theme = self.inp_theme.text().strip() if self._editing_id else ""
        content = self.inp_content.toPlainText().strip() if self._editing_id else ""
        color = self.color_bar.color() if self._editing_id else ""

        src = next(
            (r for r in self._all_records if r.get("id") == rid),
            None,
        )
        if src is None:
            records_disk = _load_records()
            src = next((r for r in records_disk if r.get("id") == rid), None)
        if src is None and not content:
            self._show_hint("找不到卡片")
            return

        if not content:
            content = (src or {}).get("content") or ""
        if not content:
            self._show_hint("内容为空")
            return
        if not theme:
            theme = (src or {}).get("theme") or (src or {}).get("title") or "未命名"
        if not color:
            color = (src or {}).get("color") or DEFAULT_COLOR

        records = _sorted_by_order(_load_records())
        existing = {
            (r.get("theme") or r.get("title") or "").strip()
            for r in records
        }
        new_theme = self._next_copy_theme(theme, existing)

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        # 插在当前卡后面
        src_order = int((src or {}).get("order") or 0)
        new_rec = {
            "id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
            "theme": new_theme,
            "title": new_theme,
            "content": content,
            "color": color,
            "date": now,
            "use_count": 0,
            "order": src_order + 1,
        }
        # 其后卡片 order +1
        for r in records:
            if int(r.get("order") or 0) > src_order:
                r["order"] = int(r.get("order") or 0) + 1
        records.append(new_rec)
        records = _reindex(_sorted_by_order(records))
        _save_records(records)
        self._all_records = records
        self._refresh_cards()
        # 进入新卡编辑态，方便继续改
        self._enter_edit(new_rec, copied=False)
        self._show_hint(f"已复制为 {new_theme}")
        QTimer.singleShot(1600, self._restore_hint_after_op)

    def _delete_selected(self):
        """「删除」：删掉当前选中（或编辑中）的卡片。"""
        rid = self._active_card_id()
        if not rid:
            self._show_hint("请先选卡片")
            return
        record = next(
            (r for r in self._all_records if r.get("id") == rid),
            None,
        )
        if record is None:
            # 内存与磁盘不一致时兜底
            records = _load_records()
            record = next((r for r in records if r.get("id") == rid), None)
        if record is None:
            self._clear_form()
            self._show_hint("找不到卡片")
            return
        self._delete(record)

    def _delete(self, record):
        if getattr(self, "_assist_watch", False):
            return
        theme = record.get("theme") or record.get("title") or "此卡片"
        ret = QMessageBox.question(
            self, "删除",
            f"删除「{theme}」？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        records = [r for r in _load_records() if r.get("id") != record.get("id")]
        records = _reindex(_sorted_by_order(records))
        _save_records(records)
        rid = record.get("id")
        if self._editing_id == rid or self._selected_id == rid:
            self._clear_form()
        self._all_records = records
        self._refresh_cards()

    def _reorder_cards(self, src_id: str, tgt_id: str, insert_after: bool = False):
        if getattr(self, "_assist_watch", False):
            return
        records = _sorted_by_order(_load_records())
        src = next((r for r in records if str(r.get("id")) == str(src_id)), None)
        if src is None:
            return
        records = [r for r in records if str(r.get("id")) != str(src_id)]
        tgt_index = next((i for i, r in enumerate(records) if str(r.get("id")) == str(tgt_id)), None)
        if tgt_index is None:
            records.append(src)
        else:
            insert_at = tgt_index + 1 if insert_after else tgt_index
            records.insert(insert_at, src)
        records = _reindex(records)
        _save_records(records)
        self._all_records = records
        self._refresh_cards()

    def _reorder_to_end(self, src_id: str):
        if getattr(self, "_assist_watch", False):
            return
        records = _sorted_by_order(_load_records())
        src = next((r for r in records if str(r.get("id")) == str(src_id)), None)
        if src is None:
            return
        records = [r for r in records if str(r.get("id")) != str(src_id)]
        records.append(src)
        records = _reindex(records)
        _save_records(records)
        self._all_records = records
        self._refresh_cards()
