# utils/selection_capture.py
# 前台选区捕获：WM_COPY / Ctrl+C，并保存/恢复剪贴板 mime。
# 供主窗 F7「静默复制」（按侧栏复制归属推送前台选中文字）使用；全程不抢焦点。

from __future__ import annotations

import ctypes
import os
import sys

try:
    import ctypes.wintypes as _wintypes
except Exception:  # pragma: no cover
    _wintypes = None

from PyQt5.QtCore import (
    QAbstractNativeEventFilter,
    QCoreApplication,
    QMimeData,
    QObject,
    QTimer,
    pyqtSignal,
)
from PyQt5.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QTextEdit

from utils.logger import get_logger

log = get_logger(__name__)

# 与旧游戏大脑 F7 同 id，解绑后由主窗接管
F7_HOTKEY_ID = 0x1030
VK_F7 = 0x76
WM_HOTKEY = 0x0312
WM_COPY = 0x0301

VK_CONTROL, VK_C = 0x11, 0x43
VK_SHIFT, VK_LSHIFT, VK_RSHIFT = 0x10, 0xA0, 0xA1
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002
CF_UNICODETEXT = 13
HK_RETRY_MS = (300, 700, 1500, 3000)

try:
    _USER32 = ctypes.WinDLL("user32", use_last_error=True)
except Exception:  # pragma: no cover
    _USER32 = None
try:
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
except Exception:  # pragma: no cover
    _KERNEL32 = None

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


def win_register_hotkey(hotkey_id: int, vk: int, mods: int = 0):
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


def win_unregister_hotkey(hotkey_id: int) -> None:
    if _USER32 is None:
        return
    try:
        _USER32.UnregisterHotKey(None, int(hotkey_id))
    except Exception:
        pass


def clip_seq() -> int:
    if _USER32 is None:
        return 0
    try:
        return int(_USER32.GetClipboardSequenceNumber() or 0)
    except Exception:
        return 0


def unstick_modifiers() -> None:
    if _USER32 is None:
        return
    for vk in (VK_SHIFT, VK_LSHIFT, VK_RSHIFT, VK_CONTROL, VK_MENU):
        try:
            if _USER32.GetAsyncKeyState(vk) & 0x8000:
                _USER32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        except Exception:
            pass


def _keybd(vk: int, up: bool = False) -> None:
    if _USER32 is None:
        return
    try:
        _USER32.keybd_event(int(vk), 0, KEYEVENTF_KEYUP if up else 0, 0)
    except Exception:
        pass


def send_ctrl(vk: int) -> None:
    _keybd(VK_CONTROL, False)
    _keybd(vk, False)
    _keybd(vk, True)
    _keybd(VK_CONTROL, True)


def focused_hwnd():
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


def send_wm(msg: int) -> None:
    if _USER32 is None:
        return
    hwnd = focused_hwnd()
    if not hwnd:
        return
    try:
        _USER32.SendMessageW(hwnd, int(msg), 0, 0)
    except Exception:
        pass


def clone_mime(md) -> QMimeData:
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


def _clipboard_cf_unicode() -> str:
    if os.name != "nt" or _USER32 is None or _KERNEL32 is None:
        return ""
    if not _USER32.OpenClipboard(None):
        return ""
    try:
        if not _USER32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return ""
        handle = _USER32.GetClipboardData(CF_UNICODETEXT)
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


def read_clipboard_text() -> str:
    text = ""
    try:
        text = _clipboard_cf_unicode()
    except Exception:
        text = ""
    if text:
        return text
    try:
        cb = QApplication.clipboard()
        if cb is None:
            return ""
        return (cb.text() or "").replace("\r\n", "\n").replace("\r", "\n")
    except Exception:
        return ""


def normalize_sel_text(text: str) -> str:
    if not text:
        return ""
    return (
        text.replace("\u2029", "\n")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def try_local_selection_text() -> str:
    """本进程焦点控件若有选区，直接取文本（不必模拟 Ctrl+C）。"""
    try:
        w = QApplication.focusWidget()
    except Exception:
        w = None
    if w is None:
        return ""
    try:
        if isinstance(w, QLineEdit):
            return normalize_sel_text(w.selectedText() or "")
        if isinstance(w, (QPlainTextEdit, QTextEdit)):
            tc = w.textCursor()
            if tc is not None and tc.hasSelection():
                return normalize_sel_text(tc.selectedText() or "")
    except Exception:
        pass
    # 兜底：常见 selectedText API
    try:
        fn = getattr(w, "selectedText", None)
        if callable(fn):
            return normalize_sel_text(fn() or "")
    except Exception:
        pass
    return ""


def restore_mime(saved: QMimeData, fallback_text: str = "") -> None:
    try:
        cb = QApplication.clipboard()
        if cb is not None and isinstance(saved, QMimeData):
            cb.setMimeData(clone_mime(saved))
            return
    except Exception:
        pass
    if fallback_text:
        try:
            cb = QApplication.clipboard()
            if cb is not None:
                cb.setText(fallback_text)
        except Exception:
            pass


class _F7HotkeyBridge(QObject):
    fired = pyqtSignal()


class _F7HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, bridge: _F7HotkeyBridge, hid: int):
        super().__init__()
        self._bridge = bridge
        self._hid = int(hid)

    def nativeEventFilter(self, eventType, message):
        try:
            if eventType not in ("windows_generic_MSG", "windows_dispatcher_MSG"):
                return False, 0
            if _wintypes is None:
                return False, 0
            msg = _wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and int(msg.wParam) == self._hid:
                self._bridge.fired.emit()
                return True, 0
        except Exception:
            log.exception("F7 静默复制热键消息过滤失败")
        return False, 0


class SelectionCapture(QObject):
    """异步捕获前台选区文本；完成后恢复剪贴板。"""

    captured = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._saved = QMimeData()
        self._saved_text = ""
        self._seq0 = 0
        self._on_begin_suppress = None
        self._on_end_suppress = None

    @property
    def busy(self) -> bool:
        return bool(self._busy)

    def set_suppress_hooks(self, begin=None, end=None):
        self._on_begin_suppress = begin
        self._on_end_suppress = end

    def start(self) -> bool:
        if self._busy:
            return False
        if not sys.platform.startswith("win"):
            self.failed.emit("仅支持 Windows")
            return False
        self._busy = True
        self._saved = QMimeData()
        self._saved_text = ""
        self._seq0 = clip_seq()
        try:
            cb = QApplication.clipboard()
            if cb is not None:
                self._saved = clone_mime(cb.mimeData())
                self._saved_text = cb.text() or ""
        except Exception:
            pass
        try:
            if callable(self._on_begin_suppress):
                self._on_begin_suppress()
        except Exception:
            log.exception("选区捕获：开始抑制监听失败")

        local = try_local_selection_text()
        if (local or "").strip():
            QTimer.singleShot(0, lambda t=local: self._finish_ok(t))
            return True
        QTimer.singleShot(20, self._do_wm_copy)
        return True

    def _do_wm_copy(self):
        if not self._busy:
            return
        unstick_modifiers()
        send_wm(WM_COPY)
        QTimer.singleShot(70, self._after_wm_copy)

    def _after_wm_copy(self):
        if not self._busy:
            return
        if clip_seq() != int(self._seq0 or 0):
            self._read_result()
            return
        unstick_modifiers()
        send_ctrl(VK_C)
        QTimer.singleShot(90, lambda: self._read_result(extra=False))

    def _read_result(self, extra: bool = False):
        if not self._busy:
            return
        b = ""
        try:
            b = normalize_sel_text(read_clipboard_text())
        except Exception:
            b = ""
        seq_now = clip_seq()
        if seq_now == int(self._seq0 or 0):
            if not extra:
                QTimer.singleShot(120, lambda: self._read_result(extra=True))
                return
            self._finish_fail("没有选中文本")
            return
        if not (b or "").strip():
            self._finish_fail("没有选中文本")
            return
        self._finish_ok(b)

    def _restore_and_end_suppress(self):
        try:
            restore_mime(self._saved, self._saved_text)
        except Exception:
            log.exception("选区捕获：恢复剪贴板失败")
        # 等 dataChanged 走完再解除抑制
        QTimer.singleShot(100, self._end_suppress_safe)

    def _end_suppress_safe(self):
        try:
            if callable(self._on_end_suppress):
                self._on_end_suppress()
        except Exception:
            log.exception("选区捕获：结束抑制监听失败")

    def _finish_ok(self, text: str):
        text = normalize_sel_text(text or "")
        self._busy = False
        self._restore_and_end_suppress()
        if not text.strip():
            self.failed.emit("没有选中文本")
            return
        self.captured.emit(text)

    def _finish_fail(self, reason: str):
        self._busy = False
        self._restore_and_end_suppress()
        self.failed.emit(str(reason or "未能读取选区"))


class F7SelectionHotkey(QObject):
    """主窗注册全局 F7；命中后发 fired（不抢焦点）。"""

    fired = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._filter = None
        self._bridge = None
        self._ok = False
        # 静默复制开关：False 时 bind（含挂起的重试定时器）一律不注册
        self._wanted = True

    @property
    def ok(self) -> bool:
        return bool(self._ok)

    @property
    def wanted(self) -> bool:
        return bool(self._wanted)

    def set_wanted(self, wanted: bool) -> None:
        self._wanted = bool(wanted)

    def bind(self, _attempt: int = 0) -> bool:
        if not self._wanted:
            self._ok = False
            return False
        if not sys.platform.startswith("win"):
            self._ok = False
            return False
        self.unbind(keep_filter=True)
        ok, err = win_register_hotkey(F7_HOTKEY_ID, VK_F7)
        if not ok:
            if _attempt < len(HK_RETRY_MS):
                delay = int(HK_RETRY_MS[_attempt])
                QTimer.singleShot(delay, lambda a=_attempt + 1: self.bind(a))
            if _attempt == 0:
                log.warning("主窗 F7 静默复制热键注册失败 winerr=%s", err)
            self._ok = False
            return False
        if self._filter is None:
            self._bridge = _F7HotkeyBridge(self)
            self._bridge.fired.connect(self.fired.emit)
            self._filter = _F7HotkeyFilter(self._bridge, F7_HOTKEY_ID)
            app = QCoreApplication.instance()
            if app is not None:
                app.installNativeEventFilter(self._filter)
        self._ok = True
        if _attempt > 0:
            log.info("主窗 F7 静默复制热键重试成功 attempt=%s", _attempt)
        return True

    def unbind(self, keep_filter: bool = False) -> None:
        win_unregister_hotkey(F7_HOTKEY_ID)
        self._ok = False
        if keep_filter:
            return
        self._filter = None
