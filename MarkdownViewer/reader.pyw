# -*- coding: utf-8 -*-
"""文件阅读。Markdown 按原样排版，Word 与 PDF 抽出文字。Ctrl+左右 切换同目录同一种文件，F11 全屏，Esc 退出全屏或关闭，Ctrl+O 打开。"""
import ctypes
import json
import os
import re
import sys
import webbrowser
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes
from tkinter import filedialog

MD_EXTS = {'.md', '.markdown', '.mdown'}
WORD_EXTS = {'.doc', '.docx'}
PDF_EXTS = {'.pdf'}
EXTS = MD_EXTS | WORD_EXTS | PDF_EXTS
_OPEN_TYPES = [
    ('文件', '*.md *.markdown *.mdown *.docx *.doc *.pdf'),
    ('Markdown', '*.md *.markdown *.mdown'),
    ('Word', '*.docx *.doc'),
    ('PDF', '*.pdf'),
    ('所有文件', '*.*'),
]
BG = '#111111'
FG = '#e6e6e6'
TITLE_BG = '#000000'
TITLE_FG = '#ffffff'
TITLE = '桌面助手 v9.18·文件阅读器'
TITLE_H = 32
MIN_W, MIN_H = 640, 400
# 与主程序滚动条同色。形状是两端半圆的胶囊，不是方条。
PILL = '#f0a542'
PILL_HOVER = '#f7bb64'
PILL_PRESS = '#dd932f'
PILL_MIN = 48
PILL_W = 16
PILL_GAP = 8
# 正文比例。点工具栏上的数字可直接选一档。
ZOOM_STEPS = (75, 90, 100, 110, 125, 150, 175, 200)
# 工具栏色值对齐主程序暗/亮主题和 kind=mini 按钮
_TOOL = {
    'dark': {
        'bar': '#141b33',
        'line': '#25345c',
        'btn': '#22283f',
        'btn_hover': '#2c3247',
        'btn_press': '#353b50',
        'btn_border': '#767b89',
        'fg': '#cfe0ff',
        'fg_dis': '#6f7fa8',
        'border_dis': '#2a3965',
        'btn_dis': '#1a2138',
    },
    'light': {
        'bar': '#ffffff',
        'line': '#e2e8f0',
        'btn': '#f8fafc',
        'btn_hover': '#e9ebed',
        'btn_press': '#dfe1e3',
        'btn_border': '#94a3b8',
        'fg': '#334155',
        'fg_dis': '#94a3b8',
        'border_dis': '#e2e8f0',
        'btn_dis': '#f1f5f9',
    },
}
_NAV = {
    'Left', 'Right', 'Up', 'Down', 'Prior', 'Next', 'Home', 'End',
    'Shift_L', 'Shift_R', 'Control_L', 'Control_R', 'Alt_L', 'Alt_R',
    'Tab', 'ISO_Left_Tab', 'Escape', 'F11',
}
_INLINE = re.compile(
    r'!\[([^\]]*)\]\(([^)\s]+)\)'
    r'|\[([^\]]+)\]\(([^)\s]+)\)'
    r'|`([^`]+)`'
    r'|\*\*([^*]+)\*\*'
    r'|__([^_]+)__'
    r'|(?<!\*)\*([^*\n]+)\*(?!\*)'
    r'|_(?!_)([^_\n]+)_(?!_)'
)

try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('DesktopAssistant.MarkdownReader')
except Exception:
    pass
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass


def app_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_ui_theme():
    """主程序当前主题。阅读器工具栏跟这一项走。"""
    path = os.path.join(app_root(), 'userdata', 'user.txt')
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        name = str((data.get('ui') or {}).get('theme') or '')
        if name in _TOOL:
            return name
    except Exception:
        pass
    return 'dark'


def _stadium(canvas, x, y, w, h, color):
    """两端半圆、中间等宽的胶囊。"""
    x, y, w, h = int(x), int(y), int(w), int(h)
    if h <= w:
        canvas.create_oval(x, y, x + w, y + max(h, w), fill=color, outline='', tags='pill')
        return
    r = w / 2.0
    canvas.create_rectangle(x, y + r - 1, x + w, y + h - r + 1, fill=color, outline='', tags='pill')
    canvas.create_oval(x, y, x + w, y + w, fill=color, outline='', tags='pill')
    canvas.create_oval(x, y + h - w, x + w, y + h, fill=color, outline='', tags='pill')


class _IconButton(tk.Canvas):
    """工具栏图标。平时是主题色字形，指上去变成橙色。"""

    def __init__(self, master, glyph, command):
        self._glyph = glyph
        self._command = command
        self._enabled = True
        self._hot = False
        self._down = False
        self._font = tkfont.Font(family='Segoe MDL2 Assets', size=15)
        super().__init__(
            master, width=28, height=28, highlightthickness=0, bd=0, cursor='hand2',
        )
        self._pal = _TOOL['dark']
        self.bind('<ButtonPress-1>', self._press)
        self.bind('<ButtonRelease-1>', self._release)
        self.bind('<Enter>', self._enter)
        self.bind('<Leave>', self._leave)
        self.paint()

    def set_palette(self, pal):
        self._pal = pal
        self.paint()

    def set_enabled(self, on):
        self._enabled = bool(on)
        self.configure(cursor='hand2' if self._enabled else 'arrow')
        self.paint()

    def paint(self):
        self.delete('all')
        pal = self._pal
        self.configure(bg=pal['bar'])
        if not self._enabled:
            fg = pal['fg_dis']
        elif self._down:
            fg = PILL_PRESS
        elif self._hot:
            fg = PILL
        else:
            fg = pal['fg']
        self.create_text(14, 14, text=self._glyph, fill=fg, font=self._font)

    def _press(self, e):
        if not self._enabled:
            return 'break'
        self._down = True
        self.paint()
        return 'break'

    def _release(self, e):
        was = self._down
        self._down = False
        inside = 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height()
        self.paint()
        if was and inside and self._enabled and self._command:
            self._command()
        return 'break'

    def _enter(self, e):
        self._hot = True
        self.paint()

    def _leave(self, e):
        self._hot = False
        self._down = False
        self.paint()


def nkey(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]


def _hwnd(widget):
    widget.update_idletasks()
    wid = int(widget.winfo_id())
    parent = ctypes.windll.user32.GetParent(wid)
    return parent or wid


# 普通窗口才能用屏幕顶部的贴靠布局。系统标题栏用 WM_NCCALCSIZE 藏起来，黑标题栏仍自己画。
WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_GETMINMAXINFO = 0x0024
WM_NCDESTROY = 0x0082
HTCAPTION = 2
HTMAXBUTTON = 9
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17
CAP_W = 46
_SNAP = {}

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
    """和系统拉边一样宽。按钮和胶囊都往左让出这一条。"""
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

def snap_hit(rel_x, rel_y, w, h, border, title_h, caption_on, zoomed, fullscreen):
    """返回系统命中码。None 表示交给原来的窗口过程。"""
    if fullscreen or w <= 0 or h <= 0:
        return None
    b = max(int(border), 1)
    if not zoomed:
        left, right = rel_x < b, rel_x >= w - b
        top, bottom = rel_y < b, rel_y >= h - b
        # 最上沿留给标题栏：左上、上边、右上不拉窗口。
        if not top or bottom:
            if bottom and left:
                return HTBOTTOMLEFT
            if bottom and right:
                return HTBOTTOMRIGHT
            if left:
                return HTLEFT
            if right:
                return HTRIGHT
            if bottom:
                return HTBOTTOM
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
            info.ptMinTrackSize.x = MIN_W
            info.ptMinTrackSize.y = MIN_H
            return 0
        if msg == 0x0047 and not view._fullscreen and not view._snap_modal:
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

def _thin_snap_proc(hwnd, msg, wparam, lparam):
    """系统拖动时只用这个过程。不能把消息交回 Tk，否则一进 Python 就退出。"""
    if msg == WM_NCCALCSIZE and wparam:
        return 0
    if msg == WM_GETMINMAXINFO:
        info = ctypes.cast(lparam, ctypes.POINTER(_MINMAXINFO)).contents
        info.ptMinTrackSize.x = MIN_W
        info.ptMinTrackSize.y = MIN_H
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


def _win_file():
    return os.path.join(app_root(), 'userdata', 'md_reader_win.txt')


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


def _file_kind(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in WORD_EXTS:
        return 'word'
    if ext in PDF_EXTS:
        return 'pdf'
    if ext in MD_EXTS:
        return 'md'
    return ''


def read_office(path):
    """抽出 PDF / Word 的文字。排版留在原文里，这里只按页往下读。"""
    ext = os.path.splitext(path)[1].lower()
    try:
        import pymupdf
    except Exception as exc:
        raise RuntimeError('没有可用的阅读组件') from exc
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        if ext == '.doc':
            raise RuntimeError('旧版 .doc 读不出文字，请另存为 .docx') from exc
        raise
    try:
        if doc.needs_pass:
            raise RuntimeError('这份文件有密码，打不开')
        parts = []
        n = doc.page_count
        for i, page in enumerate(doc):
            chunk = (page.get_text('text') or '').strip()
            if n > 1:
                body = chunk or '（这一页没有可复制的文字）'
                parts.append('第 %d 页\n%s' % (i + 1, body))
            else:
                parts.append(chunk or '（这一份没有可复制的文字）')
        return '\n\n'.join(parts)
    finally:
        doc.close()


def read_text(path):
    with open(path, 'rb') as f:
        data = f.read()
    if data.startswith(b'\xef\xbb\xbf'):
        return data[3:].decode('utf-8')
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('gbk', errors='replace')


def _starts_block(line):
    s = line.lstrip()
    if s.startswith('```') or s.startswith('#'):
        return True
    if line.startswith('>'):
        return True
    if re.match(r'^ {0,3}(---|\*\*\*|___)\s*$', line):
        return True
    if re.match(r'^\s*([-*+]|\d+[.)])\s+', line):
        return True
    return False


def _is_table_sep(line):
    return bool(re.match(r'^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$', line))


def parse_blocks(src):
    """把 Markdown 拆成块。阅读器只认常用写法。"""
    lines = src.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    i, n = 0, len(lines)
    out = []
    while i < n:
        line = lines[i]
        if line.strip().startswith('```'):
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith('```'):
                buf.append(lines[i])
                i += 1
            if i < n:
                i += 1
            out.append(('code', '\n'.join(buf)))
            continue
        if re.match(r'^ {0,3}(---|\*\*\*|___)\s*$', line):
            out.append(('hr',))
            i += 1
            continue
        m = re.match(r'^(#{1,6})\s+(.*?)\s*#*\s*$', line)
        if m:
            out.append(('h', len(m.group(1)), m.group(2).strip()))
            i += 1
            continue
        if line.startswith('>'):
            buf = []
            while i < n and lines[i].startswith('>'):
                buf.append(re.sub(r'^>\s?', '', lines[i]))
                i += 1
            out.append(('quote', '\n'.join(buf)))
            continue
        if '|' in line and i + 1 < n and _is_table_sep(lines[i + 1]):
            rows = [_split_row(line)]
            i += 2
            while i < n and '|' in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            out.append(('table', rows))
            continue
        if re.match(r'^\s*([-*+]|\d+[.)])\s+', line):
            ordered = bool(re.match(r'^\s*\d+[.)]\s+', line))
            items = []
            while i < n and re.match(r'^\s*([-*+]|\d+[.)])\s+', lines[i]):
                mm = re.match(r'^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$', lines[i])
                items.append((len(mm.group(1)) // 2, mm.group(2)))
                i += 1
            out.append(('ol' if ordered else 'ul', items))
            continue
        if not line.strip():
            i += 1
            continue
        buf = [line.strip()]
        i += 1
        while i < n and lines[i].strip() and not _starts_block(lines[i]) and not (
            '|' in lines[i] and i + 1 < n and _is_table_sep(lines[i + 1])
        ):
            buf.append(lines[i].strip())
            i += 1
        text = ' '.join(buf)
        im = re.fullmatch(r'!\[([^\]]*)\]\(([^)]+)\)', text)
        if im:
            out.append(('image', im.group(1), im.group(2).strip()))
        else:
            out.append(('p', text))
    return out


def _split_row(line):
    text = line.strip()
    if text.startswith('|'):
        text = text[1:]
    if text.endswith('|'):
        text = text[:-1]
    return [part.strip() for part in text.split('|')]


def _px(size, zoom):
    return max(8, int(round(size * zoom / 100.0)))


def _configure_tags(view, zoom=100):
    def s(n):
        return _px(n, zoom)

    def sp(n):
        return max(0, int(round(n * zoom / 100.0)))

    view.configure(font=('Microsoft YaHei UI', s(14)))
    view.tag_configure('p', font=('Microsoft YaHei UI', s(14)), foreground=FG, spacing1=sp(4), spacing3=sp(4))
    view.tag_configure('h1', font=('Microsoft YaHei UI', s(22), 'bold'), foreground='#ffffff', spacing1=sp(10), spacing3=sp(6))
    view.tag_configure('h2', font=('Microsoft YaHei UI', s(18), 'bold'), foreground='#f5f5f5', spacing1=sp(12), spacing3=sp(4))
    view.tag_configure('h3', font=('Microsoft YaHei UI', s(16), 'bold'), foreground='#f0f0f0', spacing1=sp(10), spacing3=sp(4))
    view.tag_configure('h4', font=('Microsoft YaHei UI', s(14), 'bold'), foreground='#eeeeee', spacing1=sp(8), spacing3=sp(2))
    view.tag_configure('b', font=('Microsoft YaHei UI', s(14), 'bold'))
    view.tag_configure('i', font=('Microsoft YaHei UI', s(14), 'italic'))
    view.tag_configure('code', font=('Consolas', s(12)), foreground='#e8d5a3', background='#1c1c1c')
    view.tag_configure('codeblock', font=('Consolas', s(12)), foreground='#d4d4d4', background='#1c1c1c',
                       lmargin1=s(16), lmargin2=s(16), spacing1=sp(8), spacing3=sp(8))
    view.tag_configure('quote', font=('Microsoft YaHei UI', s(14)), foreground='#bdbdbd',
                       lmargin1=s(16), lmargin2=s(16), spacing1=sp(2), spacing3=sp(2))
    view.tag_configure('li', font=('Microsoft YaHei UI', s(14)), foreground=FG, lmargin1=s(8), lmargin2=s(28), spacing1=sp(1))
    view.tag_configure('hr', foreground='#3a3a3a', justify='center', spacing1=sp(8), spacing3=sp(8))
    view.tag_configure('link', foreground='#8ab4f8', underline=1)
    view.tag_configure('muted', foreground='#8a8a8a')
    for name in ('b', 'i', 'code', 'link', 'h4', 'h3', 'h2', 'h1'):
        view.tag_raise(name)


def _insert_inline(view, text, tags, on_link):
    pos = 0
    for m in _INLINE.finditer(text or ''):
        if m.start() > pos:
            view.insert('end', text[pos:m.start()], tags)
        if m.group(1) is not None:
            alt = m.group(1) or m.group(2)
            view.insert('end', alt, tags + ('muted',))
        elif m.group(3) is not None:
            label, url = m.group(3), m.group(4)
            tag = 'lnk%d' % on_link['n']
            on_link['n'] += 1
            view.insert('end', label, tags + ('link', tag))
            view.tag_bind(tag, '<Button-1>', lambda e, u=url: on_link['open'](u))
            view.tag_bind(tag, '<Enter>', lambda e: view.configure(cursor='hand2'))
            view.tag_bind(tag, '<Leave>', lambda e: view.configure(cursor='arrow'))
        elif m.group(5) is not None:
            view.insert('end', m.group(5), tags + ('code',))
        elif m.group(6) is not None or m.group(7) is not None:
            view.insert('end', m.group(6) or m.group(7), tags + ('b',))
        else:
            view.insert('end', m.group(8) or m.group(9) or '', tags + ('i',))
        pos = m.end()
    if pos < len(text or ''):
        view.insert('end', (text or '')[pos:], tags)
    view.insert('end', '\n')


def fill_plain(view, src):
    """Word / PDF 按抽出的文字逐行放下，不再按 Markdown 解释。"""
    view.delete('1.0', 'end')
    text = (src or '').replace('\r\n', '\n').replace('\r', '\n').strip('\n')
    if not text.strip():
        view.insert('end', '（空文件）\n', ('muted',))
        return
    for line in text.split('\n'):
        tag = 'muted' if re.fullmatch(r'第 \d+ 页', line.strip()) else 'p'
        view.insert('end', line + '\n', (tag,))


def fill_view(view, src, on_link, image_holder, base_dir):
    view.delete('1.0', 'end')
    blocks = parse_blocks(src or '')
    if not blocks:
        view.insert('end', '（空文件）\n', ('muted',))
        return
    for block in blocks:
        kind = block[0]
        if kind == 'h':
            level = min(block[1], 4)
            _insert_inline(view, block[2], ('h%d' % level,), on_link)
        elif kind == 'p':
            _insert_inline(view, block[1], ('p',), on_link)
        elif kind == 'quote':
            for part in block[1].split('\n'):
                _insert_inline(view, part, ('quote',), on_link)
        elif kind == 'code':
            view.insert('end', (block[1] or '') + '\n', ('codeblock',))
        elif kind == 'hr':
            view.insert('end', '─' * 28 + '\n', ('hr',))
        elif kind in ('ul', 'ol'):
            num = 1
            for depth, item in block[1]:
                mark = '%d. ' % num if kind == 'ol' else '• '
                if kind == 'ol':
                    num += 1
                prefix = '    ' * depth
                view.insert('end', prefix + mark, ('li',))
                _insert_inline(view, item, ('li',), on_link)
        elif kind == 'table':
            for row in block[1]:
                view.insert('end', '  '.join(row) + '\n', ('codeblock',))
        elif kind == 'image':
            _insert_image(view, block[1], block[2], base_dir, image_holder)


def _insert_image(view, alt, url, base_dir, holder):
    path = url
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', url):
        path = url if os.path.isabs(url) else os.path.join(base_dir or '', url)
    shown = False
    if os.path.isfile(path):
        try:
            from PIL import Image, ImageTk
            im = Image.open(path)
            im.thumbnail((860, 640))
            photo = ImageTk.PhotoImage(im)
            holder.append(photo)
            view.image_create('end', image=photo, padx=4, pady=6)
            view.insert('end', '\n')
            shown = True
        except Exception:
            shown = False
    if not shown:
        view.insert('end', alt or os.path.basename(path) or url, ('muted',))
        view.insert('end', '\n')


class Reader:
    def __init__(self, root, path):
        self.root = root
        self.files, self.idx = [], 0
        self._fullscreen = False
        self._max = False
        self._drag = None
        self._rz = None
        self._pill_drag = False
        self._pill_hot = False
        self._scroll = (0.0, 1.0)
        self._thumb = (0, 0)
        self._zoom = 100
        self._pill_hold = False
        self._scan_token = 0
        self._status_base = ''
        self._hint_text = ''
        self._save_job = None
        self._icon_img = None
        self._images = []
        self._base = ''
        self._snap_border = snap_border()
        self._snap_hwnd = 0
        self._tk_width_extra = 0
        self._snap_modal = False
        self._sys_pending = None
        self._pending_move = None
        self._max_hot = False
        self._cur_name = ''
        root.title(TITLE)
        root.configure(bg=BG)
        root.withdraw()
        self._install_snap_frame()
        self._apply_icon()
        self._build_title()
        self._build_tools()
        body = tk.Frame(root, bg=BG)
        body.pack(fill='both', expand=True)
        self.body = body
        self.view = tk.Text(
            body, bg=BG, fg=FG, insertbackground=FG, relief='flat', bd=0,
            highlightthickness=0, padx=28, pady=16, wrap='word', cursor='arrow',
            font=('Microsoft YaHei UI', 14), undo=False,
        )
        self.view.configure(selectbackground='#264f78', selectforeground='#ffffff')
        _configure_tags(self.view, self._zoom)
        self.view.bind('<Control-MouseWheel>', self._zoom_wheel)
        for seq, fn in (
            ('<Control-plus>', self._zoom_in),
            ('<Control-equal>', self._zoom_in),
            ('<Control-KP_Add>', self._zoom_in),
            ('<Control-minus>', self._zoom_out),
            ('<Control-KP_Subtract>', self._zoom_out),
            ('<Control-0>', self._zoom_reset),
            ('<Control-KP_0>', self._zoom_reset),
        ):
            self.view.bind(seq, fn)
            root.bind(seq, fn)
        self.view.pack(side='left', fill='both', expand=True)
        # 单独一列，不盖在正文上。正文是系统控件，盖上去会把胶囊右边吃掉。
        self.pill = tk.Canvas(
            body, width=PILL_W, height=PILL_MIN, bg=BG, highlightthickness=0, bd=0,
            cursor='sb_v_double_arrow',
        )
        # 胶囊左边这一列，右边 8px 同色空档留给窗口拉边，避免鼠标指到胶囊就变成拉边。
        self._pill_gap = tk.Frame(
            body, width=max(PILL_GAP, self._snap_border), bg=BG, highlightthickness=0, bd=0,
        )
        self._pill_gap.pack_propagate(False)
        for seq, fn in (
            ('<ButtonPress-1>', self._press),
            ('<B1-Motion>', self._motion),
            ('<ButtonRelease-1>', self._release),
            ('<Motion>', self._hover),
        ):
            self._pill_gap.bind(seq, fn)
        self.view.configure(yscrollcommand=self._on_yscroll)
        self.pill.bind('<ButtonPress-1>', self._pill_press)
        self.pill.bind('<B1-Motion>', self._pill_motion)
        self.pill.bind('<ButtonRelease-1>', self._pill_release)
        self.pill.bind('<Enter>', self._pill_enter)
        self.pill.bind('<Leave>', self._pill_leave)
        self.pill.bind('<MouseWheel>', self._pill_wheel)
        self.pill.bind('<Configure>', lambda e: self._paint_pill())
        self.bar = tk.Label(root, bg=BG, fg='#888888', font=('Microsoft YaHei UI', 9), anchor='w')
        self.bar.pack(fill='x', side='bottom')
        self.view.bind('<Key>', self._eat_key)
        self.view.bind('<<Paste>>', lambda e: 'break')
        self.view.bind('<Control-c>', lambda e: None)
        self.view.bind('<Control-a>', self._select_all)
        self.view.bind('<Control-Left>', lambda e: self.step(-1))
        self.view.bind('<Control-Right>', lambda e: self.step(1))
        self.view.bind('<Control-o>', lambda e: self.ask())
        root.bind('<Control-o>', lambda e: self.ask())
        root.bind('<Escape>', self.esc)
        root.bind('<F11>', self.fs)
        root.bind('<Configure>', self._on_root_configure)
        root.protocol('WM_DELETE_WINDOW', self.close)
        for w in (root, self.view, self.title_bar, self.bar, body):
            w.bind('<ButtonPress-1>', self._press, add='+')
            w.bind('<B1-Motion>', self._motion, add='+')
            w.bind('<ButtonRelease-1>', self._release, add='+')
            w.bind('<Motion>', self._hover, add='+')
        self._place_window()
        self._taskbar()
        self.load_path(path)
        self.view.focus_set()

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
        # 窗口还没显示时做出来的图是空的。每次都重做，并让标题栏改拿新的这一张。
        try:
            from PIL import Image, ImageTk
            im = Image.open(ico).resize((16, 16), Image.LANCZOS)
            self._icon_img = ImageTk.PhotoImage(im)
        except Exception:
            return
        self._place_title_icon()

    def _place_title_icon(self):
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

    def _build_title(self):
        bar = tk.Frame(self.root, bg=TITLE_BG, height=TITLE_H)
        bar.pack(side='top', fill='x')
        bar.pack_propagate(False)
        self.title_bar = bar
        self._place_title_icon()
        self._title_font = tkfont.Font(family='Microsoft YaHei UI', size=10)
        lbl = tk.Label(bar, text=TITLE, bg=TITLE_BG, fg=TITLE_FG, font=self._title_font)
        lbl.place(relx=0.5, rely=0.5, anchor='center')
        self.title_lbl = lbl
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

    def _on_title_resize(self, e=None):
        self._refresh_title()

    def _refresh_title(self):
        lbl = getattr(self, 'title_lbl', None)
        if lbl is None:
            return
        text = getattr(self, '_cur_name', '') or TITLE
        font = getattr(self, '_title_font', None)
        bar = getattr(self, 'title_bar', None)
        if font is not None and bar is not None:
            bar_w = bar.winfo_width()
            reserve = self._snap_border + CAP_W * 3 + 8
            avail = bar_w - 2 * reserve
            if avail > 0 and font.measure(text) > avail:
                while len(text) > 1 and font.measure(text + '…') > avail:
                    text = text[:-1]
                text += '…'
        if lbl.cget('text') != text:
            lbl.config(text=text)

    def _build_tools(self):
        self._theme_name = read_ui_theme()
        pal = _TOOL[self._theme_name]
        bar = tk.Frame(self.root, bg=pal['bar'], height=44)
        bar.pack(side='top', fill='x')
        bar.pack_propagate(False)
        self.tool_bar = bar
        line = tk.Frame(bar, bg=pal['line'], height=1)
        line.pack(side='bottom', fill='x')
        self.tool_line = line
        inner = tk.Frame(bar, bg=pal['bar'])
        inner.pack(side='top', fill='both', expand=True)
        self.tool_inner = inner
        # E8E5 打开，E72B 上一篇，E72A 下一篇，E71F 缩小，E8A3 放大
        self.btn_open = _IconButton(inner, '\uE8E5', self.ask)
        self.btn_prev = _IconButton(inner, '\uE72B', lambda: self.step(-1))
        self.btn_next = _IconButton(inner, '\uE72A', lambda: self.step(1))
        self.btn_zoom_out = _IconButton(inner, '\uE71F', self._zoom_out)
        self.btn_zoom_in = _IconButton(inner, '\uE8A3', self._zoom_in)
        self.zoom_lbl = tk.Label(
            inner, text='100%', bg=pal['bar'], fg=pal['fg'],
            font=('Microsoft YaHei UI', 11), width=5, cursor='hand2',
        )
        self.zoom_lbl.bind('<Button-1>', self._choose_zoom)
        self.btn_open.pack(side='left', padx=(10, 0), pady=7)
        self.btn_prev.pack(side='left', padx=(4, 0), pady=7)
        self.btn_next.pack(side='left', padx=(4, 0), pady=7)
        self.btn_zoom_in.pack(side='right', padx=(0, 10), pady=7)
        self.zoom_lbl.pack(side='right', padx=(0, 2), pady=7)
        self.btn_zoom_out.pack(side='right', padx=(0, 2), pady=7)
        self._wire_hint(self.btn_open, '打开')
        self._wire_hint(self.btn_prev, '上一篇')
        self._wire_hint(self.btn_next, '下一篇')
        self._wire_hint(self.btn_zoom_out, '缩小')
        self._wire_hint(self.btn_zoom_in, '放大')
        self._wire_hint(self.zoom_lbl, '选择比例')
        for w in (bar, inner, line):
            w.bind('<ButtonPress-1>', self._press, add='+')
            w.bind('<B1-Motion>', self._motion, add='+')
            w.bind('<ButtonRelease-1>', self._release, add='+')
            w.bind('<Motion>', self._hover, add='+')
        self._apply_tool_theme()
        self._sync_zoom_btns()
        self.root.bind('<FocusIn>', self._sync_tool_theme, add='+')

    def _wire_hint(self, widget, text):
        widget.bind('<Enter>', lambda e, t=text: self._set_hint(t), add='+')
        widget.bind('<Leave>', lambda e: self._set_hint(''), add='+')

    def _set_status(self, text):
        self._status_base = text or ''
        self._paint_bar()

    def _set_hint(self, text):
        self._hint_text = text or ''
        self._paint_bar()

    def _paint_bar(self):
        base = self._status_base
        hint = self._hint_text
        if hint and base:
            self.bar.config(text='%s    %s' % (base, hint))
        else:
            self.bar.config(text=hint or base)

    def _sync_tool_theme(self, e=None):
        if e is not None and e.widget is not self.root:
            return
        name = read_ui_theme()
        if name == self._theme_name:
            return
        self._theme_name = name
        self._apply_tool_theme()

    def _apply_tool_theme(self):
        pal = _TOOL.get(self._theme_name) or _TOOL['dark']
        self.tool_bar.configure(bg=pal['bar'])
        self.tool_inner.configure(bg=pal['bar'])
        self.tool_line.configure(bg=pal['line'])
        for btn in (self.btn_open, self.btn_prev, self.btn_next, self.btn_zoom_out, self.btn_zoom_in):
            btn.set_palette(pal)
        self.zoom_lbl.configure(bg=pal['bar'], fg=pal['fg'])

    def _sync_tool_nav(self):
        on = len(self.files) >= 2
        self.btn_prev.set_enabled(on)
        self.btn_next.set_enabled(on)

    def _on_yscroll(self, first, last):
        if self._pill_hold:
            return
        try:
            self._scroll = (float(first), float(last))
        except (TypeError, ValueError):
            self._scroll = (0.0, 1.0)
        self._paint_pill()

    def _paint_pill(self):
        c = getattr(self, 'pill', None)
        if c is None or self._pill_hold:
            return
        first, last = self._scroll
        span = max(0.0, last - first)
        gap = self._pill_gap
        if span >= 0.995:
            self._thumb = (0, 0)
            if c.winfo_manager():
                c.pack_forget()
            if gap.winfo_manager():
                gap.pack_forget()
            return
        # 先放右边空档，胶囊再贴在它左边，整条胶囊离窗口右缘 8px。
        if not gap.winfo_manager():
            gap.pack(side='right', fill='y', before=self.view)
        if not c.winfo_manager():
            c.pack(side='right', fill='y', before=self.view)
            if c.winfo_height() <= 1:
                return
        h = max(1, c.winfo_height())
        thumb = min(h, max(PILL_MIN, int(round(span * h))))
        travel = max(1, h - thumb)
        max_first = max(0.001, 1.0 - span)
        top = int(round(min(1.0, first / max_first) * travel))
        top = max(0, min(travel, top))
        if self._pill_drag:
            color = PILL_PRESS
        elif self._pill_hot:
            color = PILL_HOVER
        else:
            color = PILL
        width = c.winfo_width()
        if width <= 1:
            width = PILL_W
        c.delete('pill')
        _stadium(c, 0, top, width, thumb, color)
        self._thumb = (top, top + thumb)

    def _move_pill(self, y):
        first, last = self._scroll
        span = max(0.0, last - first)
        if span >= 0.995:
            return
        h = max(1, self.pill.winfo_height())
        thumb = min(h, max(PILL_MIN, int(round(span * h))))
        travel = max(1, h - thumb)
        top = min(travel, max(0.0, y - self._pill_grab))
        max_first = max(0.001, 1.0 - span)
        self.view.yview_moveto((top / travel) * max_first)

    def _pill_press(self, e):
        self._pill_drag = True
        self._pill_hot = True
        self._rz = None
        self._drag = None
        top, bot = self._thumb
        if bot > top and top <= e.y <= bot:
            self._pill_grab = e.y - top
        else:
            self._pill_grab = max(1.0, (bot - top) / 2.0)
        self._move_pill(e.y)
        self._paint_pill()
        return 'break'

    def _pill_motion(self, e):
        if not self._pill_drag:
            return 'break'
        self._move_pill(e.y)
        return 'break'

    def _pill_release(self, e):
        self._pill_drag = False
        self._paint_pill()
        return 'break'

    def _pill_enter(self, e):
        self._pill_hot = True
        self.pill.configure(cursor='sb_v_double_arrow')
        self._paint_pill()

    def _pill_leave(self, e):
        if self._pill_drag:
            return
        self._pill_hot = False
        self._paint_pill()

    def _pill_wheel(self, e):
        self.view.yview_scroll(-1 if e.delta > 0 else 1, 'units')
        return 'break'

    def _install_snap_frame(self):
        hwnd = _hwnd(self.root)
        self._snap_hwnd = int(hwnd)
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
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0037)
        try:
            margins = _MARGINS(0, 0, 0, 1)
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
        except Exception:
            pass

    def _set_outer(self, w, h, x, y):
        """外框用这个尺寸。先减去 Tk 多加的左右边框，否则窗口会往右变宽。"""
        extra = self._tk_width_extra if self._snap_hwnd else 0
        tw = max(1, int(w) - int(extra))
        self.root.geometry(f'{tw}x{int(h)}+{int(x)}+{int(y)}')

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
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0037)
        if self._fullscreen:
            return
        self._max = bool(user32.IsZoomed(hwnd))
        self._sync_max_glyph()

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
            w, h = min(1100, vw), min(760, vh)
            x, y = vx + max(0, (vw - w) // 2), vy + max(0, (vh - h) // 2)
        self._normal = (x, y, w, h)
        self._set_outer(w, h, x, y)

    def _taskbar(self):
        try:
            hwnd = _hwnd(self.root)
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            style = (style | 0x00040000) & ~0x00000080
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, style)
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
        hmon = user32.MonitorFromWindow(_hwnd(self.root), 2)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        r = info.rcWork
        return r.left, r.top, r.right - r.left, r.bottom - r.top

    def minimize(self):
        self.root.iconify()

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
        # 拖橙色胶囊时，右边框不再跟着动。胶囊本身也不算窗口拉边。
        if self._fullscreen or self._max or self._pill_drag:
            return None
        if e.widget is getattr(self, 'pill', None):
            return None
        x = e.x_root - self.root.winfo_rootx()
        y = e.y_root - self.root.winfo_rooty()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        m = self._snap_border if self._snap_hwnd else 6
        left, right = x <= m, x >= w - m
        top, bottom = y <= m, y >= h - m
        if top and not bottom:
            return None
        if not (left or right or top or bottom):
            return None
        return ('n' if top else '') + ('s' if bottom else '') + ('w' if left else '') + ('e' if right else '')

    def _press(self, e):
        if self._pill_drag:
            return 'break'
        edge = self._edge(e)
        if edge:
            # 拉边继续改 geometry。系统 SC_SIZE 的模态循环里一进 Configure 就会把 pythonw 打崩。
            x, y, w, h = self._normal
            self._pending_move = None
            self._rz = (edge, e.x_root, e.y_root, x, y, w, h)
            self._drag = None
            return 'break'
        if self._is_caption(e.widget):
            if self._snap_hwnd and not self._fullscreen:
                self._pending_move = (e.x_root, e.y_root)
                return None
            if not self._max and not self._fullscreen:
                self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())
        return None

    def _motion(self, e):
        if self._pill_drag:
            return 'break'
        pending = self._pending_move
        if pending and self._snap_hwnd:
            x0, y0 = pending
            if abs(e.x_root - x0) + abs(e.y_root - y0) >= 4:
                self._pending_move = None
                self._sys_command(0xF012, e.x_root, e.y_root)
                return 'break'
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
        self._pending_move = None

    def _hover(self, e):
        if self._rz or self._pill_drag:
            return
        edge = self._edge(e)
        cursors = {
            'n': 'top_side', 's': 'bottom_side', 'w': 'left_side', 'e': 'right_side',
            'nw': 'top_left_corner', 'ne': 'top_right_corner',
            'sw': 'bottom_left_corner', 'se': 'bottom_right_corner',
        }
        try:
            if e.widget is self.view:
                if edge:
                    self.view.configure(cursor=cursors[edge])
                elif getattr(self, '_view_edge', None):
                    self.view.configure(cursor='arrow')
                self._view_edge = edge
                return
            e.widget.configure(cursor=cursors.get(edge, ''))
        except Exception:
            pass

    def close(self):
        self._save_normal()
        self.root.destroy()

    def _eat_key(self, e):
        if e.state & 0x4:
            return None
        if e.keysym in _NAV:
            return None
        return 'break'

    def _select_all(self, e=None):
        self.view.tag_add('sel', '1.0', 'end-1c')
        return 'break'

    def ask(self):
        path = filedialog.askopenfilename(
            title='打开文件',
            filetypes=_OPEN_TYPES,
        )
        if path:
            self.load_path(path)
        return 'break'

    def load_path(self, path):
        if not path:
            return
        path = os.path.abspath(path)
        self._scan_token += 1
        token = self._scan_token
        self.files = [path]
        self.idx = 0
        self.show()
        self.root.after(0, lambda t=token, p=path: self._scan_folder(t, p))

    def _scan_folder(self, token, path):
        """同目录里同一种文件稍后再列，避免双击时先卡住。"""
        if token != self._scan_token:
            return
        folder = os.path.dirname(path)
        kind = _file_kind(path)
        names = []
        try:
            names = [n for n in os.listdir(folder)
                     if kind and _file_kind(n) == kind and os.path.isfile(os.path.join(folder, n))]
        except OSError:
            names = []
        if token != self._scan_token:
            return
        names.sort(key=nkey)
        files = [os.path.join(folder, n) for n in names]
        if path not in files and os.path.normcase(path) not in [os.path.normcase(p) for p in files]:
            files = [path]
        self.files = files
        self.idx = next((i for i, f in enumerate(files) if os.path.normcase(f) == os.path.normcase(path)), 0)
        name = os.path.basename(self.files[self.idx])
        self._set_status('%d/%d  %s' % (self.idx + 1, len(self.files), name))
        self._sync_tool_nav()

    def show(self):
        if not self.files:
            return
        path = self.files[self.idx]
        self._set_title(os.path.basename(path))
        self._base = os.path.dirname(path)
        self._images = []
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext in WORD_EXTS or ext in PDF_EXTS:
                src = read_office(path)
                plain = True
            else:
                src = read_text(path)
                plain = False
        except Exception as exc:
            self.view.delete('1.0', 'end')
            self.view.insert('end', '无法打开：%s\n' % exc, ('muted',))
            self._set_status(path)
            self._sync_tool_nav()
            return
        on_link = {'n': 0, 'open': self.open_url}
        self._pill_hold = True
        try:
            if plain:
                fill_plain(self.view, src)
            else:
                fill_view(self.view, src, on_link, self._images, self._base)
            self.view.yview_moveto(0)
            self.view.update_idletasks()
        finally:
            self._pill_hold = False
        self._on_yscroll(*self.view.yview())
        name = os.path.basename(path)
        self._set_status('%d/%d  %s' % (self.idx + 1, len(self.files), name))
        self._sync_tool_nav()

    def _zoom_in(self, e=None):
        return self.zoom_by(1)

    def _zoom_out(self, e=None):
        return self.zoom_by(-1)

    def _zoom_reset(self, e=None):
        self.set_zoom(100)
        return 'break'

    def _zoom_wheel(self, e):
        self.zoom_by(1 if e.delta > 0 else -1)
        return 'break'

    def zoom_by(self, direction):
        steps = list(ZOOM_STEPS)
        try:
            i = steps.index(self._zoom)
        except ValueError:
            i = min(range(len(steps)), key=lambda n: abs(steps[n] - self._zoom))
        i = max(0, min(len(steps) - 1, i + int(direction)))
        self.set_zoom(steps[i])
        return 'break'

    def set_zoom(self, zoom):
        zoom = int(zoom)
        if zoom not in ZOOM_STEPS:
            zoom = min(ZOOM_STEPS, key=lambda n: abs(n - zoom))
        view = getattr(self, 'view', None)
        if view is None or zoom == self._zoom:
            self._zoom = zoom
            self._sync_zoom_btns()
            return
        try:
            anchor = view.index('@0,0')
        except tk.TclError:
            anchor = '1.0'
        self._pill_hold = True
        try:
            self._zoom = zoom
            _configure_tags(view, zoom)
            self.root.update_idletasks()
            view.tk.call(view._w, 'yview', anchor)
            self.root.update()
        finally:
            self._pill_hold = False
        self._on_yscroll(*view.yview())
        self._sync_zoom_btns()

    def _sync_zoom_btns(self):
        self.zoom_lbl.config(text='%d%%' % self._zoom)
        self.btn_zoom_out.set_enabled(self._zoom > ZOOM_STEPS[0])
        self.btn_zoom_in.set_enabled(self._zoom < ZOOM_STEPS[-1])

    def _choose_zoom(self, e):
        menu = tk.Menu(self.root, tearoff=0)
        for n in ZOOM_STEPS:
            menu.add_command(label='%d%%' % n, command=lambda v=n: self.set_zoom(v))
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()
        return 'break'

    def open_url(self, url):
        if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', url):
            webbrowser.open(url)
            return 'break'
        path = url if os.path.isabs(url) else os.path.join(self._base, url)
        path = os.path.normpath(path)
        if os.path.isfile(path) and os.path.splitext(path)[1].lower() in EXTS:
            self.load_path(path)
        elif os.path.isfile(path):
            os.startfile(path)
        return 'break'

    def step(self, d):
        if len(self.files) < 2:
            return 'break'
        self.idx = (self.idx + d) % len(self.files)
        self.show()
        return 'break'

    def fs(self, e=None):
        self._fullscreen = not self._fullscreen
        if self._fullscreen:
            self._save_normal()
            if self._max or self.root.state() == 'zoomed':
                self.root.state('normal')
                self._max = False
            self.root.attributes('-fullscreen', True)
            self.title_bar.pack_forget()
            self.tool_bar.pack_forget()
            self.bar.pack_forget()
        else:
            self.root.attributes('-fullscreen', False)
            self.title_bar.pack(side='top', fill='x', before=self.body)
            self.tool_bar.pack(side='top', fill='x', before=self.body)
            self.bar.pack(fill='x', side='bottom')
            self._taskbar()
            self._apply_icon()
            self._sync_max_glyph()
        return 'break'

    def esc(self, e=None):
        if self._fullscreen:
            self.fs()
        else:
            self.close()


def main():
    root = tk.Tk()
    root.withdraw()
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        path = filedialog.askopenfilename(
            title='打开文件',
            filetypes=_OPEN_TYPES,
        )
        if not path:
            return
    view = Reader(root, path)
    root.deiconify()
    root.lift()
    view._taskbar()
    view._apply_icon()
    if os.environ.get('MD_TEST'):
        def _finish():
            view._save_normal()
            root.destroy()
        root.after(int(os.environ['MD_TEST']), _finish)
    root.mainloop()


if __name__ == '__main__':
    main()
