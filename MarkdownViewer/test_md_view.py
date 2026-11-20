# -*- coding: utf-8 -*-
"""阅读器 Markdown 排版。不启动窗口也能核对拆块；排版再用一个藏起来的文本框看结果。"""
import os
import tempfile
import unittest

import md_view as md


SRC = """---
title: 标题
tags: [甲, 乙]
---

# 一级

一段
第二行

**粗** *斜* ***粗斜*** ~~删~~ ==亮== `码`

[[笔记|别名]]

> [!warning] 小心
> 正文

- [ ] 待办
- [x] 完成
  - 子项

| 名 | 值 |
| --- | ---: |
| **甲** | 1 |

~~~
原样 **不解释**
~~~

    缩进代码

标题二
---

见[^1]。

%%隐藏%%

$$
e=mc^2
$$

[^1]: 注

[站](https://example.com) 与 [参考]

[参考]: https://example.com/ref

<b>加粗</b> 换行<br>后
"""


class ParseTest(unittest.TestCase):
    def test_formats_survive_as_blocks(self):
        blocks, meta = md.parse_document(SRC)
        kinds = [b['t'] for b in blocks]
        self.assertEqual(blocks[0]['t'], 'fm')
        self.assertIn(('title', '标题'), blocks[0]['items'])
        self.assertIn('甲', blocks[0]['items'][1][1])
        self.assertIn('h', kinds)
        para = next(b for b in blocks if b['t'] == 'p' and b['text'].startswith('一段'))
        self.assertEqual(para['text'], '一段\n第二行')
        self.assertIn('callout', kinds)
        call = next(b for b in blocks if b['t'] == 'callout')
        self.assertEqual(call['kind'], 'warning')
        self.assertEqual(call['title'], '小心')
        ul = next(b for b in blocks if b['t'] == 'ul')
        self.assertEqual([it['checked'] for it in ul['items']], [False, True])
        nested = ul['items'][1]['blocks']
        self.assertTrue(any(b['t'] == 'ul' for b in nested))
        table = next(b for b in blocks if b['t'] == 'table')
        self.assertEqual(table['align'][1], 'r')
        self.assertIn('**甲**', table['rows'][1][0])
        code = next(b for b in blocks if b['t'] == 'code')
        self.assertIn('**不解释**', code['text'])
        self.assertTrue(any(b['t'] == 'h' and b['text'] == '标题二' and b['level'] == 2 for b in blocks))
        self.assertEqual(meta['notes']['1'], '注')
        self.assertIn('example.com/ref', meta['refs']['参考'])
        self.assertTrue(any(b['t'] == 'math' and 'e=mc^2' in b['text'] for b in blocks))
        flat = ''.join(b.get('text', '') for b in blocks)
        self.assertNotIn('隐藏', flat)
        self.assertNotIn('^1]:', flat)

    def test_inline_marks(self):
        ops = md.inline_ops('**粗** *斜* ***粗斜*** ~~删~~ ==亮== `码` [[笔记|别名]] #标签')
        text = md.visible_text('**粗** *斜* ***粗斜*** ~~删~~ ==亮== `码` [[笔记|别名]] #标签')
        self.assertEqual(text, '粗 斜 粗斜 删 亮 码 别名 #标签')
        kinds = [op[0] for op in ops]
        self.assertIn('b', kinds)
        self.assertIn('s', kinds)
        self.assertIn('mark', kinds)
        self.assertIn('wiki', kinds)
        self.assertIn('tag', kinds)

    def test_quote_keeps_heading(self):
        blocks, _meta = md.parse_document('> # 里面\n> 一句')
        quote = blocks[0]
        self.assertEqual(quote['t'], 'quote')
        self.assertEqual(quote['blocks'][0]['t'], 'h')
        self.assertEqual(quote['blocks'][0]['text'], '里面')

    def test_blank_line_keeps_rule(self):
        blocks, _meta = md.parse_document('段落\n\n---\n')
        self.assertEqual([b['t'] for b in blocks], ['p', 'hr'])

    def test_embed_and_render(self):
        import tkinter as tk
        folder = tempfile.mkdtemp()
        child = os.path.join(folder, '笔记.md')
        with open(child, 'w', encoding='utf-8') as f:
            f.write('# 子标题\n\n子笔记正文\n')
        parent = os.path.join(folder, '父.md')
        with open(parent, 'w', encoding='utf-8') as f:
            f.write(SRC + '\n\n![[笔记]]\n\n看 ![图](没有.png) 这里\n')
        root = tk.Tk()
        root.withdraw()
        try:
            view = tk.Text(root)
            md.configure_tags(view, 100)
            with open(parent, encoding='utf-8') as f:
                src = f.read()
            md.fill_view(view, src, {'n': 0, 'open': lambda u: None}, [], folder, parent)
            got = md.plain_widget(view)
        finally:
            root.destroy()
        self.assertIn('子笔记正文', got)
        self.assertIn('一段\n第二行', got)
        self.assertIn('粗', got)
        self.assertIn('斜', got)
        self.assertIn('粗斜', got)
        self.assertIn('删', got)
        self.assertIn('亮', got)
        self.assertIn('小心', got)
        self.assertIn('待办', got)
        self.assertIn('子项', got)
        self.assertIn('甲', got)
        self.assertIn('e=mc^2', got)
        self.assertIn('注', got)
        self.assertIn('加粗', got)
        self.assertIn('换行\n后', got)
        self.assertIn('别名', got)
        self.assertIn('图', got)
        prose = got.split('原样', 1)[0]
        for raw in ('**', '~~', '==', '[[', '[!warning]', '%%', '[^1]', '!['):
            self.assertNotIn(raw, prose, raw)
        self.assertIn('**不解释**', got)

    def test_table_is_a_grid(self):
        import tkinter as tk
        src = (
            '| 名 | 值 |\n'
            '| --- | ---: |\n'
            '| **甲** | 1 |\n'
            '| [站](https://example.com) | 中 |\n'
        )
        root = tk.Tk()
        root.withdraw()
        try:
            view = tk.Text(root, width=80, height=12)
            md.configure_tags(view, 100)
            md.fill_view(view, src, {'n': 0, 'open': lambda u: None}, [], '', '')
            root.update_idletasks()
            names = view.window_names()
            self.assertEqual(len(names), 1)
            frame = view.nametowidget(names[0])
            kids = sorted(
                frame.winfo_children(),
                key=lambda w: (int(w.grid_info()['row']), int(w.grid_info()['column'])),
            )
            texts = [w.get('1.0', 'end').strip() for w in kids]
            self.assertEqual(texts, ['名', '值', '甲', '1', '站', '中'])
            self.assertEqual(kids[1].tag_cget('al', 'justify'), 'right')
            self.assertEqual(kids[3].tag_cget('al', 'justify'), 'right')
            self.assertIn('b', kids[0].tag_names('1.0'))
            self.assertIn('link', kids[4].tag_names('1.0'))
            for w in kids:
                self.assertLessEqual(int(w.cget('height')), 2)
            bb = frame.grid_bbox()
            self.assertLessEqual(int(frame.cget('width')) - (bb[0] + bb[2]), 1)
            self.assertLessEqual(int(frame.cget('height')) - (bb[1] + bb[3]), 1)
            img = tk.PhotoImage(width=1, height=1)
            images = [img]
            md.release_document(view, images)
            self.assertEqual(images, [])
            self.assertEqual(view._md_windows, [])
            self.assertEqual(view.get('1.0', 'end-1c'), '')
        finally:
            root.destroy()

    def test_zoom_keeps_the_same_table(self):
        import tkinter as tk
        import tkinter.font as tkfont
        src = '甲\n\n| 名 | 值 |\n| --- | --- |\n| **乙** | 1 |\n'
        root = tk.Tk()
        root.withdraw()
        try:
            view = tk.Text(root, width=80, height=12, font=('Microsoft YaHei UI', 14))
            md.configure_tags(view, 100)
            md.fill_view(view, src, {'n': 0, 'open': lambda u: None}, [], '', '')
            name = view.window_names()[0]
            frame = view.nametowidget(name)
            md.configure_tags(view, 144)
            md.rescale_embedded(view)
            self.assertEqual(view.window_names(), (name,))
            texts = [w.get('1.0', 'end') for w in frame.winfo_children()]
            self.assertTrue(any('乙' in t for t in texts))
            size = tkfont.Font(font=view.cget('font')).cget('size')
            self.assertEqual(int(size), 20)
        finally:
            root.destroy()

    def test_long_cell_does_not_fill_the_screen(self):
        import tkinter as tk
        src = '| 说明 |\n| --- |\n| %s |\n' % ('字' * 80)
        root = tk.Tk()
        root.withdraw()
        try:
            view = tk.Text(root, width=80, height=12)
            md.configure_tags(view, 100)
            md.fill_view(view, src, {'n': 0, 'open': lambda u: None}, [], '', '')
            frame = view.nametowidget(view.window_names()[0])
            body = [w for w in frame.winfo_children() if int(w.grid_info()['row']) == 1][0]
            md._apply_table_width(frame, 900)
            wide_h = int(body.cget('height'))
            wide_w = int(frame.cget('width'))
            md._apply_table_width(frame, 280)
            narrow_h = int(body.cget('height'))
            narrow_w = int(frame.cget('width'))
            self.assertGreaterEqual(wide_w, 860)
            self.assertLessEqual(narrow_w, 300)
            self.assertGreater(narrow_h, wide_h)
            self.assertLessEqual(narrow_h, 12)
        finally:
            root.destroy()

    def test_wikilink_searches_obsidian_vault(self):
        import tkinter as tk
        vault = tempfile.mkdtemp()
        os.makedirs(os.path.join(vault, '.obsidian'))
        os.makedirs(os.path.join(vault, '甲'))
        os.makedirs(os.path.join(vault, '乙'))
        with open(os.path.join(vault, '乙', '远处.md'), 'w', encoding='utf-8') as f:
            f.write('远处正文\n')
        note = os.path.join(vault, '甲', '当前.md')
        with open(note, 'w', encoding='utf-8') as f:
            f.write('![[远处]]\n')
        root = tk.Tk()
        root.withdraw()
        try:
            view = tk.Text(root)
            md.configure_tags(view, 100)
            md.fill_view(view, '![[远处]]\n', {'n': 0, 'open': lambda u: None}, [], os.path.dirname(note), note)
            got = md.plain_widget(view)
        finally:
            root.destroy()
        self.assertIn('远处正文', got)

    def test_paragraph_gap_is_one_line(self):
        import tkinter as tk
        import tkinter.font as tkfont
        root = tk.Tk()
        root.withdraw()
        try:
            view = tk.Text(root, font=('Microsoft YaHei UI', 14))
            md.configure_tags(view, 100)
            md.fill_view(view, '甲\n乙\n\n丙\n', {'n': 0, 'open': lambda u: None}, [], '', '')
            self.assertNotIn('gap', view.tag_names('1.1'))
            self.assertIn('gap', view.tag_names('2.1'))
            para = int(view.tag_cget('gap', 'spacing3'))
            line = tkfont.Font(font=view.cget('font')).metrics('linespace')
            self.assertGreaterEqual(para, line)
            self.assertEqual(int(view.tag_cget('gap', 'spacing1')), 0)
            above = int(view.tag_cget('h2', 'spacing1'))
            below = int(view.tag_cget('h2', 'spacing3'))
            self.assertGreater(above, para)
            self.assertEqual(below, para)
            self.assertLess(above, para * 3)
            md.fill_view(view, '- 甲\n- 乙\n', {'n': 0, 'open': lambda u: None}, [], '', '')
            self.assertNotIn('gap', view.tag_names('1.0 lineend'))
        finally:
            root.destroy()

    def test_list_marker_stays_on_the_chinese_line(self):
        import tkinter as tk
        root = tk.Tk()
        root.geometry('780x320+30+30')
        view = tk.Text(
            root, wrap='word', font=('Microsoft YaHei UI', 14), padx=28, pady=16,
        )
        view.pack(fill='both', expand=True)
        src = (
            '1. **境界**：短。\n'
            '2. **四流派**：体修、法修、神修、炼修。克制环：神修克体修，体修克法修，'
            '法修克炼修，炼修克神修。正道以防为主，魔道以攻为主。\n'
            '- **项目**后面紧接一长串没有空格的汉字用来确认圆点和正文在同一行。\n'
        )
        try:
            root.update_idletasks()
            md.configure_tags(view, 100)
            md.fill_view(view, src, {'n': 0, 'open': lambda u: None}, [], '', '')
            root.deiconify()
            root.update()
            self.assertGreaterEqual(view.winfo_width(), 400)
            i2 = view.search('2', '1.0', stopindex='end')
            i4 = view.search('四', '1.0', stopindex='end')
            self.assertEqual(view.bbox(i2)[1], view.bbox(i4)[1])
            first = view.get(i2, view.index(i2 + ' display lineend'))
            self.assertIn('四', first)
            self.assertNotIn('\n', view.get(i2, i4))
            bullet = view.search('•', '1.0', stopindex='end')
            item = view.search('项', '1.0', stopindex='end')
            self.assertEqual(view.bbox(bullet)[1], view.bbox(item)[1])
        finally:
            root.destroy()

    def test_copy_drops_break_spaces(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        try:
            view = tk.Text(root, wrap='word', font=('Microsoft YaHei UI', 14))
            md.configure_tags(view, 100)
            md._insert_text(view, '见 3 章后面', ('p',))
            self.assertEqual(view.get('1.0', 'end-1c'), '见 3 章 后 面')
            self.assertEqual(md.plain_widget(view), '见 3 章后面')
            self.assertEqual(view.tag_cget('brk', 'elide'), '1')
            space = view.search(' ', '1.0')
            self.assertNotIn('brk', view.tag_names(space))
            iz = view.search('章', '1.0')
            hidden = view.index(iz + ' +1c')
            self.assertEqual(view.get(hidden), ' ')
            self.assertIn('brk', view.tag_names(hidden))
            view.tag_add('sel', '1.0', 'end-1c')
            view.event_generate('<<Copy>>')
            self.assertEqual(view.clipboard_get(), '见 3 章后面')
            view.pack()
            root.geometry('420x180+40+40')
            root.deiconify()
            view.focus_force()
            root.update()
            view.clipboard_clear()
            view.event_generate('<Control-Key-c>')
            root.update()
            self.assertEqual(view.clipboard_get(), '见 3 章后面')
            view.delete('1.0', 'end')
            md._insert_text(view, '上一个满身肥肉', ('p',))
            view.tag_add('sel', '1.0', 'end-1c')
            view.event_generate('<Control-Key-c>')
            root.update()
            self.assertEqual(view.clipboard_get(), '上一个满身肥肉')
            view.delete('1.0', 'end')
            md._insert_text(view, 'hello world', ('p',))
            self.assertEqual(view.get('1.0', 'end-1c'), 'hello world')
            self.assertFalse(view.tag_ranges('brk'))
        finally:
            root.destroy()

    def test_digit_space_chinese_wraps_at_the_edge(self):
        import tkinter as tk
        root = tk.Tk()
        root.geometry('640x320+30+30')
        view = tk.Text(
            root, wrap='word', font=('Microsoft YaHei UI', 14), padx=28, pady=16,
        )
        view.pack(fill='both', expand=True)
        src = '见 3 章后面紧跟着一长串没有空格的汉字用来看数字加空格会不会把后文整段换走。\n'
        try:
            root.update_idletasks()
            md.configure_tags(view, 100)
            md.fill_view(view, src, {'n': 0, 'open': lambda u: None}, [], '', '')
            root.deiconify()
            root.update()
            width = view.winfo_width()
            self.assertGreaterEqual(width, 480)
            self.assertLessEqual(width, 760)
            i3 = view.search('3', '1.0', stopindex='end')
            iz = view.search('章', '1.0', stopindex='end')
            ih = view.search('后', '1.0', stopindex='end')
            self.assertEqual(view.bbox(i3)[1], view.bbox(iz)[1])
            self.assertEqual(view.bbox(iz)[1], view.bbox(ih)[1])
            step = view.bbox(ih)[0] - view.bbox(iz)[0]
            self.assertLess(step, view.bbox(iz)[2] + 4)
            line = md.plain_widget(view, i3, view.index(i3 + ' display lineend'))
            self.assertIn('章', line)
            self.assertGreater(len(line), 8)
            self.assertIn('见 3 章后面', md.plain_widget(view))
        finally:
            root.destroy()

    def test_wheel_does_not_jump_over_a_table(self):
        import tkinter as tk
        root = tk.Tk()
        root.geometry('920x460+30+30')
        view = tk.Text(
            root, wrap='word', font=('Microsoft YaHei UI', 14), padx=28, pady=16,
        )
        view.pack(fill='both', expand=True)
        try:
            root.update_idletasks()
            md.configure_tags(view, 100)

            def block(n, ch):
                return '\n\n'.join('段%02d%s' % (i, ch * 24) for i in range(n))

            rows = '\n'.join('| %02d | %s |' % (i, '字' * 18) for i in range(6))
            src = (
                block(12, '甲') + '\n\n| 名 | 说明 |\n| --- | --- |\n' + rows
                + '\n\n' + block(12, '乙')
            )
            md.fill_view(view, src, {'n': 0, 'open': lambda u: None}, [], '', '')
            root.deiconify()
            root.update()
            self.assertGreaterEqual(view.winfo_width(), 80)
            view._md_laid_width = 0
            md._relayout_tables(view)
            root.update()
            total = int(view.tk.call(view._w, 'count', '-ypixels', '1.0', 'end'))
            self.assertLess(total, 8000)
            win = [it[2] for it in view.dump('1.0', 'end', window=True) if it[0] == 'window'][0]
            frame = view.nametowidget(view.window_names()[0])
            nxt = view.index(win + ' + 1 lines')
            line_h = int(view.tk.call(view._w, 'count', '-ypixels', win, nxt))
            frame_h = int(frame.cget('height'))
            air = int(view.tag_cget('gap', 'spacing3'))
            self.assertGreaterEqual(line_h, frame_h + air * 2 - 4)
            self.assertLess(line_h, frame_h + air * 2 + 30)
            prev = 0.0
            moved_once = False
            for _ in range(50):
                md.scroll_pixels(view, -120)
                root.update_idletasks()
                first, last = view.yview()
                moved = (first - prev) * total
                self.assertLess(abs(moved) if moved < 0 else moved, 90)
                self.assertGreater(moved, -2)
                if moved > 10:
                    moved_once = True
                if last >= 0.999:
                    break
                prev = first
            self.assertTrue(moved_once)
        finally:
            root.destroy()


def _write_silence(path, seconds):
    import wave
    rate = 16000
    n = int(rate * float(seconds))
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b'\x00\x00' * n)


def _reader():
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reader.pyw')
    spec = importlib.util.spec_from_file_location('md_reader', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QuoteBarTest(unittest.TestCase):
    def test_plain_quote_gets_obsidian_bar(self):
        import tkinter as tk
        root = tk.Tk()
        try:
            root.geometry('480x320+40+40')
            view = tk.Text(
                root, width=40, height=12, padx=28, pady=16, wrap='word',
                font=('Microsoft YaHei UI', 14),
            )
            view.pack(fill='both', expand=True)
            md.configure_tags(view, 100)
            md.fill_view(
                view, '> 外层\n>\n> > 内层一句\n\n后面\n\n> [!note] 标注\n> 不是竖线\n',
                {'n': 0, 'open': lambda u: None}, [], '', '',
            )
            self.assertEqual(view.tag_cget('quote', 'foreground'), md.FG)
            depths = [depth for _s, _e, depth in view._md_quotes]
            self.assertEqual(depths, [2, 1])
            self.assertIn('外层', md.plain_widget(view))
            self.assertIn('内层一句', md.plain_widget(view))
            root.deiconify()
            root.update()
            md.paint_quote_bars(view)
            shown = [
                bar for bar in view._md_bar_pool
                if bar.winfo_manager() == 'place'
            ]
            self.assertEqual(len(shown), 2)
            self.assertEqual({bar.cget('bg') for bar in shown}, {md.QUOTE_BAR})
            self.assertEqual({int(bar.winfo_width()) for bar in shown}, {2})
            outer = view.bbox(view.search('外', '1.0', 'end'))
            inner = view.bbox(view.search('内', '1.0', 'end'))
            # 竖线在文字左边，不压到字上。嵌套的那条更靠右。
            rights = sorted(bar.winfo_x() + bar.winfo_width() for bar in shown)
            self.assertLessEqual(rights[0], outer[0] - 8)
            self.assertLessEqual(rights[1], inner[0] - 8)
            self.assertGreater(rights[1], rights[0])
            # 外层竖线盖住引用本身，到下一段文字之前停住。
            following = view.bbox(view.search('后', '1.0', 'end'))
            outer_bar = min(shown, key=lambda bar: bar.winfo_x())
            self.assertLessEqual(abs(outer_bar.winfo_y() - outer[1]), 2)
            self.assertLessEqual(outer_bar.winfo_y() + outer_bar.winfo_height(), following[1])
        finally:
            root.destroy()

    def test_quote_bar_stays_with_text_while_pixel_scrolling(self):
        import tkinter as tk
        root = tk.Tk()
        root.geometry('520x260+40+40')
        try:
            view = tk.Text(
                root, padx=28, pady=16, wrap='word',
                font=('Microsoft YaHei UI', 14), bg='#111111', fg='#e6e6e6',
            )
            view.pack(fill='both', expand=True)
            md.configure_tags(view, 100)
            lines = ['第%02d行普通文字，用来占高度。' % i for i in range(1, 8)]
            lines += ['', '> 引用甲句应该贴着竖线。', '> 引用乙句同一条竖线。', '', '后面这段不该碰到竖线。']
            lines += ['尾%02d行继续往下撑高度。' % i for i in range(1, 40)]
            md.fill_view(
                view, '\n'.join(lines),
                {'n': 0, 'open': lambda u: None}, [], '', '',
            )
            root.update()
            idx = view.search('引用甲', '1.0', 'end')
            view.yview(idx)
            root.update()
            view.yview('scroll', 11, 'pixels')
            root.update()
            md.paint_quote_bars(view)
            root.update_idletasks()
            hit = view.bbox(idx)
            shown = [bar for bar in view._md_bar_pool if bar.winfo_manager() == 'place']
            self.assertIsNotNone(hit)
            self.assertEqual(len(shown), 1)
            bar = shown[0]
            self.assertLessEqual(abs(bar.winfo_y() - hit[1]), 2)
            self.assertLess(bar.winfo_x() + bar.winfo_width(), hit[0] - 8)
        finally:
            root.destroy()


class AutoScrollTest(unittest.TestCase):
    def test_scrollbar_report_does_not_turn_auto_off(self):
        reader = _reader()

        class Dummy:
            _auto_on = True
            _pill_hold = False
            _scroll = (0.0, 1.0)
            stopped = False

            def _stop_auto(self):
                self.stopped = True
                self._auto_on = False

            def _paint_pill(self):
                pass

        dummy = Dummy()
        reader.Reader._on_yscroll(dummy, '0.1', '0.4')
        self.assertFalse(dummy.stopped)
        self.assertTrue(dummy._auto_on)
        self.assertEqual(dummy._scroll, (0.1, 0.4))

    def test_speak_pieces_keeps_sentences(self):
        import cosy_speak
        pieces = cosy_speak.speak_pieces('第一句已经写完。第二句也写完了。后面还有一句。', first=8, rest=30)
        self.assertEqual(pieces[0], '第一句已经写完。')
        self.assertTrue(pieces[1].startswith('第二句也写完了。'))
        self.assertEqual(''.join(pieces), '第一句已经写完。第二句也写完了。后面还有一句。')
        self.assertEqual(cosy_speak.speak_pieces('   '), [])

    def test_resolve_voice_uses_saved_prompt_or_demo(self):
        import json
        import os
        import tempfile
        import wave
        import cosy_speak
        root = tempfile.mkdtemp()
        try:
            missing = cosy_speak.resolve_voice(root)
            self.assertFalse(missing['ok'])
            model = os.path.join(root, 'model', 'Fun-CosyVoice3-0.5B')
            os.makedirs(model)
            with open(os.path.join(model, 'cosyvoice3.yaml'), 'w', encoding='utf-8') as f:
                f.write('x: 1\n')
            home = os.path.join(root, 'model', 'CosyVoice', 'cosyvoice', 'cli')
            os.makedirs(home)
            with open(os.path.join(home, 'cosyvoice.py'), 'w', encoding='utf-8') as f:
                f.write('# marker\n')
            asset = os.path.join(root, 'model', 'CosyVoice', 'asset')
            os.makedirs(asset)
            demo = os.path.join(asset, 'cross_lingual_prompt.wav')
            _write_silence(demo, 0.2)
            user_dir = os.path.join(root, 'userdata')
            os.makedirs(user_dir)
            with open(os.path.join(user_dir, 'user.txt'), 'w', encoding='utf-8') as f:
                json.dump({'voice_clone': {'model_dir': model, 'speed': 1.2}}, f)
            builtin = cosy_speak.resolve_voice(root)
            self.assertTrue(builtin['ok'])
            self.assertTrue(builtin['builtin_voice'])
            self.assertEqual(builtin['prompt_wav'], demo)
            self.assertEqual(builtin['prompt_text'], '')
            own = os.path.join(root, 'mine.wav')
            _write_silence(own, 0.2)
            with open(os.path.join(user_dir, 'user.txt'), 'w', encoding='utf-8') as f:
                json.dump({
                    'voice_clone': {
                        'model_dir': model,
                        'prompt_wav': own,
                        'prompt_text': '参考稿',
                        'speed': 1.0,
                    },
                }, f)
            saved = cosy_speak.resolve_voice(root)
            self.assertFalse(saved['builtin_voice'])
            self.assertEqual(saved['prompt_wav'], own)
            self.assertEqual(saved['prompt_text'], '参考稿')
            female = os.path.join(asset, 'zero_shot_prompt.wav')
            _write_silence(female, 0.2)
            male = cosy_speak.resolve_voice(root, '男')
            self.assertTrue(male['builtin_voice'])
            self.assertEqual(male['prompt_wav'], demo)
            self.assertEqual(male['prompt_text'], '')
            self.assertEqual(male['instruct'], '')
            girl = cosy_speak.resolve_voice(root, '女')
            self.assertTrue(girl['builtin_voice'])
            self.assertEqual(girl['prompt_wav'], female)
            self.assertEqual(girl['prompt_text'], '')
            self.assertEqual(girl['instruct'], '')
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_voice_pick_is_exclusive(self):
        reader = _reader()

        class Btn:
            on = False

            def set_on(self, on):
                self.on = bool(on)

        class App:
            _voice = '男'
            _speak_on = False
            btn_voice_m = Btn()
            btn_voice_f = Btn()
            restarted = False
            _paint_voice_btns = reader.Reader._paint_voice_btns

            def _begin_speak(self, restart=False):
                self.restarted = bool(restart)

        app = App()
        app._paint_voice_btns()
        self.assertFalse(app.btn_voice_m.on)
        self.assertFalse(app.btn_voice_f.on)
        app._speak_on = True
        app._paint_voice_btns()
        self.assertTrue(app.btn_voice_m.on)
        self.assertFalse(app.btn_voice_f.on)
        reader.Reader._pick_voice(app, '女')
        self.assertTrue(app.restarted)
        self.assertFalse(app.btn_voice_m.on)
        self.assertTrue(app.btn_voice_f.on)
        app.restarted = False
        reader.Reader._pick_voice(app, '女')
        self.assertFalse(app.restarted)

    def test_wav_play_can_pause(self):
        from unittest.mock import patch
        import cosy_speak

        commands = []
        state = 'stopped'

        def send(command):
            nonlocal state
            action = command.split()[0]
            commands.append(action)
            if action in ('play', 'resume'):
                state = 'playing'
            elif action == 'pause':
                state = 'paused'
            elif action == 'close':
                state = 'stopped'
            return 0, state if action == 'status' else ''

        with patch.object(cosy_speak, '_mci', side_effect=send):
            play = cosy_speak._WavPlay()
            play.start('sample.wav')
            play.sync(True)
            self.assertTrue(play.held)
            self.assertEqual(state, 'paused')
            self.assertFalse(play.finished())
            play.sync(False)
            self.assertFalse(play.held)
            self.assertEqual(state, 'playing')
            play.close()
            self.assertFalse(play.open)
        self.assertEqual(commands[:2], ['open', 'play'])
        self.assertIn('pause', commands)
        self.assertIn('resume', commands)

    def test_speakable_text_skips_empty(self):
        import tkinter as tk
        reader = _reader()
        root = tk.Tk()
        root.withdraw()
        try:
            view = tk.Text(root)
            self.assertEqual(reader.speakable_text(view), '')
            view.insert('1.0', '（空文件）\n')
            self.assertEqual(reader.speakable_text(view), '')
            view.delete('1.0', 'end')
            view.insert('1.0', '无法打开：没有这个文件\n')
            self.assertEqual(reader.speakable_text(view), '')
            view.delete('1.0', 'end')
            view.insert('1.0', '第一句。\n第二句。')
            self.assertEqual(reader.speakable_text(view), '第一句。\n第二句。')
        finally:
            root.destroy()

    def test_speakable_text_starts_at_first_visible_line(self):
        import tkinter as tk
        reader = _reader()
        root = tk.Tk()
        root.geometry('420x180+40+40')
        try:
            view = tk.Text(
                root, padx=28, pady=16, wrap='word',
                font=('Microsoft YaHei UI', 14),
            )
            view.pack(fill='both', expand=True)
            lines = ['第%02d行的正文。' % i for i in range(1, 40)]
            view.insert('1.0', '\n'.join(lines))
            root.update()
            view.yview('15.0')
            root.update()
            spoken = reader.speakable_text(view)
            self.assertTrue(spoken.startswith('第15行的正文。'))
            self.assertNotIn('第14行', spoken)
            self.assertIn('第39行的正文。', spoken)

            view.delete('1.0', 'end')
            tail = '\n'.join('填充%02d。' % i for i in range(1, 40))
            view.insert('1.0', '上面一句。\n\n\n下面第一句。\n再后面一句。\n' + tail)
            root.update()
            view.yview('2.0')
            root.update()
            spoken = reader.speakable_text(view)
            self.assertTrue(spoken.startswith('下面第一句。'))
            self.assertNotIn('上面一句', spoken)
            self.assertIn('再后面一句。', spoken)

            view.delete('1.0', 'end')
            view.insert('1.0', ' '.join('词%02d' % i for i in range(1, 50)))
            root.update()
            view.yview(view.search('词20', '1.0'))
            root.update()
            top = view.index('@28,16 display linestart')
            top_text = view.get(top, '%s display lineend' % top).strip()
            spoken = reader.speakable_text(view)
            self.assertTrue(top_text)
            self.assertTrue(spoken.startswith(top_text))
            self.assertNotIn('词01', spoken)
            self.assertIn('词49', spoken)
        finally:
            root.destroy()

    def test_speaker_stops_and_finishes(self):
        import time
        reader = _reader()

        class Voice:
            def __init__(self):
                self.calls = []
                self.ops = []
                self.state = 1
                self.auto_finish = True

            def Speak(self, text, flags):
                self.calls.append((text, int(flags)))
                if int(flags) & reader._SVS_PURGE or not text:
                    self.state = 1
                elif int(flags) & reader._SVS_ASYNC:
                    self.state = 2

            def Pause(self):
                self.ops.append('pause')
                self.state = 1

            def Resume(self):
                self.ops.append('resume')

            def Skip(self, kind, count):
                self.ops.append('skip')
                self.state = 1

            @property
            def Status(self):
                class Box:
                    pass
                box = Box()
                box.RunningState = self.state
                if self.auto_finish and self.state == 2:
                    self.state = 1
                return box

        voice = Voice()
        done = []
        speaker = reader._WinSpeaker(done.append, lambda: voice)
        try:
            speaker.start('你好', 3)
            deadline = time.time() + 2
            while 3 not in done and time.time() < deadline:
                time.sleep(0.02)
            self.assertIn(3, done)
            self.assertIn(('你好', reader._SVS_ASYNC), voice.calls)
            voice.calls.clear()
            voice.state = 2
            speaker.stop()
            deadline = time.time() + 2
            while not any(flags & reader._SVS_PURGE for _text, flags in voice.calls) and time.time() < deadline:
                time.sleep(0.02)
            purged = [flags for text, flags in voice.calls if text == '']
            self.assertTrue(purged)
            self.assertTrue(all(flags & reader._SVS_PURGE and flags & reader._SVS_ASYNC for flags in purged))
            self.assertIn('skip', voice.ops)
        finally:
            speaker.close()

    def test_speaker_pause_does_not_finish(self):
        import time
        reader = _reader()

        class Voice:
            def __init__(self):
                self.calls = []
                self.ops = []
                self.state = 1
                self.auto_finish = False

            def Speak(self, text, flags):
                self.calls.append((text, int(flags)))
                if int(flags) & reader._SVS_PURGE or not text:
                    self.state = 1
                elif int(flags) & reader._SVS_ASYNC:
                    self.state = 2

            def Pause(self):
                self.ops.append('pause')
                self.state = 1

            def Resume(self):
                self.ops.append('resume')
                self.state = 2

            def Skip(self, kind, count):
                self.ops.append('skip')
                self.state = 1

            @property
            def Status(self):
                class Box:
                    pass
                box = Box()
                box.RunningState = self.state
                if self.auto_finish and self.state == 2:
                    self.state = 1
                return box

        voice = Voice()
        done = []
        speaker = reader._WinSpeaker(done.append, lambda: voice)
        try:
            speaker.start('你好', 5)
            deadline = time.time() + 2
            while ('你好', reader._SVS_ASYNC) not in voice.calls and time.time() < deadline:
                time.sleep(0.02)
            self.assertIn(('你好', reader._SVS_ASYNC), voice.calls)
            speaker.pause()
            deadline = time.time() + 2
            while 'pause' not in voice.ops and time.time() < deadline:
                time.sleep(0.02)
            self.assertIn('pause', voice.ops)
            time.sleep(0.3)
            self.assertNotIn(5, done)
            voice.ops.clear()
            voice.auto_finish = True
            speaker.resume()
            deadline = time.time() + 2
            while 5 not in done and time.time() < deadline:
                time.sleep(0.02)
            self.assertIn(5, done)
            self.assertEqual(voice.ops, ['resume'])
        finally:
            speaker.close()

    def test_speed_label_holds_two_digits(self):
        import tkinter as tk
        import tkinter.font as tkfont
        reader = _reader()
        root = tk.Tk()
        root.withdraw()
        try:
            font = tkfont.Font(root, family='Microsoft YaHei UI', size=11)
            chars = reader.speed_label_chars(font)
            slot = chars * font.measure('0')
            for n in range(reader.AUTO_FAST, reader.AUTO_SLOW + 1):
                self.assertGreaterEqual(slot, font.measure('%d秒/行' % n))
            self.assertGreater(font.measure('20秒/行'), font.measure('2秒/行'))
        finally:
            root.destroy()

    def test_slider_ends_are_20s_and_2s(self):
        reader = _reader()
        self.assertEqual(reader.seconds_from_pos(0), 20)
        self.assertEqual(reader.seconds_from_pos(1), 2)
        self.assertEqual(reader.seconds_from_pos(0.5), 11)
        for seconds in range(2, 21):
            self.assertEqual(
                reader.seconds_from_pos(reader.pos_from_seconds(seconds)),
                seconds,
            )

    def test_one_line_takes_the_chosen_seconds(self):
        reader = _reader()
        for seconds, ticks in ((20, 400), (2, 40)):
            acc = 0
            total = 0
            for _ in range(ticks):
                acc, step = reader.auto_scroll_pixels(acc, 24, 0.05, seconds)
                total += step
            self.assertEqual(total, 24, seconds)
            self.assertEqual(acc, 0)

    def test_slider_thumb_matches_the_ends(self):
        import tkinter as tk
        reader = _reader()
        root = tk.Tk()
        root.geometry('900x220+40+40')
        try:
            seen = []
            slider = reader._SpeedSlider(root, seen.append, 20)
            slider.pack()
            root.update()
            self.assertEqual(slider.seconds(), 20)
            self.assertEqual(seen, [])
            width = slider.winfo_width()
            self.assertGreater(width, 20)
            self.assertEqual(slider._seconds_at(0), 20)
            self.assertEqual(slider._seconds_at(width), 2)
            slider.set_seconds(2, notify=True)
            self.assertEqual(slider.seconds(), 2)
            self.assertEqual(seen[-1], 2)
            btn = reader._WordButton(root, '自动滚屏', lambda: seen.append('go'))
            btn.pack()
            root.update_idletasks()
            self.assertGreater(btn.winfo_reqwidth(), 40)
            self.assertGreater(len(btn.find_all()), 2)
            btn.set_on(True)
            self.assertGreater(len(btn.find_all()), 2)
            view = tk.Text(root, width=20, height=4, font=('Microsoft YaHei UI', 14))
            view.pack()
            view.insert('1.0', '\n'.join('第%d行' % i for i in range(60)))
            root.update()
            line = int(view.tk.call('font', 'metrics', view.cget('font'), '-linespace'))
            acc = 0
            for _ in range(40):
                acc, step = reader.auto_scroll_pixels(acc, line, 0.05, 2)
                if step:
                    view.yview('scroll', int(step), 'pixels')
            root.update_idletasks()
            first, _last = view.yview()
            self.assertGreater(first, 0)
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
