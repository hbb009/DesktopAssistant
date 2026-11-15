# -*- coding: utf-8 -*-
"""把 Markdown 排进阅读器的文本框。

按 Obsidian 阅读视图来：段内换行保留，常用标记、表格、任务列表、
标注、双链和嵌入都画出来。普通引用是左侧一条强调色竖线。
公式只按等宽原文显示，不排成印刷公式。
"""
import gc
import os
import re
import tkinter as tk
import tkinter.font as tkfont
from urllib.parse import unquote

FG = '#e6e6e6'
MD_EXTS = ('.md', '.markdown', '.mdown')
IMG_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.tif', '.tiff'}
PUNCT = set('\\`*_{}[]()#+-.!|>~=')
# Obsidian 默认强调色 hsl(258 88% 66%)。引用竖线用它，对应
# --blockquote-border-color: var(--interactive-accent)。
QUOTE_BAR = '#8a5cf5'
CALLOUT_COLOR = {
    'note': '#8ab4f8', 'info': '#8ab4f8', 'todo': '#8ab4f8', 'abstract': '#7dcea0',
    'tip': '#7dcea0', 'success': '#7dcea0', 'question': '#c792ea', 'example': '#c792ea',
    'quote': '#bdbdbd', 'warning': '#f0a542', 'danger': '#e06c75', 'failure': '#e06c75',
    'bug': '#e06c75',
}
FENCE_RE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')
ATX_RE = re.compile(r'^ {0,3}(#{1,6})(?:[ \t]+([^\n]*))?$')
HR_RE = re.compile(r'^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})\s*$')
QUOTE_RE = re.compile(r'^ {0,3}>')
LIST_RE = re.compile(r'^( {0,3})([-*+]|\d+[.)])[ \t]+(.*)$')
MATH_LINE_RE = re.compile(r'^ {0,3}\$\$(.*)\$\$\s*$')
MATH_OPEN_RE = re.compile(r'^ {0,3}\$\$(.*)$')
SETEXT_H1 = re.compile(r'^ {0,3}=+\s*$')
SETEXT_H2 = re.compile(r'^ {0,3}-+\s*$')
CALLOUT_RE = re.compile(r'^\[!([A-Za-z][A-Za-z0-9-]*)\]([+-])?(?:[ \t]+(.+))?$')
REF_RE = re.compile(
    r'^ {0,3}\[([^\]]+)\]:[ \t]*<?([^>\s]+)>?(?:[ \t]+(?:"[^"]*"|\'[^\']*\'))?\s*$'
)
FN_RE = re.compile(r'^ {0,3}\[\^([^\]]+)\]:[ \t]?(.*)$')
ONLY_EMBED = re.compile(r'^!\[\[([^\]]+)\]\]$')
ONLY_IMG = re.compile(r'^!\[([^\]]*)\]\((.+)\)\s*$')
TAG_RE = re.compile(r'#([^\W\d][\w/-]*)')


def anchor_key(text):
    """标题锚点。忽略空格、大小写和一层标记，方便 [[#标题]] 对上。"""
    raw = unquote((text or '').replace('+', ' ')).strip()
    shown = visible_text(raw).strip().casefold()
    shown = re.sub(r'\s+', '', shown)
    return shown


def visible_text(src, refs=None):
    return _ops_text(inline_ops(src or '', refs or {}))


def disp_width(text):
    width = 0
    for ch in text or '':
        o = ord(ch)
        width += 2 if o > 0x2E80 else 1
    return width


def parse_document(src):
    """拆成块。meta['refs'] 是参考链接，meta['notes'] 是脚注。"""
    text = (src or '').replace('\r\n', '\n').replace('\r', '\n')
    lines = text.split('\n')
    fm, lines = _peel_frontmatter(lines)
    lines = _strip_comments(lines)
    lines, refs, notes = _peel_defs(lines)
    blocks = _blocks(lines)
    if fm:
        blocks.insert(0, {'t': 'fm', 'items': fm})
    return blocks, {'refs': refs, 'notes': notes}


def inline_ops(src, refs=None):
    refs = refs or {}
    ops, _end = _inl(src or '', 0, len(src or ''), '', refs, 0)
    return ops


def configure_tags(view, zoom=100):
    view._md_zoom = zoom

    def px(n):
        return max(8, int(round(n * zoom / 100.0)))

    def pt(n):
        return max(1, int(round(n * zoom / 100.0)))

    def sp(n):
        return max(0, int(round(n * zoom / 100.0)))

    fonts = []

    def font(size, bold=False, italic=False, strike=False, family='Microsoft YaHei UI'):
        obj = tkfont.Font(
            family=family,
            size=pt(size),
            weight='bold' if bold else 'normal',
            slant='italic' if italic else 'roman',
            overstrike=1 if strike else 0,
        )
        obj._md_pt = size
        fonts.append(obj)
        return obj

    view._md_fonts = fonts
    body = font(14)
    view.configure(font=body)
    # Obsidian 阅读视图：行高用字体自己的 linespace。段与段之间空一行
    # （--p-spacing，上下边距会合并，所以只加在段尾）。标题上方是段距的
    # 2.5 倍，下方仍是一段距。Tk 会把上一行的 spacing3 和这一行的 spacing1
    # 加在一起，所以标题自己只再加 1.5 段距。
    para = max(8, int(body.metrics('linespace')))
    # 朗读线要在字墨水下方留出 2–3px，折行默认行距太紧，这里略加一点行下空隙。
    line_room = max(4, sp(6))
    view.tag_configure('p', font=body, foreground=FG, spacing2=line_room, spacing3=line_room)
    view.tag_configure('gap', spacing1=0, spacing2=0, spacing3=para)
    head_above = para + para // 2
    for name, size, color in (
        ('h1', 22, '#ffffff'),
        ('h2', 18, '#f5f5f5'),
        ('h3', 16, '#f0f0f0'),
        ('h4', 14, '#eeeeee'),
        ('h5', 13, '#e8e8e8'),
        ('h6', 12, '#e0e0e0'),
    ):
        view.tag_configure(
            name, font=font(size, bold=True), foreground=color,
            spacing1=head_above, spacing3=para,
        )
    for name, bold, italic, strike in (
        ('b', True, False, False), ('i', False, True, False), ('bi', True, True, False),
        ('s', False, False, True), ('bs', True, False, True), ('is', False, True, True),
        ('bis', True, True, True),
    ):
        view.tag_configure(name, font=font(14, bold, italic, strike))
    view.tag_configure('u', underline=1)
    view.tag_configure('mark', font=font(14), background='#5c4a1f', foreground='#ffe7a3')
    view.tag_configure('code', font=font(12, family='Consolas'), foreground='#e8d5a3', background='#1c1c1c')
    view.tag_configure(
        'codeblock', font=font(12, family='Consolas'), foreground='#d4d4d4', background='#1c1c1c',
        lmargin1=px(16), lmargin2=px(16), spacing1=sp(8), spacing3=sp(8),
    )
    view.tag_configure('math', font=font(12, family='Consolas'), foreground='#c792ea', background='#1c1c1c')
    view.tag_configure('mathblock', font=font(12, family='Consolas'), foreground='#c792ea', background='#1c1c1c',
                       lmargin1=px(16), lmargin2=px(16), spacing1=sp(6), spacing3=sp(6))
    # 默认主题：字色继承正文、正体、背景透明。竖线另画，不靠灰字表示引用。
    view.tag_configure('quote', font=body, foreground=FG, spacing2=line_room, spacing3=line_room)
    for n, margin in ((1, 18), (2, 36), (3, 54)):
        view.tag_configure('q%d' % n, lmargin1=px(margin), lmargin2=px(margin))
    item_gap = max(4, para // 6)
    for n in range(6):
        left = 12 + n * 20
        view.tag_configure(
            'li%d' % n, font=body, foreground=FG,
            lmargin1=px(left), lmargin2=px(left + 18),
            spacing1=0, spacing2=line_room, spacing3=max(item_gap, line_room),
        )
    view.tag_configure('box', font=font(14, family='Segoe UI Symbol'))
    view.tag_configure('hr', foreground='#3a3a3a', justify='center', spacing1=0, spacing3=max(6, para // 3))
    view.tag_configure('link', foreground='#8ab4f8', underline=1)
    view.tag_configure('miss', foreground='#d0a0a0')
    view.tag_configure('muted', foreground='#8a8a8a')
    view.tag_configure('tag', foreground='#8ab4f8')
    view.tag_configure('fm', font=font(12), foreground='#9aa4b8', spacing1=sp(1))
    view.tag_configure('fmkey', font=font(12, bold=True), foreground='#b7c0d4')
    view.tag_configure(
        'table', font=font(12, family='Consolas'), foreground=FG,
        lmargin1=px(4), spacing1=sp(2), spacing3=sp(2),
    )
    view.tag_configure('th', font=font(12, bold=True, family='Consolas'), foreground='#ffffff')
    callout_below = max(4, para // 4)
    for kind, color in CALLOUT_COLOR.items():
        view.tag_configure(
            'co_' + kind, font=font(14, bold=True), foreground=color,
            spacing1=0, spacing3=callout_below,
        )
    view.tag_configure(
        'co_other', font=font(14, bold=True), foreground='#8ab4f8',
        spacing1=0, spacing3=callout_below,
    )
    # 两个汉字之间的换行点。不占宽度，优先级放最后，避免被链接标记盖住。
    view.tag_configure('brk', elide=1)
    order = (
        ['p', 'quote', 'table', 'fm', 'fmkey', 'codeblock', 'math', 'mathblock', 'hr']
        + ['h%d' % n for n in range(6, 0, -1)]
        + ['q%d' % n for n in range(1, 4)]
        + ['li%d' % n for n in range(6)]
        + ['b', 'i', 'bi', 's', 'bs', 'is', 'bis', 'u', 'mark', 'box', 'th']
        + ['code', 'tag', 'miss', 'link']
        + ['co_' + k for k in CALLOUT_COLOR]
        + ['co_other', 'muted', 'brk']
    )
    for name in order:
        view.tag_raise(name)
    _bind_plain_copy(view)
    _bind_quote_bars(view)


def _wide_char(ch):
    return ord(ch) > 127


def _break_marks(text):
    """在相邻的两个宽字符之间插入普通空格，并返回这些空格的位置。

    wrap=word 只在普通空格处断开。数字后面的空格若是唯一断点，
    后面整段汉字会整段掉到下一行，序号后面就空出一大截。
    没有这种相邻宽字符时原样返回。
    """
    if not text or len(text) < 2:
        return text, ()
    prev = _wide_char(text[0])
    found = False
    for ch in text[1:]:
        wide = _wide_char(ch)
        if prev and wide:
            found = True
            break
        prev = wide
    if not found:
        return text, ()
    parts = []
    marks = []
    prev = False
    n = 0
    for ch in text:
        wide = _wide_char(ch)
        if prev and wide:
            marks.append(n)
            parts.append(' ')
            n += 1
        parts.append(ch)
        n += 1
        prev = wide
    return ''.join(parts), marks


def _insert_text(view, text, tags):
    """写入正文。格子按字换行，不再插入断点，避免大表的字数翻倍。"""
    if not text:
        return
    try:
        if str(view.cget('wrap')) == 'char':
            view.insert('end', text, tags)
            return
    except tk.TclError:
        pass
    cooked, marks = _break_marks(text)
    if not marks:
        view.insert('end', text, tags)
        return
    start = view.index('end-1c')
    view.insert('end', cooked, tags)
    step = 800
    for i in range(0, len(marks), step):
        pairs = []
        for off in marks[i:i + step]:
            pairs.append('%s + %dc' % (start, off))
            pairs.append('%s + %dc' % (start, off + 1))
        view.tag_add('brk', *pairs)


def plain_widget(view, start='1.0', end='end-1c'):
    """读出文字时去掉换行用的断点，复制和测试都走这里。"""
    start = view.index(start)
    end = view.index(end)
    if view.compare(start, '>=', end):
        return ''
    try:
        ranges = view.tag_ranges('brk')
    except tk.TclError:
        return view.get(start, end)
    if not ranges:
        return view.get(start, end)
    parts = []
    cursor = start
    for i in range(0, len(ranges), 2):
        a = str(ranges[i])
        b = str(ranges[i + 1])
        if view.compare(b, '<=', cursor):
            continue
        if view.compare(a, '>=', end):
            break
        cut = a if view.compare(a, '<', end) else end
        if view.compare(cursor, '<', cut):
            parts.append(view.get(cursor, cut))
        if view.compare(b, '>', cursor):
            cursor = b
        if view.compare(cursor, '>=', end):
            return ''.join(parts)
    if view.compare(cursor, '<', end):
        parts.append(view.get(cursor, end))
    return ''.join(parts)


def _bind_plain_copy(view):
    def on_copy(_event=None):
        try:
            if not view.tag_ranges('sel'):
                return 'break'
            text = plain_widget(view, 'sel.first', 'sel.last')
        except tk.TclError:
            return 'break'
        try:
            view.clipboard_clear()
            view.clipboard_append(text)
        except tk.TclError:
            return 'break'
        return 'break'

    view.bind('<<Copy>>', on_copy)
    # 同一个控件上，<Control-c> 比 <<Copy>> 更具体，系统复制又会带走隐藏空格。
    # 这里直接处理按键并打断，后面的 tk_textCopy 就不会再写一次剪贴板。
    view.bind('<Control-Key-c>', on_copy)
    view.bind('<Control-Key-Insert>', on_copy)


def _bind_quote_bars(view):
    if getattr(view, '_md_quote_bound', False):
        return
    view._md_quote_bound = True
    view.bind('<Configure>', lambda _e: _schedule_quote_bars(view), add='+')


def _quote_bar_px(view):
    """竖线 2px，和正文隔开约 12px。缩放时跟字号一起变。"""
    zoom = getattr(view, '_md_zoom', 100) or 100
    thick = max(2, int(round(2 * zoom / 100.0)))
    gap = max(8, int(round(12 * zoom / 100.0)))
    return thick, gap


def _text_pad_y(view):
    """place(y=0) 在窗口里的实际位置。边框和上内边距都算进去。"""
    inset = 0
    for option in ('highlightthickness', 'borderwidth', 'pady'):
        try:
            raw = view.cget(option)
        except tk.TclError:
            continue
        if raw in (None, ''):
            continue
        try:
            inset += int(float(raw))
        except (TypeError, ValueError):
            continue
    return inset


def _quote_scroll_shift(view):
    """屏幕第一行探出内容区的像素。滚轮按像素走时，竖线要减掉这段。"""
    try:
        info = view.dlineinfo('@0,0')
    except tk.TclError:
        return 0
    if not info:
        return 0
    return int(info[1]) - _text_pad_y(view)


def _tag_space(view, index, option):
    space = 0
    try:
        names = view.tag_names(index)
    except tk.TclError:
        return 0
    for tag in names:
        try:
            raw = view.tag_cget(tag, option)
        except tk.TclError:
            continue
        if not raw:
            continue
        try:
            space = max(space, int(float(raw)))
        except (TypeError, ValueError):
            continue
    return space


def _ypx(view, a, b):
    try:
        n = view.tk.call(view._w, 'count', '-ypixels', a, b)
    except tk.TclError:
        return None
    if n is None or n == '':
        return 0
    return int(n)


def clear_quote_bars(view):
    for _start, end, _depth in list(getattr(view, '_md_quotes', []) or []):
        if isinstance(end, str):
            try:
                view.mark_unset(end)
            except tk.TclError:
                pass
    view._md_quotes = []
    for bar in list(getattr(view, '_md_bar_pool', []) or []):
        try:
            bar.place_forget()
        except tk.TclError:
            pass


def _schedule_quote_bars(view):
    if not getattr(view, '_md_quotes', None):
        paint_quote_bars(view)
        return
    job = getattr(view, '_md_quote_job', None)
    if job:
        try:
            view.after_cancel(job)
        except tk.TclError:
            pass
    try:
        view._md_quote_job = view.after_idle(lambda v=view: paint_quote_bars(v))
    except tk.TclError:
        pass


def paint_quote_bars(view):
    """按 Obsidian 阅读视图，给每个普通引用画左侧竖线。

    按显示行各放一小段，拼成通栏。整条 Frame 在 Tk Text 里会被裁成
    第一行那么高，所以三行引用看起来会只在第一行前面有竖线。
    线宽 2px，颜色是默认强调色。嵌套的引用各自再来一条，靠右一档。
    标注（> [!type]）不走这里。
    """
    quotes = list(getattr(view, '_md_quotes', []) or [])
    pool = getattr(view, '_md_bar_pool', None)
    if pool is None:
        pool = []
        view._md_bar_pool = pool
    try:
        if not view.winfo_exists():
            return
        height = int(view.winfo_height())
    except tk.TclError:
        return
    if height < 8 or not quotes:
        for bar in pool:
            try:
                bar.place_forget()
            except tk.TclError:
                pass
        return
    thick, gap = _quote_bar_px(view)
    try:
        pady = int(float(view.cget('pady')))
    except (TypeError, ValueError, tk.TclError):
        pady = 0
    # place 的原点已经在 padx/pady 里面。dlineinfo 的 y 含上内边距，要减掉。
    limit = max(0, height - pady)
    raised = -_text_pad_y(view)
    used = 0

    def take_bar():
        nonlocal used
        if used >= len(pool):
            bar = tk.Frame(
                view, bg=QUOTE_BAR, highlightthickness=0, bd=0, cursor='arrow',
            )
            bar._quote_bar = True
            pool.append(bar)
        bar = pool[used]
        used += 1
        return bar

    for start, end, depth in quotes:
        try:
            boundary = view.index(end)
            if view.compare(start, '>=', boundary):
                continue
            last = view.index('%s - 1 chars' % boundary)
            try:
                left = int(float(view.tag_cget('q%d' % int(depth), 'lmargin1') or 0))
            except (TypeError, ValueError, tk.TclError):
                left = 18 * int(depth)
            x = left - gap - thick
            if x < 0:
                x = 0
            idx = view.index(start)
            while view.compare(idx, '<=', last):
                info = view.dlineinfo(idx)
                if info:
                    _lx, y, _lw, h, _bl = info
                    y0 = y - pady
                    y1 = y0 + h
                    if y0 < raised:
                        y0 = raised
                    if y1 > limit:
                        y1 = limit
                    if y1 - y0 >= 1:
                        bar = take_bar()
                        try:
                            bar.configure(bg=QUOTE_BAR)
                            bar.place(x=x, y=y0, width=thick, height=y1 - y0)
                            bar.lift()
                        except tk.TclError:
                            pass
                nxt = view.index('%s + 1 display lines' % idx)
                if view.compare(nxt, '<=', idx):
                    break
                idx = nxt
                if view.compare(idx, '>', last):
                    break
        except tk.TclError:
            continue
    for bar in pool[used:]:
        try:
            bar.place_forget()
        except tk.TclError:
            pass


def fill_view(view, src, on_link, image_holder, base_dir, path=''):
    _drop_embedded(view)
    clear_quote_bars(view)
    view.delete('1.0', 'end')
    view._md_anchors = {}
    raw = src or ''
    blocks, meta = parse_document(raw)
    ctx = _Ctx(view, on_link, image_holder, base_dir or '', path or '', meta)
    ctx.src = raw
    ctx.vault = _vault_of(path) or _vault_of(base_dir)
    if path:
        ctx.stack.add(os.path.normcase(path) + '#')
    if not blocks:
        view.insert('end', '（空文件）\n', ('muted',))
        return
    _render_blocks(blocks, ctx, 0, 0)
    _render_notes(ctx)
    try:
        view.tag_raise('brk')
    except tk.TclError:
        pass
    _sync_metrics(view)
    _schedule_quote_bars(view)


class _Ctx:
    def __init__(self, view, on_link, images, base, path, meta):
        self.view = view
        self.on_link = on_link
        self.images = images
        self.base = base
        self.path = path
        self.vault = ''
        self.refs = meta.get('refs') or {}
        self.notes = meta.get('notes') or {}
        self.stack = set()
        self.depth = 0
        self.index = None
        self.in_cell = False

    def finder(self):
        if self.index is None:
            self.index = _Index(self.base, self.vault or self.base)
        return self.index


def _vault_of(path):
    """有 .obsidian 的那一层才是库根。双链按库找，不局限当前文件夹。"""
    if not path:
        return ''
    cur = os.path.abspath(path)
    if not os.path.isdir(cur):
        cur = os.path.dirname(cur)
    last = ''
    while cur and cur != last:
        if os.path.isdir(os.path.join(cur, '.obsidian')):
            return cur
        last = cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return ''


class _Index:
    def __init__(self, base, vault):
        self.base = base or ''
        self.root = vault or base or ''
        self.by_name = {}
        self.by_stem = {}
        self.ready = False

    def _build(self):
        if self.ready:
            return
        self.ready = True
        if not self.root or not os.path.isdir(self.root):
            return
        count = 0
        for root, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d not in ('.git', '.obsidian', 'node_modules', '__pycache__') and not d.startswith('.')]
            for name in files:
                count += 1
                if count > 4000:
                    return
                path = os.path.join(root, name)
                self.by_name.setdefault(name.casefold(), path)
                stem, ext = os.path.splitext(name)
                if ext.lower() in MD_EXTS:
                    self.by_stem.setdefault(stem.casefold(), path)

    def note(self, target):
        found = self.file(target)
        if found and os.path.splitext(found)[1].lower() in MD_EXTS:
            return found
        self._build()
        key = (target or '').replace('\\', '/').split('/')[-1].casefold()
        if not key:
            return ''
        return self.by_stem.get(key, '')

    def file(self, target):
        target = (target or '').strip().replace('/', os.sep).replace('\\', os.sep)
        if not target:
            return ''
        if os.path.isabs(target) and os.path.isfile(target):
            return target
        for root in (self.base, self.root):
            if not root:
                continue
            direct = os.path.normpath(os.path.join(root, target))
            if os.path.isfile(direct):
                return direct
            for ext in MD_EXTS:
                if os.path.isfile(direct + ext):
                    return direct + ext
        self._build()
        name = os.path.basename(target).casefold()
        return self.by_name.get(name, '')


def _peel_frontmatter(lines):
    if not lines or lines[0].strip() != '---':
        return [], lines
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            return _parse_fm(lines[1:i]), lines[i + 1:]
    return [], lines


def _parse_fm(lines):
    items = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = re.match(r'^([^:]+):\s*(.*)$', line)
        if not m:
            items.append(('', line.strip()))
            i += 1
            continue
        key, val = m.group(1).strip(), m.group(2).strip()
        if val in ('', '|', '>'):
            buf = []
            i += 1
            while i < len(lines) and (lines[i].startswith((' ', '\t')) or lines[i].strip().startswith('- ')):
                buf.append(lines[i].strip().lstrip('-').strip())
                i += 1
            val = '、'.join(x for x in buf if x)
        else:
            i += 1
            if val.startswith('[') and val.endswith(']'):
                val = '、'.join(x.strip().strip('"\'') for x in val[1:-1].split(',') if x.strip())
            else:
                val = val.strip('"\'')
        items.append((key, val))
    return items


def _strip_comments(lines):
    """去掉 %%注释%%。没配上的 %% 只删标记，避免后文整篇消失。代码块里不动。"""
    out = []
    i = 0
    n = len(lines)
    in_code = False
    fence_ch, fence_len = '', 0
    while i < n:
        line = lines[i]
        if in_code:
            out.append(line)
            if re.match(r'^ {0,3}%s{%d,}\s*$' % (re.escape(fence_ch), fence_len), line):
                in_code = False
            i += 1
            continue
        m = FENCE_RE.match(line)
        if m:
            in_code = True
            fence_ch = m.group(1)[0]
            fence_len = len(m.group(1))
            out.append(line)
            i += 1
            continue
        buf = line
        while '%%' in buf:
            a = buf.find('%%')
            b = buf.find('%%', a + 2)
            if b >= 0:
                buf = buf[:a] + buf[b + 2:]
                continue
            j = i + 1
            found = -1
            while j < n and FENCE_RE.match(lines[j]) is None:
                found = lines[j].find('%%')
                if found >= 0:
                    break
                j += 1
            if found >= 0:
                buf = buf[:a] + lines[j][found + 2:]
                i = j
                continue
            buf = buf[:a] + buf[a + 2:]
            break
        if not in_code:
            buf = re.sub(r'\s+\^[A-Za-z0-9-]+\s*$', '', buf)
        out.append(buf)
        i += 1
    return out


def _peel_defs(lines):
    out = []
    refs = {}
    notes = {}
    i = 0
    n = len(lines)
    in_code = False
    fence_ch, fence_len = '', 0
    while i < n:
        line = lines[i]
        if in_code:
            out.append(line)
            if re.match(r'^ {0,3}%s{%d,}\s*$' % (re.escape(fence_ch), fence_len), line):
                in_code = False
            i += 1
            continue
        m = FENCE_RE.match(line)
        if m:
            in_code = True
            fence_ch = m.group(1)[0]
            fence_len = len(m.group(1))
            out.append(line)
            i += 1
            continue
        got = FN_RE.match(line)
        if got:
            body = [got.group(2)]
            i += 1
            while i < n:
                if lines[i].strip() == '':
                    if i + 1 < n and (lines[i + 1].startswith('    ') or lines[i + 1].startswith('\t')):
                        body.append('')
                        i += 1
                        continue
                    break
                if lines[i].startswith('    '):
                    body.append(lines[i][4:])
                    i += 1
                    continue
                if lines[i].startswith('\t'):
                    body.append(lines[i][1:])
                    i += 1
                    continue
                break
            notes[got.group(1)] = '\n'.join(body).strip()
            continue
        ref = REF_RE.match(line)
        if ref:
            refs[ref.group(1).casefold()] = ref.group(2)
            i += 1
            continue
        out.append(_expand_lead(line))
        i += 1
    return out, refs, notes


def _expand_lead(line):
    i = 0
    while i < len(line) and line[i] in ' \t':
        i += 1
    return line[:i].replace('\t', '    ') + line[i:]


def _lead(line):
    n = 0
    while n < len(line) and line[n] == ' ':
        n += 1
    return n


def _blocks(lines, allow_lazy_quote=True):
    i = 0
    n = len(lines)
    out = []
    while i < n:
        if not lines[i].strip():
            i += 1
            continue
        nxt, block = _one(lines, i, n, allow_lazy_quote)
        if nxt <= i:
            nxt = i + 1
        i = nxt
        if block:
            out.append(block)
    return out


def _one(lines, i, end, allow_lazy_quote=True):
    line = lines[i]
    m = FENCE_RE.match(line)
    if m:
        return _fence(lines, i, end, m)
    if MATH_OPEN_RE.match(line):
        return _math(lines, i, end)
    m = ATX_RE.match(line)
    if m and (m.group(2) is not None or line.strip() == m.group(1)):
        title = (m.group(2) or '').strip()
        title = re.sub(r'\s+#+\s*$', '', title).strip()
        return i + 1, {'t': 'h', 'level': len(m.group(1)), 'text': title}
    if HR_RE.match(line):
        return i + 1, {'t': 'hr'}
    if QUOTE_RE.match(line):
        return _quote(lines, i, end, allow_lazy=allow_lazy_quote)
    if i + 1 < end and _is_table_sep(lines[i + 1]) and not _is_table_sep(line):
        return _table(lines, i, end)
    if LIST_RE.match(line):
        return _list(lines, i, end)
    if _lead(line) >= 4:
        return _indented(lines, i, end)
    return _paragraph(lines, i, end)


def _fence(lines, i, end, m):
    fence = m.group(1)
    ch = fence[0]
    lang = (m.group(2) or '').strip().split()
    lang = lang[0] if lang else ''
    if lang.startswith('{.'):
        lang = lang[2:].rstrip('}')
    buf = []
    i += 1
    close = re.compile(r'^ {0,3}%s{%d,}\s*$' % (re.escape(ch), len(fence)))
    while i < end and not close.match(lines[i]):
        buf.append(lines[i])
        i += 1
    if i < end:
        i += 1
    text = '\n'.join(buf)
    if lang.lower() in ('math', 'latex'):
        return i, {'t': 'math', 'text': text}
    return i, {'t': 'code', 'lang': lang, 'text': text}


def _math(lines, i, end):
    one = MATH_LINE_RE.match(lines[i])
    if one and one.group(1).strip():
        return i + 1, {'t': 'math', 'text': one.group(1).strip()}
    buf = []
    rest = MATH_OPEN_RE.match(lines[i]).group(1).strip()
    if rest and rest != '$$':
        buf.append(rest.replace('$$', '').strip())
    i += 1
    while i < end and lines[i].strip() != '$$' and not MATH_LINE_RE.match(lines[i]):
        buf.append(lines[i])
        i += 1
    if i < end:
        tail = lines[i].strip()
        if tail != '$$':
            extra = tail.strip('$').strip()
            if extra:
                buf.append(extra)
        i += 1
    return i, {'t': 'math', 'text': '\n'.join(buf).strip()}


def _is_table_sep(line):
    if '|' not in line:
        return False
    cells = _split_row(line)
    return bool(cells) and all(re.fullmatch(r':?-{3,}:?', c.strip()) for c in cells)


def _split_row(line):
    text = line.strip()
    if text.startswith('|'):
        text = text[1:]
    if text.endswith('|') and not text.endswith('\\|'):
        text = text[:-1]
    parts = []
    buf = []
    esc = False
    for ch in text:
        if esc:
            buf.append(ch)
            esc = False
            continue
        if ch == '\\':
            esc = True
            continue
        if ch == '|':
            parts.append(''.join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    parts.append(''.join(buf).strip())
    return parts


def _aligns(line):
    out = []
    for cell in _split_row(line):
        item = cell.strip()
        left, right = item.startswith(':'), item.endswith(':')
        if left and right:
            out.append('c')
        elif right:
            out.append('r')
        else:
            out.append('l')
    return out


def _table(lines, i, end):
    aligns = _aligns(lines[i + 1])
    rows = [_split_row(lines[i])]
    i += 2
    while i < end and lines[i].strip() and '|' in lines[i] and not _is_table_sep(lines[i]):
        rows.append(_split_row(lines[i]))
        i += 1
    width = max(len(r) for r in rows)
    width = max(width, len(aligns))
    aligns = (aligns + ['l'] * width)[:width]
    even = [r + [''] * (width - len(r)) for r in rows]
    return i, {'t': 'table', 'align': aligns, 'rows': even}


def _boundary(lines, i, end):
    if i >= end:
        return True
    line = lines[i]
    if not line.strip():
        return True
    if FENCE_RE.match(line) or ATX_RE.match(line) or HR_RE.match(line) or QUOTE_RE.match(line):
        return True
    if MATH_OPEN_RE.match(line):
        return True
    if LIST_RE.match(line):
        return True
    if _lead(line) >= 4:
        return True
    if i + 1 < end and _is_table_sep(lines[i + 1]) and '|' in line:
        return True
    return False


def _paragraph(lines, i, end):
    buf = [lines[i].strip()]
    i += 1
    while i < end and not _boundary(lines, i, end):
        # 「文字 + 一行等号」是标题，等号不能并进段落。
        if SETEXT_H1.match(lines[i]) or (SETEXT_H2.match(lines[i]) and '|' not in lines[i]):
            break
        buf.append(lines[i].strip())
        i += 1
    text = '\n'.join(x for x in buf if x is not None).strip()
    if text and i < end and SETEXT_H1.match(lines[i]):
        return i + 1, {'t': 'h', 'level': 1, 'text': text}
    if text and i < end and SETEXT_H2.match(lines[i]) and '|' not in lines[i]:
        return i + 1, {'t': 'h', 'level': 2, 'text': text}
    return i, {'t': 'p', 'text': text}


def _quote(lines, i, end, allow_lazy=True):
    raw = []
    while i < end:
        line = lines[i]
        if QUOTE_RE.match(line):
            raw.append(re.sub(r'^ {0,3}>\s?', '', line))
            i += 1
            continue
        if not line.strip():
            if i + 1 < end and QUOTE_RE.match(lines[i + 1]):
                raw.append('')
                i += 1
                continue
            break
        # 懒惰续行只给本层引用用。内层遇到已剥掉一层 > 的普通行，
        # 应交回外层，否则「>> 内层 / > 外层又一行」会被吃进内层。
        if allow_lazy and raw and not _boundary(lines, i, end):
            raw.append(line.strip())
            i += 1
            continue
        break
    if raw and CALLOUT_RE.match(raw[0].strip()):
        m = CALLOUT_RE.match(raw[0].strip())
        return i, {
            't': 'callout',
            'kind': m.group(1).lower(),
            'title': (m.group(3) or '').strip(),
            'blocks': _blocks(raw[1:]),
        }
    return i, {'t': 'quote', 'blocks': _blocks(raw, allow_lazy_quote=False)}


def _list(lines, i, end):
    first = LIST_RE.match(lines[i])
    ordered = first.group(2)[:1].isdigit()
    start = int(re.match(r'\d+', first.group(2)).group(0)) if ordered else 1
    indent = len(first.group(1))
    items = []
    while i < end:
        if not lines[i].strip():
            k = i + 1
            while k < end and not lines[k].strip():
                k += 1
            m = LIST_RE.match(lines[k]) if k < end else None
            if m and len(m.group(1)) == indent and m.group(2)[:1].isdigit() == ordered:
                i = k
                continue
            break
        m = LIST_RE.match(lines[i])
        if not m or len(m.group(1)) != indent or m.group(2)[:1].isdigit() != ordered:
            break
        rest = m.group(3)
        checked = None
        tm = re.match(r'^\[([ xX])\](?:[ \t]+(.*))?$', rest)
        if tm:
            checked = tm.group(1).lower() == 'x'
            rest = tm.group(2) or ''
        i += 1
        cont = []
        while i < end:
            if not lines[i].strip():
                k = i + 1
                while k < end and not lines[k].strip():
                    k += 1
                # 空行后面若不再缩进，这段列表就结束，后面的表格、标题另起一块。
                if k >= end or _lead(lines[k]) <= indent:
                    break
                cont.append('')
                i += 1
                continue
            m2 = LIST_RE.match(lines[i])
            if m2 and len(m2.group(1)) <= indent:
                break
            if _lead(lines[i]) <= indent and _stops_item(lines, i, end):
                break
            if _lead(lines[i]) < indent:
                break
            cont.append(_dedent(lines[i], indent + 2))
            i += 1
        body = ([rest] if rest else []) + cont
        items.append({'checked': checked, 'blocks': _blocks(body)})
    kind = 'ol' if ordered else 'ul'
    return i, {'t': kind, 'start': start, 'items': items}


def _stops_item(lines, i, end):
    line = lines[i]
    if FENCE_RE.match(line) or ATX_RE.match(line) or HR_RE.match(line) or QUOTE_RE.match(line):
        return True
    if MATH_OPEN_RE.match(line):
        return True
    if i + 1 < end and _is_table_sep(lines[i + 1]) and '|' in line:
        return True
    return False


def _dedent(line, n):
    k = 0
    while k < len(line) and k < n and line[k] == ' ':
        k += 1
    return line[k:]


def _indented(lines, i, end):
    buf = []
    while i < end:
        line = lines[i]
        if not line.strip():
            j = i + 1
            while j < end and not lines[j].strip():
                j += 1
            if j < end and _lead(lines[j]) >= 4:
                buf.append('')
                i += 1
                continue
            break
        if _lead(line) < 4:
            break
        buf.append(line[4:])
        i += 1
    while buf and buf[-1] == '':
        buf.pop()
    return i, {'t': 'code', 'lang': '', 'text': '\n'.join(buf)}


def _inl(s, i, end, stop, refs, depth):
    ops = []
    buf = []

    def flush():
        if buf:
            ops.append(('t', ''.join(buf)))
            buf.clear()

    while i < end:
        if stop and _is_closer(s, i, stop):
            flush()
            return ops, i
        if depth > 32:
            buf.append(s[i])
            i += 1
            continue
        if s[i] == '\\' and i + 1 < end and s[i + 1] in PUNCT:
            buf.append(s[i + 1])
            i += 2
            continue
        if s.startswith('%%', i):
            j = s.find('%%', i + 2)
            if j >= 0:
                flush()
                i = j + 2
                continue
        if s[i] == '`':
            got = _match_code(s, i)
            if got:
                flush()
                ops.append(('code', got[0]))
                i = got[1]
                continue
        if s[i] == '$' and not s.startswith('$$', i):
            got = _match_math(s, i)
            if got:
                flush()
                ops.append(('math', got[0]))
                i = got[1]
                continue
        if s.startswith('![', i) or s.startswith('[[', i):
            wiki = _match_wiki(s, i)
            if wiki:
                flush()
                ops.append(wiki[0])
                i = wiki[1]
                continue
        if s.startswith('![', i):
            img = re.match(r'!\[([^\]]*)\]\(([^)]+)\)', s[i:])
            if img:
                flush()
                ops.append(('image', img.group(1), _dest_url(img.group(2))))
                i += img.end()
                continue
        if s[i] == '[':
            fn = re.match(r'\[\^([^\]]+)\]', s[i:])
            if fn:
                flush()
                ops.append(('fn', fn.group(1)))
                i += fn.end()
                continue
            link = re.match(r'\[([^\]]+)\]\(([^)]+)\)', s[i:])
            if link:
                flush()
                ops.append(('link', inline_ops(link.group(1), refs), _dest_url(link.group(2))))
                i += link.end()
                continue
            ref = re.match(r'\[([^\]]+)\]\[([^\]]*)\]', s[i:])
            if ref:
                key = (ref.group(2) or ref.group(1)).casefold()
                if key in refs:
                    flush()
                    ops.append(('link', inline_ops(ref.group(1), refs), refs[key]))
                    i += ref.end()
                    continue
            short = re.match(r'\[([^\]]+)\]', s[i:])
            if short and short.group(1).casefold() in refs and not s.startswith('[[', i):
                flush()
                ops.append(('link', inline_ops(short.group(1), refs), refs[short.group(1).casefold()]))
                i += short.end()
                continue
        if s[i] == '<':
            html = _match_html(s, i, refs)
            if html:
                flush()
                ops.extend(html[0])
                i = html[1]
                continue
        if s.startswith(('http://', 'https://'), i):
            url = _bare_url(s, i)
            if url:
                flush()
                ops.append(('link', [('t', url)], url))
                i += len(url)
                continue
        if s[i] in '*_~=' and not (stop and s.startswith(stop, i)):
            made = _match_em(s, i, end, refs, depth)
            if made:
                flush()
                ops.append(made[0])
                i = made[1]
                continue
        if s[i] == '#' and (i == 0 or not (s[i - 1].isalnum() or s[i - 1] in '_/')):
            tag = TAG_RE.match(s, i)
            if tag:
                flush()
                name = tag.group(1).rstrip('/-')
                ops.append(('tag', name))
                i = tag.end()
                continue
        buf.append(s[i])
        i += 1
    flush()
    return ops, i


def _is_closer(s, i, marker):
    if not marker or not s.startswith(marker, i):
        return False
    ch = marker[0]
    if ch not in '*_~=':
        return False
    run = _run(s, i, ch)
    if run != len(marker):
        return False
    if ch == '_' and i + run < len(s) and s[i + run].isalnum():
        return False
    return True


def _run(s, i, ch):
    j = i
    while j < len(s) and s[j] == ch:
        j += 1
    return j - i


def _match_em(s, i, end, refs, depth):
    ch = s[i]
    run = _run(s, i, ch)
    if ch in '*_':
        lengths = [n for n in (3, 2, 1) if n <= run]
    elif ch == '~' and run >= 2:
        lengths = [2]
    elif ch == '=' and run >= 2:
        lengths = [2]
    else:
        return None
    for length in lengths:
        if ch == '_' and i > 0 and s[i - 1].isalnum():
            continue
        marker = ch * length
        inner, j = _inl(s, i + length, end, marker, refs, depth + 1)
        if not inner or j >= end or not _is_closer(s, j, marker):
            continue
        kind = {'***': 'bi', '___': 'bi', '**': 'b', '__': 'b', '*': 'i', '_': 'i', '~~': 's', '==': 'mark'}[marker]
        return (kind, inner), j + length
    return None


def _match_code(s, i):
    n = 0
    while i + n < len(s) and s[i + n] == '`':
        n += 1
    if n == 0:
        return None
    j = s.find('`' * n, i + n)
    if j < 0:
        return None
    inner = s[i + n:j]
    if len(inner) >= 2 and inner.startswith(' ') and inner.endswith(' ') and inner.strip():
        inner = inner[1:-1]
    return inner, j + n


def _match_math(s, i):
    if i + 1 >= len(s) or s[i + 1].isspace():
        return None
    j = i + 1
    while j < len(s) and s[j] != '$' and s[j] != '\n':
        j += 1
    if j >= len(s) or s[j] != '$' or s[j - 1].isspace() or j == i + 1:
        return None
    return s[i + 1:j], j + 1


def _match_wiki(s, i):
    m = re.match(r'(!?)\[\[([^\]]+)\]\]', s[i:])
    if not m:
        return None
    body = m.group(2)
    alias = ''
    width = 0
    if '|' in body:
        body, alias = body.rsplit('|', 1)
        alias = alias.strip()
        if alias.isdigit():
            width = int(alias)
            alias = ''
    heading = ''
    if '#' in body:
        body, heading = body.split('#', 1)
    return ('wiki', bool(m.group(1)), body.strip(), heading.strip(), alias, width), i + m.end()


def _dest_url(raw):
    raw = (raw or '').strip()
    if raw.startswith('<'):
        m = re.match(r'<([^>]*)>', raw)
        raw = m.group(1) if m else raw[1:]
    else:
        m = re.match(r'(\S+)', raw)
        raw = m.group(1) if m else raw
    return unquote(raw.strip())


def _bare_url(s, i):
    m = re.match(r'https?://[^\s<]+', s[i:])
    if not m:
        return ''
    url = m.group(0)
    while url and url[-1] in '.,;:!?':
        url = url[:-1]
    if url.endswith(')') and url.count('(') < url.count(')'):
        url = url[:-1]
    return url


def _match_html(s, i, refs):
    br = re.match(r'<br\s*/?>', s[i:], re.I)
    if br:
        return [('br',)], i + br.end()
    auto = re.match(r'<(https?://[^>\s]+|mailto:[^>\s]+)>', s[i:], re.I)
    if auto:
        url = auto.group(1)
        return [('link', [('t', url)], url)], i + auto.end()
    anchor = re.match(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', s[i:], re.I | re.S)
    if anchor:
        return [('link', inline_ops(anchor.group(2), refs), anchor.group(1))], i + anchor.end()
    paired = re.match(r'<(b|strong|i|em|u|s|del|mark|code)(\s[^>]*)?>', s[i:], re.I)
    if paired:
        name = paired.group(1).lower()
        close = re.search(r'</' + name + r'\s*>', s[i + paired.end():], re.I)
        if close:
            inner = s[i + paired.end():i + paired.end() + close.start()]
            kind = {
                'b': 'b', 'strong': 'b', 'i': 'i', 'em': 'i', 'u': 'u',
                's': 's', 'del': 's', 'mark': 'mark', 'code': 'code',
            }[name]
            if kind == 'code':
                return [('code', inner)], i + paired.end() + close.end()
            return [(kind, inline_ops(inner, refs))], i + paired.end() + close.end()
    other = re.match(r'</?[A-Za-z][^>]*>', s[i:])
    if other:
        return [], i + other.end()
    return None


def _ops_text(ops):
    parts = []
    for op in ops:
        k = op[0]
        if k == 'tag':
            parts.append('#' + op[1])
        elif k in ('t', 'code', 'math', 'fn'):
            parts.append(op[1])
        elif k in ('b', 'i', 'bi', 's', 'mark', 'u'):
            parts.append(_ops_text(op[1]))
        elif k == 'link':
            parts.append(_ops_text(op[1]))
        elif k == 'image':
            parts.append(op[1] or os.path.basename(op[2]))
        elif k == 'wiki':
            parts.append(op[4] or op[3] or op[2] or '链接')
        elif k == 'br':
            parts.append('\n')
    return ''.join(parts)


def _style_name(styles):
    name = ''
    if 'b' in styles:
        name += 'b'
    if 'i' in styles:
        name += 'i'
    if 's' in styles:
        name += 's'
    return name


def _render_blocks(blocks, ctx, quote, depth):
    for block in blocks:
        _render_one(block, ctx, quote, depth)


def _render_one(block, ctx, quote, depth):
    kind = block['t']
    if kind == 'h':
        _render_heading(block, ctx, quote)
    elif kind == 'p':
        _render_paragraph(block['text'], ctx, quote, depth, 'p')
    elif kind == 'quote':
        _render_quote(block['blocks'], ctx, quote)
    elif kind == 'callout':
        _render_callout(block, ctx, quote, depth)
    elif kind == 'code':
        _render_pre(block.get('text') or '', ctx, 'codeblock', quote)
    elif kind == 'math':
        _render_pre(block.get('text') or '', ctx, 'mathblock', quote)
    elif kind == 'hr':
        ctx.view.insert('end', '─' * 28 + '\n', ('hr',))
    elif kind in ('ul', 'ol'):
        _render_list(block, ctx, quote, depth)
    elif kind == 'table':
        _render_table(block, ctx, quote)
    elif kind == 'fm':
        _render_fm(block, ctx)


def _add_range(view, tag, start):
    end = view.index('end-1c')
    if view.compare(start, '<', end):
        view.tag_add(tag, start, end)


def _render_heading(block, ctx, quote):
    level = min(max(int(block['level']), 1), 6)
    view = ctx.view
    start = view.index('end-1c')
    key = anchor_key(block.get('text') or '')
    if key and key not in view._md_anchors:
        view._md_anchors[key] = start
    tags = ('h%d' % level,)
    _draw_ops(inline_ops(block.get('text') or '', ctx.refs), ctx, tags, set())
    view.insert('end', '\n', tags)
    if quote:
        _add_range(view, 'q%d' % min(quote, 3), start)


def _render_paragraph(text, ctx, quote, depth, base):
    lines = (text or '').split('\n')
    buf = []

    def flush():
        if not any(part.strip() for part in buf):
            buf.clear()
            return
        _draw_paragraph('\n'.join(buf), ctx, quote, depth, base)
        buf.clear()

    for line in lines:
        embed = ONLY_EMBED.match(line.strip())
        image = ONLY_IMG.match(line.strip())
        if embed or image:
            flush()
            if embed:
                _render_embed_block(embed.group(1), ctx, quote, depth)
            else:
                _render_image_block(image.group(1), _dest_url(image.group(2)), ctx)
            continue
        buf.append(line)
    flush()


def _draw_paragraph(text, ctx, quote, depth, base):
    view = ctx.view
    start = view.index('end-1c')
    tags = (base,)
    if base == 'p' and depth:
        tags = ('li%d' % min(depth, 5),)
    lines = text.split('\n')
    for n, line in enumerate(lines):
        _draw_ops(inline_ops(line, ctx.refs), ctx, tags, set())
        if n != len(lines) - 1:
            view.insert('end', '\n', tags)
    # 列表项之间只留行距。段距会把每一项拆成空一行。
    closing = tags if depth else tags + ('gap',)
    view.insert('end', '\n', closing)
    if quote:
        _add_range(view, 'q%d' % min(quote, 3), start)
    if depth and base == 'p':
        _add_range(view, 'li%d' % min(depth, 5), start)


def _render_quote(blocks, ctx, quote):
    view = ctx.view
    start = view.index('end-1c')
    depth = min(quote + 1, 3)
    _render_blocks(blocks, ctx, depth, 0)
    _add_range(view, 'quote', start)
    _add_range(view, 'q%d' % depth, start)
    # end-1c 是文本框末尾那个换行，后面的内容会插在这里。
    # 用左重力记号钉住引用的结尾，避免竖线被带到下一段上。
    end = view.index('end-1c')
    if view.compare(start, '<', end):
        bars = getattr(view, '_md_quotes', None)
        if bars is None:
            view._md_quotes = bars = []
        mark = 'qbar%d' % len(bars)
        view.mark_set(mark, end)
        view.mark_gravity(mark, 'left')
        bars.append((start, mark, depth))


def _render_callout(block, ctx, quote, depth):
    view = ctx.view
    kind = block.get('kind') or 'note'
    title = block.get('title') or kind
    tag = 'co_' + kind if kind in CALLOUT_COLOR else 'co_other'
    start = view.index('end-1c')
    view.insert('end', '▍ ', (tag,))
    _draw_ops(inline_ops(title, ctx.refs), ctx, (tag,), set())
    view.insert('end', '\n', (tag,))
    _render_blocks(block.get('blocks') or [], ctx, min(quote + 1, 3), depth)
    _add_range(view, 'q%d' % min(quote + 1, 3), start)


def _render_pre(text, ctx, tag, quote):
    view = ctx.view
    start = view.index('end-1c')
    body = text if text.endswith('\n') or not text else text + '\n'
    if not body:
        body = '\n'
    _insert_text(view, body, (tag,))
    if quote:
        _add_range(view, 'q%d' % min(quote, 3), start)


def _render_list(block, ctx, quote, depth):
    view = ctx.view
    number = int(block.get('start') or 1)
    tag = 'li%d' % min(depth, 5)
    for item in block['items']:
        # 记号后用不断行空格。普通空格是换行点，后面整段中文没有空格时，
        # 正文会被整段换到下一行，序号后面就空出一截。
        if block['t'] == 'ol':
            mark = '%d.\u00a0' % number
            number += 1
            box = False
        elif item['checked'] is True:
            mark = '☑\u00a0'
            box = True
        elif item['checked'] is False:
            mark = '☐\u00a0'
            box = True
        else:
            mark = '•\u00a0'
            box = False
        start = view.index('end-1c')
        view.insert('end', mark, (tag, 'box') if box else (tag,))
        children = item['blocks']
        if children and children[0]['t'] == 'p':
            first = children[0]['text']
            lines = first.split('\n')
            _draw_ops(inline_ops(lines[0], ctx.refs), ctx, (tag,), set())
            view.insert('end', '\n', (tag,))
            if len(lines) > 1:
                _draw_paragraph('\n'.join(lines[1:]), ctx, quote, depth + 1, 'p')
            for child in children[1:]:
                _render_one(child, ctx, quote, depth + 1)
        else:
            view.insert('end', '\n', (tag,))
            _render_blocks(children, ctx, quote, depth + 1)
        if quote:
            _add_range(view, 'q%d' % min(quote, 3), start)


def _free_font(obj):
    """马上删掉 Tk 字体。只丢掉引用要等回收，切文件时 GDI 会堆住。"""
    try:
        obj._tk.call('font', 'delete', obj.name)
    except Exception:
        pass


def _take_fonts(widget, found):
    for child in list(widget.winfo_children()):
        _take_fonts(child, found)
    fonts = list(getattr(widget, '_md_fonts', []) or [])
    widget._md_fonts = []
    found.extend(fonts)


def _drop_embedded(view):
    held = getattr(view, '_md_fonts', None)
    doomed = []
    for w in list(getattr(view, '_md_windows', []) or []):
        spec = getattr(w, '_table', None)
        measure = spec.get('measure') if spec else None
        if measure is not None and held is not None:
            try:
                held.remove(measure)
            except ValueError:
                pass
            doomed.append(measure)
        _take_fonts(w, doomed)
        w._table = None
        try:
            w.destroy()
        except tk.TclError:
            pass
    view._md_windows = []
    for obj in doomed:
        _free_font(obj)


def release_document(view, images=None):
    """切到下一篇之前，先拆掉这一篇的表格、正文和图片。"""
    clear_quote_bars(view)
    _drop_embedded(view)
    try:
        view.delete('1.0', 'end')
    except tk.TclError:
        pass
    view._md_anchors = {}
    if images:
        pending = list(images)
        images.clear()
        for img in pending:
            try:
                img.tk.call('image', 'delete', img.name)
            except Exception:
                pass
        del pending
    gc.collect()


def _render_table(block, ctx, quote=0):
    """表格铺满正文宽度。窗口变窄就换行，变宽就收回来。"""
    rows = block.get('rows') or []
    aligns = block.get('align') or []
    if not rows or not aligns:
        return
    view = ctx.view
    zoom = getattr(view, '_md_zoom', 100)
    ncols = len(aligns)
    nrows = len(rows)

    def px(n):
        return max(1, int(round(n * zoom / 100.0)))

    measure = tkfont.Font(family='Microsoft YaHei UI', size=px(14), weight='bold')
    fonts = getattr(view, '_md_fonts', None)
    if fonts is not None:
        fonts.append(measure)
    naturals = []
    plain = []
    for c in range(ncols):
        widest = px(48)
        col_text = []
        for row in rows:
            src = row[c] if c < len(row) else ''
            shown = visible_text(src, ctx.refs)
            col_text.append(shown)
            for line in shown.split('\n'):
                widest = max(widest, measure.measure(line) + px(20))
        plain.append(col_text)
        naturals.append(widest)

    border = '#3a3a3a'
    head_bg = '#243044'
    body_bg = '#141414'
    alt_bg = '#1a1a1a'
    outer = tk.Frame(view, bg=border, bd=0, highlightthickness=0)
    outer.grid_propagate(False)
    view._md_windows.append(outer)
    just = {'l': 'left', 'c': 'center', 'r': 'right'}
    grid = []
    saved = ctx.view
    ctx.in_cell = True
    try:
        for r, row in enumerate(rows):
            bg = head_bg if r == 0 else (alt_bg if r % 2 == 0 else body_bg)
            placed = []
            for c in range(ncols):
                src = row[c] if c < len(row) else ''
                cell = tk.Text(
                    outer, width=4, height=1, wrap='char', relief='flat', bd=0,
                    highlightthickness=0, bg=bg, fg='#ffffff' if r == 0 else FG,
                    insertwidth=0, cursor='arrow', padx=px(8), pady=px(3),
                    undo=False, selectbackground='#264f78', selectforeground='#ffffff',
                )
                configure_tags(cell, zoom)
                cell.configure(bg=bg, fg='#ffffff' if r == 0 else FG, wrap='char')
                cell.tag_configure('al', justify=just.get(aligns[c], 'left'))
                ctx.view = cell
                _draw_ops(inline_ops(src, ctx.refs), ctx, ('al',), {'b'} if r == 0 else set())
                cell.grid(
                    row=r, column=c, sticky='nsew',
                    padx=(1, 1 if c == ncols - 1 else 0),
                    pady=(1, 1 if r == nrows - 1 else 0),
                )
                _keep_cell_readonly(cell, view)
                placed.append({'w': cell, 'text': plain[c][r]})
            grid.append(placed)
    finally:
        ctx.view = saved
        ctx.in_cell = False
    pad = px(8 + int(quote or 0) * 18)
    outer._table = {
        'measure': measure,
        'naturals': naturals,
        'grid': grid,
        'pad': pad,
        'px': px,
        'min_px': px(72),
        'ncols': ncols,
        'nrows': nrows,
        'quote': int(quote or 0),
    }
    outer._measure = measure
    _apply_table_width(outer, _table_avail(view, pad))
    _watch_table_width(view)
    # 上下各空一行。换行上的标记不会撑开行高，空隙加在嵌入窗口两侧。
    # 顶对齐，避免这段空白把表格托在行中间。
    try:
        air = int(view.tag_cget('gap', 'spacing3'))
    except (TypeError, ValueError, tk.TclError):
        air = px(16)
    view.window_create('end', window=outer, padx=pad, pady=max(8, air), align='top')
    view.insert('end', '\n', ('gap',))


def rescale_embedded(view):
    """字号变了之后，只改表格的字和边距，不再把整篇拆掉重排。"""
    zoom = getattr(view, '_md_zoom', 100)

    def px(n):
        return max(1, int(round(n * zoom / 100.0)))

    try:
        air = int(view.tag_cget('gap', 'spacing3'))
    except (TypeError, ValueError, tk.TclError):
        air = px(16)
    air = max(8, air)
    for outer in list(getattr(view, '_md_windows', []) or []):
        spec = getattr(outer, '_table', None)
        if not spec:
            continue
        try:
            for row in spec['grid']:
                for info in row:
                    _scale_fonts(info['w'], zoom)
                    info['w'].configure(pady=px(3))
            spec['measure'].configure(size=px(14))
            spec['px'] = px
            spec['min_px'] = px(72)
            quote = int(spec.get('quote') or 0)
            spec['pad'] = px(8 + quote * 18)
            _apply_table_width(outer, _table_avail(view, spec['pad']))
            _move_window(view, outer, spec['pad'], air)
        except tk.TclError:
            continue


def _scale_fonts(widget, zoom):
    for obj in list(getattr(widget, '_md_fonts', []) or []):
        base = getattr(obj, '_md_pt', None)
        if not base:
            continue
        size = max(1, int(round(base * zoom / 100.0)))
        try:
            if int(obj.cget('size')) != size:
                obj.configure(size=size)
        except tk.TclError:
            continue


def _move_window(view, widget, padx, pady):
    name = str(widget)
    try:
        dumped = view.dump('1.0', 'end', window=True)
    except tk.TclError:
        return
    for item in dumped or ():
        if item and item[0] == 'window' and item[1] == name:
            view.window_configure(item[2], padx=padx, pady=pady)
            return


def _table_avail(view, pad):
    """正文区能给表格的宽度。窗口还没显示时，用文本框的字符宽度估。"""
    try:
        padx = int(float(view.cget('padx')))
    except (TypeError, ValueError, tk.TclError):
        padx = 0
    width = view.winfo_width()
    if width < 80:
        try:
            chars = int(view.cget('width'))
        except (TypeError, ValueError, tk.TclError):
            chars = 80
        try:
            font = tkfont.Font(font=view.cget('font'))
            width = chars * max(int(font.measure('0')), 1) + padx * 2
        except tk.TclError:
            width = 780
    return max(160, width - padx * 2 - pad * 2)


def _fit_columns(naturals, avail, min_px):
    naturals = [max(int(n), 1) for n in naturals]
    avail = max(int(avail), len(naturals) * 8)
    total = sum(naturals)
    if total <= avail:
        extra = avail - total
        cols = [n + int(extra * n / total) for n in naturals]
    else:
        cols = [max(min_px, int(n * avail / total)) for n in naturals]
        if sum(cols) > avail:
            cols = [max(8, int(n * avail / total)) for n in naturals]
    if cols:
        cols[-1] += avail - sum(cols)
        if cols[-1] < 8:
            cols[-1] = 8
    return cols


def _cell_line_px(spec, measure):
    """格子的像素行高。粗体测量字体比正文字体高 1px，按它算会在表底攒出一条灰边。"""
    try:
        cell = spec['grid'][0][0]['w']
        return max(int(cell.tk.call('font', 'metrics', cell.cget('font'), '-linespace')), 1)
    except (tk.TclError, IndexError, KeyError, TypeError, ValueError):
        try:
            return max(int(measure.metrics('linespace')), 1)
        except tk.TclError:
            return 1


def _apply_table_width(outer, avail):
    spec = getattr(outer, '_table', None)
    if not spec or avail < 80:
        return
    measure = spec['measure']
    px = spec['px']
    # 列宽已经含格子之间那 1px。外框再加一圈，右边就会多出一条边框色。
    cols = _fit_columns(spec['naturals'], max(80, avail), spec['min_px'])
    char_w = max(int(measure.measure('0')), 1)
    line_px = _cell_line_px(spec, measure)
    pad_x = px(8)
    row_h = []
    for row in spec['grid']:
        high = 0
        for c, info in enumerate(row):
            cell = info['w']
            inner = max(px(24), cols[c] - pad_x * 2)
            lines = _cell_lines(cell, info['text'], measure, inner)
            cell.configure(width=max(2, inner // char_w), height=lines)
            # 行高用字体行距算出。每个格子都 update_idletasks 会让缩放顿住。
            try:
                pad_y = int(float(cell.cget('pady')))
            except (TypeError, ValueError, tk.TclError):
                pad_y = px(3)
            high = max(high, lines * line_px + pad_y * 2)
        row_h.append(high)
    for c, width in enumerate(cols):
        outer.grid_columnconfigure(c, minsize=width, weight=0)
    # 底下那 1px 是最后一行的边。行高按格子自己的行距，不用粗体测量字体。
    outer.configure(width=sum(cols), height=sum(row_h) + spec['nrows'] + 1)


def _relayout_tables(view):
    view._md_layout_job = None
    width = view.winfo_width()
    if width < 80 or width == getattr(view, '_md_laid_width', None):
        return
    view._md_laid_width = width
    for outer in list(getattr(view, '_md_windows', []) or []):
        spec = getattr(outer, '_table', None)
        if spec is None:
            continue
        try:
            _apply_table_width(outer, _table_avail(view, spec['pad']))
        except tk.TclError:
            continue
    _sync_metrics(view)


def _watch_table_width(view):
    if getattr(view, '_md_width_watch', False):
        return
    view._md_width_watch = True
    view._md_laid_width = 0
    view._md_layout_job = None

    def on_configure(e):
        if e.widget is not view or e.width < 80:
            return
        if e.width == getattr(view, '_md_laid_width', None):
            return
        job = getattr(view, '_md_layout_job', None)
        if job:
            try:
                view.after_cancel(job)
            except tk.TclError:
                pass
        view._md_layout_job = view.after(60, lambda: _relayout_tables(view))

    view.bind('<Configure>', on_configure, add='+')


def _sync_metrics(view):
    """行高缓存必须等文本框有真实宽度再算。

    窗口还没排开时宽度是 1，这时强制计算会把每个字当成一行，
    整篇被记成几万像素。等真正显示时缓存一对上，滚动条就跳。
    """
    try:
        if view.winfo_width() < 80:
            return
        view.tk.call(view._w, 'sync')
    except tk.TclError:
        pass


def scroll_pixels(view, delta):
    """滚轮按像素走。表格在文本框里是一整行，按行滚会整表跳过去。"""
    try:
        delta = int(delta)
    except (TypeError, ValueError):
        return
    if delta > 0:
        view.yview('scroll', -(delta // 3), 'pixels')
    elif delta < 0:
        view.yview('scroll', (2 - delta) // 3, 'pixels')


def _glyph_widths(font):
    """汉字按一个宽字计，英文数字按 0 的宽度计。逐字 measure 会把缩放拖慢。"""
    try:
        size = int(font.cget('size'))
    except (TypeError, ValueError, tk.TclError):
        size = 0
    cached = getattr(font, '_md_widths', None)
    if cached and cached[0] == size:
        return cached[1], cached[2]
    wide = max(int(font.measure('\u4e00')), 1)
    narrow = max(int(font.measure('0')), 1)
    font._md_widths = (size, wide, narrow)
    return wide, narrow


def _wrap_lines(text, font, width_px):
    width_px = max(int(width_px), 8)
    wide, narrow = _glyph_widths(font)
    total = 0
    for para in (text or '').split('\n'):
        if para == '':
            total += 1
            continue
        used = 0
        rows = 1
        for ch in para:
            w = wide if ord(ch) > 127 else narrow
            if used and used + w > width_px:
                rows += 1
                used = w
            else:
                used += w
        total += rows
    return max(1, total)


def _cell_lines(cell, text, font, width_px):
    lines = _wrap_lines(text, font, width_px)
    if not getattr(cell, '_md_has_image', False):
        return lines
    line_px = max(int(font.metrics('linespace')), 1)
    try:
        dumped = cell.dump('1.0', 'end-1c', image=True)
    except tk.TclError:
        dumped = ()
    for item in dumped or ():
        if not item or item[0] != 'image':
            continue
        try:
            h = int(cell.tk.call('image', 'height', item[1]))
        except (tk.TclError, TypeError, ValueError):
            continue
        lines = max(lines, (h + line_px - 1) // line_px)
    return lines


def _keep_cell_readonly(cell, parent):
    def eat(e):
        if e.state & 0x4 and e.keysym.lower() in ('c', 'a'):
            return None
        if e.keysym in ('Left', 'Right', 'Up', 'Down', 'Prior', 'Next', 'Home', 'End'):
            return None
        return 'break'

    def wheel(e):
        scroll_pixels(parent, e.delta)
        return 'break'

    cell.bind('<Key>', eat)
    cell.bind('<MouseWheel>', wheel)


def _render_fm(block, ctx):
    view = ctx.view
    for key, val in block['items']:
        if key:
            _insert_text(view, key, ('fmkey',))
            _insert_text(view, '  ' + val + '\n', ('fm',))
        else:
            _insert_text(view, val + '\n', ('fm',))
    view.insert('end', '\n', ('gap',))


def _render_notes(ctx):
    if not ctx.notes:
        return
    view = ctx.view
    view.insert('end', '脚注\n', ('h2',))
    for ident, text in ctx.notes.items():
        start = view.index('end-1c')
        view._md_anchors['fn:' + anchor_key(ident)] = start
        view.insert('end', '[%s] ' % ident, ('muted',))
        _render_paragraph(text, ctx, 0, 0, 'p')


def _render_embed_block(body, ctx, quote, depth):
    op = _match_wiki('![[' + body + ']]', 0)
    if not op:
        return
    _wiki = op[0]
    target, heading, alias, width = _wiki[2], _wiki[3], _wiki[4], _wiki[5]
    label = alias or heading or target or '嵌入'
    if not target:
        src = getattr(ctx, 'src', '')
        path = ctx.path or ''
    else:
        finder = ctx.finder()
        path = finder.file(target)
        if path and os.path.splitext(path)[1].lower() in IMG_EXTS:
            _insert_image(ctx, label, path, width, label, block=True)
            return
        path = finder.note(target) or ''
        src = ''
    if path and os.path.splitext(path)[1].lower() in IMG_EXTS:
        _insert_image(ctx, label, path, width, label, block=True)
        return
    if target and (not path or ctx.depth >= 2):
        _wiki_link(ctx, label, path, heading)
        return
    if not target and not heading:
        _wiki_link(ctx, label, '', '')
        return
    token = (os.path.normcase(path) if path else 'self') + '#' + heading
    if token in ctx.stack or ctx.depth >= 2:
        _wiki_link(ctx, label, path, heading)
        return
    if target:
        try:
            src = _read_note(path)
        except OSError:
            _wiki_link(ctx, label, '', heading)
            return
    if heading.startswith('^'):
        src = _slice_block(src or '', heading[1:])
    elif heading:
        src = _slice_heading(src or '', heading)
    if src is None:
        ctx.view.insert('end', '（没有这一节：%s）\n' % (heading or label), ('muted', 'gap'))
        return
    ctx.stack.add(token)
    ctx.depth += 1
    old_refs = ctx.refs
    blocks, meta = parse_document(src or '')
    ctx.refs = dict(old_refs)
    ctx.refs.update(meta.get('refs') or {})
    _render_blocks([b for b in blocks if b['t'] != 'fm'], ctx, quote, depth)
    ctx.refs = old_refs
    ctx.depth -= 1
    ctx.stack.discard(token)


def _render_image_block(alt, url, ctx):
    _insert_image(ctx, alt, _local_or_url(ctx.base, url), 0, alt, block=True)


def _slice_heading(src, heading):
    lines = src.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    key = anchor_key(heading)
    start = None
    level = 0
    for i, line in enumerate(lines):
        m = ATX_RE.match(line)
        if not m or m.group(2) is None:
            continue
        if start is None:
            if anchor_key(m.group(2)) == key:
                start = i
                level = len(m.group(1))
            continue
        if len(m.group(1)) <= level:
            return '\n'.join(lines[start:i])
    if start is None:
        return None
    return '\n'.join(lines[start:])


def _slice_block(src, bid):
    lines = src.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    mark = re.compile(r'(?:^|\s)\^%s\s*$' % re.escape(bid))
    for i, line in enumerate(lines):
        if not mark.search(line):
            continue
        a = i
        while a > 0 and lines[a - 1].strip():
            a -= 1
        b = i + 1
        while b < len(lines) and lines[b].strip():
            b += 1
        return '\n'.join(lines[a:b])
    return None


def _read_note(path):
    with open(path, 'rb') as f:
        data = f.read()
    if data.startswith(b'\xef\xbb\xbf'):
        return data[3:].decode('utf-8')
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('gbk', errors='replace')


def _wiki_link(ctx, label, path, heading):
    if path and heading:
        url = path + '#' + heading
    elif path:
        url = path
    elif heading:
        url = '#' + heading
    else:
        ctx.view.insert('end', label + '\n', ('miss', 'gap'))
        return
    _link(ctx, [('t', label)], url, ('p',))
    ctx.view.insert('end', '\n', ('gap',))


def _insert_image(ctx, alt, url, width, fallback=None, block=True):
    view = ctx.view
    path = url
    remote = re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', url or '')
    if remote:
        _link(ctx, [('t', alt or url)], url, ('p',))
        if block:
            view.insert('end', '\n', ('gap',))
        return
    if url and not os.path.isabs(url):
        path = _local_or_url(ctx.base, url)
    shown = False
    if path and os.path.isfile(path):
        try:
            from PIL import Image, ImageTk
            im = Image.open(path)
            limit = width if width and width < 860 else 860
            if getattr(ctx, 'in_cell', False):
                limit = min(limit, 240)
            im.thumbnail((limit, 640))
            photo = ImageTk.PhotoImage(im)
            ctx.images.append(photo)
            if getattr(ctx, 'in_cell', False):
                view._md_has_image = True
            view.image_create('end', image=photo, padx=4, pady=6)
            if block:
                view.insert('end', '\n', ('gap',))
            shown = True
        except Exception:
            shown = False
    if not shown:
        name = fallback or alt or os.path.basename(path or url or '') or '图片'
        if block:
            view.insert('end', name + '\n', ('muted', 'gap'))
        else:
            view.insert('end', name, ('muted',))


def _local_or_url(base, url):
    if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', url or ''):
        return url
    if url and os.path.isabs(url):
        return url
    return os.path.normpath(os.path.join(base or '', url or ''))


def _draw_ops(ops, ctx, tags, styles):
    view = ctx.view
    style = _style_name(styles)
    extra = (style,) if style else ()
    for op in ops:
        k = op[0]
        if k == 't':
            if op[1]:
                _insert_text(view, op[1], tags + extra)
        elif k in ('b', 'i', 'bi', 's', 'mark', 'u'):
            nxt = set(styles)
            if k == 'bi':
                nxt.update(('b', 'i'))
            elif k in ('b', 'i', 's'):
                nxt.add(k)
            if k == 'mark':
                _draw_ops(op[1], ctx, tags + ('mark',), nxt)
            elif k == 'u':
                _draw_ops(op[1], ctx, tags + ('u',), nxt)
            else:
                _draw_ops(op[1], ctx, tags, nxt)
        elif k == 'code':
            _insert_text(view, op[1], tags + ('code',))
        elif k == 'math':
            _insert_text(view, op[1], tags + ('math',))
        elif k == 'br':
            view.insert('end', '\n', tags)
        elif k == 'link':
            _link(ctx, op[1], op[2], tags)
        elif k == 'image':
            _insert_image(ctx, op[1], op[2], 0, block=False)
        elif k == 'wiki':
            _draw_wiki(op, ctx, tags)
        elif k == 'fn':
            _link(ctx, [('t', '[' + op[1] + ']')], '#fn:' + op[1], tags)
        elif k == 'tag':
            _insert_text(view, '#' + op[1], tags + ('tag',))


def _draw_wiki(op, ctx, tags):
    _embed, target, heading, alias, width = op[1], op[2], op[3], op[4], op[5]
    label = alias or (target + ('#' + heading if heading else '') if target else heading) or '链接'
    if op[1]:
        path = ctx.finder().file(target) if target else ''
        if path and os.path.splitext(path)[1].lower() in IMG_EXTS:
            _insert_image(ctx, alias or os.path.basename(path), path, width, block=False)
            return
        _wiki_link(ctx, label, ctx.finder().note(target) if target else ctx.path, heading)
        return
    if not target and heading:
        _link(ctx, [('t', label)], '#' + heading, tags)
        return
    path = ctx.finder().note(target) if target else ''
    if not path and target:
        path = ctx.finder().file(target)
    if path or not target:
        url = path or ('#' + heading if heading else '')
        if path and heading:
            url = path + '#' + heading
        _link(ctx, [('t', label)], url, tags)
        return
    ctx.view.insert('end', label, tags + ('miss',))


def _link(ctx, ops, url, tags):
    view = ctx.view
    n = ctx.on_link.get('n', 0)
    ctx.on_link['n'] = n + 1
    tag = 'lnk%d' % n
    _draw_ops(ops, ctx, tags + ('link', tag), set())

    def open_url(_e, u=url):
        ctx.on_link['open'](u)

    view.tag_bind(tag, '<Button-1>', open_url)
    view.tag_bind(tag, '<Enter>', lambda e: view.configure(cursor='hand2'))
    view.tag_bind(tag, '<Leave>', lambda e: view.configure(cursor='arrow'))


