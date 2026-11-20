# -*- coding: utf-8 -*-
"""文件阅读。Markdown 按 Obsidian 常用写法排版，纯文本按原样显示，Word 与 PDF 抽出文字。Ctrl+左右 切换同目录同一种文件，F11 全屏，Esc 退出全屏或关闭，Ctrl+O 打开。"""
import ctypes
import json
import os
import queue
import re
import sys
import threading
import time
import webbrowser
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes
from tkinter import filedialog

import cosy_speak
from md_view import (
    _insert_text, anchor_key, clear_quote_bars, configure_tags as _configure_tags,
    fill_view, paint_quote_bars, plain_widget, release_document, rescale_embedded,
    scroll_pixels,
)

MD_EXTS = {'.md', '.markdown', '.mdown'}
TXT_EXTS = {'.txt'}
WORD_EXTS = {'.doc', '.docx'}
PDF_EXTS = {'.pdf'}
EXTS = MD_EXTS | TXT_EXTS | WORD_EXTS | PDF_EXTS
_OPEN_TYPES = [
    ('文件', '*.md *.markdown *.mdown *.txt *.docx *.doc *.pdf'),
    ('Markdown', '*.md *.markdown *.mdown'),
    ('文本', '*.txt'),
    ('Word', '*.docx *.doc'),
    ('PDF', '*.pdf'),
    ('所有文件', '*.*'),
]
BG = '#111111'
FG = '#e6e6e6'
TITLE_BG = '#000000'
TITLE_FG = '#ffffff'
TITLE = '桌面助手 v9.19·阅读器'
TITLE_H = 32
MIN_W, MIN_H = 640, 400
# 与主程序滚动条同色。形状是两端半圆的胶囊，不是方条。
PILL = '#f0a542'
PILL_HOVER = '#f7bb64'
PILL_PRESS = '#dd932f'
# 朗读中的胶囊和朗读线用同一系淡蓝，深浅主题都能看清。
SPEAK_PILL = '#7DB9F0'
SPEAK_PILL_HOVER = '#96CCF5'
SPEAK_PILL_PRESS = '#5EA3E0'
SPEAK_PILL_FG = '#10233A'
SPEAK_LINE = '#A9D2F5'
SPEAK_LINE_H = 2
SPEAK_LINE_CLEAR = 3

SPEAK_SCROLL_MS = 400
SPEAK_FADE_OUT_MS = 200
SPEAK_FADE_IN_MS = 250
SPEAK_ANIM_TICK_MS = 16

SPEAK_STATUS_W = 200  # 100% 系统缩放下的固定宽度，跟正文缩放无关
SPEAK_IDLE_REVERT_MS = 2000

# 朗读状态：尽量短，好放进 200px。详细错误仍走原字符串映射，界面只显示短语。
_SPEAK_NOTE_SHORT = {
    '正在加载语音模型…': '加载模型…',
    '加载模型…': '加载模型…',
    '正在合成…': '合成中…',
    '合成中…': '合成中…',
    '朗读中': '朗读中',
    '已暂停': '已暂停',
    '已停止': '已停止',
    '读完了': '读完了',
    '语音模型还不能用': '出错：模型不可用',
    '还没有 Fun-CosyVoice3 模型': '出错：无模型',
    '还没有 CosyVoice 运行库': '出错：无运行库',
    '没有可用的参考音频': '出错：无参考音',
    '参考音频超过 30 秒，请先在语音克隆里裁短': '出错：参考音过长',
    '语音合成失败': '出错：合成失败',
    '合成完成但没有声音文件': '出错：无声音',
    '语音合成超时': '出错：超时',
}


def brief_speak_note(text):
    text = (text or '').strip()
    if not text:
        return ''
    mapped = _SPEAK_NOTE_SHORT.get(text)
    if mapped:
        return mapped
    if text.startswith('出错：'):
        return text
    return '出错：' + text


PILL_MIN = 48
PILL_W = 16
PILL_GAP = 8
TREE_W = 160
# Obsidian 的整页缩放：每一档是上一档的 1.2 倍，停在 50% 和 300%。
ZOOM_STEPS = (50, 58, 69, 83, 100, 120, 144, 173, 207, 249, 300)
# 自动滚屏按正文字号的一行计。滑块左端最慢，右端最快。
AUTO_SLOW = 20
AUTO_FAST = 2
AUTO_DEFAULT = 11
AUTO_TICK_MS = 50
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
        'track': '#4c5d86',
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
        'track': '#cbd5e1',
    },
}


def _foot_palette():
    """底部状态栏一直是正文底色，缩放按钮用这一套，不跟工具栏亮色走。"""
    return {
        'bar': BG,
        'line': '#2a2a2a',
        'btn': '#1c1c1c',
        'btn_hover': '#2a2a2a',
        'btn_press': '#333333',
        'btn_border': '#666666',
        'fg': '#c8c8c8',
        'fg_dis': '#666666',
        'border_dis': '#2a2a2a',
        'btn_dis': '#161616',
        'track': '#555555',
    }


_NAV = {
    'Left', 'Right', 'Up', 'Down', 'Prior', 'Next', 'Home', 'End',
    'Shift_L', 'Shift_R', 'Control_L', 'Control_R', 'Alt_L', 'Alt_R',
    'Tab', 'ISO_Left_Tab', 'Escape', 'F11',
}
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


def _fill_round(canvas, x1, y1, x2, y2, radius, color):
    """实心圆角矩形。"""
    radius = min(float(radius), max(0.0, (x2 - x1) / 2.0), max(0.0, (y2 - y1) / 2.0))
    if radius < 1:
        canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline=color)
        return
    canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2, fill=color, outline=color)
    canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius, fill=color, outline=color)
    d = radius * 2
    canvas.create_oval(x1, y1, x1 + d, y1 + d, fill=color, outline=color)
    canvas.create_oval(x2 - d, y1, x2, y1 + d, fill=color, outline=color)
    canvas.create_oval(x1, y2 - d, x1 + d, y2, fill=color, outline=color)
    canvas.create_oval(x2 - d, y2 - d, x2, y2, fill=color, outline=color)


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



def _draw_icon(canvas, name, cx, cy, color, size=18):
    """统一线宽的矢量图标，画在 28×28 按钮正中。"""
    stroke = 1.8
    def line(*xy, w=stroke):
        canvas.create_line(*xy, fill=color, width=w, capstyle=tk.ROUND, joinstyle=tk.ROUND)
    def box(x1, y1, x2, y2, r=0, fill=color):
        if r <= 0:
            canvas.create_rectangle(x1, y1, x2, y2, fill=fill, outline='')
        else:
            _fill_round(canvas, x1, y1, x2, y2, r, fill)
    if name == 'open':
        # 折角文件
        line(cx - 5, cy - 7, cx + 1, cy - 7, cx + 5, cy - 3, cx + 5, cy + 7, cx - 5, cy + 7, cx - 5, cy - 7)
        line(cx + 1, cy - 7, cx + 1, cy - 3, cx + 5, cy - 3)
    elif name == 'prev':
        line(cx + 3.5, cy - 6.5, cx - 4, cy, cx + 3.5, cy + 6.5)
    elif name == 'next':
        line(cx - 3.5, cy - 6.5, cx + 4, cy, cx - 3.5, cy + 6.5)
    elif name == 'tree':
        # 左侧栏+条目，表示目录树，避免被看成文档大纲。
        line(cx - 7, cy - 7, cx + 7, cy - 7, cx + 7, cy + 7, cx - 7, cy + 7, cx - 7, cy - 7)
        line(cx - 2, cy - 7, cx - 2, cy + 7)
        line(cx + 1, cy - 3.5, cx + 5, cy - 3.5)
        line(cx + 1, cy, cx + 5, cy)
        line(cx + 1, cy + 3.5, cx + 4, cy + 3.5)
    elif name in ('zoom_out', 'minus'):
        line(cx - 6, cy, cx + 6, cy)
    elif name in ('zoom_in', 'plus'):
        line(cx - 6, cy, cx + 6, cy)
        line(cx, cy - 6, cx, cy + 6)
    elif name == 'play':
        canvas.create_polygon(
            cx - 4.5, cy - 7, cx - 4.5, cy + 7, cx + 7, cy,
            fill=color, outline=color, joinstyle=tk.ROUND,
        )
    elif name == 'pause':
        box(cx - 5.5, cy - 6.5, cx - 2, cy + 6.5, r=1)
        box(cx + 2, cy - 6.5, cx + 5.5, cy + 6.5, r=1)
    elif name == 'stop':
        # 空心方块：未朗读时也能看清，避免实心小方块像脏点。
        canvas.create_rectangle(
            cx - 5.5, cy - 5.5, cx + 5.5, cy + 5.5,
            outline=color, fill='', width=1.8,
        )
    else:
        line(cx - 5, cy, cx + 5, cy)


class _ToolSep(tk.Canvas):
    """分组之间的细竖线。"""

    def __init__(self, master):
        super().__init__(master, width=9, height=28, highlightthickness=0, bd=0)
        self._pal = _TOOL['dark']
        self.paint()

    def set_palette(self, pal):
        self._pal = pal
        self.paint()

    def paint(self):
        pal = self._pal
        self.configure(bg=pal['bar'])
        self.delete('all')
        self.create_line(4, 6, 4, 22, fill=pal['line'], width=1)


class _IconButton(tk.Canvas):
    """工具栏图标按钮。28×28，18px 矢量图标，圆角悬停；播放键用强调色。"""

    def __init__(self, master, command, icon='', accent=False):
        self._icon = icon
        self._command = command
        self._accent = bool(accent)
        self._enabled = True
        self._hot = False
        self._down = False
        self._latched = False
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

    def set_on(self, on):
        self._latched = bool(on)
        self.paint()

    def set_icon(self, icon):
        self._icon = icon or ''
        self.paint()

    def set_glyph(self, glyph):
        # 兼容旧的 Segoe 字形：播放/暂停会在朗读时切换。
        if glyph == _SPEAK_PAUSE:
            self.set_icon('pause')
        elif glyph == _SPEAK_PLAY:
            self.set_icon('play')
        elif glyph == '\uE838':
            self.set_icon('tree')
        elif glyph == '\uE8B7':
            self.set_icon('tree')

    def paint(self):
        self.delete('all')
        pal = self._pal
        self.configure(bg=pal['bar'])
        w = int(float(self.cget('width')) or 28)
        h = int(float(self.cget('height')) or 28)
        if self._accent:
            if not self._enabled:
                fill, fg = pal['btn_dis'], pal['fg_dis']
            elif self._down:
                fill, fg = PILL_PRESS, '#1a1408'
            elif self._hot or self._latched:
                fill, fg = PILL_HOVER, '#1a1408'
            else:
                fill, fg = PILL, '#1a1408'
            _fill_round(self, 1, 1, w - 2, h - 2, 7, fill)
        else:
            if not self._enabled:
                fill, fg = None, pal['fg_dis']
            elif self._down:
                fill, fg = pal['btn_press'], pal['fg']
            elif self._latched:
                fill, fg = pal['btn_hover'], PILL
            elif self._hot:
                fill, fg = pal['btn_hover'], pal['fg']
            else:
                fill, fg = None, pal['fg']
            if fill:
                _fill_round(self, 1, 1, w - 2, h - 2, 7, fill)
        _draw_icon(self, self._icon, w / 2.0, h / 2.0, fg, 18)

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


class _WordButton(tk.Canvas):
    """工具栏文字按钮。外观对齐主程序 kind=mini。按下并保持激活时才算开。"""

    def __init__(self, master, text, command):
        self._text = text
        self._command = command
        self._enabled = True
        self._hot = False
        self._down = False
        self._latched = False
        self._font = tkfont.Font(family='Microsoft YaHei UI', size=12, weight='bold')
        # 右侧圆角会画出画布 1 像素，宽度多留出这一圈。
        width = self._font.measure(text) + 24
        super().__init__(
            master, width=width, height=28, highlightthickness=0, bd=0, cursor='hand2',
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

    def set_on(self, on):
        self._latched = bool(on)
        self.paint()

    def paint(self):
        self.delete('all')
        pal = self._pal
        self.configure(bg=pal['bar'])
        w = int(float(self.cget('width')))
        h = int(float(self.cget('height')))
        if self._latched:
            fill = PILL_PRESS if self._down else PILL
            fg = '#1a1408'
            border = PILL_PRESS
        elif not self._enabled:
            fill = pal['btn_dis']
            fg = pal['fg_dis']
            border = pal['border_dis']
        elif self._down:
            fill = pal['btn_press']
            fg = pal['fg']
            border = pal['btn_border']
        elif self._hot:
            fill = pal['btn_hover']
            fg = pal['fg']
            border = pal['btn_border']
        else:
            fill = pal['btn']
            fg = pal['fg']
            border = pal['btn_border']
        top = (h - 22) / 2.0
        # 画布右缘会裁掉正好贴边的圆角，左右各让出 1 像素。
        _fill_round(self, 1, top, w - 2, top + 22, 6, border)
        _fill_round(self, 2, top + 1, w - 3, top + 21, 5, fill)
        self.create_text(w / 2.0, h / 2.0, text=self._text, fill=fg, font=self._font)

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


class _VoiceButton(tk.Canvas):
    """朗读胶囊：一个汉字加一个播放/暂停图标，高 28px。正在读的那个用强调色。"""

    def __init__(self, master, label, command):
        self._label = label
        self._icon = 'play'
        self._command = command
        self._enabled = True
        self._hot = False
        self._down = False
        self._latched = False
        self._font = tkfont.Font(family='Microsoft YaHei UI', size=12, weight='bold')
        width = self._font.measure(label) + 18 + 44
        super().__init__(
            master, width=width, height=28, highlightthickness=0, bd=0, cursor='hand2',
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

    def set_on(self, on):
        self._latched = bool(on)
        self.paint()

    def set_icon(self, icon):
        self._icon = icon or 'play'
        self.paint()

    def paint(self):
        self.delete('all')
        pal = self._pal
        self.configure(bg=pal['bar'])
        w = int(float(self.cget('width')))
        h = int(float(self.cget('height')))
        if self._latched:
            fill = SPEAK_PILL_PRESS if self._down else (SPEAK_PILL_HOVER if self._hot else SPEAK_PILL)
            fg = SPEAK_PILL_FG
        elif not self._enabled:
            fill = pal['btn_dis']
            fg = pal['fg_dis']
        elif self._down:
            fill = pal['btn_press']
            fg = pal['fg']
        elif self._hot:
            fill = pal['btn_hover']
            fg = pal['fg']
        else:
            fill = pal['btn']
            fg = pal['fg']
        _fill_round(self, 1, 1, w - 2, h - 2, h / 2.0 - 1, fill)
        text_w = self._font.measure(self._label)
        icon_s = 16
        gap = 6
        total = text_w + gap + icon_s
        x0 = (w - total) / 2.0
        self.create_text(x0 + text_w / 2.0, h / 2.0, text=self._label, fill=fg, font=self._font)
        _draw_icon(self, self._icon, x0 + text_w + gap + icon_s / 2.0, h / 2.0 + 0.5, fg, 18)

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


class _SpeedSlider(tk.Canvas):
    """速度滑块。左边 20 秒一行，右边 2 秒一行。"""

    def __init__(self, master, command, seconds=AUTO_DEFAULT):
        super().__init__(
            master, width=168, height=28, highlightthickness=0, bd=0, cursor='hand2',
        )
        self._command = command
        self._seconds = AUTO_DEFAULT
        self._drag = False
        self._pal = _TOOL['dark']
        self.bind('<ButtonPress-1>', self._press)
        self.bind('<B1-Motion>', self._motion)
        self.bind('<ButtonRelease-1>', self._release)
        self.bind('<Configure>', lambda e: self.paint())
        self.set_seconds(seconds)

    def seconds(self):
        return self._seconds

    def set_palette(self, pal):
        self._pal = pal
        self.paint()

    def set_seconds(self, seconds, notify=False):
        self._seconds = pos_seconds(seconds)
        self.paint()
        if notify and self._command:
            self._command(self._seconds)

    def _seconds_at(self, x):
        w = max(1, int(self.winfo_width()))
        left = 8
        right = max(left + 1, w - 8)
        span = float(right - left)
        return seconds_from_pos((float(x) - left) / span)

    def paint(self):
        self.delete('all')
        pal = self._pal
        self.configure(bg=pal['bar'])
        try:
            w = int(self.winfo_width())
            h = int(self.winfo_height())
        except tk.TclError:
            return
        if w < 8 or h < 8:
            w, h = 168, 28
        left, right = 8, max(9, w - 8)
        cy = h / 2.0
        self.create_rectangle(left, cy - 2, right, cy + 2, fill=pal.get('track') or pal['line'], outline='')
        x = left + (right - left) * pos_from_seconds(self._seconds)
        r = 6
        color = PILL_PRESS if self._drag else PILL
        self.create_oval(x - r, cy - r, x + r, cy + r, fill=color, outline='')

    def _press(self, e):
        self._drag = True
        self.set_seconds(self._seconds_at(e.x), notify=True)
        return 'break'

    def _motion(self, e):
        if not self._drag:
            return 'break'
        self.set_seconds(self._seconds_at(e.x), notify=True)
        return 'break'

    def _release(self, e):
        if self._drag:
            self.set_seconds(self._seconds_at(e.x), notify=True)
        self._drag = False
        self.paint()
        return 'break'


def speed_label_chars(font):
    """「2秒/行」到「20秒/行」里最宽的一档，按数字 0 的宽度折成字符数。

    标签宽度跟着秒数变时，居中的工具栏会左右抖。
    """
    zero = max(1, int(font.measure('0')))
    wide = max(int(font.measure('%d秒/行' % n)) for n in range(AUTO_FAST, AUTO_SLOW + 1))
    return max(1, (wide + zero - 1) // zero)


# SAPI：异步朗读。清掉时必须带异步，同步的空 Speak 会一直等到这句念完。
_SVS_ASYNC = 1
_SVS_PURGE = 2
_SPRS_SPEAKING = 2
_SPEAK_PLAY = '\uE768'
_SPEAK_PAUSE = '\uE769'


def _text_pad(view, name):
    try:
        raw = str(view.cget(name) or '0')
    except tk.TclError:
        return 0
    token = raw.replace('{', ' ').replace('}', ' ').split()
    try:
        return max(0, int(float(token[0])))
    except (ValueError, IndexError, TypeError):
        return 0


def visible_speak_start(view):
    """屏幕上第一行有字的位置。上面滚出屏幕的内容不念。"""
    try:
        end = view.index('end-1c')
        if view.compare(end, '<=', '1.0'):
            return '1.0'
        hit = view.index('@%d,%d' % (_text_pad(view, 'padx'), _text_pad(view, 'pady')))
        height = int(view.winfo_height())
    except tk.TclError:
        return '1.0'
    for _ in range(100):
        if view.compare(hit, '>=', end):
            return hit
        try:
            line_start = view.index('%s display linestart' % hit)
            line_end = view.index('%s display lineend' % hit)
        except tk.TclError:
            return hit
        chunk = plain_widget(view, line_start, line_end).replace('\u00a0', ' ').strip()
        if chunk:
            return line_start
        try:
            nxt = view.index('%s display lineend + 1c' % line_start)
        except tk.TclError:
            return line_start
        if view.compare(nxt, '<=', hit):
            return line_start
        if height > 1:
            try:
                box = view.bbox(nxt)
            except tk.TclError:
                box = None
            if box is None or box[1] >= height:
                return nxt
        hit = nxt
    return hit


def _app_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def speakable_text(view):
    """从屏幕第一行有字的地方念到文末。空文件和打不开的提示不念。"""
    try:
        text = plain_widget(view, visible_speak_start(view))
    except tk.TclError:
        return ''
    text = (text or '').replace('\u00a0', ' ').strip()
    if not text or text == '（空文件）' or text.startswith('无法打开：'):
        return ''
    return text


def _plain_char_stream(view, start, end=None):
    """从 start 起，按 plain_widget 的规则列出每个可见字及其文本框位置。"""
    start = view.index(start)
    end = view.index(end or 'end-1c')
    if view.compare(start, '>=', end):
        return []
    try:
        ranges = list(view.tag_ranges('brk'))
    except tk.TclError:
        ranges = []
    chunks = []
    cursor = start
    def take(a, b):
        text = view.get(a, b)
        off = 0
        for ch in text:
            idx = view.index('%s + %dc' % (a, off))
            chunks.append((idx, ' ' if ch == '\u00a0' else ch))
            off += 1
    for i in range(0, len(ranges), 2):
        a, b = str(ranges[i]), str(ranges[i + 1])
        if view.compare(b, '<=', cursor):
            continue
        if view.compare(a, '>=', end):
            break
        cut = a if view.compare(a, '<', end) else end
        if view.compare(cursor, '<', cut):
            take(cursor, cut)
        if view.compare(b, '>', cursor):
            cursor = b
        if view.compare(cursor, '>=', end):
            return chunks
    if view.compare(cursor, '<', end):
        take(cursor, end)
    return chunks


def locate_speak_ranges(view, start, pieces):
    """把朗读分段对回文本框区间。分段里被收掉的空白在正文里会跳过。"""
    try:
        stream = _plain_char_stream(view, start)
    except tk.TclError:
        return [None] * len(pieces or [])
    i = 0
    n = len(stream)
    while i < n and stream[i][1].isspace():
        i += 1
    found = []
    for piece in pieces or []:
        piece = piece or ''
        while i < n and stream[i][1].isspace() and (not piece or not piece[0].isspace()):
            i += 1
        begin = i
        j = 0
        while j < len(piece) and i < n:
            pch, sch = piece[j], stream[i][1]
            if pch.isspace():
                if sch.isspace():
                    while i < n and stream[i][1].isspace():
                        i += 1
                j += 1
                while j < len(piece) and piece[j].isspace():
                    j += 1
                continue
            if sch.isspace():
                i += 1
                continue
            if sch != pch:
                break
            i += 1
            j += 1
        if j < len(piece) or begin >= i:
            found.append(None)
            continue
        a = stream[begin][0]
        last = stream[i - 1][0]
        b = view.index('%s + 1c' % last)
        found.append((a, b, piece))
    return found


def _font_ascent_descent(view, index):
    font = None
    try:
        for tag in view.tag_names(index):
            raw = view.tag_cget(tag, 'font')
            if raw:
                font = raw
    except tk.TclError:
        font = None
    if not font:
        try:
            font = view.cget('font')
        except tk.TclError:
            font = 'TkDefaultFont'
    try:
        ascent = int(view.tk.call('font', 'metrics', font, '-ascent'))
        descent = int(view.tk.call('font', 'metrics', font, '-descent'))
    except tk.TclError:
        ascent, descent = 20, 5
    return ascent, descent


def _text_inset(view, option):
    inset = 0
    for name in ('highlightthickness', 'borderwidth', option):
        try:
            raw = view.cget(name)
        except tk.TclError:
            continue
        if raw in (None, ''):
            continue
        try:
            inset += int(float(raw))
        except (TypeError, ValueError):
            continue
    return inset


def speak_display_starts(view, start, end):
    """[start, end) 覆盖到的每一显示行的起点。"""
    found = []
    try:
        if view.compare(start, '>=', end):
            return found
        idx = start
    except tk.TclError:
        return found
    for _ in range(400):
        try:
            if view.compare(idx, '>=', end):
                break
            found.append(idx)
            line_end = view.index('%s display lineend' % idx)
            nxt = view.index('%s + 1c' % line_end)
            if view.compare(nxt, '<=', idx):
                break
            idx = nxt
        except tk.TclError:
            break
    return found


def speak_line_at(view, start, end, progress=0.0):
    """按字符进度挑当前该画线的显示行。progress=0 落在第一行，1 落在最后一行。"""
    starts = speak_display_starts(view, start, end)
    if not starts:
        return start
    if len(starts) == 1:
        return starts[0]
    try:
        progress = max(0.0, min(1.0, float(progress or 0.0)))
    except (TypeError, ValueError):
        progress = 0.0
    weights = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else end
        try:
            n = view.tk.call(view._w, 'count', '-chars', s, e)
            n = int(n or 0)
        except (tk.TclError, TypeError, ValueError):
            n = 1
        weights.append(max(1, n))
    total = float(sum(weights))
    pos = progress * total
    acc = 0.0
    chosen = starts[0]
    for s, w in zip(starts, weights):
        acc += w
        chosen = s
        if pos <= acc:
            break
    return chosen


def _hex_rgb(color):
    raw = str(color or '').strip()
    if raw.startswith('#'):
        raw = raw[1:]
    if len(raw) == 3:
        raw = ''.join(ch * 2 for ch in raw)
    if len(raw) != 6:
        return (17, 17, 17)
    try:
        return tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (17, 17, 17)


def mix_hex(src, dst, t):
    """t=0 为 src，t=1 为 dst。"""
    try:
        t = max(0.0, min(1.0, float(t)))
    except (TypeError, ValueError):
        t = 0.0
    ar, ag, ab = _hex_rgb(src)
    br, bgc, bb = _hex_rgb(dst)
    return '#%02X%02X%02X' % (
        int(round(ar + (br - ar) * t)),
        int(round(ag + (bgc - ag) * t)),
        int(round(ab + (bb - ab) * t)),
    )


def _speak_line_y(view, idx):
    """显示行墨水下方留出空隙后的 2px 线的 place-y。看不见则 None。"""
    try:
        info = view.dlineinfo(idx)
    except tk.TclError:
        return None
    if not info:
        return None
    _lx, y, _lw, h, baseline = (int(v) for v in info)
    if h < 8:
        return None
    pad_y = _text_inset(view, 'pady')
    ascent, descent = _font_ascent_descent(view, idx)
    typo_bot = int(y) + int(baseline) + int(descent)
    want = typo_bot + int(SPEAK_LINE_CLEAR)
    max_top = int(y) + int(h) - SPEAK_LINE_H
    try:
        line_end = view.index('%s display lineend' % idx)
        nxt = view.index('%s + 1c' % line_end)
        nxt_info = view.dlineinfo(nxt)
        nxt_txt = view.get(nxt, '%s display lineend' % nxt)
    except tk.TclError:
        nxt_info, nxt_txt = None, ''
    if typo_bot > int(y) + int(h):
        return None
    if nxt_info and int(nxt_info[3]) >= 8 and (nxt_txt or '').strip():
        nxt_ascent, _nxt_desc = _font_ascent_descent(view, nxt)
        nxt_typo_top = int(nxt_info[1]) + int(nxt_info[4]) - int(nxt_ascent)
        # 下一行 spacing2 常有一部分垫在行首，线可以落到这一截空档里，不要被本行 box 底卡住。
        max_top = nxt_typo_top - SPEAK_LINE_H
    top = min(want, max_top)
    if top < typo_bot + 1 and max_top >= typo_bot + 1:
        top = typo_bot + 1
    elif top < typo_bot and max_top >= typo_bot:
        top = typo_bot
    if top > max_top:
        top = max_top
    return int(top - pad_y)


def speak_line_rect(view, idx):
    """当前显示行下一条通栏 2px 线。坐标给 Text.place 用。看不见则 None。"""
    y = _speak_line_y(view, idx)
    if y is None:
        return None
    pad_x = _text_inset(view, 'padx')
    try:
        vis_w = int(view.winfo_width() or 0)
    except tk.TclError:
        vis_w = 0
    if vis_w < 1:
        return None
    return (-pad_x, y, vis_w, SPEAK_LINE_H)


def speak_line_rects(view, start, end, progress=0.0):
    """兼容旧调用：只返回当前应画的那一条通栏线。"""
    try:
        if view.compare(start, '>=', end):
            return []
        idx = speak_line_at(view, start, end, progress)
    except tk.TclError:
        return []
    rect = speak_line_rect(view, idx)
    return [rect] if rect else []


def configure_speak_line(view):
    """朗读线改由 2px 细条叠加，不再用 tag 下划线或底色。"""
    try:
        view.tag_configure('speak_line', underline=0, background='')
    except tk.TclError:
        pass


class _WinSpeaker:
    """用 Windows 自带语音念完一段。朗读在单独的线程里，不卡住界面。"""

    def __init__(self, on_done, voice_factory=None):
        self._on_done = on_done
        self._factory = voice_factory
        self._q = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name='reader-speak', daemon=True)
        self._thread.start()

    def start(self, text, gen):
        self._q.put(('say', (text, gen)))

    def pause(self):
        self._q.put(('pause', None))

    def resume(self):
        self._q.put(('resume', None))

    def stop(self):
        self._q.put(('stop', None))

    def close(self):
        self._q.put(('quit', None))

    def _notify(self, gen):
        fn = self._on_done
        if fn is None:
            return
        try:
            fn(gen)
        except Exception:
            pass

    def _loop(self):
        import pythoncom
        pythoncom.CoInitialize()
        voice = None
        try:
            try:
                if self._factory is not None:
                    voice = self._factory()
                else:
                    import win32com.client
                    voice = win32com.client.Dispatch('SAPI.SpVoice')
            except Exception:
                voice = None
            while True:
                cmd, payload = self._q.get()
                if cmd == 'quit':
                    _sapi_purge(voice)
                    break
                if cmd == 'stop':
                    _sapi_purge(voice)
                    continue
                if cmd == 'pause':
                    _sapi_pause(voice)
                    continue
                if cmd == 'resume':
                    _sapi_resume(voice)
                    continue
                if cmd != 'say' or voice is None:
                    if cmd == 'say':
                        self._notify(payload[1])
                    continue
                text, gen = payload
                try:
                    _sapi_purge(voice)
                    voice.Speak(text, _SVS_ASYNC)
                except Exception:
                    self._notify(gen)
                    continue
                if not self._wait_until_quiet(voice, gen):
                    break
        finally:
            pythoncom.CoUninitialize()

    def _wait_until_quiet(self, voice, gen):
        """念完返回 True。暂停时不当成念完。中途退出则清掉声音并返回 False。"""
        paused = False
        while True:
            try:
                cmd, payload = self._q.get(timeout=0.05)
            except queue.Empty:
                cmd = None
            if cmd == 'quit':
                _sapi_purge(voice)
                return False
            if cmd == 'stop':
                _sapi_purge(voice)
                return True
            if cmd == 'pause':
                if _sapi_pause(voice):
                    paused = True
                continue
            if cmd == 'resume':
                _sapi_resume(voice)
                paused = False
                continue
            if cmd == 'say':
                text, gen = payload
                paused = False
                try:
                    _sapi_purge(voice)
                    voice.Speak(text, _SVS_ASYNC)
                except Exception:
                    self._notify(gen)
                    return True
                continue
            if paused:
                continue
            try:
                import pythoncom
                pythoncom.PumpWaitingMessages()
            except Exception:
                pass
            try:
                state = int(voice.Status.RunningState)
            except Exception:
                state = 1
            if state != _SPRS_SPEAKING:
                self._notify(gen)
                return True


def _sapi_pause(voice):
    if voice is None:
        return False
    try:
        voice.Pause()
        return True
    except Exception:
        return False


def _sapi_resume(voice):
    if voice is None:
        return
    try:
        voice.Resume()
    except Exception:
        pass


def _sapi_purge(voice):
    """打断正在念的内容。先恢复，再跳过剩余句子，最后异步清掉队列。"""
    if voice is None:
        return
    _sapi_resume(voice)
    try:
        voice.Skip('Sentence', 0x7fffffff)
    except Exception:
        pass
    try:
        voice.Speak('', _SVS_PURGE | _SVS_ASYNC)
    except Exception:
        pass


def pos_seconds(seconds):
    """收成 2 到 20 的整数秒。"""
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        seconds = AUTO_DEFAULT
    if seconds < AUTO_FAST:
        return AUTO_FAST
    if seconds > AUTO_SLOW:
        return AUTO_SLOW
    return seconds


def nkey(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]


def _clamp01(pos):
    try:
        pos = float(pos)
    except (TypeError, ValueError):
        return 0.0
    if pos < 0.0:
        return 0.0
    if pos > 1.0:
        return 1.0
    return pos


def seconds_from_pos(pos):
    """滑块位置 0 在左，20 秒一行；1 在右，2 秒一行。"""
    span = AUTO_SLOW - AUTO_FAST
    steps = int(_clamp01(pos) * span + 0.5)
    if steps > span:
        steps = span
    return AUTO_SLOW - steps


def pos_from_seconds(seconds):
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        seconds = AUTO_DEFAULT
    if seconds < AUTO_FAST:
        seconds = AUTO_FAST
    elif seconds > AUTO_SLOW:
        seconds = AUTO_SLOW
    return (AUTO_SLOW - seconds) / float(AUTO_SLOW - AUTO_FAST)


def auto_scroll_pixels(acc, line_px, dt, seconds):
    """按「几秒一行」把这一拍换成往下滚的像素。

    acc 是还没滚完的余数，单位是「像素 × 毫秒」。
    返回 (新余数, 本拍像素)。一拍最长算 250 毫秒，避免卡住之后猛地跳一截。
    """
    line_px = max(1, int(line_px))
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        seconds = AUTO_DEFAULT
    if seconds < AUTO_FAST:
        seconds = AUTO_FAST
    elif seconds > AUTO_SLOW:
        seconds = AUTO_SLOW
    try:
        ms = int(round(float(dt) * 1000.0))
    except (TypeError, ValueError):
        ms = 0
    if ms < 0:
        ms = 0
    elif ms > 250:
        ms = 250
    acc = int(acc) + line_px * ms
    denom = seconds * 1000
    step = acc // denom
    acc -= step * denom
    return acc, step


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

# 资源管理器把文件夹拖到目录名上时，系统发 WM_DROPFILES。只接这一条，不换整窗。
WM_DROPFILES = 0x0233
_TREE_DROP = {}

def _drop_paths(hdrop):
    shell32 = ctypes.windll.shell32
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT
    shell32.DragFinish.argtypes = [wintypes.HANDLE]
    shell32.DragFinish.restype = None
    paths = []
    try:
        count = int(shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0))
        buf = ctypes.create_unicode_buffer(32768)
        for i in range(count):
            if shell32.DragQueryFileW(hdrop, i, buf, len(buf)):
                paths.append(buf.value)
    finally:
        shell32.DragFinish(hdrop)
    return paths

WM_DESTROY = 0x0002

def _release_tree_drop(view):
    """关掉窗口时卸掉目录名上的拖放过程，避免还握着阅读器。"""
    owned = [hwnd for hwnd, item in list(_TREE_DROP.items()) if item[1] is view]
    get = ctypes.windll.user32.GetWindowLongPtrW
    get.restype = ctypes.c_void_p
    get.argtypes = [wintypes.HWND, ctypes.c_int]
    put = ctypes.windll.user32.SetWindowLongPtrW
    put.restype = ctypes.c_void_p
    put.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    ours = int(ctypes.cast(_TREE_DROP_PROC, ctypes.c_void_p).value or 0)
    for hwnd in owned:
        old, _view = _TREE_DROP.pop(hwnd)
        try:
            if int(get(hwnd, -4) or 0) == ours:
                put(hwnd, -4, ctypes.c_void_p(int(old)))
        except Exception:
            pass

def _tree_drop_proc(hwnd, msg, wparam, lparam):
    item = _TREE_DROP.get(int(hwnd))
    if not item:
        return 0
    old, view = item
    if msg in (WM_DESTROY, WM_NCDESTROY):
        _release_tree_drop(view)
        return _call_wnd_proc(old, hwnd, msg, wparam, lparam)
    if msg == WM_DROPFILES:
        # 这里还在系统拖放的消息循环里。碰 Tk 会让解释器直接退出。
        try:
            view._tree_pending = _drop_paths(wparam)
            view._tree_pending_at = time.monotonic()
        except Exception:
            pass
        return 0
    return _call_wnd_proc(old, hwnd, msg, wparam, lparam)

_TREE_DROP_PROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)(_tree_drop_proc)


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


def openable_names(folder):
    """当前目录里能打开的文件。不含子目录。"""
    found = []
    try:
        names = os.listdir(folder)
    except OSError:
        return found
    for name in names:
        if os.path.splitext(name)[1].lower() not in EXTS:
            continue
        if os.path.isfile(os.path.join(folder, name)):
            found.append(name)
    found.sort(key=nkey)
    return found


def _file_kind(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in WORD_EXTS:
        return 'word'
    if ext in PDF_EXTS:
        return 'pdf'
    if ext in MD_EXTS:
        return 'md'
    if ext in TXT_EXTS:
        return 'txt'
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


def fill_plain(view, src):
    """Word / PDF 按抽出的文字逐行放下，不再按 Markdown 解释。"""
    clear_quote_bars(view)
    view.delete('1.0', 'end')
    text = (src or '').replace('\r\n', '\n').replace('\r', '\n').strip('\n')
    if not text.strip():
        _insert_text(view, '（空文件）\n', ('muted',))
        return
    for line in text.split('\n'):
        tag = 'muted' if re.fullmatch(r'第 \d+ 页', line.strip()) else 'p'
        _insert_text(view, line + '\n', (tag,))


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
        self._auto_on = False
        self._auto_job = None
        self._auto_acc = 0
        self._auto_seconds = AUTO_DEFAULT
        self._speak_on = False
        self._speak_paused = False
        self._speak_gen = 0
        self._speak_ranges = []
        self._speak_range_i = 0
        self._speak_line_range = None
        self._speak_line_idx = None
        self._speak_line_prev = None
        self._speak_bars = []
        self._speak_anim_job = None
        self._speak_scroll = None
        self._speak_scroll_animating = False
        self._speak_fade_in_t0 = None
        self._speak_fade_out_t0 = None
        self._speak_progress_job = None
        self._speak_progress = None
        self._voice = '男'
        self._speak_done = queue.Queue()
        self._speaker = None
        self._speak_poll = None
        self._speak_note = ''
        self._idle_revert_job = None
        self._idle_revert_for = None
        self._cosy = None
        self._cosy_q = queue.Queue()
        self._cosy_poll = None
        self._auto_last = 0.0
        self._status_base = ''
        self._hint_text = ''
        self._save_job = None
        self._icon_img = None
        self._images = []
        self._md_cache = None
        self._base = ''
        self._snap_border = snap_border()
        self._snap_hwnd = 0
        self._tk_width_extra = 0
        self._snap_modal = False
        self._sys_pending = None
        self._pending_move = None
        self._max_hot = False
        self._cur_name = ''
        self._tree_on = False
        self._tree_folder = None
        self._tree_hold = None
        self._tree_follow = True
        self._tree_pending = None
        self._tree_pending_at = 0.0
        self._tree_drop_job = None
        self._tree_paths = []
        self._tree_busy = False
        root.title(TITLE)
        root.configure(bg=BG)
        root.withdraw()
        self._install_snap_frame()
        self._apply_icon()
        self._build_title()
        foot = _foot_palette()
        self.bar_row = tk.Frame(root, bg=BG, height=32)
        self.bar_row.pack(fill='x', side='bottom')
        self.bar_row.pack_propagate(False)
        self.zoom_box = tk.Frame(self.bar_row, bg=BG)
        self.btn_zoom_out = _IconButton(self.zoom_box, self._zoom_out, icon='zoom_out')
        self.btn_zoom_in = _IconButton(self.zoom_box, self._zoom_in, icon='zoom_in')
        self.zoom_lbl = tk.Label(
            self.zoom_box, text='100%', bg=BG, fg=foot['fg'],
            font=('Microsoft YaHei UI', 11), width=5, cursor='hand2',
        )
        self.zoom_lbl.bind('<Button-1>', self._choose_zoom)
        self.btn_zoom_out.pack(side='left', padx=(0, 2))
        self.zoom_lbl.pack(side='left', padx=(0, 2))
        self.btn_zoom_in.pack(side='left')
        self.zoom_box.pack(side='right', padx=(4, 10), pady=2)
        self.bar = tk.Label(
            self.bar_row, bg=BG, fg='#888888', font=('Microsoft YaHei UI', 9), anchor='w',
        )
        self.bar.pack(side='left', fill='both', expand=True, padx=(10, 8))
        self.btn_zoom_out.set_palette(foot)
        self.btn_zoom_in.set_palette(foot)
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
        configure_speak_line(self.view)
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
        for seq in ('<MouseWheel>', '<Up>', '<Down>', '<Prior>', '<Next>', '<Home>', '<End>'):
            self.view.bind(seq, self._stop_if_auto, add='+')
        self.view.bind('<space>', self._toggle_speak_key)
        root.bind('<space>', self._toggle_speak_key)
        self.view.pack(side='left', fill='both', expand=True)
        self._build_tree(body)
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
        self.view.bind('<Configure>', lambda e: self._paint_speak_line(), add='+')
        self.pill.bind('<ButtonPress-1>', self._pill_press)
        self.pill.bind('<B1-Motion>', self._pill_motion)
        self.pill.bind('<ButtonRelease-1>', self._pill_release)
        self.pill.bind('<Enter>', self._pill_enter)
        self.pill.bind('<Leave>', self._pill_leave)
        self.pill.bind('<MouseWheel>', self._pill_wheel)
        self.pill.bind('<Configure>', lambda e: self._paint_pill())
        self.view.bind('<Key>', self._eat_key)
        self.view.bind('<<Paste>>', lambda e: 'break')
        self.view.bind('<Control-a>', self._select_all)
        self.view.bind('<Control-Left>', lambda e: self.step(-1))
        self.view.bind('<Control-Right>', lambda e: self.step(1))
        self.view.bind('<Control-o>', lambda e: self.ask())
        root.bind('<Control-o>', lambda e: self.ask())
        self.view.bind('<Escape>', self.esc)
        root.bind('<Escape>', self.esc)
        root.bind('<F11>', self.fs)
        root.bind('<Configure>', self._on_root_configure)
        root.protocol('WM_DELETE_WINDOW', self.close)
        for w in (root, self.view, self.title_bar, self.bar, self.bar_row, self.zoom_box, body):
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
        self._tools_layouting = False

        left = tk.Frame(inner, bg=pal['bar'])
        mid = tk.Frame(inner, bg=pal['bar'])
        right = tk.Frame(inner, bg=pal['bar'])
        self.tool_left, self.tool_mid, self.tool_right = left, mid, right

        self.btn_tree = _IconButton(left, self._toggle_tree, icon='tree')
        self.btn_prev = _IconButton(left, lambda: self.step(-1), icon='prev')
        self.btn_next = _IconButton(left, lambda: self.step(1), icon='next')
        self._sep_nav = _ToolSep(left)
        self.btn_tree.pack(side='left', padx=(0, 2))
        self._sep_nav.pack(side='left')
        self.btn_prev.pack(side='left', padx=(0, 2))
        self.btn_next.pack(side='left')

        self.auto_box = mid
        self.btn_auto = _WordButton(mid, '自动滚屏', self._toggle_auto)
        self.speed = _SpeedSlider(mid, self._on_auto_speed, AUTO_DEFAULT)
        self._speed_font = tkfont.Font(family='Microsoft YaHei UI', size=11)
        self.speed_lbl = tk.Label(
            mid, text='%d秒/行' % AUTO_DEFAULT, bg=pal['bar'], fg=pal['fg'],
            font=self._speed_font, anchor='e', width=speed_label_chars(self._speed_font),
        )
        self.btn_auto.pack(side='left')
        self.speed.pack(side='left', padx=(8, 0))
        self.speed_lbl.pack(side='left', padx=(6, 0))

        self.voice_box = tk.Frame(right, bg=pal['bar'])
        self.btn_voice_m = _VoiceButton(self.voice_box, '男', lambda: self._on_voice_btn('男'))
        self.btn_voice_f = _VoiceButton(self.voice_box, '女', lambda: self._on_voice_btn('女'))
        self.btn_voice_m.pack(side='left', padx=(0, 6))
        self.btn_voice_f.pack(side='left')
        self.voice_box.pack(side='left')
        self._speak_status_font = tkfont.Font(family='Microsoft YaHei UI', size=11)
        self.speak_status_box = tk.Frame(
            inner, bg=pal['bar'], width=SPEAK_STATUS_W, height=28,
            highlightthickness=0, bd=0,
        )
        self.speak_status_box.pack_propagate(False)
        self.speak_status = tk.Label(
            self.speak_status_box, text='', bg=pal['bar'], fg=pal['fg'],
            font=self._speak_status_font, anchor='w', justify='left',
        )
        self.speak_status.pack(side='left', fill='y', padx=0, pady=0)
        # 200px 状态块仍贴工具栏右缘，「女」在它左边；文字在块内左对齐，超出直接裁切。

        left.pack(side='left', padx=(10, 0), pady=8)
        right.pack(side='right', padx=(0, 10), pady=8)

        self._wire_hint(self.btn_tree, '目录树')
        self._wire_hint(self.btn_prev, '上一篇')
        self._wire_hint(self.btn_next, '下一篇')
        self._wire_hint(self.btn_auto, '自动滚屏')
        self._wire_hint(self.btn_voice_m, '男声朗读，点击播放或暂停，Esc 停止')
        self._wire_hint(self.btn_voice_f, '女声朗读，点击播放或暂停，Esc 停止')
        self._wire_hint(self.speed, '速度，左边 20 秒一行，右边 2 秒一行')
        self._wire_hint(self.speed_lbl, '速度，左边 20 秒一行，右边 2 秒一行')
        self._wire_hint(self.btn_zoom_out, '缩小')
        self._wire_hint(self.btn_zoom_in, '放大')
        self._wire_hint(self.zoom_lbl, '选择比例')
        for w in (bar, inner, line, left, mid, right, self.voice_box):
            w.bind('<ButtonPress-1>', self._press, add='+')
            w.bind('<B1-Motion>', self._motion, add='+')
            w.bind('<ButtonRelease-1>', self._release, add='+')
            w.bind('<Motion>', self._hover, add='+')
        inner.bind('<Configure>', self._layout_tools, add='+')
        self._apply_tool_theme()
        self._paint_voice()
        self._paint_speak_btn()
        self._sync_zoom_btns()
        self.root.bind('<FocusIn>', self._sync_tool_theme, add='+')

    def _ellipsize(self, text, max_px, font=None):
        text = text or ''
        if max_px <= 0 or not text:
            return ''
        font = font or getattr(self, '_speak_status_font', None)
        if font is None:
            return text
        if font.measure(text) <= max_px:
            return text
        ell = '…'
        if font.measure(ell) > max_px:
            return ''
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.measure(text[:mid] + ell) <= max_px:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + ell

    def _layout_tools(self, e=None):
        """窄窗口时先收起速度滑块；200px 状态块固定贴右缘，「女」在它左边。"""
        if getattr(self, '_tools_layouting', False):
            return
        inner = getattr(self, 'tool_inner', None)
        if inner is None:
            return
        self._tools_layouting = True
        try:
            w = int(inner.winfo_width() or 0)
            if w <= 1:
                return
            self.speed.pack_forget()
            self.speed_lbl.pack_forget()
            # 已经 place 的组不要再 forget，否则 Windows 上画布按钮会暂时或一直变空白。
            if self.tool_mid.winfo_manager() == 'pack':
                self.tool_mid.pack_forget()
            if self.tool_right.winfo_manager() == 'pack':
                self.tool_right.pack_forget()
            inner.update_idletasks()
            w = int(inner.winfo_width() or w)
            left_w = self.tool_left.winfo_reqwidth()
            auto_w = self.btn_auto.winfo_reqwidth()
            speed_w = 8 + self.speed.winfo_reqwidth()
            lbl_w = 6 + self.speed_lbl.winfo_reqwidth()
            voice_w = self.tool_right.winfo_reqwidth()
            pad = 24
            left_pad, right_pad = 10, 10
            status_w = SPEAK_STATUS_W
            gap_status = 8
            self._sync_speak_status()

            def fits(mid_need):
                return left_w + mid_need + voice_w + gap_status + status_w + pad <= w

            if fits(auto_w + speed_w + lbl_w):
                show_speed = show_speed_lbl = True
            elif fits(auto_w + speed_w):
                show_speed, show_speed_lbl = True, False
            else:
                show_speed = show_speed_lbl = False
            if show_speed:
                self.speed.pack(side='left', padx=(8, 0))
            if show_speed and show_speed_lbl:
                self.speed_lbl.pack(side='left', padx=(6, 0))
            mid_w = auto_w
            if show_speed:
                mid_w += speed_w
            if show_speed and show_speed_lbl:
                mid_w += lbl_w
            # 200px 块固定贴右缘，不随文字伸缩；声音胶囊在它左边。
            voice_x = w - right_pad - status_w - gap_status - voice_w
            if voice_x < left_pad + left_w:
                voice_x = left_pad + left_w
            self.tool_right.place(x=int(voice_x), rely=0.5, anchor='w')
            box = getattr(self, 'speak_status_box', None)
            if box is not None:
                box.place(x=int(w - right_pad), rely=0.5, anchor='e', width=int(status_w), height=28)
            gap_x = left_pad + left_w
            gap_r = voice_x
            if mid_w <= (gap_r - gap_x):
                self.tool_mid.place(x=int(gap_x + (gap_r - gap_x - mid_w) / 2), rely=0.5, anchor='w')
            else:
                # 中间放不下就收起，避免叠到声音胶囊或 200px 状态块上。
                if self.tool_mid.winfo_manager() == 'place':
                    self.tool_mid.place_forget()
                elif self.tool_mid.winfo_manager() == 'pack':
                    self.tool_mid.pack_forget()
            for w in (self.btn_voice_m, self.btn_voice_f, self.btn_auto, self.speed):
                if hasattr(w, 'paint'):
                    try:
                        w.paint()
                    except tk.TclError:
                        pass
            self._paint_voice_btns()
        finally:
            self._tools_layouting = False

    def _wire_hint(self, widget, text):
        widget.bind('<Enter>', lambda e, t=text: self._set_hint(t), add='+')
        widget.bind('<Leave>', lambda e: self._set_hint(''), add='+')

    def _set_status(self, text):
        self._status_base = text or ''
        self._paint_bar()

    def _set_hint(self, text):
        self._hint_text = text or ''
        self._paint_bar()

    def _tts_model_label(self):
        try:
            return cosy_speak.tts_model_label(_app_root())
        except Exception:
            return getattr(cosy_speak, 'TTS_MISSING', 'TTS: 未安装')

    def _transient_speak_note(self, note=None):
        shown = brief_speak_note(note if note is not None else (getattr(self, '_speak_note', '') or ''))
        return shown in ('已停止', '读完了') or shown.startswith('出错：')

    def _cancel_idle_revert(self):
        job = getattr(self, '_idle_revert_job', None)
        self._idle_revert_job = None
        self._idle_revert_for = None
        if job is not None:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass

    def _arm_idle_revert(self):
        note = brief_speak_note(getattr(self, '_speak_note', '') or '')
        if not self._transient_speak_note(note):
            self._cancel_idle_revert()
            return
        if getattr(self, '_idle_revert_job', None) is not None and getattr(self, '_idle_revert_for', None) == note:
            return
        self._cancel_idle_revert()
        self._idle_revert_for = note
        try:
            self._idle_revert_job = self.root.after(SPEAK_IDLE_REVERT_MS, self._revert_speak_idle)
        except tk.TclError:
            self._idle_revert_job = None
            self._idle_revert_for = None

    def _revert_speak_idle(self):
        self._idle_revert_job = None
        self._idle_revert_for = None
        if getattr(self, '_speak_on', False):
            return
        if not self._transient_speak_note():
            return
        self._speak_note = ''
        self._sync_speak_status()

    def _sync_speak_status(self):
        lbl = getattr(self, 'speak_status', None)
        if lbl is None:
            return
        shown = brief_speak_note(getattr(self, '_speak_note', '') or '')
        idle = not shown
        if idle:
            shown = self._tts_model_label()
        pal = _TOOL.get(getattr(self, '_theme_name', None)) or _TOOL['dark']
        fg = pal.get('fg_dis') if idle else pal.get('fg')
        bg = pal.get('bar')
        try:
            lbl.config(text=shown, fg=fg, bg=bg)
        except tk.TclError:
            pass
        if self._transient_speak_note():
            self._arm_idle_revert()
        else:
            self._cancel_idle_revert()

    def _paint_bar(self):
        parts = [self._status_base, getattr(self, '_hint_text', '')]
        try:
            self.bar.config(text='    '.join(p for p in parts if p))
        except tk.TclError:
            pass
        if not getattr(self, '_tools_layouting', False):
            self._layout_tools()

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
        for fr in (self.tool_left, self.tool_mid, self.tool_right, self.auto_box, self.voice_box):
            fr.configure(bg=pal['bar'])
        for btn in (
            self.btn_tree, self.btn_prev, self.btn_next, self.btn_auto,
            self.btn_voice_m, self.btn_voice_f,
        ):
            btn.set_palette(pal)
        self._sep_nav.set_palette(pal)
        self.speed.set_palette(pal)
        self.speed_lbl.configure(bg=pal['bar'], fg=pal['fg'])
        if getattr(self, 'speak_status_box', None) is not None:
            self.speak_status_box.configure(bg=pal['bar'])
        if getattr(self, 'speak_status', None) is not None:
            self.speak_status.configure(bg=pal['bar'])
        self._sync_speak_status()
        foot = _foot_palette()
        if getattr(self, 'btn_zoom_out', None) is not None:
            self.btn_zoom_out.set_palette(foot)
            self.btn_zoom_in.set_palette(foot)
            self.zoom_lbl.configure(bg=BG, fg=foot['fg'])
            self.zoom_box.configure(bg=BG)
            self.bar_row.configure(bg=BG)
            self.bar.configure(bg=BG)

    def _sync_tool_nav(self):
        on = len(self.files) >= 2
        self.btn_prev.set_enabled(on)
        self.btn_next.set_enabled(on)

    def _on_auto_speed(self, seconds):
        self._auto_seconds = pos_seconds(seconds)
        lbl = getattr(self, 'speed_lbl', None)
        if lbl is not None:
            lbl.config(text='%d秒/行' % self._auto_seconds)

    def _toggle_auto(self):
        if self._auto_on:
            self._stop_auto()
        else:
            self._start_auto()

    def _paint_speak_btn(self):
        self._paint_voice_btns()

    def _paint_voice(self):
        self._paint_voice_btns()

    def _paint_voice_btns(self):
        voice = getattr(self, '_voice', '男')
        speaking = bool(getattr(self, '_speak_on', False))
        paused = bool(getattr(self, '_speak_paused', False))
        for name, which in (('btn_voice_m', '男'), ('btn_voice_f', '女')):
            btn = getattr(self, name, None)
            if btn is None:
                continue
            active = speaking and voice == which
            if hasattr(btn, 'set_icon'):
                btn.set_icon('pause' if (active and not paused) else 'play')
            if hasattr(btn, 'set_on'):
                btn.set_on(active)

    def _on_voice_btn(self, which):
        if which not in ('男', '女'):
            return
        current = getattr(self, '_voice', '男')
        # 正在读（含第一段还在合成）时，再点当前胶囊只切换暂停/继续，绝不重新开播。
        if getattr(self, '_speak_on', False) and current == which:
            self._toggle_speak()
            self._paint_voice_btns()
            return
        self._voice = which
        self._paint_voice_btns()
        self._begin_speak(restart=bool(getattr(self, '_speak_on', False)))

    def _pick_voice(self, which):
        # 兼容旧调用：只改音色；正在朗读则从当前可见句重读。
        if which not in ('男', '女'):
            return
        if which == getattr(self, '_voice', '男'):
            self._paint_voice_btns()
            return
        self._voice = which
        self._paint_voice_btns()
        if getattr(self, '_speak_on', False):
            self._begin_speak(restart=True)

    def _toggle_speak_key(self, e=None):
        self._toggle_speak()
        return 'break'

    def _toggle_speak(self):
        if self._speak_on and not self._speak_paused:
            self._pause_speak()
            return
        if self._speak_on and self._speak_paused:
            self._resume_speak()
            return
        self._begin_speak(restart=False)

    def _begin_speak(self, restart=False):
        text = speakable_text(self.view)
        if not text:
            if restart:
                self._stop_speak()
            return
        cfg = cosy_speak.resolve_voice(_app_root(), getattr(self, '_voice', '男'))
        if not cfg.get('ok'):
            if restart:
                self._stop_speak()
            self._speak_note = brief_speak_note(cfg.get('error') or '语音模型还不能用')
            self._paint_bar()
            return
        self._stop_auto()
        speaker = getattr(self, '_cosy', None) if restart else None
        if speaker is not None:
            speaker.stop()
        self._speak_gen += 1
        gen = self._speak_gen
        self._speak_on = True
        self._speak_paused = False
        self._speak_note = '合成中…' if speaker is not None else '加载模型…'
        self._prepare_speak_ranges(text)
        self._paint_speak_btn()
        self._paint_bar()
        self._ensure_cosy().start(text, gen, cfg)
        self._arm_cosy_poll()

    def _pause_speak(self):
        if not self._speak_on or self._speak_paused:
            return
        self._speak_paused = True
        if getattr(self, '_speak_note', '') != '已暂停':
            self._speak_note_before_pause = getattr(self, '_speak_note', '') or ''
        self._speak_note = '已暂停'
        self._paint_speak_btn()
        self._paint_bar()
        speaker = getattr(self, '_cosy', None)
        if speaker is not None:
            speaker.pause()

    def _resume_speak(self):
        if not self._speak_on or not self._speak_paused:
            return
        self._speak_paused = False
        prev = getattr(self, '_speak_note_before_pause', '') or ''
        self._speak_note = prev if prev and prev != '已暂停' else '朗读中'
        self._paint_speak_btn()
        self._paint_bar()
        speaker = getattr(self, '_cosy', None)
        if speaker is not None:
            speaker.resume()
        self._arm_speak_progress()

    def _ensure_speaker(self):
        speaker = getattr(self, '_speaker', None)
        if speaker is None:
            speaker = _WinSpeaker(self._queue_speak_done)
            self._speaker = speaker
        return speaker

    def _queue_speak_done(self, gen):
        self._speak_done.put(gen)

    def _arm_speak_poll(self):
        if self._speak_poll is not None:
            return
        try:
            self._speak_poll = self.root.after(80, self._poll_speak_done)
        except tk.TclError:
            self._speak_poll = None

    def _poll_speak_done(self):
        self._speak_poll = None
        while True:
            try:
                gen = self._speak_done.get_nowait()
            except queue.Empty:
                break
            if gen == self._speak_gen and self._speak_on:
                self._speak_on = False
                self._speak_paused = False
                self._paint_speak_btn()
        if self._speak_on:
            self._arm_speak_poll()

    def _ensure_cosy(self):
        speaker = getattr(self, '_cosy', None)
        if speaker is None:
            speaker = cosy_speak.CosyReader(_app_root(), self._queue_cosy)
            self._cosy = speaker
        return speaker

    def _queue_cosy(self, gen, kind, text):
        self._cosy_q.put((gen, kind, text))

    def _arm_cosy_poll(self):
        if self._cosy_poll is not None:
            return
        try:
            self._cosy_poll = self.root.after(80, self._poll_cosy)
        except tk.TclError:
            self._cosy_poll = None

    def _poll_cosy(self):
        self._cosy_poll = None
        while True:
            try:
                gen, kind, text = self._cosy_q.get_nowait()
            except queue.Empty:
                break
            if gen != self._speak_gen:
                continue
            if kind == 'play' and self._speak_on:
                self._show_speak_line(text)
            elif kind == 'status' and self._speak_on:
                self._speak_note = brief_speak_note(text)
                self._paint_bar()
            elif kind == 'done' and self._speak_on:
                self._speak_on = False
                self._speak_paused = False
                self._speak_note = '读完了'
                self._clear_speak_line(fade=True)
                self._paint_speak_btn()
                self._paint_bar()
            elif kind == 'error':
                self._speak_on = False
                self._speak_paused = False
                self._speak_note = brief_speak_note(text)
                self._clear_speak_line(fade=True)
                self._paint_speak_btn()
                self._paint_bar()
        if self._speak_on:
            self._arm_cosy_poll()

    def _stop_speak(self, fade=None):
        was_on = bool(getattr(self, '_speak_on', False))
        self._speak_on = False
        self._speak_paused = False
        self._speak_gen += 1
        # 正在读时停掉显示「已停止」；换篇、关窗等空闲停只清掉。
        self._speak_note = '已停止' if was_on else ''
        if fade is None:
            fade = was_on
        self._clear_speak_line(fade=bool(fade))
        self._paint_speak_btn()
        self._paint_bar()
        for attr in ('_speaker', '_cosy'):
            speaker = getattr(self, attr, None)
            if speaker is not None:
                speaker.stop()

    def _prepare_speak_ranges(self, text):
        self._speak_range_i = 0
        try:
            start = visible_speak_start(self.view)
            pieces = cosy_speak.speak_pieces(text)
            self._speak_ranges = locate_speak_ranges(self.view, start, pieces)
        except tk.TclError:
            self._speak_ranges = []
        self._clear_speak_line()

    def _show_speak_line(self, piece):
        view = getattr(self, 'view', None)
        if view is None:
            return
        rng = None
        ranges = list(getattr(self, '_speak_ranges', []) or [])
        i = int(getattr(self, '_speak_range_i', 0) or 0)
        want = (piece or '').strip()
        # 按播放顺序取下一段；对不上就在余下的段里再找一次。
        if 0 <= i < len(ranges) and ranges[i] and ranges[i][2].strip() == want:
            rng = ranges[i]
            self._speak_range_i = i + 1
        else:
            for j in range(max(i, 0), len(ranges)):
                item = ranges[j]
                if item and item[2].strip() == want:
                    rng = item
                    self._speak_range_i = j + 1
                    break
        if rng is None:
            self._clear_speak_line(fade=True)
            return
        start, end, _txt = rng
        try:
            if view.compare(start, '>=', end):
                self._clear_speak_line(fade=True)
                return
        except tk.TclError:
            return
        self._speak_line_range = (start, end)
        idx = speak_line_at(view, start, end, 0.0)
        self._move_speak_line(idx, animate=True)
        self._arm_speak_progress()

    def _same_display_line(self, a, b):
        view = self.view
        try:
            return view.compare(
                view.index('%s display linestart' % a),
                '==',
                view.index('%s display linestart' % b),
            )
        except tk.TclError:
            return False

    def _move_speak_line(self, idx, animate=True):
        """把通栏线移到 idx 所在显示行，换行时淡出旧线、淡入新线，并滚动居中。"""
        if not idx:
            return
        cur = getattr(self, '_speak_line_idx', None)
        now = time.perf_counter()
        if cur and self._same_display_line(cur, idx):
            self._speak_line_idx = idx
            self._reveal_speak_line(idx, animate=animate)
            self._paint_speak_line()
            return
        if cur and animate:
            self._speak_line_prev = cur
            self._speak_fade_out_t0 = now
            self._speak_fade_in_t0 = now
        elif animate:
            self._speak_line_prev = None
            self._speak_fade_out_t0 = None
            self._speak_fade_in_t0 = now
        else:
            self._speak_line_prev = None
            self._speak_fade_out_t0 = None
            self._speak_fade_in_t0 = None
        self._speak_line_idx = idx
        self._reveal_speak_line(idx, animate=animate)
        self._arm_speak_anim()
        self._paint_speak_line()

    def _center_frac_for_index(self, idx):
        """让 idx 所在显示行竖向居中时，yview_moveto 要用的顶部比例。"""
        view = self.view
        try:
            vis_h = int(view.winfo_height() or 0)
            if vis_h <= 1:
                return None
            pad = _text_inset(view, 'pady')
            inner_h = vis_h - 2 * pad if vis_h > 2 * pad else vis_h

            def ypx(a, b):
                try:
                    n = view.tk.call(view._w, 'count', '-ypixels', a, b)
                    return int(n or 0)
                except (tk.TclError, TypeError, ValueError):
                    return 0

            start = view.index('%s display linestart' % idx)
            last = view.index('%s display lineend' % idx)
            top = ypx('1.0', start)
            nxt = view.index('%s + 1c' % last)
            if view.compare(nxt, '>', last):
                bottom = ypx('1.0', nxt)
            else:
                bottom = ypx('1.0', 'end')
            if bottom <= top:
                bottom = top + max(1, inner_h // 8)
            total = ypx('1.0', 'end')
            if total <= 0:
                return None
            mid = (top + bottom) / 2.0
            target = (mid - inner_h / 2.0) / float(total)
            return max(0.0, min(1.0, target))
        except tk.TclError:
            return None

    def _reveal_speak_line(self, start, end=None, animate=True):
        """朗读线一出现或换行，就把这一行滚到可视区正中。end 仅兼容旧调用。"""
        idx = start
        view = getattr(self, 'view', None)
        if view is None or not idx:
            return
        target = self._center_frac_for_index(idx)
        if target is None:
            return
        try:
            first, _last = view.yview()
            cur = float(first)
        except (tk.TclError, TypeError, ValueError):
            return
        # 已经几乎居中就不再滚，避免原地抖动。
        try:
            total = int(view.tk.call(view._w, 'count', '-ypixels', '1.0', 'end') or 0)
        except (tk.TclError, TypeError, ValueError):
            total = 0
        if total > 0 and abs(cur - target) * total < 2:
            return
        if not animate:
            try:
                view.yview_moveto(target)
            except tk.TclError:
                pass
            return
        # 进行中的动画被新目标接走，从当前可见位置重新缓出。
        self._speak_scroll = {
            't0': time.perf_counter(),
            'dur': SPEAK_SCROLL_MS / 1000.0,
            'a': cur,
            'b': target,
        }
        self._arm_speak_anim()

    def _arm_speak_anim(self):
        if getattr(self, '_speak_anim_job', None) is not None:
            return
        root = getattr(self, 'root', None)
        if root is None:
            return
        try:
            self._speak_anim_job = root.after(SPEAK_ANIM_TICK_MS, self._tick_speak_anim)
        except tk.TclError:
            self._speak_anim_job = None

    def _tick_speak_anim(self):
        self._speak_anim_job = None
        now = time.perf_counter()
        view = getattr(self, 'view', None)
        sc = getattr(self, '_speak_scroll', None)
        if sc and view is not None:
            dur = sc['dur'] or 0.001
            t = (now - sc['t0']) / dur
            if t >= 1.0:
                frac = sc['b']
                self._speak_scroll = None
            else:
                ease = 1.0 - (1.0 - t) ** 3
                frac = sc['a'] + (sc['b'] - sc['a']) * ease
            self._speak_scroll_animating = True
            try:
                view.yview_moveto(max(0.0, min(1.0, frac)))
            except tk.TclError:
                self._speak_scroll = None
            finally:
                self._speak_scroll_animating = False
        out_t0 = getattr(self, '_speak_fade_out_t0', None)
        in_t0 = getattr(self, '_speak_fade_in_t0', None)
        if out_t0 is not None and (now - out_t0) * 1000 >= SPEAK_FADE_OUT_MS:
            self._speak_line_prev = None
            self._speak_fade_out_t0 = None
        if in_t0 is not None and (now - in_t0) * 1000 >= SPEAK_FADE_IN_MS:
            self._speak_fade_in_t0 = None
        try:
            self._paint_speak_line()
        except tk.TclError:
            return
        if self._speak_scroll or self._speak_fade_out_t0 or self._speak_fade_in_t0:
            self._arm_speak_anim()

    def _cancel_scroll_anim(self):
        self._speak_scroll = None
        # 定时器留给淡入淡出继续跑；若两者都停了，下一拍会自然结束。

    def _cancel_speak_anims(self, remove=False):
        job = getattr(self, '_speak_anim_job', None)
        self._speak_anim_job = None
        if job is not None:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        job = getattr(self, '_speak_progress_job', None)
        self._speak_progress_job = None
        if job is not None:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        self._cancel_idle_revert()
        self._speak_scroll = None
        self._speak_fade_in_t0 = None
        self._speak_fade_out_t0 = None
        self._speak_line_prev = None
        if remove:
            self._speak_line_idx = None
            self._speak_line_range = None
            self._paint_speak_line()

    def _arm_speak_progress(self):
        if getattr(self, '_speak_progress_job', None) is not None:
            return
        if not getattr(self, '_speak_on', False) or getattr(self, '_speak_paused', False):
            return
        try:
            self._speak_progress_job = self.root.after(80, self._tick_speak_progress)
        except tk.TclError:
            self._speak_progress_job = None

    def _play_progress(self):
        override = getattr(self, '_speak_progress', None)
        if override is not None:
            try:
                return max(0.0, min(1.0, float(override)))
            except (TypeError, ValueError):
                pass
        cosy = getattr(self, '_cosy', None)
        fn = getattr(cosy, 'progress', None) if cosy is not None else None
        if not fn:
            return None
        try:
            val = fn()
        except Exception:
            return None
        if val is None:
            return None
        try:
            return max(0.0, min(1.0, float(val)))
        except (TypeError, ValueError):
            return None

    def _tick_speak_progress(self):
        self._speak_progress_job = None
        if not getattr(self, '_speak_on', False) or getattr(self, '_speak_paused', False):
            return
        view = getattr(self, 'view', None)
        rng = getattr(self, '_speak_line_range', None)
        if view is None or not rng:
            return
        progress = self._play_progress()
        if progress is None:
            self._arm_speak_progress()
            return
        try:
            idx = speak_line_at(view, rng[0], rng[1], progress)
        except tk.TclError:
            self._arm_speak_progress()
            return
        cur = getattr(self, '_speak_line_idx', None)
        if not cur or not self._same_display_line(cur, idx):
            self._move_speak_line(idx, animate=True)
        self._arm_speak_progress()

    def _clear_speak_line(self, fade=False):
        self._speak_progress_job and self._cancel_progress_only()
        self._cancel_scroll_anim()
        if fade and getattr(self, '_speak_line_idx', None):
            now = time.perf_counter()
            self._speak_line_prev = self._speak_line_idx
            self._speak_line_idx = None
            self._speak_line_range = None
            self._speak_fade_out_t0 = now
            self._speak_fade_in_t0 = None
            self._arm_speak_anim()
            self._paint_speak_line()
            return
        self._cancel_speak_anims(remove=True)

    def _cancel_progress_only(self):
        job = getattr(self, '_speak_progress_job', None)
        self._speak_progress_job = None
        if job is not None:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass

    def _fade_amount(self, t0, ms):
        if t0 is None:
            return None
        elapsed = (time.perf_counter() - t0) * 1000.0
        if elapsed >= ms:
            return 1.0
        return max(0.0, min(1.0, elapsed / float(ms)))

    def _paint_speak_line(self):
        """只在当前正在读的那一行下画一条通栏 2px 淡蓝线。"""
        view = getattr(self, 'view', None)
        pool = getattr(self, '_speak_bars', None)
        if view is None:
            return
        if pool is None:
            self._speak_bars = pool = []
        try:
            bg = str(view.cget('bg') or BG)
        except tk.TclError:
            bg = BG
        items = []
        prev = getattr(self, '_speak_line_prev', None)
        cur = getattr(self, '_speak_line_idx', None)
        if prev:
            out_t = self._fade_amount(getattr(self, '_speak_fade_out_t0', None), SPEAK_FADE_OUT_MS)
            if out_t is None:
                color = SPEAK_LINE
            else:
                color = mix_hex(SPEAK_LINE, bg, out_t)
            if out_t is None or out_t < 1.0:
                rect = speak_line_rect(view, prev)
                if rect:
                    items.append((rect, color))
        if cur:
            in_t = self._fade_amount(getattr(self, '_speak_fade_in_t0', None), SPEAK_FADE_IN_MS)
            if in_t is None:
                color = SPEAK_LINE
            else:
                color = mix_hex(bg, SPEAK_LINE, in_t)
            rect = speak_line_rect(view, cur)
            if rect:
                items.append((rect, color))
        used = 0
        for rect, color in items:
            x, y, w, h = rect
            if w < 1 or h < 1:
                continue
            if used >= len(pool):
                bar = tk.Frame(
                    view, bg=color, highlightthickness=0, bd=0, cursor='arrow',
                )
                bar.bind('<MouseWheel>', lambda e, v=view: (v.event_generate('<MouseWheel>', delta=e.delta), 'break')[1])
                pool.append(bar)
            bar = pool[used]
            used += 1
            try:
                bar.configure(bg=color, width=int(w), height=int(SPEAK_LINE_H))
                bar.place(x=int(x), y=int(y), width=int(w), height=int(SPEAK_LINE_H))
                bar.lift()
            except tk.TclError:
                pass
        for bar in pool[used:]:
            try:
                bar.place_forget()
            except tk.TclError:
                pass

    def _start_auto(self):
        if self._at_end():
            return
        self._stop_speak()
        self._auto_on = True
        self._auto_acc = 0
        self._auto_last = time.perf_counter()
        self.btn_auto.set_on(True)
        self._auto_tick()

    def _stop_auto(self):
        self._auto_on = False
        job = self._auto_job
        self._auto_job = None
        if job is not None:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        btn = getattr(self, 'btn_auto', None)
        if btn is not None:
            btn.set_on(False)

    def _stop_if_auto(self, e=None):
        self._cancel_scroll_anim()
        if self._auto_on:
            self._stop_auto()

    def _at_end(self):
        try:
            _first, last = self.view.yview()
            return float(last) >= 0.999
        except (tk.TclError, TypeError, ValueError):
            return True

    def _line_px(self):
        """正文字号的一行。不跟着表格、标题的行高变。"""
        try:
            return max(1, int(self.view.tk.call(
                'font', 'metrics', self.view.cget('font'), '-linespace',
            )))
        except (tk.TclError, TypeError, ValueError):
            return 1

    def _auto_tick(self):
        self._auto_job = None
        if not self._auto_on:
            return
        now = time.perf_counter()
        dt = now - self._auto_last if self._auto_last else 0.0
        self._auto_last = now
        try:
            if self._at_end():
                self._stop_auto()
                return
            self._auto_acc, step = auto_scroll_pixels(
                self._auto_acc, self._line_px(), dt, self._auto_seconds,
            )
            if step:
                # 滚动条回调是事后才来的，不能拿它判断「用户在滚」。
                self.view.yview('scroll', int(step), 'pixels')
            if not self._auto_on or self._at_end():
                self._stop_auto()
                return
            self._auto_job = self.root.after(AUTO_TICK_MS, self._auto_tick)
        except tk.TclError:
            self._auto_on = False
            self._auto_job = None

    def _build_tree(self, body):
        col = tk.Frame(body, bg=BG, width=TREE_W, highlightthickness=0, bd=0)
        col.pack_propagate(False)
        self.tree_col = col
        sep = tk.Frame(col, bg='#3a3a3a', width=1)
        sep.pack(side='right', fill='y')
        inner = tk.Frame(col, bg=BG)
        inner.pack(side='left', fill='both', expand=True)
        self._tree_dir_font = tkfont.Font(family='Microsoft YaHei UI', size=8)
        self._tree_file_font = tkfont.Font(family='Microsoft YaHei UI', size=11)
        self._tree_dir_label = ''
        line = max(12, int(self._tree_dir_font.metrics('linespace')))
        self.tree_head = tk.Canvas(
            inner, bg=BG, height=line + 14, highlightthickness=0, bd=0,
            takefocus=0,
        )
        self.tree_head.pack(fill='x', padx=6, pady=(8, 6))
        self.tree_head.bind('<Configure>', self._paint_tree_head)
        self._arm_tree_drop()
        self.tree_list = tk.Text(
            inner, bg=BG, fg=FG, bd=0, highlightthickness=0, wrap='none',
            font=self._tree_file_font, cursor='arrow', padx=4, pady=2,
            insertwidth=0, state='disabled', undo=False, takefocus=0,
        )
        self.tree_list.tag_configure(
            'row', justify='left', spacing1=4, spacing3=4, lmargin1=0,
        )
        self.tree_list.tag_configure('on', background='#264f78', foreground='#ffffff')
        self.tree_list.tag_raise('on')
        self.tree_list.pack(fill='both', expand=True, padx=(2, 0), pady=(0, 6))
        self.tree_list.bind('<Button-1>', self._on_tree_click)
        self.tree_list.configure(xscrollcommand=self._keep_tree_left)
        for w in (col, inner, sep, self.tree_head, self.tree_list):
            w.bind('<MouseWheel>', self._tree_wheel)

    def _toggle_tree(self):
        self._tree_on = not self._tree_on
        self.btn_tree.set_on(self._tree_on)
        if self._tree_on:
            self._tree_folder = None
            if not self.tree_col.winfo_manager():
                self.tree_col.pack(side='left', fill='y', before=self.view)
            self._arm_tree_drop()
            self._sync_tree()
        elif self.tree_col.winfo_manager():
            self.tree_col.pack_forget()

    def _tree_wheel(self, e):
        step = -3 if e.delta > 0 else 3
        self.tree_list.yview_scroll(step, 'units')
        return 'break'

    def _paint_tree_head(self, _event=None):
        c = self.tree_head
        try:
            width = int(c.winfo_width())
            height = int(c.winfo_height())
        except tk.TclError:
            return
        if width < 8 or height < 8:
            return
        c.delete('all')
        self._stroke_round(c, 1, 1, width - 2, height - 2, 6, '#3a3a3a')
        shown = self._fit_tree_text(
            self._tree_dir_label or '没有打开的目录',
            self._tree_dir_font,
            max(8, width - 18),
        )
        c.create_text(
            9, height / 2, text=shown, anchor='w', fill='#8a8a8a',
            font=self._tree_dir_font,
        )

    def _stroke_round(self, canvas, x1, y1, x2, y2, radius, color):
        radius = min(float(radius), (x2 - x1) / 2.0, (y2 - y1) / 2.0)
        canvas.create_line(x1 + radius, y1, x2 - radius, y1, fill=color)
        canvas.create_line(x1 + radius, y2, x2 - radius, y2, fill=color)
        canvas.create_line(x1, y1 + radius, x1, y2 - radius, fill=color)
        canvas.create_line(x2, y1 + radius, x2, y2 - radius, fill=color)
        box = (
            (x1, y1, x1 + radius * 2, y1 + radius * 2, 90),
            (x2 - radius * 2, y1, x2, y1 + radius * 2, 0),
            (x2 - radius * 2, y2 - radius * 2, x2, y2, 270),
            (x1, y2 - radius * 2, x1 + radius * 2, y2, 180),
        )
        for left, top, right, bottom, start in box:
            canvas.create_arc(
                left, top, right, bottom, start=start, extent=90,
                style='arc', outline=color,
            )

    def _fit_tree_name(self, text):
        return self._fit_tree_text(text, self._tree_file_font, TREE_W - 24)

    def _fit_tree_text(self, text, font, avail):
        if font.measure(text) <= avail:
            return text
        while text and font.measure(text + '…') > avail:
            text = text[:-1]
        return (text + '…') if text else '…'

    def _keep_tree_left(self, first, last):
        try:
            if float(first) > 0:
                self.tree_list.xview_moveto(0)
        except (TypeError, ValueError, tk.TclError):
            pass

    def _arm_tree_drop(self):
        """目录名这一条接收拖进来的文件夹。"""
        for widget in (self.tree_head,):
            widget.update_idletasks()
            hwnd = int(widget.winfo_id())
            if not hwnd or hwnd in _TREE_DROP:
                continue
            old = _subclass(hwnd, _TREE_DROP_PROC)
            if not old:
                continue
            _TREE_DROP[hwnd] = (old, self)
            shell32 = ctypes.windll.shell32
            shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
            shell32.DragAcceptFiles.restype = None
            shell32.DragAcceptFiles(hwnd, True)
        if self._tree_on:
            self._schedule_tree_drop_poll()

    def _schedule_tree_drop_poll(self):
        if self._tree_drop_job:
            return
        try:
            self._tree_drop_job = self.root.after(150, self._poll_tree_drop)
        except tk.TclError:
            self._tree_drop_job = None

    def _poll_tree_drop(self):
        """等系统拖放循环退出后，再换左边的目录。"""
        self._tree_drop_job = None
        paths = self._tree_pending
        if paths and time.monotonic() - self._tree_pending_at >= 0.3:
            self._tree_pending = None
            folder = ''
            for path in paths:
                if path and os.path.isdir(path):
                    folder = os.path.abspath(path)
                    break
            if folder:
                try:
                    self._switch_tree_folder(folder)
                except tk.TclError:
                    return
        if self._tree_pending or self._tree_on:
            self._schedule_tree_drop_poll()

    def _switch_tree_folder(self, folder):
        """左边目录换成这个文件夹。正在看的那篇先不关。"""
        if not self._tree_on or not folder:
            return
        folder = os.path.abspath(folder)
        if not os.path.isdir(folder):
            return
        key = os.path.normcase(folder)
        if self._tree_hold and os.path.normcase(self._tree_hold) == key and self._tree_folder == key:
            return
        self._tree_hold = folder
        self._reload_tree(folder)
        self._tree_folder = key
        self._mark_tree_current()

    def _sync_tree(self):
        if not self._tree_on:
            return
        hold = self._tree_hold
        if hold and os.path.isdir(hold):
            folder = hold
        else:
            self._tree_hold = None
            folder = self._base or ''
        key = os.path.normcase(os.path.abspath(folder)) if folder else ''
        if key != self._tree_folder:
            self._reload_tree(folder)
            self._tree_folder = key
        self._mark_tree_current()

    def _reload_tree(self, folder):
        label = os.path.basename(folder.rstrip('\\/')) if folder else ''
        self._tree_dir_label = label or '没有打开的目录'
        self._paint_tree_head()
        self._tree_busy = True
        try:
            self.tree_list.configure(state='normal')
            self.tree_list.delete('1.0', 'end')
            self._tree_paths = []
            if folder and os.path.isdir(folder):
                for name in openable_names(folder):
                    self._tree_paths.append(os.path.join(folder, name))
                    self.tree_list.insert('end', self._fit_tree_name(name) + '\n', ('row',))
            self.tree_list.configure(state='disabled')
            self.tree_list.xview_moveto(0)
        finally:
            self._tree_busy = False

    def _mark_tree_current(self):
        current = ''
        if self.files and 0 <= self.idx < len(self.files):
            current = os.path.normcase(os.path.abspath(self.files[self.idx]))
        self._tree_busy = True
        try:
            self.tree_list.tag_remove('on', '1.0', 'end')
            for i, path in enumerate(self._tree_paths):
                if os.path.normcase(os.path.abspath(path)) == current:
                    line = '%d.0' % (i + 1)
                    self.tree_list.tag_add('on', line, '%d.end' % (i + 1))
                    self.tree_list.see(line)
                    self.tree_list.xview_moveto(0)
                    break
        finally:
            self._tree_busy = False

    def _on_tree_click(self, e):
        if self._tree_busy:
            return 'break'
        try:
            point = self.tree_list.index('@%d,%d linestart' % (e.x, e.y))
            i = int(str(point).split('.')[0]) - 1
        except (tk.TclError, ValueError):
            return 'break'
        if i < 0 or i >= len(self._tree_paths):
            return 'break'
        path = self._tree_paths[i]
        if self.files and 0 <= self.idx < len(self.files):
            if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(self.files[self.idx])):
                return 'break'
        self.load_path(path)
        self.view.focus_set()
        return 'break'

    def _on_yscroll(self, first, last):
        # 这里只更新右侧胶囊。文本框会在自己滚完之后才调用它，
        # 拿来关自动滚屏的话，一按下开就会被马上关掉。
        view = getattr(self, 'view', None)
        if view is not None:
            paint_quote_bars(view)
            self._paint_speak_line()
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
        self._stop_if_auto()
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
        self._stop_if_auto()
        scroll_pixels(self.view, e.delta)
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
        return resize_zone(x, y, w, h, m)

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
        self._cancel_speak_anims(remove=True)
        self._stop_auto()
        self._stop_speak(fade=False)
        self._cancel_idle_revert()
        speaker = getattr(self, '_speaker', None)
        self._speaker = None
        if speaker is not None:
            speaker.close()
        cosy = getattr(self, '_cosy', None)
        self._cosy = None
        if cosy is not None:
            cosy.close()
        self._save_normal()
        job = self._tree_drop_job
        self._tree_drop_job = None
        self._tree_pending = None
        if job:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        _release_tree_drop(self)
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

    def load_path(self, path, follow_dir=True):
        if not path:
            return
        path = os.path.abspath(path)
        self._scan_token += 1
        token = self._scan_token
        self.files = [path]
        self.idx = 0
        self._tree_follow = follow_dir
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
        self._stop_auto()
        self._stop_speak(fade=False)
        follow = self._tree_follow
        self._tree_follow = True
        if not self.files:
            return
        path = self.files[self.idx]
        self._set_title(os.path.basename(path))
        self._base = os.path.dirname(path)
        if follow and self._tree_hold:
            opened = os.path.normcase(os.path.abspath(self._base))
            if opened != os.path.normcase(os.path.abspath(self._tree_hold)):
                self._tree_hold = None
        self._release_note()
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext in WORD_EXTS or ext in PDF_EXTS:
                src = read_office(path)
                plain = True
            else:
                src = read_text(path)
                plain = ext in TXT_EXTS
        except Exception as exc:
            self.view.delete('1.0', 'end')
            _insert_text(self.view, '无法打开：%s\n' % exc, ('muted',))
            self._set_status(path)
            self._sync_tool_nav()
            self._sync_tree()
            return
        on_link = {'n': 0, 'open': self.open_url}
        self._pill_hold = True
        try:
            if plain:
                fill_plain(self.view, src)
            else:
                self._md_cache = (src, path)
                fill_view(self.view, src, on_link, self._images, self._base, path)
            self.view.yview_moveto(0)
            self.view.update_idletasks()
        finally:
            self._pill_hold = False
        self._on_yscroll(*self.view.yview())
        name = os.path.basename(path)
        self._set_status('%d/%d  %s' % (self.idx + 1, len(self.files), name))
        self._sync_tool_nav()
        self._sync_tree()

    def _release_note(self):
        """上一篇的表格、图片和字体先丢掉，再装下一篇。"""
        release_document(self.view, self._images)
        self._images = []
        self._md_cache = None

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
        self._zoom = zoom
        # 只改字号和表格像素。重画整篇才会让每一次点击都顿一下。
        _configure_tags(view, zoom)
        configure_speak_line(view)
        if getattr(self, '_md_cache', None):
            rescale_embedded(view)
        try:
            view.tk.call(view._w, 'yview', anchor)
        except tk.TclError:
            pass
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

    def _goto_anchor(self, raw):
        text = raw or ''
        if text.startswith('fn:'):
            key = 'fn:' + anchor_key(text[3:])
        else:
            key = anchor_key(text)
        idx = getattr(self.view, '_md_anchors', {}).get(key)
        if not idx:
            return
        self.view.see(idx)
        self.view.mark_set('insert', idx)

    def open_url(self, url):
        if not url:
            return 'break'
        if url.startswith('#'):
            self._goto_anchor(url[1:])
            return 'break'
        heading = ''
        if '#' in url and not re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', url):
            url, heading = url.split('#', 1)
        if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', url):
            webbrowser.open(url)
            return 'break'
        path = url if os.path.isabs(url) else os.path.join(self._base, url)
        path = os.path.normpath(path)
        if os.path.isfile(path) and os.path.splitext(path)[1].lower() in EXTS:
            self.load_path(path)
            if heading:
                self._goto_anchor(heading)
        elif os.path.isfile(path):
            os.startfile(path)
        return 'break'

    def step(self, d):
        if len(self.files) < 2:
            return 'break'
        self.idx = (self.idx + d) % len(self.files)
        self._tree_follow = False
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
            self.bar_row.pack_forget()
        else:
            self.root.attributes('-fullscreen', False)
            self.title_bar.pack(side='top', fill='x', before=self.body)
            self.tool_bar.pack(side='top', fill='x', before=self.body)
            self.bar_row.pack(fill='x', side='bottom')
            self._taskbar()
            self._apply_icon()
            self._sync_max_glyph()
        return 'break'

    def esc(self, e=None):
        if getattr(self, '_speak_on', False):
            self._stop_speak()
            return 'break'
        if self._fullscreen:
            self.fs()
        else:
            self.close()
        return 'break'


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
