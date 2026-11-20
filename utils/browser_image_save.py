"""Read the image under the cursor in Chrome and Edge when Alt+1 is pressed."""

import ctypes as ct
from ctypes import wintypes as wt
from queue import Empty, Full, Queue
import re
import threading
import time
from uuid import UUID, uuid4


_BROWSERS = {"chrome.exe", "msedge.exe"}
_COPY_IMAGE_NAMES = {
    "copy image address", "copy image link", "copy image url",
    "复制图片地址", "复制图片链接", "复制图像地址", "复制图像链接",
    "複製圖片網址", "複製圖片連結", "複製影像網址", "複製影像連結", "複製圖像網址", "複製圖像連結",
}
_OBJECT_FOCUS = 0x8005
_ROLE_MENU_ITEM = 0x0C
_ROLE_GRAPHIC = 0x28
_IMAGE_HOTKEY = ord("1")  # MV41 also registers Alt+1 as a Chrome/Edge command.


def _menu_label(name):
    label = re.sub(r"\s*[（(]&?[A-Za-z][)）](?=\s*[.…]*\s*$)", "", name.split("\t", 1)[0])
    return label.replace("&", "").strip().rstrip(".…").strip().casefold()


def _image_src(attributes):
    for item in re.findall(r"(?:\\.|[^;\\])+", attributes):
        name, _, value = item.partition(":")
        if name == "src":
            return re.sub(r"\\(.)", r"\1", value)
    return ""


class _VariantValue(ct.Union):
    _fields_ = [("integer", wt.LONG), ("record", ct.c_void_p * 2)]


class _Variant(ct.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", wt.WORD), ("reserved", wt.WORD * 3), ("value", _VariantValue)]


_GUID = ct.c_ubyte * 16
_IID_SERVICE_PROVIDER = _GUID.from_buffer_copy(UUID("6d5140c1-7436-11ce-8034-00aa006009fa").bytes_le)
_IID_ACCESSIBLE = _GUID.from_buffer_copy(UUID("618736e0-3c3d-11cf-810c-00aa00389b71").bytes_le)
_IID_ACCESSIBLE2 = _GUID.from_buffer_copy(UUID("e89f726e-c4f4-4c19-bb19-b647d7fa8478").bytes_le)
_OUT_POINTER = ct.POINTER(ct.c_void_p)


def _com_method(pointer, index, result, *args):
    vtable = ct.cast(pointer, ct.POINTER(_OUT_POINTER))[0]
    method = ct.WINFUNCTYPE(result, ct.c_void_p, *args)(vtable[index])
    return method


class _AccessibleImage:
    def __init__(self):
        self._from_point = ct.WinDLL("oleacc").AccessibleObjectFromPoint
        self._from_point.argtypes = (wt.POINT, _OUT_POINTER, ct.POINTER(_Variant))
        self._from_point.restype = wt.LONG
        ole = ct.WinDLL("oleaut32")
        self._clear = ole.VariantClear
        self._clear.argtypes = (ct.POINTER(_Variant),)
        self._free_string = ole.SysFreeString
        self._free_string.argtypes = (ct.c_void_p,)
        self._free_string.restype = None

    def src_at(self, point):
        accessible = ct.c_void_p()
        child_object = ct.c_void_p()
        provider = ct.c_void_p()
        ia2 = ct.c_void_p()
        attributes = ct.c_void_p()
        child, role = _Variant(), _Variant()
        name = ct.c_void_p()
        try:
            if self._from_point(point, ct.byref(accessible), ct.byref(child)) < 0 or not accessible:
                return ""
            get_role = _com_method(accessible, 13, wt.LONG, _Variant, ct.POINTER(_Variant))
            get_role(accessible, child, ct.byref(role))
            if role.type != 3 or role.integer != _ROLE_GRAPHIC:
                # Chrome can initially expose a placeholder while it builds the page accessibility tree.
                get_name = _com_method(accessible, 10, wt.LONG, _Variant, _OUT_POINTER)
                get_name(accessible, child, ct.byref(name))
            image = accessible
            if child.type == 3 and child.integer != 0:  # MSAA returned the image's parent.
                get_child = _com_method(accessible, 9, wt.LONG, _Variant, _OUT_POINTER)
                if get_child(accessible, child, ct.byref(child_object)) < 0 or not child_object:
                    return ""
                image = child_object
            query_interface = _com_method(image, 0, wt.LONG, ct.c_void_p, _OUT_POINTER)
            if query_interface(image, ct.byref(_IID_SERVICE_PROVIDER), ct.byref(provider)) < 0 or not provider:
                return ""
            query_service = _com_method(provider, 3, wt.LONG, ct.c_void_p, ct.c_void_p, _OUT_POINTER)
            if query_service(provider, ct.byref(_IID_ACCESSIBLE), ct.byref(_IID_ACCESSIBLE2), ct.byref(ia2)) < 0 or not ia2:
                return ""
            get_attributes = _com_method(ia2, 45, wt.LONG, _OUT_POINTER)  # IAccessible2::get_attributes
            if get_attributes(ia2, ct.byref(attributes)) >= 0 and attributes:
                return _image_src(ct.wstring_at(attributes.value))
            return ""
        finally:
            self._free_string(name)
            self._free_string(attributes)
            self._clear(ct.byref(role))
            self._clear(ct.byref(child))
            for pointer in (ia2, provider, child_object, accessible):
                if pointer:
                    _com_method(pointer, 2, wt.ULONG)(pointer)  # IUnknown::Release


class _AccessibleMenu:
    def __init__(self):
        self._from_event = ct.WinDLL("oleacc").AccessibleObjectFromEvent
        self._from_event.argtypes = (
            wt.HWND, wt.DWORD, wt.DWORD, ct.POINTER(ct.c_void_p), ct.POINTER(_Variant),
        )
        self._from_event.restype = wt.LONG
        ole = ct.WinDLL("oleaut32")
        self._clear = ole.VariantClear
        self._clear.argtypes = (ct.POINTER(_Variant),)
        self._free_string = ole.SysFreeString
        self._free_string.argtypes = (ct.c_void_p,)
        self._free_string.restype = None

    def event_item(self, hwnd, obj_id, child_id):
        accessible, child = ct.c_void_p(), _Variant()
        if self._from_event(hwnd, obj_id, child_id, ct.byref(accessible), ct.byref(child)) < 0 or not accessible:
            return None, ""
        vtable = ct.cast(accessible, ct.POINTER(ct.POINTER(ct.c_void_p)))[0]
        release = ct.WINFUNCTYPE(wt.ULONG, ct.c_void_p)(vtable[2])
        get_role = ct.WINFUNCTYPE(wt.LONG, ct.c_void_p, _Variant, ct.POINTER(_Variant))(vtable[13])
        get_name = ct.WINFUNCTYPE(wt.LONG, ct.c_void_p, _Variant, ct.POINTER(ct.c_void_p))(vtable[10])
        role, name = _Variant(), ct.c_void_p()
        try:
            if get_role(accessible, child, ct.byref(role)) < 0 or role.type != 3:  # VT_I4
                return None, ""
            if role.integer == _ROLE_MENU_ITEM and get_name(accessible, child, ct.byref(name)) >= 0 and name:
                return role.integer, ct.wstring_at(name.value)
            return role.integer, ""
        finally:
            self._free_string(name)
            self._clear(ct.byref(role))
            self._clear(ct.byref(child))
            release(accessible)


class _CopyImageMenu:
    def __init__(self, user32, gui, process, pythoncom):
        import win32api
        import win32clipboard

        self._user32 = user32
        self._gui = gui
        self._process = process
        self._input = win32api
        self._clipboard = win32clipboard
        self._pump = pythoncom.PumpWaitingMessages
        self._menu = _AccessibleMenu()
        self._callback_type = ct.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG, wt.DWORD, wt.DWORD)
        event_api = ct.WinDLL("user32", use_last_error=True)
        self._hook = event_api.SetWinEventHook
        self._hook.argtypes = (wt.DWORD, wt.DWORD, wt.HMODULE, self._callback_type, wt.DWORD, wt.DWORD, wt.DWORD)
        self._hook.restype = wt.HANDLE
        self._unhook = event_api.UnhookWinEvent
        self._unhook.argtypes = (wt.HANDLE,)
        self._unhook.restype = wt.BOOL

    def _popups(self, browser):
        windows = []

        def collect(hwnd, _):
            if hwnd != browser and self._gui.IsWindowVisible(hwnd) and self._gui.GetAncestor(hwnd, 3) == browser:
                if self._gui.GetClassName(hwnd).startswith("Chrome_WidgetWin_"):
                    windows.append(hwnd)
            return True

        self._gui.EnumWindows(collect, None)
        return windows

    def _press(self, key):
        self._input.keybd_event(key, 0, 0, 0)
        self._input.keybd_event(key, 0, 2, 0)

    def _clipboard_snapshot(self):
        clipboard = self._clipboard
        clipboard.OpenClipboard()
        try:
            formats = []
            kind = clipboard.EnumClipboardFormats(0)
            while kind:
                if kind < 0xC000 and kind not in (1, 7, 13, 16):  # Only restorable text and registered formats.
                    return None
                formats.append(kind)
                kind = clipboard.EnumClipboardFormats(kind)
            values = [(kind, clipboard.GetClipboardData(kind)) for kind in formats]
            if any(not isinstance(data, (str, bytes)) for _, data in values):
                return None
            return clipboard.GetClipboardSequenceNumber(), values
        finally:
            clipboard.CloseClipboard()

    def _read_and_restore(self, browser_pid, sequence, saved, owner):
        clipboard = self._clipboard
        if clipboard.GetClipboardSequenceNumber() == sequence:
            return None
        clipboard.OpenClipboard(owner)
        try:
            source = clipboard.GetClipboardOwner()
            if not source or self._process.GetWindowThreadProcessId(source)[1] != browser_pid:
                return ""
            if not clipboard.IsClipboardFormatAvailable(clipboard.CF_UNICODETEXT):
                return None
            address = clipboard.GetClipboardData(clipboard.CF_UNICODETEXT).strip()
            clipboard.EmptyClipboard()
            for kind, data in saved:
                clipboard.SetClipboardData(kind, data)
            return address if address.lower().startswith(("http://", "https://")) else ""
        finally:
            clipboard.CloseClipboard()

    def capture(self, browser, point, stop):
        gui = self._gui
        clipboard = self._clipboard
        owner = None
        window = None
        hook = None
        focused_item = None

        @self._callback_type
        def on_focus(_hook, _event, hwnd, obj, child, _thread, _time):
            nonlocal focused_item
            if hwnd == window and obj == -4 and child:
                try:
                    role, name = self._menu.event_item(hwnd, obj, child)
                    focused_item = (child, _menu_label(name)) if role == _ROLE_MENU_ITEM else None
                except (OSError, gui.error):
                    focused_item = None

        try:
            cursor = wt.POINT()
            if not self._user32.GetPhysicalCursorPos(ct.byref(cursor)):
                return "", "无法确认鼠标位置。"
            if gui.GetForegroundWindow() != browser or (cursor.x, cursor.y) != (point.x, point.y):
                return "", "识别后鼠标已移动，未继续取址。"
            if any(self._user32.GetAsyncKeyState(key) & 0x8000 for key in (0x12, 0x01, 0x02)):
                return "", "请松开 Alt 和鼠标后重试。"
            snapshot = self._clipboard_snapshot()
            if snapshot is None:
                return "", "当前剪贴板包含无法完整保留的数据，已跳过浏览器取址。"
            sequence, saved = snapshot
            owner = gui.CreateWindow("Static", "", 0, 0, 0, 0, 0, 0, 0, self._input.GetModuleHandle(None), None)
            before = set(self._popups(browser))
            if (
                stop.is_set() or clipboard.GetClipboardSequenceNumber() != sequence
                or gui.GetForegroundWindow() != browser or not self._user32.GetPhysicalCursorPos(ct.byref(cursor))
                or (cursor.x, cursor.y) != (point.x, point.y)
            ):
                return "", "浏览器状态已变化，未继续取址。"
            hook = self._hook(_OBJECT_FOCUS, _OBJECT_FOCUS, None, on_focus, 0, 0, 2)
            if not hook:
                return "", "无法读取浏览器图片菜单。"
            self._input.mouse_event(0x0008, 0, 0, 0, 0)  # MOUSEEVENTF_RIGHTDOWN
            if stop.wait(0.03):
                self._input.mouse_event(0x0010, 0, 0, 0, 0)
                return "", "后台下载已关闭。"
            self._input.mouse_event(0x0010, 0, 0, 0, 0)  # MOUSEEVENTF_RIGHTUP
            for _ in range(18):
                if stop.wait(0.03) or gui.GetAncestor(gui.GetForegroundWindow(), 3) != browser:
                    return "", "浏览器状态已变化，未继续取址。"
                window = next((hwnd for hwnd in self._popups(browser) if hwnd not in before), None)
                if window:
                    break
            if not window:
                return "", "浏览器没有显示图片菜单。"
            visited = set()
            deadline = time.monotonic() + 2.5
            while time.monotonic() < deadline:
                if stop.is_set() or not gui.IsWindowVisible(window) or gui.GetAncestor(gui.GetForegroundWindow(), 3) != browser:
                    return "", "浏览器图片菜单已关闭。"
                self._pump()
                if focused_item is not None:
                    child, label = focused_item
                    if label in _COPY_IMAGE_NAMES:
                        if clipboard.GetClipboardSequenceNumber() != sequence:
                            return "", "剪贴板已改变，取消浏览器取址。"
                        self._press(0x0D)  # Enter, while the verified menu item is focused.
                        break
                    if child in visited:
                        return "", "浏览器没有提供「复制图片地址」菜单项。"
                    visited.add(child)
                    focused_item = None
                self._press(0x28)  # Down to read the next native menu item.
                stop.wait(0.04)
            else:
                return "", "浏览器没有提供「复制图片地址」菜单项。"
            pid = self._process.GetWindowThreadProcessId(browser)[1]
            for _ in range(18):
                try:
                    address = self._read_and_restore(pid, sequence, saved, owner)
                except clipboard.error:
                    address = None
                if address is not None:
                    return address, "浏览器菜单取址。" if address else "浏览器未返回可下载的图片地址。"
                time.sleep(0.025)
            return "", "浏览器没有返回图片地址。"
        except (OSError, gui.error):
            return "", "浏览器菜单取址失败。"
        finally:
            if hook:
                self._unhook(hook)
            if window and gui.IsWindowVisible(window) and gui.GetAncestor(gui.GetForegroundWindow(), 3) == browser:
                self._press(0x1B)
            if owner:
                gui.DestroyWindow(owner)


class BrowserImageSave:
    def __init__(self, log, on_link, on_hover=None, on_trigger=None):
        self._on_link = on_link
        self._on_hover = on_hover
        self._on_trigger = on_trigger
        self._log = log
        self._lock = threading.Lock()
        self._run = None

    @property
    def running(self):
        with self._lock:
            return self._run is not None

    def configure(self, enabled):
        with self._lock:
            if not enabled and self._run is not None:
                self._run.set()
                self._run = None
            elif enabled and self._run is None:
                stop = self._run = threading.Event()
                threading.Thread(target=self._watch, args=(stop,), name="browser-image-save", daemon=True).start()

    def _watch(self, stop):
        try:
            import pythoncom
            import win32gui
            import win32process
            import psutil

            pythoncom.CoInitialize()
            try:
                self._watch_windows(stop, pythoncom, win32gui, win32process, psutil)
            finally:
                pythoncom.CoUninitialize()
        except Exception:
            self._log.exception("速存图片的浏览器窗口监控退出")
        finally:
            stop.set()
            with self._lock:
                if self._run is stop:
                    self._run = None

    def _capture_hover_links(self, stop, requests, user32, pythoncom, gui, process, supported_browser):
        # Keep COM hit testing off the keyboard-hook thread, since the browser may respond slowly.
        window_at_point = user32.WindowFromPhysicalPoint
        window_at_point.argtypes = (wt.POINT,)
        window_at_point.restype = wt.HWND
        cursor_pos = user32.GetPhysicalCursorPos

        def link_at(hwnd, point):
            try:
                hit = window_at_point(point)
                if gui.GetForegroundWindow() == hwnd and hit and gui.GetAncestor(hit, 2) == hwnd:  # GA_ROOT
                    return accessibility.src_at(point)
            except (OSError, gui.error):
                pass
            return ""

        pythoncom.CoInitialize()
        operation_id = ""
        try:
            accessibility = _AccessibleImage()
            menu = None
            last_window = None
            last_point = None
            browser_is_foreground = False
            hover_ready = False
            hover_check_at = 0.0
            while not stop.is_set():
                try:
                    operation_id, hwnd, x, y = requests.get(timeout=0.15)
                except Empty:
                    if self._on_hover is None:
                        continue
                    hwnd = gui.GetForegroundWindow()
                    if hwnd != last_window:
                        browser_is_foreground = bool(supported_browser(hwnd))
                        last_window = hwnd
                    point = wt.POINT()
                    where = (hwnd, point.x, point.y) if browser_is_foreground and cursor_pos(ct.byref(point)) else None
                    now = time.monotonic()
                    if where != last_point:
                        last_point = where
                        hover_check_at = now + 0.15
                        ready = False
                    elif where is None or now < hover_check_at:
                        continue
                    else:
                        hover_check_at = now + 0.6
                        ready = link_at(hwnd, point).lower().startswith(("http://", "https://"))
                    if ready != hover_ready and not stop.is_set():
                        hover_ready = ready
                        self._on_hover(ready)
                    continue
                point = wt.POINT(x, y)
                link = ""
                for _ in range(5):
                    if stop.is_set() or gui.GetForegroundWindow() != hwnd:
                        break
                    link = link_at(hwnd, point)
                    if link or stop.wait(0.06):
                        break
                if not stop.is_set() and self._on_link is not None:
                    if gui.GetForegroundWindow() != hwnd:
                        self._on_link(operation_id, "", "读取链接前切换了浏览器窗口。")
                    else:
                        reason = ""
                        if not link:
                            hit = window_at_point(point)
                            if hit and gui.GetAncestor(hit, 2) == hwnd:
                                if menu is None:
                                    menu = _CopyImageMenu(user32, gui, process, pythoncom)
                                link, reason = menu.capture(hwnd, point, stop)
                            else:
                                reason = "光标未命中当前浏览器窗口。"
                        if not stop.is_set():
                            self._on_link(operation_id, link, reason)
                operation_id = ""
        except Exception:
            self._log.exception("读取光标下图片地址失败")
            stop.set()
            if self._on_link is not None:
                if operation_id:
                    self._on_link(operation_id, "", "浏览器辅助功能读取失败。")
                while not requests.empty():
                    pending_id, *_ = requests.get_nowait()
                    self._on_link(pending_id, "", "浏览器辅助功能读取失败。")
        finally:
            pythoncom.CoUninitialize()

    def _watch_windows(self, stop, pythoncom, gui, process, psutil):
        user32 = ct.WinDLL("user32", use_last_error=True)
        key_callback_type = ct.WINFUNCTYPE(ct.c_ssize_t, ct.c_int, ct.c_size_t, ct.c_ssize_t)
        hook_keyboard = user32.SetWindowsHookExW
        hook_keyboard.argtypes = (ct.c_int, key_callback_type, wt.HINSTANCE, wt.DWORD)
        hook_keyboard.restype = wt.HANDLE
        unhook_keyboard = user32.UnhookWindowsHookEx
        unhook_keyboard.argtypes = (wt.HANDLE,)
        unhook_keyboard.restype = wt.BOOL
        next_keyboard = user32.CallNextHookEx
        next_keyboard.argtypes = (wt.HANDLE, ct.c_int, ct.c_size_t, ct.c_ssize_t)
        next_keyboard.restype = ct.c_ssize_t
        cursor_pos = user32.GetPhysicalCursorPos
        cursor_pos.argtypes = (ct.POINTER(wt.POINT),)
        cursor_pos.restype = wt.BOOL
        requests = Queue(maxsize=1)
        hotkey_down = False

        def browser_for(hwnd):
            _, pid = process.GetWindowThreadProcessId(hwnd)
            return psutil.Process(pid).name().lower()

        def supported_browser(hwnd):
            try:
                if hwnd and gui.GetClassName(hwnd).startswith("Chrome_WidgetWin_"):
                    name = browser_for(hwnd)
                    if name in _BROWSERS:
                        return name
            except (OSError, psutil.Error, gui.error):
                pass
            return ""

        @key_callback_type
        def on_key(code, message, data):
            nonlocal hotkey_down
            if code == 0 and not stop.is_set():
                key, _, flags = ct.cast(data, ct.POINTER(wt.DWORD * 3)).contents
                if key == _IMAGE_HOTKEY and hotkey_down:
                    if flags & 0x80:  # LLKHF_UP
                        hotkey_down = False
                    return 1
                if key == _IMAGE_HOTKEY and message in (0x0100, 0x0104) and flags & 0x20 and not any(
                    user32.GetAsyncKeyState(modifier) & 0x8000 for modifier in (0x10, 0x11, 0x5B, 0x5C)
                ):
                    hwnd = gui.GetForegroundWindow()
                    browser = supported_browser(hwnd)
                    if browser:
                        hotkey_down = True
                        operation_id = uuid4().hex
                        if self._on_trigger is not None:
                            self._on_trigger(operation_id, browser)
                        point = wt.POINT()
                        if cursor_pos(ct.byref(point)):
                            try:
                                requests.put_nowait((operation_id, hwnd, point.x, point.y))
                            except Full:
                                self._on_link(operation_id, "", "图片识别队列忙，请稍后重试。")
                        else:
                            self._on_link(operation_id, "", "无法读取鼠标位置。")
                        return 1
            return next_keyboard(None, code, message, data)

        keyboard = hook_keyboard(13, on_key, None, 0)  # WH_KEYBOARD_LL
        if not keyboard:
            raise ct.WinError(ct.get_last_error())
        try:
            threading.Thread(
                target=self._capture_hover_links, args=(stop, requests, user32, pythoncom, gui, process, supported_browser),
                name="browser-image-hit-test", daemon=True,
            ).start()
            while not stop.wait(0.03):
                if pythoncom.PumpWaitingMessages():
                    break
        finally:
            unhook_keyboard(keyboard)
