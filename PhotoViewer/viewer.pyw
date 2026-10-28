# -*- coding: utf-8 -*-
"""极简看图 - 左右方向键/A D 切换, F11/双击 全屏, Esc 退出全屏或关闭, 滚轮缩放
数字区 * 按图片比例调整窗口，画面铺满，不留黑边
数字区 / ：按一下 100%，再按一下适当比例（大图缩小，小图不放大）。0 直接适当比例"""
import ctypes
import json
import os
import re
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes
from tkinter import filedialog

EXTS = {'.jpg','.jpeg','.png','.gif','.bmp','.webp','.tif','.tiff','.ico','.jfif','.ppm','.pgm'}
BG = '#111111'
TITLE_BG = '#000000'
TITLE_FG = '#ffffff'
TITLE = '桌面助手 v9.18·图片查看器'
TITLE_H = 32
MIN_W, MIN_H = 200, 160
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('DesktopAssistant.PhotoViewer')
except Exception:
    pass
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

def app_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_cfg():
    """读系统总览里的文件夹穿透。读不到就留在当前文件夹。翻页仍认全部图片类型。"""
    try:
        root = app_root()
        if root not in sys.path:
            sys.path.insert(0, root)
        from utils.photo_viewer_cfg import read_pierce, read_lock_scale, read_auto_window, read_auto_fit
        return set(EXTS), read_pierce(), read_lock_scale(), read_auto_window(), read_auto_fit()
    except Exception:
        return set(EXTS), 'stay', False, False, True

def image_filetypes():
    pat = ' '.join('*' + e for e in sorted(EXTS))
    return [('图片', pat), ('所有文件', '*.*')]

def nkey(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]

# 灰白格按屏幕像素铺，放大缩小后格子大小不变。
_CHECKER_CELL = 16
_CHECKER_C1 = (255, 255, 255)
_CHECKER_C2 = (192, 192, 192)

def display_scale(iw, ih, cw, ch, mode, zoom, dw=0, dh=0):
    """actual 为 1 图像像素对 1 屏幕像素。fit（适应）按桌面工作区：大图缩小、小图放大，
    整张图放进工作区；fill 则缩放到刚好铺满窗口。"""
    iw, ih = max(int(iw), 1), max(int(ih), 1)
    if mode == 'actual':
        return 1.0
    if mode == 'manual':
        return zoom
    if mode == 'fill':
        return min(max(cw, 1) / iw, max(ch, 1) / ih)
    dw, dh = max(int(dw or 0), 1), max(int(dh or 0), 1)
    if dw <= 1 or dh <= 1:
        dw, dh = max(cw, 1), max(ch, 1)
    return min(dw / iw, dh / ih)

def _drawn_size(iw, ih, scale):
    return max(1, int(iw * scale)), max(1, int(ih * scale))

def clamp_pan(ox, oy, view_w, view_h, drawn_w, drawn_h):
    """画面比窗口大时，拖动不把图拖出空边。某一边已经看全就固定在中间。"""
    def axis(off, view, drawn):
        if drawn <= view + 1:
            return 0.0
        limit = (drawn - view) / 2.0
        if off > limit:
            return limit
        if off < -limit:
            return -limit
        return float(off)
    return axis(ox, view_w, drawn_w), axis(oy, view_h, drawn_h)

def hug_window_geometry(iw, ih, scale, chrome_h, max_w, max_h):
    """窗口缩放到图片比例，画布刚好等于图片的显示大小，不留黑边。

    整张图已经在画面里时，保持现在的显示大小，只改窗口。
    图比屏幕大时，缩到当前屏幕工作区里能看全的最大尺寸。
    """
    iw, ih = max(int(iw), 1), max(int(ih), 1)
    chrome_h = max(int(chrome_h), 0)
    max_w, max_h = max(int(max_w), 1), max(int(max_h), 1)
    max_ch = max(1, max_h - chrome_h)
    fit_max = min(max_w / iw, max_ch / ih)
    s = min(max(float(scale or 1.0), 0.01), fit_max)

    def box(scale_value):
        nw, nh = _drawn_size(iw, ih, scale_value)
        return nw, nh + chrome_h, scale_value

    win_w, win_h, used = box(s)
    # 太小的窗口存不下来，屏幕还放得下时再放大一点，比例不变。
    if (win_w < 200 or win_h < 160) and fit_max > used:
        need = used
        if win_w < 200:
            need = max(need, 200 / iw)
        if win_h < 160:
            need = max(need, max(1, 160 - chrome_h) / ih)
        used = min(need, fit_max)
        win_w, win_h, used = box(used)
    guard = 0
    while (win_w > max_w or win_h > max_h) and used > 0.01 and guard < 8:
        used *= 0.99
        win_w, win_h, used = box(used)
        guard += 1
    return win_w, win_h, used

def hug_origin(cur_x, cur_y, cur_w, cur_h, new_w, new_h, work_x, work_y, work_w, work_h):
    """新窗口以当前窗口中心为中心，并留在工作区内。"""
    x = int(round(cur_x + cur_w / 2 - new_w / 2))
    y = int(round(cur_y + cur_h / 2 - new_h / 2))
    x = min(max(x, int(work_x)), int(work_x + work_w - new_w))
    y = min(max(y, int(work_y)), int(work_y + work_h - new_h))
    return x, y

def checker_image(w, h, cell=_CHECKER_CELL):
    from PIL import Image
    w, h = max(int(w), 1), max(int(h), 1)
    cell = max(int(cell), 1)
    tile = Image.new('RGB', (cell * 2, cell * 2), _CHECKER_C1)
    gray = Image.new('RGB', (cell, cell), _CHECKER_C2)
    tile.paste(gray, (cell, 0))
    tile.paste(gray, (0, cell))
    board = Image.new('RGB', (w, h))
    tw, th = tile.size
    for y in range(0, h, th):
        for x in range(0, w, tw):
            board.paste(tile, (x, y))
    return board

def paint_transparency(im, cell=_CHECKER_CELL):
    """透明像素换成灰白格。没有透明通道时原样返回。"""
    from PIL import Image
    if im.mode != 'RGBA':
        return im
    board = checker_image(im.width, im.height, cell).convert('RGBA')
    return Image.alpha_composite(board, im)

def has_transparency(im):
    if im.mode != 'RGBA':
        return False
    lo, hi = im.getchannel('A').getextrema()
    return lo < 255

def _hwnd(widget):
    widget.update_idletasks()
    wid = int(widget.winfo_id())
    parent = ctypes.windll.user32.GetParent(wid)
    return parent or wid

# 普通窗口才能用屏幕顶部的贴靠布局。系统标题栏用 WM_NCCALCSIZE 藏起来，黑标题栏仍自己画。
WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_GETMINMAXINFO = 0x0024
WM_SIZE = 0x0005
WM_NCDESTROY = 0x0082
HTCLIENT = 1
HTCAPTION = 2
HTMAXBUTTON = 9
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17
HTTRANSPARENT = -1
CAP_W = 46
_SNAP = {}
_CHILDREN = {}
_SNAP_VIEW = None

class _POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

class _MINMAXINFO(ctypes.Structure):
    _fields_ = [
        ('ptReserved', _POINT), ('ptMaxSize', _POINT), ('ptMaxPosition', _POINT),
        ('ptMinTrackSize', _POINT), ('ptMaxTrackSize', _POINT),
    ]

class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [('rgrc', wintypes.RECT * 3), ('lppos', ctypes.c_void_p)]

class _MARGINS(ctypes.Structure):
    _fields_ = [
        ('cxLeftWidth', ctypes.c_int), ('cxRightWidth', ctypes.c_int),
        ('cyTopHeight', ctypes.c_int), ('cyBottomHeight', ctypes.c_int),
    ]

def snap_border():
    """和系统拉边一样宽。按钮往左让出这一条。"""
    user32 = ctypes.windll.user32
    return max(8, int(user32.GetSystemMetrics(32) + user32.GetSystemMetrics(92)))

def _tk_width_extra(hwnd):
    """Tk 按标题栏样式把左右边框加进窗口宽度，客户区被铺满后不会再减回去。"""
    user32 = ctypes.windll.user32
    style = user32.GetWindowLongW(hwnd, -16) & 0xFFFFFFFF
    exstyle = user32.GetWindowLongW(hwnd, -20) & 0xFFFFFFFF
    rect = wintypes.RECT(0, 0, 0, 0)
    if not user32.AdjustWindowRectEx(ctypes.byref(rect), style, False, exstyle):
        return 0
    return max(0, int(rect.right - rect.left))

def _signed_xy(lparam):
    val = int(lparam) & 0xFFFFFFFF
    x, y = val & 0xFFFF, (val >> 16) & 0xFFFF
    if x >= 0x8000:
        x -= 0x10000
    if y >= 0x8000:
        y -= 0x10000
    return x, y

def resize_zone(rel_x, rel_y, w, h, border):
    """左、右、下只在该边中间 30% 可拉。左下、右下各占宽高的 12%。上边不调整。"""
    if w <= 1 or h <= 1:
        return None
    cw, ch = w * 0.12, h * 0.12
    if rel_y >= h - ch:
        if rel_x < cw:
            return 'sw'
        if rel_x >= w - cw:
            return 'se'
    b = max(int(border), 1)
    if rel_x < b and h * 0.35 <= rel_y <= h * 0.65:
        return 'w'
    if rel_x >= w - b and h * 0.35 <= rel_y <= h * 0.65:
        return 'e'
    if rel_y >= h - b and w * 0.35 <= rel_x <= w * 0.65:
        return 's'
    return None

def snap_hit(rel_x, rel_y, w, h, border, title_h, caption_on, zoomed, fullscreen):
    """返回系统命中码。None 表示交给原来的窗口过程。"""
    if fullscreen or w <= 0 or h <= 0:
        return None
    if not zoomed:
        code = {
            'sw': HTBOTTOMLEFT, 'se': HTBOTTOMRIGHT,
            'w': HTLEFT, 'e': HTRIGHT, 's': HTBOTTOM,
        }.get(resize_zone(rel_x, rel_y, w, h, border))
        if code is not None:
            return code
    b = max(int(border), 1)
    if caption_on and 0 <= rel_y < title_h:
        btn_right = w - b
        if btn_right - CAP_W <= rel_x < btn_right:
            return None
        if btn_right - CAP_W * 2 <= rel_x < btn_right - CAP_W:
            return HTMAXBUTTON
        if btn_right - CAP_W * 3 <= rel_x < btn_right - CAP_W * 2:
            return None
        return HTCAPTION
    return None

def _call_wnd_proc(old, hwnd, msg, wparam, lparam):
    fn = ctypes.windll.user32.CallWindowProcW
    fn.restype = ctypes.c_ssize_t
    fn.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    return fn(ctypes.c_void_p(int(old)), hwnd, msg, wparam, lparam)

def _subclass(hwnd, proc):
    fn = ctypes.windll.user32.SetWindowLongPtrW
    fn.restype = ctypes.c_void_p
    fn.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    return fn(hwnd, -4, ctypes.cast(proc, ctypes.c_void_p))

def _def_window_proc(hwnd, msg, wparam, lparam):
    fn = ctypes.windll.user32.DefWindowProcW
    fn.restype = ctypes.c_ssize_t
    fn.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    return fn(hwnd, msg, wparam, lparam)

def _work_rect(hwnd):
    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
            ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD),
        ]
    user32 = ctypes.windll.user32
    user32.MonitorFromWindow.restype = ctypes.c_void_p
    hmon = user32.MonitorFromWindow(hwnd, 2)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
        return None
    return info.rcWork

def _snap_wnd_proc(hwnd, msg, wparam, lparam):
    item = _SNAP.get(int(hwnd))
    if not item:
        return 0
    old, view = item
    try:
        if msg == WM_NCDESTROY:
            result = _call_wnd_proc(old, hwnd, msg, wparam, lparam)
            _SNAP.pop(int(hwnd), None)
            return result
        if msg == WM_NCCALCSIZE and wparam:
            user32 = ctypes.windll.user32
            if user32.IsZoomed(hwnd) and not view._fullscreen:
                params = ctypes.cast(lparam, ctypes.POINTER(_NCCALCSIZE_PARAMS)).contents
                work = _work_rect(hwnd)
                if work is not None:
                    params.rgrc[0].left = work.left
                    params.rgrc[0].top = work.top
                    params.rgrc[0].right = work.right
                    params.rgrc[0].bottom = work.bottom
            return 0
        if msg == WM_GETMINMAXINFO:
            _call_wnd_proc(old, hwnd, msg, wparam, lparam)
            info = ctypes.cast(lparam, ctypes.POINTER(_MINMAXINFO)).contents
            info.ptMinTrackSize.x = view._track_w
            info.ptMinTrackSize.y = view._track_h
            return 0
        if msg == 0x0047 and not view._fullscreen and not view._snap_modal:
            # 最大化不走 WM_SIZE。这里只记状态，不进 Tk。
            view._max = bool(ctypes.windll.user32.IsZoomed(hwnd))
        if msg == WM_NCHITTEST:
            code = view._snap_hit_at(lparam)
            hot = code == HTMAXBUTTON
            if hot != getattr(view, '_max_hot', False):
                view._max_hot = hot
                try:
                    view.root.after(0, lambda h=hot: view._snap_max_hot(h))
                except tk.TclError:
                    pass
            if code is not None:
                return code
    except Exception:
        pass
    return _call_wnd_proc(old, hwnd, msg, wparam, lparam)

def _child_wnd_proc(hwnd, msg, wparam, lparam):
    item = _CHILDREN.get(int(hwnd))
    if not item:
        return 0
    old, kind = item
    try:
        if msg == WM_NCDESTROY:
            result = _call_wnd_proc(old, hwnd, msg, wparam, lparam)
            _CHILDREN.pop(int(hwnd), None)
            return result
        # 子窗口若回 HTTRANSPARENT，点击会穿透，标题拖不动、边框拉不动、最大化点不到。
    except Exception:
        pass
    return _call_wnd_proc(old, hwnd, msg, wparam, lparam)

def _thin_snap_proc(hwnd, msg, wparam, lparam):
    """系统拖动时只用这个过程。不能把消息交回 Tk，否则一进 Python 就退出。"""
    if msg == WM_NCCALCSIZE and wparam:
        return 0
    if msg == WM_GETMINMAXINFO:
        info = ctypes.cast(lparam, ctypes.POINTER(_MINMAXINFO)).contents
        item = _SNAP.get(int(hwnd))
        info.ptMinTrackSize.x = item[1]._track_w if item else MIN_W
        info.ptMinTrackSize.y = item[1]._track_h if item else MIN_H
        return 0
    if msg == WM_NCHITTEST:
        x, y = _signed_xy(lparam)
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        rel_x, rel_y = x - rect.left, y - rect.top
        w, h = rect.right - rect.left, rect.bottom - rect.top
        item = _SNAP.get(int(hwnd))
        border = item[1]._snap_border if item else 8
        zoomed = bool(ctypes.windll.user32.IsZoomed(hwnd))
        code = snap_hit(rel_x, rel_y, w, h, border, TITLE_H, True, zoomed, False)
        if code is not None:
            return code
    return _def_window_proc(hwnd, msg, wparam, lparam)

_SNAP_PROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)(_snap_wnd_proc)
_THIN_PROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)(_thin_snap_proc)
_CHILD_PROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)(_child_wnd_proc)

def _win_file():
    return os.path.join(app_root(), 'userdata', 'photo_viewer_win.txt')

def load_win():
    try:
        with open(_win_file(), encoding='utf-8') as f:
            data = json.load(f)
        x, y, w, h = int(data['x']), int(data['y']), int(data['w']), int(data['h'])
    except Exception:
        return None
    if w < 200 or h < 160:
        return None
    return x, y, w, h

def save_win(x, y, w, h):
    path = _win_file()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'x': int(x), 'y': int(y), 'w': int(w), 'h': int(h)}, f)
            f.write('\n')
        os.replace(tmp, path)
    except Exception:
        pass

class Viewer:
    def __init__(self, root, path):
        self.root, self.files, self.idx = root, [], 0
        self.cache, self.lock, self.zoom, self.tkimg = {}, threading.Lock(), 1.0, None
        self.scale_mode = 'fit'
        self.fit_mode = True
        self._fit_pref = None
        self._suppress_show = False
        self._shown_scale = 1.0
        self._alpha = {}
        self.exts, self.pierce, self.lock_scale, self.auto_window = set(EXTS), 'stay', False, False
        self._fullscreen = False
        self._max = False
        self._drag = None
        self._pan = None
        self._ox = 0.0
        self._oy = 0.0
        self._disp_w = 0
        self._disp_h = 0
        self._img_id = None
        self._pan_for = None
        self._pending_move = None
        self._rz = None
        self._save_job = None
        self._icon_img = None
        self._snap_border = snap_border()
        self._snap_hwnd = 0
        self._tk_width_extra = 0
        self._track_w, self._track_h = MIN_W, MIN_H
        self._snap_modal = False
        self._sys_pending = None
        self._max_hot = False
        self._toast_job = None
        self._cur_name = ''
        root.title(TITLE)
        root.configure(bg=BG)
        root.withdraw()
        self._install_snap_frame()
        self._apply_icon()
        self._build_title()
        # 状态栏必须先于画布 pack。画布默认请求大约 265 高，又带 expand，
        # 排在后面时矮窗口会把底栏挤成 1 像素，看起来像消失。
        self.bar = tk.Label(root, bg=BG, fg='#888888', font=('Microsoft YaHei UI', 9), anchor='w')
        self.bar.pack(fill='x', side='bottom')
        self.cv = tk.Canvas(root, bg=BG, highlightthickness=0, width=1, height=1)
        self.cv.pack(fill='both', expand=True)
        self.toast = tk.Label(
            root, bg='#1e2430', fg='#e6edf7', font=('Microsoft YaHei UI', 22),
            padx=20, pady=10, bd=0, highlightthickness=1,
            highlightbackground='#3a4658',
        )
        for k in ('<Right>','<d>','<D>','<Next>','<space>'): root.bind(k, lambda e: self.step(1))
        for k in ('<Left>','<a>','<A>','<Prior>','<BackSpace>'): root.bind(k, lambda e: self.step(-1))
        root.bind('<Home>', self.first)
        root.bind('<End>', self.last)
        root.bind('<Escape>', self.esc)
        self.cv.bind('<Double-Button-1>', self.fs)
        root.bind('<MouseWheel>', self.wheel)
        for k in ('<slash>', '<KP_Divide>'):
            root.bind(k, self.toggle_fit)
        # Windows 把数字区 * 报成 asterisk（键码 106），不是 KP_Multiply。
        root.bind('<asterisk>', self.hug_window)
        root.bind('<KP_Multiply>', self.hug_window)
        root.bind('<Control-o>', lambda e: self.ask())
        self.cv.bind('<Configure>', self._on_canvas_configure)
        root.bind('<Configure>', self._on_root_configure)
        root.protocol('WM_DELETE_WINDOW', self.close)
        for w in (root, self.cv, self.title_bar, self.bar):
            w.bind('<ButtonPress-1>', self._press, add='+')
            w.bind('<B1-Motion>', self._motion, add='+')
            w.bind('<ButtonRelease-1>', self._release, add='+')
            w.bind('<Motion>', self._hover, add='+')
        self._place_window()
        self._taskbar()
        root.deiconify()
        root.update_idletasks()
        self._apply_icon()
        self.load_path(path)

    def _apply_icon(self):
        ico = os.path.join(app_root(), 'assets', 'star.ico')
        if not os.path.isfile(ico):
            return
        try:
            self.root.iconbitmap(ico)
        except Exception:
            pass
        try:
            user32 = ctypes.windll.user32
            hwnd = _hwnd(self.root)
            big = user32.LoadImageW(None, ico, 1, 32, 32, 0x10)
            small = user32.LoadImageW(None, ico, 1, 16, 16, 0x10)
            user32.SendMessageW(hwnd, 0x80, 1, big)
            user32.SendMessageW(hwnd, 0x80, 0, small)
        except Exception:
            pass
        try:
            from PIL import Image, ImageTk
            im = Image.open(ico)
            im = im.resize((16, 16), Image.LANCZOS)
            self._icon_img = ImageTk.PhotoImage(im)
        except Exception:
            self._icon_img = None
            return
        self._ensure_title_icon()

    def _ensure_title_icon(self):
        if self._icon_img is None or not hasattr(self, 'title_bar'):
            return
        lbl = getattr(self, '_icon_lbl', None)
        if lbl is None:
            lbl = tk.Label(self.title_bar, image=self._icon_img, bg=TITLE_BG, bd=0)
            lbl.place(x=max(10, self._snap_border + 2), rely=0.5, anchor='w')
            lbl.bind('<ButtonPress-1>', self._press)
            lbl.bind('<B1-Motion>', self._motion)
            lbl.bind('<Double-Button-1>', self._title_double)
            self._icon_lbl = lbl
        else:
            lbl.configure(image=self._icon_img)

    def _install_snap_frame(self):
        global _SNAP_VIEW
        hwnd = _hwnd(self.root)
        self._snap_hwnd = int(hwnd)
        _SNAP_VIEW = self
        old = _subclass(hwnd, _SNAP_PROC)
        if not old:
            raise OSError('snap wndproc')
        _SNAP[int(hwnd)] = (old, self)
        user32 = ctypes.windll.user32
        get_long = user32.GetWindowLongW
        set_long = user32.SetWindowLongW
        get_long.restype = ctypes.c_uint
        set_long.restype = ctypes.c_uint
        get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_uint]
        WS_CAPTION, WS_THICKFRAME = 0x00C00000, 0x00040000
        WS_MINIMIZEBOX, WS_MAXIMIZEBOX, WS_SYSMENU = 0x00020000, 0x00010000, 0x00080000
        WS_POPUP = 0x80000000
        style = get_long(hwnd, -16)
        style = (style | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU) & ~WS_POPUP
        set_long(hwnd, -16, style)
        self._tk_width_extra = _tk_width_extra(hwnd)
        # SWP_NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0037)
        try:
            margins = _MARGINS(0, 0, 0, 1)
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
        except Exception:
            pass

    def _hook_child(self, widget, kind):
        hwnd = int(widget.winfo_id())
        if not hwnd or hwnd in _CHILDREN or hwnd == int(self._snap_hwnd):
            return
        old = _subclass(hwnd, _CHILD_PROC)
        if old:
            _CHILDREN[hwnd] = (old, kind)

    def _hook_snap_children(self):
        self.root.update_idletasks()
        self._hook_child(self.title_bar, 'pass')
        self._hook_child(self.title_lbl, 'pass')
        icon = getattr(self, '_icon_lbl', None)
        if icon is not None:
            self._hook_child(icon, 'pass')
        self._hook_child(self._cap_btns[1], 'pass')
        self._hook_child(self.cv, 'edge')
        self._hook_child(self.bar, 'edge')

    def _snap_hit_at(self, lparam):
        x, y = _signed_xy(lparam)
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(self._snap_hwnd, ctypes.byref(rect))
        rel_x, rel_y = x - rect.left, y - rect.top
        w, h = rect.right - rect.left, rect.bottom - rect.top
        caption = self.title_bar.winfo_ismapped()
        zoomed = bool(self._max) or bool(ctypes.windll.user32.IsZoomed(self._snap_hwnd))
        return snap_hit(rel_x, rel_y, w, h, self._snap_border, TITLE_H, caption, zoomed, self._fullscreen)

    def _snap_max_hot(self, hot):
        btn = getattr(self, '_max_btn', None)
        if btn is None:
            return
        btn.configure(bg='#2a2a2a' if hot else TITLE_BG)

    def _sync_max_glyph(self):
        btn = getattr(self, '_max_btn', None)
        if btn is None:
            return
        zoomed = bool(self._max) and not self._fullscreen
        btn.configure(text='\uE923' if zoomed else '\uE922')

    def _after_sys_move(self):
        hwnd = int(self._snap_hwnd or 0)
        if not hwnd:
            return
        user32 = ctypes.windll.user32
        if not user32.IsWindow(hwnd):
            return
        # 拖动期间过程换回去过，这里再按无标题栏算一次客户区。
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0037)
        if self._fullscreen:
            return
        self._max = bool(user32.IsZoomed(hwnd))
        self._sync_max_glyph()

    def _build_title(self):
        bar = tk.Frame(self.root, bg=TITLE_BG, height=TITLE_H)
        bar.pack(side='top', fill='x')
        bar.pack_propagate(False)
        self.title_bar = bar
        self._ensure_title_icon()
        self._title_font = tkfont.Font(family='Microsoft YaHei UI', size=10)
        lbl = tk.Label(
            bar, text=TITLE, bg=TITLE_BG, fg=TITLE_FG, font=self._title_font,
            anchor='w', bd=0, padx=0, pady=0, highlightthickness=0,
        )
        self.title_lbl = lbl
        self._layout_title(TITLE)
        lbl.bind('<ButtonPress-1>', self._press)
        lbl.bind('<B1-Motion>', self._motion)
        lbl.bind('<Double-Button-1>', self._title_double)
        bar.bind('<Double-Button-1>', self._title_double)
        bar.bind('<Configure>', self._on_title_resize)
        specs = (
            ('\uE921', self.minimize, '#2a2a2a'),
            ('\uE922', self.toggle_max, '#2a2a2a'),
            ('\uE8BB', self.close, '#c42b1c'),
        )
        self._cap_btns = []
        for i, (glyph, cmd, hover) in enumerate(specs):
            b = tk.Label(bar, text=glyph, bg=TITLE_BG, fg=TITLE_FG, font=('Segoe MDL2 Assets', 10))
            b.place(relx=1, x=-(self._snap_border + CAP_W * (len(specs) - i)), y=0, width=CAP_W, height=TITLE_H)
            b.bind('<Button-1>', lambda e, c=cmd: (c(), 'break')[1])
            b.bind('<Enter>', lambda e, w=b, h=hover: w.configure(bg=h))
            b.bind('<Leave>', lambda e, w=b: w.configure(bg=TITLE_BG))
            self._cap_btns.append(b)
        self._max_btn = self._cap_btns[1]

    def _set_title(self, name):
        self._cur_name = name or ''
        self.root.title(name or TITLE)
        self._refresh_title()

    def _title_span(self, bar_w):
        """文件名从图标右侧排起，右边在按钮前截断。"""
        icon_x = max(10, self._snap_border + 2)
        left = icon_x + 16 + 8
        right = self._snap_border + CAP_W * 3 + 8
        avail = int(bar_w) - left - right
        return left, max(1, avail)

    def _layout_title(self, text):
        lbl = getattr(self, 'title_lbl', None)
        bar = getattr(self, 'title_bar', None)
        if lbl is None or bar is None:
            return
        bar_w = bar.winfo_width()
        if bar_w <= 1:
            return
        left, avail = self._title_span(bar_w)
        font = getattr(self, '_title_font', None)
        if font is not None and font.measure(text) > avail:
            while len(text) > 1 and font.measure(text + '…') > avail:
                text = text[:-1]
            text += '…'
        if lbl.cget('text') != text:
            lbl.config(text=text)
        # 左端钉在图标旁边。窗口变宽变窄时，文件名不再跟着居中点左右跳。
        lbl.place_configure(x=left, y=0, width=avail, height=TITLE_H, anchor='nw', relx=0, rely=0)

    def _on_title_resize(self, e=None):
        self._refresh_title()

    def _refresh_title(self):
        self._layout_title(getattr(self, '_cur_name', '') or TITLE)

    def _place_window(self):
        saved = load_win()
        self.root.update_idletasks()
        vx, vy = self.root.winfo_vrootx(), self.root.winfo_vrooty()
        vw, vh = max(self.root.winfo_vrootwidth(), 800), max(self.root.winfo_vrootheight(), 600)
        if saved:
            x, y, w, h = saved
            w, h = max(200, min(w, vw)), max(160, min(h, vh))
            if x + 80 > vx + vw or x + w < vx + 80 or y + 40 > vy + vh or y + h < vy + 40:
                x, y = vx + max(0, (vw - w) // 2), vy + max(0, (vh - h) // 2)
        else:
            w, h = min(1200, vw), min(800, vh)
            x, y = vx + max(0, (vw - w) // 2), vy + max(0, (vh - h) // 2)
        self._normal = (x, y, w, h)
        self._set_outer(w, h, x, y)

    def _taskbar(self):
        try:
            hwnd = _hwnd(self.root)
            GWL_EXSTYLE = -20
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style | 0x00040000) & ~0x00000080
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception:
            pass

    def _on_root_configure(self, e):
        if e.widget is not self.root or self._fullscreen:
            return
        if self._snap_hwnd:
            zoomed = bool(ctypes.windll.user32.IsZoomed(self._snap_hwnd))
            if zoomed != bool(self._max):
                self._max = zoomed
                self._sync_max_glyph()
            if zoomed:
                return
        elif self._max:
            return
        if e.width < 200 or e.height < 160:
            return
        self._normal = (e.x, e.y, e.width, e.height)
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(400, self._save_normal)

    def _save_normal(self):
        self._save_job = None
        if not getattr(self, '_normal', None):
            return
        save_win(*self._normal)

    def _work_area(self):
        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ('cbSize', wintypes.DWORD),
                ('rcMonitor', wintypes.RECT),
                ('rcWork', wintypes.RECT),
                ('dwFlags', wintypes.DWORD),
            ]
        user32 = ctypes.windll.user32
        user32.MonitorFromWindow.restype = ctypes.c_void_p
        hwnd = _hwnd(self.root)
        hmon = user32.MonitorFromWindow(hwnd, 2)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        r = info.rcWork
        return r.left, r.top, r.right - r.left, r.bottom - r.top

    def minimize(self):
        self.root.iconify()

    def _after_restore(self, e):
        if e.widget is not self.root or self._fullscreen:
            return
        self.root.unbind('<Map>')
        self._taskbar()
        self._apply_icon()

    def toggle_max(self):
        if self._fullscreen:
            return
        if self._max or self.root.state() == 'zoomed':
            self.root.state('normal')
        else:
            self._save_normal()
            self.root.state('zoomed')

    def _title_double(self, e):
        self._pending_move = None
        self.toggle_max()
        return 'break'

    def _edge(self, e):
        if self._fullscreen or self._max:
            return None
        x = e.x_root - self.root.winfo_rootx()
        y = e.y_root - self.root.winfo_rooty()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        m = self._snap_border if self._snap_hwnd else 6
        return resize_zone(x, y, w, h, m)

    def _is_caption(self, widget):
        if widget in (self.title_bar, self.title_lbl):
            return True
        return getattr(widget, 'master', None) is self.title_bar and widget not in self._cap_btns

    def _sys_command(self, wparam, x_root, y_root):
        """等这次鼠标回调返回后再拖。在窗口过程里进系统模态循环会把 pythonw 打崩。"""
        ctypes.windll.user32.ReleaseCapture()
        lp = ((int(y_root) & 0xFFFF) << 16) | (int(x_root) & 0xFFFF)
        self._sys_pending = (int(wparam), lp)
        try:
            self.root.after(0, self._run_sys_command)
        except tk.TclError:
            self._sys_pending = None

    def _run_sys_command(self):
        pending = self._sys_pending
        self._sys_pending = None
        hwnd = int(self._snap_hwnd or 0)
        item = _SNAP.get(hwnd) if hwnd else None
        if not pending or not item:
            return
        wparam, lp = pending
        if (int(wparam) & 0xFFF0) == 0xF000:
            return
        self._snap_modal = True
        _subclass(hwnd, _THIN_PROC)
        try:
            ctypes.windll.user32.ReleaseCapture()
            _def_window_proc(hwnd, 0x0112, int(wparam), lp)
        finally:
            if ctypes.windll.user32.IsWindow(hwnd):
                _subclass(hwnd, _SNAP_PROC)
            self._snap_modal = False
        self._after_sys_move()

    def _can_pan(self):
        try:
            vw, vh = self.cv.winfo_width(), self.cv.winfo_height()
        except tk.TclError:
            return False
        return self._disp_w > vw + 1 or self._disp_h > vh + 1

    def _press(self, e):
        if e.widget is self.cv and self._can_pan() and not self._edge(e):
            self._pending_move = None
            self._rz = None
            self._drag = None
            self._pan = (e.x_root, e.y_root, self._ox, self._oy)
            try:
                self.cv.configure(cursor='fleur')
            except tk.TclError:
                pass
            return 'break'
        if self._fullscreen:
            return None
        edge = self._edge(e)
        if edge:
            # 拉边继续改 geometry。系统 SC_SIZE 的模态循环里一进 Configure 就会把 pythonw 打崩。
            x, y, w, h = self._normal
            self._pending_move = None
            self._rz = (edge, e.x_root, e.y_root, x, y, w, h)
            self._drag = None
            return 'break'
        if self._is_caption(e.widget):
            if self._snap_hwnd:
                self._pending_move = (e.x_root, e.y_root)
                return None
            if not self._max:
                self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())
        return None

    def _motion(self, e):
        pending = self._pending_move
        if pending and self._snap_hwnd:
            x0, y0 = pending
            if abs(e.x_root - x0) + abs(e.y_root - y0) >= 4:
                self._pending_move = None
                self._sys_command(0xF012, e.x_root, e.y_root)
                return 'break'
        if self._pan:
            x0, y0, ox, oy = self._pan
            w = max(self.cv.winfo_width(), 1)
            h = max(self.cv.winfo_height(), 1)
            self._ox, self._oy = clamp_pan(
                ox + (e.x_root - x0), oy + (e.y_root - y0),
                w, h, self._disp_w, self._disp_h)
            if self._img_id is not None:
                self.cv.coords(self._img_id, w / 2 + self._ox, h / 2 + self._oy)
            return
        if self._rz:
            edge, x0, y0, ox, oy, ow, oh = self._rz
            dx, dy = e.x_root - x0, e.y_root - y0
            x, y, w, h = ox, oy, ow, oh
            if 'e' in edge:
                w = max(MIN_W, ow + dx)
            if 's' in edge:
                h = max(MIN_H, oh + dy)
            if 'w' in edge:
                w = max(MIN_W, ow - dx)
                x = ox + ow - w
            if 'n' in edge:
                h = max(MIN_H, oh - dy)
                y = oy + oh - h
            self._set_outer(w, h, x, y)
            return
        if self._drag and not self._max:
            self.root.geometry(f'+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}')

    def _release(self, e):
        self._rz = None
        self._drag = None
        self._pan = None
        self._pending_move = None

    def _hover(self, e):
        if self._rz:
            return
        edge = self._edge(e)
        if not edge and e.widget is self.cv and self._can_pan():
            try:
                e.widget.configure(cursor='fleur')
            except tk.TclError:
                pass
            return
        cursors = {
            'n': 'top_side', 's': 'bottom_side', 'w': 'left_side', 'e': 'right_side',
            'nw': 'top_left_corner', 'ne': 'top_right_corner',
            'sw': 'bottom_left_corner', 'se': 'bottom_right_corner',
        }
        try:
            e.widget.configure(cursor=cursors.get(edge, ''))
        except Exception:
            pass

    def close(self):
        self._save_normal()
        self.root.destroy()

    def ask(self):
        p = filedialog.askopenfilename(title='打开图片', filetypes=image_filetypes())
        if p: self.load_path(p)

    def _refresh_cfg(self):
        self.exts, self.pierce, self.lock_scale, self.auto_window, auto_fit = load_cfg()
        # 只在总览里的勾选变了时才覆盖。/ 在这一窗里开关自适应，翻页不会被旧勾选冲掉。
        if auto_fit != self._fit_pref:
            self.fit_mode = bool(auto_fit)
            self._fit_pref = bool(auto_fit)

    def _list_images(self, folder):
        try:
            names = [n for n in os.listdir(folder) if os.path.splitext(n)[1].lower() in self.exts and os.path.isfile(os.path.join(folder, n))]
        except OSError:
            names = []
        names.sort(key=nkey)
        return [os.path.join(folder, n) for n in names]

    def load_path(self, path):
        if not path: return
        self._refresh_cfg()
        path = os.path.abspath(path); folder = os.path.dirname(path)
        self.files = self._list_images(folder) or [path]
        self.idx = next((i for i, f in enumerate(self.files) if os.path.normcase(f) == os.path.normcase(path)), 0)
        self.cache.clear(); self._alpha.clear(); self.scale_mode, self.zoom = self._page_view(); self._present(); self.root.after(50, self.preload)

    def _show_folder_toast(self, folder):
        try:
            name = os.path.basename(os.path.normpath(folder)) or str(folder)
            if len(name) > 48:
                name = name[:45] + '...'
            self.toast.config(text=name)
            self.toast.place(relx=0.5, rely=0.5, anchor='center')
            self.toast.lift()
            if self._toast_job is not None:
                try:
                    self.root.after_cancel(self._toast_job)
                except Exception:
                    pass
            self._toast_job = self.root.after(1000, self._hide_folder_toast)
        except Exception:
            pass

    def _hide_folder_toast(self):
        self._toast_job = None
        try:
            self.toast.place_forget()
        except Exception:
            pass

    def _enter_folder(self, folder, last):
        files = self._list_images(folder)
        if not files: return False
        self.files = files
        self.idx = len(files) - 1 if last else 0
        self.cache.clear(); self._alpha.clear(); self.scale_mode, self.zoom = self._page_view(); self._present(); self.root.after(50, self.preload)
        self._show_folder_toast(folder)
        return True

    def open_img(self, p):
        with self.lock:
            im = self.cache.get(p)
        if im is not None: return im
        from PIL import Image, ImageOps
        im = Image.open(p)
        im = ImageOps.exif_transpose(im)
        if im.mode not in ('RGB', 'RGBA'): im = im.convert('RGBA' if 'A' in im.getbands() or 'transparency' in im.info else 'RGB')
        im.load()
        with self.lock:
            self.cache[p] = im
            if len(self.cache) > 5:
                keep = {self.files[(self.idx + d) % len(self.files)] for d in (-1, 0, 1)}
                for k in [k for k in self.cache if k not in keep][:len(self.cache) - 5]: self.cache.pop(k, None)
        return im

    def preload(self):
        if len(self.files) < 2: return
        ps = [self.files[(self.idx + d) % len(self.files)] for d in (1, -1)]
        def run():
            for p in ps:
                try: self.open_img(p)
                except Exception: pass
        threading.Thread(target=run, daemon=True).start()

    def _set_status(self, text):
        self.bar.config(text=text)

    def _on_canvas_configure(self, e=None):
        if self._suppress_show:
            return
        self.show()

    def _present(self):
        """翻页时先把窗口改到最终尺寸，再画一次。中途不擦掉上一张。"""
        shown = bool(self._hug_fit())
        if self._follow_image():
            shown = True
        if not shown:
            self.show()

    def show(self):
        if not self.files or self._suppress_show:
            return
        w, h = max(self.cv.winfo_width(), 1), max(self.cv.winfo_height(), 1)
        p = self.files[self.idx]
        self._set_title(os.path.basename(p))
        try: im = self.open_img(p)
        except Exception:
            self.cv.delete('all')
            self._img_id = None
            self._disp_w = self._disp_h = 0
            self.cv.create_text(w//2, h//2, text='无法打开: ' + os.path.basename(p), fill='#cc6666', font=('Microsoft YaHei UI', 12))
            self._set_status(f'{self.idx+1}/{len(self.files)}')
            return
        from PIL import Image, ImageTk
        work_x, work_y, work_w, work_h = self._work_area()
        s = display_scale(im.width, im.height, w, h, self.scale_mode, self.zoom, work_w, max(1, work_h - self._chrome_h()))
        self._shown_scale = s
        nw, nh = max(1, int(im.width * s)), max(1, int(im.height * s))
        if p != self._pan_for:
            self._ox = self._oy = 0.0
            self._pan_for = p
        self._disp_w, self._disp_h = nw, nh
        self._ox, self._oy = clamp_pan(self._ox, self._oy, w, h, nw, nh)
        if (nw, nh) == im.size:
            disp = im
        else:
            resample = Image.LANCZOS if s < 1 else Image.BICUBIC
            disp = im.resize((nw, nh), resample, reducing_gap=2.0 if s < 0.5 else None)
        if self._has_transparency(p, im):
            disp = paint_transparency(disp.convert('RGBA') if disp.mode != 'RGBA' else disp)
        # 新图准备好再换，避免先擦成黑底再画上一闪。
        self.tkimg = ImageTk.PhotoImage(disp)
        self.cv.delete('all')
        self._img_id = self.cv.create_image(w / 2 + self._ox, h / 2 + self._oy, image=self.tkimg)
        tag = '  适应' if self.fit_mode else ''
        self._set_status(f'{self.idx+1}/{len(self.files)}  {im.width}×{im.height}  {int(round(s*100))}%{tag}')

    def _has_transparency(self, path, im):
        flag = self._alpha.get(path)
        if flag is None:
            flag = has_transparency(im)
            self._alpha[path] = flag
        return flag

    def _page_view(self):
        if self.lock_scale:
            # 适应/铺满/1:1 是独立状态，翻页时保持；仅手动缩放按数值锁定比例。
            if self.scale_mode in ('fit', 'fill', 'actual'):
                return self.scale_mode, self.zoom
            return 'manual', max(0.05, min(float(self._shown_scale or 1.0), 40.0))
        if not self.fit_mode:
            return 'actual', 1.0
        return 'fit', 1.0

    def first(self, e=None):
        if not self.files: return
        self._refresh_cfg()
        self.idx = 0
        self.scale_mode, self.zoom = self._page_view()
        self._present(); self.preload()

    def last(self, e=None):
        if not self.files: return
        self._refresh_cfg()
        self.idx = len(self.files) - 1
        self.scale_mode, self.zoom = self._page_view()
        self._present(); self.preload()

    def step(self, d):
        if not self.files: return
        self._refresh_cfg()
        nxt = self.idx + d
        if 0 <= nxt < len(self.files):
            self.idx = nxt; self.scale_mode, self.zoom = self._page_view(); self._present(); self.preload(); return
        if self.pierce != 'next':
            if len(self.files) < 2: return
            self.idx = nxt % len(self.files); self.scale_mode, self.zoom = self._page_view(); self._present(); self.preload(); return
        folder = os.path.dirname(self.files[self.idx])
        try:
            root = app_root()
            if root not in sys.path: sys.path.insert(0, root)
            from utils.photo_viewer_cfg import neighbor_folder
            target = neighbor_folder(folder, d, self.exts)
        except Exception:
            target = None
        if target: self._enter_folder(target, last=(d < 0))

    def setzoom(self, z):
        self.scale_mode = 'manual'
        self.zoom = max(0.05, min(z, 40))
        self.show()
    def wheel(self, e):
        self.setzoom(self._shown_scale * (1.15 if e.delta > 0 else 1/1.15))
    def actual_size(self, e=None):
        self.scale_mode = 'actual'
        self.show()
        return 'break'
    def fill_ratio(self, e=None):
        self.scale_mode = 'fill'
        self.show()
        return 'break'
    def toggle_fit(self, e=None):
        """/ 开关适应。开：大图缩小、小图放大到工作区，并贴合窗口。关：100%，窗口大小不动。"""
        self.fit_mode = not self.fit_mode
        if self.fit_mode:
            self.scale_mode = 'fit'
            self.zoom = 1.0
        else:
            self.scale_mode = 'actual'
        # 记在本窗里，并写回总览的「自适应」。翻页时只在总览勾选再变时才覆盖。
        self._fit_pref = bool(self.fit_mode)
        try:
            root = app_root()
            if root not in sys.path:
                sys.path.insert(0, root)
            from utils.photo_viewer_cfg import write_auto_fit
            write_auto_fit(self.fit_mode)
        except Exception:
            pass
        self._present()
        return 'break'

    def _chrome_h(self):
        # 用请求高度（reqheight）而非当前高度：窗口被压缩到很短时，状态栏当前高度会变成 0，
        # 导致 chrome 被低估、窗口越缩越短、状态栏彻底消失。请求高度不受挤压影响。
        if self._fullscreen:
            return 0
        return self.title_bar.winfo_reqheight() + self.bar.winfo_reqheight()

    def _hug_fit(self):
        """适应模式：把窗口调整到以桌面为基准，让整张图按桌面适配比例完整显示。"""
        if self.scale_mode != 'fit' or not self.files:
            return False
        if self._fullscreen or self._max or self.root.state() == 'zoomed':
            return False
        try:
            im = self.open_img(self.files[self.idx])
        except Exception:
            return False
        self.root.update_idletasks()
        chrome = self._chrome_h()
        if chrome < TITLE_H:
            chrome = TITLE_H + 22
        work_x, work_y, work_w, work_h = self._work_area()
        s = min(work_w / im.width, max(1, work_h - chrome) / im.height)
        new_w, new_h, new_s = hug_window_geometry(im.width, im.height, s, chrome, work_w, work_h)
        cur_w, cur_h = self.root.winfo_width(), self.root.winfo_height()
        if new_w == cur_w and new_h == cur_h:
            return False
        x, y = hug_origin(
            self.root.winfo_x(), self.root.winfo_y(),
            cur_w, cur_h, new_w, new_h, work_x, work_y, work_w, work_h)
        self._apply_hug(im, x, y, new_w, new_h, new_s, work_x, work_y, work_w, work_h)
        return True

    def _follow_image(self):
        """勾选「窗口随图片」后，按当前显示比例把窗口贴到这张图上。

        和按 * 一样去掉黑边。全屏或最大化时不动，避免翻页把窗口拉回来。
        比例不用改时保留适应或 100%；只有图片大于屏幕、必须缩小才改成手动比例。
        """
        if not self.auto_window or not self.files:
            return False
        if self._fullscreen or self._max or self.root.state() == 'zoomed':
            return False
        try:
            im = self.open_img(self.files[self.idx])
        except Exception:
            return False
        self.root.update_idletasks()
        chrome = self._chrome_h()
        if chrome < TITLE_H:
            chrome = TITLE_H + 22
        work_x, work_y, work_w, work_h = self._work_area()
        shown = float(self._shown_scale or 1.0)
        new_w, new_h, new_s = hug_window_geometry(
            im.width, im.height, shown, chrome, work_w, work_h)
        cur_w, cur_h = self.root.winfo_width(), self.root.winfo_height()
        if new_w == cur_w and new_h == cur_h and abs(new_s - shown) < 1e-6:
            return False
        # 适应/100% 是状态。贴窗口只改尺寸，不能改成手动比例，否则一翻页「适应」就没了，
        # 锁定比例还会把这个手动比例带到后面每一张。
        if abs(new_s - shown) > 1e-6 and self.scale_mode not in ('fit', 'fill'):
            self.scale_mode = 'manual'
            self.zoom = new_s
        x, y = hug_origin(
            self.root.winfo_x(), self.root.winfo_y(),
            cur_w, cur_h, new_w, new_h, work_x, work_y, work_w, work_h)
        self._apply_hug(im, x, y, new_w, new_h, new_s, work_x, work_y, work_w, work_h)
        return True

    def hug_window(self, e=None):
        if not self.files:
            return 'break'
        if self._fullscreen:
            self.fs()
        if self._max or self.root.state() == 'zoomed':
            self.root.state('normal')
            self._max = False
            self._sync_max_glyph()
        try:
            im = self.open_img(self.files[self.idx])
        except Exception:
            return 'break'
        self.root.update_idletasks()
        chrome = self._chrome_h()
        if chrome < TITLE_H:
            chrome = TITLE_H + 22
        work_x, work_y, work_w, work_h = self._work_area()
        new_w, new_h, new_s = hug_window_geometry(
            im.width, im.height, self._shown_scale or 1.0, chrome, work_w, work_h)
        cur_w, cur_h = self.root.winfo_width(), self.root.winfo_height()
        if new_w == cur_w and new_h == cur_h:
            return 'break'
        x, y = hug_origin(
            self.root.winfo_x(), self.root.winfo_y(),
            cur_w, cur_h,
            new_w, new_h, work_x, work_y, work_w, work_h)
        self.scale_mode = 'manual'
        self.zoom = new_s
        self._apply_hug(im, x, y, new_w, new_h, new_s, work_x, work_y, work_w, work_h)
        return 'break'

    def _set_outer(self, w, h, x, y):
        """外框用这个尺寸。先减去 Tk 多加的左右边框，否则窗口会往右变宽。

        系统最小尺寸跟这里走。再小就抓不住边，下次也存不回这个窗口。
        """
        w, h = max(1, int(w)), max(1, int(h))
        self._track_w = MIN_W
        self._track_h = MIN_H
        extra = self._tk_width_extra if self._snap_hwnd else 0
        tw = max(1, w - int(extra))
        self.root.geometry(f'{tw}x{h}+{int(x)}+{int(y)}')

    def _apply_hug(self, im, x, y, new_w, new_h, new_s, work_x, work_y, work_w, work_h):
        self._normal = (x, y, new_w, new_h)
        self._suppress_show = True
        try:
            self._set_outer(new_w, new_h, x, y)
            self.root.update_idletasks()
            self.root.update()
            drawn_w, drawn_h = _drawn_size(im.width, im.height, new_s)
            dw = drawn_w - max(self.cv.winfo_width(), 1)
            dh = drawn_h - max(self.cv.winfo_height(), 1)
            # 底栏被挤扁时画布会高出图片一截。这个差额不能拿去缩短窗口，否则底栏再也回不来。
            if not self._fullscreen and self.bar.winfo_ismapped():
                dh += max(0, self.bar.winfo_reqheight() - self.bar.winfo_height())
            if (dw or dh) and abs(dw) <= 120 and abs(dh) <= 120:
                new_w = max(1, new_w + dw)
                new_h = max(1, new_h + dh)
                x = min(max(int(x), int(work_x)), int(work_x + work_w - new_w))
                y = min(max(y, int(work_y)), int(work_y + work_h - new_h))
                self._normal = (x, y, new_w, new_h)
                self._set_outer(new_w, new_h, x, y)
                self.root.update_idletasks()
            self._suppress_show = False
            self.show()
            self.root.update_idletasks()
        finally:
            self._suppress_show = False
        self._save_normal()

    def fs(self, e=None):
        self._fullscreen = not self._fullscreen
        if self._fullscreen:
            self._save_normal()
            if self._max or self.root.state() == 'zoomed':
                self.root.state('normal')
                self._max = False
            self.root.attributes('-fullscreen', True)
            self.title_bar.pack_forget()
            self.bar.pack_forget()
        else:
            self.root.attributes('-fullscreen', False)
            self.title_bar.pack(side='top', fill='x', before=self.cv)
            self.bar.pack(fill='x', side='bottom', before=self.cv)
            self._taskbar()
            self._apply_icon()
        return 'break'
    def esc(self, e=None):
        if self._fullscreen: self.fs()
        else: self.close()

def main():
    root = tk.Tk()
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        root.withdraw()
        path = filedialog.askopenfilename(title='打开图片', filetypes=image_filetypes())
        if not path: return
        root.deiconify()
    v = Viewer(root, path)
    if os.environ.get('PV_TEST'):
        def _finish():
            v._save_normal()
            root.destroy()
        root.after(int(os.environ['PV_TEST']), _finish)
    root.focus_force(); root.mainloop()

if __name__ == '__main__': main()
